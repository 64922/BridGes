"""Tests for the in-memory capability registry.

The seam: registered capabilities can be retrieved; unknown, disabled or
unverified capabilities are rejected deterministically.
"""

import pytest

from science_companion.ai import CapabilityRegistry, CapabilityRegistryError
from science_companion.contracts.ai import CapabilityKind, CapabilityRecord, CapabilityStatus


def _sample_capability(name: str = "test_cap", version: str = "1") -> CapabilityRecord:
    return CapabilityRecord(
        name=name,
        version=version,
        kind=CapabilityKind.MODEL,
        vendor="test",
        region="cn-beijing",
        model_id="test-model",
        input_schema_version="in-v1",
        output_schema_version="out-v1",
    )


def test_register_and_get_capability() -> None:
    registry = CapabilityRegistry()
    cap = _sample_capability()
    registry.register(cap)

    retrieved = registry.get("test_cap", "1")
    assert retrieved.name == "test_cap"
    assert retrieved.version == "1"
    assert retrieved.status == CapabilityStatus.VERIFIED


def test_get_unknown_capability_raises() -> None:
    registry = CapabilityRegistry()
    with pytest.raises(CapabilityRegistryError, match="未注册能力"):
        registry.get("missing", "1")


def test_disabled_capability_cannot_be_registered() -> None:
    registry = CapabilityRegistry()
    cap = _sample_capability()
    cap.status = CapabilityStatus.DISABLED
    with pytest.raises(CapabilityRegistryError, match="disabled"):
        registry.register(cap)


def test_get_returns_disabled_capability_for_audit() -> None:
    registry = CapabilityRegistry()
    cap = _sample_capability()
    registry.register(cap)
    cap.status = CapabilityStatus.DISABLED
    registry._capabilities[(cap.name, cap.version)] = cap

    retrieved = registry.get(cap.name, cap.version)
    assert retrieved.status == CapabilityStatus.DISABLED


def test_list_active_returns_only_verified() -> None:
    registry = CapabilityRegistry()
    verified = _sample_capability("verified_cap", "1")
    deprecated = _sample_capability("deprecated_cap", "1")
    deprecated.status = CapabilityStatus.DEPRECATED
    registry.register(verified)
    registry.register(deprecated)

    active = registry.list_active()
    assert len(active) == 1
    assert active[0].name == "verified_cap"
