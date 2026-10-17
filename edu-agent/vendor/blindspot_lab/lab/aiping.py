"""Optional, explicit network transport for the user's authorized AI Ping key."""
import copy
import ctypes
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

from .errors import LabError


BASE_URL = "https://aiping.cn/api/v1"
AUTHORIZED_MODELS = {"DeepSeek-V4.1-Flash": "max", "Kimi-K3": "max", "GLM-5.3": "max"}


def load_api_key():
    """No automatic credential search: one named env var or one user DPAPI file."""
    value = os.environ.get("AIPING_API_KEY")
    if value:
        return value.strip()
    base = os.environ.get("LOCALAPPDATA")
    path = Path(base) / "BlindspotLab" / "aiping-lab.dpapi" if base else None
    if os.name != "nt" or path is None or not path.is_file():
        raise LabError("MODEL_UNAVAILABLE", "请设置AIPING_API_KEY或提供本机加密凭据文件。")
    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    try:
        encrypted = bytes.fromhex(path.read_text(encoding="ascii").strip())
        buffer = (ctypes.c_ubyte * len(encrypted)).from_buffer_copy(encrypted)
        incoming = Blob(len(encrypted), buffer)
        outgoing = Blob()
        crypt = ctypes.WinDLL("crypt32", use_last_error=True).CryptUnprotectData
        crypt.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Blob)]
        crypt.restype = ctypes.c_int
        if not crypt(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
            raise OSError("DPAPI decryption failed")
        try:
            value = ctypes.string_at(outgoing.data, outgoing.size).decode("utf-16-le").strip()
        finally:
            ctypes.memset(outgoing.data, 0, outgoing.size)
            free = ctypes.WinDLL("kernel32").LocalFree
            free.argtypes = [ctypes.c_void_p]
            free.restype = ctypes.c_void_p
            free(outgoing.data)
    except (OSError, ValueError, UnicodeError):
        raise LabError("MODEL_UNAVAILABLE", "本机加密凭据读取失败。") from None
    if not value:
        raise LabError("MODEL_UNAVAILABLE", "本机加密凭据为空。")
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AipingClient:
    """No model fallback, no routing preference/acceleration, no automatic retry."""

    def __init__(self, api_key=None, model="DeepSeek-V4.1-Flash", max_tokens=12000):
        if model not in AUTHORIZED_MODELS:
            raise ValueError("Model is outside the user-authorized set")
        self._key = api_key if api_key is not None else load_api_key()
        self.model = model
        self.max_tokens = max_tokens
        self.calls = []
        self.opener = urllib.request.build_opener(NoRedirect())

    def _request(self, suffix, body=None, timeout=120):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(BASE_URL + suffix, data=data,
            headers={"Authorization": "Bearer " + self._key, "Content-Type": "application/json"})
        started = time.monotonic()
        try:
            with self.opener.open(request, timeout=timeout) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise LabError("MODEL_OUTPUT_INVALID", "服务返回超过传输长度限制。")
                value = json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Never echo provider bodies, request headers, or the key.
            self.calls.append({"model": self.model, "status": "http_error", "http_status": exc.code,
                               "duration_seconds": round(time.monotonic() - started, 3)})
            raise LabError("MODEL_UNAVAILABLE", "AI Ping请求被拒绝。", http_status=exc.code) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, UnicodeError):
            self.calls.append({"model": self.model, "status": "transport_error",
                               "duration_seconds": round(time.monotonic() - started, 3)})
            raise LabError("MODEL_UNAVAILABLE", "AI Ping连接、超时或响应解析失败。") from None
        return value, round(time.monotonic() - started, 3)

    def models(self):
        value, _ = self._request("/models", timeout=30)
        return [entry["id"] for entry in value.get("data", []) if isinstance(entry.get("id"), str)]

    def complete(self, messages, *, timeout=120, schema=None):
        outgoing = copy.deepcopy(messages)
        if schema is not None:
            outgoing.append({"role": "system", "content": "仅输出JSON，严格遵守以下JSON Schema：" +
                             json.dumps(schema, ensure_ascii=False)})
        body = {"model": self.model, "messages": outgoing, "stream": False,
                "max_tokens": self.max_tokens, "reasoning_effort": AUTHORIZED_MODELS[self.model],
                "thinking": {"type": "enabled"}, "response_format": {"type": "json_object"}}
        value, elapsed = self._request("/chat/completions", body, timeout)
        try:
            choice = value["choices"][0]
            text = choice["message"]["content"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError()
        except (KeyError, IndexError, TypeError, ValueError):
            raise LabError("MODEL_OUTPUT_INVALID", "服务未返回非空文本结果。") from None
        # Do not save reasoning_content or raw provider envelopes.
        self.calls.append({"model_requested": self.model, "model_returned": value.get("model"),
                           "status": "ok", "request_id": value.get("id"),
                           "duration_seconds": elapsed, "usage": value.get("usage"),
                           "finish_reason": choice.get("finish_reason"),
                           "requested_reasoning_effort": AUTHORIZED_MODELS[self.model]})
        if choice.get("finish_reason") == "length":
            raise LabError("MODEL_OUTPUT_INVALID", "服务输出达到长度上限，未作为有效结果保存。")
        return text

    def generate_json(self, messages, schema, timeout_seconds):
        return self.complete(messages, timeout=timeout_seconds, schema=schema)
