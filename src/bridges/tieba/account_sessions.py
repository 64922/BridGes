"""Tieba 专用浏览器会话的账户删除适配器。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from bridges.tieba.browser_runtime import fence_account_profile, remove_account_profile

if TYPE_CHECKING:
    from bridges.tieba.browser_runtime import TiebaBrowserRuntime


class TiebaAccountSessionLifecycle:
    """API 使用运行时清理活动 worker，后台重试使用同一数据目录栅栏。"""

    def __init__(
        self,
        *,
        data_dir: Path | None = None,
        runtime: TiebaBrowserRuntime | None = None,
    ) -> None:
        if (data_dir is None) == (runtime is None):
            raise ValueError("必须指定专用会话运行时或数据目录其中之一")
        self._data_dir = data_dir
        self._runtime = runtime

    def fence(self, account_id: str) -> None:
        if self._runtime is not None:
            self._runtime.fence_account(account_id)
            return
        assert self._data_dir is not None
        fence_account_profile(self._data_dir, account_id)

    def remove(self, account_id: str) -> None:
        if self._runtime is not None:
            self._runtime.remove_account(account_id)
            return
        assert self._data_dir is not None
        remove_account_profile(self._data_dir, account_id)
