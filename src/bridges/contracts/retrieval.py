"""分层本地检索、融合排序与引用的公开契约（Issue 20）。

这些模型定义每轮对话检索的对外数据面：来源层（当前附件 → 当前项目
文件 → 已授权全局知识库）、每层状态与候选数、融合后的最终引用
（来源类型/文件名/页码或章节/可验证片段）、证据充足性信号
（供后续教学门消费）与单条引用的证据详情（含权限安全状态与
打开原文入口）。任何投影都不包含对象库路径、向量原文或凭据。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class RetrievalSourceLayer(StrEnum):
    """候选来源层（作用域顺序即优先级：附件 → 项目文件 → 知识库）。

    - ``attachment``：当前对话明确附加的文件（本轮授权）；
    - ``project``：当前学习项目的文件（对话归属项目时启用）；
    - ``knowledge_base``：用户已授权的全局知识库（可在发送前关闭）。
    """

    ATTACHMENT = "attachment"
    PROJECT = "project"
    KNOWLEDGE_BASE = "knowledge_base"


class RetrievalLayerStatus(StrEnum):
    """单层检索的对外状态。

    - ``disabled``：本轮未启用（未授权/被用户关闭），不产生任何候选；
    - ``no_material``：该层没有可检索材料（未上传/仍在处理/已删除）；
    - ``index_unavailable``：本地索引不可用，无法检索该层；
    - ``ok``：完成检索（候选数可为 0，配合整体充足性呈现）。
    """

    DISABLED = "disabled"
    NO_MATERIAL = "no_material"
    INDEX_UNAVAILABLE = "index_unavailable"
    OK = "ok"


class RetrievalSufficiency(StrEnum):
    """证据充足性信号（供后续教学门使用；本 Issue 只输出信号）。

    - ``sufficient``：去重后候选达到覆盖阈值；
    - ``no_hits``：本轮无任何命中（不以空候选表示成功）；
    - ``conflict``：关键词与向量检索的顶级命中不一致（歧义）；
    - ``insufficient_coverage``：有命中但覆盖不足；
    - ``index_unavailable``：本地索引不可用，无法产生候选。
    """

    SUFFICIENT = "sufficient"
    NO_HITS = "no_hits"
    CONFLICT = "conflict"
    INSUFFICIENT_COVERAGE = "insufficient_coverage"
    INDEX_UNAVAILABLE = "index_unavailable"


class RetrievalLayerResult(BaseModel):
    """单层检索结果（状态 + 去重后候选数 + 中文说明）。"""

    layer: RetrievalSourceLayer = Field(description="来源层。")
    status: RetrievalLayerStatus = Field(description="该层检索状态。")
    candidates: int = Field(default=0, description="该层去重后的候选数。")
    note: str | None = Field(default=None, description="面向用户的中文说明。")


class CitationProjection(BaseModel):
    """一条最终引用（融合后确定，展示数据在生成时固化不漂移）。

    ``filename``、``page_number``/``section_title`` 与 ``snippet`` 都是
    引用生成时刻的原文快照：索引重建或原文变化不会让历史引用静默漂移。
    """

    citation_id: str = Field(description="稳定引用标识。")
    source_layer: RetrievalSourceLayer = Field(description="来源层。")
    object_id: str = Field(description="原文对象标识（解析入口使用）。")
    filename: str = Field(description="来源文件名（生成时快照）。")
    media_type: str = Field(description="来源媒体类型（决定打开方式）。")
    page_number: int | None = Field(default=None, description="页码（可空）。")
    section_title: str | None = Field(default=None, description="章节标题（可空）。")
    snippet: str = Field(description="可验证片段（生成时快照，可核对原文）。")
    rank: int = Field(description="融合后排序位次（从 1 开始）。")


class RetrievalRoundProjection(BaseModel):
    """一轮检索的完整投影（绑定一条助手消息，刷新/重启后保持稳定）。"""

    round_id: str = Field(description="稳定轮次标识。")
    message_id: str = Field(description="绑定助手消息标识。")
    conversation_id: str = Field(description="所属对话标识。")
    use_knowledge_base: bool = Field(
        description="本轮是否使用了全局知识库（关闭时请求/引用均不含其候选）。"
    )
    index_version_id: str | None = Field(
        default=None, description="检索时服务的索引版本（审计用途）。"
    )
    sufficiency: RetrievalSufficiency = Field(description="证据充足性信号。")
    layers: list[RetrievalLayerResult] = Field(
        default_factory=list, description="按作用域顺序的每层结果。"
    )
    citations: list[CitationProjection] = Field(
        default_factory=list, description="融合排序后的最终引用。"
    )
    note: str | None = Field(default=None, description="面向用户的整体中文说明。")
    created_at: datetime = Field(description="检索发生时间。")


class CitationAccessStatus(StrEnum):
    """引用打开时的证据可访问状态。

    - ``accessible``：原文仍在当前账户授权范围，可精确打开；
    - ``deleted``：原文对象已删除；
    - ``permission_changed``：原文仍在但授权范围已变化（如附件已解绑、
      项目已删除/文件已移除、知识库材料已删除），显示安全中文状态。
    """

    ACCESSIBLE = "accessible"
    DELETED = "deleted"
    PERMISSION_CHANGED = "permission_changed"


class CitationDetailProjection(BaseModel):
    """单条引用的证据详情（点击展开时实时校验授权后返回）。"""

    citation: CitationProjection = Field(description="生成时固化的引用快照。")
    access_status: CitationAccessStatus = Field(description="当前可访问状态。")
    access_message: str = Field(description="面向用户的中文可访问说明。")
    download_url: str | None = Field(
        default=None, description="打开原文的授权入口（相对 URL；不可访问时为 None）。"
    )
