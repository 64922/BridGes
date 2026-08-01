"""science_companion —— BridGes 迁移兼容层（过渡期保留，由退出 Issue 删除）。

BridGes 的规范包名已迁移到 ``bridges``；本包不包含实现，只为旧名导入与
``python -m science_companion.*`` 运行路径保留兼容。新代码一律使用
``bridges``，两条入口共享同一份实现，不维护两套代码。
"""

from __future__ import annotations

import sys

import bridges as _bridges

__version__ = _bridges.__version__

# 把旧包名的子模块解析路径指向 bridGes 实现目录，使
# ``import science_companion.ai``、``python -m science_companion.cli.main``
# 等旧名访问落在 bridges 的实现文件上。
sys.modules[__name__].__path__ = _bridges.__path__
