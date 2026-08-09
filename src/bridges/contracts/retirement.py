"""用户扩展退役期间复用的兼容错误契约。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RetiredCapabilityError(BaseModel):
    """旧客户端可稳定识别、且不携带敏感请求数据的 410 响应。"""

    error: str = Field(description="稳定错误码")
    message: str = Field(description="退役说明")
    replacement_path: str = Field(description="继续使用产品的替代路径")
    endpoint: str = Field(description="稳定兼容端点标识")
    service_version: str = Field(description="服务版本")
    traffic_class: str = Field(description="real 或 probe")


__all__ = ["RetiredCapabilityError"]
