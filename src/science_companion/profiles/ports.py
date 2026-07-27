"""Stable persistence ports for the profile observation-candidate loop."""

from __future__ import annotations

from abc import ABC, abstractmethod

from science_companion.contracts.profiles import (
    ProfileAssertion,
    ProfileCandidate,
    ProfileObservation,
)


class ProfileRepository(ABC):
    """Stable port for reading and writing profile observations and candidates.

    Domain modules depend only on this port. Implementations may be in-memory,
    device-local, or persistent, but they all obey the same contract.
    """

    @abstractmethod
    def save_observation(self, observation: ProfileObservation) -> ProfileObservation:
        """Persist a profile observation."""

    @abstractmethod
    def get_observation(
        self, owner_id: str, observation_id: str
    ) -> ProfileObservation:
        """Return a profile observation or raise a domain error."""

    @abstractmethod
    def list_observations(self, owner_id: str) -> list[ProfileObservation]:
        """List observations for the owner, most recent first."""

    @abstractmethod
    def save_candidate(self, candidate: ProfileCandidate) -> ProfileCandidate:
        """Persist a profile candidate."""

    @abstractmethod
    def get_candidate(self, owner_id: str, candidate_id: str) -> ProfileCandidate:
        """Return a profile candidate or raise a domain error."""

    @abstractmethod
    def list_candidates(self, owner_id: str) -> list[ProfileCandidate]:
        """List candidates for the owner, most recent first."""

    @abstractmethod
    def save_assertion(self, assertion: ProfileAssertion) -> ProfileAssertion:
        """Persist a profile assertion."""

    @abstractmethod
    def list_assertions(self, owner_id: str) -> list[ProfileAssertion]:
        """List assertions for the owner, most recent first."""
