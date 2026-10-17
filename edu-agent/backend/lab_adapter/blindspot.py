"""盲点实验室（队友 C 交付包）桥接 adapter。

队友包的设计是「纯计算 + 宿主持有可信快照」，我们就是那个宿主：
- 每个会话的快照用 `AdaptiveHost.export_json()` 存进主后端的
  `learning_state["blindspot"]`（SPEC §10.2 允许 learning_state 是自由结构、原样透传），
  进程重启 / 多 worker 都能从 SQLite 恢复，**不引入第二套数据库**。
- 本文件只做两件事：**原子版本检查（expected_revision）+ 形状翻译**
  （队友 `lab_state` → SPEC §7.11 契约）。实验室逻辑一行都不写在这里。

诚实性约束（答辩会被问）：
- 队友首版只支持**随包自编的逻辑材料与知识点**（他们 engine 会校验 material_id/text/topic）。
  用户自己上传的资料他们分析不了 → 我们**不假装**用了用户资料：lab_state 里写
  `material_note` 明确说明本次用的是随包演示材料，并保持 `is_demo=True`。
- 他们的 `runtime_mode=mock` 走包内 MockModel（离线、无需密钥）；`live` 才会调真实模型。
  两个模式都不允许静默切换（他们自己也会校验 model.runtime_mode）。
"""
from __future__ import annotations

import json
import uuid
import logging
import time
import copy

from .. import config
from .base import LabError, STAGES

#: 队友包允许的 6 个动作（与他们的 AdaptiveHost.execute 白名单一致）
ACTIONS = ("start", "submit", "feedback", "time", "continue", "stop")
_ACTION_PARAMS = {
    "start": (),
    "submit": ("question_id", "answer"),
    "feedback": ("feedback",),
    "time": ("minutes",),
    "continue": (),
    "stop": (),
}
_DEMO_MATERIAL_NOTE = (
    "本次实验用的是「盲点实验室」随包自编的逻辑推理演示材料"
    "（队友实验室首版只支持该材料与知识点，is_demo=true）；你上传的资料没有被用于出题。"
)


def _load_package():
    """导入 vendored 队友包；失败时给一句能直接展示的中文错误。"""
    try:
        from vendor.blindspot_lab import AdaptiveLab, adaptive_context
        from vendor.blindspot_lab.examples.adaptive_host import AdaptiveHost
        from vendor.blindspot_lab.lab.errors import LabError as _BlindspotLabError
    except Exception as exc:  # pragma: no cover - 只在包缺失时触发
        raise LabError(f"盲点实验室模块未接入或损坏：{exc!r}") from exc
    return AdaptiveLab, adaptive_context, AdaptiveHost, _BlindspotLabError


def _locator_text(locator) -> str | None:
    if locator is None:
        return None
    if isinstance(locator, str):
        return locator
    if isinstance(locator, dict):
        start, end = locator.get("start"), locator.get("end")
        line = locator.get("line")
        if start is not None and end is not None:
            prefix = f"第 {line} 行，" if type(line) is int and line > 0 else ""
            return f"{prefix}随包材料第 {start}-{end} 字符"
        if type(line) is int and line > 0:
            return f"第 {line} 行"
        return json.dumps(locator, ensure_ascii=False)
    return str(locator)


_STATUS = {
    "supported": "supported",
    "revised": "revised",
    "tentative": "tentative",
    "inconclusive": "tentative",
    "no_issue": "tentative",
}


