"""Issue 06（第七轮）测试：结构化调用的预算截断、预算不足不重试、
人味化部分交付与真实错误透传。

覆盖验收标准：
- 每次非流式结构化调用的超时按「剩余预算 − 交接预留」截断（单一预算
  常量来源），单次调用不再可能吃光整轮预算；
- 网关重试前检查剩余预算：放不下「退避 + 最小调用窗口 + 交接预留」时
  重试次数为 0、以真实错误终态收尾；
- 首稿完成但修订预算不足/修订调用失败 → 交付带标注首稿（成功部分终态），
  投影与审计如实标记部分交付；
- 模型调用本身失败（上游超时/瞬断）→ 真实错误码透传，不再被
  ``budget_exceeded`` 掩盖；
- 注入式慢适配器场景整轮墙钟在（缩放后的）预算内形成终态。
"""

from __future__ import annotations

import contextlib
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.ai import CapabilityRegistry, ModelGateway
from bridges.ai.adapters import (
    REQUEST_TIMEOUT_SECONDS_KEY,
    AdapterResult,
    TransientError,
)
from bridges.chat.budget import RunBudget
from bridges.config import get_settings
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    ModelCallStatus,
    RetryPolicy,
)
from bridges.contracts.humanizer import (
    ArticleDeliveryStatus,
    HumanizerResultStatus,
)
from bridges.contracts.workflows import ObjectDomain, RunContextEnvelope
from bridges.model_call_budget import (
    MODEL_CALL_DEFAULT_TIMEOUT_SECONDS,
    MODEL_CALL_HANDOFF_RESERVE_SECONDS,
    MODEL_CALL_MIN_TIMEOUT_SECONDS,
    MODEL_CALL_MIN_WINDOW_SECONDS,
    model_call_can_retry,
    model_call_timeout_ms,
)

# ---------------------------------------------------------------------------
# 预算算术（模型调用截断与重试门槛的单一来源）
# ---------------------------------------------------------------------------


def test_model_call_timeout_truncates_by_remaining_minus_reserve() -> None:
    """截断：超时 = min(默认 60s, 剩余预算 − 交接预留)。"""
    reserve_ms = int(MODEL_CALL_HANDOFF_RESERVE_SECONDS * 1000)
    assert model_call_timeout_ms(10_000) == 10_000 - reserve_ms
    assert model_call_timeout_ms(120_000) == 60_000, "预算充足时保持默认 60 秒"


def test_model_call_timeout_never_exceeds_default() -> None:
    """默认 60 秒是上限：任意大剩余预算都不超过它。"""
    assert model_call_timeout_ms(3_600_000) == 60_000
    assert int(MODEL_CALL_DEFAULT_TIMEOUT_SECONDS * 1000) == 60_000


def test_model_call_timeout_keeps_positive_floor_when_budget_exhausted() -> None:
    """剩余预算连交接预留都不够时仍保留正下限（httpx 拒绝非正超时）。"""
    floor = int(MODEL_CALL_MIN_TIMEOUT_SECONDS * 1000)
    assert model_call_timeout_ms(0) == floor
    assert model_call_timeout_ms(-5) == floor
    assert floor > 0


def test_model_call_can_retry_requires_backoff_window_reserve() -> None:
    """重试门：剩余预算必须放得下「退避 + 最小调用窗口 + 交接预留」。"""
    window_ms = int(MODEL_CALL_MIN_WINDOW_SECONDS * 1000)
    reserve_ms = int(MODEL_CALL_HANDOFF_RESERVE_SECONDS * 1000)
    assert model_call_can_retry(window_ms + reserve_ms) is True
    assert model_call_can_retry(window_ms + reserve_ms - 1) is False
    assert model_call_can_retry(0) is False
    assert model_call_can_retry(window_ms + reserve_ms, backoff_ms=2_000) is False
    assert model_call_can_retry(window_ms + reserve_ms + 2_000, backoff_ms=2_000) is True


