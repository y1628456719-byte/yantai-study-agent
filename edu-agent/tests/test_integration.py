"""整站适配回归；默认全部模拟或注入传输，不消耗真实模型额度。"""
import asyncio
import copy
import json
import threading
import uuid
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import config, llm
from backend.core.errors import AppError
from backend.lab_adapter import get_adapter, reset_adapter
from backend.main import app
from backend.services import lab_service, session_store


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "LAB_ADAPTER", "blindspot")
    monkeypatch.setattr(config, "LAB_BLINDSPOT_MODE", "mock")
    reset_adapter()
    with TestClient(app) as result:
        yield result
    reset_adapter()


def state(client):
    response = client.get("/api/state")
    assert response.status_code == 200, response.text
    result = response.json()
    client._test_page_session_id = result["session_id"]
    return result


def act(client, action, params=None, *, expected=None, request_id=None, status=200, session_id=None):
    target = session_id or getattr(client, "_test_page_session_id", None)
    assert target, "先显式读取页面state，不在动作前自动刷新会话"
    payload = {"session_id": target, "action": action, "params": params or {},
               "request_id": request_id or uuid.uuid4().hex}
    if expected is not None:
        payload["expected_revision"] = expected
    response = client.post("/api/action", json=payload)
    assert response.status_code == status, response.text
    return response.json()


def initial_cycle(client):
    initial = state(client)
    result = act(client, "start", expected=initial["revision"])
    for _ in range(3):
        q = result["lab_state"]["question"]
        assert q
        result = act(client, "submit", {"question_id": q["question_id"], "answer": "A"}, expected=result["revision"])
    return result


def test_raw_state_cookie_and_full_adaptive_cycle(client):
    response = client.get("/api/state")
    initial = response.json()
    assert initial["lab_state"] is None and initial["plan"] is None
    assert "ok" not in initial
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    result = initial_cycle(client)
    assert result["lab_state"]["stage"] == "summary"
    assert isinstance(result["lab_state"]["explanation"], dict)
    result = act(client, "feedback", {"feedback": "unclear"}, expected=result["revision"])
    result = act(client, "time", {"minutes": 4}, expected=result["revision"])
    assert result["session"]["profile"]["daily_minutes"] == 4
    assert result["lab_state"]["cycle"]["stop_reason"] == "time_budget"
    result = act(client, "time", {"minutes": 60}, expected=result["revision"])
    result = act(client, "continue", expected=result["revision"])
    q = result["lab_state"]["question"]
    result = act(client, "submit", {"question_id": q["question_id"], "answer": "A"}, expected=result["revision"])
    result = act(client, "feedback", {"feedback": "unclear"}, expected=result["revision"])
    result = act(client, "continue", expected=result["revision"])
    result = act(client, "stop", expected=result["revision"])
    assert result["lab_state"]["stage"] == "stopped"
    assert state(client)["lab_state"] == result["lab_state"]


def test_request_replay_conflict_and_stale_are_atomic(client):
    initial = state(client)
    rid = uuid.uuid4().hex
    result = act(client, "start", expected=initial["revision"], request_id=rid)
    again = act(client, "start", expected=initial["revision"], request_id=rid)
    assert again == result
    act(client, "time", {"minutes": 15}, expected=initial["revision"], request_id=rid, status=409)
    act(client, "time", {"minutes": 15}, expected=initial["revision"], status=409)
    assert state(client)["revision"] == result["revision"]
    prior = session_store.snapshot(result["session_id"])
    act(client, "submit", {"question_id": "not-current", "answer": "B"}, expected=result["revision"], status=400)
    assert session_store.snapshot(result["session_id"]) == prior


def test_original_api_without_ids_does_not_merge_new_feedback_or_continue(client):
    result = initial_cycle(client)
    sid = result["session_id"]
    endpoint = f"/api/v1/sessions/{sid}/lab/action"
    for _ in range(2):
        response = client.post(endpoint, json={"action": "feedback", "feedback": "unclear"})
        assert response.status_code == 200, response.text
        response = client.post(endpoint, json={"action": "continue"})
        assert response.status_code == 200, response.text
        q = response.json()["data"]["question"]
        response = client.post(f"/api/v1/sessions/{sid}/lab/answer", json={"question_id": q["question_id"], "answer": "A"})
        assert response.status_code == 200, response.text
    stored = json.loads(session_store.snapshot(sid)["learning_state"]["blindspot"])
    assert len(stored["context"]["learning_state"]["feedback_records"]) == 2
    assert stored["context"]["learning_state"]["rounds_started"] == 3


