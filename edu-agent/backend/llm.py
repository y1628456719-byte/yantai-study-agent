"""模型调用入口（SPEC §11）。

主入口是 **`generate_json(messages, schema)`**：
- 返回**必须经结构校验**，失败即报错，不返回半成品
- 最多 2 次退避重试，超时 60~120s
- 密钥只存服务端环境变量；实验室不另配密钥
- `MOCK_LLM=1` 时不发真实请求（离线演示用），返回 schema 兼容的占位结构并标记 `_mock`

【N32 实现约定】模型**只输出文本**，不参与维度取舍与排序——排序由硬规则（N8）给出。
"""
from __future__ import annotations

import asyncio
import json
import re
import logging
import time
from collections import deque

import httpx

from . import config

logger = logging.getLogger("edu_agent.model_calls")
CALLS = deque(maxlen=256)


def _safe_metadata(value):
    if not isinstance(value, str):
        return None
    return value.replace(config.LLM_API_KEY, "[redacted]")[:128] if config.LLM_API_KEY else value[:128]


def _record_call(started, status, **fields):
    meta = {"model_requested": config.LLM_MODEL, "status": status,
            "duration_seconds": round(time.monotonic() - started, 3), **fields}
    CALLS.append(meta)
    logger.info("MODEL_CALL %s", json.dumps(meta, ensure_ascii=False))


class LLMError(RuntimeError):
    """模型调用失败（含超时、HTTP 错误、结构校验失败）。"""


class LLMResponse:
    def __init__(self, content: str = "", tool_calls=None, raw=None):
        self.content = content or ""
        self.tool_calls = tool_calls or []
        self.raw = raw


# --------------------------------------------------------------------------
# 底层：一次 chat/completions
# --------------------------------------------------------------------------

