"""考研择校决策 Agent —— FastAPI 应用入口（SPEC v1.1 §7 契约）。

端点全部挂在 `/api/v1`；响应统一 `{ok,data,error,request_id}`。
旧的单科备考闭环代码（agent/tools/prompts/store）已作废，仅保留在仓库里作对照，
不在本应用注册任何路由。
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .api import api_router
from .api.frontend import router as frontend_router
from .core import response as response_mod
from .core import subjects as subject_table
from .services import school_data, session_store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger("edu_agent")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    session_store.init_db()
    logger.info(
        "启动完成：院校数据 %s；LAB_ADAPTER=%s；MOCK_LLM=%s；模型密钥=%s",
        school_data.school_ids(), config.LAB_ADAPTER, bool(config.MOCK_LLM),
        "已配置" if config.LLM_API_KEY else "未配置（走规则文案，不影响可演示性）",
    )
    yield


app = FastAPI(
    title="考研择校决策 Agent",
    version="1.1.0",
    description="莱森地平线 LAISEN HORIZON · 赛道 03 Agent 驱动的真实场景应用（教育学习辅助）",
    lifespan=lifespan,
)
response_mod.install(app)


@app.middleware("http")
async def local_origin(request: Request, call_next):
    """本机同源工具：拒绝 DNS 重绑定 Host 和跨站浏览器写请求。"""
    from urllib.parse import urlsplit
    allowed = {"127.0.0.1", "localhost", "::1"}
    if request.client and request.client.host == "testclient":
        allowed.add("testserver")
    protected = request.method not in {"GET", "HEAD", "OPTIONS"} or request.url.path == "/api/state"
    origin = request.headers.get("origin")
    origin_ok = True
    try:
        host_parts = urlsplit("//" + request.headers.get("host", ""))
        host_ok = (host_parts.hostname in allowed and host_parts.username is None
                   and not host_parts.path and not host_parts.query and not host_parts.fragment)
        if protected and origin:
            parsed = urlsplit(origin)
            origin_ok = (parsed.scheme in {"http", "https"} and parsed.hostname in allowed
                         and not parsed.path and not parsed.query and not parsed.fragment
                         and parsed.netloc.lower() == request.headers.get("host", "").lower())
    except ValueError:
        host_ok = origin_ok = False
    if not host_ok or not origin_ok:
        return JSONResponse(status_code=403, content={"ok": False, "data": None,
            "error": {"code": "FORBIDDEN", "message": "仅允许本机同源访问。", "retryable": False},
            "request_id": response_mod.current_request_id()})
    return await call_next(request)


@app.get("/api/health")
async def health():
    """运维用健康检查（不在 12 端点契约内，但便于现场排障）。"""
    return {
        "ok": True,
        "data": {
            "status": "ok",
            "schools": school_data.school_ids(),
            "school_count": len(school_data.school_ids()),
            "lab_adapter": config.LAB_ADAPTER,
            "mock_llm": bool(config.MOCK_LLM),
            "model_key_configured": bool(config.LLM_API_KEY),
            "subjects": len(subject_table.subjects()),
        },
        "error": None,
        "request_id": response_mod.current_request_id(),
    }


@app.get("/api/version")
async def version():
    return response_mod.ok({"spec": "v1.1", "api_prefix": "/api/v1"})


app.include_router(api_router)
app.include_router(frontend_router)

# ---- 前端静态文件（队友 B 的产物；不存在时不影响后端） ----
if config.FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(config.FRONTEND_DIR)), name="static")

    @app.get("/")
    async def index():
        page = Path(config.FRONTEND_DIR) / "index.html"
        if page.exists():
            return FileResponse(str(page))
        return JSONResponse({"ok": False, "data": None,
                             "error": {"code": "NOT_FOUND", "message": "前端尚未就绪"},
                             "request_id": response_mod.current_request_id()})

    # index.html 里用的是相对路径（href="style.css" / src="app.js"）：从 / 打开时
    # 会请求 /style.css、/app.js。这里把 frontend/ 兜底挂到根路径（所有 API 路由与 /docs
    # 都已先注册，仍然优先命中），否则页面只有 HTML 而没有样式与脚本。
    app.mount("/", StaticFiles(directory=str(config.FRONTEND_DIR), html=True), name="frontend")
else:
    @app.get("/")
    async def index_placeholder():
        return JSONResponse({
            "ok": True,
            "data": {
                "service": "考研择校决策 Agent 后端",
                "api_prefix": "/api/v1",
                "docs": "/docs",
                "note": "前端目录尚未就绪（队友 B 负责），此响应来自后端。",
            },
            "error": None,
            "request_id": response_mod.current_request_id(),
        })


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8000")),
        reload=False,
    )
