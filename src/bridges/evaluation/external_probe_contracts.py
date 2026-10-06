"""外部能力探测合同、当前宣称与降级依据。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import httpx

from bridges.evaluation.workflow_scenario_contracts import ExternalGate

PROBE_ACCOUNT_ID = "issue42-external-probe"


class ProbeStatus(StrEnum):
    """探针执行状态。"""

    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class AvailabilityLevel(StrEnum):
    """能力可用层次；顺序见 :data:`LEVEL_ORDER`。"""

    FULL = "full"
    PARTIAL = "partial"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    CONFIGURED_UNVERIFIED = "configured_unverified"


LEVEL_ORDER: dict[AvailabilityLevel, int] = {
    AvailabilityLevel.FULL: 4,
    AvailabilityLevel.PARTIAL: 3,
    AvailabilityLevel.DEGRADED: 2,
    AvailabilityLevel.UNAVAILABLE: 1,
    AvailabilityLevel.CONFIGURED_UNVERIFIED: 0,
}

#: 当前代码实际宣称的能力层次（产品层可得性合同的单一来源）。
PRODUCT_CLAIMS: dict[ExternalGate, AvailabilityLevel] = {
    ExternalGate.ARXIV_FULL_TEXT: AvailabilityLevel.DEGRADED,
    ExternalGate.AMAP_CAMPUS_ROUTES: AvailabilityLevel.PARTIAL,
    ExternalGate.TIEBA_REPLIES: AvailabilityLevel.PARTIAL,
    ExternalGate.PUBLIC_JOBS: AvailabilityLevel.PARTIAL,
    ExternalGate.VIDEO_INTRO: AvailabilityLevel.PARTIAL,
    ExternalGate.GITHUB_FILES: AvailabilityLevel.PARTIAL,
    ExternalGate.MODEL_CAPABILITIES: AvailabilityLevel.FULL,
    ExternalGate.WEB_SEARCH: AvailabilityLevel.FULL,
}

#: 产品对低可用层次是否有已测试的如实降级合同（由确定性场景证据支撑）。
PRODUCT_DEGRADATION_CONTRACT: dict[ExternalGate, bool] = {
    ExternalGate.ARXIV_FULL_TEXT: True,
    ExternalGate.AMAP_CAMPUS_ROUTES: True,
    ExternalGate.TIEBA_REPLIES: True,
    ExternalGate.PUBLIC_JOBS: True,
    ExternalGate.VIDEO_INTRO: True,
    ExternalGate.GITHUB_FILES: True,
    ExternalGate.MODEL_CAPABILITIES: False,
    ExternalGate.WEB_SEARCH: True,
}

#: 降级合同依据（确定性证据场景；无合同的门必须实测通过才可用）。
PRODUCT_DEGRADATION_BASIS: dict[ExternalGate, str] = {
    ExternalGate.ARXIV_FULL_TEXT: "A10：未读全文时结论只基于已读证据",
    ExternalGate.AMAP_CAMPUS_ROUTES: "A12：某方式无路线按方式如实失败",
    ExternalGate.TIEBA_REPLIES: "A14：只交付线索，不总结未读回复",
    ExternalGate.PUBLIC_JOBS: "A16：不可读时只给未核实链接",
    ExternalGate.VIDEO_INTRO: "A09：视频失败仍交付书目与真实缺口",
    ExternalGate.GITHUB_FILES: "A17/A18：只基于已读文件下结论",
    ExternalGate.MODEL_CAPABILITIES: "无降级：声明能力未实测通过不得激活",
    ExternalGate.WEB_SEARCH: "L04：一路失败只交付可支持部分并标缺口",
}

#: 宣称依据（写清产品当前如实保证到哪一层，便于探针结果对照审查）。
PRODUCT_CLAIM_BASIS: dict[ExternalGate, str] = {
    ExternalGate.ARXIV_FULL_TEXT: ("PaperSearchService 未装配全文读取，界面按摘要+书目层交付"),
    ExternalGate.AMAP_CAMPUS_ROUTES: "三种方式独立调用；某方式无路线按方式如实失败",
    ExternalGate.TIEBA_REPLIES: "公开页有界读取；受限时只交付帖链，不总结未读回复",
    ExternalGate.PUBLIC_JOBS: "只把公开可读岗位纳入样本；读不到只给未核实链接",
    ExternalGate.VIDEO_INTRO: "只交付核对过的公开元数据（标题/时长/简介）",
    ExternalGate.GITHUB_FILES: "公开 REST API 只读；受限时只基于已读文件下结论",
    ExternalGate.MODEL_CAPABILITIES: "四项能力（文本/图片/工具/结构化）须实测通过才激活",
    ExternalGate.WEB_SEARCH: "Tavily 公网搜索服务按预算、缓存与审计路径交付",
}


@dataclass(frozen=True)
class ProbeContext:
    """一次探针运行所需的凭据、模型运行配置与共享 HTTP 客户端。"""

    http: httpx.Client
    qwen_key: str | None = None
    tavily_key: str | None = None
    amap_key: str | None = None
    effective_model_id: str = ""
    model_capabilities: tuple[str, ...] = ()
    model_context_window: int | None = None
    model_config_source: str = "factory"


@dataclass(frozen=True)
class ProbeResult:
    """单个外部门的最小真实探测结论。"""

    gate: ExternalGate
    status: ProbeStatus
    level: AvailabilityLevel
    summary: str
    measurements: dict[str, Any] = field(default_factory=dict)
    degradation: str = ""
    checked_at: str = ""
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate.value,
            "status": self.status.value,
            "level": self.level.value,
            "declared_level": PRODUCT_CLAIMS[self.gate].value,
            "declared_basis": PRODUCT_CLAIM_BASIS[self.gate],
            "degradation_contract": PRODUCT_DEGRADATION_CONTRACT[self.gate],
            "degradation_basis": PRODUCT_DEGRADATION_BASIS[self.gate],
            "summary": self.summary,
            "measurements": self.measurements,
            "degradation": self.degradation,
            "checked_at": self.checked_at,
            "duration_ms": self.duration_ms,
        }


ProbeFn = Callable[[ProbeContext], ProbeResult]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _missing_key(gate: ExternalGate, what: str) -> ProbeResult:
    return ProbeResult(
        gate=gate,
        status=ProbeStatus.INCONCLUSIVE,
        level=AvailabilityLevel.CONFIGURED_UNVERIFIED,
        summary=f"未配置{what}，未发出任何请求；该门保持未验证。",
        degradation="未验证的能力不宣称深读，界面保持如实降级。",
        checked_at=_now(),
    )
