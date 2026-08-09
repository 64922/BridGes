"""回合编排深模块（Issue 42 架构加深）。

对话生成的整个回合管线收敛在本模块：模式路由（日常陪伴 / 学习模式 /
SKILL / 生涯规划 / 图片 / 视频 / MCP）、分层检索、教学证据门、联网与
论文搜索、最小画像切片编译、提示词组装与流式收敛，全部隐藏在一个小
interface 之后——``TurnOrchestrator.stream_turn`` 只回答「驱动一次生
成，产出事件流」。

外部对外的唯一接口是 ``stream_turn``；模式路由成为内部 seam（每种载
荷对应一个内部编排路径），检索只有 ``_run_retrieval`` 一个实现，提示
词组装只有 ``assemble_payload`` 一个实现，思考摘要以类型化
``ChatThinkingSummary`` 记录（不再是裸 dict 约定）。``ChatService``
只保留对话/消息的账户隔离持久化与投影，回合编排委托给本模块，公开
接口与行为保持不变。
"""

from __future__ import annotations

import contextlib
import re
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ALL_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from bridges.skills.humanizer.service import HumanizerRunEvent

from pydantic import ValidationError

from bridges.ai import ModelGateway
from bridges.ai.adapters import StreamEvent
from bridges.arxiv_mcp.contracts import ArxivSearchProjection, ArxivSearchStatus
from bridges.arxiv_mcp.service import ArxivSearchService
from bridges.career.intake import assess_intake
from bridges.career.intent import is_career_intent
from bridges.chat.budget import (
    EXTERNAL_TIMEOUT_SECONDS,
    RESULT_FAILED,
    RESULT_OK,
    RESULT_TIMEOUT,
    RunBudget,
    RunStage,
)
from bridges.chat.lifecycle import GenerationLifecycle
from bridges.chat.repository import ConversationRepository, MessageRecord
from bridges.chat.selections import ChatSelectionsService, selection_key
from bridges.contracts.ai import ModelRunLock
from bridges.contracts.career import (
    CareerPlanningProcessState,
    CareerPlanningProjection,
    CareerPlanningStatus,
    CareerRunEvent,
)
from bridges.contracts.chat import (
    ChatMessageRole,
    ChatMessageStatus,
    ChatMode,
    ChatStreamCareerData,
    ChatStreamHumanizerData,
    ChatStreamImageData,
    ChatStreamMcpData,
    ChatStreamStageData,
    ChatStreamVideoData,
    ChatThinkingSummary,
    ContextNoteProjection,
    ContextNoteState,
    ImageRequestPayload,
    McpCallMessageProjection,
    McpCallRequestPayload,
    McpCallStatus,
    VideoRequestPayload,
)
from bridges.contracts.humanizer import (
    HumanizerResultStatus,
    HumanizerSkillInput,
)
from bridges.contracts.image import ImageError, ImageTaskKind, ImageTaskProjection
from bridges.contracts.mcp import McpCallRequest, McpError
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.contracts.profiles import (
    PROFILE_DIMENSION_LABELS,
    ProfileDimension,
    ProfileSlice,
    ProfileSliceItem,
)
from bridges.contracts.retrieval import (
    CitationProjection,
    RetrievalLayerStatus,
    RetrievalRoundProjection,
    RetrievalSourceLayer,
)
from bridges.contracts.teaching import (
    TeachingCardStatus,
    TeachingIntent,
    TeachingStage,
    TeachingTurnProjection,
)
from bridges.contracts.video import VideoError, VideoTaskProjection
from bridges.contracts.workflows import RunContextEnvelope
from bridges.learning.teaching_gate import TeachingTurnService
from bridges.mcp.service import McpService
from bridges.observability.service import ObservabilityService
from bridges.profiles.four_dimensions import FourDimensionProfileService
from bridges.profiles.service import ProfileService
from bridges.retrieval.service import LayeredRetrievalService
from bridges.web_search.contracts import WebSearchProjection, WebSearchStatus
from bridges.web_search.service import WebSearchService

#: 核心对话能力的固定绑定（ADR-0009 固定模型矩阵）。
CHAT_CAPABILITY_NAME = "qwen_text_chat"
CHAT_CAPABILITY_VERSION = "1"

#: 并行公开搜索超时占位（预算到期未完成的结果；调用方按降级处理）。
_SEARCH_TIMEOUT = object()


def _wait_timeout(futures: list[Any], timeout: float) -> tuple[set[Any], set[Any]]:
    """等待全部 future 完成或总超时（并行语义：总耗时接近较慢者）。"""
    if not futures:
        return set(), set()
    if timeout <= 0:
        return set(), set(futures)
    # 等全部来源完成（或总超时）：并行语义 = 总耗时接近较慢者而非之和
    done, pending = wait(futures, timeout=timeout, return_when=ALL_COMPLETED)
    return done, pending

#: 对话双模式（ADR-0022）：普通新聊天默认日常陪伴，学习项目新建对话默认学习模式。
CHAT_MODE = ChatMode.COMPANION


class OrchestrationStep(Protocol):
    """模式编排中的一步（Issue 14 声明的代码级编排 seam）。

    每步向用户披露一条可公开进度（``describe``）。学习模式的教学证据门、
    自动联网检索、理解检查与测验由 ``TeachingTurnService`` 和生成主路径
    执行，步骤本身只负责提供稳定的公开进度文案。
    """

    @property
    def name(self) -> str: ...

    def describe(self) -> str: ...


class DeclarativeStep:
    """声明式编排步骤：只提供可披露进度，不承载教学业务行为。

    教学业务行为在生成主路径中按当前模式执行，避免把进度文案误当成
    证据检查或回答能力。
    """

    def __init__(self, name: str, description: str) -> None:
        self._name = name
        self._description = description

    @property
    def name(self) -> str:
        return self._name

    def describe(self) -> str:
        return self._description


class ModeContract:
    """对话模式的回答策略合同（Issue 14，AC-05/AC-04 的代码级编排接口）。

    每种模式一份合同，统一提供提示词与公开进度；教学证据门、材料检索、
    教学规划、理解检查与测验由学习模式的教学服务执行。思考摘要的初始
    步骤从 ``steps`` 派生，合同是进度文案的唯一来源。
    """

    def __init__(
        self,
        system_prompt: str,
        steps: tuple[OrchestrationStep, ...],
    ) -> None:
        self.system_prompt = system_prompt
        #: 合同声明的编排步骤（按执行顺序）；生成开始即全部披露为
        #: 可公开进度，生命周期只在其后追加质量检查结论。
        self.steps = steps


#: 两种模式的角色合同（不包含隐藏指令或原始思维链，只定义回答策略）。
#: 日常陪伴：自然、有分寸的个性化陪伴——识别当前情境信号但不形成心理诊断，
#: 不把单次情绪写成长期事实。
#: 学习模式：因材施教老师合同——把「界定目标 → 参考知识状态 → 循序讲解 →
#: 理解检查/适量测验」的编排阶段显式声明为合同步骤；证据门与教学轮次
#: 由 ``TeachingTurnService`` 负责，回答不得绕过证据门或伪造工具结果。
_MODE_CONTRACTS: dict[ChatMode, ModeContract] = {
    ChatMode.COMPANION: ModeContract(
        system_prompt=(
            "你是 BridGes，一位长期科学学习与表达伙伴。当前使用「日常陪伴」合同："
            "回应自然、有分寸、有人情味，把用户当作一起长期学习的朋友。"
            "可以识别对话里出现的当前情境信号（如此刻的情绪、正在做的事），"
            "并温和地接住它们；但绝不据此形成心理诊断，也不把单次情绪写成长期事实。"
            "表达要简洁、真诚、不居高临下。涉及健康、心理等话题时，如实说明你能帮助的"
            "范围，需要专业意见时建议咨询专业人士。"
        ),
        steps=(
            DeclarativeStep("context", "理解你的问题与当前语境"),
            DeclarativeStep("answer", "组织并生成回答"),
        ),
    ),
    ChatMode.STUDY: ModeContract(
        system_prompt=(
            "你是 BridGes，一位因材施教的科学老师。当前使用「学习模式」合同，"
            "按以下编排约定组织每次回答："
            "1）先界定学习目标：区分概念理解、方法掌握与练习巩固，确定本次回答的层次；"
            "2）参考对话内已有的知识状态与学习进度，从学生当前水平出发循序渐进讲解；"
            "3）讲解中用具体例子连接新知识与已有认知，必要时主动安排理解检查"
            "（简短提问或请学生复述）与适量测验；"
            "4）回答结束给出下一步学习建议。"
            "每轮先核对当前对话、附件、项目资料和授权知识库；本地证据不足时按需"
            "检索公开来源。证据不足、冲突或不可用时，必须明确说明缺口，不得用模型"
            "记忆补全或伪造资料。教学卡片记录的理解检查只能作为待确认状态，不能"
            "仅凭一次回答宣布用户已掌握。"
        ),
        # 编排步骤与提示词合同一致；教学业务行为由学习模式教学服务执行。
        steps=(
            DeclarativeStep("teaching_objective", "按学习目标分析你的问题与已有知识"),
            DeclarativeStep("teaching_explain", "组织循序渐进的教学回答"),
        ),
    ),
}


def _contract(mode: ChatMode) -> ModeContract:
    return _MODE_CONTRACTS[mode]


#: 生成失败/断流时向用户展示的中文说明（稳定错误码 → 可操作提示）。
STREAM_INTERRUPTED_MESSAGE = "连接中断，已保留已接收内容，可点击重试。"

#: 稳定错误码 → 可操作中文提示（绝不输出供应商原文或调试字段）。
_ERROR_MESSAGES: dict[str, str] = {
    "rate_limit": "请求过于频繁（已触发限流），请稍后重试。",
    "transient": "连接中断或服务暂时不可用，请检查网络后重试。",
    # Issue 02：后台执行器失联且无恢复预算时的明确可重试终态。
    "generation_worker_lost": "生成进程意外退出，已保留已接收内容，可点击重试。",
    "region_error": "无法连接 Qwen 服务，请检查网络后重试。",
    "auth_error": "Qwen API Key 无效或已失效，请检查启动服务的全局百炼配置与权限。",
    "stream_interrupted": STREAM_INTERRUPTED_MESSAGE,
    "unregistered_capability": "核心对话能力未就绪，请稍后重试。",
    "capability_not_verified": "核心对话能力未通过验证，请检查启动服务的全局百炼配置与权限。",
    "no_adapter": "核心对话能力未就绪（缺少适配器），请检查服务配置。",
    "provider_rejected": "供应商拒绝了本次请求，请稍后重试。",
    "internal_error": "生成过程出现内部错误，请重试。",
    "web_search_timeout": "联网搜索超时，请重试。",
    "web_search_rate_limit": "公网搜索请求过于频繁，请稍后重试。",
    "web_search_offline": "当前无法连接公网搜索，请检查网络后重试。",
    "web_search_permission": "当前网络未允许访问公网搜索，请检查网络权限后重试。",
    "web_search_parse": "搜索结果暂时无法解析，请重试。",
    "web_search_request": "公网搜索请求未完成，请重试。",
    "web_search_no_results": "没有找到可核实的公开网页结果，请修改问题后重试。",
    "web_search_citation_invalid": "联网回答缺少可核实引用，请重试。",
    "arxiv_timeout": "arXiv 搜索超时，请重试。",
    "arxiv_rate_limit": "arXiv 请求过于频繁，请稍后重试。",
    "arxiv_offline": "当前无法连接 arXiv，请检查网络后重试。",
    "arxiv_permission": "当前网络未允许访问 arXiv，请检查网络权限后重试。",
    "arxiv_parse": "arXiv 返回内容损坏，无法解析，请重试。",
    "arxiv_request": "arXiv 搜索请求未完成，请重试。",
    "arxiv_startup": "arXiv 搜索服务启动失败，请重试。",
    # Issue 05：worker 启动握手与中途退出的独立错误分类。
    "arxiv_handshake": "arXiv 搜索服务启动失败，请重试。",
    "arxiv_worker_exit": "arXiv 搜索服务进程已退出，请重试。",
    "arxiv_internal": "arXiv 搜索服务异常，请重试。",
    "arxiv_cancelled": "已取消本轮论文搜索。",
    "arxiv_no_results": "没有找到匹配的 arXiv 论文，请调整领域或约束后重试。",
    "arxiv_citation_invalid": "论文回答缺少可核实的 arXiv 引用，请重试。",
    # Issue 07：humanizer 独有错误码不在此映射——其错误消息由编排服务
    # 构造（含冲突项与恢复方式的中文可操作说明），命中映射会吞掉详情；
    # 未命中时 executor 以服务消息为兜底（错误文案仍不泄漏内部 prompt）。
    "skill_unavailable": "SKILL 能力暂不可用，请稍后重试。",
    "budget_exceeded": "本次生成超过时延预算，已停止继续执行；请重试（输入已保留）。",
}

#: 用户点击重试后有望成功的错误码（限流/瞬时故障/断流/内部错误）。
_RETRYABLE_CODES = frozenset(
    {
        "rate_limit",
        "transient",
        "region_error",
        "stream_interrupted",
        # Issue 02：执行器失联收尸为可重试错误（用户点击重试创建新运行）。
        "generation_worker_lost",
        "internal_error",
        "web_search_timeout",
        "web_search_rate_limit",
        "web_search_offline",
        "web_search_parse",
        "web_search_request",
        "web_search_no_results",
        "web_search_citation_invalid",
        "arxiv_timeout",
        "arxiv_rate_limit",
        "arxiv_offline",
        "arxiv_permission",
        "arxiv_parse",
        "arxiv_request",
        "arxiv_startup",
        # Issue 05：握手/中途退出/异常均为可重试故障（取消除外）。
        "arxiv_handshake",
        "arxiv_worker_exit",
        "arxiv_internal",
        "arxiv_no_results",
        "arxiv_citation_invalid",
        # Issue 28：人味化任务可重试错误（原任务输入保留）。
        "humanizer_generation_failed",
        "humanizer_failed",
        "empty_output",
        "output_contract_incomplete",
        # Issue 06：超预算终止可重试（重试创建新运行，预算重新开始）。
        "budget_exceeded",
    }
)


def user_facing_error(error_code: str | None, fallback: str | None = None) -> str:
    """稳定错误码 → 可操作中文提示；未知码用回退文案，绝不输出原文。"""
    if error_code in _ERROR_MESSAGES:
        return _ERROR_MESSAGES[error_code]
    return fallback or "生成过程出现内部错误，请重试。"


def error_is_retryable(error_code: str | None) -> bool:
    """该错误码是否值得用户点击重试（幂等，不产生副作用）。"""
    return error_code in _RETRYABLE_CODES


class HumanizerOrchestrator(Protocol):
    """内置 SKILL（bridges-humanizer）编排接缝（Issue 28，运行时由
    ``skills.humanizer.service`` 实现）。聊天分支只负责载荷校验与事件
    收敛；证据合同检索、改写/生成、事实门复核都在编排服务内部完成。
    """

    def resolve_skill(self, skill_input: HumanizerSkillInput) -> str: ...

    def run_task(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        skill_input: HumanizerSkillInput,
        run_context: RunContextEnvelope,
        *,
        retrieval_round: RetrievalRoundProjection | None,
        web_search_projection: WebSearchProjection | None,
        arxiv_search_projection: ArxivSearchProjection | None,
    ) -> Iterator[HumanizerRunEvent]: ...


class CareerPlannerOrchestrator(Protocol):
    """生涯规划编排接缝（Issue 29，运行时由 career 服务实现）。

    聊天分支只负责证据合同检索与事件收敛；规划、确定性复核与六类输出
    生成都在编排服务内部完成。
    """

    def run_task(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        intent: str,
        *,
        mode: str,
        run_context: RunContextEnvelope,
        profile_enabled: bool,
        profile_used: bool,
        profile_items: list[ProfileSliceItem],
        retrieval_round: RetrievalRoundProjection | None,
        web_search_projection: WebSearchProjection | None,
        arxiv_search_projection: ArxivSearchProjection | None,
        budget: RunBudget | None = None,
    ) -> Iterator[CareerRunEvent]: ...


class ImageOrchestrator(Protocol):
    """图片生成/编辑编排接缝（Issue 31，运行时由 ImageService 实现）。

    聊天分支只负责把请求载荷转成异步任务并收敛消息；云端提交/轮询、
    资产落库与迟到结果隔离都在后台执行器进程内完成，不阻塞消息流。
    """

    def submit_generation(
        self, account_id: str, conversation_id: str, message_id: str, prompt: str
    ) -> ImageTaskProjection: ...

    def submit_edit(
        self,
        account_id: str,
        conversation_id: str,
        message_id: str,
        prompt: str,
        *,
        source_version_id: str,
        source_object_id: str,
    ) -> ImageTaskProjection: ...


class VideoOrchestrator(Protocol):
    """文生视频编排接缝（Issue 32，运行时由 VideoService 实现）。

    聊天分支只负责把请求载荷转成异步任务并收敛消息；云端提交/轮询、
    资产落库与迟到结果隔离都在后台执行器进程内完成，不阻塞消息流。
    """

    def submit(
        self, account_id: str, conversation_id: str, message_id: str, prompt: str
    ) -> VideoTaskProjection: ...


# ---------------------------------------------------------------------------
# 思考摘要（Issue 14）
# ---------------------------------------------------------------------------
#
# 「思考摘要」是面向用户的过程说明，不是模型隐藏推理。全部由结构化进度
# 事件与可披露结果构造：步骤（处理过程）、证据（采用的来源）、工具（调用
# 进度）、质量（检查结论）。绝不包含原始 Chain-of-Thought、系统提示、
# 隐藏指令或逐 token 推理。学习模式会在证据门与检索完成后追加结构化来源、
# 工具状态与质量结论；普通陪伴模式只在实际触发检索时追加对应信息。
# 思考摘要以类型化 ``ChatThinkingSummary`` 记录承载（不再是裸 dict 约定），
# 落库时经 ``finalize_message`` 统一转为 JSON。

def initial_thinking(mode: ChatMode) -> ChatThinkingSummary:
    """生成开始时的初始摘要（前端据此自动展开思考区域）。

    初始即含该模式的完整编排步骤（来自合同的 ``steps``，合同是唯一
    来源）；生命周期只在其后追加质量检查结论，杜绝「首 delta 才补
    第二步」的时序缺口。
    """
    return ChatThinkingSummary(
        steps=[step.describe() for step in _contract(mode).steps],
    )


