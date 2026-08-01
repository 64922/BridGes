"""存储层统一错误类型。

所有消息面向普通用户与运维输出：使用中文说明数据目录、权限、迁移版本或对象
损坏原因，且不包含宿主敏感绝对路径。
"""

from __future__ import annotations


class StorageError(Exception):
    """数据库或对象库失败。"""
