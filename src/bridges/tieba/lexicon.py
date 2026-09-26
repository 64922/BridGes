"""贴吧信息搜集的确定性词表（不做模型推断）。

三份词表分别服务于三个真实判断：问题里哪些词是检索用的原始名词、哪些是
事件／地点、以及问题是否涉及校规／费用／开放时间／办事流程（需要追加官方
核验）。词表只做确定性匹配，任何未命中都如实按未命中处理。
"""

from __future__ import annotations

import re

#: 目标贴吧名称：结果只纳入有证据确认属于该吧的公开帖子。
TARGET_FORUM_NAME = "华东交通大学吧"

#: 指向本校的写法（含简称）：出现即不把摘要文本当作他吧证据。
TARGET_SCHOOL_ALIASES: tuple[str, ...] = ("华东交通大学", "华东交大", "华交")

#: 贴吧帖子页面的固定主机（最小公开查询词里的域名提示，真实召回需要它）。
TIEBA_HOST = "tieba.baidu.com"

#: 模块自身用语：这些词不能进入检索查询词。
MODULE_STOPWORDS: frozenset[str] = frozenset(
    {
        "贴吧",
        "吧里",
        "吧友",
        "帖子",
        "贴子",
        "华交吧",
        "华东交通大学",
        "交大",
        "帮忙",
        "帮我",
        "请",
        "一下",
        "看看",
        "查查",
        "查一下",
        "搜一下",
        "搜索",
        "搜集",
        "收集",
        "信息",
        "消息",
        "怎么样",
        "怎么说",
        "如何",
        "有没有",
        "有吗",
        "是不是",
        "有没有人",
        "大家",
        "最近",
        "现在",
        "听说",
        "据说",
        "求问",
        "请问",
        "想问",
        "想知道",
        "the",
        "and",
    }
)

#: 事件／地点类名词（校园语境）。命中即作为事件／地点记录并参与检索。
PLACE_EVENT_TERMS: frozenset[str] = frozenset(
    {
        "宿舍",
        "寝室",
        "食堂",
        "图书馆",
        "教室",
        "澡堂",
        "浴室",
        "热水",
        "开水机",
        "空调",
        "校园卡",
        "一卡通",
        "校园网",
        "快递",
        "校医院",
        "操场",
        "体育馆",
        "军训",
        "开学",
        "报到",
        "新生",
        "入学",
        "体检",
        "体测",
        "选课",
        "考试",
        "补考",
        "重修",
        "挂科",
        "成绩",
        "绩点",
        "奖学金",
        "助学贷款",
        "助学金",
        "社团",
        "学生会",
        "转专业",
        "保研",
        "考研",
        "复试",
        "调剂",
        "毕业",
        "学位",
        "实习",
        "校招",
        "就业",
        "招聘",
        "校园招聘",
        "寒暑假",
        "校历",
        "放假",
        "通勤",
        "校车",
        "班车",
        "南区",
        "北区",
        "军山湖校区",
        "进贤校区",
        "地铁",
        "公交",
        "快递站",
        "门禁",
        "晚归",
        "宿舍楼",
    }
)

#: 官方核验触发词：涉及校规、费用、开放时间或办事流程时必须追加官方核对。
OFFICIAL_TRIGGERS: frozenset[str] = frozenset(
    {
        "规定",
        "校规",
        "规章制度",
        "管理办法",
        "条例",
        "要求",
        "标准",
        "政策",
        "费用",
        "学费",
        "住宿费",
        "收费",
        "价格",
        "多少钱",
        "缴费",
        "退费",
        "报销",
        "开放时间",
        "几点",
        "时间表",
        "作息",
        "关闭时间",
        "开门",
        "关门",
        "流程",
        "手续",
        "怎么办",
        "办理",
        "申请",
        "材料",
        "证明",
        "审批",
        "备案",
        "转专业",
        "休学",
        "复学",
        "退学",
        "学分",
        "毕业要求",
        "考核",
        "评定",
        "认定",
        "报名",
        "录取",
        "招生",
        "考试安排",
        "成绩复核",
        "缓考",
        "免修",
    }
)

#: 官方页面只允许来自学校官方域名（含各二级学院子域）。
OFFICIAL_DOMAIN_SUFFIX = "ecjtu.edu.cn"

#: 时间条件：保留用户原话，并在能取得帖子时间时真正参与过滤。
_TIME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"20\d{2}\s*年(?:\s*\d{1,2}\s*月)?"),
    re.compile(r"\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?"),
    re.compile(
        r"最近|近期|这几天|这两天|上个月|这个月|本月|去年|今年|前年|"
        r"上学期|下学期|这学期|开学初|开学季|毕业季|期末|期中|寒假|暑假|上周|本周|这周"
    ),
    re.compile(r"20\d{2}-\d{2}(?:-\d{2})?"),
)

_YEAR = re.compile(r"(20\d{2})\s*年")

