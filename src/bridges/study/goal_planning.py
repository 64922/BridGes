"""学习路线规划：用既有结构化输出能力形成简短、可调整的导师方案。

C01：本模块的提示词与合同按 BridGes 需求自主设计，只输出结构化 JSON，
不复制参考项目的代码或提示词。规划只在新系统目标与确需修订时调用，调用
经统一网关计入运行预算；失败返回 None，由调用方如实降级。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from bridges.ai.model_gateway import ModelGateway
from bridges.ai.model_quota import RunModelQuota
from bridges.contracts.ai import ModelCallStatus
from bridges.contracts.study_goals import StudyGoalProjection, StudyRouteNode, StudyRouteProjection

if TYPE_CHECKING:
    from bridges.chat.repository import GenerationRunRecord
    from bridges.chat.run_budget import RunBudget

#: 路线必须简短：超过该数量的节点属于完整课程，不是本票的导师方案。
MAX_ROUTE_NODES = 8

#: 规划模式；resume/switch 是 Issue 08 的续学与明确改学判定。
StudyGoalPlanMode = Literal["create", "build_route", "revision", "resume", "switch"]

_PLANNER_SYSTEM_PROMPT = (
    "你是 BridGes 的学习规划导师。你的职责是判断用户最新消息属于系统性学习目标、"
    "局部具体问题、续学既有目标还是明确改学，并为系统性目标形成「简短、可调整」的导师路线。"
    "用户的学习目的与明确限制是权威事实：只能原样引用，绝不改写、扩写或替换。\n"
    "\n"
    "只输出一个 JSON 对象，字段如下：\n"
    "{\n"
    '  "kind": "systematic_goal"、"local_question"、"resume" 或 "switch",\n'
    '  "target_goal_id": "resume/switch 选中的既有目标标识；新建或无法确定时为空字符串",\n'
    '  "observable_result": "用户明确说出的可观察结果；未说出时为空字符串",\n'
    '  "scope": "用户明确说出的适用范围；未说出时为空字符串",\n'
    '  "constraints": "用户明确说出的限制条件；未说出时为空字符串",\n'
    '  "clarification_question": "缺少真正决定教法的信息、或续学目标确有歧义时，'
    '只问一个必要问题；否则为空字符串",\n'
    '  "route": {"nodes": [{"node_id": "n1", "title": "知识点名称",'
    ' "prerequisites": [], "depth": ""}], "current_node_id": "n1",'
    ' "revision_reason": "形成或修订本路线的依据"} 或 null,\n'
    '  "note": "可选的一句话说明"\n'
    "}\n"
    "\n"
    "规则：\n"
    "- 用户最新消息只是具体题目或单个问题（问某个概念、求解某题、请求解释）时，"
    "kind 必须为 local_question，不建立长期目标或完整课程。\n"
    "- 用户明确表达宽泛领域的学习意图（如「带我学线性代数」「我想系统学概率论」）时，"
    "kind 为 systematic_goal。\n"
    "- 用户明确要求继续之前的学习（上下文给出候选目标列表）时，kind 为 resume："
    "只有当前消息提到的主题与某个候选目标原话明显对应时才填 target_goal_id；"
    "多个候选都可能或无法确定时 target_goal_id 留空，并在 clarification_question "
    "中只问一个让用户选择的问题；消息与任何候选都无关时 kind 为 local_question。"
    "绝不能选择候选列表之外的目标，也不要默认选择最近更新的目标。\n"
    "- 用户明确要求换一个学习目标、不再学当前目标时，kind 为 switch："
    "目标在候选列表中时填 target_goal_id；是全新目标时 target_goal_id 留空并按"
    "系统性目标给出路线与相关字段。只是顺带问一句、临时岔题或没有改变当前目标的"
    "意图时，kind 为 local_question。不确定是否真的要改学时按 local_question 处理。\n"
    "- 不要统一先做入门考试，不要假定用户已有基础；只有缺少真正决定教法的信息时，"
    "才在 clarification_question 中问恰好一个问题，否则直接给出路线。\n"
    "- 路线必须简短：不超过 8 个知识点，按教学顺序排列；prerequisites 只引用本路线内的"
    "node_id；current_node_id 必须是其中一个节点；depth 可写「基础」「深入」等简短提示"
    "或留空。\n"
    "- observable_result、scope、constraints 只能来自用户明确说过的内容，绝不猜测；"
    "猜测不得成为用户约束。\n"
    "- 用户的学习目的不可更改；调整只涉及顺序、深度或当前节点，并在 revision_reason "
    "写明依据。\n"
    "- 只输出 JSON，不要输出解释性正文；用简洁中文。"
)


class StudyRouteDraft(BaseModel):
    """规划输出的路线草案；在写入前校验图结构。"""

    model_config = ConfigDict(extra="ignore")

    nodes: list[StudyRouteNode] = Field(min_length=1, max_length=MAX_ROUTE_NODES)
    current_node_id: str = Field(min_length=1, max_length=64)
    revision_reason: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def _validate_route_graph(self) -> StudyRouteDraft:
        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("路线节点标识必须唯一。")
        known = set(node_ids)
        for node in self.nodes:
            unknown = [ref for ref in node.prerequisites if ref not in known]
            if unknown:
                raise ValueError("前置关系只能引用本路线内的节点。")
        if self.current_node_id not in known:
            raise ValueError("当前节点必须是路线内的节点。")
        return self


class StudyGoalPlan(BaseModel):
    """一次规划调用的结构化结果。"""

    model_config = ConfigDict(extra="ignore")

    kind: Literal["systematic_goal", "local_question", "resume", "switch"] = (
        "systematic_goal"
    )
    target_goal_id: str = Field(default="", max_length=64)
    observable_result: str = Field(default="", max_length=500)
    scope: str = Field(default="", max_length=500)
    constraints: str = Field(default="", max_length=500)
    clarification_question: str = Field(default="", max_length=300)
    route: StudyRouteDraft | None = None
    note: str = Field(default="", max_length=300)


class StudyGoalPlanner:
    """经统一网关调用结构化输出；失败一律返回 None，不伪造路线。"""

    def __init__(self, gateway: ModelGateway) -> None:
        self._gateway = gateway

    def plan(
        self,
        *,
        mode: StudyGoalPlanMode,
        run: GenerationRunRecord,
        user_content: str,
        goal: StudyGoalProjection | None,
        route: StudyRouteProjection | None,
        messages: list[dict[str, str]] | None,
        model_quota: RunModelQuota | None,
        budget: RunBudget | None,
        candidates: Sequence[StudyGoalProjection] = (),
    ) -> StudyGoalPlan | None:
        system_prompt = _PLANNER_SYSTEM_PROMPT + "\n" + _mode_context(
            mode, goal=goal, route=route, candidates=candidates
        )
        planning_messages = _planning_messages(messages, user_content, system_prompt)
        # 运行时导入：避免 bridges.chat 包初始化期间的反向依赖。
        from bridges.chat.run_executor import chat_run_context

        result = self._gateway.invoke(
            "qwen_structured_output",
            "1",
            chat_run_context(run.account_id, run.conversation_id, run.run_id),
            payload={
                "task": "study.goal_planning",
                "messages": planning_messages,
                "max_tokens": 1024,
                "temperature": 0.01,
            },
            budget=budget,
            model_override=(run.config or {}).get("run_model_id"),
            model_quota=model_quota,
        )
        if result.status not in {ModelCallStatus.SUCCESS, ModelCallStatus.DEGRADED}:
            return None
        if not isinstance(result.output, dict):
            return None
        try:
            return StudyGoalPlan.model_validate(result.output)
        except ValidationError:
            return None


def route_projection_from_draft(
    draft: StudyRouteDraft,
    *,
    version: int,
    origin: Literal["assistant_proposal", "user_request"],
    revision_reason: str,
    created_at: datetime,
) -> StudyRouteProjection:
    """把校验过的草案转换为可持久化的路线投影。"""

    return StudyRouteProjection(
        version=version,
        nodes=list(draft.nodes),
        current_node_id=draft.current_node_id,
        revision_reason=revision_reason,
        origin=origin,
        created_at=created_at,
    )


def _mode_context(
    mode: StudyGoalPlanMode,
    *,
    goal: StudyGoalProjection | None,
    route: StudyRouteProjection | None,
    candidates: Sequence[StudyGoalProjection] = (),
) -> str:
    if mode == "revision" and goal is not None and route is not None:
        nodes = "、".join(node.title for node in route.nodes)
        return (
            "本次是对既有路线的修订：不得改动用户的学习目的与目标字段。"
            f"当前目标原话：{goal.statement}\n"
            f"当前路线（第 {route.version} 版）：{nodes}；"
            f"当前节点：{route.current_node_id}。\n"
            "根据用户最新要求只调整顺序、深度或当前节点，并在 revision_reason 写依据；"
            "若评估认为无需修订，route 返回 null 并在 note 说明理由。"
        )
    if mode == "build_route" and goal is not None:
        return (
            "用户此前已表达系统性学习目标，本轮根据用户补充的信息形成路线。"
            f"目标原话：{goal.statement}\n"
            "若仍缺少真正决定教法的信息，只问一个问题。"
        )
    if mode == "resume":
        return (
            "本轮判断用户最新消息是否要求继续某个既有学习目标，并从下列候选"
            "（均为当前账户本人目标）中选择：\n"
            + _candidate_lines(candidates)
            + "\n选择规则：消息主题与候选目标原话明显对应才填 target_goal_id；"
            "多个候选都可能或无法确定时 target_goal_id 留空并只问一个澄清问题；"
            "与任何候选无关时 kind=local_question。绝不选择列表外的目标。"
        )
    if mode == "switch" and goal is not None:
        route_text = ""
        if route is not None:
            route_text = (
                f"当前路线（第 {route.version} 版）当前节点：{route.current_node_id}。"
            )
        return (
            "本轮判断用户是否明确要求换一个学习目标、不再学当前目标。"
            f"当前目标原话：{goal.statement}\n"
            + route_text
            + "\n可切换的既有候选目标（不含当前目标）：\n"
            + _candidate_lines(candidates)
            + "\n目标在候选列表中时填 target_goal_id；是全新目标时 target_goal_id "
            "留空，并按系统性目标给出路线与 observable_result/scope/constraints；"
            "只是顺带提问、临时岔题或意图不明确时 kind=local_question。"
        )
    return "本轮判断用户最新消息是否为新的系统性学习目标，并据此形成简短路线。"


def _candidate_lines(candidates: Sequence[StudyGoalProjection]) -> str:
    if not candidates:
        return "（当前没有可选的既有目标）"
    lines: list[str] = []
    for candidate in candidates:
        route = candidate.route
        node_title = ""
        if route is not None:
            node_title = next(
                (
                    node.title
                    for node in route.nodes
                    if node.node_id == route.current_node_id
                ),
                "",
            )
        lines.append(
            f"- target_goal_id={candidate.goal_id}；目标原话：{candidate.statement}"
            f"；状态：{candidate.status}"
            + (f"；当前节点：{node_title}" if node_title else "")
        )
    return "\n".join(lines)


def _planning_messages(
    messages: list[dict[str, str]] | None,
    user_content: str,
    system_prompt: str,
) -> list[dict[str, str]]:
    """复用本轮已编译的上下文；无编译记录时退化为系统提示词 + 当前消息。"""

    if not messages:
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
    planning = [dict(message) for message in messages]
    if planning and planning[0].get("role") == "system":
        planning[0]["content"] = system_prompt
    else:
        planning.insert(0, {"role": "system", "content": system_prompt})
    return planning
