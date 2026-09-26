"""词表与确定性抽取（无网络、无模型）。

解析只做两件事：把用户原文里的**核心场景**与**必要功能**切成可检索的原词
（逐字保留，不翻译、不同义替换），以及识别「找实现它的项目」这类**指向前文**
的请求。指代链不在这里解析——前文原词的来源由 ``parsing`` 从已确认的会话
前文里取，这里只负责判断本轮有没有自己的主题。
"""

from __future__ import annotations

import re

#: 上游主机与接口（写入查询记录时用的来源名）。
GITHUB_HOST = "github.com"
GITHUB_API_HOST = "api.github.com"

#: 查询记录的来源标识：检索接口与仓库读取分开记，正文里的「实际查询词」只取前者。
SEARCH_SOURCE = "github_search"
INSPECT_SOURCE = "github_repository"

#: 多字请求壳：抽取 idea 原词时在**首尾**整体剥离（不改写中间保留的词）。
INTENT_PHRASES: tuple[str, ...] = (
    "帮我做一个",
    "帮我做个",
    "我想做一款",
    "我想做一个",
    "我想做个",
    "我要做一个",
    "我要做个",
    "想做个",
    "帮我",
    "帮忙",
    "我需要",
    "我想做",
    "我想",
    "我要",
    "请帮",
    "请",
    "麻烦",
    "推荐",
    "有没有人做过",
    "有没有",
    "有无",
    "给我",
    "列出",
    "列一下",
    "给出",
    "介绍",
    "看看",
    "看下",
    "找个",
    "找一个",
    "找一些",
    "找一下",
    "找找",
    "搜个",
    "搜一下",
    "查一下",
    "那些",
    "几个",
    "一些",
    "类似",
    "差不多",
    "相似",
    "接近",
    "现成的",
    "现成",
    "开源的",
    "开源",
    "好用的",
    "比较好",
    "参考",
    "学习",
    "做一个",
    "做一款",
    "做个",
    "弄个",
    "要能",    "支持",
    "能够",
    "可以",
    "需要",
    "要有",
    "还要",
    "github",
    "项目里",
    "项目",
    "仓库",
    "repository",
    "repo",
    "代码",
    "轮子",
    "实现",
    "做的",
    "做的事",
    "一个",
    "一款",
    "一套",
    "这样",
    "这个",
    "那个",
)

#: 单字请求动词：只在**首部且后面紧跟分隔符／空格／结束**时剥离。
#: 否则「搜索」会被剥成「索想要的书」——原词必须逐字保留。
INTENT_LEAD_CHARS: frozenset[str] = frozenset({"找", "搜", "查", "看", "做", "要", "写", "请"})

#: 判定「本轮有没有自己的主题」用的激进词表（含单字，可出现在任何位置）。
INTENT_ALL: tuple[str, ...] = (*INTENT_PHRASES, *sorted(INTENT_LEAD_CHARS))

#: 指向前文的词：命中这些词且剥掉意图词后没有自己的主题时，才去前文找原词。
REFERENCE_MARKERS: tuple[str, ...] = (
    "它",
    "他",
    "他们",
    "这个",
    "那个",
    "这些",
    "那些",
    "这种",
    "那种",
    "上面",
    "上边",
    "刚才",
    "前面",
    "之前",
    "此",
    "该",
    "这",
)

#: 「只要某个组件」的明说词：命中即认为用户要找的不是完整产品。
COMPONENT_HINTS: tuple[str, ...] = (
    "组件",
    "控件",
    "插件",
    "中间件",
    "sdk",
    "SDK",
    "库",
    "接口",
    "模块",
    "某个功能",
    "单一功能",
)

#: 「要完整产品」的明说词：命中即认为用户要找整体项目。
WHOLE_HINTS: tuple[str, ...] = (
    "平台",
    "系统",
    "应用",
    "网站",
    "小程序",
    "app",
    "App",
    "APP",
    "整套",
    "完整",
    "端到端",
    "全流程",
)

#: 登记的技术词（原文里出现即作为可选技术词；只作补充线索，不替掉原词）。
TECH_TERMS: tuple[str, ...] = (
    "react",
    "vue",
    "next.js",
    "nextjs",
    "svelte",
    "angular",
    "typescript",
    "javascript",
    "python",
    "django",
    "flask",
    "fastapi",
    "spring",
    "springboot",
    "java",
    "golang",
    "rust",
    "node",
    "nodejs",
    "flutter",
    "android",
    "ios",
    "swift",
    "kotlin",
    "unity",
    "unreal",
    "mysql",
    "postgresql",
    "postgres",
    "mongodb",
    "redis",
    "sqlite",
    "docker",
    "kubernetes",
    "langchain",
    "langgraph",
    "pytorch",
    "tensorflow",
    "opencv",
    "wechat",
    "微信小程序",
    "小程序",
)

