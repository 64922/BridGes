"""后台执行器的上传附件清理步骤。"""

from __future__ import annotations

from datetime import datetime

from bridges.chat.attachments import ChatAttachmentService


def sweep_expired_attachments(
    service: ChatAttachmentService, *, cutoff: datetime
) -> list[str]:
    """清理超期未绑定附件和草稿，分别保留可诊断的失败摘要。"""
    summaries: list[str] = []
    try:
        swept = service.sweep_unbound(cutoff)
        if swept:
            summaries.append(f"worker: 清理 {swept} 个过期未绑定附件。")
    except Exception as exc:  # noqa: BLE001 - 清理失败记录但不退出循环
        summaries.append(f"worker: 附件清理出错：{exc}")
    try:
        swept = service.sweep_drafts(cutoff)
        if swept:
            summaries.append(f"worker: 清理 {swept} 个过期附件草稿。")
    except Exception as exc:  # noqa: BLE001 - 草稿清理失败不应停止循环
        summaries.append(f"worker: 草稿清理出错：{exc}")
    return summaries
