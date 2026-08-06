"""评测套件注册表测试（Issue 40 AC-1/AC-11）。"""

from __future__ import annotations

import pytest

from bridges.contracts.evaluation_suite import SuiteInvalidationTrigger, SuiteStatus
from bridges.evaluation import suite_data
from bridges.evaluation.suite_registry import SuiteRegistry, SuiteRegistryError


@pytest.fixture
def registry() -> SuiteRegistry:
    registry = SuiteRegistry()
    registry.register(suite_data.build_science_baseline_suite())
    return registry


def test_suite_registers_with_full_manifest(registry: SuiteRegistry) -> None:
    suite = registry.get(suite_data.SUITE_ID)
    assert suite.suite_id == suite_data.SUITE_ID
    assert suite.version == suite_data.SUITE_VERSION
    # 数据清单、许可证、任务、运行矩阵、量表、Schema、数据卡齐全。
    assert suite.manifest
    assert suite.licenses
    assert suite.tasks
    assert suite.run_matrix.entries
    assert suite.scales
    assert suite.artifact_schemas
    assert suite.data_cards
    assert suite.model_skill_pins
    assert suite.seeds


def test_suite_digest_is_sensitive_to_any_change(registry: SuiteRegistry) -> None:
    """AC-11：任一固定快照变化都改变摘要（新版本，旧结果可追溯）。"""
    suite = registry.get(suite_data.SUITE_ID)
    original = suite.digest()
    # 修改任务内容 → 摘要变化。
    mutated = suite.model_copy(deep=True)
    mutated.tasks[0] = mutated.tasks[0].model_copy(
        update={"title": "被篡改的任务标题"}
    )
    assert mutated.digest() != original
    # 修改数据清单 → 摘要变化。
    mutated2 = suite.model_copy(deep=True)
    mutated2.manifest[0] = mutated2.manifest[0].model_copy(
        update={"version": "2"}
    )
    assert mutated2.digest() != original
    # 修改评分量表 → 摘要变化。
    mutated3 = suite.model_copy(deep=True)
    mutated3.scales[0] = mutated3.scales[0].model_copy(update={"version": "2"})
    assert mutated3.digest() != original


def test_register_rejects_silent_overwrite(registry: SuiteRegistry) -> None:
    """同一标识内容不一致的登记被拒绝（防静默覆盖）。"""
    suite = registry.get(suite_data.SUITE_ID)
    mutated = suite.model_copy(deep=True)
    mutated.tasks[0] = mutated.tasks[0].model_copy(update={"title": "X"})
    with pytest.raises(SuiteRegistryError, match="静默覆盖"):
        registry.register(mutated)


def test_register_same_content_is_idempotent(registry: SuiteRegistry) -> None:
    suite = registry.get(suite_data.SUITE_ID)
    digest = registry.register(suite)
    assert digest == suite.digest()


def test_invalidate_keeps_history(registry: SuiteRegistry) -> None:
    """失效不删除历史版本（AC-11 旧结果可追溯）。"""
    invalidated = registry.invalidate(
        suite_data.SUITE_ID,
        suite_data.SUITE_VERSION,
        SuiteInvalidationTrigger.DATA_RECALL,
        "数据集被召回。",
    )
    assert invalidated.status == SuiteStatus.INVALIDATED
    assert invalidated.invalidation_trigger == SuiteInvalidationTrigger.DATA_RECALL
    # 仍可按版本取回（可追溯）。
    again = registry.get(suite_data.SUITE_ID, suite_data.SUITE_VERSION)
    assert again.status == SuiteStatus.INVALIDATED
    with pytest.raises(SuiteRegistryError, match="重复失效"):
        registry.invalidate(
            suite_data.SUITE_ID,
            suite_data.SUITE_VERSION,
            SuiteInvalidationTrigger.MANUAL,
            "再次失效。",
        )


def test_reference_integrity_validation() -> None:
    """注册校验拒绝悬空引用（许可证/数据集/量表/Schema/任务）。"""
    suite = suite_data.build_science_baseline_suite()
    broken = suite.model_copy(deep=True)
    broken.tasks[0] = broken.tasks[0].model_copy(update={"scale_id": "no-such-scale"})
    with pytest.raises(SuiteRegistryError, match="评分量表不存在"):
        SuiteRegistry().register(broken)

    no_license = suite.model_copy(deep=True)
    no_license.manifest[0] = no_license.manifest[0].model_copy(
        update={"license_ref": "missing-license"}
    )
    with pytest.raises(SuiteRegistryError, match="许可证记录不存在"):
        SuiteRegistry().register(no_license)


def test_every_case_has_expected_schema_and_scale(registry: SuiteRegistry) -> None:
    """AC-1：全部案例有预期产物 Schema 与人工量表，任务引用完整。"""
    suite = registry.get(suite_data.SUITE_ID)
    for case in suite_data.CASES:
        task = suite.task(case.task_id)
        assert case.expected_artifact_schema_id == task.expected_artifact_schema_id
        assert case.budget.max_model_calls > 0
        assert case.human_scale_id == task.scale_id
        # 案例进入运行矩阵。
        matrix_case_ids = {
            case_id
            for entry in suite.run_matrix.entries
            if entry.task_id == case.task_id
            for case_id in entry.case_ids
        }
        assert case.case_id in matrix_case_ids


def test_run_matrix_covers_all_suts_and_tasks(registry: SuiteRegistry) -> None:
    """AC-8：矩阵覆盖全部 6 种被测系统 × 全部任务。"""
    suite = registry.get(suite_data.SUITE_ID)
    coverage = suite.run_matrix.coverage()
    from bridges.evaluation.sut import build_sut_registry

    for sut_id in build_sut_registry():
        for task in suite.tasks:
            assert f"{sut_id}@{task.task_id}" in coverage
