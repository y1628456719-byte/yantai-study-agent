"""同源前端桥接入口，Cookie 仅关联主数据库的随机会话 ID。"""
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ..core.errors import AppError
from ..core.errors import invalid_argument
from ..services import journey, lab_service, session_store
from .. import config

router = APIRouter(tags=["frontend"])
COOKIE = "edu_session"


def _cookie_name():
    # Cookies share a host across ports: keep mock/live previews from replacing each other.
    mode = config.LAB_BLINDSPOT_MODE if config.LAB_ADAPTER == "blindspot" else "mock"
    return COOKIE + "_" + mode


class ActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=128, strict=True)
    action: str
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    expected_revision: int | None = Field(default=None, ge=0, strict=True)
    params: dict = Field(default_factory=dict)
    question_id: str | None = None
    answer: str | None = None
    feedback: str | None = None
    minutes: int | None = Field(default=None, ge=1, le=1440, strict=True)


class ResetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=128, strict=True)
    expected_revision: int = Field(ge=0, strict=True)


class PrepareIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=128, strict=True)
    expected_revision: int = Field(ge=0, strict=True)
    request_id: str = Field(min_length=1, max_length=128, strict=True)
    goal: str = Field(max_length=2000, strict=True)
    use_demo: bool = Field(default=False, strict=True)


class JourneyReturnIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=128, strict=True)
    expected_revision: int = Field(ge=0, strict=True)
    request_id: str = Field(min_length=1, max_length=128, strict=True)


def _session(request: Request, response: Response, *, create=False):
    cookie_name = _cookie_name()
    session_id = request.cookies.get(cookie_name) or request.cookies.get(COOKIE)
    if session_id and session_store.exists(session_id):
        if not request.cookies.get(cookie_name):
            response.set_cookie(cookie_name, session_id, httponly=True, samesite="strict", path="/", max_age=30*86400)
        return session_id
    if not create:
        raise AppError("SESSION_NOT_FOUND", "请先读取页面状态以创建会话。")
    session_id = session_store.create_session()["session_id"]
    response.set_cookie(cookie_name, session_id, httponly=True, samesite="strict", path="/", max_age=30*86400)
    response.headers["Cache-Control"] = "no-store"
    return session_id


@router.get("/api/state")
async def state(request: Request, response: Response):
    session_id = _session(request, response, create=True)
    response.headers["Cache-Control"] = "no-store"
    return await lab_service.public_state(session_id)


@router.post("/api/prepare")
@router.post("/api/journey/prepare")
async def prepare(payload: PrepareIn, request: Request, response: Response):
    """创建或续接准备旅程，并和当前实验室会话一起按 revision 原子保存。"""
    cookie_name = _cookie_name()
    cookie_session_id = request.cookies.get(cookie_name)
    cached = journey.cached_prepare(
        session_id=payload.session_id, expected_revision=payload.expected_revision,
        request_id=payload.request_id, goal=payload.goal, use_demo=payload.use_demo)
    if cached is not None:
        target_id = cached["state"]["session_id"]
        if cookie_session_id not in {payload.session_id, target_id}:
            raise AppError("CONFLICT", "当前页面已切换到其他档案，未重放该准备请求。")
        response.set_cookie(cookie_name, target_id, httponly=True, samesite="strict",
                            path="/", max_age=30*86400)
        response.headers["Cache-Control"] = "no-store"
        return cached["state"]
    if cookie_session_id != payload.session_id:
        raise AppError("CONFLICT", "页面会话已切换，请刷新页面后重试；本次准备未执行。")
    if not session_store.exists(payload.session_id):
        raise AppError("CONFLICT", "页面会话已失效，请刷新页面后重试；本次准备未执行。")
    current = await lab_service.public_state(payload.session_id)
    result = await journey.prepare(
        session_id=payload.session_id,
        expected_revision=payload.expected_revision,
        request_id=payload.request_id,
        goal=payload.goal,
        use_demo=payload.use_demo,
        base_state=current,
    )
    target_id = result["state"]["session_id"]
    if target_id != cookie_session_id:
        response.set_cookie(cookie_name, target_id, httponly=True, samesite="strict",
                            path="/", max_age=30*86400)
    response.headers["Cache-Control"] = "no-store"
    return result["state"]


