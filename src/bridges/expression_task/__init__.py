"""表达任务契约编译（Issue 03；Issue 21 从退役 SKILL 包独立出来）。

文章专用编排退役后，本包只保留确定性契约编译器：普通聊天的轻量表达
策略（Issue 07）与路由的改写动作词表共用它，编译结果仍由
``bridges.contracts.expression_task`` 定义。
"""

from bridges.expression_task.contract_compiler import (
    CompileRequest,
    CompileResult,
    ContractVersionError,
    compile_task_contract,
)

__all__ = [
    'CompileRequest',
    'CompileResult',
    'ContractVersionError',
    'compile_task_contract',
]
