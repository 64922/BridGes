"""固定模型矩阵（ADR-0009 单一事实源，AI 领域所有）。

BridGes 为每个逻辑能力类别固定一个经评测的具体模型快照，用户不能修改
（ADR-0006 / ADR-0009）。模型 ID 是能力注册表与索引合同的架构资产：
核心对话、结构化生成、语音转写、回答朗读、图片、视频与知识库向量化
的全部调用方统一从这里取常量，不在各自模块硬编码模型正文。

本模块由 ``credentials/matrix.py`` 迁移而来（GQ-07）：原矩阵中"逐账户
真实能力探测"语义随账户百炼密钥一并清退，模型快照本身保留为架构资产。
变更任何常量都必须作为受控变更执行合同测试与质量评测。
"""

from __future__ import annotations

from bridges.video.constants import (
    VIDEO_DEFAULT_DURATION_SECONDS,
    VIDEO_DEFAULT_SIZE,
    VIDEO_MODEL_ID,
    VIDEO_SUPPORTED_DURATIONS_SECONDS,
    VIDEO_SUPPORTED_SIZES,
)

#: 核心对话固定模型快照（qwen3.7-plus 评测版）。
CHAT_MODEL_ID = "qwen3.7-plus-2026-05-26"
#: 知识库向量化固定模型快照（1024 维，ADR-0008 合同锁定）。
EMBEDDING_MODEL_ID = "text-embedding-v4"
#: 语音转写固定模型（qwen3-asr-flash 当前版本；2025-09-08 快照将于
#: 2026-10-10 下线且无免费额度，已随适配器同步迁移到当前版本，
#: ADR-0009 已同步更新）。
ASR_MODEL_ID = "qwen3-asr-flash"
#: 单条回答朗读固定模型快照。
TTS_MODEL_ID = "qwen3-tts-flash-2025-11-27"
#: 图片生成与编辑固定模型快照。
IMAGE_MODEL_ID = "qwen-image-2.0-pro-2026-06-22"

__all__ = [
    "ASR_MODEL_ID",
    "CHAT_MODEL_ID",
    "EMBEDDING_MODEL_ID",
    "IMAGE_MODEL_ID",
    "TTS_MODEL_ID",
    "VIDEO_DEFAULT_DURATION_SECONDS",
    "VIDEO_DEFAULT_SIZE",
    "VIDEO_MODEL_ID",
    "VIDEO_SUPPORTED_DURATIONS_SECONDS",
    "VIDEO_SUPPORTED_SIZES",
]
