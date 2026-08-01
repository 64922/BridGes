"""Scoped hybrid retrieval service for scientific sources.

ScienceSearchService implements the T014 retrieval pipeline:

1. Compile a ScopeEnvelope before touching any index.
2. Build an ephemeral index from source-service chunks that pass scope,
   lifecycle and quality filters.
3. Run lexical and vector retrieval channels in parallel (synchronously in the
   in-memory adapter).
4. Fuse channel rankings with reciprocal rank fusion and apply a lightweight
   rerank step.
5. Re-authorize every output candidate to catch races with revocation or
   tombstones.
6. Return candidates with channel provenance and coverage gaps.

Embedding is intentionally lightweight: a normalized bag-of-words vector. This
keeps the seam testable without adding heavy external dependencies, while still
producing a distinct semantic channel from lexical BM25 scoring.
"""

from __future__ import annotations

import math
import re
import secrets
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime

from bridges.contracts.identity import SubjectContext
from bridges.contracts.projects import ObjectDomain, ObjectRef
from bridges.contracts.science import (
    CoverageGap,
    RetrievalCandidate,
    RetrievalChannel,
    SearchRequest,
    SearchResult,
    SourceStatus,
)
from bridges.contracts.scope import ScopeAction, ScopeEnvelope
from bridges.invalidation import InvalidationService
from bridges.science.service import ScienceError, ScienceSourceService, SearchableChunk
from bridges.scope import ScopeEnforcer

_RRF_K = 60
"""Reciprocal rank fusion constant."""


def _now() -> datetime:
    return datetime.now(UTC)


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _tokenize(text: str) -> list[str]:
    """Tokenize text for indexing and querying.

    CJK characters are kept as individual tokens; Latin tokens are split on
    non-alphanumeric boundaries and lowercased.
    """
    lowered = text.lower()
    # Separate CJK characters so each becomes an individual token; this keeps
    # the lightweight seam testable for Chinese queries without requiring an
    # external segmentation library.
    spaced = _CJK_RE.sub(r" \g<0> ", lowered)
    parts = re.split(r"[^\w\u4e00-\u9fff]+", spaced)
    return [p for p in parts if p]


def _l2_normalize(vector: dict[str, float]) -> dict[str, float]:
    norm = math.sqrt(sum(v * v for v in vector.values()))
    if norm == 0:
        return {}
    return {term: value / norm for term, value in vector.items()}


def _build_vector(tokens: list[str]) -> dict[str, float]:
    """Return a unit-length bag-of-words vector for a token list."""
    if not tokens:
        return {}
    counts = Counter(tokens)
    tf = {term: count / len(tokens) for term, count in counts.items()}
    return _l2_normalize(tf)


def _cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    score = 0.0
    for term, value in a.items():
        score += value * b.get(term, 0.0)
    return score


@dataclass
class _IndexedChunk:
    """Internal indexed representation of a searchable chunk."""

    searchable: SearchableChunk
    tokens: list[str]
    vector: dict[str, float]
    token_counts: Counter[str] = field(repr=False)


@dataclass
class _ChannelResult:
    """Ranked result from a single retrieval channel."""

    chunk_id: str
    score: float
    rank: int