def test_restart_restores_snapshot_and_request_result(client):
    initial = state(client)
    rid = uuid.uuid4().hex
    result = act(client, "start", request_id=rid, expected=initial["revision"])
    session_store._conn.close()
    session_store._conn = None
    reset_adapter()
    restored = state(client)
    assert restored["revision"] == result["revision"]
    assert restored["lab_state"] == result["lab_state"]
    assert act(client, "start", request_id=rid, expected=initial["revision"]) == result


def test_all_time_entry_points_keep_main_profile_and_host_consistent(client):
    result = initial_cycle(client)
    sid = result["session_id"]
    before = json.loads(session_store.snapshot(sid)["learning_state"]["blindspot"])["context"]["learning_state"]
    assert client.put(f"/api/v1/sessions/{sid}/target", json={"school_id": "jlu"}).status_code == 200
    for minutes, entry in [(4, "lab"), (25, "profile"), (30, "plan")]:
        if entry == "lab":
            act(client, "time", {"minutes": minutes})
        elif entry == "profile":
            response = client.patch(f"/api/v1/sessions/{sid}/profile", json={"daily_minutes": minutes})
            assert response.status_code == 200, response.text
        else:
            response = client.post(f"/api/v1/sessions/{sid}/plan/revise", json={"daily_minutes": minutes})
            assert response.status_code == 200, response.text
        current = session_store.snapshot(sid)
        host = json.loads(current["learning_state"]["blindspot"])
        assert current["profile"]["daily_minutes"] == minutes
        assert host["context"]["profile"]["daily_minutes"] == minutes
        assert host["context"]["learning_state"]["core"]["answers"] == before["core"]["answers"]
        assert state(client)["plan"]["daily_minutes"] == minutes
    act(client, "feedback", {"feedback": "unclear"})
    assert state(client)["session"]["profile"]["daily_minutes"] == 30


def test_public_sessions_never_return_host_snapshot_requests_or_answer_key(client):
    state(client)
    result = act(client, "start")
    sid = result["session_id"]
    stored = session_store.snapshot(sid)
    assert json.loads(stored["learning_state"]["blindspot"])["requests"]
    for response in [client.get("/api/state"), client.get(f"/api/v1/sessions/{sid}"),
                     client.patch(f"/api/v1/sessions/{sid}/profile", json={"major": "图书情报"})]:
        assert response.status_code == 200
        text = response.text
        for private in ['"blindspot":', '"requests":', '"context":', '"correct_option_id":', '"mock_candidate":']:
            assert private not in text


@pytest.mark.parametrize("same_id", [False, True])
def test_concurrent_actions_use_database_cas(client, monkeypatch, same_id):
    state(client)
    result = act(client, "start")
    sid, base = result["session_id"], result["revision"]
    adapter = get_adapter()
    original = adapter._impl.act
    entered, release = threading.Event(), threading.Event()
    calls = []
    def synchronized(context, action, **params):
        calls.append(action)
        candidate = original(context, action, **params)
        entered.set()
        assert release.wait(3)
        return candidate
    monkeypatch.setattr(adapter._impl, "act", synchronized)
    ids = [uuid.uuid4().hex, uuid.uuid4().hex]
    if same_id:
        ids[1] = ids[0]
    def run(index):
        try:
            return asyncio.run(lab_service.execute(sid, "time", {"minutes": 20},
                request_id=ids[index], expected_revision=base))
        except AppError as exc:
            return exc
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run, 0)
        assert entered.wait(3)
        second = pool.submit(run, 1)
        try:
            pending = second.result(timeout=3)
        finally:
            release.set()
        results = [first.result(timeout=3), pending]
    assert session_store.snapshot(sid)["revision"] == base + 1
    assert calls == ['time']
    assert isinstance(results[1], AppError) and results[1].code == 'CONFLICT'
    if same_id:
        replay = asyncio.run(lab_service.execute(sid, 'time', {'minutes':20},
            request_id=ids[0], expected_revision=base))
        assert replay == results[0]
    else:
        assert sum(isinstance(item, AppError) and item.code == "CONFLICT" for item in results) == 1


