"""生涯规划助手公开契约（Issue 29）。

生涯规划在既有日常陪伴/学习模式中按明确意图触发：只使用当前账户授权的
画像切片、学习记录、用户陈述与可追溯证据，输出固定六类结构化结果——
已知事实、待验证假设、可选方向、关键风险、分阶段成长路径、近期学习建议，
外加自然中文正文与保证边界声明。事实/假设/方向/风险/路径/建议条目都带
证据引用与核查时间；能力不可用时给出可恢复错误与安全替代，不输出模板化
假成功。

契约边界：不提供招聘撮合、职位投递、录取预测或执业资格认证；不对薪酬、
就业或人生结果作保证；不基于单次情绪、敏感身份猜测或未确认候选画像作
稳定职业判断（切片编译在 Issue 27 已排除这些记录）。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class CareerPlanningProcessState(StrEnum):
    """生涯规划过程卡状态（与 humanizer 五态一致，另加 done/clarify）。

    - ``loading``：进行中；
    - ``clarify``：信息不足，先问一个关键澄清问题（Issue 09 intake）；
    - ``empty``：无可用画像/证据的合法空态（回答仍基于用户陈述）；
    - ``error``：不可重试错误；
    - ``permission``：凭据/能力未就绪；
    - ``recovery``：可重试错误；
    - ``done``：完成。
    """

    LOADING = "loading"
    CLARIFY = "clarify"
    EMPTY = "empty"
    ERROR = "error"
    PERMISSION = "permission"
    RECOVERY = "recovery"
    DONE = "done"


class CareerPlanningStatus(StrEnum):
    """一条规划消息的终态。"""

    DONE = "done"
    ERROR = "error"


class CareerEvidenceKind(StrEnum):
    """生涯规划可使用的证据类别（全部按当前账户授权）。"""

    PROFILE_SLICE = "profile_slice"      # 画像切片（最小必要记录）
    LEARNING_RECORD = "learning_record"  # 学习记录（使命/知识状态/学习证据）
    RETRIEVAL = "retrieval"              # 本地分层检索引用（附件/项目/知识库）
    WEB_SEARCH = "web_search"            # DuckDuckGo 公开来源
    ARXIV = "arxiv"                      # arXiv 论文来源
    USER_STATEMENT = "user_statement"    # 用户陈述（本轮消息或会话内可见片段）


class CareerPlanningRouteContract(BaseModel):
    """自然语言进入生涯规划时固化的最小规划合同。"""

    version: Literal["career-route-1"] = Field(default="career-route-1")
    target: str = Field(description="本轮要做出的职业方向决策目标。")
    time_horizon: str = Field(description="规划时间范围；未明确时使用稳定默认值。")
    constraints: list[str] = Field(
        default_factory=list, description="用户已经表达的地点、时间或资源约束。"
    )
    evidence_requirements: list[
        Literal["user_statement", "authorized_knowledge_base", "current_search"]
    ] = Field(
        default_factory=lambda: ["user_statement"],
        description="允许进入本轮规划的证据类别。",
    )
    profile_usage: Literal["minimal_task_slice"] = Field(
        default="minimal_task_slice",
        description="画像只能按当前任务使用最小切片。",
    )
    image_usage: Literal["none", "task_relevant_minimal_slice"] = Field(
        default="none",
        description="图片只可调整建议层次、例子和约束，不可充当事实。",
    )
    open_questions: list[str] = Field(
        default_factory=list, description="尚未决定且会影响方案的事项。"
    )


class CareerEvidenceSource(BaseModel):
    """一条可追溯证据：可定位来源 + 核查时间。

    ``accessed_at`` 是该来源本次读取/搜索/编译的核查时间；涉及岗位、教育
    路径、资格、行业趋势等会变化的信息时，条目必须引用本模型才能获得
    verified 状态。私人原文不复制，只保留标题/定位/摘要快照。
    """

    evidence_id: str = Field(description="稳定证据标识（供条目 evidence_refs 引用）。")
    kind: CareerEvidenceKind = Field(description="证据类别。")
    title: str = Field(description="来源标题或文件名。")
    locator: str | None = Field(
        default=None, description="定位：页码/章节/网址/对话消息等。"
    )
    url: str | None = Field(default=None, description="可打开来源链接（联网来源）。")
    summary: str | None = Field(
        default=None, description="可验证片段摘要（截断，不复制完整私人原文）。"
    )
    accessed_at: datetime = Field(description="本次核查时间。")
    stale: bool = Field(
        default=False,
        description="来源已超过新鲜度阈值（默认 90 天），引用其的条目应标注过时。",
    )


class CareerItemState(StrEnum):
    """条目证据状态（确定性复核结果，前端展示标签）。"""

    VERIFIED = "verified"      # 有可定位来源与核查时间
    UNVERIFIED = "unverified"  # 无来源引用或引用不可核验
    CONFLICTED = "conflicted"  # 引用多个证据但未说明关系，暂不作稳定判断
    OUTDATED = "outdated"      # 引用的来源已过时


class CareerItemBase(BaseModel):
    """六类条目的公共结构。"""

    item_id: str = Field(description="稳定条目标识（供逐项反馈定位，如 fact:1）。")
    content: str = Field(description="条目内容（自然中文，事实与推测分离）。")
    evidence_refs: list[str] = Field(
        default_factory=list, description="引用的证据标识（可空，复核会标记状态）。"
    )
    note: str | None = Field(
        default=None,
        description="补充说明：核查方式/证据关系/降级原因等（模型或复核填写）。",
    )
    verified_at: datetime | None = Field(
        default=None, description="本条核查时间（服务端复核时填充）。"
    )


class CareerFact(CareerItemBase):
    """已知事实：必须带可定位来源与核查时间，否则复核标记未核实。"""


class CareerAssumption(CareerItemBase):
    """待验证假设：说明下一步核查方式，不得包装成结论。"""

    verification_next_step: str | None = Field(
        default=None, description="下一步核查方式（查什么来源、如何验证）。"
    )


class CareerOption(CareerItemBase):
    """可选方向：带依据说明，不暗示唯一正确选择。"""

    rationale: str | None = Field(
        default=None, description="该方向成立的主要依据。"
    )


class CareerRisk(CareerItemBase):
    """关键风险：带触发条件与影响，不作确定预言。"""

    trigger: str | None = Field(
        default=None, description="风险的触发条件或情景。"
    )


class CareerStage(CareerItemBase):
    """分阶段成长路径中的一步。"""

    timeline: str | None = Field(
        default=None, description="建议时间范围（如「第 1-3 个月」）。"
    )


class CareerSuggestion(CareerItemBase):
    """近期学习建议：带验证方式，不替代持证职业顾问。"""

    verification: str | None = Field(
        default=None, description="如何验证该建议是否适合自己。"
    )


class CareerPlanningOutputContract(BaseModel):
    """生涯规划的模型输出合同（六类 + 正文 + 边界声明 + 未决问题）。

    完整性门：``final_text`` 与 ``boundary_statement`` 非空，且六类中至少
    一个类别有内容，否则不标记完成（缺一不完成）。
    """

    final_text: str = Field(description="自然中文整体正文（含开场与引导追问）。")
    facts: list[CareerFact] = Field(
        default_factory=list, description="已知事实（须带证据与核查时间）。"
    )
    assumptions: list[CareerAssumption] = Field(
        default_factory=list, description="待验证假设。"
    )
    options: list[CareerOption] = Field(
        default_factory=list, description="可选方向。"
    )
    risks: list[CareerRisk] = Field(
        default_factory=list, description="关键风险。"
    )
    path: list[CareerStage] = Field(
        default_factory=list, description="分阶段成长路径。"
    )
    suggestions: list[CareerSuggestion] = Field(
        default_factory=list, description="近期学习建议。"
    )
    boundary_statement: str = Field(
        description="保证边界声明：不作就业/薪酬/录取保证，不替代持证顾问。"
    )
    open_questions: list[str] = Field(
        default_factory=list, description="证据缺口与未决问题（含下一步核查方式）。"
    )


class CareerReviewItem(BaseModel):
    """一条确定性复核结果（事实证据门/引用核验/过时/冲突标注）。"""

    item_id: str = Field(description="被复核的条目标识。")
    category: str = Field(description="类别：facts/assumptions/options/risks/path/suggestions。")
    state: CareerItemState = Field(description="复核后的证据状态。")
    reason: str = Field(description="中文原因说明。")


class CareerReviewResult(BaseModel):
    """生涯规划输出复核结论。

    ``passed`` 为 False 表示存在阻断性边界违反（如就业/薪酬/录取承诺），
    此时不得交付规划正文，消息进入可恢复错误态。
    """

    passed: bool = Field(description="是否可交付。")
    reviews: list[CareerReviewItem] = Field(
        default_factory=list, description="逐条复核结果。"
    )
    boundary_violations: list[str] = Field(
        default_factory=list, description="阻断性边界违反（承诺词命中原文片段）。"
    )
    warnings: list[str] = Field(
        default_factory=list, description="非阻断提示（如引用缺失/关系未说明）。"
    )


class CareerPlanningProjection(BaseModel):
    """一条助手消息的生涯规划结果投影（按账户隔离持久化）。

    刷新、退出重登与应用重启后由同一账户从消息历史恢复；其他账户不可读。
    """

    plan_id: str = Field(description="稳定规划标识（与助手消息绑定）。")
    intent: str = Field(description="用户原始生涯问题（截断）。")
    route_contract: CareerPlanningRouteContract | None = Field(
        default=None, description="自然语言路由编译的规划合同快照。"
    )
    status: CareerPlanningStatus = Field(description="终态。")
    profile_enabled: bool = Field(description="发送前是否启用了画像使用。")
    profile_used: bool = Field(description="本轮是否实际使用了画像切片。")
    verified_at: datetime = Field(description="整体核查时间（复核完成时间）。")
    output: CareerPlanningOutputContract | None = Field(
        default=None, description="交付的六类输出合同；失败/阻断时为 None。"
    )
    clarification: str | None = Field(
        default=None,
        description="信息不足时的关键澄清问题（Issue 09 intake；非空时"
        "消息正文即为该问题，不交付六类规划）。",
    )
    evidence_sources: list[CareerEvidenceSource] = Field(
        default_factory=list, description="本轮使用的全部证据（含核查时间）。"
    )
    review: CareerReviewResult | None = Field(
        default=None, description="确定性复核结论（降级/未核实/冲突/过时/边界）。"
    )
    process_state: CareerPlanningProcessState = Field(
        description="过程卡状态。"
    )
    process_steps: list[str] = Field(
        default_factory=list, description="已完成的步骤中文轨迹。"
    )
    error_code: str | None = Field(default=None, description="失败分类码。")
    error_message: str | None = Field(
        default=None, description="可操作中文错误说明（含安全替代步骤）。"
    )
    created_at: datetime = Field(description="创建时间。")


class CareerRunEventKind(StrEnum):
    """生涯规划编排事件类别（过程事件 / 结果事件）。"""

    PROCESS = "process"
    RESULT = "result"


class CareerRunEvent(BaseModel):
    """编排事件：过程事件驱动前端五态过程卡；结果事件携带最终投影。"""

    kind: CareerRunEventKind = Field(description="事件类别。")
    state: CareerPlanningProcessState = Field(
        default=CareerPlanningProcessState.DONE,
        description="过程卡状态（RESULT 事件由 result.status 决定终态）。",
    )
    step_label: str = Field(description="当前步骤中文说明。")
    detail: str | None = Field(default=None, description="补充中文说明。")
    retryable: bool = Field(default=False, description="当前是否可重试。")
    progress_steps: list[str] = Field(
        default_factory=list, description="已完成的步骤中文轨迹。"
    )
    result: CareerPlanningProjection | None = Field(
        default=None, description="结果投影（RESULT 事件携带）。"
    )


class CareerPlanningContractSummary(BaseModel):
    """生涯规划输出合同的轻量摘要（供审计 details，不含正文）。"""

    item_counts: dict[str, int] = Field(
        default_factory=dict, description="六类条目数（facts/assumptions/...）。"
    )
    evidence_count: int = Field(default=0, description="证据来源数。")
    slice_id: str | None = Field(default=None, description="使用的画像切片标识。")
    profile_used: bool = Field(default=False, description="是否使用了画像。")
    verified_at: datetime = Field(description="核查时间。")
