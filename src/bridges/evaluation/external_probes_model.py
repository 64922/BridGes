"""当前生效模型的能力探针。"""

from __future__ import annotations

import json

from pydantic import SecretStr

from bridges.ai.model_probe import ModelCapabilityProbe
from bridges.ai.qwen_client import QwenApiClient, choice_text, first_choice
from bridges.contracts.ai import ModelCapabilities
from bridges.evaluation.external_probe_contracts import (
    AvailabilityLevel,
    ProbeContext,
    ProbeResult,
    ProbeStatus,
    _missing_key,
    _now,
)
from bridges.evaluation.workflow_scenario_contracts import ExternalGate


def probe_model_capabilities(context: ProbeContext) -> ProbeResult:
    """对当前生效模型逐项实测文本/图片/工具调用/结构化输出。"""
    gate = ExternalGate.MODEL_CAPABILITIES
    if not context.qwen_key:
        return _missing_key(gate, "Qwen API Key")
    declared = context.model_capabilities or (
        "text",
        "image",
        "tool_calling",
        "structured_output",
    )
    capabilities = ModelCapabilities(
        text="text" in declared,
        image="image" in declared,
        tool_calling="tool_calling" in declared,
        structured_output="structured_output" in declared,
    )
    client = QwenApiClient(
        api_key=SecretStr(context.qwen_key),
        workspace_id=None,
        region="cn",
        http_client=context.http,
    )
    outcomes = ModelCapabilityProbe(client).run(
        model_id=context.effective_model_id,
        capabilities=capabilities,
    )
    results = {
        outcome.capability: {
            "ok": outcome.ok,
            "error_code": outcome.error_code,
            "message": outcome.message,
        }
        for outcome in outcomes
    }
    context_sample = probe_context_sample(client, context.effective_model_id)
    all_ok = bool(outcomes) and all(outcome.ok for outcome in outcomes) and context_sample["ok"]
    text_ok = all(outcome.ok for outcome in outcomes if outcome.capability == "text")
    if all_ok:
        level = AvailabilityLevel.FULL
        status = ProbeStatus.PASSED
    elif text_ok:
        level = AvailabilityLevel.DEGRADED
        status = ProbeStatus.FAILED
    else:
        level = AvailabilityLevel.UNAVAILABLE
        status = ProbeStatus.FAILED
    failed = [outcome.capability for outcome in outcomes if not outcome.ok]
    if not context_sample["ok"]:
        failed.append("bounded_context")
    return ProbeResult(
        gate=gate,
        status=status,
        level=level,
        summary=(
            f"生效模型 {context.effective_model_id}（{context.model_config_source}）"
            + ("四项能力与有界上下文样本实测通过。" if all_ok else f"未通过：{'、'.join(failed)}。")
        ),
        measurements={
            "model_id": context.effective_model_id,
            "config_source": context.model_config_source,
            "context_window": context.model_context_window,
            "context_sample": context_sample,
            "context_window_note": "窗口上限为配置元数据；有界样本通过不证明整个窗口可靠。",
            "capabilities": results,
        },
        degradation="未实测通过的能力不得激活或宣称为可用。",
        checked_at=_now(),
    )


def probe_context_sample(client: QwenApiClient, model_id: str) -> dict[str, object]:
    """一次最小合成上下文检索；只声明本次输入范围，避免大额边界探测。"""
    text = (
        "开头标记=bridge-alpha。\n"
        + "这里是无用户信息的合成上下文填充。\n" * 80
        + "中间标记=bridge-beta。\n"
        + "这里是无用户信息的合成上下文填充。\n" * 80
        + "末尾标记=bridge-gamma。"
    )
    response = client.chat_completions(
        {
            "model": model_id,
            "messages": [
                {"role": "system", "content": "只返回 JSON 数组，按顺序列出三个标记的值。"},
                {"role": "user", "content": text},
            ],
            "temperature": 0,
            "max_completion_tokens": 128,
            "enable_thinking": False,
        },
        timeout=30,
    )
    choice = first_choice(response)
    try:
        values = json.loads(choice_text(choice))
    except (ValueError, TypeError):
        values = None
    return {
        "ok": choice.get("finish_reason") == "stop"
        and values == ["bridge-alpha", "bridge-beta", "bridge-gamma"],
        "input_chars": len(text),
        "usage": response.get("usage"),
        "scope": "仅本次合成输入范围；未验证配置窗口上限",
    }
