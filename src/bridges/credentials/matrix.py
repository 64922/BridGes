"""ADR-0009 固定模型与供应商矩阵（单一事实源）。

BridGes 为每个逻辑能力类别固定一个经评测的具体模型快照，用户不能修改
（ADR-0006 / ADR-0009）。本模块是密钥设置页能力列表与真实探测的唯一
数据来源；任何新增或调整都必须作为受控变更执行合同测试与质量评测。

除 DuckDuckGo（无需 Key）外，全部云端智能能力共用当前账户的百炼密钥，
分别执行真实能力探测；失败只重试同一绑定，模型不可用时明确停用对应能力，
不得静默降级。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: 核心对话固定模型快照（qwen3.7-plus 评测版）。
CHAT_MODEL_ID = "qwen3.7-plus-2026-05-26"
#: 知识库向量化固定模型快照（1024 维）。
EMBEDDING_MODEL_ID = "text-embedding-v4"
#: 语音转写固定模型快照。
ASR_MODEL_ID = "qwen3-asr-flash-2025-09-08"
#: 单条回答朗读固定模型快照。
TTS_MODEL_ID = "qwen3-tts-flash-2025-11-27"
#: 图片生成与编辑固定模型快照。
IMAGE_MODEL_ID = "qwen-image-2.0-pro-2026-06-22"
#: 文生视频固定模型快照（Wan 已批准的同一百炼生态例外）。
VIDEO_MODEL_ID = "wan2.7-t2v-2026-06-12"


@dataclass(frozen=True)
class CapabilityBinding:
    """固定矩阵中的单个能力绑定（不可变）。"""

    capability_id: str  # 稳定能力标识，用于 API 与存储
    display_name: str  # 中文展示名
    model_id: str  # 固定模型快照
    probe_kind: str  # 探测类型：chat/embedding/asr/tts/image/video
    parameters: dict[str, Any] = field(default_factory=dict)  # 非秘密探测参数


#: ADR-0009 固定能力矩阵。顺序即密钥设置页展示顺序。
FIXED_CAPABILITY_MATRIX: tuple[CapabilityBinding, ...] = (
    CapabilityBinding(
        capability_id="chat",
        display_name="核心对话",
        model_id=CHAT_MODEL_ID,
        probe_kind="chat",
        parameters={"max_tokens": 8, "temperature": 0.0},
    ),
    CapabilityBinding(
        capability_id="embedding",
        display_name="知识库向量化",
        model_id=EMBEDDING_MODEL_ID,
        probe_kind="embedding",
        parameters={"dimensions": 1024},
    ),
    CapabilityBinding(
        capability_id="asr",
        display_name="语音转写",
        model_id=ASR_MODEL_ID,
        probe_kind="asr",
        parameters={"duration_seconds": 1},
    ),
    CapabilityBinding(
        capability_id="tts",
        display_name="语音朗读",
        model_id=TTS_MODEL_ID,
        probe_kind="tts",
        parameters={"voice": "Cherry", "format": "wav"},
    ),
    CapabilityBinding(
        capability_id="image",
        display_name="图片生成与编辑",
        model_id=IMAGE_MODEL_ID,
        probe_kind="image",
        parameters={"size": "1024*1024", "n": 1},
    ),
    CapabilityBinding(
        capability_id="video",
        display_name="视频生成",
        model_id=VIDEO_MODEL_ID,
        probe_kind="video",
        parameters={"size": "1280*720"},
    ),
)


def get_binding(capability_id: str) -> CapabilityBinding:
    """按能力标识返回固定绑定；未知能力抛 KeyError。"""
    for binding in FIXED_CAPABILITY_MATRIX:
        if binding.capability_id == capability_id:
            return binding
    raise KeyError(f"未知能力标识：{capability_id}")
