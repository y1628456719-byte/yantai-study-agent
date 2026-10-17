"""HTTP 层：SPEC §7 的 12 个端点 + 3 个 additive 端点（agent / lab.action / materials 列表）。"""
from __future__ import annotations

from fastapi import APIRouter

from . import agent, lab, materials, meta, plan, sessions

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(agent.router)
api_router.include_router(sessions.router)
api_router.include_router(materials.router)
api_router.include_router(lab.router)
api_router.include_router(plan.router)
api_router.include_router(meta.router)

__all__ = ["api_router"]
