"""会话上下文编译器（V2 Issue 03：长对话上下文）。

原始消息是长期权威源，摘要是派生缓存（``docs/v2/architecture.md`` §4）。
本模块把一轮模型调用的输入编译为「当前请求 + 近期原文 + 较早摘要 + 按需
补回的原文」，全部材料带来源消息 ID，且在锁定模型的已验证窗口内：

1. 从锁定模型的已验证 ``context_window`` 取上界，预留输出与工具调用成本；
   系统规则文本与当前回合图片按实际估算计入输入预算。
2. 固定纳入模式合同（系统规则）、当前用户原文与近期消息原文；较早片段以
   带消息 ID 和范围的摘要覆盖。
3. 用户请求引用较早实体或约定（引号原文或回指词）时，按同一会话的原始
   消息补回相关原文；找不到时附上「承认不确定并询问」的规则，不编造。
4. 预算不足时先收缩较旧原文（移入摘要），再加重摘要压缩；当前请求与
   补回的关键证据绝不裁掉（宁可记录预算触底也不丢证据）。

编译是纯函数：只读传入的消息记录，绝不改写或另存消息。跨票接缝：后续
工单（05/07/08 与模块票）把附件、画像切片、知识库片段与经核验的工具结果
以带来源的材料接入本编译器，不得另造互不兼容的拼装链。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from unicodedata import category

from bridges.ai.fixed_models import MODEL_CONTEXT_WINDOWS
from bridges.chat.turn import history_items, mode_system_contract
from bridges.contracts.chat import ChatMessageRole, ChatMode

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bridges.chat.repository import MessageRecord

#: 预算版本（预留常量或预算公式变化时递增；编译记录携带，便于审计复算）。
CONTEXT_BUDGET_VERSION = "ctx-budget-v1"
#: 摘要版本（摘要格式或压缩规则变化时递增）。
SUMMARY_VERSION = "summary-v1"
#: token 估算版本（估算规则变化时递增）。
TOKEN_ESTIMATE_VERSION = "token-estimate-v1"

#: 输出预留：模型载荷 ``max_tokens``（1024）+ 安全余量。
OUTPUT_RESERVE_TOKENS = 1024
OUTPUT_RESERVE_MARGIN_TOKENS = 256
#: 工具调用预留：本轮工具调用与工具结果回传的输入成本预留（模块票接入
#: 真实工具后在各自合同内细化，本值为日常普通聊天的保守下限）。
TOOL_RESERVE_TOKENS = 512
#: 每张当前回合图片的输入成本估算（文本预算口径下的保守常量）。
IMAGE_COST_TOKENS = 1024
#: 未知模型的保守缺省窗口（已验证模型以 ``fixed_models.MODEL_CONTEXT_WINDOWS``
#: 为准——受控模型资产单一事实源）。
DEFAULT_CONTEXT_WINDOW = 32768
#: 近期原文最多可占可用材料预算的既定份额（其余留给摘要与补回原文）。
RECENT_VERBATIM_SHARE = 0.6
#: 初次摘要单条最大字符数。
SUMMARY_ENTRY_MAX_CHARS = 160
#: 预算触底后重做摘要的单条最大字符数。
SUMMARY_ENTRY_HARD_MAX_CHARS = 60
#: 单轮最多补回的原始消息数。
RECOVERED_MAX_MESSAGES = 4
#: 回指词（用户请求中出现即视为引用较早内容）。
_BACK_REFERENCE_MARKERS = (
    "之前",
    "先前",
    "早先",
    "上次",
    "刚才",
    "前面",
    "开头",
    "说好的",
    "约定的",
    "记得",
    "答应",
)
#: 引号原文（用户显式引用的实体或约定；含直角、双角、弯引号与直引号）。
_QUOTED_SPAN_RE = re.compile(r"[「『“\"]([^「」『』”\"]{1,64})[」』”\"]")
#: CJK 连续串（≥2 字）与拉丁/数字词（≥3 字符）。
_TOKEN_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]{3,}")
#: 标记路径候选词在较早消息中的最大出现占比（超过视为太常见，不作为线索）。
_MARKER_CANDIDATE_MAX_DOC_FREQUENCY = 0.4


def verified_context_window(model_id: str | None) -> int:
    """返回锁定模型的已验证上下文窗口（未知模型用保守缺省值）。"""
    if model_id is None:
        return DEFAULT_CONTEXT_WINDOW
    return MODEL_CONTEXT_WINDOWS.get(model_id, DEFAULT_CONTEXT_WINDOW)


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
    #: 本轮实际采用原文的消息 ID（近期原文 + 补回原文 + 当前请求，按
    #: 会话时间序）。
    adopted_message_ids: list[str]
    #: 摘要覆盖的消息 ID 范围（最早, 最晚）；无较早消息时为 None。
    summary_source_range: tuple[str, str] | None
    #: 本轮补回原文的消息 ID。
    recovered_message_ids: list[str] = field(default_factory=list)
    #: 用户引用较早内容但未能在会话历史中找到原文。
    unresolved_reference: bool = False
    #: 裁剪已到下限仍超预算（当前请求与关键证据保留，如实记录触底）。
    budget_floor_exceeded: bool = False

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


def _recovery_candidates(
    request: str, older_items: Sequence[_TurnItem]
) -> list[str]:
    """从当前请求提取「引用较早内容」的候选线索。

    引号原文优先；无引号但出现回指词时，用请求中的名词性连续串（过滤掉
    在较早消息中出现过于频繁的词）作为线索。没有较早消息（引用对象只可
    能已在近期原文里）时不产生线索、不触发不确定。
    """
    if not older_items:
        return []
    quoted = [span.strip() for span in _QUOTED_SPAN_RE.findall(request)]
    quoted = [span for span in quoted if span]
    if quoted:
        return quoted
    if not any(marker in request for marker in _BACK_REFERENCE_MARKERS):
        return []
    runs = {run.lower() for run in _TOKEN_RUN_RE.findall(request)}
    docs = len(older_items)
    max_docs = max(1, int(docs * _MARKER_CANDIDATE_MAX_DOC_FREQUENCY))
    candidates = [
        run
        for run in runs
        if sum(1 for item in older_items if run in item.content.lower()) <= max_docs
    ]
    return sorted(candidates, key=lambda run: (-len(run), run))[:8]


def _recover_older_items(
    candidates: Sequence[str], older_items: Sequence[_TurnItem]
) -> list[_TurnItem]:
    """按线索在较早消息中定位相关原文（确定性排序，限单轮补回条数）。"""
    scored: list[tuple[int, int, _TurnItem]] = []
    for index, item in enumerate(older_items):
        content = item.content.lower()
        best = 0
        for candidate in candidates:
            if candidate.lower() in content:
                best = max(best, len(candidate))
        if best > 0:
            scored.append((best, index, item))
    scored.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return [item for _, _, item in scored[:RECOVERED_MAX_MESSAGES]]


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


def _recovered_block(recovered: Sequence[_TurnItem]) -> str:
    """补回原文的系统块（完整原文 + 来源消息 ID）。"""
    lines = [
        f"- [{item.message_id}] "
        f"{'用户' if item.role == ChatMessageRole.USER else '助手'}：{item.content}"
        for item in recovered
    ]
    return (
        "用户请求涉及较早对话中的实体或约定，以下为从本会话原始消息中"
        "找回的相关原文（消息 ID）：\n" + "\n".join(lines)
    )


_UNCERTAINTY_RULE = (
    "用户请求似乎引用了较早的对话内容，但本轮提供的材料中没有找到对应的"
    "原始消息：如无法仅凭已提供材料回答，请明确说明未能在会话历史中找到"
    "该内容，并向用户询问；绝不能编造或臆测早先的原文、约定或结论。"
)


def compile_turn_context(
    *,
    messages: Sequence[MessageRecord],
    current_user_message_id: str,
    model_id: str | None,
    mode: ChatMode,
    context_window: int | None = None,
) -> CompiledTurnContext:
    """编译一轮普通对话的模型输入上下文（纯函数；详见模块说明）。

    ``context_window`` 显式传入时优先（模型切换后的下一轮按新窗口重算；
    测试可注入小窗口验证裁剪），否则按 :func:`verified_context_window` 取
    锁定模型的已验证窗口。
    """
    window = (
        context_window
        if context_window is not None
        else verified_context_window(model_id)
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

    output_reserve = OUTPUT_RESERVE_TOKENS + OUTPUT_RESERVE_MARGIN_TOKENS
    input_budget = max(0, window - output_reserve - TOOL_RESERVE_TOKENS)

    # 近期原文窗口：从最新往回贪心纳入，占用不超过可用材料预算的既定份额；
    # 当前请求无论如何都保留。较早片段进摘要。
    contract = mode_system_contract(mode)
    system_tokens = estimate_tokens(contract.system_prompt)
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
    while True:
        candidates = _recovery_candidates(current.content, older)
        recovered = _recover_older_items(candidates, older)
        unresolved = bool(candidates) and not recovered
        summary_text, summary_range = _summary_block(
            older, entry_max_chars=entry_max_chars
        )
        blocks: list[str] = [contract.system_prompt]
        if summary_text is not None:
            blocks.append(summary_text)
        if recovered:
            blocks.append(_recovered_block(recovered))
        if unresolved:
            blocks.append(_UNCERTAINTY_RULE)
        input_estimate = sum(estimate_tokens(block) for block in blocks)
        input_estimate += sum(estimate_tokens(item.content) for item in recent)
        input_estimate += image_tokens
        if input_estimate <= input_budget:
            break
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
        adopted_message_ids=adopted,
        summary_source_range=summary_range,
        recovered_message_ids=[item.message_id for item in recovered],
        unresolved_reference=unresolved,
        budget_floor_exceeded=floor_exceeded,
    )
