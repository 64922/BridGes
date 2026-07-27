"""Scientific source ingestion and versioning domain service.

The ScienceSourceService is the deep module boundary for importing text and PDF
sources into scientific projects. It enforces:

- Scope isolation: sources are owned by an account and optional project.
- Input quality gates: MIME, magic number, size, bomb detection, malicious
  content, license and parse gates.
- Immutable document versions: every correction creates a new version.
- Traceable chunks: every chunk carries source, document, offsets and hashes.
- Invalidation integration: revoked or tombstoned sources cannot enter evidence.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from science_companion.contracts.identity import AuthMethod, SubjectContext
from science_companion.contracts.projects import ObjectDomain, ObjectRef
from science_companion.contracts.scope import ScopeAction, ScopeEnvelope, ScopeIsolationError
from science_companion.contracts.invalidation import (
    AffectedDownstream,
    InvalidationEventType,
)
from science_companion.invalidation import InvalidationService
from science_companion.contracts.science import (
    ChunkCorrection,
    ChunkVersion,
    DocumentVersion,
    GateResult,
    IngestionRunRef,
    IngestionStatus,
    InputQualityGate,
    LifecycleStatus,
    LicenseState,
    MediaType,
    ParseResult,
    Source,
    SourceError,
    SourceKind,
    SourceLicense,
    SourceProjection,
    SourceStatus,
    SourceSummary,
    SourceUploadRequest,
    SourceVersionRequest,
)
from science_companion.invalidation import InvalidationError
from science_companion.scope import ScopeEnforcer
from science_companion.science.parser import (
    ParserError,
    parser_id_for,
    parser_version_for,
    select_parser,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _source_ref(source: Source) -> ObjectRef:
    """Build an ObjectRef for a source entry."""
    domain = ObjectDomain.SHARED_PROJECT if source.project_id else ObjectDomain.PERSONAL_VAULT
    owner_id = source.project_id if source.project_id else source.account_id
    return ObjectRef(domain=domain, owner_id=owner_id, object_id=source.source_id, version=1)


class ScienceError(Exception):
    """Domain exception for science source failures.

    The message is safe to expose to callers; it never leaks whether a source
    exists or belongs to another account.
    """


@dataclass
class _StoredSource:
    source: Source
    documents: dict[str, DocumentVersion]
    chunks: dict[str, ChunkVersion]
    gate_results: dict[InputQualityGate, GateResult] | None = None


@dataclass
class SearchableChunk:
    """A chunk exposed to the search service for indexing.

    This is an internal seam type between ingestion and retrieval within the
    science module. It carries the source and document metadata needed to apply
    scope, lifecycle and invalidation filters at query time.
    """

    source: Source
    document: DocumentVersion
    chunk: ChunkVersion


class ScienceSourceService:
    """In-memory scientific source service for T013."""

    def __init__(
        self,
        scope_enforcer: ScopeEnforcer | None = None,
        invalidation_service: InvalidationService | None = None,
    ) -> None:
        self._sources: dict[str, _StoredSource] = {}
        self._ingestion_runs: dict[str, IngestionRunRef] = {}
        self._scope_enforcer = scope_enforcer or ScopeEnforcer()
        self._invalidation = invalidation_service

    def _subject(self, account_id: str) -> SubjectContext:
        return SubjectContext(
            account_id=account_id,
            session_id="service-session",
            auth_method=AuthMethod.PASSWORD,
        )

    def _require_active(self, source_ref: ObjectRef) -> None:
        """Fail closed if the source is revoked or tombstoned."""
        if self._invalidation is None:
            return
        try:
            self._invalidation.require_active(source_ref)
        except InvalidationError as exc:
            raise ScienceError(str(exc)) from exc

    def _authorize_source(
        self, account_id: str, source: Source, action: ScopeAction
    ) -> ScopeEnvelope:
        """Authorize an action against a source entry.

        T013: sources are owned by an account; project scoping does not grant
        cross-account access until explicit project membership and object grants
        are implemented.
        """
        if account_id != source.account_id:
            raise ScienceError("来源不存在或没有访问权限。")
        subject = self._subject(account_id)
        source_ref = _source_ref(source)
        try:
            return self._scope_enforcer.authorize(subject, action, source_ref)
        except ScopeIsolationError as exc:
            raise ScienceError(str(exc)) from exc

    def _authorize_source_id(
        self, account_id: str, source_id: str, action: ScopeAction
    ) -> tuple[Source, ScopeEnvelope]:
        """Look up a source and authorize the action."""
        stored = self._sources.get(source_id)
        if stored is None:
            raise ScienceError("来源不存在或没有访问权限。")
        scope = self._authorize_source(account_id, stored.source, action)
        return stored.source, scope

    def _document_version_number(self, source_id: str) -> int:
        stored = self._sources[source_id]
        if not stored.documents:
            return 1
        return max(d.version_number for d in stored.documents.values()) + 1

    def _create_source(
        self,
        account_id: str,
        project_id: str | None,
        request: SourceUploadRequest,
    ) -> Source:
        now = _now()
        source = Source(
            source_id=secrets.token_urlsafe(16),
            account_id=account_id,
            project_id=project_id,
            source_kind=SourceKind.USER_UPLOAD,
            canonical_identity=request.canonical_identity,
            publisher_or_owner=None,
            authority_scope=None,
            title=request.title or request.filename,
            license=SourceLicense(
                state=request.license_state or LicenseState.USER_OWNED,
                rights_statement="用户上传",
            ),
            trust_assertions=[],
            connector_id=None,
            connector_version=None,
            status=SourceStatus.DISCOVERED,
            current_version_id=None,
            created_at=now,
            updated_at=now,
        )
        self._sources[source.source_id] = _StoredSource(
            source=source, documents={}, chunks={}
        )
        return source

    def ingest_upload(
        self,
        account_id: str,
        project_id: str | None,
        request: SourceUploadRequest,
        *,
        raw_blob_ref: str | None = None,
    ) -> IngestionRunRef:
        """Ingest a text or PDF upload, running all input quality gates.

        The ingestion is synchronous in the in-memory adapter; production would
        queue this to the ingestion worker.
        """
        run_id = secrets.token_urlsafe(16)
        now = _now()
        run_ref = IngestionRunRef(
            run_id=run_id,
            status=IngestionStatus.RUNNING,
            created_at=now,
            updated_at=now,
        )
        self._ingestion_runs[run_id] = run_ref

        try:
            content = base64.b64decode(request.content)
        except Exception as exc:
            run_ref.status = IngestionStatus.FAILED
            run_ref.error = f"base64 decode failed: {exc}"
            run_ref.gate_results[InputQualityGate.PARSE] = GateResult.FAIL
            run_ref.updated_at = _now()
            return run_ref

        source = self._create_source(account_id, project_id, request)
        run_ref.source_id = source.source_id

        try:
            # Authorize creation before touching storage.
            try:
                scope = self._authorize_source(account_id, source, ScopeAction.CREATE)
            except ScienceError as exc:
                run_ref.status = IngestionStatus.FAILED
                run_ref.error = str(exc)
                run_ref.gate_results[InputQualityGate.SCOPE] = GateResult.FAIL
                run_ref.updated_at = _now()
                return run_ref

            # License gate.
            if source.license.state == LicenseState.UNKNOWN:
                run_ref.gate_results[InputQualityGate.LICENSE] = GateResult.WAIT
            else:
                run_ref.gate_results[InputQualityGate.LICENSE] = GateResult.PASS

            # Parse.
            try:
                parser = select_parser(request.media_type)
                document_id = secrets.token_urlsafe(16)
                parse_result, rights_snapshot, parser_gates = parser.parse(
                    source.source_id, document_id, content
                )
                run_ref.gate_results.update(parser_gates)
                run_ref.gate_results[InputQualityGate.PARSE] = GateResult.PASS
            except ParserError as exc:
                run_ref.status = IngestionStatus.FAILED
                run_ref.error = str(exc)
                run_ref.gate_results[InputQualityGate.PARSE] = GateResult.FAIL
                source.status = SourceStatus.BLOCKED
                source.updated_at = _now()
                run_ref.updated_at = _now()
                return run_ref

            # Any gate failure (magic, size, bomb, malicious) quarantines or blocks.
            failed_gates = [
                g
                for g, r in run_ref.gate_results.items()
                if r == GateResult.FAIL
            ]
            if failed_gates:
                if InputQualityGate.PARSE in failed_gates:
                    source.status = SourceStatus.BLOCKED
                    run_ref.status = IngestionStatus.FAILED
                else:
                    source.status = SourceStatus.QUARANTINED
                    run_ref.status = IngestionStatus.QUARANTINED
                source.updated_at = _now()
                run_ref.updated_at = _now()
                return run_ref

            # Build document version.
            version_number = self._document_version_number(source.source_id)
            version_label = f"v{version_number}"
            content_hash = _sha256(content)
            metadata = {
                "filename": request.filename,
                "media_type": request.media_type.value,
                "title": source.title,
                "license_state": source.license.state.value,
            }
            metadata_hash = _sha256(str(metadata).encode("utf-8"))

            document = DocumentVersion(
                document_id=document_id,
                source_id=source.source_id,
                version_label=version_label,
                version_number=version_number,
                version_date=now,
                retrieved_at=now,
                lifecycle_status=LifecycleStatus.ACTIVE,
                content_hash=content_hash,
                metadata_hash=metadata_hash,
                media_type=request.media_type,
                language=parse_result.language,
                rights_snapshot=rights_snapshot,
                raw_blob_ref=raw_blob_ref,
                parser_id=parser_id_for(request.media_type),
                parser_version=parser_version_for(request.media_type),
                chunk_ids=[c.chunk_id for c in parse_result.chunks],
                created_at=now,
            )

            stored = self._sources[source.source_id]
            stored.documents[document.document_id] = document
            for chunk in parse_result.chunks:
                stored.chunks[chunk.chunk_id] = chunk

            source.current_version_id = document.document_id
            source.status = SourceStatus.PARSED
            source.updated_at = _now()

            run_ref.status = IngestionStatus.COMPLETED
            run_ref.updated_at = _now()
            return run_ref
        finally:
            # Persist gate results for later projection queries.
            persisted = self._sources.get(source.source_id)
            if persisted is not None:
                persisted.gate_results = dict(run_ref.gate_results)

    def get_source(self, account_id: str, source_id: str) -> SourceProjection:
        """Return a source projection including current document and chunks."""
        source, _scope = self._authorize_source_id(account_id, source_id, ScopeAction.READ)
        self._require_active(_source_ref(source))

        stored = self._sources[source_id]
        current_document = None
        current_chunks: list[ChunkVersion] = []
        if source.current_version_id:
            current_document = stored.documents.get(source.current_version_id)
            if current_document:
                for chunk_id in current_document.chunk_ids:
                    chunk = stored.chunks.get(chunk_id)
                    if chunk is not None:
                        current_chunks.append(chunk)

        gate_results: dict[InputQualityGate, GateResult] = {}
        # Use the gate results recorded during ingestion if available.
        if stored.gate_results:
            gate_results = dict(stored.gate_results)
        else:
            gate_results[InputQualityGate.PARSE] = GateResult.PASS
            gate_results[InputQualityGate.LICENSE] = (
                GateResult.PASS
                if source.license.state not in {LicenseState.UNKNOWN, LicenseState.PENDING_REVIEW}
                else GateResult.WAIT
            )

        can_enter_evidence = (
            source.status == SourceStatus.PARSED
            and source.license.state != LicenseState.UNKNOWN
            and source.license.state != LicenseState.PENDING_REVIEW
        )

        return SourceProjection(
            source=source,
            current_document=current_document,
            current_chunks=current_chunks,
            version_count=len(stored.documents),
            can_enter_evidence=can_enter_evidence,
            gate_results=gate_results,
        )

    def list_sources(
        self,
        account_id: str,
        project_id: str | None = None,
    ) -> list[SourceSummary]:
        """List source summaries visible to the account."""
        subject = self._subject(account_id)
        summaries: list[SourceSummary] = []
        for stored in self._sources.values():
            source = stored.source
            if source.account_id != account_id:
                continue
            if project_id is not None and source.project_id != project_id:
                continue
            try:
                self._authorize_source(account_id, source, ScopeAction.READ)
            except ScienceError:
                continue
            current_doc = (
                stored.documents.get(source.current_version_id)
                if source.current_version_id
                else None
            )
            summaries.append(
                SourceSummary(
                    source_id=source.source_id,
                    title=source.title,
                    media_type=current_doc.media_type if current_doc else None,
                    status=source.status,
                    version_count=len(stored.documents),
                    updated_at=source.updated_at,
                )
            )
        summaries.sort(key=lambda s: s.updated_at, reverse=True)
        return summaries

    def list_document_versions(self, account_id: str, source_id: str) -> list[DocumentVersion]:
        """Return all document versions for a source."""
        self._authorize_source_id(account_id, source_id, ScopeAction.READ)
        stored = self._sources[source_id]
        return sorted(
            stored.documents.values(), key=lambda d: d.version_number, reverse=True
        )

    def get_chunk(
        self, account_id: str, source_id: str, chunk_id: str
    ) -> ChunkVersion:
        """Return a single chunk."""
        self._authorize_source_id(account_id, source_id, ScopeAction.READ)
        stored = self._sources[source_id]
        chunk = stored.chunks.get(chunk_id)
        if chunk is None:
            raise ScienceError("结构块不存在或没有访问权限。")
        return chunk

    def create_new_version(
        self,
        account_id: str,
        source_id: str,
        request: SourceVersionRequest,
    ) -> DocumentVersion:
        """Create a new document version by applying chunk corrections.

        Corrections never overwrite the previous version; they produce a new
        DocumentVersion with new chunk identities and updated hashes.
        """
        source, _scope = self._authorize_source_id(account_id, source_id, ScopeAction.UPDATE)
        self._require_active(_source_ref(source))
        stored = self._sources[source_id]

        base_document = stored.documents.get(request.base_document_id)
        if base_document is None:
            raise ScienceError("基础文档版本不存在或没有访问权限。")

        now = _now()
        new_document_id = secrets.token_urlsafe(16)
        version_number = self._document_version_number(source_id)

        correction_map = {c.chunk_id: c for c in request.chunk_corrections}
        new_chunk_ids: list[str] = []

        for old_chunk_id in base_document.chunk_ids:
            old_chunk = stored.chunks.get(old_chunk_id)
            if old_chunk is None:
                continue

            correction = correction_map.get(old_chunk_id)
            if correction is not None:
                new_text = correction.corrected_text
                human_corrected = True
                parse_confidence = 1.0
            else:
                new_text = old_chunk.text
                human_corrected = old_chunk.human_corrected
                parse_confidence = old_chunk.parse_confidence

            new_chunk = ChunkVersion(
                chunk_id=secrets.token_urlsafe(16),
                document_id=new_document_id,
                source_id=source_id,
                parent_chunk_id=old_chunk.parent_chunk_id,
                previous_chunk_id=new_chunk_ids[-1] if new_chunk_ids else None,
                next_chunk_id=None,
                structure_path=old_chunk.structure_path,
                start_offset=old_chunk.start_offset,
                end_offset=old_chunk.end_offset,
                text=new_text,
                text_hash=_sha256(new_text.encode("utf-8")),
                parse_confidence=parse_confidence,
                human_corrected=human_corrected,
                injection_flags=old_chunk.injection_flags,
                created_at=now,
            )
            # Link previous chunk forward.
            if new_chunk_ids:
                previous = stored.chunks[new_chunk_ids[-1]]
                previous.next_chunk_id = new_chunk.chunk_id
            new_chunk_ids.append(new_chunk.chunk_id)
            stored.chunks[new_chunk.chunk_id] = new_chunk

        # Build metadata hash from base document metadata plus correction reason.
        metadata = {
            "base_document_id": base_document.document_id,
            "reason": request.reason,
            "corrected_chunk_ids": list(correction_map.keys()),
        }
        metadata_hash = _sha256(str(metadata).encode("utf-8"))

        new_document = DocumentVersion(
            document_id=new_document_id,
            source_id=source_id,
            version_label=f"v{version_number}",
            version_number=version_number,
            version_date=now,
            retrieved_at=base_document.retrieved_at,
            lifecycle_status=LifecycleStatus.ACTIVE,
            content_hash=base_document.content_hash,
            metadata_hash=metadata_hash,
            media_type=base_document.media_type,
            language=base_document.language,
            rights_snapshot=base_document.rights_snapshot,
            raw_blob_ref=base_document.raw_blob_ref,
            parser_id=base_document.parser_id,
            parser_version=base_document.parser_version,
            ocr_asr_id=base_document.ocr_asr_id,
            ocr_asr_version=base_document.ocr_asr_version,
            provenance_bundle_id=base_document.provenance_bundle_id,
            superseded_by_document_id=None,
            chunk_ids=new_chunk_ids,
            created_at=now,
        )

        # Mark base document as superseded by the new version.
        base_document.superseded_by_document_id = new_document.document_id
        stored.documents[new_document.document_id] = new_document

        source.current_version_id = new_document.document_id
        source.status = SourceStatus.PARSED
        source.updated_at = now

        return new_document

    def list_searchable_chunks(
        self,
        account_id: str,
        project_id: str | None = None,
    ) -> list[SearchableChunk]:
        """Return chunks that may enter a scoped search index.

        Filters apply the same scope, status, lifecycle and quality rules as
        evidence admission: only parsed, active, non-quarantined sources owned by
        the account (and optionally project) are exposed.
        """
        subject = self._subject(account_id)
        results: list[SearchableChunk] = []
        for stored in self._sources.values():
            source = stored.source
            if source.account_id != account_id:
                continue
            if project_id is not None:
                if source.project_id != project_id:
                    continue
            else:
                # Scoped personal-vault search without a project only surfaces
                # sources that are not attached to a project.
                if source.project_id is not None:
                    continue
            if source.status != SourceStatus.PARSED:
                continue
            if source.license.state in {LicenseState.UNKNOWN, LicenseState.PENDING_REVIEW}:
                continue
            if source.current_version_id is None:
                continue
            document = stored.documents.get(source.current_version_id)
            if document is None:
                continue
            if document.lifecycle_status not in {
                LifecycleStatus.ACTIVE,
                LifecycleStatus.CORRECTED,
            }:
                continue
            for chunk_id in document.chunk_ids:
                chunk = stored.chunks.get(chunk_id)
                if chunk is None or chunk.injection_flags:
                    continue
                try:
                    self._authorize_source(account_id, source, ScopeAction.READ)
                    self._require_active(_source_ref(source))
                except ScienceError:
                    continue
                results.append(SearchableChunk(source=source, document=document, chunk=chunk))
        return results

    def revoke_source(self, account_id: str, source_id: str, reason: str) -> ObjectRef:
        """Revoke a source, blocking it from entering new evidence."""
        source, _scope = self._authorize_source_id(account_id, source_id, ScopeAction.DELETE)
        source.status = SourceStatus.RETRACTED
        source.updated_at = _now()
        return _source_ref(source)

    def get_ingestion_run(self, account_id: str, run_id: str) -> IngestionRunRef:
        """Return an ingestion run if it belongs to the account's sources."""
        run = self._ingestion_runs.get(run_id)
        if run is None or run.source_id is None:
            raise ScienceError("摄入运行不存在或没有访问权限。")
        self._authorize_source_id(account_id, run.source_id, ScopeAction.READ)
        return run


