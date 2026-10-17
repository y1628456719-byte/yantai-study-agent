"""模型业务封装（SPEC §11 / §4.2）。

**职责边界**：模型只负责把已经算好的结构用中文说清楚；排序、分档、淘汰一律由硬规则给出。
因此这里所有函数在模型不可用或未配置密钥时都有**规则文案兜底**，并如实标记 `model_used`。
"""
from __future__ import annotations

import json

from .. import config, llm

# --------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------

RECOMMEND_TEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "understood_as": {"type": "string"},
        "ranked": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "school_id": {"type": "string"},
                    "verdict": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["school_id", "verdict", "text"],
            },
        },
    },
    "required": ["understood_as", "ranked"],
}


def _rule_understood_as(profile: dict, phase_name: str | None) -> str:
    bits = []
    if profile.get("exam_year"):
        bits.append(f"{profile['exam_year']} 年考研")
    if profile.get("target_major") or profile.get("major"):
        bits.append(f"目标方向「{profile.get('target_major') or profile.get('major')}」")
    regions = profile.get("preferred_regions") or []
    if regions:
        bits.append("偏好地域：" + "、".join(regions))
    if profile.get("daily_minutes"):
        bits.append(f"每天可投入 {profile['daily_minutes']} 分钟")
    if profile.get("target_score"):
        bits.append(f"目标分 {profile['target_score']}")
    if phase_name:
        bits.append(f"当前阶段：{phase_name}")
    if not bits:
        return "你还没有填写具体的报考条件，系统会先按默认候选院校范围给出对比，并提示还缺哪些信息。"
    return "系统理解为：" + "；".join(bits) + "。如果哪一项理解错了，请直接改档案。"


def _rule_verdict(item: dict) -> tuple[str, str]:
    """规则文案：完全由 metrics 拼出，可复现、可核对。"""
    metrics = item.get("metrics") or {}
    tier = metrics.get("tier") or "未分档"
    total = (metrics.get("score_line_total") or {}).get("value") if isinstance(metrics.get("score_line_total"), dict) else metrics.get("score_line_total")
    scale = (metrics.get("admission_scale") or {}).get("value") if isinstance(metrics.get("admission_scale"), dict) else metrics.get("admission_scale")
    parts = []
    if total is not None:
        parts.append(f"复试线 {total} 分")
    if scale is not None:
        parts.append(f"统考招生 {scale} 人")
    risk_count = len(item.get("risks") or [])
    if risk_count:
        parts.append(f"{risk_count} 条风险提示")
    text = f"{item.get('school_name')}：分档「{tier}」，" + ("，".join(parts) if parts else "关键数据缺失，暂不能给结论")
    return tier, text + "。"


def _rule_recommend_text(payload: dict, phase_name: str | None) -> dict:
    ranked = []
    for item in payload.get("recommendations") or []:
        tier, text = _rule_verdict(item)
        ranked.append({
            "school_id": item.get("school_id"),
            "verdict": f"{item.get('school_name')}（{tier}）",
            "text": text,
        })
    return {
        "understood_as": _rule_understood_as(payload.get("profile_echo") or {}, phase_name),
        "ranked": ranked,
        "generated_by": "rules",
    }


def _prompt_payload(payload: dict) -> str:
    """只把**已经算好的事实**交给模型，避免它自己发挥。"""
    compact = {
        "profile_echo": payload.get("profile_echo"),
        "tier_rule": payload.get("tier_rule"),
        "schools": [
            {
                "school_id": item.get("school_id"),
                "school_name": item.get("school_name"),
                "metrics": {k: v for k, v in (item.get("metrics") or {}).items()
                            if k in ("score_line_total", "score_line_single", "admission_scale",
                                     "exam_subjects", "tuition", "city", "duration_years",
                                     "reference_books_count", "tier")},
                "fit_reasons": [r.get("text") for r in (item.get("fit_reasons") or [])],
                "risks": [r.get("text") for r in (item.get("risks") or [])],
                "unknowns_count": len(item.get("unknowns") or []),
            }
            for item in (payload.get("recommendations") or [])
        ],
        "excluded": payload.get("excluded"),
    }
    return json.dumps(compact, ensure_ascii=False)


SYSTEM_RECOMMEND = (
    "你是考研择校顾问。用户会给你一份已经由硬规则算好的院校对比数据。"
    "你的任务只有一件：用简洁、克制的中文把结论说清楚。"
    "硬性约束：①不得新增数据里没有的数字；②不得改变分档（冲/稳/保）；"
    "③不得做数据里没有的推断；④每条 1~2 句，不要客套话；"
    "⑤必须用『证据条数』而不是百分比表达不确定性。只输出 JSON。"
)


