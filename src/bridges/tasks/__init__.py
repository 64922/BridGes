"""跨轮任务领域模块（工单 08）：任务、不可变版本、有效条件与澄清等待。

权威写模型在 :mod:`bridges.tasks.repository`，语义裁决在
:mod:`bridges.tasks.service`，对外合同在 :mod:`bridges.contracts.tasks`。
"""

from __future__ import annotations

from bridges.tasks.repository import (
    TaskError,
    TaskNotFound,
    TaskRepository,
    TaskStateConflict,
    TaskVersionConflict,
)
from bridges.tasks.service import TaskService, user_condition

__all__ = [
    "TaskError",
    "TaskNotFound",
    "TaskRepository",
    "TaskService",
    "TaskStateConflict",
    "TaskVersionConflict",
    "user_condition",
]
