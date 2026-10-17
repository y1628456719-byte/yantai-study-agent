"""路由公共依赖。"""
from __future__ import annotations

from ..core.errors import session_not_found
from ..services import session_store


def require_session(session_id: str) -> dict:
    if not session_store.exists(session_id):
        raise session_not_found(session_id)
    return session_store.snapshot(session_id)