def test_runbudget_model_call_methods_use_shared_arithmetic() -> None:
    """RunBudget 方法走同一预算常量来源（单一来源，不散落魔法数）。"""
    budget = RunBudget("run-truncate-1")
    assert budget.model_call_timeout_ms() == 60_000
    assert budget.can_retry_model_call() is True
    exhausted = RunBudget("run-truncate-2", total_ms=0)
    assert exhausted.model_call_timeout_ms() == int(
        MODEL_CALL_MIN_TIMEOUT_SECONDS * 1000
    )
    assert exhausted.can_retry_model_call() is False


# ---------------------------------------------------------------------------
# 网关：截断注入与预算不足不重试
# ---------------------------------------------------------------------------


def _structured_capability(
    *,
    max_attempts: int = 1,
    backoff_seconds: float = 0.0,
    jitter: bool = False,
) -> CapabilityRecord:
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
        retry_policy=RetryPolicy(
            max_attempts=max_attempts, backoff_seconds=backoff_seconds, jitter=jitter
        ),
    )


class _RecordingAdapter:
    """可编程适配器：记录每次调用的载荷与截断超时，可注入异常与延迟。

    ``honor_timeout`` 为 True 时按载荷中的 ``request_timeout_seconds``
    休眠（模拟真实 HTTP 客户端超时语义），休眠后抛 TransientError——
    用于验证「单次调用在截断超时内被终止、整轮在预算内形成终态」。
    """

    def __init__(
        self,
        *,
        output: dict[str, Any] | None = None,
        outputs: list[dict[str, Any]] | None = None,
        errors: list[Exception] | None = None,
        error_from_call: int = 1,
        honor_timeout: bool = False,
    ) -> None:
        self._output = output
        self._outputs = list(outputs or [])
        self._errors = list(errors or [])
        self._error_from_call = error_from_call
        self._honor_timeout = honor_timeout
        self.calls = 0
        self.payloads: list[dict[str, Any]] = []
        self.timeouts: list[float | None] = []

    def call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        self.calls += 1
        self.payloads.append(payload)
        timeout = payload.get(REQUEST_TIMEOUT_SECONDS_KEY)
        self.timeouts.append(timeout)
        if self._errors and self.calls >= self._error_from_call:
            error_index = self.calls - self._error_from_call
            if error_index < len(self._errors):
                if self._honor_timeout and timeout is not None:
                    time.sleep(float(timeout) + 0.05)
                raise self._errors[error_index]
        if self._honor_timeout and timeout is not None:
            time.sleep(float(timeout) + 0.05)
        step = (
            self._outputs[self.calls - 1]
            if self.calls <= len(self._outputs)
            else self._output
        )
        return AdapterResult(
            actual_model_id=capability.model_id,
            output=step or {},
        )


def _gateway_with(adapter: Any, *, max_attempts: int = 1, backoff: float = 0.0) -> ModelGateway:
    registry = CapabilityRegistry()
    registry.register(
        _structured_capability(max_attempts=max_attempts, backoff_seconds=backoff)
    )
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_structured_output", "1", adapter)
    return gateway


def _run_context(run_id: str = "run-gw") -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id=run_id,
        account_id="acc-gw",
        project_id="conv-gw",
        workflow_name="chat",
        workflow_version="1",
        object_domain=ObjectDomain.PERSONAL_VAULT,
        submitted_at=datetime.now(UTC),
    )


def test_gateway_injects_truncated_timeout_into_payload() -> None:
    """有预算时每次调用注入截断后超时（剩余 − 预留），并记录进运行锁。"""
    adapter = _RecordingAdapter(output={"final_text": "草稿"})
    gateway = _gateway_with(adapter)
    budget = RunBudget("run-truncate-3", total_ms=30_000)
    result = gateway.invoke(
        "qwen_structured_output", "1", _run_context(), {"messages": []}, budget=budget
    )
    assert result.status == ModelCallStatus.SUCCESS
    assert adapter.calls == 1
    injected = adapter.payloads[0].get(REQUEST_TIMEOUT_SECONDS_KEY)
    assert injected is not None
    assert injected <= 30.0, "截断超时不得超过剩余预算"
    assert injected == pytest.approx(
        min(60.0, 30.0 - MODEL_CALL_HANDOFF_RESERVE_SECONDS), abs=1.0
    )
    # 运行锁参数记录截断后的超时值（审计可区分完整窗口与被截断调用）
    assert result.lock is not None
    assert result.lock.parameters.get(REQUEST_TIMEOUT_SECONDS_KEY) == injected


