"""bridges-humanizer 编排服务测试（Issue 28）。

两条路径（改写/生成）经可编程结构化适配器驱动：输出合同完整性门、
事实锁冲突停止、体裁复核、引用保持、失败恢复（重试不丢输入）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from bridges.ai import CapabilityRegistry, ModelGateway
from bridges.ai.adapters import AdapterError, AdapterResult, RateLimitError
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    RetryPolicy,
)
from bridges.contracts.expression import Genre
from bridges.contracts.humanizer import (
    HumanizerPath,
    HumanizerProcessState,
    HumanizerResultStatus,
    HumanizerSkillInput,
    HumanizerTaskContract,
)
from bridges.contracts.workflows import ObjectDomain, RunContextEnvelope
from bridges.skills.humanizer.service import HumanizerService
from bridges.skills.registry import create_builtin_registry

_CORPUS = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "bridges"
    / "skills"
    / "humanizer"
    / "skill"
    / "fixtures"
    / "rewrite_corpus.md"
).read_text(encoding="utf-8")
_SOURCE = _CORPUS.split("## 原文")[1].split("## 事实锁清单")[0].strip()

#: 保持全部事实锁的合规改写结果（模型应产出的形状）
_COMPLIANT_FINAL = (
    "光合作用指的是植物把光能转化为化学能的过程。研究显示，在光照充足"
    "的条件下，水稻叶片的净光合速率约为 25 μmol·m⁻²·s⁻¹；当温度超过 "
    "35°C 时，速率会显著下降（Zhang et al., 2021）。核心反应可以写成 "
    "6CO₂ + 6H₂O → C₆H₁₂O₆ + 6O₂。每固定 1 mol CO₂ 大约需要 8-10 个"
    "光量子；有研究表明，在 25°C 条件下 Rubisco 的周转速率约为每秒 3 "
    "次，仅相当于部分 C4 植物的一半。需要特别说明的是，数据仅适用于受控"
    "温室条件下的栽培品种，不能直接外推到田间。目前只能说初步结果支持"
    "「高温会抑制光合效率」这一判断，尚不能证明其普遍适用（参见 Smith, "
    "2020）。你可以把光合作用比作植物的充电过程，但比喻到此为止，真正的"
    "机制是叶绿素吸收光子；对你说来，这意味着理解温室栽培需要先了解这些"
    "条件。"
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
    """可编程结构化适配器：返回预置 JSON 输出或抛供应商错误。"""

    def __init__(
        self,
        output: dict[str, Any] | None = None,
        error: AdapterError | None = None,
    ) -> None:
        self._output = output
        self._error = error

    def call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        if self._error is not None:
            raise self._error
        return AdapterResult(
            actual_model_id=capability.model_id,
            output=self._output or {},
        )


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
    from datetime import UTC, datetime

    return RunContextEnvelope(
        run_id="run-test",
        account_id="acc-test",
        project_id="conv-test",
        workflow_name="chat",
        workflow_version="1",
        object_domain=ObjectDomain.PERSONAL_VAULT,
        submitted_at=datetime.now(UTC),
    )


def _rewrite_input(*, source: str = _SOURCE) -> HumanizerSkillInput:
    return HumanizerSkillInput(
        skill_id="bridges-humanizer",
        contract=HumanizerTaskContract(
            path=HumanizerPath.REWRITE,
            genre=Genre.POPULAR_SCIENCE,
            source_text=source,
            audience="普通读者",
            channel="公众号",
            length_target="800 字",
        ),
    )


def _good_output(final_text: str = _COMPLIANT_FINAL) -> dict[str, Any]:
    return {
        "final_text": final_text,
        "edits": [
            {
                "original": "研究显示",
                "revised": "数据显示",
                "kind": "word_choice",
                "reason": "科普文案规则：使用面向读者的表述。",
            }
        ],
        "fact_check": [
            {"item": "25 μmol·m⁻²·s⁻¹", "result": "已核实", "evidence": "与原文一致。"}
        ],
        "open_questions": ["田间高温胁迫下的实际速率仍待验证。"],
    }


def _run(service: HumanizerService, skill_input: HumanizerSkillInput) -> tuple[list[Any], Any]:
    events = list(
        service.run_task(
            "acc-test",
            "conv-test",
            "msg-test",
            skill_input,
            _run_context(),
        )
    )
    result = next(e.result for e in events if e.kind == "result")
    return events, result


def test_rewrite_path_produces_complete_contract() -> None:
    service = _make_service(_ProgrammableStructuredAdapter(output=_good_output()))
    events, result = _run(service, _rewrite_input())
    process_events = [e for e in events if e.kind == "process"]
    assert process_events  # 过程事件（loading 步骤）已产出
    assert result.status == HumanizerResultStatus.DONE
    assert result.skill_id == "bridges-humanizer"
    assert result.skill_version == "1.0.0"
    assert result.output is not None
    assert result.output.final_text == _COMPLIANT_FINAL
    assert result.output.edits and all(e.reason for e in result.output.edits)
    assert result.output.fact_check
    assert result.output.completeness_gaps() == []
    assert result.fact_lock_check is not None and result.fact_lock_check.passed
    # 事实核查包含确定性与模型条目
    assert any(item.result == "已核实" for item in result.output.fact_check)


def test_rewrite_fact_lock_conflict_stops_delivery() -> None:
    violating = _good_output(_COMPLIANT_FINAL.replace("25 μmol·m⁻²·s⁻¹", "30 μmol·m⁻²·s⁻¹"))
    service = _make_service(_ProgrammableStructuredAdapter(output=violating))
    _, result = _run(service, _rewrite_input())
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "fact_lock_conflict"
    assert result.output is None  # 冲突时停止交付，不输出违规文本
    assert result.fact_lock_check is not None
    assert result.fact_lock_check.blocking_conflicts


def test_strength_upgrade_stops_delivery() -> None:
    violating = _good_output(_COMPLIANT_FINAL.replace("尚不能证明其普遍适用", "证明其普遍适用"))
    service = _make_service(_ProgrammableStructuredAdapter(output=violating))
    _, result = _run(service, _rewrite_input())
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "fact_lock_conflict"


def test_output_contract_incomplete_rejected() -> None:
    incomplete = _good_output()
    incomplete.pop("edits")
    service = _make_service(_ProgrammableStructuredAdapter(output=incomplete))
    _, result = _run(service, _rewrite_input())
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "output_contract_incomplete"
    assert "修改细节" in (result.error_message or "")


def test_genre_check_failure_rejected() -> None:
    # 保持全部事实锁但缺失类比/边界/行动相关性的科普文案 → 体裁复核不通过
    bad_genre = _good_output(_COMPLIANT_FINAL.split("你可以把光合作用比作")[0].rstrip("。"))
    service = _make_service(_ProgrammableStructuredAdapter(output=bad_genre))
    _, result = _run(service, _rewrite_input())
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "genre_check_failed"
    assert "科普文案" in (result.error_message or "")


def test_empty_source_is_empty_state() -> None:
    service = _make_service(_ProgrammableStructuredAdapter(output=_good_output()))
    empty_input = _rewrite_input(source="   ")
    events, result = _run(service, empty_input)
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "empty_source"
    process_states = [e.state for e in events if e.kind == "process"]
    assert HumanizerProcessState.EMPTY in process_states


def test_auth_failure_maps_to_permission_state() -> None:
    from bridges.ai.adapters import AdapterError

    service = _make_service(
        _ProgrammableStructuredAdapter(
            error=AdapterError(code="auth_error", message="key invalid", retryable=False)
        )
    )
    events, result = _run(service, _rewrite_input())
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "auth_error"
    process_states = [e.state for e in events if e.kind == "process"]
    assert HumanizerProcessState.PERMISSION in process_states


def test_model_failure_is_retryable_and_input_preserved() -> None:
    service = _make_service(
        _ProgrammableStructuredAdapter(error=RateLimitError("slow"))
    )
    skill_input = _rewrite_input()
    events, result = _run(service, skill_input)
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code in ("rate_limit", "humanizer_generation_failed")
    # 任务契约快照保留（失败后可原任务重试不丢输入）
    assert result.contract.path == HumanizerPath.REWRITE
    assert result.contract.source_text == _SOURCE
    process_states = [e.state for e in events if e.kind == "process"]
    assert HumanizerProcessState.RECOVERY in process_states


def test_generate_path_collects_and_reviews() -> None:
    topic = "为什么人的睡眠时长随着年龄变化"
    final = (
        "睡眠时长指的是一个人一天里用于睡眠的时间总量。人的睡眠时长随"
        "年龄变化：婴儿期每天约 14 小时，成年期约 7-8 小时，老年期约 7 "
        "小时。个体差异很大，时长只是参考。你可以把睡眠比作手机的充电，"
        "但比喻到此为止——充电时长并不决定电池好坏。对你说来，了解自己"
        "所在年龄段的参考时长有助于安排作息。睡眠时长并不会决定健康水平。"
    )
    output = {
        "final_text": final,
        "edits": [
            {
                "original": "睡眠时长随年龄变化",
                "revised": "人的睡眠时长随年龄变化",
                "kind": "rewrite",
                "reason": "面向大一新生的科普表述。",
            }
        ],
        "fact_check": [],
        "open_questions": ["睡眠时长的个体差异来源仍待研究。"],
    }
    service = _make_service(_ProgrammableStructuredAdapter(output=output))
    skill_input = HumanizerSkillInput(
        skill_id="bridges-humanizer",
        contract=HumanizerTaskContract(
            path=HumanizerPath.GENERATE,
            genre=Genre.POPULAR_SCIENCE,
            topic=topic,
            audience="大一新生",
            channel="公众号",
            hard_constraints=[
                "必须给出婴儿期、成年期与老年期三个年龄段的典型睡眠时长（数值+单位）。",
                "必须保留“个体差异很大，时长只是参考”这一限定条件。",
                "不得声称睡眠时长决定健康水平（对象关系方向不得夸大）。",
                "引用来源必须标注为需人工核实，不得虚构论文。",
                "全文不超过 1200 字。",
            ],
        ),
    )
    _, result = _run(service, skill_input)
    # 正向自由文本约束（必须给出/引用来源）无法确定性验证 → 诚实标注
    # needs_human 并交付完整输出（不阻断、不伪造核查结论）
    assert result.status == HumanizerResultStatus.NEEDS_HUMAN
    assert result.output is not None
    assert result.fact_lock_check is not None and result.fact_lock_check.passed
    assert "婴儿期" in result.output.final_text
    assert result.output.completeness_gaps() == []


def test_generate_violating_relation_is_blocking() -> None:
    bad = (
        "人的睡眠时长随年龄变化。睡眠时长决定了健康水平，因此必须保证"
        "睡眠时间。个体差异很大，时长只是参考。"
    )
    output = {
        "final_text": bad,
        "edits": [
            {
                "original": "睡眠时长随年龄变化",
                "revised": "人的睡眠时长随年龄变化",
                "kind": "rewrite",
                "reason": "科普表述。",
            }
        ],
        "fact_check": [],
        "open_questions": [],
    }
    service = _make_service(_ProgrammableStructuredAdapter(output=output))
    skill_input = HumanizerSkillInput(
        skill_id="bridges-humanizer",
        contract=HumanizerTaskContract(
            path=HumanizerPath.GENERATE,
            genre=Genre.POPULAR_SCIENCE,
            topic="为什么人的睡眠时长随着年龄变化",
            hard_constraints=["不得声称睡眠时长决定健康水平。"],
        ),
    )
    _, result = _run(service, skill_input)
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "fact_lock_conflict"


def test_unknown_skill_rejected() -> None:
    service = _make_service(_ProgrammableStructuredAdapter(output=_good_output()))
    skill_input = _rewrite_input()
    skill_input = skill_input.model_copy(update={"skill_id": "not-registered"})
    with pytest.raises(Exception) as exc_info:
        service.resolve_skill(skill_input)
    assert getattr(exc_info.value, "code", "") == "skill_not_found"


def test_added_citation_without_evidence_is_unverified() -> None:
    final_with_new_cite = (
        _COMPLIANT_FINAL.replace(
            "（参见 Smith, 2020）",
            "（参见 Smith, 2020；另见 Jones et al., 2022）",
        )
    )
    output = _good_output(final_with_new_cite)
    service = _make_service(_ProgrammableStructuredAdapter(output=output))
    _, result = _run(service, _rewrite_input())
    # 新增引用不在证据合同内 → 标记未核实（不阻断，但事实核查披露）
    unverified = [r for r in result.references if r.source_type == "unverified"]
    assert unverified
    assert result.status == HumanizerResultStatus.NEEDS_HUMAN
    assert any("引用" in item.item for item in result.output.fact_check)
