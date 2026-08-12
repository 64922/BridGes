"""证据安全修订契约（人味化改造 Issue 06）。

区分「默认保留结论」与「用户授权的证据安全修订」：默认模式只生成独立
证据风险项，候选正文保持原结论语义；只有契约进入
``EvidenceRevisionMode.EVIDENCE_SAFE`` 时，才允许按来源账本降级结论措辞，
并为每项实质变化记录可重建的前后 span、变化类型、来源条目与理由。

本契约全部为确定性数据：claim 分类、风险项与修订变化都由程序在原文与
修订文本上重建，模型自述不能代替差异证据。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from bridges.contracts.expression_task import EvidenceRevisionMode
from bridges.contracts.humanizer import SpanLocation


class ClaimStrengthKind(StrEnum):
    """原创 claim 类型：观察/相关/预测/比较/机制/因果/显著性/泛化/真实场景有效性。

    覆盖 Issue 06 验收要求：区分「做了什么、看到什么、能说明什么、还不能
    说明什么」；类型判定只做句子级标记识别，不做统计推断。
    """

    OBSERVATION = "observation"
    CORRELATION = "correlation"
    PREDICTION = "prediction"
    COMPARISON = "comparison"
    MECHANISM = "mechanism"
    CAUSALITY = "causality"
    SIGNIFICANCE = "significance"
    GENERALIZATION = "generalization"
    REAL_WORLD_EFFECTIVENESS = "real_world_effectiveness"


class ClaimStrengthTier(StrEnum):
    """claim 当前强度档位（与事实锁强度分级同源词汇，独立枚举）。"""

    WEAK = "weak"
    MEDIUM = "medium"
    STRONG = "strong"


class EvidenceClaim(BaseModel):
    """一条 claim 分类结果：类型 + 当前强度 + 来源位置 + 绑定账本条目。

    位置基于所在文本（原文或修订文本，未规范化）；每个句子按最高优先级
    类型记一条，强度取句内出现的最强档词汇。
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(description="稳定 claim 标识。")
    kind: ClaimStrengthKind = Field(description="claim 类型（观察/相关/…）。")
    tier: ClaimStrengthTier = Field(description="当前强度档位。")
    surface: str = Field(description="claim 表面 span 文本。")
    location: SpanLocation = Field(description="所在文本中的位置。")
    source_entry_id: str | None = Field(
        default=None, description="绑定来源账本条目标识；无法判定为 None。"
    )
    note: str = Field(default="", description="中文说明（为何判为此类型/档位）。")


class EvidenceRiskCode(StrEnum):
    """稳定风险 code：默认模式只生成风险项，不改变正文。"""

    CORRELATION_AS_CAUSALITY = "correlation_as_causality"
    SIGNIFICANCE_WITHOUT_TEST = "significance_without_test"
    MECHANISM_FROM_FIT = "mechanism_from_fit"
    PREDICTION_AS_CAUSALITY = "prediction_as_causality"
    PREDICTION_AS_REAL_WORLD = "prediction_as_real_world"


class EvidenceRiskItem(BaseModel):
    """一条独立证据风险项（默认模式输出；正文保持原结论语义）。"""

    model_config = ConfigDict(extra="forbid")

    risk_id: str = Field(description="稳定风险标识。")
    code: EvidenceRiskCode = Field(description="稳定风险 code。")
    category: str = Field(description="中文类别说明。")
    surface: str = Field(description="触发风险 claim 的句子表面文本。")
    location: SpanLocation = Field(description="句子在候选正文中的位置。")
    explanation: str = Field(description="为何构成风险的中文说明（含账本依据）。")


class RevisionChangeType(StrEnum):
    """一次结论强度变化的类型（确定性比较结果，不是模型自述）。"""

    DOWNGRADED = "downgraded"
    REWORDED = "reworded"
    UPGRADED = "upgraded"
    REMOVED = "removed"


class EvidenceRevisionChange(BaseModel):
    """一条可重建的结论强度变化记录。"""

    model_config = ConfigDict(extra="forbid")

    change_id: str = Field(description="稳定变化标识。")
    change_type: RevisionChangeType = Field(description="变化类型。")
    kind: ClaimStrengthKind = Field(description="claim 类型。")
    original_span: str = Field(description="原文 span 表面文本。")
    revised_span: str = Field(description="改后 span 表面文本。")
    original_location: SpanLocation = Field(description="原文中的位置。")
    revised_location: SpanLocation = Field(description="改后文本中的位置。")
    source_entry_id: str | None = Field(
        default=None, description="本次变化消费的来源账本条目。"
    )
    reason: str = Field(description="为何修订的中文理由。")
    needs_user_confirmation: bool = Field(
        default=False, description="是否需用户确认（删除/升级/无法判定时 True）。"
    )


class EvidenceRevisionStatus(StrEnum):
    """修订状态：默认未修订 / 已应用 / 材料不足保持原文待用户确认。"""

    NOT_APPLIED = "not_applied"
    APPLIED = "applied"
    HOLD_FOR_USER = "hold_for_user"


class EvidenceSafeSummary(BaseModel):
    """脱敏计数（指标与审计用，不记录正文）。"""

    claim_count: int = Field(description="原文 claim 分类数。")
    by_kind: dict[str, int] = Field(default_factory=dict, description="按类型计数。")
    risk_count: int = Field(description="证据风险项数。")
    by_risk_code: dict[str, int] = Field(default_factory=dict, description="按风险码计数。")
    revision_count: int = Field(description="实质结论变化数。")
    protected_conflict_count: int = Field(description="受保护 span 冲突数。")
    hold_for_user_count: int = Field(description="需用户确认项数。")


class EvidenceSafeReport(BaseModel):
    """一次证据安全检查的完整报告（结果投影给 Issue 08 的稳定结构）。

    默认模式：``risks`` 非空、``revisions`` 为空、``revision_status`` 为
    not_applied，候选正文未被改动；普通无风险文章 ``risks`` 为空，不生成
    空洞的固定「事实核查总结」。
    """

    model_config = ConfigDict(extra="forbid")

    report_version: str = Field(description="报告 Schema 版本。")
    contract_hash: str = Field(description="本次任务表达任务契约的不可变哈希。")
    mode: EvidenceRevisionMode = Field(description="本次任务的证据修订模式。")
    claims: list[EvidenceClaim] = Field(
        default_factory=list, description="原文中的 claim 分类（全部类型）。"
    )
    risks: list[EvidenceRiskItem] = Field(
        default_factory=list, description="默认模式的风险项（不改变正文）。"
    )
    revisions: list[EvidenceRevisionChange] = Field(
        default_factory=list, description="授权修订的实质变化记录。"
    )
    revision_status: EvidenceRevisionStatus = Field(
        default=EvidenceRevisionStatus.NOT_APPLIED, description="修订状态。"
    )
    summary: EvidenceSafeSummary = Field(description="脱敏计数摘要。")


__all__ = [
    "ClaimStrengthKind",
    "ClaimStrengthTier",
    "EvidenceClaim",
    "EvidenceRiskCode",
    "EvidenceRiskItem",
    "RevisionChangeType",
    "EvidenceRevisionChange",
    "EvidenceRevisionStatus",
    "EvidenceSafeSummary",
    "EvidenceSafeReport",
]
