"""确定性自然语言论文搜索路由。"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from bridges.routing.contracts import (
    CapabilityRoute,
    MainCapability,
    PaperSearchConstraints,
    PaperSearchPlan,
    RouteStatus,
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


_CHINESE_NUMBERS = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
