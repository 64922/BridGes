"""改进工单 03：锁定完整模型额度与每次调用版本。

覆盖验收标准（``.scratch/2/issues/03-model-quota-and-call-snapshots.md``）：

1. 旧模型排队后激活新模型，续跑与重试仍采用旧快照真实额度；新消息采用新配置；
2. 不同输出额度的调用预留不同空间，元数据未知不会静默回退为已验证窗口；
3. 每次调用可定位实际能力与全部必要版本，历史模型标识不重写；
4. 快照账户隔离、迁移、恢复和导出通过，响应与日志不包含凭据。

验证材料为确定性替身与合成配置：只证明机制正确，不代表真实模型体验。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.ai import CapabilityRegistry
from bridges.ai.adapters import AdapterResult, RateLimitError, StreamChunk
from bridges.ai.errors import ModelRunLockSecurityError
from bridges.ai.fixed_models import (
    CHAT_MODEL_ID,
    EMBEDDING_MODEL_ID,
    FACTORY_MAIN_MODEL_CONTEXT_WINDOW,
    FACTORY_MAIN_MODEL_MAX_INPUT_TOKENS,
)
from bridges.ai.model_gateway import ModelGateway
from bridges.ai.model_quota import (
    MODEL_QUOTA_VERSION,
    QUOTA_REASON_LEGACY_UNVERIFIED,
    QUOTA_REASON_UNREADABLE,
    RUN_MODEL_QUOTA_CONFIG_KEY,
    QuotaVerificationBasis,
    RunModelQuota,
    build_run_model_quota,
    export_run_model_quota,
    redaction_audit,
    resolve_run_quota,
)
from bridges.ai.run_model_config import (
    ModelConfigSource,
    RunModelConfigSnapshot,
)
from bridges.chat.context_compiler import (
    DEFAULT_CONTEXT_WINDOW,
    OUTPUT_RESERVE_MARGIN_TOKENS,
    TOOL_RESERVE_TOKENS,
    compile_turn_context,
    is_verified_context_window,
)
from bridges.chat.repository import ConversationRepository, MessageRecord
from bridges.chat.service import ChatDomainError, ChatService
from bridges.contracts.ai import (
    BusinessRef,
    CallContractVersions,
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    ModelCallStatus,
    ModelCapabilities,
    ModelRunLock,
)
from bridges.contracts.chat import ChatMessageRole, ChatMessageStatus, ChatMode
from bridges.contracts.observability import AuditAction
from bridges.contracts.workflows import RunContextEnvelope
from bridges.storage.database import SCHEMA_VERSION, BridgesDatabase
from tests.chat.test_chat_api import (
    _chat_capability,
    _create_conversation,
    _gateway_with,
    _register,
)
from tests.chat.test_issue02_durable_generation import (  # noqa: F401 - 复用夹具
    client as client,
)
from tests.chat.test_issue02_durable_generation import (
    sqlite_app as sqlite_app,
)

_MODEL_A = "qwen-user-model-a"
_MODEL_B = "qwen-user-model-b"
_WINDOW_A = 131_072
_MAX_INPUT_A = 130_048
_WINDOW_B = 200_000
_MAX_INPUT_B = 199_000


# ---------------------------------------------------------------------------
# 材料构造
# ---------------------------------------------------------------------------


def _snapshot(
    model_id: str,
    *,
    context_window: int | None,
    max_input_tokens: int | None,
    source: ModelConfigSource = ModelConfigSource.SETTINGS,
    revision: int = 3,
) -> RunModelConfigSnapshot:
    return RunModelConfigSnapshot(
        model_id=model_id,
        capabilities=ModelCapabilities(
            text=True, image=True, tool_calling=True, structured_output=True
        ),
        context_window=context_window,
        max_input_tokens=max_input_tokens,
        source=source,
        revision=revision,
    )


def _record(message_id: str, role: ChatMessageRole, content: str) -> MessageRecord:
    now = datetime(2026, 9, 30, tzinfo=UTC)
    return MessageRecord(
        message_id=message_id,
        conversation_id="conv-1",
        account_id="acc-1",
        role=role,
        attempt_number=1,
        status=ChatMessageStatus.DONE,
        content=content,
        thinking=None,
        error_code=None,
        error_message=None,
        duration_ms=None,
        model_id=None,
        run_lock_id=None,
        created_at=now,
        updated_at=now,
    )


class _ProgrammableAdapter:
    """可编程替身：同时支持 ``call`` 与 ``stream_call``，记录每次模型。"""

    def __init__(self, fail: bool = False) -> None:
        self.models: list[str] = []
        self.fail = fail

    def call(self, capability: Any, run_context: Any, payload: dict[str, Any]) -> Any:
        self.models.append(capability.model_id or "")
        return AdapterResult(
            actual_model_id=capability.model_id, output={"content": "好的"}
        )

    def stream_call(self, capability: Any, run_context: Any, payload: dict[str, Any]):
        self.models.append(capability.model_id or "")
        if self.fail:
            raise RateLimitError("slow")
        yield StreamChunk(kind="delta", delta="好的")
        yield StreamChunk(kind="done", actual_model_id=capability.model_id)


def _chat_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    return registry


def _context(run_id: str = "run-1", *, account_id: str = "account-1") -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id=run_id,
        account_id=account_id,
        project_id="conversation-1",
        workflow_name="chat",
        workflow_version="1",
        submitted_at=datetime.now(UTC),
    )


def _activate(app: Any, model_id: str, *, window: int, max_input: int) -> None:
    app.state.run_model_config_provider.activate(
        model_id=model_id,
        capabilities=ModelCapabilities(
            text=True, image=True, tool_calling=True, structured_output=True
        ),
        context_window=window,
        max_input_tokens=max_input,
        validated_at=None,
    )


def _compiled_audit_records(app: Any) -> list[dict[str, Any]]:
    events = app.state.observability_service.list_audit_events(
        action=AuditAction.CONTEXT_COMPILED
    )
    return [event.details for event in events]


def _assistant_messages(
    client: TestClient, conversation_id: str
) -> list[dict[str, Any]]:
    projection = client.get(f"/chat/conversations/{conversation_id}").json()
    return [m for m in projection["messages"] if m["role"] == "assistant"]


# ---------------------------------------------------------------------------
# 验收 2（基础）：额度快照构造与解析
# ---------------------------------------------------------------------------


def test_factory_snapshot_records_the_factory_matrix_basis() -> None:
    quota = build_run_model_quota(
        _snapshot(
            CHAT_MODEL_ID,
            context_window=FACTORY_MAIN_MODEL_CONTEXT_WINDOW,
            max_input_tokens=FACTORY_MAIN_MODEL_MAX_INPUT_TOKENS,
            source=ModelConfigSource.FACTORY,
            revision=0,
        )
    )
    assert quota.verification_basis is QuotaVerificationBasis.FACTORY_MATRIX
    assert quota.is_verified is True
    assert quota.input_upper_bound() == min(
        FACTORY_MAIN_MODEL_CONTEXT_WINDOW, FACTORY_MAIN_MODEL_MAX_INPUT_TOKENS
    )


def test_settings_snapshot_records_activation_basis_and_takes_min_bound() -> None:
    quota = build_run_model_quota(
        _snapshot(_MODEL_A, context_window=_WINDOW_A, max_input_tokens=_MAX_INPUT_A)
    )
    assert quota.verification_basis is QuotaVerificationBasis.SETTINGS_ACTIVATION
    assert quota.config_revision == 3
    assert quota.input_upper_bound() == _MAX_INPUT_A


def test_snapshot_without_window_is_explicitly_unverified() -> None:
    quota = build_run_model_quota(
        _snapshot(_MODEL_A, context_window=None, max_input_tokens=None)
    )
    assert quota.verification_basis is QuotaVerificationBasis.UNVERIFIED
    assert quota.is_verified is False
    assert quota.input_upper_bound() is None


def test_resolve_prefers_the_run_snapshot() -> None:
    quota = RunModelQuota(
        model_id=_MODEL_A,
        context_window=_WINDOW_A,
        max_input_tokens=_MAX_INPUT_A,
        verification_basis=QuotaVerificationBasis.SETTINGS_ACTIVATION,
    )
    resolution = resolve_run_quota(
        {RUN_MODEL_QUOTA_CONFIG_KEY: quota.to_config(), "run_model_id": _MODEL_A},
        current_snapshot=_snapshot(
            _MODEL_B, context_window=_WINDOW_B, max_input_tokens=_MAX_INPUT_B
        ),
    )
    assert resolution.resolved is True
    assert resolution.quota is not None
    assert resolution.quota.model_id == _MODEL_A
    assert resolution.quota.context_window == _WINDOW_A
    assert resolution.compat_applied is False


def test_resolve_locks_out_an_unreadable_quota_version() -> None:
    payload = RunModelQuota(
                  verification_basis=QuotaVerificationBasis.SETTINGS_ACTIVATION,
        model_id=_MODEL_A, context_window=_WINDOW_A, max_input_tokens=_MAX_INPUT_A
    ).to_config()
    payload["quota_version"] = "model-quota-v99"
    resolution = resolve_run_quota(
        {RUN_MODEL_QUOTA_CONFIG_KEY: payload}, current_snapshot=None
    )
    assert resolution.resolved is False
    assert resolution.reason == QUOTA_REASON_UNREADABLE


def test_legacy_run_with_matching_config_gets_auditable_compat() -> None:
    """旧运行只锁模型 ID、当前配置仍是同一模型：可审计兼容补齐。"""
    resolution = resolve_run_quota(
        {"run_model_id": _MODEL_A},
        current_snapshot=_snapshot(
            _MODEL_A, context_window=_WINDOW_A, max_input_tokens=_MAX_INPUT_A
        ),
    )
    assert resolution.resolved is True
    assert resolution.compat_applied is True
    assert resolution.basis is QuotaVerificationBasis.RUNTIME_CONFIG_SNAPSHOT
    assert resolution.quota is not None
    assert resolution.quota.input_upper_bound() == _MAX_INPUT_A


def test_legacy_run_whose_model_switched_is_locked_out_not_32768() -> None:
    """复核脚本旧自定义模型分支：旧运行不再静默回退 32,768 缺省窗口。"""
    resolution = resolve_run_quota(
        {"run_model_id": "旧自定义模型"},
        current_snapshot=_snapshot(
            _MODEL_B, context_window=_WINDOW_B, max_input_tokens=_MAX_INPUT_B
        ),
    )
    assert resolution.resolved is False
    assert resolution.quota is None
    assert resolution.reason == QUOTA_REASON_LEGACY_UNVERIFIED


def test_run_without_a_lock_falls_back_to_the_factory_snapshot() -> None:
    resolution = resolve_run_quota({}, current_snapshot=None)
    assert resolution.resolved is True
    assert resolution.basis is QuotaVerificationBasis.FACTORY_MATRIX


def test_export_and_redaction_audit_exclude_sensitive_fields() -> None:
    quota = RunModelQuota(
                verification_basis=QuotaVerificationBasis.SETTINGS_ACTIVATION,
        model_id=_MODEL_A, context_window=_WINDOW_A, max_input_tokens=_MAX_INPUT_A
    )
    exported = export_run_model_quota(quota)
    assert exported["model_id"] == _MODEL_A
    assert exported["quota_version"] == MODEL_QUOTA_VERSION
    assert "api_key" in exported["redacted_fields"]
    audit = redaction_audit()
    assert audit["contains_credentials"] is False
    assert audit["contains_prompt_body"] is False
    assert "prompt" in audit["excluded_fields"]


# ---------------------------------------------------------------------------
# 验收 2：不同输出额度预留不同空间 + 未知元数据不冒充已验证窗口
# ---------------------------------------------------------------------------


def test_quota_snapshot_drives_window_and_marks_verified() -> None:
    quota = RunModelQuota(
        model_id=_MODEL_A,
        context_window=_WINDOW_A,
        max_input_tokens=_MAX_INPUT_A,
        verification_basis=QuotaVerificationBasis.SETTINGS_ACTIVATION,
    )
    compiled = compile_turn_context(
        messages=[_record("u1", ChatMessageRole.USER, "当前请求")],
        current_user_message_id="u1",
        model_id=_MODEL_A,
        mode=ChatMode.COMPANION,
        quota=quota,
        output_tokens=512,
    )
    assert compiled.context_window == _MAX_INPUT_A
    assert compiled.quota_verified is True
    assert compiled.window_verified is True
    assert compiled.output_quota_tokens == 512
    assert compiled.reserves.output_tokens == 512 + OUTPUT_RESERVE_MARGIN_TOKENS
    assert compiled.input_budget_tokens == (
        _MAX_INPUT_A - (512 + OUTPUT_RESERVE_MARGIN_TOKENS) - TOOL_RESERVE_TOKENS
    )
    record = compiled.to_record()
    assert record["quota_version"] == MODEL_QUOTA_VERSION
    assert record["quota_verification_basis"] == "settings_activation"
    assert record["quota_verified"] is True
    assert record["window_verified"] is True
    assert record["output_quota_tokens"] == 512


def test_different_output_quotas_reserve_different_space() -> None:
    quota = RunModelQuota(
                verification_basis=QuotaVerificationBasis.SETTINGS_ACTIVATION,
        model_id=_MODEL_A,
        context_window=_WINDOW_A,
        max_input_tokens=_MAX_INPUT_A,
    )
    messages = [_record("u1", ChatMessageRole.USER, "当前请求")]
    small = compile_turn_context(
        messages=messages,
        current_user_message_id="u1",
        model_id=_MODEL_A,
        mode=ChatMode.COMPANION,
        quota=quota,
        output_tokens=256,
    )
    large = compile_turn_context(
        messages=messages,
        current_user_message_id="u1",
        model_id=_MODEL_A,
        mode=ChatMode.COMPANION,
        quota=quota,
        output_tokens=2048,
    )
    assert small.reserves.output_tokens == 256 + OUTPUT_RESERVE_MARGIN_TOKENS
    assert large.reserves.output_tokens == 2048 + OUTPUT_RESERVE_MARGIN_TOKENS
    assert small.input_budget_tokens > large.input_budget_tokens


def test_unknown_model_window_is_not_reported_as_verified() -> None:
    compiled = compile_turn_context(
        messages=[_record("u1", ChatMessageRole.USER, "当前请求")],
        current_user_message_id="u1",
        model_id="unknown-custom-model",
        mode=ChatMode.COMPANION,
    )
    assert compiled.context_window == DEFAULT_CONTEXT_WINDOW
    assert compiled.window_verified is False
    assert compiled.quota_verified is False
    assert compiled.to_record()["window_verified"] is False
    known = compile_turn_context(
        messages=[_record("u1", ChatMessageRole.USER, "当前请求")],
        current_user_message_id="u1",
        model_id=CHAT_MODEL_ID,
        mode=ChatMode.COMPANION,
    )
    assert is_verified_context_window(CHAT_MODEL_ID) is True
    assert known.window_verified is True


def test_zero_max_input_quota_does_not_fall_back_to_default_window() -> None:
    """验收 2：已验证快照的 0 额度不得被 ``or`` 换成 32,768 缺省窗口。

    回归：``input_upper_bound() or DEFAULT_CONTEXT_WINDOW`` 会把 0（假值）
    误判为缺省，同时仍标注已验证——正是规范禁止的「任意常数冒充已验证额度」。
    """
    quota = RunModelQuota(
        model_id=_MODEL_A,
        context_window=_WINDOW_A,
        max_input_tokens=0,
        verification_basis=QuotaVerificationBasis.SETTINGS_ACTIVATION,
    )
    assert quota.is_verified is True
    assert quota.input_upper_bound() == 0
    compiled = compile_turn_context(
        messages=[_record("u1", ChatMessageRole.USER, "当前请求")],
        current_user_message_id="u1",
        model_id=_MODEL_A,
        mode=ChatMode.COMPANION,
        quota=quota,
    )
    # 采用真实上界 0（闭锁到无输入预算），而不是缺省 32,768。
    assert compiled.context_window == 0
    assert compiled.context_window != DEFAULT_CONTEXT_WINDOW
    assert compiled.input_budget_tokens == 0


# ---------------------------------------------------------------------------
# 验收 1：排队、重试与闭锁
# ---------------------------------------------------------------------------


def test_queued_old_run_keeps_its_snapshot_and_next_run_takes_new_config(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """验收 1：排队期间换配置，旧运行用旧快照真实额度，新消息用新配置。"""
    account = _register(client)
    adapter = _ProgrammableAdapter()
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    _activate(sqlite_app, _MODEL_A, window=_WINDOW_A, max_input=_MAX_INPUT_A)
    conversation_id = _create_conversation(client)

    first = generation_helpers["send"](client, conversation_id, content="第一轮")
    run_one = sqlite_app.state.chat_service.generation_run(  # noqa: SLF001
        account["id"], first["run_id"]
    )
    assert run_one is not None and run_one.config is not None
    # 入队时原子保存完整额度快照。
    assert run_one.config["run_model_id"] == _MODEL_A
    quota_one = run_one.config[RUN_MODEL_QUOTA_CONFIG_KEY]
    assert quota_one["context_window"] == _WINDOW_A
    assert quota_one["max_input_tokens"] == _MAX_INPUT_A

    # 排队期间激活新模型：旧运行不得随新配置漂移。
    _activate(sqlite_app, _MODEL_B, window=_WINDOW_B, max_input=_MAX_INPUT_B)
    generation_helpers["drive"](sqlite_app)

    assert adapter.models == [_MODEL_A]
    records = _compiled_audit_records(sqlite_app)
    assert records[0]["model_id"] == _MODEL_A
    assert records[0]["context_window"] == _MAX_INPUT_A
    assert records[0]["quota_verified"] is True

    # 新消息采用新配置。
    generation_helpers["send"](client, conversation_id, content="第二轮")
    generation_helpers["drive"](sqlite_app)
    assert adapter.models == [_MODEL_A, _MODEL_B]
    records = _compiled_audit_records(sqlite_app)
    assert records[1]["model_id"] == _MODEL_B
    assert records[1]["context_window"] == _MAX_INPUT_B
    # 历史模型标识不被改写。
    assert [m["model_id"] for m in _assistant_messages(client, conversation_id)] == [
        _MODEL_A,
        _MODEL_B,
    ]


def test_retry_after_config_switch_reuses_the_original_snapshot(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """验收 1：重试沿用原轮次启动时的模型与额度快照。"""
    account = _register(client, tag="2")
    adapter = _ProgrammableAdapter(fail=True)
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    _activate(sqlite_app, _MODEL_A, window=_WINDOW_A, max_input=_MAX_INPUT_A)
    conversation_id = _create_conversation(client)

    first = generation_helpers["send"](client, conversation_id, content="帮我分析论文")
    generation_helpers["drive"](sqlite_app)
    assert adapter.models == [_MODEL_A]

    _activate(sqlite_app, _MODEL_B, window=_WINDOW_B, max_input=_MAX_INPUT_B)
    adapter.fail = False
    _, retried, _ = sqlite_app.state.chat_service.retry_generation(  # noqa: SLF001
        account["id"], conversation_id, first["assistant_message"]["message_id"]
    )
    retry_run = sqlite_app.state.chat_service._repo.get_run_by_message(  # noqa: SLF001
        account["id"], retried.message_id
    )
    assert retry_run is not None and retry_run.config is not None
    # 重试仍锁定旧模型与旧额度快照（不因新配置漂移）。
    assert retry_run.config["run_model_id"] == _MODEL_A
    assert (
        retry_run.config[RUN_MODEL_QUOTA_CONFIG_KEY]["max_input_tokens"] == _MAX_INPUT_A
    )

    generation_helpers["drive"](sqlite_app)
    assert adapter.models == [_MODEL_A, _MODEL_A]


def test_service_locks_out_an_unresolvable_legacy_run() -> None:
    """验收 1：旧运行缺真实额度时明确闭锁，而不是用 32,768 冒充。"""
    current = _record("u1", ChatMessageRole.USER, "合成请求")
    service = ChatService.__new__(ChatService)
    service._repo = SimpleNamespace(  # noqa: SLF001 - 复现复核脚本的最小装配
        get_message=lambda *_: current,
        get_conversation=lambda *_: SimpleNamespace(mode="companion"),
        list_messages=lambda *_: [current],
    )
    service._attachments = None
    service._observability = None
    service._model_config_provider = SimpleNamespace(
        snapshot=lambda: _snapshot(
            _MODEL_B, context_window=_WINDOW_B, max_input_tokens=_MAX_INPUT_B
        )
    )
    run = SimpleNamespace(
        account_id="合成账户",
        conversation_id="合成会话",
        user_message_id=current.message_id,
        assistant_message_id="合成助手消息",
        config={"run_model_id": "旧自定义模型"},
    )
    with pytest.raises(ChatDomainError) as excinfo:
        service.compile_turn_context(run)
    assert excinfo.value.code == "model_quota_unverified"


# ---------------------------------------------------------------------------
# 验收 3：每次调用可定位实际能力与全部必要版本
# ---------------------------------------------------------------------------


def test_each_call_records_its_own_versions_and_model() -> None:
    """验收 3：同一运行多次调用各自成锁，锁内含全部必要版本。"""
    registry = _chat_registry()
    gateway = ModelGateway(registry)
    adapter = _ProgrammableAdapter()
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    contract = CallContractVersions(
        recipe_version="daily-parent-v2",
        context_compile_version="ctx-budget-v1",
        quality_policy_version="global-chat-lightweight-v2",
        estimate_version="token-estimate-v1",
        quota_version=MODEL_QUOTA_VERSION,
    )

    first = gateway.invoke(
        "qwen_text_chat",
        "1",
        _context("run-multi"),
        call_contract=contract,
        model_override=_MODEL_A,
        model_quota=RunModelQuota(
                        verification_basis=QuotaVerificationBasis.SETTINGS_ACTIVATION,
            model_id=_MODEL_A, context_window=_WINDOW_A, max_input_tokens=_MAX_INPUT_A
        ),
    )
    second = gateway.invoke(
        "qwen_text_chat",
        "1",
        _context("run-multi"),
        call_contract=contract,
        model_override=_MODEL_B,
        model_quota=RunModelQuota(
                        verification_basis=QuotaVerificationBasis.SETTINGS_ACTIVATION,
            model_id=_MODEL_B, context_window=_WINDOW_B, max_input_tokens=_MAX_INPUT_B
        ),
    )

    assert first.lock is not None and second.lock is not None
    assert first.lock.actual_model_id == _MODEL_A
    assert second.lock.actual_model_id == _MODEL_B
    # 两次调用各自记录完整版本合同，而不是用最后一次代表整次工作流。
    for lock in (first.lock, second.lock):
        assert lock.call_contract is not None
        assert lock.call_contract.recipe_version == "daily-parent-v2"
        assert lock.call_contract.context_compile_version == "ctx-budget-v1"
        assert lock.call_contract.estimate_version == "token-estimate-v1"
        assert lock.call_contract.quota_version == MODEL_QUOTA_VERSION
        # 能力合同（提示词/Schema）由网关从能力记录补齐。
        assert lock.call_contract.prompt_version == "1"
        assert lock.call_contract.input_schema_version
        assert lock.call_contract.output_schema_version
        assert lock.call_contract.redaction_audit()["contains_credentials"] is False


def test_embedding_binding_is_untouched_by_a_run_quota() -> None:
    """验收 4：向量化模型与索引版本独立，不随运行额度快照改变。"""
    registry = _chat_registry()
    registry.register(
        CapabilityRecord(
            name="qwen_embedding",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id=EMBEDDING_MODEL_ID,
            input_schema_version="embed-in-v1",
            output_schema_version="embed-out-v1",
            status=CapabilityStatus.VERIFIED,
        )
    )
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", _ProgrammableAdapter())
    embedding_adapter = _ProgrammableAdapter()
    gateway.register_adapter("qwen_embedding", "1", embedding_adapter)

    result = gateway.invoke(
        "qwen_embedding",
        "1",
        _context("run-embed"),
        model_override=_MODEL_A,
        model_quota=RunModelQuota(
                        verification_basis=QuotaVerificationBasis.SETTINGS_ACTIVATION,
            model_id=_MODEL_A, context_window=_WINDOW_A, max_input_tokens=_MAX_INPUT_A
        ),
    )

    assert result.lock is not None
    assert result.lock.actual_model_id == EMBEDDING_MODEL_ID
    assert embedding_adapter.models == [EMBEDDING_MODEL_ID]


# ---------------------------------------------------------------------------
# 验收 4：迁移、恢复与脱敏
# ---------------------------------------------------------------------------


def test_call_contract_is_persisted_and_read_back(tmp_path: Path) -> None:
    """验收 3/4：运行锁的版本合同随锁持久化并可读回。"""
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    assert SCHEMA_VERSION == 60
    columns = {
        str(row["name"])
        for row in database.connection.execute(
            "PRAGMA table_info(model_run_locks)"
        ).fetchall()
    }
    assert "call_contract_json" in columns

    repository = ConversationRepository(database)
    service = ChatService(
        repository=repository, gateway=_gateway_with(_ProgrammableAdapter())
    )
    created = service.create_conversation("alice")
    _, assistant, _ = service.start_generation("alice", created.conversation_id, "你好")
    list(
        service.stream_generation(
            "alice",
            created.conversation_id,
            assistant.message_id,
            _context("run-persist", account_id="alice"),
        )
    )
    recorder = repository._run_lock_recorder  # noqa: SLF001 - 测试直读
    locks = recorder.list_locks_by_run("alice", "run-persist")
    assert len(locks) == 1
    contract = locks[0].call_contract
    assert contract is not None
    assert contract.recipe_version == "daily-parent-v2"
    assert contract.quota_version == MODEL_QUOTA_VERSION


def test_legacy_rows_without_a_call_contract_read_back_none(tmp_path: Path) -> None:
    """验收 4：迁移 60 之前的旧行读回 None（不伪造版本合同）。"""
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder

    recorder = SqliteModelRunLockRecorder(database)
    lock = ModelRunLock(
        lock_id="lock-legacy",
        run_id="run-1",
        account_id="alice",
        project_id="conversation-1",
        capability_name="qwen_text_chat",
        capability_version="1",
        actual_model_id=CHAT_MODEL_ID,
        region="cn-beijing",
        parameters={},
        prompt_version="1",
        input_output_contract="qwen_text_chat:in->out",
        status=ModelCallStatus.SUCCESS,
        created_at=datetime.now(UTC),
    )
    recorder.record(
        lock,
        business_ref=BusinessRef(
            object_type="message", object_id="m1", operation="generate"
        ),
    )
    persisted = recorder.get_lock("lock-legacy", "alice")
    assert persisted is not None
    assert persisted.call_contract is None


def test_recorder_rejects_credential_shaped_call_contract(tmp_path: Path) -> None:
    """验收 4：版本合同不得夹带凭据形态内容。"""
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder

    recorder = SqliteModelRunLockRecorder(database)
    lock = ModelRunLock(
        lock_id="lock-secret",
        run_id="run-1",
        account_id="alice",
        project_id="conversation-1",
        capability_name="qwen_text_chat",
        capability_version="1",
        actual_model_id=CHAT_MODEL_ID,
        region="cn-beijing",
        parameters={},
        prompt_version="1",
        input_output_contract="qwen_text_chat:in->out",
        call_contract=CallContractVersions(recipe_version="sk-abcdefghijklmnop0123"),
        status=ModelCallStatus.SUCCESS,
        created_at=datetime.now(UTC),
    )
    with pytest.raises(ModelRunLockSecurityError):
        recorder.record(
            lock,
            business_ref=BusinessRef(
                object_type="message", object_id="m1", operation="generate"
            ),
        )
    # 合同未落库（整个事务回滚），无孤儿锁。
    assert recorder.get_lock("lock-secret", "alice") is None


def test_quota_export_contains_no_credentials(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """验收 4：快照导出与审计不含凭据，账户隔离成立。"""
    account = _register(client)
    sqlite_app.state.chat_service._gateway = _gateway_with(  # noqa: SLF001
        _ProgrammableAdapter()
    )
    _activate(sqlite_app, _MODEL_A, window=_WINDOW_A, max_input=_MAX_INPUT_A)
    conversation_id = _create_conversation(client)
    created = generation_helpers["send"](client, conversation_id, content="你好")
    run = sqlite_app.state.chat_service.generation_run(  # noqa: SLF001
        account["id"], created["run_id"]
    )
    assert run is not None and run.config is not None
    quota = RunModelQuota.model_validate(run.config[RUN_MODEL_QUOTA_CONFIG_KEY])
    exported = export_run_model_quota(quota)
    # 值域（额度字段本身）不得出现任何凭据形态内容。
    values_only = json.dumps(
        {k: v for k, v in exported.items() if k != "redacted_fields"},
        ensure_ascii=False,
    )
    for forbidden in ("sk-", "Bearer", "api_key", "authorization"):
        assert forbidden not in values_only
    # 排除声明仍应显式列出敏感字段名，供导出与审计复核。
    assert "api_key" in exported["redacted_fields"]
    assert "authorization" in exported["redacted_fields"]
    # 审计声明自证不含凭据与私人正文。
    audit = redaction_audit()
    assert audit["contains_credentials"] is False
    assert audit["contains_prompt_body"] is False
    # 跨账户读取同一 run 快照不可得（账户隔离）。
    other = sqlite_app.state.chat_service.generation_run(  # noqa: SLF001
        "someone-else", created["run_id"]
    )
    assert other is None
