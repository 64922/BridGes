"""Issue 13：混合抽取来源投影、模型锁持久化与账户隔离合同测试。

覆盖：
- SQLite 下 run/observation 的 source 字段跨重启可查、回放不改写来源；
- qwen_model 分支每次真实 attempt 持久化独立锁（失败锁保留、重试新增
  下一序号锁、幂等不重复）；
- local_rule 分支零调用零锁；
- 来源守卫（本地误调模型 / Qwen 缺锁）失败关闭；
- /profiles/status 投影诚实解释混合策略且不含画像正文与凭据；
- production-like 组合：Stub/cassette/缺 adapter 时 Qwen 分支失败关闭，
  本地分支保持纯本地。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from bridges.ai.capability_registry import CapabilityRegistry
from bridges.ai.adapters import StubQwenAdapter
from bridges.ai.composition import (
    MODEL_MATRIX_DRIFT,
    PRODUCTION_TEST_ADAPTER,
    validate_production_composition,
)
from bridges.ai.model_gateway import ModelGateway
from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder
from bridges.api.main import create_app
from bridges.contracts.ai import (
    BusinessRef,
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    ModelCallStatus,
    ModelRunLock,
)
from bridges.contracts.profile_extraction import (
    PROFILE_HYBRID_EXPLANATION,
    PROFILE_SOURCE_LABELS,
    ProfileExtractionOutcome,
    ProfileExtractionSource,
    ProfileExtractionStatus,
)
from bridges.contracts.profiles import FourDimension
from bridges.profiles import (
    AutomaticProfileService,
    FourDimensionProfileService,
    GatewayAutomaticProfileExtractor,
    InMemoryFourDimensionProfileRepository,
    InMemoryAutomaticProfileRepository,
    RuleBasedAutomaticProfileExtractor,
    SqliteAutomaticProfileRepository,
    SqliteFourDimensionProfileRepository,
)
from bridges.storage import BridgesDatabase

_AMBIGUOUS_CONTENT = "我可能想学习 Transformer"
_OBSERVE_OUTPUT: dict[str, object] = {
    "items": [
        {
            "dimension": FourDimension.KNOWLEDGE_INTEREST.value,
            "normalized_value": "Transformer",
            "evidence_ref": "message-1",
            "reliability": 0.9,
            "action": "observe",
        }
    ]
}


def _fake_lock(
    lock_id: str,
    *,
    status: ModelCallStatus,
    error_code: str | None = None,
    run_id: str = "run-1",
    account_id: str = "account-alice",
    project_id: str = "conversation-1",
) -> ModelRunLock:
    """构造与真实网关同形的不可变运行锁（不携带正文/凭据）。"""

    return ModelRunLock(
        lock_id=lock_id,
        run_id=run_id,
        account_id=account_id,
        project_id=project_id,
        capability_name="qwen_profile_extraction",
        capability_version="1",
        actual_model_id="qwen3.7-plus-2026-05-26",
        region="cn-beijing",
        parameters={"temperature": 0, "max_tokens": 512},
        prompt_version="2026-08-12",
        input_output_contract="qwen_profile_extraction:profile-message-v1->profile-extraction-v2",
        status=status,
        error_code=error_code,
        created_at=datetime.now(UTC),
    )


class _LockAwareCountingGateway:
    """记录调用次数并按调用上下文返回真实形制锁的假网关。

    ``fail="transient"`` + ``fail_once=True`` 时只让第一次调用失败，
    用于队列重试恢复用例。
    """

    def __init__(
        self,
        output: dict[str, object] | None = None,
        *,
        fail: str | None = None,
        fail_once: bool = False,
        with_lock: bool = True,
    ) -> None:
        self.calls = 0
        self._output = output if output is not None else {"items": []}
        self._fail = fail
        self._fail_once = fail_once
        self._with_lock = with_lock

    def invoke(self, *args: object, **kwargs: object) -> SimpleNamespace:
        del kwargs
        self.calls += 1
        failing = self._fail is not None and (
            not self._fail_once or self.calls == 1
        )
        # 真实网关签名：invoke(capability_name, capability_version,
        # run_context, payload=...)，run_context 是第三个位置参数。
        run_context = args[2] if len(args) > 2 else None
        lock = (
            _fake_lock(
                f"fake-lock-{self.calls}",
                status=(
                    ModelCallStatus.RETRYABLE_FAIL
                    if failing
                    else ModelCallStatus.SUCCESS
                ),
                error_code=self._fail if failing else None,
                run_id=run_context.run_id if run_context is not None else "run-1",
                account_id=(
                    run_context.account_id
                    if run_context is not None
                    else "account-alice"
                ),
                project_id=(
                    run_context.project_id
                    if run_context is not None
                    else "conversation-1"
                ),
            )
            if self._with_lock
            else None
        )
        if failing:
            return SimpleNamespace(
                status=ModelCallStatus.RETRYABLE_FAIL,
                output=None,
                error_code=self._fail,
                error_message="temporary provider failure",
                lock=lock,
            )
        return SimpleNamespace(
            status=ModelCallStatus.SUCCESS,
            output=self._output,
            error_code=None,
            error_message=None,
            lock=lock,
        )


def _sqlite_service(
    database: BridgesDatabase,
    gateway: _LockAwareCountingGateway,
    *,
    recorder: SqliteModelRunLockRecorder,
) -> AutomaticProfileService:
    return AutomaticProfileService(
        four_dimension_service=FourDimensionProfileService(
            source_repository=None,  # type: ignore[arg-type]
            repository=SqliteFourDimensionProfileRepository(database),
        ),
        repository=SqliteAutomaticProfileRepository(database),
        extractor=GatewayAutomaticProfileExtractor(cast(ModelGateway, gateway)),
        lock_recorder=recorder,
    )


def _memory_service(
    gateway: _LockAwareCountingGateway,
    *,
    recorder: SqliteModelRunLockRecorder | None = None,
) -> AutomaticProfileService:
    return AutomaticProfileService(
        four_dimension_service=FourDimensionProfileService(
            source_repository=None,  # type: ignore[arg-type]
            repository=InMemoryFourDimensionProfileRepository(),
        ),
        repository=InMemoryAutomaticProfileRepository(),
        extractor=GatewayAutomaticProfileExtractor(cast(ModelGateway, gateway)),
        lock_recorder=recorder,
    )


# ---------------------------------------------------------------------------
# SQLite 持久化：来源字段、锁、重启与回放
# ---------------------------------------------------------------------------


def test_sqlite_source_fields_survive_restart_and_replay_keeps_source(tmp_path) -> None:
    database_path = tmp_path / "source.db"
    first_database = BridgesDatabase(database_path)
    first_database.initialize()
    gateway = _LockAwareCountingGateway(_OBSERVE_OUTPUT)
    recorder = SqliteModelRunLockRecorder(first_database)
    first_service = _sqlite_service(first_database, gateway, recorder=recorder)

    local = first_service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-local",
        content="我的目标是今年通过雅思考试",
        run_id="run-local",
        mode="companion",
    )
    ambiguous = first_service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content=_AMBIGUOUS_CONTENT,
        run_id="run-qwen",
        mode="companion",
    )

    assert local.run.source == ProfileExtractionSource.LOCAL_RULE
    assert ambiguous.run.source == ProfileExtractionSource.QWEN_MODEL
    assert ambiguous.run.status == ProfileExtractionStatus.SUCCEEDED
    assert gateway.calls == 1
    first_database.close()

    second_database = BridgesDatabase(database_path)
    second_recorder = SqliteModelRunLockRecorder(second_database)
    second_service = _sqlite_service(
        second_database, _LockAwareCountingGateway(_OBSERVE_OUTPUT), recorder=second_recorder
    )

    # 回放同一消息：不重复调用网关、来源不被改写。
    replay = second_service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content=_AMBIGUOUS_CONTENT,
        run_id="run-replay",
        mode="companion",
    )
    assert replay.run.source == ProfileExtractionSource.QWEN_MODEL
    assert replay.run.status == ProfileExtractionStatus.SUCCEEDED

    runs = second_service._repository.list_runs("account-alice")  # type: ignore[attr-defined]
    sources = {run.message_id: run.source for run in runs}
    assert sources == {
        "message-local": ProfileExtractionSource.LOCAL_RULE,
        "message-1": ProfileExtractionSource.QWEN_MODEL,
    }
    observations = second_service._repository.list_observations(  # type: ignore[attr-defined]
        "account-alice",
        FourDimension.KNOWLEDGE_INTEREST,
        "Transformer",
        since=datetime.now(UTC) - timedelta(days=1),
    )
    assert all(
        observation.source == ProfileExtractionSource.QWEN_MODEL
        for observation in observations
    )
    # 锁跨重启可查：qwen run 一条锁，local run 零条锁。
    qwen_run = next(run for run in runs if run.message_id == "message-1")
    local_run = next(run for run in runs if run.message_id == "message-local")
    qwen_locks = second_recorder.list_locks_by_run(
        "account-alice", qwen_run.extraction_id
    )
    assert len(qwen_locks) == 1
    assert qwen_locks[0].status == ModelCallStatus.SUCCESS
    assert second_recorder.list_locks_by_run("account-alice", local_run.extraction_id) == []
    second_database.close()


def test_sqlite_qwen_retry_persists_failed_lock_then_appends_next_ordinal(
    tmp_path,
) -> None:
    database_path = tmp_path / "retry-locks.db"
    database = BridgesDatabase(database_path)
    database.initialize()
    gateway = _LockAwareCountingGateway(
        _OBSERVE_OUTPUT, fail="transient", fail_once=True
    )
    recorder = SqliteModelRunLockRecorder(database)
    service = _sqlite_service(database, gateway, recorder=recorder)

    first = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content=_AMBIGUOUS_CONTENT,
        run_id="run-1",
        mode="companion",
    )
    assert first.run.status == ProfileExtractionStatus.PENDING
    assert first.run.outcome == ProfileExtractionOutcome.PENDING_RETRY

    service.run_retry_tick()

    task = service.list_retry_tasks("account-alice")[0]
    assert task.status == ProfileExtractionStatus.SUCCEEDED
    assert gateway.calls == 2
    locks = recorder.list_locks_by_run("account-alice", first.run.extraction_id)
    # 两条独立锁：首次失败锁保留，重试成功锁追加，序号稳定递增。
    assert len(locks) == 2
    assert [lock.status for lock in locks] == [
        ModelCallStatus.RETRYABLE_FAIL,
        ModelCallStatus.SUCCESS,
    ]
    assert [ref.attempt_ordinal for ref in locks[0].business_refs] == [1]
    assert [ref.attempt_ordinal for ref in locks[1].business_refs] == [2]
    assert all(
        ref.object_type == "profile_extraction_run"
        and ref.object_id == first.run.extraction_id
        for lock in locks
        for ref in lock.business_refs
    )
    # 业务引用查询同样按序号返回。
    by_ref = recorder.list_locks_by_business_ref(
        "account-alice", "profile_extraction_run", first.run.extraction_id
    )
    assert len(by_ref) == 2
    assert by_ref[0].business_refs[0].attempt_ordinal == 1
    assert by_ref[1].business_refs[0].attempt_ordinal == 2

    # 回放同一消息（幂等）：不新增锁。
    service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content=_AMBIGUOUS_CONTENT,
        run_id="run-replay",
        mode="companion",
    )
    assert gateway.calls == 2
    assert len(recorder.list_locks_by_run("account-alice", first.run.extraction_id)) == 2
    database.close()


def test_cross_account_locks_and_sources_stay_isolated(tmp_path) -> None:
    database_path = tmp_path / "isolation.db"
    database = BridgesDatabase(database_path)
    database.initialize()
    gateway = _LockAwareCountingGateway(_OBSERVE_OUTPUT)
    recorder = SqliteModelRunLockRecorder(database)
    service = _sqlite_service(database, gateway, recorder=recorder)

    alice = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content=_AMBIGUOUS_CONTENT,
        run_id="run-alice",
        mode="companion",
    )
    bob = service.preprocess_message(
        "account-bob",
        conversation_id="conversation-1",
        message_id="message-1",
        content=_AMBIGUOUS_CONTENT,
        run_id="run-bob",
        mode="companion",
    )

    assert gateway.calls == 2
    assert alice.run.extraction_id != bob.run.extraction_id
    assert alice.run.account_id == "account-alice"
    assert bob.run.account_id == "account-bob"
    # 账户作用域：互相查不到对方锁。
    assert recorder.list_locks_by_run("account-alice", alice.run.extraction_id)
    assert recorder.list_locks_by_run("account-alice", bob.run.extraction_id) == []
    assert recorder.list_locks_by_run("account-bob", alice.run.extraction_id) == []
    assert recorder.list_locks_by_run("account-bob", bob.run.extraction_id)
    # 共享全局 Key 不产生任何跨账户关联：锁 ID 全局唯一且按账户隔离。
    alice_locks = recorder.list_locks_by_run("account-alice", alice.run.extraction_id)
    bob_locks = recorder.list_locks_by_run("account-bob", bob.run.extraction_id)
    assert {lock.lock_id for lock in alice_locks}.isdisjoint(
        {lock.lock_id for lock in bob_locks}
    )
    database.close()


def test_sqlite_local_branch_never_persists_any_model_lock(tmp_path) -> None:
    database_path = tmp_path / "local-locks.db"
    database = BridgesDatabase(database_path)
    database.initialize()
    gateway = _LockAwareCountingGateway()
    recorder = SqliteModelRunLockRecorder(database)
    service = _sqlite_service(database, gateway, recorder=recorder)

    for index, content in enumerate(
        (
            "我的目标是今年通过雅思考试",
            "想学习Transformer",
            "找Transformer论文",
        ),
        start=1,
    ):
        result = service.preprocess_message(
            "account-alice",
            conversation_id=f"conversation-{index}",
            message_id=f"message-{index}",
            content=content,
            run_id=f"run-{index}",
            mode="companion",
        )
        assert result.run.source == ProfileExtractionSource.LOCAL_RULE
        assert recorder.list_locks_by_run("account-alice", result.run.extraction_id) == []

    assert gateway.calls == 0
    assert len(recorder.list_locks_by_run("account-alice", "any-run")) == 0
    database.close()


# ---------------------------------------------------------------------------
# 来源守卫失败关闭
# ---------------------------------------------------------------------------


def test_guard_fails_closed_when_qwen_branch_has_no_lock(tmp_path) -> None:
    database_path = tmp_path / "guard.db"
    database = BridgesDatabase(database_path)
    database.initialize()
    gateway = _LockAwareCountingGateway(with_lock=False)
    recorder = SqliteModelRunLockRecorder(database)
    service = _sqlite_service(database, gateway, recorder=recorder)

    result = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content=_AMBIGUOUS_CONTENT,
        run_id="run-1",
        mode="companion",
    )

    assert gateway.calls == 1
    assert result.run.status == ProfileExtractionStatus.EXHAUSTED
    assert result.run.outcome == ProfileExtractionOutcome.PERMANENT_FAILURE
    assert result.run.last_error == "profile_qwen_missing_run_lock"
    assert (
        service._four_dimensions.list_records("account-alice") == []
    )  # noqa: SLF001
    database.close()


def test_guard_fails_closed_when_local_branch_emits_lock(tmp_path) -> None:
    database_path = tmp_path / "guard-local.db"
    database = BridgesDatabase(database_path)
    database.initialize()
    recorder = SqliteModelRunLockRecorder(database)

    class _LockEmittingLocalExtractor:
        version = "local-leaking-v1"

        def extract(self, **kwargs: object) -> Any:
            lock_sink = kwargs.get("lock_sink")
            if lock_sink is not None:
                lock_sink(
                    _fake_lock(
                        "leaked-lock",
                        status=ModelCallStatus.SUCCESS,
                    )
                )
            return {"items": []}

    service = AutomaticProfileService(
        four_dimension_service=FourDimensionProfileService(
            source_repository=None,  # type: ignore[arg-type]
            repository=SqliteFourDimensionProfileRepository(database),
        ),
        repository=SqliteAutomaticProfileRepository(database),
        extractor=cast(Any, _LockEmittingLocalExtractor()),
        lock_recorder=recorder,
    )

    result = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我的目标是今年通过雅思考试",
        run_id="run-1",
        mode="companion",
    )

    assert result.run.status == ProfileExtractionStatus.EXHAUSTED
    assert result.run.last_error == "profile_local_unexpected_model_call"
    assert result.run.outcome == ProfileExtractionOutcome.PERMANENT_FAILURE
    # 来源不变量：local_rule 分支即使出现异常锁，也绝不落库（0 锁）。
    assert (
        recorder.list_locks_by_run("account-alice", result.run.extraction_id) == []
    )
    database.close()


# ---------------------------------------------------------------------------
# 用户可见投影与 API 合同
# ---------------------------------------------------------------------------


def test_source_labels_are_stable_chinese_copy() -> None:
    assert PROFILE_SOURCE_LABELS == {
        ProfileExtractionSource.LOCAL_RULE: "本地规则识别，未调用模型",
        ProfileExtractionSource.QWEN_MODEL: "Qwen 辅助识别",
    }


def test_profile_status_projection_counts_sources_and_explains_hybrid_strategy() -> None:
    gateway = _LockAwareCountingGateway(_OBSERVE_OUTPUT)
    recorder = SqliteModelRunLockRecorder(BridgesDatabase(":memory:"))
    service = _memory_service(gateway, recorder=recorder)

    service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-local",
        content="我的目标是今年通过雅思考试",
        run_id="run-local",
        mode="companion",
    )
    service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-qwen",
        content=_AMBIGUOUS_CONTENT,
        run_id="run-qwen",
        mode="companion",
    )

    projection = service.profile_status("account-alice")
    assert projection.extraction_sources == {
        "local_rule": 1,
        "qwen_model": 1,
    }
    assert projection.source_explanation == PROFILE_HYBRID_EXPLANATION
    # 诚实披露：既不夸大为"全部由 Qwen 生成"，也不声称"完全不使用模型"。
    assert "本机规则" in projection.source_explanation
    assert "Qwen" in projection.source_explanation
    assert "所有画像均由 Qwen" not in projection.source_explanation
    assert "完全不使用模型" not in projection.source_explanation
    # 不包含画像正文、消息正文或凭据细节。
    assert "Transformer" not in projection.source_explanation
    assert "sk-" not in projection.source_explanation

    # 其他账户不共享来源统计。
    assert service.profile_status("account-bob").extraction_sources == {}


def test_profile_status_api_contract_for_hybrid_explanation() -> None:
    client = TestClient(create_app())

    registered = client.post(
        "/auth/register",
        json={
            "username": "Issue13",
            "qq_email": "131313@qq.com",
            "password": "correct-horse-25",
        },
    )
    assert registered.status_code == 201, registered.text

    response = client.get("/profiles/status")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "empty"
    assert body["extraction_sources"] == {}
    assert body["source_explanation"] == PROFILE_HYBRID_EXPLANATION
    # API 投影不携带凭据或正文。
    assert "sk-" not in response.text
    assert "API_KEY" not in response.text


def test_source_metrics_enable_gray_period_comparison() -> None:
    """灰度期核对：local_rule 0 调用 0 锁、qwen_model 每 attempt 1 锁。

    指标按 {source}:{outcome} 低基数计数，不携带账户、消息或正文。
    """
    from bridges.observability.service import ObservabilityService

    gateway = _LockAwareCountingGateway(_OBSERVE_OUTPUT)
    observability = ObservabilityService()
    service = AutomaticProfileService(
        four_dimension_service=FourDimensionProfileService(
            source_repository=None,  # type: ignore[arg-type]
            repository=InMemoryFourDimensionProfileRepository(),
        ),
        repository=InMemoryAutomaticProfileRepository(),
        extractor=GatewayAutomaticProfileExtractor(cast(ModelGateway, gateway)),
        observability_service=observability,
    )

    service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-local",
        content="我的目标是今年通过雅思考试",
        run_id="run-local",
        mode="companion",
    )
    service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content=_AMBIGUOUS_CONTENT,
        run_id="run-qwen",
        mode="companion",
    )

    metrics = observability.profile_metrics_snapshot()
    assert metrics["profile_extraction_source_total:local_rule:succeeded_written"] == 1
    assert metrics["profile_extraction_source_total:qwen_model:succeeded_observed"] == 1
    assert gateway.calls == 1
    # 守卫指标在正常路径下为 0。
    assert "profile_local_unexpected_model_call_total:total" not in metrics
    assert "profile_qwen_missing_run_lock_total:total" not in metrics
    assert "profile_lock_persist_failed_total:total" not in metrics


# ---------------------------------------------------------------------------
# production-like 组合：Stub/cassette/缺 adapter 失败关闭，本地分支保持纯本地
# ---------------------------------------------------------------------------


def _profile_only_registry_gateway() -> tuple[CapabilityRegistry, ModelGateway]:
    registry = CapabilityRegistry()
    registry.register(
        CapabilityRecord(
            name="qwen_profile_extraction",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen3.7-plus-2026-05-26",
            input_schema_version="profile-message-v1",
            output_schema_version="profile-extraction-v2",
            status=CapabilityStatus.VERIFIED,
            prompt_version="2026-08-12",
        )
    )
    return registry, ModelGateway(registry)


def test_production_composition_rejects_stub_for_qwen_branch() -> None:
    registry, gateway = _profile_only_registry_gateway()
    gateway.register_adapter(
        "qwen_profile_extraction", "1", StubQwenAdapter()
    )
    violations = validate_production_composition(
        registry,
        gateway,
        global_key_configured=True,
        vision_ocr_compatibility_proven=True,
    )
    codes = {violation.code for violation in violations}
    assert PRODUCTION_TEST_ADAPTER in codes
    assert any(
        violation.capability == "qwen_profile_extraction" for violation in violations
    )


def test_production_composition_rejects_cassette_for_qwen_branch() -> None:
    """cassette 回放适配器对 Qwen 分支同样失败关闭（Issue 13 AC 5）。"""
    registry, gateway = _profile_only_registry_gateway()
    violations = validate_production_composition(
        registry,
        gateway,
        global_key_configured=True,
        cassette_enabled=True,
        vision_ocr_compatibility_proven=True,
    )
    assert PRODUCTION_TEST_ADAPTER in {v.code for v in violations}


def test_production_composition_rejects_model_drift_for_qwen_branch() -> None:
    """模型矩阵漂移对 qwen_profile_extraction 失败关闭（Issue 13 AC 5）。"""
    registry, gateway = _profile_only_registry_gateway()
    drifted = registry.get("qwen_profile_extraction", "1")
    drifted.model_id = "qwen3.6-flash"
    violations = validate_production_composition(
        registry,
        gateway,
        global_key_configured=True,
        vision_ocr_compatibility_proven=True,
    )
    codes = {violation.code for violation in violations}
    assert MODEL_MATRIX_DRIFT in codes
    assert any(
        violation.capability == "qwen_profile_extraction" for violation in violations
    )


def test_qwen_unavailable_fails_closed_while_local_branch_stays_pure(tmp_path) -> None:
    """缺 adapter 的真实网关：Qwen 分支 no_adapter 失败关闭、不伪造本地
    输出；本地规则分支完全不受影响。"""
    registry, gateway = _profile_only_registry_gateway()
    database = BridgesDatabase(tmp_path / "no-adapter.db")
    database.initialize()
    recorder = SqliteModelRunLockRecorder(database)
    service = AutomaticProfileService(
        four_dimension_service=FourDimensionProfileService(
            source_repository=None,  # type: ignore[arg-type]
            repository=SqliteFourDimensionProfileRepository(database),
        ),
        repository=SqliteAutomaticProfileRepository(database),
        extractor=GatewayAutomaticProfileExtractor(gateway),
        lock_recorder=recorder,
    )

    ambiguous = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-qwen",
        content=_AMBIGUOUS_CONTENT,
        run_id="run-qwen",
        mode="companion",
    )
    assert ambiguous.run.status == ProfileExtractionStatus.EXHAUSTED
    assert ambiguous.run.outcome == ProfileExtractionOutcome.PERMANENT_FAILURE
    assert ambiguous.run.last_error == "no_adapter"
    assert ambiguous.run.source == ProfileExtractionSource.QWEN_MODEL
    # 失败关闭：绝不伪造本地规则输出来冒充等价语义。
    assert service._four_dimensions.list_records("account-alice") == []  # noqa: SLF001

    local = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-local",
        content="我的目标是今年通过雅思考试",
        run_id="run-local",
        mode="companion",
    )
    assert local.run.status == ProfileExtractionStatus.SUCCEEDED
    assert local.run.source == ProfileExtractionSource.LOCAL_RULE
    assert service._four_dimensions.list_records("account-alice")  # noqa: SLF001
    # 真实失败的 Qwen 尝试也留下不可变失败锁（no_adapter 属于网关阻断）。
    locks = recorder.list_locks_by_run("account-alice", ambiguous.run.extraction_id)
    assert len(locks) == 1
    assert locks[0].status == ModelCallStatus.BLOCKED
    assert locks[0].error_code == "no_adapter"
    database.close()


def test_rule_extractor_is_never_degraded_by_missing_qwen() -> None:
    """本地分支不依赖 Qwen 可用性：规则抽取器独立工作。"""
    service = AutomaticProfileService(
        four_dimension_service=FourDimensionProfileService(
            source_repository=None,  # type: ignore[arg-type]
            repository=InMemoryFourDimensionProfileRepository(),
        ),
        repository=InMemoryAutomaticProfileRepository(),
        extractor=RuleBasedAutomaticProfileExtractor(),
    )

    result = service.preprocess_message(
        "account-alice",
        conversation_id="conversation-1",
        message_id="message-1",
        content="想学习 Transformer",
        run_id="run-1",
        mode="companion",
    )

    assert result.run.status == ProfileExtractionStatus.SUCCEEDED
    assert result.run.source == ProfileExtractionSource.LOCAL_RULE
    assert result.run.outcome == ProfileExtractionOutcome.SUCCEEDED_WRITTEN
