"""账户级模型凭据与能力探测契约。

本模块定义密钥设置页与 API 的公共表面：探测状态、不可变探测记录、
能力摘要与密钥设置投影。任何模型都不包含秘密正文——探测记录只保存
模型、区域、参数与追踪标识，密钥内容只在保存请求中出现一次并立即
进入受保护凭据存储。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, SecretStr


class ProbeStatus(StrEnum):
    """单项能力探测状态。"""

    NOT_PROBED = "not_probed"  # 尚未探测（未配置或刚刚变更）
    PROBING = "probing"  # 探测中
    AVAILABLE = "available"  # 可用
    UNAVAILABLE = "unavailable"  # 不可用（含中文原因）


class ProbeRecord(BaseModel):
    """一次能力探测的不可变记录。

    每次探测都会生成一条新记录（``probe_id`` 即追踪标识），记录创建后不再
    修改：模型、区域与参数是探测时的固定绑定快照，满足"重试保持同一绑定"
    的审计要求。
    """

    probe_id: str = Field(description="追踪标识，每次探测唯一。")
    capability_id: str = Field(description="能力标识（如 chat、embedding）。")
    model_id: str = Field(description="本次探测绑定的不可变模型快照。")
    region: str = Field(description="本次探测使用的区域。")
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="本次探测的非秘密调用参数快照。",
    )
    status: ProbeStatus = Field(description="探测结果状态。")
    probed_at: datetime = Field(description="探测完成（或开始）时间。")
    attempt_count: int = Field(
        default=1, description="同一绑定内的尝试次数（重试不换模型）。"
    )
    duration_ms: int = Field(default=0, description="探测耗时（毫秒）。")
    error_code: str | None = Field(default=None, description="稳定错误码。")
    error_message: str | None = Field(
        default=None, description="中文原因，面向用户且不回显任何秘密。"
    )


class CapabilityProbeSummary(BaseModel):
    """密钥设置页上单个能力的展示摘要。"""

    capability_id: str = Field(description="能力标识。")
    display_name: str = Field(description="中文能力名。")
    model_id: str = Field(description="固定模型快照，用户不可更换。")
    status: ProbeStatus = Field(description="当前探测状态。")
    message: str | None = Field(
        default=None, description="中文状态说明；不可用时为原因。"
    )
    can_retry: bool = Field(
        default=False, description="是否提供同模型重试入口（失败或未探测时）。"
    )
    probed_at: datetime | None = Field(
        default=None, description="最近一次探测完成时间。"
    )


class KeySettingsStatus(StrEnum):
    """模型密钥配置状态。"""

    UNCONFIGURED = "unconfigured"
    CONFIGURED = "configured"


class KeySettingsProjection(BaseModel):
    """受保护的模型密钥设置投影，不含任何秘密正文。"""

    status: KeySettingsStatus = Field(description="当前配置状态。")
    configured: bool = Field(description="当前账户是否已保存百炼 Key。")
    key_tail: str | None = Field(
        default=None,
        description="脱敏尾号（如 sk-…4F3a），绝不包含完整 Key。",
    )
    updated_at: datetime | None = Field(
        default=None, description="最近一次保存或删除时间。"
    )
    capabilities: list[CapabilityProbeSummary] = Field(
        default_factory=list,
        description="固定能力矩阵的逐项探测状态。",
    )
    message: str = Field(description="人类可读的状态说明。")
    next_step: str = Field(description="安全、可操作的建议。")

    @staticmethod
    def masked_tail(key: SecretStr) -> str:
        """返回 Key 的脱敏尾号：只保留最后 4 个字符。"""
        value = key.get_secret_value()
        return f"…{value[-4:]}" if value else ""


class KeySaveRequest(BaseModel):
    """保存或替换当前账户百炼 Key 的请求。

    请求体中的 Key 只在本请求内出现，服务端立即写入受保护凭据存储，
    不落入数据库明文字段、日志、API 响应或浏览器存储。
    """

    key: SecretStr = Field(
        description="百炼 API Key。", min_length=8, max_length=512
    )
