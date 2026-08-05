"""显式授权 MCP 插件管理契约（Issue 35）。

这些模型定义 MCP 插件中心的公开表面：固定版本 + 完整性 + 来源匹配的
安装描述（单个 MCP.yaml）、逐项权限清单（网络域名/文件读写目录/外部
命令/数据类别/敏感操作）、受限进程运行状态（starting/healthy/
disabled/failed/stopped）、每次调用只携带当前任务明确授权的数据切片、
首次敏感操作的再次确认（目标与影响）、撤权/启停/卸载后的真实调用统计
（次数/最近结果/失败原因）。任何投影与审计都不携带秘密、完整私人正文
或未授权数据；拒绝原因必须是可操作的具体中文说明。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class McpStatus(StrEnum):
    """MCP 运行状态机。

    starting 是进程启动中的瞬时态；healthy 表示配置合法、进程可惰性
    启动并接受调用；disabled 是用户停用（不再启动）；failed 是启动
    失败、进程崩溃或描述校验不匹配（带失败原因）；stopped 是撤权后
    终止的停止态。卸载后记录删除，不再展示。
    """

    STARTING = "starting"
    HEALTHY = "healthy"
    DISABLED = "disabled"
    FAILED = "failed"
    STOPPED = "stopped"


class McpSensitiveKind(StrEnum):
    """敏感操作类别：每次执行前必须再次确认（不扩展成永久授权）。"""

    WRITE_FILE = "write_file"
    RUN_COMMAND = "run_command"
    SEND_EXTERNAL = "send_external"


#: 允许 MCP 声明的数据类别受控集合（与调用 data_slice 一一对应）。
ALLOWED_DATA_CATEGORIES: frozenset[str] = frozenset(
    {"current_message_text", "attachment_files", "public_query_terms"}
)

#: 允许 MCP 声明的敏感操作受控集合。
ALLOWED_SENSITIVE_OPERATIONS: frozenset[McpSensitiveKind] = frozenset(
    {McpSensitiveKind.WRITE_FILE, McpSensitiveKind.RUN_COMMAND, McpSensitiveKind.SEND_EXTERNAL}
)


class McpPermissionManifest(BaseModel):
    """MCP 安装前可审计的权限声明；空集合即默认拒绝。"""

    network_domains: list[str] = Field(
        default_factory=list, description="允许访问的 HTTPS 网络域名（不含路径与协议）。"
    )
    filesystem_read: list[str] = Field(
        default_factory=list, description="允许读取的本地绝对目录列表。"
    )
    filesystem_write: list[str] = Field(
        default_factory=list, description="允许写入的本地绝对目录列表（敏感操作）。"
    )
    external_commands: list[str] = Field(
        default_factory=list, description="允许执行的受控外部命令程序名列表（敏感操作）。"
    )
    data_categories: list[str] = Field(
        default_factory=list, description="每次调用将接收的数据类别（受控集合）。"
    )
    sensitive_operations: list[McpSensitiveKind] = Field(
        default_factory=list, description="声明的敏感操作类别（受控集合）。"
    )


class McpInstallDescriptor(BaseModel):
    """用户提交的 MCP 安装描述（MCP.yaml 解析结果）。"""

    mcp_id: str = Field(description="稳定标识，如 bridges-note。")
    name: str = Field(description="显示名（中文）。")
    version: str = Field(description="固定版本（拒绝 latest/*/^ 前缀/空）。")
    description: str | None = Field(default=None, description="能力说明（中文）。")
    source: str = Field(description="来源：https URL 或 local。")
    integrity: str | None = Field(
        default=None, description="完整性声明：sha256:<hex>；安装时锁定描述哈希。"
    )
    command: list[str] = Field(description="启动命令 argv（程序 + 参数）。")
    permissions: McpPermissionManifest = Field(description="权限声明（默认拒绝）。")


class McpCheckResult(BaseModel):
    """一次安装检查的结果：通过时携带清单，拒绝时携带具体原因。"""

    ok: bool = Field(description="检查是否通过。")
    mcp_id: str | None = Field(default=None, description="声明的 MCP 标识。")
    name: str | None = Field(default=None, description="声明的显示名。")
    version: str | None = Field(default=None, description="声明的固定版本。")
    description: str | None = Field(default=None, description="声明的能力说明。")
    source: str | None = Field(default=None, description="声明的来源。")
    integrity: str | None = Field(default=None, description="声明的完整性信息。")
    command: list[str] = Field(default_factory=list, description="声明的启动命令。")
    permissions: McpPermissionManifest | None = Field(
        default=None, description="声明的权限清单（仅检查通过时非空）。"
    )
    integrity_sha256: str | None = Field(
        default=None, description="安装锁定的描述原文哈希（检查通过时计算）。"
    )
    rejected_reasons: list[str] = Field(
        default_factory=list, description="拒绝原因（具体中文，逐条可操作）。"
    )


class McpAttachmentSlice(BaseModel):
    """调用数据切片中的一条附件：只含元数据与已授权正文片段。"""

    filename: str = Field(description="附件文件名。")
    media_type: str = Field(default="", description="媒体类型。")
    preview: str = Field(default="", description="已授权正文片段（最多 2000 字符）。")


class McpDataSlice(BaseModel):
    """每次调用只接收当前消息明确授权的最小数据切片。

    不含画像、完整聊天历史、学习项目或其他账户数据；字段与权限清单的
    data_categories 一一对应（text→current_message_text，attachments→
    attachment_files），未声明的类别调用时拒绝。
    """

    text: str = Field(default="", description="当前消息明确授权的文本。")
    attachments: list[McpAttachmentSlice] = Field(
        default_factory=list, description="当前消息明确授权的附件片段。"
    )


class McpCallRequest(BaseModel):
    """一次真实 MCP 调用请求。"""

    tool: str = Field(description="要调用的 MCP 服务器工具名。")
    input: dict[str, Any] = Field(default_factory=dict, description="工具入参（不含秘密）。")
    data_slice: McpDataSlice = Field(default_factory=McpDataSlice, description="授权数据切片。")


class McpSensitiveConfirmation(BaseModel):
    """敏感操作再次确认：展示目标与影响，确认仅对本次调用有效。"""

    confirmation_id: str = Field(description="确认令牌（幂等）。")
    mcp_id: str = Field(description="发起调用的 MCP 标识。")
    kind: McpSensitiveKind = Field(description="敏感操作类别。")
    tool: str = Field(description="触发敏感操作的工具名。")
    target: str = Field(description="目标（绝对路径/命令名/域名，中文）。")
    impact: str = Field(description="影响说明（中文）。")
    status: str = Field(description="pending/approved/denied。")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="创建时间。"
    )


class McpCallResult(BaseModel):
    """一次调用的结果：成功 / 失败 / 敏感操作挂起待确认。"""

    status: str = Field(description="success/failed/sensitive_pending。")
    result: Any | None = Field(default=None, description="服务器返回结果（成功时）。")
    confirmation: McpSensitiveConfirmation | None = Field(
        default=None, description="敏感挂起时的确认载荷（202）。"
    )
    error_code: str | None = Field(default=None, description="失败分类码。")
    error_message: str | None = Field(default=None, description="可操作的中文提示。")


class McpCallRecord(BaseModel):
    """插件中心展示的一次真实调用记录（不含输入与正文）。"""

    call_id: str = Field(description="调用记录标识。")
    mcp_id: str = Field(description="被调用的 MCP 标识。")
    tool: str = Field(description="工具名。")
    status: str = Field(description="success/failed/denied。")
    error_code: str | None = Field(default=None, description="失败分类码。")
    error_message: str | None = Field(default=None, description="可操作的中文提示。")
    latency_ms: int = Field(default=0, description="调用耗时（毫秒）。")
    sensitive_ops: int = Field(default=0, description="本调用内完成的敏感操作数。")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="调用时间。"
    )


class McpServerProjection(BaseModel):
    """MCP 卡展示：固定清单 + 运行状态 + 真实调用统计。"""

    mcp_id: str = Field(description="稳定标识。")
    name: str = Field(description="显示名。")
    version: str = Field(description="固定版本。")
    description: str | None = Field(default=None, description="能力说明。")
    source: str = Field(description="来源。")
    integrity: str | None = Field(default=None, description="完整性声明。")
    command: list[str] = Field(default_factory=list, description="启动命令。")
    permissions: McpPermissionManifest = Field(description="权限清单（预览/撤权）。")
    status: McpStatus = Field(description="运行状态。")
    enabled: bool = Field(description="当前账户是否启用。")
    failure_reason: str | None = Field(default=None, description="失败原因（中文）。")
    installed_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="安装时间。"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="最近状态变更时间。"
    )
    call_count: int = Field(default=0, description="真实调用次数。")
    last_call_status: str | None = Field(default=None, description="最近一次调用结果。")
    last_call_error: str | None = Field(default=None, description="最近失败原因（中文）。")
    last_call_at: datetime | None = Field(default=None, description="最近调用时间。")


class McpListProjection(BaseModel):
    """MCP 分区完整呈现：当前账户全部 MCP 服务器与真实统计。"""

    servers: list[McpServerProjection] = Field(
        default_factory=list, description="当前账户的 MCP 服务器列表。"
    )


class McpError(Exception):
    """MCP 域错误；message 为面向用户的中文说明。"""

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
    "ALLOWED_DATA_CATEGORIES",
    "ALLOWED_SENSITIVE_OPERATIONS",
    "McpAttachmentSlice",
    "McpCallRecord",
    "McpCallRequest",
    "McpCallResult",
    "McpCheckResult",
    "McpDataSlice",
    "McpError",
    "McpInstallDescriptor",
    "McpListProjection",
    "McpPermissionManifest",
    "McpSensitiveConfirmation",
    "McpSensitiveKind",
    "McpServerProjection",
    "McpStatus",
]