class BlindspotLabAdapter:
    """队友 C「盲点实验室」的自适应升级版（lab.v2）适配器。"""

    name = "blindspot"

    def __init__(self, *, runtime_mode: str | None = None) -> None:
        mode = (runtime_mode or config.LAB_BLINDSPOT_MODE or "mock").strip().lower()
        if mode not in ("mock", "live", "replay"):
            raise LabError(f"LAB_BLINDSPOT_MODE 取值非法：{mode!r}（只能是 mock / live / replay）")
        self.runtime_mode = mode
        self._AdaptiveLab, self._adaptive_context, self._Host, self._PkgError = _load_package()
        self.client = None
        model = None
        if mode == "live":
            from vendor.blindspot_lab.lab.aiping import AipingClient
            from vendor.blindspot_lab.lab.models import GenerateJSONAdapter
            try:
                self.client = AipingClient(model="DeepSeek-V4.1-Flash")
                model = GenerateJSONAdapter(self._generate_json)
            except self._PkgError as exc:
                raise LabError(exc.message, code=exc.code) from None
        self._timeout_seconds = min(120, config.LAB_TIMEOUT)
        self._model = model
        self._lab = self._AdaptiveLab(model=model, timeout_seconds=self._timeout_seconds)

    # ------------------------------------------------------------------ 宿主
    def _host(self, context: dict):
        """恢复该会话的宿主快照；没有就从随包 context 新建。"""
        learning = context.get("learning_state") or {}
        stored = learning.get("blindspot") if isinstance(learning, dict) else None
        if isinstance(stored, str) and stored.strip():
            try:
                host = self._Host.restore_json(stored)
            except Exception as exc:
                raise LabError("盲点实验室会话快照损坏，无法恢复。") from None
        else:
            host = self._Host(self._adaptive_context(session_id=context.get("session_id") or "s_blindspot",
                                                     runtime_mode=self.runtime_mode))
            host.context.update(dynamic_bank=[], dynamic_bank_digest=self._digest([]),
                dynamic_followups_enabled=self.runtime_mode == "live",
                max_rounds=5 if self.runtime_mode == "live" else 3)
        from ..services.journey import preferred_track
        host.context["preferred_track"] = preferred_track(learning)
        mins = ((context.get("profile") or {}).get("daily_minutes"))
        if not stored and type(mins) is int and 1 <= mins <= 1440:
            host.context.setdefault("profile", {})
            host.context["profile"]["daily_minutes"] = mins
        school = context.get("selected_school_id")
        if not stored and school and isinstance(host.context.get("target"), dict):
            host.context["target"]["school_id"] = school
        return host

    @staticmethod
    def _digest(value):
        from vendor.blindspot_lab.lab.content import digest
        return digest(value)

    def _lab_for(self, host):
        """Build a request-scoped question map; the adapter singleton stays immutable."""
        from vendor.blindspot_lab.lab.dynamic_questions import validate_bank
        bank = host.context.get("dynamic_bank", [])
        bank_digest = host.context.get("dynamic_bank_digest", self._digest(bank))
        if not validate_bank(bank, bank_digest):
            raise LabError("盲点实验室动态题库校验失败。", code="INVALID_INPUT")
        lab = self._AdaptiveLab(model=self._model, timeout_seconds=self._timeout_seconds)
        # The ModelRunner owns the shared concurrency slots and transport model;
        # the new lab only supplies a per-host question dictionary.
        lab.core.runners = self._lab.core.runners
        lab.set_dynamic_questions(bank)
        return lab

    @staticmethod
    def _bind_bank_digest(host, bank):
        from vendor.blindspot_lab.lab.content import digest
        digest_value = digest(bank)
        host.context["dynamic_bank"] = copy.deepcopy(bank)
        host.context["dynamic_bank_digest"] = digest_value
        state = host.context.get("learning_state")
        if isinstance(state, dict):
            state["dynamic_bank_digest"] = digest_value
            state.pop("integrity", None)
            state["integrity"] = digest({k: v for k, v in state.items() if k != "integrity"})

    def _generate_dynamic(self, host, lab, request_id):
        from vendor.blindspot_lab.lab.dynamic_questions import (
            MAX_DYNAMIC_QUESTIONS, generate_questions, validate_bank,
        )
        from vendor.blindspot_lab.lab.errors import LabError as PkgLabError
        bank = copy.deepcopy(host.context.get("dynamic_bank", []))
        if len(bank) >= MAX_DYNAMIC_QUESTIONS:
            return

        def call(messages, schema, timeout_seconds):
            return self._lab.core.runners["live"].call(
                {"messages": messages, "schema": schema})

        focus = host.context.get("preferred_track")
        if focus not in ("direction", "reading", "transfer"):
            current = host.context.get("learning_state") or {}
            focus = ("transfer" if current.get("strategy") == "transfer" else
                     "reading" if current.get("strategy") in ("steps", "analogy") else "direction")
        questions = generate_questions(call, session_id=host.context["session_id"],
                                       request_id=request_id, preferred_track=focus,
                                       existing_questions=bank,
                                       count=MAX_DYNAMIC_QUESTIONS - len(bank),
                                       timeout_seconds=self._timeout_seconds)
        bank.extend(questions)
        bank_digest = self._digest(bank)
        if not validate_bank(bank, bank_digest):
            raise PkgLabError("INVALID_INPUT", "生成题目未通过会话题库校验。")
        self._bind_bank_digest(host, bank)
        lab.set_dynamic_questions(bank)

    def _generate_json(self, messages, schema, timeout_seconds):
        """只记录安全元数据，不记录题目、提示词、思考内容或密钥。"""
        started = time.monotonic()
        meta = {"component": "blindspot", "model_requested": "DeepSeek-V4.1-Flash",
                "requested_reasoning_effort": "max"}
        try:
            value = self.client.generate_json(messages, schema, timeout_seconds)
            meta["status"] = "ok"
            return value
        except Exception:
            meta["status"] = "failed"
            raise
        finally:
            meta["duration_seconds"] = round(time.monotonic() - started, 3)
            logging.getLogger("edu_agent.model_calls").info("MODEL_CALL %s", json.dumps(meta))

    @staticmethod
    def _request_id(context: dict, action: str, params: dict) -> str:
        """客户端重试沿用 ID；旧调用方每次新动作生成 ID，不能按动作内容去重。"""
        return context.get("request_id") or uuid.uuid4().hex

    def public_read(self, context: dict) -> dict:
        try:
            host = self._host(context)
            return host.public_read(self._lab_for(host))
        except self._PkgError as exc:
            raise LabError(exc.message, code=exc.code) from None

    # ------------------------------------------------------- 形状翻译（§7.11）
    def _to_contract(self, lab_state: dict, *, plan, their_next_action: str | None) -> dict:
        stage = lab_state.get("stage")
        if stage == "stopped":
            stage = "summary"
        if stage not in STAGES:
            stage = "diagnosis"

        question = None
        raw_q = lab_state.get("question")
        if isinstance(raw_q, dict):
            question = dict(raw_q)
            opts = []
            for opt in raw_q.get("options") or []:
                if not isinstance(opt, dict):
                    continue
                key = opt.get("key") or opt.get("id")
                opts.append({"key": key, "id": opt.get("id") or key, "text": opt.get("text")})
            question["options"] = opts

        hypotheses = []
        for item in lab_state.get("hypotheses") or []:
            if not isinstance(item, dict):
                continue
            hypotheses.append({
                "label": item.get("label") or item.get("id") or "本轮观察",
                "subject_code": None,  # 队友的逻辑推理题不属于标准科目表，如实置空（不硬凑）
                "status": _STATUS.get(str(item.get("status")), "tentative"),
                "reason": item.get("reason") or "",
                "evidence_count": len(item.get("evidence_ids") or []),
                "hypothesis_id": item.get("id"),
            })

        evidence = []
        for rec in lab_state.get("evidence") or []:
            if not isinstance(rec, dict):
                continue
            kind = rec.get("type")
            if kind == "answer":
                evidence.append({
                    "kind": "answer",
                    "question_id": rec.get("question_id"),
                    "track": rec.get("track"),
                    "quote": rec.get("answer_text") or rec.get("answer_id") or "本题作答",
                    "locator": f"第 {rec.get('answer_number') or rec.get('sequence') or '?'} 题",
                    "is_correct": rec.get("is_correct"),
                })
            elif kind in ("material", "rule_source"):
                evidence.append({
                    "kind": "material",
                    "material_id": rec.get("material_id"),
                    "material_title": rec.get("material_title"),
                    "quote": rec.get("quote"),
                    "locator": _locator_text(rec.get("locator")),
                })

        feedback = lab_state.get("feedback")
        if isinstance(feedback, dict):
            feedback = feedback.get("text")
        explanation = lab_state.get("explanation")
        if isinstance(explanation, dict):
            explanation = explanation.get("text")

        if their_next_action in ("pause_or_finish",) or stage == "summary" or lab_state.get("stopped"):
            next_action = "complete"
        else:
            next_action = "answer"

        out = dict(lab_state)  # 先保留他们的全部键（adaptation/cycle/history/summary…）
        out.update({
            "stage": stage,
            "question": question,
            "feedback": feedback,
            "hypotheses": hypotheses,
            "evidence": evidence,
            "explanation": explanation,
            "next_action": next_action,
            "is_demo": True,          # 随包自编材料与题库，永远是演示数据
            "adapter": "blindspot",
            "adapter_version": "lab.v2",
            "runtime_mode": self.runtime_mode,
            "their_next_action": their_next_action,
            "allowed_actions": list(ACTIONS),
            "material_note": _DEMO_MATERIAL_NOTE,
            "plan": plan,
        })
        return out

    # ------------------------------------------------------------- 对外动作
    def _call(self, context: dict, action: str, **params) -> dict:
        if action not in ACTIONS:
            raise LabError(f"盲点实验室不支持的动作：{action!r}（支持 {', '.join(ACTIONS)}）")
        wanted = _ACTION_PARAMS[action]
        if tuple(sorted(params)) != tuple(sorted(wanted)):
            raise LabError(f"动作 {action} 的参数必须是 {wanted or '（无）'}，收到 {tuple(sorted(params))}")
        host = self._host(context)
        request_id = self._request_id(context, action, {k: params[k] for k in wanted})
        try:
            expected_revision = host.context["revision"]
            cached = host.cached_result(action, request_id, expected_revision, **params)
            lab = self._lab_for(host)
            if cached is not None:
                result = cached
            else:
                if action == "continue" and host.context.get("dynamic_followups_enabled") is True:
                    probe = copy.deepcopy(host.context)
                    probe["request_id"] = request_id
                    c, s, _ = lab._context(probe)
                    can_continue, _ = lab._continuation(c, s) if s else (False, "not_started")
                    if can_continue and len(host.context.get("dynamic_bank", [])) < 2:
                        self._generate_dynamic(host, lab, request_id)
                result = host.execute(lab, action, request_id, expected_revision, **params)
        except self._PkgError as exc:
            # 队友的业务错误码原样透出（STALE_REVISION / MODEL_UNAVAILABLE / MATERIAL_UNSUPPORTED…）
            raise LabError(f"盲点实验室拒绝本次请求（{exc.code}）：{exc.message}", code=exc.code) from exc
        except LabError:
            raise
        except Exception as exc:  # pragma: no cover - 兜底，不静默降级
            raise LabError("盲点实验室调用失败，本次状态未保存。") from None

        lab_state = self._to_contract(result.get("lab_state") or {}, plan=result.get("plan"),
                                      their_next_action=(result.get("lab_state") or {}).get("next_action"))
        learning_state = {
            "blindspot": host.export_json(),
            "adapter": "blindspot",
            "runtime_mode": self.runtime_mode,
            "lab_version": "lab.v2",
            "revision": host.context.get("revision"),
        }
        return {"lab_state": lab_state, "learning_state": learning_state,
                "public_lab_state": result.get("lab_state"), "plan": result.get("plan"),
                "profile": host.context.get("profile")}

    def start(self, context: dict) -> dict:
        return self._call(context, "start")

    def answer(self, context: dict, question_id: str, answer: str) -> dict:
        return self._call(context, "submit", question_id=question_id, answer=answer)

    #: 队友 lab.v2 的额外动作（后端 additive 端点用；SPEC 只要求 start/answer）
    def act(self, context: dict, action: str, **params) -> dict:
        return self._call(context, action, **params)
