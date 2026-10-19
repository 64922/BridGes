"""贴吧总结的模型调用、契约版本与运行锁审计。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from bridges.ai.model_quota import RunModelQuota
from bridges.contracts.ai import CallContractVersions, ModelCallResult
from bridges.tieba.recipe import TIEBA_RECIPE_VERSION
from bridges.tieba.summary_contracts import (
    SUMMARY_INPUT_SCHEMA_VERSION,
    SUMMARY_PROMPT_VERSION,
    SUMMARY_SCHEMA_VERSION,
    TiebaSummaryAuditError,
)
from bridges.tieba.summary_support import request_payload


def invoke_summary(
    gateway: Any,
    on_result: Callable[[ModelCallResult], None] | None,
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
    result = gateway.invoke(
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
    if on_result is not None:
        try:
            on_result(result)
        except Exception as exc:
            raise TiebaSummaryAuditError("贴吧模型运行锁审计失败。") from exc
    return result
