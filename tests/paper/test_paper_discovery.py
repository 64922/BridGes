from __future__ import annotations

from datetime import UTC, datetime

import httpx

from bridges.contracts.modules import ModuleQueryStatus
from bridges.paper.discovery import CrossrefPaperSource, OpenAlexPaperSource
from bridges.paper.sources import OPENALEX_SOURCE, PaperCandidate


def _work(work_id: str, *, references: list[str] | None = None) -> dict[str, object]:
    return {
        "id": f"https://openalex.org/{work_id}",
        "ids": {},
        "title": f"Paper {work_id}",
        "publication_year": 2024,
        "publication_date": "2024-03-10",
        "authorships": [{"author": {"display_name": "Example Author"}}],
        "abstract_inverted_index": {"A": [0], "study": [1]},
        "best_oa_location": {
            "landing_page_url": f"https://example.org/{work_id}",
            "pdf_url": f"https://example.org/{work_id}.pdf",
        },
        "open_access": {"is_oa": True},
        "cited_by_count": 12,
        "referenced_works": references or [],
    }


def _seed(work_id: str) -> PaperCandidate:
    return PaperCandidate(
        arxiv_id=None,
        title=f"Seed {work_id}",
        authors=[],
        published_at=datetime(2022, 1, 1, tzinfo=UTC),
        abs_url=f"https://openalex.org/{work_id}",
        pdf_url="",
        abstract="",
        source=OPENALEX_SOURCE,
        source_id=work_id,
        openalex_id=f"https://openalex.org/{work_id}",
    )


class _Budget:
    def __init__(self, *, allow: bool = True) -> None:
        self.allow = allow
        self.registered: list[str] = []
        self.released: list[tuple[str, str]] = []

    def register_external(self, call_key: str, *, purpose: str) -> bool:
        del purpose
        self.registered.append(call_key)
        return self.allow

    def release_external(self, call_key: str, *, outcome_code: str) -> None:
        self.released.append((call_key, outcome_code))


def test_openalex_search_sends_year_bounds_and_maps_candidate() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"results": [_work("W123")]})

    source = OpenAlexPaperSource(client=httpx.Client(transport=httpx.MockTransport(respond)))
    outcome = source.search(
        "account",
        "convolutional neural network",
        max_results=30,
        year_from=2022,
        year_to=2025,
    )

    assert outcome.record.status is ModuleQueryStatus.SUCCESS
    assert requests[0].url.params["search"] == "convolutional neural network"
    assert requests[0].url.params["per-page"] == "10"
    assert requests[0].url.params["filter"] == (
        "from_publication_date:2022-01-01,to_publication_date:2025-12-31"
    )
    candidate = outcome.candidates[0]
    assert candidate.candidate_id == "openalex:w123"
    assert candidate.abstract == "A study"
    assert candidate.pdf_url == "https://example.org/W123.pdf"
    assert candidate.cited_by_count == 12


def test_crossref_search_marks_malformed_payload_as_failure() -> None:
    source = CrossrefPaperSource(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json={"message": {"wrong": []}})
            )
        )
    )

    outcome = source.search("account", "bearing fault diagnosis", max_results=8)

    assert outcome.record.status is ModuleQueryStatus.ERROR
    assert outcome.record.error_code == "crossref_parse"
    assert outcome.candidates == []


def test_crossref_search_sends_bibliographic_query_and_maps_citation_metadata() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "message": {
                    "items": [
                        {
                            "DOI": "10.5555/example",
                            "title": ["CNN for few-shot image classification"],
                            "author": [{"given": "Jane", "family": "Doe"}],
                            "published": {"date-parts": [[2024, 4, 1]]},
                            "is-referenced-by-count": 9,
                            "container-title": ["Example Journal"],
                            "URL": "https://doi.org/10.5555/example",
                            "abstract": "<jats:p>A useful abstract.</jats:p>",
                            "link": [
                                {
                                    "URL": "https://example.org/paper.pdf",
                                    "content-type": "application/pdf",
                                }
                            ],
                        }
                    ]
                }
            },
        )

    source = CrossrefPaperSource(client=httpx.Client(transport=httpx.MockTransport(respond)))
    outcome = source.search(
        "account",
        "convolutional neural network few-shot image classification",
        max_results=20,
        year_from=2022,
        year_to=2025,
    )

    assert outcome.record.status is ModuleQueryStatus.SUCCESS
    assert requests[0].url.params["query.bibliographic"].startswith("convolutional neural network")
    assert requests[0].url.params["rows"] == "10"
    assert requests[0].url.params["filter"] == "from-pub-date:2022-01-01,until-pub-date:2025-12-31"
    assert "is-referenced-by-count" in requests[0].url.params["select"]
    candidate = outcome.candidates[0]
    assert candidate.candidate_id == "doi:10.5555/example"
    assert candidate.cited_by_count == 9
    assert candidate.full_text_url == "https://example.org/paper.pdf"
    assert candidate.abstract == "A useful abstract."


