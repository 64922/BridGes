"""整体画像概览的纯投影层（画像工单 15）。

从原子条目派生概览陈述，并给出上下文新鲜度、诚实状态提示与部分可用时
仍然有效的陈述；不持有存储、控制或来源依赖，供 :mod:`bridges.profiles.
overview` 与测试直接复用。

陈述规则（确定性，不产生新事实）：

- 每条陈述只由条目正文**原样**组成：陈述文本 = 各段原文用「；」连接，
  逐段保存条目标识与版本；
- 每条目只出现在一条陈述里（多主题不复制事实）：按「默认主题的规范顺序
  在前、自定义主题按名称在后」选择条目自身的第一个主题作为归属；没有
  主题的条目归入无主题陈述（``topic=None``），不因主题缺失被丢弃；
- 组内条目按最近更新倒序（相同时按标识），组与陈述顺序确定；
- 近义主题先按 ``canonical_profile_topic`` 精确归一（未知主题原样保留）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime

from bridges.contracts.atomic_profile import (
    DEFAULT_PROFILE_TOPICS,
    AtomicProfileItem,
    AtomicProfileItemStatus,
    canonical_profile_topic,
)
from bridges.contracts.profile_overview import (
    PROFILE_OVERVIEW_CONTRACT_VERSION,
    ProfileOverviewSegmentProjection,
    ProfileOverviewStatementProjection,
)
from bridges.profiles.overview_repository import StoredProfileOverview

#: 使用开关关闭时的页面提示（稳定文案，前端直接展示）。概览本来就只用于
#: 查看与管理，不被回答默认采用；关闭使用后回答一律不采用长期记忆。
PROFILE_OVERVIEW_USAGE_DISABLED_NOTICE = (
    "长期记忆使用已关闭：回答不会使用这些信息；概览仍可查看与管理。"
)

#: 部分可用提示：重建竞争失败时展示仍然有效的旧陈述，绝不冒充全部为当前。
PROFILE_OVERVIEW_PARTIAL_NOTICE = (
    "记忆刚刚发生变化，部分概览陈述正在更新；下面只显示仍然有效的内容。"
)

#: 整理中提示：没有可信内容可展示时的诚实状态，绝不回退旧概览。
PROFILE_OVERVIEW_REBUILDING_NOTICE = (
    "记忆刚刚发生变化，概览正在整理；请稍后重新加载查看新内容。"
)

_DEFAULT_TOPIC_RANK: dict[str, int] = {
    topic: index for index, topic in enumerate(DEFAULT_PROFILE_TOPICS)
}


def _primary_topic(item: AtomicProfileItem) -> str | None:
    """条目的归属主题：规范顺序最靠前的主题；没有主题时为 ``None``。"""

    canonical: list[str] = []
    for raw in item.topics:
        value = canonical_profile_topic(raw)
        if value and value not in canonical:
            canonical.append(value)
    if not canonical:
        return None
    return min(canonical, key=_topic_sort_key)


def _topic_sort_key(topic: str | None) -> tuple[int, int, str]:
    """默认主题按定义顺序在前，自定义主题按名称在后，无主题最后。"""

    if topic is None:
        return (2, 0, "")
    rank = _DEFAULT_TOPIC_RANK.get(topic)
    if rank is not None:
        return (0, rank, topic)
    return (1, 0, topic)


def _statement_id(topic: str | None) -> str:
    digest = hashlib.sha256((topic or "").encode("utf-8")).hexdigest()[:16]
    return f"overview-{digest}"


def build_overview_statements(
    items: Sequence[AtomicProfileItem],
) -> list[ProfileOverviewStatementProjection]:
    """从活动条目生成概览陈述；输入顺序不影响结果。"""

    grouped: dict[str | None, list[AtomicProfileItem]] = {}
    for item in items:
        if item.status is not AtomicProfileItemStatus.ACTIVE:
            continue
        grouped.setdefault(_primary_topic(item), []).append(item)
    statements: list[ProfileOverviewStatementProjection] = []
    for topic in sorted(grouped, key=_topic_sort_key):
        group = sorted(
            grouped[topic],
            key=lambda item: (-item.updated_at.timestamp(), item.profile_item_id),
        )
        segments = [
            ProfileOverviewSegmentProjection(
                profile_item_id=item.profile_item_id,
                version=item.version,
                text=item.text,
            )
            for item in group
        ]
        statements.append(
            ProfileOverviewStatementProjection(
                statement_id=_statement_id(topic),
                topic=topic,
                text="；".join(segment.text for segment in segments),
                segments=segments,
            )
        )
    return statements


def parse_overview_payload(
    payload_json: str,
) -> list[ProfileOverviewStatementProjection]:
    """解析持久化的陈述载荷；损坏载荷如实返回空表（当作无内容）。"""

    try:
        raw = json.loads(payload_json)
    except (TypeError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    statements: list[ProfileOverviewStatementProjection] = []
    for entry in raw:
        try:
            statements.append(
                ProfileOverviewStatementProjection.model_validate(entry)
            )
        except ValueError:
            return []
    return statements


def valid_remaining_statements(
    stored: StoredProfileOverview, items: Sequence[AtomicProfileItem]
) -> list[ProfileOverviewStatementProjection]:
    """部分可用：只保留每一段依赖条目与版本仍然一致的旧陈述。"""

    versions = {item.profile_item_id: item.version for item in items}
    return [
        statement
        for statement in parse_overview_payload(stored.payload_json)
        if all(
            versions.get(segment.profile_item_id) == segment.version
            for segment in statement.segments
        )
    ]


def is_fresh(stored: StoredProfileOverview, fingerprint: str) -> bool:
    return (
        stored.is_current
        and stored.context_version == PROFILE_OVERVIEW_CONTRACT_VERSION
        and stored.dependency_fingerprint == fingerprint
    )


def _now() -> datetime:
    return datetime.now(UTC)
