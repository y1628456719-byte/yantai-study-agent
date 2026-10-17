"""统一错误定义（SPEC §8.4）。

约定：
- 每个错误都有稳定的 ``code``、对应的 HTTP 状态码、以及**可直接展示给用户的中文 message**（N25）。
- 路由层只负责 ``raise AppError(...)``；序列化由 ``core.response`` 的异常处理器统一完成。
- 禁止把失败包装成空的成功结果（§7.0）。
"""
from __future__ import annotations

# code -> (http_status, retryable)
ERROR_TABLE: dict[str, tuple[int, bool]] = {
    "SESSION_NOT_FOUND": (404, False),
    "MATERIAL_NOT_FOUND": (404, False),
    "INVALID_ARGUMENT": (400, False),
    "TARGET_NOT_SELECTED": (409, False),
    "LAB_UNAVAILABLE": (503, False),
    "MODEL_UNAVAILABLE": (503, True),
    "CONFLICT": (409, False),
    "INTERNAL_ERROR": (500, False),
}


class AppError(Exception):
    """所有业务异常的基类。"""

    def __init__(self, code: str, message: str, *, retryable: bool | None = None):
        if code not in ERROR_TABLE:
            raise ValueError(f"未知错误码：{code}")
        status, default_retryable = ERROR_TABLE[code]
        self.code = code
        self.message = message
        self.http_status = status
        self.retryable = default_retryable if retryable is None else retryable
        super().__init__(message)

    def to_error(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


def session_not_found(session_id: str) -> AppError:
    return AppError("SESSION_NOT_FOUND", f"会话不存在或已过期（{session_id}），请重新创建会话。")


def material_not_found(material_id: str) -> AppError:
    return AppError("MATERIAL_NOT_FOUND", f"资料不存在（{material_id}），可能已被删除。")


def invalid_argument(message: str) -> AppError:
    return AppError("INVALID_ARGUMENT", message)


def target_not_selected() -> AppError:
    return AppError("TARGET_NOT_SELECTED", "还没有确认目标院校，请先在推荐结果里选定一所学校。")


def conflict(message: str) -> AppError:
    return AppError("CONFLICT", message)


def lab_unavailable(detail: str = "") -> AppError:
    return AppError(
        "LAB_UNAVAILABLE",
        "盲点实验室暂时不可用，请稍后重试或在设置里切回 mock 模式。" + (f"（{detail}）" if detail else ""),
    )


def model_unavailable(detail: str = "") -> AppError:
    return AppError(
        "MODEL_UNAVAILABLE",
        "模型服务调用失败，本次没有返回结果。请检查网络或稍后重试。" + (f"（{detail}）" if detail else ""),
        retryable=True,
    )
