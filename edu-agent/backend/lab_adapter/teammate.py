"""队友 C 的实现接入点（SPEC §10.1 / §10.6）。

队友 C 交付两个**同步**函数：
```python
start_lab(context: dict) -> dict
submit_answer(context: dict, question_id: str, answer: str) -> dict
```
本 adapter 负责：动态导入 → 线程池执行（带超时）→ 结构校验。
**任何失败都抛 `LabError`**，绝不静默切 mock（SPEC §10.5）。
"""
from __future__ import annotations

import asyncio
import importlib
import os

from .. import config
from .base import LabAdapter, LabError, normalize_result

#: 队友 C 的实现模块；可用环境变量 LAB_TEAMMATE_MODULE 覆盖
DEFAULT_MODULE = "lab.teammate_impl"


class TeammateLabAdapter(LabAdapter):
    name = "teammate"

    def __init__(self, module_name: str | None = None):
        self.module_name = module_name or os.environ.get("LAB_TEAMMATE_MODULE") or DEFAULT_MODULE
        self._module = None

    # ---------------------------------------------------------------- 内部
    def _load(self):
        if self._module is None:
            try:
                self._module = importlib.import_module(self.module_name)
            except ImportError as exc:
                raise LabError(
                    f"实验室模块 {self.module_name} 未接入（LAB_ADAPTER=teammate）：{exc}"
                ) from exc
        return self._module

    async def _call(self, func_name: str, *args):
        module = self._load()
        func = getattr(module, func_name, None)
        if not callable(func):
            raise LabError(f"实验室模块 {self.module_name} 缺少函数 {func_name}")
        try:
            return await asyncio.wait_for(asyncio.to_thread(func, *args), timeout=config.LAB_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise LabError(f"实验室调用超时（{config.LAB_TIMEOUT:.0f}s）") from exc
        except LabError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 边界处统一转成 LabError
            raise LabError(f"实验室调用失败：{exc}") from exc

    # ------------------------------------------------------------- 接口
    async def start(self, context: dict) -> dict:  # type: ignore[override]
        return normalize_result(await self._call("start_lab", context), is_demo=False)

    async def answer(self, context: dict, question_id: str, answer: str) -> dict:  # type: ignore[override]
        return normalize_result(
            await self._call("submit_answer", context, question_id, answer), is_demo=False
        )
