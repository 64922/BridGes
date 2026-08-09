"""旧能力退役期间的兼容合同。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RetiredCapabilityError(BaseModel):
    """稳定的 410 响应体，不回显账户、路径参数或请求正文。"""

    error: str = Field(description="稳定退役错误码。")
    message: str = Field(description="中文退役说明。")
    replacement_path: str = Field(description="继续使用产品的替代路径。")
    endpoint: str = Field(description="稳定兼容端点标识。")
    service_version: str = Field(description="产生该响应的服务版本。")
    traffic_class: str = Field(default="real", description="real 或 probe")


__all__ = ["RetiredCapabilityError"]
