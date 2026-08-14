"""静态架构测试：Humanizer 全部模型调用点必须经统一记录接缝（Issue 11 AC）。

规则：
- ``src/bridges/skills/humanizer/service.py`` 内每个 ``self._gateway.invoke``
  调用点的结果变量必须在同一函数内传给 ``self._record_model_run_lock``
  （首参即该结果变量），不允许「调用后丢弃 call_result.lock」的路径；
- 全部调用点必须使用 ``HUMANIZER_CAPABILITY_NAME`` /
  ``HUMANIZER_CAPABILITY_VERSION`` 常量，禁止硬编码能力标识；
- 记录接缝必须同时绑定消息与会话两类业务关联（主/辅），并定义稳定
  失败关闭错误码。
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_PATH = REPO_ROOT / "src" / "bridges" / "skills" / "humanizer" / "service.py"

#: Humanizer 服务内结构化模型调用点总数（首稿、证据安全修订/裁决定向修订
#: 共用 _invoke_draft_model/_invoke_revision_model，旧兼容路径首稿与软门
#: 修复共用 _invoke_model——共三个方法）。
EXPECTED_INVOKE_SITES = 3


def _is_gateway_invoke(call: ast.Call) -> bool:
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "invoke"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "_gateway"
        and isinstance(func.value.value, ast.Name)
        and func.value.value.id == "self"
    )


def _is_record_seam_call(call: ast.Call) -> bool:
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "_record_model_run_lock"
        and isinstance(func.value, ast.Name)
        and func.value.id == "self"
    )


def _collect(tree: ast.AST) -> tuple[list[ast.Call], dict[ast.Call, ast.FunctionDef]]:
    """返回全部 gateway.invoke 调用点及其所在函数。"""
    sites: list[ast.Call] = []
    enclosing: dict[ast.Call, ast.FunctionDef] = {}
    current: ast.FunctionDef | None = None

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            nonlocal current
            previous = current
            current = node
            self.generic_visit(node)
            current = previous

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.visit_FunctionDef(node)  # type: ignore[arg-type]

        def visit_Call(self, node: ast.Call) -> None:
            if _is_gateway_invoke(node):
                sites.append(node)
                enclosing[node] = current  # type: ignore[assignment]
            self.generic_visit(node)

    Visitor().visit(tree)
    return sites, enclosing


def _assignment_target(call: ast.Call) -> str | None:
    """``result = self._gateway.invoke(...)`` 中的结果变量名。"""
    for parent in ast.walk(call):
        pass
    # 直接父级赋值：用文本级定位替代——找包含该 Call 的最小 Assign
    return None


def _result_variable(module: ast.Module, call: ast.Call) -> str | None:
    """在模块树里找把该 Call 作为右值的 Assign，返回左值变量名。"""
    for node in ast.walk(module):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.value is call
        ):
            return node.targets[0].id
    return None


def test_every_gateway_invoke_result_reaches_record_seam() -> None:
    """每个 gateway.invoke 调用点都经统一记录接缝，不丢弃 call_result.lock。"""
    module = ast.parse(
        SERVICE_PATH.read_text(encoding="utf-8"), filename=str(SERVICE_PATH)
    )
    sites, enclosing = _collect(module)
    assert len(sites) == EXPECTED_INVOKE_SITES, (
        f"Humanizer 模型调用点数量变化（期望 {EXPECTED_INVOKE_SITES}，"
        f"实际 {len(sites)}）——新调用点必须显式接入记录接缝。"
    )
    for call in sites:
        result_var = _result_variable(module, call)
        assert result_var is not None, (
            f"{SERVICE_PATH.name}:{call.lineno} gateway.invoke 结果未赋值。"
        )
        function = enclosing[call]
        seam_calls = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call) and _is_record_seam_call(node)
        ]
        assert any(
            seam.args
            and isinstance(seam.args[0], ast.Name)
            and seam.args[0].id == result_var
            for seam in seam_calls
        ), (
            f"{SERVICE_PATH.name}:{call.lineno} 调用点结果 {result_var} 没有"
            "在同一函数内经 self._record_model_run_lock 记录——存在丢弃"
            "call_result.lock 的路径。"
        )


def test_invoke_sites_use_capability_constants_not_literals() -> None:
    """全部调用点使用 HUMANIZER_CAPABILITY_NAME/VERSION 常量。"""
    module = ast.parse(
        SERVICE_PATH.read_text(encoding="utf-8"), filename=str(SERVICE_PATH)
    )
    sites, _ = _collect(module)
    for call in sites:
        args = call.args
        assert len(args) >= 2, f"{SERVICE_PATH.name}:{call.lineno} 调用点参数不足。"
        name_arg, version_arg = args[0], args[1]
        assert (
            isinstance(name_arg, ast.Name)
            and name_arg.id == "HUMANIZER_CAPABILITY_NAME"
        ), f"{SERVICE_PATH.name}:{call.lineno} 能力名必须是 HUMANIZER_CAPABILITY_NAME。"
        assert (
            isinstance(version_arg, ast.Name)
            and version_arg.id == "HUMANIZER_CAPABILITY_VERSION"
        ), (
            f"{SERVICE_PATH.name}:{call.lineno} 能力版本必须是 "
            "HUMANIZER_CAPABILITY_VERSION。"
        )


def test_record_seam_links_message_and_conversation() -> None:
    """记录接缝同时建立消息（主）与会话（辅）业务关联。"""
    text = SERVICE_PATH.read_text(encoding="utf-8")
    assert 'object_type="message"' in text
    assert 'object_type="conversation"' in text
    assert "is_primary=True" in text
    assert "is_primary=False" in text
    # 稳定错误码在模块中定义并被记录接缝使用
    assert 'HUMANIZER_MISSING_RUN_LOCK = "humanizer_missing_run_lock"' in text
    assert 'HUMANIZER_LOCK_PERSIST_FAILED = "humanizer_lock_persist_failed"' in text
    assert 'HUMANIZER_LOCK_BUSINESS_MISMATCH = "humanizer_lock_business_mismatch"' in text


def test_scan_flags_discarded_lock_in_fake_module(tmp_path: Path) -> None:
    """负向控制：扫描器必须能发现「invoke 后不记录」的违规调用点。"""
    fake = tmp_path / "service.py"
    fake.write_text(
        "class Service:\n"
        "    def run(self):\n"
        "        result = self._gateway.invoke('qwen_structured_output', '1', ctx, {})\n"
        "        return result\n",
        encoding="utf-8",
    )
    module = ast.parse(fake.read_text(encoding="utf-8"), filename=str(fake))
    sites, enclosing = _collect(module)
    assert len(sites) == 1
    call = sites[0]
    function = enclosing[call]
    seam_calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and _is_record_seam_call(node)
    ]
    assert seam_calls == [], "负向样本必须无记录接缝调用。"