def test_gateway_without_budget_does_not_inject_timeout() -> None:
    """未传入预算时保持既有行为：不注入截断键。"""
    adapter = _RecordingAdapter(output={"final_text": "草稿"})
    gateway = _gateway_with(adapter)
    result = gateway.invoke(
        "qwen_structured_output", "1", _run_context(), {"messages": []}
    )
    assert result.status == ModelCallStatus.SUCCESS
    assert REQUEST_TIMEOUT_SECONDS_KEY not in adapter.payloads[0]


def test_gateway_budget_exhausted_still_attempts_with_floor_timeout() -> None:
    """预算耗尽仍以正下限真实发起一次调用（让上游超时如实透传）。"""
    adapter = _RecordingAdapter(output={"final_text": "草稿"})
    gateway = _gateway_with(adapter)
    budget = RunBudget("run-truncate-4", total_ms=0)
    result = gateway.invoke(
        "qwen_structured_output", "1", _run_context(), {"messages": []}, budget=budget
    )
    assert result.status == ModelCallStatus.SUCCESS, "首次调用不被网关伪造拒绝"
    assert adapter.calls == 1
    assert adapter.timeouts[0] == pytest.approx(MODEL_CALL_MIN_TIMEOUT_SECONDS)


def test_gateway_retry_suppressed_by_budget_returns_real_error() -> None:
    """预算放不下「退避 + 最小窗口 + 预留」：不重试，真实错误终态、次数 0。"""
    adapter = _RecordingAdapter(
        errors=[TransientError("Qwen request timeout: test")]
    )
    gateway = _gateway_with(adapter, max_attempts=2, backoff=1.0)
    budget = RunBudget("run-truncate-5", total_ms=100)
    started = time.monotonic()
    result = gateway.invoke(
        "qwen_structured_output", "1", _run_context(), {"messages": []}, budget=budget
    )
    elapsed = time.monotonic() - started
    assert result.status == ModelCallStatus.RETRYABLE_FAIL
    assert result.error_code == "transient", "真实错误码透传而非 budget_exceeded"
    assert adapter.calls == 1, "预算不足时网关重试次数为 0"
    assert result.lock is not None and result.lock.retry_count == 0
    # 被截断/失败的调用同样有独立运行锁，且锁参数记录截断后的超时值
    assert result.lock.parameters.get(REQUEST_TIMEOUT_SECONDS_KEY) is not None
    assert elapsed < 1.0, "预算不足时不得再等退避"


def test_gateway_retries_within_budget_and_counts_attempts() -> None:
    """预算充足：首次瞬断后按既有语义重试一次并成功（retry_count=1）。"""
    adapter = _RecordingAdapter(
        output={"final_text": "草稿"},
        errors=[TransientError("Qwen transient: test")],
    )
    gateway = _gateway_with(adapter, max_attempts=2, backoff=0.01)
    budget = RunBudget("run-truncate-6", total_ms=60_000)
    result = gateway.invoke(
        "qwen_structured_output", "1", _run_context(), {"messages": []}, budget=budget
    )
    assert result.status == ModelCallStatus.SUCCESS
    assert adapter.calls == 2
    assert result.lock is not None and result.lock.retry_count == 1


