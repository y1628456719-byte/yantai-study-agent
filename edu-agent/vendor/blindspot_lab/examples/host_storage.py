"""Reference test store only: not a production database or a second backend."""
import copy
import json
import threading

from ..lab.content import digest
from ..lab.errors import LabError


class MemoryHost:
    def __init__(self, context):
        self.context = copy.deepcopy(context)
        self.requests = {}
        self.lock = threading.Lock()

    def read(self):
        with self.lock:
            return copy.deepcopy(self.context)

    def export_json(self):
        with self.lock:
            return json.dumps({"context": self.context, "requests": self.requests}, ensure_ascii=False)

    @classmethod
    def restore_json(cls, raw):
        obj = json.loads(raw)
        store = cls(obj["context"])
        store.requests = obj["requests"]
        return store

    def execute(self, lab, action, request_id, expected_revision, question_id=None, answer=None):
        if action not in ("start", "submit"):
            raise LabError("INVALID_INPUT", "未知模块动作。")
        fingerprint = digest([action, expected_revision, question_id, answer])

        def cached():
            entry = self.requests.get(request_id)
            if entry and entry["fingerprint"] != fingerprint:
                raise LabError("REQUEST_CONFLICT", "同一request_id携带了不同请求。")
            return copy.deepcopy(entry["result"]) if entry else None

        with self.lock:
            existing = cached()
            if existing:
                return existing
            if expected_revision != self.context["revision"]:
                raise LabError("STALE_REVISION", "主后端原子版本检查失败。")
            context = copy.deepcopy(self.context)
            context["request_id"] = request_id
        result = (lab.start_lab(context) if action == "start" else
                  lab.submit_answer(context, question_id, answer))
        with self.lock:
            existing = cached()
            if existing:
                return existing
            if result["base_revision"] != self.context["revision"]:
                raise LabError("STALE_REVISION", "计算后原子保存冲突，候选未保存。")
            if result["state_changed"]:
                self.context["learning_state"] = copy.deepcopy(result["learning_state"])
                self.context["revision"] = result["next_revision"]
            self.requests[request_id] = {"fingerprint": fingerprint, "result": copy.deepcopy(result)}
            return result

    def update_daily_minutes(self, minutes, expected_revision):
        if type(minutes) is not int or not 1 <= minutes <= 1440:
            raise LabError("INVALID_INPUT", "每日时间无效。")
        with self.lock:
            if expected_revision != self.context["revision"]:
                raise LabError("STALE_REVISION", "修改时间时版本已改变。")
            if not isinstance(self.context.get("profile"), dict):
                self.context["profile"] = {}
            self.context["profile"]["daily_minutes"] = minutes
            self.context["revision"] += 1
