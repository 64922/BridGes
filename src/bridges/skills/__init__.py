"""内置声明式 SKILL 宿主（Issue 28）。

随应用发布且只读的内置 SKILL 在此注册（ADR 0010/0011）：稳定注册标识、
固定版本与只读来源；后续插件治理页（Issue 34）只负责展示与治理，不能
改变其净室与事实锁合同。
"""

from bridges.skills.registry import SkillRegistry, create_builtin_registry

__all__ = ["SkillRegistry", "create_builtin_registry"]