def test_gateway_retry_exhaustion_returns_real_error() -> None:
    """重试全部失败：真实错误终态（max_attempts 耗尽，非预算抑制）。"""
    adapter = _RecordingAdapter(
        errors=[TransientError("Qwen transient: 1"), TransientError("Qwen transient: 2")]
    )
    gateway = _gateway_with(adapter, max_attempts=2, backoff=0.01)
    budget = RunBudget("run-truncate-7", total_ms=60_000)
    result = gateway.invoke(
        "qwen_structured_output", "1", _run_context(), {"messages": []}, budget=budget
    )
    assert result.status == ModelCallStatus.RETRYABLE_FAIL
    assert result.error_code == "transient"
    assert adapter.calls == 2
    assert result.lock is not None and result.lock.retry_count == 1


def test_gateway_rate_limit_also_passes_through_real_code() -> None:
    """限流同样以真实错误码透传（rate_limit），不被预算文案改写。"""
    from bridges.ai.adapters import RateLimitError

    adapter = _RecordingAdapter(errors=[RateLimitError("Qwen rate limit: test")])
    gateway = _gateway_with(adapter, max_attempts=2, backoff=1.0)
    budget = RunBudget("run-truncate-8", total_ms=100)
    result = gateway.invoke(
        "qwen_structured_output", "1", _run_context(), {"messages": []}, budget=budget
    )
    assert result.status == ModelCallStatus.RETRYABLE_FAIL
    assert result.error_code == "rate_limit"
    assert adapter.calls == 1
    assert result.lock is not None and result.lock.retry_count == 0


# ---------------------------------------------------------------------------
# 人味化：首稿部分交付与真实错误透传（服务级）
# ---------------------------------------------------------------------------

_HZ_SOURCE = (
    "番茄工作法把时间切成 25 分钟的工作块和 5 分钟的休息块。"
    "四个工作块后休息 15 分钟。"
)

#: 高置信表达问题触发一次定向修订的模板化首稿（与既有修订服务测试一致）。
_HZ_TEMPLATED = (
    "本助手认为，总而言之，番茄工作法把时间切成 25 分钟的工作块"
    "和 5 分钟的休息块，四个工作块后休息 15 分钟。未来可期。"
)

_HZ_CLEAN = (
    "把时间切成 25 分钟的工作块和 5 分钟的休息块，四个工作块后"
    "休息 15 分钟，这就是番茄工作法的大致框架。"
)


def _hz_expression_input() -> Any:
    from bridges.skills.humanizer.intent import route_humanizer_message

    routed = route_humanizer_message(f"帮我改写这段话：{_HZ_SOURCE}")
    assert routed is not None
    return routed.skill_input


def _hz_service(adapter: Any) -> Any:
    from bridges.skills.humanizer.service import HumanizerService
    from bridges.skills.registry import create_builtin_registry

    return HumanizerService(
        registry=create_builtin_registry(),
        gateway=_gateway_with(adapter),
    )


def _hz_run(service: Any, skill_input: Any, *, budget: RunBudget | None = None) -> Any:
    events = list(
        service.run_task(
            "acc-hz",
            "conv-hz",
            "msg-hz",
            skill_input,
            _run_context("run-hz"),
            budget=budget,
        )
    )
    result = next(event.result for event in events if event.kind == "result")
    return result


def test_humanizer_partial_delivery_when_revision_budget_insufficient() -> None:
    """首稿成功 + 修订预算不足：交付带标注首稿（成功部分终态）。"""
    adapter = _RecordingAdapter(output={"final_text": _HZ_TEMPLATED})
    budget = RunBudget("run-hz-1", total_ms=1_000)
    result = _hz_run(_hz_service(adapter), _hz_expression_input(), budget=budget)
    assert adapter.calls == 1, "预算不足不得发起修订调用"
    assert result.status == HumanizerResultStatus.DONE
    assert result.error_code is None, "部分交付是成功终态而非错误"
    article = result.article
    assert article is not None
    assert article.delivery_status == ArticleDeliveryStatus.PARTIAL
    assert article.delivery_note is not None
    assert "已交付首稿" in article.delivery_note
    assert article.revision is not None
    assert article.revision.skipped_reason == "预算不足"
    # 正文仍是首稿（部分交付不抹掉已完成工作）
    assert article.final_text == _HZ_TEMPLATED


