"""贴吧共享预算边界的外部结果分类回归。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from bridges.contracts.modules import ModuleQueryStatus
from bridges.tieba.budget import (
    BudgetedOfficialReader,
    BudgetedSearchPort,
    TiebaBudget,
    current_request_budget,
)
from bridges.tieba.contracts import TiebaOfficialCheck
from bridges.tieba.searching import HitDiagnostics, SearchOutcome, query_record


class _Ledger:
    def __init__(self) -> None:
        self.releases: list[dict[str, Any]] = []

    def register_external_call(self, **_values: Any) -> bool:
        return True

    def record_external_call_result(self, **values: Any) -> None:
        self.releases.append(values)


class _FailedOfficialReader:
    def fetch(self, url: str, **_values: Any) -> TiebaOfficialCheck:
        return TiebaOfficialCheck(
            title=url,
            url=url,
            host="jwc.ecjtu.edu.cn",
            fetched_at=datetime.now(UTC),
            status="fetch_failed",
            error_code="tieba_official_unavailable",
        )


def test_official_fetch_failure_is_not_recorded_as_completed() -> None:
    ledger = _Ledger()
    budget = TiebaBudget(
        ledger=ledger,  # type: ignore[arg-type]
        account_id="account-1",
        run_id="run-1",
        work_deadline=datetime.now(UTC) + timedelta(minutes=5),
    )
    reader = BudgetedOfficialReader(_FailedOfficialReader(), budget)  # type: ignore[arg-type]

    result = reader.fetch(
        "https://jwc.ecjtu.edu.cn/notice", terms=("宿舍",)
    )

    assert result.status == "fetch_failed"
    assert len(ledger.releases) == 1
    assert ledger.releases[0]["outcome_code"] == "fetch_failed"


def test_browser_search_uses_request_budget_without_operation_double_count() -> None:
    ledger = _Ledger()
    budget = TiebaBudget(
        ledger=ledger,  # type: ignore[arg-type]
        account_id="account-1",
        run_id="run-1",
        work_deadline=datetime.now(UTC) + timedelta(minutes=5),
    )

    class RequestScopedSearch:
        uses_per_request_budget = True

        def search_public(self, _account_id: str, **_values: Any) -> SearchOutcome:
            assert current_request_budget() is budget
            return SearchOutcome(
                record=query_record(
                    query="贴吧查询",
                    status=ModuleQueryStatus.EMPTY,
                    evidence_count=0,
                ),
                hits=(),
                candidates=(),
                rejected=(),
                diagnostics=HitDiagnostics(),
            )

    result = BudgetedSearchPort(
        RequestScopedSearch(), budget, purpose="tieba_search"
    ).search_public("account-1", query="贴吧查询", reason="用户请求")  # type: ignore[arg-type]

    assert result.record.status is ModuleQueryStatus.EMPTY
    assert ledger.releases == []
    assert current_request_budget() is None
