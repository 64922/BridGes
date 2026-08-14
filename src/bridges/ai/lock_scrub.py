"""运行锁落库前脱敏（Issue 16 媒体边缘接缝使用）。

Issue 10 录制器对自由文本字段（``error_message`` / ``degradation_reason``）
做关键词拒绝（api_key/authorization/secret/token），供应商原文（如鉴权
失败消息）常命中这些词导致锁无法持久化。媒体边缘接缝（图片替代文本、
图片/视频供应商取消）必须先脱敏再落库：稳定错误码保留，正文一律不进
入审计库。主生成链行为不变。
"""

from __future__ import annotations

from typing import Any

from bridges.contracts.ai import ModelRunLock

#: 与 ``SqliteModelRunLockRecorder._sanitize`` 的自由文本拒绝词一致。
_FORBIDDEN_TEXT_KEYWORDS = ("api_key", "authorization", "secret", "token")


def scrub_lock_text(lock: ModelRunLock) -> ModelRunLock:
    """返回自由文本不含凭据形态关键词的运行锁副本。

    未命中关键词时原样返回同一对象（零拷贝）；命中时把
    ``error_message`` / ``degradation_reason`` 替换为只含稳定错误码的
    中文说明。锁 ID、状态、参数、fallback 路径等审计事实保持不变。
    """
    updates: dict[str, Any] = {}
    for field in ("error_message", "degradation_reason"):
        text = getattr(lock, field)
        if text is None:
            continue
        lowered = str(text).lower()
        if any(keyword in lowered for keyword in _FORBIDDEN_TEXT_KEYWORDS):
            updates[field] = f"供应商调用失败（{lock.error_code or 'unknown'}）。"
    if not updates:
        return lock
    return lock.model_copy(update=updates)


__all__ = ["scrub_lock_text"]
