"""聊天自然语言能力路由的版本化合同。

路由只选择一个主能力并编译最小输入合同；它不执行供应商调用，也不读取
知识库正文。后续能力通过注册表追加，不覆盖已经注册的能力定义。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


ROUTE_CONTRACT_VERSION = "chat-route-v1"
IMAGE_ROUTE_CONTRACT_VERSION = "image-route-v1"
IMAGE_OUTPUT_CONTRACT_VERSION = "image-output-v1"


class RouteCapability(StrEnum):
    """统一聊天主能力名。"""

    IMAGE = "image"
    PAPER_SEARCH = "paper_search"
    HUMANIZER = "humanizer"
    VIDEO = "video"
    CAREER = "career"


class RouteOperation(StrEnum):
    """一次路由的执行动作。"""

    GENERATE = "generate"
    EDIT = "edit"
    CLARIFY = "clarify"
    REJECT = "reject"


class ImageRouteParameters(BaseModel):
    """图片模型可接受的稳定参数快照。"""

    model_config = ConfigDict(extra="forbid")

    size: str = Field(
        default="1024*1024",
        pattern=r"^(1024\*1024|1536\*1024|1024\*1536)$",
        description="图片尺寸；只允许已登记模型支持的尺寸。",
    )
    n: int = Field(default=1, ge=1, le=1, description="输出张数；当前固定为 1。")


class ImageRouteContract(BaseModel):
    """图片生成/编辑提交给图片域的稳定输入合同。"""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(
        default=IMAGE_ROUTE_CONTRACT_VERSION, description="图片路由合同版本。"
    )
    capability: RouteCapability = Field(default=RouteCapability.IMAGE)
    operation: RouteOperation = Field(description="generate 或 edit。")
    prompt: str = Field(min_length=1, max_length=2000, description="图片提示词或编辑指令。")
    source_object_id: str | None = Field(
        default=None, description="编辑时当前账户知识库中的图片对象标识。"
    )
    parameters: ImageRouteParameters = Field(
        default_factory=ImageRouteParameters, description="尺寸与输出约束。"
    )
    output_version: str = Field(
        default=IMAGE_OUTPUT_CONTRACT_VERSION, description="输出资产合同版本。"
    )

    @model_validator(mode="after")
    def validate_operation_source(self) -> "ImageRouteContract":
        if self.operation == RouteOperation.GENERATE and self.source_object_id is not None:
            raise ValueError("生成合同不能携带编辑来源。")
        if self.operation == RouteOperation.EDIT and self.source_object_id is None:
            raise ValueError("编辑合同必须携带来源图片。")
        return self


class RouteDecision(BaseModel):
    """一条消息的路由快照；持久化后重试与恢复不得重新分类。"""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(default=ROUTE_CONTRACT_VERSION, description="路由合同版本。")
    capability: RouteCapability = Field(description="选中的主能力。")
    operation: RouteOperation = Field(description="本次路由动作。")
    confidence: float = Field(ge=0, le=1, description="确定性路由置信度。")
    reason: str = Field(min_length=1, max_length=240, description="路由判定原因。")
    contract: ImageRouteContract | None = Field(
        default=None, description="图片能力的输入合同；其他能力暂不携带。"
    )
    clarification_question: str | None = Field(
        default=None, max_length=240, description="需要用户补充时唯一的中文问题。"
    )
    error_code: str | None = Field(default=None, description="拒绝路由时的稳定错误码。")
    error_message: str | None = Field(
        default=None, max_length=240, description="拒绝路由时的可操作中文说明。"
    )
    competing_capabilities: list[RouteCapability] = Field(
        default_factory=list, max_length=5, description="检测到的互斥能力集合。"
    )


def route_json(decision: RouteDecision | None) -> dict[str, Any] | None:
    """返回适合消息 JSON 列持久化的路由快照。"""

    return decision.model_dump(mode="json") if decision is not None else None


__all__ = [
    "IMAGE_OUTPUT_CONTRACT_VERSION",
    "IMAGE_ROUTE_CONTRACT_VERSION",
    "ROUTE_CONTRACT_VERSION",
    "ImageRouteContract",
    "ImageRouteParameters",
    "RouteCapability",
    "RouteDecision",
    "RouteOperation",
    "route_json",
]
