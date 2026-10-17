"""统一响应封装 + request_id（SPEC §7.0）。

成功：{"ok": true,  "data": {...}, "error": null,           "request_id": "..."}
失败：{"ok": false, "data": null,  "error": {...},          "request_id": "..."}
"""
from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .errors import AppError

logger = logging.getLogger("edu_agent")

_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def current_request_id() -> str:
    return _request_id.get()


def ok(data=None) -> dict:
    return {"ok": True, "data": _public_data(data), "error": None, "request_id": current_request_id()}


def _public_data(data):
    if isinstance(data, list):
        return [_public_data(v) for v in data]
    if isinstance(data, dict):
        if "learning_state" in data:
            from ..services.session_store import public_session
            data = public_session(data)
        return {k: _public_data(v) for k, v in data.items()}
    return data


def fail_body(error: AppError) -> dict:
    return {
        "ok": False,
        "data": None,
        "error": error.to_error(),
        "request_id": current_request_id(),
    }


def install(app: FastAPI) -> None:
    """注册 request_id 中间件与全局异常处理器。"""

    @app.middleware("http")
    async def _request_id_middleware(request: Request, call_next):
        rid = request.headers.get("X-Request-Id") or new_request_id()
        token = _request_id.set(rid)
        try:
            response = await call_next(request)
        finally:
            _request_id.reset(token)
        response.headers["X-Request-Id"] = rid
        return response

    @app.exception_handler(AppError)
    async def _app_error_handler(_request: Request, exc: AppError):
        logger.warning("AppError %s: %s", exc.code, exc.message)
        return JSONResponse(status_code=exc.http_status, content=fail_body(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_request: Request, exc: RequestValidationError):
        detail = "; ".join(
            f"{'.'.join(str(p) for p in err.get('loc', ())[1:])}: {err.get('msg', '')}"
            for err in exc.errors()[:5]
        )
        error = AppError("INVALID_ARGUMENT", f"请求参数不合法：{detail}")
        return JSONResponse(status_code=error.http_status, content=fail_body(error))

    @app.exception_handler(Exception)
    async def _unhandled_handler(_request: Request, exc: Exception):
        logger.exception("未处理异常: %s", exc)
        error = AppError("INTERNAL_ERROR", "服务内部错误，本次请求没有完成。请查看服务端日志。")
        return JSONResponse(status_code=error.http_status, content=fail_body(error))
