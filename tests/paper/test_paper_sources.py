"""``bridges.paper.sources``：元数据补充的核对、有限尝试与披露审计。

补充来源只作有限尝试：标题核对不过就当没找到（宁缺勿错配），失败只记账不
重试；每次外发请求留下账户归属的脱敏披露审计（只记来源、标题指纹与状态）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from bridges.contracts.observability import AuditAction, AuditResult
from bridges.observability.service import ObservabilityService
from bridges.paper.sources import MetadataEnricher, PaperCandidate

ACCOUNT_ID = "account-sources"


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    """按 URL 片段返回固定 JSON 的假客户端（记录每次请求的参数）。"""

    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, *, params: dict[str, str], **_: Any) -> _FakeResponse:
        self.calls.append((url, params))
        for fragment, response in self._responses.items():
            if fragment in url:
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"未配置的 URL：{url}")

    def close(self) -> None:
        return None


def _candidate(title: str = "Attention Is All You Need") -> PaperCandidate:
    return PaperCandidate(
        arxiv_id="1706.03762",
        title=title,
        authors=["Ashish Vaswani"],
        published_at=datetime(2017, 6, 12, tzinfo=UTC),
        abs_url="https://arxiv.org/abs/1706.03762",
        pdf_url="https://arxiv.org/pdf/1706.03762",
        abstract="The dominant sequence transduction models are based on attention.",
        primary_category="cs.CL",
    )


def test_crossref_match_requires_title_similarity() -> None:
    """标题相似度不足时视为没有找到，绝不把别人的元数据挂到这篇论文上。"""
    client = _FakeClient(
        {
            "crossref": _FakeResponse(
                {"message": {"items": [{"title": ["A Different Paper Entirely"]}]}}
            ),
            "openalex": _FakeResponse({"results": []}),
        }
    )
    enricher = MetadataEnricher(client=client)  # type: ignore[arg-type]
    outcome = enricher.enrich(
        [_candidate()], account_id=ACCOUNT_ID, need_publication_info=True
    )
    assert outcome.metadata == {}
    assert all(record.status.value == "empty" for record in outcome.records)
    assert all(record.error_code for record in outcome.records)


def test_matching_metadata_merges_openalex_full_text() -> None:
    client = _FakeClient(
        {
            "crossref": _FakeResponse(
                {
                    "message": {
                        "items": [
                            {
                                "title": ["Attention is all you need"],
                                "DOI": "10.1/abc",
                                "is-referenced-by-count": 120000,
                                "container-title": ["NeurIPS"],
                                "issued": {"date-parts": [[2017, 6, 12]]},
                            }
                        ]
                    }
                }
            ),
            "openalex": _FakeResponse(
                {
                    "results": [
                        {
                            "title": "Attention Is All You Need",
                            "cited_by_count": 130000,
                            "publication_year": 2017,
                            "best_oa_location": {"pdf_url": "https://example.org/a.pdf"},
                            "open_access": {"is_oa": True},
                        }
                    ]
                }
            ),
        }
    )
    enricher = MetadataEnricher(client=client)  # type: ignore[arg-type]
    outcome = enricher.enrich(
        [_candidate()], account_id=ACCOUNT_ID, need_publication_info=True
    )
    metadata = outcome.metadata["1706.03762"]
    assert metadata.venue == "NeurIPS"
    assert metadata.cited_by_count == 130000
    assert metadata.full_text_url == "https://example.org/a.pdf"
    # 最小查询词：只发送论文标题，不含账户或私有上下文。
    assert all(params != {} for _, params in client.calls)
    for _, params in client.calls:
        assert ACCOUNT_ID not in str(params)
        assert set(params.values()) == {_candidate().title} or _candidate().title in params.values()


def test_every_lookup_leaves_account_attributed_disclosure_audit() -> None:
    """云端披露记录：每次外发请求都有账户归属、服务、数据类别与状态。"""
    client = _FakeClient(
        {
            "crossref": _FakeResponse({"message": {"items": []}}),
            "openalex": _FakeResponse({"results": []}, status_code=429),
        }
    )
    observability = ObservabilityService()
    enricher = MetadataEnricher(
        client=client,  # type: ignore[arg-type]
        observability=observability,
    )
    enricher.enrich([_candidate()], account_id=ACCOUNT_ID, need_publication_info=True)

    events = observability.list_audit_events(
        account_id=ACCOUNT_ID, action=AuditAction.ACADEMIC_METADATA_LOOKUP
    )
    assert len(events) == 2, "Crossref 与 OpenAlex 各一次，逐次记账"
    providers = {event.details["provider"] for event in events}
    assert providers == {"crossref", "openalex"}
    assert all(event.details["data_categories"] == ["public_query_terms"] for event in events)
    assert all("query_fingerprint" in event.details for event in events)
    # 审计只记指纹与长度，不保存标题正文。
    assert all(_candidate().title not in str(event.details) for event in events)


def test_enrich_skips_upstream_when_not_needed() -> None:
    client = _FakeClient({})
    enricher = MetadataEnricher(client=client)  # type: ignore[arg-type]
    outcome = enricher.enrich(
        [_candidate()], account_id=ACCOUNT_ID, need_publication_info=False
    )
    assert outcome.records == []
    assert client.calls == []


def test_lookups_are_bounded_per_round() -> None:
    """有限尝试：每篇论文每来源最多一次，超出上限的候选不再外发。"""
    client = _FakeClient(
        {
            "crossref": _FakeResponse({"message": {"items": []}}),
            "openalex": _FakeResponse({"results": []}),
        }
    )
    enricher = MetadataEnricher(client=client, max_lookups=2)  # type: ignore[arg-type]
    enricher.enrich(
        [_candidate(f"Paper {index}") for index in range(5)],
        account_id=ACCOUNT_ID,
        need_publication_info=True,
    )
    assert len(client.calls) == 4, "2 篇候选 × 2 个来源"


def test_timeout_is_recorded_and_retryable() -> None:
    import httpx

    client = _FakeClient(
        {
            "crossref": httpx.TimeoutException("timeout"),
            "openalex": httpx.TimeoutException("timeout"),
        }
    )
    enricher = MetadataEnricher(client=client)  # type: ignore[arg-type]
    outcome = enricher.enrich(
        [_candidate()], account_id=ACCOUNT_ID, need_publication_info=True
    )
    assert [record.error_code for record in outcome.records] == [
        "crossref_timeout",
        "openalex_timeout",
    ]
    assert all(record.retryable for record in outcome.records)
    assert outcome.metadata == {}


@pytest.mark.parametrize("status", [200])
def test_audit_result_reflects_upstream_status(status: int) -> None:
    client = _FakeClient(
        {
            "crossref": _FakeResponse({"message": {"items": []}}, status_code=status),
            "openalex": _FakeResponse({"results": []}, status_code=503),
        }
    )
    observability = ObservabilityService()
    enricher = MetadataEnricher(
        client=client,  # type: ignore[arg-type]
        observability=observability,
    )
    enricher.enrich([_candidate()], account_id=ACCOUNT_ID, need_publication_info=True)
    results = {
        event.details["provider"]: event.result
        for event in observability.list_audit_events(
            account_id=ACCOUNT_ID, action=AuditAction.ACADEMIC_METADATA_LOOKUP
        )
    }
    assert results["crossref"] is AuditResult.DEGRADED
    assert results["openalex"] is AuditResult.DEGRADED
