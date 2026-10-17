"""Journey bridge regressions; all requests use the temporary SQLite fixture."""
import uuid

import pytest
from fastapi.testclient import TestClient

from backend import config
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


def _state(client):
    response = client.get("/api/state")
    assert response.status_code == 200, response.text
    return response.json()


def _prepare(client, state, *, goal="复习条件推理", use_demo=False, request_id=None,
             endpoint="/api/prepare", session_id=None, expected_revision=None):
    payload = {
        "session_id": session_id or state["session_id"],
        "expected_revision": state["revision"] if expected_revision is None else expected_revision,
        "request_id": request_id or uuid.uuid4().hex,
        "goal": goal,
        "use_demo": use_demo,
    }
    response = client.post(endpoint, json=payload)
    return response, payload


def _action(client, state, action, params=None):
    response = client.post("/api/action", json={
        "session_id": state["session_id"],
        "action": action,
        "params": params or {},
        "expected_revision": state["revision"],
        "request_id": uuid.uuid4().hex,
    })
    assert response.status_code == 200, response.text
    return response.json()


def _start_and_answer_three(client, page, final_choice):
    result = _action(client, page, "start")
    question = result["lab_state"]["question"]
    result = _action(client, result, "submit", {"question_id": question["question_id"], "answer": "A"})
    question = result["lab_state"]["question"]
    assert question["question_id"] == "q_distinguish_01"
    result = _action(client, result, "submit", {"question_id": question["question_id"], "answer": "A"})
    question = result["lab_state"]["question"]
    assert question["question_id"] == "q_verify_reading_01"
    return _action(client, result, "submit", {"question_id": question["question_id"], "answer": final_choice})


def test_get_state_does_not_create_journey_or_seed_history(client):
    first = _state(client)
    assert first["journey"] is None
    assert first["lab_state"] is None
    sid = first["session_id"]
    before = session_store.snapshot(sid)
    second = _state(client)
    assert second["journey"] is None
    assert second["session"]["learning_state"] is None
    assert session_store.snapshot(sid) == before


@pytest.mark.parametrize('original_is_demo', [False, True])
def test_demo_history_is_explicit_and_publicly_labeled(client, original_is_demo):
    if original_is_demo:
        original_sid = session_store.create_session(is_demo=True)['session_id']
        client.cookies.set('edu_session_mock', original_sid, domain='testserver.local', path='/')
    page = _state(client)
    original = session_store.snapshot(page["session_id"])
    response, _payload = _prepare(client, page, use_demo=True)
    assert response.status_code == 200, response.text
    result = response.json()
    summary = result["journey"]
    assert result["session_id"] != page["session_id"]
    assert summary["can_return"] is True
    assert "return_session_id" not in summary
    assert result["lab_state"] is None
    assert summary["seed_count"] == 24
    assert summary["actual_count"] == 0
    assert summary["source_label"].startswith("预置演示档案")
    assert len(summary["records"]) == 24
    assert {row["origin"] for row in summary["records"]} == {"preset"}
    assert {row["track"] for row in summary["records"]} == {"direction", "reading", "transfer"}
    assert all(set(row) == {"track", "choice", "grade", "origin"} for row in summary["records"])
    assert sum(row["minutes"] for row in summary["plan"]) <= summary["daily_minutes"]
    assert "daily_minutes" not in result["session"]["profile"]
    # Loading an example creates a separate SID and leaves every source field,
    # including revision and any existing lab snapshot, untouched.
    assert session_store.snapshot(page["session_id"]) == original
    stored = session_store.snapshot(result["session_id"])["learning_state"]
    assert len(stored["journey"]["records"]) == 24
    assert stored["journey"]["return_session_id"] == page["session_id"]
    assert "return_session_id" not in result["session"]["learning_state"]


