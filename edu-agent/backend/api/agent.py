"""Agent 入口（additive）：一句话进，Agent 自己决定调用哪些工具。

`POST /api/v1/agent` —— SPEC 的 12 个端点保持不变；这是它们的"另一个调用方"。
工具调用走进程内 ASGI（`backend/tools.py`），因此校验、错误码、持久化、revision
全部复用同一套服务层逻辑，Agent 不拥有第二份实现。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..agent import run_agent
from ..core import response
from ..core.errors import invalid_argument, model_unavailable
from ..core.schemas import AgentIn
from ..llm import LLMError

router = APIRouter(tags=["agent"])


@router.post("/agent")
async def agent_turn(payload: AgentIn, request: Request):
    """跑一轮 Agent：它可能会连续调用多个工具，最后给一句话回复。"""
    message = (payload.message or "").strip()
    if not message:
        raise invalid_argument("message 不能为空。")

    history = [{"role": item.role, "content": item.content} for item in payload.history]
    try:
        result = await run_agent(
            request.app,
            message=message,
            session_id=payload.session_id,
            history=history,
        )
    except LLMError as exc:
        # 决策层模型不可用：如实报 503，不静默退回规则（N25 / SPEC §8.4）
        raise model_unavailable(str(exc)) from exc
    # 公开的是已经执行的动作与结果，不展示模型内部 decision.thought。
    keys = {"step", "action", "tool", "ok", "result_summary", "latency_ms", "error", "code"}
    result["trace"] = [{k: v for k, v in entry.items() if k in keys}
                       for entry in result.get("trace", [])]
    return response.ok(result)