def test_humanizer_partial_delivery_when_revision_call_fails() -> None:
    """首稿成功 + 修订调用失败：交付带标注首稿，真实原因保留在审计。"""
    adapter = _RecordingAdapter(
        output={"final_text": _HZ_TEMPLATED},
        errors=[TransientError("Qwen request timeout: revision")],
        error_from_call=2,
    )
    budget = RunBudget("run-hz-2", total_ms=60_000)
    result = _hz_run(_hz_service(adapter), _hz_expression_input(), budget=budget)
    assert adapter.calls == 2, "预算充足时修订调用确实发起"
    assert result.status == HumanizerResultStatus.DONE
    article = result.article
    assert article is not None
    assert article.delivery_status == ArticleDeliveryStatus.PARTIAL
    assert article.delivery_note is not None
    assert "修订调用失败" in article.delivery_note
    assert result.revision is not None
    assert result.revision.skipped_reason == "model_error:transient"


def test_humanizer_draft_timeout_passes_through_real_error() -> None:
    """首稿即超时：真实错误码透传，不再被 budget_exceeded 掩盖。"""
    adapter = _RecordingAdapter(
        errors=[TransientError("Qwen request timeout: draft")]
    )
    budget = RunBudget("run-hz-3", total_ms=60_000)
    result = _hz_run(_hz_service(adapter), _hz_expression_input(), budget=budget)
    assert adapter.calls == 1
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "transient", "真实上游超时错误码"
    assert result.error_code != "budget_exceeded"
    assert "连接中断" in (result.error_message or ""), "真实错误必须是中文文案"
    article = result.article
    assert article is not None
    assert article.delivery_status == ArticleDeliveryStatus.FAILED


def test_humanizer_full_delivery_is_not_marked_partial() -> None:
    """首稿 + 修订均成功：完整交付（delivered），无部分交付标注。"""
    adapter = _RecordingAdapter(
        outputs=[
            {"final_text": _HZ_TEMPLATED},
            {"final_text": _HZ_CLEAN},
        ]
    )
    budget = RunBudget("run-hz-4", total_ms=60_000)
    result = _hz_run(_hz_service(adapter), _hz_expression_input(), budget=budget)
    # 修订成功 → 交付修订稿，不标部分交付
    assert adapter.calls == 2
    assert result.status == HumanizerResultStatus.DONE
    article = result.article
    assert article is not None
    assert article.delivery_status == ArticleDeliveryStatus.DELIVERED
    assert article.delivery_note is None
    assert result.revision is not None
    assert result.revision.skipped_reason is None


# ---------------------------------------------------------------------------
# 生涯：真实错误透传与修复门（服务级）
# ---------------------------------------------------------------------------


def _career_good_output() -> dict[str, Any]:
    return {
        "final_text": "综合你的阶段与目标，数据分析是值得考虑的方向。",
        "facts": [
            {
                "content": "数据分析相关岗位需求持续增长。",
                "evidence_refs": ["statement:current"],
                "note": "来源：行业报告。",
            }
        ],
        "assumptions": [
            {
                "content": "你可能适合偏业务的数据分析岗。",
                "evidence_refs": [],
                "note": None,
                "verification_next_step": "与从业者交流。",
            }
        ],
        "options": [
            {
                "content": "数据分析方向（业务侧）。",
                "evidence_refs": ["statement:current"],
                "note": None,
                "rationale": "与你的兴趣匹配。",
            }
        ],
        "risks": [
            {
                "content": "岗位竞争加剧。",
                "evidence_refs": ["statement:current"],
                "note": None,
                "trigger": "应届求职季人数增加。",
            }
        ],
        "path": [
            {
                "content": "先补齐统计与 SQL 基础。",
                "evidence_refs": ["statement:current"],
                "note": None,
                "timeline": "第 1-3 个月",
            }
        ],
        "suggestions": [
            {
                "content": "完成一个端到端数据分析小项目。",
                "evidence_refs": ["statement:current"],
                "note": None,
                "verification": "项目上线后可验证兴趣与能力。",
            }
        ],
        "boundary_statement": "本规划不构成就业保证，也不替代持证职业顾问。",
        "open_questions": ["行业报告口径未完全披露。"],
    }


