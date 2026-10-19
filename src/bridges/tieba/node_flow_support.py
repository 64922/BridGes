"""节点输入解码、证据对象恢复与产物构造。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from bridges.contracts.modules import ModuleQueryRecord
from bridges.kernel.contracts import (
    ArtifactTrust,
    InputDependency,
    NodeArtifact,
    NodeExecution,
    NodeInvocation,
    NodeReceiptStatus,
    QualityVerdict,
)
from bridges.tieba.contracts import (
    TiebaCandidateLink,
    TiebaEvidencePlan,
    TiebaOfficialCheck,
    TiebaPostProjection,
    TiebaQuestionAnalysis,
    TiebaRejectedCandidate,
    TiebaSearchHit,
    TiebaSearchScope,
)
from bridges.tieba.evidence import ReadOutcome
from bridges.tieba.evidence_search import (
    SearchAttempts,
)
from bridges.tieba.recipe import (
    NODE_PARSE,
    NODE_PLAN,
    NODE_READ,
    NODE_SEARCH,
    NODE_VERIFY_OFFICIAL,
    TIEBA_RECIPE_ID,
    TIEBA_RECIPE_VERSION,
)
from bridges.tieba.searching import (
    REJECT_NOT_A_THREAD,
    HitDiagnostics,
)


def read_analysis(invocation: NodeInvocation) -> TiebaQuestionAnalysis:
    return TiebaQuestionAnalysis.model_validate(
        dependency_payload(invocation, NODE_PARSE)["analysis"]
    )


def read_plan(invocation: NodeInvocation) -> TiebaEvidencePlan:
    return TiebaEvidencePlan.model_validate(dependency_payload(invocation, NODE_PLAN)["plan"])


def read_checks(invocation: NodeInvocation) -> list[TiebaOfficialCheck]:
    checks: list[TiebaOfficialCheck] = []
    for raw in dependency_payload(invocation, NODE_VERIFY_OFFICIAL).get("checks", []):
        try:
            checks.append(TiebaOfficialCheck.model_validate(raw))
        except ValueError:
            continue
    return checks


def read_search_attempts(invocation: NodeInvocation) -> SearchAttempts:
    payload = dependency_payload(invocation, NODE_SEARCH)
    hits: list[TiebaSearchHit] = []
    for raw in payload.get("candidates", []):
        try:
            hits.append(TiebaSearchHit.model_validate(raw))
        except ValueError:
            continue
    rejected: list[TiebaRejectedCandidate] = []
    for raw in payload.get("rejected", []):
        try:
            rejected.append(TiebaRejectedCandidate.model_validate(raw))
        except ValueError:
            continue
    records: list[ModuleQueryRecord] = []
    for raw in payload.get("records", []):
        try:
            records.append(ModuleQueryRecord.model_validate(raw))
        except ValueError:
            continue
    # 产物里只记原始命中数与可用候选数；剔除构成按唯一剔除记录重建，
    # 保证跨节点恢复后「原始命中构成」与剔除记录条目仍能对上。
    not_a_thread = sum(1 for item in rejected if item.evidence == REJECT_NOT_A_THREAD)
    other_forum = len(rejected) - not_a_thread
    raw_hits = int(payload.get("raw_hits") or 0)
    usable = int(payload.get("usable") or len(hits))
    diagnostics = HitDiagnostics(
        raw_hits=raw_hits,
        not_a_thread=not_a_thread,
        other_forum=other_forum,
        duplicate=max(0, raw_hits - not_a_thread - other_forum - usable),
        usable=usable,
    )
    scope: list[TiebaSearchScope] = []
    for raw in payload.get("search_scope", []):
        try:
            scope.append(TiebaSearchScope.model_validate(raw))
        except ValueError:
            continue
    return SearchAttempts(
        hits=tuple(hits),
        rejected=tuple(rejected),
        records=records,
        diagnostics=diagnostics,
        rounds=tuple(str(note) for note in payload.get("rounds", [])),
        scope=tuple(scope),
        partial=bool(payload.get("partial")),
    )


def read_outcome(invocation: NodeInvocation) -> ReadOutcome:
    payload = dependency_payload(invocation, NODE_READ)
    confirmed: list[TiebaPostProjection] = []
    for raw in payload.get("confirmed", []):
        try:
            confirmed.append(TiebaPostProjection.model_validate(raw))
        except ValueError:
            continue
    rejected: list[TiebaRejectedCandidate] = []
    for raw in payload.get("rejected", []):
        try:
            rejected.append(TiebaRejectedCandidate.model_validate(raw))
        except ValueError:
            continue
    unconfirmed: list[TiebaCandidateLink] = []
    for raw in payload.get("unconfirmed", []):
        try:
            unconfirmed.append(TiebaCandidateLink.model_validate(raw))
        except ValueError:
            continue
    return ReadOutcome(
        confirmed=confirmed,
        rejected=rejected,
        unconfirmed=unconfirmed,
        unreadable=int(payload.get("unreadable") or 0),
        not_attempted=int(payload.get("not_attempted") or 0),
        unconfirmed_total=int(payload.get("unconfirmed_total") or len(unconfirmed)),
        attempted_urls=tuple(str(url) for url in payload.get("attempted_urls", [])),
        retry_urls=tuple(str(url) for url in payload.get("retry_urls", [])),
        partial=bool(payload.get("partial")),
    )


def dependency_payload(invocation: NodeInvocation, node: str) -> dict[str, Any]:
    return dict(invocation.dependencies[node].payload)


def build_artifact(
    invocation: NodeInvocation,
    *,
    clock: Callable[[], datetime],
    trust_state: ArtifactTrust,
    payload: dict[str, Any],
    source_refs: Sequence[str] = (),
    read_scope: str = "",
    requirement_coverage: Sequence[Mapping[str, Any]] = (),
    unconfirmed: Sequence[str] = (),
    error: dict[str, Any] | None = None,
) -> NodeArtifact:
    inputs = invocation.inputs
    dependencies = tuple(
        InputDependency(
            node=name,
            artifact_id=artifact.artifact_id,
            content_hash=artifact.content_hash,
        )
        for name, artifact in invocation.dependencies.items()
    )
    return NodeArtifact.build(
        account_id=inputs.account_id,
        conversation_id=inputs.conversation_id,
        run_id=inputs.run_id,
        task_id=inputs.task_id,
        task_version=inputs.task_version,
        recipe_id=TIEBA_RECIPE_ID,
        recipe_version=TIEBA_RECIPE_VERSION,
        node=invocation.spec.name,
        artifact_type=invocation.spec.artifact_type,
        capability_version=invocation.spec.capability_version,
        trust_state=trust_state,
        input_key=invocation.spec.input_key(inputs),
        input_deps=dependencies,
        source_refs=tuple(source_refs),
        read_scope=read_scope,
        requirement_coverage=tuple(dict(item) for item in requirement_coverage),
        unconfirmed=tuple(unconfirmed),
        error=error,
        payload=payload,
        now=clock(),
    )


def failure_execution(
    invocation: NodeInvocation,
    *,
    clock: Callable[[], datetime],
    payload: dict[str, Any],
    node: str,
    verdict: QualityVerdict,
    status: NodeReceiptStatus,
    code: str,
    message: str,
    retryable: bool,
) -> NodeExecution:
    artifact = build_artifact(
        invocation,
        clock=clock,
        trust_state=ArtifactTrust.INVALIDATED,
        payload=payload,
        error={"code": code, "message": message, "retryable": retryable},
    )
    return NodeExecution(
        artifact=artifact,
        verdict=verdict,
        status=status,
        detail={
            "code": code,
            "message": message,
            "retryable": retryable,
            "node": node,
        },
        stop_recipe=True,
        recovery=invocation.spec.recovery,
    )


def remaining_seconds(invocation: NodeInvocation, seconds: float) -> float:
    """本地截止与运行预算取较小者：预算用尽时不再空等上游。"""
    remaining = seconds
    if invocation.remaining_budget_ms is not None:
        remaining = min(remaining, invocation.remaining_budget_ms / 1000.0)
    return max(0.0, remaining)
