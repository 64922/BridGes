"""文档摄取与版本化索引的公开契约（Issue 17）。

这些模型定义文档摄取状态机与全文/向量索引对外的数据面：摄取状态
（含失败阶段与中文原因）、分块统计、索引版本合同（模型、维度、
规范化、分块器与 Schema）与向量可用性。任何投影都不包含对象库路径、
凭据或密钥正文。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class DocumentIngestionStatus(StrEnum):
    """文档摄取状态（持久化状态机的对外呈现）。

    - ``queued``：已入队，等待后台执行器处理；
    - ``processing``：解析、分块、向量化或索引进行中；
    - ``ready``：完成，可被全文/向量索引检索；
    - ``empty``：解析完成但没有可索引的文本内容；
    - ``error``：失败（携带失败阶段与中文原因），可从失败阶段重试；
    - ``recovery``：上次处理中断（领取租约已过期），后台恢复中；
    - ``none``：尚无摄取记录（本 Issue 之前上传且未回填的对象）。
    """

    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    EMPTY = "empty"
    ERROR = "error"
    RECOVERY = "recovery"
    NONE = "none"


class IndexVersionStatus(StrEnum):
    """索引版本生命周期状态。

    - ``building``：正在全量重建（合同变化触发），尚未服务检索；
    - ``active``：当前唯一可服务检索的版本；
    - ``obsolete``：被新版本替代，但在明确清理前仍可回滚；
    - ``failed``：重建失败，上一可用版本继续服务。
    """

    BUILDING = "building"
    ACTIVE = "active"
    OBSOLETE = "obsolete"
    FAILED = "failed"


class IndexContractProjection(BaseModel):
    """不可混写的索引版本合同（ADR-0008/0020）。

    任一字段变化都会构造新合同哈希；新哈希与活跃版本不一致时触发
    全量重建与原子切换，绝不向当前版本混合写入。
    """

    model_id: str = Field(description="固定 Embedding 模型快照。")
    dimensions: int = Field(description="向量维度。")
    normalization: str = Field(description="向量规范化合同（如 l2）。")
    chunker: str = Field(description="分块器版本标识。")
    schema_version: str = Field(description="索引 Schema 版本标识。")
    contract_hash: str = Field(description="合同规范化的 SHA-256 摘要。")

    @property
    def display(self) -> str:
        """面向用户的中文合同说明。"""
        return (
            f"模型 {self.model_id} · {self.dimensions} 维 · "
            f"{self.normalization} 规范化 · {self.chunker} · {self.schema_version}"
        )


class IndexVersionProjection(BaseModel):
    """单个索引版本的对外投影。"""

    version_id: str = Field(description="稳定版本标识。")
    contract: IndexContractProjection = Field(description="本版本锁定的合同。")
    status: IndexVersionStatus = Field(description="版本生命周期状态。")
    expected_chunk_count: int = Field(description="重建开始时预期的分块数。")
    chunk_count: int = Field(description="已写入全文索引的分块数。")
    vector_count: int = Field(description="已写入向量索引的分块数。")
    error_message: str | None = Field(default=None, description="重建失败的中文原因。")
    built_at: datetime | None = Field(default=None, description="构建完成时间。")
    switched_at: datetime | None = Field(default=None, description="原子切换时间。")
    created_at: datetime = Field(description="版本创建时间。")


class IndexStatusProjection(BaseModel):
    """当前账户的索引整体状态（用于展示向量可用性与版本链）。

    GQ-05 起向量可用性由运行时是否成功构造全局 Embedding 端口决定，
    不再依赖账户能力探测；``embedding_probed`` 保留字段位，语义为
    「Embedding 可用性已确定」（端口已构造即 True），与
    ``embedding_available`` 取值一致。
    """

    embedding_probed: bool = Field(
        description="Embedding 可用性是否已确定（全局端口已构造，GQ-05 起不再有账户探测）。"
    )
    embedding_available: bool = Field(description="Embedding 能力当前是否可用。")
    vector_unavailable_reason: str | None = Field(
        default=None, description="向量索引不可用时的中文原因。"
    )
    active_version: IndexVersionProjection | None = Field(
        default=None, description="当前服务检索的版本；尚无文档时为 None。"
    )
    versions: list[IndexVersionProjection] = Field(
        default_factory=list, description="全部版本（含可回滚的旧版本）。"
    )


class DocumentIngestionProjection(BaseModel):
    """单个文档的摄取详情投影（不包含对象库路径或原文）。"""

    document_id: str = Field(description="稳定文档摄取标识。")
    object_id: str = Field(description="来源对象标识。")
    conversation_id: str | None = Field(
        default=None, description="所属对话标识；全局知识库材料不绑定对话，为 None。"
    )
    status: DocumentIngestionStatus = Field(description="摄取状态。")
    parser_version: str = Field(description="实际使用的解析器版本。")
    content_hash: str = Field(description="对象内容 SHA-256 摘要。")
    title: str | None = Field(default=None, description="提取的文档标题。")
    page_count: int = Field(default=0, description="页码数。")
    section_count: int = Field(default=0, description="章节数。")
    chunk_count: int = Field(default=0, description="分块数。")
    vector_enabled: bool = Field(
        default=False, description="处理时向量能力是否可用（按探测结果）。"
    )
    vector_indexed: bool = Field(
        default=False, description="分块向量是否已写入当前索引版本。"
    )
    failure_stage: str | None = Field(
        default=None, description="失败阶段：parse/embed/index。"
    )
    failure_reason: str | None = Field(default=None, description="失败的中文原因。")
    retry_count: int = Field(default=0, description="已处理尝试次数。")
    index_rebuilding: bool = Field(
        default=False, description="当前账户索引版本正在重建（旧版继续服务）。"
    )
    vector_unavailable_reason: str | None = Field(
        default=None, description="向量索引不可用时的中文原因。"
    )
    created_at: datetime = Field(description="入队时间。")
    updated_at: datetime = Field(description="最近更新时间。")