def test_prepare_replay_and_later_demo_load_do_not_duplicate_records(client):
    page = _state(client)
    response, payload = _prepare(client, page, use_demo=True,
                                 endpoint="/api/journey/prepare", request_id="journey-once")
    assert response.status_code == 200, response.text
    first = response.json()
    replay = client.post("/api/journey/prepare", json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json() == first
    second_state = _state(client)
    later, _ = _prepare(client, second_state, use_demo=True,
                        goal="多步条件链，每天二十分钟", request_id="journey-later")
    assert later.status_code == 200, later.text
    assert later.json()["session_id"] == first["session_id"]
    assert later.json()["journey"]["seed_count"] == 24
    assert later.json()["journey"]["goal"] == "多步条件链，每天二十分钟"
    assert later.json()["journey"]["daily_minutes"] == 20
    assert later.json()["journey"]["focus"]["track"] == "transfer"
    assert len(later.json()["journey"]["records"]) == 24
    source_rows = session_store.get_conn().execute(
        "SELECT COUNT(*) FROM lab_requests WHERE session_id=?", (page["session_id"],)
    ).fetchone()[0]
    demo_rows = session_store.get_conn().execute(
        "SELECT COUNT(*) FROM lab_requests WHERE session_id=?", (first["session_id"],)
    ).fetchone()[0]
    assert source_rows == 1
    assert demo_rows == 1


def test_cookie_session_and_revision_conflicts_write_nothing(client):
    page = _state(client)
    original_sid = page["session_id"]
    other = session_store.create_session()
    before_original = session_store.snapshot(original_sid)
    before_other = session_store.snapshot(other["session_id"])
    before_requests = session_store.get_conn().execute("SELECT COUNT(*) FROM lab_requests").fetchone()[0]
    before_sessions = session_store.get_conn().execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

    wrong_sid, payload = _prepare(client, page, use_demo=True, session_id=other["session_id"])
    assert wrong_sid.status_code == 409
    assert session_store.snapshot(original_sid) == before_original
    assert session_store.snapshot(other["session_id"]) == before_other
    assert session_store.get_conn().execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == before_sessions

    stale, _ = _prepare(client, page, use_demo=True, expected_revision=page["revision"] - 1)
    assert stale.status_code == 409
    assert session_store.snapshot(original_sid) == before_original
    assert session_store.snapshot(other["session_id"]) == before_other
    assert session_store.get_conn().execute("SELECT COUNT(*) FROM lab_requests").fetchone()[0] == before_requests
    assert session_store.get_conn().execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == before_sessions


def test_demo_return_is_cookie_bound_idempotent_and_does_not_write_source(client):
    page = _state(client)
    source_after_start = _action(client, page, "start")
    source_sid = page["session_id"]
    source_snapshot = session_store.snapshot(source_sid)
    prepared, _ = _prepare(client, source_after_start, use_demo=True)
    assert prepared.status_code == 200, prepared.text
    demo = prepared.json()
    demo_sid = demo["session_id"]
    assert demo_sid != source_sid
    assert session_store.snapshot(source_sid) == source_snapshot

    # A different same-mode cookie cannot use the demo's private origin link.
    other_sid = session_store.create_session()["session_id"]
    other_snapshot = session_store.snapshot(other_sid)
    client.cookies.set("edu_session_mock", other_sid, domain="testserver.local", path="/")
    return_payload = {
        "session_id": demo_sid,
        "expected_revision": demo["revision"],
        "request_id": "return-bound-to-demo",
    }
    denied = client.post("/api/journey/return", json=return_payload)
    assert denied.status_code == 409
    assert session_store.snapshot(source_sid) == source_snapshot
    assert session_store.snapshot(other_sid) == other_snapshot

    client.cookies.set("edu_session_mock", demo_sid, domain="testserver.local", path="/")
    first = client.post("/api/journey/return", json=return_payload)
    assert first.status_code == 200, first.text
    returned = first.json()
    assert returned["session_id"] == source_sid
    assert returned["revision"] == source_snapshot["revision"]
    assert returned["lab_state"]["question"]["question_id"] == "q_initial_01"
    assert session_store.snapshot(source_sid) == source_snapshot
    assert client.cookies.get("edu_session_mock") == source_sid

    # Replaying after the cookie switched back returns the exact cached state.
    replay = client.post("/api/journey/return", json=return_payload)
    assert replay.status_code == 200, replay.text
    assert replay.json() == returned
    assert session_store.snapshot(source_sid) == source_snapshot


def test_demo_return_rejects_stale_demo_revision_and_returns_latest_origin(client):
    page = _state(client)
    prepared, _ = _prepare(client, page, use_demo=True)
    demo = prepared.json()
    demo_started = _action(client, demo, "start")
    demo_snapshot = session_store.snapshot(demo["session_id"])
    source_sid = page["session_id"]

    stale = client.post("/api/journey/return", json={
        "session_id": demo["session_id"],
        "expected_revision": demo["revision"],
        "request_id": "stale-return",
    })
    assert stale.status_code == 409
    assert session_store.snapshot(demo["session_id"]) == demo_snapshot

    # Source changes are not overwritten: return reads and presents its newest state.
    latest_source = session_store.update_fields(
        source_sid, profile={"daily_minutes": 35},
        expected_revision=page["revision"],
    )
    source_before_return = session_store.snapshot(source_sid)
    payload = {
        "session_id": demo["session_id"],
        "expected_revision": demo_started["revision"],
        "request_id": "return-latest-origin",
    }
    response = client.post("/api/journey/return", json=payload)
    assert response.status_code == 200, response.text
    returned = response.json()
    assert returned["session_id"] == source_sid
    assert returned["revision"] == latest_source["revision"]
    assert returned["session"]["profile"]["daily_minutes"] == 35
    assert session_store.snapshot(source_sid) == source_before_return


def test_actual_answer_is_counted_once_and_self_report_does_not_change_grade(client):
    page = _state(client)
    prepared, _ = _prepare(client, page)
    assert prepared.status_code == 200, prepared.text
    page = prepared.json()
    started = _action(client, page, "start")
    question = started["lab_state"]["question"]
    payload = {
        "session_id": started["session_id"], "action": "submit",
        "params": {"question_id": question["question_id"], "answer": "B"},
        "expected_revision": started["revision"], "request_id": "one-real-answer",
    }
    answered = client.post("/api/action", json=payload)
    assert answered.status_code == 200, answered.text
    first = answered.json()
    assert first["journey"]["actual_count"] == 1
    assert first["journey"]["records"][0]["choice"] == "B"
    assert first["journey"]["records"][0]["grade"] == "correct"
    replay = client.post("/api/action", json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json() == first
    assert replay.json()["journey"]["actual_count"] == 1

    feedback = _action(client, first, "feedback", {"feedback": "guessed"})
    assert feedback["journey"]["actual_count"] == 1
    direction = next(item for item in feedback["journey"]["skills"] if item["id"] == "direction")
    assert direction["status"] == "needs_check"
    assert feedback["journey"]["feedback_text"] == "我是猜的"
    assert "不会把答对等同于掌握" in feedback["journey"]["next_step"]
    assert feedback["journey"]["records"][0]["grade"] == "correct"


def test_prepare_preserves_an_existing_in_progress_lab_snapshot(client):
    page = _state(client)
    started = _action(client, page, "start")
    before = session_store.snapshot(page["session_id"])
    snapshot = before["learning_state"]["blindspot"]
    response, _ = _prepare(client, started)
    assert response.status_code == 200, response.text
    result = response.json()
    after = session_store.snapshot(page["session_id"])
    assert after["learning_state"]["blindspot"] == snapshot
    assert result["lab_state"]["question"]["question_id"] == "q_initial_01"
    assert result["journey"]["actual_count"] == 0


def test_prepare_budget_change_returns_refreshed_lab_without_losing_progress(client):
    page = _state(client)
    started = _action(client, page, "start")
    first_question = started["lab_state"]["question"]["question_id"]
    answered = _action(client, started, "submit", {
        "question_id": first_question, "answer": "A",
    })
    pending_before = answered["lab_state"]["question"]["question_id"]
    assert pending_before == "q_distinguish_01"
    assert any(item.get("type") == "answer" and item.get("question_id") == first_question
               for item in answered["lab_state"]["evidence"])

    prepared_response, payload = _prepare(client, answered, goal="每天练习20分钟")
    assert prepared_response.status_code == 200, prepared_response.text
    prepared = prepared_response.json()
    assert prepared["journey"]["daily_minutes"] == 20
    assert prepared["session"]["profile"]["daily_minutes"] == 20
    assert prepared["lab_state"]["cycle"]["budget_minutes"] == 20
    assert prepared["lab_state"]["question"]["question_id"] == pending_before
    assert any(item.get("type") == "answer" and item.get("question_id") == first_question
               for item in prepared["lab_state"]["evidence"])

    refreshed = _state(client)
    assert prepared["lab_state"] == refreshed["lab_state"]
    assert prepared["plan"] == refreshed["plan"]
    assert refreshed["lab_state"]["cycle"]["budget_minutes"] == 20
    assert sum(item["minutes"] for item in prepared["plan"]["tasks"]) <= 20

    # The response cache stores the same synchronized state as the commit.
    revision = session_store.snapshot(page["session_id"])["revision"]
    replay = client.post("/api/prepare", json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json() == prepared
    assert session_store.snapshot(page["session_id"])["revision"] == revision


def test_different_actual_histories_change_plan_priority_and_next_track(client):
    first_page = _state(client)
    first_prepared, _ = _prepare(client, first_page)
    first = _start_and_answer_three(client, first_prepared.json(), "A")
    assert first["journey"]["focus"]["track"] == "reading"
    assert first["journey"]["plan"][0]["track"] == "reading"
    assert next(item["minutes"] for item in first["journey"]["plan"]
                if item["track"] == "direction") < first["journey"]["plan"][0]["minutes"]
    first_next = _action(client, first, "continue")
    assert first_next["lab_state"]["question"]["question_id"] == "q_adapt_read_02"

    second_sid = session_store.create_session()["session_id"]
    client.cookies.clear()
    client.cookies.set("edu_session_mock", second_sid, domain="testserver.local", path="/")
    second_page = _state(client)
    second_prepared, _ = _prepare(client, second_page)
    second = _start_and_answer_three(client, second_prepared.json(), "B")
    assert second["journey"]["focus"]["track"] == "direction"
    assert second["journey"]["plan"][0]["track"] == "direction"
    assert next(item["minutes"] for item in second["journey"]["plan"]
                if item["track"] == "reading") < second["journey"]["plan"][0]["minutes"]
    second_next = _action(client, second, "continue")
    assert second_next["lab_state"]["question"]["question_id"] == "q_adapt_direction_02"


@pytest.mark.parametrize(("goal", "track"), [
    ("我想练多步条件链", "transfer"),
    ("重点理解只有与必要条件", "reading"),
    ("练条件方向和逆否", "direction"),
])
def test_explicit_goal_intent_changes_focus_and_plan_without_claiming_mastery(client, goal, track):
    page = _state(client)
    prepared, _ = _prepare(client, page, goal=goal)
    assert prepared.status_code == 200, prepared.text
    summary = prepared.json()["journey"]
    assert summary["focus"]["track"] == track
    assert summary["plan"][0]["track"] == track
    assert summary["actual_count"] == 0
    assert all(item["total"] == 0 and item["status"] == "pending"
               for item in summary["skills"])
    assert "自报意图" in summary["focus"]["reason"]
    assert "不是掌握证据" in summary["focus"]["reason"]


def test_explicit_transfer_goal_overrides_historical_weakness_for_next_question(client):
    page = _state(client)
    prepared, _ = _prepare(client, page, goal="多步条件链")
    assert prepared.status_code == 200, prepared.text
    result = _start_and_answer_three(client, prepared.json(), "A")

    # The first question was answered incorrectly, but the explicit target is
    # a self-reported practice intent and therefore has precedence over history.
    assert result["journey"]["focus"]["track"] == "transfer"
    assert result["journey"]["plan"][0]["track"] == "transfer"
    assert result["journey"]["actual_count"] == 3
    continued = _action(client, result, "continue")
    assert continued["lab_state"]["question"]["question_id"] == "q_adapt_transfer_02"


def test_generic_sufficient_and_necessary_topic_does_not_override_demo_weakness(client):
    page = _state(client)
    prepared, _ = _prepare(client, page,
                           goal="排查充分与必要条件的理解盲点", use_demo=True)
    assert prepared.status_code == 200, prepared.text
    summary = prepared.json()["journey"]
    assert summary["seed_count"] == 24
    assert summary["focus"]["track"] == "transfer"
    assert summary["plan"][0]["track"] == "transfer"
    assert "答错 5 / 8 条" in summary["focus"]["reason"]


def test_guessed_feedback_overrides_history_for_next_track(client):
    page = _state(client)
    prepared, _ = _prepare(client, page)
    result = _start_and_answer_three(client, prepared.json(), "B")
    result = _action(client, result, "continue")
    assert result["lab_state"]["question"]["question_id"] == "q_adapt_direction_02"
    question = result["lab_state"]["question"]
    result = _action(client, result, "submit", {"question_id": question["question_id"], "answer": "B"})
    assert result["journey"]["focus"]["track"] == "direction"
    result = _action(client, result, "feedback", {"feedback": "unclear"})
    # The same journey summary drives question selection; the explanation
    # strategy still responds to the explicit feedback.
    assert result["journey"]["focus"]["track"] == "direction"
    assert result["lab_state"]["adaptation"]["strategy"] == "steps"
    continued = _action(client, result, "continue")
    assert continued["lab_state"]["question"]["question_id"] == "q_adapt_direction_03"
    assert continued["journey"]["feedback_text"] == "我还没理解"


def test_too_easy_feedback_changes_focus_and_plan_priority(client):
    page = _state(client)
    prepared, _ = _prepare(client, page)
    result = _action(client, prepared.json(), "start")
    question = result["lab_state"]["question"]
    result = _action(client, result, "submit", {"question_id": question["question_id"], "answer": "B"})
    result = _action(client, result, "feedback", {"feedback": "too_easy"})
    assert result["journey"]["focus"]["track"] == "transfer"
    assert result["journey"]["plan"][0]["track"] == "transfer"
    assert result["journey"]["feedback_text"] == "这组题太简单"


def test_explicit_minutes_update_profile_and_plan_stays_within_budget(client):
    page = _state(client)
    response, _ = _prepare(client, page, goal="每天复习 1 小时", use_demo=True)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["journey"]["daily_minutes"] == 60
    assert result["session"]["profile"]["daily_minutes"] == 60
    assert sum(item["minutes"] for item in result["journey"]["plan"]) <= 60
    assert "journey" not in result["session"]["learning_state"]
    result = _action(client, result, "start")
    result = _action(client, result, "time", {"minutes": 13})
    assert result["journey"]["daily_minutes"] == 13
    assert result["session"]["profile"]["daily_minutes"] == 13
    assert sum(item["minutes"] for item in result["journey"]["plan"]) <= 13


@pytest.mark.parametrize(("goal", "minutes"), [
    ("每天十五分钟", 15),
    ("每天二十分钟", 20),
    ("每天一百二十分钟", 120),
    ("每天一千零二十分钟", 1020),
    ("每天半小时", 30),
    ("每天一个半小时", 90),
    ("每天1个半小时", 90),
    ("每天一小时半", 90),
    ("每天1.5小时", 90),
    ("每天4.1小时", 246),
])
@pytest.mark.parametrize("active", [False, True], ids=["fresh", "active-lab"])
def test_chinese_daily_budget_is_parsed_persisted_and_reflected_in_get(client, goal, minutes, active):
    page = _state(client)
    expected_pending = None
    previous_answer = None
    if active:
        started = _action(client, page, "start")
        first = started["lab_state"]["question"]
        answered = _action(client, started, "submit", {
            "question_id": first["question_id"], "answer": "A",
        })
        expected_pending = answered["lab_state"]["question"]["question_id"]
        previous_answer = first["question_id"]
        page = answered

    response, _payload = _prepare(client, page, goal=goal)
    assert response.status_code == 200, response.text
    prepared = response.json()
    assert prepared["journey"]["daily_minutes"] == minutes
    assert prepared["session"]["profile"]["daily_minutes"] == minutes
    assert sum(item["minutes"] for item in prepared["journey"]["plan"]) <= minutes
    assert session_store.snapshot(page["session_id"])["profile"]["daily_minutes"] == minutes
    if active:
        assert prepared["lab_state"]["cycle"]["budget_minutes"] == minutes
        assert prepared["lab_state"]["question"]["question_id"] == expected_pending
        assert any(item.get("type") == "answer" and item.get("question_id") == previous_answer
                   for item in prepared["lab_state"]["evidence"])

    refreshed = _state(client)
    assert refreshed["journey"]["daily_minutes"] == minutes
    assert refreshed["session"]["profile"]["daily_minutes"] == minutes
    assert refreshed["lab_state"] == prepared["lab_state"]
    assert refreshed["plan"] == prepared["plan"]
    if active:
        assert refreshed["lab_state"]["cycle"]["budget_minutes"] == minutes


@pytest.mark.parametrize("goal", [
    "每天二十多分钟",
    "每天一个半小时左右",
    "每天20分钟到30分钟",
    "每天十五分钟或二十分钟",
    "每天二三十分钟",
])
def test_unsupported_or_ambiguous_budget_is_rejected_without_writes(client, goal):
    page = _state(client)
    before = session_store.snapshot(page["session_id"])
    before_sessions = session_store.get_conn().execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    before_requests = session_store.get_conn().execute("SELECT COUNT(*) FROM lab_requests").fetchone()[0]
    response, _payload = _prepare(client, page, goal=goal)
    assert response.status_code == 400, response.text
    assert session_store.snapshot(page["session_id"]) == before
    assert session_store.get_conn().execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == before_sessions
    assert session_store.get_conn().execute("SELECT COUNT(*) FROM lab_requests").fetchone()[0] == before_requests


@pytest.mark.parametrize('goal', ['每天-5分钟', '每天1.5.5小时', '每天.5小时', '每天- 5分钟',
                                 '每天1e3分钟', '每天二 十分钟', '每天负二十分钟'])
@pytest.mark.parametrize('active', [False, True])
@pytest.mark.parametrize('use_demo', [False, True])
def test_malformed_budget_never_changes_progress_or_creates_a_demo(client, goal, active, use_demo):
    page = _state(client)
    if active:
        page = _action(client, page, 'start')
        question = page['lab_state']['question']
        page = _action(client, page, 'submit', {'question_id': question['question_id'], 'answer': 'A'})
    before = session_store.snapshot(page['session_id'])
    conn = session_store.get_conn()
    sessions = conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]
    requests = conn.execute('SELECT COUNT(*) FROM lab_requests').fetchone()[0]
    response, _ = _prepare(client, page, goal=goal, use_demo=use_demo)
    assert response.status_code == 400, response.text
    assert session_store.snapshot(page['session_id']) == before
    assert conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == sessions
    assert conn.execute('SELECT COUNT(*) FROM lab_requests').fetchone()[0] == requests
