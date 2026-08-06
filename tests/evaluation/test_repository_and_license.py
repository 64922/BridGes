"""评测持久化与许可证审计测试（Issue 40 AC-11 / Verification-3）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from bridges.contracts.evaluation_suite import (
    BlindReviewSet,
    CaseResult,
    CaseRunStatus,
    EvaluationReport,
    SuiteRunLock,
    now_iso,
)
from bridges.evaluation import suite_data
from bridges.evaluation.repository import EvaluationRepository
from bridges.evaluation.suite_registry import SuiteRegistry
from bridges.storage.database import SCHEMA_VERSION, BridgesDatabase


@pytest.fixture
def repository(tmp_path: Path) -> EvaluationRepository:
    database = BridgesDatabase(tmp_path / "bridges.db")
    assert database.initialize() == SCHEMA_VERSION
    return EvaluationRepository(database)


def _lock() -> SuiteRunLock:
    return SuiteRunLock(
        lock_id="lock-1",
        suite_id="science-baseline",
        suite_version="1.0.0",
        suite_digest="d1",
        code_commit_or_build_digest="c1",
        runtime_identifier="eval-harness",
        os_hardware_summary="test",
        database_migration_version="26",
        config_digest="cfg",
        dataset_versions={},
        domain_pack_versions={},
        model_run_locks=[],
        prompt_versions={},
        schema_versions={},
        tool_adapter_versions={},
        judge_versions={},
        scoring_scale_versions={},
        random_seeds=[1],
        execution_count=1,
        network_cache_policy="frozen",
        created_at=now_iso(),
    )


def _result(lock_id: str, case_result_id: str) -> CaseResult:
    return CaseResult(
        case_result_id=case_result_id,
        lock_id=lock_id,
        sut_id="bridges_full",
        task_id="task-science",
        case_id="science-bell-evidence",
        seed=42,
        execution_index=0,
        status=CaseRunStatus.SUCCEEDED,
        outputs={"final_answer": "x"},
        artifacts=[],
        tool_records=[],
        state_trajectory=["done"],
        auto_assertions=[],
        metrics=[],
        judge_scores=[],
        failure_case=None,
        reproduction_command="BridGes evaluate replay",
        latency_ms=1,
        cost_estimate=None,
        created_at=now_iso(),
    )


def test_migration_26_creates_eval_tables(tmp_path: Path) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    tables = {
        row["name"]
        for row in database.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {
        "eval_suites",
        "eval_run_locks",
        "eval_case_results",
        "eval_reports",
        "eval_blind_reviews",
    } <= tables


def test_suite_and_lock_persistence(repository: EvaluationRepository) -> None:
    registry = SuiteRegistry()
    registry.register(suite_data.build_science_baseline_suite())
    suite = registry.get(suite_data.SUITE_ID)
    repository.save_suite(suite)
    loaded = repository.get_suite(suite_data.SUITE_ID, suite_data.SUITE_VERSION)
    assert loaded is not None
    assert loaded.digest() == suite.digest()

    lock = _lock()
    repository.save_lock(lock)
    loaded_lock = repository.get_lock("lock-1")
    assert loaded_lock is not None
    assert loaded_lock.lock_id == "lock-1"
    assert loaded_lock.suite_digest == "d1"


def test_results_are_append_only(repository: EvaluationRepository) -> None:
    """AC-11：同一锁的重放产生新行，旧结果不被覆盖。"""
    repository.save_result(_result("lock-1", "result-1"))
    repository.save_result(_result("lock-1", "result-2"))
    results = repository.list_results("lock-1")
    assert len(results) == 2
    assert {r.case_result_id for r in results} == {"result-1", "result-2"}
    # 旧结果仍可单独取回。
    assert repository.get_result("result-1") is not None


def test_reports_are_versioned_not_overwritten(repository: EvaluationRepository) -> None:
    from bridges.contracts.evaluation_suite import BlindReviewSummary

    report_v1 = EvaluationReport(
        report_id="report-1",
        report_version="1",
        lock_id="lock-1",
        suite_id="science-baseline",
        suite_version="1.0.0",
        generated_at=now_iso(),
        estimates=[],
        comparisons=[],
        failure_stats=[],
        slices=[],
        blind_review_summary=BlindReviewSummary(),
        failure_cases=[],
        cost_latency={},
    )
    report_v2 = report_v1.model_copy(
        update={"report_version": "2", "generated_at": now_iso()}
    )
    repository.save_report(report_v1)
    repository.save_report(report_v2)
    latest = repository.get_report("report-1")
    assert latest is not None and latest.report_version == "2"
    # 旧版本仍可追溯。
    old = repository.get_report("report-1", "1")
    assert old is not None and old.report_version == "1"


def test_review_set_persistence(repository: EvaluationRepository) -> None:
    review_set = BlindReviewSet(
        review_set_id="review-1", lock_id="lock-1", created_at=now_iso()
    )
    repository.save_review_set(review_set)
    loaded = repository.get_review_set("review-1")
    assert loaded is not None
    assert loaded.review_set_id == "review-1"


# ---------------------------------------------------------------------------
# 许可证审计（Verification-3）
# ---------------------------------------------------------------------------


def test_all_datasets_have_license_records() -> None:
    """Verification-3：数据清单每项都有许可证记录，无悬空引用。"""
    suite = suite_data.build_science_baseline_suite()
    license_ids = {record.asset_id for record in suite.licenses}
    for entry in suite.manifest:
        assert entry.license_ref in license_ids, (
            f"数据集 {entry.dataset_id} 缺少许可证记录"
        )
    for card in suite.data_cards:
        assert card.license_ref in license_ids


def test_all_licenses_are_clean_room() -> None:
    """Verification-3：外部参考仅限通用方法与公共常识，无受保护来源逐字复制。"""
    suite = suite_data.build_science_baseline_suite()
    for record in suite.licenses:
        # 来源必须是原创、方法启发或公共常识；许可必须明确。
        assert record.source
        assert record.license
        assert record.checked_at
        assert "原创" in record.source or "公共" in record.source or "启发" in record.source
    # 参考方法实现为原创。
    from bridges.evaluation.reference_method import REFERENCE_METHOD_VERSION

    assert REFERENCE_METHOD_VERSION
    reference_license = next(
        r for r in suite.licenses if r.asset_id == "license-reference-method"
    )
    assert "原创" in reference_license.source
    assert "MIT" in reference_license.license


def test_suite_data_contains_no_external_project_text() -> None:
    """数据与案例文本全部为原创（抽查关键字段，不引用外部项目原文）。"""
    suite = suite_data.build_science_baseline_suite()
    all_text = " ".join(
        [entry.title for entry in suite.manifest]
        + [task.title for task in suite.tasks]
        + [record.title for record in suite.licenses]
    )
    # 不包含任何已知外部评测集/项目的专有标识。
    for marker in ("MMLU", "HumanEval", "GSM8K", "LMSYS", "CHATGPT"):
        assert marker not in all_text
