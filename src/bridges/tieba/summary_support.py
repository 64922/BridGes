"""贴吧总结的运行期计数、说明合并与最终请求封装。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from bridges.tieba.summary_contracts import (
    SUMMARY_AGGREGATION_SCHEMA,
    SUMMARY_CLAIMS_SCHEMA,
    SUMMARY_VERIFY_SCHEMA,
    TiebaSummaryMetrics,
    TiebaSummaryOutcome,
)


def merge_notes(*parts: str | None) -> str | None:
    values = [part for part in parts if part]
    return "；".join(dict.fromkeys(values)) or None


def stopped_outcome(
    tracker: TiebaSummaryTracker, notes: list[str]
) -> TiebaSummaryOutcome:
    return TiebaSummaryOutcome(
        None,
        [],
        [],
        merge_notes("已停止，未继续调用模型。", *notes),
        tracker.snapshot(),
    )


def request_payload(
    instruction: str,
    payload: dict[str, Any],
    output_key: str,
    output_tokens: int,
) -> dict[str, Any]:
    schema = (
        SUMMARY_VERIFY_SCHEMA
        if output_key == "support-check"
        else SUMMARY_AGGREGATION_SCHEMA
        if output_key == "aggregate"
        else SUMMARY_CLAIMS_SCHEMA
    )
    return {
        "messages": [
            {"role": "system", "content": "你是贴吧研究助手。" + instruction},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "json_schema": schema,
        "temperature": 0,
        "max_tokens": output_tokens,
    }


@dataclass
class TiebaSummaryTracker:
    """累计一次总结的批次、调用与证据计量（只含标识和数字）。"""

    model_id: str | None
    read_evidence_count: int = 0
    model_evidence_ids: set[str] = field(default_factory=set)
    batch_count: int = 0
    succeeded_batch_count: int = 0
    failed_batch_count: int = 0
    model_call_count: int = 0
    claim_count: int = 0
    supported_claim_count: int = 0
    summary_supported: bool = False

    def snapshot(self) -> TiebaSummaryMetrics:
        model_count = len(self.model_evidence_ids)
        return TiebaSummaryMetrics(
            model_id=self.model_id,
            read_evidence_count=self.read_evidence_count,
            model_evidence_count=model_count,
            omitted_evidence_count=max(0, self.read_evidence_count - model_count),
            batch_count=self.batch_count,
            succeeded_batch_count=self.succeeded_batch_count,
            failed_batch_count=self.failed_batch_count,
            model_call_count=self.model_call_count,
            claim_count=self.claim_count,
            supported_claim_count=self.supported_claim_count,
            summary_supported=self.summary_supported,
        )


__all__ = [
    "TiebaSummaryTracker",
    "merge_notes",
    "request_payload",
    "stopped_outcome",
]
