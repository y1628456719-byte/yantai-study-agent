"""学习计划端点（SPEC §7.10、N21/N22/N28）。

硬性要求：修改时间**不得清空**已选院校、资料与作答记录——
所以这里只更新 `plan` 与 `profile.daily_minutes`，并对 `profile` 做**合并写**（不整表覆盖）。
"""
from __future__ import annotations
import time

from fastapi import APIRouter

from ..core import response
from ..core.errors import model_unavailable, target_not_selected
from ..core.schemas import PlanReviseIn
from ..llm import LLMError
from ..services import modelsvc, planner, session_store
from ._deps import require_session

router = APIRouter(tags=["plan"])


@router.post("/sessions/{session_id}/plan/revise")
async def revise_plan(session_id: str, payload: PlanReviseIn):
    """§7.10 修改每日学习时间并重排计划。"""
    session = require_session(session_id)
    if not session.get("selected_school_id"):
        raise target_not_selected()

    result = planner.revise(session, payload.daily_minutes)
    context = {
        "onboarding": session.get("onboarding") or {},
        "learning_state": session.get("learning_state") or {},
        "selected_school_id": session.get("selected_school_id"),
    }
    try:
        texts = await modelsvc.build_plan_changes(
            result["plan"],
            previous_minutes=result["previous_daily_minutes"],
            daily_minutes=result["daily_minutes"],
            context=context,
        )
    except LLMError as exc:
        raise model_unavailable(str(exc)) from exc

    kept = [
        {"task": item.get("task"), "minutes": item.get("minutes"), "reason": item.get("reason")}
        for item in result["plan"] if item.get("kept", True)
    ]
    data = {
        "previous_daily_minutes": result["previous_daily_minutes"],
        "daily_minutes": result["daily_minutes"],
        "changes": texts.get("changes") or [],
        "plan": kept,
        "generated_by": texts.get("generated_by"),
        "model_error": texts.get("model_error"),
    }

    dropped = [c.get("task") for c in data["changes"] if c.get("action") == "dropped"]
    decisions = list(session.get("decisions") or [])
    decisions.append({
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "choice": f"每日学习时间调整为 {data['daily_minutes']} 分钟",
        "reason": (f"用户把每日学习时间从 {data['previous_daily_minutes']} 分钟改为 {data['daily_minutes']} 分钟；"
                   f"按优先级暂缓 {len(dropped)} 项：" + "、".join(dropped)
                   if dropped else
                   f"用户把每日学习时间从 {data['previous_daily_minutes']} 分钟改为 {data['daily_minutes']} 分钟，计划已重排"),
        "alternatives": [],
    })
    session_store.update_fields(
        session_id,
        plan={
            "daily_minutes": data["daily_minutes"],
            "items": kept,
            "changes": data["changes"],
            "dropped": [c for c in data["changes"] if c.get("action") == "dropped"],
        },
        profile={**(session.get("profile") or {}), "daily_minutes": data["daily_minutes"]},
        decisions=decisions,
        expected_revision=session["revision"],
    )
    data["revision"] = session_store.snapshot(session_id).get("revision")
    return response.ok(data)
