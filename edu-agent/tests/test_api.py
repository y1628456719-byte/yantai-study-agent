"""HTTP 契约回归测试（离线模式，真实三校数据 + mock 实验室）。

覆盖：12 个业务端点的主路径 + 4 类错误码 + 关键不变量
（同一 revision 幂等、改时间不清空记录、自评与实测不混用、N32 维度口径）。
"""
import pytest
from fastapi.testclient import TestClient

from backend.main import app  # noqa: E402  （conftest 已设好 MOCK_LLM / EDU_AGENT_DB）


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def session_id(client):
    resp = client.post(
        "/api/v1/sessions",
        json={
            "profile": {
                "exam_year": 2027,
                "major": "信息与通信工程",
                "preferred_regions": ["长春", "哈尔滨", "沈阳"],
                "daily_minutes": 90,
                "study_status": "边工作边考",
            }
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    return body["data"]["session_id"]


def envelope(resp, expect=200):
    assert resp.status_code == expect, resp.text
    body = resp.json()
    assert set(body) == {"ok", "data", "error", "request_id"}, body
    if expect == 200:
        assert body["ok"] is True and body["error"] is None
    else:
        assert body["ok"] is False and body["data"] is None
        assert body["error"]["message"], "失败必须带可直接展示的中文 message"
    return body


# ---------------------------------------------------------------- 基础


def test_health_lists_three_schools(client):
    data = envelope(client.get("/api/health"))["data"]
    assert set(data["schools"]) >= {"jlu", "heu", "neu"}
    assert data["subjects"] >= 30


def test_meta_sources_is_traceable(client):
    data = envelope(client.get("/api/v1/meta/sources"))["data"]
    assert data["total_records"] > 0
    for row in data["coverage"]:
        assert row["school_id"] and row["categories"]
        assert row["latest_year"]
    assert data["levels"]["official"] > 0


def test_meta_subjects_matches_spec_shape(client):
    data = envelope(client.get("/api/v1/meta/subjects"))["data"]
    codes = {s["subject_code"] for s in data["subjects"]}
    assert {"MATH1", "ENG1", "POLITICS", "SIG_SYS"} <= codes
    for subject in data["subjects"]:
        assert subject["category"]
        assert isinstance(subject["aliases"], list)


# ---------------------------------------------------------------- 会话


def test_create_session_returns_onboarding(client, session_id):
    data = envelope(client.get(f"/api/v1/sessions/{session_id}"))["data"]
    assert data["session_id"] == session_id
    assert data["onboarding"]["phase"]
    assert data["onboarding"]["phase_confidence"] in ("inferred", "confirmed")


def test_patch_profile_merges_and_recomputes(client, session_id):
    data = envelope(
        client.patch(f"/api/v1/sessions/{session_id}/profile", json={"daily_minutes": 60})
    )["data"]
    assert data["profile"]["daily_minutes"] == 60
    # 未提及的字段不能被清掉
    assert data["profile"]["major"] == "信息与通信工程"


def test_session_not_found_is_404(client):
    body = envelope(client.get("/api/v1/sessions/nope"), expect=404)
    assert body["error"]["code"] == "SESSION_NOT_FOUND"


# ---------------------------------------------------------------- 推荐


def test_recommendations_shape_and_dimension_accounting(client, session_id):
    data = envelope(client.post(f"/api/v1/sessions/{session_id}/recommendations", json={}))["data"]
    assert data["profile_echo"]["understood_as"]
    assert data["recommendations"], "三校数据下不应为空"
    total = len(data["dimension_hits"]) + len(data["dimension_missing"])
    assert total == 33, "N32：命中 + 缺失必须等于 33 个决策维度"
    for item in data["recommendations"]:
        assert item["school_id"] and item["school_name"]
        assert isinstance(item["fit_reasons"], list)
        for reason in item["fit_reasons"]:
            assert reason["kind"] in ("fact", "judgement")
            if reason["kind"] == "judgement":
                assert reason.get("source_index") is None
        for source in item["sources"]:
            assert source["title"] and source["url"]


def test_recommendations_are_idempotent_within_revision(client, session_id):
    first = envelope(client.post(f"/api/v1/sessions/{session_id}/recommendations", json={}))["data"]
    second = envelope(client.post(f"/api/v1/sessions/{session_id}/recommendations", json={}))["data"]
    assert [r["school_id"] for r in first["recommendations"]] == [
        r["school_id"] for r in second["recommendations"]
    ]
    assert [r["metrics"]["tier"] for r in first["recommendations"]] == [
        r["metrics"]["tier"] for r in second["recommendations"]
    ]


def test_tier_comes_from_hard_rule_not_model(client, session_id):
    data = envelope(client.post(f"/api/v1/sessions/{session_id}/recommendations", json={}))["data"]
    tiers = {r["metrics"]["tier"] for r in data["recommendations"]}
    assert tiers <= {None, "冲", "稳", "保"}
    assert data["tier_rule"], "必须说明分档依据"
    assert any(r["metrics"]["score_line_total"] for r in data["recommendations"])


# ---------------------------------------------------------------- 目标 / 名单


def test_target_unknown_school_is_400(client, session_id):
    body = envelope(
        client.put(f"/api/v1/sessions/{session_id}/target", json={"school_id": "nope"}),
        expect=400,
    )
    assert body["error"]["code"] == "INVALID_ARGUMENT"


def test_set_target_and_shortlist(client, session_id):
    data = envelope(
        client.put(f"/api/v1/sessions/{session_id}/target", json={"school_id": "jlu", "user_note": "长春优先"})
    )["data"]
    assert data["selected_school_id"] == "jlu"
    assert data["decisions"], "N28：确认目标要写决策时间线"
    data = envelope(client.put(f"/api/v1/sessions/{session_id}/shortlist", json={"school_ids": ["jlu", "neu"]}))["data"]
    assert data["shortlist"] == ["jlu", "neu"]


# ---------------------------------------------------------------- 资料


def test_material_preview_and_idempotent_delete(client, session_id):
    text = "模拟电子技术 第一章 半导体基础\n\n三极管放大电路 静态工作点 Q 点计算。\n\n" + "共射极放大电路的饱和失真与截止失真。" * 20
    created = envelope(
        client.post(
            f"/api/v1/sessions/{session_id}/materials",
            json={"title": "模电笔记", "text": text, "is_demo": True, "subject": "ANALOG_CIRCUIT"},
        )
    )["data"]
    assert created["character_count"] == len(text)
    assert created["preview"]
    listed = envelope(client.get(f"/api/v1/sessions/{session_id}/materials"))["data"]
    assert any(m["material_id"] == created["material_id"] for m in listed["materials"])
    assert envelope(client.delete(f"/api/v1/sessions/{session_id}/materials/{created['material_id']}"))["data"]["deleted"] is True
    # 幂等：再删一次仍返回成功（不 404）
    again = envelope(client.delete(f"/api/v1/sessions/{session_id}/materials/{created['material_id']}"))
    assert again["data"]["deleted"] is True
    assert envelope(
        client.delete(f"/api/v1/sessions/{session_id}/materials/{created['material_id']}")
    )["data"]["deleted"] is True


def test_material_invalid_argument(client, session_id):
    body = envelope(client.post(f"/api/v1/sessions/{session_id}/materials", json={"title": "空", "text": ""}), expect=400)
    assert body["error"]["code"] == "INVALID_ARGUMENT"


# ---------------------------------------------------------------- 实验室


def test_lab_self_assessment_path(client, session_id):
    started = envelope(
        client.post(
            f"/api/v1/sessions/{session_id}/lab/start",
            json={"material_id": None, "topic": "MATH_FOUNDATION", "mode": "self_assessment"},
        )
    )["data"]
    assert started["stage"] == "diagnosis"
    assert started["question"]["question_id"].startswith("self_")
    assert started["is_demo"] is True
    answered = envelope(
        client.post(
            f"/api/v1/sessions/{session_id}/lab/answer",
            json={"question_id": started["question"]["question_id"], "answer": "3"},
        )
    )["data"]
    assert answered["next_action"] in ("answer", "complete")
    assert all(ev["kind"] == "answer" for ev in answered["evidence"])
    snapshot = envelope(client.get(f"/api/v1/sessions/{session_id}"))["data"]
    self_assessment = snapshot["learning_state"].get("self_assessment") or []
    assert self_assessment and self_assessment[0]["basis"] == "self_reported"


def test_lab_answer_rejects_stale_question(client, session_id):
    body = envelope(
        client.post(f"/api/v1/sessions/{session_id}/lab/answer", json={"question_id": "not-current", "answer": "A"}),
        expect=400,
    )
    assert body["error"]["code"] == "INVALID_ARGUMENT"


def test_lab_material_path_keeps_evidence_locator(client, session_id):
    text = "模拟电子技术 静态工作点\n\n共射极放大电路 Q 点：基极偏置电阻与集电极电阻决定。\n\n饱和失真：输出波形顶部被削平。"
    material = envelope(
        client.post(
            f"/api/v1/sessions/{session_id}/materials",
            json={"title": "模电提纲", "text": text, "subject": "ANALOG_CIRCUIT"},
        )
    )["data"]
    state = envelope(
        client.post(
            f"/api/v1/sessions/{session_id}/lab/start",
            json={"material_id": material["material_id"], "topic": "ANALOG_CIRCUIT"},
        )
    )["data"]
    for evidence in state["evidence"]:
        assert evidence["kind"] == "material"
        assert evidence["quote"] and evidence["locator"]


def test_lab_unavailable_is_not_silently_mocked(client, monkeypatch):
    """LAB_ADAPTER 取值非法时必须显式报 LAB_UNAVAILABLE，而不是偷偷用 mock。"""
    from backend import config
    from backend.lab_adapter import get_adapter, reset_adapter

    monkeypatch.setattr(config, "LAB_ADAPTER", "nonsense")
    reset_adapter()  # 丢掉之前测试建好的 mock 单例
    try:
        with pytest.raises(Exception) as excinfo:
            get_adapter()
        assert "nonsense" in str(excinfo.value) or "LAB" in str(excinfo.value)
    finally:
        reset_adapter()


# ---------------------------------------------------------------- 计划


def test_plan_revise_keeps_records(client, session_id):
    before = envelope(client.get(f"/api/v1/sessions/{session_id}"))["data"]
    data = envelope(client.post(f"/api/v1/sessions/{session_id}/plan/revise", json={"daily_minutes": 30}))["data"]
    assert data["previous_daily_minutes"] != 30 or data["daily_minutes"] == 30
    assert data["plan"], "计划不能为空"
    assert data["changes"], "N21：必须说明砍了什么"
    for change in data["changes"]:
        assert change["action"] in ("dropped", "kept")
        assert change["reason"]
    after = envelope(client.get(f"/api/v1/sessions/{session_id}"))["data"]
    assert after["selected_school_id"] == before["selected_school_id"]
    assert len(after["materials"]) == len(before["materials"])
    assert after["learning_state"] == before["learning_state"]
    assert after["plan"]["daily_minutes"] == 30


def test_plan_actions_come_from_rules_not_model():
    """action（保留/砍掉）必须由后端规则裁定。

    实测真实模型会把被砍掉的条目写成 action=kept，屏幕上就会出现
    「保留：思想政治理论」而 plan 里根本没有它 —— 这里锁住合并逻辑。
    """
    from backend.services import modelsvc

    rule_changes = [
        {"action": "dropped", "task": "初试科目：思想政治理论", "reason": "规则：按优先级暂缓"},
        {"action": "kept", "task": "初试科目：数学（一）", "reason": "规则：目标院校初试科目"},
    ]
    model_changes = [
        {"action": "kept", "task": "初试科目：思想政治理论", "reason": "模型：政治权重最低，本轮让位"},
        {"action": "kept", "task": "初试科目：数学（一）", "reason": "模型：权重 3，优先保留"},
    ]
    merged = modelsvc._merge_plan_changes(rule_changes, model_changes)["changes"]
    assert [c["action"] for c in merged] == ["dropped", "kept"]
    assert merged[0]["reason"].startswith("模型：")
    assert merged[1]["reason"].startswith("模型：")
    # 模型没提到的条目仍用规则文案，条目集合也由规则决定
    merged2 = modelsvc._merge_plan_changes(rule_changes, [])["changes"]
    assert [c["reason"] for c in merged2] == [c["reason"] for c in rule_changes]


def test_plan_revise_requires_target(client):
    """未选目标校 → 409 TARGET_NOT_SELECTED（而不是给一份没有依据的计划）。"""
    fresh = envelope(client.post("/api/v1/sessions", json={"profile": {"exam_year": 2027}}))["data"]
    body = envelope(
        client.post(f"/api/v1/sessions/{fresh['session_id']}/plan/revise", json={"daily_minutes": 60}),
        expect=409,
    )
    assert body["error"]["code"] == "TARGET_NOT_SELECTED"


# ---------------------------------------------------------------- 阶段推断


def test_phase_inference_does_not_default_to_junior_year(client):
    fresh = envelope(client.post("/api/v1/sessions", json={"profile": {"exam_year": 2027}}))["data"]
    onboarding = fresh["onboarding"]
    assert onboarding["phase_confidence"] == "inferred"
    assert onboarding["next_question"]
    assert "大三" not in str(onboarding)
