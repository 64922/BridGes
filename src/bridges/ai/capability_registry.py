"""In-memory capability registry for T009.

The registry stores verified logical capabilities. It intentionally does not
know how to invoke them; that responsibility belongs to the model gateway and
its adapters.
"""

from __future__ import annotations

from bridges.contracts.ai import CapabilityRecord, CapabilityStatus


class CapabilityRegistryError(Exception):
    """Domain error for registry operations."""


class CapabilityRegistry:
    """Store and retrieve registered logical capabilities.

    Capabilities are keyed by ``(name, version)``. The registry is thread-safe
    enough for the in-memory service boundary used in T009; persistent storage
    will be introduced when the registry becomes a runtime asset.
    """

    def __init__(self) -> None:
        self._capabilities: dict[tuple[str, str], CapabilityRecord] = {}

    def register(self, capability: CapabilityRecord) -> None:
        """Register or overwrite a capability.

        Raises ``CapabilityRegistryError`` if the capability is disabled at
        registration time, because disabled capabilities must not enter the
        active catalog.
        """
        if capability.status == CapabilityStatus.DISABLED:
            raise CapabilityRegistryError(
                f"Cannot register disabled capability {capability.name}@{capability.version}."
            )
        self._capabilities[(capability.name, capability.version)] = capability

    def get(self, name: str, version: str) -> CapabilityRecord:
        """Return a capability by name and version.

        Raises ``CapabilityRegistryError`` only if the capability is unknown.
        Disabled or deprecated capabilities are returned so the gateway can
        report a precise, auditable status.
        """
        key = (name, version)
        record = self._capabilities.get(key)
        if record is None:
            raise CapabilityRegistryError(f"未注册能力：{name}@{version}。")
        return record

    def list_active(self) -> list[CapabilityRecord]:
        """Return all verified capabilities in registration order."""
        return [
            record
            for record in self._capabilities.values()
            if record.status == CapabilityStatus.VERIFIED
        ]