def _truncate_text(text: str, max_chars: int) -> str:
    """截断文本到指定长度（生涯澄清投影的意图快照用）。"""
    value = (text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1] + "…"


def done_thinking(thinking: ChatThinkingSummary) -> ChatThinkingSummary:
    """完成时的摘要：质量检查结论（耗时由消息 duration_ms 呈现）。"""
    return thinking.model_copy(update={"quality": ["回答已完整生成并保存"]})


def failed_thinking(
    thinking: ChatThinkingSummary, error_code: str | None
) -> ChatThinkingSummary:
    """失败/断流/内部错误时的摘要：保留已完成步骤并给出中文质量结论。"""
    return thinking.model_copy(
        update={"quality": [user_facing_error(error_code, "生成失败，已保留已完成部分。")]}
    )


def stopped_thinking(thinking: ChatThinkingSummary) -> ChatThinkingSummary:
    """用户停止时的摘要：保留已完成步骤并给出中文质量结论。"""
    return thinking.model_copy(update={"quality": ["已停止生成，保留已生成内容。"]})


def budget_warning_thinking(thinking: ChatThinkingSummary) -> ChatThinkingSummary:
    """预算受限交付时的摘要：已交付草稿，重试可获得更完整回答（Issue 06）。"""
    return thinking.model_copy(
        update={
            "quality": [
                "回答已交付（受时延预算限制，内容可能不完整）；重试可获得更完整回答。"
            ]
        }
    )


# ---------------------------------------------------------------------------
# 分层检索的思考摘要与上下文（Issue 20）
# ---------------------------------------------------------------------------
#
# 思考摘要的「引用依据」（evidence）与「工具进度」（tools）由本轮检索
# 结果构造，与「模型组织说明」（steps，来自模式合同）严格区分；引用
# 展示数据在检索时固化，索引重建不会让历史引用静默漂移。

#: 来源层的中文呈现（思考摘要工具条目与上下文说明共用）。
_LAYER_NAMES: dict[RetrievalSourceLayer, str] = {
    RetrievalSourceLayer.ATTACHMENT: "当前附件",
    RetrievalSourceLayer.PROJECT: "学习项目文件",
    RetrievalSourceLayer.KNOWLEDGE_BASE: "全局知识库",
}
#: 思考摘要中引用片段的最大长度。
_EVIDENCE_SNIPPET_MAX = 160
#: 注入模型的最小上下文总长度上限。
_CONTEXT_MAX_CHARS = 2400


def retrieval_thinking(
    thinking: ChatThinkingSummary, retrieval_round: RetrievalRoundProjection
) -> ChatThinkingSummary:
    """把一轮检索结果并入思考摘要：evidence=引用依据，tools=检索进度。"""
    evidence: list[str] = []
    for citation in retrieval_round.citations:
        snippet = citation.snippet
        if len(snippet) > _EVIDENCE_SNIPPET_MAX:
            snippet = snippet[:_EVIDENCE_SNIPPET_MAX] + "…"
        evidence.append(
            f"引用了「{citation.filename}」{citation_location(citation)}"
            f"：{snippet}"
        )
    tools: list[str] = []
    for layer in retrieval_round.layers:
        if layer.status == RetrievalLayerStatus.OK and layer.candidates > 0:
            tools.append(
                f"已检索{_LAYER_NAMES[layer.layer]}：{layer.candidates} 条候选"
            )
    if not tools:
        tools.append(sufficiency_tool(retrieval_round))
    return thinking.model_copy(update={"evidence": evidence, "tools": tools})


def sufficiency_tool(retrieval_round: RetrievalRoundProjection) -> str:
    """无候选时面向用户的检索进度说明（结构化，不以空候选表示成功）。

    直接复用检索轮次的中文注记（``note`` 是充足性信号的唯一文案来源），
    避免同一信号在后端多份措辞漂移。
    """
    if retrieval_round.note:
        return retrieval_round.note
    if retrieval_round.citations:
        return "已检索本地材料。"
    return "本轮未检索到匹配的本地材料。"


def citation_location(citation: CitationProjection, *, snippet_max: int = 0) -> str:
    """引用位置的面向用户中文描述（页码/章节/原文片段）。"""
    parts: list[str] = []
    if citation.page_number is not None:
        parts.append(f"第 {citation.page_number} 页")
    if citation.section_title:
        parts.append(f"章节：{citation.section_title}")
    if parts:
        return " · ".join(parts)
    return "原文片段"


def retrieval_context(citations: list[CitationProjection]) -> str:
    """构造注入模型的最小检索上下文（固定格式，可测试）。

    材料只作为参考：模型引用时必须注明来源编号，不得声称存在未提供的
    文件或页码——引用芯片本身由检索轮次提供，杜绝幻觉来源。
    """
    lines = [
        "以下是本轮检索到的本地材料（仅作参考；引用时注明来源编号，"
        "不得声称存在未提供的文件或页码）："
    ]
    used = 0
    for index, citation in enumerate(citations, start=1):
        snippet = citation.snippet
        if len(snippet) > _EVIDENCE_SNIPPET_MAX:
            snippet = snippet[:_EVIDENCE_SNIPPET_MAX] + "…"
        line = f"[{index}]「{citation.filename}」{citation_location(citation)}：{snippet}"
        if used + len(line) > _CONTEXT_MAX_CHARS:
            break
        used += len(line)
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 公网搜索的思考摘要与上下文（Issue 21）
# ---------------------------------------------------------------------------

def web_search_thinking(
    thinking: ChatThinkingSummary, projection: WebSearchProjection
) -> ChatThinkingSummary:
    """把搜索触发原因、结果与失败状态变成可公开进度。"""
    tools = [f"已触发联网搜索：{projection.trigger_reason}；查询概述：{projection.query_summary}"]
    if projection.status == WebSearchStatus.SUCCESS:
        evidence = [f"{item.title}（{item.site}）" for item in projection.results]
        tools.append(f"已返回 {len(projection.results)} 条公开网页结果")
        return thinking.model_copy(
            update={
                "evidence": [*thinking.evidence, *evidence],
                "tools": [*thinking.tools, *tools],
            }
        )
    if projection.status == WebSearchStatus.EMPTY:
        tools.append("公网搜索没有返回可核实结果")
    elif projection.status == WebSearchStatus.PERMISSION:
        tools.append("公网搜索权限未通过")
    else:
        tools.append(projection.error_message or "公网搜索未完成")
    return thinking.model_copy(update={"tools": [*thinking.tools, *tools]})


def web_search_context(projection: WebSearchProjection) -> str:
    """只把真实返回的公开来源注入模型，并要求来源可追溯。"""
    lines = [
        "以下是本轮公网搜索返回的公开来源。只能依据这些真实来源回答联网部分，"
        "引用时使用对应的 [web-n]，不得编造来源或 URL："
    ]
    used = 0
    for index, result in enumerate(projection.results, start=1):
        snippet = result.snippet[:_EVIDENCE_SNIPPET_MAX]
        line = (
            f"[web-{index}] {result.title}（{result.site}）\n"
            f"URL：{result.url}\n摘要：{snippet}\n访问时间：{result.accessed_at.isoformat()}"
        )
        if used + len(line) > _CONTEXT_MAX_CHARS:
            break
        used += len(line)
        lines.append(line)
    return "\n".join(lines)


_WEB_CITATION_RE = re.compile(r"\[web-(\d+)\]")


def web_search_citation_error(content: str, result_count: int) -> str | None:
    """要求联网回答至少引用一个真实结果，且不能引用不存在的结果编号。"""
    references = [int(match) for match in _WEB_CITATION_RE.findall(content)]
    if not references:
        return "联网回答缺少可核实引用，请重试。"
    if any(reference < 1 or reference > result_count for reference in references):
        return "联网回答引用了不存在的来源，请重试。"
    return None


# ---------------------------------------------------------------------------
# arXiv 搜索的思考摘要与上下文（Issue 22）
# ---------------------------------------------------------------------------

def arxiv_search_thinking(
    thinking: ChatThinkingSummary, projection: ArxivSearchProjection
) -> ChatThinkingSummary:
    """把论文搜索原因、真实论文与失败状态变成可公开进度。"""
    tools = [
        f"已触发 arXiv 论文搜索：{projection.trigger_reason}；"
        f"查询主题：{projection.query_summary}"
    ]
    if projection.status == ArxivSearchStatus.SUCCESS:
        evidence = [
            f"{paper.title}（arXiv:{paper.arxiv_id}）" for paper in projection.papers
        ]
        tools.append(f"已返回 {len(projection.papers)} 篇真实 arXiv 论文")
        return thinking.model_copy(
            update={
                "evidence": [*thinking.evidence, *evidence],
                "tools": [*thinking.tools, *tools],
            }
        )
    if projection.status == ArxivSearchStatus.EMPTY:
        tools.append("arXiv 没有返回匹配论文")
    elif projection.status == ArxivSearchStatus.PERMISSION:
        tools.append("arXiv 网络权限未通过")
    else:
        tools.append(projection.error_message or "arXiv 论文搜索未完成")
    return thinking.model_copy(update={"tools": [*thinking.tools, *tools]})


def arxiv_search_context(projection: ArxivSearchProjection) -> str:
    """只把真实 arXiv 元数据注入模型，并要求使用稳定引用编号。"""
    lines = [
        "以下是本轮 arXiv 返回的真实论文。只能依据这些论文回答论文部分，"
        "引用时必须使用对应的 [arxiv-n]，不得编造论文、作者、标识符或链接："
    ]
    used = 0
    for paper in projection.papers:
        line = (
            f"[{paper.citation_id}] arXiv:{paper.arxiv_id}\n"
            f"标题：{paper.title}\n作者：{'、'.join(paper.authors)}\n"
            f"发布日期：{paper.published_at.date().isoformat()}\n"
            f"摘要链接：{paper.abs_url}\nPDF：{paper.pdf_url}\n"
            f"摘要：{paper.abstract[:_EVIDENCE_SNIPPET_MAX]}"
        )
        if used + len(line) > _CONTEXT_MAX_CHARS:
            break
        used += len(line)
        lines.append(line)
    return "\n".join(lines)


def arxiv_citation_error(
    content: str, projection: ArxivSearchProjection
) -> str | None:
    """要求论文回答至少引用一个真实结果且不引用不存在的编号。"""
    references = set(re.findall(r"\[arxiv-(\d+)\]", content))
    if not references:
        return "论文回答缺少可核实的 arXiv 引用，请重试。"
    valid = {paper.citation_id.removeprefix("arxiv-") for paper in projection.papers}
    if not references.issubset(valid):
        return "论文回答引用了不存在的 arXiv 结果，请重试。"
    return None


# ---------------------------------------------------------------------------
# 教学证据门的思考摘要与上下文（Issue 23）
# ---------------------------------------------------------------------------

def teaching_thinking(
    thinking: ChatThinkingSummary, teaching: TeachingTurnProjection
) -> ChatThinkingSummary:
    """将证据门的检查、联网触发和缺口写入用户可见的过程摘要。"""
    gate = teaching.evidence_gate
    tools = [
        f"教学证据门：{gate.status.value}；检查理由：{gate.reason}",
        f"公开来源策略：{gate.required_search.value}",
    ]
    if gate.search_status is not None:
        tools.append(f"公开来源状态：{gate.search_status.value}")
    evidence = [
        f"{source.source_type}：{source.title}"
        for source in [*gate.local_sources, *gate.external_sources]
    ]
    return thinking.model_copy(
        update={
            "evidence": [*thinking.evidence, *evidence],
            "tools": [*thinking.tools, *tools],
        }
    )


def stopped_teaching_projection(
    teaching: dict[str, Any] | None,
) -> TeachingTurnProjection | None:
    """把用户取消时的教学卡片从 loading 收敛为可恢复状态。"""

    if teaching is None:
        return None
    projection = TeachingTurnProjection.model_validate(teaching)
    gate = projection.evidence_gate.model_copy(
        update={"search_status": TeachingCardStatus.RECOVERY}
    )
    return projection.model_copy(
        update={
            "status": TeachingCardStatus.RECOVERY,
            "evidence_gate": gate,
            "next_prompt": "本轮已取消；你可以重试证据检查，或继续日常陪伴。",
            "gap_response": "本轮教学检查已取消，尚未形成可靠教学结论。",
            "can_answer_reliably": False,
            "can_cancel": False,
            "can_retry": True,
            "quiz": None,
        }
    )


def cancelled_web_search(
    web_search: dict[str, Any] | None, now: datetime
) -> dict[str, Any] | None:
    """把已触发的公网搜索与生成终态一起收敛为取消，避免卡片残留 loading。"""
    if web_search is None:
        return None
    cancelled = dict(web_search)
    cancelled.update(
        {
            "status": WebSearchStatus.CANCELLED.value,
            "results": [],
            "searched_at": now.isoformat(),
            "error_code": None,
            "error_message": "已取消本轮联网搜索。",
            "can_retry": False,
            "can_cancel": False,
        }
    )
    return cancelled


def cancelled_arxiv_search(
    arxiv_search: dict[str, Any] | None, now: datetime
) -> dict[str, Any] | None:
    """把 arXiv 搜索与生成终态一起收敛为取消，避免卡片残留 loading。"""
    if arxiv_search is None:
        return None
    cancelled = dict(arxiv_search)
    cancelled.update(
        {
            "status": ArxivSearchStatus.CANCELLED.value,
            "papers": [],
            "searched_at": now.isoformat(),
            "error_code": None,
            "error_message": "已取消本轮论文搜索。",
            "can_retry": False,
            "can_cancel": False,
        }
    )
    return cancelled