def test_model_failure_never_commits_or_falls_back(client, monkeypatch):
    state(client)
    initial = act(client, "start")
    def unavailable(*args, **kwargs):
        raise RuntimeError("unavailable test transport")
    monkeypatch.setattr(get_adapter()._impl._lab.core.runners["mock"].model, "generate", unavailable)
    q = initial["lab_state"]["question"]
    act(client, "submit", {"question_id": q["question_id"], "answer": "A"}, expected=initial["revision"], status=503)
    assert state(client)["revision"] == initial["revision"]
    assert state(client)["lab_state"] == initial["lab_state"]


def test_external_sqlite_writer_invalidates_computed_candidate(client, monkeypatch):
    state(client)
    started = act(client, "start")
    sid = started["session_id"]
    before_host = session_store.snapshot(sid)["learning_state"]["blindspot"]
    adapter = get_adapter()
    original = adapter._impl.act
    def external_update(context, action, **params):
        candidate = original(context, action, **params)
        with sqlite3.connect(config.DB_PATH) as other:
            other.execute("UPDATE sessions SET revision=revision+1 WHERE session_id=?", (sid,))
        return candidate
    monkeypatch.setattr(adapter._impl, "act", external_update)
    rid = uuid.uuid4().hex
    act(client, "time", {"minutes": 20}, expected=started["revision"], request_id=rid, status=409)
    assert session_store.snapshot(sid)["learning_state"]["blindspot"] == before_host
    assert session_store.get_conn().execute("SELECT COUNT(*) FROM lab_requests WHERE request_id=?", (rid,)).fetchone()[0] == 0


def test_slow_plan_generation_cannot_overwrite_concurrent_time_change(client, monkeypatch):
    from backend.services import modelsvc
    result = initial_cycle(client)
    sid = result["session_id"]
    assert client.put(f"/api/v1/sessions/{sid}/target", json={"school_id": "jlu"}).status_code == 200
    original = modelsvc.build_plan_changes
    async def concurrent_time(*args, **kwargs):
        output = await original(*args, **kwargs)
        session_store.merge_profile(sid, {"daily_minutes": 22})
        return output
    monkeypatch.setattr(modelsvc, "build_plan_changes", concurrent_time)
    response = client.post(f"/api/v1/sessions/{sid}/plan/revise", json={"daily_minutes": 30})
    assert response.status_code == 409, response.text
    session = session_store.snapshot(sid)
    assert session["profile"]["daily_minutes"] == 22
    assert json.loads(session["learning_state"]["blindspot"])["context"]["profile"]["daily_minutes"] == 22


