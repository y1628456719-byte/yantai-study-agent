"""Atomic in-memory reference adapter, deliberately not a production backend."""
import copy
from .host_storage import MemoryHost
from ..lab.content import digest
from ..lab.errors import LabError
from ..lab.adaptive_plan import build_adaptive_plan


class AdaptiveHost(MemoryHost):
    def cached_result(self, action, request_id, expected_revision, **params):
        """Read idempotency cache before costly request-scoped candidate generation."""
        lab = None
        # The cache fingerprint is independent of the lab instance.
        if not isinstance(request_id, str) or not request_id.strip():
            raise LabError("INVALID_INPUT", "request_id无效。")
        fingerprint = digest([action, expected_revision, params])
        with self.lock:
            entry = self.requests.get(request_id)
            if entry and entry["fingerprint"] != fingerprint:
                raise LabError("REQUEST_CONFLICT", "同一请求ID携带不同请求。")
            return copy.deepcopy(entry["result"]) if entry else None

    def execute(self, lab, action, request_id, expected_revision, **params):
        lab.core._text(request_id,"request_id")
        if type(expected_revision) is not int or expected_revision < 0:
            raise LabError("INVALID_INPUT","expected_revision须为非负整数。")
        allowed = {"start":set(),"submit":{"question_id","answer"},"feedback":{"feedback"},
                   "time":{"minutes"},"continue":set(),"stop":set()}
        if not isinstance(action,str) or action not in allowed or set(params) != allowed[action]:
            raise LabError("INVALID_INPUT","动作参数无效。")
        fingerprint = digest([action,expected_revision,params])
        def cached():
            entry = self.requests.get(request_id)
            if entry and entry["fingerprint"] != fingerprint:
                raise LabError("REQUEST_CONFLICT","同一请求ID携带不同参数。")
            return copy.deepcopy(entry["result"]) if entry else None
        with self.lock:
            existing = cached()
            if existing:
                return existing
            if expected_revision != self.context["revision"]:
                raise LabError("STALE_REVISION","页面状态过期，请刷新。")
            context = copy.deepcopy(self.context)
            context["request_id"] = request_id
        result = lab.act(context,action,**params)
        state = result["learning_state"]
        minutes = lab._minutes({**context,"profile":result.get("profile",context.get("profile"))})
        result["plan"] = build_adaptive_plan(state,minutes,result["lab_state"])
        result["revision"] = result["next_revision"]
        with self.lock:
            existing = cached()
            if existing:
                return existing
            if result["base_revision"] != self.context["revision"]:
                raise LabError("STALE_REVISION","计算后保存冲突，结果未应用。")
            if result["state_changed"]:
                self.context.update(learning_state=copy.deepcopy(state), revision=result["next_revision"])
                if "profile" in result:
                    self.context["profile"] = copy.deepcopy(result["profile"])
            self.requests[request_id] = {"fingerprint":fingerprint,"result":copy.deepcopy(result)}
        return result

    def public_read(self, lab):
        c = self.read()
        if c["learning_state"] is None:
            return {"revision":c["revision"],"lab_state":None,"plan":None,"runtime_mode":c["runtime_mode"]}
        result = lab.start_lab(c)
        return {"revision":c["revision"],"lab_state":result["lab_state"],
                "plan":build_adaptive_plan(result["learning_state"],lab._minutes(c),result["lab_state"]),
                "runtime_mode":c["runtime_mode"]}
