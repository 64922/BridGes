"""固定模型矩阵（ADR-0009 单一事实源，AI 领域所有）。

BridGes 为每个逻辑能力类别固定一个经评测的具体模型快照，用户不能修改
（ADR-0006 / ADR-0009）。模型 ID 是能力注册表与索引合同的架构资产：
核心对话、结构化生成、画像提取、视觉理解、OCR、语音转写、回答朗读、
图片、视频与知识库向量化的全部调用方统一从这里取常量，不在各自模块
硬编码模型正文（Issue 09 起由静态架构测试强制执行）。

本模块由 ``credentials/matrix.py`` 迁移而来（GQ-07）：原矩阵中"逐账户
真实能力探测"语义随账户百炼密钥一并清退，模型快照本身保留为架构资产。
变更任何常量都必须作为受控变更执行合同测试与质量评测。

V2 Issue 09 起，主对话／视觉／OCR 的实际绑定由用户在设置中手填并经
「百炼元数据核对 + 真实能力探测」验证的运行配置给出（ADR-0031）；本
矩阵仍是**出厂批准快照与生产组合门禁**的事实源：未激活用户配置时运行
配置即 :data:`CHAT_MODEL_ID`，注册表与组合校验器始终以矩阵为准，
向量化（``EMBEDDING_MODEL_ID``）不随主模型改变。
"""

from __future__ import annotations

from bridges.contracts.ai import ModelCapabilities

#: 核心对话/推理/视觉理解/工具调用固定模型快照（qwen3.7-plus 评测版，
#: ADR-0009 唯一绑定；结构化输出与画像提取复用同一快照）。
CHAT_MODEL_ID = "qwen3.7-plus-2026-05-26"
#: 知识库向量化固定模型快照（1024 维，ADR-0008 合同锁定）。
EMBEDDING_MODEL_ID = "text-embedding-v4"
#: 语音转写固定模型（qwen3-asr-flash 当前版本；2025-09-08 快照将于
#: 2026-10-10 下线且无免费额度，已随适配器同步迁移到当前版本，
#: ADR-0009 已同步更新）。
ASR_MODEL_ID = "qwen3-asr-flash"
#: 长音频/文件转写变体（qwen_asr_long 能力，同一 ASR 固定绑定的文件
#: 转写入口；Issue 09 固化矩阵的一部分，与 ``ASR_MODEL_ID`` 同族）。
ASR_LONG_MODEL_ID = "qwen3-asr-flash-filetrans"
#: 单条回答朗读固定模型快照。
TTS_MODEL_ID = "qwen3-tts-flash-2025-11-27"
#: 图片生成与编辑固定模型快照。
IMAGE_MODEL_ID = "qwen-image-2.0-pro-2026-06-22"
#: 文生视频固定模型快照（ADR-0007：Wan 是模型矩阵唯一非 Qwen 系列例外）。
VIDEO_MODEL_ID = "wan2.7-t2v-2026-06-12"
#: 视觉理解固定模型快照（Issue 09：对齐 ``CHAT_MODEL_ID``；对齐以真实
#: 兼容 smoke 为前提，未证实前生产组合门禁以
#: ``vision_ocr_compatibility_unproven`` 失败关闭，禁止未经 ADR 的例外）。
VISION_MODEL_ID = CHAT_MODEL_ID
#: OCR 固定模型快照（Issue 09：与视觉理解同一对齐/门禁语义）。
OCR_MODEL_ID = CHAT_MODEL_ID

#: 出厂批准快照的能力档案（V2 Issue 09）：用户手填主模型 ID 并通过验证前，
#: 运行配置即此档案；设置页展示的「实际能力」与上下文长度同源。四项能力
#: 与 ``CHAT_MODEL_ID`` 批准绑定的声明值一致（ADR-0009 多模态核心快照）。
FACTORY_MAIN_MODEL_CAPABILITIES = ModelCapabilities(
    text=True,
    image=True,
    tool_calling=True,
    structured_output=True,
)
#: 出厂批准快照记录的上下文窗口（token）。用户激活的模型以百炼元数据返回
#: 的真实值为准，此常量只描述出厂快照。
FACTORY_MAIN_MODEL_CONTEXT_WINDOW = 1_000_000
#: 出厂批准快照记录的最大输入额度（token）。
FACTORY_MAIN_MODEL_MAX_INPUT_TOKENS = 999_000

#: 文生视频固定尺寸（与能力矩阵探测一致的默认值，1280*720 16:9）。
VIDEO_DEFAULT_SIZE = "1280*720"
#: 文生视频支持的画面尺寸（横屏/竖屏各一档）。
VIDEO_SUPPORTED_SIZES = ("1280*720", "720*1280")
#: 文生视频默认时长（秒）。
VIDEO_DEFAULT_DURATION_SECONDS = 5
#: 文生视频支持的时长（秒）。
VIDEO_SUPPORTED_DURATIONS_SECONDS = (5, 10)

#: 活跃 MODEL capability → 批准模型快照的完整矩阵（Issue 09 固化）。
#: 生产组合校验器（``bridges.ai.composition``）以此为准枚举每个活跃
#: capability 的绑定；注册表、adapter、runtime、服务与测试合同一律从
#: 本矩阵或对应常量取值，不得另写生产模型字面量。
MODEL_BY_CAPABILITY: dict[str, str] = {
    "qwen_text_chat": CHAT_MODEL_ID,
    "qwen_structured_output": CHAT_MODEL_ID,
    "qwen_profile_extraction": CHAT_MODEL_ID,
    "qwen_vision": VISION_MODEL_ID,
    "qwen_ocr": OCR_MODEL_ID,
    "qwen_asr_short": ASR_MODEL_ID,
    "qwen_asr_long": ASR_LONG_MODEL_ID,
    "qwen_tts": TTS_MODEL_ID,
    "qwen_image": IMAGE_MODEL_ID,
    "qwen_wan": VIDEO_MODEL_ID,
    "qwen_embedding": EMBEDDING_MODEL_ID,
}

__all__ = [
    "ASR_LONG_MODEL_ID",
    "ASR_MODEL_ID",
    "CHAT_MODEL_ID",
    "EMBEDDING_MODEL_ID",
    "FACTORY_MAIN_MODEL_CAPABILITIES",
    "FACTORY_MAIN_MODEL_CONTEXT_WINDOW",
    "FACTORY_MAIN_MODEL_MAX_INPUT_TOKENS",
    "IMAGE_MODEL_ID",
    "MODEL_BY_CAPABILITY",
    "OCR_MODEL_ID",
    "TTS_MODEL_ID",
    "VIDEO_DEFAULT_DURATION_SECONDS",
    "VIDEO_DEFAULT_SIZE",
    "VIDEO_MODEL_ID",
    "VIDEO_SUPPORTED_DURATIONS_SECONDS",
    "VIDEO_SUPPORTED_SIZES",
    "VISION_MODEL_ID",
]
