"""听写与单条回答朗读的公开契约（Issue 30）。

听写固定使用 qwen3-asr-flash：用户停止录音后才提交完整音频，
收到可编辑文本后由用户自行决定是否发送，绝不自动发送。朗读固定使用
qwen3-tts-flash-2025-11-27：每条已完成的助手文本回答可独立生成朗读，
播放/暂停/继续/停止由前端音频会话管理，这里只固化生成结果与失败语义。
两项能力共用当前账户百炼密钥与独立真实探测快照；失败只重试同一模型
快照，能力不可用时由 API 层禁用入口并说明原因。

音频数据最小留存：听写音频只在请求体内存中直传 ASR，不落盘、不生成
对象记录；生成朗读音频转存到账户隔离的加密对象库，删除/重试时先清理
旧对象，任何情况下不遗留跨账户或孤儿音频。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class DictationStatus(StrEnum):
    """听写结果状态。

    - ``success``：转写完成，transcript 可直接编辑后发送；
    - ``failed``：转写失败，error_code/error_message 说明原因，
      retryable 表示是否可原样重试同一音频。
    """

    SUCCESS = "success"
    FAILED = "failed"


class DictationProjection(BaseModel):
    """一次听写请求的结果投影；不包含音频本身与账户信息。

    转写文本进入前端输入框可自由编辑，由用户自行决定发送，系统绝不
    自动产生用户消息。
    """

    status: DictationStatus = Field(description="转写结果状态。")
    transcript: str = Field(
        default="", description="转写文本（成功时非空，可编辑回填输入框）。"
    )
    model_id: str | None = Field(
        default=None, description="实际使用的固定 ASR 模型快照。"
    )
    duration_ms: int | None = Field(
        default=None, description="本次转写调用耗时（毫秒）。"
    )
    error_code: str | None = Field(default=None, description="稳定错误码。")
    error_message: str | None = Field(
        default=None, description="可操作的中文错误说明。"
    )
    retryable: bool = Field(
        default=False, description="失败后是否可原样重试同一音频。"
    )
    created_at: datetime = Field(description="转写完成时间。")


class ReadAloudState(StrEnum):
    """单条回答朗读的持久化状态。

    - ``not_generated``：尚未生成朗读（可发起生成）；
    - ``ready``：音频已生成并转存到账户对象库，可请求播放；
    - ``failed``：生成失败，error_message 说明原因，可重试。
    """

    NOT_GENERATED = "not_generated"
    READY = "ready"
    FAILED = "failed"


class ReadAloudProjection(BaseModel):
    """单条助手回答朗读的公开投影；不包含音频内容与账户信息。

    ``audio_ref`` 是账户对象库中的对象 ID，播放时通过音频端点按账户
    授权校验后流式返回。同一条回答的受控重试复用同一消息正文重新合成，
    成功后旧音频先清理再写入新对象。
    """

    message_id: str = Field(description="所属助手消息标识。")
    state: ReadAloudState = Field(description="朗读状态。")
    model_id: str | None = Field(
        default=None, description="实际使用的固定 TTS 模型快照。"
    )
    audio_ref: str | None = Field(
        default=None, description="账户对象库中的音频对象 ID；ready 时存在。"
    )
    media_type: str | None = Field(
        default=None, description="音频媒体类型（ready 时存在）。"
    )
    content_length: int | None = Field(
        default=None, description="音频字节数（ready 时存在）。"
    )
    char_count: int | None = Field(
        default=None, description="实际合成的字符数。"
    )
    truncated: bool = Field(
        default=False, description="是否因长度限制截断（明确披露，不静默）。"
    )
    error_code: str | None = Field(default=None, description="稳定错误码。")
    error_message: str | None = Field(
        default=None, description="可操作的中文错误说明。"
    )
    retryable: bool = Field(
        default=False, description="失败后是否可对同一条回答重试生成。"
    )
    generated_at: datetime | None = Field(
        default=None, description="最近一次生成完成时间；未生成为 None。"
    )


class SpeechError(Exception):
    """听写/朗读域错误；message 为面向用户的中文说明。"""

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
    "DictationProjection",
    "DictationStatus",
    "ReadAloudProjection",
    "ReadAloudState",
    "SpeechError",
]
