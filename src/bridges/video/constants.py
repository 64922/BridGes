"""文生视频固定合同常量；路由、适配器和任务服务共用。

Issue 09 起模型 ID 的单一事实源是 ``bridges.ai.fixed_models``（ADR-0009）：
本模块不再定义任何模型字面量，只从单一事实源再导出历史常量名，保持
既有 ``bridges.video.constants`` 导入路径兼容。变更视频合同常量仍视为
受控变更（ADR-0006 / ADR-0009 合同测试与质量评测）。
"""

from __future__ import annotations

from bridges.ai.fixed_models import (
    VIDEO_DEFAULT_DURATION_SECONDS,
    VIDEO_DEFAULT_SIZE,
    VIDEO_MODEL_ID,
    VIDEO_SUPPORTED_DURATIONS_SECONDS,
    VIDEO_SUPPORTED_SIZES,
)

__all__ = [
    "VIDEO_DEFAULT_DURATION_SECONDS",
    "VIDEO_DEFAULT_SIZE",
    "VIDEO_MODEL_ID",
    "VIDEO_SUPPORTED_DURATIONS_SECONDS",
    "VIDEO_SUPPORTED_SIZES",
]
