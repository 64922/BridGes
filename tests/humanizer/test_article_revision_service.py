"""文章定向二次修订服务接线测试（人味化改造 Issue 05）。

用可编程假模型跑完整文章服务：触发裁决、至多两次写作调用、修订请求只
含待修问题、修订后同一版本硬门/契约/审稿重跑、预算/停止/开关门、持久
计数恢复与跨进程恢复不达三次调用。
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from bridges.ai import CapabilityRegistry, ModelGateway
from bridges.ai.adapters import AdapterError, AdapterResult
from bridges.chat.budget import RunBudget
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    RetryPolicy,
)
from bridges.contracts.humanizer import HumanizerResultStatus
from bridges.contracts.workflows import ObjectDomain, RunContextEnvelope
from bridges.skills.humanizer.intent import route_humanizer_message
from bridges.skills.humanizer.service import HumanizerService
from bridges.skills.registry import create_builtin_registry

_SOURCE = "番茄工作法把时间切成 25 分钟的工作块和 5 分钟的休息块。四个工作块后休息 15 分钟。"


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
        retry_policy=RetryPolicy(max_attempts=1, backoff_seconds=0.01),
    )


class _SequencedAdapter:
    """按序返回输出的可编程适配器：记录调用次数与载荷，可注入异常。"""

    def __init__(self, outputs: list[dict[str, Any] | Exception]) -> None:
        self._outputs = list(outputs)
        self.calls = 0
        self.payloads: list[dict[str, Any]] = []

    def call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        self.calls += 1
        self.payloads.append(payload)
        step = self._outputs[min(self.calls - 1, len(self._outputs) - 1)]
        if isinstance(step, Exception):
            raise step
        return AdapterResult(actual_model_id=capability.model_id, output=step)


def _gateway_with(adapter: Any) -> ModelGateway:
    registry = CapabilityRegistry()
    registry.register(_structured_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_structured_output", "1", adapter)
    return gateway


def _make_service(adapter: Any) -> HumanizerService:
    return HumanizerService(
        registry=create_builtin_registry(),
        gateway=_gateway_with(adapter),
    )


def _run_context() -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id="run-test",
        account_id="acc-test",
        project_id="conv-test",
        workflow_name="chat",
        workflow_version="1",
        object_domain=ObjectDomain.PERSONAL_VAULT,
        submitted_at=datetime.now(UTC),
    )


def _expression_input() -> Any:
    routed = route_humanizer_message(f"帮我改写这段话：{_SOURCE}")
    assert routed is not None
    return routed.skill_input


def _run(
    service: HumanizerService,
    skill_input: Any,
    *,
    writing_call_count: int = 0,
    recovered_draft: str | None = None,
    stop_event: threading.Event | None = None,
    budget: RunBudget | None = None,
) -> tuple[list[Any], Any]:
    events = list(
        service.run_task(
            "acc-test",
            "conv-test",
            "msg-test",
            skill_input,
            _run_context(),
            writing_call_count=writing_call_count,
            recovered_draft=recovered_draft,
            stop_event=stop_event,
            budget=budget,
        )
    )
    result = next(e.result for e in events if e.kind == "result")
    return events, result


def _draft(final: str) -> dict[str, Any]:
    return {"final_text": final}


# ---------------------------------------------------------------------------
# 触发与终态状态机
# ---------------------------------------------------------------------------


def test_natural_draft_triggers_no_revision_and_single_call() -> None:
    natural = (
        "把时间切成 25 分钟的工作块和 5 分钟的休息块，四个工作块后"
        "休息 15 分钟，这就是番茄工作法的大致框架。"
    )
    adapter = _SequencedAdapter([_draft(natural)])
    _, result = _run(_make_service(adapter), _expression_input())
    assert adapter.calls == 1
    assert result.status == HumanizerResultStatus.DONE
    assert result.writing_call_count == 1
    assert result.revision is not None
    assert result.revision.triggered is False
    assert result.revision.final_state == "deliver_draft"


def test_high_confidence_expression_triggers_exactly_one_revision() -> None:
    templated = (
        "本助手认为，总而言之，番茄工作法把时间切成 25 分钟的工作块"
        "和 5 分钟的休息块，四个工作块后休息 15 分钟。未来可期。"
    )
    fixed = (
        "把时间切成 25 分钟的工作块和 5 分钟的休息块，四个工作块后"
        "休息 15 分钟，这就是番茄工作法的大致框架。"
    )
    adapter = _SequencedAdapter([_draft(templated), _draft(fixed)])
    _, result = _run(_make_service(adapter), _expression_input())
    assert adapter.calls == 2
    assert result.status == HumanizerResultStatus.DONE
    assert result.output is not None
    assert result.output.final_text == fixed  # 交付修订稿
    assert result.writing_call_count == 2
    assert result.revision is not None
    assert result.revision.triggered is True
    assert result.revision.final_state == "deliver_revised"
    assert result.revision.resolved_problem_count >= 1


def test_revision_request_contains_only_targeted_problems() -> None:
    templated = (
        "本助手认为，总而言之，番茄工作法把时间切成 25 分钟的工作块"
        "和 5 分钟的休息块，四个工作块后休息 15 分钟。未来可期。"
    )
    adapter = _SequencedAdapter([_draft(templated), _draft(templated)])
    _, result = _run(_make_service(adapter), _expression_input())
    assert adapter.calls == 2
    second_payload = adapter.payloads[1]
    system_prompt = second_payload["messages"][0]["content"]
    user_prompt = second_payload["messages"][1]["content"]
    # 待修问题 code 与证据在修订提示中
    assert "assistant_identity_residue" in system_prompt
    assert "mechanical_transition_closing" in system_prompt
    assert "本助手认为" in system_prompt or "总而言之" in system_prompt
    # 不包含全量方法规则 ID
    assert "rewrite.main-clause-first" not in system_prompt
    assert "fact.relevance-not-causality" not in system_prompt
    # 必要原文与首稿在请求中（保护项来源与已通过部分）
    assert "25 分钟" in system_prompt
    assert templated in system_prompt
    # 用户输入保持（修订请求携带必要上下文，不扩张事实）
    assert "番茄工作法" in user_prompt or "番茄工作法" in system_prompt
    assert result.status == HumanizerResultStatus.DONE


def test_revision_still_soft_warning_delivers_with_warn() -> None:
    still_templated = (
        "本助手认为，总而言之，番茄工作法把时间切成 25 分钟的工作块"
        "和 5 分钟的休息块，四个工作块后休息 15 分钟。未来可期。"
    )
    adapter = _SequencedAdapter([_draft(still_templated), _draft(still_templated)])
    _, result = _run(_make_service(adapter), _expression_input())
    # 修订后仍有非关键风格警告 → 按现有 WARN 语义交付并展示具体风险
    assert adapter.calls == 2
    assert result.status == HumanizerResultStatus.DONE
    assert result.output is not None
    assert result.quality_warnings
    assert result.revision is not None
    assert result.revision.triggered is True
    assert result.revision.final_state == "deliver_revised"


def test_revision_breaks_facts_again_stops_delivery() -> None:
    violating = _draft(_SOURCE.replace("25 分钟", "35 分钟"))
    adapter = _SequencedAdapter([violating, violating])
    _, result = _run(_make_service(adapter), _expression_input())
    # 修订后仍有关键保真失败 → 停止交付
    assert adapter.calls == 2
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "fidelity_gate_conflict"
    assert result.output is None
    assert result.revision is not None
    assert result.revision.triggered is True
    assert result.revision.revised_fidelity_blocking >= 1
    assert result.revision.final_state == "stop_delivery"


def test_unsourced_claim_revision_can_remove_it() -> None:
    fabricated = (
        "我去年靠番茄钟坚持了三十天，把时间切成 25 分钟的工作块"
        "和 5 分钟的休息块，四个工作块后休息 15 分钟。"
    )
    cleaned = (
        "把时间切成 25 分钟的工作块和 5 分钟的休息块，四个工作块后"
        "休息 15 分钟，这就是番茄工作法的大致框架。"
    )
    adapter = _SequencedAdapter([_draft(fabricated), _draft(cleaned)])
    _, result = _run(_make_service(adapter), _expression_input())
    # 无来源新增 claim 触发修订；修订删除该 claim 后恢复硬门并交付修订稿
    assert adapter.calls == 2
    assert result.status == HumanizerResultStatus.DONE
    assert result.output is not None
    assert result.output.final_text == cleaned
    assert result.revision is not None
    assert result.revision.triggered is True
    assert result.revision.trigger_code == "unattributed_claim"
    assert result.revision.final_state == "deliver_revised"


def test_revision_model_error_returns_draft_state() -> None:
    templated = (
        "本助手认为，总而言之，番茄工作法把时间切成 25 分钟的工作块"
        "和 5 分钟的休息块，四个工作块后休息 15 分钟。未来可期。"
    )
    adapter = _SequencedAdapter(
        [_draft(templated), AdapterError(code="transient", message="临时失败")]
    )
    _, result = _run(_make_service(adapter), _expression_input())
    # 修订调用失败：返回首稿的真实硬门/软审稿状态，不把未通过稿标为成功
    assert adapter.calls == 2
    assert result.status == HumanizerResultStatus.DONE
    assert result.output is not None
    assert result.revision is not None
    assert result.revision.triggered is True
    assert result.revision.skipped_reason == "model_error:transient"
    assert result.revision.final_state == "deliver_draft"


def test_contract_omission_triggers_revision() -> None:
    skill_input = _expression_input()
    skill_input.contract.hard_constraints = ["必须包含「每小时休息」"]
    adapter = _SequencedAdapter(
        [
            _draft(
                "把时间切成 25 分钟的工作块和 5 分钟的休息块，四个"
                "工作块后休息 15 分钟。"
            ),
            _draft(
                "把时间切成 25 分钟的工作块和 5 分钟的休息块，四个"
                "工作块后休息 15 分钟，每小时休息一次。"
            ),
        ]
    )
    _, result = _run(_make_service(adapter), skill_input)
    assert adapter.calls == 2
    assert result.revision is not None
    assert result.revision.triggered is True
    # 用户指定措辞同时是账本保护项（quote_changed）与契约硬约束（contract
    # omission）；裁决按优先级取硬问题类别，但契约遗漏也计入待修问题
    assert result.revision.draft_contract_omissions >= 1
    assert result.revision.trigger_code == "quote_changed"
    assert result.revision.final_state == "deliver_revised"
    # 修订稿已补齐契约要求：不再展示契约遗漏警告（审查修复：有遗漏必展示）
    assert not any("任务契约遗漏" in w for w in result.quality_warnings)


# ---------------------------------------------------------------------------
# 预算 / 停止 / 开关门
# ---------------------------------------------------------------------------


def test_budget_insufficient_skips_revision() -> None:
    templated = (
        "本助手认为，总而言之，番茄工作法把时间切成 25 分钟的工作块"
        "和 5 分钟的休息块，四个工作块后休息 15 分钟。未来可期。"
    )
    budget = RunBudget("run-budget", total_ms=1_000)
    adapter = _SequencedAdapter([_draft(templated)])
    _, result = _run(
        _make_service(adapter), _expression_input(), budget=budget
    )
    assert adapter.calls == 1
    assert result.revision is not None
    assert result.revision.triggered is True
    assert result.revision.skipped_reason == "budget_insufficient"
    assert result.revision.final_state == "deliver_draft"


def test_user_stopped_skips_revision() -> None:
    templated = (
        "本助手认为，总而言之，番茄工作法把时间切成 25 分钟的工作块"
        "和 5 分钟的休息块，四个工作块后休息 15 分钟。未来可期。"
    )
    stop_event = threading.Event()
    stop_event.set()
    adapter = _SequencedAdapter([_draft(templated)])
    _, result = _run(
        _make_service(adapter), _expression_input(), stop_event=stop_event
    )
    assert adapter.calls == 1
    assert result.revision is not None
    assert result.revision.skipped_reason == "user_stopped"


def test_contract_omission_remaining_after_revision_is_shown() -> None:
    """修订后契约遗漏仍在：WARN 交付并展示具体风险（审查修复）。

    用自由文本约束（非引号措辞，不产生账本保护项）构造纯契约遗漏场景，
    避免触发保真硬门。
    """
    skill_input = _expression_input()
    skill_input.contract.hard_constraints = ["包含时间管理方法的说明"]
    # 首稿与修订稿都没有该说明 → 遗漏持续存在（无保真失败）
    missing = _draft(
        "把时间切成 25 分钟的工作块和 5 分钟的休息块，四个工作块后"
        "休息 15 分钟。"
    )
    adapter = _SequencedAdapter([missing, missing])
    _, result = _run(_make_service(adapter), skill_input)
    assert adapter.calls == 2
    assert result.status == HumanizerResultStatus.DONE
    assert result.output is not None
    assert result.output.quality_status.value == "warn"
    assert any("任务契约遗漏" in w for w in result.quality_warnings)
    assert result.revision is not None
    assert result.revision.revised_contract_omissions >= 1


def test_skipped_revision_with_blocked_draft_stops_delivery() -> None:
    """修订被跳过且首稿本身保真失败时：终态为停止交付（审查修复）。"""
    violating = _draft(_SOURCE.replace("25 分钟", "35 分钟"))
    stop_event = threading.Event()
    stop_event.set()
    adapter = _SequencedAdapter([violating])
    _, result = _run(
        _make_service(adapter), _expression_input(), stop_event=stop_event
    )
    assert adapter.calls == 1
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "fidelity_gate_conflict"
    assert result.revision is not None
    assert result.revision.skipped_reason == "user_stopped"
    # 修订未执行但首稿真实状态是保真阻断 → 停止交付，不误标 deliver_draft
    assert result.revision.final_state == "stop_delivery"


def test_capability_switch_disables_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    templated = (
        "本助手认为，总而言之，番茄工作法把时间切成 25 分钟的工作块"
        "和 5 分钟的休息块，四个工作块后休息 15 分钟。未来可期。"
    )
    monkeypatch.setattr(
        "bridges.skills.humanizer.service.REVISION_CAPABILITY_ENABLED", False
    )
    adapter = _SequencedAdapter([_draft(templated)])
    _, result = _run(_make_service(adapter), _expression_input())
    # 开关关闭：恢复一次调用，不改变硬门，不把未通过首稿标为成功
    assert adapter.calls == 1
    assert result.revision is not None
    assert result.revision.triggered is True
    assert result.revision.skipped_reason == "capability_disabled"
    assert result.revision.final_state == "deliver_draft"
    assert result.status == HumanizerResultStatus.DONE


# ---------------------------------------------------------------------------
# 调用上限与持久计数恢复
# ---------------------------------------------------------------------------


def test_writing_call_limit_reached_rejected_before_any_call() -> None:
    adapter = _SequencedAdapter([_draft("任何正文")])
    _, result = _run(
        _make_service(adapter), _expression_input(), writing_call_count=2
    )
    assert adapter.calls == 0
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "writing_call_limit_reached"
    assert result.output is None


def test_recovered_draft_skips_first_call_and_revises_once() -> None:
    """计数 1 + 恢复正文：跳过首稿调用，只做检查与一次修订（总调用 2）。"""
    recovered = (
        "把时间切成 25 分钟的工作块和 5 分钟的休息块，四个工作块后"
        "休息 15 分钟，这就是番茄工作法的大致框架。"
    )
    templated_recovery = (
        "本助手认为，总而言之，番茄工作法把时间切成 25 分钟的工作块"
        "和 5 分钟的休息块，四个工作块后休息 15 分钟。未来可期。"
    )
    adapter = _SequencedAdapter([_draft(templated_recovery)])
    _, result = _run(
        _make_service(adapter),
        _expression_input(),
        writing_call_count=1,
        recovered_draft=recovered,
    )
    # 恢复的正文已自然 → 不触发修订，本次不再调用模型
    assert adapter.calls == 0
    assert result.status == HumanizerResultStatus.DONE
    assert result.writing_call_count == 1
    assert result.output is not None
    assert result.output.final_text == recovered


def test_recovered_draft_triggers_revision_without_redrafting() -> None:
    templated_recovery = (
        "本助手认为，总而言之，番茄工作法把时间切成 25 分钟的工作块"
        "和 5 分钟的休息块，四个工作块后休息 15 分钟。未来可期。"
    )
    fixed = (
        "把时间切成 25 分钟的工作块和 5 分钟的休息块，四个工作块后"
        "休息 15 分钟，这就是番茄工作法的大致框架。"
    )
    adapter = _SequencedAdapter([_draft(fixed)])
    _, result = _run(
        _make_service(adapter),
        _expression_input(),
        writing_call_count=1,
        recovered_draft=templated_recovery,
    )
    # 首稿已恢复（不调用），修订恰一次 → 总调用 2，不达 3
    assert adapter.calls == 1
    assert result.status == HumanizerResultStatus.DONE
    assert result.writing_call_count == 2
    assert result.output is not None
    assert result.output.final_text == fixed
    assert result.revision is not None
    assert result.revision.triggered is True
    assert result.revision.final_state == "deliver_revised"


def test_cross_attempt_recovery_never_reaches_three_calls() -> None:
    """模拟跨进程恢复：第一次 run 耗尽 2 次额度，恢复后无第三次调用。"""
    violating = _draft(_SOURCE.replace("25 分钟", "35 分钟"))
    adapter = _SequencedAdapter([violating, violating])
    service = _make_service(adapter)
    _, first = _run(service, _expression_input())
    assert adapter.calls == 2
    assert first.writing_call_count == 2
    assert first.status == HumanizerResultStatus.ERROR
    # 跨进程恢复（模拟服务重启后重试）：计数从持久投影恢复，直接拒绝
    adapter2 = _SequencedAdapter([_draft("不该被调用")])
    service2 = _make_service(adapter2)
    _, second = _run(
        service2,
        _expression_input(),
        writing_call_count=first.writing_call_count,
    )
    assert adapter2.calls == 0
    assert second.status == HumanizerResultStatus.ERROR
    assert second.error_code == "writing_call_limit_reached"


# ---------------------------------------------------------------------------
# 旧显式 SKILL 路径兼容（共享统一调用上限与终态语义，Issue 05 AC6）
# ---------------------------------------------------------------------------


def test_legacy_path_shares_writing_call_limit() -> None:
    """旧显式 SKILL 路径（无表达契约）共享写作调用上限：额度用尽即拒绝。"""
    from bridges.contracts.humanizer import (
        HumanizerPath,
        HumanizerSkillInput,
        HumanizerTaskContract,
    )

    skill_input = HumanizerSkillInput(
        skill_id="bridges-humanizer",
        contract=HumanizerTaskContract(
            path=HumanizerPath.GENERATE,
            topic="时间管理",
        ),
    )
    adapter = _SequencedAdapter([_draft("时间管理的关键是先列清单。")])
    _, result = _run(
        _make_service(adapter), skill_input, writing_call_count=2
    )
    assert adapter.calls == 0
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "writing_call_limit_reached"


def test_legacy_path_records_writing_call_count() -> None:
    """旧路径首稿调用后投影携带持久计数（重试恢复依据）。"""
    from bridges.contracts.humanizer import (
        HumanizerPath,
        HumanizerSkillInput,
        HumanizerTaskContract,
    )

    skill_input = HumanizerSkillInput(
        skill_id="bridges-humanizer",
        contract=HumanizerTaskContract(
            path=HumanizerPath.GENERATE,
            topic="时间管理",
        ),
    )
    adapter = _SequencedAdapter([_draft("时间管理的关键是先列清单。")])
    _, result = _run(_make_service(adapter), skill_input)
    assert adapter.calls == 1
    assert result.writing_call_count == 1


def test_legacy_path_repair_respects_quota() -> None:
    """旧路径软门修复共享上限：恢复计数 1 时首稿与修复合计不达三次。"""
    from bridges.contracts.humanizer import (
        HumanizerPath,
        HumanizerSkillInput,
        HumanizerTaskContract,
    )

    skill_input = HumanizerSkillInput(
        skill_id="bridges-humanizer",
        contract=HumanizerTaskContract(
            path=HumanizerPath.GENERATE,
            topic="时间管理",
            genre="lecture_script",
        ),
    )
    adapter = _SequencedAdapter([_draft("先列清单，再排优先级。")])
    _, result = _run(
        _make_service(adapter), skill_input, writing_call_count=1
    )
    # 计数 1：至多再调用一次（首稿，或首稿+修复=2），任何路径都不达三次
    assert adapter.calls <= 2
    assert result.writing_call_count == 1 + adapter.calls
    assert result.writing_call_count <= 2
