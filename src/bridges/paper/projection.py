"""持久节点产物到用户论文投影的读取与派生。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from bridges.contracts.modules import ModuleQueryRecord
from bridges.kernel.contracts import KernelResult, KernelStatus, NodeArtifact
from bridges.paper.contracts import PaperQueryPlan, PaperTermAnalysis
from bridges.paper.lexicon import AMBIGUOUS_TERMS
from bridges.paper.parsing import pending_payload
from bridges.paper.planning import plan_queries
from bridges.paper.presenting import ExpressionPolicy
from bridges.paper.recipe import (
    NODE_ENRICH,
    NODE_EVALUATE,
    NODE_READ,
    NODE_SCREEN,
    NODE_SEARCH,
    NODE_VERIFY,
)


def _analysis(artifact: NodeArtifact | None) -> PaperTermAnalysis:
    if artifact is None:
        return PaperTermAnalysis(
            original_phrase="", normalized_term="", confidence=0.0, final_query=""
        )
    payload = artifact.payload.get("analysis")
    if not isinstance(payload, Mapping):
        return PaperTermAnalysis(
            original_phrase="",
            normalized_term="",
            confidence=0.0,
            final_query="",
        )
    return PaperTermAnalysis.model_validate(payload)


def _plan(artifact: NodeArtifact | None, analysis: PaperTermAnalysis) -> PaperQueryPlan:
    if artifact is None:
        return plan_queries(analysis)[0]
    plans = artifact.payload.get("plans") or []
    if not plans:
        fallback = plan_queries(analysis)
        if fallback:
            return fallback[0]
        return PaperQueryPlan(
            query="", source="", sort_by="relevance", sort_order="descending",
            max_results=1, target_count=1, rationale="没有可用来源。",
        )
    return PaperQueryPlan.model_validate(plans[0])


def _records(artifact: NodeArtifact | None) -> list[ModuleQueryRecord]:
    if artifact is None:
        return []
    return [
        ModuleQueryRecord.model_validate(item)
        for item in artifact.payload.get("records") or []
    ]


def _notes(result: KernelResult) -> list[str]:
    """汇总各节点如实声明的证据边界与未确认项（用户可见，不粉饰）。"""
    notes: list[str] = []
    for node in (
        NODE_SEARCH,
        NODE_SCREEN,
        NODE_READ,
        NODE_ENRICH,
        NODE_EVALUATE,
        NODE_VERIFY,
    ):
        artifact = result.artifact(node)
        if artifact is None:
            continue
        payload = artifact.payload
        for key in ("notes", "unconfirmed"):
            for item in payload.get(key) or ():
                text = (
                    str(item.get("message") or item.get("text") or "")
                    if isinstance(item, Mapping)
                    else str(item).strip()
                )
                if text:
                    notes.append(text)
    return list(dict.fromkeys(notes))


def _abstracts(artifact: NodeArtifact | None) -> dict[str, str]:
    if artifact is None:
        return {}
    return {
        str(item.get("arxiv_id") or item.get("candidate_id")): str(item.get("abstract") or "")
        for item in artifact.payload.get("candidates") or []
    }


def _artifact_refs(result: KernelResult) -> dict[str, str]:
    return {artifact.node: artifact.artifact_id for artifact in result.artifacts}


def _as_stopped(result: KernelResult) -> KernelResult:
    return replace(result, status=KernelStatus.STOPPED)


def _expression_snapshot(policy: Mapping[str, Any] | None) -> ExpressionPolicy | None:
    if not policy:
        return None
    try:
        from bridges.chat.global_writing_policy import GlobalWritingPolicySnapshot

        return GlobalWritingPolicySnapshot.model_validate(policy)
    except Exception:  # noqa: BLE001 - 旧快照或非法快照回退安全基线
        return None


def _clarification_payload(analysis: PaperTermAnalysis) -> dict[str, object]:
    return pending_payload(analysis, ambiguous_term=_ambiguous_key(analysis))


def _ambiguous_key(analysis: PaperTermAnalysis) -> str | None:
    """恢复澄清时按歧义术语的候选语境判定（术语键取自原始短语）。"""
    lowered = analysis.original_phrase.lower()
    for term in AMBIGUOUS_TERMS:
        if any(alias.lower() in lowered for alias in term.aliases):
            return term.term
    return None
