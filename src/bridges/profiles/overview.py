"""整体画像概览服务（画像工单 15）。

概览从**有效原子条目**派生：每条陈述只由条目正文原样组成，逐段保存条目
标识与版本，不产生新事实，也不写心理或能力判断。它持久化在
:mod:`bridges.profiles.overview_repository`，是带依赖指纹的派生视图：

- 依赖 = 条目（标识、版本、主题）、来源消息的可用性与内容摘要、账户使用
  控制（开关与版本）与合同版本；任一变化都让指纹不匹配，读取方先重建再
  使用。
- 重建以捕获的世代（epoch）做 CAS 提交；失效发生在重建期间时提交失败
  ——迟到重建不能覆盖新状态；重试后退还失败时如实返回「部分可用」（只
  保留依赖仍一致的旧陈述）或「整理中」（不显示任何陈述），绝不把旧概览
  冒充当前事实。
- 概览只服务页面展示，绝不进入提取证据或回答编译；不是默认全量上下文。
- 没有任何长期信息时如实返回空态，不写伪造的概览行。
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Sequence
from datetime import datetime

from bridges.contracts.atomic_profile import AtomicProfileItem
from bridges.contracts.profile_extraction import ProfileAccountControlsProjection
from bridges.contracts.profile_overview import (
    PROFILE_OVERVIEW_CONTRACT_VERSION,
    ProfileOverviewProjection,
    ProfileOverviewState,
)
from bridges.profiles.atomic import (
    AtomicProfileService,
    ProfileEvidenceSourceReader,
)
from bridges.profiles.overview_projection import (
    PROFILE_OVERVIEW_PARTIAL_NOTICE,
    PROFILE_OVERVIEW_REBUILDING_NOTICE,
    PROFILE_OVERVIEW_USAGE_DISABLED_NOTICE,
    _now,
    build_overview_statements,
    is_fresh,
    parse_overview_payload,
    valid_remaining_statements,
)
from bridges.profiles.overview_repository import (
    ProfileOverviewStore,
    StoredProfileOverview,
)

logger = logging.getLogger(__name__)

#: 重建在竞争/失效下重试的次数；仍不稳定则如实返回部分可用或整理中。
_OVERVIEW_REBUILD_ATTEMPTS = 2


class ProfileOverviewService:
    """整体概览的派生、失效与读取实现（画像工单 15）。"""

    def __init__(
        self,
        *,
        atomic: AtomicProfileService,
        overview_store: ProfileOverviewStore,
        controls_reader: Callable[[str], ProfileAccountControlsProjection],
        message_reader: ProfileEvidenceSourceReader | None = None,
    ) -> None:
        self._atomic = atomic
        self._store = overview_store
        # 控制状态复用自动画像服务的同一份持久状态（改进工单 07）。
        self._controls_reader = controls_reader
        # 来源可用性/内容摘要依赖消息读取；未注入时按「不可读」处理，
        # 绝不伪造来源仍存在。
        self._message_reader = message_reader

    # ------------------------------------------------------------------
    # 失效与读取
    # ------------------------------------------------------------------

    def invalidate(self, account_id: str, *, reason: str) -> None:
        """立即失效：世代加一；读取方不再把它当作当前内容（画像工单 15）。"""

        self._store.invalidate(account_id, reason=reason, now=_now())

    def list_overview(
        self, account_id: str, *, now: datetime | None = None
    ) -> ProfileOverviewProjection:
        """页面读取：当前 / 部分可用 / 整理中 / 空态（绝不回退旧内容冒充当前）。"""

        moment = now or _now()
        controls = self._controls_reader(account_id)
        items = self._atomic.list_items(account_id)
        fingerprint = self._fingerprint(account_id, items, controls)
        stored = self._store.read(account_id)
        if stored is None and not items:
            # 从未建立且没有条目：如实为空，不落一行空概览。
            return self._empty(controls)
        if stored is not None and is_fresh(stored, fingerprint):
            return self._projection(stored, controls)
        for _ in range(_OVERVIEW_REBUILD_ATTEMPTS):
            if self.commit_overview_rebuild(
                account_id, stored, items, now=moment
            ):
                stored = self._store.read(account_id)
                if stored is not None and is_fresh(stored, fingerprint):
                    return self._projection(stored, controls)
            # 提交丢失（重建期间发生失效/竞争）：重读依赖与世代再试一次。
            controls = self._controls_reader(account_id)
            items = self._atomic.list_items(account_id)
            fingerprint = self._fingerprint(account_id, items, controls)
            stored = self._store.read(account_id)
            if stored is not None and is_fresh(stored, fingerprint):
                return self._projection(stored, controls)
        if not items:
            return self._empty(controls)
        if stored is not None:
            remaining = valid_remaining_statements(stored, items)
            if remaining:
                return ProfileOverviewProjection(
                    state=ProfileOverviewState.PARTIAL,
                    usage_enabled=controls.usage_enabled,
                    notice=PROFILE_OVERVIEW_PARTIAL_NOTICE,
                    statements=remaining,
                    generated_at=stored.built_at,
                )
        return ProfileOverviewProjection(
            state=ProfileOverviewState.REBUILDING,
            usage_enabled=controls.usage_enabled,
            notice=PROFILE_OVERVIEW_REBUILDING_NOTICE,
            statements=[],
            generated_at=None,
        )

    # ------------------------------------------------------------------
    # 重建与守卫（观测/验证接缝）
    # ------------------------------------------------------------------

    def begin_overview_rebuild(
        self, account_id: str
    ) -> tuple[StoredProfileOverview | None, list[AtomicProfileItem]]:
        """捕获当前世代与条目快照供 CAS 提交（迟到重建不能覆盖新状态）。"""

        stored = self._store.read(account_id)
        snapshot = self._atomic.list_items(account_id)
        return stored, snapshot

    def commit_overview_rebuild(
        self,
        account_id: str,
        stored: StoredProfileOverview | None,
        snapshot: Sequence[AtomicProfileItem],
        *,
        now: datetime | None = None,
    ) -> bool:
        """以捕获的世代提交重建；返回是否赢得提交（迟到重建为 ``False``）。"""

        moment = now or _now()
        controls = self._controls_reader(account_id)
        statements = build_overview_statements(snapshot)
        fingerprint = self._fingerprint(account_id, snapshot, controls)
        payload = [statement.model_dump(mode="json") for statement in statements]
        return self._store.commit_rebuild(
            account_id,
            captured_epoch=0 if stored is None else stored.epoch,
            context_version=PROFILE_OVERVIEW_CONTRACT_VERSION,
            dependency_fingerprint=fingerprint,
            payload_json=json.dumps(payload, ensure_ascii=False),
            now=moment,
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _projection(
        self,
        stored: StoredProfileOverview,
        controls: ProfileAccountControlsProjection,
    ) -> ProfileOverviewProjection:
        statements = parse_overview_payload(stored.payload_json)
        return ProfileOverviewProjection(
            state=(
                ProfileOverviewState.CURRENT
                if statements
                else ProfileOverviewState.EMPTY
            ),
            usage_enabled=controls.usage_enabled,
            notice=(
                None
                if controls.usage_enabled
                else PROFILE_OVERVIEW_USAGE_DISABLED_NOTICE
            ),
            statements=statements,
            generated_at=stored.built_at,
        )

    def _empty(
        self, controls: ProfileAccountControlsProjection
    ) -> ProfileOverviewProjection:
        return ProfileOverviewProjection(
            state=ProfileOverviewState.EMPTY,
            usage_enabled=controls.usage_enabled,
            notice=(
                None
                if controls.usage_enabled
                else PROFILE_OVERVIEW_USAGE_DISABLED_NOTICE
            ),
            statements=[],
            generated_at=None,
        )

    def _fingerprint(
        self,
        account_id: str,
        items: Sequence[AtomicProfileItem],
        controls: ProfileAccountControlsProjection,
    ) -> str:
        """依赖指纹：条目版本与主题、来源消息可用性与内容、控制与合同版本。"""

        lines = [
            PROFILE_OVERVIEW_CONTRACT_VERSION,
            f"usage|{int(controls.usage_enabled)}|{controls.usage_control_version}",
        ]
        for item in sorted(items, key=lambda entry: entry.profile_item_id):
            lines.append(
                f"i|{item.profile_item_id}|{item.version}"
                f"|{'|'.join(item.topics)}"
            )
        message_ids = sorted(
            {
                message_id
                for item in items
                for message_id in item.source_message_ids
            }
        )
        for message_id in message_ids:
            message = None
            if self._message_reader is not None:
                try:
                    message = self._message_reader(account_id, message_id)
                except Exception:  # noqa: BLE001 - 读取失败按不可用，不伪造来源
                    message = None
            if message is None:
                lines.append(f"m|{message_id}|absent")
            else:
                digest = hashlib.sha256(
                    message.content.encode("utf-8")
                ).hexdigest()[:16]
                lines.append(f"m|{message_id}|{message.status}|{digest}")
        return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:32]


__all__ = ["ProfileOverviewService"]
