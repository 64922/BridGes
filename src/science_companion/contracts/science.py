"""Scientific source, document and chunk contracts.

These models define the public surface of imported scientific sources (text and
PDF), their immutable document versions, structural chunks, lifecycle status and
input quality gates. They are the authoritative shape of CONTRACT-SCI-01.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SourceKind(str, Enum):
    """Kind of source entry."""

    USER_UPLOAD = "user_upload"
    PUBLISHER = "publisher"
    REPOSITORY = "repository"
    STANDARDS_BODY = "standards_body"
    GOVERNMENT = "government"
    DATABASE = "database"
    WEB = "web"


class MediaType(str, Enum):
    """Media type of an imported document."""

    TEXT_PLAIN = "text/plain"
    APPLICATION_PDF = "application/pdf"


class LifecycleStatus(str, Enum):
    """Lifecycle status of a specific document version."""

    ACTIVE = "active"
    CORRECTED = "corrected"
    EXPRESSION_OF_CONCERN = "expression_of_concern"
    RETRACTED = "retracted"
    WITHDRAWN = "withdrawn"
    SUPERSEDED = "superseded"
    UNKNOWN = "unknown"


class SourceStatus(str, Enum):
    """Current status of a source entry."""

    DISCOVERED = "discovered"
    PARSING = "parsing"
    PARSED = "parsed"
    QUARANTINED = "quarantined"
    BLOCKED = "blocked"
    RETRACTED = "retracted"


class IngestionStatus(str, Enum):
    """Status of an ingestion run."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class LicenseState(str, Enum):
    """License/Access state for the document."""

    UNKNOWN = "unknown"
    PUBLIC_DOMAIN = "public_domain"
    OPEN_ACCESS = "open_access"
    CLOSED_ACCESS = "closed_access"
    EMBARGOED = "embargoed"
    USER_OWNED = "user_owned"
    PENDING_REVIEW = "pending_review"


class InputQualityGate(str, Enum):
    """Named gates that imported content must pass before entering evidence."""

    MIME_TYPE = "mime_type"
    MAGIC_NUMBER = "magic_number"
    SIZE_LIMIT = "size_limit"
    DECOMPRESSION_BOMB = "decompression_bomb"
    MALICIOUS_CONTENT = "malicious_content"
    LICENSE = "license"
    PARSE = "parse"
    SCOPE = "scope"


class GateResult(str, Enum):
    """Result of a single input quality gate."""

    PASS = "pass"
    WAIT = "wait"
    FAIL = "fail"


class SourceLicense(BaseModel):
    """License snapshot for a document version."""

    state: LicenseState = Field(default=LicenseState.UNKNOWN)
    rights_statement: str | None = Field(
        default=None, description="Human-readable rights statement."
    )
    canonical_url: str | None = Field(default=None, description="License URL if known.")
    checked_at: datetime | None = Field(default=None)


class SourceTrustAssertion(BaseModel):
    """A recorded trust assertion about a source."""

    asserted_by: str = Field(description="Agent that made the assertion.")
    asserted_at: datetime = Field(description="When the assertion was recorded.")
    assertion_kind: str = Field(
        description="Kind of assertion, e.g. peer_reviewed, authoritative_host."
    )
    reason: str | None = Field(default=None)


class Source(BaseModel):
    """A source entry: the identity and ownership of one scientific work.

    A Source is not a specific content version; it owns the version history and
    current pointer. The actual content snapshots are DocumentVersion objects.
    """

    source_id: str = Field(description="Stable source identifier.")
    account_id: str = Field(description="Owning account identifier.")
    project_id: str | None = Field(
        default=None, description="Project scope when owned by a project."
    )
    source_kind: SourceKind = Field(default=SourceKind.USER_UPLOAD)
    canonical_identity: str | None = Field(
        default=None,
        description="DOI, arXiv ID, domain or local canonical identifier.",
    )
    publisher_or_owner: str | None = Field(default=None)
    authority_scope: str | None = Field(
        default=None, description="Description of authority scope."
    )
    title: str | None = Field(default=None)
    license: SourceLicense = Field(default_factory=SourceLicense)
    trust_assertions: list[SourceTrustAssertion] = Field(default_factory=list)
    connector_id: str | None = Field(default=None)
    connector_version: str | None = Field(default=None)
    status: SourceStatus = Field(default=SourceStatus.DISCOVERED)
    current_version_id: str | None = Field(default=None)
    created_at: datetime = Field(description="Source creation timestamp.")
    updated_at: datetime = Field(description="Last source update timestamp.")


class ChunkStructurePath(BaseModel):
    """Hierarchical location of a chunk within a document."""

    section: str | None = Field(default=None)
    subsection: str | None = Field(default=None)
    paragraph: int | None = Field(default=None)
    page: int | None = Field(default=None)
    figure: str | None = Field(default=None)
    table: str | None = Field(default=None)
    formula: str | None = Field(default=None)