def test_local_host_origin_controls_cover_all_write_routes(client):
    initial = state(client)
    assert client.get("/api/state", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/state", headers={"Host": "evil.example"}).status_code == 403
    for path, payload in [("/api/action", {"action": "start"}), ("/api/v1/sessions", {"profile": {}})]:
        response = client.post(path, json=payload, headers={"Origin": "https://evil.example"})
        assert response.status_code == 403
        assert "access-control-allow-origin" not in response.headers
    assert client.get("/api/state", headers={"Origin": "http://testserver"}).status_code == 200
    assert client.post("/api/action", json={"session_id": initial["session_id"], "action": "start", "params": {}}, headers={"Origin": "http://testserver"}).status_code == 200


def test_flat_frontend_requests_and_duplicate_parameter_guard(client):
    first = state(client)
    result = client.post("/api/action", json={"session_id": first["session_id"], "action": "start", "expected_revision": first["revision"],
        "request_id": uuid.uuid4().hex}).json()
    q = result["lab_state"]["question"]
    response = client.post("/api/action", json={"session_id": first["session_id"], "action": "submit", "expected_revision": result["revision"],
        "request_id": uuid.uuid4().hex, "question_id": q["question_id"], "answer": "A"})
    assert response.status_code == 200, response.text
    assert response.json()["lab_state"]["question"]["question_id"] != q["question_id"]
    response = client.post("/api/action", json={"session_id": first["session_id"], "action": "time", "minutes": 20, "params": {"minutes": 20}})
    assert response.status_code == 400


def test_public_agent_trace_contains_actions_without_internal_thoughts(client):
    response = client.post("/api/v1/agent", json={"message": "我想考长春的学校"})
    assert response.status_code == 200, response.text
    trace = response.json()["data"]["trace"]
    assert trace and any(item.get("tool") == "create_session" for item in trace)
    assert all("thought" not in item for item in trace)


def test_live_adapter_injects_authorized_client_without_network(monkeypatch):
    from vendor.blindspot_lab.lab import aiping
    from backend.lab_adapter.blindspot import BlindspotLabAdapter
    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs == {"model": "DeepSeek-V4.1-Flash"}
        def generate_json(self, *args):
            raise RuntimeError("must only be called for live generation")
    monkeypatch.setattr(aiping, "AipingClient", FakeClient)
    adapter = BlindspotLabAdapter(runtime_mode="live")
    assert adapter._lab.core.runners["live"].model.runtime_mode == "live"
    assert adapter._lab.core.runners["live"].timeout == min(120, config.LAB_TIMEOUT)


@pytest.mark.parametrize("status,body", [(200, {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
    "model": "DeepSeek-V4.1-Flash", "usage": {"total_tokens": 2}}), (503, "secret-test-key upstream body"),
    (200, "secret-test-key invalid-json")])
def test_main_model_max_effort_metadata_and_error_redaction(monkeypatch, status, body):
    monkeypatch.setattr(config, "LLM_API_KEY", "secret-test-key")
    monkeypatch.setattr(config, "LLM_MODEL", "DeepSeek-V4.1-Flash")
    monkeypatch.setattr(config, "LLM_BASE_URL", "https://aiping.cn/api/v1")
    captured = {}
    def transport(request):
        captured.update(json.loads(request.content))
        return httpx.Response(status, json=body) if isinstance(body, dict) else httpx.Response(status, text=body)
    original = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(transport), **kwargs))
    try:
        result = asyncio.run(llm._post_chat([{"role": "user", "content": "synthetic test"}], 0.2, True))
        assert result == "{}"
    except llm.LLMError as exc:
        assert status != 200 or not isinstance(body, dict)
        assert "secret-test-key" not in str(exc)
        assert "upstream body" not in str(exc)
    assert captured["reasoning_effort"] == "max"
    assert captured["thinking"] == {"type": "enabled"}
    metadata = json.dumps(llm.CALLS[-1])
    assert "secret-test-key" not in metadata and "synthetic test" not in metadata


@pytest.mark.parametrize("snapshot_size", [100, 50000])
def test_plan_model_input_excludes_private_host_cache(monkeypatch, snapshot_size):
    from backend.services import modelsvc
    captured = {}
    async def fake_generate(messages, *args, **kwargs):
        captured["messages"] = json.dumps(messages, ensure_ascii=False)
        return {"changes": []}
    monkeypatch.setattr(config, "MOCK_LLM", False)
    monkeypatch.setattr(config, "LLM_API_KEY", "synthetic-only")
    monkeypatch.setattr(llm, "generate_json", fake_generate)
    plan = [{"task": "目标科目复习", "minutes": 20, "kept": True, "reason": "已确认目标科目"}]
    result = asyncio.run(modelsvc.build_plan_changes(plan, previous_minutes=60, daily_minutes=20,
        context={"selected_school_id": "jlu", "onboarding": {"phase_name": "备考"},
                 "learning_state": {"blindspot": "PRIVATE_HOST_CACHE" * snapshot_size,
                                    "lab": {"stage": "summary", "topic": "自编逻辑样本"}}}))
    assert "PRIVATE_HOST_CACHE" not in captured["messages"]
    assert "blindspot" not in captured["messages"]
    assert len(captured["messages"]) < 5000
    assert "自编逻辑样本" in captured["messages"] and "jlu" in captured["messages"]
    assert result["changes"][0]["task"] == "目标科目复习"


def test_mock_and_live_preview_cookies_do_not_replace_each_other(client, monkeypatch):
    monkeypatch.setenv("AIPING_API_KEY", "synthetic-only-no-network")
    mock_sid = state(client)["session_id"]
    assert client.cookies.get("edu_session_mock") == mock_sid
    monkeypatch.setattr(config, "LAB_BLINDSPOT_MODE", "live")
    reset_adapter()
    live_sid = state(client)["session_id"]
    assert live_sid != mock_sid
    assert client.cookies.get("edu_session_live") == live_sid
    monkeypatch.setattr(config, "LAB_BLINDSPOT_MODE", "mock")
    reset_adapter()
    assert state(client)["session_id"] == mock_sid


