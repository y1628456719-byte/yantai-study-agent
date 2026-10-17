"""Agent 入口回归测试（P0-1 / P0-2）。

守住三件事：
1. **Agent 会自主调工具**（一句话 → 建档 → 推荐），且工具链不重复调用；
2. **Agent 不替用户答题**，也不在缺前置条件时报错（改为追问）；
3. **决策轨迹可审计**：每步都有实际动作 / 工具 / 结果摘要；模型不可用时报 503 而不是静默降级。
"""
import pytest
from fastapi.testclient import TestClient

from backend import config
from backend.llm import LLMError
from backend.main import app  # noqa: E402  （conftest 已设好 MOCK_LLM / EDU_AGENT_DB）


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def envelope(resp, expect=200):
    assert resp.status_code == expect, resp.text
    body = resp.json()
    assert set(body) == {"ok", "data", "error", "request_id"}, body
    if expect == 200:
        assert body["ok"] is True and body["error"] is None
    else:
        assert body["ok"] is False and body["data"] is None
        assert body["error"]["message"]
    return body


def turn(client, message, session_id=None, expect=200):
    payload = {"message": message}
    if session_id:
        payload["session_id"] = session_id
    return envelope(client.post("/api/v1/agent", json=payload), expect)["data"]


# ---------------------------------------------------------------- 自主调用工具


def test_agent_builds_session_and_recommends_from_one_sentence(client):
    data = turn(client, "我想考长春或哈尔滨的学校")
    assert data["session_id"], "Agent 应自己建档"
    assert data["tools_used"] == ["create_session", "recommend"], data["tools_used"]
    assert data["generated_by"] == "rules(offline)" and data["is_demo"] is True
    assert data["steps"] == 2 and data["terminated"] == "reply"
    assert data["state_patch"]["revision"] >= 2
    # 轨迹只公开实际动作和结果摘要，不返回模型内部解释
    for entry in data["trace"]:
        assert entry["step"] >= 1
        assert "thought" not in entry, entry
        assert entry["action"] in ("tool", "ask", "final"), entry
        assert entry["ok"] is True
    assert all(e["result_summary"] for e in data["trace"] if e["action"] == "tool")
    assert data["reply"]


def test_agent_does_not_call_the_same_tool_twice_in_one_turn(client):
    data = turn(client, "我是通信工程大三，想考长春或哈尔滨的学校，但数学一般")
    used = data["tools_used"]
    assert len(used) == len(set(used)), used
    assert "recommend" in used and "lab_start" in used
    assert len(used) <= 4


def test_agent_has_no_target_but_still_answers(client):
    """没有会话/没有目标时也不报错：该追问就追问。"""
    data = turn(client, "我想考研")
    assert data["session_id"]
    assert data["tools_used"] == ["create_session"]
    assert "专业方向" in data["reply"]
    assert data["trace"][-1]["action"] == "ask"


def test_agent_asks_for_target_before_revising_plan(client):
    """后端 revise_plan 需要目标院校（409）；Agent 不该撞墙，而应改口追问。"""
    sid = envelope(client.post("/api/v1/sessions", json={"profile": {"exam_year": 2027}}))["data"]["session_id"]
    data = turn(client, "每天只能学 30 分钟", session_id=sid)
    assert data["tools_used"] == [], data["tools_used"]
    assert "目标院校" in data["reply"]


# ---------------------------------------------------------------- 不替用户答题


def test_agent_hands_the_question_to_the_user(client):
    sid = turn(client, "我想考长春的学校，数学一般")["session_id"]
    data = turn(client, "B", session_id=sid)
    assert data["tools_used"].count("lab_answer") == 1, data["tools_used"]
    assert data["trace"][-1]["action"] == "ask"
    assert "question_id=" in data["reply"]


def test_agent_records_ability_evidence_in_session(client):
    """自评只是先验；真正写进 ability_profile 的是随后那道实测小题的作答（basis=measured）。"""
    sid = turn(client, "我想考长春的学校，数学一般")["session_id"]
    first = turn(client, "B", session_id=sid)
    assert first["tools_used"].count("lab_answer") == 1
    snapshot = envelope(client.get(f"/api/v1/sessions/{sid}"))["data"]
    learning = snapshot.get("learning_state") or {}
    assert (learning.get("self_assessment") or []), "自评作答必须结构化成 self_reported 先验"
    assert (learning.get("self_assessment") or [])[0]["basis"] == "self_reported"

    # 第二题是实测小题：答完才产生 measured 证据
    turn(client, "B", session_id=sid)
    snapshot = envelope(client.get(f"/api/v1/sessions/{sid}"))["data"]
    ability = (snapshot.get("learning_state") or {}).get("ability_profile") or []
    assert ability, "实测作答后应写入能力画像"
    assert ability[0]["basis"] == "measured"
    assert ability[0]["evidence_count"] >= 1


# ---------------------------------------------------------------- 定校与决策时间线


def test_agent_select_school_writes_decision_timeline(client):
    sid = turn(client, "我想考长春或哈尔滨的学校")["session_id"]
    data = turn(client, "就考东北大学", session_id=sid)
    assert data["tools_used"] == ["select_school"], data["tools_used"]
    snapshot = envelope(client.get(f"/api/v1/sessions/{sid}"))["data"]
    assert snapshot["selected_school_id"] == "neu"
    assert snapshot["decisions"], "确认目标院校必须写决策时间线（N28）"
    # 同一句再说一次不重复写
    again = turn(client, "就考东北大学", session_id=sid)
    assert again["tools_used"] == [], again["tools_used"]


def test_agent_recovers_from_unknown_session_id(client):
    data = turn(client, "我想考哈尔滨的学校", session_id="s_does_not_exist")
    assert data["session_id"] and data["session_id"] != "s_does_not_exist"
    assert "create_session" in data["tools_used"]


# ---------------------------------------------------------------- 错误与诚实性


def test_agent_rejects_blank_message(client):
    body = envelope(client.post("/api/v1/agent", json={"message": "   "}), expect=400)
    assert body["error"]["code"] == "INVALID_ARGUMENT"


def test_agent_model_failure_is_not_silently_degraded(client, monkeypatch):
    """配了密钥但模型不可用 → 503，不静默退回规则文案。"""
    async def boom(*args, **kwargs):
        raise LLMError("决策层连接失败（测试注入）")

    monkeypatch.setattr(config, "MOCK_LLM", False)
    monkeypatch.setattr(config, "RECOMMEND_DEGRADE", False)
    monkeypatch.setattr("backend.agent.llm.generate_json", boom)

    body = envelope(client.post("/api/v1/agent", json={"message": "我想考长春的学校"}), expect=503)
    assert body["error"]["code"] == "MODEL_UNAVAILABLE"
    assert body["error"]["retryable"] is True
    assert "模型服务" in body["error"]["message"]
