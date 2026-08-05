"""生涯规划编排服务测试（Issue 29）。

验证：六类输出合同生成与投影、学习记录证据编译、失败分类（可重试/
权限/边界违反阻断）、审计不含正文、证据状态标注。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bridges.ai import CapabilityRegistry, ModelGateway
from bridges.ai.adapters import AdapterResult, AuthError, RateLimitError
from bridges.career.service import CareerPlannerService
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
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
    """可编程结构化适配器（生涯规划编排使用）。"""

    def __init__(
        self,
        output: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._output = output
        self._error = error
        self.last_payload: dict[str, Any] | None = None

    def call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        self.last_payload = payload
        if self._error is not None:
            raise self._error
        return AdapterResult(
            actual_model_id=capability.model_id,
            output=self._output or {},
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
