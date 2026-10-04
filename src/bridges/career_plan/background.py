"""工单 29：个人规划分支的背景快照与来源纪律。

个人准备分支只读取三类允许来源，且每一项都保留来源引用：

1. **当前陈述**：本轮用户原文与当前任务的已确认目标/条件原话（自述）；
2. **允许使用的 19 切片**：由注入的背景提供者按用途编译的采用快照，提供者
   负责检查画像使用开关、切片版本与撤回状态；
3. **相关简历片段**：只有登记了简历来源的提供者才会产生，本轮没有登记来源
   时不读取、也不伪造。

快照只保存采用条目的完整事实与来源标识；未采用条目的正文不进入本模块产物
（沿用工单 19 的最小化口径）。时间约束以当前陈述优先：本轮明确说的每天可用
时间覆盖长期默认值，只有当前陈述没有给时才使用画像条目的约束。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from bridges.career_plan.contracts import (
    CareerBackgroundItem,
    CareerBackgroundSnapshot,
    CareerBackgroundSource,
    CareerRequestAnalysis,
)
from bridges.career_plan.parsing import detect_time_budget
from bridges.contracts.profile_adoption import AdoptedProfileSlice

#: 个人规划分支最多采用的背景条目数（含当前陈述；整条采用，不截断）。
MAX_BACKGROUND_ITEMS = 8

#: 背景正文采用预算：按完整条目计数，超出时整条排除，不截断事实。
MAX_BACKGROUND_CHARACTERS = 8000

#: 画像事实里“允许改变计划时间”的决策标签（工单 19 用途语义）。
PLAN_TIME_DECISION = "plan_time_budget"


class CareerBackgroundProvider(Protocol):
    """登记的个人背景提供者：只返回允许且当前有效的来源。"""

    def load(
        self,
        account_id: str,
        *,
        run_id: str,
        query: str | None,
        current_user_message_id: str | None = None,
        now: datetime | None = None,
    ) -> CareerBackgroundSnapshot: ...


def build_statement_items(
    *,
    user_content: str,
    user_message_id: str,
    task_texts: tuple[str, ...] = (),
    task_ref: str | None = None,
) -> list[CareerBackgroundItem]:
    """当前陈述条目：本轮原文一条，任务已确认原话逐条（都有来源）。"""

    items: list[CareerBackgroundItem] = []
    text = user_content.strip()
    if text:
        items.append(
            CareerBackgroundItem(
                source=CareerBackgroundSource.USER_STATEMENT,
                text=text,
                source_ref=user_message_id,
                authority="user",
            )
        )
    for index, task_text in enumerate(task_texts):
        value = task_text.strip()
        if not value or value == text:
            continue
        items.append(
            CareerBackgroundItem(
                source=CareerBackgroundSource.TASK,
                text=value,
                source_ref=f"{task_ref or user_message_id}#task-text:{index}",
                authority="user",
            )
        )
    return items[:MAX_BACKGROUND_ITEMS]


def snapshot_from_adopted_slice(
    adopted: AdoptedProfileSlice,
    *,
    checked_at: datetime | None = None,
    unavailable_reason: str | None = None,
) -> CareerBackgroundSnapshot:
    """把工单 19 的采用快照折算为背景快照（只带采用正文与来源标识）。"""

    moment = checked_at or datetime.now(UTC)
    items = [
        CareerBackgroundItem(
            source=CareerBackgroundSource.PROFILE,
            text=item.fact_text,
            source_ref=f"{item.profile_item_id}#v{item.version}",
            authority=item.source_authority,
            relation=item.relation.value,
            version=item.version,
            expires_at=item.expires_at,
            applicable_to=list(item.applicable_to),
            conditions=list(item.conditions),
            is_default=item.is_default,
            overridden=item.overridden,
        )
        for item in adopted.adopted_items
    ]
    excluded_notes = []
    if adopted.excluded_items:
        excluded_notes.append(
            f"另有 {len(adopted.excluded_items)} 条已记住信息未采用"
            "（按用途规则、有效性门与最小切片预算排除）。"
        )
    return CareerBackgroundSnapshot(
        items=items,
        slice_id=adopted.slice_id,
        revocation_version=adopted.revocation_version,
        compiled_policy_version=adopted.compiled_policy_version,
        used_profile=bool(items),
        purpose_task_kind=adopted.purpose.task_kind.value,
        unavailable_reason=unavailable_reason,
        excluded_notes=excluded_notes,
        checked_at=moment,
    )


def unavailable_snapshot(
    reason: str, *, checked_at: datetime | None = None
) -> CareerBackgroundSnapshot:
    """未取得长期背景时的快照：如实说明原因，不阻断公开岗位部分。"""

    return CareerBackgroundSnapshot(
        items=[],
        used_profile=False,
        unavailable_reason=reason,
        checked_at=checked_at or datetime.now(UTC),
    )


def finalize_snapshot(
    *,
    analysis: CareerRequestAnalysis,
    statement_items: list[CareerBackgroundItem],
    profile_snapshot: CareerBackgroundSnapshot | None,
) -> CareerBackgroundSnapshot:
    """合并当前陈述与允许的长期来源，并确定时间约束的优先级。"""

    profile = profile_snapshot or unavailable_snapshot("本轮没有可用的长期背景来源。")
    items: list[CareerBackgroundItem] = []
    remaining_characters = MAX_BACKGROUND_CHARACTERS
    excluded_count = 0
    for item in [*statement_items, *profile.items]:
        if item.overridden:
            continue
        if len(items) >= MAX_BACKGROUND_ITEMS or len(item.text) > remaining_characters:
            excluded_count += 1
            continue
        items.append(item)
        remaining_characters -= len(item.text)
    adopted_profile_items = [
        item for item in items if item.source is CareerBackgroundSource.PROFILE
    ]
    statement_budget = analysis.time_budget_minutes
    statement_budget_adopted = statement_budget is not None and any(
        item.source in {CareerBackgroundSource.USER_STATEMENT, CareerBackgroundSource.TASK}
        and detect_time_budget(item.text) == statement_budget
        for item in items
    )
    budget: int | None = statement_budget if statement_budget_adopted else None
    source: str | None = "statement" if statement_budget_adopted else None
    # 本轮明确时间的原话超预算时，不偷偷回退长期默认时间。
    if statement_budget is None:
        for item in adopted_profile_items:
            if PLAN_TIME_DECISION not in item.applicable_to:
                continue
            found = detect_time_budget(item.text)
            if found is not None:
                budget = found
                source = "profile"
                break
    return CareerBackgroundSnapshot(
        items=items,
        slice_id=profile.slice_id,
        revocation_version=profile.revocation_version,
        compiled_policy_version=profile.compiled_policy_version,
        used_profile=bool(adopted_profile_items),
        purpose_task_kind=profile.purpose_task_kind,
        time_budget_minutes=budget,
        time_budget_source=source,
        unavailable_reason=profile.unavailable_reason,
        excluded_notes=[
            *profile.excluded_notes,
            *(
                [f"另有 {excluded_count} 条背景超出本轮预算，整条未采用。"]
                if excluded_count
                else []
            ),
        ],
        checked_at=profile.checked_at,
    )


__all__ = [
    "MAX_BACKGROUND_ITEMS",
    "PLAN_TIME_DECISION",
    "CareerBackgroundProvider",
    "build_statement_items",
    "finalize_snapshot",
    "snapshot_from_adopted_slice",
    "unavailable_snapshot",
]