@pytest.mark.parametrize("entry,minutes", [("lab", 4), ("lab", 30), ("profile", 4), ("profile", 30)])
def test_time_changes_atomically_rebuild_existing_main_plan_and_preserve_records(client, monkeypatch, entry, minutes):
    result = initial_cycle(client)
    sid = result["session_id"]
    assert client.put(f"/api/v1/sessions/{sid}/target", json={"school_id": "jlu"}).status_code == 200
    material = client.post(f"/api/v1/sessions/{sid}/materials", json={
        "title": "本地测试材料", "text": "供回看，尚未参与实验室诊断的测试资料。"})
    assert material.status_code == 200, material.text
    response = client.post(f"/api/v1/sessions/{sid}/plan/revise", json={"daily_minutes": 60})
    assert response.status_code == 200, response.text
    before = session_store.snapshot(sid)
    assert before["plan"]["daily_minutes"] == 60
    original_answers = json.loads(before["learning_state"]["blindspot"])["context"]["learning_state"]["core"]["answers"]
    def no_model(*args, **kwargs):
        raise AssertionError("自动重排整体计划不应请求模型")
    monkeypatch.setattr(llm, "generate_json", no_model)
    request_id = uuid.uuid4().hex
    if entry == "lab":
        result = act(client, "time", {"minutes": minutes}, expected=before["revision"], request_id=request_id)
        public = result["session"]
    else:
        response = client.patch(f"/api/v1/sessions/{sid}/profile", json={"daily_minutes": minutes})
        assert response.status_code == 200, response.text
        public = response.json()["data"]
    saved = session_store.snapshot(sid)
    assert public["plan"] == saved["plan"]
    assert saved["plan"]["daily_minutes"] == saved["profile"]["daily_minutes"] == minutes
    assert sum(item["minutes"] for item in saved["plan"]["items"]) <= minutes
    assert saved["plan"]["generated_by"] == "rules"
    if minutes < 10:
        assert saved["plan"]["items"] == []
        assert saved["plan"]["dropped"] and all("不足最小任务" in item["reason"] for item in saved["plan"]["dropped"])
    assert saved["selected_school_id"] == before["selected_school_id"]
    assert saved["materials"] == before["materials"]
    host = json.loads(saved["learning_state"]["blindspot"])["context"]
    assert host["learning_state"]["core"]["answers"] == original_answers
    assert host["profile"]["daily_minutes"] == minutes
    reread = state(client)
    assert reread["session"]["plan"]["daily_minutes"] == reread["plan"]["daily_minutes"] == minutes
    if entry == "lab":
        # 即便稍后已有新动作，重试原ID仍精确返回原事务结果而不回写旧状态。
        newer = act(client, "time", {"minutes": minutes + 1}, expected=reread["revision"])
        assert act(client, "time", {"minutes": minutes}, expected=before["revision"], request_id=request_id) == result
        assert state(client)["session"]["plan"] == newer["session"]["plan"]
        assert state(client)["session"]["profile"]["daily_minutes"] == minutes + 1


@pytest.mark.parametrize("change", ["target", "major", "target_major"])
def test_existing_main_plan_refreshes_scope_when_target_or_direction_changes(client, monkeypatch, change):
    initial = state(client)
    sid = initial["session_id"]
    assert client.patch(f"/api/v1/sessions/{sid}/profile", json={"major": "信息与通信工程"}).status_code == 200
    assert client.put(f"/api/v1/sessions/{sid}/target", json={"school_id": "jlu"}).status_code == 200
    assert client.post(f"/api/v1/sessions/{sid}/plan/revise", json={"daily_minutes": 60}).status_code == 200
    def no_model(*args, **kwargs):
        raise AssertionError("切换计划条件不应请求模型")
    monkeypatch.setattr(llm, "generate_json", no_model)
    if change == "target":
        response = client.put(f"/api/v1/sessions/{sid}/target", json={"school_id": "neu"})
        expected = {"selected_school_id": "neu", "major": "信息与通信工程", "target_major": None}
    else:
        response = client.patch(f"/api/v1/sessions/{sid}/profile", json={change: "控制科学与工程"})
        expected = {"selected_school_id": "jlu", "major": "控制科学与工程" if change == "major" else "信息与通信工程",
                    "target_major": "控制科学与工程" if change == "target_major" else None}
    assert response.status_code == 200, response.text
    plan = response.json()["data"]["plan"]
    assert plan["scope"] == expected
    assert plan == session_store.snapshot(sid)["plan"]
    assert plan["daily_minutes"] == 60
    assert sum(item["minutes"] for item in plan["items"]) <= 60


