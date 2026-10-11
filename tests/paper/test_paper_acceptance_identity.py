"""独立验收：融合不得丢失来源年份、真实引用或混淆旧 arXiv ID。"""

from dataclasses import replace
from datetime import UTC, datetime

from bridges.paper.identity import merge_candidates
from bridges.paper.sources import PaperCandidate


def _candidate(**values: object) -> PaperCandidate:
    candidate = PaperCandidate(
        arxiv_id=None,
        title="Example",
        authors=[],
        published_at=datetime(1, 1, 1, tzinfo=UTC),
        abs_url="",
        pdf_url="",
        abstract="",
        source="openalex",
        source_id="W1",
    )
    return replace(candidate, **values)  # type: ignore[arg-type]


def test_native_identity_deduplicates_without_doi_and_keeps_discovery_paths() -> None:
    found = merge_candidates(
        [
            _candidate(discovery_paths=("search:first",)),
            _candidate(discovery_paths=("search:second",)),
        ]
    )
    assert len(found) == 1
    assert {"search:first", "search:second"}.issubset(found[0].discovery_paths)


def test_fusion_keeps_known_year_and_real_references_from_later_source() -> None:
    found = merge_candidates(
        [
            _candidate(source="crossref", source_id="10.1/example", doi="10.1/example"),
            _candidate(
                doi="10.1/example",
                published_at=datetime(2024, 1, 1, tzinfo=UTC),
                referenced_works=("https://openalex.org/W2",),
            ),
        ]
    )
    assert len(found) == 1
    assert found[0].published_year == 2024
    assert found[0].referenced_works == ("https://openalex.org/W2",)


def test_old_arxiv_category_ids_are_distinct_but_versions_merge() -> None:
    found = merge_candidates(
        [
            _candidate(arxiv_id="solv-int/9901001v1", source_id=None),
            _candidate(arxiv_id="solv-int/9901002v1", source_id=None),
            _candidate(arxiv_id="solv-int/9901001v2", source_id=None),
        ]
    )
    assert len(found) == 2
