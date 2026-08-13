"""Issue 01：显式联网的 arXiv 真实 smoke。

默认只输出 ``inconclusive``，不会发起网络请求。设置
``BRIDGES_ARXIV_SMOKE=1`` 后，脚本才会用一个低成本普通主题访问官方
Atom API。输出只包含提供方、状态、延迟、结果数量和脱敏错误类别。
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Protocol

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bridges.arxiv_mcp.client import ArxivMcpClient, ArxivMcpError  # noqa: E402
from bridges.arxiv_mcp.contracts import ArxivPaper  # noqa: E402

SMOKE_QUERY = "quantum error correction"
_INCONCLUSIVE_STATUSES = frozenset(
    {
        "network",
        "timeout",
        "permission",
        "http_4xx_permission",
        "http_429",
        "http_5xx",
    }
)


class _SmokeClient(Protocol):
    def search(self, query: str, *, max_results: int = 5) -> list[ArxivPaper]: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class ArxivSmokeReport:
    provider: str
    status: str
    query_type: str
    latency_ms: int
    result_count: int
    query_fingerprint: str
    error_code: str | None = None
    error_category: str | None = None
    reason: str | None = None

    def as_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


def run_smoke(
    client: _SmokeClient | None = None,
    *,
    enabled: bool | None = None,
) -> ArxivSmokeReport:
    """运行一次真实探针；``enabled=False`` 时保证不创建网络客户端。"""
    started = time.monotonic()
    if enabled is None:
        enabled = os.getenv("BRIDGES_ARXIV_SMOKE") == "1"
    fingerprint = sha256(SMOKE_QUERY.encode("utf-8")).hexdigest()[:16]
    if not enabled:
        return ArxivSmokeReport(
            provider="arxiv",
            status="inconclusive",
            query_type="ordinary",
            latency_ms=0,
            result_count=0,
            query_fingerprint=fingerprint,
            error_category="opt_in_required",
            reason="BRIDGES_ARXIV_SMOKE=1 未设置，未发起网络请求",
        )

    owns_client = client is None
    active_client: _SmokeClient | None = client
    try:
        if active_client is None:
            active_client = ArxivMcpClient(timeout=15.0)
        papers = active_client.search(SMOKE_QUERY, max_results=1)
        if not papers:
            return _report(
                started,
                fingerprint,
                status="failed",
                result_count=0,
                error_code="arxiv_no_results",
                error_category="empty_response",
            )
        paper = papers[0]
        if not _has_official_metadata(paper):
            return _report(
                started,
                fingerprint,
                status="failed",
                result_count=len(papers),
                error_code="arxiv_contract",
                error_category="atom_contract",
            )
        return _report(
            started,
            fingerprint,
            status="passed",
            result_count=len(papers),
        )
    except ArxivMcpError as exc:
        category = exc.upstream_status or exc.code
        status = (
            "inconclusive" if category in _INCONCLUSIVE_STATUSES else "failed"
        )
        return _report(
            started,
            fingerprint,
            status=status,
            result_count=0,
            error_code=exc.code,
            error_category=category,
        )
    except Exception:
        return _report(
            started,
            fingerprint,
            status="failed",
            result_count=0,
            error_code="arxiv_smoke_internal",
            error_category="internal",
        )
    finally:
        if owns_client and active_client is not None:
            active_client.close()


def _report(
    started: float,
    fingerprint: str,
    *,
    status: str,
    result_count: int,
    error_code: str | None = None,
    error_category: str | None = None,
) -> ArxivSmokeReport:
    return ArxivSmokeReport(
        provider="arxiv",
        status=status,
        query_type="ordinary",
        latency_ms=max(0, int((time.monotonic() - started) * 1000)),
        result_count=result_count,
        query_fingerprint=fingerprint,
        error_code=error_code,
        error_category=error_category,
    )


def _has_official_metadata(paper: ArxivPaper) -> bool:
    return bool(
        paper.arxiv_id
        and paper.title
        and paper.authors
        and paper.abs_url.startswith("https://arxiv.org/abs/")
        and paper.pdf_url.startswith("https://arxiv.org/pdf/")
    )


def main() -> int:
    report = run_smoke()
    print(report.as_json(), flush=True)
    return {"passed": 0, "failed": 1, "inconclusive": 2}[report.status]


if __name__ == "__main__":
    raise SystemExit(main())
