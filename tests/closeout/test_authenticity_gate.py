"""Issue 17：真实性发布门测试（组合门、spy 探针、live suite、重启复查）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from bridges.ai.adapters import AdapterError, AdapterResult, CapabilityAdapter, StubQwenAdapter
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.ai.fixed_models import MODEL_BY_CAPABILITY
from bridges.ai.model_gateway import ModelGateway
from bridges.ai.production import ProductionComposition, register_builtin_capabilities
from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder
from bridges.closeout.authenticity_gate import (
    LOCAL_JOURNEY_MODEL_CALL,
    MISSING_ADAPTER,
    PROBE_ACCOUNT_ID,
    PRODUCTION_STUB,
    check_production_composition,
    check_retired_contract,
    run_authenticity_gate,
    run_live_suite,
    run_local_journey_spy_probes,
    verify_locks_after_restart,
)
from bridges.closeout.manifest import RETIRED_ROUTE_ACTIVE
from bridges.contracts.ai import (
    CapabilityRecord,
    ModelCallStatus,
)
from bridges.contracts.workflows import RunContextEnvelope
from bridges.storage.database import BridgesDatabase

# ---------------------------------------------------------------------------
# 组合检查（Test plan 1/2：生产 Stub、缺 adapter、退役能力复活、漂移）
# ---------------------------------------------------------------------------


def _composition_with_adapter(
    adapter: CapabilityAdapter | None = None,
    *,
    global_key: bool = True,
) -> ProductionComposition:
    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    gateway = ModelGateway(registry)
    if adapter is not None:
        for name in MODEL_BY_CAPABILITY:
            gateway.register_adapter(name, "1", adapter)
    return ProductionComposition(
        registry=registry,
        gateway=gateway,
        cassette_enabled=False,
        global_key_configured=global_key,
    )


def test_stub_adapter_fails_composition_gate() -> None:
    composition = _composition_with_adapter(StubQwenAdapter())
    violations = check_production_composition(composition)
    assert any(v.code == PRODUCTION_STUB for v in violations)
    assert all(
        v.code in {PRODUCTION_STUB, "model_fallback_configured"} for v in violations
    )


def test_missing_adapter_fails_composition_gate() -> None:
    composition = _composition_with_adapter(None)
    violations = check_production_composition(composition)
    assert any(v.code == MISSING_ADAPTER for v in violations)
    # 每个清单 qwen_model 能力都被点名
    for capability in MODEL_BY_CAPABILITY:
        assert any(
            v.code == MISSING_ADAPTER and v.target == capability for v in violations
        )


def test_missing_global_key_fails_composition_gate() -> None:
    composition = _composition_with_adapter(None, global_key=False)
    violations = check_production_composition(composition)
    assert any(v.code == "missing_global_qwen_key" for v in violations)


def test_retired_capability_revival_fails_gate() -> None:
    from bridges.closeout.manifest import RETIRED_EXPRESSION_CAPABILITY, check_retired_registry
    from bridges.contracts.ai import CapabilityKind, CapabilityStatus

    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    registry.register(
        CapabilityRecord(
            name=RETIRED_EXPRESSION_CAPABILITY,
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="deterministic",
            input_schema_version="v1",
            output_schema_version="v1",
            status=CapabilityStatus.VERIFIED,
        )
    )
    violations = check_retired_registry(registry)
    assert any(v.code == RETIRED_ROUTE_ACTIVE for v in violations)


# ---------------------------------------------------------------------------
# 退役契约探针（Test plan 5：410 + 稳定错误码）
# ---------------------------------------------------------------------------


@pytest.fixture
def gate_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{(tmp_path / 'app.db').as_posix()}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "release-gate-test-secret-key-0001")
    from bridges.config import get_settings

    get_settings.cache_clear()
    from bridges.api.main import create_app

    app = create_app(None)
    yield app
    get_settings.cache_clear()


def test_retired_contract_probe_passes(gate_app) -> None:
    violations = check_retired_contract(gate_app)
    assert violations == []


# ---------------------------------------------------------------------------
# 本地旅程 spy 探针（Test plan 4：local_deterministic 0 调用 0 锁）
# ---------------------------------------------------------------------------


def test_local_journey_spy_probes_are_zero_call_zero_lock(
    gate_app, tmp_path: Path
) -> None:
    database = BridgesDatabase(tmp_path / "probe.db")
    database.initialize()
    violations = run_local_journey_spy_probes(gate_app, database)
    assert violations == []


def test_local_journey_spy_detects_model_call(gate_app, tmp_path: Path) -> None:
    database = BridgesDatabase(tmp_path / "probe.db")
    database.initialize()
    violations = run_local_journey_spy_probes(gate_app, database)
    assert violations == []
    # 手动触发一次网关调用：spy 必须发现
    gateway = gate_app.state.model_gateway
    gateway.invoke(
        "qwen_text_chat",
        "1",
        RunContextEnvelope(
            run_id="spy-check",
            account_id=PROBE_ACCOUNT_ID,
            project_id="p",
            workflow_name="w",
            workflow_version="1",
            submitted_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        ),
        {"prompt": "hi"},
    )
    violations = run_local_journey_spy_probes(gate_app, database)
    assert any(v.code == LOCAL_JOURNEY_MODEL_CALL for v in violations)


# ---------------------------------------------------------------------------
# live suite（Test plan 3/6：可控 adapter 锁数量/顺序/幂等 + 无 Key 失败关闭）
# ---------------------------------------------------------------------------


class ProgrammableAdapter(CapabilityAdapter):
    """可控 adapter：按 payload.kind 返回提交/取消/向量等真实形态输出。"""

    def __init__(self, *, fail_with: str | None = None) -> None:
        self.fail_with = fail_with
        self.calls: list[tuple[str, dict]] = []

    def call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict,
    ) -> AdapterResult:
        self.calls.append((capability.name, payload))
        if self.fail_with is not None:
            raise AdapterError(code=self.fail_with, message="controlled failure", retryable=False)
        kind = payload.get("kind", "")
        if kind == "submit":
            return AdapterResult(
                actual_model_id=capability.model_id,
                output={"cloud_task_id": "probe-cloud-task-1"},
            )
        if kind == "cancel":
            return AdapterResult(
                actual_model_id=capability.model_id,
                output={"cancelled": True},
            )
        if capability.name == "qwen_embedding":
            return AdapterResult(
                actual_model_id=capability.model_id,
                output={"vectors": [[0.1] * 1024]},
            )
        if capability.name == "qwen_profile_extraction":
            return AdapterResult(
                actual_model_id=capability.model_id,
                output={
                    "items": [
                        {
                            "dimension": "knowledge_interest",
                            "normalized_value": "系统学习数据分析",
                            "evidence_ref": "probe-msg",
                            "reliability": 0.7,
                            "action": "observe",
                        }
                    ]
                },
            )
        if capability.name in {"qwen_ocr", "qwen_vision"}:
            return AdapterResult(
                actual_model_id=capability.model_id,
                output={"content": "BRIDGES-QWEN-2026 探针文本"},
            )
        return AdapterResult(
            actual_model_id=capability.model_id,
            output={"content": "探针输出"},
        )


def _live_composition(adapter: CapabilityAdapter) -> tuple[ProductionComposition, BridgesDatabase]:
    composition = _composition_with_adapter(adapter)
    database = BridgesDatabase(":memory:")
    database.initialize()
    return composition, database


def test_live_suite_records_one_lock_per_real_call(tmp_path: Path) -> None:
    """每次真实供应商动作恰好一条锁；提交+取消=两条不同锁（不折叠）。"""
    adapter = ProgrammableAdapter()
    composition = _composition_with_adapter(adapter)
    database = BridgesDatabase(tmp_path / "live.db")
    database.initialize()
    recorder = SqliteModelRunLockRecorder(database)
    settings = _test_settings()
    probes = run_live_suite(composition, recorder, settings, token="t1")
    assert probes, "live suite 必须产生探针"
    assert all(probe.status == "passed" for probe in probes), [
        (p.capability, p.status, p.error_code) for p in probes
    ]
    # 提交+取消必须保留两条不同锁
    image = next(p for p in probes if p.capability == "qwen_image")
    assert image.expected_locks == 2
    assert len(image.lock_ids) == 2
    assert image.lock_ids[0] != image.lock_ids[1]
    # 服务级探针：Humanizer 首稿锁与 Career generation 锁分别存在且成功
    career = next(p for p in probes if p.capability == "qwen_structured_output")
    assert career.expected_locks >= 1
    assert career.status == "passed"
    # 报告含清单类别与提供方字段
    chat = next(p for p in probes if p.capability == "qwen_text_chat")
    assert chat.category == "qwen_model"
    assert chat.provider == "qwen"
    # 全部锁可按账户/run 前缀查询且业务关联完整
    locks = recorder.list_locks_by_run(PROBE_ACCOUNT_ID, "release-gate-authn:t1:qwen_image")
    assert len(locks) == 2
    assert all(lock.business_refs for lock in locks)
    # 重启复查通过
    database.close()
    assert verify_locks_after_restart(tmp_path / "live.db", probes) == []


def test_live_suite_without_key_is_inconclusive(tmp_path: Path) -> None:
    """缺全局 Key：全部探针 inconclusive，门禁失败关闭。"""
    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    gateway = ModelGateway(registry)
    composition = ProductionComposition(
        registry=registry,
        gateway=gateway,
        cassette_enabled=False,
        global_key_configured=False,
    )
    database = BridgesDatabase(tmp_path / "live.db")
    database.initialize()
    recorder = SqliteModelRunLockRecorder(database)
    probes = run_live_suite(composition, recorder, _test_settings(), token="t2")
    assert probes
    assert all(probe.status == "inconclusive" for probe in probes)
    assert all(probe.error_code == "missing_global_qwen_key" for probe in probes)


def test_live_suite_failure_keeps_failed_lock(tmp_path: Path) -> None:
    """供应商失败：锁仍持久化且状态准确（Test plan 3 失败路径）。"""
    adapter = ProgrammableAdapter(fail_with="auth_error")
    composition, database = _composition_with_adapter(adapter), BridgesDatabase(
        tmp_path / "live.db"
    )
    database.initialize()
    recorder = SqliteModelRunLockRecorder(database)
    probes = run_live_suite(composition, recorder, _test_settings(), token="t3")
    assert all(probe.status == "failed" for probe in probes)
    assert any(probe.error_code == "auth_error" for probe in probes)
    failed_locks = recorder.list_locks_by_run(
        PROBE_ACCOUNT_ID, "release-gate-authn:t3:qwen_text_chat"
    )
    assert len(failed_locks) == 1
    assert failed_locks[0].status == ModelCallStatus.BLOCKED


def _test_settings():
    from pydantic import SecretStr

    from bridges.config import Settings

    return Settings(
        environment="test",
        qwen_api_key=SecretStr("gate-test-dummy-key-not-real"),
        database_url=None,
        secret_key=None,
        _env_file=None,
    )


# ---------------------------------------------------------------------------
# 门禁整体（Test plan 6/7：缺 Key 失败关闭、报告脱敏、非零退出语义）
# ---------------------------------------------------------------------------


def test_gate_blocked_without_key_reports_redacted(
    gate_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BRIDGES_QWEN_API_KEY", raising=False)
    monkeypatch.delenv("BRIDGES_QWEN_API_KEY_FILE", raising=False)
    report = run_authenticity_gate(real_probes=False, app=gate_app)
    assert report.status == "blocked"
    assert not report.release_ready
    assert any(v.code == "missing_global_qwen_key" for v in report.violations)
    assert any(v.code == MISSING_ADAPTER for v in report.violations)
    # 报告只含脱敏字段：不得出现 Key 值形态/Authorization/请求正文。
    payload = report.as_dict()
    text = str(payload)
    for forbidden in ("authorization", "sk-", "bearer "):
        assert forbidden not in text.casefold()
    # 探针正文（提示词/用户消息/OCR 文本）不得进入报告
    for forbidden in ("Transformer", "红色圆形"):
        assert forbidden not in text


def test_gate_deterministic_checks_pass_but_gate_blocks_without_key(
    gate_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """除组合门（缺 Key）外，清单/扫描/退役契约/本地旅程全部通过。"""
    monkeypatch.delenv("BRIDGES_QWEN_API_KEY", raising=False)
    monkeypatch.delenv("BRIDGES_QWEN_API_KEY_FILE", raising=False)
    report = run_authenticity_gate(real_probes=False, app=gate_app)
    names = {check["name"]: check["status"] for check in report.deterministic_checks}
    assert names["manifest-completeness"] == "passed"
    assert names["static-scans"] == "passed"
    assert names["retired-contract"] == "passed"
    assert names["local-journey-spy"] == "passed"
    assert names["production-composition"] == "failed"
