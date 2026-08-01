"""统一 CLI 入口；延迟加载以避免 ``python -m`` 重复导入警告。"""

from typing import Any


def __getattr__(name: str) -> Any:
    if name == "app":
        from .main import app

        return app
    raise AttributeError(name)

__all__ = ["app"]
