"""bridges-humanizer 编排服务测试（Issue 28）。

两条路径（改写/生成）经可编程结构化适配器驱动：输出合同完整性门、
事实锁冲突停止、体裁复核、引用保持、失败恢复（重试不丢输入）。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bridges.ai import CapabilityRegistry, ModelGateway
from bridges.ai.adapters import AdapterError, AdapterResult, RateLimitError
from bridges.chat.attachments import ChatAttachmentError
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
    HumanizerQualityStatus,
    HumanizerResultStatus,
    HumanizerRouteDecision,
    HumanizerRouteSource,
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
    """可编程结构化适配器：返回预置 JSON 输出或抛供应商错误。

    ``sequence``（Issue 07）按调用次数依次返回输出或抛错误（末项复用），
    用于软门「初始产出 → 至多一次修复」的调用序列断言。
    """

    def __init__(
        self,
        output: dict[str, Any] | None = None,
        error: AdapterError | None = None,
        sequence: list[Any] | None = None,
    ) -> None:
        self._output = output
        self._error = error
        self._sequence = list(sequence or [])
        self.calls = 0

    def call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        self.calls += 1
        if self._sequence:
            item = self._sequence[min(self.calls - 1, len(self._sequence) - 1)]
            if isinstance(item, AdapterError):
                raise item
            return AdapterResult(actual_model_id=capability.model_id, output=item)
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


def _run(
    service: HumanizerService,
    skill_input: HumanizerSkillInput,
    *,
    budget: Any = None,
) -> tuple[list[Any], Any]:
    events = list(
        service.run_task(
            "acc-test",
            "conv-test",
            "msg-test",
            skill_input,
            _run_context(),
            budget=budget,
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


def test_genre_check_failure_delivers_text_with_warnings() -> None:
    # Issue 07：软门（体裁等风格指标）失败不扣留正文——交付当前最佳
    # 正文并附未完全满足项；重新生成是可选项而非唯一出口。
    bad_genre = _good_output(_COMPLIANT_FINAL.split("你可以把光合作用比作")[0].rstrip("。"))
    service = _make_service(_ProgrammableStructuredAdapter(output=bad_genre))
    events, result = _run(service, _rewrite_input())
    assert result.status == HumanizerResultStatus.NEEDS_HUMAN
    assert result.output is not None
    assert result.output.final_text == bad_genre["final_text"]
    assert result.output.quality_status == HumanizerQualityStatus.WARN
    assert result.quality_warnings  # 具体未满足项
    assert any("类比" in warning for warning in result.quality_warnings)
    assert result.repair_attempts == 0  # 未传预算 → 预算内未执行修复
    # 草稿事件：模型产出正文后立即下发
    drafts = [e.draft_text for e in events if e.kind == "draft"]
    assert drafts and drafts[-1] == bad_genre["final_text"]


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


def test_unknown_natural_language_route_version_is_rejected() -> None:
    service = _make_service(_ProgrammableStructuredAdapter(output=_good_output()))
    skill_input = _rewrite_input().model_copy(
        update={
            "route": HumanizerRouteDecision(
                source=HumanizerRouteSource.NATURAL_LANGUAGE,
                version="humanizer-route-v0",
                reason="测试未知版本",
            )
        }
    )

    with pytest.raises(Exception) as exc_info:
        service.resolve_skill(skill_input)

    assert getattr(exc_info.value, "code", "") == "humanizer_route_version_conflict"


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


# ---------------------------------------------------------------------------
# Issue 04：附件解析失败指名文件与支持格式，不静默改用另一材料
# ---------------------------------------------------------------------------

class _FakeAttachmentService:
    """可控附件服务：download 返回指定文件名/内容或抛附件错误。"""

    def __init__(
        self,
        *,
        filename: str = "损坏文件.xyz",
        content: bytes = b"\x00\x01 not a document",
        media_type: str = "application/pdf",
        error: Exception | None = None,
    ) -> None:
        self._filename = filename
        self._content = content
        self._media_type = media_type
        self._error = error

    def download(
        self, account_id: str, conversation_id: str, object_id: str
    ) -> tuple[Any, bytes]:
        if self._error is not None:
            raise self._error
        record = SimpleNamespace(
            original_filename=self._filename, media_type=self._media_type
        )
        return record, self._content


def _rewrite_with_attachment(*, source: str = "") -> HumanizerSkillInput:
    return HumanizerSkillInput(
        skill_id="bridges-humanizer",
        contract=HumanizerTaskContract(
            path=HumanizerPath.REWRITE,
            genre=Genre.POPULAR_SCIENCE,
            source_text=source or None,
            attachment_ids=["obj-broken"],
        ),
    )


def test_attachment_parse_failure_names_file_and_formats() -> None:
    """解析失败：指名具体文件与支持格式，保留附件供重试，可重试。"""
    service = _make_service(
        _ProgrammableStructuredAdapter(output=_good_output())
    )
    service._attachments = _FakeAttachmentService()  # noqa: SLF001 - 测试直连
    _, result = _run(service, _rewrite_with_attachment())
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "attachment_parse_failed"
    assert "损坏文件.xyz" in (result.error_message or "")
    assert "PDF" in (result.error_message or "") and "DOCX" in (result.error_message or "")
    assert result.process_state == HumanizerProcessState.RECOVERY  # retryable


def test_attachment_parse_failure_does_not_fall_back_to_pasted_text() -> None:
    """粘贴文本存在也不顶替失败附件：仍指名失败文件，绝不自动改用另一材料。"""
    service = _make_service(_ProgrammableStructuredAdapter(output=_good_output()))
    service._attachments = _FakeAttachmentService()  # noqa: SLF001 - 测试直连
    _, result = _run(
        service, _rewrite_with_attachment(source="可用的粘贴文本。")
    )
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "attachment_parse_failed"


def test_attachment_read_failure_is_retryable_and_names_attachment() -> None:
    """附件读取失败：可重试错误，不把失败折叠成「没有原文」。"""
    service = _make_service(_ProgrammableStructuredAdapter(output=_good_output()))
    service._attachments = _FakeAttachmentService(  # noqa: SLF001 - 测试直连
        error=ChatAttachmentError(
            "attachment_not_found", "附件不存在或没有访问权限。", 404
        )
    )
    _, result = _run(service, _rewrite_with_attachment())
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "attachment_unreadable"
    assert result.process_state == HumanizerProcessState.RECOVERY


# ---------------------------------------------------------------------------
# Issue 07：两级质量门——软门（体裁等风格）至多一次有预算修复，仍不过交付
# 正文与具体警告；硬门（事实锁冲突）才阻止标记最终稿并附恢复方式。
# ---------------------------------------------------------------------------

def _bad_genre_output() -> dict[str, Any]:
    """保持全部事实锁但缺失类比/边界/行动相关性的科普文案（体裁软门不过）。"""
    return _good_output(_COMPLIANT_FINAL.split("你可以把光合作用比作")[0].rstrip("。"))


def _budget(total_ms: int = 120_000) -> Any:
    from bridges.chat.budget import RunBudget

    return RunBudget("run-test", total_ms=total_ms)


def test_soft_gate_repairs_once_and_succeeds() -> None:
    """软门失败且预算允许：至多一次定向修复；修复后通过 → 正常终态。"""
    good = _good_output()
    adapter = _ProgrammableStructuredAdapter(sequence=[_bad_genre_output(), good])
    _, result = _run(service := _make_service(adapter), _rewrite_input(), budget=_budget())
    assert result.status == HumanizerResultStatus.DONE
    assert result.output is not None
    assert result.output.final_text == good["final_text"]
    assert result.output.quality_status == HumanizerQualityStatus.OK
    assert result.repair_attempts == 1
    assert adapter.calls == 2  # 初始产出 + 一次修复，绝无第二次循环


def test_natural_language_route_generates_at_most_once() -> None:
    route = HumanizerRouteDecision(
        source=HumanizerRouteSource.NATURAL_LANGUAGE,
        version="humanizer-route-v1",
        reason="测试自然语言路由",
    )
    adapter = _ProgrammableStructuredAdapter(
        sequence=[_bad_genre_output(), _good_output()]
    )
    _, result = _run(
        service := _make_service(adapter),
        _rewrite_input().model_copy(update={"route": route}),
        budget=_budget(),
    )

    assert result.status == HumanizerResultStatus.NEEDS_HUMAN
    assert result.repair_attempts == 0
    assert adapter.calls == 1


def test_soft_gate_repair_failure_delivers_original_draft() -> None:
    """修复调用失败（供应商错误）：交付原草稿 + 具体警告，不升级硬门。"""
    from bridges.ai.adapters import RateLimitError

    bad = _bad_genre_output()
    adapter = _ProgrammableStructuredAdapter(
        sequence=[bad, RateLimitError("slow")]
    )
    _, result = _run(service := _make_service(adapter), _rewrite_input(), budget=_budget())
    assert result.status == HumanizerResultStatus.NEEDS_HUMAN
    assert result.output is not None
    assert result.output.final_text == bad["final_text"]  # 原草稿未被清空
    assert result.output.quality_status == HumanizerQualityStatus.WARN
    assert result.repair_attempts == 1
    assert result.quality_warnings


def test_soft_gate_repairs_at_most_once() -> None:
    """修复后仍不过：不循环重生成，交付正文 + 未完全满足项。"""
    bad = _bad_genre_output()
    adapter = _ProgrammableStructuredAdapter(sequence=[bad, bad])
    _, result = _run(service := _make_service(adapter), _rewrite_input(), budget=_budget())
    assert result.status == HumanizerResultStatus.NEEDS_HUMAN
    assert result.output is not None
    assert result.output.final_text == bad["final_text"]
    assert result.repair_attempts == 1
    assert adapter.calls == 2  # 恰好一次修复，无第三次调用
    assert any("类比" in warning for warning in result.quality_warnings)


def test_soft_gate_skips_repair_when_budget_exhausted() -> None:
    """预算耗尽（issue 06 总预算约束）：不修复，交付正文并说明预算内未修复。"""
    bad = _bad_genre_output()
    adapter = _ProgrammableStructuredAdapter(sequence=[bad])
    _, result = _run(service := _make_service(adapter), _rewrite_input(), budget=_budget(0))
    assert result.status == HumanizerResultStatus.NEEDS_HUMAN
    assert result.output is not None
    assert result.repair_attempts == 0
    assert adapter.calls == 1  # 预算不足绝不发起修复调用
    assert any("预算" in warning for warning in result.quality_warnings)


def test_draft_event_carries_final_text_before_result() -> None:
    """草稿事件：模型产出正文后立即下发（复核/修复前），正文永不清空。"""
    service = _make_service(_ProgrammableStructuredAdapter(output=_good_output()))
    events, result = _run(service, _rewrite_input())
    drafts = [e for e in events if e.kind == "draft"]
    assert drafts
    assert drafts[-1].draft_text == _COMPLIANT_FINAL
    result_index = next(i for i, e in enumerate(events) if e.kind == "result")
    assert all(i < result_index for i, e in enumerate(events) if e.kind == "draft")


def test_rewrite_records_source_attachment_ids() -> None:
    """改写解析成功的附件 ID 随输出持久化，与消息绑定一致。"""
    service = _make_service(_ProgrammableStructuredAdapter(output=_good_output()))
    service._attachments = _FakeAttachmentService(  # noqa: SLF001 - 测试直连
        filename="原文.txt",
        content="光合作用指的是植物把光能转化为化学能的过程。研究显示，在"
        "光照充足的条件下，水稻叶片的净光合速率约为 25 μmol·m⁻²·s⁻¹；"
        "当温度超过 35°C 时，速率会显著下降（Zhang et al., 2021）。"
        "你可以把光合作用比作植物的充电过程，但比喻到此为止。".encode(),
        media_type="text/plain",
    )
    skill_input = HumanizerSkillInput(
        skill_id="bridges-humanizer",
        contract=HumanizerTaskContract(
            path=HumanizerPath.REWRITE,
            genre=Genre.POPULAR_SCIENCE,
            attachment_ids=["obj-docx-1"],
        ),
    )
    _, result = _run(service, skill_input)
    assert result.output is not None
    assert result.output.source_attachment_ids == ["obj-docx-1"]
    # 原文进入引用清单（界面列出原文来源）
    assert any(ref.source_type == "attachment" for ref in result.references)
    assert any("文件：" in (ref.detail or "") for ref in result.references)


def test_rewrite_default_ignores_retrieval_without_round() -> None:
    """默认路径（未显式开启补充检索）：不产生任何知识库检索引用。"""
    service = _make_service(_ProgrammableStructuredAdapter(output=_good_output()))
    events = list(
        service.run_task(
            "acc-test",
            "conv-test",
            "msg-test",
            _rewrite_input(),
            _run_context(),
            retrieval_round=None,
        )
    )
    result = next(e.result for e in events if e.kind == "result")
    assert not any(ref.source_type == "retrieval" for ref in result.references)


def test_rewrite_explicit_retrieval_round_adds_citations() -> None:
    """显式开启补充检索（retrieval_round 非空）：检索引用仅作补充进入
    证据合同，绝不替代原文（改写原文仍来自用户材料）。"""
    citation = SimpleNamespace(
        page_number=3,
        section_title="插图",
        filename="补充材料.pdf",
        snippet="显式开启后的知识库补充材料",
    )
    retrieval_round = SimpleNamespace(citations=[citation])
    service = _make_service(_ProgrammableStructuredAdapter(output=_good_output()))
    events = list(
        service.run_task(
            "acc-test",
            "conv-test",
            "msg-test",
            _rewrite_input(),
            _run_context(),
            retrieval_round=retrieval_round,
        )
    )
    result = next(e.result for e in events if e.kind == "result")
    assert any(ref.source_type == "retrieval" for ref in result.references)
    assert any("补充材料.pdf" in (ref.label or "") for ref in result.references)


def test_fact_lock_conflict_message_includes_recovery_path() -> None:
    """硬门：阻止标记最终稿，错误投影含冲突字段与可操作恢复方式。"""
    violating = _good_output(_COMPLIANT_FINAL.replace("25 μmol·m⁻²·s⁻¹", "30 μmol·m⁻²·s⁻¹"))
    service = _make_service(_ProgrammableStructuredAdapter(output=violating))
    events, result = _run(service, _rewrite_input(), budget=_budget())
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "fact_lock_conflict"
    assert result.output is None  # 硬门：不交付违规版本（允许无 final text）
    assert result.fact_lock_check is not None
    assert result.fact_lock_check.blocking_conflicts
    assert "恢复方式" in (result.error_message or "")
    assert "重试" in (result.error_message or "")
    # 硬门不执行软门修复：修复只在无硬门冲突时触发
    drafts = [e.draft_text for e in events if e.kind == "draft"]
    assert drafts  # 草稿仍在（不删除），但结果卡明确停止交付


def test_explicit_knowledge_base_reference_uses_matching_account_citation() -> None:
    """自然语言引用知识库时只把同名账户材料切片送入人味化服务。"""
    service = _make_service(_ProgrammableStructuredAdapter(output=_good_output()))
    skill_input = HumanizerSkillInput(
        skill_id="bridges-humanizer",
        contract=HumanizerTaskContract(
            path=HumanizerPath.REWRITE,
            genre=Genre.POPULAR_SCIENCE,
            knowledge_base_reference="实验报告.md",
        ),
    )
    retrieval_round = SimpleNamespace(
        citations=[
            SimpleNamespace(
                filename="实验报告.md",
                page_number=1,
                section_title="摘要",
                snippet=_SOURCE,
            ),
            SimpleNamespace(
                filename="无关材料.md",
                page_number=1,
                section_title="正文",
                snippet="不应进入本次任务的材料。",
            ),
        ]
    )

    events = list(
        service.run_task(
            "acc-test",
            "conv-test",
            "msg-test",
            skill_input,
            _run_context(),
            retrieval_round=retrieval_round,
        )
    )
    result = next(event.result for event in events if event.kind == "result")
    assert result is not None
    assert result.status == HumanizerResultStatus.DONE
    assert result.references
    assert any(reference.label == "实验报告.md" for reference in result.references)


def test_unreadable_knowledge_base_reference_fails_before_model_call() -> None:
    adapter = _ProgrammableStructuredAdapter(output=_good_output())
    service = _make_service(adapter)
    skill_input = HumanizerSkillInput(
        skill_id="bridges-humanizer",
        contract=HumanizerTaskContract(
            path=HumanizerPath.REWRITE,
            genre=Genre.POPULAR_SCIENCE,
            knowledge_base_reference="另一账户.md",
        ),
    )

    events = list(
        service.run_task(
            "acc-test",
            "conv-test",
            "msg-test",
            skill_input,
            _run_context(),
            retrieval_round=SimpleNamespace(citations=[]),
        )
    )
    result = next(event.result for event in events if event.kind == "result")
    assert result is not None
    assert result.error_code == "knowledge_base_reference_unreadable"
    assert adapter.calls == 0


def test_ambiguous_knowledge_base_reference_fails_before_model_call() -> None:
    adapter = _ProgrammableStructuredAdapter(output=_good_output())
    service = _make_service(adapter)
    skill_input = HumanizerSkillInput(
        skill_id="bridges-humanizer",
        contract=HumanizerTaskContract(
            path=HumanizerPath.REWRITE,
            genre=Genre.POPULAR_SCIENCE,
            knowledge_base_reference="实验报告.md",
        ),
    )
    citations = [
        SimpleNamespace(filename="实验报告.md", snippet="第一份材料。"),
        SimpleNamespace(filename="实验报告.md", snippet="第二份材料。"),
    ]

    events = list(
        service.run_task(
            "acc-test",
            "conv-test",
            "msg-test",
            skill_input,
            _run_context(),
            retrieval_round=SimpleNamespace(citations=citations),
        )
    )
    result = next(event.result for event in events if event.kind == "result")
    assert result is not None
    assert result.error_code == "knowledge_base_reference_ambiguous"
    assert adapter.calls == 0
