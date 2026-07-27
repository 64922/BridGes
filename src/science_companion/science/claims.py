"""Claim--Evidence--Citation domain service for T015.

ClaimEvidenceService turns scoped retrieval results into a versioned graph of
locatable claims, each bound to source-versioned evidence and precise citations.
It supports:

- deterministic claim generation from search candidates;
- supports/refutes/limits evidence relations;
- precise citation locators derived from chunk structure paths and offsets;
- re-verification of citations after source version changes or revocation;
- immutable versioning of claims, evidence and citations;
- a publish gate that rejects fabricated or unlocatable critical citations.

The service intentionally does not generate free-form natural-language answers;
that responsibility lives in downstream expression nodes (T016/T025). Here the
output is a structured ClaimGraph that those nodes consume.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from science_companion.ai import ModelGateway
from science_companion.contracts.ai import ModelCallStatus, ModelRunLock
from science_companion.contracts.identity import SubjectContext
from science_companion.contracts.invalidation import (
    AffectedDownstream,
    ImpactResolver,
    InvalidationEvent,
    InvalidationEventType,
    RevalidationResult,
    RevalidationSchedule,
)
from science_companion.contracts.projects import ObjectDomain, ObjectRef
from science_companion.contracts.science import (
    Citation,
    CitationLocator,
    CitationValidationResult,
    CitationVerificationStatus,
    Claim,
    ClaimGraph,
    ClaimGraphResult,
    ClaimImportance,
    ClaimRequest,
    ClaimTrustStatus,
    ClaimType,
    Evidence,
    EvidenceRelation,
    FactLockSet,
    PublishGateCheck,
    PublishGateResult,
    RetrievalCandidate,
    ScientificQualityGateResult,
    SearchRequest,
    SearchResult,
    SourceStatus,
    ValidationReport,
)
from science_companion.contracts.workflows import RunContextEnvelope
from science_companion.invalidation import InvalidationService
from science_companion.science.fact_lock import (
    apply_honest_degradation,
    compile_fact_locks,
    update_claim_graph_with_report,
)
from science_companion.science.search import ScienceSearchService
from science_companion.science.service import ScienceError, ScienceSourceService, SearchableChunk


def _now() -> datetime:
    return datetime.now(UTC)


def _sha256_digest(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ClaimGeneratorPort(Protocol):
    """Abstract port for turning retrieval candidates into claim text.

    The default deterministic implementation keeps the seam testable without a
    live model. Production adapters may call a registered capability through the
    model gateway and still return the same structured output contract.
    """

    def generate_claims(
        self,
        query: str,
        candidates: list[RetrievalCandidate],
        run_context: RunContextEnvelope | None,
    ) -> list[tuple[str, ClaimType, ClaimImportance, str | None]]:
        """Return a list of (text, claim_type, importance, scope) tuples."""
        ...


class _DeterministicClaimGenerator:
    """Testable claim generator that derives claims from candidate text.

    This is not a model replacement; it is a deterministic placeholder that lets
    the claim-evidence-citation seam run without external dependencies. Each
    top candidate becomes one claim, and the evidence/citation machinery is the
    real product of T015.
    """

    def generate_claims(
        self,
        query: str,
        candidates: list[RetrievalCandidate],
        run_context: RunContextEnvelope | None,
    ) -> list[tuple[str, ClaimType, ClaimImportance, str | None]]:
        claims: list[tuple[str, ClaimType, ClaimImportance, str | None]] = []
        seen: set[str] = set()
        for candidate in candidates[:5]:
            text = candidate.text.strip()
            if not text or text in seen:
                continue
            seen.add(text)
            # Heuristic claim typing based on candidate content.
            claim_type = ClaimType.DESCRIPTIVE
            if any(op in text for op in [">", "<", "=", "±", "%"]):
                claim_type = ClaimType.QUANTITATIVE
            elif any(w in text.lower() for w in ["cause", "lead to", "due to", "导致", "引起"]):
                claim_type = ClaimType.CAUSAL
            elif any(w in text.lower() for w in ["define", "defined as", "定义为"]):
                claim_type = ClaimType.DEFINITION
            importance = (
                ClaimImportance.KEY
                if candidate.fused_rank <= 2
                else ClaimImportance.SUPPORTING
            )
            scope = f"candidate:{candidate.candidate_id}"
            claims.append((text, claim_type, importance, scope))
        if not claims:
            # Even with no candidates, record that the graph was attempted.
            claims.append(
                (
                    f"未能从检索结果中为问题“{query}”提取可验证的科学判断。",
                    ClaimType.DESCRIPTIVE,
                    ClaimImportance.KEY,
                    None,
                )
            )
        return claims


@dataclass
class _StoredGraph:
    graph: ClaimGraph
    search_result: SearchResult
    account_id: str


class ClaimEvidenceService:
    """In-memory claim--evidence--citation service for T015."""

    def __init__(
        self,
        source_service: ScienceSourceService,
        search_service: ScienceSearchService,
        invalidation_service: InvalidationService | None = None,
        model_gateway: ModelGateway | None = None,
        claim_generator: ClaimGeneratorPort | None = None,
    ) -> None:
        self._source_service = source_service
        self._search_service = search_service
        self._invalidation = invalidation_service
        self._model_gateway = model_gateway
        self._claim_generator = claim_generator or _DeterministicClaimGenerator()
        self._graphs: dict[str, _StoredGraph] = {}

    def _subject(self, account_id: str) -> SubjectContext:
        from science_companion.contracts.identity import AuthMethod

        return SubjectContext(
            account_id=account_id,
            session_id="claim-service",
            auth_method=AuthMethod.PASSWORD,
        )

    def _require_active_source(self, source_id: str, account_id: str) -> None:
        """Fail closed if the source is revoked or tombstoned."""
        if self._invalidation is None:
            return
        projection = self._source_service.get_source(account_id, source_id)
        source = projection.source
        domain = (
            ObjectDomain.SHARED_PROJECT
            if source.project_id
            else ObjectDomain.PERSONAL_VAULT
        )
        owner_id = source.project_id if source.project_id else account_id
        ref = ObjectRef(domain=domain, owner_id=owner_id, object_id=source_id, version=1)
        from science_companion.invalidation import InvalidationError

        try:
            self._invalidation.require_active(ref)
        except InvalidationError as exc:
            raise ScienceError(str(exc)) from exc

    def _authorize_graph(self, account_id: str, graph_id: str) -> ClaimGraph:
        """Return a graph only if it belongs to the account."""
        stored = self._graphs.get(graph_id)
        if stored is None or stored.account_id != account_id:
            raise ScienceError("Claim 图不存在或没有访问权限。")
        return stored.graph

    def _build_locator(self, candidate: RetrievalCandidate) -> CitationLocator:
        """Build a precise locator from a retrieval candidate's chunk metadata."""
        path = candidate.structure_path
        searchable = self._find_chunk_across_sources(candidate.chunk_id)
        if searchable is not None:
            start_offset = searchable.chunk.start_offset
            end_offset = searchable.chunk.end_offset
        else:
            start_offset = None
            end_offset = None
        return CitationLocator(
            page=path.page,
            section=path.section or path.subsection,
            paragraph=path.paragraph,
            figure=path.figure,
            table=path.table,
            formula=path.formula,
            start_offset=start_offset,
            end_offset=end_offset,
        )

    def _build_identifier_snapshot(
        self, source: Any, document: Any
    ) -> dict[str, str | None]:
        """Snapshot canonical identifiers at citation creation time."""
        return {
            "source_id": source.source_id,
            "document_id": document.document_id,
            "canonical_identity": source.canonical_identity,
        }

    def _find_chunk_across_sources(self, chunk_id: str) -> SearchableChunk | None:
        """Locate a chunk by id across all sources owned by scanning the store."""
        for stored in self._source_service._sources.values():
            chunk = stored.chunks.get(chunk_id)
            if chunk is not None:
                document = stored.documents.get(chunk.document_id)
                if document is not None:
                    return SearchableChunk(source=stored.source, document=document, chunk=chunk)
        return None

    def _find_source_by_document_id(self, document_id: str) -> Any | None:
        """Return the source entry that owns a given document version."""
        for stored in self._source_service._sources.values():
            if document_id in stored.documents:
                return stored.source
        return None

    def find_source_id_for_document(self, document_id: str) -> str | None:
        """Public seam: return the source id that owns a document version, if known."""
        source = self._find_source_by_document_id(document_id)
        if source is None:
            return None
        return str(source.source_id)

    def _run_model_for_claims(
        self,
        subject: SubjectContext,
        request: ClaimRequest,
        candidates: list[RetrievalCandidate],
    ) -> tuple[ModelRunLock | None, ClaimTrustStatus, str | None]:
        """Optionally invoke a structured-output capability and return a run lock.

        The model is only used to produce a review/assessment lock; the actual
        claims are generated deterministically so the seam remains testable.
        """
        if self._model_gateway is None:
            return None, ClaimTrustStatus.VERIFIED, None

        run_context = RunContextEnvelope(
            run_id=request.run_id or f"claim-{secrets.token_urlsafe(12)}",
            account_id=subject.account_id,
            project_id=request.project_id or "claim-service",
            workflow_name="claim_generation",
            workflow_version="1",
            object_domain=request.object_domain,
            submitted_at=_now(),
        )
        result = self._model_gateway.invoke(
            capability_name="qwen_structured_output",
            capability_version="1",
            run_context=run_context,
            payload={
                "query": request.query,
                "candidate_count": len(candidates),
                "task": "claim_evidence_citation_generation",
            },
        )
        if result.status in {ModelCallStatus.SUCCESS, ModelCallStatus.DEGRADED}:
            return result.lock, ClaimTrustStatus.VERIFIED, None
        return (
            result.lock,
            ClaimTrustStatus.BLOCKED,
            result.degradation_reason or result.error_message or "模型调用被阻塞。",
        )

    def generate_claim_graph(
        self,
        subject: SubjectContext,
        request: ClaimRequest,
    ) -> ClaimGraphResult:
        """Generate a claim graph from a scientific question.

        The pipeline: compile scope, run hybrid retrieval, generate claims,
        bind each claim to evidence with precise locators, create citations,
        run the publish gate, and return the graph plus audit lock.
        """
        search_request = SearchRequest(
            query=request.query,
            project_id=request.project_id,
            object_domain=request.object_domain,
            top_k=request.top_k,
            include_lexical=True,
            include_vector=True,
            rerank=True,
        )
        search_result = self._search_service.search(subject, search_request)

        lock, model_status, model_reason = self._run_model_for_claims(
            subject, request, search_result.candidates
        )

        graph_id = secrets.token_urlsafe(16)
        now = _now()
        graph = ClaimGraph(
            graph_id=graph_id,
            account_id=subject.account_id,
            project_id=request.project_id,
            run_id=request.run_id,
            query=request.query,
            status=model_status or ClaimTrustStatus.VERIFIED,
            status_reason=model_reason,
            version_number=1,
            claims=[],
            evidence=[],
            citations=[],
            index_snapshot_id=search_result.index_snapshot_id,
            created_at=now,
            model_run_lock_id=lock.lock_id if lock else None,
        )

        generated = self._claim_generator.generate_claims(
            request.query,
            search_result.candidates,
            None,
        )

        # If the model call blocked, we still create an empty graph so the failure
        # is observable and auditable.
        if graph.status == ClaimTrustStatus.BLOCKED:
            publish_gate = self._run_publish_gate(graph)
            self._graphs[graph_id] = _StoredGraph(
                graph=graph, search_result=search_result, account_id=subject.account_id
            )
            return ClaimGraphResult(
                graph=graph,
                search_result=search_result,
                publish_gate=publish_gate,
                model_run_lock=lock,
            )

        for idx, (claim_text, claim_type, importance, scope) in enumerate(generated):
            claim_id = secrets.token_urlsafe(16)
            claim = Claim(
                claim_id=claim_id,
                graph_id=graph_id,
                claim_type=claim_type,
                text=claim_text,
                importance=importance,
                scope=scope,
                status=ClaimTrustStatus.VERIFIED,
                version_number=1,
                evidence_ids=[],
                citation_ids=[],
                created_at=now,
                model_run_lock_id=lock.lock_id if lock else None,
            )

            # Bind evidence to the top candidate that produced this claim, if any.
            candidate = (
                search_result.candidates[idx]
                if idx < len(search_result.candidates)
                else None
            )
            if candidate is not None:
                self._require_active_source(candidate.source_id, subject.account_id)
                searchable = self._find_chunk_across_sources(candidate.chunk_id)
                if searchable is not None:
                    evidence_id = secrets.token_urlsafe(16)
                    evidence = Evidence(
                        evidence_id=evidence_id,
                        claim_id=claim_id,
                        document_id=candidate.document_id,
                        chunk_ids=[candidate.chunk_id],
                        relation=EvidenceRelation.SUPPORTS,
                        quoted_span_or_data_ref=candidate.text[:200],
                        evidence_role="primary_result",
                        source_proximity="full_text",
                        lifecycle_status=searchable.document.lifecycle_status,
                        assessment_reason=(
                            "Top retrieval candidate bound deterministically "
                            "as supporting evidence."
                        ),
                        assessor="rule",
                        valid_from=now,
                        model_run_lock_id=lock.lock_id if lock else None,
                    )
                    claim.evidence_ids.append(evidence_id)
                    graph.evidence.append(evidence)

                    citation_id = secrets.token_urlsafe(16)
                    citation = Citation(
                        citation_id=citation_id,
                        evidence_id=evidence_id,
                        claim_id=claim_id,
                        locator=self._build_locator(candidate),
                        identifier_snapshot=self._build_identifier_snapshot(
                            searchable.source, searchable.document
                        ),
                        title_snapshot=searchable.source.title,
                        source_version_label=searchable.document.version_label,
                        accessed_at=now,
                        render_style="footnote",
                        verification_status=CitationVerificationStatus.VERIFIED,
                        verification_reason="Citation created against current source version.",
                    )
                    claim.citation_ids.append(citation_id)
                    graph.citations.append(citation)

                    # Optionally add a limiting evidence for the same candidate when
                    # the claim is high-stakes (causal/predictive/normative).
                    if request.include_refutations and claim_type in {
                        ClaimType.CAUSAL,
                        ClaimType.PREDICTIVE,
                        ClaimType.NORMATIVE,
                    }:
                        limit_evidence_id = secrets.token_urlsafe(16)
                        limit_evidence = Evidence(
                            evidence_id=limit_evidence_id,
                            claim_id=claim_id,
                            document_id=candidate.document_id,
                            chunk_ids=[candidate.chunk_id],
                            relation=EvidenceRelation.LIMITS,
                            quoted_span_or_data_ref=candidate.text[:200],
                            evidence_role="contextual_limit",
                            source_proximity="full_text",
                            lifecycle_status=searchable.document.lifecycle_status,
                            assessment_reason=(
                                "High-stakes claim type requires explicit limiting evidence."
                            ),
                            assessor="rule",
                            valid_from=now,
                            model_run_lock_id=lock.lock_id if lock else None,
                        )
                        claim.evidence_ids.append(limit_evidence_id)
                        graph.evidence.append(limit_evidence)

            graph.claims.append(claim)

        # If no candidates were returned, the graph is partial or metadata_only.
        if not search_result.candidates:
            graph.status = ClaimTrustStatus.METADATA_ONLY
            graph.status_reason = "No retrieval candidates; claims cannot be source-located."

        # T016: compile fact locks and apply honest-degradation analysis. The
        # graph status is updated deterministically from evidence state.
        self._refresh_all_citations(graph)
        validation_report = apply_honest_degradation(
            graph, had_candidates=bool(search_result.candidates)
        )
        update_claim_graph_with_report(graph, validation_report)

        publish_gate = self._run_publish_gate(graph)
        self._graphs[graph_id] = _StoredGraph(
            graph=graph, search_result=search_result, account_id=subject.account_id
        )
        return ClaimGraphResult(
            graph=graph,
            search_result=search_result,
            publish_gate=publish_gate,
            model_run_lock=lock,
            validation_report=validation_report,
        )

    def get_claim_graph(self, account_id: str, graph_id: str) -> ClaimGraph:
        """Return a claim graph by id, scoped to the account."""
        return self._authorize_graph(account_id, graph_id)

    def list_claim_graphs(
        self,
        account_id: str,
        project_id: str | None = None,
    ) -> list[ClaimGraph]:
        """List claim graphs visible to the account, optionally filtered by project."""
        result: list[ClaimGraph] = []
        for stored in self._graphs.values():
            if stored.account_id != account_id:
                continue
            if project_id is not None and stored.graph.project_id != project_id:
                continue
            result.append(stored.graph)
        result.sort(key=lambda g: g.created_at, reverse=True)
        return result

    def validate_citation(
        self,
        account_id: str,
        graph_id: str,
        citation_id: str,
    ) -> CitationValidationResult:
        """Re-verify a citation against the current source state and locator."""
        graph = self._authorize_graph(account_id, graph_id)
        citation = next((c for c in graph.citations if c.citation_id == citation_id), None)
        if citation is None:
            raise ScienceError("引用不存在或没有访问权限。")

        evidence = next((e for e in graph.evidence if e.evidence_id == citation.evidence_id), None)
        if evidence is None:
            return CitationValidationResult(
                citation_id=citation_id,
                verification_status=CitationVerificationStatus.UNLOCATABLE,
                reason="关联的 Evidence 已丢失。",
            )

        source = self._find_source_by_document_id(evidence.document_id)
        if source is None:
            return CitationValidationResult(
                citation_id=citation_id,
                verification_status=CitationVerificationStatus.SOURCE_REVOKED,
                reason="来源已不可用。",
            )

        self._require_active_source(source.source_id, account_id)

        if source.status in {SourceStatus.RETRACTED, SourceStatus.BLOCKED}:
            return CitationValidationResult(
                citation_id=citation_id,
                verification_status=CitationVerificationStatus.SOURCE_REVOKED,
                reason="来源已被撤权或阻塞。",
            )

        if source.current_version_id is None:
            return CitationValidationResult(
                citation_id=citation_id,
                verification_status=CitationVerificationStatus.UNLOCATABLE,
                reason="来源没有当前版本。",
            )

        current_document_id = source.current_version_id
        if current_document_id != evidence.document_id:
            current_doc = self._source_service._sources[source.source_id].documents.get(
                current_document_id
            )
            version_label = current_doc.version_label if current_doc else "unknown"
            return CitationValidationResult(
                citation_id=citation_id,
                verification_status=CitationVerificationStatus.SOURCE_SUPERSEDED,
                reason=f"来源已有新版本 {version_label}。",
                current_document_id=current_document_id,
                current_version_label=current_doc.version_label if current_doc else None,
            )

        # TODO: precise offset/span verification can be added once chunk offsets
        # are normalized across re-versioning. For now, document version match is
        # the deterministic locator check.
        return CitationValidationResult(
            citation_id=citation_id,
            verification_status=CitationVerificationStatus.VERIFIED,
            reason="引用指向的来源版本仍然有效。",
        )

    def create_claim_version(
        self,
        account_id: str,
        graph_id: str,
        claim_id: str,
        new_text: str,
        *,
        reason: str = "",
    ) -> ClaimGraph:
        """Create a new version of a claim graph with an updated claim.

        The old graph is preserved and marked as superseded; the new graph starts
        from a copy with the requested claim replaced by a versioned successor.
        """
        old_graph = self._authorize_graph(account_id, graph_id)
        old_claim = next((c for c in old_graph.claims if c.claim_id == claim_id), None)
        if old_claim is None:
            raise ScienceError("Claim 不存在或没有访问权限。")

        new_graph_id = secrets.token_urlsafe(16)
        now = _now()
        new_claim_id = secrets.token_urlsafe(16)

        new_claim = old_claim.model_copy(
            update={
                "claim_id": new_claim_id,
                "graph_id": new_graph_id,
                "text": new_text,
                "version_number": old_claim.version_number + 1,
                "created_at": now,
                "superseded_by_claim_id": None,
            }
        )
        old_claim.superseded_by_claim_id = new_claim_id

        new_graph = old_graph.model_copy(
            update={
                "graph_id": new_graph_id,
                "version_number": old_graph.version_number + 1,
                "created_at": now,
                "superseded_by_graph_id": None,
            }
        )
        new_graph.claims = [
            new_claim if c.claim_id == claim_id else c.model_copy()
            for c in old_graph.claims
        ]
        new_graph.evidence = [e.model_copy() for e in old_graph.evidence]
        new_graph.citations = [c.model_copy() for c in old_graph.citations]
        old_graph.superseded_by_graph_id = new_graph_id

        self._graphs[new_graph_id] = _StoredGraph(
            graph=new_graph,
            search_result=self._graphs[graph_id].search_result,
            account_id=account_id,
        )
        return new_graph

    def _refresh_citation_verification(
        self, graph: ClaimGraph, citation: Citation
    ) -> None:
        """Update a citation's verification status against current source state.

        This is a deterministic refresh used by the publish gate; it does not
        create new versions, only surfaces staleness.
        """
        evidence = next(
            (e for e in graph.evidence if e.evidence_id == citation.evidence_id), None
        )
        if evidence is None:
            citation.verification_status = CitationVerificationStatus.UNLOCATABLE
            citation.verification_reason = "关联的 Evidence 已丢失。"
            return

        source = self._find_source_by_document_id(evidence.document_id)
        if source is None:
            citation.verification_status = CitationVerificationStatus.SOURCE_REVOKED
            citation.verification_reason = "来源已不可用。"
            return

        if source.status in {SourceStatus.RETRACTED, SourceStatus.BLOCKED}:
            citation.verification_status = CitationVerificationStatus.SOURCE_REVOKED
            citation.verification_reason = "来源已被撤权或阻塞。"
            return

        if source.current_version_id != evidence.document_id:
            citation.verification_status = CitationVerificationStatus.SOURCE_SUPERSEDED
            citation.verification_reason = "来源已有新版本。"
            return

    def _run_publish_gate(self, graph: ClaimGraph) -> PublishGateResult:
        """Run deterministic publish gate over the claim graph.

        The gate rejects fabricated or unlocatable critical citations and derives
        the graph's trust status. It does not depend on model judgment.
        """
        checks: dict[PublishGateCheck, bool] = {
            PublishGateCheck.KEY_CLAIM_COVERAGE: True,
            PublishGateCheck.CITATION_LOCATABLE: True,
            PublishGateCheck.SOURCE_ACTIVE: True,
            PublishGateCheck.SOURCE_CURRENT_VERSION: True,
            PublishGateCheck.NO_FABRICATED_CITATIONS: True,
        }
        blocked_claim_ids: list[str] = []
        reasons: list[str] = []

        key_claims = [c for c in graph.claims if c.importance == ClaimImportance.KEY]
        if not key_claims and graph.status != ClaimTrustStatus.BLOCKED:
            checks[PublishGateCheck.KEY_CLAIM_COVERAGE] = False
            reasons.append("没有关键 Claim。")

        for claim in graph.claims:
            if claim.importance != ClaimImportance.KEY:
                continue
            if not claim.evidence_ids:
                checks[PublishGateCheck.KEY_CLAIM_COVERAGE] = False
                blocked_claim_ids.append(claim.claim_id)
                reasons.append(f"关键 Claim {claim.claim_id} 缺少 Evidence。")
                continue

            for citation_id in claim.citation_ids:
                citation = next((c for c in graph.citations if c.citation_id == citation_id), None)
                if citation is None:
                    checks[PublishGateCheck.NO_FABRICATED_CITATIONS] = False
                    blocked_claim_ids.append(claim.claim_id)
                    reasons.append(f"关键 Claim {claim.claim_id} 引用了不存在的 Citation。")
                    continue

                self._refresh_citation_verification(graph, citation)

                if citation.verification_status in {
                    CitationVerificationStatus.UNLOCATABLE,
                    CitationVerificationStatus.SOURCE_REVOKED,
                }:
                    checks[PublishGateCheck.CITATION_LOCATABLE] = False
                    blocked_claim_ids.append(claim.claim_id)
                    reasons.append(f"Citation {citation_id} 无法定位或来源已撤权。")

                evidence = next(
                    (e for e in graph.evidence if e.evidence_id == citation.evidence_id), None
                )
                if evidence is None or evidence.invalidated_at is not None:
                    checks[PublishGateCheck.SOURCE_ACTIVE] = False
                    blocked_claim_ids.append(claim.claim_id)
                    reasons.append(f"Citation {citation_id} 关联的 Evidence 已失效。")
                    continue

                source = self._find_source_by_document_id(evidence.document_id)
                if source is not None and source.current_version_id != evidence.document_id:
                    checks[PublishGateCheck.SOURCE_CURRENT_VERSION] = False
                    blocked_claim_ids.append(claim.claim_id)
                    reasons.append(f"Citation {citation_id} 指向的来源版本已过期。")

        passed = all(checks.values())
        failed = [check for check, ok in checks.items() if not ok]

        graph_status = graph.status
        if not passed and graph_status in {ClaimTrustStatus.VERIFIED, ClaimTrustStatus.QUALIFIED}:
            graph_status = ClaimTrustStatus.BLOCKED

        return PublishGateResult(
            passed=passed,
            graph_status=graph_status,
            checks=checks,
            failed_checks=failed,
            blocked_claim_ids=list(set(blocked_claim_ids)),
            reason="；".join(reasons) if reasons else None,
        )

    def run_publish_gate(
        self,
        account_id: str,
        graph_id: str,
    ) -> PublishGateResult:
        """Re-run the publish gate against the current state of a graph."""
        graph = self._authorize_graph(account_id, graph_id)
        return self._run_publish_gate(graph)

    def compile_fact_locks(
        self,
        account_id: str,
        graph_id: str,
    ) -> FactLockSet:
        """Compile the fact lock set for a claim graph."""
        graph = self._authorize_graph(account_id, graph_id)
        return compile_fact_locks(graph)

    def _refresh_all_citations(self, graph: ClaimGraph) -> None:
        """Re-verify every citation in the graph against current source state."""
        for citation in graph.citations:
            self._refresh_citation_verification(graph, citation)

    def validate_claim_graph(
        self,
        account_id: str,
        graph_id: str,
        *,
        apply: bool = False,
    ) -> ValidationReport:
        """Run honest-degradation analysis and return a validation report.

        If `apply` is True, the graph and claim statuses are updated in place.
        """
        graph = self._authorize_graph(account_id, graph_id)
        self._refresh_all_citations(graph)
        stored = self._graphs[graph_id]
        had_candidates = bool(stored.search_result.candidates)
        report = apply_honest_degradation(graph, had_candidates=had_candidates)
        if apply:
            update_claim_graph_with_report(graph, report)
        return report

    def run_scientific_quality_gate(
        self,
        account_id: str,
        graph_id: str,
    ) -> ScientificQualityGateResult:
        """Run the scientific quality gate for a claim graph."""
        graph = self._authorize_graph(account_id, graph_id)
        self._refresh_all_citations(graph)
        stored = self._graphs[graph_id]
        report = apply_honest_degradation(
            graph, had_candidates=bool(stored.search_result.candidates)
        )
        if report.scientific_gate is None:
            raise RuntimeError("Expected scientific gate result from honest-degradation analysis.")
        return report.scientific_gate

    def find_graphs_by_source(self, source_id: str) -> list[ClaimGraph]:
        """Return all claim graphs that cite the given source."""
        result: list[ClaimGraph] = []
        for stored in self._graphs.values():
            for citation in stored.graph.citations:
                evidence = next(
                    (e for e in stored.graph.evidence if e.evidence_id == citation.evidence_id),
                    None,
                )
                if evidence is None or not evidence.chunk_ids:
                    continue
                searchable = self._find_chunk_across_sources(evidence.chunk_ids[0])
                if searchable is not None and searchable.source.source_id == source_id:
                    result.append(stored.graph)
                    break
        return result

    def _create_revalidated_graph_version(
        self, old_graph: ClaimGraph, source_id: str
    ) -> ClaimGraph:
        """Create a new claim graph version reflecting current source invalidation state.

        The original graph is preserved as an immutable run snapshot. The new
        graph version records the current citation verification status and
        evidence validity without overwriting history.
        """
        new_graph_id = secrets.token_urlsafe(16)
        now = _now()

        new_graph = old_graph.model_copy(
            update={
                "graph_id": new_graph_id,
                "version_number": old_graph.version_number + 1,
                "created_at": now,
                "superseded_by_graph_id": None,
            }
        )
        new_graph.claims = [c.model_copy() for c in old_graph.claims]
        new_graph.evidence = [e.model_copy() for e in old_graph.evidence]
        new_graph.citations = [c.model_copy() for c in old_graph.citations]

        evidence_index = {e.evidence_id: e for e in new_graph.evidence}
        for citation in new_graph.citations:
            evidence = evidence_index.get(citation.evidence_id)
            if evidence is None or not evidence.chunk_ids:
                continue
            searchable = self._find_chunk_across_sources(evidence.chunk_ids[0])
            if searchable is None or searchable.source.source_id != source_id:
                continue

            evidence.invalidated_at = now
            if searchable.source.status in {SourceStatus.RETRACTED, SourceStatus.BLOCKED}:
                citation.verification_status = CitationVerificationStatus.SOURCE_REVOKED
                citation.verification_reason = "来源已被撤权或阻塞。"
            elif searchable.source.current_version_id != evidence.document_id:
                citation.verification_status = CitationVerificationStatus.SOURCE_SUPERSEDED
                citation.verification_reason = "来源已有新版本。"
            else:
                citation.verification_status = CitationVerificationStatus.STALE
                citation.verification_reason = "来源状态需要重新验证。"

        self._refresh_all_citations(new_graph)
        stored = self._graphs[old_graph.graph_id]
        validation_report = apply_honest_degradation(
            new_graph, had_candidates=bool(stored.search_result.candidates)
        )
        update_claim_graph_with_report(new_graph, validation_report)

        old_graph.superseded_by_graph_id = new_graph_id
        self._graphs[new_graph_id] = _StoredGraph(
            graph=new_graph,
            search_result=self._graphs[old_graph.graph_id].search_result,
            account_id=old_graph.account_id,
        )
        return new_graph

    def revalidate_graphs_for_source(self, account_id: str, source_id: str) -> list[str]:
        """Create new claim graph versions for every graph that cites the source.

        Returns the ids of the newly created graph versions. The original graphs
        remain as immutable run snapshots.
        """
        graphs = [
            g for g in self.find_graphs_by_source(source_id) if g.account_id == account_id
        ]
        new_ids: list[str] = []
        for graph in graphs:
            new_graph = self._create_revalidated_graph_version(graph, source_id)
            new_ids.append(new_graph.graph_id)
        return new_ids

    def invalidate_citations_for_source(
        self,
        account_id: str,
        source_id: str,
    ) -> list[str]:
        """Revalidate every claim graph that cites the source.

        Instead of mutating existing graphs in place, this creates new graph
        versions that reflect the current invalidation state. The original graphs
        are preserved as run snapshots.
        """
        return self.revalidate_graphs_for_source(account_id, source_id)


