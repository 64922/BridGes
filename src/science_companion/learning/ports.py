"""Stable persistence ports for the learning mission and diagnosis domain."""

from __future__ import annotations

from abc import ABC, abstractmethod

from science_companion.contracts.learning import (
    DiagnosticResult,
    DiagnosticRun,
    ExerciseAttempt,
    KnowledgeState,
    KnowledgeStateProposal,
    KnowledgeStateProposalStatus,
    LearningActivity,
    LearningMission,
    LearningPath,
    LearningRecord,
    ShortLesson,
    TeachingPlan,
)


class LearningRepository(ABC):
    """Stable port for reading and writing learning-domain objects.

    Domain modules depend only on this port. Implementations may be in-memory,
    device-local, or persistent, but they all obey the same ownership and
    versioning contract.
    """

    @abstractmethod
    def save_mission(self, mission: LearningMission) -> LearningMission:
        """Persist a learning mission."""

    @abstractmethod
    def get_mission(self, owner_id: str, mission_id: str) -> LearningMission:
        """Return a learning mission or raise a domain error."""

    @abstractmethod
    def list_missions(
        self, owner_id: str, project_id: str | None = None
    ) -> list[LearningMission]:
        """List missions for the owner, optionally filtered by project."""

    @abstractmethod
    def save_diagnostic_run(self, run: DiagnosticRun) -> DiagnosticRun:
        """Persist a diagnostic run."""

    @abstractmethod
    def get_diagnostic_run(self, owner_id: str, run_id: str) -> DiagnosticRun:
        """Return a diagnostic run or raise a domain error."""

    @abstractmethod
    def list_diagnostic_runs_for_mission(
        self, owner_id: str, mission_id: str
    ) -> list[DiagnosticRun]:
        """List diagnostic runs for a mission, most recent first."""

    @abstractmethod
    def save_knowledge_state(self, state: KnowledgeState) -> KnowledgeState:
        """Persist a knowledge state.

        When a new state replaces an existing one for the same concept (same
        account, mission and concept_id), the adapter must set the previous
        state's ``superseded_by_state_id`` to the new state's id so the version
        chain is preserved. Only the latest active state (with
        ``superseded_by_state_id=None``) is returned by
        ``list_knowledge_states_for_mission``.
        """

    @abstractmethod
    def get_knowledge_state(
        self, owner_id: str, mission_id: str, concept_id: str
    ) -> KnowledgeState | None:
        """Return the latest active knowledge state for a concept."""

    @abstractmethod
    def list_knowledge_states_for_mission(
        self, owner_id: str, mission_id: str
    ) -> list[KnowledgeState]:
        """List latest active knowledge states for a mission."""

    @abstractmethod
    def save_diagnostic_result(self, result: DiagnosticResult) -> DiagnosticResult:
        """Persist a diagnostic result."""

    @abstractmethod
    def get_diagnostic_result(self, owner_id: str, result_id: str) -> DiagnosticResult:
        """Return a diagnostic result or raise a domain error."""

    @abstractmethod
    def save_teaching_plan(self, plan: TeachingPlan) -> TeachingPlan:
        """Persist a teaching plan."""

    @abstractmethod
    def get_teaching_plan(self, owner_id: str, plan_id: str) -> TeachingPlan:
        """Return a teaching plan or raise a domain error."""

    @abstractmethod
    def list_teaching_plans_for_mission(
        self, owner_id: str, mission_id: str
    ) -> list[TeachingPlan]:
        """List teaching plans for a mission, most recent first."""

    @abstractmethod
    def save_lesson(self, lesson: ShortLesson) -> ShortLesson:
        """Persist a short lesson."""

    @abstractmethod
    def get_lesson(self, owner_id: str, lesson_id: str) -> ShortLesson:
        """Return a short lesson or raise a domain error."""

    @abstractmethod
    def list_lessons_for_plan(
        self, owner_id: str, plan_id: str
    ) -> list[ShortLesson]:
        """List lessons for a teaching plan, most recent first."""

    @abstractmethod
    def save_exercise_attempt(self, attempt: ExerciseAttempt) -> ExerciseAttempt:
        """Persist an exercise attempt."""

    @abstractmethod
    def list_attempts_for_exercise(
        self, owner_id: str, exercise_id: str
    ) -> list[ExerciseAttempt]:
        """List attempts for an exercise, most recent first."""

    @abstractmethod
    def save_activity(self, activity: LearningActivity) -> LearningActivity:
        """Persist a learning activity that does not update knowledge state."""

    @abstractmethod
    def save_learning_record(self, record: LearningRecord) -> LearningRecord:
        """Persist a qualified learning record."""

    @abstractmethod
    def get_learning_record(self, owner_id: str, record_id: str) -> LearningRecord:
        """Return a learning record or raise a domain error."""

    @abstractmethod
    def list_learning_records(
        self,
        owner_id: str,
        mission_id: str,
        concept_id: str | None = None,
    ) -> list[LearningRecord]:
        """List learning records for a mission, optionally filtered by concept."""

    @abstractmethod
    def save_knowledge_state_proposal(
        self, proposal: KnowledgeStateProposal
    ) -> KnowledgeStateProposal:
        """Persist a knowledge-state proposal."""

    @abstractmethod
    def get_knowledge_state_proposal(
        self, owner_id: str, proposal_id: str
    ) -> KnowledgeStateProposal:
        """Return a knowledge-state proposal or raise a domain error."""

    @abstractmethod
    def list_knowledge_state_proposals(
        self,
        owner_id: str,
        mission_id: str,
        concept_id: str | None = None,
        status: KnowledgeStateProposalStatus | None = None,
    ) -> list[KnowledgeStateProposal]:
        """List knowledge-state proposals for a mission."""

    @abstractmethod
    def save_learning_path(self, path: LearningPath) -> LearningPath:
        """Persist a learning path."""

    @abstractmethod
    def get_learning_path(self, owner_id: str, path_id: str) -> LearningPath:
        """Return a learning path or raise a domain error."""

    @abstractmethod
    def get_learning_path_for_mission(
        self, owner_id: str, mission_id: str
    ) -> LearningPath | None:
        """Return the latest learning path for a mission, if any."""
