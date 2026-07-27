"""In-memory adapters for exercising the learning port contracts."""

from __future__ import annotations

from science_companion.contracts.learning import (
    DiagnosticResult,
    DiagnosticRun,
    ExerciseAttempt,
    KnowledgeState,
    LearningActivity,
    LearningMission,
    ShortLesson,
    TeachingPlan,
)
from science_companion.learning.ports import LearningRepository


class LearningError(Exception):
    """Domain exception for learning failures.

    The message is safe to expose to callers; it never leaks whether an object
    exists or belongs to another account.
    """


class InMemoryLearningRepository(LearningRepository):
    """In-memory learning repository for tests and prototypes."""

    def __init__(self) -> None:
        self._missions: dict[str, LearningMission] = {}
        self._runs: dict[str, DiagnosticRun] = {}
        self._states: dict[str, KnowledgeState] = {}
        self._results: dict[str, DiagnosticResult] = {}
        self._plans: dict[str, TeachingPlan] = {}
        self._activities: dict[str, LearningActivity] = {}
        self._lessons: dict[str, ShortLesson] = {}
        self._attempts: dict[str, ExerciseAttempt] = {}

    def _key(self, owner_id: str, object_id: str) -> str:
        return f"{owner_id}:{object_id}"

    def _state_key(self, owner_id: str, mission_id: str, concept_id: str) -> str:
        return f"{owner_id}:{mission_id}:{concept_id}"

    def save_mission(self, mission: LearningMission) -> LearningMission:
        self._missions[self._key(mission.owner_account_id, mission.mission_id)] = mission
        return mission

    def get_mission(self, owner_id: str, mission_id: str) -> LearningMission:
        mission = self._missions.get(self._key(owner_id, mission_id))
        if mission is None:
            raise LearningError("学习使命不存在或没有访问权限。")
        return mission

    def list_missions(
        self, owner_id: str, project_id: str | None = None
    ) -> list[LearningMission]:
        missions = [
            m for m in self._missions.values() if m.owner_account_id == owner_id
        ]
        if project_id is not None:
            missions = [m for m in missions if m.project_id == project_id]
        missions.sort(key=lambda m: m.created_at, reverse=True)
        return missions

    def save_diagnostic_run(self, run: DiagnosticRun) -> DiagnosticRun:
        self._runs[self._key(run.owner_account_id, run.run_id)] = run
        return run

    def get_diagnostic_run(self, owner_id: str, run_id: str) -> DiagnosticRun:
        run = self._runs.get(self._key(owner_id, run_id))
        if run is None:
            raise LearningError("诊断运行不存在或没有访问权限。")
        return run

    def list_diagnostic_runs_for_mission(
        self, owner_id: str, mission_id: str
    ) -> list[DiagnosticRun]:
        runs = [
            r
            for r in self._runs.values()
            if r.owner_account_id == owner_id and r.mission_id == mission_id
        ]
        runs.sort(key=lambda r: r.created_at, reverse=True)
        return runs

    def save_knowledge_state(self, state: KnowledgeState) -> KnowledgeState:
        key = self._state_key(state.owner_account_id, state.mission_id, state.concept_id)
        existing = self._states.get(key)
        if existing is not None and existing.state_id != state.state_id:
            existing.superseded_by_state_id = state.state_id
            self._states[key] = state
        else:
            self._states[key] = state
        return state

    def get_knowledge_state(
        self, owner_id: str, mission_id: str, concept_id: str
    ) -> KnowledgeState | None:
        return self._states.get(self._state_key(owner_id, mission_id, concept_id))

    def list_knowledge_states_for_mission(
        self, owner_id: str, mission_id: str
    ) -> list[KnowledgeState]:
        states = [
            s
            for s in self._states.values()
            if s.owner_account_id == owner_id
            and s.mission_id == mission_id
            and s.superseded_by_state_id is None
        ]
        states.sort(key=lambda s: s.updated_at, reverse=True)
        return states

    def save_diagnostic_result(self, result: DiagnosticResult) -> DiagnosticResult:
        self._results[self._key(result.owner_account_id, result.result_id)] = result
        return result

    def get_diagnostic_result(self, owner_id: str, result_id: str) -> DiagnosticResult:
        result = self._results.get(self._key(owner_id, result_id))
        if result is None:
            raise LearningError("诊断结果不存在或没有访问权限。")
        return result

    def save_teaching_plan(self, plan: TeachingPlan) -> TeachingPlan:
        self._plans[self._key(plan.owner_account_id, plan.plan_id)] = plan
        return plan

    def get_teaching_plan(self, owner_id: str, plan_id: str) -> TeachingPlan:
        plan = self._plans.get(self._key(owner_id, plan_id))
        if plan is None:
            raise LearningError("教学计划不存在或没有访问权限。")
        return plan

    def list_teaching_plans_for_mission(
        self, owner_id: str, mission_id: str
    ) -> list[TeachingPlan]:
        plans = [
            p
            for p in self._plans.values()
            if p.owner_account_id == owner_id and p.mission_id == mission_id
        ]
        plans.sort(key=lambda p: p.created_at, reverse=True)
        return plans

    def save_lesson(self, lesson: ShortLesson) -> ShortLesson:
        self._lessons[self._key(lesson.owner_account_id, lesson.lesson_id)] = lesson
        return lesson

    def get_lesson(self, owner_id: str, lesson_id: str) -> ShortLesson:
        lesson = self._lessons.get(self._key(owner_id, lesson_id))
        if lesson is None:
            raise LearningError("短课不存在或没有访问权限。")
        return lesson

    def list_lessons_for_plan(
        self, owner_id: str, plan_id: str
    ) -> list[ShortLesson]:
        lessons = [
            lesson
            for lesson in self._lessons.values()
            if lesson.owner_account_id == owner_id and lesson.plan_id == plan_id
        ]
        lessons.sort(key=lambda lesson: lesson.created_at, reverse=True)
        return lessons

    def save_exercise_attempt(self, attempt: ExerciseAttempt) -> ExerciseAttempt:
        self._attempts[self._key(attempt.owner_account_id, attempt.attempt_id)] = attempt
        return attempt

    def list_attempts_for_exercise(
        self, owner_id: str, exercise_id: str
    ) -> list[ExerciseAttempt]:
        attempts = [
            a
            for a in self._attempts.values()
            if a.owner_account_id == owner_id and a.exercise_id == exercise_id
        ]
        attempts.sort(key=lambda a: a.created_at, reverse=True)
        return attempts

    def save_activity(self, activity: LearningActivity) -> LearningActivity:
        self._activities[self._key(activity.owner_account_id, activity.activity_id)] = activity
        return activity
