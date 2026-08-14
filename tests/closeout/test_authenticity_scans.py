"""Issue 17：静态扫描测试（直连 bypass/模型字面量/外部 Key 隔离/确定性替身）。"""

from __future__ import annotations

from pathlib import Path

from bridges.ai.fixed_models import CHAT_MODEL_ID
from bridges.closeout.manifest import DIRECT_CLIENT_BYPASS, MODEL_MATRIX_DRIFT, PRODUCTION_STUB
from bridges.closeout.scans import (
    run_all_scans,
    scan_deterministic_production,
    scan_direct_client_bypass,
    scan_external_key_isolation,
    scan_model_literals,
)


def _write_fake_module(root: Path, relative: str, content: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_real_production_tree_is_clean() -> None:
    assert run_all_scans() == []


def test_scan_flags_direct_client_in_business_module(tmp_path: Path) -> None:
    _write_fake_module(
        tmp_path,
        "src/bridges/ingestion/ocr.py",
        "def build():\n    client = QwenApiClient(api_key=None)\n    return client\n",
    )
    hits = scan_direct_client_bypass(tmp_path)
    assert any(hit.code == DIRECT_CLIENT_BYPASS for hit in hits)
    hit = next(hit for hit in hits if hit.code == DIRECT_CLIENT_BYPASS)
    assert hit.path == "src/bridges/ingestion/ocr.py"
    assert hit.line == 2


def test_scan_allows_client_in_ai_module(tmp_path: Path) -> None:
    _write_fake_module(
        tmp_path,
        "src/bridges/ai/qwen_adapters.py",
        "def build():\n    client = QwenApiClient(api_key=None)\n    return client\n",
    )
    assert scan_direct_client_bypass(tmp_path) == []


def test_scan_flags_approved_literal_outside_fixed_models(tmp_path: Path) -> None:
    _write_fake_module(
        tmp_path,
        "src/bridges/chat/service.py",
        f'model = "{CHAT_MODEL_ID}"\n',
    )
    hits = scan_model_literals(tmp_path)
    assert any(hit.code == MODEL_MATRIX_DRIFT for hit in hits)


def test_scan_allows_approved_literal_in_fixed_models(tmp_path: Path) -> None:
    _write_fake_module(
        tmp_path,
        "src/bridges/ai/fixed_models.py",
        f'CHAT_MODEL_ID = "{CHAT_MODEL_ID}"\n',
    )
    assert scan_model_literals(tmp_path) == []


def test_scan_flags_banned_historical_id_even_in_comments(tmp_path: Path) -> None:
    _write_fake_module(
        tmp_path,
        "src/bridges/runtime/executor.py",
        "# 历史注释提到 qwen3.6-flash\n",
    )
    hits = scan_model_literals(tmp_path)
    assert any(hit.code == MODEL_MATRIX_DRIFT for hit in hits)


def test_scan_flags_external_module_reading_qwen_key(tmp_path: Path) -> None:
    _write_fake_module(
        tmp_path,
        "src/bridges/web_search/service.py",
        "key = os.environ.get('BRIDGES_QWEN_API_KEY')\n",
    )
    hits = scan_external_key_isolation(tmp_path)
    assert any(hit.code == DIRECT_CLIENT_BYPASS for hit in hits)


def test_scan_flags_external_module_importing_qwen_client(tmp_path: Path) -> None:
    _write_fake_module(
        tmp_path,
        "src/bridges/arxiv_mcp/service.py",
        "from bridges.ai.qwen_client import QwenApiClient\n",
    )
    hits = scan_external_key_isolation(tmp_path)
    assert any(hit.code == DIRECT_CLIENT_BYPASS for hit in hits)


def test_scan_ignores_other_modules_for_external_isolation(tmp_path: Path) -> None:
    _write_fake_module(
        tmp_path,
        "src/bridges/chat/service.py",
        "key = os.environ.get('BRIDGES_QWEN_API_KEY')\n",
    )
    assert scan_external_key_isolation(tmp_path) == []


def test_scan_flags_deterministic_class_in_production(tmp_path: Path) -> None:
    _write_fake_module(
        tmp_path,
        "src/bridges/humanizer/service.py",
        "adapter = DeterministicQwenAdapter()\n",
    )
    hits = scan_deterministic_production(tmp_path)
    assert any(hit.code == PRODUCTION_STUB for hit in hits)


def test_scan_allows_stub_in_evaluation_harness(tmp_path: Path) -> None:
    _write_fake_module(
        tmp_path,
        "src/bridges/evaluation/executors.py",
        "adapter = DeterministicQwenAdapter()\n",
    )
    assert scan_deterministic_production(tmp_path) == []
