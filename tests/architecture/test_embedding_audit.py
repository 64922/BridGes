"""静态架构测试：Embedding 生产调用必须走统一审计接缝（Issue 15 AC）。

规则：
- ``QwenApiClient.embeddings`` 的直接调用只允许出现在真实 adapter
  （``src/bridges/ai/embedding_adapter.py``）；知识库业务代码绕过接缝直连
  客户端即违规（``embedding_direct_client_bypass``）；
- ``QwenEmbeddingPort`` 的每个生产构造点必须同时注入 ``gateway`` 与
  ``recorder``（API/worker 共享同一固定模型事实源与统一 recorder）；
- 测试替身 ``DeterministicEmbeddingPort`` 只允许出现在测试评估
  harness（``src/bridges/evaluation/executors.py``），不得进入生产接线。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 允许直连 embeddings 的唯一生产模块（真实 adapter）。
EMBEDDING_ADAPTER_MODULE = "src/bridges/ai/embedding_adapter.py"
#: 允许构造 QwenEmbeddingPort 的生产组合根（API 与 worker）。
PORT_COMPOSITION_ROOTS = {
    "src/bridges/api/main.py",
    "src/bridges/runtime/executor.py",
}
#: 允许测试替身构造的生产模块（评估 harness 的 SUT 装配）。
DETERMINISTIC_ALLOWED = {
    "src/bridges/evaluation/executors.py",
}


@dataclass(frozen=True)
class EmbeddingViolation:
    """一次 Embedding 审计违规：稳定错误码 + 文件 + 行号。"""

    code: str
    path: str
    line: int

    def __str__(self) -> str:
        return f"{self.path}:{self.line} [{self.code}]"


def _module_relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _production_modules(root: Path) -> list[Path]:
    source_root = root / "src" / "bridges"
    return sorted(source_root.rglob("*.py"))


def scan_direct_embeddings_calls(root: Path = REPO_ROOT) -> list[EmbeddingViolation]:
    """扫描 ``.embeddings(...)`` 调用：只允许真实 adapter 出现。"""
    violations: list[EmbeddingViolation] = []
    for path in _production_modules(root):
        relative = _module_relative(path, root)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr != "embeddings":
                continue
            if relative != EMBEDDING_ADAPTER_MODULE:
                violations.append(
                    EmbeddingViolation(
                        "embedding_direct_client_bypass", relative, node.lineno
                    )
                )
    return violations


def scan_embedding_port_constructs(root: Path = REPO_ROOT) -> list[EmbeddingViolation]:
    """扫描 ``QwenEmbeddingPort(...)`` 构造：必须在组合根且注入网关与 recorder。"""
    violations: list[EmbeddingViolation] = []
    for path in _production_modules(root):
        relative = _module_relative(path, root)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
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
            if name != "QwenEmbeddingPort":
                continue
            if relative not in PORT_COMPOSITION_ROOTS:
                violations.append(
                    EmbeddingViolation("embedding_unauthorized_construct", relative, node.lineno)
                )
                continue
            keywords = {keyword.arg for keyword in node.keywords if keyword.arg}
            if "gateway" not in keywords or "recorder" not in keywords:
                violations.append(
                    EmbeddingViolation("embedding_missing_run_lock", relative, node.lineno)
                )
    return violations


def scan_deterministic_port_constructs(root: Path = REPO_ROOT) -> list[EmbeddingViolation]:
    """扫描 ``DeterministicEmbeddingPort(...)`` 构造：测试替身禁入生产接线。"""
    violations: list[EmbeddingViolation] = []
    for path in _production_modules(root):
        relative = _module_relative(path, root)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
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
            if name != "DeterministicEmbeddingPort":
                continue
            if relative not in DETERMINISTIC_ALLOWED:
                violations.append(
                    EmbeddingViolation("production_test_adapter", relative, node.lineno)
                )
    return violations


def test_no_direct_embeddings_calls_outside_real_adapter() -> None:
    violations = scan_direct_embeddings_calls()
    assert violations == [], (
        "生产路径出现绕过审计接缝的 QwenApiClient.embeddings 直连调用：\n"
        + "\n".join(str(violation) for violation in violations)
    )


def test_every_production_port_construct_injects_gateway_and_recorder() -> None:
    violations = scan_embedding_port_constructs()
    assert violations == [], (
        "QwenEmbeddingPort 生产构造必须位于组合根并注入 gateway 与 recorder：\n"
        + "\n".join(str(violation) for violation in violations)
    )


def test_deterministic_port_never_enters_production_wiring() -> None:
    violations = scan_deterministic_port_constructs()
    assert violations == [], (
        "DeterministicEmbeddingPort 不得出现在生产接线（仅评估 harness 允许）：\n"
        + "\n".join(str(violation) for violation in violations)
    )


# ---------------------------------------------------------------------------
# 负向控制：扫描器本身必须能发现违规
# ---------------------------------------------------------------------------


def _write_fake_module(tmp_path: Path, relative: str, content: str) -> None:
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_scan_flags_direct_client_call_in_business_code(tmp_path: Path) -> None:
    _write_fake_module(
        tmp_path,
        "src/bridges/ingestion/embedding.py",
        "def run(client):\n    return client.embeddings({'model': 'm'})\n",
    )
    hits = scan_direct_embeddings_calls(tmp_path)
    assert any(hit.code == "embedding_direct_client_bypass" for hit in hits)


def test_scan_flags_port_without_recorder(tmp_path: Path) -> None:
    _write_fake_module(
        tmp_path,
        "src/bridges/api/main.py",
        "port = QwenEmbeddingPort(api_key=key, gateway=gw)\n",
    )
    hits = scan_embedding_port_constructs(tmp_path)
    assert any(hit.code == "embedding_missing_run_lock" for hit in hits)


def test_scan_flags_deterministic_port_in_business_module(tmp_path: Path) -> None:
    _write_fake_module(
        tmp_path,
        "src/bridges/ingestion/service.py",
        "embedding = DeterministicEmbeddingPort()\n",
    )
    hits = scan_deterministic_port_constructs(tmp_path)
    assert any(hit.code == "production_test_adapter" for hit in hits)
