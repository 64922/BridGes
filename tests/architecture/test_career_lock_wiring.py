"""Issue 12 架构测试：Career 网关调用点统一持久化与模型 ID 单一事实源。

静态规则：
- Career 域（``src/bridges/career``）只允许一个网关调用点（``invoke``），
  且该调用点必须位于 ``_invoke_structured``，其结果必须立即交给
  ``_persist_call_lock``（调用后丢弃锁即违规）；禁止 ``stream`` 调用点；
- ``_persist_call_lock`` 必须经 Issue 10 统一 recorder 端口
  （``ModelRunLockRecorder.record_many``）持久化，不允许直接触碰
  chat repository 或裸 INSERT；
- 生产组合（``src/bridges/api/main.py``）必须为 ``CareerPlannerService``
  注入 ``SqliteModelRunLockRecorder``；
- 模型 ID 单一事实源：生产注册表 qwen_structured_output 绑定
  ``fixed_models`` 批准矩阵，与运行锁实际模型一致。
"""

from __future__ import annotations

import ast
from pathlib import Path

from bridges.ai import CapabilityRegistry
from bridges.ai.fixed_models import MODEL_BY_CAPABILITY
from bridges.ai.production import register_builtin_capabilities

REPO_ROOT = Path(__file__).resolve().parents[2]
CAREER_DIR = REPO_ROOT / "src" / "bridges" / "career"
SERVICE_PATH = CAREER_DIR / "service.py"
MAIN_PATH = REPO_ROOT / "src" / "bridges" / "api" / "main.py"


def _gateway_call_sites(tree: ast.AST) -> list[tuple[str, int, str]]:
    """枚举 AST 中 `self._gateway.<method>(...)` 调用点（method, lineno, 函数名）。"""
    hits: list[tuple[str, int, str]] = []

    def enclosing_function(node: ast.AST, root: ast.AST) -> str:
        for parent in ast.walk(root):
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for child in ast.walk(parent):
                    if child is node:
                        return parent.name
        return "<module>"

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or not isinstance(
            func.value, ast.Attribute
        ):
            continue
        if func.value.attr not in ("_gateway", "gateway"):
            continue
        if func.attr in ("invoke", "stream"):
            hits.append((func.attr, node.lineno, enclosing_function(node, tree)))
    return hits


def _function_body(tree: ast.AST, name: str) -> list[ast.stmt] | None:
    """在整棵 AST（含类体内方法）中查找指定函数的方法体。"""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return list(node.body)
    return None


def test_career_has_single_gateway_call_point_wired_to_recorder() -> None:
    """Career 域全部 qwen_structured_output 调用点收敛到 _invoke_structured
    的唯一次 invoke，且调用后立即持久化，不存在调用后丢弃锁的路径。"""
    tree = ast.parse(SERVICE_PATH.read_text(encoding="utf-8"), filename=str(SERVICE_PATH))
    sites = _gateway_call_sites(tree)
    assert len(sites) == 1, (
        "Career 域必须只有唯一网关调用点（invoke），当前发现："
        + repr(sites)
    )
    method, lineno, function = sites[0]
    assert method == "invoke"
    assert function == "_invoke_structured", "唯一调用点必须位于 _invoke_structured"

    body = _function_body(tree, "_invoke_structured")
    assert body is not None
    call_nodes = [
        node
        for node in body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and (
            (
                isinstance(node.value.func, ast.Name)
                and node.value.func.id == "_persist_call_lock"
            )
            or (
                isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == "_persist_call_lock"
            )
        )
    ]
    assert len(call_nodes) == 1, "invoke 之后必须立即持久化锁（_persist_call_lock）"
    invoke_index = next(
        index
        for index, stmt in enumerate(body)
        if isinstance(stmt, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "call_result"
            for target in stmt.targets
        )
        and any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "invoke"
            for node in ast.walk(stmt)
        )
    )
    persist_index = body.index(call_nodes[0])
    assert persist_index > invoke_index, "持久化必须发生在 invoke 之后"
    # invoke 与持久化之间不得有 return/raise（调用后丢弃锁即违规）
    between = body[invoke_index + 1 : persist_index]
    assert not any(
        isinstance(stmt, (ast.Return, ast.Raise)) for stmt in between
    ), "invoke 与持久化之间不得提前返回/抛错丢弃锁"


def test_persist_call_lock_uses_unified_recorder_port() -> None:
    """锁持久化必须走 Issue 10 统一 recorder 端口（record_many），
    不得直连 chat repository 或裸 INSERT。"""
    text = SERVICE_PATH.read_text(encoding="utf-8")
    assert "from bridges.ai.ports import ModelRunLockRecorder, RecordRequest" in text
    tree = ast.parse(text, filename=str(SERVICE_PATH))
    body = _function_body(tree, "_persist_call_lock")
    assert body is not None
    calls: list[str] = []
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            calls.append(node.func.attr)
    assert "record_many" in calls, "_persist_call_lock 必须调用 recorder.record_many"
    assert "record" not in calls, "应使用 record_many 原子批量（含会话关联）"
    # 不得在服务内裸写 SQL（统一 recorder 是唯一持久化入口）
    sql_literals = [
        node.value
        for node in ast.walk(ast.Module(body=body, type_ignores=[]))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "INSERT" in node.value.upper()
    ]
    assert not sql_literals, "服务内不得出现裸 INSERT SQL"
    # 服务不得依赖 chat repository 持久化锁
    assert "ConversationRepository" not in text


def test_production_wiring_injects_recorder() -> None:
    """生产组合必须为 CareerPlannerService 注入统一 recorder（缺审计
    证据即失败关闭，不允许无 recorder 的完成态）。"""
    text = MAIN_PATH.read_text(encoding="utf-8")
    assert "run_lock_recorder=SqliteModelRunLockRecorder(bridges_database)" in text
    assert "from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder" in text


def test_structured_output_model_id_from_fixed_models() -> None:
    """Issue 09 单一事实源：生产注册表与运行锁的 qwen_structured_output
    模型 ID 必须等于 fixed_models 批准矩阵。"""
    approved = MODEL_BY_CAPABILITY["qwen_structured_output"]
    assert approved
    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    record = registry.get("qwen_structured_output", "1")
    assert record.model_id == approved
