"""画像工单 17：历史重整的合同词表、窗口与确定性标识。

本模块只定义跨层共享的稳定取值与构造函数，不接触数据库、不调用模型：

- 策略版本（``policy_version``）与状态版本（``state_version``）随每次重
  整记录，恢复时版本不一致必须显式拒绝，不做静默兼容；
- 幂等标识：``rebuild_id`` 由（账户、内容状态摘要、预览合同版本、策略
  版本、窗口）确定性派生，同一批次重复执行得到同一标识，不重复生效；
- 台账词表：运行状态 / 逐条状态 / 跳过原因。跳过原因与预览
  ``chat_scope`` 的计数键一一对应（``unfinished`` 等），运行期屏障使用
  与提交前复核一致的稳定错误码（``source_invalidated`` 等）。

台账只保存标识、计数与摘要，绝不保存消息或条目正文。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from bridges.profiles.history_contracts import digest_lines
from bridges.profiles.history_preview import ProfileHistoryError

#: 历史重整的本地门控/窗口/任务分类策略版本；策略变化必须显式拒绝恢复。
HISTORY_REBUILD_POLICY_VERSION = "profile-history-rebuild-policy-v1"
#: 台账载荷（baseline_json）与报告结构的合同版本。
HISTORY_REBUILD_STATE_VERSION = "profile-history-rebuild-state-v1"

#: 工作目录内的固定文件名（工作副本与恢复前提分开保存）。
REBUILD_METADATA_FILENAME = "rebuild.json"
REBUILD_PREVIEW_FILENAME = "preview.json"
REBUILD_BACKUP_FILENAME = "history.db"
REBUILD_WORKING_DIRNAME = "working"
REBUILD_WORKING_DB_FILENAME = "bridges.db"
REBUILD_REPORT_FILENAME = "reconciliation.json"


class ProfileRebuildError(ProfileHistoryError):
    """重整硬门失败；错误码供 CLI 与审计调用方稳定分支。"""


class RebuildStatus(StrEnum):
    """一次重整的运行状态（台账的单一权威取值）。"""

    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class RebuildItemStatus(StrEnum):
    """逐条进度状态。"""

    PENDING = "pending"
    SCHEDULED = "scheduled"
    EXTRACTED = "extracted"
    SKIPPED = "skipped"
    FAILED = "failed"


class RebuildSkipReason(StrEnum):
    """跳过原因词表（与预览 chat_scope 计数键和提交前复核错误码对齐）。"""

    # 种子门控：与预览 chat_scope 计数一一对应，任何一项都不进入模型调用。
    UNFINISHED = "unfinished"
    EMPTY = "empty"
    TOMBSTONED = "tombstoned"
    CONTENT_BLOCKED = "content_blocked"
    WITHDRAWN_SUPPRESSED = "withdrawn_suppressed"
    RECORDING_OFF = "recording_off"
    NO_SIGNAL = "no_signal"
    # 管道既有结论：材料指令消息 / 来源不存在。
    MATERIAL_RUN_PRESENT = "material_run_present"
    SOURCE_MISSING = "source_missing"
    # 运行期屏障：提交前复核（墓碑/控制/版本/来源变化）拒绝迟到写入。
    SOURCE_INVALIDATED = "source_invalidated"
    PRIVACY_BLOCKED = "privacy_blocked"
    VERSION_UNAVAILABLE = "version_unavailable"
    BLOCKED_AT_SCHEDULE = "blocked_at_schedule"


#: 提交前复核把运行终结为屏障跳过时使用的稳定错误码（不是失败）。
BARRIER_EXHAUSTION_CODES: dict[str, RebuildSkipReason] = {
    "profile_extraction_source_invalidated": RebuildSkipReason.SOURCE_INVALIDATED,
    "profile_extraction_privacy_blocked": RebuildSkipReason.PRIVACY_BLOCKED,
    "profile_extraction_version_unavailable": RebuildSkipReason.VERSION_UNAVAILABLE,
}


@dataclass(frozen=True)
class RebuildWindow:
    """有界来源窗口：``messages.created_at`` 上的半开区间 ``[start, end)``。"""

    start_at: str | None
    end_at: str | None

    def as_dict(self) -> dict[str, object]:
        return {"start_at": self.start_at, "end_at": self.end_at}

    @property
    def bounded(self) -> bool:
        return self.start_at is not None or self.end_at is not None

    def contains(self, created_at: str) -> bool:
        """按解析后的时间比较；无法解析的时间戳不落入任何有界窗口。"""

        moment = _parse_moment(created_at)
        if moment is None:
            return not self.bounded
        if self.start_at is not None:
            start = _parse_moment(self.start_at)
            if start is None or moment < start:
                return False
        if self.end_at is not None:
            end = _parse_moment(self.end_at)
            if end is None or moment >= end:
                return False
        return True


def _parse_moment(value: str) -> datetime | None:
    """解析 ISO 时间戳为 UTC；缺失时区按 UTC 处理，损坏返回 None。"""

    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def rebuild_id_for(
    *,
    account_id: str,
    state_digest: str,
    policy_version: str,
    preview_version: str,
    window: RebuildWindow,
) -> str:
    """确定性派生重整标识：同一批次重复执行得到同一标识（幂等入口）。"""

    digest = digest_lines(
        [
            f"account={account_id}",
            f"state={state_digest}",
            f"policy={policy_version}",
            f"preview={preview_version}",
            f"window_start={window.start_at or ''}",
            f"window_end={window.end_at or ''}",
        ]
    )
    return f"profile-rebuild-{digest[:32]}"


def now_text(now: datetime | None = None) -> str:
    """台账时间戳：UTC ISO 秒精度（操作时间，不承担事实时间语义）。"""

    moment = now or datetime.now(UTC)
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def text_digest(text: str) -> str:
    return digest_lines([text])


def json_mapping(payload: Any) -> Mapping[str, object]:
    """解析台账中的 JSON 对象；损坏或非对象一律按空映射（失败关闭）。"""

    if isinstance(payload, Mapping):
        return payload
    return {}


__all__ = [
    "BARRIER_EXHAUSTION_CODES",
    "HISTORY_REBUILD_POLICY_VERSION",
    "HISTORY_REBUILD_STATE_VERSION",
    "ProfileRebuildError",
    "REBUILD_BACKUP_FILENAME",
    "REBUILD_METADATA_FILENAME",
    "REBUILD_PREVIEW_FILENAME",
    "REBUILD_REPORT_FILENAME",
    "REBUILD_WORKING_DB_FILENAME",
    "REBUILD_WORKING_DIRNAME",
    "RebuildItemStatus",
    "RebuildSkipReason",
    "RebuildStatus",
    "RebuildWindow",
    "json_mapping",
    "now_text",
    "rebuild_id_for",
    "text_digest",
]
