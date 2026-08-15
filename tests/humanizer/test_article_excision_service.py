"""文章确定性句子级剔除的服务接线测试（第八次改进 Issue 02）。

用可编程假模型跑完整文章服务：修订后仍仅含机械可剔除类 blocking →
剔除 → 重检通过 → excised_delivery 交付成品（全程写作调用 ≤2 次）；
含任一破坏事实类码 → 维持停止交付；剔除稿重检不通过 / 句数占比超阈值
→ 停止交付；开关关闭恢复 ADR-0027 原终态；旧显式 SKILL 路径一致。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from bridges.ai import CapabilityRegistry, ModelGateway
from bridges.ai.adapters import AdapterResult
from bridges.chat.budget import RunBudget
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    RetryPolicy,
)
from bridges.contracts.expression import Genre
from bridges.contracts.humanizer import (
    ArticleDeliveryStatus,
    HumanizerPath,
    HumanizerResultStatus,
    HumanizerSkillInput,
    HumanizerTaskContract,
)
from bridges.contracts.workflows import ObjectDomain, RunContextEnvelope
from bridges.skills.humanizer.excision import excise_blocking_content
from bridges.skills.humanizer.intent import route_humanizer_message
from bridges.skills.humanizer.service import HumanizerService
from bridges.skills.humanizer.source_ledger import run_fidelity_check
from bridges.skills.registry import create_builtin_registry

_SOURCE = "番茄工作法把时间切成 25 分钟的工作块和 5 分钟的休息块。四个工作块后休息 15 分钟。"

#: 违规稿：只有最后一句是无来源新增（90%），剔除占比 1/4 = 25% ≤ 40%。
_VIOLATING = (
    "把时间切成 25 分钟的工作块和 5 分钟的休息块。四个工作块后休息 15 分钟。"
    "番茄工作法适合办公室人群。全球有 90% 的用户使用番茄钟。"
)
_EXPECTED_EXCISED = (
    "把时间切成 25 分钟的工作块和 5 分钟的休息块。四个工作块后休息 15 分钟。"
    "番茄工作法适合办公室人群。"
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
        retry_policy=RetryPolicy(max_attempts=1, backoff_seconds=0.01),
    )


class _SequencedAdapter:
    """按序返回输出的可编程适配器：记录调用次数与载荷。"""

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
            budget=budget,
        )
    )
    result = next(e.result for e in events if e.kind == "result")
    return events, result


def _draft(final: str) -> dict[str, Any]:
    return {"final_text": final}


# ---------------------------------------------------------------------------
# 新表达契约路径：修订失败 → 剔除 → 交付
# ---------------------------------------------------------------------------


def test_revision_fails_then_excises_and_delivers() -> None:
    adapter = _SequencedAdapter([_draft(_VIOLATING), _draft(_VIOLATING)])
    events, result = _run(_make_service(adapter), _expression_input())
    # 全程模型写作调用 ≤ 2 次（首稿 + 一次修订；剔除是零模型调用）
    assert adapter.calls == 2
    assert result.status == HumanizerResultStatus.DONE
    assert result.output is not None
    assert result.output.final_text == _EXPECTED_EXCISED
    assert "90%" not in result.output.final_text
    assert result.article is not None
    assert result.article.delivery_status == ArticleDeliveryStatus.DELIVERED
    assert result.article.excision is not None
    assert result.article.excision.removed_count == 1
    assert result.article.excision.removed_sentence_count == 1
    assert result.article.excision.items[0].code == "unattributed_claim"
    assert "已移除 1 处无来源/未授权内容" in (result.article.delivery_note or "")
    # 审计：excised_delivery 终态 + 移除计数
    assert result.revision is not None
    assert result.revision.triggered is True
    assert result.revision.final_state == "excised_delivery"
    assert result.revision.excision_attempted is True
    assert result.revision.excision_removed_count == 1
    assert result.revision.excision_removed_sentences == 1
    # 过程事件包含剔除步骤
    assert any("剔除" in e.step_label for e in events if e.kind == "process")
    # 剔除稿重检通过：保真硬门无 blocking
    assert result.fidelity_check is not None
    assert not result.fidelity_check.blocking_failures


def test_revision_skipped_by_budget_still_excises_draft() -> None:
    """修订因预算不足未执行、首稿仅含机械可剔除类 blocking → 剔除交付。

    剔除是终态前的确定性救援，不依赖修订是否发生；写作调用仍为 1 次。
    """
    budget = RunBudget("run-budget", total_ms=1_000)
    adapter = _SequencedAdapter([_draft(_VIOLATING)])
    _, result = _run(
        _make_service(adapter), _expression_input(), budget=budget
    )
    assert adapter.calls == 1
    assert result.status == HumanizerResultStatus.DONE
    assert result.output is not None
    assert result.output.final_text == _EXPECTED_EXCISED
    assert result.revision is not None
    assert result.revision.skipped_reason == "budget_insufficient"
    assert result.revision.final_state == "excised_delivery"
    assert result.article is not None
    assert result.article.excision is not None


def test_mixed_codes_stop_delivery() -> None:
    """含任一破坏事实类码（number_changed）→ 维持 fidelity_gate_conflict。

    混合码场景不满足「全部 blocking findings ∈ 机械可剔除集合」前提，
    剔除流程根本不会启动（无无效尝试与过程事件）。
    """
    mixed = (
        "把时间切成 35 分钟的工作块和 5 分钟的休息块。四个工作块后休息"
        " 15 分钟。番茄工作法适合办公室人群。全球有 90% 的用户使用番茄钟。"
    )
    adapter = _SequencedAdapter([_draft(mixed), _draft(mixed)])
    events, result = _run(_make_service(adapter), _expression_input())
    assert adapter.calls == 2
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "fidelity_gate_conflict"
    assert result.output is None
    assert result.revision is not None
    assert result.revision.final_state == "stop_delivery"
    # 前提不满足：未启动剔除（审计与过程事件都不出现）
    assert result.revision.excision_attempted is False
    assert result.revision.excision_removed_count is None
    assert not any("剔除" in e.step_label for e in events if e.kind == "process")
    assert result.article is not None
    assert result.article.delivery_status == ArticleDeliveryStatus.FAILED
    assert result.article.excision is None


def test_excised_recheck_still_blocking_stops_delivery() -> None:
    """违规句同时承载保留事实（200 只在违规句出现）：剔除后重检不通过
    → 停止交付，不交付残稿。"""
    custom_source = (
        "光合作用是植物把光能转化为化学能的过程。叶绿素吸收光能。"
        "每片叶子含 200 个叶绿体。"
    )
    routed = route_humanizer_message(f"帮我改写这段话：{custom_source}")
    assert routed is not None
    entangled = (
        "光合作用是植物把光能转化为化学能的过程。叶绿素吸收光能。"
        "全球有 90% 的用户使用它，每片叶子含 200 个叶绿体。"
    )
    adapter = _SequencedAdapter([_draft(entangled), _draft(entangled)])
    _, result = _run(_make_service(adapter), routed.skill_input)
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "fidelity_gate_conflict"
    assert result.output is None
    assert result.revision is not None
    assert result.revision.excision_attempted is True
    assert result.revision.excision_removed_count is None
    # 终态为停止交付：剔除稿重检发现保留数值被破坏（NUMBER_CHANGED）
    assert result.revision.final_state == "stop_delivery"
    # 投影中的保真检查仍是原候选的真实结果（剔除未通过不覆盖）
    codes = {
        f.code.value
        for f in result.fidelity_check.blocking_failures
    }
    assert "unattributed_claim" in codes, codes
    # 端到端复核护栏：剔除稿确实会触发 NUMBER_CHANGED（200 被一并剔除）
    record = excise_blocking_content(
        entangled, result.fidelity_check.blocking_failures
    )
    assert record is not None
    assert result.source_ledger is not None
    recheck = run_fidelity_check(
        result.source_ledger,
        record.text,
        contract_path=HumanizerPath.REWRITE,
    )
    assert any(
        f.code.value == "number_changed" for f in recheck.blocking_failures
    ), recheck.blocking_failures


def test_sentence_ratio_guard_stops_delivery() -> None:
    """两句话稿中违规句占 50% > 40% 阈值 → 停止交付，不交付残稿。"""
    short = (
        "把时间切成 25 分钟的工作块和 5 分钟的休息块，四个工作块后休息"
        " 15 分钟。全球有 90% 的用户使用番茄钟。"
    )
    adapter = _SequencedAdapter([_draft(short), _draft(short)])
    _, result = _run(_make_service(adapter), _expression_input())
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "fidelity_gate_conflict"
    assert result.output is None
    assert result.revision is not None
    assert result.revision.final_state == "stop_delivery"
    assert result.revision.excision_attempted is True
    assert result.revision.excision_removed_count is None


def test_excision_switch_disabled_restores_original_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "bridges.skills.humanizer.service.EXCISION_DELIVERY_ENABLED", False
    )
    adapter = _SequencedAdapter([_draft(_VIOLATING), _draft(_VIOLATING)])
    _, result = _run(_make_service(adapter), _expression_input())
    # 开关关闭：剔除不可用 → 回到 ADR-0027 原终态（修订后仍 blocking 停止）
    assert adapter.calls == 2
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "fidelity_gate_conflict"
    assert result.output is None
    assert result.revision is not None
    assert result.revision.final_state == "stop_delivery"
    assert result.revision.excision_attempted is False


def test_excised_delivery_revision_summary_shows_zero_remaining() -> None:
    adapter = _SequencedAdapter([_draft(_VIOLATING), _draft(_VIOLATING)])
    _, result = _run(_make_service(adapter), _expression_input())
    # 剔除稿重检通过：修订摘要的「仍剩风险」如实为 0（审计 revised_*
    # 计数仍保留修订稿自身的真实结果，不被覆盖）
    assert result.article is not None
    assert result.article.revision is not None
    assert result.article.revision.remaining_count == 0
    assert result.revision is not None
    assert result.revision.revised_fidelity_blocking >= 1


# ---------------------------------------------------------------------------
# 旧显式 SKILL 路径（无表达契约）：语义一致
# ---------------------------------------------------------------------------


def _legacy_rewrite_input() -> HumanizerSkillInput:
    return HumanizerSkillInput(
        skill_id="bridges-humanizer",
        contract=HumanizerTaskContract(
            path=HumanizerPath.REWRITE,
            genre=Genre.POPULAR_SCIENCE,
            source_text=_SOURCE,
            audience="普通读者",
            channel="公众号",
        ),
    )


def _legacy_output(final: str) -> dict[str, Any]:
    return {
        "final_text": final,
        "edits": [
            {
                "original": "原文片段",
                "revised": "改写片段",
                "kind": "word_choice",
                "reason": "科普表述。",
            }
        ],
        "fact_check": [],
        "open_questions": [],
    }


def test_legacy_path_excises_and_delivers() -> None:
    adapter = _SequencedAdapter([_legacy_output(_VIOLATING)])
    _, result = _run(_make_service(adapter), _legacy_rewrite_input())
    assert adapter.calls == 1
    assert result.status in (
        HumanizerResultStatus.DONE,
        HumanizerResultStatus.NEEDS_HUMAN,
    )
    assert result.output is not None
    assert result.output.final_text == _EXPECTED_EXCISED
    assert "90%" not in result.output.final_text
    assert result.article is not None
    assert result.article.delivery_status == ArticleDeliveryStatus.DELIVERED
    assert result.article.excision is not None
    assert result.article.excision.removed_count == 1
    assert "已移除 1 处无来源/未授权内容" in (result.article.delivery_note or "")


def test_legacy_path_fact_breaking_still_stops() -> None:
    mixed = _legacy_output(
        "把时间切成 35 分钟的工作块和 5 分钟的休息块。四个工作块后休息"
        " 15 分钟。番茄工作法适合办公室人群。全球有 90% 的用户使用番茄钟。"
    )
    adapter = _SequencedAdapter([mixed])
    _, result = _run(_make_service(adapter), _legacy_rewrite_input())
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "fidelity_gate_conflict"
    assert result.output is None
    assert result.article is not None
    assert result.article.delivery_status == ArticleDeliveryStatus.FAILED
    assert result.article.excision is None
