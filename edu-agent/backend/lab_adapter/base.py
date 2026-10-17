"""实验室适配接口（SPEC §10.1 / §10.6）。

后端**不在路由里写死实验室逻辑**：只通过这个 adapter 调用。
队友 C 的实现放在 `teammate.py`，未接入时用 `mock.py`。

【铁律】真实调用失败**不得静默降级**为 mock —— 必须抛 `LabError`，由路由转成
`LAB_UNAVAILABLE`（503）。is_demo 的区分交给前端（N26）。
"""
from __future__ import annotations


#: lab_state 的合法取值
STAGES = ("diagnosis", "explanation", "verification", "summary")
NEXT_ACTIONS = ("answer", "complete")
BASIS_VALUES = ("measured", "self_reported")


class LabError(RuntimeError):
    """实验室不可用 / 返回结构不合法。

    `code` 是**可选的业务错误码**（队友实验室会给我们 `STALE_REVISION`、
    `CONTINUE_UNAVAILABLE`、`MODEL_UNAVAILABLE` 等），路由据此选 HTTP 状态码：
    版本冲突 → 409，输入/业务规则不允许 → 400，其余 → 503 LAB_UNAVAILABLE。
    """

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


def empty_lab_state(*, is_demo: bool = False) -> dict:
    return {
        "stage": "diagnosis",
        "question": None,
        "feedback": None,
        "hypotheses": [],
        "evidence": [],
        "explanation": None,
        "next_action": "answer",
        "is_demo": is_demo,
    }


def normalize_result(result: dict, *, is_demo: bool | None = None) -> dict:
    """校验并补全 adapter 返回的 `{lab_state, learning_state}`。

    缺字段就补默认值；**结构错到无法修补就抛 `LabError`**，不产出半成品。
    """
    if not isinstance(result, dict):
        raise LabError("实验室返回的不是 object")
    lab_state = result.get("lab_state")
    if not isinstance(lab_state, dict):
        raise LabError("实验室返回缺少 lab_state")

    fixed = empty_lab_state(is_demo=lab_state.get("is_demo", is_demo if is_demo is not None else False))
    # 先原样保留 adapter 的全部键（队友实验室会额外给 adaptation/cycle/history/summary/plan 等），
    # 再补默认值 —— 丢掉这些键等于把他们的可视化证据扔了（N32/N26 的展示依赖它们）。
    fixed.update(lab_state)
    fixed.setdefault("stage", "diagnosis")
    fixed.setdefault("question", None)
    fixed.setdefault("hypotheses", [])
    fixed.setdefault("evidence", [])
    fixed.setdefault("next_action", "answer")
    if fixed["stage"] not in STAGES:
        raise LabError(f"lab_state.stage 非法：{fixed['stage']!r}")
    if fixed["next_action"] not in NEXT_ACTIONS:
        fixed["next_action"] = "answer"
    if fixed["question"] is not None and not isinstance(fixed["question"], dict):
        raise LabError("lab_state.question 必须是 object 或 null")
    for key in ("hypotheses", "evidence"):
        if fixed[key] is None:
            fixed[key] = []
        if not isinstance(fixed[key], list):
            raise LabError(f"lab_state.{key} 必须是数组")
    for item in fixed["hypotheses"]:
        if not isinstance(item, dict) or "label" not in item:
            raise LabError("hypotheses[] 缺少 label")
        if item.get("status") not in ("tentative", "supported", "revised"):
            item["status"] = "tentative"
        # N31：subject_code 必须来自标准科目表，否则无法与院校 exam_subjects 对齐
        code = item.get("subject_code")
        if code:
            from ..core import subjects as subject_table
            if code not in subject_table.by_code():
                item["subject_code"] = None
    for item in fixed["evidence"]:
        if not isinstance(item, dict):
            raise LabError("evidence[] 必须是 object")
        if item.get("kind") not in ("material", "answer"):
            item["kind"] = "answer"
        if item["kind"] == "answer" and not item.get("question_id"):
            raise LabError("kind=answer 的证据必须带 question_id")
    if is_demo is not None:
        fixed["is_demo"] = bool(lab_state.get("is_demo", is_demo))

    learning_state = result.get("learning_state")
    if learning_state is not None and not isinstance(learning_state, dict):
        raise LabError("learning_state 必须是 object 或 null")
    return {"lab_state": fixed, "learning_state": learning_state,
            **{k: result[k] for k in ("public_lab_state", "plan", "profile") if k in result}}


class LabAdapter:
    name = "base"

    def start(self, context: dict) -> dict:
        raise NotImplementedError

    def answer(self, context: dict, question_id: str, answer: str) -> dict:
        raise NotImplementedError


def build_context(session: dict, *, material: dict | None, diagnosis_mode: str,
                  topic: str | None = None) -> dict:
    """SPEC §10.1：context 至少含 profile / selected_school / material / learning_state / subject_table。"""
    from ..core import subjects as subject_table
    from ..services import school_data

    selected = session.get("selected_school_id")
    return {
        "session_id": session.get("session_id"),
        "profile": session.get("profile") or {},
        "selected_school_id": selected,
        "selected_school": school_data.get(selected) if selected else None,
        "material": material,
        "learning_state": session.get("learning_state") or {},
        "subject_table": subject_table.as_meta(),
        "diagnosis_mode": diagnosis_mode,
        "topic": topic,
    }
