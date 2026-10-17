"""资料端点（SPEC §7.6 / §7.7）。"""
from __future__ import annotations

from fastapi import APIRouter

from ..core import response
from ..core.errors import AppError, invalid_argument
from ..core.schemas import MaterialIn
from ..services import onboarding, school_data, session_store
from ._deps import require_session

router = APIRouter(tags=["materials"])


@router.get("/sessions/{session_id}/materials")
async def list_materials(session_id: str):
    """列出资料（前端刷新用；删除后列表立即反映）。"""
    require_session(session_id)
    return response.ok({"materials": session_store.list_materials(session_id)})


@router.post("/sessions/{session_id}/materials")
async def add_material(session_id: str, payload: MaterialIn):
    """§7.6 录入学习资料（N11 分段+locator、N12 可选 school_id/subject、N13 preview）。"""
    require_session(session_id)
    if not (payload.text or "").strip():
        raise invalid_argument("资料正文不能为空（text 必填）")
    if payload.school_id and not school_data.get(payload.school_id):
        raise invalid_argument(f"未知院校：{payload.school_id}")

    material = session_store.add_material(
        session_id,
        title=(payload.title or "").strip() or "未命名资料",
        text=payload.text,
        source_url=payload.source_url,
        is_demo=bool(payload.is_demo),
        school_id=payload.school_id,
        subject=payload.subject,
    )
    # 资料数量会影响阶段推断（N29：有无资料是降级提问顺序的一环）
    # 这里不花模型调用：加资料不是"用户正等着看下一句"的场合，规则文案足够
    await onboarding.recompute(session_id)
    return response.ok(material)


@router.delete("/sessions/{session_id}/materials/{material_id}")
async def delete_material(session_id: str, material_id: str):
    """§7.7 删除资料（N35）：已被诊断引用则软删除；**幂等**。

    重复删除（含硬删除后行已消失）一律返回 `ok: true`，避免演示时反复点删除报错。
    """
    require_session(session_id)
    try:
        session_store.get_material_row(session_id, material_id)
    except AppError as exc:
        if exc.code != "MATERIAL_NOT_FOUND":
            raise
        return response.ok({
            "deleted": True,
            "material_id": material_id,
            "soft": False,
            "note": "该资料此前已不存在（可能已删除），按幂等返回成功",
        })
    result = session_store.delete_material(session_id, material_id)
    await onboarding.recompute(session_id)
    return response.ok({
        "deleted": True,
        "material_id": material_id,
        **{k: v for k, v in result.items() if k in ("soft", "note")},
    })