def test_repeating_current_time_repairs_legacy_stale_main_budget(client):
    result = initial_cycle(client)
    sid = result["session_id"]
    assert client.put(f"/api/v1/sessions/{sid}/target", json={"school_id": "jlu"}).status_code == 200
    assert client.post(f"/api/v1/sessions/{sid}/plan/revise", json={"daily_minutes": 60}).status_code == 200
    old_plan = session_store.snapshot(sid)["plan"]
    act(client, "time", {"minutes": 4})
    # 模拟升级前遗留：档案和宿主已经是4分钟，整体计划仍为60分钟。
    session_store.update_fields(sid, plan=old_plan)
    result = act(client, "time", {"minutes": 4})
    assert result["session"]["plan"]["daily_minutes"] == 4
    assert result["session"]["plan"]["items"] == []
    assert result["session"]["plan"] == session_store.snapshot(sid)["plan"]


@pytest.mark.parametrize("minutes", [1, 4, 9, 10, 11, 30, 60, 89, 90, 120, 1440])
def test_main_plan_never_overruns_budget_and_preserves_subject_priority(minutes):
    from backend.services import planner
    session = {"selected_school_id": "jlu", "profile": {"major": "信息与通信工程"},
               "materials": [{"material_id": "synthetic"}], "learning_state": {"ability_profile": [
                   {"topic": "MATH_CALC", "status": "weak", "basis": "measured", "evidence_count": 2}]}}
    rows = planner.build_plan(session, minutes)
    kept = [item for item in rows if item["kept"]]
    dropped = [item for item in rows if not item["kept"]]
    assert sum(item["minutes"] for item in kept) <= minutes
    assert all(item["minutes"] >= 10 and item["minutes"] % 5 == 0 for item in kept)
    if minutes < 10:
        assert not kept and dropped
    if kept and dropped:
        assert min(item["weight"] for item in kept) >= max(item["weight"] for item in dropped)
    if kept:
        assert "薄弱专项" in kept[0]["task"]
    material = next(item for item in rows if item["task"] == "回看已保存资料")
    assert "1 份资料" in material["reason"]
    assert "错题集中" not in material["reason"] and "高错题率" not in material["task"]


def _database_footprint(*session_ids):
    """比较拒绝请求前后的完整可信快照与原始幂等记录，不刷新页面。"""
    conn = session_store.get_conn()
    placeholders = ",".join("?" for _ in session_ids)
    return {
        "sessions": {sid: session_store.snapshot(sid) for sid in session_ids},
        "session_count": conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
        "requests": [tuple(row) for row in conn.execute(
            f"SELECT * FROM lab_requests WHERE session_id IN ({placeholders}) ORDER BY session_id, request_id",
            session_ids).fetchall()],
    }


def _replace_mock_cookie(client, session_id):
    client.cookies.clear()
    client.cookies.set("edu_session_mock", session_id, domain="testserver.local", path="/")


@pytest.mark.parametrize("action,params", [("start", {}), ("time", {"minutes": 4})])
def test_stale_page_cannot_mutate_same_revision_cookie_session(client, monkeypatch, action, params):
    page = state(client)
    a = page["session_id"]
    b = session_store.create_session()["session_id"]
    if action == "time":
        page = act(client, "start")
        asyncio.run(lab_service.execute(b, "start", {}, request_id=uuid.uuid4().hex, expected_revision=1))
    assert session_store.snapshot(a)["revision"] == session_store.snapshot(b)["revision"] == page["revision"]
    _replace_mock_cookie(client, b)
    before = _database_footprint(a, b)
    def no_execute(*args, **kwargs):
        raise AssertionError("会话不匹配必须先拒绝，不能进入模型、候选计算或缓存读取")
    monkeypatch.setattr(lab_service, "execute", no_execute)
    # helper沿用最后一次显式读取的页面A，不从Cookie猜sid，也不先GET刷新。
    response = act(client, action, params, expected=page["revision"], status=409)
    assert response["error"]["code"] == "CONFLICT"
    assert "刷新" in response["error"]["message"]
    assert _database_footprint(a, b) == before
    assert client._test_page_session_id == a


