"""新旧 HTTP 入口共用的实验室动作与原子保存边界。"""
from __future__ import annotations

import copy
import asyncio
import hashlib
import json
import uuid

from ..core.errors import conflict, invalid_argument, lab_unavailable
from ..lab_adapter import LabError, build_context, get_adapter
from . import session_store

_active_requests = set()  # strong references until shielded work completes


def _retain_task(coroutine):
    task = asyncio.create_task(coroutine)
    _active_requests.add(task)
    def finished(done):
        _active_requests.discard(done)
        if not done.cancelled():
            done.exception()
    task.add_done_callback(finished)
    return task


async def _release_after_work(session_id, owner, tracker):
    # Outer/model timeouts only stop waiting, not synchronous IO. Keep both the
    # reservation and this task alive until the actual worker threads have exited.
    while not tracker.finished.is_set():
        if not session_store.renew_lab_request(session_id, owner):
            return
        await asyncio.to_thread(tracker.finished.wait, 10)
    session_store.release_lab_request(session_id, owner)

PARAMS = {"start": (), "submit": ("question_id", "answer"), "feedback": ("feedback",),
          "time": ("minutes",), "continue": (), "stop": ()}


def raise_lab(exc):
    code = (getattr(exc, "code", None) or "").upper()
    if code in {"STALE_REVISION", "REQUEST_CONFLICT"}:
        raise conflict(f"{exc}（请读取服务端状态后重试）") from exc
    if code in {"INVALID_INPUT", "MATERIAL_UNSUPPORTED", "CONTINUE_UNAVAILABLE", "TIME_BUDGET_EXHAUSTED",
                "SESSION_STOPPED", "QUESTION_MISMATCH", "MATERIAL_CHANGED"}:
        raise invalid_argument(str(exc)) from exc
    raise lab_unavailable(str(exc)) from exc


def _context(session, *, material_id=None, topic=None, diagnosis_mode="self_assessment"):
    material = None
    if material_id:
        row = session_store.get_material_row(session["session_id"], material_id)
        material = {"material_id": row["material_id"], "title": row["title"], "text": row["content"],
                    "subject": row["subject"], "school_id": row["school_id"],
                    "paragraphs": [p["text"] for p in session_store.material_paragraphs(session["session_id"], material_id)]}
    return build_context(session, material=material, diagnosis_mode=diagnosis_mode, topic=topic)


async def public_state(session_id: str) -> dict:
    session = session_store.snapshot(session_id)
    learning = session.get("learning_state") or {}
    mode = learning.get("runtime_mode")
    lab_state = plan = None
    adapter = None
    if learning.get("blindspot"):
        try:
            adapter = get_adapter()
            read = await adapter.public_read(_context(session))
            lab_state, plan, mode = read["lab_state"], read["plan"], read["runtime_mode"]
        except LabError as exc:
            raise_lab(exc)
    else:
        from .. import config
        mode = config.LAB_BLINDSPOT_MODE if config.LAB_ADAPTER == "blindspot" else "mock"
        if config.LAB_ADAPTER == "blindspot":
            try:
                adapter = get_adapter()
            except LabError as exc:
                raise_lab(exc)
    from .journey import public_summary
    return {"revision": session["revision"], "runtime_mode": mode,
            "model": getattr(adapter, "model_name", None) if mode == "live" else None,
            "lab_state": lab_state, "plan": plan, "session_id": session_id,
            "session": session_store.public_session(session),
            "journey": public_summary(session)}


