"""全局本地知识库的材料投影（Issue 18）。

知识库材料是账户级的全局检索材料：不绑定对话，上传后进入与聊天附件
相同的持久化摄取状态机（Issue 17），全文/向量索引状态逐材料呈现。
任何投影都不包含对象库路径、原文内容或凭据。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from bridges.contracts.ingestion import DocumentIngestionStatus


class KnowledgeBaseMaterialProjection(BaseModel):
    """单份知识库材料的列表/详情投影（列表与详情共用同一模型）。"""

    document_id: str = Field(description="稳定文档摄取标识。")
    object_id: str = Field(description="来源对象标识。")
    filename: str = Field(description="原始文件名。")
    media_type: str = Field(description="服务端嗅探确认的媒体类型。")
    content_length: int = Field(description="文件大小（字节）。")
    content_hash: str = Field(description="对象内容 SHA-256 摘要。")
    content_hash_summary: str = Field(description="内容哈希短摘要（前 12 位）。")
    source: str = Field(description="材料来源的中文说明（如“本地上传”）。")
    status: DocumentIngestionStatus = Field(description="摄取状态。")
    title: str | None = Field(default=None, description="提取的文档标题。")
    chunk_count: int = Field(default=0, description="分块数。")
    failure_stage: str | None = Field(
        default=None, description="失败阶段：read/parse/chunk/embed/index。"
    )
    failure_reason: str | None = Field(default=None, description="失败的中文原因。")
    retry_count: int = Field(default=0, description="已处理尝试次数。")
    vector_enabled: bool = Field(
        default=False, description="处理时向量能力是否可用（按探测结果）。"
    )
    vector_indexed: bool = Field(
        default=False, description="分块向量是否已写入当前索引版本。"
    )
    embedding_available: bool = Field(
        default=False, description="Embedding 能力当前是否可用（探测快照）。"
    )
    vector_unavailable_reason: str | None = Field(
        default=None, description="向量索引不可用时的中文原因。"
    )
    index_version_id: str | None = Field(
        default=None, description="当前服务检索的索引版本；尚无索引时为 None。"
    )
    index_rebuilding: bool = Field(
        default=False, description="当前账户索引版本正在重建（旧版继续服务）。"
    )
    usable_for_chat: bool = Field(
        default=False, description="材料已就绪、可在对话检索中使用。"
    )
    created_at: datetime = Field(description="上传入队时间。")
    updated_at: datetime = Field(description="最近状态更新时间。")