def build_claim_impact_resolver(
    claim_service: ClaimEvidenceService,
) -> ImpactResolver:
    """Build an impact resolver that locates affected claim graphs and fact locks.

    The resolver scans claim graphs owned by the source's account and returns
    downstream entries for revalidation. It never expands scope beyond the event.
    """

    def _resolver(event: InvalidationEvent) -> list[AffectedDownstream]:
        if event.event_type not in {
            InvalidationEventType.SOURCE_RETRACTED,
            InvalidationEventType.SOURCE_STATUS_UNKNOWN,
            InvalidationEventType.SOURCE_VERSION_SUPERSEDED,
        }:
            return []

        source_id = event.object_ref.object_id
        affected_graphs = [
            g
            for g in claim_service.find_graphs_by_source(source_id)
            if g.account_id == event.scope_envelope.account_id
        ]
        if not affected_graphs:
            return []

        fact_lock_set_ids: list[str] = []
        for graph in affected_graphs:
            try:
                fact_lock_set = claim_service.compile_fact_locks(
                    graph.account_id, graph.graph_id
                )
                fact_lock_set_ids.append(fact_lock_set.set_id)
            except ScienceError:
                continue

        downstreams: list[AffectedDownstream] = [
            AffectedDownstream(
                downstream_id=f"claim_graph:{source_id}",
                downstream_type="claim_graph",
                object_refs=[g.graph_id for g in affected_graphs],
                scope_envelope=event.scope_envelope,
                action="revalidate",
            )
        ]
        if fact_lock_set_ids:
            downstreams.append(
                AffectedDownstream(
                    downstream_id=f"fact_lock_set:{source_id}",
                    downstream_type="fact_lock_set",
                    object_refs=fact_lock_set_ids,
                    scope_envelope=event.scope_envelope,
                    action="revalidate",
                )
            )
        return downstreams

    return _resolver


class ClaimGraphRevalidationHandler:
    """Revalidation handler that creates new claim graph versions for a source."""

    def __init__(
        self,
        claim_service: ClaimEvidenceService,
    ) -> None:
        self._claim_service = claim_service

    def __call__(self, schedule: RevalidationSchedule) -> RevalidationResult:
        source_id = schedule.object_ref.object_id
        account_id = schedule.scope_envelope.account_id
        try:
            new_graph_ids = self._claim_service.revalidate_graphs_for_source(
                account_id, source_id
            )
            return RevalidationResult(
                schedule_id=schedule.schedule_id,
                success=True,
                reason=f"为来源 {source_id} 创建了 {len(new_graph_ids)} 个新 Claim Graph 版本。",
                details={"new_graph_ids": new_graph_ids},
            )
        except ScienceError as exc:
            return RevalidationResult(
                schedule_id=schedule.schedule_id,
                success=False,
                reason=str(exc),
            )


__all__ = [
    "ClaimEvidenceService",
    "ClaimGeneratorPort",
    "build_claim_impact_resolver",
    "ClaimGraphRevalidationHandler",
]