def _career_service(adapter: Any) -> Any:
    from bridges.career.service import CareerPlannerService
    from bridges.learning import InMemoryLearningRepository, LearningService
    from bridges.observability.service import ObservabilityService

    return CareerPlannerService(
        gateway=_gateway_with(adapter, max_attempts=2, backoff=0.01),
        learning_service=LearningService(repository=InMemoryLearningRepository()),
        observability_service=ObservabilityService(),
    )


def _career_run(service: Any, *, budget: RunBudget | None = None) -> Any:
    events = list(
        service.run_task(
            "account-1",
            "conv-1",
            "assistant-1",
            "生涯规划：数据分析方向怎么安排",
            mode="companion",
            run_context=_run_context("run-career"),
            profile_enabled=True,
            profile_used=False,
            profile_items=[],
            budget=budget,
        )
    )
    result = next(event.result for event in events if event.kind == "result")
    return result


def test_career_generation_timeout_twice_returns_real_error() -> None:
    """上游两次超时：终态为真实瞬断错误码而非 budget_exceeded。"""
    adapter = _RecordingAdapter(
        errors=[
            TransientError("Qwen request timeout: 1"),
            TransientError("Qwen request timeout: 2"),
        ]
    )
    budget = RunBudget("run-career-1", total_ms=60_000)
    result = _career_run(_career_service(adapter), budget=budget)
    assert adapter.calls == 2, "预算充足时网关按既有语义重试一次"
    assert result.error_code == "transient"
    assert result.error_code != "budget_exceeded"
    assert "连接中断" in (result.error_message or ""), "真实错误必须是中文文案"


def test_career_timeout_retry_suppressed_by_budget_returns_real_error() -> None:
    """剩余预算不足：网关重试次数为 0，终态仍是真实错误码。"""
    adapter = _RecordingAdapter(errors=[TransientError("Qwen request timeout: 1")])
    budget = RunBudget("run-career-2", total_ms=100)
    result = _career_run(_career_service(adapter), budget=budget)
    assert adapter.calls == 1, "预算不足时网关重试次数为 0"
    assert result.error_code == "transient"
    assert result.error_code != "budget_exceeded"


def test_career_success_under_budget_delivers_plan() -> None:
    """预算内成功：正常交付规划（回归护栏）。"""
    adapter = _RecordingAdapter(output=_career_good_output())
    budget = RunBudget("run-career-3", total_ms=60_000)
    result = _career_run(_career_service(adapter), budget=budget)
    assert result.status.value == "done"
    assert result.error_code is None
    assert result.output is not None and result.output.final_text


# ---------------------------------------------------------------------------
# 聊天集成：注入式慢适配器整轮墙钟在（缩放后）预算内形成终态
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """构建挂载 sqlite bridges.db 的应用（真实聊天服务 + 技能编排）。"""
    from bridges.api.main import create_app

    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    app = create_app()
    yield app
    # teardown：关闭数据库连接，释放文件句柄（Windows 下避免 pytest
    # 清理 tmp_path 时 WinError 32 文件锁）。
    database = getattr(app.state, "bridges_database", None)
    if database is not None:
        with contextlib.suppress(Exception):
            database.connection.close()


@pytest.fixture
def client(sqlite_app: Any) -> TestClient:
    return TestClient(sqlite_app)


