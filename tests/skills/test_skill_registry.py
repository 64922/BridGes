"""内置 SKILL 注册表测试（Issue 28；Issue 21 已无内置 SKILL）。

文章人味化是唯一内置 SKILL，随旧入口退役移除；注册表作为只读展示与
版本冲突校验的缝保留，本文件覆盖该缝本身（空内置清单 + 通用注册语义）。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bridges.contracts.humanizer import HumanizerSkillManifest
from bridges.skills.registry import (
    SkillRegistry,
    SkillRegistryError,
    create_builtin_registry,
)


def _manifest(
    skill_id: str = "sample-skill", version: str = "1.0.0"
) -> HumanizerSkillManifest:
    """构造一个只读 SKILL 清单（注册表缝的通用元素类型）。"""
    return HumanizerSkillManifest(
        skill_id=skill_id,
        name="示例 SKILL",
        version=version,
        description="仅用于注册表缝测试的只读清单。",
        read_only=True,
        source="测试构造",
        license="原创",
        capabilities=["示例能力"],
        registration_version="1",
        registered_at=datetime.now(UTC),
    )


def test_builtin_registry_is_empty_after_retirement() -> None:
    """Issue 21：唯一内置 SKILL（文章人味化）退役后内置清单为空。"""
    registry = create_builtin_registry()
    assert registry.list_builtin() == []
    assert not registry.is_builtin("bridges-humanizer")


def test_registered_builtin_is_read_only_and_sorted() -> None:
    registry = SkillRegistry()
    registry.register(_manifest("skill-b"))
    registry.register(_manifest("skill-a"))

    manifests = registry.list_builtin()
    assert [m.skill_id for m in manifests] == ["skill-a", "skill-b"]
    assert all(m.read_only for m in manifests)


def test_get_unknown_skill_raises() -> None:
    registry = create_builtin_registry()
    with pytest.raises(SkillRegistryError) as exc_info:
        registry.get("not-a-skill")
    assert exc_info.value.code == "skill_not_found"


def test_duplicate_register_same_version_is_idempotent() -> None:
    registry = SkillRegistry()
    registry.register(_manifest())
    registry.register(_manifest())  # 同版本重复注册幂等
    assert registry.get("sample-skill").version == "1.0.0"


def test_duplicate_register_conflicting_version_rejected() -> None:
    registry = SkillRegistry()
    registry.register(_manifest())
    conflicting = _manifest(version="9.9.9")
    with pytest.raises(SkillRegistryError) as exc_info:
        registry.register(conflicting)
    assert exc_info.value.code == "version_conflict"


def test_is_builtin() -> None:
    registry = SkillRegistry()
    registry.register(_manifest())
    assert registry.is_builtin("sample-skill")
    assert not registry.is_builtin("user-uploaded-thing")
