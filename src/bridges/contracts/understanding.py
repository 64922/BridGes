"""主智能体混合入口与跨轮任务关系的结构化理解合同（改进工单 12）。

本模块把 ``docs/workflow/orchestration.md`` 第 2/3/5 节与
``docs/workflow/daily-workflows.md`` 第 1 节固化为**一次**结构化理解的
输出类型，供聊天入口、父图门禁与后续消费者（37 复合计划、38 投影）共用：

1. **一次理解**：同一次理解输出轻量回复判定或任务关系（new/continue/
   revise/pause/cancel/complete/block）、原话目标、硬条件、缺项、目标引用
   与登记能力建议；路由与参数不再叠加第二次规划调用。
2. **正文优先、提示保留**：``requested_module_id`` 是请求中的模块提示与
   历史标识；``actual_module_id``/``route_source`` 另记实际路由来源、能力
   列表与简短理由。正文明确时按正文路由，模块提示只作辅助理解。
3. **代码门禁**：模式、任务归属/版本、能力注册、参数来源、硬条件与预算
   由代码校验；模型建议不能启动未知/退役能力，也不能切换锁定模式。
4. **任务关系有限**：普通聊天不创建任务；只有明确目标、模块意图或来源
   条件才落地跨轮任务。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bridges.contracts.tasks import TaskRelation

#: 本模块合同的版本标识。理解快照随运行持久化，供跨版本读取与审计。
UNDERSTANDING_CONTRACT_VERSION = "main-understanding-v1"


class RouteSource(StrEnum):
    """实际路由的来源；与请求中的模块提示分开记录。"""

    #: 无能力意图：保持普通聊天轻量路径。
    ORDINARY_CHAT = "ordinary_chat"
    #: 正文表达明确的单模块意图（优先于模块提示）。
    BODY_INTENT = "body_intent"
    #: 请求携带模块提示且正文未覆盖（模块菜单保留为可选入口）。
    MODULE_HINT = "module_hint"
    #: 点击建议的一键启动：绑定任务版本，不改写原消息。
    SUGGESTION_CLICK = "suggestion_click"
    #: 续接/修订已有跨轮任务。
    TASK_CONTINUATION = "task_continuation"
    #: 学习模式策略：只读权威阶段，不派发日常模块。
    LEARNING_STRATEGY = "learning_strategy"


class HardConditionKind(StrEnum):
    """用户正文中必须遵守的硬条件类别。"""

    #: 只查某一来源/只看某种材料（如「只查论文」）。
    SOURCE_RESTRICTION = "source_restriction"
    #: 明确不要联网（不拓宽到公开搜索）。
    NO_NETWORK = "no_network"
    #: 只用本地材料（知识库/附件），不作外部检索。
    LOCAL_ONLY = "local_only"
    #: 明确排除本地材料/知识库（如「不要查知识库」）：公网检索不受影响。
    NO_LOCAL = "no_local"
    #: 限定城市（如「换杭州」）。
    CITY = "city"
    #: 限定年份或年份区间。
    YEAR_RANGE = "year_range"
    #: 限定数量。
    COUNT = "count"
    #: 其他明确限定。
    OTHER = "other"


class HardCondition(BaseModel):
    """一条硬条件：类别 + 原话，绝不静默放宽。"""

    model_config = ConfigDict(extra="forbid")

    kind: HardConditionKind
    text: str = Field(min_length=1, max_length=120, description="条件原话（用户可见）。")
    source_span: str | None = Field(
        default=None, max_length=120, description="可定位的最小原话片段。"
    )


class MainUnderstanding(BaseModel):
    """一次结构化理解的完整输出（随运行快照持久化）。

    ``actual_module_id`` 只允许是服务端已登记能力对应的模块；``None`` 表示
    本轮不派发模块（普通聊天或澄清）。``missing_fields`` 与
    ``clarification_question`` 同时非空表示只问一个必要问题；
    ``task_relation`` 为 ``None`` 表示普通聊天不触碰跨轮任务。
    """

    model_config = ConfigDict(extra="forbid")

    contract_version: str = Field(default=UNDERSTANDING_CONTRACT_VERSION)
    user_message_id: str = Field(min_length=1)
    mode: str = Field(description="服务端锁定的会话模式（理解不得改写）。")
    requested_module_id: str | None = Field(
        default=None, description="请求携带的模块提示（历史标识，不重写）。"
    )
    actual_module_id: str | None = Field(
        default=None, description="代码校验后的实际模块；None 表示不派发。"
    )
    route_source: RouteSource = Field(default=RouteSource.ORDINARY_CHAT)
    capability_list: list[str] = Field(default_factory=list, max_length=8)
    reason: str = Field(min_length=1, max_length=240, description="简短路由理由。")
    goal: str | None = Field(default=None, max_length=2_000, description="本轮目标原话（若有）。")
    hard_conditions: list[HardCondition] = Field(default_factory=list, max_length=8)
    effective_hard_conditions: list[HardCondition] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list, max_length=8)
    task_relation: TaskRelation | None = Field(
        default=None, description="跨轮任务关系；None 表示普通聊天。"
    )
    target_task_id: str | None = Field(default=None, description="明确指代的目标任务 ID。")
    expected_task_version: int | None = Field(
        default=None, description="理解读取到的目标任务版本（乐观校验）。"
    )
    clarification_question: str | None = Field(default=None, max_length=240)
    answer_fields: list[str] = Field(
        default_factory=list, max_length=8, description="本消息实际补齐的缺项字段。"
    )
    is_new_topic: bool = Field(default=False, description="明确开启无关话题（暂停旧任务）。")
    is_profile_command: bool = Field(default=False, description="画像命令：不得填旧等待。")
    is_learning_action: bool = Field(
        default=False, description="学习阶段动作：不得填旧等待、不派发日常模块。"
    )
    source_span: str | None = Field(
        default=None, max_length=200, description="目标或关系对应的原话片段。"
    )

    @property
    def blocks_network(self) -> bool:
        """正文明确不要联网或只用本地材料时为真。"""

        return any(
            item.kind in {HardConditionKind.NO_NETWORK, HardConditionKind.LOCAL_ONLY}
            for item in [*self.hard_conditions, *self.effective_hard_conditions]
        )

    @property
    def blocks_knowledge_base(self) -> bool:
        """正文限定外部来源或明确排除本地材料时为真。"""

        return any(
            item.kind
            in {HardConditionKind.SOURCE_RESTRICTION, HardConditionKind.NO_LOCAL}
            for item in [*self.hard_conditions, *self.effective_hard_conditions]
        )

    def allows_module(self, module_id: str) -> bool:
        """指定来源限制同时约束自动派发与建议点击。"""
        for item in [*self.hard_conditions, *self.effective_hard_conditions]:
            if item.kind != HardConditionKind.SOURCE_RESTRICTION:
                continue
            text = item.text.lower()
            source = (
                "paper" if any(word in text for word in ("论文", "文献", "研究文章"))
                else "tieba" if any(word in text for word in ("贴吧", "吧里", "吧内"))
                else "github"
            )
            if module_id != source:
                return False
        return True


def understanding_from_snapshot(document: object) -> MainUnderstanding | None:
    """读取运行配置中的理解快照；缺失或版本不可解析时返回 ``None``。"""

    if not isinstance(document, dict):
        return None
    try:
        return MainUnderstanding.model_validate(document)
    except ValidationError:
        return None


__all__ = [
    "UNDERSTANDING_CONTRACT_VERSION",
    "HardCondition",
    "HardConditionKind",
    "MainUnderstanding",
    "RouteSource",
    "understanding_from_snapshot",
]
