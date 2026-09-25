"""按聊天模式、任务与用户请求决定是否检索全局知识库。

决策层是纯确定性控制平面：只读取已经编译好的模式、能力路由和用户请求，
不调用语言模型，也不访问索引正文。触发条件只有三件事：本轮模式（学习
模式的教学任务先检查本地材料能否覆盖）、能力路由（专用能力默认不检索）
与用户请求（点名知识库或自己的材料，或提出知识型问题）。判定只看请求
形态，不看问题属于哪个学科——没有任何专业名词词表，同一个名词在任何
学科下得到同一结论。

持久化由 ``LayeredRetrievalService`` 负责，确保生成恢复和同一用户回合的
重试复用相同快照。
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass

from bridges.contracts.retrieval import (
    RetrievalDecisionAction,
    RetrievalDecisionReason,
)

#: 决策规则版本（Issue 07 由 ``retrieval-intent/v2`` 升为 v3：移除学科
#: 名词白名单；已持久化的历史决策保留当时的版本号，只影响新回合）。
RULES_VERSION = "retrieval-intent/v3"

_EXPLICIT_KNOWLEDGE_BASE_TERMS = (
    "知识库",
    "我的资料",
    "资料库",
    "根据我的文件",
    "根据我的材料",
    "查我的",
    "查询我的",
)
#: 用户点名「我的材料」的请求词表（V2 首版材料：讲义、笔记、简历、教材
#: 章节）。这是请求词表而非学科词表：它判断用户是否在问自己的材料，
#: 不判断问题属于哪个学科。材料名只在词表出现一次——学习/日常模式共用
#: 这一判定（``_STUDY_TERMS`` 不再重复登记「教材」，避免遮蔽与漂移）。
_UPLOADED_MATERIAL_TERMS = (
    "上传的材料",
    "上传材料",
    "上传的文件",
    "上传文件",
    "资料",
    "材料",
    "文件",
    "附件",
    "文档",
    "讲义",
    "笔记",
    "简历",
    "教材",
)
_STUDY_TERMS = (
    "解释",
    "为什么",
    "怎么理解",
    "如何理解",
    "原理",
    "概念",
    "学习",
    "练习",
    "证明",
    "推导",
    "题目",
    "知识点",
)
_SPECIALIZED_ROUTES = frozenset({"arxiv", "humanizer", "image", "image_edit", "video", "mcp"})
_CASUAL_TERMS = (
    "你好",
    "嗨",
    "哈喽",
    "谢谢",
    "晚安",
    "早安",
    "在吗",
    "陪我聊",
    "聊聊天",
)
_COMPANION_KNOWLEDGE_TERMS = (
    "是什么",
    "什么是",
    "什么意思",
    "怎么理解",
    "为什么",
    "为何",
    "如何理解",
    "原理",
    "概念",
    "解释",
    "介绍",
    "定义",
    "含义",
    "区别",
    "关系",
    "作用",
)


@dataclass(frozen=True, slots=True)
class RetrievalDecision:
    """未持久化的纯决策结果。"""

    action: RetrievalDecisionAction
    reason: RetrievalDecisionReason


def query_fingerprint(query: str) -> str:
    """生成不可逆请求指纹，避免把用户原文写入决策或性能记录。"""

    normalized = unicodedata.normalize("NFKC", query).strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def infer_capability_route(
    *,
    has_humanizer: bool = False,
    has_image: bool = False,
    image_edit: bool = False,
    has_video: bool = False,
    has_mcp: bool = False,
) -> str:
    """把明确提交的能力载荷归一为审计用路由名。"""

    if has_humanizer:
        return "humanizer"
    if has_image:
        return "image_edit" if image_edit else "image"
    if has_video:
        return "video"
    if has_mcp:
        return "mcp"
    return "companion"


def capability_route_for_request(
    *,
    mode: str,
    has_humanizer: bool = False,
    has_image: bool = False,
    image_edit: bool = False,
    has_video: bool = False,
    has_mcp: bool = False,
) -> str:
    """根据已解析的能力载荷形成回合级路由快照。"""
    route = infer_capability_route(
        has_humanizer=has_humanizer,
        has_image=has_image,
        image_edit=image_edit,
        has_video=has_video,
        has_mcp=has_mcp,
    )
    return "study" if mode == "study" and route == "companion" else route


def decide_retrieval(
    query: str,
    *,
    mode: str,
    capability_route: str,
    use_knowledge_base: bool,
) -> RetrievalDecision:
    """按模式、任务与用户请求形成全局知识库决策。

    判定顺序（先到先得）：

    1. 用户本轮关闭知识库 → ``user_disabled``；
    2. 图像编辑 → ``specialized_capability``（编辑任务不做本地检索）；
    3. 用户点名知识库 → ``explicit_knowledge_base``；
    4. 用户点名自己的材料类型（讲义/笔记/简历/教材等）→ ``uploaded_material``；
    5. 其余专用能力（humanizer/arxiv/image/video/mcp）→ ``specialized_capability``；
    6. 学习模式的教学请求（解释/原理/推导等）→ ``study_explanation``；
    7. 日常模式的知识型问句 → ``knowledge_base_required``；
    8. 其余（寒暄、创作、无请求形态的主题名词）→ 不检索。

    只看请求形态与模式/路由，不做学科判定：同一专业名词在任何学科下
    结论一致（Issue 07 删除了学科名词白名单）。
    """

    if not use_knowledge_base:
        return RetrievalDecision(
            RetrievalDecisionAction.SKIP, RetrievalDecisionReason.USER_DISABLED
        )

    explicit = any(term in query for term in _EXPLICIT_KNOWLEDGE_BASE_TERMS)
    uploaded = any(term in query for term in _UPLOADED_MATERIAL_TERMS)
    specialized = capability_route in _SPECIALIZED_ROUTES
    if capability_route == "image_edit":
        return RetrievalDecision(
            RetrievalDecisionAction.SKIP,
            RetrievalDecisionReason.SPECIALIZED_CAPABILITY,
        )
    if explicit:
        return RetrievalDecision(
            RetrievalDecisionAction.RETRIEVE,
            RetrievalDecisionReason.EXPLICIT_KNOWLEDGE_BASE,
        )
    if uploaded:
        return RetrievalDecision(
            RetrievalDecisionAction.RETRIEVE,
            RetrievalDecisionReason.UPLOADED_MATERIAL,
        )
    if specialized:
        return RetrievalDecision(
            RetrievalDecisionAction.SKIP,
            RetrievalDecisionReason.SPECIALIZED_CAPABILITY,
        )

    if mode == "study" and any(term in query for term in _STUDY_TERMS):
        return RetrievalDecision(
            RetrievalDecisionAction.RETRIEVE,
            RetrievalDecisionReason.STUDY_EXPLANATION,
        )
    if mode == "companion":
        if any(term in query for term in _CASUAL_TERMS):
            return RetrievalDecision(
                RetrievalDecisionAction.SKIP,
                RetrievalDecisionReason.COMPANION_DEFAULT,
            )
        if any(term in query for term in _COMPANION_KNOWLEDGE_TERMS):
            return RetrievalDecision(
                RetrievalDecisionAction.RETRIEVE,
                RetrievalDecisionReason.KNOWLEDGE_BASE_REQUIRED,
            )
    return RetrievalDecision(
        RetrievalDecisionAction.SKIP,
        RetrievalDecisionReason.COMPANION_DEFAULT,
    )


__all__ = [
    "RULES_VERSION",
    "RetrievalDecision",
    "capability_route_for_request",
    "decide_retrieval",
    "infer_capability_route",
    "query_fingerprint",
]