@pytest.mark.parametrize("action,params", [("start", {}), ("time", {"minutes": 4})])
def test_bridge_requires_top_level_session_id_before_any_execution(client, monkeypatch, action, params):
    page = state(client)
    if action == "time":
        page = act(client, "start")
    sid = page["session_id"]
    before = _database_footprint(sid)
    def no_execute(*args, **kwargs):
        raise AssertionError("缺少页面sid不能执行动作")
    monkeypatch.setattr(lab_service, "execute", no_execute)
    response = client.post("/api/action", json={"action": action, "params": params,
        "request_id": uuid.uuid4().hex, "expected_revision": page["revision"]})
    assert response.status_code == 400, response.text
    assert "session_id" in response.json()["error"]["message"]
    assert _database_footprint(sid) == before
    # 把sid放进动作params不能替代顶层绑定。
    response = client.post("/api/action", json={"action": action, "params": {**params, "session_id": sid}})
    assert response.status_code == 400, response.text
    assert _database_footprint(sid) == before


@pytest.mark.parametrize("action,params", [("start", {}), ("time", {"minutes": 4})])
def test_wrong_session_is_rejected_before_same_id_cached_result(client, monkeypatch, action, params):
    page = state(client)
    a = page["session_id"]
    b = session_store.create_session()["session_id"]
    if action == "time":
        page = act(client, "start")
        asyncio.run(lab_service.execute(b, "start", {}, request_id=uuid.uuid4().hex, expected_revision=1))
    rid = uuid.uuid4().hex
    cached = asyncio.run(lab_service.execute(b, action, params, request_id=rid, expected_revision=page["revision"]))
    assert cached["state"]["session_id"] == b
    _replace_mock_cookie(client, b)
    before = _database_footprint(a, b)
    def no_execute(*args, **kwargs):
        raise AssertionError("不匹配sid不能读取另一个会话的同名请求缓存")
    monkeypatch.setattr(lab_service, "execute", no_execute)
    response = act(client, action, params, expected=page["revision"], request_id=rid, status=409)
    assert response["error"]["code"] == "CONFLICT"
    assert _database_footprint(a, b) == before


@pytest.mark.parametrize("in_progress", [False, True])
@pytest.mark.parametrize("material_case", ["missing", "other_session"])
def test_v1_start_rejects_missing_or_foreign_material_before_adapter_without_any_write(
        client, monkeypatch, in_progress, material_case):
    page = state(client)
    sid = page["session_id"]
    other_sid = session_store.create_session()["session_id"]
    session_store.add_material(sid, title="当前会话资料", text="保留的本地测试正文。\n\n第二段仍应完整保留。")
    foreign = session_store.add_material(other_sid, title="另一会话的资料", text="仅供另一会话使用的测试正文。")
    if in_progress:
        started = act(client, "start")
        question = started["lab_state"]["question"]
        act(client, "submit", {"question_id": question["question_id"], "answer": "A"})
    before = _database_footprint(sid, other_sid)
    # 整库逐行比较也覆盖资料正文、段落、deleted/referenced标记与所有缓存记录。
    before_rows = tuple(session_store.get_conn().iterdump())
    def forbidden(*args, **kwargs):
        raise AssertionError("资料访问校验失败后不得获取adapter或调用模型")
    monkeypatch.setattr(lab_service, "get_adapter", forbidden)
    monkeypatch.setattr(llm, "generate_json", forbidden)
    material_id = foreign["material_id"] if material_case == "other_session" else "m_missing_" + uuid.uuid4().hex
    response = client.post(f"/api/v1/sessions/{sid}/lab/start", json={
        "material_id": material_id, "mode": "material", "topic": "测试资料",
        "request_id": uuid.uuid4().hex, "expected_revision": before["sessions"][sid]["revision"],
    })
    assert response.status_code == 404, response.text
    body = response.json()
    assert body["ok"] is False and body["data"] is None
    assert body["error"]["code"] == "MATERIAL_NOT_FOUND"
    assert body["error"]["retryable"] is False and body["request_id"]
    assert _database_footprint(sid, other_sid) == before
    assert tuple(session_store.get_conn().iterdump()) == before_rows
