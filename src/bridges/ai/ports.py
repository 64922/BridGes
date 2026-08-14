"""AI/audit ownership boundary: ports for durable model run lock recording.

Business domains depend on these abstractions, not on ``ConversationRepository``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from bridges.contracts.ai import BusinessRef, ModelRunLock, PersistedModelRunLock


@dataclass(frozen=True)
class RecordRequest:
    """One lock to record together with its business context."""

    lock: ModelRunLock
    business_ref: BusinessRef


class ModelRunLockRecorder(ABC):
    """Port for persistently recording immutable model run locks.

    Implementations guarantee:

    - account-scoped reads and writes;
    - idempotent inserts for the same ``lock_id`` + canonical content;
    - stable ``model_run_lock_conflict`` errors when the same ``lock_id`` carries
      different canonical content;
    - ordered ``record_many`` (atomicity is provided by the caller's transaction);
    - rejection or scrubbing of secrets and private body in ``parameters``.
    """

    @abstractmethod
    def record(
        self,
        lock: ModelRunLock,
        *,
        business_ref: BusinessRef,
    ) -> PersistedModelRunLock:
        """Persist a single lock and its business association.

        Calling this repeatedly with the same ``lock_id`` and identical canonical
        content returns the previously persisted lock without creating duplicates.
        """

    @abstractmethod
    def record_many(
        self,
        requests: list[RecordRequest],
    ) -> list[PersistedModelRunLock]:
        """Persist multiple locks and their associations in the order given.

        The batch is atomic: either every lock and link is committed in order,
        or none is. When the caller holds an explicit transaction the batch
        joins it and rolls back together with the surrounding business writes.
        """

    @abstractmethod
    def get_lock(
        self,
        lock_id: str,
        account_id: str,
    ) -> PersistedModelRunLock | None:
        """Return the persisted lock together with its business associations."""

    @abstractmethod
    def list_locks_by_run(
        self,
        account_id: str,
        run_id: str,
    ) -> list[PersistedModelRunLock]:
        """Return all locks for a run, ordered by attempt ordinal then created_at."""

    @abstractmethod
    def list_locks_by_business_ref(
        self,
        account_id: str,
        object_type: str,
        object_id: str,
    ) -> list[PersistedModelRunLock]:
        """Return all locks linked to a business object, ordered by attempt ordinal."""
