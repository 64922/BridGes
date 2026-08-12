"""版本化 judge registry 与 panel 门（Test plan 7：多样性与可用性门）。"""

from __future__ import annotations

import pytest

from bridges.humanize_eval.generation import GenerationParameters
from bridges.humanize_eval.registry import (
    JudgeRegistry,
    JudgeRegistryError,
    active_judges,
    build_default_registry,
    panel_gate_issues,
    register_judge,
)


def _registry(**kwargs) -> JudgeRegistry:
    defaults = dict(
        generation_family="qwen",
        canary_sha256="canary-sha",
    )
    defaults.update(kwargs)
    return JudgeRegistry(**defaults)


def _register(registry: JudgeRegistry, *, judge_id: str, family: str, **kwargs):
    return register_judge(
        registry,
        judge_id=judge_id,
        model_family=family,
        provider=f"provider-{family}",
        model_id=f"{family}-model",
        judge_version="v1",
        system_prompt_sha256="sha",
        schema_version="judge-schema-v2",
        parameters=GenerationParameters(),
        **kwargs,
    )


def test_registry_records_full_registration():
    """登记项记录模型家族/提供方/模型快照/版本/提示哈希/schema/参数。"""
    registry = _register(_registry(), judge_id="j1", family="family-a")
    registration = registry.registrations["j1"]
    assert registration.model_family == "family-a"
    assert registration.provider == "provider-family-a"
    assert registration.model_id == "family-a-model"
    assert registration.system_prompt_sha256 == "sha"
    assert registration.schema_version == "judge-schema-v2"
    assert registration.parameters.temperature == 0.7
    assert registration.enabled is False  # 未通过 canary 前不启用


def test_registry_versioning_and_digest():
    """registry 版本化：内容变化改变 digest（进入运行锁）。"""
    registry_a = _register(_registry(), judge_id="j1", family="family-a")
    registry_b = _register(_registry(), judge_id="j1", family="family-b")
    assert registry_a.digest() != registry_b.digest()
    # 同一内容 digest 稳定（可重放）。
    registry_c = _register(_registry(), judge_id="j1", family="family-a")
    assert registry_a.digest() == registry_c.digest()


def test_duplicate_registration_rejected():
    """预注册只追加：重复登记同一裁判拒绝。"""
    registry = _register(_registry(), judge_id="j1", family="family-a")
    with pytest.raises(JudgeRegistryError, match="重复登记"):
        _register(registry, judge_id="j1", family="family-a")


def test_panel_requires_three_judges():
    """正式 panel 少于三个有效裁判 → 门不通过（inconclusive）。"""
    registry = _register(_registry(), judge_id="j1", family="family-a")
    issues = panel_gate_issues(registry)
    assert any("少于 3" in issue for issue in issues)


def test_panel_requires_diversity():
    """三个同家族裁判 → 多样性不足（inconclusive）。"""
    registry = _registry()
    for index in range(1, 4):
        registry = _register(registry, judge_id=f"j{index}", family="qwen")
    issues = panel_gate_issues(registry)
    assert any("多样性不足" in issue for issue in issues)


def test_panel_ok_with_three_distinct_families():
    """三个不同家族/提供方且都不与生成模型同族 → 门通过。"""
    registry = _registry(generation_family="qwen")
    for index, family in enumerate(("family-a", "family-b", "family-c")):
        registry = _register(registry, judge_id=f"j{index}", family=family)
    # 模拟全部通过 canary 门（enabled）。
    registry = registry.model_copy(update={
        "registrations": {
            judge_id: reg.model_copy(update={"enabled": True})
            for judge_id, reg in registry.registrations.items()
        }
    })
    assert panel_gate_issues(registry) == []


def test_panel_forbidden_when_all_same_family_as_generation():
    """全部裁判与候选生成模型同家族 → 不得作为正式 panel 结论来源。"""
    registry = _registry(generation_family="qwen")
    for index in range(1, 4):
        registry = _register(registry, judge_id=f"j{index}", family="qwen")
    issues = panel_gate_issues(registry)
    assert any("与候选生成模型同家族" in issue for issue in issues)


def test_active_judges_only_enabled():
    """只有通过 canary 门（enabled）的裁判进入 active panel。"""
    registry = _register(_registry(), judge_id="j1", family="family-a")
    assert active_judges(registry) == []
    registry = registry.model_copy(update={
        "registrations": {
            **registry.registrations,
            "j1": registry.registrations["j1"].model_copy(
                update={"canary_status": "passed", "enabled": True}
            ),
        }
    })
    assert [reg.judge_id for reg in active_judges(registry)] == ["j1"]


def test_default_registry_qwen_only_inconclusive():
    """默认 registry（唯一 Qwen 家族）：多样性不足，门不通过。"""
    registry = build_default_registry(generation_model_id="qwen3.7-plus")
    assert len(registry.judge_ids) == 3
    assert panel_gate_issues(registry), "唯一模型家族下 panel 门必须不通过"
    # registry 哈希进入运行锁：与裁判提示哈希一致。
    assert registry.digest()