def build_source_impact_resolver(
    science_service: ScienceSourceService,
) -> Any:
    """Build an impact resolver for source invalidation events.

    Returns a callable compatible with InvalidationService.register_impact_resolver.
    The resolver does not call back into source reads (which would be blocked by
    the invalidation it is reacting to); it derives the affected downstreams
    directly from the event.
    """

    def _resolver(event: Any) -> list[AffectedDownstream]:
        source_id = event.object_ref.object_id
        affected: list[AffectedDownstream] = []
        # Index projection must be revalidated.
        affected.append(
            AffectedDownstream(
                downstream_id=f"science_index:{source_id}",
                downstream_type="index_projection",
                object_refs=[source_id],
                scope_envelope=event.scope_envelope,
                action="revalidate",
            )
        )
        # Cache entries for this source must be invalidated.
        affected.append(
            AffectedDownstream(
                downstream_id=f"science_cache:{source_id}",
                downstream_type="cache",
                object_refs=[source_id],
                scope_envelope=event.scope_envelope,
                action="invalidate",
            )
        )
        # New workflow runs must not use this source.
        affected.append(
            AffectedDownstream(
                downstream_id=f"science_run:{source_id}",
                downstream_type="workflow_run",
                object_refs=[source_id],
                scope_envelope=event.scope_envelope,
                action="block_new_use",
            )
        )
        return affected

    return _resolver
