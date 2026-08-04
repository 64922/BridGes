"""In-memory adapters for exercising the profile port contracts."""

from __future__ import annotations

from datetime import datetime

from bridges.contracts.profiles import (
    ProfileAssertion,
    ProfileAssertionVersion,
    ProfileCandidate,
    ProfileDimension,
    ProfileNotification,
    ProfileObservation,
    ProfilePermission,
    ProfileSlice,
)
from bridges.profiles.ports import ProfileRepository


class ProfileError(Exception):
    """Domain exception for profile failures.

    The message is safe to expose to callers; it never leaks whether an object
    exists or belongs to another account.
    """


class InMemoryProfileRepository(ProfileRepository):
    """In-memory profile repository for tests and prototypes."""

    def __init__(self) -> None:
        self._observations: dict[str, ProfileObservation] = {}
        self._candidates: dict[str, ProfileCandidate] = {}
        self._assertions: dict[str, ProfileAssertion] = {}
        self._slices: dict[str, ProfileSlice] = {}
        self._assertion_versions: dict[str, list[ProfileAssertionVersion]] = {}
        self._permissions: dict[str, ProfilePermission] = {}
        self._notifications: dict[str, ProfileNotification] = {}

    def _key(self, owner_id: str, object_id: str) -> str:
        return f"{owner_id}:{object_id}"

    def save_observation(self, observation: ProfileObservation) -> ProfileObservation:
        self._observations[
            self._key(observation.owner_account_id, observation.observation_id)
        ] = observation
        return observation

    def get_observation(
        self, owner_id: str, observation_id: str
    ) -> ProfileObservation:
        observation = self._observations.get(self._key(owner_id, observation_id))
        if observation is None:
            raise ProfileError("对象不存在或没有访问权限。")
        return observation

    def list_observations(self, owner_id: str) -> list[ProfileObservation]:
        observations = [
            obs for obs in self._observations.values() if obs.owner_account_id == owner_id
        ]
        observations.sort(key=lambda o: o.created_at, reverse=True)
        return observations

    def save_candidate(self, candidate: ProfileCandidate) -> ProfileCandidate:
        self._candidates[
            self._key(candidate.owner_account_id, candidate.candidate_id)
        ] = candidate
        return candidate

    def get_candidate(self, owner_id: str, candidate_id: str) -> ProfileCandidate:
        candidate = self._candidates.get(self._key(owner_id, candidate_id))
        if candidate is None:
            raise ProfileError("对象不存在或没有访问权限。")
        return candidate

    def list_candidates(self, owner_id: str) -> list[ProfileCandidate]:
        candidates = [
            cand for cand in self._candidates.values() if cand.owner_account_id == owner_id
        ]
        candidates.sort(key=lambda c: c.proposed_at, reverse=True)
        return candidates

    def save_assertion(self, assertion: ProfileAssertion) -> ProfileAssertion:
        self._assertions[
            self._key(assertion.owner_account_id, assertion.assertion_id)
        ] = assertion
        return assertion

    def list_assertions(self, owner_id: str) -> list[ProfileAssertion]:
        assertions = [
            assertion
            for assertion in self._assertions.values()
            if assertion.owner_account_id == owner_id
        ]
        assertions.sort(key=lambda a: a.created_at, reverse=True)
        return assertions

    def save_slice(self, slice_: ProfileSlice) -> ProfileSlice:
        self._slices[self._key(slice_.owner_account_id, slice_.slice_id)] = slice_
        return slice_

    def get_slice(self, owner_id: str, slice_id: str) -> ProfileSlice:
        slice_ = self._slices.get(self._key(owner_id, slice_id))
        if slice_ is None:
            raise ProfileError("对象不存在或没有访问权限。")
        return slice_

    def list_slices_for_run(self, owner_id: str, run_id: str) -> list[ProfileSlice]:
        slices = [
            slice_
            for slice_ in self._slices.values()
            if slice_.owner_account_id == owner_id and slice_.run_id == run_id
        ]
        slices.sort(key=lambda s: s.compiled_at, reverse=True)
        return slices

    def get_slice_by_id(self, slice_id: str) -> ProfileSlice:
        for slice_ in self._slices.values():
            if slice_.slice_id == slice_id:
                return slice_
        raise ProfileError("对象不存在或没有访问权限。")

    def get_assertion(self, owner_id: str, assertion_id: str) -> ProfileAssertion:
        assertion = self._assertions.get(self._key(owner_id, assertion_id))
        if assertion is None:
            raise ProfileError("对象不存在或没有访问权限。")
        return assertion

    def save_assertion_version(
        self, version: ProfileAssertionVersion
    ) -> ProfileAssertionVersion:
        key = self._key(version.owner_account_id, version.assertion_id)
        self._assertion_versions.setdefault(key, []).append(version)
        return version

    def list_assertion_versions(
        self, owner_id: str, assertion_id: str
    ) -> list[ProfileAssertionVersion]:
        versions = self._assertion_versions.get(self._key(owner_id, assertion_id), [])
        return list(versions)

    def list_slices_containing_assertion(
        self, owner_id: str, assertion_id: str
    ) -> list[ProfileSlice]:
        slices = [
            slice_
            for slice_ in self._slices.values()
            if slice_.owner_account_id == owner_id
            and any(item.assertion_id == assertion_id for item in slice_.included_items)
        ]
        slices.sort(key=lambda s: s.compiled_at, reverse=True)
        return slices

    def list_permissions(self, owner_id: str) -> list[ProfilePermission]:
        permissions = [
            permission
            for permission in self._permissions.values()
            if permission.account_id == owner_id
        ]
        permissions.sort(key=lambda p: (p.dimension.value, p.scene))
        return permissions

    def set_permission(
        self,
        owner_id: str,
        dimension: str,
        scene: str,
        enabled: bool,
        updated_at: datetime,
    ) -> ProfilePermission:
        permission = ProfilePermission(
            account_id=owner_id,
            dimension=ProfileDimension(dimension),
            scene=scene,
            enabled=enabled,
            updated_at=updated_at,
        )
        self._permissions[self._key(owner_id, f"{dimension}:{scene}")] = permission
        return permission

    def find_assertion_by_value(
        self, owner_id: str, dimension: str, value: str
    ) -> ProfileAssertion | None:
        for assertion in self._assertions.values():
            if (
                assertion.owner_account_id == owner_id
                and assertion.canonical_dimension == dimension
                and assertion.value_or_rule == value
                and assertion.status.value in {"active", "frozen", "withdrawn"}
            ):
                return assertion
        return None

    def find_discarded_observation(
        self, owner_id: str, content_hash: str
    ) -> ProfileObservation | None:
        for observation in self._observations.values():
            if (
                observation.owner_account_id == owner_id
                and observation.content_hash == content_hash
                and observation.status.value == "discarded"
            ):
                return observation
        return None

    def find_observation_by_source(
        self, owner_id: str, content_hash: str, source_ref: str
    ) -> ProfileObservation | None:
        for observation in self._observations.values():
            if (
                observation.owner_account_id == owner_id
                and observation.content_hash == content_hash
                and observation.source_ref == source_ref
            ):
                return observation
        return None

    def save_notification(self, notification: ProfileNotification) -> ProfileNotification:
        self._notifications[
            self._key(notification.owner_account_id, notification.notification_id)
        ] = notification
        return notification

    def get_notification(
        self, owner_id: str, notification_id: str
    ) -> ProfileNotification:
        notification = self._notifications.get(self._key(owner_id, notification_id))
        if notification is None:
            raise ProfileError("对象不存在或没有访问权限。")
        return notification

    def list_notifications(self, owner_id: str) -> list[ProfileNotification]:
        notifications = [
            notification
            for notification in self._notifications.values()
            if notification.owner_account_id == owner_id
        ]
        notifications.sort(key=lambda n: n.created_at, reverse=True)
        return notifications

    def mark_notification_read(
        self, owner_id: str, notification_id: str, read_at: datetime
    ) -> ProfileNotification:
        notification = self.get_notification(owner_id, notification_id)
        notification.read_at = read_at
        return notification