def _register(client: TestClient, tag: str = "1") -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={
            "username": f"truncate_user_{tag}",
            "qq_email": f"12345678{tag}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def _create_conversation(client: TestClient) -> str:
    response = client.post("/chat/conversations", json={})
    assert response.status_code == 201, response.text
    return response.json()["conversation_id"]


def _swap_career_gateways(sqlite_app: Any, adapter: Any) -> None:
    gateway = _gateway_with(adapter, max_attempts=2, backoff=0.01)
    sqlite_app.state.chat_service._gateway = gateway  # noqa: SLF001
    sqlite_app.state.career_planner_service._gateway = gateway  # noqa: SLF001



def test_career_slow_call_terminates_within_scaled_budget(
    sqlite_app: Any,
    client: TestClient,
    generation_helpers: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """生涯路径同样的截断约束：整轮墙钟在（缩放后）预算内、真实错误透传。"""
    monkeypatch.setattr("bridges.chat.budget.TOTAL_BUDGET_MS", 3_000)
    account = _register(client, "2")
    _swap_career_gateways(
        sqlite_app,
        _RecordingAdapter(
            errors=[TransientError("Qwen request timeout: generation")],
            honor_timeout=True,
        ),
    )
    conversation_id = _create_conversation(client)
    client.headers.update({"X-Account-Id": account["id"]})
    created = generation_helpers["send"](
        client,
        conversation_id,
        content=(
            "生涯规划助手：我大二在读计算机科学，喜欢数据分析，"
            "怎么规划接下来的方向"
        ),
    )
    message_id = created["assistant_message"]["message_id"]

    started = time.monotonic()
    generation_helpers["drive"](sqlite_app, timeout=12.0)
    events = generation_helpers["subscribe"](
        client, conversation_id, message_id, timeout=12.0
    )
    elapsed = time.monotonic() - started
    assert elapsed < 3.0 + 0.8, f"整轮墙钟 {elapsed:.2f}s 应不超过缩放预算+容差"
    assert events[-1][0] == "error"
    code = events[-1][1]["error"]["code"]
    assert code == "transient", f"真实错误码透传而非 budget_exceeded（实际 {code}）"


def test_career_over_budget_with_real_result_delivers_plan(
    sqlite_app: Any,
    client: TestClient,
    generation_helpers: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """预算到期但真实结果已形成：先消费真实结果——交付规划而非 budget_exceeded。

    与 Issue 06 第七轮语义一致：只有「预算耗尽且无任何草稿/真实错误可
    交付」时才使用 budget_exceeded。适配器忽略截断超时（模拟不可中断的
    慢上游）但最终返回了完整规划。
    """
    monkeypatch.setattr("bridges.chat.budget.TOTAL_BUDGET_MS", 50)
    account = _register(client, "3")

    class _SlowIgnoringAdapter(_RecordingAdapter):
        def call(
            self,
            capability: CapabilityRecord,
            run_context: Any,
            payload: dict[str, Any],
        ) -> AdapterResult:
            # 忽略截断超时：真实慢上游在预算外返回完整结果
            time.sleep(0.3)
            return AdapterResult(
                actual_model_id=capability.model_id,
                output=_career_good_output(),
            )

    _swap_career_gateways(sqlite_app, _SlowIgnoringAdapter())
    conversation_id = _create_conversation(client)
    client.headers.update({"X-Account-Id": account["id"]})
    created = generation_helpers["send"](
        client,
        conversation_id,
        content=(
            "生涯规划助手：我大二在读计算机科学，喜欢数据分析，"
            "怎么规划接下来的方向"
        ),
    )
    message_id = created["assistant_message"]["message_id"]
    generation_helpers["drive"](sqlite_app, timeout=12.0)
    events = generation_helpers["subscribe"](
        client, conversation_id, message_id, timeout=12.0
    )
    assert events[-1][0] == "done", "真实结果优先消费：交付规划而非 budget_exceeded"
    final = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = [m for m in final["messages"] if m["role"] == "assistant"][0]
    assert assistant["status"] == "done"
    assert assistant.get("error_code") is None
    assert (assistant.get("career_planning") or {}).get("output") is not None
