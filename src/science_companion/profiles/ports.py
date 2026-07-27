"""Stable persistence ports for the profile observation-candidate loop."""

from __future__ import annotations

from abc import ABC, abstractmethod

from science_companion.contracts.profiles import (
    ProfileAssertion,
    ProfileAssertionVersion,
    ProfileCandidate,
    ProfileObservation,
    ProfileSlice,
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

    @abstractmethod
    def save_slice(self, slice_: ProfileSlice) -> ProfileSlice:
        """Persist a compiled memory slice."""

    @abstractmethod
    def get_slice(self, owner_id: str, slice_id: str) -> ProfileSlice:
        """Return a compiled memory slice or raise a domain error."""

    @abstractmethod
    def list_slices_for_run(self, owner_id: str, run_id: str) -> list[ProfileSlice]:
        """List slices bound to a run for the owner, most recent first."""

    @abstractmethod
    def get_slice_by_id(self, slice_id: str) -> ProfileSlice:
        """Return a compiled memory slice by its identifier without owner check.

        Callers are responsible for verifying the run binding and scope. This is
        intended for model/worker lookups that authorize by run_id, not account.
        """

    @abstractmethod
    def get_assertion(
        self, owner_id: str, assertion_id: str
    ) -> ProfileAssertion:
        """Return a profile assertion or raise a domain error."""

    @abstractmethod
    def save_assertion_version(
        self, version: ProfileAssertionVersion
    ) -> ProfileAssertionVersion:
        """Persist a snapshot of a profile assertion version."""

    @abstractmethod
    def list_assertion_versions(
        self, owner_id: str, assertion_id: str
    ) -> list[ProfileAssertionVersion]:
        """Return version history for an assertion, oldest first."""

    @abstractmethod
    def list_slices_containing_assertion(
        self, owner_id: str, assertion_id: str
    ) -> list[ProfileSlice]:
        """Return slices that include the assertion in their included items."""
