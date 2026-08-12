"""表达任务契约新流程服务测试（人味化改造 Issue 04）。

使用假模型跑完整文章服务：契约版本校验在模型调用前拒绝、首稿一次调用、
保真硬门、表达审稿报告、程序生成审计信息与旧投影兼容；风格发现默认
只报警不阻止交付。
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from bridges.ai import CapabilityRegistry, ModelGateway
from bridges.ai.adapters import AdapterError, AdapterResult
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    RetryPolicy,
)
from bridges.contracts.expression_review import ReviewCode
from bridges.contracts.humanizer import (
    HumanizerEditKind,
    HumanizerQualityStatus,
    HumanizerResultStatus,
)
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
        retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=0.01),
    )


class _ProgrammableStructuredAdapter:
    """可编程结构化适配器：记录调用次数与载荷，返回预置输出。"""

    def __init__(self, output: dict[str, Any] | None = None) -> None:
        self._output = output or {}
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
        return AdapterResult(actual_model_id=capability.model_id, output=self._output)


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
    """通过真实自然语言路由构造带表达任务契约的 SKILL 输入。"""
    routed = route_humanizer_message(f"帮我改写这段话：{_SOURCE}")
    assert routed is not None
    return routed.skill_input


def _run(service: HumanizerService, skill_input: Any) -> tuple[list[Any], Any]:
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


def _draft_text(final: str) -> dict[str, Any]:
    return {"final_text": final}


def test_expression_path_invokes_model_exactly_once() -> None:
    adapter = _ProgrammableStructuredAdapter(
        _draft_text(f"这段讲时间管理。{_SOURCE}")
    )
    events, result = _run(_make_service(adapter), _expression_input())
    assert adapter.calls == 1  # 本 Issue 模型调用始终至多一次
    assert result.status == HumanizerResultStatus.DONE
    assert result.expression_contract is not None
    assert result.output is not None
    assert "25 分钟" in result.output.final_text
    # 旧投影字段兼容：contract/edits/fact_check/open_questions 由程序生成
    assert result.contract.path.value == "rewrite"
    assert result.output.edits and all(e.reason for e in result.output.edits)
    assert result.output.fact_check
    assert result.output.open_questions is not None
    assert result.fidelity_check is not None and result.fidelity_check.passed
    # 草稿事件携带正文
    drafts = [e.draft_text for e in events if e.kind == "draft"]
    assert drafts and drafts[-1] == result.output.final_text


def test_expression_prompt_compiles_current_profile_only() -> None:
    adapter = _ProgrammableStructuredAdapter(_draft_text(_SOURCE))
    service = _make_service(adapter)
    _run(service, _expression_input())
    system_prompt = adapter.payloads[0]["messages"][0]["content"]
    # 只含当前 profile 的 6—10 条规则，不含内部方法 ID 与体裁必现短语
    section = system_prompt.split("【本次写作要点】")[1].split("【")[0]
    rules = [line for line in section.splitlines() if line.strip().startswith("- ")]
    assert 6 <= len(rules) <= 10
    assert "rewrite.main-clause-first" not in system_prompt
    assert "humanizer-method-rules-v1" not in system_prompt
    assert "必含" not in system_prompt
    # 模型只收到正文为中心的 JSON 合同
    assert adapter.payloads[0]["json_schema"]["required"] == ["final_text"]
    # 说话位置与权限行
    assert "【说话位置】" in system_prompt
    assert "【读者下一问】" in system_prompt
    assert "第一人称：" in system_prompt


def test_unsupported_contract_version_rejected_before_model_call() -> None:
    skill_input = _expression_input()
    bad_contract = skill_input.expression_contract.model_copy(
        update={"schema_version": "expression-task-v99"}
    )
    skill_input = skill_input.model_copy(update={"expression_contract": bad_contract})
    adapter = _ProgrammableStructuredAdapter(_draft_text(_SOURCE))
    _, result = _run(_make_service(adapter), skill_input)
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "expression_contract_unsupported"
    assert adapter.calls == 0  # 模型调用前稳定拒绝


def test_contract_hash_mismatch_rejected_before_model_call() -> None:
    skill_input = _expression_input()
    bad_contract = skill_input.expression_contract.model_copy(
        update={"version_hash": "tampered"}
    )
    skill_input = skill_input.model_copy(update={"expression_contract": bad_contract})
    adapter = _ProgrammableStructuredAdapter(_draft_text(_SOURCE))
    _, result = _run(_make_service(adapter), skill_input)
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "expression_contract_hash_mismatch"
    assert adapter.calls == 0


def test_fidelity_gate_blocks_expression_path() -> None:
    violating = _draft_text(_SOURCE.replace("25 分钟", "35 分钟"))
    adapter = _ProgrammableStructuredAdapter(violating)
    _, result = _run(_make_service(adapter), _expression_input())
    # Issue 05：可修复保真问题触发一次定向修订；修订稿仍破坏事实时停止交付
    assert adapter.calls == 2
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "fidelity_gate_conflict"
    assert result.output is None
    assert result.writing_call_count == 2
    assert result.revision is not None
    assert result.revision.triggered is True
    assert result.revision.final_state == "stop_delivery"
    assert result.fidelity_check is not None
    assert result.fidelity_check.blocking_failures


def test_style_findings_are_soft_and_attach_report() -> None:
    templated = _draft_text(
        "本助手认为，总而言之，番茄工作法把时间切成 25 分钟的工作块"
        "和 5 分钟的休息块，四个工作块后休息 15 分钟。未来可期。"
    )
    adapter = _ProgrammableStructuredAdapter(templated)
    _, result = _run(_make_service(adapter), _expression_input())
    # 风格发现是软审稿：正文照常交付，附审稿报告与具体警告
    assert result.status == HumanizerResultStatus.DONE
    assert result.output is not None
    assert result.expression_review is not None
    assert not result.expression_review.no_change_recommended
    codes = {f.code.value for f in result.expression_review.findings}
    assert ReviewCode.ASSISTANT_IDENTITY_RESIDUE.value in codes
    assert ReviewCode.MECHANICAL_TRANSITION_CLOSING.value in codes
    assert result.output.quality_status == HumanizerQualityStatus.WARN
    assert result.quality_warnings
    # 修改清单由程序从审稿发现生成，不伪装成模型自证
    assert result.output.edits
    assert any(e.kind in (HumanizerEditKind.WORD_CHOICE, HumanizerEditKind.REWRITE)
               for e in result.output.edits)


def test_natural_output_reports_no_change() -> None:
    natural = _draft_text(
        "把时间切成 25 分钟的工作块和 5 分钟的休息块，四个工作块后"
        "休息 15 分钟，这就是番茄工作法的大致框架。"
    )
    adapter = _ProgrammableStructuredAdapter(natural)
    _, result = _run(_make_service(adapter), _expression_input())
    assert result.status == HumanizerResultStatus.DONE
    assert result.expression_review is not None
    assert result.expression_review.no_change_recommended is True
    assert result.output is not None
    assert result.output.quality_status == HumanizerQualityStatus.OK
    assert not result.quality_warnings
    assert result.output.edits[0].kind == HumanizerEditKind.NO_CHANGE


def test_unauthorized_first_person_blocked_by_fidelity_gate() -> None:
    fabricated = _draft_text(
        "我去年靠番茄钟坚持了三十天，把时间切成 25 分钟的工作块和"
        "5 分钟的休息块，四个工作块后休息 15 分钟。"
    )
    adapter = _ProgrammableStructuredAdapter(fabricated)
    _, result = _run(_make_service(adapter), _expression_input())
    # 原文无第一人称、用户未授权 → Issue 02 保真硬门先拦截虚构亲历
    assert result.status == HumanizerResultStatus.ERROR
    assert result.error_code == "fidelity_gate_conflict"
    # 虚构亲历（「我去年」时间地点）被账本识别为无来源 claim 并拦截
    assert result.fidelity_check is not None
    assert any("去年" in f.note for f in result.fidelity_check.blocking_failures)
    assert result.output is None


def test_info_only_findings_report_no_change() -> None:
    """只有 info 级观察时：no_change_recommended 与 edits NO_CHANGE 一致。"""
    from bridges.contracts.humanizer import HumanizerEditKind

    # 三句等长构成 uniform_length（INFO 级），无 warning/suggestion 级发现；
    # 保留原文数字以免触发保真硬门
    info_only = _draft_text(
        "工作块切 25 分钟。休息块切 5 分钟。四块后休息 15 分钟。"
    )
    adapter = _ProgrammableStructuredAdapter(info_only)
    _, result = _run(_make_service(adapter), _expression_input())
    assert result.status == HumanizerResultStatus.DONE
    assert result.expression_review is not None
    assert result.expression_review.no_change_recommended is True
    assert result.output is not None
    assert result.output.quality_status == HumanizerQualityStatus.OK
    assert result.output.edits and result.output.edits[0].kind == HumanizerEditKind.NO_CHANGE


def test_open_questions_come_from_fidelity_confirmation() -> None:
    # 无保真待确认项时 open_questions 为空列表（字段恒存在）
    adapter = _ProgrammableStructuredAdapter(_draft_text(_SOURCE))
    _, result = _run(_make_service(adapter), _expression_input())
    assert result.output is not None
    assert result.output.open_questions == []


def test_generate_without_contract_keeps_legacy_path() -> None:
    """旧显式 SKILL 生成路径（无表达契约）在 Issue 08 前保持兼容。

    生成路径无授权材料时新增可核查 claim 被 Issue 02 保真硬门拦截是
    既有正确行为；本测试只验证旧路径不走新流程字段。
    """
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
    adapter = _ProgrammableStructuredAdapter(
        {
            "final_text": "时间管理的关键是先列清单，再排优先级。",
            "edits": [
                {
                    "original": "先列清单",
                    "revised": "先列清单",
                    "kind": "no_change",
                    "reason": "原文已自然。",
                }
            ],
            "fact_check": [],
            "open_questions": [],
        }
    )
    _, result = _run(_make_service(adapter), skill_input)
    assert adapter.calls == 1
    assert result.status == HumanizerResultStatus.DONE
    assert result.expression_contract is None
    assert result.expression_review is None  # 旧流程无表达审稿字段
