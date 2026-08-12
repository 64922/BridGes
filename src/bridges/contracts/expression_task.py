"""版本化表达任务契约（人味化改造 Issue 03）。

契约在生成前回答任务边界问题：这是聊天还是文章、现实承诺是什么、谁在说、
允许知道什么、改写多深、材料是否足够、是否允许第一人称或假设、用户期望的
受众/渠道/长度，以及是否进入证据安全修订。普通聊天和文章共用任务边界词汇，
但分别编译自己的轻量策略和完整文章流程，不合并成同一块总提示词。

契约一旦编译即不可变：``version_hash`` 覆盖全部语义字段，同一任务重试必须
复用原契约，不因规则热更新改变强度、来源或第一人称权限。
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bridges.contracts.expression import Genre


class Surface(StrEnum):
    """表面类型：明确区分普通聊天与文章。"""

    CHAT = "chat"
    ARTICLE = "article"


class Operation(StrEnum):
    """文章操作：改写、扩写与按主题生成。"""

    REWRITE = "rewrite"
    EXPAND = "expand"
    GENERATE_BY_TOPIC = "generate_by_topic"


class ConversationMode(StrEnum):
    """普通聊天的模式合同；文章流程不使用。"""

    CASUAL = "casual"
    LEARNING = "learning"


class RealityMode(StrEnum):
    """现实承诺：现实禁止补亲历；虚构或混合必须标明哪些内容允许创作。"""

    REAL = "real"
    FICTIONAL = "fictional"
    MIXED = "mixed"


class RewriteIntensity(StrEnum):
    """三级改写强度：轻度/标准/深度，默认标准，用户显式选择优先。"""

    LIGHT = "light"
    STANDARD = "standard"
    DEEP = "deep"


class MaterialSufficiency(StrEnum):
    """材料充分度裁决。"""

    SUFFICIENT = "sufficient"
    ASK_ONE_QUESTION = "ask_one_question"
    SHORTEN = "shorten"
    USE_PLACEHOLDERS = "use_placeholders"


class EvidenceRevisionMode(StrEnum):
    """证据修订模式：默认保留原结论；只有用户明确选择才证据安全修订。"""

    PRESERVE = "preserve"
    EVIDENCE_SAFE = "evidence_safe"


class SourceScope(StrEnum):
    """允许使用的来源范围。"""

    ORIGINAL_ONLY = "original_only"
    KNOWLEDGE_BASE = "knowledge_base"
    WEB = "web"


class RulePriorityLayer(StrEnum):
    """规则优先级固定链：用户显式要求与安全边界 → 来源/现实承诺 →
    表面与模式合同 → 场景表达规则 → 软风格检查。"""

    USER_AND_SAFETY = "user_and_safety"
    SOURCE_REALITY = "source_reality"
    SURFACE_MODE = "surface_mode"
    SCENE_EXPRESSION = "scene_expression"
    SOFT_STYLE = "soft_style"


class ContractAdjudication(BaseModel):
    """一条冲突或选择裁决：优先级层、判定结果与中文理由（可解释、稳定）。"""

    rule_id: str = Field(description="稳定裁决标识。")
    priority_layer: RulePriorityLayer = Field(description="裁决所依据的优先级层。")
    decision: str = Field(description="裁定结果的中文说明。")
    reason: str = Field(description="裁定依据的中文理由。")


class ExpressionTaskContract(BaseModel):
    """版本化表达任务契约：生成前回答全部任务边界问题。

    ``version_hash`` 是除自身外的规范化字段哈希，构成不可变快照；重试时
    调用方必须原样回传契约，编译器只做版本校验后复用，不重新编译。
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(description="契约 Schema 版本（如 expression-task-v1）。")
    version_hash: str = Field(description="规范化序列化哈希，不可变快照。")
    surface: Surface = Field(description="聊天还是文章。")
    operation: Operation = Field(description="改写、扩写还是按主题生成。")
    conversation_mode: ConversationMode | None = Field(
        default=None, description="普通聊天的模式合同；文章为 None。"
    )
    reality_mode: RealityMode = Field(description="现实/虚构/混合承诺。")
    rewrite_intensity: RewriteIntensity = Field(
        description="改写强度；标准为默认，用户显式选择优先。"
    )
    speaker_position: str = Field(description="说话位置（谁在说、以什么身份说）。")
    first_person_permission: bool = Field(description="是否允许第一人称。")
    hypothetical_permission: bool = Field(description="是否允许假设或设想内容。")
    audience: str | None = Field(default=None, description="目标受众；未声明为 None。")
    channel: str | None = Field(default=None, description="渠道；未声明为 None。")
    length_target: str | None = Field(default=None, description="长度或时长目标。")
    genre: Genre | None = Field(
        default=None, description="识别出的科学体裁；未识别为 None（通用文章 profile）。"
    )
    material_sufficiency: MaterialSufficiency = Field(description="材料充分度裁决。")
    source_scope: SourceScope = Field(description="允许使用的来源范围。")
    evidence_revision_mode: EvidenceRevisionMode = Field(
        description="证据修订模式；默认保留原结论。"
    )
    user_constraints: list[str] = Field(
        default_factory=list, description="用户声明的硬约束。"
    )
    one_question: str | None = Field(
        default=None, description="材料不足时返回的最高价值问题（只问一个）。"
    )
    profile_slice_id: str | None = Field(
        default=None, description="允许使用的最小画像切片标识。"
    )
    profile_item_count: int = Field(
        default=0, description="最小画像条目数（日志不含画像正文）。"
    )
    source_text_present: bool = Field(
        default=False, description="本次任务是否有用户提供的原文/材料。"
    )

    def compute_version_hash(self) -> str:
        """基于除 version_hash 外的全部规范化字段计算不可变哈希。"""
        payload = self.model_dump(exclude={"version_hash"}, mode="json")
        canonical = json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ContractCompileRecord(BaseModel):
    """契约编译的审计记录。

    只记录裁决元数据，不保存画像正文或完整私人材料：``profile_item_count``
    而非切片内容，``source_text_present`` 而非原文。
    """

    model_config = ConfigDict(extra="forbid")

    compile_id: str = Field(description="稳定编译标识。")
    schema_version: str = Field(description="编译使用的契约 Schema 版本。")
    version_hash: str = Field(description="产出契约的不可变哈希。")
    surface: Surface = Field(description="编译裁决的表面类型。")
    operation: Operation = Field(description="编译裁决的操作。")
    rewrite_intensity: RewriteIntensity = Field(description="编译裁决的改写强度。")
    material_sufficiency: MaterialSufficiency = Field(description="材料充分度裁决。")
    asked_question: bool = Field(description="是否返回了一个追问问题。")
    profile_slice_id: str | None = Field(default=None, description="允许的画像切片标识。")
    profile_item_count: int = Field(default=0, description="最小画像条目数。")
    degradation_reason: str | None = Field(
        default=None, description="降级原因（如材料不足触发 shorten/占位）。"
    )
    adjudications: list[ContractAdjudication] = Field(
        default_factory=list, description="规则优先级链上的稳定裁决。"
    )

    def adjudication_chain(self) -> list[str]:
        """按优先级链顺序输出可解释裁决链。"""
        order = {
            RulePriorityLayer.USER_AND_SAFETY: 0,
            RulePriorityLayer.SOURCE_REALITY: 1,
            RulePriorityLayer.SURFACE_MODE: 2,
            RulePriorityLayer.SCENE_EXPRESSION: 3,
            RulePriorityLayer.SOFT_STYLE: 4,
        }
        return [
            f"[{a.priority_layer.value}] {a.rule_id}: {a.decision}（{a.reason}）"
            for a in sorted(self.adjudications, key=lambda a: order[a.priority_layer])
        ]


__all__ = [
    "Surface",
    "Operation",
    "ConversationMode",
    "RealityMode",
    "RewriteIntensity",
    "MaterialSufficiency",
    "EvidenceRevisionMode",
    "SourceScope",
    "RulePriorityLayer",
    "ContractAdjudication",
    "ExpressionTaskContract",
    "ContractCompileRecord",
]
