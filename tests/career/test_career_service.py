"""生涯规划编排服务测试（Issue 29）。

验证：六类输出合同生成与投影、学习记录证据编译、失败分类（可重试/
权限/边界违反阻断）、审计不含正文、证据状态标注。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bridges.ai import CapabilityRegistry, ModelGateway
from bridges.ai.adapters import AdapterError, AdapterResult, AuthError, RateLimitError
from bridges.ai.errors import ModelRunLockPersistError
from bridges.ai.ports import ModelRunLockRecorder
from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder
from bridges.career.service import (
    CAREER_LOCK_CONVERSATION_OBJECT_TYPE,
    CAREER_LOCK_OBJECT_TYPE,
    CAREER_OPERATION_GENERATION,
    CAREER_OPERATION_REPAIR,
    CareerPlannerService,
)
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    ModelCallStatus,
    RetryPolicy,
)
from bridges.contracts.career import (
    CareerEvidenceKind,
    CareerPlanningProcessState,
    CareerPlanningStatus,
)
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.contracts.workflows import RunContextEnvelope
from bridges.learning import InMemoryLearningRepository, LearningService
from bridges.observability.service import ObservabilityService
from bridges.storage import BridgesDatabase
from bridges.web_search.contracts import (
    WebSearchProjection,
    WebSearchResult,
    WebSearchStatus,
)

NOW = datetime.now(UTC)

_RUN_CONTEXT = RunContextEnvelope(
    run_id="run-career-1",
    account_id="account-1",
    project_id="project-1",
    workflow_name="chat",
    workflow_version="1",
    submitted_at=NOW,
)


def _web_search_projection() -> WebSearchProjection:
    """模拟一次成功的联网搜索（证据集合含 web:0）。"""
    return WebSearchProjection(
        status=WebSearchStatus.SUCCESS,
        trigger_reason="生涯规划涉及行业趋势，需要公开来源核查。",
        query_summary="数据分析 岗位需求",
        results=[
            WebSearchResult(
                result_id="web:0",
                title="行业报告：数据分析岗位需求",
                site="example.com",
                url="https://example.com/report",
                snippet="近三年数据分析相关岗位需求持续增长。",
                accessed_at=NOW,
            )
        ],
        searched_at=NOW,
    )

def _structured_capability() -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_structured_output",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id="qwen3.6-flash",
        input_schema_version="structured-messages-v1",
        output_schema_version="json-schema-v1",
        status=CapabilityStatus.VERIFIED,
        retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=0.01),
    )


class _ProgrammableStructuredAdapter:
    """可编程结构化适配器（生涯规划编排使用）。

    ``outputs`` 非空时按调用顺序依次返回；耗尽后回退 ``output``。
    ``errors`` 为一次性异常队列（按调用顺序消费，None 表示该次成功）；
    ``error`` 与 ``error_from_call`` 配合表示从第 N 次调用起每次抛错
    （覆盖网关内部重试，如修复调用持续限流）。
    """

    def __init__(
        self,
        output: dict[str, Any] | None = None,
        error: Exception | None = None,
        outputs: list[dict[str, Any]] | None = None,
        errors: list[Exception | None] | None = None,
        error_from_call: int | None = None,
    ) -> None:
        self._output = output
        self._error = error
        self._outputs = list(outputs or [])
        self._errors = list(errors or [])
        self._error_from_call = error_from_call
        self.last_payload: dict[str, Any] | None = None
        self.call_count = 0

    def call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        self.last_payload = payload
        self.call_count += 1
        if self._errors:
            raised = self._errors.pop(0)
            if raised is not None:
                raise raised
        if self._error is not None and (
            self._error_from_call is None
            or self.call_count >= self._error_from_call
        ):
            raise self._error
        output = self._outputs.pop(0) if self._outputs else self._output
        return AdapterResult(
            actual_model_id=capability.model_id,
            output=output or {},
        )


def _gateway_with(adapter: _ProgrammableStructuredAdapter) -> ModelGateway:
    registry = CapabilityRegistry()
    registry.register(_structured_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_structured_output", "1", adapter)
    return gateway


def _good_output() -> dict[str, Any]:
    return {
        "final_text": "综合你的阶段与目标，数据分析是值得考虑的方向。",
        "facts": [
            {
                "content": "数据分析相关岗位需求在近三年持续增长。",
                "evidence_refs": ["web:0"],
                "note": "来源：行业报告。",
            }
        ],
        "assumptions": [
            {
                "content": "你可能适合偏业务的数据分析岗。",
                "evidence_refs": [],
                "note": None,
                "verification_next_step": "与从业者交流或做一次实习验证。",
            }
        ],
        "options": [
            {
                "content": "数据分析方向（业务侧）。",
                "evidence_refs": ["web:0"],
                "note": None,
                "rationale": "与你的兴趣与课程背景匹配。",
            }
        ],
        "risks": [
            {
                "content": "岗位竞争加剧。",
                "evidence_refs": ["web:0"],
                "note": None,
                "trigger": "应届求职季人数增加。",
            }
        ],
        "path": [
            {
                "content": "先补齐统计与 SQL 基础。",
                "evidence_refs": ["learning:mission-1"],
                "note": None,
                "timeline": "第 1-3 个月",
            }
        ],
        "suggestions": [
            {
                "content": "完成一个端到端数据分析小项目。",
                "evidence_refs": ["learning:state:state-1"],
                "note": None,
                "verification": "项目上线后可验证兴趣与能力。",
            }
        ],
        "boundary_statement": "本规划不构成就业、薪酬或录取保证，也不替代持证职业顾问。",
        "open_questions": ["行业报告的统计口径未完全披露，建议进一步核查。"],
    }


def _service(
    adapter: _ProgrammableStructuredAdapter,
) -> tuple[CareerPlannerService, ObservabilityService]:
    observability = ObservabilityService()
    learning_repository = InMemoryLearningRepository()
    learning_service = LearningService(repository=learning_repository)
    service = CareerPlannerService(
        gateway=_gateway_with(adapter),
        learning_service=learning_service,
        observability_service=observability,
    )
    return service, observability


def _run(service: CareerPlannerService, **kwargs: Any) -> list[Any]:
    events = list(
        service.run_task(
            "account-1",
            "conv-1",
            "assistant-1",
            "生涯规划：数据分析方向怎么安排",
            mode="companion",
            run_context=_RUN_CONTEXT,
            profile_enabled=kwargs.get("profile_enabled", True),
            profile_used=kwargs.get("profile_used", False),
            profile_items=kwargs.get("profile_items", []),
            retrieval_round=kwargs.get("retrieval_round"),
            web_search_projection=kwargs.get(
                "web_search_projection", _web_search_projection()
            ),
            arxiv_search_projection=kwargs.get("arxiv_search_projection"),
            budget=kwargs.get("budget"),
        )
    )
    return events


def test_run_task_yields_six_section_contract_and_done_projection() -> None:
    adapter = _ProgrammableStructuredAdapter(output=_good_output())
    service, _ = _service(adapter)
    events = _run(service)
    process_events = [event for event in events if event.kind == "process"]
    assert process_events, "必须产出过程事件（五态过程卡）"
    assert process_events[0].state == CareerPlanningProcessState.LOADING
    result_events = [event for event in events if event.kind == "result"]
    assert len(result_events) == 1
    projection = result_events[0].result
    assert projection is not None
    assert projection.status == CareerPlanningStatus.DONE
    assert projection.process_state == CareerPlanningProcessState.DONE
    output = projection.output
    assert output is not None
    assert output.final_text
    assert len(output.facts) == 1
    assert len(output.assumptions) == 1
    assert len(output.options) == 1
    assert len(output.risks) == 1
    assert len(output.path) == 1
    assert len(output.suggestions) == 1
    assert output.boundary_statement
    assert output.open_questions
    # 每条带稳定条目标识（供逐项反馈）与核查时间
    assert output.facts[0].item_id == "fact:1"
    assert output.facts[0].verified_at is not None
    # 证据含画像/学习/联网类别
    kinds = {source.kind for source in projection.evidence_sources}
    assert CareerEvidenceKind.WEB_SEARCH in kinds
    assert CareerEvidenceKind.USER_STATEMENT in kinds


def test_learning_records_enter_evidence() -> None:
    adapter = _ProgrammableStructuredAdapter(output=_good_output())
    observability = ObservabilityService()
    learning_repository = InMemoryLearningRepository()
    learning_service = LearningService(repository=learning_repository)
    from bridges.contracts.learning import LearningMissionCreateRequest

    learning_service.create_mission(
        account_id="account-1",
        request=LearningMissionCreateRequest(
            title="数据分析学习",
            goal="掌握 SQL 与统计基础，完成一个端到端项目。",
            scope_concepts=["SQL", "统计"],
            constraints=[],
            success_criteria=["项目上线"],
        ),
    )
    service = CareerPlannerService(
        gateway=_gateway_with(adapter),
        learning_service=learning_service,
        observability_service=observability,
    )
    events = _run(service)
    projection = events[-1].result
    assert projection is not None
    learning_sources = [
        source for source in projection.evidence_sources
        if source.kind == CareerEvidenceKind.LEARNING_RECORD
    ]
    assert learning_sources
    assert any("数据分析学习" in source.title for source in learning_sources)
    assert all(source.accessed_at is not None for source in learning_sources)


def test_model_failure_is_retryable_recovery() -> None:
    adapter = _ProgrammableStructuredAdapter(error=RateLimitError("slow"))
    service, _ = _service(adapter)
    events = _run(service)
    result_events = [event for event in events if event.kind == "result"]
    projection = result_events[0].result
    assert projection is not None
    assert projection.status == CareerPlanningStatus.ERROR
    assert projection.process_state == CareerPlanningProcessState.RECOVERY
    assert projection.error_code is not None
    assert "重试" in (projection.error_message or "")
    assert projection.output is None  # 不输出模板化假成功


def test_permission_error_maps_to_permission_state() -> None:
    adapter = _ProgrammableStructuredAdapter(error=AuthError("密钥无效"))
    service, _ = _service(adapter)
    events = _run(service)
    projection = events[-1].result
    assert projection is not None
    assert projection.process_state == CareerPlanningProcessState.PERMISSION


def test_boundary_violation_blocks_delivery() -> None:
    violating = _good_output()
    violating["final_text"] = "选这条路，包就业。"
    adapter = _ProgrammableStructuredAdapter(output=violating)
    service, _ = _service(adapter)
    events = _run(service)
    projection = events[-1].result
    assert projection is not None
    assert projection.status == CareerPlanningStatus.ERROR
    assert projection.error_code == "career_boundary_violation"
    assert "就业/薪酬/录取" in (projection.error_message or "")
    assert "承诺" in (projection.error_message or "")
    assert projection.output is None


def test_stale_profile_assertion_marks_evidence_outdated() -> None:
    """旧画像记录（超过新鲜度阈值未更新）→ 证据标记过时，复核可达。"""
    from datetime import timedelta

    from bridges.contracts.profiles import (
        ManualAssertionCreateRequest,
        ProfileDimension,
        ProfileSensitivityClass,
    )
    from bridges.profiles import InMemoryProfileRepository
    from bridges.profiles.service import ProfileService

    profile_repository = InMemoryProfileRepository()
    profile_service = ProfileService(repository=profile_repository)
    assertion = profile_service.manual_create_assertion(
        account_id="account-1",
        request=ManualAssertionCreateRequest(
            dimension=ProfileDimension.INTEREST_PREFERENCE,
            value_or_rule="两年前确认喜欢数据分析。",
            applicable_scenes=["companion", "study"],
            sensitivity_class=ProfileSensitivityClass.PREFERENCE,
            authorization_scope="general",
            source_note="测试播种",
        ),
    )
    # 把断言更新时间回拨到 200 天前（真实旧记录）
    old = profile_repository.get_assertion("account-1", assertion.assertion_id)
    assert old is not None
    old.updated_at = old.updated_at - timedelta(days=200)
    profile_repository.save_assertion(old)

    output = _good_output()
    output["facts"] = [
        {
            "content": "数据分析相关岗位需求在近三年持续增长。",
            "evidence_refs": [f"profile:{assertion.assertion_id}"],
            "note": None,
        }
    ]
    adapter = _ProgrammableStructuredAdapter(output=output)
    observability = ObservabilityService()
    service = CareerPlannerService(
        gateway=_gateway_with(adapter),
        profile_service=profile_service,
        observability_service=observability,
    )
    events = _run(
        service,
        profile_items=[
            type(
                "Item",
                (),
                {
                    "assertion_id": assertion.assertion_id,
                    "dimension": "interest_preference",
                    "value_summary": "喜欢数据分析。",
                    "version": 1,
                },
            )()
        ],
    )
    projection = events[-1].result
    assert projection is not None
    profile_sources = [
        source for source in projection.evidence_sources
        if source.kind == CareerEvidenceKind.PROFILE_SLICE
    ]
    assert profile_sources
    assert profile_sources[0].stale is True
    # 被引用的条目复核为过时
    review = projection.review
    assert review is not None
    assert any(item.state.value == "outdated" for item in review.reviews)


def test_empty_evidence_state_is_legal() -> None:
    """无画像/学习记录/检索/联网时过程卡进入 empty 态，回答仍基于用户陈述。"""
    adapter = _ProgrammableStructuredAdapter(output=_good_output())
    service, _ = _service(adapter)
    events = _run(
        service,
        profile_enabled=False,
        profile_used=False,
        web_search_projection=None,
    )
    process_events = [event for event in events if event.kind == "process"]
    assert any(event.state == CareerPlanningProcessState.EMPTY for event in process_events)
    projection = events[-1].result
    assert projection is not None
    assert projection.status == CareerPlanningStatus.DONE
    assert projection.profile_used is False


def test_audit_records_summary_without_body() -> None:
    adapter = _ProgrammableStructuredAdapter(output=_good_output())
    service, observability = _service(adapter)
    _run(service)
    events = observability.list_audit_events(
        account_id="account-1", action=AuditAction.CAREER_PLANNING_GENERATED
    )
    assert len(events) == 1
    audit = events[0]
    assert audit.result == AuditResult.SUCCESS
    details = audit.details
    assert details["item_counts"]["facts"] == 1
    assert details["evidence_count"] > 0
    assert "profile_used" in details
    # 审计不含正文：任何字段不得包含输出文本
    serialized = str(details)
    assert "数据分析" not in serialized
    assert "包就业" not in serialized


# ----------------------------------------------------------------------
# Issue 12：真实 Qwen 生成与修复审计闭环（统一 recorder 接线）
# ----------------------------------------------------------------------


def _service_with_recorder(
    adapter: _ProgrammableStructuredAdapter,
    database: BridgesDatabase,
) -> tuple[CareerPlannerService, ObservabilityService]:
    """构造注入 Issue 10 统一 recorder 的生涯服务（临时 SQLite）。"""
    observability = ObservabilityService()
    learning_repository = InMemoryLearningRepository()
    learning_service = LearningService(repository=learning_repository)
    service = CareerPlannerService(
        gateway=_gateway_with(adapter),
        learning_service=learning_service,
        observability_service=observability,
        run_lock_recorder=SqliteModelRunLockRecorder(database),
    )
    return service, observability


def _invalid_output() -> dict[str, Any]:
    """结构非法输出：final_text 缺失（可触发一次有界修复）。"""
    invalid = _good_output()
    invalid["final_text"] = ""
    return invalid


def test_generation_records_exactly_one_lock_with_associations(
    tmp_path: Path,
) -> None:
    """正常一次生成恰好一条 career_generation:1 锁，关联账户/会话/助手
    消息/业务 run/固定模型/阶段/序号；投影保留轻量引用。"""
    from bridges.storage import BridgesDatabase

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    adapter = _ProgrammableStructuredAdapter(output=_good_output())
    service, _ = _service_with_recorder(adapter, database)
    events = _run(service)
    assert events[-1].result is not None
    assert events[-1].result.status == CareerPlanningStatus.DONE

    recorder = SqliteModelRunLockRecorder(database)
    locks = recorder.list_locks_by_business_ref(
        "account-1", CAREER_LOCK_OBJECT_TYPE, "assistant-1"
    )
    assert len(locks) == 1, "正常生成必须恰好写入一条锁"
    lock = locks[0]
    assert lock.account_id == "account-1"
    assert lock.run_id == "run-career-1"
    assert lock.project_id == "project-1"
    assert lock.capability_name == "qwen_structured_output"
    assert lock.capability_version == "1"
    assert lock.actual_model_id == "qwen3.6-flash"
    assert lock.status == ModelCallStatus.SUCCESS
    plan_refs = [
        ref
        for ref in lock.business_refs
        if ref.object_type == CAREER_LOCK_OBJECT_TYPE and ref.object_id == "assistant-1"
    ]
    assert len(plan_refs) == 1
    assert plan_refs[0].operation == CAREER_OPERATION_GENERATION
    assert plan_refs[0].attempt_ordinal == 1
    assert plan_refs[0].is_primary is True
    # 会话关联：锁同时链接到 conversation 业务对象
    conv_refs = [
        ref
        for ref in lock.business_refs
        if ref.object_type == CAREER_LOCK_CONVERSATION_OBJECT_TYPE and ref.object_id == "conv-1"
    ]
    assert len(conv_refs) == 1
    assert conv_refs[0].operation == CAREER_OPERATION_GENERATION

    # 投影保留主要锁引用与业务 run，可定位同 run 完整集合
    projection = events[-1].result
    assert projection.run_id == "run-career-1"
    assert len(projection.run_lock_refs) == 1
    ref = projection.run_lock_refs[0]
    assert ref.lock_id == lock.lock_id
    assert ref.operation == CAREER_OPERATION_GENERATION
    assert ref.attempt_ordinal == 1


def test_invalid_structure_then_repair_records_two_locks_in_order(
    tmp_path: Path,
) -> None:
    """首次结构无效且修复成功：恰好两条不同锁，顺序 generation → repair；
    重启后两条均可查，投影可定位完整集合。"""
    from bridges.storage import BridgesDatabase

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    adapter = _ProgrammableStructuredAdapter(
        outputs=[_invalid_output(), _good_output()]
    )
    service, _ = _service_with_recorder(adapter, database)
    events = _run(service)
    assert events[-1].result is not None
    assert events[-1].result.status == CareerPlanningStatus.DONE
    assert adapter.call_count == 2
    # 修复调用携带修复要求（第二次用户提示含【修复要求】）
    assert adapter.last_payload is not None
    assert "【修复要求】" in str(adapter.last_payload.get("messages", []))

    recorder = SqliteModelRunLockRecorder(database)
    locks = recorder.list_locks_by_business_ref(
        "account-1", CAREER_LOCK_OBJECT_TYPE, "assistant-1"
    )
    assert len(locks) == 2, "首次无效 + 修复成功必须恰好两条锁"
    first = next(
        ref
        for lock in locks
        for ref in lock.business_refs
        if ref.object_type == CAREER_LOCK_OBJECT_TYPE and ref.attempt_ordinal == 1
    )
    second = next(
        ref
        for lock in locks
        for ref in lock.business_refs
        if ref.object_type == CAREER_LOCK_OBJECT_TYPE and ref.attempt_ordinal == 2
    )
    assert first.operation == CAREER_OPERATION_GENERATION
    assert first.attempt_ordinal == 1
    assert second.operation == CAREER_OPERATION_REPAIR
    assert second.attempt_ordinal == 2
    assert first.is_primary is True
    assert second.is_primary is False
    assert all(lock.status == ModelCallStatus.SUCCESS for lock in locks)

    # 按 run 查询两条锁且按序号排序（重启后同一 recorder 可查）
    by_run = recorder.list_locks_by_run("account-1", "run-career-1")
    assert len(by_run) == 2
    ordinals = [
        ref.attempt_ordinal
        for lock in by_run
        for ref in lock.business_refs
        if ref.object_type == CAREER_LOCK_OBJECT_TYPE
    ]
    assert ordinals == [1, 2], "锁集合必须按调用序号稳定排序"

    # 投影引用完整集合（重启后定位依据）
    projection = events[-1].result
    assert [ref.attempt_ordinal for ref in projection.run_lock_refs] == [1, 2]
    assert projection.run_lock_refs[0].operation == CAREER_OPERATION_GENERATION
    assert projection.run_lock_refs[1].operation == CAREER_OPERATION_REPAIR


def test_parse_failure_then_repair_records_two_locks(tmp_path: Path) -> None:
    """格式类失败（structured_output_parse_failed）触发修复：首次调用锁
    如实记录 BLOCKED + 错误码，修复成功锁为 SUCCESS。"""
    from bridges.storage import BridgesDatabase

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    adapter = _ProgrammableStructuredAdapter(
        errors=[
            AdapterError(
                code="structured_output_parse_failed",
                message="Model output was not valid JSON",
            ),
            None,
        ],
        output=_good_output(),
    )
    service, _ = _service_with_recorder(adapter, database)
    events = _run(service)
    assert events[-1].result is not None
    assert events[-1].result.status == CareerPlanningStatus.DONE

    recorder = SqliteModelRunLockRecorder(database)
    locks = recorder.list_locks_by_business_ref(
        "account-1", CAREER_LOCK_OBJECT_TYPE, "assistant-1"
    )
    assert len(locks) == 2
    by_ordinal = {
        ref.attempt_ordinal: lock
        for lock in locks
        for ref in lock.business_refs
        if ref.object_type == CAREER_LOCK_OBJECT_TYPE
    }
    first = by_ordinal[1]
    second = by_ordinal[2]
    assert first.status == ModelCallStatus.BLOCKED
    assert first.error_code == "structured_output_parse_failed"
    assert second.status == ModelCallStatus.SUCCESS


def test_budget_insufficient_records_only_generation_lock(
    tmp_path: Path,
) -> None:
    """首次结构无效但预算不足：只有一条 generation 锁，不伪造 repair 锁，
    现有可重试中文错误保持不变。"""
    from bridges.chat.budget import RunBudget
    from bridges.storage import BridgesDatabase

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    adapter = _ProgrammableStructuredAdapter(
        outputs=[_invalid_output(), _good_output()]
    )
    service, _ = _service_with_recorder(adapter, database)
    budget = RunBudget(run_id="run-career-1")
    budget.mark_exhausted()
    events = _run(service, budget=budget)
    projection = events[-1].result
    assert projection is not None
    assert projection.status == CareerPlanningStatus.ERROR
    assert projection.error_code == "career_output_invalid"
    assert "预算不足" in (projection.error_message or "")
    assert adapter.call_count == 1, "预算不足不得发起修复调用"

    recorder = SqliteModelRunLockRecorder(database)
    locks = recorder.list_locks_by_business_ref(
        "account-1", CAREER_LOCK_OBJECT_TYPE, "assistant-1"
    )
    assert len(locks) == 1
    refs = [
        ref
        for lock in locks
        for ref in lock.business_refs
        if ref.object_type == CAREER_LOCK_OBJECT_TYPE
    ]
    assert refs[0].operation == CAREER_OPERATION_GENERATION
    assert refs[0].attempt_ordinal == 1
    assert locks[0].status == ModelCallStatus.SUCCESS, (
        "模型调用成功但业务结构无效：锁仍如实记录调用成功"
    )


def test_first_call_failure_records_failed_lock(tmp_path: Path) -> None:
    """首次调用限流失败：每个实际发起的供应商调用都有状态锁（含网关
    内部重试后的最终结果），业务终态不覆盖模型运行状态。"""
    from bridges.storage import BridgesDatabase

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    adapter = _ProgrammableStructuredAdapter(error=RateLimitError("slow"))
    service, _ = _service_with_recorder(adapter, database)
    events = _run(service)
    projection = events[-1].result
    assert projection is not None
    assert projection.status == CareerPlanningStatus.ERROR
    assert projection.process_state == CareerPlanningProcessState.RECOVERY

    recorder = SqliteModelRunLockRecorder(database)
    locks = recorder.list_locks_by_business_ref(
        "account-1", CAREER_LOCK_OBJECT_TYPE, "assistant-1"
    )
    assert len(locks) == 1
    assert locks[0].status == ModelCallStatus.RETRYABLE_FAIL
    assert locks[0].error_code == "rate_limit"
    refs = [
        ref
        for ref in locks[0].business_refs
        if ref.object_type == CAREER_LOCK_OBJECT_TYPE
    ]
    assert refs[0].operation == CAREER_OPERATION_GENERATION
    assert refs[0].attempt_ordinal == 1


def test_repair_failure_records_two_locks_with_statuses(tmp_path: Path) -> None:
    """首次结构无效 + 修复调用限流失败：两条锁分别如实记录 SUCCESS 与
    RETRYABLE_FAIL，投影为可重试错误。"""
    from bridges.storage import BridgesDatabase

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    adapter = _ProgrammableStructuredAdapter(
        outputs=[_invalid_output()],
        output=_good_output(),
        error=RateLimitError("slow"),
        error_from_call=2,
    )
    service, _ = _service_with_recorder(adapter, database)
    events = _run(service)
    projection = events[-1].result
    assert projection is not None
    assert projection.status == CareerPlanningStatus.ERROR
    assert projection.error_code == "rate_limit"
    assert projection.process_state == CareerPlanningProcessState.RECOVERY

    recorder = SqliteModelRunLockRecorder(database)
    locks = recorder.list_locks_by_business_ref(
        "account-1", CAREER_LOCK_OBJECT_TYPE, "assistant-1"
    )
    assert len(locks) == 2
    by_ordinal = {
        ref.attempt_ordinal: lock
        for lock in locks
        for ref in lock.business_refs
        if ref.object_type == CAREER_LOCK_OBJECT_TYPE
    }
    assert by_ordinal[1].status == ModelCallStatus.SUCCESS
    assert by_ordinal[1].error_code is None
    assert by_ordinal[2].status == ModelCallStatus.RETRYABLE_FAIL
    assert by_ordinal[2].error_code == "rate_limit"
    # 失败投影仍保留已发生调用的锁引用
    assert [ref.attempt_ordinal for ref in projection.run_lock_refs] == [1, 2]


def test_auth_failure_records_blocked_lock(tmp_path: Path) -> None:
    """鉴权失败：已经发起的供应商调用有 BLOCKED 状态锁（auth_error），
    业务终态 permission 不覆盖模型运行状态。"""
    from bridges.storage import BridgesDatabase

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    adapter = _ProgrammableStructuredAdapter(error=AuthError("密钥无效"))
    service, _ = _service_with_recorder(adapter, database)
    events = _run(service)
    projection = events[-1].result
    assert projection is not None
    assert projection.process_state == CareerPlanningProcessState.PERMISSION

    recorder = SqliteModelRunLockRecorder(database)
    locks = recorder.list_locks_by_business_ref(
        "account-1", CAREER_LOCK_OBJECT_TYPE, "assistant-1"
    )
    assert len(locks) == 1
    assert locks[0].status == ModelCallStatus.BLOCKED
    assert locks[0].error_code == "auth_error"
    assert locks[0].retry_count == 0
    refs = [
        ref
        for ref in locks[0].business_refs
        if ref.object_type == CAREER_LOCK_OBJECT_TYPE
    ]
    assert refs[0].operation == CAREER_OPERATION_GENERATION
    assert refs[0].attempt_ordinal == 1


class _FailingRecorder(ModelRunLockRecorder):
    """持久化必失败的 recorder：验证失败关闭（Issue 10 合同）。"""

    def __init__(self) -> None:
        self.record_many_calls = 0

    def record(self, lock: Any, *, business_ref: Any) -> Any:
        raise ModelRunLockPersistError("boom")

    def record_many(self, requests: list[Any]) -> list[Any]:
        self.record_many_calls += 1
        raise ModelRunLockPersistError("boom")

    def get_lock(self, lock_id: str, account_id: str) -> Any:
        return None

    def list_locks_by_run(self, account_id: str, run_id: str) -> list[Any]:
        return []

    def list_locks_by_business_ref(
        self, account_id: str, object_type: str, object_id: str
    ) -> list[Any]:
        return []


def test_recorder_persist_failure_fails_closed(tmp_path: Path) -> None:
    """注入 recorder 持久化失败：不得把无可持久审计证据的模型结果提升为
    Career 完成态（career_lock_persist_failed 失败关闭）。"""
    from bridges.storage import BridgesDatabase

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    adapter = _ProgrammableStructuredAdapter(output=_good_output())
    failing = _FailingRecorder()
    observability = ObservabilityService()
    learning_repository = InMemoryLearningRepository()
    learning_service = LearningService(repository=learning_repository)
    service = CareerPlannerService(
        gateway=_gateway_with(adapter),
        learning_service=learning_service,
        observability_service=observability,
        run_lock_recorder=failing,
    )
    events = _run(service)
    projection = events[-1].result
    assert projection is not None
    assert projection.status == CareerPlanningStatus.ERROR
    assert projection.error_code == "career_lock_persist_failed"
    assert projection.output is None
    assert projection.run_lock_refs == [], "持久化失败不得产生锁引用"
    assert failing.record_many_calls == 1


class _CountingRecorder(ModelRunLockRecorder):
    """统计 record/record_many 调用的 spy（验证本地步骤不新增模型锁）。"""

    def __init__(self, inner: SqliteModelRunLockRecorder) -> None:
        self._inner = inner
        self.record_calls = 0
        self.record_many_calls = 0

    def record(self, lock: Any, *, business_ref: Any) -> Any:
        self.record_calls += 1
        return self._inner.record(lock, business_ref=business_ref)

    def record_many(self, requests: list[Any]) -> list[Any]:
        self.record_many_calls += 1
        return self._inner.record_many(requests)

    def get_lock(self, lock_id: str, account_id: str) -> Any:
        return self._inner.get_lock(lock_id, account_id)

    def list_locks_by_run(self, account_id: str, run_id: str) -> list[Any]:
        return list(self._inner.list_locks_by_run(account_id, run_id))

    def list_locks_by_business_ref(
        self, account_id: str, object_type: str, object_id: str
    ) -> list[Any]:
        return list(
            self._inner.list_locks_by_business_ref(account_id, object_type, object_id)
        )


def test_local_steps_do_not_create_extra_model_locks(tmp_path: Path) -> None:
    """路由判定、证据组装、宽容解析、本地复核与投影构造不新增模型锁：
    正常 run 恰好 1 次 record_many（generation），修复 run 恰好 2 次
    （generation + repair），无任何 record() 单条路径。"""
    from bridges.storage import BridgesDatabase

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()

    # 正常 run：恰好 1 次锁持久化
    adapter = _ProgrammableStructuredAdapter(output=_good_output())
    spy = _CountingRecorder(SqliteModelRunLockRecorder(database))
    observability = ObservabilityService()
    learning_service = LearningService(repository=InMemoryLearningRepository())
    service = CareerPlannerService(
        gateway=_gateway_with(adapter),
        learning_service=learning_service,
        observability_service=observability,
        run_lock_recorder=spy,
    )
    events = _run(service)
    assert events[-1].result is not None
    assert events[-1].result.status == CareerPlanningStatus.DONE
    assert adapter.call_count == 1
    assert spy.record_many_calls == 1
    assert spy.record_calls == 0

    # 修复 run：恰好 2 次锁持久化
    adapter2 = _ProgrammableStructuredAdapter(
        outputs=[_invalid_output(), _good_output()]
    )
    spy2 = _CountingRecorder(SqliteModelRunLockRecorder(database))
    service2 = CareerPlannerService(
        gateway=_gateway_with(adapter2),
        learning_service=learning_service,
        observability_service=observability,
        run_lock_recorder=spy2,
    )
    events2 = _run(service2)
    assert events2[-1].result is not None
    assert events2[-1].result.status == CareerPlanningStatus.DONE
    assert adapter2.call_count == 2
    assert spy2.record_many_calls == 2
    assert spy2.record_calls == 0


def test_boundary_violation_keeps_success_lock(tmp_path: Path) -> None:
    """供应商返回成功但输出违反承诺词边界：模型锁仍准确记录调用成功，
    领域审计另行记录 career_boundary_violation，不能篡改模型锁状态。"""
    from bridges.storage import BridgesDatabase

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    violating = _good_output()
    violating["final_text"] = "选这条路，包就业。"
    adapter = _ProgrammableStructuredAdapter(output=violating)
    service, _ = _service_with_recorder(adapter, database)
    events = _run(service)
    projection = events[-1].result
    assert projection is not None
    assert projection.status == CareerPlanningStatus.ERROR
    assert projection.error_code == "career_boundary_violation"

    recorder = SqliteModelRunLockRecorder(database)
    locks = recorder.list_locks_by_business_ref(
        "account-1", CAREER_LOCK_OBJECT_TYPE, "assistant-1"
    )
    assert len(locks) == 1
    assert locks[0].status == ModelCallStatus.SUCCESS
    assert locks[0].error_code is None, "模型锁不得表达业务复核结果"


def test_audit_details_include_lock_refs_without_body(tmp_path: Path) -> None:
    """审计 details 携带 run 与锁引用（ID/阶段/序号），但不含提示词、
    规划正文或用户内容。"""
    from bridges.storage import BridgesDatabase

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    adapter = _ProgrammableStructuredAdapter(output=_good_output())
    service, observability = _service_with_recorder(adapter, database)
    _run(service)
    events = observability.list_audit_events(
        account_id="account-1", action=AuditAction.CAREER_PLANNING_GENERATED
    )
    assert len(events) == 1
    details = events[0].details
    assert details["run_id"] == "run-career-1"
    lock_refs = details["lock_refs"]
    assert len(lock_refs) == 1
    assert lock_refs[0]["operation"] == CAREER_OPERATION_GENERATION
    assert lock_refs[0]["attempt_ordinal"] == 1
    assert lock_refs[0]["lock_id"]
    assert details["repair_triggered"] is False
    serialized = str(details)
    assert "数据分析" not in serialized
    assert "包就业" not in serialized
    assert "【修复要求】" not in serialized


def test_audit_records_repair_triggered_on_successful_repair(
    tmp_path: Path,
) -> None:
    """首次结构无效且修复成功：领域审计显式记录 repair 触发（AC6），
    终态仍为 SUCCESS 且不含正文。"""
    from bridges.storage import BridgesDatabase

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    adapter = _ProgrammableStructuredAdapter(
        outputs=[_invalid_output(), _good_output()]
    )
    service, observability = _service_with_recorder(adapter, database)
    events = _run(service)
    assert events[-1].result is not None
    assert events[-1].result.status == CareerPlanningStatus.DONE
    audit_events = observability.list_audit_events(
        account_id="account-1", action=AuditAction.CAREER_PLANNING_GENERATED
    )
    assert len(audit_events) == 1
    details = audit_events[0].details
    assert details["repair_triggered"] is True
    assert details["error_code"] is None, "修复成功后业务终态不携带错误码"
    assert len(details["lock_refs"]) == 2
    assert "【修复要求】" not in str(details)


def test_lock_metrics_aggregate_calls_by_stage_and_status(
    tmp_path: Path,
) -> None:
    """Observability：按阶段/模型状态聚合调用数与延迟/usage；
    缺锁、持久化失败与序号异常有独立稳定计数。"""
    from bridges.career.metrics import InMemoryCareerLockMetrics
    from bridges.storage import BridgesDatabase

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    metrics = InMemoryCareerLockMetrics()
    adapter = _ProgrammableStructuredAdapter(
        outputs=[_invalid_output(), _good_output()]
    )
    observability = ObservabilityService()
    learning_service = LearningService(repository=InMemoryLearningRepository())
    service = CareerPlannerService(
        gateway=_gateway_with(adapter),
        learning_service=learning_service,
        observability_service=observability,
        run_lock_recorder=SqliteModelRunLockRecorder(database),
        lock_metrics=metrics,
    )
    events = _run(service)
    assert events[-1].result is not None
    assert events[-1].result.status == CareerPlanningStatus.DONE

    snapshot = metrics.snapshot()
    assert snapshot.get(
        "career_lock_call_total:career_generation:success"
    ) == 1, "首次生成调用按阶段+状态聚合"
    assert snapshot.get(
        "career_lock_call_total:career_repair:success"
    ) == 1, "真实修复调用单独成档"
    assert metrics.call_count() == 2
    assert metrics.last_duration_ms() is not None
    assert metrics.last_usage() is None or isinstance(metrics.last_usage(), dict)
    # 缺锁/持久化失败/序号异常计数在正常路径为零
    assert "career_lock_missing_total" not in snapshot
    assert "career_lock_persist_failed_total" not in snapshot
    assert "career_call_sequence_mismatch_total" not in snapshot


def test_lock_metrics_count_failure_paths(tmp_path: Path) -> None:
    """持久化失败与鉴权失败分别产生稳定计数与状态聚合。"""
    from bridges.career.metrics import InMemoryCareerLockMetrics
    from bridges.storage import BridgesDatabase

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()

    # 持久化失败 → career_lock_persist_failed_total
    metrics_persist = InMemoryCareerLockMetrics()
    adapter = _ProgrammableStructuredAdapter(output=_good_output())
    service = CareerPlannerService(
        gateway=_gateway_with(adapter),
        learning_service=LearningService(repository=InMemoryLearningRepository()),
        observability_service=ObservabilityService(),
        run_lock_recorder=_FailingRecorder(),
        lock_metrics=metrics_persist,
    )
    events = _run(service)
    assert events[-1].result is not None
    assert events[-1].result.error_code == "career_lock_persist_failed"
    assert metrics_persist.snapshot().get(
        "career_lock_persist_failed_total"
    ) == 1
    assert metrics_persist.snapshot().get(
        "career_lock_call_total:career_generation:success"
    ) == 1, "调用已真实发生，即使持久化失败也计入调用数"

    # 鉴权失败 → 按 blocked 状态聚合
    metrics_auth = InMemoryCareerLockMetrics()
    adapter2 = _ProgrammableStructuredAdapter(error=AuthError("密钥无效"))
    service2 = CareerPlannerService(
        gateway=_gateway_with(adapter2),
        learning_service=LearningService(repository=InMemoryLearningRepository()),
        observability_service=ObservabilityService(),
        run_lock_recorder=SqliteModelRunLockRecorder(database),
        lock_metrics=metrics_auth,
    )
    events2 = _run(service2)
    assert events2[-1].result is not None
    assert events2[-1].result.process_state == CareerPlanningProcessState.PERMISSION
    snapshot2 = metrics_auth.snapshot()
    assert snapshot2.get(
        "career_lock_call_total:career_generation:blocked"
    ) == 1


def test_lock_metrics_count_missing_recorder(tmp_path: Path) -> None:
    """未注入 recorder 的组合（评估/替身）：调用发生后标记
    career_lock_recorder_missing，投影仍携带锁引用（不丢弃）。"""
    from bridges.career.metrics import InMemoryCareerLockMetrics

    metrics = InMemoryCareerLockMetrics()
    adapter = _ProgrammableStructuredAdapter(output=_good_output())
    observability = ObservabilityService()
    service = CareerPlannerService(
        gateway=_gateway_with(adapter),
        learning_service=LearningService(repository=InMemoryLearningRepository()),
        observability_service=observability,
        run_lock_recorder=None,
        lock_metrics=metrics,
    )
    events = _run(service)
    projection = events[-1].result
    assert projection is not None
    assert projection.status == CareerPlanningStatus.DONE
    assert len(projection.run_lock_refs) == 1, "无 recorder 时引用仍保留"
    snapshot = metrics.snapshot()
    assert snapshot.get("career_lock_recorder_missing_total") == 1


def test_lock_metrics_count_missing_lock(tmp_path: Path) -> None:
    """网关缺锁（防御路径）：career_lock_missing_total 计数且失败关闭。"""
    from bridges.career.metrics import InMemoryCareerLockMetrics
    from bridges.contracts.ai import ModelCallResult
    from bridges.storage import BridgesDatabase

    class _NoLockGateway:
        """网关契约的缺锁替身：返回无锁结果（防御路径测试）。"""

        def invoke(self, *args: Any, **kwargs: Any) -> ModelCallResult:
            return ModelCallResult(
                status=ModelCallStatus.SUCCESS,
                lock=None,
                output={},
            )

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    metrics = InMemoryCareerLockMetrics()
    service = CareerPlannerService(
        gateway=_NoLockGateway(),  # type: ignore[arg-type]
        learning_service=LearningService(repository=InMemoryLearningRepository()),
        observability_service=ObservabilityService(),
        run_lock_recorder=SqliteModelRunLockRecorder(database),
        lock_metrics=metrics,
    )
    events = _run(service)
    projection = events[-1].result
    assert projection is not None
    assert projection.error_code == "career_missing_run_lock"
    snapshot = metrics.snapshot()
    assert snapshot.get("career_lock_missing_total") == 1
    assert snapshot.get(
        "career_lock_call_total:career_generation:success"
    ) == 1, "调用本身已发起，仍计入调用数"