#: 必要功能的切分符（只按用户自己的分段切，不重写措辞）。
_FEATURE_SEPARATORS = re.compile(
    r"[、，,；;。\n\r]+|(?:并且|同时|还要|还有|以及|然后|并且可以|又可以|也可)"
)

#: 有意义的连续短语长度下限（短于此的碎段不算要点）。
MIN_FEATURE_CHARS = 2

#: 必要功能条目上限（有界，避免把整段话当要点列表）。
MAX_FEATURES = 6

#: 核心场景的长度上限（超出即截断，检索词有界）。
MAX_SCENARIO_CHARS = 60

#: 剥离意图词时按长度倒序，避免「找」先吃掉「找一下」。
_ORDERED_PHRASES: tuple[str, ...] = tuple(sorted(INTENT_PHRASES, key=len, reverse=True))

#: 结尾处**不剥**的意图词：它们常是复合名词的后半截（深度学习／联邦学习／
#: 技术支持／电影推荐），在结尾剥离会把用户要的东西改成另一件事。
TRAILING_KEEP: frozenset[str] = frozenset(
    {"学习", "参考", "支持", "需要", "可以", "能够", "介绍", "推荐", "列出", "给出"}
)

_ORDERED_TRAILING: tuple[str, ...] = tuple(
    word for word in _ORDERED_PHRASES if word not in TRAILING_KEEP
)
_ORDERED_ALL: tuple[str, ...] = tuple(sorted(INTENT_ALL, key=len, reverse=True))

_QUOTED = re.compile(r"[「“\"']([^」”\"']{1,80})[」”\"']")
_LATIN_SEQUENCE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-_]*(?:\s+[A-Za-z][A-Za-z0-9+.\-_]*)*")
_CJK = re.compile(r"[\u4e00-\u9fff]")
_WHITESPACE = re.compile(r"\s+")
_EDGE_CHARS = " ，。！？、；：,.!?;:-—~～·「」【】（）()《》<>"
_LEAD_DELIMITERS = frozenset(" ，。！？、；：,.!?;:-—~～·\t")


#: 可选技术词上限（有界，只作排序辅助）。
MAX_TECH_TERMS = 6

#: 场景末尾的通用品类词（按长度倒序匹配，剥一次就停）。
GENERIC_SCENARIO_SUFFIXES: tuple[str, ...] = tuple(
    sorted(
        (
            "小程序",
            "管理系统",
            "平台",
            "系统",
            "网站",
            "网页",
            "应用",
            "软件",
            "工具",
            "程序",
            "服务",
            "后台",
            "管理",
        ),
        key=len,
        reverse=True,
    )
)


def strip_intent_words(text: str) -> str:
    """剥离请求壳（只剥首尾），其余原词与语序逐字保留。

    单字请求动词（找／搜／查／看…）只在后面紧跟分隔符或结束时才剥离：
    「搜索想要的书」里的「搜」属于原词，剥掉就成了「索想要的书」。
    剥完只剩助词（「的」）时按空处理——碎片不是主题。
    """
    value = text.strip()
    for _ in range(8):
        before = value
        value = _strip_leading_phrases(value)
        value = _strip_trailing_phrases(value)
        value = _strip_leading_chars(value)
        if value == before:
            break
    value = value.strip(_EDGE_CHARS).strip()
    if not _CJK.search(value) and not _LATIN_SEQUENCE.search(value):
        return ""
    # 只剥剩助词时按空处理：碎片（「的」）不是主题。
    return value if len(value) >= MIN_FEATURE_CHARS else ""


def strip_all_intent_words(text: str) -> str:
    """激进剥离（**任何位置**的请求壳），只用于「本轮有没有自己的主题」的判定。"""
    stripped = text
    for word in _ORDERED_ALL:
        stripped = stripped.replace(word, " ")
    return _WHITESPACE.sub(" ", stripped).strip(_EDGE_CHARS).strip()


def _strip_leading_phrases(value: str) -> str:
    changed = True
    while changed:
        changed = False
        candidate = value.lstrip()
        for word in _ORDERED_PHRASES:
            if candidate.startswith(word):
                value = candidate[len(word) :].lstrip()
                changed = True
                break
    return value


def _strip_trailing_phrases(value: str) -> str:
    changed = True
    while changed:
        changed = False
        candidate = value.rstrip()
        for word in _ORDERED_TRAILING:
            if candidate.endswith(word):
                value = candidate[: -len(word)].rstrip()
                changed = True
                break
        if not changed:
            # 结尾助词：「能记账的」里的「的」不是检索词，留着只会拉低召回。
            trimmed = candidate.rstrip("的了着")
            if trimmed != candidate and len(trimmed) >= MIN_FEATURE_CHARS:
                value = trimmed
                changed = True
    return value


