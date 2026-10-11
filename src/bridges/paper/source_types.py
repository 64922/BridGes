"""学术来源候选、元数据与调用预算合同。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from bridges.contracts.modules import ModuleQueryRecord

ARXIV_SOURCE = "arxiv"


CROSSREF_SOURCE = "crossref"


OPENALEX_SOURCE = "openalex"


CROSSREF_ENDPOINT = "https://api.crossref.org/works"


OPENALEX_ENDPOINT = "https://api.openalex.org/works"


ENRICH_TIMEOUT_SECONDS = 6.0


ENRICH_MAX_LOOKUPS = 5


TITLE_MATCH_THRESHOLD = 0.82


@dataclass(frozen=True)
class PaperCandidate:
    """单一来源返回的论文记录；跨来源融合在边界处生成新对象。"""

    arxiv_id: str | None
    title: str
    authors: list[str]
    published_at: datetime
    abs_url: str
    pdf_url: str
    abstract: str
    primary_category: str | None = None
    source: str = ARXIV_SOURCE
    source_id: str | None = None
    doi: str | None = None
    openalex_id: str | None = None
    cited_by_count: int | None = None
    venue: str | None = None
    full_text_url: str | None = None
    is_open_access: bool | None = None
    sources: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    discovery_paths: tuple[str, ...] = ()
    referenced_works: tuple[str, ...] = ()

    @property
    def published_year(self) -> int | None:
        """返回已知年份；日期解析的 0001 哨兵表示来源未给出年份。"""
        return self.published_at.year if self.published_at.year > 1 else None

    @property
    def candidate_id(self) -> str:
        from bridges.paper.identity import paper_candidate_id

        return paper_candidate_id(self)


@dataclass(frozen=True)
class CandidateSearchOutcome:
    """一次 arXiv 检索的结果与统一记录。"""

    query: str
    candidates: list[PaperCandidate]
    record: ModuleQueryRecord


@dataclass(frozen=True)
class EnrichedMetadata:
    """一条学术元数据补充结果（按来源标注，绝不混为 arXiv 元数据）。"""

    source: str
    matched_title: str | None = None
    doi: str | None = None
    cited_by_count: int | None = None
    venue: str | None = None
    year: int | None = None
    is_open_access: bool | None = None
    full_text_url: str | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class EnrichOutcome:
    """一轮补充检索的结果、记录与逐篇元数据（按 arxiv_id 归档）。"""

    metadata: dict[str, EnrichedMetadata] = field(default_factory=dict)
    records: list[ModuleQueryRecord] = field(default_factory=list)


class MetadataResponseError(Exception):
    """上游元数据响应无法使用（解析失败/非预期结构）。"""


class ExternalCallBudget(Protocol):
    """外部来源共享的运行调用额度。"""

    def register_external(self, call_key: str, *, purpose: str) -> bool: ...

    def release_external(self, call_key: str, *, outcome_code: str) -> None: ...
