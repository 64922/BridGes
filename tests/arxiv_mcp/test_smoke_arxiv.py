"""Issue 01：真实 arXiv smoke 的状态与脱敏输出测试。"""

from __future__ import annotations

from datetime import UTC, datetime

from bridges.arxiv_mcp.client import ArxivMcpError
from bridges.arxiv_mcp.contracts import ArxivPaper
from scripts.smoke_arxiv_search import run_smoke


def _paper() -> ArxivPaper:
    return ArxivPaper(
        arxiv_id="2401.12345v2",
        title="Quantum Error Correction",
        authors=["Ada Lovelace"],
        published_at=datetime(2024, 1, 18, tzinfo=UTC),
        abs_url="https://arxiv.org/abs/2401.12345v2",
        pdf_url="https://arxiv.org/pdf/2401.12345v2",
        abstract="An official abstract.",
    )


class _Client:
    def __init__(self, result: list[ArxivPaper] | ArxivMcpError) -> None:
        self.result = result
        self.closed = False

    def search(self, query: str, *, max_results: int = 5) -> list[ArxivPaper]:
        if isinstance(self.result, ArxivMcpError):
            raise self.result
        return self.result

    def close(self) -> None:
        self.closed = True


def test_smoke_is_inconclusive_without_explicit_opt_in() -> None:
    report = run_smoke(enabled=False)

    assert report.status == "inconclusive"
    assert report.error_category == "opt_in_required"
    assert report.result_count == 0


def test_smoke_reports_passed_with_provider_metadata() -> None:
    report = run_smoke(client=_Client([_paper()]), enabled=True)

    assert report.status == "passed"
    assert report.provider == "arxiv"
    assert report.result_count == 1
    assert report.query_fingerprint != "quantum error correction"


def test_smoke_reports_network_unavailability_as_inconclusive() -> None:
    report = run_smoke(
        client=_Client(
            ArxivMcpError(
                "arxiv_offline",
                "不应输出的详细错误",
                upstream_status="network",
            )
        ),
        enabled=True,
    )

    assert report.status == "inconclusive"
    assert report.error_code == "arxiv_offline"
    assert report.error_category == "network"
    assert "不应输出" not in report.as_json()


def test_smoke_reports_permission_boundary_as_inconclusive() -> None:
    report = run_smoke(
        client=_Client(
            ArxivMcpError(
                "arxiv_permission",
                "不应输出的详细错误",
                permission=True,
                upstream_status="http_4xx_permission",
            )
        ),
        enabled=True,
    )

    assert report.status == "inconclusive"
    assert report.error_category == "http_4xx_permission"