def teaching_context(teaching: TeachingTurnProjection) -> str:
    """向模型注入教学证据边界，防止把模型记忆冒充为本轮依据。"""
    gate = teaching.evidence_gate
    lines = [
        "你正在执行学习模式的一轮教学。只能使用下列证据门允许的来源，不得用模型记忆填补缺口。",
        f"证据门状态：{gate.status.value}；理由：{gate.reason}",
        f"本轮可靠回答许可：{'是' if teaching.can_answer_reliably else '否'}",
        "不得仅凭一次自述或一次题目回答宣称用户已掌握；知识状态只能作为待确认候选。",
    ]
    for source in [*gate.local_sources, *gate.external_sources]:
        locator = f"（{source.locator}）" if source.locator else ""
        lines.append(f"[{source.source_id}] {source.title}{locator}")
    if gate.gap:
        lines.append(f"必须向用户明确说明缺口：{gate.gap}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 最小画像切片的思考摘要与上下文（Issue 27）
# ---------------------------------------------------------------------------
#
# 切片是长期画像信息进入模型上下文的唯一载体：只包含当前模式相关、
# 已授权、仍有效的记录（值截断），绝不注入完整画像中心或未确认候选。
# 上下文说明披露只展示类别、用途、来源记录链接与使用时间，不暴露系统
# 提示、隐藏提示或原始思维链。

def profile_slice_context(profile_slice: ProfileSlice) -> str:
    """构造注入模型的最小画像切片上下文（固定格式，可测试）。

    模型只能依据切片内提供的记录回答，不得推断更多画像信息；未确认
    候选、已撤回/冻结/过期记录已由编译器排除，不在此上下文中。
    """
    lines = [
        "以下是本轮为你使用的画像切片（仅包含你已授权且与当前任务相关的最小记录；"
        "只能据此回答，不得推断或声称存在更多画像信息）："
    ]
    used = 0
    for index, item in enumerate(profile_slice.included_items, start=1):
        label = dimension_label(item.dimension)
        line = f"[{index}]（类别：{label}）{item.value_or_rule}。用途：{item.inclusion_reason}"
        if used + len(line) > _CONTEXT_MAX_CHARS:
            break
        used += len(line)
        lines.append(line)
    return "\n".join(lines)


def dimension_label(dimension: str) -> str:
    """画像维度中文标签；未知维度直接回退原始值，不抛错。"""
    try:
        return PROFILE_DIMENSION_LABELS[ProfileDimension(dimension)]
    except ValueError:
        return dimension


def context_note_ready_text(items: list[ProfileSliceItem]) -> str:
    """披露卡的中文一句话说明（ready 态）。"""
    categories = "、".join(dict.fromkeys(dimension_label(item.dimension) for item in items))
    return (
        f"本轮回答使用了 {len(items)} 条画像记录（{categories}），"
        "仅包含与当前任务相关的最小切片。"
    )


def context_note_thinking(
    thinking: ChatThinkingSummary, context_note: ContextNoteProjection
) -> ChatThinkingSummary:
    """把画像披露摘要并入可公开思考（不暴露隐藏提示或思维链）。"""
    tools = list(thinking.tools)
    if context_note.state == ContextNoteState.READY:
        tools.append(
            f"已使用 {context_note.profile_item_count} 条画像记录（最小切片，仅限当前任务）"
        )
    elif context_note.state == ContextNoteState.OFF:
        tools.append("本轮未使用画像记录（发送前已关闭）")
    elif context_note.state == ContextNoteState.EMPTY:
        tools.append("本轮没有与当前任务相关的画像记录")
    elif context_note.state == ContextNoteState.ERROR:
        tools.append("本轮画像切片不可用，回答未基于画像信息")
    return thinking.model_copy(update={"tools": tools})


# ---------------------------------------------------------------------------
# 用户消息载荷与轮次辅助（重试沿用同一份任务契约）
# ---------------------------------------------------------------------------

def owner_user_message(
    messages: list[MessageRecord], assistant_message_id: str
) -> MessageRecord | None:
    """返回某条助手消息所属（其之前最近的）用户消息。"""
    owner: MessageRecord | None = None
    for message in messages:
        if message.role == ChatMessageRole.USER:
            owner = message
        elif message.message_id == assistant_message_id:
            return owner
    return None


def skill_input_from(owner: MessageRecord | None) -> HumanizerSkillInput | None:
    """从用户消息的 SKILL 载荷快照还原任务契约（重试沿用同一份输入）。"""
    if owner is None or not owner.skill:
        return None
    try:
        return HumanizerSkillInput.model_validate(owner.skill)
    except ValidationError:
        return None


def image_payload_from(owner: MessageRecord | None) -> ImageRequestPayload | None:
    """从用户消息的 image 列还原图片请求载荷（重试沿用同一份输入）。

    用户消息的 image 列只存请求载荷；任务状态快照只写在助手消息的
    image 列（含 task_id/status 字段），据此判别避免误解析。
    """
    if owner is None or not owner.image:
        return None
    if "task_id" in owner.image or "status" in owner.image:
        return None
    try:
        return ImageRequestPayload.model_validate(owner.image)
    except ValidationError:
        return None


def video_payload_from(owner: MessageRecord | None) -> VideoRequestPayload | None:
    """从用户消息的 video 列还原视频请求载荷（重试沿用同一份输入）。

    用户消息的 video 列只存请求载荷；任务状态快照只写在助手消息的
    video 列（含 task_id/status 字段），据此判别避免误解析。
    """
    if owner is None or not owner.video:
        return None
    if "task_id" in owner.video or "status" in owner.video:
        return None
    try:
        return VideoRequestPayload.model_validate(owner.video)
    except ValidationError:
        return None


def mcp_call_payload_from(owner: MessageRecord | None) -> McpCallRequestPayload | None:
    """从用户消息的 mcp_call 列还原调用载荷（重试沿用同一份输入）。

    用户消息的 mcp_call 列只存请求载荷；助手消息的 mcp_call 列存结果
    投影（含 status 字段），据此判别避免误解析。
    """
    if owner is None or not owner.mcp_call:
        return None
    if "status" in owner.mcp_call:
        return None
    try:
        return McpCallRequestPayload.model_validate(owner.mcp_call)
    except ValidationError:
        return None


def previous_teaching_turn(
    messages: list[MessageRecord], user_message_id: str | None
) -> TeachingTurnProjection | None:
    """找到本轮用户消息之前最近一轮教学记录，用于评价连续对话中的回答。"""
    previous: TeachingTurnProjection | None = None
    for message in messages:
        if user_message_id is not None and message.message_id == user_message_id:
            return previous
        if message.role == ChatMessageRole.ASSISTANT and message.teaching is not None:
            previous = TeachingTurnProjection.model_validate(message.teaching)
    return previous


def attempt_group(
    messages: list[MessageRecord], user_message_id: str
) -> list[MessageRecord]:
    """返回某轮用户消息之下的全部助手尝试（不含后续其他轮次）。"""
    group: list[MessageRecord] = []
    in_group = False
    for message in messages:
        if message.role == ChatMessageRole.USER:
            in_group = message.message_id == user_message_id
            continue
        if in_group:
            group.append(message)
    return group


def input_summary(input_data: dict[str, Any]) -> str:
    """入参摘要：JSON 序列化截断，最长 200 字符（不含秘密与完整正文）。"""
    import json

    try:
        text = json.dumps(input_data, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return ""
    return text[:200]


def result_summary(result: Any) -> str | None:
    """成功结果摘要：字符串化截断，最长 500 字符（不保存完整私人正文）。"""
    if result is None:
        return None
    text = str(result)
    if not text.strip():
        return None
    return text[:500]


def mcp_call_summary(projection: McpCallMessageProjection) -> str:
    """助手消息正文收敛为调用摘要（结果卡承载详情）。"""
    name = projection.mcp_name or projection.mcp_id
    if projection.status == McpCallStatus.SENSITIVE_PENDING:
        return f"已调用「{name}」工具 {projection.tool}，等待敏感操作确认…"
    if projection.status == McpCallStatus.FAILED:
        reason = projection.error_message or "未知原因"
        return f"「{name}」工具 {projection.tool} 调用失败：{reason}"
    if projection.status == McpCallStatus.DENIED:
        return f"已拒绝「{name}」工具 {projection.tool} 的敏感操作。"
    return f"已调用「{name}」工具 {projection.tool}。"


# ---------------------------------------------------------------------------
# 提示词组装单点
# ---------------------------------------------------------------------------

def assemble_payload(
    history: list[dict[str, str]],
    *,
    tools_context: str | None = None,
    retrieval_round: RetrievalRoundProjection | None = None,
    web_search_projection: WebSearchProjection | None = None,
    arxiv_search_projection: ArxivSearchProjection | None = None,
    teaching_projection: TeachingTurnProjection | None = None,
    profile_context: str | None = None,
) -> dict[str, Any]:
    """提示词组装单点：上下文按固定顺序以独立 system 块注入。

    所有编排路径的模型载荷都经此构造——新增上下文来源只改这里，不散落
    在调用方（原来的 ``payload["messages"].insert(1, ...)`` 约定收敛于
    本函数）。注入顺序固定：工具集合 → 检索 → 公网 → arXiv → 教学 →
    画像切片（与既有语义一致：最具体的上下文在最上方）。模型只能引用
    各块提供的材料，不得声称存在未提供的文件、页码或来源。
    """
    messages = list(history)
    blocks: list[str | None] = [
        tools_context,
        (
            retrieval_context(retrieval_round.citations)
            if retrieval_round is not None and retrieval_round.citations
            else None
        ),
        (
            web_search_context(web_search_projection)
            if web_search_projection is not None
            else None
        ),
        (
            arxiv_search_context(arxiv_search_projection)
            if arxiv_search_projection is not None
            else None
        ),
        (
            teaching_context(teaching_projection)
            if teaching_projection is not None
            else None
        ),
        profile_context,
    ]
    for block in blocks:
        if block:
            messages.insert(1, {"role": "system", "content": block})
    return {"messages": messages, "temperature": 0.7, "max_tokens": 1024}


# ---------------------------------------------------------------------------
# 消息终态收敛
# ---------------------------------------------------------------------------

def finalize_message(
    repo: ConversationRepository,
    account_id: str,
    message_id: str,
    *,
    status: ChatMessageStatus,
    error_code: str | None,
    error_message: str | None,
    duration_ms: int | None,
    model_id: str | None,
    run_lock_id: str | None,
    started: float,
    now: datetime,
    thinking: ChatThinkingSummary | dict[str, list[str]] | None = None,
    web_search: dict[str, Any] | None = None,
    arxiv_search: dict[str, Any] | None = None,
    teaching: dict[str, Any] | None = None,
) -> None:
    """原子收敛生成状态；仅当仍处于 streaming 时生效（防竞态双写）。

    思考摘要接受类型化记录或既有 JSON dict，统一在此转为落库格式——
    所有终态路径（生成/停止/陈旧收敛）共用这一处。
    """
    measured = max(1, int((time.monotonic() - started) * 1000))
    repo.finalize_message(
        account_id,
        message_id,
        status=status,
        error_code=error_code,
        error_message=error_message,
        duration_ms=duration_ms or measured,
        model_id=model_id,
        run_lock_id=run_lock_id,
        updated_at=now,
        thinking=(
            thinking.model_dump(mode="json")
            if isinstance(thinking, ChatThinkingSummary)
            else thinking
        ),
        web_search=web_search,
        arxiv_search=arxiv_search,
        teaching=teaching,
    )


class TurnOrchestrator:
    """回合编排深模块：一个 ``stream_turn`` 接口，整个生成管线在其后。

    依赖与 ``ChatService`` 相同的注入集合（同一实例由服务传入）；内部
    按载荷分派到各编排路径（内部 seam），分层检索只有 ``_run_retrieval``
    一个实现，提示词组装只有 ``assemble_payload`` 一个实现。
    """

    def __init__(
        self,
        *,
        repository: ConversationRepository,
        lifecycle: GenerationLifecycle,
        retrieval_service: LayeredRetrievalService | None = None,
        web_search_service: WebSearchService | None = None,
        arxiv_search_service: ArxivSearchService | None = None,
        teaching_service: TeachingTurnService | None = None,
        profile_service: ProfileService | None = None,
        four_dimension_profile_service: FourDimensionProfileService | None = None,
        observability_service: ObservabilityService | None = None,
        humanizer_service: HumanizerOrchestrator | None = None,
        career_planner_service: CareerPlannerOrchestrator | None = None,
        image_service: ImageOrchestrator | None = None,
        video_service: VideoOrchestrator | None = None,
        selections_service: ChatSelectionsService | None = None,
        mcp_service: McpService | None = None,
    ) -> None:
        self._repo = repository
        self._lifecycle = lifecycle
        #: 分层本地检索（Issue 20）；未挂载时生成不检索、不产生引用。
        self._retrieval = retrieval_service
        #: 明确联网/时效/核查请求的固定 DuckDuckGo 搜索（Issue 21）。
        self._web_search = web_search_service
        #: 受限内置 arXiv MCP（Issue 22）；结果失败时不调用模型兜底。
        self._arxiv_search = arxiv_search_service
        #: 学习模式教学证据门与统一聊天教学轮次（Issue 23）。
        self._teaching = teaching_service or TeachingTurnService()
        #: 画像记忆意图处理（Issue 26）；未挂载时聊天不产生画像通知。
        self._profiles = profile_service
        self._four_dimension_profiles = four_dimension_profile_service
        #: 云端披露审计（Issue 27）；未挂载时跳过审计，不阻断生成。
        self._observability = observability_service
        #: 内置 bridges-humanizer SKILL 编排（Issue 28）。
        self._humanizer = humanizer_service
        #: 生涯规划编排（Issue 29）。
        self._career_planner = career_planner_service
        #: 图片生成与编辑编排（Issue 31）。
        self._image = image_service
        #: 文生视频编排（Issue 32，Wan 固定绑定）。
        self._video = video_service
        #: 对话级插件选择域（Issue 36）：选择校验、失效清洗与工具上下文编译。
        self._selections = selections_service
        #: MCP 服务器服务（Issue 36）：聊天内对选中 MCP 的真实调用。
        self._mcp = mcp_service

    # ------------------------------------------------------------------
    # 回合入口（对外唯一 interface）
    # ------------------------------------------------------------------

    def stream_turn(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        run_context: RunContextEnvelope,
        until_user_message_id: str | None = None,
        use_knowledge_base: bool = True,
        use_profile: bool = True,
        *,
        gateway: ModelGateway,
    ) -> Iterator[StreamEvent]:
        """驱动一次生成：调用网关流式接口，边收边落库，结束时收敛状态。

        ``gateway`` 每次调用传入（模型传输是回合级依赖；测试与组合路径
        通过服务层替换网关后仍经同一接口生效）。

        生成前执行一轮分层本地检索（Issue 20）：按「当前附件 → 当前项目
        文件 → 已授权全局知识库」确定候选作用域，把最终引用固化为消息的
        检索轮次，并注入最小上下文；关闭全局知识库时本轮不查询该层。
        生成前还编译最小画像切片（Issue 27）：按当前模式只取任务相关的
        已授权记录，注入最小上下文并落库「本次上下文说明」披露；用户
        发送前关闭画像（``use_profile=False``）时本轮不编译、不注入，披露
        与审计均不含任何画像内容。检索/切片失败都不阻断生成。
        事件经此处原样透传给 API 层；停止信号（用户停止/切换账户导致的
        客户端断开）都会把消息收敛到明确终态，绝不留 streaming 僵尸。
        """
        # 生成器惰性启动：若在启动前已被停止/收敛（例如停止接口先行完成），
        # 直接退出，绝不重新唤起一条已经终态的消息；同时校验消息归属
        # 对话，杜绝跨对话用 message_id 驱动生成。
        current = self._repo.get_message(account_id, assistant_message_id)
        if (
            current is None
            or current.conversation_id != conversation_id
            or current.status != ChatMessageStatus.STREAMING
        ):
            return

        # 复用 start/retry 阶段预注册的停止信号（保证注册表在 start 即
        # 生效，读取陈旧收敛不会误伤进行中的流）；缺失时补注册。
        entry = self._lifecycle.signal_and_started(assistant_message_id)
        stop_event = (
            entry[0] if entry is not None else self._lifecycle.register(assistant_message_id)
        )
        content = ""
        started = time.monotonic()
        # Issue 06：本次 run 的统一阶段时钟与预算控制器（总预算 120s 硬门；
        # 阶段事件经 stage 事件流下发，指标只记录 ID/阶段/毫秒/结果码）。
        budget = RunBudget(run_context.run_id)
        conversation = self._repo.get_conversation(account_id, conversation_id)
        thinking = initial_thinking(
            ChatMode(conversation.mode) if conversation is not None else CHAT_MODE
        )
        web_search_projection: WebSearchProjection | None = None
        arxiv_search_projection: ArxivSearchProjection | None = None
        #: 模型阶段标记：try 前初始化，finally 统一关闭计时（异常路径安全）
        generation_entered = False
        first_token_ms: int | None = None
        try:
            history = self._model_history(
                account_id, conversation_id, until_user_message_id
            )
            mode = ChatMode(conversation.mode) if conversation is not None else CHAT_MODE
            # Issue 28：用户消息携带 SKILL 载荷（bridges-humanizer）时走
            # 内置 SKILL 编排路径——同一真实消息流程（持久化/重试/审计），
            # 过程卡五态经 humanizer SSE 事件下发，终态 done/error 收敛。
            owner_skill = skill_input_from(
                owner_user_message(
                    self._repo.list_messages(account_id, conversation_id),
                    assistant_message_id,
                )
            )
            if owner_skill is not None:
                if self._humanizer is None:
                    finalize_message(
                        self._repo,
                        account_id,
                        assistant_message_id,
                        status=ChatMessageStatus.ERROR,
                        error_code="skill_unavailable",
                        error_message="SKILL 能力暂不可用，请稍后重试。",
                        duration_ms=None,
                        model_id=None,
                        run_lock_id=None,
                        started=started,
                        now=datetime.now(UTC),
                        thinking=failed_thinking(thinking, "skill_unavailable"),
                    )
                    yield StreamEvent(
                        kind="error",
                        error_code="skill_unavailable",
                        error_message="SKILL 能力暂不可用，请稍后重试。",
                    )
                    return
                yield from self._stream_humanizer(
                    account_id,
                    conversation_id,
                    assistant_message_id,
                    owner_skill,
                    run_context,
                    until_user_message_id,
                    use_knowledge_base,
                    budget,
                )
                return
            # Issue 31：用户消息携带图片生成/编辑载荷（前端图片对话框提交）
            # 走图片异步任务编排——创建任务并立即收敛消息（任务卡状态由
            # 后台执行器写回消息投影），绝不阻塞等待云端生成。
            owner_message = owner_user_message(
                self._repo.list_messages(account_id, conversation_id),
                assistant_message_id,
            )
            owner_image = image_payload_from(owner_message)
            if owner_image is not None:
                if self._image is None:
                    finalize_message(
                        self._repo,
                        account_id,
                        assistant_message_id,
                        status=ChatMessageStatus.ERROR,
                        error_code="image_unavailable",
                        error_message="图片能力暂不可用，请稍后重试。",
                        duration_ms=None,
                        model_id=None,
                        run_lock_id=None,
                        started=started,
                        now=datetime.now(UTC),
                        thinking=failed_thinking(thinking, "image_unavailable"),
                    )
                    yield StreamEvent(
                        kind="error",
                        error_code="image_unavailable",
                        error_message="图片能力暂不可用，请稍后重试。",
                    )
                    return
                yield from self._stream_image_request(
                    account_id,
                    conversation_id,
                    assistant_message_id,
                    owner_image,
                )
                return
            # Issue 32：用户消息携带文生视频载荷（前端视频对话框提交）走
            # 视频异步任务编排（Wan 固定绑定）——创建任务并立即收敛消息，
            # 绝不阻塞等待云端生成；失败原因与安全重试边界由任务卡呈现。
            owner_video = video_payload_from(owner_message)
            if owner_video is not None:
                if self._video is None:
                    finalize_message(
                        self._repo,
                        account_id,
                        assistant_message_id,
                        status=ChatMessageStatus.ERROR,
                        error_code="video_unavailable",
                        error_message="视频能力暂不可用，请稍后重试。",
                        duration_ms=None,
                        model_id=None,
                        run_lock_id=None,
                        started=started,
                        now=datetime.now(UTC),
                        thinking=failed_thinking(thinking, "video_unavailable"),
                    )
                    yield StreamEvent(
                        kind="error",
                        error_code="video_unavailable",
                        error_message="视频能力暂不可用，请稍后重试。",
                    )
                    return
                yield from self._stream_video_request(
                    account_id,
                    conversation_id,
                    assistant_message_id,
                    owner_video,
                )
                return
            # Issue 36：用户消息携带 MCP 调用载荷（前端选中插件的调用
            # 对话框提交）走真实 MCP 调用编排——同步执行 invoke（数据
            # 切片/敏感确认/审计由 MCP 服务既有链路完成），结果写入
            # 消息 mcp_call 列；未选中的 MCP 拒绝调用，绝不绕过选择器。
            owner_mcp_call = mcp_call_payload_from(owner_message)
            if owner_mcp_call is not None:
                if self._mcp is None:
                    finalize_message(
                        self._repo,
                        account_id,
                        assistant_message_id,
                        status=ChatMessageStatus.ERROR,
                        error_code="mcp_unavailable",
                        error_message="MCP 服务暂不可用，请稍后重试。",
                        duration_ms=None,
                        model_id=None,
                        run_lock_id=None,
                        started=started,
                        now=datetime.now(UTC),
                        thinking=failed_thinking(thinking, "mcp_unavailable"),
                    )
                    yield StreamEvent(
                        kind="error",
                        error_code="mcp_unavailable",
                        error_message="MCP 服务暂不可用，请稍后重试。",
                    )
                    return
                yield from self._stream_mcp_call(
                    account_id,
                    conversation_id,
                    assistant_message_id,
                    owner_mcp_call,
                )
                return
            # Issue 29：明确生涯规划意图（含前端「生涯规划助手」预填前缀）
            # 走生涯规划编排——同一真实消息流程，六类输出经 career 过程事件
            # 呈现，终态 done/error 收敛；非规划消息继续普通回答。
            # Issue 09：上一轮助手是澄清问题时（career_planning.clarification
            # 非空），本轮用户回复继续走生涯编排——澄清问答是同一规划的
            # 延续，不因回复不含触发词而断裂。
            if (
                owner_message is not None
                and self._career_planner is not None
                and (
                    is_career_intent(owner_message.content)
                    or self._has_pending_career_clarification(
                        account_id, conversation_id, until_user_message_id
                    )
                )
            ):
                yield from self._stream_career_planning(
                    account_id,
                    conversation_id,
                    assistant_message_id,
                    owner_message.content,
                    run_context,
                    until_user_message_id,
                    use_knowledge_base,
                    use_profile,
                    budget,
                )
                return
            retrieval_round: RetrievalRoundProjection | None = None
            teaching_projection: TeachingTurnProjection | None = (
                TeachingTurnProjection.model_validate(current.teaching)
                if current.teaching is not None and mode == ChatMode.STUDY
                else None
            )
            if current.teaching is not None and mode != ChatMode.STUDY:
                # 若用户在流启动后切回日常陪伴，不能留下永久 loading 教学卡，
                # 也不能把教学上下文注入这条日常回答。
                switched_teaching = TeachingTurnProjection.model_validate(current.teaching)
                switched_gate = switched_teaching.evidence_gate.model_copy(
                    update={"search_status": TeachingCardStatus.RECOVERY}
                )
                switched_teaching = switched_teaching.model_copy(
                    update={
                        "status": TeachingCardStatus.RECOVERY,
                        "evidence_gate": switched_gate,
                        "next_prompt": "本轮已切回日常陪伴，教学检查不会强制继续。",
                        "gap_response": "本轮尚未形成教学回答，因为对话已切回日常陪伴。",
                        "can_answer_reliably": False,
                        "can_cancel": False,
                        "can_retry": False,
                        "quiz": None,
                    }
                )
                self._repo.update_message_teaching(
                    account_id,
                    assistant_message_id,
                    switched_teaching.model_dump(mode="json"),
                    datetime.now(UTC),
                )

            # 学习模式先检查三层本地材料，再按证据门结果自动选择公开来源。
            # 普通陪伴模式继续沿用“用户明确要求/时效/核查才联网”的规则。
            if mode == ChatMode.STUDY:
                messages = self._repo.list_messages(account_id, conversation_id)
                owner = owner_user_message(messages, assistant_message_id)
                round_query = owner.content if owner is not None else ""
                # 用户原文用于作答评价与 mission 更新；检索查询可能被替换
                # 为规范概念（见下），两者必须分离。
                user_input = round_query
                previous_turn = previous_teaching_turn(
                    messages, owner.message_id if owner else None
                )
                # Issue 08：先分类意图，再按教学状态机分派；不检索整句意图。
                mission = (
                    previous_turn.mission if previous_turn is not None else None
                )
                intent = self._teaching.classify_intent(round_query, mission)

                # 建立/修改目标：只确认目标与水平，不过证据门、不调模型。
                if intent in {
                    TeachingIntent.ESTABLISH_MISSION,
                    TeachingIntent.MODIFY_MISSION,
                }:
                    teaching_projection = self._teaching.mission_setup(
                        round_query, previous_mission=mission
                    )
                    self._repo.update_message_teaching(
                        account_id,
                        assistant_message_id,
                        teaching_projection.model_dump(mode="json"),
                        datetime.now(UTC),
                    )
                    mission_content = (
                        f"好的，我们一起来学：{teaching_projection.goal}。"
                        f"{teaching_projection.next_prompt}"
                    )
                    self._repo.update_message_content(
                        account_id, assistant_message_id, mission_content, datetime.now(UTC)
                    )
                    finalize_message(
                        self._repo,
                        account_id,
                        assistant_message_id,
                        status=ChatMessageStatus.DONE,
                        error_code=None,
                        error_message=None,
                        duration_ms=None,
                        model_id=None,
                        run_lock_id=None,
                        started=started,
                        now=datetime.now(UTC),
                        thinking=done_thinking(thinking),
                        teaching=teaching_projection.model_dump(mode="json"),
                    )
                    yield StreamEvent(kind="delta", delta=mission_content)
                    yield StreamEvent(kind="done")
                    return

                # mission 确认：解析水平假设与首概念，检索查询用规范主题。
                if mission is not None and mission.stage == TeachingStage.MISSION_SETUP:
                    mission = self._teaching.confirm_mission(round_query, mission)
                    round_query = self._teaching.micro_lesson_query(mission)
                elif mission is not None:
                    # Issue 08：作答/追问/跳过等轮次的检索与公开搜索都用
                    # 当前概念，不拿短答复或整句意图搜索。
                    round_query = self._teaching.micro_lesson_query(mission)

                # Issue 06：教学轮次受总时延预算约束（run 级共享 budget）。
                if budget.enter(RunStage.LOCAL_RETRIEVAL):
                    yield self._stage_event(
                        assistant_message_id, RunStage.LOCAL_RETRIEVAL, "active"
                    )
                    thinking, retrieval_round = self._run_retrieval(
                        account_id,
                        conversation_id,
                        assistant_message_id,
                        until_user_message_id
                        or (owner.message_id if owner is not None else None),
                        round_query,
                        use_knowledge_base=use_knowledge_base,
                        thinking=thinking,
                        stop_event=stop_event,
                    )
                    budget.exit(
                        RunStage.LOCAL_RETRIEVAL,
                        category="layered_retrieval",
                        count=1 if retrieval_round is not None else 0,
                    )
                    yield self._stage_event(
                        assistant_message_id,
                        RunStage.LOCAL_RETRIEVAL,
                        "done",
                        duration_ms=budget.metrics()[-1].duration_ms,
                    )
                else:
                    budget.exit(RunStage.LOCAL_RETRIEVAL)
                    yield self._stage_event(
                        assistant_message_id, RunStage.LOCAL_RETRIEVAL, "skipped"
                    )

                required_search = self._teaching.required_search(round_query, retrieval_round)
                # 超预算跳过搜索时仍保持投影变量可引用（None 表示未执行）。
                web_search_projection: WebSearchProjection | None = None
                arxiv_search_projection: ArxivSearchProjection | None = None

                arxiv_plan = None
                search_plan = None
                public_search_entered = False
                if not stop_event.is_set() and budget.enter(RunStage.PUBLIC_SEARCH):
                    public_search_entered = True
                    yield self._stage_event(
                        assistant_message_id, RunStage.PUBLIC_SEARCH, "active"
                    )
                    # Issue 06 T3：彼此独立且都已确定需要的公开来源并行执行，
                    # 总耗时接近较慢者而非两者之和；结果按固定顺序（先论文
                    # 后公网）处理，顺序确定。
                    calls: list[tuple[str, Callable[[], object] | None]] = []
                    if (
                        required_search.value in {"arxiv", "both"}
                        and self._arxiv_search is not None
                    ):
                        arxiv_plan = self._arxiv_search.plan(round_query, mode, force=True)
                        calls.append(
                            (
                                "arxiv",
                                lambda: self._arxiv_search.search(  # type: ignore[union-attr]
                                    account_id, arxiv_plan, stop_event=stop_event  # type: ignore[arg-type]
                                ),
                            )
                        )
                    if (
                        required_search.value in {"duckduckgo", "both"}
                        and self._web_search is not None
                    ):
                        search_plan = self._web_search.plan(round_query, mode, force=True)
                        calls.append(
                            (
                                "web",
                                lambda: self._web_search.search(  # type: ignore[union-attr]
                                    account_id, search_plan, stop_event=stop_event  # type: ignore[arg-type]
                                ),
                            )
                        )
                    # 搜索阶段墙钟：取来源超时中较小者，且不超出剩余总预算
                    stage_budget = min(
                        EXTERNAL_TIMEOUT_SECONDS["arxiv_search"],
                        EXTERNAL_TIMEOUT_SECONDS["web_search"],
                    )
                    search_results = self._parallel_search(
                        calls,
                        timeout_seconds=min(
                            stage_budget, budget.remaining_ms() / 1000
                        ),
                    )
                    arxiv_result = search_results.get("arxiv")
                    web_result = search_results.get("web")
                    if arxiv_plan is not None and arxiv_result is not _SEARCH_TIMEOUT:
                        if isinstance(arxiv_result, Exception):
                            # Issue 05：意外异常不再折叠成启动失败，投影为
                            # 独立的内部错误码（常规失败由服务层分类）。
                            arxiv_search_projection = ArxivSearchProjection(
                                status=ArxivSearchStatus.ERROR,
                                trigger_reason=arxiv_plan.reason,
                                query_summary=arxiv_plan.query,
                                error_code="arxiv_internal",
                                error_message="arXiv 搜索服务异常，请重试。",
                                can_retry=True,
                            )
                        else:
                            arxiv_search_projection = arxiv_result
                    elif arxiv_plan is not None:
                        # 阶段预算到期：不等待慢来源，按超时降级（主流程继续）
                        arxiv_search_projection = ArxivSearchProjection(
                            status=ArxivSearchStatus.ERROR,
                            trigger_reason=arxiv_plan.reason,
                            query_summary=arxiv_plan.query,
                            error_code="arxiv_timeout",
                            error_message="arXiv 搜索超时，请重试。",
                            can_retry=True,
                        )
                    if search_plan is not None and web_result is not _SEARCH_TIMEOUT:
                        if isinstance(web_result, Exception):
                            web_search_projection = WebSearchProjection(
                                status=WebSearchStatus.ERROR,
                                trigger_reason=search_plan.reason,
                                query_summary=search_plan.query,
                                error_code="web_search_request",
                                error_message="公网搜索请求未完成，请重试。",
                                can_retry=True,
                            )
                        else:
                            web_search_projection = web_result
                    elif search_plan is not None:
                        web_search_projection = WebSearchProjection(
                            status=WebSearchStatus.ERROR,
                            trigger_reason=search_plan.reason,
                            query_summary=search_plan.query,
                            error_code="web_search_timeout",
                            error_message="联网搜索超时，请重试。",
                            can_retry=True,
                        )
                    if arxiv_search_projection is not None:
                        self._repo.update_message_arxiv_search(
                            account_id,
                            assistant_message_id,
                            arxiv_search_projection.model_dump(mode="json"),
                            datetime.now(UTC),
                        )
                        thinking = arxiv_search_thinking(
                            thinking, arxiv_search_projection
                        )
                        if arxiv_search_projection.status == ArxivSearchStatus.CANCELLED:
                            finalize_message(
                                self._repo,
                                account_id,
                                assistant_message_id,
                                status=ChatMessageStatus.STOPPED,
                                error_code=None,
                                error_message=None,
                                duration_ms=None,
                                model_id=None,
                                run_lock_id=None,
                                started=started,
                                now=datetime.now(UTC),
                                thinking=stopped_thinking(thinking),
                                arxiv_search=arxiv_search_projection.model_dump(mode="json"),
                                teaching=(
                                    teaching_projection.model_dump(mode="json")
                                    if teaching_projection is not None
                                    else current.teaching
                                ),
                            )
                            return
                    if web_search_projection is not None:
                        self._repo.update_message_web_search(
                            account_id,
                            assistant_message_id,
                            web_search_projection.model_dump(mode="json"),
                            datetime.now(UTC),
                        )
                        thinking = web_search_thinking(thinking, web_search_projection)
                        if web_search_projection.status == WebSearchStatus.CANCELLED:
                            finalize_message(
                                self._repo,
                                account_id,
                                assistant_message_id,
                                status=ChatMessageStatus.STOPPED,
                                error_code=None,
                                error_message=None,
                                duration_ms=None,
                                model_id=None,
                                run_lock_id=None,
                                started=started,
                                now=datetime.now(UTC),
                                thinking=stopped_thinking(thinking),
                                web_search=web_search_projection.model_dump(mode="json"),
                                teaching=(
                                    teaching_projection.model_dump(mode="json")
                                    if teaching_projection is not None
                                    else current.teaching
                                ),
                            )
                            return
                if public_search_entered:
                    budget.exit(
                        RunStage.PUBLIC_SEARCH,
                        category="web_search",
                        count=sum(
                            1
                            for projection in (
                                web_search_projection,
                                arxiv_search_projection,
                            )
                            if projection is not None
                        ),
                    )
                    yield self._stage_event(
                        assistant_message_id,
                        RunStage.PUBLIC_SEARCH,
                        "done",
                        duration_ms=budget.metrics()[-1].duration_ms,
                    )

                teaching_projection = self._teaching.prepare(
                    round_query,
                    retrieval=retrieval_round,
                    web_search=web_search_projection,
                    arxiv_search=arxiv_search_projection,
                    previous_turn=previous_turn,
                    answer_text=user_input
                    if previous_turn is not None
                    and intent in {TeachingIntent.ANSWER, TeachingIntent.SKIP}
                    else None,
                    answer_message_id=owner.message_id if owner is not None else None,
                    mission=mission,
                    intent=intent,
                )
                self._repo.update_message_teaching(
                    account_id,
                    assistant_message_id,
                    teaching_projection.model_dump(mode="json"),
                    datetime.now(UTC),
                )
                thinking = teaching_thinking(thinking, teaching_projection)

                # 证据门仍未通过时用明确缺口结束本轮，不让模型记忆冒充来源。
                # Issue 06：教学轮次超预算时不再进入模型生成，用明确说明
                # 交付并保留 mission（进度不丢失，可重试继续）。
                if budget.expired() and teaching_projection.can_answer_reliably:
                    teaching_projection = teaching_projection.model_copy(
                        update={
                            "status": TeachingCardStatus.RECOVERY,
                            "can_answer_reliably": False,
                            "gap_response": (
                                "本轮教学受时延预算限制未完成生成；"
                                "教学进度已保留，重试可继续。"
                            ),
                            "next_prompt": "重试可继续同一教学进度。",
                        }
                    )
                    thinking = budget_warning_thinking(thinking)

                if not teaching_projection.can_answer_reliably:
                    safe_response = teaching_projection.gap_response or (
                        "这轮的依据还不够，我先不把不确定内容说成可靠结论。"
                    )
                    self._repo.update_message_content(
                        account_id, assistant_message_id, safe_response, datetime.now(UTC)
                    )
                    finalize_message(
                        self._repo,
                        account_id,
                        assistant_message_id,
                        status=ChatMessageStatus.DONE,
                        error_code=None,
                        error_message=None,
                        duration_ms=None,
                        model_id=None,
                        run_lock_id=None,
                        started=started,
                        now=datetime.now(UTC),
                        thinking=done_thinking(thinking),
                        web_search=(
                            web_search_projection.model_dump(mode="json")
                            if web_search_projection is not None
                            else None
                        ),
                        arxiv_search=(
                            arxiv_search_projection.model_dump(mode="json")
                            if arxiv_search_projection is not None
                            else None
                        ),
                        teaching=teaching_projection.model_dump(mode="json"),
                    )
                    yield StreamEvent(kind="delta", delta=safe_response)
                    yield StreamEvent(kind="done")
                    return

            # 论文型问题优先走固定、只读的 arXiv MCP；公网搜索只接收当前
            # 用户消息经本地规划器脱敏后的最小词组。两个来源失败时都 fail
            # closed（不让模型用记忆伪装成真实结论）。Issue 06 T3：彼此
            # 独立且都已确定需要的来源并行执行，总耗时接近较慢者；结果
            # 按固定顺序（先论文后公网）处理，顺序确定。
            if mode != ChatMode.STUDY:
                messages = self._repo.list_messages(account_id, conversation_id)
                owner = owner_user_message(messages, assistant_message_id)
                round_query = owner.content if owner is not None else ""
                mode_for_plan = (
                    ChatMode(conversation.mode) if conversation is not None else CHAT_MODE
                )
                arxiv_plan = None
                search_plan = None
                public_search_entered = False
                if not stop_event.is_set() and budget.enter(RunStage.PUBLIC_SEARCH):
                    public_search_entered = True
                    yield self._stage_event(
                        assistant_message_id, RunStage.PUBLIC_SEARCH, "active"
                    )
                    calls: list[tuple[str, Callable[[], object] | None]] = []
                    if self._arxiv_search is not None:
                        planned = self._arxiv_search.plan(round_query, mode_for_plan)
                        if planned.should_search:
                            arxiv_plan = planned
                            calls.append(
                                (
                                    "arxiv",
                                    lambda: self._arxiv_search.search(  # type: ignore[union-attr]
                                        account_id, arxiv_plan, stop_event=stop_event  # type: ignore[arg-type]
                                    ),
                                )
                            )
                    if self._web_search is not None:
                        planned = self._web_search.plan(round_query, mode_for_plan)
                        if planned.should_search:
                            search_plan = planned
                            calls.append(
                                (
                                    "web",
                                    lambda: self._web_search.search(  # type: ignore[union-attr]
                                        account_id, search_plan, stop_event=stop_event  # type: ignore[arg-type]
                                    ),
                                )
                            )
                    stage_budget = min(
                        EXTERNAL_TIMEOUT_SECONDS["arxiv_search"],
                        EXTERNAL_TIMEOUT_SECONDS["web_search"],
                    )
                    search_results = self._parallel_search(
                        calls,
                        timeout_seconds=min(
                            stage_budget, budget.remaining_ms() / 1000
                        ),
                    )
                    arxiv_result = search_results.get("arxiv")
                    if arxiv_plan is not None:
                        if arxiv_result is _SEARCH_TIMEOUT:
                            arxiv_search_projection = ArxivSearchProjection(
                                status=ArxivSearchStatus.ERROR,
                                trigger_reason=arxiv_plan.reason,
                                query_summary=arxiv_plan.query,
                                error_code="arxiv_timeout",
                                error_message="arXiv 搜索超时，请重试。",
                                can_retry=True,
                            )
                        elif isinstance(arxiv_result, Exception):
                            arxiv_search_projection = ArxivSearchProjection(
                                status=ArxivSearchStatus.ERROR,
                                trigger_reason=arxiv_plan.reason,
                                query_summary=arxiv_plan.query,
                                error_code="arxiv_startup",
                                error_message="arXiv 搜索服务启动失败，请重试。",
                                can_retry=True,
                            )
                        else:
                            arxiv_search_projection = arxiv_result
                        if arxiv_search_projection is not None:
                            self._repo.update_message_arxiv_search(
                                account_id,
                                assistant_message_id,
                                arxiv_search_projection.model_dump(mode="json"),
                                datetime.now(UTC),
                            )
                            if (
                                arxiv_search_projection.status
                                == ArxivSearchStatus.CANCELLED
                            ):
                                finalize_message(
                                    self._repo,
                                    account_id,
                                    assistant_message_id,
                                    status=ChatMessageStatus.STOPPED,
                                    error_code=None,
                                    error_message=None,
                                    duration_ms=None,
                                    model_id=None,
                                    run_lock_id=None,
                                    started=started,
                                    now=datetime.now(UTC),
                                    thinking=stopped_thinking(thinking),
                                    arxiv_search=arxiv_search_projection.model_dump(
                                        mode="json"
                                    ),
                                )
                                return
                            thinking = arxiv_search_thinking(
                                thinking, arxiv_search_projection
                            )
                            if (
                                arxiv_search_projection.status
                                != ArxivSearchStatus.SUCCESS
                            ):
                                error_code = (
                                    arxiv_search_projection.error_code
                                    or "arxiv_no_results"
                                )
                                error_message = (
                                    arxiv_search_projection.error_message
                                    or user_facing_error(error_code)
                                )
                                finalize_message(
                                    self._repo,
                                    account_id,
                                    assistant_message_id,
                                    status=ChatMessageStatus.ERROR,
                                    error_code=error_code,
                                    error_message=error_message,
                                    duration_ms=None,
                                    model_id=None,
                                    run_lock_id=None,
                                    started=started,
                                    now=datetime.now(UTC),
                                    thinking=failed_thinking(thinking, error_code),
                                    arxiv_search=arxiv_search_projection.model_dump(
                                        mode="json"
                                    ),
                                )
                                yield StreamEvent(
                                    kind="error",
                                    error_code=error_code,
                                    error_message=error_message,
                                )
                                return
                    web_result = search_results.get("web")
                    if search_plan is not None:
                        if web_result is _SEARCH_TIMEOUT:
                            web_search_projection = WebSearchProjection(
                                status=WebSearchStatus.ERROR,
                                trigger_reason=search_plan.reason,
                                query_summary=search_plan.query,
                                error_code="web_search_timeout",
                                error_message="联网搜索超时，请重试。",
                                can_retry=True,
                            )
                        elif isinstance(web_result, Exception):
                            web_search_projection = WebSearchProjection(
                                status=WebSearchStatus.ERROR,
                                trigger_reason=search_plan.reason,
                                query_summary=search_plan.query,
                                error_code="web_search_request",
                                error_message="公网搜索请求未完成，请重试。",
                                can_retry=True,
                            )
                        else:
                            web_search_projection = web_result
                        if web_search_projection is not None:
                            self._repo.update_message_web_search(
                                account_id,
                                assistant_message_id,
                                web_search_projection.model_dump(mode="json"),
                                datetime.now(UTC),
                            )
                            if (
                                web_search_projection.status
                                == WebSearchStatus.CANCELLED
                            ):
                                finalize_message(
                                    self._repo,
                                    account_id,
                                    assistant_message_id,
                                    status=ChatMessageStatus.STOPPED,
                                    error_code=None,
                                    error_message=None,
                                    duration_ms=None,
                                    model_id=None,
                                    run_lock_id=None,
                                    started=started,
                                    now=datetime.now(UTC),
                                    thinking=stopped_thinking(thinking),
                                    web_search=web_search_projection.model_dump(
                                        mode="json"
                                    ),
                                )
                                return
                            thinking = web_search_thinking(
                                thinking, web_search_projection
                            )
                            if web_search_projection.status != WebSearchStatus.SUCCESS:
                                error_code = (
                                    web_search_projection.error_code
                                    or "web_search_no_results"
                                )
                                error_message = (
                                    web_search_projection.error_message
                                    or user_facing_error(error_code)
                                )
                                finalize_message(
                                    self._repo,
                                    account_id,
                                    assistant_message_id,
                                    status=ChatMessageStatus.ERROR,
                                    error_code=error_code,
                                    error_message=error_message,
                                    duration_ms=None,
                                    model_id=None,
                                    run_lock_id=None,
                                    started=started,
                                    now=datetime.now(UTC),
                                    thinking=failed_thinking(thinking, error_code),
                                )
                                yield StreamEvent(
                                    kind="error",
                                    error_code=error_code,
                                    error_message=error_message,
                                )
                                return
                if public_search_entered:
                    budget.exit(
                        RunStage.PUBLIC_SEARCH,
                        category="web_search",
                        count=sum(
                            1
                            for projection in (
                                web_search_projection,
                                arxiv_search_projection,
                            )
                            if projection is not None
                        ),
                    )
                    yield self._stage_event(
                        assistant_message_id,
                        RunStage.PUBLIC_SEARCH,
                        "done",
                        duration_ms=budget.metrics()[-1].duration_ms,
                    )
            if stop_event.is_set():
                finalize_message(
                    self._repo,
                    account_id,
                    assistant_message_id,
                    status=ChatMessageStatus.STOPPED,
                    error_code=None,
                    error_message=None,
                    duration_ms=None,
                    model_id=None,
                    run_lock_id=None,
                    started=started,
                    now=datetime.now(UTC),
                    thinking=stopped_thinking(thinking),
                    web_search=cancelled_web_search(
                        web_search_projection.model_dump(mode="json")
                        if web_search_projection is not None
                        else current.web_search,
                        datetime.now(UTC),
                    ),
                )
                return
            # 生成前执行一轮分层检索：查询文本取所属用户消息正文；检索
            # 结果固化到消息投影（引用展示数据不漂移），并注入最小上下文。
            if retrieval_round is None:
                messages = self._repo.list_messages(account_id, conversation_id)
                owner = owner_user_message(messages, assistant_message_id)
                round_query = owner.content if owner is not None else ""
                if budget.enter(RunStage.LOCAL_RETRIEVAL):
                    yield self._stage_event(
                        assistant_message_id, RunStage.LOCAL_RETRIEVAL, "active"
                    )
                    thinking, retrieval_round = self._run_retrieval(
                        account_id,
                        conversation_id,
                        assistant_message_id,
                        until_user_message_id
                        or (owner.message_id if owner is not None else None),
                        round_query,
                        use_knowledge_base=use_knowledge_base,
                        thinking=thinking,
                        stop_event=stop_event,
                    )
                    budget.exit(
                        RunStage.LOCAL_RETRIEVAL,
                        category="layered_retrieval",
                        count=1 if retrieval_round is not None else 0,
                    )
                    yield self._stage_event(
                        assistant_message_id,
                        RunStage.LOCAL_RETRIEVAL,
                        "done",
                        duration_ms=budget.metrics()[-1].duration_ms,
                    )
                else:
                    budget.exit(RunStage.LOCAL_RETRIEVAL)
                    yield self._stage_event(
                        assistant_message_id, RunStage.LOCAL_RETRIEVAL, "skipped"
                    )
            # Issue 36：本对话启用的插件工具集合（选择器持久化到会话）
            # 以独立 system 块注入——模型只能引用本清单列出的插件能力；
            # 未选择或清除选择后该块不再注入（Verification 3：清除后
            # 不再携带旧上下文）。解析失败静默降级（回答照常）。
            tools_context: str | None = None
            if self._selections is not None:
                try:
                    tools_context = self._selections.resolve(
                        account_id, conversation_id
                    ).context
                except Exception:  # noqa: BLE001 - 辅助路径静默降级
                    tools_context = None
            # Issue 27：最小画像切片编译、注入与「本次上下文说明」披露。
            # 只把当前模式相关、已授权、仍有效且未撤回/冻结的记录注入模型；
            # 用户发送前关闭画像时本轮不编译、不注入，披露与审计都不含
            # 画像内容。编译/披露失败一律静默降级（回答照常，披露 error
            # 态可解释），绝不阻断生成。
            context_note, profile_context, _profile_items = self._compile_profile_slice(
                account_id,
                conversation_id,
                assistant_message_id,
                mode,
                use_profile=use_profile,
                retrieval_round=retrieval_round,
                web_search_projection=web_search_projection,
                arxiv_search_projection=arxiv_search_projection,
            )
            if context_note is not None:
                thinking = context_note_thinking(thinking, context_note)
            payload = assemble_payload(
                history,
                tools_context=tools_context,
                retrieval_round=retrieval_round,
                web_search_projection=web_search_projection,
                arxiv_search_projection=arxiv_search_projection,
                teaching_projection=teaching_projection,
                profile_context=profile_context,
            )
            generation_entered = budget.enter(RunStage.MODEL_GENERATION)
            generation_started = time.monotonic()
            if generation_entered:
                yield self._stage_event(
                    assistant_message_id, RunStage.MODEL_GENERATION, "active"
                )
            else:
                # 预算耗尽（T5）：有草稿带警告交付，无草稿失败可重试——
                # 不得保持永久 running，必须提交明确终态。
                if content:
                    finalize_message(
                        self._repo,
                        account_id,
                        assistant_message_id,
                        status=ChatMessageStatus.DONE,
                        error_code=None,
                        error_message=None,
                        duration_ms=None,
                        model_id=None,
                        run_lock_id=None,
                        started=started,
                        now=datetime.now(UTC),
                        thinking=budget_warning_thinking(thinking),
                    )
                    yield StreamEvent(kind="done")
                else:
                    finalize_message(
                        self._repo,
                        account_id,
                        assistant_message_id,
                        status=ChatMessageStatus.ERROR,
                        error_code="budget_exceeded",
                        error_message=user_facing_error("budget_exceeded"),
                        duration_ms=None,
                        model_id=None,
                        run_lock_id=None,
                        started=started,
                        now=datetime.now(UTC),
                        thinking=failed_thinking(thinking, "budget_exceeded"),
                    )
                    yield StreamEvent(
                        kind="error",
                        error_code="budget_exceeded",
                        error_message=user_facing_error("budget_exceeded"),
                    )
                return
            for event in gateway.stream(
                CHAT_CAPABILITY_NAME, CHAT_CAPABILITY_VERSION, run_context, payload
            ):
                if stop_event.is_set():
                    finalize_message(
                        self._repo,
                        account_id,
                        assistant_message_id,
                        status=ChatMessageStatus.STOPPED,
                        error_code=None,
                        error_message=None,
                        duration_ms=None,
                        model_id=None,
                        run_lock_id=None,
                        started=started,
                        now=datetime.now(UTC),
                        thinking=stopped_thinking(thinking),
                        web_search=cancelled_web_search(
                            web_search_projection.model_dump(mode="json")
                            if web_search_projection is not None
                            else current.web_search,
                            datetime.now(UTC),
                        ),
                    )
                    return
                if event.kind == "delta":
                    if first_token_ms is None:
                        first_token_ms = max(
                            1, int((time.monotonic() - generation_started) * 1000)
                        )
                    content += event.delta
                    self._repo.update_message_content(
                        account_id, assistant_message_id, content, datetime.now(UTC)
                    )
                    self._lifecycle.touch(assistant_message_id)
                    yield event
                    if budget.expired():
                        # 预算到期：停止后续模型输出，按草稿交付（T5）
                        budget.mark_exhausted()
                        break
                elif event.kind == "error":
                    self._persist_lock(account_id, event.lock)
                    finalize_message(
                        self._repo,
                        account_id,
                        assistant_message_id,
                        status=ChatMessageStatus.ERROR,
                        error_code=event.error_code,
                        error_message=user_facing_error(event.error_code, event.error_message),
                        duration_ms=None,
                        model_id=self._lock_model_id(event.lock),
                        run_lock_id=self._lock_id(event.lock),
                        started=started,
                        now=datetime.now(UTC),
                        thinking=failed_thinking(thinking, event.error_code),
                    )
                    yield event
                    return
                elif event.kind == "done":
                    self._persist_lock(account_id, event.lock)
                    # 模型阶段关闭事件（在质量检查前发出，duration 只含
                    # 模型流式本身；脱敏首 token 指标随事件持久化，供本地
                    # 性能摘要聚合；错误路径由 finally 关闭计时）
                    budget.exit(
                        RunStage.MODEL_GENERATION,
                        category="qwen_text_chat",
                        first_token_ms=first_token_ms,
                    )
                    yield self._stage_event(
                        assistant_message_id,
                        RunStage.MODEL_GENERATION,
                        "done",
                        duration_ms=budget.metrics()[-1].duration_ms,
                        first_token_ms=first_token_ms,
                    )
                    # 质量检查阶段（Issue 06）：联网/论文引用核验
                    quality_entered = budget.enter(RunStage.QUALITY_CHECK)
                    if quality_entered:
                        yield self._stage_event(
                            assistant_message_id, RunStage.QUALITY_CHECK, "active"
                        )
                    if web_search_projection is not None:
                        citation_error = web_search_citation_error(
                            content, len(web_search_projection.results)
                        )
                        if citation_error is not None:
                            invalid_web_projection = web_search_projection.model_copy(
                                update={
                                    "status": WebSearchStatus.ERROR,
                                    "error_code": "web_search_citation_invalid",
                                    "error_message": citation_error,
                                    "can_retry": True,
                                    "can_cancel": False,
                                }
                            )
                            self._repo.update_message_web_search(
                                account_id,
                                assistant_message_id,
                                invalid_web_projection.model_dump(mode="json"),
                                datetime.now(UTC),
                            )
                            finalize_message(
                                self._repo,
                                account_id,
                                assistant_message_id,
                                status=ChatMessageStatus.ERROR,
                                error_code="web_search_citation_invalid",
                                error_message=citation_error,
                                duration_ms=None,
                                model_id=self._lock_model_id(event.lock),
                                run_lock_id=self._lock_id(event.lock),
                                started=started,
                                now=datetime.now(UTC),
                                thinking=failed_thinking(
                                    thinking, "web_search_citation_invalid"
                                ),
                                web_search=invalid_web_projection.model_dump(mode="json"),
                            )
                            if quality_entered:
                                budget.exit(
                                    RunStage.QUALITY_CHECK,
                                    result=RESULT_FAILED,
                                    category="citation_check",
                                )
                            yield StreamEvent(
                                kind="error",
                                error_code="web_search_citation_invalid",
                                error_message=citation_error,
                                lock=event.lock,
                            )
                            return
                    if arxiv_search_projection is not None:
                        citation_error = arxiv_citation_error(
                            content, arxiv_search_projection
                        )
                        if citation_error is not None:
                            invalid_arxiv_projection = arxiv_search_projection.model_copy(
                                update={
                                    "status": ArxivSearchStatus.ERROR,
                                    "error_code": "arxiv_citation_invalid",
                                    "error_message": citation_error,
                                    "can_retry": True,
                                    "can_cancel": False,
                                }
                            )
                            self._repo.update_message_arxiv_search(
                                account_id,
                                assistant_message_id,
                                invalid_arxiv_projection.model_dump(mode="json"),
                                datetime.now(UTC),
                            )
                            finalize_message(
                                self._repo,
                                account_id,
                                assistant_message_id,
                                status=ChatMessageStatus.ERROR,
                                error_code="arxiv_citation_invalid",
                                error_message=citation_error,
                                duration_ms=None,
                                model_id=self._lock_model_id(event.lock),
                                run_lock_id=self._lock_id(event.lock),
                                started=started,
                                now=datetime.now(UTC),
                                thinking=failed_thinking(
                                    thinking, "arxiv_citation_invalid"
                                ),
                                arxiv_search=invalid_arxiv_projection.model_dump(mode="json"),
                            )
                            if quality_entered:
                                budget.exit(
                                    RunStage.QUALITY_CHECK,
                                    result=RESULT_FAILED,
                                    category="citation_check",
                                )
                            yield StreamEvent(
                                kind="error",
                                error_code="arxiv_citation_invalid",
                                error_message=citation_error,
                                lock=event.lock,
                            )
                            return
                    if quality_entered:
                        budget.exit(
                            RunStage.QUALITY_CHECK,
                            category="citation_check",
                            count=(
                                1
                                if web_search_projection is not None
                                or arxiv_search_projection is not None
                                else 0
                            ),
                        )
                        yield self._stage_event(
                            assistant_message_id,
                            RunStage.QUALITY_CHECK,
                            "done",
                            duration_ms=budget.metrics()[-1].duration_ms,
                        )
                    # 收尾阶段（Issue 06）：结果落库与终态提交（瞬时）
                    if budget.enter(RunStage.FINALIZING):
                        yield self._stage_event(
                            assistant_message_id, RunStage.FINALIZING, "active"
                        )
                    budget.exit(RunStage.FINALIZING)
                    finalize_message(
                        self._repo,
                        account_id,
                        assistant_message_id,
                        status=ChatMessageStatus.DONE,
                        error_code=None,
                        error_message=None,
                        duration_ms=None,
                        model_id=self._lock_model_id(event.lock),
                        run_lock_id=self._lock_id(event.lock),
                        started=started,
                        now=datetime.now(UTC),
                        thinking=done_thinking(thinking),
                    )
                    yield event
                    return
            if budget.expired():
                # 预算到期收敛（T5）：已接收草稿带警告交付，绝不永久 running。
                # 模型阶段计时由 finally 统一关闭（结果码按预算状态推断）。
                finalize_message(
                    self._repo,
                    account_id,
                    assistant_message_id,
                    status=ChatMessageStatus.DONE,
                    error_code=None,
                    error_message=None,
                    duration_ms=None,
                    model_id=None,
                    run_lock_id=None,
                    started=started,
                    now=datetime.now(UTC),
                    thinking=budget_warning_thinking(thinking),
                )
                yield self._stage_event(
                    assistant_message_id,
                    RunStage.MODEL_GENERATION,
                    "timeout",
                    duration_ms=max(
                        1, int((time.monotonic() - generation_started) * 1000)
                    ),
                    first_token_ms=first_token_ms,
                )
                yield StreamEvent(kind="done")
                return
        except GeneratorExit:
            # 客户端断开：收敛为可重试错误，保留已接收正文与已完成摘要。
            finalize_message(
                self._repo,
                account_id,
                assistant_message_id,
                status=ChatMessageStatus.ERROR,
                error_code="stream_interrupted",
                error_message=STREAM_INTERRUPTED_MESSAGE,
                duration_ms=None,
                model_id=None,
                run_lock_id=None,
                started=started,
                now=datetime.now(UTC),
                thinking=failed_thinking(thinking, "stream_interrupted"),
            )
            raise
        except Exception:  # noqa: BLE001 - 未分类异常也须收敛，绝不滞留 streaming 僵尸
            # 内部异常（如落库失败）：收敛为可重试错误并产出 error 事件，
            # 让 SSE 契约始终以终态事件结束，不向用户泄漏内部细节。
            finalize_message(
                self._repo,
                account_id,
                assistant_message_id,
                status=ChatMessageStatus.ERROR,
                error_code="internal_error",
                error_message="生成过程出现内部错误，请重试。",
                duration_ms=None,
                model_id=None,
                run_lock_id=None,
                started=started,
                now=datetime.now(UTC),
                thinking=failed_thinking(thinking, "internal_error"),
            )
            yield StreamEvent(
                kind="error",
                error_code="internal_error",
                error_message="生成过程出现内部错误，请重试。",
            )
            return
        finally:
            # 模型阶段统一关闭（Issue 06）：结果码按预算状态与消息终态推断，
            # 仅记录脱敏指标；stage 关闭事件在特殊路径（预算到期）已发。
            if generation_entered:
                status_map = {
                    ChatMessageStatus.DONE.value: RESULT_OK,
                    ChatMessageStatus.ERROR.value: RESULT_FAILED,
                    ChatMessageStatus.STOPPED.value: RESULT_TIMEOUT,
                }
                message = self._repo.get_message(account_id, assistant_message_id)
                result = (
                    RESULT_TIMEOUT
                    if budget.expired()
                    else status_map.get(
                        message.status.value if message is not None else None,
                        RESULT_FAILED,
                    )
                )
                budget.exit(
                    RunStage.MODEL_GENERATION,
                    result=result,
                    category="qwen_text_chat",
                    first_token_ms=first_token_ms,
                )
            self._lifecycle.unregister(assistant_message_id)

    # ------------------------------------------------------------------
    # 阶段事件与并行公开搜索（Issue 06：统一阶段时钟/预算埋点）
    # ------------------------------------------------------------------

    def _stage_event(
        self,
        assistant_message_id: str,
        stage: RunStage,
        status: Literal["active", "done", "timeout", "failed", "skipped"],
        *,
        duration_ms: int | None = None,
        first_token_ms: int | None = None,
    ) -> StreamEvent:
        """构造 stage 事件（脱敏：只带阶段/状态/毫秒，绝无正文）。"""
        return StreamEvent(
            kind="stage",
            stage=ChatStreamStageData(
                message_id=assistant_message_id,
                stage=stage.value,
                status=status,
                duration_ms=duration_ms,
                first_token_ms=first_token_ms,
            ),
        )

    def _parallel_search(
        self,
        calls: list[tuple[str, Callable[[], object] | None]],
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """并行执行彼此独立且都已确定需要的公开搜索（Issue 06 T3）。

        ``calls`` 为 ``(来源名, 可调用)`` 列表，全部经线程池提交（单源
        同样受阶段墙钟约束，避免慢来源阻塞主流程）。结果按原列表顺序
        返回（顺序确定）；超时未完成或抛异常的结果以 ``_SEARCH_TIMEOUT``
        /异常对象占位，由调用方按既有失败语义降级，
        后台线程自灭（搜索客户端自带超时），主流程绝不等待超预算来源。
        """
        if not calls:
            return {}
        executor = ThreadPoolExecutor(
            max_workers=len(calls), thread_name_prefix="public-search"
        )
        try:
            futures = {
                name: executor.submit(call) if call is not None else None
                for name, call in calls
            }
            done, _ = _wait_timeout(
                [f for f in futures.values() if f is not None], timeout_seconds
            )
            results: dict[str, Any] = {}
            for name, future in futures.items():
                if future is None:
                    results[name] = None
                elif future not in done:
                    results[name] = _SEARCH_TIMEOUT
                else:
                    try:
                        results[name] = future.result()
                    except Exception as exc:  # noqa: BLE001 - 调用方按异常降级
                        results[name] = exc
            return results
        finally:
            # 不等待后台线程（超时来源自灭）；工作线程由进程退出回收
            executor.shutdown(wait=False)

    # ------------------------------------------------------------------
    # 检索（全部编排路径的单一实现）
    # ------------------------------------------------------------------

    def _run_retrieval(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        until_user_message_id: str | None,
        round_query: str,
        *,
        use_knowledge_base: bool,
        thinking: ChatThinkingSummary,
        stop_event: threading.Event,
    ) -> tuple[ChatThinkingSummary, RetrievalRoundProjection | None]:
        """一轮分层检索：全部编排路径共用这一个实现。

        未挂载检索服务或已收到停止信号时返回 ``(thinking, None)``——
        调用方无须重复「None 检查 + 停止检查」，行为与既有各路径一致。
        """
        if self._retrieval is None or stop_event.is_set():
            return thinking, None
        retrieval_round = self._retrieval.run_round(
            account_id,
            conversation_id,
            assistant_message_id,
            until_user_message_id,
            round_query,
            use_knowledge_base=use_knowledge_base,
        )
        if retrieval_round is None:
            return thinking, None
        return retrieval_thinking(thinking, retrieval_round), retrieval_round

    # ------------------------------------------------------------------
    # Issue 28：内置 SKILL 编排路径（bridges-humanizer）
    # ------------------------------------------------------------------

    def _stream_humanizer(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        skill_input: HumanizerSkillInput,
        run_context: RunContextEnvelope,
        until_user_message_id: str | None,
        use_knowledge_base: bool,
        budget: RunBudget,
    ) -> Iterator[StreamEvent]:
        """SKILL 编排：证据合同检索 → 过程事件 → 终态收敛（done/error）。

        与普通生成共用停止信号与终态收敛语义：停止/失败绝不悬挂，重试
        新建尝试沿用原用户消息上的任务契约（输入不丢失）。
        """
        started = time.monotonic()
        if self._humanizer is None:
            return
        entry = self._lifecycle.signal_and_started(assistant_message_id)
        stop_event = (
            entry[0] if entry is not None else self._lifecycle.register(assistant_message_id)
        )
        conversation = self._repo.get_conversation(account_id, conversation_id)
        thinking = initial_thinking(
            ChatMode(conversation.mode) if conversation is not None else CHAT_MODE
        )
        retrieval_round: RetrievalRoundProjection | None = None
        messages = self._repo.list_messages(account_id, conversation_id)
        owner = owner_user_message(messages, assistant_message_id)
        round_query = owner.content if owner is not None else ""
        # Issue 07：改写路径只以用户粘贴/附件为原文，默认不检索全局知识库
        # （知识库中不相关图片等材料绝不进入证据合同）；用户显式开启「补充
        # 检索全局知识库」时检索轮次进入改写证据合同（仅作补充，不替代原文）。
        # 注意：改写默认关闭知识库由调用方传 use_knowledge_base=False
        # （retrieval 层只关知识库来源），检索轮次本身仍须存在——附件层
        # 披露是 Issue 04 的绑定契约，跳过整个阶段会使消息投影丢失检索
        # 披露（retrieval=null）。
        if budget.enter(RunStage.LOCAL_RETRIEVAL):
            yield self._stage_event(
                assistant_message_id, RunStage.LOCAL_RETRIEVAL, "active"
            )
            thinking, retrieval_round = self._run_retrieval(
                account_id,
                conversation_id,
                assistant_message_id,
                until_user_message_id or (owner.message_id if owner is not None else None),
                round_query,
                # 改写路径默认不检索全局知识库（issue 07 意图）由调用方
                # 传值保证（改写默认 false、显式开启才 true），此处原样传递。
                use_knowledge_base=use_knowledge_base,
                thinking=thinking,
                stop_event=stop_event,
            )
            budget.exit(
                RunStage.LOCAL_RETRIEVAL,
                category="layered_retrieval",
                count=1 if retrieval_round is not None else 0,
            )
            yield self._stage_event(
                assistant_message_id,
                RunStage.LOCAL_RETRIEVAL,
                "done",
                duration_ms=budget.metrics()[-1].duration_ms,
            )
        else:
            budget.exit(RunStage.LOCAL_RETRIEVAL)
            yield self._stage_event(
                assistant_message_id, RunStage.LOCAL_RETRIEVAL, "skipped"
            )
        # Spec AC8：可引用来源经本地/联网证据合同呈现。联网与 arXiv 按
        # 既有触发器（明确要求/时效/核查等）决定是否执行；结果投影传入
        # 人味化编排，只可引用清单内材料，新增引用一律标记未核实。
        web_search_projection: WebSearchProjection | None = None
        arxiv_search_projection: ArxivSearchProjection | None = None
        public_search_entered = False
        if not stop_event.is_set() and budget.enter(RunStage.PUBLIC_SEARCH):
            public_search_entered = True
            yield self._stage_event(
                assistant_message_id, RunStage.PUBLIC_SEARCH, "active"
            )
            messages = self._repo.list_messages(account_id, conversation_id)
            owner = owner_user_message(messages, assistant_message_id)
            round_query = owner.content if owner is not None else ""
            mode_for_plan = (
                ChatMode(conversation.mode) if conversation is not None else CHAT_MODE
            )
            # Issue 06 T3：彼此独立且都已确定需要的公开来源并行执行，
            # 总耗时接近较慢者；顺序处理（先公网后论文）保持确定。
            calls: list[tuple[str, Callable[[], object] | None]] = []
            search_plan = None
            arxiv_plan = None
            if self._web_search is not None:
                planned = self._web_search.plan(round_query, mode_for_plan)
                if planned.should_search:
                    search_plan = planned
                    calls.append(
                        (
                            "web",
                            lambda plan=planned: self._web_search.search(  # type: ignore[union-attr]
                                account_id, plan, stop_event=stop_event  # type: ignore[arg-type]
                            ),
                        )
                    )
            if self._arxiv_search is not None:
                planned = self._arxiv_search.plan(round_query, mode_for_plan)
                if planned.should_search:
                    arxiv_plan = planned
                    calls.append(
                        (
                            "arxiv",
                            lambda plan=planned: self._arxiv_search.search(  # type: ignore[union-attr]
                                account_id, plan, stop_event=stop_event  # type: ignore[arg-type]
                            ),
                        )
                    )
            stage_budget = min(
                EXTERNAL_TIMEOUT_SECONDS["arxiv_search"],
                EXTERNAL_TIMEOUT_SECONDS["web_search"],
            )
            search_results = self._parallel_search(
                calls,
                timeout_seconds=min(stage_budget, budget.remaining_ms() / 1000),
            )
            web_result = search_results.get("web")
            if (
                search_plan is not None
                and web_result is not _SEARCH_TIMEOUT
                and not isinstance(web_result, Exception)
            ):
                web_search_projection = web_result
            arxiv_result = search_results.get("arxiv")
            if (
                arxiv_plan is not None
                and arxiv_result is not _SEARCH_TIMEOUT
                and not isinstance(arxiv_result, Exception)
            ):
                arxiv_search_projection = arxiv_result
            if web_search_projection is not None:
                self._repo.update_message_web_search(
                    account_id,
                    assistant_message_id,
                    web_search_projection.model_dump(mode="json"),
                    datetime.now(UTC),
                )
                thinking = web_search_thinking(thinking, web_search_projection)
            if arxiv_search_projection is not None:
                self._repo.update_message_arxiv_search(
                    account_id,
                    assistant_message_id,
                    arxiv_search_projection.model_dump(mode="json"),
                    datetime.now(UTC),
                )
                thinking = arxiv_search_thinking(thinking, arxiv_search_projection)
        if public_search_entered:
            budget.exit(
                RunStage.PUBLIC_SEARCH,
                category="web_search",
                count=sum(
                    1
                    for projection in (
                        web_search_projection,
                        arxiv_search_projection,
                    )
                    if projection is not None
                ),
            )
            yield self._stage_event(
                assistant_message_id,
                RunStage.PUBLIC_SEARCH,
                "done",
                duration_ms=budget.metrics()[-1].duration_ms,
            )
        try:
            generation_entered = budget.enter(RunStage.MODEL_GENERATION)
            if generation_entered:
                yield self._stage_event(
                    assistant_message_id, RunStage.MODEL_GENERATION, "active"
                )
            else:
                # 预算耗尽：结构化技能无可交付草稿，直接失败并允许重试
                finalize_message(
                    self._repo,
                    account_id,
                    assistant_message_id,
                    status=ChatMessageStatus.ERROR,
                    error_code="budget_exceeded",
                    error_message=user_facing_error("budget_exceeded"),
                    duration_ms=None,
                    model_id=None,
                    run_lock_id=None,
                    started=started,
                    now=datetime.now(UTC),
                    thinking=failed_thinking(thinking, "budget_exceeded"),
                )
                yield StreamEvent(
                    kind="error",
                    error_code="budget_exceeded",
                    error_message=user_facing_error("budget_exceeded"),
                )
                return
            # 质量检查阶段（Issue 06）：技能编排内含生成与确定性复核
            quality_entered = budget.enter(RunStage.QUALITY_CHECK)
            if quality_entered:
                yield self._stage_event(
                    assistant_message_id, RunStage.QUALITY_CHECK, "active"
                )
            for run_event in self._humanizer.run_task(
                account_id,
                conversation_id,
                assistant_message_id,
                skill_input,
                run_context,
                retrieval_round=retrieval_round,
                web_search_projection=web_search_projection,
                arxiv_search_projection=arxiv_search_projection,
                budget=budget,
            ):
                if budget.expired():
                    # 预算检查点：技能循环内不得绕开总预算（审查修复 C）
                    budget.mark_exhausted()
                    if quality_entered:
                        budget.exit(
                            RunStage.QUALITY_CHECK,
                            result=RESULT_FAILED,
                            category="qwen_structured_output",
                        )
                    finalize_message(
                        self._repo,
                        account_id,
                        assistant_message_id,
                        status=ChatMessageStatus.ERROR,
                        error_code="budget_exceeded",
                        error_message=user_facing_error("budget_exceeded"),
                        duration_ms=None,
                        model_id=None,
                        run_lock_id=None,
                        started=started,
                        now=datetime.now(UTC),
                        thinking=failed_thinking(thinking, "budget_exceeded"),
                    )
                    yield StreamEvent(
                        kind="error",
                        error_code="budget_exceeded",
                        error_message=user_facing_error("budget_exceeded"),
                    )
                    return
                if stop_event.is_set():
                    finalize_message(
                        self._repo,
                        account_id,
                        assistant_message_id,
                        status=ChatMessageStatus.STOPPED,
                        error_code=None,
                        error_message=None,
                        duration_ms=None,
                        model_id=None,
                        run_lock_id=None,
                        started=started,
                        now=datetime.now(UTC),
                        thinking=stopped_thinking(thinking),
                    )
                    return
                if run_event.kind == "process":
                    if run_event.state is None:
                        continue
                    yield StreamEvent(
                        kind="humanizer",
                        humanizer=ChatStreamHumanizerData(
                            message_id=assistant_message_id,
                            state=run_event.state,
                            step_label=run_event.step_label,
                            detail=run_event.detail,
                            retryable=run_event.retryable,
                            progress_steps=run_event.progress_steps,
                        ),
                    )
                    continue
                if run_event.kind == "draft":
                    # Issue 07：模型产出正文后立即持久化草稿——刷新/切会话
                    # 后仍可见；随后的软门修复与复核只更新状态，不删草稿。
                    if run_event.draft_text:
                        self._repo.update_message_content(
                            account_id,
                            assistant_message_id,
                            run_event.draft_text,
                            datetime.now(UTC),
                        )
                    continue
                result = run_event.result
                if result is None:
                    continue
                now = datetime.now(UTC)
                self._repo.update_message_humanizer(
                    account_id,
                    assistant_message_id,
                    result.model_dump(mode="json"),
                    now,
                )
                if result.status == HumanizerResultStatus.ERROR:
                    finalize_message(
                        self._repo,
                        account_id,
                        assistant_message_id,
                        status=ChatMessageStatus.ERROR,
                        error_code=result.error_code,
                        error_message=result.error_message,
                        duration_ms=None,
                        model_id=None,
                        run_lock_id=None,
                        started=started,
                        now=now,
                        thinking=failed_thinking(thinking, result.error_code),
                    )
                    yield StreamEvent(
                        kind="error",
                        error_code=result.error_code or "humanizer_failed",
                        error_message=result.error_message
                        or "人味化任务未完成，请重试。",
                    )
                    return
                final_text = result.output.final_text if result.output else ""
                self._repo.update_message_content(
                    account_id, assistant_message_id, final_text, now
                )
                if quality_entered:
                    budget.exit(
                        RunStage.QUALITY_CHECK,
                        category="qwen_structured_output",
                        count=1,
                    )
                    yield self._stage_event(
                        assistant_message_id,
                        RunStage.QUALITY_CHECK,
                        "done",
                        duration_ms=budget.metrics()[-1].duration_ms,
                    )
                # 收尾阶段（Issue 06）：结果落库与终态提交
                if budget.enter(RunStage.FINALIZING):
                    yield self._stage_event(
                        assistant_message_id, RunStage.FINALIZING, "active"
                    )
                budget.exit(RunStage.FINALIZING)
                finalize_message(
                    self._repo,
                    account_id,
                    assistant_message_id,
                    status=ChatMessageStatus.DONE,
                    error_code=None,
                    error_message=None,
                    duration_ms=None,
                    model_id=None,
                    run_lock_id=None,
                    started=started,
                    now=now,
                    thinking=done_thinking(thinking),
                )
                yield StreamEvent(kind="done")
                return
        except Exception:  # noqa: BLE001 - 编排意外异常收敛为可重试错误
            finalize_message(
                self._repo,
                account_id,
                assistant_message_id,
                status=ChatMessageStatus.ERROR,
                error_code="humanizer_failed",
                error_message="人味化任务执行异常，请重试（输入已保留）。",
                duration_ms=None,
                model_id=None,
                run_lock_id=None,
                started=started,
                now=datetime.now(UTC),
                thinking=failed_thinking(thinking, "humanizer_failed"),
            )
            yield StreamEvent(
                kind="error",
                error_code="humanizer_failed",
                error_message="人味化任务执行异常，请重试（输入已保留）。",
            )
        finally:
            # 模型阶段脱敏计时（终态事件已发出，stage 关闭不额外发事件）
            if generation_entered:
                budget.exit(
                    RunStage.MODEL_GENERATION, category="qwen_structured_output"
                )

    # ------------------------------------------------------------------
    # Issue 29：生涯规划编排路径
    # ------------------------------------------------------------------

    def _stream_career_planning(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        intent: str,
        run_context: RunContextEnvelope,
        until_user_message_id: str | None,
        use_knowledge_base: bool,
        use_profile: bool,
        budget: RunBudget,
    ) -> Iterator[StreamEvent]:
        """生涯规划编排（Issue 29）：证据获取 → 过程事件 → 终态收敛。

        复用普通生成与 humanizer 编排的证据合同语义：本地检索 → 明确联网/
        时效触发 DuckDuckGo/arXiv → 最小画像切片编译与披露（发送前关闭时
        本轮不编译、不注入、披露 off 态）→ 生涯规划服务生成六类输出 →
        确定性复核 → 结果投影落库。停止/失败绝不悬挂，重试新建尝试沿用
        同一用户消息（意图与输入不丢失）。
        """
        started = time.monotonic()
        if self._career_planner is None:
            return
        entry = self._lifecycle.signal_and_started(assistant_message_id)
        stop_event = (
            entry[0]
            if entry is not None
            else self._lifecycle.register(assistant_message_id)
        )
        conversation = self._repo.get_conversation(account_id, conversation_id)
        mode = ChatMode(conversation.mode) if conversation is not None else CHAT_MODE
        thinking = initial_thinking(mode)
        # Issue 09 intake：信息不足时 2 秒内返回一个关键澄清问题，不启动
        # 检索与完整规划生成（确定性判断，无模型调用）。
        assessment = assess_intake(intent)
        if not assessment.enough:
            yield from self._stream_career_clarification(
                account_id,
                conversation_id,
                assistant_message_id,
                intent,
                mode,
                use_profile=use_profile,
                question=assessment.question
                or "请补充你的目标方向与当前阶段，我好为你规划。",
                thinking=thinking,
                started=started,
            )
            return
        retrieval_round: RetrievalRoundProjection | None = None
        web_search_projection: WebSearchProjection | None = None
        arxiv_search_projection: ArxivSearchProjection | None = None
        # 检索与联网证据获取（复用 humanizer 分支的同一套证据合同）。
        if budget.enter(RunStage.LOCAL_RETRIEVAL):
            yield self._stage_event(
                assistant_message_id, RunStage.LOCAL_RETRIEVAL, "active"
            )
            thinking, retrieval_round = self._run_retrieval(
                account_id,
                conversation_id,
                assistant_message_id,
                until_user_message_id,
                intent,
                use_knowledge_base=use_knowledge_base,
                thinking=thinking,
                stop_event=stop_event,
            )
            budget.exit(
                RunStage.LOCAL_RETRIEVAL,
                category="layered_retrieval",
                count=1 if retrieval_round is not None else 0,
            )
            yield self._stage_event(
                assistant_message_id,
                RunStage.LOCAL_RETRIEVAL,
                "done",
                duration_ms=budget.metrics()[-1].duration_ms,
            )
        else:
            budget.exit(RunStage.LOCAL_RETRIEVAL)
            yield self._stage_event(
                assistant_message_id, RunStage.LOCAL_RETRIEVAL, "skipped"
            )
        public_search_entered = False
        if not stop_event.is_set() and budget.enter(RunStage.PUBLIC_SEARCH):
            public_search_entered = True
            yield self._stage_event(
                assistant_message_id, RunStage.PUBLIC_SEARCH, "active"
            )
            # Issue 06 T3：彼此独立且都已确定需要的公开来源并行执行，
            # 总耗时接近较慢者；顺序处理（先公网后论文）保持确定。
            calls: list[tuple[str, Callable[[], object] | None]] = []
            search_plan = None
            arxiv_plan = None
            if self._web_search is not None:
                planned = self._web_search.plan(intent, mode)
                if planned.should_search:
                    search_plan = planned
                    calls.append(
                        (
                            "web",
                            lambda plan=planned: self._web_search.search(  # type: ignore[union-attr]
                                account_id, plan, stop_event=stop_event  # type: ignore[arg-type]
                            ),
                        )
                    )
            if self._arxiv_search is not None:
                planned = self._arxiv_search.plan(intent, mode)
                if planned.should_search:
                    arxiv_plan = planned
                    calls.append(
                        (
                            "arxiv",
                            lambda plan=planned: self._arxiv_search.search(  # type: ignore[union-attr]
                                account_id, plan, stop_event=stop_event  # type: ignore[arg-type]
                            ),
                        )
                    )
            stage_budget = min(
                EXTERNAL_TIMEOUT_SECONDS["arxiv_search"],
                EXTERNAL_TIMEOUT_SECONDS["web_search"],
            )
            search_results = self._parallel_search(
                calls,
                timeout_seconds=min(stage_budget, budget.remaining_ms() / 1000),
            )
            web_result = search_results.get("web")
            if (
                search_plan is not None
                and web_result is not _SEARCH_TIMEOUT
                and not isinstance(web_result, Exception)
            ):
                web_search_projection = web_result
            arxiv_result = search_results.get("arxiv")
            if (
                arxiv_plan is not None
                and arxiv_result is not _SEARCH_TIMEOUT
                and not isinstance(arxiv_result, Exception)
            ):
                arxiv_search_projection = arxiv_result
            if web_search_projection is not None:
                self._repo.update_message_web_search(
                    account_id,
                    assistant_message_id,
                    web_search_projection.model_dump(mode="json"),
                    datetime.now(UTC),
                )
                thinking = web_search_thinking(thinking, web_search_projection)
            if arxiv_search_projection is not None:
                self._repo.update_message_arxiv_search(
                    account_id,
                    assistant_message_id,
                    arxiv_search_projection.model_dump(mode="json"),
                    datetime.now(UTC),
                )
                thinking = arxiv_search_thinking(
                    thinking, arxiv_search_projection
                )
        if public_search_entered:
            budget.exit(
                RunStage.PUBLIC_SEARCH,
                category="web_search",
                count=sum(
                    1
                    for projection in (
                        web_search_projection,
                        arxiv_search_projection,
                    )
                    if projection is not None
                ),
            )
            yield self._stage_event(
                assistant_message_id,
                RunStage.PUBLIC_SEARCH,
                "done",
                duration_ms=budget.metrics()[-1].duration_ms,
            )
        # 最小画像切片编译与「本次上下文说明」披露：模型提示词由编排服务
        # 自行组装（这里只复用编译/披露/审计，切片上下文不在本路径注入）。
        context_note, _profile_context, profile_items = self._compile_profile_slice(
            account_id,
            conversation_id,
            assistant_message_id,
            mode,
            use_profile=use_profile,
            retrieval_round=retrieval_round,
            web_search_projection=web_search_projection,
            arxiv_search_projection=arxiv_search_projection,
        )
        if context_note is not None:
            thinking = context_note_thinking(thinking, context_note)
        profile_used = (
            context_note.state == ContextNoteState.READY if context_note else False
        )
        try:
            generation_entered = budget.enter(RunStage.MODEL_GENERATION)
            if generation_entered:
                yield self._stage_event(
                    assistant_message_id, RunStage.MODEL_GENERATION, "active"
                )
            else:
                # 预算耗尽：结构化技能无可交付草稿，直接失败并允许重试
                finalize_message(
                    self._repo,
                    account_id,
                    assistant_message_id,
                    status=ChatMessageStatus.ERROR,
                    error_code="budget_exceeded",
                    error_message=user_facing_error("budget_exceeded"),
                    duration_ms=None,
                    model_id=None,
                    run_lock_id=None,
                    started=started,
                    now=datetime.now(UTC),
                    thinking=failed_thinking(thinking, "budget_exceeded"),
                )
                yield StreamEvent(
                    kind="error",
                    error_code="budget_exceeded",
                    error_message=user_facing_error("budget_exceeded"),
                )
                return
            # 质量检查阶段（Issue 06）：技能编排内含生成与确定性复核
            quality_entered = budget.enter(RunStage.QUALITY_CHECK)
            if quality_entered:
                yield self._stage_event(
                    assistant_message_id, RunStage.QUALITY_CHECK, "active"
                )
            for run_event in self._career_planner.run_task(
                account_id,
                conversation_id,
                assistant_message_id,
                intent,
                mode=mode.value,
                run_context=run_context,
                profile_enabled=use_profile,
                profile_used=profile_used,
                profile_items=profile_items,
                retrieval_round=retrieval_round,
                web_search_projection=web_search_projection,
                arxiv_search_projection=arxiv_search_projection,
                budget=budget,
            ):
                if budget.expired():
                    # 预算检查点：技能循环内不得绕开总预算（审查修复 C）
                    budget.mark_exhausted()
                    if quality_entered:
                        budget.exit(
                            RunStage.QUALITY_CHECK,
                            result=RESULT_FAILED,
                            category="qwen_structured_output",
                        )
                    finalize_message(
                        self._repo,
                        account_id,
                        assistant_message_id,
                        status=ChatMessageStatus.ERROR,
                        error_code="budget_exceeded",
                        error_message=user_facing_error("budget_exceeded"),
                        duration_ms=None,
                        model_id=None,
                        run_lock_id=None,
                        started=started,
                        now=datetime.now(UTC),
                        thinking=failed_thinking(thinking, "budget_exceeded"),
                    )
                    yield StreamEvent(
                        kind="error",
                        error_code="budget_exceeded",
                        error_message=user_facing_error("budget_exceeded"),
                    )
                    return
                if stop_event.is_set():
                    finalize_message(
                        self._repo,
                        account_id,
                        assistant_message_id,
                        status=ChatMessageStatus.STOPPED,
                        error_code=None,
                        error_message=None,
                        duration_ms=None,
                        model_id=None,
                        run_lock_id=None,
                        started=started,
                        now=datetime.now(UTC),
                        thinking=stopped_thinking(thinking),
                    )
                    return
                if run_event.kind == "process":
                    if run_event.state is None:
                        continue
                    yield StreamEvent(
                        kind="career",
                        career=ChatStreamCareerData(
                            message_id=assistant_message_id,
                            state=run_event.state,
                            step_label=run_event.step_label,
                            detail=run_event.detail,
                            retryable=run_event.retryable,
                            progress_steps=run_event.progress_steps,
                        ),
                    )
                    continue
                result = run_event.result
                if result is None:
                    continue
                now = datetime.now(UTC)
                self._repo.update_message_career_planning(
                    account_id,
                    assistant_message_id,
                    result.model_dump(mode="json"),
                    now,
                )
                if result.status == CareerPlanningStatus.ERROR:
                    finalize_message(
                        self._repo,
                        account_id,
                        assistant_message_id,
                        status=ChatMessageStatus.ERROR,
                        error_code=result.error_code,
                        error_message=result.error_message,
                        duration_ms=None,
                        model_id=None,
                        run_lock_id=None,
                        started=started,
                        now=now,
                        thinking=failed_thinking(thinking, result.error_code),
                    )
                    yield StreamEvent(
                        kind="error",
                        error_code=result.error_code or "career_failed",
                        error_message=result.error_message
                        or "生涯规划未完成，请重试。",
                    )
                    return
                final_text = result.output.final_text if result.output else ""
                self._repo.update_message_content(
                    account_id, assistant_message_id, final_text, now
                )
                if quality_entered:
                    budget.exit(
                        RunStage.QUALITY_CHECK,
                        category="qwen_structured_output",
                        count=1,
                    )
                    yield self._stage_event(
                        assistant_message_id,
                        RunStage.QUALITY_CHECK,
                        "done",
                        duration_ms=budget.metrics()[-1].duration_ms,
                    )
                # 收尾阶段（Issue 06）：结果落库与终态提交
                if budget.enter(RunStage.FINALIZING):
                    yield self._stage_event(
                        assistant_message_id, RunStage.FINALIZING, "active"
                    )
                budget.exit(RunStage.FINALIZING)
                finalize_message(
                    self._repo,
                    account_id,
                    assistant_message_id,
                    status=ChatMessageStatus.DONE,
                    error_code=None,
                    error_message=None,
                    duration_ms=None,
                    model_id=None,
                    run_lock_id=None,
                    started=started,
                    now=now,
                    thinking=done_thinking(thinking),
                )
                yield StreamEvent(kind="done")
                return
        except Exception:  # noqa: BLE001 - 编排意外异常收敛为可重试错误
            finalize_message(
                self._repo,
                account_id,
                assistant_message_id,
                status=ChatMessageStatus.ERROR,
                error_code="career_failed",
                error_message="生涯规划执行异常，请重试（输入已保留）。",
                duration_ms=None,
                model_id=None,
                run_lock_id=None,
                started=started,
                now=datetime.now(UTC),
                thinking=failed_thinking(thinking, "career_failed"),
            )
            yield StreamEvent(
                kind="error",
                error_code="career_failed",
                error_message="生涯规划执行异常，请重试（输入已保留）。",
            )
        finally:
            # 模型阶段脱敏计时（终态事件已发出，stage 关闭不额外发事件）
            if generation_entered:
                budget.exit(
                    RunStage.MODEL_GENERATION, category="qwen_structured_output"
                )

    def _stream_career_clarification(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        intent: str,
        mode: ChatMode,
        *,
        use_profile: bool,
        question: str,
        thinking: ChatThinkingSummary,
        started: float,
    ) -> Iterator[StreamEvent]:
        """信息不足：只问一个关键澄清问题，不启动完整规划生成（Issue 09）。

        消息以 DONE 终态交付（正文即澄清问题，不调用任何模型）；用户
        回复后由澄清粘性（``_has_pending_career_clarification``）继续走
        生涯编排——澄清问答是同一规划的延续。
        """
        now = datetime.now(UTC)
        projection = CareerPlanningProjection(
            plan_id=assistant_message_id,
            intent=_truncate_text(intent, 240),
            status=CareerPlanningStatus.DONE,
            profile_enabled=use_profile,
            profile_used=False,
            verified_at=now,
            output=None,
            clarification=question,
            evidence_sources=[],
            review=None,
            process_state=CareerPlanningProcessState.CLARIFY,
            process_steps=["收集信息"],
            error_code=None,
            error_message=None,
            created_at=now,
        )
        self._repo.update_message_career_planning(
            account_id,
            assistant_message_id,
            projection.model_dump(mode="json"),
            now,
        )
        self._repo.update_message_content(
            account_id, assistant_message_id, question, now
        )
        finalize_message(
            self._repo,
            account_id,
            assistant_message_id,
            status=ChatMessageStatus.DONE,
            error_code=None,
            error_message=None,
            duration_ms=None,
            model_id=None,
            run_lock_id=None,
            started=started,
            now=now,
            thinking=done_thinking(thinking),
        )
        yield StreamEvent(
            kind="career",
            career=ChatStreamCareerData(
                message_id=assistant_message_id,
                state=CareerPlanningProcessState.CLARIFY,
                step_label="需要补充信息",
                detail=question,
                retryable=False,
                progress_steps=["收集信息"],
            ),
        )
        yield StreamEvent(kind="done")

    def _has_pending_career_clarification(
        self,
        account_id: str,
        conversation_id: str,
        until_user_message_id: str | None,
    ) -> bool:
        """会话级生涯澄清粘性：本用户消息之前最近的助手消息是否在等澄清。

        澄清问题由助手以 DONE 终态交付（career_planning.clarification
        非空）；用户回复它的下一轮继续走生涯编排，保证「先问关键问题、
        再给完整规划」的两轮交互不断裂。
        """
        for message in reversed(
            self._repo.list_messages(account_id, conversation_id)
        ):
            if (
                until_user_message_id is not None
                and message.message_id == until_user_message_id
            ):
                # 当前轮用户消息本身：跳过（它之前的助手消息才是历史）。
                continue
            if message.role != ChatMessageRole.ASSISTANT:
                continue
            if message.status == ChatMessageStatus.STREAMING:
                # 本轮占位助手消息：跳过，继续向前找最近的已终态助手消息。
                continue
            career = message.career_planning or {}
            if not career:
                # 无生涯投影的普通助手消息：澄清可能隔了几轮普通消息，
                # 继续向前找最近的生涯助手消息。
                continue
            return bool((career.get("clarification") or "").strip())
        return False

    # ------------------------------------------------------------------
    # Issue 31/32/36：异步任务与 MCP 调用编排路径
    # ------------------------------------------------------------------

    def _stream_image_request(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        payload: ImageRequestPayload,
    ) -> Iterator[StreamEvent]:
        """图片生成/编辑编排：创建异步任务 → IMAGE 事件 → 收敛 DONE。

        任务创建（含助手消息投影）在图片服务的事务内原子完成；本方法
        只发事件与收敛消息状态，不调用任何模型。任务完成/失败/取消由
        后台执行器写回消息投影，前端刷新消息列表即可恢复——任务表是
        权威，消息投影是快照。
        """
        started = time.monotonic()
        now = datetime.now(UTC)
        assert self._image is not None
        try:
            if payload.kind == ImageTaskKind.EDIT:
                projection = self._image.submit_edit(
                    account_id,
                    conversation_id,
                    assistant_message_id,
                    payload.prompt,
                    source_version_id=payload.source_version_id,
                    source_object_id=payload.source_object_id,
                )
            else:
                projection = self._image.submit_generation(
                    account_id,
                    conversation_id,
                    assistant_message_id,
                    payload.prompt,
                )
        except ImageError as exc:
            finalize_message(
                self._repo,
                account_id,
                assistant_message_id,
                status=ChatMessageStatus.ERROR,
                error_code=exc.code,
                error_message=exc.message,
                duration_ms=None,
                model_id=None,
                run_lock_id=None,
                started=started,
                now=now,
                thinking=failed_thinking(initial_thinking(CHAT_MODE), exc.code),
            )
            yield StreamEvent(kind="error", error_code=exc.code, error_message=exc.message)
            return
        except Exception:  # noqa: BLE001 - 意外异常收敛为可重试错误
            finalize_message(
                self._repo,
                account_id,
                assistant_message_id,
                status=ChatMessageStatus.ERROR,
                error_code="image_submit_failed",
                error_message="图片任务提交异常，请重试。",
                duration_ms=None,
                model_id=None,
                run_lock_id=None,
                started=started,
                now=now,
                thinking=failed_thinking(initial_thinking(CHAT_MODE), "image_submit_failed"),
            )
            yield StreamEvent(
                kind="error",
                error_code="image_submit_failed",
                error_message="图片任务提交异常，请重试。",
            )
            return

        # 图片任务不再流式产出文本：正文收敛为提交摘要，状态由任务卡呈现。
        summary = (
            "已提交图片编辑请求，正在处理…"
            if payload.kind == ImageTaskKind.EDIT
            else "已提交图片生成请求，正在处理…"
        )
        self._repo.update_message_content(
            account_id, assistant_message_id, summary, now
        )
        finalize_message(
            self._repo,
            account_id,
            assistant_message_id,
            status=ChatMessageStatus.DONE,
            error_code=None,
            error_message=None,
            duration_ms=None,
            model_id=None,
            run_lock_id=None,
            started=started,
            now=now,
            thinking=done_thinking(initial_thinking(CHAT_MODE)),
        )
        yield StreamEvent(
            kind="image",
            image=ChatStreamImageData(
                message_id=assistant_message_id,
                task=projection,
            ),
        )
        yield StreamEvent(kind="done")

    def _stream_video_request(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        payload: VideoRequestPayload,
    ) -> Iterator[StreamEvent]:
        """文生视频编排：创建异步任务 → VIDEO 事件 → 收敛 DONE。

        任务创建（含助手消息投影）在视频服务的事务内原子完成；本方法
        只发事件与收敛消息状态，不调用任何模型。任务完成/失败/取消由
        后台执行器写回消息投影，前端刷新消息列表即可恢复——任务表是
        权威，消息投影是快照。
        """
        started = time.monotonic()
        now = datetime.now(UTC)
        assert self._video is not None
        try:
            projection = self._video.submit(
                account_id,
                conversation_id,
                assistant_message_id,
                payload.prompt,
            )
        except VideoError as exc:
            finalize_message(
                self._repo,
                account_id,
                assistant_message_id,
                status=ChatMessageStatus.ERROR,
                error_code=exc.code,
                error_message=exc.message,
                duration_ms=None,
                model_id=None,
                run_lock_id=None,
                started=started,
                now=now,
                thinking=failed_thinking(initial_thinking(CHAT_MODE), exc.code),
            )
            yield StreamEvent(kind="error", error_code=exc.code, error_message=exc.message)
            return
        except Exception:  # noqa: BLE001 - 意外异常收敛为可重试错误
            finalize_message(
                self._repo,
                account_id,
                assistant_message_id,
                status=ChatMessageStatus.ERROR,
                error_code="video_submit_failed",
                error_message="视频任务提交异常，请重试。",
                duration_ms=None,
                model_id=None,
                run_lock_id=None,
                started=started,
                now=now,
                thinking=failed_thinking(initial_thinking(CHAT_MODE), "video_submit_failed"),
            )
            yield StreamEvent(
                kind="error",
                error_code="video_submit_failed",
                error_message="视频任务提交异常，请重试。",
            )
            return

        # 视频任务不再流式产出文本：正文收敛为提交摘要，状态由任务卡呈现。
        self._repo.update_message_content(
            account_id, assistant_message_id, "已提交视频生成请求，正在处理…", now
        )
        finalize_message(
            self._repo,
            account_id,
            assistant_message_id,
            status=ChatMessageStatus.DONE,
            error_code=None,
            error_message=None,
            duration_ms=None,
            model_id=None,
            run_lock_id=None,
            started=started,
            now=now,
            thinking=done_thinking(initial_thinking(CHAT_MODE)),
        )
        yield StreamEvent(
            kind="video",
            video=ChatStreamVideoData(
                message_id=assistant_message_id,
                task=projection,
            ),
        )
        yield StreamEvent(kind="done")

    def _stream_mcp_call(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        payload: McpCallRequestPayload,
    ) -> Iterator[StreamEvent]:
        """真实 MCP 调用编排：选中校验 → invoke → MCP_CALL 事件 → 收敛。

        同步执行：invoke 走 MCP 服务既有真实链路（受限进程/数据切片/
        敏感确认/审计）。结果投影写入消息 mcp_call 列，刷新可恢复；
        敏感操作挂起时消息终态收敛为 done + sensitive_pending，前端经
        chat 域确认路由 approve/deny 后写回最终结果（不丢失）。
        """
        started = time.monotonic()
        now = datetime.now(UTC)
        assert self._mcp is not None
        # 1. 目标 MCP 必须被本对话选中（可用集合=已安装且启用，选择器
        #    持久化到会话）；未选中拒绝，绝不绕过选择器发起调用。
        if self._selections is not None:
            resolution = self._selections.resolve(account_id, conversation_id)
            selected = {selection_key(item) for item in resolution.valid}
            if ("mcp", payload.mcp_id) not in selected:
                finalize_message(
                    self._repo,
                    account_id,
                    assistant_message_id,
                    status=ChatMessageStatus.ERROR,
                    error_code="mcp_not_selected",
                    error_message="该 MCP 插件未在本对话选择，请先在「+」菜单选择插件。",
                    duration_ms=None,
                    model_id=None,
                    run_lock_id=None,
                    started=started,
                    now=now,
                    thinking=failed_thinking(initial_thinking(CHAT_MODE), "mcp_not_selected"),
                )
                yield StreamEvent(
                    kind="error",
                    error_code="mcp_not_selected",
                    error_message="该 MCP 插件未在本对话选择，请先在「+」菜单选择插件。",
                )
                return
        mcp_name = self._mcp_name(account_id, payload.mcp_id)
        loading = McpCallMessageProjection(
            status=McpCallStatus.LOADING,
            mcp_id=payload.mcp_id,
            mcp_name=mcp_name,
            tool=payload.tool,
            input_summary=input_summary(payload.input),
            created_at=now,
            updated_at=now,
        )
        self._repo.update_message_mcp_call(
            account_id, assistant_message_id, loading.model_dump(mode="json"), now
        )
        # 2. 真实 invoke：失败分类由 MCP 服务给出（中文可操作原因），
        #    不伪造成功、不把服务器错误当结果。
        try:
            result = self._mcp.invoke(
                account_id,
                payload.mcp_id,
                McpCallRequest(
                    tool=payload.tool,
                    input=payload.input,
                    data_slice=payload.data_slice,
                ),
            )
        except McpError as exc:
            failed_projection = McpCallMessageProjection(
                status=McpCallStatus.FAILED,
                mcp_id=payload.mcp_id,
                mcp_name=mcp_name,
                tool=payload.tool,
                input_summary=input_summary(payload.input),
                error_code=exc.code,
                error_message=exc.message,
                created_at=now,
                updated_at=now,
            )
            # 失败投影落库：刷新后调用卡呈现失败原因，不留「调用中…」。
            self._repo.update_message_mcp_call(
                account_id,
                assistant_message_id,
                failed_projection.model_dump(mode="json"),
                now,
            )
            finalize_message(
                self._repo,
                account_id,
                assistant_message_id,
                status=ChatMessageStatus.ERROR,
                error_code=exc.code,
                error_message=exc.message,
                duration_ms=None,
                model_id=None,
                run_lock_id=None,
                started=started,
                now=now,
                thinking=failed_thinking(initial_thinking(CHAT_MODE), exc.code),
            )
            yield StreamEvent(kind="error", error_code=exc.code, error_message=exc.message)
            return
        except Exception:  # noqa: BLE001 - 意外异常收敛为可重试错误
            failed_projection = McpCallMessageProjection(
                status=McpCallStatus.FAILED,
                mcp_id=payload.mcp_id,
                mcp_name=mcp_name,
                tool=payload.tool,
                input_summary=input_summary(payload.input),
                error_code="mcp_invoke_failed",
                error_message="MCP 调用异常，请重试。",
                created_at=now,
                updated_at=now,
            )
            self._repo.update_message_mcp_call(
                account_id,
                assistant_message_id,
                failed_projection.model_dump(mode="json"),
                now,
            )
            finalize_message(
                self._repo,
                account_id,
                assistant_message_id,
                status=ChatMessageStatus.ERROR,
                error_code="mcp_invoke_failed",
                error_message="MCP 调用异常，请重试。",
                duration_ms=None,
                model_id=None,
                run_lock_id=None,
                started=started,
                now=now,
                thinking=failed_thinking(initial_thinking(CHAT_MODE), "mcp_invoke_failed"),
            )
            yield StreamEvent(
                kind="error",
                error_code="mcp_invoke_failed",
                error_message="MCP 调用异常，请重试。",
            )
            return
        if result.status == "success":
            projection = McpCallMessageProjection(
                status=McpCallStatus.SUCCEEDED,
                mcp_id=payload.mcp_id,
                mcp_name=mcp_name,
                tool=payload.tool,
                input_summary=input_summary(payload.input),
                result_summary=result_summary(result.result),
                created_at=now,
                updated_at=now,
            )
        elif result.status == "sensitive_pending":
            projection = McpCallMessageProjection(
                status=McpCallStatus.SENSITIVE_PENDING,
                mcp_id=payload.mcp_id,
                mcp_name=mcp_name,
                tool=payload.tool,
                input_summary=input_summary(payload.input),
                confirmation=result.confirmation,
                created_at=now,
                updated_at=now,
            )
        else:
            projection = McpCallMessageProjection(
                status=McpCallStatus.FAILED,
                mcp_id=payload.mcp_id,
                mcp_name=mcp_name,
                tool=payload.tool,
                input_summary=input_summary(payload.input),
                error_code=result.error_code,
                error_message=result.error_message or "调用失败，请重试。",
                created_at=now,
                updated_at=now,
            )
        self._repo.update_message_mcp_call(
            account_id, assistant_message_id, projection.model_dump(mode="json"), now
        )
        # 3. 收敛：成功与敏感挂起为 done（正文=调用摘要，结果卡呈现）；
        #    失败在错误收敛后由前端错误呈现（mcp_call 列保留失败原因）。
        self._repo.update_message_content(
            account_id,
            assistant_message_id,
            mcp_call_summary(projection),
            now,
        )
        finalize_message(
            self._repo,
            account_id,
            assistant_message_id,
            status=ChatMessageStatus.DONE,
            error_code=None,
            error_message=None,
            duration_ms=None,
            model_id=None,
            run_lock_id=None,
            started=started,
            now=now,
            thinking=done_thinking(initial_thinking(CHAT_MODE)),
        )
        yield StreamEvent(
            kind="mcp_call",
            mcp_call=ChatStreamMcpData(
                message_id=assistant_message_id,
                call=projection,
            ),
        )
        yield StreamEvent(kind="done")

    def _mcp_name(self, account_id: str, mcp_id: str) -> str | None:
        try:
            listing = self._mcp.list_servers(account_id) if self._mcp is not None else None
        except Exception:  # noqa: BLE001 - 名称只是展示快照，失败不阻断调用
            return None
        if listing is None:
            return None
        for server in listing.servers:
            if server.mcp_id == mcp_id:
                return server.name
        return None

    # ------------------------------------------------------------------
    # Issue 27：最小画像切片、披露与反馈闭环
    # ------------------------------------------------------------------

    def _compile_profile_slice(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        mode: ChatMode,
        *,
        use_profile: bool,
        retrieval_round: RetrievalRoundProjection | None,
        web_search_projection: WebSearchProjection | None,
        arxiv_search_projection: ArxivSearchProjection | None,
    ) -> tuple[ContextNoteProjection | None, str | None, list[ProfileSliceItem]]:
        """编译本轮最小画像切片并落库上下文说明披露。

        关闭画像（``use_profile=False``）时：不编译、不注入，披露为 off
        态并审计记录 disabled，保证模型请求与审计均不含画像内容。启用时
        只注入当前模式相关、已授权、仍有效的最小记录；编译或披露失败
        一律降级为 error 态（回答照常，不向模型注入未经验证的内容）。
        注入模型的上下文以字符串形式返回（提示词组装由
        ``assemble_payload`` 统一完成，本方法不再改动 payload）。
        """
        now = datetime.now(UTC)
        material_categories = self._material_categories(
            retrieval_round, web_search_projection, arxiv_search_projection
        )
        # 画像服务未挂载（退化环境）时无画像能力：不披露、不审计，聊天
        # 行为与旧版一致（thinking 不追加画像说明）。
        profile_service = self._four_dimension_profiles or self._profiles
        if profile_service is None:
            return None, None, []
        if not use_profile:
            self._audit_slice_usage(
                account_id,
                mode=mode.value,
                enabled=False,
                slice_id=None,
                item_count=0,
                excluded_count=0,
                material_categories=material_categories,
            )
            return (
                self._persist_context_note(
                    account_id,
                    assistant_message_id,
                    ContextNoteProjection(
                        state=ContextNoteState.OFF,
                        profile_enabled=False,
                        mode=mode,
                        used_at=now,
                        material_categories=material_categories,
                        note="本轮未使用你的画像记录（发送前已关闭）。回答不基于任何画像信息。",
                    ),
                ),
                None,
                [],
            )
        try:
            profile_slice = profile_service.compile_chat_slice(
                account_id,
                mode=mode.value,
                run_id=assistant_message_id,
                project_id=conversation_id,
            )
        except Exception:  # noqa: BLE001 - 切片编译失败只影响披露，不阻断回答
            self._audit_slice_usage(
                account_id,
                mode=mode.value,
                enabled=True,
                slice_id=None,
                item_count=0,
                excluded_count=0,
                material_categories=material_categories,
                failed=True,
            )
            return (
                self._persist_context_note(
                    account_id,
                    assistant_message_id,
                    ContextNoteProjection(
                        state=ContextNoteState.ERROR,
                        profile_enabled=True,
                        mode=mode,
                        used_at=now,
                        material_categories=material_categories,
                        note="本轮画像切片编译失败，回答已在不使用画像的情况下正常生成。",
                    ),
                ),
                None,
                [],
            )
        profile_context = (
            profile_slice_context(profile_slice) if profile_slice.included_items else None
        )
        profile_items = list(profile_slice.included_items)
        self._audit_slice_usage(
            account_id,
            mode=mode.value,
            enabled=True,
            slice_id=profile_slice.slice_id,
            item_count=len(profile_slice.included_items),
            excluded_count=len(profile_slice.unused_items)
            + len(profile_slice.rejected_items),
            material_categories=material_categories,
        )
        context_note = ContextNoteProjection(
            state=(
                ContextNoteState.READY
                if profile_items
                else ContextNoteState.EMPTY
            ),
            profile_enabled=True,
            mode=mode,
            used_at=now,
            profile_item_count=len(profile_items),
            material_categories=material_categories,
            note=(
                context_note_ready_text(profile_items)
                if profile_items
                else "本轮没有与你当前任务相关的画像记录，因此没有使用画像信息。"
            ),
        )
        return (
            self._persist_context_note(account_id, assistant_message_id, context_note),
            profile_context,
            profile_items,
        )

    def _persist_context_note(
        self,
        account_id: str,
        assistant_message_id: str,
        context_note: ContextNoteProjection,
    ) -> ContextNoteProjection:
        """把披露快照固化到助手消息（历史回答保留当时切片版本）。"""
        self._repo.update_message_context_note(
            account_id,
            assistant_message_id,
            context_note.model_dump(mode="json"),
            datetime.now(UTC),
        )
        return context_note

    @staticmethod
    def _material_categories(
        retrieval_round: RetrievalRoundProjection | None,
        web_search_projection: WebSearchProjection | None,
        arxiv_search_projection: ArxivSearchProjection | None,
    ) -> list[str]:
        """本轮实际使用的材料类别（仅成功且非空的来源进入披露）。"""
        categories: list[str] = []
        if retrieval_round is not None:
            for layer in retrieval_round.layers:
                if layer.status == RetrievalLayerStatus.OK and layer.candidates > 0:
                    categories.append(_LAYER_NAMES[layer.layer])
        if (
            web_search_projection is not None
            and web_search_projection.status == WebSearchStatus.SUCCESS
        ):
            categories.append("公网搜索")
        if (
            arxiv_search_projection is not None
            and arxiv_search_projection.status == ArxivSearchStatus.SUCCESS
        ):
            categories.append("arXiv 论文")
        return categories

    def _audit_slice_usage(
        self,
        account_id: str,
        *,
        mode: str,
        enabled: bool,
        slice_id: str | None,
        item_count: int,
        excluded_count: int,
        material_categories: list[str],
        failed: bool = False,
    ) -> None:
        """云端披露审计：只记类别、切片 ID、条目数与授权快照，不复制正文。"""
        if self._observability is None:
            return
        details: dict[str, Any] = {
            "mode": mode,
            "profile_enabled": enabled,
            "slice_id": slice_id,
            "item_count": item_count,
            "excluded_count": excluded_count,
            "material_categories": material_categories,
            "authorization_snapshot": "authz-1.0",
        }
        if failed:
            details["result"] = "compilation_failed"
        self._observability.log_audit(
            actor_account_id=account_id,
            action=AuditAction.PROFILE_SLICE_USED,
            result=AuditResult.DEGRADED if failed else AuditResult.SUCCESS,
            object_refs=[slice_id] if slice_id else None,
            reason="本轮画像切片使用披露。",
            details=details,
        )

    # ------------------------------------------------------------------
    # 历史与收敛
    # ------------------------------------------------------------------

    def _model_history(
        self,
        account_id: str,
        conversation_id: str,
        until_user_message_id: str | None = None,
    ) -> list[dict[str, str]]:
        """组装发送给模型的会话历史。

        首条为当前对话模式的系统角色合同（companion/study，Issue 14）；
        每轮用户消息只带最新一条已完成（done）的助手回答；失败、停止与
        进行中的尝试不进上下文，避免把错误内容当成回答。``until_user_message_id``
        把历史截断到指定轮次（重试旧轮次失败消息时，新尝试的上下文不得
        包含其后的后续轮次）。
        """
        messages = self._repo.list_messages(account_id, conversation_id)
        record = self._repo.get_conversation(account_id, conversation_id)
        mode = ChatMode(record.mode) if record is not None else CHAT_MODE
        history: list[dict[str, str]] = [
            {"role": "system", "content": _contract(mode).system_prompt}
        ]
        latest_done: MessageRecord | None = None
        for message in messages:
            if message.role == ChatMessageRole.USER:
                if latest_done is not None:
                    history.append({"role": "assistant", "content": latest_done.content})
                    latest_done = None
                history.append({"role": "user", "content": message.content})
                if (
                    until_user_message_id is not None
                    and message.message_id == until_user_message_id
                ):
                    break
            elif message.status == ChatMessageStatus.DONE:
                latest_done = message
        else:
            if latest_done is not None:
                history.append({"role": "assistant", "content": latest_done.content})
        return history

    def _persist_lock(self, account_id: str, lock: ModelRunLock | None) -> None:
        if lock is None:
            return
        self._repo.insert_run_lock(account_id, lock)

    @staticmethod
    def _lock_model_id(lock: ModelRunLock | None) -> str | None:
        return lock.actual_model_id if lock is not None else None

    @staticmethod
    def _lock_id(lock: ModelRunLock | None) -> str | None:
        return lock.lock_id if lock is not None else None
