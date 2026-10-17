"""恢复默认只切换到新会话；使用TestClient和conftest的独立临时SQLite。"""
import uuid

import httpx
import pytest
import urllib.request
from fastapi.testclient import TestClient

from backend import config, llm
from backend.lab_adapter import reset_adapter
from backend.main import app
from backend.services import session_store


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "LAB_ADAPTER", "blindspot")
    monkeypatch.setattr(config, "LAB_BLINDSPOT_MODE", "mock")
    reset_adapter()
    with TestClient(app) as result:
        yield result
    reset_adapter()


def _block_network_and_models(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("恢复默认不得调用模型或网络")
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    monkeypatch.setattr(llm, "generate_json", forbidden)


def _rows(session_id):
    conn = session_store.get_conn()
    return {table: [tuple(row) for row in conn.execute(
        f"SELECT * FROM {table} WHERE session_id=? ORDER BY rowid", (session_id,)).fetchall()]
        for table in ("sessions", "materials", "material_chunks", "lab_requests")}


def test_reset_returns_new_empty_default_and_preserves_all_old_records(client, monkeypatch):
    first = client.get("/api/state").json()
    sid = first["session_id"]
    session_store.merge_profile(sid, {"daily_minutes": 25, "major": "图书情报"})
    session_store.set_target(sid, "jlu")
    session_store.add_material(sid, title="保留的测试资料", text="第一段正文。\n\n第二段正文。")
    started = client.post("/api/action", json={"session_id": sid, "action": "start",
        "expected_revision": session_store.snapshot(sid)["revision"], "request_id": uuid.uuid4().hex}).json()
    question = started["lab_state"]["question"]
    answered = client.post("/api/action", json={"session_id": sid, "action": "submit", "question_id": question["question_id"],
        "answer": "A", "expected_revision": started["revision"], "request_id": uuid.uuid4().hex})
    assert answered.status_code == 200, answered.text
    before = session_store.snapshot(sid)
    old_rows = _rows(sid)
    count = session_store.get_conn().execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    assert old_rows["materials"] and old_rows["material_chunks"] and old_rows["lab_requests"]
    _block_network_and_models(monkeypatch)
    response = client.post("/api/reset", json={"session_id": sid, "expected_revision": before["revision"]})
    assert response.status_code == 200, response.text
    result = response.json()
    new_sid = result["session_id"]
    assert new_sid != sid and result["revision"] == 1
    assert result["lab_state"] is None and result["plan"] is None
    assert result["session"]["profile"] == first["session"]["profile"] == {}
    assert result["session"]["learning_state"] is None
    assert result["session"]["materials"] == [] and result["session"]["selected_school_id"] is None
    assert result["session"]["plan"] is None and result["session"]["decisions"] == []
    assert client.cookies.get("edu_session_mock") == new_sid
    assert "HttpOnly" in response.headers["set-cookie"] and "SameSite=strict" in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/state").json() == result
    assert session_store.snapshot(sid) == before and _rows(sid) == old_rows
    assert session_store.get_conn().execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == count + 1


@pytest.mark.parametrize("conflict", ["session", "revision"])
def test_reset_rejects_wrong_session_or_stale_revision_without_writing(client, monkeypatch, conflict):
    current = client.get("/api/state").json()
    sid = current["session_id"]
    before = tuple(session_store.get_conn().iterdump())
    _block_network_and_models(monkeypatch)
    response = client.post("/api/reset", json={
        "session_id": "s_other_page" if conflict == "session" else sid,
        "expected_revision": current["revision"] if conflict == "session" else current["revision"] - 1})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "CONFLICT"
    assert "刷新" in response.json()["error"]["message"]
    assert "set-cookie" not in response.headers
    assert client.cookies.get("edu_session_mock") == sid
    assert tuple(session_store.get_conn().iterdump()) == before


def test_reset_switches_only_current_mode_cookie_without_network(client, monkeypatch):
    from vendor.blindspot_lab.lab import aiping
    mock = client.get("/api/state").json()
    mock_before = _rows(mock["session_id"])
    class FakeLiveClient:
        def __init__(self, *, model):
            self.model = model
        def generate_json(self, *args):
            raise AssertionError("恢复默认不得调用真实模型")
    monkeypatch.setattr(aiping, "AipingClient", FakeLiveClient)
    monkeypatch.setattr(config, "LAB_BLINDSPOT_MODE", "live")
    reset_adapter()
    _block_network_and_models(monkeypatch)
    live = client.get("/api/state").json()
    live_before = _rows(live["session_id"])
    response = client.post("/api/reset", json={"session_id": live["session_id"], "expected_revision": live["revision"]})
    assert response.status_code == 200, response.text
    new_live = response.json()
    assert new_live["runtime_mode"] == "live" and new_live["lab_state"] is None
    assert new_live["session_id"] not in (live["session_id"], mock["session_id"])
    assert client.cookies.get("edu_session_live") == new_live["session_id"]
    assert client.cookies.get("edu_session_mock") == mock["session_id"]
    assert _rows(live["session_id"]) == live_before and _rows(mock["session_id"]) == mock_before
    monkeypatch.setattr(config, "LAB_BLINDSPOT_MODE", "mock")
    reset_adapter()
    assert client.get("/api/state").json()["session_id"] == mock["session_id"]
