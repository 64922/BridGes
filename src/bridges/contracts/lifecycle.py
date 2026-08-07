"""数据生命周期契约（Issue 37）：账户导出、账户删除、加密备份与恢复。

这些模型定义「数据与隐私」页的公开表面：导出范围预览（确认前可见的类别
条数与预计大小）、导出包结构、删除状态机投影（部分失败可重试不宣称成功）、
备份清单与恢复预检结果。任何投影与审计都不携带凭据、会话令牌、运行密钥、
审计正文或数据正文；恢复的预检原因必须是可操作的具体中文说明。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ExportCategoryProjection(BaseModel):
    """导出预览中的一类数据：类别名、条数与预计大小。"""

    category: str = Field(description="数据类别标识（英文小写）。")
    label: str = Field(description="数据类别显示名（中文）。")
    item_count: int = Field(description="该类别的记录条数。")
    estimated_bytes: int = Field(description="该类别的预计导出字节数。")


class ExportPreviewProjection(BaseModel):
    """导出范围与预计大小预览（确认前可见，不含任何数据正文）。"""

    categories: list[ExportCategoryProjection] = Field(
        default_factory=list, description="逐类别范围清单。"
    )
    total_items: int = Field(description="全部类别记录总数。")
    total_estimated_bytes: int = Field(description="预计导出总字节数。")
    secrets_omitted: bool = Field(
        default=True,
        description="导出不包含 SMTP 授权码、会话令牌或运行密钥。",
    )


class DeleteAccountRequest(BaseModel):
    """账户删除请求：强确认文本 + 最近认证（API 层敏感门）。"""

    confirmation: str = Field(description="必须为「删除」的确认文本。")


class AccountDeletionStatus(StrEnum):
    """账户删除状态机：进行中/完成/失败可重试。"""

    DELETING = "deleting"
    COMPLETED = "completed"
    FAILED = "failed"


class AccountDeletionProjection(BaseModel):
    """账户删除状态投影：部分失败时可观察、可重试，绝不冒充成功。"""

    deletion_id: str = Field(description="删除状态记录标识。")
    account_id: str = Field(description="目标账户不可变内部 ID。")
    status: AccountDeletionStatus = Field(description="当前状态。")
    retry_count: int = Field(default=0, description="已重试次数。")
    last_error: str | None = Field(
        default=None, description="最近一次失败的中文原因（无秘密）。"
    )
    started_at: datetime = Field(description="删除开始时间。")
    completed_at: datetime | None = Field(
        default=None, description="删除完成或最后一次失败的时间。"
    )


class BackupCreateRequest(BaseModel):
    """创建本地加密备份请求：口令 + 最近认证（API 层敏感门）。"""

    passphrase: str = Field(description="备份口令（必填，用于派生加密密钥）。")


class RestoreRequest(BaseModel):
    """恢复请求：备份文件 + 口令 + 强确认文本。"""

    passphrase: str = Field(description="备份口令（派生加密密钥）。")
    confirmation: str = Field(description="必须为「恢复」的确认文本。")


class BackupFileEntry(BaseModel):
    """备份包内部文件清单条目（完整性摘要逐文件校验）。"""

    name: str = Field(description="包内相对路径（正斜杠）。")
    sha256: str = Field(description="文件内容 SHA-256 摘要。")
    size: int = Field(description="文件大小（字节）。")


class BackupManifest(BaseModel):
    """备份包明文清单：格式版本、加密参数、完整性摘要与数据统计。"""

    format_version: int = Field(description="备份格式版本（当前为 1）。")
    created_at: datetime = Field(description="备份创建时间（UTC）。")
    kdf_iterations: int = Field(description="口令派生迭代次数（PBKDF2-HMAC-SHA256）。")
    salt: str = Field(description="口令派生盐（hex）。")
    payload_sha256: str = Field(description="加密载荷整体 SHA-256 摘要。")
    payload_size: int = Field(description="加密载荷字节数。")
    files: list[BackupFileEntry] = Field(
        default_factory=list, description="载荷内文件清单（逐文件摘要）。"
    )
    schema_version: int = Field(description="备份时数据库模式版本。")
    account_count: int = Field(default=0, description="备份包含的账户数。")
    stats: dict[str, int] = Field(
        default_factory=dict, description="数据统计（对话/消息/对象等条数）。"
    )


class RestorePreview(BaseModel):
    """恢复预检结果：备份内容摘要与目标状态检查（供确认与失败原因）。"""

    ok: bool = Field(description="预检是否全部通过。")
    format_version: int = Field(description="备份格式版本。")
    schema_version: int = Field(description="备份时的数据库模式版本。")
    created_at: datetime | None = Field(default=None, description="备份创建时间。")
    account_count: int = Field(default=0, description="备份包含的账户数。")
    stats: dict[str, int] = Field(default_factory=dict, description="数据统计。")
    payload_size: int = Field(default=0, description="解压后数据字节数。")
    requires_space: int = Field(
        default=0, description="恢复所需的最小可用空间（含回滚余量）。"
    )
    reasons: list[str] = Field(
        default_factory=list, description="未通过原因（具体中文，逐条可操作）。"
    )


class DataLifecycleError(Exception):
    """数据生命周期域错误；message 为面向用户的中文说明。"""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(message)


__all__ = [
    "AccountDeletionProjection",
    "AccountDeletionStatus",
    "BackupCreateRequest",
    "BackupFileEntry",
    "BackupManifest",
    "DataLifecycleError",
    "DeleteAccountRequest",
    "ExportCategoryProjection",
    "ExportPreviewProjection",
    "RestorePreview",
    "RestoreRequest",
]
