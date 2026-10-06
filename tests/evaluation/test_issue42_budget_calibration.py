"""工单 42：09 预算初值的基线校准锁定。

初值不是性能承诺；本测试把「当前生效的冻结初值与唯一合同版本」锁成基线，
任何调整都必须带着配对实测证据显式改本文件，避免静默漂移。
"""

from __future__ import annotations

from bridges.chat.run_budget_ledger import (
    RUN_BUDGET_CONTRACT_VERSION,
    RUN_BUDGET_INITIALS,
    RunBudgetClass,
)

CALIBRATED_INITIALS: dict[str, dict[str, int]] = {
    "lightweight": {
        "total_budget_ms": 120_000,
        "verify_deliver_reserve_ms": 0,
        "model_call_limit": 6,
        "external_parallel_max": 2,
        "candidate_screen_max": 20,
        "deep_read_max": 3,
        "adjustment_rounds_max": 1,
        "transient_retry_max": 1,
    },
    "normal": {
        "total_budget_ms": 60_000,
        "verify_deliver_reserve_ms": 15_000,
        "model_call_limit": 6,
        "external_parallel_max": 2,
        "candidate_screen_max": 20,
        "deep_read_max": 3,
        "adjustment_rounds_max": 1,
        "transient_retry_max": 1,
    },
    "deep": {
        "total_budget_ms": 120_000,
        "verify_deliver_reserve_ms": 30_000,
        "model_call_limit": 8,
        "external_parallel_max": 2,
        "candidate_screen_max": 20,
        "deep_read_max": 5,
        "adjustment_rounds_max": 1,
        "transient_retry_max": 1,
    },
}


def test_contract_version_and_initials_are_frozen() -> None:
    assert RUN_BUDGET_CONTRACT_VERSION == "run-budget-v1"
    actual = {
        budget_class.value: {
            "total_budget_ms": initials.total_budget_ms,
            "verify_deliver_reserve_ms": initials.verify_deliver_reserve_ms,
            "model_call_limit": initials.model_call_limit,
            "external_parallel_max": initials.external_parallel_max,
            "candidate_screen_max": initials.candidate_screen_max,
            "deep_read_max": initials.deep_read_max,
            "adjustment_rounds_max": initials.adjustment_rounds_max,
            "transient_retry_max": initials.transient_retry_max,
        }
        for budget_class, initials in RUN_BUDGET_INITIALS.items()
    }
    assert actual == CALIBRATED_INITIALS


def test_initials_keep_control_envelope_consistency() -> None:
    lightweight = RUN_BUDGET_INITIALS[RunBudgetClass.LIGHTWEIGHT]
    normal = RUN_BUDGET_INITIALS[RunBudgetClass.NORMAL]
    deep = RUN_BUDGET_INITIALS[RunBudgetClass.DEEP]
    for initials in (lightweight, normal, deep):
        assert initials.total_budget_ms > initials.verify_deliver_reserve_ms
        assert initials.model_call_limit >= 1
        assert initials.external_parallel_max >= 1
        assert initials.candidate_screen_max >= 1
        assert initials.deep_read_max >= 0
        assert initials.adjustment_rounds_max >= 0
        assert initials.transient_retry_max <= 2
    assert deep.total_budget_ms >= normal.total_budget_ms
    assert deep.model_call_limit >= normal.model_call_limit
    assert deep.deep_read_max >= normal.deep_read_max
    assert lightweight.verify_deliver_reserve_ms == 0


def test_pairing_report_budget_snapshot_matches_ledger() -> None:
    from scripts.run_issue42_workflow_pairing import _budget_snapshot

    snapshot = _budget_snapshot()
    assert snapshot["contract_version"] == RUN_BUDGET_CONTRACT_VERSION
    assert snapshot["classes"] == CALIBRATED_INITIALS
