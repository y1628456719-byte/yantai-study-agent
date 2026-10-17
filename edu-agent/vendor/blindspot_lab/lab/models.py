"""Explicit mock/replay/live adapters. No credential discovery or default network."""
import asyncio
import copy
import contextvars
import inspect
import json
import queue
import threading

from .errors import LabError


_CURRENT_WORK = contextvars.ContextVar('blindspot_model_work', default=None)


class ModelWorkTracker:
    """Optional host lifecycle: track real thread exit, including timed-out IO."""
    def __init__(self):
        self._lock = threading.Lock()
        self._count = 0
        self.finished = threading.Event()
        self.finished.set()

    def activate(self):
        return _CURRENT_WORK.set(self)

    def reset(self, token):
        _CURRENT_WORK.reset(token)

    def begin(self):
        with self._lock:
            self._count += 1
            self.finished.clear()

    def end(self):
        with self._lock:
            self._count -= 1
            if self._count == 0:
                self.finished.set()


def current_work_tracker():
    return _CURRENT_WORK.get()


OUTPUT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["feedback", "hypotheses", "explanation", "next_question_id"],
    "properties": {
        "feedback": {"type": "string", "minLength": 1, "maxLength": 600},
        "hypotheses": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "status", "reason", "evidence_ids"],
            "properties": {"id": {"type": "string"},
                "status": {"enum": ["tentative", "supported", "revised", "inconclusive"]},
                "reason": {"type": "string", "minLength": 1, "maxLength": 600},
                "evidence_ids": {"type": "array", "minItems": 1,
                                 "items": {"type": "string"}}}}},
        "explanation": {"anyOf": [{"type": "null"}, {"type": "object",
            "additionalProperties": False, "required": ["text", "evidence_ids"],
            "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 1500},
                "evidence_ids": {"type": "array", "minItems": 1,
                                 "items": {"type": "string"}}}}]},
        "next_question_id": {"type": ["string", "null"]}
    }
}


class GenerateJSONAdapter:
    """Sync host callable(messages, schema, timeout_seconds); no HTTP assumptions."""
    runtime_mode = "live"

    def __init__(self, generate_json):
        self.generate_json = generate_json

    def generate(self, request, timeout_seconds):
        return self.generate_json(copy.deepcopy(request["messages"]),
                                  copy.deepcopy(request.get("schema", OUTPUT_SCHEMA)), timeout_seconds)


class AsyncGenerateJSONAdapter(GenerateJSONAdapter):
    """Async host callable, executed in the isolated call thread's event loop."""

    def generate(self, request, timeout_seconds):
        async def call():
            return await asyncio.wait_for(self.generate_json(
                copy.deepcopy(request["messages"]), copy.deepcopy(OUTPUT_SCHEMA),
                timeout_seconds), timeout=timeout_seconds)
        return asyncio.run(call())


class MockModel:
    runtime_mode = "mock"

    def generate(self, request, timeout_seconds):
        return copy.deepcopy(request["mock_candidate"])


class ReplayModel:
    """A replay must match the saved request digest, never consume arbitrary output."""
    runtime_mode = "replay"

    def __init__(self, records):
        self.records = copy.deepcopy(records)

    def generate(self, request, timeout_seconds):
        if request["replay_key"] not in self.records:
            raise LabError("MODEL_UNAVAILABLE", "没有与本次证据匹配的回放记录。")
        raw = json.dumps(self.records[request["replay_key"]], ensure_ascii=False)
        for actual, saved in request["replay_bindings"].items():
            raw = raw.replace(json.dumps(saved), json.dumps(actual))
        return json.loads(raw)


def replay_record(request, candidate):
    """Capture a normalized candidate; callers own where records are saved."""
    raw = json.dumps(candidate, ensure_ascii=False)
    for actual, saved in request["replay_bindings"].items():
        raw = raw.replace(json.dumps(actual), json.dumps(saved))
    return request["replay_key"], json.loads(raw)


class ModelRunner:
    def __init__(self, model, timeout_seconds=30, max_output_chars=12000):
        if not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be in (0, 120]")
        self.model = model
        self.timeout = timeout_seconds
        self.max_output_chars = max_output_chars
        self.slots = threading.BoundedSemaphore(2)

    def call(self, request):
        # Bounded daemon workers: timeout never mutates state; transport must also
        # enforce its own deadline. A Python thread cannot terminate remote work.
        if self.model is None:
            raise LabError("MODEL_UNAVAILABLE", "尚未注入模型服务；本次状态未推进。")
        if not self.slots.acquire(blocking=False):
            raise LabError("MODEL_UNAVAILABLE", "模型请求仍在退出中，请稍后重试。")
        result = queue.Queue(maxsize=1)
        tracker = current_work_tracker()
        if tracker:
            tracker.begin()

        def work():
            try:
                outcome = (True, self.model.generate(copy.deepcopy(request), self.timeout))
            except Exception as exc:
                outcome = (False, exc)
            finally:
                self.slots.release()
                if tracker:
                    tracker.end()
            result.put(outcome)

        try:
            threading.Thread(target=work, daemon=True).start()
        except BaseException:
            self.slots.release()
            if tracker:
                tracker.end()
            raise
        try:
            ok, value = result.get(timeout=self.timeout)
        except queue.Empty:
            raise LabError("MODEL_UNAVAILABLE", "模型请求超时；本次状态未推进。") from None
        if not ok:
            # Do not leak provider exceptions (which can contain credentials).
            raise LabError("MODEL_UNAVAILABLE", "模型服务未完成请求；本次状态未推进。") from None
        if inspect.isawaitable(value):
            if inspect.iscoroutine(value):
                value.close()
            raise LabError("MODEL_OUTPUT_INVALID", "同步适配器返回协程，请使用异步适配器。")
        try:
            raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            if len(raw) > self.max_output_chars:
                raise LabError("MODEL_OUTPUT_INVALID", "模型输出超过长度限制。")
            value = json.loads(raw)
        except (ValueError, TypeError, RecursionError):
            raise LabError("MODEL_OUTPUT_INVALID", "模型输出不是有效JSON。") from None
        if not isinstance(value, dict):
            raise LabError("MODEL_OUTPUT_INVALID", "模型输出必须是JSON对象。")
        return value
