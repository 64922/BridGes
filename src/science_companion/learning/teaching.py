"""Short-lesson and retrieval-practice domain service for T022.

TeachingService turns a TeachingPlan into a short lesson around a single learning
victory. Explanations, examples, answers and feedback are bound to an EvidenceSet
and constrained by fact locks. Exercises require active recall, explanation,
computation, comparison or application; browsing-only or completion-only
activities are rejected. Insufficient evidence or high-risk topics degrade the
lesson or require a human gate.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from science_companion.contracts.learning import (
    AnswerEvaluatedState,
    EvidenceRef,
    ExerciseAttempt,
    GenerateLessonRequest,
    ImmediateFeedback,
    LessonExample,
    LessonStatus,
    RetrievalExercise,
    RetrievalExerciseType,
    ShortLesson,
    TeachingQualityGateCheck,
    TeachingQualityGateResult,
)
from science_companion.contracts.science import FactLock
from science_companion.learning.adapters import LearningError
from science_companion.learning.ports import LearningRepository


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return secrets.token_urlsafe(16)


# Thresholds for deterministic exercise evaluation.
_CORRECT_THRESHOLD = 0.6
_PARTIAL_THRESHOLD = 0.3

# Keywords that trigger a mandatory human gate because the topic can affect
# real-world safety, health, or regulated decisions.
_HIGH_RISK_KEYWORDS: frozenset[str] = frozenset(
    {
        "诊断",
        "处方",
        "治疗",
        "临床",
        "医学",
        "药物",
        "剂量",
        "手术",
        "放疗",
        "化疗",
        "辐射",
        "危险化学品",
        "爆炸",
        "中毒",
        "急救",
        "自杀",
        "自残",
        # English variants
        "diagnosis",
        "prescription",
        "treatment",
        "clinical",
        "medical",
        "dosage",
        "surgery",
        "radiation",
        "hazardous chemical",
        "explosive",
        "poisoning",
    }
)


class TeachingService:
    """Application service for generating credible short lessons and exercises."""

    def __init__(self, repository: LearningRepository) -> None:
        self._repository = repository

    def generate_short_lesson(
        self,
        account_id: str,
        plan_id: str,
        request: GenerateLessonRequest | None = None,
    ) -> ShortLesson:
        """Generate a short lesson from a teaching plan and optional evidence bundle."""
        plan = self._repository.get_teaching_plan(account_id, plan_id)
        mission = self._repository.get_mission(account_id, plan.mission_id)

        evidence_bundle = request.evidence_bundle if request else None
        evidence_refs = (
            list(evidence_bundle.evidence_refs)
            if evidence_bundle and evidence_bundle.evidence_refs
            else []
        )
        fact_locks = (
            list(evidence_bundle.fact_locks)
            if evidence_bundle and evidence_bundle.fact_locks
            else []
        )
        fact_lock_ids = [lock.lock_id for lock in fact_locks]
        fact_lock_set_id = evidence_bundle.graph_id if evidence_bundle else None

        # Short lesson focuses on the single next learning victory.
        lesson_concepts = plan.target_concepts[:1] or mission.scope_concepts[:1]
        if not lesson_concepts:
            raise LearningError("教学计划没有可聚焦的学习目标。")
        target_concept = lesson_concepts[0]

        objective = plan.learning_objective
        title = f"{mission.title}：{target_concept}"
        lesson_id = _new_id()

        explanation = self._build_explanation(
            objective, target_concept, evidence_refs, fact_lock_ids
        )
        example = self._build_example(target_concept, evidence_refs, fact_lock_ids)
        exercises = self._build_exercises(
            lesson_id, target_concept, evidence_refs, fact_lock_ids
        )

        # Pass original plan concepts to the quality gate so it can verify
        # the plan itself doesn't try to cover too many concepts at once.
        quality_gate = self._run_teaching_quality_gate(
            mission_title=mission.title,
            objective=objective,
            target_concepts=plan.target_concepts,
            evidence_refs=evidence_refs,
            fact_locks=fact_locks,
            exercises=exercises,
        )

        status = self._derive_lesson_status(quality_gate)

        lesson = ShortLesson(
            lesson_id=lesson_id,
            plan_id=plan.plan_id,
            mission_id=mission.mission_id,
            owner_account_id=account_id,
            title=title,
            learning_objective=objective,
            target_concepts=lesson_concepts,
            explanation=explanation,
            examples=[example],
            exercises=exercises,
            evidence_refs=evidence_refs,
            fact_lock_set_id=fact_lock_set_id,
            fact_lock_ids=fact_lock_ids,
            quality_gate=quality_gate,
            status=status,
            human_gate_required=quality_gate.requires_human_gate,
            version=1,
            created_at=_now(),
        )
        return self._repository.save_lesson(lesson)

    def get_lesson(self, account_id: str, lesson_id: str) -> ShortLesson:
        """Return a short lesson if the account owns it."""
        return self._repository.get_lesson(account_id, lesson_id)

    def list_lessons_for_plan(
        self, account_id: str, plan_id: str
    ) -> list[ShortLesson]:
        """List short lessons for a teaching plan."""
        return self._repository.list_lessons_for_plan(account_id, plan_id)

    def submit_exercise_attempt(
        self,
        account_id: str,
        lesson_id: str,
        exercise_id: str,
        response_text: str,
    ) -> ExerciseAttempt:
        """Record a retrieval-exercise attempt and return immediate feedback.

        The evaluation is deterministic and rule-based so the seam is testable
        without a live model. Feedback references the exercise's evidence and
        fact locks.
        """
        lesson = self._repository.get_lesson(account_id, lesson_id)
        exercise = next(
            (e for e in lesson.exercises if e.exercise_id == exercise_id),
            None,
        )
        if exercise is None:
            raise LearningError("练习不属于当前短课或没有访问权限。")

        evaluated_state, feedback = self._evaluate_response(
            exercise, response_text
        )

        attempt = ExerciseAttempt(
            attempt_id=_new_id(),
            exercise_id=exercise_id,
            lesson_id=lesson_id,
            owner_account_id=account_id,
            response_text=response_text,
            evaluated_state=evaluated_state,
            evaluator="rule",
            feedback=feedback,
            created_at=_now(),
        )
        return self._repository.save_exercise_attempt(attempt)

    def list_attempts_for_exercise(
        self, account_id: str, exercise_id: str
    ) -> list[ExerciseAttempt]:
        """List attempts for an exercise, verifying ownership."""
        return self._repository.list_attempts_for_exercise(account_id, exercise_id)

    def _build_explanation(
        self,
        objective: str,
        concept: str,
        evidence_refs: list[EvidenceRef],
        fact_lock_ids: list[str],
    ) -> str:
        evidence_count = len(evidence_refs)
        lock_count = len(fact_lock_ids)
        return (
            f"本课聚焦于“{concept}”。学习目标：{objective} "
            f"讲解基于 {evidence_count} 条证据，并受 {lock_count} 项事实锁约束。"
        )

    def _build_example(
        self,
        concept: str,
        evidence_refs: list[EvidenceRef],
        fact_lock_ids: list[str],
    ) -> LessonExample:
        return LessonExample(
            example_id=_new_id(),
            concept_id=concept,
            title=f"示例：{concept}",
            explanation=(
                f"下面通过一个具体情境说明“{concept}”。"
                "该示例必须与绑定证据一致，且不得突破事实锁。"
            ),
            evidence_refs=list(evidence_refs),
            fact_lock_ids=list(fact_lock_ids),
        )

    def _build_exercises(
        self,
        lesson_id: str,
        concept: str,
        evidence_refs: list[EvidenceRef],
        fact_lock_ids: list[str],
    ) -> list[RetrievalExercise]:
        """Generate retrieval-type exercises only.

        Covers all five active-recall types required by the spec: recall,
        explanation, computation, comparison, and application.
        """
        exercise_specs: list[tuple[RetrievalExerciseType, str, str, list[str]]] = [
            (
                RetrievalExerciseType.RECALL,
                f"请回忆并简要说明“{concept}”的核心定义或关键内容。",
                f"{concept};定义;核心内容",
                ["只给出例子而没有定义", "把相关概念混淆"],
            ),
            (
                RetrievalExerciseType.EXPLANATION,
                f"请用自己的话解释“{concept}”为什么重要，并给出一个例子。",
                f"{concept};作用;例子",
                ["只描述现象未解释原因", "例子与概念不匹配"],
            ),
            (
                RetrievalExerciseType.COMPUTATION,
                f"请对“{concept}”进行一项相关计算或定量推导，并说明每一步的含义。",
                f"{concept};计算;推导;步骤;含义",
                ["只写出最终结果", "省略关键推导步骤"],
            ),
            (
                RetrievalExerciseType.COMPARISON,
                f"请比较“{concept}”与一个相关概念或方法的异同，并说明各自的适用条件。",
                f"{concept};比较;异同;适用条件",
                ["只列举一方特点", "未说明适用条件"],
            ),
            (
                RetrievalExerciseType.APPLICATION,
                f"请在一个新情境中应用“{concept}”，并说明你的推理过程。",
                f"{concept};新情境;应用;推理",
                ["直接照搬原例子", "缺少推理过程"],
            ),
        ]
        exercises: list[RetrievalExercise] = []
        for ex_type, question, expected, hints in exercise_specs:
            exercises.append(
                RetrievalExercise(
                    exercise_id=_new_id(),
                    lesson_id=lesson_id,
                    concept_id=concept,
                    exercise_type=ex_type,
                    question_text=question,
                    expected_answer=expected,
                    misconception_hints=list(hints),
                    evidence_refs=list(evidence_refs),
                    fact_lock_ids=list(fact_lock_ids),
                )
            )
        return exercises

    def _run_teaching_quality_gate(
        self,
        *,
        mission_title: str,
        objective: str,
        target_concepts: list[str],
        evidence_refs: list[EvidenceRef],
        fact_locks: list[FactLock],
        exercises: list[RetrievalExercise],
    ) -> TeachingQualityGateResult:
        """Run deterministic teaching quality gate."""
        checks: dict[TeachingQualityGateCheck, bool] = dict.fromkeys(TeachingQualityGateCheck, True)
        failed: list[TeachingQualityGateCheck] = []
        reasons: list[str] = []
        requires_human_gate = False
        human_gate_reasons: list[str] = []

        if not mission_title or not objective or not target_concepts:
            checks[TeachingQualityGateCheck.MISSION_BOUND] = False
            reasons.append("学习使命或学习目标未绑定。")

        if len(target_concepts) > 2:
            checks[TeachingQualityGateCheck.SINGLE_LEARNING_VICTORY] = False
            reasons.append("短课应围绕单一学习胜利，不应同时覆盖过多概念。")

        if not evidence_refs:
            checks[TeachingQualityGateCheck.EVIDENCE_BOUND] = False
            reasons.append("短课缺少 EvidenceSet 绑定。")

        if not fact_locks:
            checks[TeachingQualityGateCheck.FACT_LOCK_BOUND] = False
            reasons.append("短课缺少事实锁约束。")

        if not exercises:
            checks[TeachingQualityGateCheck.EXERCISE_REQUIRES_RETRIEVAL] = False
            reasons.append("短课未包含任何练习。")
        else:
            allowed_types = set(RetrievalExerciseType)
            for exercise in exercises:
                if exercise.exercise_type not in allowed_types:
                    checks[TeachingQualityGateCheck.EXERCISE_REQUIRES_RETRIEVAL] = False
                    reasons.append(f"练习 {exercise.exercise_id} 不是检索练习类型。")
                    break

        if self._detect_high_risk(objective, target_concepts):
            checks[TeachingQualityGateCheck.NO_HIGH_RISK_WITHOUT_HUMAN_GATE] = False
            reasons.append("检测到高风险主题，需要人工门确认。")
            requires_human_gate = True
            human_gate_reasons.append("高风险主题：可能涉及安全、医疗或专业决策。")

        passed = all(checks.values())
        failed = [check for check, ok in checks.items() if not ok]

        return TeachingQualityGateResult(
            passed=passed,
            checks=checks,
            failed_checks=failed,
            requires_human_gate=requires_human_gate,
            human_gate_reason="；".join(human_gate_reasons) if human_gate_reasons else None,
            reason="；".join(reasons) if reasons else None,
        )

    def _derive_lesson_status(self, gate: TeachingQualityGateResult) -> LessonStatus:
        if gate.requires_human_gate:
            return LessonStatus.DEGRADED
        if gate.passed:
            return LessonStatus.QUALIFIED
        return LessonStatus.BLOCKED

    def _detect_high_risk(self, objective: str, concepts: list[str]) -> bool:
        text = (objective + " ".join(concepts)).lower()
        return any(keyword in text for keyword in _HIGH_RISK_KEYWORDS)

    def _evaluate_response(
        self, exercise: RetrievalExercise, response_text: str
    ) -> tuple[AnswerEvaluatedState, ImmediateFeedback]:
        """Deterministic evaluation of a retrieval response."""
        response_lower = response_text.lower()
        expected = exercise.expected_answer

        # Split expected answer into meaningful key terms.
        delimiters = " ,;，、.。;；:：!！?？\"\"''()（）"
        for delimiter in delimiters:
            expected = expected.replace(delimiter, " ")
        terms = [term.strip() for term in expected.split() if len(term.strip()) > 1]
        if not terms:
            terms = [exercise.expected_answer]

        matched = sum(1 for term in terms if term.lower() in response_lower)
        ratio = matched / len(terms) if terms else 0.0

        # Detect misconception hints in the response.
        detected_misconception: str | None = None
        for hint in exercise.misconception_hints:
            if hint.lower() in response_lower:
                detected_misconception = hint
                break

        if ratio >= _CORRECT_THRESHOLD:
            state = AnswerEvaluatedState.CORRECT
            explanation = (
                "回答覆盖了关键要点。请继续完成后续练习，或在新的情境中应用该概念。"
            )
            next_step = "尝试应用类练习或进行延迟复测。"
        elif ratio >= _PARTIAL_THRESHOLD or detected_misconception is not None:
            state = AnswerEvaluatedState.PARTIAL
            explanation = (
                "回答包含部分正确内容，但尚未完整覆盖要点，或出现了常见误区。"
            )
            next_step = f"复习“{exercise.concept_id}”的讲解与示例，再试一次。"
        else:
            state = AnswerEvaluatedState.INCORRECT
            explanation = (
                "回答尚未覆盖本题期望的关键内容。建议回到讲解和示例，确认理解后再作答。"
            )
            next_step = f"重新阅读“{exercise.concept_id}”的讲解与事实锁，再尝试。"

        feedback = ImmediateFeedback(
            is_correct=state == AnswerEvaluatedState.CORRECT,
            explanation=explanation,
            misconception=detected_misconception,
            evidence_refs=list(exercise.evidence_refs),
            next_step=next_step,
        )
        return state, feedback
