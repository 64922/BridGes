"""确定性自然语言论文搜索路由。"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal, cast

from pydantic import ValidationError

from bridges.career.intake import assess_intake
from bridges.career.intent import is_career_intent
from bridges.contracts.career import CareerPlanningRouteContract
from bridges.routing.contracts import (
    CapabilityRoute,
    MainCapability,
    PaperSearchConstraints,
    PaperSearchPlan,
    RouteStatus,
    VideoGenerationPlan,
)
from bridges.video.constants import (
    VIDEO_DEFAULT_DURATION_SECONDS,
    VIDEO_DEFAULT_SIZE,
    VIDEO_MODEL_ID,
    VIDEO_SUPPORTED_DURATIONS_SECONDS,
)
from bridges.routing.registry import CapabilityRouteRegistry


class NaturalLanguageRouter:
    """先做确定性预检，论文路由不调用语言模型。"""

    _PAPER = re.compile(r"arxiv|论文|文献|研究文章|学术文章|preprint|papers?|research\s+papers?", re.I)
    _ACTION = re.compile(
        r"搜索|搜|查找|查一下|检索|筛选|找|找出|找几篇|推荐(?:几篇|一些)?|核对|对比|"
        r"search|find|look\s+up|retrieve|filter",
        re.I,
    )
    _REWRITE = re.compile(r"润色|改写|重写|修改|降重|翻译|写作方法|怎么写|段落|摘要改", re.I)
    _KNOWLEDGE_QA = re.compile(
        r"知识库|(?:文档|文件)(?:中|里的|内容|问答)|这篇|该论文|论文内容|讲了什么|总结|解释|问答|"
        r"knowledge\s+base|what\s+(?:does\s+)?this\s+paper\s+say|"
        r"summar(?:ize|ise)|explain(?:\s+this\s+paper)?",
        re.I,
    )
    _OTHER_CAPABILITY = re.compile(
        r"生成(?:一张|图片|图像|视频)|画一张|视频|职业规划|转行|考研|找工作|提醒", re.I
    )
    _ARXIV_ID = re.compile(r"(?<![\w-])(?:arxiv\s*[:：]\s*)?(\d{4}\.\d{4,5}(?:v\d+)?)(?![\w-])", re.I)
    _EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
    _URL = re.compile(r"https?://\S+", re.I)
    _SECRET = re.compile(
        r"\b(?:password|passwd|pwd|credential|api[_ -]?key|authorization|secret|token)"
        r"\s*[:=]\s*(?:bearer\s+)?[^\s,，。！？；;]+",
        re.I,
    )
    _CODE = re.compile(r"```.*?```", re.S)
    _PRIVATE = re.compile(
        r"(?:私人|私密|个人)?(?:文档|文件|附件|资料|画像|档案|凭据|密码|密钥|"
        r"attachment|private\s*(?:document|file)|token|secret|api\s*key|apikey|qq|邮箱|用户名|"
        r"profile|account[_ ]?id|user[_ ]?id)[^。！？；;\n]*[。！？；;]?",
        re.I,
    )
    _PERSONAL = re.compile(
        r"(?:^|(?<=[。！？；;\n]))(?=[^。！？；;\n]*(?:我的(?:姓名|名字|职业|背景|资料|画像|账户|账号|邮箱|"
        r"密码|密钥|附件|档案|个人信息)|本人|个人|我(?:是|叫|住在|来自|有|使用)))"
        r"[^。！？；;\n]+[。！？；;]?",
        re.I,
    )
    _PERSONAL_ENGLISH = re.compile(
        r"(?:^|(?<=[.!?,;]))\s*(?:my\s+(?:name|email|profile|account|address)\s+is|"
        r"i(?:'m| am| live in| come from)|private\s+(?:document|file))"
        r"[^.!?,;\n]*[.!?,;]?",
        re.I,
    )
    _TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{1,}|[\u4e00-\u9fff]{2,}")
    _RECENT_YEARS = re.compile(r"近\s*([一二两三四五六七八九十\d]+)\s*年|last\s+(\d+)\s+years?", re.I)
    _COUNT = re.compile(r"(?:前|最多|至少|返回|推荐)\s*(\d{1,3})\s*(?:篇|个|条|papers?|results?)?", re.I)
    _AUTHOR = re.compile(
        r"(?:作者|author(?:s)?|by)\s*(?:是|为|叫|:|：)?\s*([A-Za-z][A-Za-z .'-]{1,100}|[\u4e00-\u9fff]{2,12})",
        re.I,
    )
    _TITLE = re.compile(r"(?:标题|title)\s*(?:是|为|叫|:|：)?\s*[《“\"']?([^》”\"'，。；;\n]{2,200})", re.I)
    _QUERY_DIRECTIVES = re.compile(
        r"请|帮我|帮忙|一下|看看|告诉我|搜索|搜|查找|查一下|检索|筛选|找出|找几篇|找|"
        r"推荐|核对|对比|关于|有关|论文|文献|文章|研究|作者|author(?:s)?|标题|title|"
        r"是|为|叫|最多|至少|返回|前|篇|个|条|years?|year|年|20\d{2}",
        re.I,
    )
    _STOPWORDS = frozenset(
        {
            "请", "帮我", "帮忙", "一下", "看看", "告诉我", "论文", "文献", "文章",
            "研究", "搜索", "搜", "查找", "检索", "筛选", "找出", "推荐", "核对", "对比",
            "近", "年", "作者", "标题", "关于", "有关", "的", "并且", "以及", "最好", "前",
            "最多", "至少", "返回", "篇", "个", "条", "arxiv", "paper", "papers", "find",
            "search", "look", "up", "retrieve", "filter", "last", "years", "year",
            "please", "help", "me", "about", "show", "give", "return", "at", "most",
            "to", "from", "the", "by", "in", "on", "for", "of", "and",
        }
    )
    _CONSTRAINT_WORDS = re.compile(
        r"作者|author|title|标题|近[一二两三四五六七八九十\d]+年|20\d{2}|前\d+|最多|至少|篇|papers?|results?",
        re.I,
    )
    _MULTI_TASK = re.compile(
        r"(?:论文.*(?:润色|改写|生成(?:一张|图片|图像|视频))|"
        r"(?:润色|改写|生成(?:一张|图片|图像|视频)).*论文)",
        re.I,
    )
    _GENERAL_WEB_TASK = re.compile(
        r"新闻|网页|网站|通用网页|latest\s+(?:news|updates)|web\s+search",
        re.I,
    )
    _VIDEO = re.compile(r"视频|短视频|video|clip", re.I)
    _VIDEO_DISCUSSION = re.compile(
        r"(?:这个|该|此)?(?:视频|短视频).*(?:讲了什么|怎么制作|如何制作|"
        r"是什么内容|内容是什么|什么意思|介绍了什么|总结|分析|评价)",
        re.I,
    )
    _VIDEO_ACTION = re.compile(
        r"生成|制作|做成|做一个|拍摄|创建|generate|create|make|turn\s+.+\s+into",
        re.I,
    )
    _VIDEO_EDIT = re.compile(
        r"剪辑|编辑|裁剪|拼接|加字幕|配音|口型|换背景|edit|trim|splice|dub|lip\s*sync",
        re.I,
    )
    _VIDEO_DURATION = re.compile(
        r"(?<!\d)(\d{1,3}|[一二两三四五六七八九十]+)\s*"
        r"(?:秒|s|seconds?)(?![A-Za-z0-9_])",
        re.I,
    )
    _VIDEO_ASPECT_16_9 = re.compile(r"16\s*[:：比/]\s*9|横屏|宽屏", re.I)
    _VIDEO_ASPECT_9_16 = re.compile(r"9\s*[:：比/]\s*16|竖屏|竖版|手机屏", re.I)
    _VIDEO_UNSUPPORTED_ASPECT = re.compile(
        r"(?:4\s*[:：比/]\s*3|1\s*[:：比/]\s*1|21\s*[:：比/]\s*9)", re.I
    )
    _VIDEO_PREFIX = re.compile(
        r"^\s*(?:请|帮我|请帮我)?\s*(?:生成|制作|做成|做一个|拍摄|创建|"
        r"generate|create|make)\s*(?:一段|一条|一个|一支|一部)?\s*"
        r"(?:\d{1,3}\s*(?:秒|s|seconds?)\s*)?(?:的\s*)?"
        r"(?:横屏|宽屏|竖屏|竖版|手机屏|短视频|视频|video|clip)?\s*",
        re.I,
    )
    _VIDEO_PARAMETER = re.compile(
        r"\s*(?:\d{1,3}\s*(?:秒|s|seconds?)|16\s*[:：比/]\s*9|"
        r"9\s*[:：比/]\s*16|横屏|宽屏|竖屏|竖版|手机屏)\s*",
        re.I,
    )
    _VIDEO_TRAILING_WORD = re.compile(r"(?:短视频|视频|video|clip)\s*$", re.I)

    _CAREER_ACTION_WORDS = (
        "规划",
        "方向",
        "路径",
        "选择",
        "转行",
        "求职",
        "找工作",
        "找实习",
        "该不该",
        "要不要",
        "适合",
        "打算",
        "考虑",
    )
    _CAREER_INFO_WORDS = ("是什么", "什么意思", "怎么理解", "概念", "介绍一下")
    _LEARNING_WORDS = (
        "学习计划",
        "学习规划",
        "学习路线",
        "课程规划",
        "复习计划",
        "怎么复习",
        "怎么学",
        "学习方向",
    )
    _HUMANIZER_WORDS = (
        "润色",
        "改写",
        "改得自然",
        "更自然",
        "去模板腔",
        "人味",
        "优化简历",
        "润色简历",
    )
    _PAPER_ACTION_WORDS = (
        "搜索论文",
        "查论文",
        "找论文",
        "检索论文",
        "推荐论文",
        "筛选论文",
    )
    _IMAGE_WORDS = ("生成图片", "生成一张图", "做一张海报", "画一张", "生成图像")
    _VIDEO_WORDS = ("生成视频", "做个视频", "制作视频", "生成短片", "做成短视频")

    _ENGLISH_COUNT = re.compile(
        r"(?:(?:at\s+most|up\s+to|return|show|give(?:\s+me)?|maximum|max|"
        r"find|search|look(?:\s+up)?)\s*)?"
        r"(\d{1,3})\s+(?:papers?|results?)\b",
        re.I,
    )
    _TITLE_QUOTED = re.compile(
        r"(?:标题|title(?:d)?)\b\s*(?:是|为|叫|:|is)?\s*"
        r"[\"'“‘]([^\"'”’]{2,200})[\"'”’]",
        re.I,
    )
    _TITLE_PLAIN = re.compile(
        r"(?:标题|title(?:d)?)\b\s*(?:是|为|叫|:|is)?\s*"
        r"([^,.;，。；\n]{2,200})",
        re.I,
    )
    _YEAR_RANGE = re.compile(
        r"(?<![\d.])(\d{4})\s*(?:-|–|—|到|至|~)\s*(\d{4})(?![\d.])"
    )
    _YEAR_SINGLE = re.compile(r"(?<![\d.])(\d{4})(?![\d.])")

    def __init__(self, registry: CapabilityRouteRegistry | None = None) -> None:
        self.registry = registry or CapabilityRouteRegistry.builtin()

    def classify(self, content: str) -> CapabilityRoute:
        text = content.strip()
        if not text:
            return self._clarify("paper_empty_query", "你想查哪一主题的论文？")

        career_route = self._classify_career(text)
        if career_route is not None:
            return career_route

        if self._VIDEO.search(text) and self._VIDEO_DISCUSSION.search(text):
            return self._ordinary("当前请求是在讨论视频内容，而不是生成视频。")

        if self._VIDEO.search(text) and (
            self._VIDEO_ACTION.search(text) or self._VIDEO_EDIT.search(text)
        ):
            return self._classify_video(text)

        has_paper = bool(self._PAPER.search(text))
        has_action = bool(self._ACTION.search(text))
        has_id = bool(self._ARXIV_ID.search(text))
        if self._REWRITE.search(text) and not self._OTHER_CAPABILITY.search(text):
            return self._ordinary("当前请求更像论文内容改写，而不是查找论文。")
        if self._MULTI_TASK.search(text) or (
            has_paper and self._GENERAL_WEB_TASK.search(text)
        ):
            return self._clarify(
                "multiple_capabilities",
                "这条消息包含多个任务；你想先执行论文搜索，还是先完成其他任务？",
            )
        if not has_paper and not has_id:
            return self._ordinary("未识别到论文搜索意图。")
        if self._KNOWLEDGE_QA.search(text) and not has_id:
            return self._ordinary("当前请求更像论文内容处理或知识库问答。")
        if not has_action and not has_id:
            return self._ordinary("仅提到论文，不足以触发论文搜索。")

        try:
            constraints = self._constraints(text)
        except _InvalidPaperQuery as exc:
            if exc.code == "paper_empty_query":
                return self._clarify(exc.code, exc.message)
            return CapabilityRoute(
                status=RouteStatus.REJECTED,
                main_capability=MainCapability.PAPER_SEARCH,
                confidence=0.99,
                reason="论文搜索参数校验未通过。",
                error_code=exc.code,
                clarification_question=exc.message,
                knowledge_base_allowed=False,
                web_search_allowed=False,
            )
        if not any((constraints.topic_terms, constraints.author, constraints.title, constraints.arxiv_id)):
            return self._clarify("paper_empty_query", "你想查哪一主题、作者、标题或 arXiv 标识符？")
        plan = PaperSearchPlan(
            normalized_query=self._normalized_query(constraints), constraints=constraints
        )
        return CapabilityRoute(
            status=RouteStatus.MATCHED,
            main_capability=MainCapability.PAPER_SEARCH,
            confidence=0.99 if has_id or has_action else 0.8,
            reason="识别到明确的论文查找/筛选/核对请求。",
            paper_search=plan,
            knowledge_base_allowed=False,
            web_search_allowed=False,
        )

    def _classify_career(self, text: str) -> CapabilityRoute | None:
        """在统一路由合同中编译自然语言生涯规划意图。"""
        if not self._is_career_request(text):
            return None
        signals: list[MainCapability] = [MainCapability.CAREER]
        if self._is_learning_request(text):
            signals.append(MainCapability.ORDINARY_CHAT)
        if any(word in text for word in self._HUMANIZER_WORDS):
            signals.append(MainCapability.HUMANIZER)
        if "论文" in text and any(word in text for word in self._PAPER_ACTION_WORDS):
            signals.append(MainCapability.PAPER_SEARCH)
        if any(word in text for word in self._IMAGE_WORDS):
            signals.append(MainCapability.IMAGE)
        if any(word in text for word in self._VIDEO_WORDS):
            signals.append(MainCapability.VIDEO)
        if len(signals) > 1:
            return CapabilityRoute(
                status=RouteStatus.CLARIFY,
                main_capability=MainCapability.CLARIFICATION,
                confidence=0.35,
                reason="这条消息包含多个独立任务，需要先确定主能力。",
                clarification_question="这条消息包含多个独立任务，本轮先做哪一项？",
                error_code="multiple_text_capabilities",
                knowledge_base_allowed=False,
                web_search_allowed=False,
            )
        contract = self._compile_career_contract(text)
        return CapabilityRoute(
            status=RouteStatus.MATCHED,
            main_capability=MainCapability.CAREER,
            confidence=0.99,
            reason="识别到明确的生涯规划决策请求。",
            normalized_query=text[:2_000],
            career_contract=contract,
            knowledge_base_allowed="authorized_knowledge_base" in contract.evidence_requirements,
            web_search_allowed="current_search" in contract.evidence_requirements,
        )

    def _is_career_request(self, text: str) -> bool:
        if not text or any(word in text for word in self._CAREER_INFO_WORDS):
            return False
        if re.fullmatch(r".{0,20}职业(?:发展|方向)?前景(?:怎么样|如何)[？?。]?", text):
            return False
        if self._is_learning_request(text) and not any(
            word in text for word in ("职业", "就业", "求职", "转行", "工作方向")
        ):
            return False
        if is_career_intent(text):
            if text.startswith(("生涯规划助手：", "生涯规划：", "职业规划：")):
                return True
            if any(
                word in text
                for word in self._CAREER_ACTION_WORDS
                if word not in {"规划", "方向", "路径", "选择"}
            ):
                return True
            if any(word in text for word in ("怎么", "如何", "比较", "想做", "想规划", "帮我")):
                return True
            if re.search(r"规划.{0,20}(职业|就业|方向|路径|方案)", text):
                return True
            return False
        return bool(
            ("规划" in text and any(word in text for word in ("方向", "职业", "就业", "工作")))
            or re.search(r"适合.{0,12}(工作|岗位|职业)", text)
            or re.search(r"(科研|企业).{0,8}(选择|方向|发展)", text)
            or re.search(r"毕业后.{0,12}(去哪|做什么|方向)", text)
        )

    def _is_learning_request(self, text: str) -> bool:
        return any(word in text for word in self._LEARNING_WORDS)

    def _compile_career_contract(self, text: str) -> CareerPlanningRouteContract:
        target = "明确当前阶段可验证的职业方向，并比较可选路径"
        if "转行" in text:
            target = "评估是否转行，并比较转行与留在当前方向的可行路径"
        elif any(word in text for word in ("求职", "找工作", "找实习")):
            target = "规划求职方向、准备重点和近期验证行动"
        elif "考研" in text and any(word in text for word in ("职业", "就业", "方向")):
            target = "规划考研后的职业方向，并比较继续深造与就业选择"
        elif "科研" in text and "企业" in text:
            target = "比较科研与企业路径，并明确当前阶段的验证行动"

        time_horizon = "未来 1-3 年（先做近 90 天验证）"
        time_match = re.search(
            r"(?:未来|接下来|近|之后)\s*([一二三四五六七八九十百\d]+\s*(?:个)?(?:月|年|周|天))",
            text,
        )
        if time_match:
            time_horizon = f"未来{time_match.group(1)}"
        elif "长期" in text:
            time_horizon = "长期（先做近 90 天验证）"

        constraints: list[str] = []
        location = re.search(
            r"(?:地点|城市|地区)\s*(?:先)?(?:考虑|限定|选择|是|为|：|:)\s*([^，。；,;\s]+)",
            text,
        )
        if location:
            constraints.append(f"地点：{location.group(1)}")
        for marker, label in (("不想", "用户不希望"), ("不能", "用户不能"), ("预算", "预算")):
            index = text.find(marker)
            if index >= 0:
                fragment = text[index : index + 24].rstrip("，。；,;")
                constraints.append(f"{label}：{fragment}")

        evidence_requirements: list[
            Literal["user_statement", "authorized_knowledge_base", "current_search"]
        ] = ["user_statement"]
        if any(word in text for word in ("知识库", "资料", "材料")):
            evidence_requirements.append("authorized_knowledge_base")
        if any(word in text for word in ("最新", "当前", "政策", "趋势", "岗位", "市场", "薪资")):
            evidence_requirements.append("current_search")
        intake = assess_intake(text)
        open_questions = [intake.question] if not intake.enough and intake.question else []
        return CareerPlanningRouteContract(
            target=target,
            time_horizon=time_horizon,
            constraints=constraints,
            evidence_requirements=evidence_requirements,
            image_usage=(
                "task_relevant_minimal_slice"
                if any(word in text for word in ("图片", "画像", "截图"))
                else "none"
            ),
            open_questions=open_questions,
        )

    def route_explicit_video(
        self,
        prompt: str,
        *,
        aspect_ratio: str = "16:9",
        duration_seconds: int = VIDEO_DEFAULT_DURATION_SECONDS,
    ) -> CapabilityRoute:
        """把旧的显式视频载荷编译为同一份版本化路由合同。"""
        try:
            plan = self._video_plan(prompt, aspect_ratio, duration_seconds)
        except _InvalidVideoRequest as exc:
            return self._video_rejected(exc.code, exc.message)
        return self._video_matched(plan, reason="已提交显式视频能力载荷")

    def _classify_video(self, text: str) -> CapabilityRoute:
        has_paper_action = bool(
            self._PAPER.search(text)
            and (self._ACTION.search(text) or self._ARXIV_ID.search(text))
        )
        has_other_capability = bool(
            self._REWRITE.search(text)
            or re.search(
                r"生成(?:一张|图片|图像)|职业规划|转行|考研|找工作|"
                r"人味化|更像人类|humanizer",
                text,
                re.I,
            )
        )
        if has_paper_action or has_other_capability:
            return self._clarify(
                "multiple_capabilities",
                "这条消息包含多个任务；请先只说明视频生成，或先执行其他任务。",
            )
        if self._VIDEO_EDIT.search(text):
            return self._video_rejected(
                "video_editing_unsupported",
                "当前只支持文生视频，不支持编辑现有视频、剪辑、字幕或配音。",
            )
        try:
            duration = self._video_duration(text)
            aspect_ratio = self._video_aspect_ratio(text)
            prompt = self._video_prompt(text)
            if not prompt:
                raise _InvalidVideoRequest(
                    "video_missing_scene", "请说明视频要展示的主题、场景或镜头内容。"
                )
            plan = self._video_plan(prompt, aspect_ratio, duration)
        except _InvalidVideoRequest as exc:
            if exc.code == "video_missing_scene":
                return self._clarify(exc.code, exc.message)
            return self._video_rejected(exc.code, exc.message)
        return self._video_matched(plan, reason="识别到明确的文生视频请求。")

    def _video_plan(self, prompt: str, aspect_ratio: str, duration: int) -> VideoGenerationPlan:
        if aspect_ratio not in {"16:9", "9:16"}:
            raise _InvalidVideoRequest(
                "video_aspect_unsupported", "视频画幅目前只支持 16:9 横屏或 9:16 竖屏。"
            )
        if duration not in VIDEO_SUPPORTED_DURATIONS_SECONDS:
            raise _InvalidVideoRequest(
                "video_duration_unsupported", "视频时长目前只支持 5 秒或 10 秒。"
            )
        typed_aspect_ratio = cast(Literal["16:9", "9:16"], aspect_ratio)
        typed_size = cast(
            Literal["1280*720", "720*1280"],
            "720*1280" if aspect_ratio == "9:16" else VIDEO_DEFAULT_SIZE,
        )
        typed_duration = cast(Literal[5, 10], duration)
        try:
            return VideoGenerationPlan(
                model_id=VIDEO_MODEL_ID,
                prompt=prompt.strip(),
                aspect_ratio=typed_aspect_ratio,
                size=typed_size,
                duration_seconds=typed_duration,
                account_object_domain="account",
            )
        except ValidationError as exc:
            message = "视频请求参数不受支持，请使用 5 或 10 秒、16:9 或 9:16。"
            if "duration_seconds" in str(exc):
                code = "video_duration_unsupported"
            elif "aspect_ratio" in str(exc) or "size" in str(exc):
                code = "video_aspect_unsupported"
            else:
                code = "video_contract_invalid"
            raise _InvalidVideoRequest(code, message) from exc

    def _video_duration(self, text: str) -> int:
        match = self._VIDEO_DURATION.search(text)
        if match is None:
            return VIDEO_DEFAULT_DURATION_SECONDS
        raw_duration = match.group(1)
        duration = (
            int(raw_duration)
            if raw_duration.isdigit()
            else _CHINESE_NUMBERS.get(raw_duration, 0)
        )
        if duration not in VIDEO_SUPPORTED_DURATIONS_SECONDS:
            raise _InvalidVideoRequest(
                "video_duration_unsupported", "视频时长目前只支持 5 秒或 10 秒。"
            )
        return duration

    def _video_aspect_ratio(self, text: str) -> str:
        if self._VIDEO_UNSUPPORTED_ASPECT.search(text):
            raise _InvalidVideoRequest(
                "video_aspect_unsupported", "视频画幅目前只支持 16:9 横屏或 9:16 竖屏。"
            )
        if self._VIDEO_ASPECT_9_16.search(text):
            return "9:16"
        return "16:9"

    def _video_prompt(self, text: str) -> str:
        prompt = text
        if "：" in prompt or ":" in prompt:
            prefix, candidate = re.split(r"[：:]", prompt, maxsplit=1)
            if candidate.strip():
                prompt = candidate
        else:
            into_match = re.match(
                r"^\s*(?:请|帮我|请帮我)?把(?P<scene>.+?)"
                r"(?:做成|制作成|转成|变成)\s*"
                r"(?:一段|一条|一个|一支)?\s*(?:短视频|视频|video|clip)\s*$",
                prompt,
                re.I,
            )
            english_into_match = re.match(
                r"^\s*(?:turn|make|create|generate)\s+(?P<scene>.+?)\s+"
                r"into\s+(?:a\s+)?(?:video|clip)\s*$",
                prompt,
                re.I,
            )
            if into_match is not None:
                prompt = into_match.group("scene")
            elif english_into_match is not None:
                prompt = english_into_match.group("scene")
            else:
                prompt = self._VIDEO_PREFIX.sub("", prompt, count=1)
        prompt = self._VIDEO_PARAMETER.sub(" ", prompt)
        prompt = self._VIDEO_TRAILING_WORD.sub("", prompt)
        prompt = re.sub(r"\s+", " ", prompt).strip(" ，,。.!！?？：:")
        return prompt

    def _video_matched(self, plan: VideoGenerationPlan, *, reason: str) -> CapabilityRoute:
        return CapabilityRoute(
            status=RouteStatus.MATCHED,
            main_capability=MainCapability.VIDEO,
            confidence=0.99,
            reason=reason,
            video=plan,
            knowledge_base_allowed=False,
            web_search_allowed=False,
        )

    def _video_rejected(self, code: str, message: str) -> CapabilityRoute:
        return CapabilityRoute(
            status=RouteStatus.REJECTED,
            main_capability=MainCapability.VIDEO,
            confidence=0.99,
            reason="视频请求未通过能力或参数校验。",
            clarification_question=message,
            error_code=code,
            knowledge_base_allowed=False,
            web_search_allowed=False,
        )

    def _constraints(self, text: str) -> PaperSearchConstraints:
        cleaned = self._scrub(text)
        arxiv_match = self._ARXIV_ID.search(text)
        arxiv_id = arxiv_match.group(1) if arxiv_match else None
        author_match = self._AUTHOR.search(text)
        explicit_author = bool(author_match and re.search(r"作者|author|by", author_match.group(0), re.I))
        author = author_match.group(1).strip(" .,:：") if explicit_author and author_match else None
        if author:
            author = re.sub(
                r"\s+(?:from|between|in|about|on|for)$", "", author, flags=re.I
            ).strip()
        title_match = (
            self._TITLE_QUOTED.search(text)
            or self._TITLE_PLAIN.search(text)
            or self._TITLE.search(text)
        )
        title = title_match.group(1).strip() if title_match else None
        year_from, year_to = self._years(text)
        count_match = self._COUNT.search(text) or self._ENGLISH_COUNT.search(text)
        max_results = int(count_match.group(1)) if count_match else 5
        if not 1 <= max_results <= 10:
            raise _InvalidPaperQuery("paper_result_limit", "一次最多只能查询 10 篇论文，请缩小数量范围。")
        cleaned_for_tokens = cleaned
        if author:
            cleaned_for_tokens = cleaned_for_tokens.replace(author, " ")
        if title:
            cleaned_for_tokens = cleaned_for_tokens.replace(title, " ")
        if arxiv_id:
            cleaned_for_tokens = cleaned_for_tokens.replace(arxiv_id, " ")
        cleaned_for_tokens = self._YEAR_RANGE.sub(" ", cleaned_for_tokens)
        cleaned_for_tokens = self._YEAR_SINGLE.sub(" ", cleaned_for_tokens)
        cleaned_for_tokens = self._QUERY_DIRECTIVES.sub(" ", cleaned_for_tokens)
        tokens = [
            token.strip("-_的")
            for token in self._TOKEN.findall(cleaned_for_tokens)
            if token.lower() not in self._STOPWORDS
            and not self._CONSTRAINT_WORDS.fullmatch(token)
            and token != arxiv_id
            and token.strip("-_的")
        ]
        # 明确作者/标题条件才保留；普通“作者”提及不能把身份信息发往 arXiv。
        topic_terms = tokens[:10]
        if author and not any(author_part.lower() in " ".join(topic_terms).lower() for author_part in author.split()):
            topic_terms.append(f"author:{author}")
        if title:
            topic_terms.append(f"title:{title}")
        if arxiv_id:
            topic_terms.append(f"id:{arxiv_id}")
        if year_from is not None or year_to is not None:
            topic_terms.append(f"year:{year_from or 1900}-{year_to or datetime.now(UTC).year}")
        if not any((topic_terms, author, title, arxiv_id)):
            raise _InvalidPaperQuery(
                "paper_empty_query", "你想查哪一主题、作者、标题或 arXiv 标识符？"
            )
        return PaperSearchConstraints(
            topic_terms=topic_terms[:12],
            author=author,
            title=title,
            arxiv_id=arxiv_id,
            year_from=year_from,
            year_to=year_to,
            max_results=max_results,
        )

    def _years(self, text: str) -> tuple[int | None, int | None]:
        match = self._YEAR_RANGE.search(text)
        if match:
            year_from, year_to = int(match.group(1)), int(match.group(2))
        else:
            single = self._YEAR_SINGLE.search(text)
            if single:
                year_from = year_to = int(single.group(1))
            else:
                recent = self._RECENT_YEARS.search(text)
                if not recent:
                    return None, None
                count = recent.group(1) or recent.group(2) or "0"
                count = int(count) if count.isdigit() else _CHINESE_NUMBERS.get(count, 0)
                if not 1 <= count <= 10:
                    raise _InvalidPaperQuery("paper_year_range", "年份范围应在 1 到 10 年内，请重新说明。")
                year_to = datetime.now(UTC).year
                year_from = year_to - count + 1
        if year_from < 1900 or year_to > datetime.now(UTC).year + 1 or year_from > year_to:
            raise _InvalidPaperQuery("paper_year_range", "年份范围不合法，请提供 1900 年以后且起止顺序正确的年份。")
        return year_from, year_to

    def _scrub(self, text: str) -> str:
        cleaned = self._CODE.sub(" ", text)
        cleaned = self._SECRET.sub(" ", cleaned)
        cleaned = self._PRIVATE.sub(" ", cleaned)
        cleaned = self._PERSONAL.sub(" ", cleaned)
        cleaned = self._EMAIL.sub(" ", cleaned)
        cleaned = self._PERSONAL_ENGLISH.sub(" ", cleaned)
        cleaned = self._URL.sub(" ", cleaned)
        return cleaned

    def _normalized_query(self, constraints: PaperSearchConstraints) -> str:
        return " ".join(constraints.topic_terms)[:240].strip() or "公开论文主题"

    def _ordinary(self, reason: str) -> CapabilityRoute:
        return CapabilityRoute(
            status=RouteStatus.ORDINARY,
            main_capability=MainCapability.ORDINARY_CHAT,
            confidence=0.99,
            reason=reason,
        )

    def _clarify(self, code: str, question: str) -> CapabilityRoute:
        return CapabilityRoute(
            status=RouteStatus.CLARIFY,
            main_capability=MainCapability.CLARIFICATION,
            confidence=0.35,
            reason="论文请求缺少确定路由所需的信息。",
            clarification_question=question,
            error_code=code,
            knowledge_base_allowed=False,
            web_search_allowed=False,
        )


class _InvalidPaperQuery(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class _InvalidVideoRequest(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


_CHINESE_NUMBERS = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
