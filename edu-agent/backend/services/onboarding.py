"""阶段推断结果的落库封装（N29 + P1-4 追问）。

`phase` 由 `known_facts` 推断；**推断结果只写进 `onboarding`，绝不回写 `profile.phase`**。
原因：`profile.phase` 的语义是"用户显式声明的阶段"（`phase_confidence=confirmed`），
一旦把推断值回写进去，下一次推断就会把自己的推断当成用户声明，从而谎报
"用户显式声明阶段为 X"——这是可被评委当场拆穿的不诚实，必须从结构上避免。

P1-4：**问什么由规则定，怎么问由模型说**。规则的降级提问顺序（SPEC §5.4）决定
`next_question_key`，模型只负责把这句问话说自然；模型不可用时逐级退回规则原话，
并且规则原话**始终**留在 `onboarding.rule_next_question` 里（可审计，也方便当场解释）。
"""
from __future__ import annotations

from . import modelsvc, phase_detector, session_store


async def recompute(session_id: str, *, ask_model: bool = False) -> dict:
    """重算阶段并写回会话，返回最新快照。

    `ask_model=True` 时才调用模型润色追问（建档 / 改档案这两个"用户正等着看下一句"的场合）；
    其余场合（加资料、选院校、缩备选）保持纯规则，避免为一句追问多付一次模型调用与延迟。
    """
    session = session_store.snapshot(session_id)
    profile = dict(session.get("profile") or {})
    result = phase_detector.detect(
        profile,
        selected_school_id=session.get("selected_school_id"),
        material_count=len(session.get("materials") or []),
        has_shortlist=bool(session.get("shortlist")),
    )

    rule_question = result["next_question"]
    question = rule_question
    source = "rules"
    model_error = None
    if ask_model and result.get("next_question_key"):
        asked = await modelsvc.build_next_question(
            rule_question=rule_question,
            missing_key=result.get("next_question_key"),
            profile=profile,
            phase_name=result.get("phase_name"),
            reasons=result.get("reasons"),
        )
        question = asked.get("question") or rule_question
        source = asked.get("generated_by") or "rules"
        model_error = asked.get("model_error")
    elif ask_model:
        source = "rules(complete)"

    onboarding = {
        "phase": result["phase"],
        "phase_name": result["phase_name"],
        "phase_confidence": result["phase_confidence"],
        "next_question": question,
        "next_question_source": source,
        "next_question_key": result.get("next_question_key"),
        # 规则原话始终保留：模型换了说法也要能对得上 SPEC §5.4 的顺序
        "rule_next_question": rule_question,
        "missing_critical": result["missing_critical"],
        "missing_profile_fields": result["missing_profile_fields"],
        "reasons": result["reasons"],
        "known_facts": result["known_facts"],
    }
    if model_error:
        onboarding["next_question_error"] = model_error
    session_store.update_fields(session_id, onboarding=onboarding, expected_revision=session["revision"])
    return session_store.snapshot(session_id)