async def build_recommend_text(payload: dict, phase_name: str | None = None) -> dict:
    """给推荐结果补 `understood_as` 与每校 verdict/text。

    返回 `{"understood_as":..., "ranked":[...], "generated_by":"model"|"rules", "model_error":...}`
    """
    fallback = _rule_recommend_text(payload, phase_name)
    if config.MOCK_LLM or not config.LLM_API_KEY:
        fallback["model_error"] = None if config.MOCK_LLM else "未配置 LLM_API_KEY，已使用规则文案"
        fallback["generated_by"] = "rules(offline)" if config.MOCK_LLM else "rules(no_key)"
        return fallback

    messages = [
        {"role": "system", "content": SYSTEM_RECOMMEND},
        {"role": "user", "content": _prompt_payload(payload)},
    ]
    try:
        result = await llm.generate_json(messages, RECOMMEND_TEXT_SCHEMA,
                                         mock_factory=lambda schema: fallback)
    except llm.LLMError as exc:
        if config.RECOMMEND_DEGRADE:
            fallback["model_error"] = f"模型调用失败，已降级为规则文案：{exc}"
            return fallback
        raise
    result["generated_by"] = "model"
    return result


# --------------------------------------------------------------------------
# P1-4：阶段追问（问什么由规则定，怎么问由模型说；模型挂了就退回规则原话）
# --------------------------------------------------------------------------

NEXT_QUESTION_SCHEMA = {
    "type": "object",
    "properties": {"question": {"type": "string"}},
    "required": ["question"],
}

SYSTEM_ASK = (
    "你是考研择校 Agent 的追问层。系统已经用规则决定了**这一轮必须问哪一项信息**——"
    "不要改问别的，也不要一次问两件事。"
    "硬性约束：①只问这一项，一句话，不超过 40 字；②不要复述已知档案，不要客套；"
    "③不得编造用户没给过的数据；④如果用户的档案里已经有相关线索，请顺着线索问；"
    "⑤只输出 JSON，键名必须是 question。"
)


def _rule_question_payload(question: str) -> dict:
    return {"question": question, "generated_by": "rules"}


async def build_next_question(
    *,
    rule_question: str,
    missing_key: str | None,
    profile: dict | None = None,
    phase_name: str | None = None,
    reasons: list[str] | None = None,
) -> dict:
    """把「下一句该问什么」交给模型说，规则原话始终保留。

    返回 `{"question": str, "generated_by": "model"|"rules"|"rules(offline)"|"rules(no_key)",
    "model_error": str|None}`。**任何失败都退回 `rule_question`**，绝不空手而归。
    """
    fallback = _rule_question_payload(rule_question)
    if config.MOCK_LLM or not config.LLM_API_KEY:
        fallback["model_error"] = None if config.MOCK_LLM else "未配置 LLM_API_KEY，已使用规则追问"
        fallback["generated_by"] = "rules(offline)" if config.MOCK_LLM else "rules(no_key)"
        return fallback

    profile = profile or {}
    known = {
        "phase_name": phase_name,
        "phase_reasons": (reasons or [])[:3],
        "exam_year": profile.get("exam_year"),
        "major": profile.get("major"),
        "target_major": profile.get("target_major"),
        "background": profile.get("background"),
        "preferred_regions": profile.get("preferred_regions"),
        "daily_minutes": profile.get("daily_minutes"),
        "study_status": profile.get("study_status"),
        "english_level": profile.get("english_level"),
    }
    payload = {
        "must_ask_about": missing_key,
        "system_rule_question": rule_question,
        "known_profile": {k: v for k, v in known.items() if v not in (None, "", [], {})},
        "note": "question 字段必须仍然是关于 must_ask_about 的同一个问题，只换一种更自然的说法。",
    }
    messages = [
        {"role": "system", "content": SYSTEM_ASK},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    try:
        result = await llm.generate_json(messages, NEXT_QUESTION_SCHEMA,
                                         mock_factory=lambda schema: fallback)
    except llm.LLMError as exc:
        # 追问失败不该打断主流程（用户仍要能建档/改档案），一律降级为规则原话并如实记录
        fallback["model_error"] = f"模型调用失败，已降级为规则追问：{exc}"
        fallback["generated_by"] = "rules(degraded)"
        return fallback
    question = str(result.get("question") or "").strip()
    if not question:
        fallback["model_error"] = "模型返回了空的追问，已降级为规则追问"
        fallback["generated_by"] = "rules(degraded)"
        return fallback
    result["question"] = question[:120]
    result["generated_by"] = "model"
    result["model_error"] = None
    return result


def _merge_plan_changes(rule_changes: list[dict], model_changes: list[dict],
                        *, result: dict | None = None) -> dict:
    """动作（dropped/kept）与条目集合只由规则决定，模型只提供 reason 文案。

    实测真实模型会把"被砍掉"的条目写成 `action=kept`（它理解成"按优先级保留高权重科目"），
    屏幕上就会出现「保留：思想政治理论」而计划里根本没有它；下游按 action 统计的
    `plan.dropped` 与决策时间线也会跟着错。所以后端按 task 名合并，只取模型的 reason。
    """
    reasons: dict[str, str] = {}
    for item in model_changes:
        task = str(item.get("task") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if task and reason:
            reasons.setdefault(task, reason)
    merged = [{**item, "reason": reasons.get(item["task"], item["reason"])} for item in rule_changes]
    payload = dict(result or {})
    payload["changes"] = merged
    return payload

# --------------------------------------------------------------------------

PLAN_TEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["task", "reason"],
            },
        },
    },
    "required": ["changes"],
}