async def execute(session_id: str, action: str, params: dict, *, request_id=None,
                  expected_revision=None, material_id=None, topic=None, diagnosis_mode=None) -> dict:
    """返回统一结果，重复 ID 先读取数据库原结果，不再批改或调用模型。"""
    if action not in PARAMS or set(params) != set(PARAMS[action]):
        raise invalid_argument("动作或参数无效")
    if request_id is not None and (not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128):
        raise invalid_argument("request_id 须为 1~128 字符")
    if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
        raise invalid_argument("expected_revision 须为非负整数")
    request_id = request_id or uuid.uuid4().hex
    try:
        fingerprint = hashlib.sha256(json.dumps(
            [action, params, expected_revision, material_id, topic, diagnosis_mode],
            ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
    except (TypeError, ValueError):
        raise invalid_argument("动作参数必须是有效 JSON 值。") from None
    cached = session_store.cached_lab_request(session_id, request_id, fingerprint)
    if cached is not None:
        return cached
    owner, prior = session_store.reserve_lab_request(session_id, request_id, fingerprint)
    if prior is not None:
        return prior

    async def owned():
        release = True
        lifecycle = {}
        try:
            return await _execute_owned(session_id, action, params, request_id=request_id,
                fingerprint=fingerprint, expected_revision=expected_revision, material_id=material_id,
                topic=topic, diagnosis_mode=diagnosis_mode, claim_owner=owner, lifecycle=lifecycle)
        except LabError as exc:
            raise_lab(exc)
        except asyncio.CancelledError:
            # Shutdown/cancellation cannot prove a background transport has exited.
            # Keep its reservation until expiry rather than allowing duplicate work.
            release = False
            raise
        finally:
            tracker, token = lifecycle.get('tracker'), lifecycle.get('token')
            if tracker is not None:
                tracker.reset(token)
            if release:
                if tracker is not None and not tracker.finished.is_set():
                    _retain_task(_release_after_work(session_id, owner, tracker))
                else:
                    session_store.release_lab_request(session_id, owner)

    task = _retain_task(owned())
    return await asyncio.shield(task)


async def _execute_owned(session_id, action, params, *, request_id, fingerprint,
                         expected_revision, material_id, topic, diagnosis_mode, claim_owner, lifecycle):
    session = session_store.snapshot(session_id)
    base = session["revision"]
    if expected_revision is not None and expected_revision != base:
        raise conflict("页面状态已更新，请读取最新状态后重试。")
    meta = (session.get("learning_state") or {}).get("lab") or {}
    if action != "start":
        material_id, topic = meta.get("material_id"), meta.get("topic")
    diagnosis_mode = diagnosis_mode or meta.get("diagnosis_mode") or "self_assessment"
    if action == "submit":
        if not isinstance(params["question_id"], str) or not isinstance(params["answer"], str):
            raise invalid_argument("submit 需要 question_id 与 answer")
        pending = meta.get("current_question_id")
        if not pending or params["question_id"] != pending:
            raise invalid_argument("question_id 与当前待答题不匹配，请刷新。")
    if action == "time" and (type(params["minutes"]) is not int or not 1 <= params["minutes"] <= 1440):
        raise invalid_argument("minutes 须为 1~1440 的整数")
    if action == "feedback" and (not isinstance(params["feedback"], str) or
            params["feedback"] not in {"unclear", "too_easy", "guessed", "new_example"}):
        raise invalid_argument("feedback 只能是 unclear / too_easy / guessed / new_example")
    context = _context(session, material_id=material_id, topic=topic, diagnosis_mode=diagnosis_mode)
    context["request_id"] = request_id
    try:
        adapter = get_adapter()
        if hasattr(adapter, 'create_work_tracker'):
            tracker = adapter.create_work_tracker()
            lifecycle.update(tracker=tracker, token=tracker.activate())
        if hasattr(adapter, "act"):
            result = await adapter.act(context, action, **params)
        elif action == "start":
            result = await adapter.start(context)
        elif action == "submit":
            result = await adapter.answer(context, **params)
        else:
            raise invalid_argument("当前适配器不支持自适应动作，请启用 LAB_ADAPTER=blindspot。")
    except LabError as exc:
        raise_lab(exc)
    contract = result["lab_state"]
    merged = copy.deepcopy(session.get("learning_state") or {})
    if result.get("learning_state") is not None:
        merged.update(result["learning_state"])
    lab = dict(merged.get("lab") or {})
    qid = (contract.get("question") or {}).get("question_id")
    lab.update(material_id=material_id, topic=topic, diagnosis_mode=diagnosis_mode,
               stage=contract.get("stage"), current_question_id=qid,
               next_action=contract.get("next_action"), is_demo=contract.get("is_demo"),
               reason=contract.get("reason"))
    if qid and qid not in (lab.get("asked") or []):
        lab["asked"] = [*(lab.get("asked") or []), qid]
    merged["lab"] = lab
    profile = dict(session.get("profile") or {})
    if action == "time":
        profile["daily_minutes"] = params["minutes"]
    if isinstance(merged.get("journey"), dict):
        from .journey import record_feedback, record_public_answers, public_summary
        journey_state = copy.deepcopy(merged["journey"])
        public_lab = result.get("public_lab_state", contract)
        record_public_answers(journey_state, public_lab)
        if action == "feedback":
            record_feedback(journey_state, params["feedback"], request_id)
        summary = public_summary({**session, "profile": profile,
                                  "learning_state": {**merged, "journey": journey_state}})
        journey_state["summary"] = summary
        merged["journey"] = journey_state
    fields = {"learning_state": merged, "profile": profile}
    public_lab = result.get("public_lab_state", contract)
    mode = public_lab.get("runtime_mode", "mock")
    candidate = {**session, **fields, "revision": base + 1}
    data = {"contract": contract, "state": {
        "revision": base + 1, "runtime_mode": mode,
        "model": getattr(adapter, "model_name", None) if mode == "live" else None,
        "lab_state": public_lab, "plan": result.get("plan"),
        "session_id": session_id, "session": session_store.public_session(candidate)}}
    from .journey import public_summary
    data["state"]["journey"] = public_summary(candidate)
    return session_store.commit_lab_request(session_id, request_id, fingerprint, base, fields, data, claim_owner=claim_owner)