#: 语气词与结构词：不构成原始名词。
_PARTICLES = frozenset(
    {
        "的",
        "了",
        "吗",
        "呢",
        "吧",
        "啊",
        "呀",
        "是",
        "不",
        "在",
        "有",
        "和",
        "跟",
        "给",
        "对",
        "都",
        "很",
        "太",
        "会",
        "要",
        "能",
        "我",
        "你",
        "他",
        "她",
        "它",
        "们",
        "个",
        "些",
        "啥",
        "什么",
        "哪",
        "哪个",
        "多少",
        "里",
        "内",
        "外",
        "中",
        "上",
        "下",
        "想",
        "知道",
        "关于",
        "有关",
        "方面",
        "相关",
        "具体",
        "学校",
        "帖子",
        "贴子",
        "说法",
        "情况",
        "事情",
        "问题",
        "内容",
        "信息",
        "怎么样",
        "怎么",
        "如何",
    }
)

#: 尾随的形容词／疑问词：短语尾部的这些字不构成名词（「名额多」→「名额」）。
_TRAILING_MODIFIERS = frozenset({"多", "少", "好", "差", "大", "小", "高", "低", "吗", "呢"})

_SPLIT = re.compile(r"[\s,，。！？；;、:：/|\-—~“”\"'（）()\[\]{}<>=+*#@!?]+")
_CHINESE_RUN = re.compile(r"[\u4e00-\u9fff]{2,12}")
_LATIN_RUN = re.compile(r"[A-Za-z][A-Za-z0-9_+#.-]{1,30}")

#: 检索查询词的词数上限（最小公开查询词）。
MAX_QUERY_TERMS = 4


def detect_time_requirement(question: str) -> str | None:
    """返回问题里出现的时间条件原话；没有则 None（保持原文，不归一化改写）。"""
    for pattern in _TIME_PATTERNS:
        match = pattern.search(question)
        if match is not None:
            return match.group(0).strip()
    return None


def detect_time_year(question: str) -> int | None:
    """时间条件里的绝对年份（有 4 位年份时才有；相对说法返回 None）。"""
    match = _YEAR.search(question)
    return int(match.group(1)) if match is not None else None


def extract_topic_terms(question: str) -> list[str]:
    """提取检索用的原始名词，逐字保留用户写法，最多 ``MAX_QUERY_TERMS`` 个。

    先认校园词表里的长词（宿舍／转专业／保研…），再把模块用语与语气词从
    文本里去掉，剩余的短片段才作为补充原始名词；找不到任何名词时就返回空，
    由调用方去问一个问题。
    """
    remainder = question
    terms: list[str] = []
    for term in sorted(PLACE_EVENT_TERMS | OFFICIAL_TRIGGERS, key=len, reverse=True):
        if term in remainder and term not in terms:
            terms.append(term)
            remainder = remainder.replace(term, " ")
            if len(terms) >= MAX_QUERY_TERMS:
                return terms
    for word in sorted(MODULE_STOPWORDS | _PARTICLES, key=len, reverse=True):
        remainder = remainder.replace(word, " ")
    for chunk in _SPLIT.split(remainder):
        for candidate in _topic_candidates(chunk):
            if candidate in MODULE_STOPWORDS or candidate in _PARTICLES:
                continue
            if candidate in terms:
                continue
            terms.append(candidate)
            if len(terms) >= MAX_QUERY_TERMS:
                return terms
    return terms


def _topic_candidates(chunk: str) -> list[str]:
    """把一段文本切成候选词：中文按 2–8 字短语，西文按字母串。"""
    candidates: list[str] = []
    for match in _CHINESE_RUN.finditer(chunk):
        candidates.append(_trim_trailing_modifier(match.group(0)))
    for match in _LATIN_RUN.finditer(chunk):
        candidates.append(match.group(0))
    if not candidates and len(chunk.strip()) >= 2:
        candidates.append(_trim_trailing_modifier(chunk.strip()))
    return [candidate for candidate in candidates if len(candidate) >= 2]


def _trim_trailing_modifier(candidate: str) -> str:
    """去掉短语尾部的形容词／疑问词，让原始名词保持完整。"""
    while len(candidate) > 2 and candidate[-1] in _TRAILING_MODIFIERS:
        candidate = candidate[:-1]
    return candidate


def detect_place_or_event(terms: list[str]) -> list[str]:
    """在已提取的原始名词里标出事件／地点词（原词次序）。"""
    return [term for term in terms if term in PLACE_EVENT_TERMS]


def detect_official_topics(question: str, terms: list[str]) -> list[str]:
    """命中官方核验触发词的原始名词与触发词原话（用于官方查询词）。"""
    topics: list[str] = []
    for term in terms:
        if term in OFFICIAL_TRIGGERS and term not in topics:
            topics.append(term)
    for trigger in OFFICIAL_TRIGGERS:
        if trigger in question and trigger not in topics:
            topics.append(trigger)
    return topics
