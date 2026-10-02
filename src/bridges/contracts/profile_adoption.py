"""改进工单 19：用途明确、整条采用的画像切片合同。

生成前由 :class:`~bridges.profiles.atomic.AtomicProfileService` 编译一次
:class:`AdoptedProfileSlice`：它冻结当轮已提交的有效条目、用途语义、采用
与排除原因、适用条件和撤回版本，是 22/29 等后续消费者唯一的采用真相源。

合同的边界：

- **完整事实**：条目正文原样保留（含尾部条件与否定），不做 80 字机械截断。
- **用途语义**：任务种类、模式、模块与学习阶段进入 :class:`ProfileSlicePurpose`，
  选择规则据此决定“跨主题默认偏好”与“按任务召回的背景/目标/约束”。
- **同一版本**：同轮后续节点只能通过 :meth:`AdoptedProfileSlice.select_subset`
  从冻结快照里再选子集，不重新查询仓库、不纳入后台迟到的新提取。
- **可撤回**：``revocation_version`` 与切片同一快照折算，删除/纠正/撤回后
  旧切片失效。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from bridges.contracts.atomic_profile import (
    AtomicProfileFactRelation,
    AtomicProfileFactScope,
)
from bridges.contracts.profiles import ProfileSlice, ProfileSliceItem


class ProfileTaskKind(StrEnum):
    """当前任务的种类（确定性规则识别，不调用模型）。"""

    EXPLAIN = "explain"
    PLAN = "plan"
    RECOMMEND = "recommend"
    PRACTICE = "practice"
    REVIEW = "review"
    GENERAL = "general"


class ProfileSlicePurpose(BaseModel):
    """编译画像切片时确定的用途语义。

    ``explicit_request`` 只保存本轮明确要求的确定性签名（如 ``detailed``），
    不保存用户原文；``query`` 仅在同一进程内用于复现选择依据。
    """

    model_config = ConfigDict(extra="forbid")

    mode: str = Field(description="对话模式：companion / study。")
    task_kind: ProfileTaskKind = Field(
        default=ProfileTaskKind.GENERAL, description="确定性识别的任务种类。"
    )
    module_id: str | None = Field(
        default=None, description="显式模块提示；只作理解提示，不决定事实真伪。"
    )
    learning_stage: str | None = Field(
        default=None, description="当前学习阶段或水平假设，可为空。"
    )
    query: str | None = Field(
        default=None, description="本轮请求原文（仅进程内使用，不进入审计）。"
    )
    explicit_request: str | None = Field(
        default=None, description="本轮明确要求签名，例如 detailed / concise。"
    )


class AdoptedProfileItem(BaseModel):
    """一条被本轮整条采用的完整事实及其适用决策。"""

    model_config = ConfigDict(extra="forbid")

    profile_item_id: str = Field(description="原子条目标识。")
    version: int = Field(ge=1, description="条目版本；撤回核对时使用。")
    fact_text: str = Field(description="完整事实正文（不截断）。")
    relation: AtomicProfileFactRelation = Field(description="事实关系/属性槽。")
    scope: AtomicProfileFactScope = Field(description="事实适用范围。")
    source_authority: str = Field(
        description="来源权威：user / automatic / migration。"
    )
    expires_at: datetime | None = Field(
        default=None, description="条目有效期终点；没有明示期限时为空。"
    )
    applicable_to: list[str] = Field(
        default_factory=list,
        description="本条允许改变的回答决策标签（用途语义）。",
    )
    adoption_reason: str = Field(description="被采用的可解释原因。")
    conditions: list[str] = Field(
        default_factory=list,
        description="适用条件；无明示期限的约束提醒依赖前确认。",
    )
    is_default: bool = Field(
        default=False, description="长期默认值；本轮明确要求可以覆盖它。"
    )
    overridden: bool = Field(
        default=False, description="本轮明确要求优先，该默认本轮不生效。"
    )


class ProfileSliceExclusion(BaseModel):
    """一条未采用条目及原因；正文只留在进程内披露，不进入审计。"""

    model_config = ConfigDict(extra="forbid")

    profile_item_id: str = Field(description="原子条目标识。")
    fact_text: str = Field(description="完整事实正文（不截断）。")
    exclusion_reason: str = Field(description="未采用的可解释原因。")


class AdoptedProfileSlice(BaseModel):
    """一次生成前编译的唯一采用切片。

    采用条目与排除原因都随对象冻结；后续节点调用 :meth:`select_subset`
    只在这个快照里按用途再选子集，不再访问仓库，因此同一轮不会各自产生
    互相矛盾的采用结果。
    """

    model_config = ConfigDict(extra="forbid")

    slice_id: str = Field(description="稳定切片标识。")
    owner_account_id: str = Field(description="所属账户标识。")
    run_id: str = Field(description="绑定的运行标识。")
    purpose: ProfileSlicePurpose = Field(description="编译用途语义。")
    adopted_items: list[AdoptedProfileItem] = Field(
        default_factory=list, description="整条采用的完整事实。"
    )
    excluded_items: list[ProfileSliceExclusion] = Field(
        default_factory=list, description="未采用条目与原因。"
    )
    revocation_version: str | None = Field(
        default=None, description="同一快照折算的撤回版本。"
    )
    length_budget: int = Field(
        default=4, ge=0, description="本轮采用的条目数上限。"
    )
    compiled_policy_version: str = Field(
        default="purpose-slice-1.0", description="切片编译规则版本。"
    )
    compiled_at: datetime = Field(description="编译时间。")

    def select_subset(self, *needs: str) -> list[AdoptedProfileItem]:
        """从冻结快照按用途标签再选子集；无标签时返回全部采用条目。"""

        if not needs:
            return list(self.adopted_items)
        wanted = set(needs)
        return [
            item
            for item in self.adopted_items
            if wanted.intersection(item.applicable_to)
        ]

    def with_items(
        self, adopted_items: list[AdoptedProfileItem]
    ) -> AdoptedProfileSlice:
        """返回只保留给定条目的同一快照副本（预算裁剪用，不碰仓库）。"""

        return self.model_copy(update={"adopted_items": list(adopted_items)})

    def to_profile_slice(self) -> ProfileSlice:
        """折算为既有渲染合同；原子条目不携带类别。"""

        return ProfileSlice(
            slice_id=self.slice_id,
            owner_account_id=self.owner_account_id,
            run_id=self.run_id,
            purpose=f"chat:{self.purpose.mode}",
            project_id=None,
            included_items=[
                ProfileSliceItem(
                    assertion_id=item.profile_item_id,
                    dimension="",
                    value_or_rule=item.fact_text,
                    inclusion_reason=item.adoption_reason,
                )
                for item in self.adopted_items
            ],
            unused_items=[],
            compiled_policy_version=self.compiled_policy_version,
            revocation_version=self.revocation_version,
            length_budget=self.length_budget,
            compiled_at=self.compiled_at,
        )


__all__ = [
    "AdoptedProfileItem",
    "AdoptedProfileSlice",
    "ProfileSliceExclusion",
    "ProfileSlicePurpose",
    "ProfileTaskKind",
]
