"""运行期主模型配置：验证结果、原子激活与按运行锁定（V2 Issue 09）。

用户可在设置中手填 Qwen 主模型 ID。该 ID 通过「百炼精确元数据核对 + 真实
能力探测」后，才作为**运行配置**原子激活，并自下一条消息起用于新会话与
已有会话的下一轮；进行中的轮次沿用启动时锁定的模型，历史消息的模型记录
不改写（``docs/v2/architecture.md`` §7）。

边界：

- 覆盖范围是主对话与其对齐的结构化输出、画像提取、视觉理解与 OCR 能力
  （:data:`CONFIGURABLE_CAPABILITIES`）——用户的运行配置就是「视觉/OCR 与
  核心对话同一模型」这一既有对齐关系的唯一来源，不留隐藏的固定主模型调用；
- 知识库向量化（``EMBEDDING_MODEL_ID``）与索引版本独立固定，绝不随主模型
  改变，换向量模型需要重建索引而不是复用；
- 语音、朗读、图片与视频能力保持出厂矩阵绑定（ADR-0009/ADR-0007），不在
  本次手填范围内。

激活结果经 ``application_state`` 的单一命名空间整体替换写入（单事务、全量
提交），因此 API 进程与后台执行器共享同一真相源：执行器进程在每次模型调用
前读取该值，换模型无须重启进程；进程内缓存只在读取失败时兜底。
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field

from bridges.ai.fixed_models import (
    CHAT_MODEL_ID,
    FACTORY_MAIN_MODEL_CAPABILITIES,
    FACTORY_MAIN_MODEL_CONTEXT_WINDOW,
    FACTORY_MAIN_MODEL_MAX_INPUT_TOKENS,
    MODEL_BY_CAPABILITY,
)
from bridges.ai.model_metadata import MODEL_METADATA_VERSION
from bridges.contracts.ai import ModelCapabilities

#: 运行配置持久化命名空间（单一键：整体替换即原子激活）。
MODEL_CONFIG_NAMESPACE = "run_model_config"

#: 用户手填主模型 ID 覆盖的 capability：主对话、结构化输出、画像提取与
#: 与之对齐的视觉/OCR。向量化、语音、朗读、图片与视频不在其中。
CONFIGURABLE_CAPABILITIES: frozenset[str] = frozenset(
    {
        "qwen_text_chat",
        "qwen_structured_output",
        "qwen_profile_extraction",
        "qwen_vision",
        "qwen_ocr",
    }
)

#: 出厂矩阵中绑定到主对话快照的 capability（由矩阵派生，避免清单漂移）。
#: 它们必须全部可手填，否则会留下隐藏的固定主模型调用。
_ALIGNED_FACTORY_CAPABILITIES: frozenset[str] = frozenset(
    name for name, model in MODEL_BY_CAPABILITY.items() if model == CHAT_MODEL_ID
)


class ModelConfigSource(StrEnum):
    """运行配置的来源：出厂批准快照或用户在设置中验证保存。"""

    FACTORY = "factory"
    SETTINGS = "settings"


class ModelConfigStatePort(Protocol):
    """运行配置持久化端口（由 ``bridges.persistence.StateStore`` 实现）。"""

    def load(self, namespace: str) -> dict[str, Any] | None:
        """读取一个命名空间；不存在返回 None。"""

    def save(self, namespace: str, state: dict[str, Any]) -> None:
        """整体替换一个命名空间（单事务原子写）。"""


class RunModelConfigSnapshot(BaseModel):
    """一次激活的主模型运行配置（含验证证据的元数据）。"""

    model_id: str = Field(description="主对话/视觉/OCR 实际使用的模型 ID。")
    capabilities: ModelCapabilities = Field(description="验证通过的模型能力档案。")
    context_window: int | None = Field(default=None, description="上下文窗口（token）。")
    max_input_tokens: int | None = Field(default=None, description="最大输入额度（token）。")
    metadata_version: str = Field(
        default=MODEL_METADATA_VERSION, description="元数据解析合同版本。"
    )
    validated_at: datetime | None = Field(
        default=None, description="最近一次通过验证并激活的时间。"
    )
    revision: int = Field(default=0, description="激活序号；0 表示出厂快照。")
    source: ModelConfigSource = Field(
        default=ModelConfigSource.FACTORY, description="配置来源。"
    )


def factory_run_model_config() -> RunModelConfigSnapshot:
    """出厂运行配置：批准快照 ``CHAT_MODEL_ID`` 及其能力档案。"""
    return RunModelConfigSnapshot(
        model_id=CHAT_MODEL_ID,
        capabilities=FACTORY_MAIN_MODEL_CAPABILITIES,
        context_window=FACTORY_MAIN_MODEL_CONTEXT_WINDOW,
        max_input_tokens=FACTORY_MAIN_MODEL_MAX_INPUT_TOKENS,
        revision=0,
        source=ModelConfigSource.FACTORY,
    )


def configured_model_id(capability_name: str, config: RunModelConfigSnapshot) -> str | None:
    """返回某 capability 在该运行配置下应使用的模型 ID。

    只覆盖 :data:`CONFIGURABLE_CAPABILITIES`；其余能力保持出厂矩阵绑定
    （向量化独立固定是其中的硬约束）。
    """
    if capability_name not in CONFIGURABLE_CAPABILITIES:
        return None
    return config.model_id


def is_configurable_capability(capability_name: str) -> bool:
    """某 capability 是否允许被运行配置（或运行级锁定）覆盖模型绑定。

    非可配置能力（向量化、语音、图片、视频等）永远取出厂矩阵绑定：运行级
    锁定也必须经此判定，避免一个锁定值把向量化等固定项带走。
    """
    return capability_name in CONFIGURABLE_CAPABILITIES


class RunModelConfigProvider:
    """运行配置的唯一读取/激活入口（进程内缓存 + 共享持久化真相源）。"""

    def __init__(self, state_port: ModelConfigStatePort | None = None) -> None:
        self._state_port = state_port
        self._lock = threading.RLock()
        self._cached = factory_run_model_config()
        self._load_error: str | None = None

    @property
    def load_error(self) -> str | None:
        """最近一次读取失败的中文原因（读取成功时为 None）。"""
        with self._lock:
            return self._load_error

    def snapshot(self) -> RunModelConfigSnapshot:
        """返回当前生效的运行配置。

        配置了持久化端口时每次都从共享状态表读取（后台执行器进程因此能在
        下一次模型调用采用新配置）；读取失败或尚无激活记录时回落到进程内
        缓存/出厂快照，绝不因为状态表异常而中断模型调用。
        """
        if self._state_port is None:
            with self._lock:
                return self._cached
        with self._lock:
            try:
                payload = self._state_port.load(MODEL_CONFIG_NAMESPACE)
            except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
                self._load_error = (
                    f"读取主模型运行配置失败（{exc.__class__.__name__}），"
                    "继续使用上一次生效的配置。"
                )
                return self._cached
            if payload is None:
                self._load_error = None
                self._cached = factory_run_model_config()
                return self._cached
            try:
                self._cached = RunModelConfigSnapshot.model_validate(payload)
            except ValueError as exc:
                self._load_error = (
                    f"主模型运行配置内容无法解析（{exc.__class__.__name__}），"
                    "继续使用上一次生效的配置。"
                )
                return self._cached
            self._load_error = None
            return self._cached

    def activate(
        self,
        *,
        model_id: str,
        capabilities: ModelCapabilities,
        context_window: int | None,
        max_input_tokens: int | None,
        validated_at: datetime | None,
        metadata_version: str = MODEL_METADATA_VERSION,
    ) -> RunModelConfigSnapshot:
        """原子激活一次通过验证的运行配置，返回已生效的快照。

        先写持久化命名空间（单事务），再更新进程内缓存：任何读方要么看到旧
        配置、要么看到完整的新配置，不存在半套状态。
        """
        with self._lock:
            snapshot = RunModelConfigSnapshot(
                model_id=model_id,
                capabilities=capabilities,
                context_window=context_window,
                max_input_tokens=max_input_tokens,
                metadata_version=metadata_version,
                validated_at=validated_at,
                revision=self.snapshot().revision + 1,
                source=ModelConfigSource.SETTINGS,
            )
            if self._state_port is not None:
                self._state_port.save(
                    MODEL_CONFIG_NAMESPACE, snapshot.model_dump(mode="json")
                )
            self._cached = snapshot
            self._load_error = None
            return snapshot


def validate_configurable_capabilities() -> None:
    """架构自检：可手填的能力必须覆盖出厂矩阵中的全部对齐项。

    主对话/结构化输出/画像提取/视觉/OCR 共用一个主模型是出厂合同；任一
    对齐项漏出 :data:`CONFIGURABLE_CAPABILITIES` 都会留下隐藏的固定主模型
    调用，故在模块导入时直接失败。
    """
    missing = _ALIGNED_FACTORY_CAPABILITIES - CONFIGURABLE_CAPABILITIES
    if missing:
        raise RuntimeError(
            "运行配置未覆盖对齐能力：" + "、".join(sorted(missing))
        )
    for name in CONFIGURABLE_CAPABILITIES:
        if name not in MODEL_BY_CAPABILITY:
            raise RuntimeError(f"运行配置包含未注册能力：{name}")
    if "qwen_embedding" in CONFIGURABLE_CAPABILITIES:
        raise RuntimeError("向量化能力必须独立固定，不得随主模型改变。")


validate_configurable_capabilities()