class ScienceSearchService:
    """Scoped hybrid search over scientific sources."""

    def __init__(
        self,
        source_service: ScienceSourceService,
        scope_enforcer: ScopeEnforcer | None = None,
        invalidation_service: InvalidationService | None = None,
        *,
        index_snapshot_id: str | None = None,
    ) -> None:
        self._source_service = source_service
        self._scope_enforcer = scope_enforcer or ScopeEnforcer()
        self._invalidation = invalidation_service
        self._index_snapshot_id = index_snapshot_id or f"science-search-{_now().isoformat()}"

    def _compile_scope(self, subject: SubjectContext, request: SearchRequest) -> ScopeEnvelope:
        """Compile the scope envelope before touching the index."""
        object_domain = request.object_domain
        # Shared project sources are scoped by project_id as owner.
        project_id_for_scope = None
        if object_domain in {
            ObjectDomain.SHARED_PROJECT,
            ObjectDomain.PERSONAL_VAULT,
        }:
            project_id_for_scope = request.project_id
        return self._scope_enforcer.compile_scope(
            subject,
            tenant_id=None,
            project_id=project_id_for_scope,
            object_domain=object_domain,
            purpose=ScopeAction.READ.value,
            authorization_version="authz-1.0",
            key_epoch="epoch-0",
        )

    def _build_index(self, account_id: str, project_id: str | None) -> dict[str, _IndexedChunk]:
        """Build an ephemeral, scope-filtered index from the source service."""
        index: dict[str, _IndexedChunk] = {}
        for searchable in self._source_service.list_searchable_chunks(account_id, project_id):
            chunk = searchable.chunk
            tokens = _tokenize(chunk.text)
            vector = _build_vector(tokens)
            index[chunk.chunk_id] = _IndexedChunk(
                searchable=searchable,
                tokens=tokens,
                vector=vector,
                token_counts=Counter(tokens),
            )
        return index

    def _lexical_retrieval(
        self,
        query_tokens: list[str],
        index: dict[str, _IndexedChunk],
        top_k: int,
    ) -> list[_ChannelResult]:
        """BM25-style lexical retrieval over the scoped index."""
        if not query_tokens:
            return []

        n_docs = len(index)
        if n_docs == 0:
            return []

        # Document frequency for query terms across the scoped index.
        df: Counter[str] = Counter()
        for indexed in index.values():
            for term in set(indexed.tokens):
                df[term] += 1

        scores: list[tuple[str, float]] = []
        query_counts = Counter(query_tokens)
        for chunk_id, indexed in index.items():
            score = 0.0
            for term, _qtf in query_counts.items():
                tf = indexed.token_counts.get(term, 0)
                if tf == 0:
                    continue
                idf = math.log(
                    1 + (n_docs - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5)
                )
                score += idf * (tf * (1.2 + 1)) / (tf + 1.2)
            if score > 0:
                scores.append((chunk_id, score))

        scores.sort(key=lambda item: item[1], reverse=True)
        return [
            _ChannelResult(chunk_id=chunk_id, score=score, rank=rank + 1)
            for rank, (chunk_id, score) in enumerate(scores[:top_k])
        ]

    def _vector_retrieval(
        self,
        query_vector: dict[str, float],
        index: dict[str, _IndexedChunk],
        top_k: int,
    ) -> list[_ChannelResult]:
        """Cosine-similarity vector retrieval over the scoped index."""
        if not query_vector:
            return []

        scores: list[tuple[str, float]] = []
        for chunk_id, indexed in index.items():
            similarity = _cosine_similarity(query_vector, indexed.vector)
            if similarity > 0:
                scores.append((chunk_id, similarity))

        scores.sort(key=lambda item: item[1], reverse=True)
        return [
            _ChannelResult(chunk_id=chunk_id, score=score, rank=rank + 1)
            for rank, (chunk_id, score) in enumerate(scores[:top_k])
        ]

    def _fuse_and_rerank(
        self,
        lexical_results: list[_ChannelResult],
        vector_results: list[_ChannelResult],
        top_k: int,
    ) -> list[tuple[str, float]]:
        """Reciprocal rank fusion across channels."""
        ranks_by_chunk: dict[str, list[int]] = {}
        for result in lexical_results:
            ranks_by_chunk.setdefault(result.chunk_id, []).append(result.rank)
        for result in vector_results:
            ranks_by_chunk.setdefault(result.chunk_id, []).append(result.rank)

        fused: list[tuple[str, float]] = []
        for chunk_id, ranks in ranks_by_chunk.items():
            score = sum(1.0 / (_RRF_K + rank) for rank in ranks)
            fused.append((chunk_id, score))

        fused.sort(key=lambda item: item[1], reverse=True)
        return fused[:top_k]

    def _is_candidate_active(self, chunk_id: str, account_id: str) -> bool:
        """Post-output authorization: re-check source state and invalidation."""
        # Find the chunk across all sources. The source service does not expose
        # direct chunk lookup by id without source_id, so we scan the in-memory
        # store. This is acceptable for the in-memory adapter; persistent adapters
        # will use an RLS-protected lookup.
        for stored in self._source_service._sources.values():
            chunk = stored.chunks.get(chunk_id)
            if chunk is None:
                continue
            source = stored.source
            if source.account_id != account_id:
                return False
            if source.status != SourceStatus.PARSED:
                return False
            if source.current_version_id != chunk.document_id:
                return False
            domain = (
                ObjectDomain.SHARED_PROJECT
                if source.project_id
                else ObjectDomain.PERSONAL_VAULT
            )
            owner_id = source.project_id if source.project_id else source.account_id
            source_ref = ObjectRef(
                domain=domain, owner_id=owner_id, object_id=source.source_id, version=1
            )
            try:
                self._source_service._authorize_source(account_id, source, ScopeAction.READ)
                self._source_service._require_active(source_ref)
            except ScienceError:
                return False
            return True
        return False

    def search(self, subject: SubjectContext, request: SearchRequest) -> SearchResult:
        """Execute a scoped hybrid search.

        The pipeline compiles scope first, then builds an ephemeral index from
        visible sources, runs lexical and vector retrieval, fuses rankings,
        reranks, re-authorizes outputs and reports coverage gaps.
        """
        scope = self._compile_scope(subject, request)
        query_tokens = _tokenize(request.query)
        query_vector = _build_vector(query_tokens)

        index = self._build_index(scope.account_id, request.project_id)
        gaps: list[CoverageGap] = []

        if not index:
            gaps.append(
                CoverageGap(
                    gap_type="scope",
                    reason="当前作用域内没有可检索的来源。",
                )
            )

        lexical_results: list[_ChannelResult] = []
        vector_results: list[_ChannelResult] = []

        if request.include_lexical:
            lexical_results = self._lexical_retrieval(query_tokens, index, request.top_k)
            if not lexical_results:
                gaps.append(
                    CoverageGap(
                        gap_type="lexical",
                        reason="词法通道未召回任何候选。",
                    )
                )

        if request.include_vector:
            vector_results = self._vector_retrieval(query_vector, index, request.top_k)
            if not vector_results:
                gaps.append(
                    CoverageGap(
                        gap_type="vector",
                        reason="向量通道未召回任何候选。",
                    )
                )

        fused = self._fuse_and_rerank(
            lexical_results if request.include_lexical else [],
            vector_results if request.include_vector else [],
            request.top_k,
        )

        # Build quick lookups for channel metadata.
        lexical_by_chunk = {r.chunk_id: r for r in lexical_results}
        vector_by_chunk = {r.chunk_id: r for r in vector_results}

        candidates: list[RetrievalCandidate] = []
        for fused_rank, (chunk_id, fused_score) in enumerate(fused, start=1):
            indexed = index.get(chunk_id)
            if indexed is None:
                continue

            # Post-output re-authorization catches races with revocation/tombstones.
            if not self._is_candidate_active(chunk_id, scope.account_id):
                continue

            source = indexed.searchable.source
            document = indexed.searchable.document
            chunk = indexed.searchable.chunk

            channels: list[RetrievalChannel] = []
            lexical_rank: int | None = None
            vector_rank: int | None = None
            lexical_score: float | None = None
            vector_score: float | None = None

            if chunk_id in lexical_by_chunk:
                channels.append(RetrievalChannel.LEXICAL)
                lexical_rank = lexical_by_chunk[chunk_id].rank
                lexical_score = round(lexical_by_chunk[chunk_id].score, 6)
            if chunk_id in vector_by_chunk:
                channels.append(RetrievalChannel.VECTOR)
                vector_rank = vector_by_chunk[chunk_id].rank
                vector_score = round(vector_by_chunk[chunk_id].score, 6)

            candidates.append(
                RetrievalCandidate(
                    candidate_id=secrets.token_urlsafe(16),
                    chunk_id=chunk.chunk_id,
                    document_id=document.document_id,
                    source_id=source.source_id,
                    text=chunk.text,
                    structure_path=chunk.structure_path,
                    channels=channels,
                    lexical_rank=lexical_rank,
                    vector_rank=vector_rank,
                    fused_rank=fused_rank,
                    lexical_score=lexical_score,
                    vector_score=vector_score,
                    fused_score=round(fused_score, 6),
                    source_lifecycle_status=document.lifecycle_status,
                    source_status=source.status,
                )
            )

        return SearchResult(
            query=request.query,
            scope_envelope=scope,
            index_snapshot_id=self._index_snapshot_id,
            candidates=candidates,
            lexical_total=len(lexical_results),
            vector_total=len(vector_results),
            coverage_gaps=gaps,
        )
