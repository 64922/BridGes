"""工单 37 复合计划与综合核验的确定性机制测试。

覆盖验收标准：论文+资料并行且一总预算；身份未确认不得宣称对应实现；
岗位组合不把私人简历发公网；换城市只重算条件相关步骤并复用公共材料；
支持不足的关键结论被最终门阻止、普通工具不强制模型裁判；可选失败保留
有效部分、必要失败阻塞相关结论；非法循环/未登记能力/硬限制绕过/第二轮
调整/旧版本迟到写入均被拒绝。
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from bridges.chat.run_budget_ledger import (
    RunBudgetClass,
    RunBudgetLedgerRepository,
    derive_run_budget_plan,
)
from bridges.contracts.understanding import HardCondition, HardConditionKind
from bridges.kernel.guard import CommitDecision
from bridges.orchestration.contracts import (
    Claim,
    CompositeStatus,
    CompositeStep,
    DataClassification,
    IndependentVerification,
    ParameterBinding,
    ParameterSource,
    PlanViolationCode,
    StepFailure,
    StepResult,
    StepState,
    SynthesisDraft,
    SynthesisSection,
    VerificationSource,
    VerificationTrigger,
    VerificationVerdict,
)
from bridges.orchestration.executor import (
    CompositeBudget,
    CompositeExecutor,
    FunctionStepRunner,
    StepRunContext,
)
from bridges.orchestration.planner import CompositePlanner
from bridges.orchestration.synthesis import FinalGate, Synthesizer
from bridges.storage.database import BridgesDatabase

ACCOUNT = "acc-37"
CONVERSATION = "conv-37"
RUN = "run-37"
ASSISTANT = "msg-assistant-37"
USER = "msg-user-37"
RESUME_TEXT = "张三的私人简历：曾在某公司实习，手机 13800000000"
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


@pytest.fixture
def database(tmp_path: Path) -> BridgesDatabase:
    db = BridgesDatabase(tmp_path / "bridges.db")
    assert db.initialize() > 0
    return db


def _seed(database: BridgesDatabase) -> None:
    stamp = NOW.isoformat()
    with database.transaction():
        database.connection.execute(
            "INSERT OR IGNORE INTO conversations"
            "(conversation_id, account_id, title, mode, created_at, updated_at)"
            " VALUES (?, ?, '', 'companion', ?, ?)",
            (CONVERSATION, ACCOUNT, stamp, stamp),
        )
        database.connection.execute(
            "INSERT OR IGNORE INTO messages"
            "(message_id, conversation_id, account_id, role, status, content,"
            " created_at, updated_at)"
            " VALUES (?, ?, ?, 'assistant', 'streaming', '', ?, ?)",
            (ASSISTANT, CONVERSATION, ACCOUNT, stamp, stamp),
        )
        database.connection.execute(
            "INSERT OR IGNORE INTO generation_runs"
            "(run_id, account_id, conversation_id, user_message_id,"
            " assistant_message_id, status, lease_owner, lease_expires_at,"
            " stop_requested, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 'running', 'worker-37', ?, 0, ?, ?)",
            (
                RUN,
                ACCOUNT,
                CONVERSATION,
                USER,
                ASSISTANT,
                (NOW + timedelta(minutes=5)).isoformat(),
                stamp,
                stamp,
            ),
        )


def _budget(database: BridgesDatabase) -> CompositeBudget:
    ledger = RunBudgetLedgerRepository(database)
    plan = derive_run_budget_plan(
        RunBudgetClass.NORMAL, deadline_at=NOW + timedelta(minutes=5)
    )
    ledger.freeze_for_run(
        account_id=ACCOUNT,
        run_id=RUN,
        conversation_id=CONVERSATION,
        plan=plan,
        now=NOW,
    )
    return CompositeBudget(
        ledger, account_id=ACCOUNT, run_id=RUN, clock=lambda: NOW
    )


def _context(**overrides: Any) -> StepRunContext:
    defaults: dict[str, Any] = {
        "account_id": ACCOUNT,
        "conversation_id": CONVERSATION,
        "run_id": RUN,
        "user_message_id": USER,
        "assistant_message_id": ASSISTANT,
        "goal": "找入门论文和学习资料",
    }
    defaults.update(overrides)
    return StepRunContext(**defaults)


def _completed(
    step: CompositeStep,
    *,
    summary: str,
    claims: Sequence[Claim] = (),
    evidence: Sequence[str] = (),
    unconfirmed: Sequence[str] = (),
) -> StepResult:
    return StepResult(
        step_id=step.step_id,
        module_id=step.module_id,
        state=StepState.COMPLETED,
        trust_state="qualified",
        summary=summary,
        claims=list(claims),
        evidence_refs=list(evidence),
        unconfirmed=list(unconfirmed),
        artifact_refs={f"{step.module_id}.verify": f"art-{step.module_id}"},
        read_scope="摘要级",
    )


class _RecordingRunner:
    """记录解析与执行调用的确定性步骤执行体。"""

    def __init__(
        self,
        *,
        module_id: str,
        resolver=None,
        executor=None,
        barrier: threading.Barrier | None = None,
    ) -> None:
        self.module_id = module_id
        self.resolve_calls: list[Mapping[str, Any]] = []
        self.run_calls: list[Mapping[str, Any]] = []
        self._resolver = resolver or (lambda step, context: {"topic": context.goal})
        self._executor = executor
        self._barrier = barrier

    def resolve(self, step: CompositeStep, context: StepRunContext) -> Mapping[str, Any]:
        resolved = self._resolver(step, context)
        self.resolve_calls.append(dict(resolved))
        return resolved

    def run(
        self,
        step: CompositeStep,
        context: StepRunContext,
        resolved: Mapping[str, Any],
        remaining_budget_ms: int | None,
    ) -> StepResult:
        self.run_calls.append(dict(resolved))
        if self._barrier is not None:
            self._barrier.wait(timeout=5)
        if self._executor is not None:
            return self._executor(step, context, resolved)
        return _completed(step, summary=f"{self.module_id} 完成")


# ---------------------------------------------------------------------------
# 验收 1：论文+资料并行、一总预算、身份未确认不宣称实现
# ---------------------------------------------------------------------------


def test_paper_resources_run_in_parallel_with_one_shared_budget(
    database: BridgesDatabase,
) -> None:
    _seed(database)
    budget = _budget(database)
    planner = CompositePlanner()
    plan = planner.plan(
        goal="找入门论文和学习资料",
        user_message_id=USER,
        module_ids=["paper", "resources"],
        now=NOW,
    )
    assert plan is not None
    assert plan.step("paper").depends_on == []
    assert plan.step("resources").depends_on == []

    barrier = threading.Barrier(2)
    paper = _RecordingRunner(module_id="paper", barrier=barrier)
    resources = _RecordingRunner(module_id="resources", barrier=barrier)
    executor = CompositeExecutor(planner=planner, budget=budget, clock=lambda: NOW)

    outcome = executor.execute(
        plan=plan,
        runners={"paper": paper, "resources": resources},
        context=_context(),
    )

    assert outcome.status is CompositeStatus.COMPLETED
    assert {step.step_id for step in outcome.steps} == {"paper", "resources"}
    assert len(paper.run_calls) == 1 and len(resources.run_calls) == 1
    snapshot = budget.snapshot()
    assert snapshot is not None and snapshot.active
    assert snapshot.plan.adjustment_rounds_max == 1
    assert snapshot.adjustment_rounds_used == 0


def test_github_cannot_claim_correspondence_when_paper_identity_unconfirmed(
    database: BridgesDatabase,
) -> None:
    _seed(database)
    planner = CompositePlanner()
    plan = planner.plan(
        goal="第二篇论文有没有对应实现",
        user_message_id=USER,
        module_ids=["paper", "github"],
        now=NOW,
    )
    assert plan is not None
    github_step = plan.step("github")
    assert github_step.identity_required is True
    assert github_step.identity_source_step == "paper"
    assert github_step.depends_on == ["paper"]
    order: list[str] = []

    def paper_run(
        step: CompositeStep, context: StepRunContext, resolved, remaining_ms=None
    ):
        order.append(step.step_id)
        # 论文身份尚未确认：结论必须带限定条件，不得声称实现对应。
        return _completed(
            step,
            summary="检索到候选论文，尚未确认你要的是哪一篇。",
            claims=[
                Claim(
                    text="候选论文列表已取得",
                    evidence_refs=["art-paper"],
                    qualification="论文身份未确认，不能据此宣称仓库对应实现。",
                )
            ],
            evidence=["art-paper"],
            unconfirmed=["论文身份未确认"],
        )

    def github_run(
        step: CompositeStep, context: StepRunContext, resolved, remaining_ms=None
    ):
        assert "paper" in context.upstream
        paper_result = context.upstream["paper"]
        assert paper_result.state is StepState.COMPLETED
        assert "论文身份未确认" in paper_result.unconfirmed
        order.append(step.step_id)
        fingerprint = str(resolved.get("identity_confirmed"))
        assert fingerprint == "False"
        return _completed(
            step,
            summary="只在已确认身份后才会说明仓库对应关系。",
            claims=[
                Claim(
                    text="候选仓库只按通用需求筛选",
                    evidence_refs=["art-github"],
                    qualification="未确认论文身份，不断言对应实现。",
                )
            ],
            evidence=["art-github"],
            unconfirmed=["论文身份未确认"],
        )

    def github_resolve(step: CompositeStep, context: StepRunContext):
        upstream = context.upstream.get("paper")
        confirmed = bool(
            upstream is not None
            and upstream.delivery is not None
            and "论文身份未确认" not in upstream.unconfirmed
        )
        return {"topic": "论文实现", "identity_confirmed": confirmed}

    executor = CompositeExecutor(planner=planner, clock=lambda: NOW)
    outcome = executor.execute(
        plan=plan,
        runners={
            "paper": FunctionStepRunner(
                resolve=lambda step, context: {"topic": "论文实现"},
                run=paper_run,
            ),
            "github": FunctionStepRunner(resolve=github_resolve, run=github_run),
        },
        context=_context(goal="第二篇论文有没有对应实现"),
    )
    assert order == ["paper", "github"]
    assert outcome.status is CompositeStatus.COMPLETED

    broken = plan.step("github").model_copy(update={"depends_on": []})
    broken_plan = plan.model_copy(
        update={"steps": [plan.step("paper"), broken]}
    )
    validation = planner.validate(broken_plan)
    assert not validation.ok
    assert PlanViolationCode.SKIPPED_PREREQUISITE.value in validation.codes()


# ---------------------------------------------------------------------------
# 验收 2：岗位组合不把私人简历发公网
# ---------------------------------------------------------------------------


def test_career_combo_never_sends_private_resume_to_public_steps(
    database: BridgesDatabase,
) -> None:
    _seed(database)
    planner = CompositePlanner()
    plan = planner.plan(
        goal="准备 Java 后端实习，给学习资料和练手项目",
        user_message_id=USER,
        module_ids=["career", "resources", "github"],
        now=NOW,
    )
    assert plan is not None
    assert plan.step("resources").depends_on == ["career"]
    assert plan.step("github").depends_on == ["career"]
    career_binding = {
        item.name: item for item in plan.step("career").parameter_bindings
    }
    assert career_binding["background"].classification.value == "private"
    assert career_binding["background"].leaves_device is False

    def career_resolve(step: CompositeStep, context: StepRunContext):
        return {"job_terms": "Java 后端实习", "city": "上海", "background": RESUME_TEXT}

    def career_run(step, context, resolved):
        assert resolved["background"] == RESUME_TEXT  # 本地步骤可以使用
        return _completed(
            step,
            summary="岗位样本与差距分析完成。",
            claims=[
                Claim(
                    text="样本高频要求：Spring Boot、MySQL",
                    evidence_refs=["art-career"],
                    risk="personal_gap",
                )
            ],
            evidence=["art-career"],
        )

    def public_resolve(step: CompositeStep, context: StepRunContext):
        upstream = context.upstream["career"]
        return {
            "topic": "Java 后端实习",
            "requirements": ["Spring Boot", "MySQL"],
            "source_step": upstream.step_id,
        }

    def public_run(step, context, resolved):
        # 公网步骤只接收最小查询参数，绝不携带简历原文。
        assert RESUME_TEXT not in str(resolved)
        return _completed(
            step,
            summary=f"{step.step_id} 按最小查询参数完成。",
            evidence=[f"art-{step.module_id}"],
        )

    career = _RecordingRunner(
        module_id="career", resolver=career_resolve, executor=career_run
    )
    resources = _RecordingRunner(
        module_id="resources", resolver=public_resolve, executor=public_run
    )
    github = _RecordingRunner(
        module_id="github", resolver=public_resolve, executor=public_run
    )
    executor = CompositeExecutor(planner=planner, clock=lambda: NOW)
    outcome = executor.execute(
        plan=plan,
        runners={"career": career, "resources": resources, "github": github},
        context=_context(goal="准备 Java 后端实习"),
    )
    assert outcome.status is CompositeStatus.COMPLETED
    assert not any(RESUME_TEXT in str(call) for call in resources.run_calls)
    assert not any(RESUME_TEXT in str(call) for call in github.run_calls)

    # 恶意计划：把简历标成离设备参数送入公网步骤，必须被硬条件校验拒绝。
    bad_step = plan.step("career").model_copy(
        update={
            "parameter_bindings": [
                ParameterBinding(
                    name="resume",
                    source=ParameterSource.USER_MESSAGE,
                    source_ref=USER,
                    classification=DataClassification.PRIVATE,
                    leaves_device=True,
                )
            ],
            "public_network": True,
        }
    )
    validation = planner.validate(plan.model_copy(update={"steps": [bad_step]}))
    assert not validation.ok
    assert PlanViolationCode.HARD_CONDITION_BYPASS.value in validation.codes()


# ---------------------------------------------------------------------------
# 验收 3：换城市只重算条件相关步骤，公共材料复用
# ---------------------------------------------------------------------------


def test_city_change_recomputes_career_but_reuses_resources(
    database: BridgesDatabase,
) -> None:
    _seed(database)
    planner = CompositePlanner()
    plan = planner.plan(
        goal="Java 后端实习，先看岗位再推荐资料",
        user_message_id=USER,
        module_ids=["career", "resources"],
        now=NOW,
    )
    assert plan is not None

    state = {"city": "上海"}

    def career_resolve(step: CompositeStep, context: StepRunContext):
        return {"job_terms": "Java 后端实习", "city": state["city"]}

    def career_run(step, context, resolved):
        return _completed(step, summary="岗位分析完成", evidence=["art-career"])

    def resources_resolve(step: CompositeStep, context: StepRunContext):
        upstream = context.upstream["career"]
        assert upstream.state is StepState.COMPLETED
        return {"topic": "Java 后端实习"}

    def resources_run(step, context, resolved):
        return _completed(step, summary="资料路径完成", evidence=["art-resources"])

    career = _RecordingRunner(
        module_id="career", resolver=career_resolve, executor=career_run
    )
    resources = _RecordingRunner(
        module_id="resources", resolver=resources_resolve, executor=resources_run
    )
    executor = CompositeExecutor(planner=planner, clock=lambda: NOW)
    first = executor.execute(
        plan=plan,
        runners={"career": career, "resources": resources},
        context=_context(goal=plan.goal),
    )
    assert first.status is CompositeStatus.COMPLETED

    state["city"] = "杭州"
    second = executor.execute(
        plan=plan,
        runners={"career": career, "resources": resources},
        context=_context(goal=plan.goal),
        prior_results={step.step_id: step for step in first.steps},
        changed_conditions=["city"],
    )
    assert second.status is CompositeStatus.COMPLETED
    assert second.step("career").reused is False
    assert second.step("resources").reused is True
    assert len(career.run_calls) == 2
    assert len(resources.run_calls) == 1


def test_unrelated_step_reuses_without_changes(database: BridgesDatabase) -> None:
    _seed(database)
    planner = CompositePlanner()
    plan = planner.plan(
        goal="找论文和资料",
        user_message_id=USER,
        module_ids=["paper", "resources"],
        now=NOW,
    )
    assert plan is not None
    paper = _RecordingRunner(module_id="paper")
    resources = _RecordingRunner(module_id="resources")
    executor = CompositeExecutor(planner=planner, clock=lambda: NOW)
    first = executor.execute(
        plan=plan,
        runners={"paper": paper, "resources": resources},
        context=_context(goal=plan.goal),
    )
    second = executor.execute(
        plan=plan,
        runners={"paper": paper, "resources": resources},
        context=_context(goal=plan.goal),
        prior_results={step.step_id: step for step in first.steps},
    )
    assert all(step.reused for step in second.steps)
    assert len(paper.run_calls) == 1 and len(resources.run_calls) == 1


# ---------------------------------------------------------------------------
# 验收 4：支持不足被门阻止；普通工具不强制模型裁判
# ---------------------------------------------------------------------------


def _step_result_with_claim(
    *,
    claim: Claim,
    evidence: Sequence[str] = (),
    unconfirmed: Sequence[str] = (),
) -> StepResult:
    return StepResult(
        step_id="paper",
        module_id="paper",
        state=StepState.COMPLETED,
        trust_state="qualified",
        summary="论文结论",
        claims=[claim],
        evidence_refs=list(evidence),
        unconfirmed=list(unconfirmed),
    )


def test_unsupported_claim_blocked_by_final_gate() -> None:
    step = _step_result_with_claim(claim=Claim(text="该方法最新", evidence_refs=[]))
    draft = SynthesisDraft(
        summary="总结",
        sections=[
            SynthesisSection(
                title="论文",
                body=step.summary,
                step_ids=["paper"],
                evidence_refs=[],
            )
        ],
    )
    gate = FinalGate().check(draft, [step])
    assert not gate.passed
    assert gate.unsupported_claims == ["该方法最新"]


def test_plain_tool_does_not_require_model_judge() -> None:
    class _NeverCalled:
        def verify(self, **kwargs: Any) -> IndependentVerification:  # pragma: no cover
            raise AssertionError("普通工具不应触发模型裁判")

    step = _step_result_with_claim(
        claim=Claim(
            text="5000 米",
            evidence_refs=["art-calc"],
            risk=VerificationTrigger.DETERMINISTIC_TOOL.value,
        ),
        evidence=["art-calc"],
    )
    draft = SynthesisDraft(
        summary="换算结果",
        sections=[
            SynthesisSection(
                title="工具",
                body="5000 米",
                step_ids=["paper"],
                evidence_refs=["art-calc"],
            )
        ],
    )
    gate = FinalGate(verifier=_NeverCalled()).check(draft, [step])
    assert gate.passed
    assert gate.verifications[0].required is False
    assert gate.verifications[0].source is VerificationSource.DETERMINISTIC_CODE


def test_personal_gap_requires_independent_verification_and_structure() -> None:
    calls: list[dict[str, Any]] = []

    class _Verifier:
        def __init__(self, verdict: VerificationVerdict, source: VerificationSource) -> None:
            self.verdict = verdict
            self.source = source

        def verify(self, **kwargs: Any) -> IndependentVerification:
            calls.append(kwargs)
            return IndependentVerification(
                required=True,
                trigger=kwargs["trigger"],
                verdict=self.verdict,
                source=self.source,
                independent_fact_source=self.source is VerificationSource.INDEPENDENT_MODEL,
                rules=["岗位要求必须由样本原文支持"],
                evidence_refs=list(kwargs["evidence"]),
                note="只看结论、原证据与规则",
            )

    step = _step_result_with_claim(
        claim=Claim(
            text="你缺少 Spring Boot 证据",
            evidence_refs=["art-gap"],
            risk=VerificationTrigger.PERSONAL_GAP.value,
        ),
        evidence=["art-gap"],
    )
    draft = SynthesisDraft(
        summary="差距",
        sections=[
            SynthesisSection(
                title="职业",
                body="差距",
                step_ids=["paper"],
                evidence_refs=["art-gap"],
            )
        ],
    )
    same_model = FinalGate(
        verifier=_Verifier(
            VerificationVerdict.PASS, VerificationSource.SAME_MODEL_INDEPENDENT_CALL
        )
    ).check(draft, [step])
    assert calls and "defense" not in calls[0]
    assert same_model.verifications[0].independent_fact_source is False
    assert same_model.verifications[0].source is VerificationSource.SAME_MODEL_INDEPENDENT_CALL

    blocked = FinalGate(
        verifier=_Verifier(VerificationVerdict.BLOCK, VerificationSource.INDEPENDENT_MODEL)
    ).check(draft, [step])
    assert not blocked.passed
    assert "verification_blocked" in blocked.code


def test_missing_qualification_blocks_gate() -> None:
    step = _step_result_with_claim(
        claim=Claim(text="薪资中位数", evidence_refs=["art-pay"]),
        evidence=["art-pay"],
        unconfirmed=["薪资单位未经核对"],
    )
    draft = SynthesisDraft(
        summary="岗位",
        sections=[
            SynthesisSection(
                title="职业",
                body="薪资中位数",
                step_ids=["paper"],
                evidence_refs=["art-pay"],
            )
        ],
    )
    gate = FinalGate().check(draft, [step])
    assert not gate.passed
    assert gate.missing_qualifications == ["薪资单位未经核对"]


# ---------------------------------------------------------------------------
# 验收 5：可选失败保留有效部分；必要失败阻塞相关结论
# ---------------------------------------------------------------------------


def test_optional_branch_failure_keeps_valid_parts(database: BridgesDatabase) -> None:
    _seed(database)
    planner = CompositePlanner()
    plan = planner.plan(
        goal="找论文和资料",
        user_message_id=USER,
        module_ids=["paper", "resources"],
        now=NOW,
    )
    assert plan is not None
    paper = _RecordingRunner(module_id="paper")
    resources = _RecordingRunner(
        module_id="resources",
        executor=lambda step, context, resolved: StepResult(
            step_id=step.step_id,
            module_id=step.module_id,
            state=StepState.FAILED,
            trust_state="invalidated",
            blocked_reason="视频来源失败，书目部分仍可用但本轮未交付。",
            failure=StepFailure(
                code="resources_search_failed",
                message="视频来源失败",
                retryable=False,
            ),
        ),
    )
    executor = CompositeExecutor(planner=planner, clock=lambda: NOW)
    outcome = executor.execute(
        plan=plan, runners={"paper": paper, "resources": resources}, context=_context()
    )
    assert outcome.status is CompositeStatus.PARTIAL
    assert outcome.delivered_steps == ["paper"]
    assert any("resources" in item for item in outcome.blocked_conclusions)
    draft = Synthesizer().build(outcome)
    assert any("学习资料" in item for item in draft.limitations)
    assert draft.sections and draft.sections[0].step_ids == ["paper"]


def test_required_failure_blocks_dependent_conclusions(
    database: BridgesDatabase,
) -> None:
    _seed(database)
    planner = CompositePlanner()
    plan = planner.plan(
        goal="第二篇论文的对应实现",
        user_message_id=USER,
        module_ids=["paper", "github"],
        now=NOW,
    )
    assert plan is not None
    paper = _RecordingRunner(
        module_id="paper",
        executor=lambda step, context, resolved: StepResult(
            step_id=step.step_id,
            module_id=step.module_id,
            state=StepState.FAILED,
            blocked_reason="论文检索不可用。",
            failure=StepFailure(code="paper_failed", message="论文检索不可用", retryable=False),
        ),
    )
    github = _RecordingRunner(module_id="github")
    executor = CompositeExecutor(planner=planner, clock=lambda: NOW)
    outcome = executor.execute(
        plan=plan, runners={"paper": paper, "github": github}, context=_context()
    )
    assert outcome.status is CompositeStatus.FAILED
    github_result = outcome.step("github")
    assert github_result.state is StepState.BLOCKED
    assert "paper" in (github_result.blocked_reason or "")
    assert len(github.run_calls) == 0


# ---------------------------------------------------------------------------
# 验收 6：第二轮调整与旧版本迟到写入被拒绝；非法计划被拒绝
# ---------------------------------------------------------------------------


def test_second_adjustment_round_rejected(database: BridgesDatabase) -> None:
    _seed(database)
    budget = _budget(database)
    planner = CompositePlanner()
    plan = planner.plan(
        goal="找论文和资料",
        user_message_id=USER,
        module_ids=["paper", "resources"],
        now=NOW,
    )
    assert plan is not None
    attempts = {"paper": 0, "resources": 0}

    def failing(
        step: CompositeStep, context: StepRunContext, resolved, remaining_ms=None
    ):
        attempts[step.step_id] += 1
        return StepResult(
            step_id=step.step_id,
            module_id=step.module_id,
            state=StepState.FAILED,
            blocked_reason="临时超时",
            failure=StepFailure(code="timeout", message="临时超时", retryable=True),
        )

    executor = CompositeExecutor(planner=planner, budget=budget, clock=lambda: NOW)
    outcome = executor.execute(
        plan=plan,
        runners={
            "paper": FunctionStepRunner(resolve=lambda s, c: {"topic": "x"}, run=failing),
            "resources": FunctionStepRunner(resolve=lambda s, c: {"topic": "x"}, run=failing),
        },
        context=_context(),
    )
    assert outcome.adjustments_used == 1
    assert outcome.plan.revision == 1
    assert attempts == {"paper": 2, "resources": 2}
    assert budget.begin_adjustment("second_round") is False
    _, violation = planner.adjusted(outcome.plan, reason="again", now=NOW)
    assert violation is not None
    assert violation.code is PlanViolationCode.ADJUSTMENT_ROUNDS_EXHAUSTED


def test_late_write_rejected_by_commit_guard(database: BridgesDatabase) -> None:
    _seed(database)
    planner = CompositePlanner()
    plan = planner.plan(
        goal="找论文和资料",
        user_message_id=USER,
        module_ids=["paper", "resources"],
        now=NOW,
    )
    assert plan is not None
    executor = CompositeExecutor(
        planner=planner,
        commit_check=lambda: CommitDecision(False, "lease_lost", "执行租约已转移。"),
        clock=lambda: NOW,
    )
    outcome = executor.execute(
        plan=plan,
        runners={
            "paper": _RecordingRunner(module_id="paper"),
            "resources": _RecordingRunner(module_id="resources"),
        },
        context=_context(),
    )
    assert outcome.status is CompositeStatus.REJECTED
    assert outcome.rejection_code == "lease_lost"


def test_illegal_plans_rejected(database: BridgesDatabase) -> None:
    _seed(database)
    planner = CompositePlanner()
    plan = planner.plan(
        goal="找论文和资料",
        user_message_id=USER,
        module_ids=["paper", "resources"],
        now=NOW,
    )
    assert plan is not None

    # 未登记能力
    broken = plan.step("paper").model_copy(update={"capability": "paper.not_registered"})
    broken_plan = plan.model_copy(update={"steps": [broken, plan.step("resources")]})
    validation = planner.validate(broken_plan)
    assert not validation.ok
    assert PlanViolationCode.UNKNOWN_CAPABILITY.value in validation.codes()

    # 非法循环
    first = plan.step("paper").model_copy(update={"depends_on": ["resources"]})
    second = plan.step("resources").model_copy(update={"depends_on": ["paper"]})
    cyclic = plan.model_copy(update={"steps": [first, second]})
    validation = planner.validate(cyclic)
    assert not validation.ok
    assert any(
        code in validation.codes()
        for code in {
            PlanViolationCode.CYCLIC_PLAN.value,
            PlanViolationCode.SKIPPED_PREREQUISITE.value,
        }
    )

    # 硬限制绕过：不要联网
    no_network = [
        HardCondition(kind=HardConditionKind.NO_NETWORK, text="不要联网")
    ]
    validation = planner.validate(plan, hard_conditions=no_network)
    assert not validation.ok
    assert PlanViolationCode.HARD_CONDITION_BYPASS.value in validation.codes()

    # 来源限制：只查论文
    paper_only = [
        HardCondition(kind=HardConditionKind.SOURCE_RESTRICTION, text="只查论文")
    ]
    validation = planner.validate(plan, hard_conditions=paper_only)
    codes = validation.codes()
    assert not validation.ok
    assert PlanViolationCode.HARD_CONDITION_BYPASS.value in codes

    # 未登记组合：论文+贴吧不进入复合计划
    assert planner.plan(
        goal="论文和贴吧",
        user_message_id=USER,
        module_ids=["paper", "tieba"],
        now=NOW,
    ) is None

    # 并行宽度超预算
    three = planner.plan(
        goal="岗位+资料+项目",
        user_message_id=USER,
        module_ids=["career", "resources", "github"],
        now=NOW,
    )
    assert three is not None
    validation = planner.validate(three, external_parallel_max=1)
    assert not validation.ok
    assert PlanViolationCode.BUDGET_EXCEEDED.value in validation.codes()
