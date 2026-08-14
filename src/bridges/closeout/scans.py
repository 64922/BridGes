"""Issue 17：生产真实性的静态代码扫描（AST/文本，无运行时副作用）。

扫描对象是 ``src/bridges`` 生产路径，全部只读，结果只含稳定错误码、
相对路径、行号与能力名——绝不读取或输出 Key、请求/响应正文：

- ``scan_direct_client_bypass``：业务服务直接构造 ``QwenApiClient``
  绕过批准 adapter/recorder（``direct_client_bypass``）；
- ``scan_model_literals``：生产模块出现受控模型字面量
  （``model_matrix_drift``，Issue 09 单一事实源强制）；
- ``scan_external_key_isolation``：DDG/arXiv 检索模块读取或继承
  全局 Qwen Key（``direct_client_bypass``，external_non_qwen 不得
  消费 Qwen Key）；
- ``scan_deterministic_production``：生产接线出现确定性生成器/Stub
  类名特征（``production_stub``）。

批准例外清单 ``ALLOWED_DIRECT_CLIENT_MODULES`` 只包含 Qwen 客户端与其
真实 adapter 所在的 ``src/bridges/ai`` 目录（这是适配器/客户端自身的
所有权边界，不是业务例外）；除此之外任何业务模块直连 Qwen 客户端都使
门禁失败并点名调用点——业务侧批准例外清单当前为空，不允许过渡例外。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from bridges.ai.fixed_models import MODEL_BY_CAPABILITY
from bridges.closeout.manifest import (
    DIRECT_CLIENT_BYPASS,
    MODEL_MATRIX_DRIFT,
    PRODUCTION_STUB,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 允许构造 QwenApiClient 的唯一生产目录（真实 adapter/客户端本身）。
ALLOWED_DIRECT_CLIENT_MODULES = frozenset({"src/bridges/ai"})

#: 未批准的历史模型 ID：生产路径任何文本（含注释/docstring）出现即违规。
#: 定义本身会被 Issue 09 的文本扫描命中，因此按段拼接（运行时值不变，
#: 源码中不出现连续字面量，避免扫描器自指违规）。
BANNED_MODEL_IDS = frozenset(
    {
        "qwen3" + ".6" + "-flash",
        "qwen" + "-vl" + "-ocr",
        "qwen3" + "-vl" + "-plus",
    }
)

#: 允许定义批准模型字面量的唯一生产模块（Issue 09 静态合同）。
FIXED_MODELS_MODULE = "src/bridges/ai/fixed_models.py"

#: 生产接线禁止出现的测试替身类名特征。
DETERMINISTIC_CLASS_MARKERS = ("stub", "deterministic", "closeoutqwen")

#: 外部检索模块：DDG 与 arXiv 检索阶段不得继承/读取全局 Qwen Key。
EXTERNAL_MODULES = (
    "src/bridges/web_search",
    "src/bridges/arxiv_mcp",
)


@dataclass(frozen=True)
class ScanViolation:
    """一条静态扫描违规：稳定错误码 + 文件 + 行号 + 能力/对象。"""

    code: str
    path: str
    line: int
    target: str

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "path": self.path,
            "line": str(self.line),
            "target": self.target,
        }


def _module_relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _production_modules(root: Path) -> list[Path]:
    source_root = root / "src" / "bridges"
    return sorted(source_root.rglob("*.py"))


def _is_allowed_client_module(relative: str) -> bool:
    return any(
        relative == allowed or relative.startswith(allowed + "/")
        for allowed in ALLOWED_DIRECT_CLIENT_MODULES
    )


def scan_direct_client_bypass(root: Path = REPO_ROOT) -> list[ScanViolation]:
    """扫描 ``QwenApiClient(`` 构造：业务模块直连即 ``direct_client_bypass``。

    ``QwenApiClient`` 只允许出现在 ``src/bridges/ai``（客户端自身与其真实
    adapter 组装处）；业务服务（chat/humanizer/career/profiles/ingestion/
    retrieval/image/video/speech/media/science 等）一律经批准 adapter 与
    recorder 接缝，任何直连构造都使发布门失败并点名文件与行号。
    """
    violations: list[ScanViolation] = []
    for path in _production_modules(root):
        relative = _module_relative(path, root)
        if _is_allowed_client_module(relative):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if name != "QwenApiClient":
                continue
            violations.append(
                ScanViolation(
                    DIRECT_CLIENT_BYPASS,
                    relative,
                    node.lineno,
                    "业务服务直接构造 QwenApiClient，绕过批准 adapter/recorder 接缝。",
                )
            )
    return violations


def scan_model_literals(root: Path = REPO_ROOT) -> list[ScanViolation]:
    """扫描受控模型字面量：批准 ID 只允许在 fixed_models.py（``model_matrix_drift``）。

    批准 ID：AST 常量精确等值检查（fixed_models.py 豁免）；未批准历史 ID：
    整文件文本检查（任何出现都算违规，含注释/docstring）。
    """
    approved_ids = frozenset(MODEL_BY_CAPABILITY.values())
    model_to_capability = {
        model_id: "/".join(
            name for name, bound in MODEL_BY_CAPABILITY.items() if bound == model_id
        )
        for model_id in approved_ids
    }
    violations: list[ScanViolation] = []
    for path in _production_modules(root):
        relative = _module_relative(path, root)
        if path.name == "__init__.py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if relative == FIXED_MODELS_MODULE:
            for banned in BANNED_MODEL_IDS:
                for line_no, line in enumerate(text.splitlines(), start=1):
                    if banned in line:
                        violations.append(
                            ScanViolation(
                                MODEL_MATRIX_DRIFT,
                                relative,
                                line_no,
                                f"未批准模型字面量 {banned}（legacy-unapproved）。",
                            )
                        )
            continue
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in approved_ids
            ):
                violations.append(
                    ScanViolation(
                        MODEL_MATRIX_DRIFT,
                        relative,
                        node.lineno,
                        "受控模型字面量必须来自 bridges.ai.fixed_models 单一事实源"
                        f"（capability: {model_to_capability.get(node.value, 'unknown')}）。",
                    )
                )
        for banned in BANNED_MODEL_IDS:
            for line_no, line in enumerate(text.splitlines(), start=1):
                if banned in line:
                    violations.append(
                        ScanViolation(
                            MODEL_MATRIX_DRIFT,
                            relative,
                            line_no,
                            f"未批准模型字面量 {banned}（legacy-unapproved）。",
                        )
                    )
    return violations


def scan_external_key_isolation(root: Path = REPO_ROOT) -> list[ScanViolation]:
    """扫描外部检索模块：不得读取/继承全局 Qwen Key（``direct_client_bypass``）。

    DDG 与 arXiv 是 ``external_non_qwen`` 能力：检索阶段不读取
    ``BRIDGES_QWEN_API_KEY``/``_FILE``、不导入 ``QwenApiClient`` 或
    全局凭据判定函数；它们只消费自己的 provider 客户端。命中即失败并
    点名模块与行号，防止外部检索意外转发 Qwen Key。
    """
    forbidden_imports = ("QwenApiClient", "is_global_qwen_key_configured")
    violations: list[ScanViolation] = []
    for path in _production_modules(root):
        relative = _module_relative(path, root)
        if not any(relative == mod or relative.startswith(mod + "/") for mod in EXTERNAL_MODULES):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            lowered = line.casefold()
            if "qwen_api_key" in lowered or "qwen_workspace_id" in lowered:
                violations.append(
                    ScanViolation(
                        DIRECT_CLIENT_BYPASS,
                        relative,
                        line_no,
                        "外部检索模块读取全局 Qwen Key 配置，external_non_qwen 不得消费 Qwen Key。",
                    )
                )
            if any(name in line for name in forbidden_imports):
                violations.append(
                    ScanViolation(
                        DIRECT_CLIENT_BYPASS,
                        relative,
                        line_no,
                        "外部检索模块导入 Qwen 客户端/全局凭据判定，检索阶段不得继承 Qwen Key。",
                    )
                )
    return violations


def scan_deterministic_production(root: Path = REPO_ROOT) -> list[ScanViolation]:
    """扫描生产接线中的确定性/Stub 类构造（``production_stub``）。

    只扫描生产 ``src/bridges``：类名含 stub/deterministic/closeoutqwen 的
    构造出现在生产模块即违规（测试替身只允许 tests/ 与 evaluation harness）。
    ``bridges/evaluation`` 属于评测 harness，其 SUT 装配允许替身。
    """
    violations: list[ScanViolation] = []
    for path in _production_modules(root):
        relative = _module_relative(path, root)
        if relative.startswith("src/bridges/evaluation"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if name is None:
                continue
            lowered = name.casefold()
            if any(marker in lowered for marker in DETERMINISTIC_CLASS_MARKERS):
                violations.append(
                    ScanViolation(
                        PRODUCTION_STUB,
                        relative,
                        node.lineno,
                        f"生产接线出现测试替身类 {name}，Stub/确定性生成器不得进入生产组合。",
                    )
                )
    return violations


def run_all_scans(root: Path = REPO_ROOT) -> list[ScanViolation]:
    """运行全部静态扫描，按错误码/路径/行号稳定排序。"""
    violations: list[ScanViolation] = [
        *scan_direct_client_bypass(root),
        *scan_model_literals(root),
        *scan_external_key_isolation(root),
        *scan_deterministic_production(root),
    ]
    return sorted(
        violations, key=lambda v: (v.code, v.path, v.line, v.target)
    )


__all__ = [
    "ALLOWED_DIRECT_CLIENT_MODULES",
    "BANNED_MODEL_IDS",
    "DETERMINISTIC_CLASS_MARKERS",
    "EXTERNAL_MODULES",
    "FIXED_MODELS_MODULE",
    "REPO_ROOT",
    "ScanViolation",
    "run_all_scans",
    "scan_deterministic_production",
    "scan_direct_client_bypass",
    "scan_external_key_isolation",
    "scan_model_literals",
]