@router.post("/api/journey/return")
async def journey_return(payload: JourneyReturnIn, request: Request, response: Response):
    """Return only to the origin privately linked by this isolated demo session."""
    cookie_name = _cookie_name()
    cookie_session_id = request.cookies.get(cookie_name)
    cached = journey.cached_return(session_id=payload.session_id,
                                   expected_revision=payload.expected_revision,
                                   request_id=payload.request_id)
    if cached is not None:
        target_id = cached["state"]["session_id"]
        if cookie_session_id not in {payload.session_id, target_id}:
            raise AppError("CONFLICT", "当前页面已切换到其他档案，未重放该返回请求。")
        response.set_cookie(cookie_name, target_id, httponly=True, samesite="strict",
                            path="/", max_age=30*86400)
        response.headers["Cache-Control"] = "no-store"
        return cached["state"]
    if cookie_session_id != payload.session_id:
        raise AppError("CONFLICT", "页面会话已切换，无法从当前档案返回。")
    if not session_store.exists(payload.session_id):
        raise AppError("CONFLICT", "演示档案已失效，无法返回。")
    result = await journey.return_to_origin(
        session_id=payload.session_id,
        expected_revision=payload.expected_revision,
        request_id=payload.request_id,
    )
    target_id = result["state"]["session_id"]
    response.set_cookie(cookie_name, target_id, httponly=True, samesite="strict",
                        path="/", max_age=30*86400)
    response.headers["Cache-Control"] = "no-store"
    return result["state"]


@router.post("/api/action")
async def action(payload: ActionIn, request: Request, response: Response):
    session_id = _session(request, response)
    if payload.session_id != session_id:
        raise AppError("CONFLICT", "页面会话已切换，请刷新页面后重试；本次操作未执行。")
    flat = {k: getattr(payload, k) for k in ("question_id", "answer", "feedback", "minutes")
            if k in payload.model_fields_set}
    if set(flat) & set(payload.params):
        raise invalid_argument("动作参数不能同时出现在顶层和 params 中。")
    result = await lab_service.execute(session_id, payload.action, {**payload.params, **flat},
        request_id=payload.request_id, expected_revision=payload.expected_revision)
    response.headers["Cache-Control"] = "no-store"
    return result["state"]


@router.post("/api/reset")
async def reset(payload: ResetIn, request: Request, response: Response):
    """恢复空白默认会话；旧档案、资料和实验历史全部保留。"""
    cookie_name = _cookie_name()
    session_id = request.cookies.get(cookie_name)
    if session_id != payload.session_id:
        raise AppError("CONFLICT", "页面会话已切换，请刷新页面后再恢复默认。")
    # 与创建新会话一起持有SQLite写锁，避免版本核对后又被别的写请求推进。
    # create_session保留原有空档案默认值，并提交此事务；不更新旧会话。
    with session_store._lock:
        conn = session_store.get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if not session_store.exists(session_id):
                raise AppError("CONFLICT", "页面会话已失效，请刷新页面后再恢复默认。")
            current = session_store.snapshot(session_id)
            if current["revision"] != payload.expected_revision:
                raise AppError("CONFLICT", "会话状态已更新，请刷新页面后再恢复默认。")
            created = session_store.create_session()
        except Exception:
            conn.rollback()
            raise
    result = await lab_service.public_state(created["session_id"])
    response.set_cookie(cookie_name, created["session_id"], httponly=True, samesite="strict", path="/", max_age=30*86400)
    response.headers["Cache-Control"] = "no-store"
    return result
