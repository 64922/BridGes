"""会话上下文编译器（V2 Issue 03：长对话上下文）。

原始消息是长期权威源，摘要是派生缓存（``docs/v2/architecture.md`` §4）。
本模块把一轮模型调用的输入编译为「当前请求 + 近期原文 + 较早摘要 + 按需
补回的原文」，全部材料带来源消息 ID，且在锁定模型的已验证窗口内：

1. 从锁定模型的已验证 ``context_window`` 取上界，预留输出与工具调用成本；
   系统规则文本与当前回合图片按实际估算计入输入预算。
2. 固定纳入模式合同（系统规则）、当前用户原文与近期消息原文；较早片段以
   带消息 ID 和范围的摘要覆盖。
3. 用户请求引用较早实体、约定、结果列表或当前任务时，交给
   :mod:`bridges.chat.reference_resolution` 按定位顺序解析（明确任务/产物
   指代 → 当前任务 → 近期前文 → 同会话原文检索），补回原文并保留原值→
   纠正→撤销关系；找不到时如实说明缺口，不编造、不说用户从未讲过。
4. 预算不足时先收缩较旧原文（移入摘要），再加重摘要压缩；当前请求与
   补回的关键证据绝不裁掉（宁可记录预算触底也不丢证据）。

编译是纯函数：只读传入的消息记录，绝不改写或另存消息。跨票接缝：后续
工单（05/07/08 与模块票）把附件、画像切片、知识库片段与经核验的工具结果
以带来源的材料接入本编译器，不得另造互不兼容的拼装链。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from bridges.ai.fixed_models import MODEL_CONTEXT_WINDOWS
from bridges.ai.model_quota import RunModelQuota

# 改进工单 04：图片成本与最终载荷门共用同一常量（IMAGE_COST_TOKENS 是
# payload_budget.IMAGE_PART_COST_TOKENS 的别名），估算函数也只有一个事实源。
from bridges.ai.payload_budget import (
    IMAGE_PART_COST_TOKENS as IMAGE_COST_TOKENS,
)
from bridges.ai.payload_budget import (
    TOKEN_ESTIMATE_VERSION as TOKEN_ESTIMATE_VERSION,
)
from bridges.ai.payload_budget import (
    estimate_tokens as estimate_tokens,
)
from bridges.chat.reference_resolution import (
    RECOVERED_MAX_MESSAGES,
    resolve_references,
)
from bridges.chat.turn import (
    CHAT_OUTPUT_TOKENS,
    history_items,
    mode_system_contract,
)
from bridges.contracts.chat import ChatMessageRole, ChatMode
from bridges.contracts.references import (
    REFERENCE_CONTRACT_VERSION,
    AnchorKind,
    ReferenceResolution,
    ReferenceStatus,
    ReferenceTaskContext,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bridges.chat.repository import MessageRecord

#: 预算版本（预留常量或预算公式变化时递增；编译记录携带，便于审计复算）。
CONTEXT_BUDGET_VERSION = "ctx-budget-v1"
#: 摘要版本（摘要格式或压缩规则变化时递增）。
SUMMARY_VERSION = "summary-v1"

#: 输出预留缺省值：与主对话调用的输出额度 :data:`CHAT_OUTPUT_TOKENS` 同源
#: （改进工单 03）——单一事实源，避免两处 ``1024`` 漂移。调用方可传
#: ``output_tokens`` 覆盖为自身额度；缺省即聊天调用额度。
OUTPUT_RESERVE_TOKENS = CHAT_OUTPUT_TOKENS
OUTPUT_RESERVE_MARGIN_TOKENS = 256
#: 工具调用预留：本轮工具调用与工具结果回传的输入成本预留（模块票接入
#: 真实工具后在各自合同内细化，本值为日常普通聊天的保守下限）。
TOOL_RESERVE_TOKENS = 512
#: 未知模型的保守缺省窗口。它**不是**已验证额度：仅在纯函数直接调用且调用方
#: 未提供额度快照时作为裁剪上界，编译记录以 ``window_verified=False`` 如实
#: 标注；生产路径由运行额度快照（``resolve_run_quota``）提供已验证窗口。
DEFAULT_CONTEXT_WINDOW = 32768
#: 已验证上下文窗口登记表：出厂批准模型（``fixed_models`` 单一事实源）。
#: 不在表内的模型没有已验证窗口，不得用缺省值冒充。
VERIFIED_CONTEXT_WINDOWS: dict[str, int] = MODEL_CONTEXT_WINDOWS
#: 近期原文最多可占可用材料预算的既定份额（其余留给摘要与补回原文）。
RECENT_VERBATIM_SHARE = 0.6
#: 初次摘要单条最大字符数。
SUMMARY_ENTRY_MAX_CHARS = 160
#: 预算触底后重做摘要的单条最大字符数。
SUMMARY_ENTRY_HARD_MAX_CHARS = 60


def verified_context_window(model_id: str | None) -> int:
    """返回锁定模型的已验证上下文窗口（未知模型用保守缺省值）。

    注意：缺省值只是裁剪上界，**不是**已验证额度；需要区分时用
    :func:`is_verified_context_window`。生产路径应优先使用运行额度快照。
    """
    if model_id is None:
        return DEFAULT_CONTEXT_WINDOW
    return VERIFIED_CONTEXT_WINDOWS.get(model_id, DEFAULT_CONTEXT_WINDOW)


def is_verified_context_window(model_id: str | None) -> bool:
    """该模型是否登记有已验证上下文窗口（未知模型为 False）。"""
    return model_id is not None and model_id in VERIFIED_CONTEXT_WINDOWS


@dataclass(frozen=True)
class ContextReserves:
    """本轮输入预算的预留构成（token）。"""

    #: 输出预留（含安全余量）。
    output_tokens: int
    #: 工具调用预留。
    tool_tokens: int
    #: 当前回合图片成本估算。
    image_tokens: int
    #: 系统规则（模式合同等固定系统块）实际估算。
    system_tokens: int


@dataclass(frozen=True)
class ContextEvidence:
    """按相关性降序传入的带来源材料；首项是本轮必要证据。"""

    evidence_id: str
    content: str


@dataclass(frozen=True)
class CompiledTurnContext:
    """一次上下文编译的产物与审计记录字段。"""

    #: 模型就绪消息列表（首条为模式合同；后续票的附件/画像/KB/工具材料
    #: 也编译进此列表，不另造拼装链）。
    messages: list[dict[str, str]]
    model_id: str | None
    context_window: int
    input_budget_tokens: int
    reserves: ContextReserves
    input_token_estimate: int
    budget_version: str
    summary_version: str
    token_estimate_version: str
    #: 本次调用实际使用的输出额度（token，改进工单 03）：不同输出额度的调用
    #: 预留不同空间，不再统一假定 1,024。
    output_quota_tokens: int
    #: 运行额度快照合同版本（无快照时 None）。
    quota_version: str | None
    #: 运行额度快照验证依据（无快照时 None）。
    quota_verification_basis: str | None
    #: 是否持有已验证的运行额度快照。
    quota_verified: bool
    #: ``context_window`` 是否来自已验证来源（额度快照或登记表）而非缺省值。
    window_verified: bool
    #: 本轮实际采用原文的消息 ID（近期原文 + 补回原文 + 当前请求，按
    #: 会话时间序）。
    adopted_message_ids: list[str]
    #: 摘要覆盖的消息 ID 范围（最早, 最晚）；无较早消息时为 None。
    summary_source_range: tuple[str, str] | None
    #: 本轮补回原文的消息 ID。
    recovered_message_ids: list[str] = field(default_factory=list)
    #: 用户引用较早内容但未能完整定位（含部分命中；真缺口见
    #: ``reference_missing_count`` 与编译消息中的缺口规则）。
    unresolved_reference: bool = False
    #: 裁剪已到下限仍超预算（当前请求与关键证据保留，如实记录触底）。
    budget_floor_exceeded: bool = False
    adopted_evidence_ids: list[str] = field(default_factory=list)
    #: 指代解析合同版本（``reference-v1`` 等；审计复算用）。
    reference_contract_version: str = REFERENCE_CONTRACT_VERSION
    #: 指代解析状态（``reference-v1`` 合同的 ReferenceStatus 值）。
    reference_status: str = ReferenceStatus.NONE.value
    #: 指代存在实质歧义：本轮应先问一个必要澄清问题。
    ambiguous_reference: bool = False
    #: 采用的可追溯锚点 ID（任务/条件/消息/列表/列表项）。
    reference_anchor_ids: list[str] = field(default_factory=list)
    #: 采用的结果对象 ID（论文/仓库等）。
    reference_adopted_object_ids: list[str] = field(default_factory=list)
    #: 未采用锚点的 ID 与原因（不含完整私人正文）。
    reference_rejected: list[dict[str, str]] = field(default_factory=list)
    #: 未能定位的引用内容条数（短标签不进审计，只记计数）。
    reference_missing_count: int = 0

    def model_messages(self) -> list[dict[str, str]]:
        """返回模型就绪消息列表的独立副本（图状态/载荷安全复用）。"""
        return [dict(message) for message in self.messages]

    def to_record(self) -> dict[str, object]:
        """审计记录（只含 ID、版本与计数，不含任何消息正文）。"""
        return {
            "model_id": self.model_id,
            "context_window": self.context_window,
            "input_budget_tokens": self.input_budget_tokens,
            "input_token_estimate": self.input_token_estimate,
            "budget_version": self.budget_version,
            "summary_version": self.summary_version,
            "token_estimate_version": self.token_estimate_version,
            "output_quota_tokens": self.output_quota_tokens,
            "quota_version": self.quota_version,
            "quota_verification_basis": self.quota_verification_basis,
            "quota_verified": self.quota_verified,
            "window_verified": self.window_verified,
            "reserves": {
                "output_tokens": self.reserves.output_tokens,
                "tool_tokens": self.reserves.tool_tokens,
                "image_tokens": self.reserves.image_tokens,
                "system_tokens": self.reserves.system_tokens,
            },
            "adopted_message_ids": list(self.adopted_message_ids),
            "summary_source_range": (
                list(self.summary_source_range)
                if self.summary_source_range is not None
                else None
            ),
            "recovered_message_ids": list(self.recovered_message_ids),
            "unresolved_reference": self.unresolved_reference,
            "budget_floor_exceeded": self.budget_floor_exceeded,
            "adopted_evidence_ids": list(self.adopted_evidence_ids),
            "reference_contract_version": self.reference_contract_version,
            "reference_status": self.reference_status,
            "reference_ambiguous": self.ambiguous_reference,
            "reference_anchor_ids": list(self.reference_anchor_ids),
            "reference_adopted_object_ids": list(self.reference_adopted_object_ids),
            "reference_rejected": [dict(item) for item in self.reference_rejected],
            "reference_missing_count": self.reference_missing_count,
        }


@dataclass(frozen=True)
class _TurnItem:
    """编译内部的会话历史项（与 :class:`turn.HistoryItem` 同形）。"""

    message_id: str
    role: ChatMessageRole
    content: str


def _pair_history_items(
    messages: Sequence[MessageRecord], current_user_message_id: str
) -> list[_TurnItem]:
    """取共享配对结果并断言当前用户消息在列（编译需要当前请求在末尾）。

    配对语义（用户原文总是纳入、助手只取最近一条已完成、按当前轮次
    截断）由 :func:`turn.history_items` 统一实现，避免两处漂移。
    """
    items = [
        _TurnItem(item.message_id, item.role, item.content)
        for item in history_items(
            messages, until_user_message_id=current_user_message_id
        )
    ]
    if not items or items[-1].message_id != current_user_message_id:
        raise ValueError(
            f"当前用户消息不在会话历史中：{current_user_message_id}"
        )
    return items


def _compact(text: str) -> str:
    """摘要用空白折叠。"""
    return " ".join(text.split())


def _truncate_for_summary(text: str, max_chars: int) -> str:
    compact = _compact(text)
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 1] + "…"


def _summary_block(
    older_items: Sequence[_TurnItem], *, entry_max_chars: int
) -> tuple[str | None, tuple[str, str] | None]:
    """较早消息的摘要系统块；返回 (块文本, 来源消息 ID 范围)。"""
    if not older_items:
        return None, None
    lines = [
        f"- [{item.message_id}] "
        f"{'用户' if item.role == ChatMessageRole.USER else '助手'}："
        f"{_truncate_for_summary(item.content, entry_max_chars)}"
        for item in older_items
    ]
    block = (
        f"以下是对较早对话的摘要（摘要版本 {SUMMARY_VERSION}，"
        f"来源消息 ID：{older_items[0].message_id} 至 "
        f"{older_items[-1].message_id}）；"
        "摘要只是线索，回答涉及这些内容时以带 ID 的原始消息原文为准。\n"
        + "\n".join(lines)
    )
    return block, (older_items[0].message_id, older_items[-1].message_id)


def _recovered_block(
    recovered: Sequence[_TurnItem], corrections: dict[str, str]
) -> str:
    """补回原文的系统块（完整原文 + 来源消息 ID + 纠正关系说明）。"""
    lines = [
        f"- [{item.message_id}] "
        f"{'用户' if item.role == ChatMessageRole.USER else '助手'}：{item.content}"
        + (
            f"（说明：{corrections[item.message_id]}）"
            if item.message_id in corrections
            else ""
        )
        for item in recovered
    ]
    return (
        "用户请求涉及较早对话中的实体、约定或结果对象，以下为从本会话原始"
        "消息中找回的相关原文（消息 ID；标注纠正关系的以较新来源为准，"
        "已被纠正或撤销的值不得作为当前条件）：\n" + "\n".join(lines)
    )


_UNCERTAINTY_RULE = (
    "用户请求似乎引用了较早的对话内容，但本轮提供的材料中没有找到对应的"
    "原始消息：如无法仅凭已提供材料回答，请明确说明未能在会话历史中找到"
    "该内容，并向用户询问；绝不能编造或臆测早先的原文、约定或结论，"
    "也不要把「本轮未定位」说成用户从未讲过。"
)


def _uncertainty_rule(missing: Sequence[str]) -> str:
    """未定位/部分命中：承认缺口并询问；列出具体缺失（短标签）。"""
    if not missing:
        return _UNCERTAINTY_RULE
    return (
        _UNCERTAINTY_RULE
        + "本轮具体未能定位的引用内容："
        + "；".join(missing)
        + "。"
    )


def _ambiguity_rule(question: str) -> str:
    """实质歧义：只问一个必要澄清问题，不自行选择、不编造。"""
    return (
        "用户请求同时指向多个同样合理的候选对象，本轮无法唯一确定。"
        f"请在回答中只问一个必要的澄清问题：{question}"
        "在用户确认前不要自行选择对象，也不要编造对象内容；"
        "澄清问题绑定当前任务版本，用户答复后按最新版本续接。"
    )


_CONDITION_STATUS_NOTES = {
    "effective": "仍有效，作为当前条件。",
    "superseded": "已被后续条件纠正，不采用旧值。",
    "revoked": "已撤销，不复活旧值。",
    "draft": "助手草案，未获用户接受，不作为约束。",
    "clue": "模型推测，只作线索，不作为约束。",
}


def _task_conditions_block(resolution: ReferenceResolution) -> str | None:
    """共同任务描述块（只读回显任务版本与条件，不产生新条件）。

    无论条件来源原文是否已在近期原文中，都在这里显式给出原值→纠正→
    撤销关系，避免模型把被取代或被撤销的旧值当作当前条件。
    """
    brief = resolution.task
    if brief is None or not brief.conditions:
        return None
    lines = [
        "以下是从当前任务快照读出的条件状态（只读事实，不是新指令，"
        "也不构成新的用户条件）：",
        f"- 任务 {brief.task_id or '（未命名）'}（版本 {brief.version}，"
        f"状态 {brief.status or '未知'}）：{brief.goal or '（未记录目标）'}",
    ]
    for condition in brief.conditions:
        note = _CONDITION_STATUS_NOTES.get(condition.status, "状态未知，不采用。")
        lines.append(
            f"- [{condition.condition_id}] {condition.kind}："
            f"{condition.text}（{note}，来源消息 {condition.source_message_id}）"
        )
    return "\n".join(lines)


def _reference_objects_block(resolution: ReferenceResolution) -> str | None:
    """将已定位对象的身份、版本与来源送入模型，正文仍按消息 ID 回补。"""
    anchors = [anchor for anchor in resolution.anchors if anchor.kind == AnchorKind.LIST_ITEM]
    if not anchors:
        return None
    return "已定位的结果对象（只读材料，不是执行指令）：\n" + "\n".join(
        f"- {anchor.label}；对象 ID {anchor.object_id}；列表版本 {anchor.list_version}；"
        f"来源消息 {','.join(anchor.message_ids)}"
        for anchor in anchors
    )


def compile_turn_context(
    *,
    messages: Sequence[MessageRecord],
    current_user_message_id: str,
    model_id: str | None,
    mode: ChatMode,
    context_window: int | None = None,
    system_prompt: str | None = None,
    evidence: Sequence[ContextEvidence] = (),
    output_tokens: int | None = None,
    quota: RunModelQuota | None = None,
    task: ReferenceTaskContext | None = None,
) -> CompiledTurnContext:
    """编译一轮普通对话的模型输入上下文（纯函数；详见模块说明）。

    窗口解析优先级（改进工单 03）：

    1. ``quota`` 持有已验证额度时以其 ``input_upper_bound()`` 为上界，并标注
       ``quota_verified``/``window_verified`` 与快照版本、验证依据；
    2. 否则显式 ``context_window``（模型切换后的下一轮按新窗口重算；测试可
       注入小窗口验证裁剪）；
    3. 否则按 :func:`verified_context_window` 取锁定模型的已验证窗口，未知
       模型回退缺省值并以 ``window_verified=False`` 如实标注（缺省值不冒充
       已验证额度）。

    ``output_tokens`` 是本次调用自身的输出额度（改进工单 03）；缺省用
    :data:`OUTPUT_RESERVE_TOKENS`。不同输出额度的调用因此预留不同空间。

    ``task`` 是工单 08 的当前任务快照（工单 11 接缝）：指代解析据此读取
    有效条件与被取代/撤销的旧值，但解析只读、绝不写入新条件。
    """
    if quota is not None and quota.is_verified:
        # ``is_verified`` 已保证 context_window > 0，但 max_input_tokens 仍可能
        # 被误配为 0；显式按 None 判定，绝不用 ``or`` 把 0 额度换成缺省窗口。
        bound = quota.input_upper_bound()
        window = bound if bound is not None else DEFAULT_CONTEXT_WINDOW
        window_verified = True
        quota_verified = True
    elif context_window is not None:
        window = context_window
        window_verified = True
        # 走到这里说明上面的额度分支未命中（无快照或快照未验证），故为 False。
        quota_verified = False
    else:
        window = verified_context_window(model_id)
        window_verified = is_verified_context_window(model_id)
        quota_verified = False
    output_quota = (
        output_tokens if output_tokens is not None else OUTPUT_RESERVE_TOKENS
    )
    items = _pair_history_items(messages, current_user_message_id)
    current = items[-1]
    current_record = next(
        (
            message
            for message in messages
            if message.message_id == current_user_message_id
        ),
        None,
    )
    image_tokens = (
        IMAGE_COST_TOKENS
        if current_record is not None and current_record.image is not None
        else 0
    )

    output_reserve = output_quota + OUTPUT_RESERVE_MARGIN_TOKENS
    input_budget = max(0, window - output_reserve - TOOL_RESERVE_TOKENS)

    # 近期原文窗口：从最新往回贪心纳入，占用不超过可用材料预算的既定份额；
    # 当前请求无论如何都保留。较早片段进摘要。
    contract = mode_system_contract(mode)
    prompt = system_prompt if system_prompt is not None else contract.system_prompt
    selected_evidence = list(evidence)
    system_tokens = estimate_tokens(prompt)
    materials_budget = max(0, input_budget - system_tokens - image_tokens)
    recent_budget = int(materials_budget * RECENT_VERBATIM_SHARE)
    recent: list[_TurnItem] = [current]
    recent_cost = estimate_tokens(current.content)
    for item in reversed(items[:-1]):
        cost = estimate_tokens(item.content)
        if recent_cost + cost > recent_budget:
            break
        recent.append(item)
        recent_cost += cost
    recent.reverse()
    recent_ids = {item.message_id for item in recent}
    older: list[_TurnItem] = [
        item for item in items if item.message_id not in recent_ids
    ]

    # 裁剪顺序（预算不足时）：先缩较旧原文（移入摘要），再重做摘要；
    # 当前请求、系统规则与补回的关键证据不裁，触底如实记录。补回在每轮
    # 裁剪后重算——预算收缩把近期原文降级进摘要时，被引用的原文要能从
    # 摘要区补回，不丢关键证据。
    entry_max_chars = SUMMARY_ENTRY_MAX_CHARS
    floor_exceeded = False
    resolution = ReferenceResolution()
    recovered: list[_TurnItem] = []
    while True:
        resolution = resolve_references(
            request=current.content,
            messages=messages,
            current_user_message_id=current_user_message_id,
            recent_message_ids=[item.message_id for item in recent],
            task=task,
        )
        items_by_id = {item.message_id: item for item in items}
        recovered = [
            items_by_id[message_id]
            for message_id in resolution.recovered_message_ids
            if message_id in items_by_id
        ][:RECOVERED_MAX_MESSAGES]
        unresolved = resolution.status in {
            ReferenceStatus.UNRESOLVED,
            ReferenceStatus.PARTIAL,
        }
        ambiguous = resolution.status == ReferenceStatus.AMBIGUOUS
        summary_text, summary_range = _summary_block(
            older, entry_max_chars=entry_max_chars
        )
        blocks: list[str] = [prompt, *(item.content for item in selected_evidence)]
        if summary_text is not None:
            blocks.append(summary_text)
        if recovered:
            blocks.append(
                _recovered_block(recovered, resolution.correction_notes)
            )
        if resolution.status != ReferenceStatus.NONE:
            object_block = _reference_objects_block(resolution)
            if object_block is not None:
                blocks.append(object_block)
            task_block = _task_conditions_block(resolution)
            if task_block is not None:
                blocks.append(task_block)
        if ambiguous and resolution.clarification is not None:
            blocks.append(_ambiguity_rule(resolution.clarification.question))
        elif unresolved:
            blocks.append(_uncertainty_rule(resolution.missing_requirements))
        input_estimate = sum(estimate_tokens(block) for block in blocks)
        input_estimate += sum(estimate_tokens(item.content) for item in recent)
        input_estimate += image_tokens
        if input_estimate <= input_budget:
            break
        if len(selected_evidence) > 1:
            selected_evidence.pop()
            continue
        if len(recent) > 1:
            # 最旧的近期原文降级进摘要（追加到较早序列末尾，保持时间序）。
            demoted = recent.pop(0)
            older.append(demoted)
        elif entry_max_chars > SUMMARY_ENTRY_HARD_MAX_CHARS:
            entry_max_chars = SUMMARY_ENTRY_HARD_MAX_CHARS
        else:
            floor_exceeded = True
            break

    model_messages: list[dict[str, str]] = [
        {"role": "system", "content": blocks[0]}
    ]
    model_messages.extend(
        {"role": "system", "content": block} for block in blocks[1:]
    )
    model_messages.extend(
        {
            "role": "user" if item.role == ChatMessageRole.USER else "assistant",
            "content": item.content,
        }
        for item in recent
    )

    # 实际采用原文的消息 ID（近期 + 补回去重后按会话时间序）。
    adopted: list[str] = []
    for item in sorted([*recent, *recovered], key=items.index):
        if item.message_id not in adopted:
            adopted.append(item.message_id)
    return CompiledTurnContext(
        messages=model_messages,
        model_id=model_id,
        context_window=window,
        input_budget_tokens=input_budget,
        reserves=ContextReserves(
            output_tokens=output_reserve,
            tool_tokens=TOOL_RESERVE_TOKENS,
            image_tokens=image_tokens,
            system_tokens=system_tokens,
        ),
        input_token_estimate=input_estimate,
        budget_version=CONTEXT_BUDGET_VERSION,
        summary_version=SUMMARY_VERSION,
        token_estimate_version=TOKEN_ESTIMATE_VERSION,
        output_quota_tokens=output_quota,
        quota_version=(quota.quota_version if quota is not None else None),
        quota_verification_basis=(
            quota.verification_basis.value if quota is not None else None
        ),
        quota_verified=quota_verified,
        window_verified=window_verified,
        adopted_message_ids=adopted,
        summary_source_range=summary_range,
        recovered_message_ids=[item.message_id for item in recovered],
        unresolved_reference=unresolved,
        budget_floor_exceeded=floor_exceeded,
        adopted_evidence_ids=[item.evidence_id for item in selected_evidence],
        reference_contract_version=resolution.contract_version,
        reference_status=resolution.status.value,
        ambiguous_reference=ambiguous,
        reference_anchor_ids=resolution.adopted_anchor_ids(),
        reference_adopted_object_ids=list(resolution.adopted_object_ids),
        reference_rejected=resolution.rejected_reasons(),
        reference_missing_count=len(resolution.missing_requirements),
    )