def test_crossref_search_skips_null_date_parts_and_uses_a_later_date_field() -> None:
    source = CrossrefPaperSource(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={
                        "message": {
                            "items": [
                                {
                                    "title": ["A paper with partial dates"],
                                    "published": {"date-parts": [[None]]},
                                    "published-print": {"date-parts": [[2023, 9]]},
                                    "issued": {"date-parts": [[None]]},
                                }
                            ]
                        }
                    },
                )
            )
        )
    )

    outcome = source.search("account", "partial date parsing", max_results=2)

    assert outcome.record.status is ModuleQueryStatus.SUCCESS
    assert outcome.candidates[0].published_at == datetime(2023, 9, 1, tzinfo=UTC)


def test_crossref_search_keeps_missing_publication_year_unknown() -> None:
    source = CrossrefPaperSource(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={
                        "message": {
                            "items": [
                                {
                                    "DOI": "10.5555/undated",
                                    "title": ["Convolutional neural network paper"],
                                    "issued": {"date-parts": [[None]]},
                                }
                            ]
                        }
                    },
                )
            )
        )
    )

    outcome = source.search("account", "convolutional neural network", max_results=2)

    assert outcome.record.status is ModuleQueryStatus.SUCCESS
    assert outcome.candidates[0].published_year is None


def test_citation_expansion_deduplicates_works_and_keeps_only_real_edges() -> None:
    works = [
        _work("W10", references=["https://openalex.org/W1", "https://openalex.org/W2"]),
        _work("W11", references=["https://openalex.org/W1"]),
        _work("W12", references=[]),
    ]
    source = OpenAlexPaperSource(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json={"results": works})
            )
        )
    )
    budget = _Budget()

    result = source.expand_citations("account", [_seed("W1"), _seed("W2")], budget=budget)

    assert [item.candidate_id for item in result.candidates] == ["openalex:w10", "openalex:w11"]
    assert {(edge.citing_candidate_id, edge.cited_candidate_id) for edge in result.edges} == {
        ("openalex:w10", "openalex:w1"),
        ("openalex:w10", "openalex:w2"),
        ("openalex:w11", "openalex:w1"),
    }
    assert len(result.records) == 2
    assert budget.registered == ["paper.citation:W1", "paper.citation:W2"]
    assert budget.released == [
        ("paper.citation:W1", "success"),
        ("paper.citation:W2", "success"),
    ]


def test_citation_failure_is_recorded_and_releases_its_budget_reservation() -> None:
    source = OpenAlexPaperSource(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(503, json={"error": "unavailable"})
            )
        )
    )
    budget = _Budget()

    result = source.expand_citations("account", [_seed("W1")], budget=budget)

    assert result.candidates == []
    assert len(result.records) == 1
    assert result.records[0].status is ModuleQueryStatus.ERROR
    assert result.records[0].error_code == "openalex_http_503"
    assert budget.released == [("paper.citation:W1", "error")]


def test_citation_malformed_payload_is_recorded_as_failure() -> None:
    source = OpenAlexPaperSource(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json={"unexpected": []})
            )
        )
    )
    budget = _Budget()

    result = source.expand_citations("account", [_seed("W1")], budget=budget)

    assert len(result.records) == 1
    assert result.records[0].status is ModuleQueryStatus.ERROR
    assert result.records[0].error_code == "openalex_parse"
    assert budget.released == [("paper.citation:W1", "error")]


def test_citation_search_is_skipped_when_shared_budget_refuses_registration() -> None:
    requests: list[httpx.Request] = []
    source = OpenAlexPaperSource(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (
                    requests.append(request) or httpx.Response(200, json={"results": []})
                )
            )
        )
    )
    budget = _Budget(allow=False)

    result = source.expand_citations("account", [_seed("W1")], budget=budget)

    assert requests == []
    assert len(result.records) == 1
    assert result.records[0].status is ModuleQueryStatus.SKIPPED
    assert budget.released == []