class ChunkVersion(BaseModel):
    """A structural chunk of a document version.

    Chunks are the smallest retrievable units that preserve locator information
    so that claims and citations can point back to precise positions.
    """

    chunk_id: str = Field(description="Stable chunk identifier.")
    document_id: str = Field(description="Parent document version identifier.")
    source_id: str = Field(description="Source entry identifier.")
    parent_chunk_id: str | None = Field(default=None)
    previous_chunk_id: str | None = Field(default=None)
    next_chunk_id: str | None = Field(default=None)
    structure_path: ChunkStructurePath = Field(default_factory=ChunkStructurePath)
    start_offset: int = Field(default=0, ge=0)
    end_offset: int = Field(default=0, ge=0)
    text: str = Field(description="Chunk text content.")
    text_hash: str = Field(description="SHA-256 hash of chunk text.")
    parse_confidence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Confidence of automatic parsing."
    )
    human_corrected: bool = Field(default=False)
    injection_flags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(description="Chunk creation timestamp.")


class DocumentVersion(BaseModel):
    """Immutable content snapshot of a source.

    Each new upload, correction or re-parse creates a new DocumentVersion; older
    versions remain addressable so citations can be verified against the exact
    snapshot that produced them.
    """

    document_id: str = Field(description="Stable document version identifier.")
    source_id: str = Field(description="Parent source identifier.")
    version_label: str = Field(description="Human-readable version label, e.g. v1, v2.")
    version_number: int = Field(ge=1, description="Monotonic version number.")
    version_date: datetime = Field(description="Version timestamp.")
    retrieved_at: datetime | None = Field(default=None)
    publication_stage: str | None = Field(
        default=None, description="preprint, accepted_manuscript, version_of_record, etc."
    )
    lifecycle_status: LifecycleStatus = Field(default=LifecycleStatus.ACTIVE)
    status_evidence: list[str] = Field(default_factory=list)
    status_checked_at: datetime | None = Field(default=None)
    content_hash: str = Field(description="SHA-256 hash of raw content bytes.")
    metadata_hash: str = Field(description="SHA-256 hash of canonical metadata.")
    media_type: MediaType = Field(description="Media type of raw content.")
    language: str | None = Field(default=None)
    rights_snapshot: SourceLicense = Field(default_factory=SourceLicense)
    raw_blob_ref: str | None = Field(
        default=None, description="Reference to encrypted raw content object."
    )
    parser_id: str = Field(description="Parser that produced this version.")
    parser_version: str = Field(description="Parser version.")
    ocr_asr_id: str | None = Field(default=None)
    ocr_asr_version: str | None = Field(default=None)
    provenance_bundle_id: str | None = Field(default=None)
    superseded_by_document_id: str | None = Field(default=None)
    chunk_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(description="Document creation timestamp.")


class IngestionRunRef(BaseModel):
    """Reference to an asynchronous ingestion run."""

    run_id: str = Field(description="Ingestion run identifier.")
    source_id: str | None = Field(default=None)
    status: IngestionStatus = Field(default=IngestionStatus.PENDING)
    gate_results: dict[InputQualityGate, GateResult] = Field(default_factory=dict)
    error: str | None = Field(default=None)
    created_at: datetime = Field(description="Run creation timestamp.")
    updated_at: datetime = Field(description="Last run update timestamp.")


class SourceUploadRequest(BaseModel):
    """Request to upload a text or PDF scientific source."""

    filename: str = Field(description="Original filename.", min_length=1, max_length=500)
    media_type: MediaType = Field(description="Declared media type.")
    content: str = Field(
        description="Base64-encoded raw content bytes.", min_length=1
    )
    license_state: LicenseState | None = Field(default=None)
    title: str | None = Field(default=None)
    canonical_identity: str | None = Field(default=None)


class SourceVersionRequest(BaseModel):
    """Request to create a new document version from a corrected source."""

    base_document_id: str = Field(description="Document version to base the new version on.")
    chunk_corrections: list[ChunkCorrection] = Field(
        default_factory=list, description="Corrections to apply to chunks."
    )
    reason: str = Field(default="", description="Reason for the new version.")


class SourceProjection(BaseModel):
    """Public projection of a source with its current document version."""

    source: Source = Field(description="Source entry.")
    current_document: DocumentVersion | None = Field(default=None)
    current_chunks: list[ChunkVersion] = Field(default_factory=list)
    version_count: int = Field(default=0, ge=0)
    can_enter_evidence: bool = Field(
        default=False, description="Whether the source passed all input quality gates."
    )
    gate_results: dict[InputQualityGate, GateResult] = Field(default_factory=dict)


class SourceSummary(BaseModel):
    """List item for sources."""

    source_id: str = Field(description="Source identifier.")
    title: str | None = Field(default=None)
    media_type: MediaType | None = Field(default=None)
    status: SourceStatus = Field(description="Current source status.")
    version_count: int = Field(default=0, ge=0)
    updated_at: datetime = Field(description="Last update timestamp.")


class SourceError(BaseModel):
    """Uniform source error response."""

    error: str = Field(description="Stable error code.")
    message: str = Field(description="Human-readable, non-leaking message.")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Opaque detail safe for logging; must not expose internal state.",
    )


class ChunkCorrection(BaseModel):
    """A human correction to a single chunk.

    Corrections create a new DocumentVersion so history is preserved.
    """

    chunk_id: str = Field(description="Chunk to correct.")
    corrected_text: str = Field(description="Corrected chunk text.")
    reason: str | None = Field(default=None)


class ParseResult(BaseModel):
    """Result of parsing raw content into chunks."""

    chunks: list[ChunkVersion] = Field(default_factory=list)
    language: str | None = Field(default=None)
    parse_warnings: list[str] = Field(default_factory=list)
    injection_flags: list[str] = Field(default_factory=list)
