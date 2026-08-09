"""文件夹式学习项目（Issue 19）。"""

from bridges.learning_projects.service import (
    LearningProjectError,
    LearningProjectRecord,
    LearningProjectService,
)
from bridges.learning_projects.migration import (
    ProjectMigrationError,
    ProjectMigrationService,
    project_writes_frozen,
)

__all__ = [
    "LearningProjectError",
    "LearningProjectRecord",
    "LearningProjectService",
    "ProjectMigrationError",
    "ProjectMigrationService",
    "project_writes_frozen",
]
