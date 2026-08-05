"""内置 SKILL 注册表测试（Issue 28）。"""

from __future__ import annotations

import pytest

from bridges.contracts.humanizer import HumanizerSkillManifest
from bridges.skills.registry import (
    SkillRegistryError,
    create_builtin_registry,
)


def test_builtin_registry_registers_humanizer_read_only() -> None:
    registry = create_builtin_registry()
    manifest = registry.get("bridges-humanizer")
    assert isinstance(manifest, HumanizerSkillManifest)
    assert manifest.read_only is True
    assert manifest.version == "1.0.0"
    assert manifest.name == "文章人味化"
    assert manifest.license  # 许可证声明存在
    assert manifest.source  # 来源说明存在
    assert manifest.capabilities  # 能力清单存在


def test_builtin_registry_lists_sorted() -> None:
    registry = create_builtin_registry()
    manifests = registry.list_builtin()
    assert [m.skill_id for m in manifests] == sorted(m.skill_id for m in manifests)
    assert all(m.read_only for m in manifests)


def test_get_unknown_skill_raises() -> None:
    registry = create_builtin_registry()
    with pytest.raises(SkillRegistryError) as exc_info:
        registry.get("not-a-skill")
    assert exc_info.value.code == "skill_not_found"


def test_duplicate_register_same_version_is_idempotent() -> None:
    registry = create_builtin_registry()
    manifest = registry.get("bridges-humanizer")
    registry.register(manifest)  # 同版本重复注册幂等
    assert registry.get("bridges-humanizer").version == "1.0.0"


def test_duplicate_register_conflicting_version_rejected() -> None:
    registry = create_builtin_registry()
    manifest = registry.get("bridges-humanizer")
    conflicting = manifest.model_copy(update={"version": "9.9.9"})
    with pytest.raises(SkillRegistryError) as exc_info:
        registry.register(conflicting)
    assert exc_info.value.code == "version_conflict"


def test_is_builtin() -> None:
    registry = create_builtin_registry()
    assert registry.is_builtin("bridges-humanizer")
    assert not registry.is_builtin("user-uploaded-thing")
