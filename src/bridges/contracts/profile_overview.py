"""整体画像概览与开放主题浏览的合同（画像工单 15）。

整体概览是**依赖有效条目及其版本的可重建视图**：每条陈述只由条目正文
原样组成（陈述文本 = 各段原文用「；」连接），逐段保存条目标识与版本，
不生成新事实，也不写心理或能力判断。条目、来源、事项或使用控制变化后
概览立即失效，重建以依赖版本提交，迟到结果不能覆盖新状态。

概览绝不作为提取证据或默认全量上下文：它只在「关于我的记忆」页面展示，
回答编译与提取路径都不读取它。主题是开放、多选、可扩展的浏览组织方式，
不决定事实身份与授权范围；近义主题的归一化见
:mod:`bridges.contracts.atomic_profile` 的 ``PROFILE_TOPIC_ALIASES``。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

#: 概览派生合同的版本标识；派生逻辑变化时旧快照一律视为失效重建。
PROFILE_OVERVIEW_CONTRACT_VERSION = "profile-overview-v1"


class ProfileOverviewState(StrEnum):
    """概览的当前状态：如实反映能否取得可信内容。"""

    #: 有当前有效内容（或刚重建完成）。
    CURRENT = "current"
    #: 重建竞争失败，但仍有依赖未变化的旧陈述可如实展示。
    PARTIAL = "partial"
    #: 内容已失效且重建未完成或失败：不显示任何陈述，提示更新中。
    REBUILDING = "rebuilding"
    #: 没有任何长期信息：如实为空，不显示伪造概览。
    EMPTY = "empty"


class ProfileOverviewSegmentProjection(BaseModel):
    """概览陈述的一段：对应一条原子条目（点击可定位依据与编辑入口）。"""

    model_config = ConfigDict(extra="forbid")

    profile_item_id: str = Field(description="稳定的原子条目标识。")
    version: int = Field(ge=1, description="该段生成时依赖的条目版本。")
    text: str = Field(description="条目正文原样（不摘要、不改写）。")


class ProfileOverviewStatementProjection(BaseModel):
    """一条概览陈述：某个主题下若干条目原文的忠实拼接。"""

    model_config = ConfigDict(extra="forbid")

    statement_id: str = Field(description="本次生成的稳定陈述标识。")
    topic: str | None = Field(
        default=None,
        description="陈述归属的浏览主题；无主题条目的陈述为空。",
    )
    text: str = Field(
        description="陈述文本：各段原文原样拼接，不产生新事实。"
    )
    segments: list[ProfileOverviewSegmentProjection] = Field(
        min_length=1,
        description="陈述依赖的条目段（逐段可定位依据与编辑入口）。",
    )


class ProfileOverviewProjection(BaseModel):
    """「整体概览」页面投影（加载/空态/整理中由前端按 ``state`` 渲染）。"""

    model_config = ConfigDict(extra="forbid")

    state: ProfileOverviewState = Field(description="概览当前状态。")
    usage_enabled: bool = Field(
        description="长期记忆使用开关；关闭时回答不使用记忆，概览仍可查看管理。"
    )
    notice: str | None = Field(
        default=None,
        description="使用关闭或部分可用等稳定提示文案；没有时为空。",
    )
    statements: list[ProfileOverviewStatementProjection] = Field(
        default_factory=list,
        description=(
            "概览陈述（主题成组、每条目只出现在一处）；"
            "无法保证新版本时为空，绝不回退旧内容冒充当前。"
        ),
    )
    generated_at: datetime | None = Field(
        default=None, description="最近一次成功重建时间；从未重建时为空。"
    )


__all__ = [
    "PROFILE_OVERVIEW_CONTRACT_VERSION",
    "ProfileOverviewProjection",
    "ProfileOverviewSegmentProjection",
    "ProfileOverviewStatementProjection",
    "ProfileOverviewState",
]
