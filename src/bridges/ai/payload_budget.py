"""最终模型载荷预算门与调用材料清单（改进工单 04）。

改进工单 03 已把「一次运行实际使用的模型额度」做成入队原子保存的快照，但
编译器只控制历史文本；工具、检索、附件说明、画像、记忆纠正与表达策略在
编译之后**追加**到最终载荷，没有共同的最终预算门（``docs/上下文工程/审查与
改进建议.md`` §1）。复核里预算 408、编译估算 13，最终载荷估算 1,013；1 token
余量仍能插入估算 250 token 的画像块。

本模块把「一次调用的最终输入预算」做成**共同调用边界**上的硬门：

- 输入硬上界 = ``min(已验证最大输入额度, 已验证上下文窗口 − 本次输出预留 −
  安全余量)``，由运行额度快照（:class:`~bridges.ai.model_quota.RunModelQuota`）
  提供，绝不用缺省窗口冒充已验证额度；实际任务预算可以更小。
- 最终消息、角色封装、系统规则、图片部件、工具声明与已取得工具结果全部计入
  本次实际输入；图片部件按 :data:`IMAGE_PART_COST_TOKENS` 计成本，不因来源
  类别绕过预算。
- 按任务必要性裁剪：先无关（``IRRELEVANT``）、再可选（``OPTIONAL``）、再背景
  （``BACKGROUND``）；``REQUIRED``（当前请求、权限规则、有效硬条件、当前结论
  的必要证据）绝不静默丢弃。放不下时由调用方给出明确的受限结果，不发送超限
  载荷。
- 每次实际调用形成**脱敏材料清单**：材料 ID／类别／必要性／来源版本／读取
  范围、采用与排除原因、预算门结果、估算与可取得的实际用量。清单不含任何
  正文、凭据或图片内容。

估算（:data:`TOKEN_ESTIMATE_VERSION`）不是供应商真实分词计数，只用于预算控制；
后续工单 40 用可取得的实际用量校准。``estimate_tokens`` 的单一事实源在本模块，
``chat.context_compiler`` 复用同一实现，避免两处估算漂移。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Any
from unicodedata import category

from bridges.ai.model_quota import RunModelQuota

#: 载荷预算合同版本（估算/裁剪/门公式变化时递增）。
PAYLOAD_BUDGET_VERSION = "payload-budget-v1"
#: 材料清单合同版本（字段或语义变化时递增）。
MATERIAL_MANIFEST_VERSION = "material-manifest-v1"
#: token 估算版本（估算规则变化时递增）。单一事实源，``context_compiler`` 复用。
TOKEN_ESTIMATE_VERSION = "token-estimate-v1"

#: 单张图片部件的输入成本估算（文本预算口径下的保守常量）。图片数量与输入
#: 方式都进入预算，照片轮不再绕过编译。
IMAGE_PART_COST_TOKENS = 1024
#: 发送前安全余量（token）：在输出预留之外再留出的估算误差空间。
PAYLOAD_SAFETY_MARGIN_TOKENS = 256
#: 载荷未显式声明输出额度时的保守缺省（与主对话调用额度同量级）。
DEFAULT_PAYLOAD_OUTPUT_TOKENS = 1024

#: 清单**绝不出现**的敏感字段（导出与审计据此声明排除范围）。
MANIFEST_FORBIDDEN_FIELDS: tuple[str, ...] = (
    "api_key",
    "authorization",
    "auth_token",
    "access_token",
    "secret",
    "password",
    "credential",
    "prompt",
    "messages",
    "content",
    "response",
    "image",
    "image_url",
    "video",
    "audio",
    "ocr",
)


def estimate_tokens(text: str) -> int:
    """确定性 token 估算（:data:`TOKEN_ESTIMATE_VERSION`）。

    CJK 汉字与全角/CJK 标点按 1 token/字、其余字符按 4 字符 1 token 向上
    取整——对当前 Qwen 文本模型整体略偏保守，只用于预算控制，不冒充真实
    分词结果。
    """
    tokens = 0
    other = 0
    for char in text:
        code = ord(char)
        if (
            "\u4e00" <= char <= "\u9fff"
            or "\u3000" <= char <= "\u303f"
            or "\uff00" <= char <= "\uffef"
            or (category(char).startswith("L") and code > 0x2E7F)
        ):
            tokens += 1
        else:
            other += 1
    return tokens + (other + 3) // 4


class MaterialNecessity(IntEnum):
    """载荷块的必要性等级；数值越大越先被裁剪。

    ``REQUIRED`` 永不静默丢弃：当前请求、权限规则、有效硬条件与当前结论的
    必要证据都属于它。``IRRELEVANT`` 是已判定与本任务无关的材料，最先删除。
    """

    REQUIRED = 0
    BACKGROUND = 1
    OPTIONAL = 2
    IRRELEVANT = 3


class MaterialCategory(StrEnum):
    """材料来源类别（仅用于清单分类与审计，不决定裁剪顺序）。"""

    SYSTEM_RULE = "system_rule"
    HISTORY = "history"
    SUMMARY = "summary"
    PROFILE = "profile"
    ATTACHMENT = "attachment"
    RETRIEVAL = "retrieval"
    TOOL = "tool"
    WEB = "web"
    ARXIV = "arxiv"
    TEACHING = "teaching"
    WRITING_POLICY = "writing_policy"
    CORRECTION = "correction"
    MEMORY = "memory"


@dataclass(frozen=True)
class PayloadBlock:
    """一个待注入最终载荷的材料块（带来源与必要性）。

    ``content`` 是渲染进 ``system`` 角色的数据块；``material_id`` 是清单里
    可定位的来源标识（消息 ID／切片 ID／来源 ID 等），不要求全局唯一。
    """

    material_id: str
    category: str
    necessity: MaterialNecessity
    content: str
    source_version: str | None = None
    read_range: str | None = None


@dataclass(frozen=True)
class MaterialManifestEntry:
    """材料清单中的一条采用/排除记录（脱敏，不含正文）。"""

    material_id: str
    category: str
    necessity: str
    adopted: bool
    reason: str
    estimated_tokens: int = 0
    source_version: str | None = None
    read_range: str | None = None


@dataclass(frozen=True)
class PayloadGateDecision:
    """最终载荷预算门的裁决结果。"""

    #: 估算是否落在输入硬上界内。
    within_budget: bool
    #: 输入硬上界（token）；额度不可验证时为 0。
    input_upper_bound: int
    #: 最终载荷的输入估算（token）。
    estimated_input_tokens: int
    #: 本次调用的输出预留（token）。
    output_tokens: int
    #: 发送前安全余量（token）。
    safety_margin_tokens: int
    #: 运行额度快照的已验证窗口与最大输入额度（不可验证时为 None）。
    window_tokens: int | None
    max_input_tokens: int | None
    #: 预算门版本与估算版本。
    budget_version: str
    token_estimate_version: str
    #: 运行额度快照版本与是否已验证。
    quota_version: str | None
    quota_verified: bool
    #: 稳定原因码：``None``（通过）/ ``payload_budget_exceeded`` /
    #: ``payload_budget_unverified``。
    reason: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "within_budget": self.within_budget,
            "input_upper_bound": self.input_upper_bound,
            "estimated_input_tokens": self.estimated_input_tokens,
            "output_tokens": self.output_tokens,
            "safety_margin_tokens": self.safety_margin_tokens,
            "window_tokens": self.window_tokens,
            "max_input_tokens": self.max_input_tokens,
            "budget_version": self.budget_version,
            "token_estimate_version": self.token_estimate_version,
            "quota_version": self.quota_version,
            "quota_verified": self.quota_verified,
            "reason": self.reason,
        }


#: 预算门稳定原因码。
PAYLOAD_REASON_EXCEEDED = "payload_budget_exceeded"
PAYLOAD_REASON_UNVERIFIED = "payload_budget_unverified"


def _part_tokens(part: Any) -> int:
    """单个内容部件的输入成本：图片按常量、文本按估算。"""
    if not isinstance(part, Mapping):
        return estimate_tokens(str(part))
    if part.get("type") == "image_url":
        return IMAGE_PART_COST_TOKENS
    text = part.get("text")
    if isinstance(text, str):
        return estimate_tokens(text)
    return estimate_tokens(str(part.get("content") or ""))


def estimate_message_tokens(message: Mapping[str, Any]) -> int:
    """估算一条模型消息的输入成本（支持纯文本与多模态内容部件）。"""
    content = message.get("content")
    if isinstance(content, str):
        return estimate_tokens(content)
    if isinstance(content, Sequence):
        return sum(_part_tokens(part) for part in content)
    return estimate_tokens(str(content or ""))


def estimate_messages_tokens(messages: Sequence[Mapping[str, Any]]) -> int:
    """估算一组消息的输入成本（最终载荷按实际封装计数）。"""
    return sum(estimate_message_tokens(message) for message in messages)


def estimate_payload_tokens(payload: Mapping[str, Any]) -> int:
    """估算一次最终模型载荷的输入成本（只计输入侧，不计输出预留）。"""
    messages = payload.get("messages")
    if not isinstance(messages, Sequence):
        return 0
    return estimate_messages_tokens(messages)


def payload_input_upper_bound(
    quota: RunModelQuota | None,
    *,
    output_tokens: int,
    safety_margin: int = PAYLOAD_SAFETY_MARGIN_TOKENS,
) -> int | None:
    """最终载荷的输入硬上界（token）。

    ``min(已验证最大输入额度, 已验证上下文窗口 − 本次输出预留 − 安全余量)``
    （工单 04 规格公式：输出预留只从窗口项扣除，不从最大输入额度再扣一次）。
    额度不可验证时返回 ``None``——调用方必须闭锁，绝不用缺省窗口冒充。
    """
    if quota is None or not quota.is_verified:
        return None
    assert quota.context_window is not None
    window_bound = quota.context_window - output_tokens - safety_margin
    if quota.max_input_tokens is None:
        return max(0, window_bound)
    return max(0, min(quota.max_input_tokens, window_bound))


def evaluate_payload_gate(
    payload: Mapping[str, Any],
    *,
    quota: RunModelQuota | None,
    output_tokens: int,
    safety_margin: int = PAYLOAD_SAFETY_MARGIN_TOKENS,
) -> PayloadGateDecision:
    """对**最终**载荷执行预算门（共同调用边界的唯一裁决）。"""
    estimate = estimate_payload_tokens(payload)
    upper = payload_input_upper_bound(
        quota, output_tokens=output_tokens, safety_margin=safety_margin
    )
    verified = quota is not None and quota.is_verified
    if upper is None:
        within = False
        reason: str | None = PAYLOAD_REASON_UNVERIFIED
    elif estimate <= upper:
        within = True
        reason = None
    else:
        within = False
        reason = PAYLOAD_REASON_EXCEEDED
    return PayloadGateDecision(
        within_budget=within,
        input_upper_bound=upper if upper is not None else 0,
        estimated_input_tokens=estimate,
        output_tokens=output_tokens,
        safety_margin_tokens=safety_margin,
        window_tokens=(quota.context_window if quota is not None else None),
        max_input_tokens=(quota.max_input_tokens if quota is not None else None),
        budget_version=PAYLOAD_BUDGET_VERSION,
        token_estimate_version=TOKEN_ESTIMATE_VERSION,
        quota_version=(quota.quota_version if quota is not None else None),
        quota_verified=verified,
        reason=reason,
    )


def select_blocks_within_budget(
    blocks: Sequence[PayloadBlock],
    *,
    budget_tokens: int,
) -> tuple[list[PayloadBlock], list[MaterialManifestEntry]]:
    """按必要性裁剪材料块，使总估算不超过 ``budget_tokens``。

    裁剪顺序：先无关（``IRRELEVANT``）、再可选（``OPTIONAL``）、再背景
    （``BACKGROUND``）；``REQUIRED`` 绝不静默丢弃。返回 (采用块, 清单条目)：
    清单对每个块给出采用/排除与原因，估算为各块自身成本。
    """
    costs = [estimate_tokens(block.content) for block in blocks]
    # 排除优先级：必要性数值越大越先被剔除；同必要性保持输入顺序稳定。
    drop_order = sorted(
        range(len(blocks)),
        key=lambda index: (-int(blocks[index].necessity), index),
    )
    dropped: set[int] = set()
    total = sum(costs)
    for index in drop_order:
        if total <= budget_tokens:
            break
        block = blocks[index]
        if block.necessity is MaterialNecessity.REQUIRED:
            continue
        dropped.add(index)
        total -= costs[index]

    adopted: list[PayloadBlock] = []
    entries: list[MaterialManifestEntry] = []
    for index, block in enumerate(blocks):
        is_adopted = index not in dropped
        if is_adopted:
            adopted.append(block)
        entries.append(
            MaterialManifestEntry(
                material_id=block.material_id,
                category=block.category,
                necessity=block.necessity.name.lower(),
                adopted=is_adopted,
                reason=(
                    "在最终预算内采用"
                    if is_adopted
                    else "最终载荷超出预算，按必要性裁剪"
                ),
                estimated_tokens=costs[index],
                source_version=block.source_version,
                read_range=block.read_range,
            )
        )
    return adopted, entries


@dataclass(frozen=True)
class CallMaterialManifest:
    """一次实际调用的脱敏材料清单（采用/排除、预算门与用量）。"""

    entries: list[MaterialManifestEntry]
    gate: PayloadGateDecision
    output_tokens: int
    estimated_input_tokens: int
    summary_instance: str | None = None
    actual_input_tokens: int | None = None
    actual_output_tokens: int | None = None
    manifest_version: str = MATERIAL_MANIFEST_VERSION

    @property
    def adopted_ids(self) -> list[str]:
        return [entry.material_id for entry in self.entries if entry.adopted]

    @property
    def excluded_ids(self) -> list[str]:
        return [entry.material_id for entry in self.entries if not entry.adopted]

    def to_record(self) -> dict[str, Any]:
        """审计记录（只含 ID、类别、必要性、版本与计数，绝不含正文）。"""
        return {
            "manifest_version": self.manifest_version,
            "gate": self.gate.to_record(),
            "output_tokens": self.output_tokens,
            "estimated_input_tokens": self.estimated_input_tokens,
            "summary_instance": self.summary_instance,
            "actual_input_tokens": self.actual_input_tokens,
            "actual_output_tokens": self.actual_output_tokens,
            "adopted_ids": self.adopted_ids,
            "excluded_ids": self.excluded_ids,
            "entries": [
                {
                    "material_id": entry.material_id,
                    "category": entry.category,
                    "necessity": entry.necessity,
                    "adopted": entry.adopted,
                    "reason": entry.reason,
                    "estimated_tokens": entry.estimated_tokens,
                    "source_version": entry.source_version,
                    "read_range": entry.read_range,
                }
                for entry in self.entries
            ],
            "redacted_fields": list(MANIFEST_FORBIDDEN_FIELDS),
        }


def evaluate_call_manifest(
    payload: Mapping[str, Any],
    *,
    quota: RunModelQuota | None,
    output_tokens: int,
    entries: Sequence[MaterialManifestEntry],
) -> CallMaterialManifest | None:
    """对子模块**最终**载荷执行预算门并装配脱敏清单；无额度快照时返回 None。

    工单 15：论文概述、GitHub 借鉴角度等工具结果后的模型调用共用本入口，
    不各自复制预算门与清单装配（``.scratch/2/README.md`` 接缝表：
    「消费者不复制预算器，门后不得追加材料」）。调用方按门结果决定是否
    发起调用；超限时只交付真实证据并如实说明。
    """
    if quota is None:
        return None
    decision = evaluate_payload_gate(payload, quota=quota, output_tokens=output_tokens)
    return CallMaterialManifest(
        entries=list(entries),
        gate=decision,
        output_tokens=output_tokens,
        estimated_input_tokens=decision.estimated_input_tokens,
    )


def redaction_audit() -> dict[str, Any]:
    """脱敏审计声明：材料清单合同保证不携带的敏感字段与保证内容。"""
    return {
        "manifest_version": MATERIAL_MANIFEST_VERSION,
        "budget_version": PAYLOAD_BUDGET_VERSION,
        "excluded_fields": list(MANIFEST_FORBIDDEN_FIELDS),
        "contains_credentials": False,
        "contains_prompt_body": False,
        "contains_message_body": False,
        "notes": (
            "材料清单只记录材料 ID、类别、必要性、来源版本、读取范围、采用/"
            "排除原因、估算与可取得的实际用量；消息正文、提示词、图片内容与"
            "凭据不进入该清单。"
        ),
    }


__all__ = [
    "DEFAULT_PAYLOAD_OUTPUT_TOKENS",
    "IMAGE_PART_COST_TOKENS",
    "MANIFEST_FORBIDDEN_FIELDS",
    "MATERIAL_MANIFEST_VERSION",
    "PAYLOAD_BUDGET_VERSION",
    "PAYLOAD_REASON_EXCEEDED",
    "PAYLOAD_REASON_UNVERIFIED",
    "PAYLOAD_SAFETY_MARGIN_TOKENS",
    "TOKEN_ESTIMATE_VERSION",
    "CallMaterialManifest",
    "MaterialCategory",
    "MaterialManifestEntry",
    "MaterialNecessity",
    "PayloadBlock",
    "PayloadGateDecision",
    "estimate_message_tokens",
    "estimate_messages_tokens",
    "estimate_payload_tokens",
    "estimate_tokens",
    "evaluate_payload_gate",
    "payload_input_upper_bound",
    "redaction_audit",
    "select_blocks_within_budget",
]
