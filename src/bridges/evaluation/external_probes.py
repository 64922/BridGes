"""工单 42：外部能力最小真实探针与上线门一致性检查。

探针用**已授权配置**发出最小必要请求，逐门记录实测层次与如实降级文案；
凭据缺失或网络不可达记为 ``inconclusive``，不冒充通过也不冒充失败。
真实模型探针与外部服务探针共用同一报告结构，但由不同脚本分别运行，
保证「真实模型」与「外部可得性」分开报告。

一致性规则：产品宣称层不得高于实测层（``PRODUCT_CLAIMS`` 是当前代码
实际宣称的层次；探针报告同时给出二者）。未实测通过的能力必须保持降级。
"""

from __future__ import annotations

import time

from bridges.evaluation.external_probe_contracts import (
    LEVEL_ORDER,
    PRODUCT_CLAIMS,
    PRODUCT_DEGRADATION_CONTRACT,
    AvailabilityLevel,
    ProbeContext,
    ProbeFn,
    ProbeResult,
    ProbeStatus,
    _now,
)
from bridges.evaluation.external_probes_academic_routes import (
    probe_amap_campus_routes,
    probe_arxiv_full_text,
)
from bridges.evaluation.external_probes_model import probe_model_capabilities
from bridges.evaluation.external_probes_public_pages import (
    probe_github_files,
    probe_public_jobs,
    probe_tieba_replies,
    probe_video_intro,
    probe_web_search,
)
from bridges.evaluation.workflow_scenario_contracts import ExternalGate

PROBE_REGISTRY: dict[ExternalGate, ProbeFn] = {
    ExternalGate.ARXIV_FULL_TEXT: probe_arxiv_full_text,
    ExternalGate.AMAP_CAMPUS_ROUTES: probe_amap_campus_routes,
    ExternalGate.WEB_SEARCH: probe_web_search,
    ExternalGate.TIEBA_REPLIES: probe_tieba_replies,
    ExternalGate.PUBLIC_JOBS: probe_public_jobs,
    ExternalGate.VIDEO_INTRO: probe_video_intro,
    ExternalGate.GITHUB_FILES: probe_github_files,
    ExternalGate.MODEL_CAPABILITIES: probe_model_capabilities,
}


def run_probe(gate: ExternalGate, context: ProbeContext) -> ProbeResult:
    """执行单个探针；任何异常都转为可报告结论，不静默吞掉。"""
    started = time.monotonic()
    try:
        result = PROBE_REGISTRY[gate](context)
    except Exception as exc:  # noqa: BLE001 - 探针必须给出结论而不是中断报告
        result = ProbeResult(
            gate=gate,
            status=ProbeStatus.INCONCLUSIVE,
            level=AvailabilityLevel.CONFIGURED_UNVERIFIED,
            summary=f"探针执行异常（{type(exc).__name__}），未取得可判定结论。",
            degradation="未验证的能力保持降级。",
            checked_at=_now(),
        )
    return ProbeResult(
        gate=result.gate,
        status=result.status,
        level=result.level,
        summary=result.summary,
        measurements=result.measurements,
        degradation=result.degradation,
        checked_at=result.checked_at or _now(),
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def run_all_probes(
    context: ProbeContext, gates: tuple[ExternalGate, ...] | None = None
) -> list[ProbeResult]:
    """按登记顺序运行全部门（或指定门），返回逐门结论。"""
    selected = gates or tuple(PROBE_REGISTRY)
    return [run_probe(gate, context) for gate in selected]


def claim_consistency_problems(results: list[ProbeResult]) -> list[str]:
    """返回「宣称高于实测且无降级合同」的问题；空列表表示如实降级。

    有降级合同的门在实际不可用时按合同逐请求降级（由确定性场景证据支撑），
    不算违规；无降级合同的门（如模型能力）一旦实测不足即违规。
    """
    problems: list[str] = []
    for result in results:
        claim = PRODUCT_CLAIMS[result.gate]
        if LEVEL_ORDER[claim] <= LEVEL_ORDER[result.level]:
            continue
        if PRODUCT_DEGRADATION_CONTRACT[result.gate]:
            continue
        problems.append(
            f"{result.gate.value}: 宣称层 {claim.value} 高于实测层 "
            f"{result.level.value} 且无降级合同（{result.summary}）"
        )
    return problems