async def _post_chat(messages, temperature: float, json_mode: bool) -> str:
    if not config.LLM_API_KEY:
        raise LLMError(
            "未配置 LLM_API_KEY。请复制 .env.example 为 .env 并填入密钥，"
            "或设置 MOCK_LLM=1 使用离线演示模式。"
        )
    payload = {
        "model": config.LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    # These are the explicitly authorized AI Ping models, all supporting max.
    effort = "max" if config.LLM_MODEL in {"DeepSeek-V4.1-Flash", "Kimi-K3", "GLM-5.3"} else None
    if effort:
        payload.update(reasoning_effort=effort, thinking={"type": "enabled"})
    if json_mode and config.LLM_JSON_MODE:
        payload["response_format"] = {"type": "json_object"}
    url = f"{config.LLM_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=config.LLM_TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        _record_call(started, "timeout", requested_reasoning_effort=effort)
        raise LLMError(f"模型服务超时（{config.LLM_TIMEOUT:.0f}s）") from exc
    except httpx.HTTPError as exc:
        _record_call(started, "transport_error", requested_reasoning_effort=effort)
        raise LLMError("请求模型服务失败。") from None

    if resp.status_code != 200:
        _record_call(started, "http_error", http_status=resp.status_code, requested_reasoning_effort=effort)
        raise LLMError(f"模型服务返回 HTTP {resp.status_code}。")
    try:
        data = resp.json()
        choice = data["choices"][0]
        content = choice["message"].get("content")
        if not isinstance(content, str) or not content.strip() or choice.get("finish_reason") == "length":
            raise ValueError()
    except (KeyError, IndexError, ValueError, TypeError, AttributeError):
        _record_call(started, "invalid_response", requested_reasoning_effort=effort)
        raise LLMError("模型返回格式异常或输出不完整。") from None
    _record_call(started, "ok", model_returned=_safe_metadata(data.get("model")),
                 request_id=_safe_metadata(data.get("id")), finish_reason=_safe_metadata(choice.get("finish_reason")),
                 usage={k: data.get("usage", {}).get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                        if isinstance(data.get("usage"), dict) and type(data["usage"].get(k)) is int},
                 requested_reasoning_effort=effort)
    return content


async def chat(messages, tools=None, temperature: float = 0.3) -> LLMResponse:
    """兼容旧调用的纯文本对话（不含函数调用）。"""
    if config.MOCK_LLM:
        return LLMResponse(content="（离线演示模式）已收到。")
    content = await _post_chat(messages, temperature, json_mode=False)
    return LLMResponse(content=content)


# --------------------------------------------------------------------------
# 主入口：结构化 JSON
# --------------------------------------------------------------------------

_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def _extract_json(text: str) -> dict | None:
    """从模型输出里抠出 JSON（容忍 ```json 包裹与前后废话）。"""
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK.search(text)
    if match:
        try:
            value = json.loads(match.group())
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def validate_schema(payload: dict, schema: dict) -> list[str]:
    """极简 JSON Schema 校验（object 根、type/properties/required/items）。

    返回错误列表，空列表表示通过。**故意保持极小**，避免引入额外依赖。
    """
    errors: list[str] = []

    def check(value, spec, path: str):
        expected = spec.get("type")
        if expected == "object":
            if not isinstance(value, dict):
                errors.append(f"{path}: 期望 object，实际 {type(value).__name__}")
                return
            for key in spec.get("required", []):
                if key not in value:
                    errors.append(f"{path}.{key}: 缺少必填字段")
            for key, subspec in (spec.get("properties") or {}).items():
                if key in value:
                    check(value[key], subspec, f"{path}.{key}")
        elif expected == "array":
            if not isinstance(value, list):
                errors.append(f"{path}: 期望 array，实际 {type(value).__name__}")
                return
            items = spec.get("items")
            if items:
                for idx, item in enumerate(value[:20]):
                    check(item, items, f"{path}[{idx}]")
        elif expected == "string":
            if not isinstance(value, str):
                errors.append(f"{path}: 期望 string，实际 {type(value).__name__}")
        elif expected == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                errors.append(f"{path}: 期望 integer，实际 {type(value).__name__}")
        elif expected == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                errors.append(f"{path}: 期望 number，实际 {type(value).__name__}")
        elif expected == "boolean":
            if not isinstance(value, bool):
                errors.append(f"{path}: 期望 boolean，实际 {type(value).__name__}")
        if "enum" in spec and value not in spec["enum"]:
            errors.append(f"{path}: 值不在允许集合")

    check(payload, schema, "$")
    return errors


def schema_skeleton(spec: dict):
    """按 schema 造一个"形状正确"的骨架（键名/层级/类型都对，值用占位符）。

    为什么要它：OpenAI 兼容网关上的 `response_format=json_object` **只保证是 JSON**，
    不保证键名。实测把 schema 只放在代码里、提示词里只说"只输出 JSON"时，模型会自创
    键名（例：`summary/schools/comment`），导致结构校验必然失败。把骨架写进提示词，
    模型才会照着填（见 docs/API.md「模型输出契约」一节）。
    """
    kind = spec.get("type")
    if kind == "object":
        return {
            key: schema_skeleton((spec.get("properties") or {}).get(key) or {})
            for key in (spec.get("required") or list((spec.get("properties") or {}).keys()))
        }
    if kind == "array":
        return [schema_skeleton(spec.get("items") or {})]
    if kind == "string":
        return "中文说明"
    if kind in ("integer", "number"):
        return 0
    if kind == "boolean":
        return False
    if kind == "enum" or spec.get("enum"):
        return (spec.get("enum") or [None])[0]
    return None


def schema_contract(schema: dict) -> str:
    """把 schema 渲染成"必须照抄的 JSON 形状"提示词。"""
    skeleton = json.dumps(schema_skeleton(schema), ensure_ascii=False, indent=2)
    return (
        "输出格式契约（**必须严格遵守**：键名、层级、类型都不得改动；"
        "数组元素个数按实际数据条数；不要新增或遗漏字段；只输出这个 JSON，"
        "不要解释、不要 markdown 代码块）：\n"
        f"{skeleton}\n"
        "把上面 JSON 里的占位值（中文说明 / 0 / false）替换成真实内容。"
    )


async def generate_json(messages: list[dict], schema: dict, *, temperature: float = 0.2,
                        mock_factory=None, max_retries: int | None = None) -> dict:
    """SPEC §11 的模型入口：结构化输出 + 校验 + 有限重试。

    - `mock_factory(schema)` 在 `MOCK_LLM=1` 时用于生成占位结构
    - 结构校验失败会**带错误信息重试**，仍失败则抛 `LLMError`（不返回半成品）
    - 真实调用前会把 `schema` 的骨架作为"输出格式契约"追加进消息，否则模型会自创键名
    """
    retries = config.LLM_MAX_RETRIES if max_retries is None else max_retries
    if config.MOCK_LLM:
        payload = mock_factory(schema) if mock_factory else _generic_mock(schema)
        if isinstance(payload, dict):
            payload.setdefault("_mock", True)
        return payload

    messages = list(messages) + [{"role": "system", "content": schema_contract(schema)}]
    last_error = ""
    for attempt in range(retries + 1):
        try:
            content = await _post_chat(messages, temperature, json_mode=True)
        except LLMError as exc:
            last_error = str(exc)
            if attempt < retries:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            raise

        payload = _extract_json(content)
        if payload is None:
            last_error = "模型输出不是合法 JSON。"
        else:
            errors = validate_schema(payload, schema)
            if not errors:
                return payload
            last_error = "结构校验失败：" + "；".join(errors[:5])

        if attempt < retries:
            messages = messages + [
                {"role": "user", "content": f"上一次输出不合法（{last_error}）。请只输出符合 schema 的 JSON。"}
            ]
            await asyncio.sleep(1.5 * (attempt + 1))

    raise LLMError(last_error or "模型输出不合法")


def _generic_mock(schema: dict) -> dict:
    """按 schema 生成最小可用的占位结构（离线演示）。"""
    def build(spec):
        kind = spec.get("type")
        if kind == "object":
            out = {}
            for key, sub in (spec.get("properties") or {}).items():
                if key in (spec.get("required") or []):
                    out[key] = build(sub)
            return out
        if kind == "array":
            return []
        if kind == "string":
            return "（离线演示模式占位文本）"
        if kind == "integer":
            return 0
        if kind == "number":
            return 0
        if kind == "boolean":
            return False
        return None

    return build(schema)
