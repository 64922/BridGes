"""按聊天意图决定是否检索全局知识库。

决策层是纯确定性控制平面：只读取已经编译好的模式、能力路由和用户请求，
不调用语言模型，也不访问索引正文。持久化由 ``LayeredRetrievalService``
负责，确保生成恢复和同一用户回合的重试复用相同快照。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

from bridges.contracts.retrieval import (
    RetrievalDecisionAction,
    RetrievalDecisionReason,
)

RULES_VERSION = "retrieval-intent/v1"

_EXPLICIT_KNOWLEDGE_BASE_TERMS = (
    "知识库",
    "我的资料",
    "资料库",
    "根据我的文件",
    "根据我的材料",
    "查我的",
    "查询我的",
)
_UPLOADED_MATERIAL_TERMS = (
    "上传的材料",
    "上传材料",
    "上传的文件",
    "上传文件",
    "附件",
    "文档",
    "讲义",
    "笔记",
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
    "教材",
    "知识点",
)
# 这些是科学学习伙伴的低成本确定性提示，不是通用语义分类器；它们只
# 让未带完整问句的主题（例如“热力学”）进入已授权本地材料路径。
_SCIENCE_TOPIC_TERMS = (
    "热力",
    "量子",
    "熵",
    "物理",
    "化学",
    "生物",
    "数学",
    "统计",
    "算法",
    "编程",
    "机器学习",
    "神经网络",
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
    query: str,
    *,
    has_humanizer: bool = False,
    has_image: bool = False,
    image_edit: bool = False,
    has_video: bool = False,
    has_mcp: bool = False,
) -> str:
    """把已完成的能力载荷与少量专用意图归一为审计用路由名。"""

    if has_humanizer:
        return "humanizer"
    if has_image:
        return "image_edit" if image_edit else "image"
    if has_video:
        return "video"
    if has_mcp:
        return "mcp"
    if re.search(r"(?:arxiv|arXiv|论文|文献|论文搜索)", query):
        return "arxiv"
    return "companion"


def capability_route_for_request(
    query: str,
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
        query,
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
    """根据用户请求、锁定模式和能力路由形成全局知识库决策。"""

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
    if any(term in query for term in _SCIENCE_TOPIC_TERMS):
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