def _strip_leading_chars(value: str) -> str:
    candidate = value.lstrip()
    if not candidate:
        return candidate
    head = candidate[0]
    if head not in INTENT_LEAD_CHARS:
        return candidate
    if len(candidate) == 1 or candidate[1] in _LEAD_DELIMITERS:
        return candidate[1:].lstrip()
    return candidate


def strip_reference_markers(text: str) -> str:
    """剥离指代词，用于判断本轮有没有自己的主题。"""
    stripped = text
    for marker in sorted(REFERENCE_MARKERS, key=len, reverse=True):
        stripped = stripped.replace(marker, " ")
    return _WHITESPACE.sub(" ", stripped).strip(_EDGE_CHARS).strip()


def has_own_subject(text: str) -> bool:
    """本轮是否自带主题：剥掉请求壳与指代词后仍有实义中文或拉丁串。"""
    remainder = strip_reference_markers(strip_all_intent_words(text))
    if len(remainder) >= MIN_FEATURE_CHARS:
        return True
    return bool(len(remainder.strip()) >= 1 and _LATIN_SEQUENCE.search(remainder))


def extract_scenario(text: str) -> str:
    """抽取核心场景：引号内短语优先，否则取剥离意图词后的**第一段**。

    idea 通常写成「核心场景，功能一、功能二」，第一段就是场景；整句都当成
    场景会让检索词长到召回为零。
    """
    quoted = _QUOTED.search(text)
    if quoted is not None and quoted.group(1).strip():
        return _truncate(_normalize(quoted.group(1)))
    stripped = strip_intent_words(text)
    for segment in _FEATURE_SEPARATORS.split(stripped):
        cleaned = _normalize(segment)
        if len(cleaned) >= MIN_FEATURE_CHARS:
            return _truncate(cleaned)
    return _truncate(stripped)


def extract_features(text: str, *, scenario: str) -> list[str]:
    """把原文按用户自己的分段切成要点，逐字保留（不翻译、不改写）。

    场景本身不重复计入要点：它是整句话的主题，不是某一条功能。整句只有场景
    （没有分隔）时，要点即场景本身，避免「只有场景没有要点」。
    """
    features: list[str] = []
    for segment in _FEATURE_SEPARATORS.split(text):
        cleaned = strip_intent_words(_normalize(segment))
        if len(cleaned) < MIN_FEATURE_CHARS:
            continue
        if not _CJK.search(cleaned) and not _LATIN_SEQUENCE.search(cleaned):
            continue
        if cleaned == scenario or cleaned in features:
            continue
        features.append(cleaned)
        if len(features) >= MAX_FEATURES:
            break
    if not features and scenario:
        features = [scenario]
    return features


def extract_tech_terms(text: str) -> list[str]:
    """抽取可选技术词：登记技术词 + 原文里的拉丁串（逐字保留）。"""
    lowered = text.lower()
    found: list[str] = []
    for term in TECH_TERMS:
        if term.lower() in lowered and term not in found:
            found.append(term)
    for match in _LATIN_SEQUENCE.finditer(text):
        value = match.group(0).strip()
        if len(value) < 2 or value.lower() in {item.lower() for item in found}:
            continue
        if value.lower() in {"github", "repo", "repository", "app"}:
            continue
        found.append(value)
    return found[:MAX_TECH_TERMS]


def wants_whole_idea(text: str) -> bool:
    """用户要的是完整产品还是单个组件。

    只有明确说了「组件／库／插件…」且没有说「平台／系统／应用…」时才算组件；
    两种情况都提到或都没提到时按整体项目处理（先查整体，是设计的默认顺序）。
    """
    component = any(hint in text for hint in COMPONENT_HINTS)
    whole = any(hint in text for hint in WHOLE_HINTS)
    return whole or not component


def compact_scenario(scenario: str) -> str:
    """把场景末尾的通用品类词去掉，得到更适合检索的短语。

    上游检索对中文是按字模糊匹配的：``校园二手书交换平台``（10 字）会把简介里
    偶然含相同字的无关仓库一并召回，而 ``校园二手书交换`` 命中的几乎都是同类
    项目。这里只做**有界后缀剥离**（不翻译、不同义替换）：只剥一次、只剥品类词，
    剥完太短就原样返回——用户的原词仍在核心场景里逐字展示。
    """
    trimmed = scenario.strip()
    for suffix in GENERIC_SCENARIO_SUFFIXES:
        if not trimmed.endswith(suffix):
            continue
        remainder = trimmed[: -len(suffix)].strip()
        if len(remainder) >= MIN_FEATURE_CHARS + 2:
            return remainder
    return scenario


def _normalize(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip(_EDGE_CHARS).strip()


def _truncate(value: str) -> str:
    return value[:MAX_SCENARIO_CHARS].strip()
