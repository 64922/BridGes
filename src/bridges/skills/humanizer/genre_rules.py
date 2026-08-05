"""四体裁表达规则加载与确定性校验（Issue 28）。

每类体裁从 ``skill/genres/*.md`` 加载独立的表达合同（必含/禁止/保留/人工
责任），并映射为可测试的断言：required 标记缺失 → 复核不通过；prohibited
标记出现 → 复核不通过。四类体裁规则集各自独立，不共用单一泛化模板。
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
    """一条可测试的表达规则断言（required/prohibited 由所在元组决定）。"""

    rule_id: str
    label: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class GenreRuleSet:
    """单个体裁的完整规则集（与 SKILL 资产一一对应）。"""

    genre: Genre
    display_name: str
    required: tuple[GenreRule, ...] = ()
    prohibited: tuple[GenreRule, ...] = ()
    human_responsibility: str = ""


@dataclass
class GenreCheckFinding:
    """单条规则检查结果。"""

    rule_id: str
    label: str
    kind: str  # required | prohibited
    passed: bool
    detail: str


@dataclass
class GenreCheckResult:
    """一次体裁规则复核的结果。"""

    genre: Genre
    passed: bool
    findings: list[GenreCheckFinding] = field(default_factory=list)

    def summary(self) -> list[str]:
        """面向用户的中文摘要。"""
        return [f"{'✓' if f.passed else '✗'} {f.label}：{f.detail}" for f in self.findings]


# ---------------------------------------------------------------------------
# 规则定义（与 skill/genres/*.md 一一对应，可测试断言）
# ---------------------------------------------------------------------------

def _rule(rule_id: str, label: str, pattern: str) -> GenreRule:
    return GenreRule(rule_id, label, re.compile(pattern))


_POPULAR_SCIENCE = GenreRuleSet(
    genre=Genre.POPULAR_SCIENCE,
    display_name="科普文案",
    required=(
        _rule(
            "ps_core_concept",
            "核心概念的一句话定义",
            r"所谓|指的是|简单说就是|可以理解为|本质上是",
        ),
        _rule(
            "ps_analogy",
            "至少一个类比（好比/就像/比作）",
            r"好比|就像|如同|仿佛|比作|可以比作",
        ),
        _rule(
            "ps_analogy_boundary",
            "类比的边界说明（类比不能无限延伸）",
            r"但|不过|需要注意的是|类比到此为止|只能说明|并不等于|不能理解为|并不意味着",
        ),
        _rule(
            "ps_action_relevance",
            "与读者生活相关的行动或关联",
            r"你可以|对你来说|对你说来|生活中|例如|比如|实际上|下次|当你",
        ),
    ),
    prohibited=(
        _rule(
            "ps_paper_tone", "论文腔套话（本文将/综上所述）",
            r"本文将|综上所述|首先，我们"
        ),
        _rule(
            "ps_overclaim", "绝对化结论词（必定/毫无疑问地治愈等）",
            r"必定|毫无疑问地|绝对能|保证(?:治愈|解决)"
        ),
    ),
    human_responsibility="类比准确性由作者复核；涉及健康、安全、理财等高风险建议时作者须确认。",
)


_LECTURE_SCRIPT = GenreRuleSet(
    genre=Genre.LECTURE_SCRIPT,
    display_name="课程讲稿",
    required=(
        _rule(
            "ls_learning_objective",
            "可观察的学习目标",
            r"学完本节，你将能|本讲目标|学习目标|你能做到|学完后你可以",
        ),
        _rule(
            "ls_prerequisite",
            "先备知识提醒",
            r"你需要先知道|前提是|如果你还不熟悉|先备|如果之前没接触过",
        ),
        _rule(
            "ls_example",
            "讲解中的举例（比如/举个例子）",
            r"比如|举个例子|例如|以……为例",
        ),
        _rule(
            "ls_comprehension_check",
            "至少一处理解检查（提问句式）",
            r"检查一下|想想看|你能说出|提问|来，问大家|试着回答",
        ),
        _rule(
            "ls_practice_pause",
            "至少一处练习停顿建议",
            r"停一下|花\s?1\s?分钟|花一分钟|尝试做|请同学们|现在练习",
        ),
    ),
    prohibited=(
        _rule(
            "ls_no_question_answer", "理解检查写成自问自答装饰句",
            r"提问：[^。]{1,12}。答案："
        ),
    ),
    human_responsibility="学习目标与检查点须与真实教学计划一致；高风险主题（实验操作、医疗）步骤须教师确认。",
)


_RESEARCH_REPORT = GenreRuleSet(
    genre=Genre.RESEARCH_REPORT,
    display_name="科研汇报",
    required=(
        _rule("rr_topic", "研究问题或汇报主题", r"本报告|本次汇报|研究问题|围绕|汇报主题"),
        _rule(
            "rr_observation", "观察句（数据/结果显示）",
            r"数据显示|结果表明|结果显示|观察到|实验发现"
        ),
        _rule(
            "rr_method", "分析方法说明",
            r"采用|使用|通过[^。]{0,20}?方法|统计|实验设计|采集"
        ),
        _rule("rr_limitation", "局限性说明", r"局限|限制|需要说明的是|不足|受限于"),
        _rule("rr_next_step", "下一步计划", r"下一步|接下来|后续计划|未来工作|后续将"),
    ),
    prohibited=(
        _rule(
            "rr_positive_only", "隐藏负面结果（结果部分无任何转折）",
            r"全部为正|毫无异常"
        ),
        _rule(
            "rr_hyperbole", "过度修辞（惊人/重大突破）",
            r"惊人|重大突破|石破天惊|颠覆性"
        ),
    ),
    human_responsibility="数据真实性由汇报者负责；未发表数据的使用须经数据所有者确认。",
)


_PAPER_ASSIST = GenreRuleSet(
    genre=Genre.PAPER_ASSIST,
    display_name="论文写作",
    required=(
        _rule("pa_structure", "结构建议（章节级）", r"摘要|引言|方法|结果|讨论|章节|结构"),
        _rule("pa_language", "具体语言建议", r"句式|术语|表达|措辞|长句|主语|语言"),
        _rule("pa_citation", "引用核查条目", r"引用|参考文献|文献核查|cite|DOI"),
        _rule("pa_argument", "论证建议", r"论证|逻辑|证据强度|因果|推理"),
        _rule("pa_disclosure", "AI 使用披露提醒", r"披露|声明|AI 辅助|人工智能辅助|使用说明"),
    ),
    prohibited=(
        _rule(
            "pa_fabricate", "编造已完成实验（建议补充必须标注未实施）",
            r"我们完成了.{0,30}实验并得到"
        ),
    ),
    human_responsibility=(
        "作者对全文内容、数据准确性与引用完整性负全部责任；"
        "AI 工具是辅助者而非作者。"
    ),
)

_GENRE_RULES: dict[Genre, GenreRuleSet] = {
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


def check_genre(text: str, genre: Genre) -> GenreCheckResult:
    """对一段文本执行指定体裁的确定性规则复核。"""
    rules = _GENRE_RULES[genre]
    findings: list[GenreCheckFinding] = []
    for rule in rules.required:
        found = rule.pattern.search(text) is not None
        findings.append(
            GenreCheckFinding(
                rule_id=rule.rule_id,
                label=rule.label,
                kind="required",
                passed=found,
                detail="已出现" if found else "缺失",
            )
        )
    for rule in rules.prohibited:
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
    passed = all(f.passed for f in findings)
    return GenreCheckResult(genre=genre, passed=passed, findings=findings)


def genre_rule_set(genre: Genre) -> GenreRuleSet:
    """读取单个体裁的完整规则集（含资产文档）。"""
    return _GENRE_RULES[genre]


@lru_cache(maxsize=1)
def skill_doc(genre: Genre) -> str:
    """从 SKILL 资产读取体裁合同原文（与规则定义交叉校验）。"""
    path = _GENRE_DIR / _GENRE_FILE[genre]
    if not path.exists():
        raise GenreRuleError(f"体裁合同资产缺失：{path}")
    return path.read_text(encoding="utf-8")


def all_genres() -> list[Genre]:
    """四类体裁清单（保证各自独立规则，不共用泛化模板）。"""
    return list(_GENRE_RULES.keys())


__all__ = [
    "GenreRule",
    "GenreRuleSet",
    "GenreCheckFinding",
    "GenreCheckResult",
    "check_genre",
    "genre_rule_set",
    "skill_doc",
    "all_genres",
    "GenreRuleError",
]
