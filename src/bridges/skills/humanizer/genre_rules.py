"""体裁表达 profile 与确定性复核（Issue 28，人味化改造 Issue 03/04）。

每类体裁从 ``skill/genres/*.md`` 加载独立表达合同。人味化改造 Issue 04
起，体裁 profile 只规定任务目标、风险和可选表达方式，不再有「必须出现
定义短语/类比/练习停顿/局限/下一步」等正则完成条件；可选表达一律按需
使用，未声明体裁使用通用文章 profile。体裁复核只判断任务完成（正文
非空）与禁止模式，不搜索指定套话。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from bridges.contracts.expression import Genre

_GENRE_DIR = Path(__file__).resolve().parent / "skill" / "genres"


class GenreRuleError(Exception):
    """体裁规则资产缺失或损坏。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class GenreRule:
    """一条可测试的禁止模式断言。"""

    rule_id: str
    label: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class GenreStyleProfile:
    """单个体裁的表达 profile：任务目标、风险、可选表达与禁止模式。

    ``genre`` 为 None 表示通用文章 profile（无强制元素、无禁止模式）。
    可选表达方式全部按需使用，不构成正则完成条件。
    """

    genre: Genre | None
    display_name: str
    task_goal: str = ""
    risks: tuple[str, ...] = ()
    optional_devices: tuple[str, ...] = ()
    prohibited: tuple[GenreRule, ...] = ()
    human_responsibility: str = ""


@dataclass
class GenreCheckFinding:
    """单条规则检查结果。"""

    rule_id: str
    label: str
    kind: str  # completion | prohibited
    passed: bool
    detail: str


@dataclass
class GenreCheckResult:
    """一次体裁复核的结果；genre 为 None 表示通用文章 profile。

    Issue 04 起不检查任何必现元素：passed 只要求正文非空且无禁止模式。
    """

    genre: Genre | None
    passed: bool
    findings: list[GenreCheckFinding] = field(default_factory=list)

    def summary(self) -> list[str]:
        """面向用户的中文摘要。"""
        return [f"{'✓' if f.passed else '✗'} {f.label}：{f.detail}" for f in self.findings]


# ---------------------------------------------------------------------------
# 体裁 profile 定义（与 skill/genres/*.md 一一对应，BridGes 原创）
# ---------------------------------------------------------------------------

def _rule(rule_id: str, label: str, pattern: str) -> GenreRule:
    return GenreRule(rule_id, label, re.compile(pattern))


_POPULAR_SCIENCE = GenreStyleProfile(
    genre=Genre.POPULAR_SCIENCE,
    display_name="科普文案",
    task_goal="向非专业读者解释科学主题：让读者理解核心概念、知道与自己相关、清楚行动边界。",
    risks=(
        "把相关性说成因果或把初步结果说成定论",
        "隐藏不确定性（删除「可能/初步/在……条件下」等限定词）",
        "术语堆砌且不做解释",
    ),
    optional_devices=(
        "用一句话定义核心概念（按需使用，不需要时不加）",
        "用类比帮助理解并说明类比边界（按需使用）",
        "把科学内容与读者的行动或日常关联（按需使用）",
        "用设问或小标题引导阅读（按需使用）",
    ),
    prohibited=(
        _rule(
            "ps_paper_tone", "论文腔套话（本文将/综上所述）",
            r"本文将|综上所述|首先，我们",
        ),
        _rule(
            "ps_overclaim", "绝对化结论词（必定/毫无疑问地治愈等）",
            r"必定|毫无疑问地|绝对能|保证(?:治愈|解决)",
        ),
    ),
    human_responsibility="类比准确性由作者复核；涉及健康、安全、理财等高风险建议时作者须确认。",
)


_LECTURE_SCRIPT = GenreStyleProfile(
    genre=Genre.LECTURE_SCRIPT,
    display_name="课程讲稿",
    task_goal="在课堂场景下讲清知识，让听众能跟上并自己完成一次检查或练习。",
    risks=(
        "把理解检查写成自问自答装饰句",
        "练习与学习目标不一致",
    ),
    optional_devices=(
        "开场给出学习目标或预告（按需使用）",
        "提醒先备知识（按需使用）",
        "举例讲解（按需使用）",
        "在关键处设置理解检查或练习停顿（按需使用）",
    ),
    prohibited=(
        _rule(
            "ls_no_question_answer", "理解检查写成自问自答装饰句",
            r"提问：[^。]{1,12}。答案：",
        ),
    ),
    human_responsibility="学习目标与检查点须与真实教学计划一致；高风险主题（实验操作、医疗）步骤须教师确认。",
)


