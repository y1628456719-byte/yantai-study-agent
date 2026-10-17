"""实验室 adapter 工厂（SPEC §10.6）。

`LAB_ADAPTER` 取 `mock` / `teammate` / `blindspot`；**非法取值直接报错，不静默降级**。
对外统一暴露 async 接口（mock 也包一层，路由代码不必区分）。
"""
from __future__ import annotations

import asyncio

from .. import config
from .base import LabAdapter, LabError, build_context, empty_lab_state, normalize_result
from .mock import MockLabAdapter
from .teammate import TeammateLabAdapter

__all__ = [
    "LabAdapter", "LabError", "MockLabAdapter", "TeammateLabAdapter", "BlindspotLabAdapter",
    "get_adapter", "build_context", "empty_lab_state", "normalize_result", "reset_adapter",
]

_adapter = None


def reset_adapter() -> None:
    """丢弃单例（换 `LAB_ADAPTER` 配置或测试时用；不静默降级，重新取时仍会校验取值）。"""
    global _adapter
    _adapter = None


class _AsyncMockAdapter:
    """把同步 mock 包成 async；mock 是纯计算，无需线程池。"""

    name = "mock"

    def __init__(self) -> None:
        self._impl = MockLabAdapter()

    async def start(self, context: dict) -> dict:
        return normalize_result(self._impl.start(context), is_demo=True)

    async def answer(self, context: dict, question_id: str, answer: str) -> dict:
        return normalize_result(self._impl.answer(context, question_id, answer), is_demo=True)


class _AsyncBlindspotAdapter:
    """队友 C 盲点实验室：同步纯计算，放到线程池并加超时（失败转 LabError，不静默降级）。"""

    name = "blindspot"

    def __init__(self) -> None:
        from .blindspot import BlindspotLabAdapter  # 延迟导入：只有启用时才要求 vendored 包存在

        self._impl = BlindspotLabAdapter()
        self._workers = set()

    def create_work_tracker(self):
        from vendor.blindspot_lab.lab.models import ModelWorkTracker
        return ModelWorkTracker()

    @property
    def model_name(self):
        return self._impl.client.model if self._impl.client else None

    async def _run(self, func, *args):
        from vendor.blindspot_lab.lab.models import current_work_tracker
        tracker = current_work_tracker()
        if tracker:
            tracker.begin()
        def work():
            try:
                return func(*args)
            finally:
                if tracker:
                    tracker.end()
        worker = asyncio.create_task(asyncio.to_thread(work))
        self._workers.add(worker)
        def finished(done):
            self._workers.discard(done)
            if not done.cancelled():
                done.exception()
        worker.add_done_callback(finished)
        try:
            # Continue can generate once and repair one explanation: three bounded calls.
            deadline = 3 * min(120, config.LAB_TIMEOUT) + 5
            return await asyncio.wait_for(asyncio.shield(worker), timeout=deadline)
        except asyncio.TimeoutError as exc:
            raise LabError(f"盲点实验室超过 {deadline:.0f} 秒未返回，本次状态未推进。") from exc

    async def start(self, context: dict) -> dict:
        result = await self._run(self._impl.start, context)
        return normalize_result(result, is_demo=True)

    async def answer(self, context: dict, question_id: str, answer: str) -> dict:
        result = await self._run(self._impl.answer, context, question_id, answer)
        return normalize_result(result, is_demo=True)

    async def act(self, context: dict, action: str, **params) -> dict:
        result = await self._run(lambda: self._impl.act(context, action, **params))
        return normalize_result(result, is_demo=True)

    async def public_read(self, context: dict) -> dict:
        return await self._run(self._impl.public_read, context)


def get_adapter():
    global _adapter
    if _adapter is None:
        kind = (config.LAB_ADAPTER or "mock").strip().lower()
        if kind == "mock":
            _adapter = _AsyncMockAdapter()
        elif kind == "teammate":
            _adapter = TeammateLabAdapter()
        elif kind == "blindspot":
            _adapter = _AsyncBlindspotAdapter()
        else:
            raise LabError(f"LAB_ADAPTER 取值非法：{kind!r}（只能是 mock / teammate / blindspot）")
    return _adapter
