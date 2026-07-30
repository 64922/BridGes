"""领域包版本目录。"""

from __future__ import annotations

from science_companion.domain.loader import DomainPackLoader, _parse_version
from science_companion.domain.protocol import (
    DomainPack,
    DomainPackRegistryError,
    LoadedDomainPack,
)


class DomainPackRegistry:
    """In-memory version catalog that preserves trusted historical packs."""

    def __init__(self, loader: DomainPackLoader | None = None) -> None:
        self._loader = loader or DomainPackLoader()
        self._packs: dict[tuple[str, str], LoadedDomainPack] = {}

    def register(self, candidate: LoadedDomainPack | DomainPack) -> LoadedDomainPack:
        """Validate and register a new version without replacing history."""
        loaded = (
            candidate
            if isinstance(candidate, LoadedDomainPack)
            else self._loader.load(candidate)
        )
        key = (loaded.pack_id, loaded.pack_version)
        if key in self._packs:
            raise DomainPackRegistryError(
                f"领域包版本已登记：{loaded.pack_id}@{loaded.pack_version}。"
            )
        previous_versions = [
            pack
            for (pack_id, _), pack in self._packs.items()
            if pack_id == loaded.pack_id
        ]
        if previous_versions:
            previous = max(
                previous_versions,
                key=lambda pack: _parse_version(pack.pack_version) or (0, 0, 0),
            )
            upgrade = self._loader.validate_upgrade(previous.manifest, loaded.manifest)
            if not upgrade.compatible:
                raise DomainPackRegistryError(
                    f"领域包升级被阻断：{'；'.join(upgrade.issues)}"
                )
        self._packs[key] = loaded
        return loaded

    def get(self, pack_id: str, version: str | None = None) -> LoadedDomainPack:
        """Return an exact version, or the highest registered version."""
        candidates = [
            pack
            for (registered_id, _), pack in self._packs.items()
            if registered_id == pack_id
        ]
        if version is not None:
            for pack in candidates:
                if pack.pack_version == version:
                    return pack
        elif candidates:
            return max(candidates, key=lambda pack: _parse_version(pack.pack_version) or (0, 0, 0))
        raise DomainPackRegistryError(f"未登记领域包：{pack_id}@{version or 'latest'}。")

    def list_versions(self, pack_id: str) -> list[str]:
        """List registered versions in ascending semantic-version order."""
        versions = [
            pack.pack_version
            for (registered_id, _), pack in self._packs.items()
            if registered_id == pack_id
        ]
        return sorted(versions, key=lambda version: _parse_version(version) or (0, 0, 0))
