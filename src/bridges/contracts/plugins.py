"""SKILL 插件中心契约（Issue 34）。

这些模型定义插件中心的公开表面：内置只读插件清单（固定版本/能力/来源/
授权/接收数据类别）、用户上传声明式包的安装检查结果与内容清单、账户级
安装/启停/卸载状态投影与内置能力演示结果。任何投影与审计都不携带 zip
包内容、脚本正文或附件正文；安装检查的拒绝原因必须是可操作的具体中文
说明。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class PluginKind(StrEnum):
    """插件来源类别：随应用发布的内置只读插件 / 用户上传包。"""

    BUILTIN = "builtin"
    USER = "user"


class PluginStatus(StrEnum):
    """用户包的安装状态机。

    installed 与 disabled 是运行注册表中的有效状态；install_failed 是
    可恢复的失败态——同一插件标识重新安装成功后即退出，绝不让失败记录
    冒充已安装包进入运行注册表。
    """

    INSTALLED = "installed"
    DISABLED = "disabled"
    INSTALL_FAILED = "install_failed"


class PluginFileKind(StrEnum):
    """包内文件类别（内容清单展示用）。"""

    SKILL_MD = "skill_md"
    REFERENCE = "reference"
    TEMPLATE = "template"
    RESOURCE = "resource"


class BuiltinPluginManifest(BaseModel):
    """内置只读插件的固定注册清单（版本进入运行与审计）。"""

    skill_id: str = Field(description="稳定注册标识，如 bridges-pdf。")
    name: str = Field(description="显示名（中文）。")
    version: str = Field(description="随应用发布的固定版本。")
    description: str = Field(description="能力说明（中文）。")
    source: str = Field(description="来源说明（BridGes 内置实现）。")
    license: str = Field(description="许可证声明。")
    capabilities: list[str] = Field(
        default_factory=list, description="能力清单（中文，逐项可读）。"
    )
    data_categories: list[str] = Field(
        default_factory=list, description="插件将接收的数据类别（中文）。"
    )
    read_only: bool = Field(
        default=True, description="内置只读：账户可启停，不可篡改或卸载。"
    )
    demo_kind: str | None = Field(
        default=None, description="演示方式：parse（真实附件解析）或 chat（跳转聊天既有合同）。"
    )


class PluginFileEntry(BaseModel):
    """安装检查内容清单中的一条文件记录。"""

    path: str = Field(description="包内相对路径（正斜杠）。")
    size: int = Field(description="文件大小（字节）。")
    kind: PluginFileKind = Field(description="文件类别（SKILL.md/参考/模板/资源）。")


class PluginCheckResult(BaseModel):
    """一次安装检查的结果：通过时携带清单与声明，拒绝时携带具体原因。"""

    ok: bool = Field(description="检查是否通过。")
    skill_id: str | None = Field(default=None, description="声明或推断的插件标识。")
    name: str | None = Field(default=None, description="声明或推断的显示名。")
    version: str | None = Field(default=None, description="SKILL.md 声明的固定版本。")
    description: str | None = Field(
        default=None, description="声明的能力说明（可选）。"
    )
    source: str | None = Field(default=None, description="声明的来源（可选）。")
    license: str | None = Field(default=None, description="声明的许可证（可选）。")
    capabilities: list[str] = Field(
        default_factory=list, description="声明的能力清单（可选）。"
    )
    data_categories: list[str] = Field(
        default_factory=list, description="声明将接收的数据类别（可选）。"
    )
    files: list[PluginFileEntry] = Field(
        default_factory=list, description="内容清单（仅检查通过时非空）。"
    )
    file_count: int = Field(default=0, description="包内文件总数（不含目录）。")
    total_bytes: int = Field(default=0, description="包内文件解压后总大小。")
    rejected_reasons: list[str] = Field(
        default_factory=list, description="拒绝原因（具体中文，逐条可操作）。"
    )


class BuiltinPluginProjection(BaseModel):
    """插件页展示的内置包：固定清单 + 当前账户启停状态。"""

    skill_id: str = Field(description="稳定注册标识。")
    name: str = Field(description="显示名。")
    version: str = Field(description="固定版本。")
    description: str = Field(description="能力说明。")
    source: str = Field(description="来源说明。")
    license: str = Field(description="许可证声明。")
    capabilities: list[str] = Field(default_factory=list, description="能力清单。")
    data_categories: list[str] = Field(
        default_factory=list, description="将接收的数据类别。"
    )
    read_only: bool = Field(default=True, description="内置只读标记。")
    enabled: bool = Field(description="当前账户是否启用。")
    demo_kind: str | None = Field(
        default=None, description="演示方式：parse 或 chat。"
    )


class UserPluginProjection(BaseModel):
    """插件页展示的用户包：安装状态、固定版本与失败原因。"""

    package_id: str = Field(description="安装记录标识。")
    plugin_id: str = Field(description="插件标识（包内声明）。")
    name: str = Field(description="显示名。")
    version: str = Field(description="安装时的固定版本。")
    description: str | None = Field(default=None, description="能力说明。")
    source: str | None = Field(default=None, description="来源说明。")
    license: str | None = Field(default=None, description="许可证声明。")
    capabilities: list[str] = Field(default_factory=list, description="能力清单。")
    data_categories: list[str] = Field(
        default_factory=list, description="将接收的数据类别。"
    )
    status: PluginStatus = Field(description="安装状态。")
    enabled: bool = Field(default=True, description="当前是否启用。")
    object_id: str | None = Field(
        default=None, description="包内容对象标识（账户隔离存储）。"
    )
    file_count: int = Field(default=0, description="包内文件数。")
    content_length: int = Field(default=0, description="包压缩后大小。")
    failure_reason: str | None = Field(
        default=None, description="安装失败的具体原因（中文）。"
    )
    installed_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="安装时间。"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="最近状态变更时间。"
    )


class PluginListProjection(BaseModel):
    """插件中心完整呈现：内置（含账户启停）+ 用户包（含失败态）。"""

    builtin: list[BuiltinPluginProjection] = Field(
        default_factory=list, description="内置只读插件（随应用发布）。"
    )
    user: list[UserPluginProjection] = Field(
        default_factory=list, description="当前账户的用户包（含安装失败记录）。"
    )


class PluginDemoProjection(BaseModel):
    """内置能力演示结果：真实解析的统计与预览片段，不含全文。"""

    skill_id: str = Field(description="演示的内置插件标识。")
    name: str = Field(description="插件显示名。")
    version: str = Field(description="固定版本。")
    filename: str = Field(description="演示的附件文件名。")
    parser_version: str = Field(description="实际使用的解析器版本标识。")
    pages: int = Field(default=0, description="解析页数（非分页类型为 0）。")
    sections: int = Field(default=0, description="解析章节数。")
    char_count: int = Field(default=0, description="解析文本字符数。")
    preview: str = Field(default="", description="文本预览片段（最多 500 字符）。")
    media_type: str = Field(default="", description="识别到的媒体类型。")


class PluginError(Exception):
    """插件域错误；message 为面向用户的中文说明。"""

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
    "BuiltinPluginManifest",
    "BuiltinPluginProjection",
    "PluginCheckResult",
    "PluginDemoProjection",
    "PluginError",
    "PluginFileEntry",
    "PluginFileKind",
    "PluginKind",
    "PluginListProjection",
    "PluginStatus",
    "UserPluginProjection",
]
