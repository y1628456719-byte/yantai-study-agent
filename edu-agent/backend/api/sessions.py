"""会话相关端点（SPEC §7.1 ~ §7.5、§7.13）。"""
from __future__ import annotations

from fastapi import APIRouter

from ..core import response
from ..core.errors import AppError, invalid_argument, model_unavailable
from ..core.schemas import ProfilePatchIn, SessionCreateIn, ShortlistIn, TargetIn
from ..llm import LLMError
from ..services import modelsvc, onboarding, recommender, school_data, session_store
from ._deps import require_session

router = APIRouter(tags=["sessions"])


@router.post("/sessions")
async def create_session(payload: SessionCreateIn):
    """§7.1 创建会话。"""
    profile = payload.profile.model_dump(exclude_none=True)
    created = session_store.create_session(profile)
    session_id = created["session_id"]
    # P1-4：建档后用户正等着看"下一句问什么"，这里让模型把规则选定的那一问说自然
    snapshot = await onboarding.recompute(session_id, ask_model=True)
    return response.ok(snapshot)


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """§7.3 读取会话快照。"""
    require_session(session_id)
    return response.ok(session_store.snapshot(session_id))


@router.patch("/sessions/{session_id}/profile")
async def patch_profile(session_id: str, payload: ProfilePatchIn):
    """§7.2 部分更新档案（N1/N29/N30）。"""
    require_session(session_id)
    updates = payload.model_dump(exclude_unset=True)
    # 显式传 null 表示"清空这一项"，因此这里不用 exclude_none
    session_store.merge_profile(session_id, updates)
    snapshot = await onboarding.recompute(session_id, ask_model=True)
    return response.ok(snapshot)


@router.put("/sessions/{session_id}/target")
async def set_target(session_id: str, payload: TargetIn):
    """§7.5 确认目标院校（N9 备注 / N28 决策记录）。"""
    session = require_session(session_id)
    if not school_data.get(payload.school_id):
        raise invalid_argument(f"未知院校：{payload.school_id}（可用院校见 GET /api/v1/meta/sources）")
    alternatives = [it.get("school_id") for it in
                    (session.get("recommendations") or {}).get("recommendations", [])]
    school_store = school_data.get(payload.school_id)
    session_store.set_target(
        session_id,
        payload.school_id,
        school_name=(school_store or {}).get("school"),
        user_note=payload.user_note,
        alternatives=[a for a in alternatives if a != payload.school_id],
    )
    snapshot = await onboarding.recompute(session_id)
    return response.ok(snapshot)


@router.put("/sessions/{session_id}/shortlist")
async def set_shortlist(session_id: str, payload: ShortlistIn):
    """N10 备选清单：允许用户"先缩小到 2 所"。"""
    require_session(session_id)
    unknown = [sid for sid in payload.school_ids if not school_data.get(sid)]
    if unknown:
        raise invalid_argument("未知院校：" + "、".join(unknown))
    session_store.update_fields(session_id, shortlist=list(dict.fromkeys(payload.school_ids)))
    snapshot = await onboarding.recompute(session_id)
    return response.ok(snapshot)


@router.post("/sessions/{session_id}/recommendations")
async def recommendations(session_id: str):
    """§7.4 生成院校推荐（N2/N4/N5/N6/N7/N8/N27/N32）。

    **可重复调用且幂等**（SPEC §4.2.5 闭环顺序）：同一 `revision` 下结果一致；
    若会话里已有 `learning_state.ability_profile`，返回的 `ability_flags` 会体现"风险上调"。
    """
    session = require_session(session_id)
    payload = recommender.recommend(session)
    if not payload["recommendations"] and not payload["excluded"]:
        raise AppError("INVALID_ARGUMENT", "院校数据集为空，无法生成推荐；请检查 data/schools 目录。")

    phase_name = ((session.get("onboarding") or {}).get("phase_name"))
    try:
        texts = await modelsvc.build_recommend_text(payload, phase_name)
    except LLMError as exc:
        raise model_unavailable(str(exc)) from exc

    payload["profile_echo"]["understood_as"] = texts.get("understood_as")
    payload["model_texts"] = {
        "understood_as": texts.get("understood_as"),
        "ranked": texts.get("ranked") or [],
        "generated_by": texts.get("generated_by"),
        "model_error": texts.get("model_error"),
    }
    payload["model_used"] = texts.get("generated_by") == "model"
    payload["model"] = None if payload["model_used"] else "未调用模型（规则文案）"

    session_store.update_fields(session_id, recommendations=payload, expected_revision=session["revision"])
    payload["revision"] = session_store.snapshot(session_id).get("revision")
    return response.ok(payload)
