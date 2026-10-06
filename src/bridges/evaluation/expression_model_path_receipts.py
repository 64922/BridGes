"""工单 39：正式路径真实执行收据编排（聊天、长任务、复合与运行器）。

模型相关路径的失败以 failed 收据如实记录；结构化输出偶发解析失败由
评测侧的 `RetryingStructuredGateway` 有界重试，重试记录随收据公开。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bridges.evaluation.expression_generator_receipts import (
    run_github_receipt,
    run_paper_receipt,
)
from bridges.evaluation.expression_path_receipts import (
    build_receipt,
    serialize_locks,
)
from bridges.evaluation.expression_policy_arms import StrategyArm
from bridges.evaluation.expression_real_run import ArmRunResult, RealArmSender
from bridges.evaluation.expression_spec import ExpressionCategory, ExpressionScenario
from bridges.evaluation.expression_study_receipts import (
    run_study_review_receipt,
    run_study_scope_receipt,
    run_study_summary_receipt,
    run_study_tutoring_receipt,
)

CHAT_PROBE = ExpressionScenario(
    scenario_id="receipt-chat-companion",
    title="正式路径收据：普通聊天",
    category=ExpressionCategory.FORMAL_PATH,
    formal_path="chat.companion",
    turns=("用一句话解释浮点数为什么会有精度误差。",),
    real_runnable=False,
    notes="正式路径真实执行收据，不进入主配对语料。",
)

STUDY_PROBE = ExpressionScenario(
    scenario_id="receipt-chat-study",
    title="正式路径收据：学习模式普通聊天",
    category=ExpressionCategory.FORMAL_PATH,
    formal_path="chat.study",
    turns=("明天再继续，今天先到这里。",),
    mode="study",
    real_runnable=False,
    notes="正式路径真实执行收据，不进入主配对语料。",
)


def _transcript_text(result: ArmRunResult) -> str:
    lines: list[str] = []
    for turn in result.transcript.turns:
        lines.append(f"用户：{turn.user}")
        lines.append(f"助手：{turn.assistant}")
    return "\n".join(lines)


def _seeded_study_sender(gateway: Any) -> RealArmSender:
    from bridges.evaluation.expression_study_receipts import _seed_study_state

    return RealArmSender(gateway, conversation_seed=_seed_study_state)


def _run_receipt(path_id: str, gateway: Any, scenario: ExpressionScenario) -> dict[str, Any]:
    sender = (
        _seeded_study_sender(gateway) if scenario.mode == "study" else RealArmSender(gateway)
    )
    result = sender.run(scenario, StrategyArm.CURRENT)
    failed_turns = [
        measurement
        for measurement in result.measurements
        if measurement.status != "done"
    ]
    empty_turns = [
        turn
        for turn in result.transcript.turns
        if not (turn.assistant or "").strip()
    ]
    receipt = build_receipt(
        path_id,
        execution_kind="real_chat_call",
        output=_transcript_text(result),
        model_locks=serialize_locks(result.locks),
        notes=f"场景 {scenario.scenario_id}；候选臂真实模型调用。",
    )
    if failed_turns or empty_turns:
        errors = [
            f"回合 {item.turn_index} {item.status}/{item.error_code}"
            for item in failed_turns
        ]
        if empty_turns:
            errors.append(f"回合回答为空（共 {len(empty_turns)} 个）")
        receipt["status"] = "failed"
        receipt["error"] = "；".join(errors)
    return receipt


def run_long_task_receipts(gateway: Any) -> list[dict[str, Any]]:
    """修复后的长任务场景真实执行（明确对象/方法且长度可完成）。"""

    from bridges.evaluation.expression_corpus import SCENARIOS

    receipts: list[dict[str, Any]] = []
    sender = RealArmSender(gateway)
    for scenario_id in ("long-derivation", "long-comparison"):
        scenario = next(s for s in SCENARIOS if s.scenario_id == scenario_id)
        result = sender.run(scenario, StrategyArm.CURRENT)
        failed_turns = [
            measurement
            for measurement in result.measurements
            if measurement.status != "done"
        ]
        empty_turns = [
            turn
            for turn in result.transcript.turns
            if not (turn.assistant or "").strip()
        ]
        receipt = {
            "scenario_id": scenario_id,
            "title": scenario.title,
            "status": "executed" if not failed_turns and not empty_turns else "failed",
            "turns": list(scenario.turns),
            "transcript": _transcript_text(result),
            "measurements": [item.to_dict() for item in result.measurements],
            "model_locks": serialize_locks(result.locks),
        }
        if failed_turns or empty_turns:
            errors = [
                f"回合 {item.turn_index} {item.status}/{item.error_code}"
                for item in failed_turns
            ]
            if empty_turns:
                errors.append(f"空回答回合 {len(empty_turns)} 个")
            receipt["error"] = "；".join(errors)
        receipts.append(receipt)
    return receipts


def run_composite_receipt() -> dict[str, Any]:
    from bridges.orchestration.contracts import (
        CompositeOutcome,
        CompositePlan,
        CompositeStatus,
        StepResult,
        StepState,
    )
    from bridges.orchestration.production import render_final_content
    from bridges.orchestration.synthesis import Synthesizer

    plan = CompositePlan(
        plan_id="receipt-plan",
        goal="先找论文再找 GitHub 项目，最后给综合建议",
        user_message_id="receipt-message",
        created_at=datetime.now(UTC),
    )
    step = StepResult(
        step_id="step-paper",
        module_id="paper",
        state=StepState.COMPLETED,
        trust_state="qualified",
        summary="论文概述：给出线性代数入门综述与阅读顺序。",
        evidence_refs=["arxiv:2401.00001"],
    )
    outcome = CompositeOutcome(
        status=CompositeStatus.COMPLETED,
        plan=plan,
        steps=[step],
        created_at=datetime.now(UTC),
    )
    draft = Synthesizer().build(outcome)
    content = render_final_content(draft)
    return build_receipt(
        "composite",
        execution_kind="composite_synthesis_render",
        output=content,
        notes=(
            "真实执行复合计划的确定性综合与最终渲染；承载模型的 paper/github "
            "步骤另有独立真实生成收据。"
        ),
    )


#: 结构化输出偶发解析失败属模型侧瞬时格式问题；证据执行有界重试并记录次数。
MAX_RECEIPT_ATTEMPTS = 4


def run_model_path_receipts(gateway: Any, quota: Any) -> list[dict[str, Any]]:
    """逐条执行模型相关正式路径；失败以 failed 收据如实记录，不隐藏。

    每条路径最多尝试 ``MAX_RECEIPT_ATTEMPTS`` 次：抛出异常或收据未达到
    ``executed``（如生成器降级返回）都进入下一次尝试；最终仍失败时保留
    最后一次收据的真实锁与输出，供审计失败原因。
    """

    runners: list[tuple[str, Any]] = [
        ("chat.companion", lambda: _run_receipt("chat.companion", gateway, CHAT_PROBE)),
        ("chat.study", lambda: _run_receipt("chat.study", gateway, STUDY_PROBE)),
        ("study.tutoring", lambda: run_study_tutoring_receipt(gateway)),
        ("study.review", lambda: run_study_review_receipt(gateway)),
        ("study.summary", lambda: run_study_summary_receipt(gateway)),
        ("study.scope", lambda: run_study_scope_receipt(gateway, quota)),
        ("paper.summary", lambda: run_paper_receipt(gateway, quota)),
        ("github.insights", lambda: run_github_receipt(gateway, quota)),
        ("composite", run_composite_receipt),
    ]
    receipts: list[dict[str, Any]] = []
    for path_id, runner in runners:
        errors: list[str] = []
        last_receipt: dict[str, Any] | None = None
        for attempt in range(1, MAX_RECEIPT_ATTEMPTS + 1):
            try:
                receipt = runner()
            except Exception as exc:  # noqa: BLE001 - 失败必须进入收据
                errors.append(f"{type(exc).__name__}: {exc}")
                continue
            receipt["attempts"] = attempt
            if receipt.get("status") == "executed":
                if errors:
                    receipt["notes"] = (
                        receipt.get("notes", "")
                        + f" 早前 {len(errors)} 次尝试失败："
                        + "; ".join(errors)
                    ).strip()
                receipts.append(receipt)
                break
            last_receipt = receipt
            errors.append(
                f"尝试 {attempt} 未完成："
                f"{receipt.get('notes') or receipt.get('error') or '未达到 executed'}"
            )
        else:
            if last_receipt is None:
                last_receipt = build_receipt(
                    path_id, execution_kind="failed", output=""
                )
                last_receipt["status"] = "failed"
            last_receipt["attempts"] = MAX_RECEIPT_ATTEMPTS
            last_receipt["error"] = "; ".join(errors)
            receipts.append(last_receipt)
    return receipts


__all__ = [
    "CHAT_PROBE",
    "MAX_RECEIPT_ATTEMPTS",
    "STUDY_PROBE",
    "run_composite_receipt",
    "run_long_task_receipts",
    "run_model_path_receipts",
]
