"""贴吧已读证据的有界模型总结及二次支持度核验。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from bridges.ai.model_quota import RunModelQuota
from bridges.ai.payload_budget import evaluate_payload_gate
from bridges.contracts.ai import CallContractVersions, ModelCallResult
from bridges.tieba.contracts import (
    TiebaPostProjection,
    TiebaSummaryCitation,
    TiebaSummaryClaim,
    TiebaSummaryClaimKind,
    TiebaTimeFilter,
    TiebaTimeScopeMode,
)
from bridges.tieba.recipe import TIEBA_RECIPE_VERSION
from bridges.tieba.summary_contracts import (
    ITEMS_PER_BATCH,
    MAX_BATCH_CALLS,
    MAX_EVIDENCE_ITEMS,
    MAX_SUMMARY_CLAIMS,
    SUMMARY_AGGREGATE_INSTRUCTION,
    SUMMARY_CLAIMS_INSTRUCTION,
    SUMMARY_INPUT_SCHEMA_VERSION,
    SUMMARY_OUTPUT_TOKENS,
    SUMMARY_PROMPT_VERSION,
    SUMMARY_SCHEMA_VERSION,
    SUMMARY_VERIFY_INSTRUCTION,
    VERIFY_OUTPUT_TOKENS,
    TiebaSummaryAuditError,
    TiebaSummaryOutcome,
)
from bridges.tieba.summary_material import (
    aggregation_payload,
    build_verification_payload,
    contains_consensus_phrase,
    deduplicate_unknown_group_citations,
    read_claims,
    read_summary,
    sources,
    summary_payload,
    supported_indices,
    time_scoped_materials,
)
from bridges.tieba.summary_support import (
    TiebaSummaryTracker,
    merge_notes,
    request_payload,
)


class TiebaSummaryGenerator:
    """只向模型发送目标吧实际读取的内容，引用身份由代码回填。"""

    def __init__(
        self,
        gateway: Any,
        *,
        on_result: Callable[[ModelCallResult], None] | None = None,
    ) -> None:
        self._gateway = gateway
        self._on_result = on_result

    def generate(
        self,
        run_context: Any,
        posts: list[TiebaPostProjection],
        *,
        topic: str,
        time_requirement: str | None = None,
        time_filter: TiebaTimeFilter | None = None,
        model_id: str | None,
        model_quota: RunModelQuota | None,
        budget: Any = None,
    ) -> TiebaSummaryOutcome:
        all_materials = sources(posts)
        tracker = TiebaSummaryTracker(
            model_id=model_id or (model_quota.model_id if model_quota is not None else None)
        )
        historical_count = sum(
            item.get("temporal_scope") == "historical" for item in all_materials
        )
        unknown_count = sum(item.get("temporal_scope") == "unknown" for item in all_materials)
        materials, time_notes = time_scoped_materials(
            all_materials,
            mode=(
                time_filter.mode
                if time_filter is not None
                else TiebaTimeScopeMode.EXPLICIT
                if time_requirement
                else None
            ),
            requirement=time_requirement,
        )
        tracker.read_evidence_count = len(materials)
        if not materials:
            if time_filter is not None and time_filter.mode is TiebaTimeScopeMode.UNAVAILABLE:
                note = (
                    f"时间条件「{time_requirement or time_filter.requirement or '未注明'}」"
                    "无法可靠换算成日历区间；本轮没有把范围外发言当成范围内证据，"
                    "因此未生成总结。"
                )
            elif time_requirement and (historical_count or unknown_count):
                note = (
                    f"时间条件内没有可总结的发言；已排除历史原文 {historical_count} 条、"
                    f"时间未知原文 {unknown_count} 条。"
                )
            else:
                note = "本轮没有读到可供总结的帖子或回复原文。"
            return TiebaSummaryOutcome(None, [], [], note, tracker.snapshot())
        historical_context_count = sum(
            1
            for item in materials
            if item.get("context_only") and item.get("temporal_scope") == "historical"
        )
        historical_excluded_count = sum(
            1
            for item in all_materials
            if item.get("temporal_scope") == "historical"
            and not item.get("context_only")
            and item not in materials
        )
        unknown_excluded_count = sum(
            1
            for item in all_materials
            if item.get("temporal_scope") == "unknown"
            and not item.get("context_only")
            and item not in materials
        )
        if model_quota is None or not model_quota.is_verified:
            return TiebaSummaryOutcome(
                None,
                [],
                [],
                "总结未生成：本轮模型额度未经验证。",
                tracker.snapshot(),
            )

        claim_rows: list[tuple[TiebaSummaryClaimKind, str, list[str]]] = []
        batch_notes: list[str] = list(time_notes)
        if time_requirement and historical_excluded_count:
            batch_notes.append(
                f"显式时间条件下未将 {historical_excluded_count} 条历史发言作为总结依据。"
            )
        if historical_context_count:
            batch_notes.append(
                f"另附 {historical_context_count} 条相关历史父回复，仅供理解近期楼中楼上下文，"
                "不作为时间范围内的观点依据或引用。"
            )
        if time_requirement and unknown_excluded_count:
            batch_notes.append(
                f"时间条件下排除了 {unknown_excluded_count} 条发言时间未知的原文。"
            )
        omitted_sources = max(0, len(materials) - MAX_EVIDENCE_ITEMS)
        if omitted_sources:
            batch_notes.append(f"超过模型素材上限，另有 {omitted_sources} 条已读原文未进入总结。")
        materials = materials[:MAX_EVIDENCE_ITEMS]
        batches = [
            materials[index : index + ITEMS_PER_BATCH]
            for index in range(0, len(materials), ITEMS_PER_BATCH)
        ]
        tracker.batch_count = len(batches)
        pending_batches = list(batches)
        summary_calls = 0
        batch_index = 0
        while pending_batches and summary_calls < MAX_BATCH_CALLS:
            batch = pending_batches.pop(0)
            batch_index += 1
            payload = summary_payload(topic, batch)
            instruction = SUMMARY_CLAIMS_INSTRUCTION
            request = request_payload(
                instruction, payload, "claims", SUMMARY_OUTPUT_TOKENS
            )
            gate = evaluate_payload_gate(
                request, quota=model_quota, output_tokens=SUMMARY_OUTPUT_TOKENS
            )
            if not gate.within_budget:
                if (
                    len(batch) > 1
                    and summary_calls + len(pending_batches) < MAX_BATCH_CALLS
                ):
                    middle = len(batch) // 2
                    pending_batches[0:0] = [batch[:middle], batch[middle:]]
                    tracker.batch_count += 1
                else:
                    batch_notes.append(
                        f"第 {batch_index} 组证据超出当前模型输入额度，未发送该组载荷。"
                    )
                continue
            tracker.model_evidence_ids.update(str(item["id"]) for item in batch)
            tracker.model_call_count += 1
            summary_calls += 1
            try:
                result = self._invoke(
                    instruction,
                    payload,
                    run_context,
                    model_id,
                    model_quota,
                    budget,
                    "claims",
                    SUMMARY_OUTPUT_TOKENS,
                    final_payload=request,
                )
            except TiebaSummaryAuditError:
                raise
            except Exception:  # noqa: BLE001 - 总结失败不丢弃真实采集
                tracker.failed_batch_count += 1
                batch_notes.append(f"第 {batch_index} 组模型总结调用失败。")
                continue
            tracker.succeeded_batch_count += 1
            parsed_claims = read_claims(result)
            allowed = {str(item["id"]) for item in batch if not item.get("context_only")}
            accepted = [
                (kind, text, ids)
                for kind, text, ids in parsed_claims
                if text
                and ids
                and all(citation_id in allowed for citation_id in ids)
                and not contains_consensus_phrase(text)
            ]
            claim_rows.extend(accepted)
            if not accepted:
                batch_notes.append(f"第 {batch_index} 组证据没有产生可定位观点。")
        if pending_batches:
            unsent = sum(len(batch) for batch in pending_batches)
            batch_notes.append(
                f"模型调用额度已满，另有 {unsent} 条证据未送入总结。"
            )

        if not claim_rows:
            return TiebaSummaryOutcome(
                None,
                [],
                [],
                merge_notes("总结未生成：模型没有返回绑定本轮已读证据的观点。", *batch_notes),
                tracker.snapshot(),
            )
        # 同一观点可能在分批结果中重复；保留首次出现及其证据集合。
        unique: dict[tuple[TiebaSummaryClaimKind, str], list[str]] = {}
        for kind, text, ids in claim_rows:
            unique.setdefault((kind, text), list(dict.fromkeys(ids)))
        unique_claims = list(unique.items())
        if len(unique_claims) > MAX_SUMMARY_CLAIMS:
            batch_notes.append(
                f"观点超过 {MAX_SUMMARY_CLAIMS} 条展示上限，"
                f"另有 {len(unique_claims) - MAX_SUMMARY_CLAIMS} 条未进入总结。"
            )
        source_by_id = {
            str(item["id"]): item for item in materials if not item.get("context_only")
        }
        summary_claims = [
            TiebaSummaryClaim(kind=kind, text=text, citation_ids=ids)
            for (kind, text), ids in unique_claims[:MAX_SUMMARY_CLAIMS]
            if all(citation_id in source_by_id for citation_id in ids)
            and not contains_consensus_phrase(text)
        ]
        aggregate_payload = aggregation_payload(topic, summary_claims)
        aggregate_request = request_payload(
            SUMMARY_AGGREGATE_INSTRUCTION,
            aggregate_payload,
            "aggregate",
            SUMMARY_OUTPUT_TOKENS,
        )
        aggregate_gate = evaluate_payload_gate(
            aggregate_request, quota=model_quota, output_tokens=SUMMARY_OUTPUT_TOKENS
        )
        if not aggregate_gate.within_budget:
            return TiebaSummaryOutcome(
                None,
                [],
                [],
                merge_notes(
                    "观点汇总载荷超出本轮模型额度；未提交分段拼接式摘要。",
                    *batch_notes,
                ),
                tracker.snapshot(),
            )
        tracker.model_call_count += 1
        try:
            aggregated = self._invoke(
                SUMMARY_AGGREGATE_INSTRUCTION,
                aggregate_payload,
                run_context,
                model_id,
                model_quota,
                budget,
                "aggregate",
                SUMMARY_OUTPUT_TOKENS,
                final_payload=aggregate_request,
            )
        except TiebaSummaryAuditError:
            raise
        except Exception:  # noqa: BLE001 - 整合失败时不拼接冒充总结
            return TiebaSummaryOutcome(
                None,
                [],
                [],
                merge_notes("观点语义整合失败；未提交分段拼接式摘要。", *batch_notes),
                tracker.snapshot(),
            )
        aggregate_summary = read_summary(aggregated)
        if aggregate_summary and contains_consensus_phrase(aggregate_summary):
            batch_notes.append("连贯总结含全体共识式表述，未提交该正文。")
            aggregate_summary = None
        allowed = {
            citation_id
            for claim in summary_claims
            for citation_id in claim.citation_ids
        }
        aggregated_claims: list[TiebaSummaryClaim] = []
        rejected_unsent = 0
        for kind, text, ids in read_claims(aggregated):
            if not text or not ids or contains_consensus_phrase(text):
                continue
            if all(citation_id in allowed for citation_id in ids):
                aggregated_claims.append(
                    TiebaSummaryClaim(kind=kind, text=text, citation_ids=ids)
                )
            elif any(citation_id in source_by_id for citation_id in ids):
                rejected_unsent += 1
        if rejected_unsent:
            batch_notes.append(
                f"整合结果有 {rejected_unsent} 条观点引用了未送入模型的证据，已拒绝。"
            )
        if not aggregated_claims:
            return TiebaSummaryOutcome(
                None,
                [],
                [],
                merge_notes("观点整合没有产生可验证的带引用观点。", *batch_notes),
                tracker.snapshot(),
            )
        aggregated_claims, collapsed = deduplicate_unknown_group_citations(
            aggregated_claims, source_by_id
        )
        if collapsed:
            batch_notes.append("同一未知身份的重复原文只按一份支持计。")
        summary_claims = aggregated_claims
        tracker.claim_count = len(summary_claims)
        summary_citation_ids = list(
            dict.fromkeys(
                citation_id for claim in summary_claims for citation_id in claim.citation_ids
            )
        )
        verification_payload = build_verification_payload(
            summary_claims,
            source_by_id,
            summary_text=aggregate_summary,
            summary_citation_ids=summary_citation_ids,
        )
        verify_count = len(summary_claims) + (1 if aggregate_summary else 0)
        verify_request = request_payload(
            SUMMARY_VERIFY_INSTRUCTION,
            verification_payload,
            "support-check",
            VERIFY_OUTPUT_TOKENS,
        )
        gate = evaluate_payload_gate(
            verify_request, quota=model_quota, output_tokens=VERIFY_OUTPUT_TOKENS
        )
        if not gate.within_budget:
            return TiebaSummaryOutcome(
                None,
                [],
                [],
                merge_notes(
                    "总结核验载荷超出本轮模型额度；未提交未经核验的观点。",
                    *batch_notes,
                ),
                tracker.snapshot(),
            )
        tracker.model_call_count += 1
        try:
            checked = self._invoke(
                SUMMARY_VERIFY_INSTRUCTION,
                verification_payload,
                run_context,
                model_id,
                model_quota,
                budget,
                "support-check",
                VERIFY_OUTPUT_TOKENS,
                final_payload=verify_request,
            )
        except TiebaSummaryAuditError:
            raise
        except Exception:  # noqa: BLE001 - 核验失败时闭锁总结
            return TiebaSummaryOutcome(
                None,
                [],
                [],
                merge_notes(
                    "总结的语义支持度核验失败；未提交未经核验的观点。",
                    *batch_notes,
                ),
                tracker.snapshot(),
            )
        supported = supported_indices(checked, verify_count)
        supported_claims = [
            claim for index, claim in enumerate(summary_claims) if index in supported
        ]
        summary_supported = bool(aggregate_summary) and len(summary_claims) in supported
        if not supported_claims and not summary_supported:
            return TiebaSummaryOutcome(
                None,
                [],
                [],
                merge_notes("总结核验未能确认观点得到所引原文支持。", *batch_notes),
                tracker.snapshot(),
            )
        if aggregate_summary and not summary_supported:
            batch_notes.append("连贯总结未通过语义支持核验，未提交总结正文。")
        tracker.supported_claim_count = len(supported_claims)
        tracker.summary_supported = summary_supported
        citation_ids = list(
            dict.fromkeys(
                [
                    *(summary_citation_ids if summary_supported else []),
                    *(
                        citation_id
                        for claim in supported_claims
                        for citation_id in claim.citation_ids
                    ),
                ]
            )
        )
        citations = [
            TiebaSummaryCitation(
                citation_id=citation_id,
                url=str(source_by_id[citation_id]["url"]),
                title=source_by_id[citation_id].get("title"),
                reply_id=source_by_id[citation_id].get("reply_id"),
                parent_reply_id=source_by_id[citation_id].get("parent_reply_id"),
                floor=source_by_id[citation_id].get("floor"),
                posted_at=source_by_id[citation_id].get("posted_at"),
                author=source_by_id[citation_id].get("author"),
                nested=bool(source_by_id[citation_id].get("nested")),
                quote=str(source_by_id[citation_id]["content"])[:240],
            )
            for citation_id in citation_ids
        ]
        summary: str | None = aggregate_summary if summary_supported else None
        return TiebaSummaryOutcome(
            summary,
            supported_claims,
            citations,
            merge_notes(*batch_notes),
            tracker.snapshot(),
        )

    def _invoke(
        self,
        instruction: str,
        payload: dict[str, Any],
        run_context: Any,
        model_id: str | None,
        quota: RunModelQuota,
        budget: Any,
        output_key: str,
        output_tokens: int,
        *,
        final_payload: dict[str, Any] | None = None,
    ) -> ModelCallResult:
        model_payload = final_payload or request_payload(
            instruction, payload, output_key, output_tokens
        )
        result = self._gateway.invoke(
            "qwen_structured_output",
            "1",
            run_context,
            model_payload,
            budget=budget,
            model_override=model_id,
            model_quota=quota,
            call_contract=CallContractVersions(
                prompt_version=SUMMARY_PROMPT_VERSION,
                input_schema_version=SUMMARY_INPUT_SCHEMA_VERSION,
                output_schema_version=SUMMARY_SCHEMA_VERSION,
                recipe_version=TIEBA_RECIPE_VERSION,
                context_compile_version="payload-budget-v2",
                quality_policy_version="tieba-citation-support-v1",
                estimate_version="token-estimate-v2",
                quota_version=quota.quota_version,
            ),
        )
        result = cast(ModelCallResult, result)
        if self._on_result is not None:
            try:
                self._on_result(result)
            except Exception as exc:
                raise TiebaSummaryAuditError("贴吧模型运行锁审计失败。") from exc
        return result
