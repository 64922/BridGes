"""静态架构测试：图片/视频全部真实供应商动作必须经 recorder 接缝（Issue 16）。

规则（仅扫描生产路径 ``src/bridges/image/service.py`` 与
``src/bridges/video/service.py``）：

1. 所有 ``gateway.invoke`` 调用必须位于接缝方法白名单内（这些方法负责
   把每次真实调用产生的不可变运行锁持久化，调用序号/业务关联/脱敏由
   接缝统一处理）；
2. 任何 ``{"kind": "cancel", ...}`` 载荷必须位于取消接缝方法内（用户
   请求与 worker 收敛/重试都不得绕过接缝直接调用网关）；
3. 任何 ``gateway.invoke`` 不得被 ``contextlib.suppress`` 包裹——吞异常
   的同时会丢弃网关锁（Issue 16 根因之一）；
4. 接缝方法不允许以 ``suppress`` 包裹 invoke 而丢弃锁；主生成链接缝
   （``_invoke_image`` / ``_invoke_video``）保持不变。

模型矩阵单一事实源（Issue 09）由 ``test_model_literal_scan.py`` 独立
覆盖：本测试只保证"调用点必须经过接缝"。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 允许出现 gateway.invoke 的接缝方法（文件基名 → 方法名集合）。
SEAM_METHODS: dict[str, frozenset[str]] = {
    "image_service": frozenset({"_invoke_image", "_invoke_image_cancel", "_invoke_alt_text"}),
    "video_service": frozenset({"_invoke_video", "_invoke_video_cancel"}),
}
#: 允许出现 {"kind": "cancel"} 载荷的接缝方法。
CANCEL_SEAM_METHODS: frozenset[str] = frozenset(
    {"_invoke_image_cancel", "_invoke_video_cancel"}
)

SCANNED_FILES = [
    "src/bridges/image/service.py",
    "src/bridges/video/service.py",
]


@dataclass(frozen=True)
class SeamViolation:
    """一次接缝违规：文件、行号、类别与说明。"""

    path: str
    line: int
    kind: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "line": str(self.line),
            "kind": self.kind,
            "detail": self.detail,
        }


def _is_gateway_invoke(node: ast.AST) -> bool:
    """``self._gateway.invoke(...)`` / ``self.gateway.invoke(...)`` 调用。"""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "invoke"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr in ("gateway", "_gateway")
    )


def _is_suppress_with(node: ast.AST) -> bool:
    """``with contextlib.suppress(...)`` / ``with suppress(...)`` 上下文。"""
    if not isinstance(node, ast.With):
        return False
    for item in node.items:
        expr = item.context_expr
        if not isinstance(expr, ast.Call):
            continue
        func = expr.func
        if isinstance(func, ast.Name) and func.id == "suppress":
            return True
        if isinstance(func, ast.Attribute) and func.attr == "suppress":
            return True
    return False


def _dict_has_kind_cancel(node: ast.AST) -> bool:
    if not isinstance(node, ast.Dict):
        return False
    for key, value in zip(node.keys, node.values, strict=False):
        if (
            isinstance(key, ast.Constant)
            and key.value == "kind"
            and isinstance(value, ast.Constant)
            and value.value == "cancel"
        ):
            return True
    return False


def scan_media_edge_seams(root: Path = REPO_ROOT) -> list[SeamViolation]:
    """扫描图片/视频服务的网关调用点，报告绕过 recorder 接缝的违规。"""
    violations: list[SeamViolation] = []
    for relative in SCANNED_FILES:
        path = root / relative
        if not path.exists():
            continue
        basename = Path(relative).stem
        module_key = f"{Path(relative).parent.name}_{basename}"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        _attach_parents(tree)
        for node in ast.walk(tree):
            if _is_gateway_invoke(node):
                enclosing = _enclosing_function(node)
                if enclosing is None or enclosing not in SEAM_METHODS[module_key]:
                    violations.append(
                        SeamViolation(
                            relative,
                            node.lineno,
                            "gateway_invoke",
                            f"gateway.invoke 位于 {enclosing or '模块级'}，未经过 recorder 接缝。",
                        )
                    )
                if _under_suppress(node):
                    violations.append(
                        SeamViolation(
                            relative,
                            node.lineno,
                            "suppress_invoke",
                            "gateway.invoke 被 contextlib.suppress 包裹，运行锁会被丢弃。",
                        )
                    )
            if _dict_has_kind_cancel(node):
                enclosing = _enclosing_function(node)
                if enclosing is None or enclosing not in CANCEL_SEAM_METHODS:
                    violations.append(
                        SeamViolation(
                            relative,
                            node.lineno,
                            "cancel_payload",
                            f"kind=cancel 载荷位于 {enclosing or '模块级'}，未经过取消接缝。",
                        )
                    )
    return violations


def _enclosing_function(node: ast.AST) -> str | None:
    """返回包含 node 的最内层函数名；不在函数内返回 None。"""
    current: ast.AST | None = node
    while current is not None:
        parent = _parent_of(current)
        if isinstance(parent, ast.FunctionDef):
            return parent.name
        current = parent
    return None


def _parent_of(node: ast.AST) -> ast.AST | None:
    """借助 walk 顺序反查父节点（AST 节点在遍历中先于子节点出现）。"""
    return getattr(node, "_dsh_parent", None)


def _under_suppress(node: ast.AST) -> bool:
    current: ast.AST | None = node
    while current is not None:
        parent = _parent_of(current)
        if _is_suppress_with(parent):
            return True
        current = parent
    return False


def _attach_parents(tree: ast.AST) -> None:
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child._dsh_parent = parent  # type: ignore[attr-defined]


def test_all_media_edge_gateway_invocations_go_through_recorder_seams() -> None:
    violations = scan_media_edge_seams(REPO_ROOT)
    assert violations == [], (
        "图片/视频真实供应商动作绕过 recorder 接缝（Issue 16）：\n"
        + "\n".join(
            f"{v.path}:{v.line} [{v.kind}] {v.detail}" for v in violations
        )
    )
    # 非空洞保证：每个接缝方法恰好一处网关调用，接缝外不允许存在调用。
    for relative in SCANNED_FILES:
        basename = Path(relative).stem
        module_key = f"{Path(relative).parent.name}_{basename}"
        tree = ast.parse(
            (REPO_ROOT / relative).read_text(encoding="utf-8"),
            filename=str(relative),
        )
        _attach_parents(tree)
        enclosing = {
            _enclosing_function(node)
            for node in ast.walk(tree)
            if _is_gateway_invoke(node)
        }
        assert enclosing == SEAM_METHODS[module_key], (
            f"{relative} 的网关调用点集合 {sorted(enclosing)} "
            f"与接缝方法白名单 {sorted(SEAM_METHODS[module_key])} 不一致。"
        )


def test_scan_flags_invoke_outside_seam(tmp_path: Path) -> None:
    """负向控制：扫描器必须能发现绕过接缝的 gateway.invoke。"""
    fake = tmp_path / "src" / "bridges" / "image" / "service.py"
    fake.parent.mkdir(parents=True)
    fake.write_text(
        "class S:\n"
        "    def cancel(self):\n"
        "        self.gateway.invoke('qwen_image', '1', ctx, {'kind': 'cancel'})\n",
        encoding="utf-8",
    )
    violations = scan_media_edge_seams(tmp_path)
    assert any(v.kind == "gateway_invoke" for v in violations)
    assert any(v.kind == "cancel_payload" for v in violations)


def test_scan_flags_suppress_wrapped_invoke(tmp_path: Path) -> None:
    """负向控制：suppress 包裹的 invoke（丢弃锁的既有根因模式）必须被发现。"""
    fake = tmp_path / "src" / "bridges" / "video" / "service.py"
    fake.parent.mkdir(parents=True)
    fake.write_text(
        "import contextlib\n"
        "class S:\n"
        "    def _invoke_video_cancel(self):\n"
        "        with contextlib.suppress(Exception):\n"
        "            self._gateway.invoke('qwen_wan', '1', ctx, {'kind': 'cancel'})\n",
        encoding="utf-8",
    )
    violations = scan_media_edge_seams(tmp_path)
    assert any(v.kind == "suppress_invoke" for v in violations)
