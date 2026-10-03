"""主智能体确定性结构化理解（改进工单 12）。

本模块实现「同一次结构化理解」：给定本轮用户原话、服务端锁定模式、请求
中的模块提示与当前任务快照，一次产出 :class:`MainUnderstanding`——轻量
判定或任务关系、原话目标、硬条件、缺项、目标引用与实际路由来源。理解
过程**不调用模型、不执行外部检索、不创建运行**，因此普通聊天保持一次
轻量生成，路由与参数不叠加第二次规划调用。

边界与不变量：

- **正文优先，提示保留**：正文出现明确单模块意图时按正文路由；请求中的
  ``module_id`` 只作辅助理解与历史标识，绝不重写。
- **歧义只问一个必要问题**：多个能力同样合理或指代不唯一时给出唯一澄清
  问题，不先检索猜测领域。
- **硬条件先于模型建议**：「只查论文」「不要联网」等硬条件进入理解快照
  与路由快照，代码门禁据此收紧联网/本地材料，语义扩展不得放宽。
- **普通聊天不创建任务**：无目标、无条件、无模块意图时不产生任务关系，
  也不暂停仍活跃的旧任务（由任务领域再次保证）。
- **学习模式不派发日常模块**：只读权威阶段，产出学习动作标记。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from bridges.career.intake import assess_intake
from bridges.career.intent import is_study_planning_request
from bridges.career_plan.suggestion import detect_career_suggestion
from bridges.chat.reference_resolution import resolve_references
from bridges.commute.suggestion import detect_commute_suggestion
from bridges.contracts.chat import ChatMode
from bridges.contracts.references import (
    ReferenceResolution,
    ReferenceStatus,
    ReferenceTaskContext,
)
from bridges.contracts.tasks import (
    ConditionOrigin,
    ConditionScope,
    TaskConditionInput,
    TaskRecord,
    TaskRelation,
    TaskStatus,
    TaskTurnRequest,
    TaskWait,
    WaitStatus,
)
from bridges.contracts.understanding import (
    UNDERSTANDING_CONTRACT_VERSION,
    HardCondition,
    HardConditionKind,
    MainUnderstanding,
    RouteSource,
)
from bridges.github.suggestion import detect_github_suggestion
from bridges.paper.suggestion import PAPER_REQUEST_HINTS, detect_paper_suggestion
from bridges.resources.suggestion import detect_resources_suggestion
from bridges.routing import NaturalLanguageRouter, RouteStatus
from bridges.state_copy import (
    CLARIFICATION_REFERENCE_AMBIGUITY_TEXT,
    CLARIFICATION_TASK_AMBIGUITY_TEXT,
    CLARIFICATION_TASK_HISTORY_AMBIGUITY_TEXT,
    render_state_copy,
)
from bridges.tieba.suggestion import detect_tieba_suggestion

#: 正文模块识别器：按既有建议优先级排列（论文最先，职业最后），命中即止
#: 用于「明确单模块」判定；多个命中时交给澄清，不猜测组合。
_MODULE_DETECTORS: tuple[tuple[str, object], ...] = (
    ("paper", detect_paper_suggestion),
    ("tieba", detect_tieba_suggestion),
    ("github", detect_github_suggestion),
    ("commute", detect_commute_suggestion),
    ("resources", detect_resources_suggestion),
    ("career", detect_career_suggestion),
)

#: 资源模块直启的明确检索诉求词；只有「想学/学习/课程安排」这类规划或
#: 泛泛学习意图时不直启（保留为下一次建议或普通聊天），避免把含糊主题
#: 猜测成领域检索。
_DIRECT_RESOURCE_RE = re.compile(
    r"找|搜|查|推荐|看看|看一下|有什么|有哪些|求推荐|来个|盘点|书单|教程|教材|网课"
)

#: 取消当前任务（任务级动作，不是内容里的「不用联网」等硬条件）。
_CANCEL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:算了|行了)?[，,]?\s*(?:不找了|不查了|不弄了|不做了)[。！!]?$"),
    re.compile(r"取消(?:这个|那个|当前|一下)?(?:任务|请求|查询)"),
    re.compile(r"^不(?:用|要)(?:再)?(?:查|找|弄|做)(?:这个|那个|了)[。！!]?$"),
    re.compile(r"这个任务(?:就)?(?:取消|不做了)"),
)
#: 暂停当前任务（保留条件与历史，可续接）。
_PAUSE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:先)?(?:暂停|停一下|先停)(?:一下)?(?:这个|当前)?(?:任务)?[。！!]?$"),
    re.compile(r"^(?:先)?(?:不|别)(?:找|查|弄|做|聊)了[，,]?\s*(?:等|回头|过?会)"),
    re.compile(r"^(?:等|回头|过?会)(?:儿|一会)?再(?:说|聊|弄|做|继续)"),
    re.compile(r"先(?:放一放|放放|搁置|缓缓)"),
)
#: 修订当前任务条件。
_REVISE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:改|换)(?:成|为|到)\s*\S+"),
    re.compile(r"(?:修改|调整)(?:为|成)\s*\S+"),
    re.compile(r"不(?:要|用)\S{0,12}(?:了)?[，,]\s*(?:改|换)"),
)
#: 续接已有任务。
_CONTINUE_MARKERS: tuple[str, ...] = (
    "继续",
    "接着",
    "回到刚才",
    "回到之前",
    "按刚才",
    "按之前",
    "刚才那个",
    "上一个",
    "上述",
    "那个任务",
    "这个任务",
)
#: 明确换话题。
_NEW_TOPIC_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"换个话题"),
    re.compile(r"聊点别的"),
    re.compile(r"不说这个了"),
    re.compile(r"先不聊这个"),
    re.compile(r"别聊这个了"),
)
#: 画像命令：不得填入旧澄清等待。
_PROFILE_COMMAND_RE = re.compile(r"记住|忘掉|忘记|别记|不要再记|不用记|停止记录|不记录")

#: 来源限制：正向「只查某一来源」收紧公网与知识库；反向「不要知识库」
#: 只排除本地材料，不得连带关闭公网检索。
_SOURCE_RESTRICTION_PATTERNS: tuple[
    tuple[re.Pattern[str], HardConditionKind], ...
] = (
    (
        re.compile(r"(?:只|仅|光)(?:查|看|搜|找|用|要|给)?(?:论文|文献|研究文章)"),
        HardConditionKind.SOURCE_RESTRICTION,
    ),
    (
        re.compile(r"(?:只|仅)(?:在|看|查|用)?(?:贴吧|吧里|吧内)"),
        HardConditionKind.SOURCE_RESTRICTION,
    ),
    (
        re.compile(r"(?:只|仅)(?:看|查|用)(?:github|开源项目|仓库)"),
        HardConditionKind.SOURCE_RESTRICTION,
    ),
    (
        re.compile(r"不要(?:查|用|看)(?:知识库|本地|资料)"),
        HardConditionKind.NO_LOCAL,
    ),
)
#: 不联网。
_NO_NETWORK_RE = re.compile(
    r"不要联网|不联网|别联网|禁止联网|不上网|不查网|不用联网|"
    r"不要(?:联网)?搜索|不用搜索|不要搜|别搜|不额外搜索|不要用网络"
)
#: 只用本地材料。
_LOCAL_ONLY_RE = re.compile(
    r"(?:只|仅)(?:用|查|看|基于)(?:当前)?(?:知识库|本地|附件|材料)|"
    r"只用本地|只看本地|本地资料即可|不需要外部来源|不用外部来源"
)
#: 城市词表（限定条件识别；只用于抽取明确出现的城市名）。
_CITY_WORDS: tuple[str, ...] = (
    "北京", "上海", "广州", "深圳", "杭州", "南京", "成都", "武汉", "西安", "重庆",
    "天津", "苏州", "长沙", "郑州", "青岛", "合肥", "南昌", "福州", "厦门", "济南",
    "大连", "沈阳", "哈尔滨", "昆明", "贵阳", "南宁", "石家庄", "太原", "兰州",
    "乌鲁木齐", "海口", "三亚", "宁波", "无锡", "佛山", "东莞", "珠海",
)
_YEAR_RE = re.compile(
    r"(?:19|20)\d{2}\s*(?:[-~─—至到]\s*(?:19|20)\d{2})?"
    r"|近[一二两三四五六七八九十\d]+年"
)
_COUNT_RE = re.compile(r"\d{1,3}\s*(?:篇|个|本|条)")
_META_REPLY_RE = re.compile(
    r"^(?:好的?|好嘞|嗯+|哦+|收到|行|可以|谢谢|多谢|哈哈+|ok|OK)[。！!~～]?$"
)
_TERM_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{1,}|[\u4e00-\u9fff]{2,}")
_QUOTED_RE = re.compile(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"]*"|‘[^’]*’')
_NON_ACTION_RE = re.compile(
    r"(?:不要|别|不用|不想|不必|不能)(?:再)?(?:取消|暂停|继续|改|换)|"
    r"如果|假如|假设|要是|他说|她说|提到|引用"
)
_RESTART_RE = re.compile(r"重新(?:开始|发起|查|找)|重来|再来一次")


class MainAgentUnderstanding:
    """确定性主理解：一次产出关系/参数/硬条件与实际路由来源。"""

    def __init__(self, router: NaturalLanguageRouter | None = None) -> None:
        self._router = router or NaturalLanguageRouter()

    # -- 对外入口 --------------------------------------------------------

    def understand(
        self,
        *,
        conversation_id: str,
        user_message_id: str,
        content: str,
        mode: ChatMode | str,
        requested_module_id: str | None = None,
        task_context: ReferenceTaskContext | None = None,
        tasks: Sequence[TaskRecord] = (),
        open_waits: Sequence[TaskWait] = (),
        messages: Sequence[object] = (),
    ) -> MainUnderstanding:
        """产出一次完整理解；不调用模型、不执行检索、不写任何状态。"""

        mode_value = mode.value if isinstance(mode, ChatMode) else str(mode)
        text = " ".join(content.split())
        hard_conditions = self._hard_conditions(text)
        profile_command = bool(_PROFILE_COMMAND_RE.search(text))
        if mode_value == ChatMode.STUDY.value:
            return self._study_understanding(
                user_message_id=user_message_id,
                mode=mode_value,
                content=text,
                requested_module_id=requested_module_id,
                hard_conditions=hard_conditions,
                profile_command=profile_command,
            )

        resolution = self._resolve(
            text=text,
            messages=messages,
            user_message_id=user_message_id,
            task_context=task_context,
        )
        detected = self._detect_modules(text)
        target_task, ambiguity = self._target_task(
            text=text,
            tasks=tasks,
            task_context=task_context,
            resolution=resolution,
        )
        matching_waits = [
            wait for wait in open_waits
            if target_task is not None
            and wait.task_id == target_task.task_id
            and wait.expected_version == target_task.current_version
            and wait.status == WaitStatus.OPEN
        ]
        answer_fields = self._answer_fields(
            text=text,
            open_waits=([] if profile_command or self._is_new_topic(text) else matching_waits),
            detected=detected,
            resolution=resolution,
        )
        relation, goal = self._relation(
            text=text,
            detected=detected,
            requested_module_id=requested_module_id,
            target_task=target_task,
            answering=bool(answer_fields),
        )
        if answer_fields and target_task is not None:
            relation = TaskRelation.CONTINUE
            if "topic" in answer_fields:
                detected = ["paper"]
                goal = text
            elif {"direction", "stage"} & set(answer_fields):
                detected = ["career"]
                goal = f"{target_task.goal}；{text}"
        touches_task = relation in {
            TaskRelation.CONTINUE,
            TaskRelation.REVISE,
            TaskRelation.PAUSE,
            TaskRelation.CANCEL,
        }
        if not touches_task:
            target_task = None
        if (
            relation is None
            and open_waits
            and task_context is not None
            and answer_fields
            and not _META_REPLY_RE.fullmatch(text)
        ):
            # 有未决等待但本消息给出可识别答案时按续接处理；否则保持轻量
            # （「好的」这类消息不填旧等待，也不误判为任务续接）。
            relation = TaskRelation.CONTINUE
            target_task = next(
                (task for task in tasks if task.task_id == task_context.task_id),
                None,
            )
        clarification, missing = self._clarification(
            ambiguity=ambiguity,
            detected=detected,
            requested_module_id=requested_module_id,
            resolution=resolution,
        )
        classification_text = goal if "career" in detected and goal else text
        classified = self._router.classify(classification_text)
        if (
            clarification is None
            and not answer_fields
            and classified.status == RouteStatus.CLARIFY
            and classified.error_code in {"paper_empty_query", "paper_ambiguous_query"}
            and any(hint.lower() in text.lower() for hint in PAPER_REQUEST_HINTS)
        ):
            clarification = classified.clarification_question
            missing = ["topic"]
            if relation is None:
                relation, goal = TaskRelation.NEW, text
        if (
            clarification is None
            and classified.is_career
            and not is_study_planning_request(text)
            and classified.career_contract is not None
            and classified.career_contract.open_questions
        ):
            intake = assess_intake(goal or text)
            clarification = intake.question
            missing = [intake.missing_dimension] if intake.missing_dimension else []
            if relation is None:
                relation, goal = TaskRelation.NEW, text
        actual_module, route_source, reason = self._route(
            detected=detected,
            requested_module_id=requested_module_id,
            relation=relation,
            clarification=clarification,
            target_task=target_task,
        )
        return MainUnderstanding(
            user_message_id=user_message_id,
            mode=mode_value,
            requested_module_id=requested_module_id,
            actual_module_id=actual_module,
            route_source=route_source,
            capability_list=[actual_module] if actual_module else [],
            reason=reason,
            goal=goal,
            hard_conditions=hard_conditions,
            missing_fields=missing,
            task_relation=relation,
            target_task_id=(
                target_task.task_id if target_task is not None else None
            ),
            expected_task_version=(
                target_task.current_version if target_task is not None else None
            ),
            clarification_question=clarification,
            answer_fields=answer_fields,
            is_new_topic=self._is_new_topic(text),
            is_profile_command=profile_command,
            is_learning_action=False,
            source_span=text[:200] or None,
        )

    # -- 学习模式 --------------------------------------------------------

    def _study_understanding(
        self,
        *,
        user_message_id: str,
        mode: str,
        content: str,
        requested_module_id: str | None,
        hard_conditions: Sequence[HardCondition],
        profile_command: bool,
    ) -> MainUnderstanding:
        """学习模式只读权威阶段：不派发日常模块，也不切换模式。"""

        return MainUnderstanding(
            user_message_id=user_message_id,
            mode=mode,
            requested_module_id=requested_module_id,
            actual_module_id=None,
            route_source=RouteSource.LEARNING_STRATEGY,
            capability_list=[],
            reason="学习模式由权威阶段策略处理，本轮不派发日常模块。",
            goal=None,
            hard_conditions=list(hard_conditions),
            missing_fields=[],
            task_relation=None,
            clarification_question=None,
            answer_fields=[],
            is_new_topic=False,
            is_profile_command=profile_command,
            is_learning_action=True,
            source_span=content[:200] or None,
        )

    # -- 关系与目标 ------------------------------------------------------

    def _relation(
        self,
        *,
        text: str,
        detected: Sequence[str],
        requested_module_id: str | None,
        target_task: TaskRecord | None,
        answering: bool = False,
    ) -> tuple[TaskRelation | None, str | None]:
        action_text = _QUOTED_RE.sub("", text)
        is_action = not _NON_ACTION_RE.search(action_text)
        if target_task is not None and is_action and self._matches(action_text, _CANCEL_PATTERNS):
            return TaskRelation.CANCEL, None
        if target_task is not None and is_action and self._matches(action_text, _PAUSE_PATTERNS):
            return TaskRelation.PAUSE, None
        if target_task is not None and is_action and self._matches(action_text, _REVISE_PATTERNS):
            return TaskRelation.REVISE, None
        if is_action and _RESTART_RE.search(action_text):
            return TaskRelation.NEW, text
        has_continue = any(marker in action_text for marker in _CONTINUE_MARKERS)
        if detected or requested_module_id:
            if answering or (has_continue and target_task is not None):
                # 补齐旧澄清/继续旧任务的单模块请求：同一任务推进，不新建。
                return (
                    TaskRelation.CONTINUE if target_task is not None else TaskRelation.NEW,
                    None if target_task is not None else (text or None),
                )
            if (
                not detected
                and requested_module_id is not None
                and target_task is not None
                and not self._is_new_topic(text)
            ):
                # 模块提示续接现有任务（如「骑车」回答通勤等待、点击建议绑定
                # 任务版本）：不新建第二个任务，任务范围才能带上等待来源前文。
                return TaskRelation.CONTINUE, None
            return TaskRelation.NEW, text or None
        if is_action and has_continue and target_task is not None:
            return TaskRelation.CONTINUE, None
        if self._is_new_topic(text):
            # 明确换话题：暂停旧任务的信号交给任务领域（NEW + is_new_topic）。
            return TaskRelation.NEW, None
        return None, None

    @staticmethod
    def _looks_like_task_goal(text: str) -> bool:
        return bool(text) and len(text) > 4 and not _META_REPLY_RE.fullmatch(text)

    def _target_task(
        self,
        *,
        text: str,
        tasks: Sequence[TaskRecord],
        task_context: ReferenceTaskContext | None,
        resolution: ReferenceResolution,
    ) -> tuple[TaskRecord | None, str | None]:
        """定位目标任务；多个历史任务同样合理时返回歧义说明。"""

        candidates = [
            task
            for task in tasks
            if task.status not in {TaskStatus.CANCELLED, TaskStatus.COMPLETED}
        ]
        has_continuation = any(marker in text for marker in _CONTINUE_MARKERS)
        has_back_reference = "那个" in text or "哪一个" in text or "哪个" in text
        if has_continuation and has_back_reference and len(candidates) > 1:
            matched = self._match_tasks_by_terms(text, candidates)
            if len(matched) == 1:
                return matched[0], None
            if len(matched) > 1:
                return None, CLARIFICATION_TASK_AMBIGUITY_TEXT
            return None, CLARIFICATION_TASK_HISTORY_AMBIGUITY_TEXT
        current = (
            next(
                (task for task in candidates if task.task_id == task_context.task_id),
                None,
            )
            if task_context is not None
            else None
        )
        if current is not None:
            return current, None
        if (
            resolution.status in {ReferenceStatus.AMBIGUOUS, ReferenceStatus.UNRESOLVED}
            and has_continuation
        ):
            matched = self._match_tasks_by_terms(text, candidates)
            if len(matched) == 1:
                return matched[0], None
            if resolution.status == ReferenceStatus.AMBIGUOUS:
                return None, CLARIFICATION_REFERENCE_AMBIGUITY_TEXT
        return None, None

    @staticmethod
    def _match_tasks_by_terms(text: str, tasks: Sequence[TaskRecord]) -> list[TaskRecord]:
        terms = {term for term in _TERM_RE.findall(text) if len(term) >= 2}
        matched: list[TaskRecord] = []
        for task in tasks:
            goal = task.goal or ""
            if any(term in goal for term in terms):
                matched.append(task)
        return matched

    # -- 缺项与澄清 ------------------------------------------------------

    def _clarification(
        self,
        *,
        ambiguity: str | None,
        detected: Sequence[str],
        requested_module_id: str | None,
        resolution: ReferenceResolution,
    ) -> tuple[str | None, list[str]]:
        if ambiguity is not None:
            return ambiguity, ["task"]
        if len(detected) > 1 and requested_module_id not in detected:
            names = "、".join(detected)
            return (
                render_state_copy("chat.clarification.multiple_tasks", names=names),
                ["capability"],
            )
        if resolution.status == ReferenceStatus.AMBIGUOUS and resolution.clarification:
            return resolution.clarification.question, ["reference"]
        return None, []

    # -- 路由 ------------------------------------------------------------

    def _route(
        self,
        *,
        detected: Sequence[str],
        requested_module_id: str | None,
        relation: TaskRelation | None,
        clarification: str | None,
        target_task: TaskRecord | None,
    ) -> tuple[str | None, RouteSource, str]:
        if clarification is not None:
            return None, RouteSource.ORDINARY_CHAT, "需要先澄清，本轮不派发模块。"
        if len(detected) == 1:
            module = detected[0]
            return (
                module,
                RouteSource.BODY_INTENT,
                f"正文明确要求 {module} 能力，按正文路由（模块提示只作辅助）。",
            )
        if len(detected) > 1 and requested_module_id in detected:
            return (
                requested_module_id,
                RouteSource.MODULE_HINT,
                "正文包含多个能力信号，按请求中的模块提示消解歧义。",
            )
        if requested_module_id is not None:
            return (
                requested_module_id,
                RouteSource.MODULE_HINT,
                "正文未给出其他明确意图，按请求中的模块提示启动。",
            )
        if relation in {
            TaskRelation.CONTINUE,
            TaskRelation.REVISE,
            TaskRelation.PAUSE,
            TaskRelation.CANCEL,
        }:
            return (
                None,
                RouteSource.TASK_CONTINUATION,
                "续接或修订已有任务，不另行派发新模块。",
            )
        if target_task is not None and relation is not None:
            return (
                None,
                RouteSource.TASK_CONTINUATION,
                "按当前任务继续处理。",
            )
        return None, RouteSource.ORDINARY_CHAT, "未识别到明确能力意图，保持普通聊天。"

    # -- 硬条件 ----------------------------------------------------------

    def _hard_conditions(self, text: str) -> list[HardCondition]:
        conditions: list[HardCondition] = []
        if matched := _NO_NETWORK_RE.search(text):
            conditions.append(
                HardCondition(
                    kind=HardConditionKind.NO_NETWORK,
                    text=matched.group(0),
                    source_span=matched.group(0),
                )
            )
        if matched := _LOCAL_ONLY_RE.search(text):
            conditions.append(
                HardCondition(
                    kind=HardConditionKind.LOCAL_ONLY,
                    text=matched.group(0),
                    source_span=matched.group(0),
                )
            )
        for pattern, kind in _SOURCE_RESTRICTION_PATTERNS:
            matched = pattern.search(text)
            if matched:
                conditions.append(
                    HardCondition(
                        kind=kind,
                        text=matched.group(0),
                        source_span=matched.group(0),
                    )
                )
        year = _YEAR_RE.search(text)
        if year:
            conditions.append(
                HardCondition(
                    kind=HardConditionKind.YEAR_RANGE, text=year.group(0), source_span=year.group(0)
                )
            )
        count = _COUNT_RE.search(text)
        if count:
            conditions.append(
                HardCondition(
                    kind=HardConditionKind.COUNT, text=count.group(0), source_span=count.group(0)
                )
            )
        city = next((item for item in _CITY_WORDS if item in text), None)
        if city and (self._matches(text, _REVISE_PATTERNS) or "城市" in text):
            conditions.append(
                HardCondition(kind=HardConditionKind.CITY, text=city, source_span=city)
            )
        return conditions

    # -- 等待答案字段 ----------------------------------------------------

    def _answer_fields(
        self,
        *,
        text: str,
        open_waits: Sequence[TaskWait],
        detected: Sequence[str],
        resolution: ReferenceResolution,
    ) -> list[str]:
        """只把本消息真实给出的值算作答案；部分作答不解决等待。"""

        if not open_waits:
            return []
        missing: set[str] = set()
        for wait in open_waits:
            missing.update(wait.missing_fields)
        answered: list[str] = []
        if "capability" in missing and len(detected) == 1:
            answered.append("capability")
        if "reference" in missing and resolution.status == ReferenceStatus.RESOLVED:
            answered.append("reference")
        if "task" in missing and resolution.status == ReferenceStatus.RESOLVED:
            answered.append("task")
        if "city" in missing:
            city = next((item for item in _CITY_WORDS if item in text), None)
            if city:
                answered.append("city")
        if "year" in missing and _YEAR_RE.search(text):
            answered.append("year")
        if "goal" in missing and self._looks_like_task_goal(text):
            answered.append("goal")
        if "direction" in missing and assess_intake(text).missing_dimension != "direction":
            answered.append("direction")
        if "stage" in missing and assess_intake(f"数据分析；{text}").enough:
            answered.append("stage")
        if (
            "topic" in missing
            and not detected
            and len(text) >= 2
            and len(text) <= 80
            and not _META_REPLY_RE.fullmatch(text)
            and not re.search(r"[？?]|天气|心情|取消|暂停|换个话题", text)
        ):
            answered.append("topic")
        return answered

    # -- 基础工具 --------------------------------------------------------

    def _detect_modules(self, text: str) -> list[str]:
        detected: list[str] = []
        for module_id, detector in _MODULE_DETECTORS:
            payload = detector(text)  # type: ignore[operator]
            if payload is None and module_id == "paper":
                # 论文以统一路由器为权威（如「研究文章」这类建议词表未覆盖
                # 的明确表达）；建议检测器命中仍视为明确意图。
                payload = (
                    {"module_id": "paper"}
                    if self._router.classify(text).is_paper_search
                    else None
                )
            if payload is None:
                continue
            if module_id == "resources" and not _DIRECT_RESOURCE_RE.search(text):
                # 「学习计划/课程安排/学习任务」这类规划意图不当作资料检索。
                continue
            detected.append(module_id)
        return detected

    @staticmethod
    def _matches(text: str, patterns: Sequence[re.Pattern[str]]) -> bool:
        return any(pattern.search(text) for pattern in patterns)

    @staticmethod
    def _is_new_topic(text: str) -> bool:
        return any(pattern.search(text) for pattern in _NEW_TOPIC_PATTERNS)

    def _resolve(
        self,
        *,
        text: str,
        messages: Sequence[object],
        user_message_id: str,
        task_context: ReferenceTaskContext | None,
    ) -> ReferenceResolution:
        try:
            return resolve_references(
                request=text,
                messages=messages,  # type: ignore[arg-type]
                current_user_message_id=user_message_id,
                task=task_context,
            )
        except Exception:  # noqa: BLE001 - 定位失败不阻断入口，按未解析处理
            return ReferenceResolution(status=ReferenceStatus.NONE)


def task_turn_request(
    understanding: MainUnderstanding,
    *,
    conversation_id: str,
    answer_text: str | None = None,
) -> TaskTurnRequest | None:
    """把理解快照翻译为任务领域的关系落地请求；普通聊天返回 ``None``。"""

    if understanding.task_relation is None:
        return None
    conditions = [
        TaskConditionInput(
            kind=item.kind.value,
            text=item.text,
            scope=ConditionScope.TASK,
            origin=ConditionOrigin.USER_STATED,
            source_message_id=understanding.user_message_id,
            source_span=item.source_span,
        )
        for item in understanding.hard_conditions
    ]
    return TaskTurnRequest(
        conversation_id=conversation_id,
        user_message_id=understanding.user_message_id,
        relation=understanding.task_relation,
        goal=understanding.goal,
        explicit_task_id=understanding.target_task_id,
        conditions=conditions,
        is_new_topic=understanding.is_new_topic,
        is_profile_command=understanding.is_profile_command,
        is_learning_action=understanding.is_learning_action,
        expected_version=understanding.expected_task_version,
        answer_fields=list(understanding.answer_fields),
        answer_text=answer_text if understanding.answer_fields else None,
    )


__all__ = [
    "MainAgentUnderstanding",
    "UNDERSTANDING_CONTRACT_VERSION",
    "task_turn_request",
]