_RESEARCH_REPORT = GenreStyleProfile(
    genre=Genre.RESEARCH_REPORT,
    display_name="科研汇报",
    task_goal="让同行看清做了什么、看到什么、能说明什么、还不能说明什么。",
    risks=(
        "隐藏负面结果",
        "过度修辞（惊人/重大突破）",
        "把初步结果说成定论",
    ),
    optional_devices=(
        "先交代研究问题与背景（按需使用）",
        "用数据或结果说明观察（按需使用）",
        "说明方法与分析（按需使用）",
        "交代局限与下一步（按需使用）",
    ),
    prohibited=(
        _rule(
            "rr_positive_only", "隐藏负面结果（结果部分无任何转折）",
            r"全部为正|毫无异常",
        ),
        _rule(
            "rr_hyperbole", "过度修辞（惊人/重大突破）",
            r"惊人|重大突破|石破天惊|颠覆性",
        ),
    ),
    human_responsibility="数据真实性由汇报者负责；未发表数据的使用须经数据所有者确认。",
)


_PAPER_ASSIST = GenreStyleProfile(
    genre=Genre.PAPER_ASSIST,
    display_name="论文写作",
    task_goal="辅助作者完成结构、语言、引用与论证建议，帮助论文达到投稿标准。",
    risks=(
        "编造已完成实验或数据",
        "建议与期刊要求脱节",
    ),
    optional_devices=(
        "给出章节级结构建议（按需使用）",
        "给出具体语言建议（按需使用）",
        "核查引用完整性（按需使用）",
        "给出论证与证据强度建议（按需使用）",
        "提醒 AI 使用披露（按需使用）",
    ),
    prohibited=(
        _rule(
            "pa_fabricate", "编造已完成实验（建议补充必须标注未实施）",
            r"我们完成了.{0,30}实验并得到",
        ),
    ),
    human_responsibility=(
        "作者对全文内容、数据准确性与引用完整性负全部责任；"
        "AI 工具是辅助者而非作者。"
    ),
)


_GENERIC_PROFILE = GenreStyleProfile(
    genre=None,
    display_name="通用文章",
    task_goal="按任务需要组织文章，完成用户要求的写作目标；材料支持到哪里就写到哪里。",
    risks=(),
    optional_devices=(),
    human_responsibility="未识别体裁时按通用文章 profile 处理，不强制科普定义、类比或边界提醒。",
)

_GENRE_PROFILES: dict[Genre, GenreStyleProfile] = {
    Genre.POPULAR_SCIENCE: _POPULAR_SCIENCE,
    Genre.LECTURE_SCRIPT: _LECTURE_SCRIPT,
    Genre.RESEARCH_REPORT: _RESEARCH_REPORT,
    Genre.PAPER_ASSIST: _PAPER_ASSIST,
}

_GENRE_FILE: dict[Genre, str] = {
    Genre.POPULAR_SCIENCE: "popular_science.md",
    Genre.LECTURE_SCRIPT: "lecture_script.md",
    Genre.RESEARCH_REPORT: "research_report.md",
    Genre.PAPER_ASSIST: "paper_assist.md",
}


def check_genre(text: str, genre: Genre | None) -> GenreCheckResult:
    """对一段文本执行体裁复核：只判断任务完成与禁止模式，不搜索必现套话。

    ``genre`` 为 None（通用文章 profile）时只报告一条完成性说明。
    正文非空且无禁止模式即通过——Issue 04 起不再有「必现元素」概念。
    """
    if genre is None:
        return GenreCheckResult(
            genre=None,
            passed=bool(text.strip()),
            findings=[
                GenreCheckFinding(
                    rule_id="generic_completion",
                    label="通用文章 profile",
                    kind="completion",
                    passed=bool(text.strip()),
                    detail="正文已交付，不强制任何必现元素" if text.strip() else "缺少正文",
                )
            ],
        )
    profile = _GENRE_PROFILES[genre]
    findings: list[GenreCheckFinding] = []
    for rule in profile.prohibited:
        found = rule.pattern.search(text) is not None
        findings.append(
            GenreCheckFinding(
                rule_id=rule.rule_id,
                label=rule.label,
                kind="prohibited",
                passed=not found,
                detail="存在禁止模式" if found else "未出现",
            )
        )
    passed = bool(text.strip()) and all(f.passed for f in findings)
    return GenreCheckResult(genre=genre, passed=passed, findings=findings)


def genre_rule_set(genre: Genre | None) -> GenreStyleProfile:
    """读取单个体裁的表达 profile（含资产文档）。

    ``None`` 返回通用文章 profile：无强制元素、无禁止模式。
    """
    if genre is None:
        return _GENERIC_PROFILE
    return _GENRE_PROFILES[genre]


@lru_cache(maxsize=1)
def skill_doc(genre: Genre) -> str:
    """从 SKILL 资产读取体裁合同原文（与规则定义交叉校验）。"""
    path = _GENRE_DIR / _GENRE_FILE[genre]
    if not path.exists():
        raise GenreRuleError(f"体裁合同资产缺失：{path}")
    return path.read_text(encoding="utf-8")


def all_genres() -> list[Genre]:
    """四类体裁清单（各自独立 profile，不共用泛化模板）。"""
    return list(_GENRE_PROFILES.keys())


__all__ = [
    "GenreRule",
    "GenreStyleProfile",
    "GenreCheckFinding",
    "GenreCheckResult",
    "check_genre",
    "genre_rule_set",
    "skill_doc",
    "all_genres",
    "GenreRuleError",
]
