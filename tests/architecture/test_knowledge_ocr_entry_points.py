"""静态架构测试：知识库 OCR 入口必须统一走注册能力与运行记录接缝（Issue 14）。

规则（``src/bridges/ingestion`` = 知识库摄取业务代码）：

- 禁止导入/使用 ``QwenApiClient``、``QwenOcrAdapter``/``QwenVisionAdapter``
  ——知识库 OCR 不得直接构造 client 或调用 adapter（测试目录允许 fake）；
- 禁止直接构造生产 ``CapabilityRecord`` 快照——能力/模型/区域/重试政策
  只来自 Issue 09 注册表与 ``bridges.ai.fixed_models`` 单一事实源；
- 禁止以 ``.call(...)`` 属性调用直接驱动 adapter；
- 唯一的网关/录制器接缝是 ``src/bridges/ingestion/ocr.py``（OcrPort），
  且该模块同样不得出现上述直接 client/adapter 用法。

附带扫描其他 OCR/视觉提取入口（science 扫描 PDF 解析、media 媒体提取）
确认它们同样只经 ``ModelGateway`` 调用统一能力，不存在直接
client/adapter 绕行路径（用户旅程调用清单见 issue 14 实现说明）。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 知识库摄取业务代码目录（含 OcrPort 接缝）。
_INGESTION_ROOT = REPO_ROOT / "src" / "bridges" / "ingestion"
#: 其他 OCR/视觉提取入口（用户旅程调用清单）。
_OTHER_OCR_ROOTS = (
    REPO_ROOT / "src" / "bridges" / "science",
    REPO_ROOT / "src" / "bridges" / "media",
)
#: 不属于 OCR 旅程、由后续 issue 接管的模块（白名单 + 理由）。
#: embedding.py 是知识库 Embedding 端口（QwenEmbeddingPort 直接构造
#: QwenApiClient），其统一接缝由 Issue 15（knowledge-embedding）完成，
#: 不在本 issue 的 OCR 扫描范围。
_EXCLUDED_MODULES = frozenset({"embedding.py"})

#: 直接 client/adapter 导入路径（出现即违规；允许测试目录中的 fake）。
_BANNED_IMPORTS = (
    "bridges.ai.qwen_client",
    "bridges.ai.qwen_vision_adapters",
    "bridges.ai.adapters",
)
#: 直接构造的生产能力快照（出现即违规）。
_BANNED_CONSTRUCTIONS = ("CapabilityRecord",)
#: 直接 adapter 调用形态（``xxx.call(...)``）。
_BANNED_ATTR_CALLS = ("call",)


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    message: str

    def render(self) -> str:
        return f"{self.path}:{self.line} {self.message}"


def _module_relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        # 负向控制用 tmp_path 构造的模块不在仓库根下：直接使用绝对路径。
        return str(path)


def _scan_module(path: Path) -> list[Violation]:
    relative = _module_relative(path, REPO_ROOT)
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    hits: list[Violation] = []
    for node in ast.walk(tree):
        # 禁止直接导入 client/adapter。
        if isinstance(node, ast.ImportFrom):
            if node.module in _BANNED_IMPORTS:
                names = ", ".join(alias.name for alias in node.names)
                hits.append(
                    Violation(
                        relative,
                        node.lineno,
                        f"禁止从 {node.module} 导入 {names}"
                        "（应统一经 ModelGateway/OcrPort 接缝）。",
                    )
                )
            elif node.module and any(
                name in node.module for name in ("qwen_client", "qwen_vision_adapters")
            ):
                hits.append(
                    Violation(relative, node.lineno, f"禁止导入 {node.module}。")
                )
        if isinstance(node, ast.Import):
            for alias in node.names:
                if any(
                    alias.name.startswith(name)
                    for name in ("bridges.ai.qwen_client", "bridges.ai.qwen_vision_adapters")
                ):
                    hits.append(
                        Violation(relative, node.lineno, f"禁止导入 {alias.name}。")
                    )
        # 禁止直接构造生产能力快照。
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _BANNED_CONSTRUCTIONS:
                hits.append(
                    Violation(
                        relative,
                        node.lineno,
                        "禁止直接构造生产 CapabilityRecord（能力定义只来自注册表）。",
                    )
                )
            # 禁止以 .call(...) 直接驱动 adapter。
            if isinstance(func, ast.Attribute) and func.attr in _BANNED_ATTR_CALLS:
                hits.append(
                    Violation(
                        relative,
                        node.lineno,
                        "禁止直接调用 adapter.call（应统一经 ModelGateway.invoke）。",
                    )
                )
    return hits


def scan_knowledge_ocr_entry_points(root: Path = REPO_ROOT) -> list[Violation]:
    """扫描知识库 OCR 入口：摄取业务代码 + 其他 OCR/视觉提取入口。"""
    hits: list[Violation] = []
    for base in (root / "src" / "bridges" / "ingestion", *_OTHER_OCR_ROOTS):
        if not base.exists():
            continue
        for path in sorted(base.glob("*.py")):
            if path.name == "__init__.py" or path.name in _EXCLUDED_MODULES:
                continue
            hits.extend(_scan_module(path))
    return hits


def test_knowledge_ocr_entry_points_use_unified_seam_only() -> None:
    hits = scan_knowledge_ocr_entry_points()
    assert hits == [], (
        "知识库 OCR 入口出现直接 client/adapter 绕行（Issue 14 要求统一经 "
        "ModelGateway + ModelRunLockRecorder 接缝）：\n"
        + "\n".join(hit.render() for hit in hits)
    )


def test_scan_flags_direct_adapter_call_in_ingestion(tmp_path: Path) -> None:
    """负向控制：扫描器必须能发现知识库代码中的直接 adapter 调用。"""
    module = tmp_path / "src" / "bridges" / "ingestion" / "bad_ocr.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "from bridges.ai.qwen_vision_adapters import QwenOcrAdapter\n"
        "adapter.call(capability, ctx, payload)\n",
        encoding="utf-8",
    )
    hits = scan_knowledge_ocr_entry_points(tmp_path)
    messages = " ".join(hit.render() for hit in hits)
    assert "QwenOcrAdapter" in messages
    assert "adapter.call" in messages


def test_scan_flags_capability_snapshot_construction(tmp_path: Path) -> None:
    """负向控制：端口内不得再自建生产能力快照。"""
    module = tmp_path / "src" / "bridges" / "ingestion" / "bad_ocr.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "from bridges.contracts.ai import CapabilityRecord\n"
        "CapabilityRecord(name='qwen_ocr', model_id='x')\n",
        encoding="utf-8",
    )
    hits = scan_knowledge_ocr_entry_points(tmp_path)
    assert any("CapabilityRecord" in hit.message for hit in hits)


def test_port_seam_may_use_gateway_and_recorder(tmp_path: Path) -> None:
    """正对照：接缝模块经 ModelGateway/Recorder 调用不算违规。"""
    module = tmp_path / "src" / "bridges" / "ingestion" / "ocr.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "from bridges.ai.model_gateway import ModelGateway\n"
        "from bridges.ai.ports import ModelRunLockRecorder\n"
        "self._gateway.invoke('qwen_ocr', '1', ctx, payload)\n"
        "self._recorder.record(lock, business_ref=ref)\n",
        encoding="utf-8",
    )
    assert scan_knowledge_ocr_entry_points(tmp_path) == []
