"""Stable persistence ports for the learning mission and diagnosis domain."""

from __future__ import annotations

from abc import ABC, abstractmethod

from science_companion.contracts.learning import (
    DiagnosticResult,
    DiagnosticRun,
    KnowledgeState,
    LearningActivity,
    LearningMission,
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
        """Persist a knowledge state."""

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
    def save_activity(self, activity: LearningActivity) -> LearningActivity:
        """Persist a learning activity that does not update knowledge state."""