SYSTEM_PLAN = (
    "你在帮考研学生调整每日计划。计划条目与『保留/砍掉』已由规则判定，你只负责为**每一条**写 reason。"
    "硬性约束：①理由必须指向具体的诊断结论或数据（例如某个薄弱知识点、某所院校的考试科目、每日分钟数变化），"
    "禁止写『为了巩固』『为了提升』这类空话；②不得新增没有依据的任务，也不要输出 action 字段"
    "（保留还是砍掉由系统决定，你写的 action 会被忽略）；③「为什么砍掉」要回答优先级取舍，"
    "不要复用「这项为什么重要」的说法；只输出 JSON。"
)


def _plan_context(context: dict | None) -> dict:
    """Only bounded, relevant observations go to the model, never host snapshots/caches."""
    source = context if isinstance(context, dict) else {}
    onboarding = source.get("onboarding") or {}
    learning = source.get("learning_state") or {}
    lab = learning.get("lab") or {}
    def bounded(record, keys):
        if not isinstance(record, dict):
            return {}
        return {key: (value[:300] if isinstance(value, str) else value)
                for key in keys if isinstance((value := record.get(key)), (str, int, float, bool))}
    ability = learning.get("ability_profile") or []
    return {
        "selected_school_id": str(source.get("selected_school_id") or "")[:100],
        "onboarding": bounded(onboarding, ("phase", "phase_name")),
        "lab": bounded(lab, ("stage", "topic", "diagnosis_mode", "is_demo")),
        "ability_profile": [bounded(item, ("topic", "topic_name", "subject_code", "status",
                                             "evidence_count", "basis")) for item in ability[:12]]
                            if isinstance(ability, list) else [],
    }


async def build_plan_changes(plan: list[dict], *, previous_minutes: int, daily_minutes: int,
                             context: dict | None = None) -> dict:
    """N21/N22：说明"砍了什么、为什么"；模型不可用时用规则文案。"""
    dropped, kept = [], []
    for task in plan:
        entry = {"action": "kept", "task": task.get("task"), "reason": task.get("reason")}
        (kept if task.get("kept", True) else dropped).append(entry)
    # 被砍掉的条目：reason 必须回答"为什么砍"——原 reason 只说明"这项为什么重要"，
    # 直接复用会出现"因为这是目标院校的初试科目，所以砍掉"这种自相矛盾的话。
    cut_note = (f"每日时间从 {previous_minutes} 分钟降到 {daily_minutes} 分钟，"
                f"按优先级暂缓本项（诊断结论与作答记录均保留，时间恢复后会自动重新排入）")
    rule_changes = [
        {"action": "dropped", "task": d["task"], "reason": cut_note} for d in dropped
    ] + [dict(k, action="kept") for k in kept]
    fallback = {"changes": rule_changes, "generated_by": "rules"}

    if config.MOCK_LLM or not config.LLM_API_KEY:
        fallback["generated_by"] = "rules(offline)" if config.MOCK_LLM else "rules(no_key)"
        return fallback
    payload = {
        "previous_daily_minutes": previous_minutes,
        "daily_minutes": daily_minutes,
        "plan": plan,
        "diagnosis_context": _plan_context(context),
    }
    messages = [
        {"role": "system", "content": SYSTEM_PLAN},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    try:
        result = await llm.generate_json(messages, PLAN_TEXT_SCHEMA,
                                         mock_factory=lambda schema: fallback)
    except llm.LLMError as exc:
        if config.RECOMMEND_DEGRADE:
            fallback["model_error"] = f"模型调用失败，已降级为规则文案：{exc}"
            return fallback
        raise
    result["generated_by"] = "model"
    return _merge_plan_changes(rule_changes, result.get("changes") or [], result=result)
