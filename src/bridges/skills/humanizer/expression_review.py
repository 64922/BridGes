"""表达审稿器（人味化改造 Issue 04）。

首稿生成后对候选正文做确定性软审稿：定位模板动作、PPT 式抽象包装、假
具体、假口语、强行场景、节奏单一和无必要升华，输出带稳定 code、位置、
证据与定向建议的 ``ExpressionReviewReport``。审稿只检查作者新增正文：
精确引语、代码、公式、URL、合法术语、用户指定措辞与原文已有表达跳过；
单个词语、冒号或破折号不独立造成任何发现。来源保真问题由 Issue 02
硬门独立裁决，本模块不产生硬失败。
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field

from bridges.contracts.expression import Genre
from bridges.contracts.expression_review import (
    EXPRESSION_REVIEW_VERSION,
    ExpressionReviewFinding,
    ExpressionReviewReport,
    ExpressionReviewSummary,
    ReviewCode,
    ReviewSeverity,
)
from bridges.contracts.expression_task import ExpressionTaskContract
from bridges.contracts.humanizer import SourceLedger, SpanLocation

# ---------------------------------------------------------------------------
# 词表与模式（原创净室：只描述模板动作本身，不做全局禁词）
# ---------------------------------------------------------------------------

#: 助手身份残留：把工具自我陈述写成内容主角。
_ASSISTANT_IDENTITY_RE = re.compile(
    r"本助手|本模型|作为(?:一个)?\s*AI|作为人工智能|我是(?:一个)?\s*AI(?:助手)?|"
    r"AI\s*(?:助手)?(?:认为|建议|提示|指出)|我作为(?:你的)?助手|我来为您"
)

#: 机械承接与收尾：无信息增量的过渡词和协作式尾句。
_CLOSING_RESIDUE_RE = re.compile(
    r"总而言之|总的来说|归根结底|希望(?:这些|以上|我的)?(?:建议|方法|分享|内容)?"
    r"(?:对)?(?:你)?(?:能)?(?:有)?(?:所)?(?:帮助|启发|有用)|如有(?:任何)?(?:问题|需要)"
    r"(?:请|欢迎|随时)|如果需要进一步|让我为您|祝您|随时为您"
)
#: 科研/论文场景的常规收尾与综述句（不报，避免误伤体裁常规表达）。
_CLOSING_SCENE_ALLOW = re.compile(r"综上所述|归根结底")

#: 体裁脚手架：固定序号与教学停顿标记的装饰性堆叠。
_SCAFFOLDING_RE = re.compile(
    r"首先，|其次，|再次，|最后，|第一，|第二，|第三，|"
    r"本讲目标|学习目标|请同学们|练习停顿|理解检查|让我们来(?:练习|思考)"
)

#: 抽象名词链：用抽象包装代替动作与事实的词（按场景解释，非全局禁词）。
_ABSTRACT_WORDS = (
    "颗粒度",
    "带宽",
    "底层逻辑",
    "抓手",
    "元认知",
    "认知负荷",
    "心智模型",
    "范式",
    "维度",
    "闭环",
    "方法论",
    "框架思维",
    "释放内存",
    "大脑内存",
    "降维",
    "打法",
)

#: 商业/PPT 隐喻：广告式比喻与承诺词。
_BUSINESS_METAPHOR_WORDS = (
    "赋能",
    "闭环",
    "打通",
    "生态化",
    "打造",
    "护城河",
    "弯道超车",
    "降维打击",
    "价值主张",
    "里程碑式",
    "全面升级",
    "引爆",
    "抓手",
)

#: 用术语解释术语的句式标记。
_TERM_EXPLAIN_RE = re.compile(
    r"所谓(?P<a>[^，。！？；]{1,20})，(?:就是|即|本质上是)(?P<b>[^，。！？；]{1,20})|"
    r"(?P<c>[^，。！？；]{1,20})(?:的)?(?:就是|即是|本质上)(?:一种)?"
    r"(?P<d>[^，。！？；]{1,12})(?:的)?(?:化|思维|过程|机制)"
)
#: 术语解释的嵌套结构（X 的 X）与抽象词命中。
_X_OF_X_RE = re.compile(r"[一-鿿]{2,6}(?:化|化思维)?的[一-鿿]{2,6}(?:化|思维|过程|机制)")

#: 同义循环：换词复述同一个判断的标记。
_SYNONYM_LOOP_RE = re.compile(r"换言之|也就是说|换句话说|简言之|换一种说法|换言之说")

#: 空段：纯转折/承接词构成的段落（无信息增量）。
_EMPTY_PARAGRAPH_RE = re.compile(
    r"^(?:然而|但是|不过|因此|所以|总之|此外|同时|最后|首先)[。，、]{0,2}$"
)

#: 假具体：没有来源的具体化表述。
_FAKE_CONCRETE_RE = re.compile(
    r"有人问我|很多朋友|不少同学|不少读者|身边(?:的)?朋友|大家都说|有人说|"
    r"经常有人|许多人都(?:说|认为)|很多人都(?:有|说|觉得|遇到过|问过)|"
    r"据我了解(?!来源)|众所周知(?!的)"
)

#: 无权限第一人称亲历（当下判断「我认为」不算亲历，不在模式内）。
_FIRST_PERSON_EXPERIENCE_RE = re.compile(
    r"我(?:去年|上个月|上周|那天|当时|曾经|记得|亲自|亲历|经历过|做过|"
    r"坚持了|试过|用过|体验过|去过|见过|感受到)"
)

#: 强行生活场景：装饰性时间与场景词（无信息功能）。
_LIFE_SCENE_WORDS = (
    "深夜",
    "凌晨",
    "午夜",
    "咖啡",
    "雨天",
    "雨夜",
    "窗外的雨",
    "周末午后",
    "夕阳下",
    "华灯初上",
)

#: 每段金句：短判断句构成的装饰性段落。
_MOTIVATION_SEG_LENGTH = 12

#: 通用乐观升华：无依据的向上总结。
_OPTIMISTIC_ENDING_RE = re.compile(
    r"让我们(?:共同)?期待|未来可期|值得期待|更加美好|美好的明天|必将更加|"
    r"共创辉煌|携手走向|驶向(?:更)?好|开启新(?:的)?篇章"
)

#: 修辞密度标记：问号句、冒号、破折号。
_QUESTION_SENT_RE = re.compile(r"？$|\?$")
_COLON_RE = re.compile(r"：|:")
_DASH_RE = re.compile(r"——|—|–")

#: 具体性证据：数字或可追踪动作动词，用于判断抽象词是准确指代还是包装。
_CONCRETE_EVIDENCE_RE = re.compile(
    r"\d|测量|比较|统计|观察到|使用|耗时|完成|记录|拆分|拆解|计算|验证|"
    r"对比|排序|标注|测试|汇总|核对|换算|预计|分配|花费|节省|增加|减少|"
    r"列出|划掉|回顾|复盘|设(?:定|置)截止|按照|步骤|分钟|小时|次数|百分比"
)


@dataclass(frozen=True)
class _SceneConfig:
    """场景 profile 的审稿配置：禁用与场景冲突的发现 code。"""

    scene: str
    disabled: frozenset[ReviewCode] = frozenset()
    #: 场景收尾词白名单（科研/论文的「综上所述」等常规表达）。
    closing_allowed: re.Pattern[str] | None = None
    #: 场景是否把设问视为正常表达手段（讲稿的理解检查设问）。
    allow_questions: bool = False


def _scene_config(genre: Genre | None) -> _SceneConfig:
    if genre == Genre.RESEARCH_REPORT or genre == Genre.PAPER_ASSIST:
        return _SceneConfig(
            scene="research_or_paper",
            disabled=frozenset({ReviewCode.GENRE_SCAFFOLDING}),
            closing_allowed=_CLOSING_SCENE_ALLOW,
        )
    if genre == Genre.LECTURE_SCRIPT:
        return _SceneConfig(
            scene="lecture",
            disabled=frozenset({ReviewCode.GENRE_SCAFFOLDING}),
            allow_questions=True,
        )
    return _SceneConfig(scene="generic")


# ---------------------------------------------------------------------------
# 上下文与保护
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ReviewContext:
    """一次审稿的不可变上下文：候选正文、契约、账本与预计算的保护区域。"""

    candidate: str
    contract: ExpressionTaskContract
    source_text: str
    ledger: SourceLedger | None
    scene: str
    config: _SceneConfig
    user_phrases: frozenset[str] = field(default_factory=frozenset)
    ledger_terms: frozenset[str] = field(default_factory=frozenset)
    quote_regions: list[SpanLocation] = field(default_factory=list)
    sentences: list[tuple[str, SpanLocation]] = field(default_factory=list)
    paragraphs: list[tuple[str, SpanLocation]] = field(default_factory=list)

    def is_protected(self, text: str, start: int, end: int) -> bool:
        """命中区域是否受保护：引号内、原文已有、账本术语或用户指定措辞。

        单个词或短语在原文中已是合法表达时跳过（按场景解释词语），
        不因词表命中独立报警。
        """
        for region in self.quote_regions:
            if start < region.end and end > region.start:
                return True
        stripped = text.strip()
        if not stripped:
            return True
        if stripped in self.user_phrases:
            return True
        if self.source_text and stripped in self.source_text:
            return True
        return any(term and term in stripped for term in self.ledger_terms)


def _extract_user_phrases(constraints: Iterable[str]) -> frozenset[str]:
    """从用户硬约束提取指定措辞（引号内或「必须保留」短语）。"""
    phrases: set[str] = set()
    for constraint in constraints:
        for match in re.finditer(
            r"[「“『\"]([^「」””『』\"']{2,40})[」”』\"]",
            constraint,
        ):
            phrase = match.group(1).strip()
            if phrase:
                phrases.add(phrase)
        for match in re.finditer(
            r"必须(?:保留|写清|使用|体现|写出)[：:，,]?\s*([一-鿿A-Za-z0-9]{2,30})",
            constraint,
        ):
            phrase = match.group(1).strip()
            if phrase:
                phrases.add(phrase)
    return frozenset(phrases)


def _ledger_terms(ledger: SourceLedger | None) -> frozenset[str]:
    """账本中可作合法术语引用的规范化键（专名/引语/代码/公式/URL/引用）。"""
    if ledger is None:
        return frozenset()
    terms: set[str] = set()
    for entry in ledger.entries:
        terms.update(entry.proper_nouns)
        terms.update(entry.quotes)
        terms.update(entry.codes)
        terms.update(entry.formulas)
        terms.update(entry.urls)
        terms.update(entry.citations)
    return frozenset(t for t in terms if t)


def _quote_regions(text: str) -> list[SpanLocation]:
    """精确引语区域：引号内的内容整体跳过审稿。"""
    regions: list[SpanLocation] = []
    for match in re.finditer(r"[「“『\"][^「」“”『』\"]{2,}[」”』\"]", text):
        regions.append(SpanLocation(start=match.start(), end=match.end()))
    return regions


def _sentences(text: str) -> list[tuple[str, SpanLocation]]:
    """按句末标点与换行切句，返回（文本, 位置）。"""
    result: list[tuple[str, SpanLocation]] = []
    start = 0
    for match in re.finditer(r"[。！？；\n]+", text):
        end = match.end()
        content = text[start:end].strip()
        if content:
            result.append((content, SpanLocation(start=start, end=end)))
        start = end
    tail = text[start:].strip()
    if tail:
        result.append((tail, SpanLocation(start=start, end=len(text))))
    return result


def _paragraphs(text: str) -> list[tuple[str, SpanLocation]]:
    """按换行切段，返回（文本, 位置）。"""
    result: list[tuple[str, SpanLocation]] = []
    start = 0
    for match in re.finditer(r"\n+", text):
        content = text[start : match.start()].strip()
        if content:
            result.append((content, SpanLocation(start=start, end=match.start())))
        start = match.end()
    tail = text[start:].strip()
    if tail:
        result.append((tail, SpanLocation(start=start, end=len(text))))
    return result


def _build_context(
    candidate: str,
    *,
    contract: ExpressionTaskContract,
    source_text: str,
    ledger: SourceLedger | None,
) -> _ReviewContext:
    genre = contract.genre
    config = _scene_config(genre)
    scene = f"{(genre.value if genre else 'generic')}:{contract.rewrite_intensity.value}"
    return _ReviewContext(
        candidate=candidate,
        contract=contract,
        source_text=source_text,
        ledger=ledger,
        scene=scene,
        config=config,
        user_phrases=_extract_user_phrases(contract.user_constraints),
        ledger_terms=_ledger_terms(ledger),
        quote_regions=_quote_regions(candidate),
        sentences=_sentences(candidate),
        paragraphs=_paragraphs(candidate),
    )


def _finding(
    ctx: _ReviewContext,
    code: ReviewCode,
    severity: ReviewSeverity,
    start: int,
    end: int,
    evidence: str,
    explanation: str,
    suggestion: str,
) -> ExpressionReviewFinding:
    # 确定性发现标识：同一输入每次审稿产生相同 ID，供审计与 Issue 05
    # 跨运行锚定发现（修订输入以发现为准，不依赖随机值）。
    return ExpressionReviewFinding(
        finding_id=f"{code.value}-{start}-{end}",
        code=code,
        severity=severity,
        category=_CATEGORY_CN[code],
        location=SpanLocation(start=start, end=end),
        evidence=evidence[:80],
        explanation=explanation,
        suggestion=suggestion,
        scene_profile=ctx.scene,
    )


_CATEGORY_CN: dict[ReviewCode, str] = {
    ReviewCode.ASSISTANT_IDENTITY_RESIDUE: "助手身份残留",
    ReviewCode.MECHANICAL_TRANSITION_CLOSING: "机械承接/收尾",
    ReviewCode.GENRE_SCAFFOLDING: "体裁脚手架",
    ReviewCode.ABSTRACT_NOUN_CHAIN: "抽象名词链",
    ReviewCode.BUSINESS_PPT_METAPHOR: "商业/PPT 隐喻",
    ReviewCode.TERM_EXPLAINS_TERM: "用术语解释术语",
    ReviewCode.SYNONYM_LOOP: "同义循环",
    ReviewCode.EMPTY_PARAGRAPH: "空段",
    ReviewCode.REPEATED_OPENING: "重复开场",
    ReviewCode.UNIFORM_LENGTH: "句长/段长过齐",
    ReviewCode.DENSE_RHETORIC: "密集排比或设问",
    ReviewCode.FAKE_CONCRETENESS: "假具体",
    ReviewCode.UNAUTHORIZED_FIRST_PERSON: "无权限第一人称",
    ReviewCode.FORCED_LIFE_SCENE: "强行生活场景",
    ReviewCode.PER_PARAGRAPH_MOTIVATION: "每段金句",
    ReviewCode.GENERIC_OPTIMISTIC_ENDING: "通用乐观升华",
}


# ---------------------------------------------------------------------------
# 各发现检查器：全部只做软审稿（INFO/SUGGESTION/WARNING）
# ---------------------------------------------------------------------------

Checker = callable  # noqa: A001 - 类型别名，避免与内置冲突


def _check_assistant_identity(ctx: _ReviewContext) -> list[ExpressionReviewFinding]:
    findings: list[ExpressionReviewFinding] = []
    for match in _ASSISTANT_IDENTITY_RE.finditer(ctx.candidate):
        if ctx.is_protected(match.group(0), match.start(), match.end()):
            continue
        findings.append(
            _finding(
                ctx,
                ReviewCode.ASSISTANT_IDENTITY_RESIDUE,
                ReviewSeverity.WARNING,
                match.start(),
                match.end(),
                match.group(0),
                "把工具身份写成内容主角，读者会听到「助手在自我介绍」而不是作者的判断。",
                "删除身份自称，直接用作者口吻陈述判断与依据。",
            )
        )
    return findings


def _check_mechanical_closing(ctx: _ReviewContext) -> list[ExpressionReviewFinding]:
    findings: list[ExpressionReviewFinding] = []
    for match in _CLOSING_RESIDUE_RE.finditer(ctx.candidate):
        if ctx.config.closing_allowed and ctx.config.closing_allowed.search(match.group(0)):
            continue
        if ctx.is_protected(match.group(0), match.start(), match.end()):
            continue
        findings.append(
            _finding(
                ctx,
                ReviewCode.MECHANICAL_TRANSITION_CLOSING,
                ReviewSeverity.WARNING,
                match.start(),
                match.end(),
                match.group(0),
                "收尾只复述「已经说完」或客套邀请，没有为读者提供新信息或真实下一步。",
                "删掉装饰性收尾；有真实下一步时直接写出下一步的动作与条件。",
            )
        )
    return findings


def _check_genre_scaffolding(ctx: _ReviewContext) -> list[ExpressionReviewFinding]:
    # 科研/论文/讲稿场景在场景配置中禁用本检查器（教学与论述骨架是常规表达）
    hits = list(_SCAFFOLDING_RE.finditer(ctx.candidate))
    if len(hits) < 3:
        return []
    first = hits[0]
    evidence = "".join(m.group(0).strip() for m in hits[:4])
    return [
        _finding(
            ctx,
            ReviewCode.GENRE_SCAFFOLDING,
            ReviewSeverity.SUGGESTION,
            first.start(),
            hits[-1].end(),
            evidence,
            "固定序号与教学标记成段堆叠，结构像模板而不是材料推进。",
            "按内容推进组织段落：每段先回答读者上一段留下的问题，序号只在承担真实顺序时保留。",
        )
    ]


def _is_abstract_wrap(ctx: _ReviewContext, sentence: str) -> bool:
    """抽象词所在句是否缺少具体性证据（数字/可追踪动作/账本术语）。"""
    if _CONCRETE_EVIDENCE_RE.search(sentence):
        return False
    return not any(term and term in sentence for term in ctx.ledger_terms)


def _scan_word_bag(
    ctx: _ReviewContext,
    code: ReviewCode,
    words: tuple[str, ...],
    explanation: str,
    suggestion: str,
) -> list[ExpressionReviewFinding]:
    """扫描句子中的抽象包装词表：只报缺少具体性证据的命中，每句至多一条。

    抽象名词链与商业/PPT 隐喻共用同一判定骨架（句循环→词查找→保护区
    跳过→具体性证据判断），区别只在词表、code 与解释文案。
    """
    findings: list[ExpressionReviewFinding] = []
    for sentence, loc in ctx.sentences:
        for word in words:
            start = sentence.find(word)
            if start < 0:
                continue
            abs_start = loc.start + start
            if ctx.is_protected(word, abs_start, abs_start + len(word)):
                continue
            if _is_abstract_wrap(ctx, sentence):
                findings.append(
                    _finding(
                        ctx,
                        code,
                        ReviewSeverity.SUGGESTION,
                        abs_start,
                        abs_start + len(word),
                        word,
                        explanation,
                        suggestion,
                    )
                )
                break
    return findings[:5]


def _check_abstract_chain(ctx: _ReviewContext) -> list[ExpressionReviewFinding]:
    return _scan_word_bag(
        ctx,
        ReviewCode.ABSTRACT_NOUN_CHAIN,
        _ABSTRACT_WORDS,
        "抽象名词代替了动作与事实；读者只能感到「在讲概念」，不知道发生了什么。",
        "把抽象包装落到具体动作或已有材料：谁做了什么、测得什么、结果如何。",
    )


def _check_business_metaphor(ctx: _ReviewContext) -> list[ExpressionReviewFinding]:
    return _scan_word_bag(
        ctx,
        ReviewCode.BUSINESS_PPT_METAPHOR,
        _BUSINESS_METAPHOR_WORDS,
        "商业比喻承诺效果，但没有可观察的动作、对象与结果。",
        "换成具体行为与结果：做了什么、改变了哪个指标、边界是什么。",
    )


def _check_term_explains_term(ctx: _ReviewContext) -> list[ExpressionReviewFinding]:
    findings: list[ExpressionReviewFinding] = []
    for match in _TERM_EXPLAIN_RE.finditer(ctx.candidate):
        if ctx.is_protected(match.group(0), match.start(), match.end()):
            continue
        left = match.group("a") or match.group("c") or ""
        right = match.group("b") or match.group("d") or ""
        is_nested = bool(_X_OF_X_RE.search(left) or _X_OF_X_RE.search(right))
        has_abstract = any(
            word in left or word in right for word in (*_ABSTRACT_WORDS, *_BUSINESS_METAPHOR_WORDS)
        )
        if not (is_nested or has_abstract):
            continue
        findings.append(
            _finding(
                ctx,
                ReviewCode.TERM_EXPLAINS_TERM,
                ReviewSeverity.SUGGESTION,
                match.start(),
                match.end(),
                match.group(0),
                "用术语解释术语，读者如果不懂前者也不会懂后者。",
                "换成可操作的定义：这个概念解决什么问题、对应哪个动作或材料。",
            )
        )
    return findings[:3]


def _check_synonym_loop(ctx: _ReviewContext) -> list[ExpressionReviewFinding]:
    hits = list(_SYNONYM_LOOP_RE.finditer(ctx.candidate))
    if len(hits) < 2:
        return []
    return [
        _finding(
            ctx,
            ReviewCode.SYNONYM_LOOP,
            ReviewSeverity.SUGGESTION,
            hits[0].start(),
            hits[-1].end(),
            "".join(m.group(0) for m in hits),
            "换词复述同一个判断，段落没有增加新信息。",
            "保留第一个表达，把其余换成新的动作、区别或后果。",
        )
    ]


def _check_empty_paragraph(ctx: _ReviewContext) -> list[ExpressionReviewFinding]:
    findings: list[ExpressionReviewFinding] = []
    for text, loc in ctx.paragraphs:
        if len(text) <= 12 and _EMPTY_PARAGRAPH_RE.match(text):
            findings.append(
                _finding(
                    ctx,
                    ReviewCode.EMPTY_PARAGRAPH,
                    ReviewSeverity.INFO,
                    loc.start,
                    loc.end,
                    text,
                    "段落只有承接词，没有承载事实、动作或判断。",
                    "删除空段，或把承接内容并入相邻段落。",
                )
            )
    return findings


def _check_repeated_opening(ctx: _ReviewContext) -> list[ExpressionReviewFinding]:
    by_prefix: dict[str, SpanLocation] = {}
    for text, loc in ctx.paragraphs:
        prefix = text[:2]
        if not prefix:
            continue
        if prefix in by_prefix:
            return [
                _finding(
                    ctx,
                    ReviewCode.REPEATED_OPENING,
                    ReviewSeverity.SUGGESTION,
                    by_prefix[prefix].start,
                    loc.end,
                    prefix,
                    "多段以相同句式开场，节奏像模板而非推进。",
                    "让后段的开场承接前段的末尾，而不是重复同一个开场句式。",
                )
            ]
        by_prefix[prefix] = loc
    return []


def _check_uniform_length(ctx: _ReviewContext) -> list[ExpressionReviewFinding]:
    # 用去标点后的字长衡量节奏；阈值只捕捉明显等距的句子序列。
    lengths = [
        len(re.sub(r"[，。！？；：、\s]", "", text))
        for text, _ in ctx.sentences
        if len(text) >= 4
    ]
    if len(lengths) < 3:
        return []
    mean = statistics.mean(lengths)
    if mean <= 0:
        return []
    relative_spread = statistics.stdev(lengths) / mean if len(lengths) > 1 else 0.0
    if relative_spread < 0.12:
        first = ctx.sentences[0][1]
        last = ctx.sentences[-1][1]
        return [
            _finding(
                ctx,
                ReviewCode.UNIFORM_LENGTH,
                ReviewSeverity.INFO,
                first.start,
                last.end,
                ctx.candidate[:60],
                "句子长度高度一致，读起来像等距的条目而不是有重音的叙述。",
                "把关键判断写成短句，论证与条件交给长句，拉开节奏。",
            )
        ]
    return []


def _check_dense_rhetoric(ctx: _ReviewContext) -> list[ExpressionReviewFinding]:
    question_sentences = [t for t, _ in ctx.sentences if _QUESTION_SENT_RE.search(t)]
    total = len(ctx.sentences)
    question_ratio = len(question_sentences) / total if total else 0.0
    dense_questions = len(question_sentences) >= 3 and question_ratio >= 0.4

    colons = len(_COLON_RE.findall(ctx.candidate))
    dashes = len(_DASH_RE.findall(ctx.candidate))
    # 冒号/破折号只在成段密度下才报警：单个标点永不独立造成发现
    dense_punctuation = (colons + dashes) >= 5 and len(ctx.candidate) <= 600

    prefixes: dict[str, int] = {}
    for text, _ in ctx.sentences:
        if text:
            key = text[:2]
            prefixes[key] = prefixes.get(key, 0) + 1
    dense_parallel = any(count >= 3 for count in prefixes.values())

    if ctx.config.allow_questions:
        dense_questions = False
    if not (dense_questions or dense_punctuation or dense_parallel):
        return []
    return [
        _finding(
            ctx,
            ReviewCode.DENSE_RHETORIC,
            ReviewSeverity.SUGGESTION,
            0,
            len(ctx.candidate),
            ctx.candidate[:60],
            "设问、冒号/破折号或同构句成段密集，修辞节奏压过了信息推进。",
            "保留承担信息的一处修辞，其余改成直接陈述。",
        )
    ]


def _check_fake_concreteness(ctx: _ReviewContext) -> list[ExpressionReviewFinding]:
    findings: list[ExpressionReviewFinding] = []
    for match in _FAKE_CONCRETE_RE.finditer(ctx.candidate):
        if ctx.is_protected(match.group(0), match.start(), match.end()):
            continue
        findings.append(
            _finding(
                ctx,
                ReviewCode.FAKE_CONCRETENESS,
                ReviewSeverity.WARNING,
                match.start(),
                match.end(),
                match.group(0),
                "「很多人/朋友/大家」式具体化没有来源，像是为了显得接地气而虚构的读者反馈。",
                "如果素材来自材料，改写为「材料中提到」并保留来源；否则删掉无来源的具体化。",
            )
        )
    return findings


def _check_unauthorized_first_person(ctx: _ReviewContext) -> list[ExpressionReviewFinding]:
    if ctx.contract.first_person_permission:
        return []
    findings: list[ExpressionReviewFinding] = []
    for match in _FIRST_PERSON_EXPERIENCE_RE.finditer(ctx.candidate):
        if ctx.is_protected(match.group(0), match.start(), match.end()):
            continue
        findings.append(
            _finding(
                ctx,
                ReviewCode.UNAUTHORIZED_FIRST_PERSON,
                ReviewSeverity.WARNING,
                match.start(),
                match.end(),
                match.group(0),
                "没有授权却出现第一人称亲历（时间/事件/体验），会把虚构经历投影为作者事实。",
                "删除亲历描述；当下判断（我认为/在我看来）可直接陈述判断与依据。",
            )
        )
    return findings


def _check_forced_life_scene(ctx: _ReviewContext) -> list[ExpressionReviewFinding]:
    for text, loc in ctx.sentences:
        hits = [word for word in _LIFE_SCENE_WORDS if word in text]
        if len(hits) >= 2:
            if ctx.is_protected(text, loc.start, loc.end):
                continue
            return [
                _finding(
                    ctx,
                    ReviewCode.FORCED_LIFE_SCENE,
                    ReviewSeverity.SUGGESTION,
                    loc.start,
                    loc.end,
                    text,
                    "装饰性场景（深夜/咖啡/雨）堆叠出现但没有承载事实或动作。",
                    "删除装饰性场景；场景只有在提供真实信息（谁、何时、何地、为何）时保留。",
                )
            ]
    return []


def _check_per_paragraph_motivation(ctx: _ReviewContext) -> list[ExpressionReviewFinding]:
    short_paragraphs = [
        (text, loc)
        for text, loc in ctx.paragraphs
        if 2 <= len(text) <= _MOTIVATION_SEG_LENGTH
    ]
    if len(short_paragraphs) < 2:
        return []
    first = short_paragraphs[0]
    last = short_paragraphs[-1]
    return [
        _finding(
            ctx,
            ReviewCode.PER_PARAGRAPH_MOTIVATION,
            ReviewSeverity.SUGGESTION,
            first[1].start,
            last[1].end,
            "；".join(text for text, _ in short_paragraphs[:4]),
            "短判断句连续成段，每段都在「总结一句」而不是推进内容。",
            "把金句并入相邻段落的论证，让判断紧跟证据出现。",
        )
    ]


def _check_optimistic_ending(ctx: _ReviewContext) -> list[ExpressionReviewFinding]:
    findings: list[ExpressionReviewFinding] = []
    for match in _OPTIMISTIC_ENDING_RE.finditer(ctx.candidate):
        if ctx.is_protected(match.group(0), match.start(), match.end()):
            continue
        findings.append(
            _finding(
                ctx,
                ReviewCode.GENERIC_OPTIMISTIC_ENDING,
                ReviewSeverity.SUGGESTION,
                match.start(),
                match.end(),
                match.group(0),
                "无依据的向上总结与展望，是模板式结尾而不是材料支持的收束。",
                "删掉升华句；结尾应回到材料能支持的结论或给出真实的下一步。",
            )
        )
    return findings


_CHECKERS: dict[ReviewCode, Checker] = {
    ReviewCode.ASSISTANT_IDENTITY_RESIDUE: _check_assistant_identity,
    ReviewCode.MECHANICAL_TRANSITION_CLOSING: _check_mechanical_closing,
    ReviewCode.GENRE_SCAFFOLDING: _check_genre_scaffolding,
    ReviewCode.ABSTRACT_NOUN_CHAIN: _check_abstract_chain,
    ReviewCode.BUSINESS_PPT_METAPHOR: _check_business_metaphor,
    ReviewCode.TERM_EXPLAINS_TERM: _check_term_explains_term,
    ReviewCode.SYNONYM_LOOP: _check_synonym_loop,
    ReviewCode.EMPTY_PARAGRAPH: _check_empty_paragraph,
    ReviewCode.REPEATED_OPENING: _check_repeated_opening,
    ReviewCode.UNIFORM_LENGTH: _check_uniform_length,
    ReviewCode.DENSE_RHETORIC: _check_dense_rhetoric,
    ReviewCode.FAKE_CONCRETENESS: _check_fake_concreteness,
    ReviewCode.UNAUTHORIZED_FIRST_PERSON: _check_unauthorized_first_person,
    ReviewCode.FORCED_LIFE_SCENE: _check_forced_life_scene,
    ReviewCode.PER_PARAGRAPH_MOTIVATION: _check_per_paragraph_motivation,
    ReviewCode.GENERIC_OPTIMISTIC_ENDING: _check_optimistic_ending,
}


def run_expression_review(
    candidate: str,
    *,
    contract: ExpressionTaskContract,
    source_text: str = "",
    ledger: SourceLedger | None = None,
) -> ExpressionReviewReport:
    """对候选正文执行一次软审稿，返回版本化 ``ExpressionReviewReport``。

    - 只检查作者新增正文：原文已有表达、精确引语、账本术语与用户指定
      措辞跳过；单个词语/冒号/破折号不独立产生发现。
    - 全部发现为软审稿（INFO/SUGGESTION/WARNING），不阻止交付；来源
      保真问题由 Issue 02 硬门独立裁决。
    - 无高价值发现时 ``no_change_recommended`` 为 True，系统不为满足
      规则强行改写。
    """
    ctx = _build_context(
        candidate,
        contract=contract,
        source_text=source_text,
        ledger=ledger,
    )
    findings: list[ExpressionReviewFinding] = []
    for code, checker in _CHECKERS.items():
        if code in ctx.config.disabled:
            continue
        findings.extend(checker(ctx))
    findings.sort(key=lambda f: (f.location.start, f.location.end))

    by_code: dict[str, int] = {}
    warning = suggestion = info = 0
    for finding in findings:
        by_code[finding.code.value] = by_code.get(finding.code.value, 0) + 1
        if finding.severity == ReviewSeverity.WARNING:
            warning += 1
        elif finding.severity == ReviewSeverity.SUGGESTION:
            suggestion += 1
        else:
            info += 1
    # 自然稿或只有 info 级观察时视为无高价值修改建议
    high_value = warning + suggestion
    no_change = not findings or high_value == 0
    return ExpressionReviewReport(
        review_version=EXPRESSION_REVIEW_VERSION,
        contract_hash=contract.version_hash,
        scene_profile=ctx.scene,
        findings=findings,
        no_change_recommended=no_change,
        summary=ExpressionReviewSummary(
            finding_count=len(findings),
            by_code=by_code,
            warning_count=warning,
            suggestion_count=suggestion,
            info_count=info,
            no_change_recommended=no_change,
        ),
    )


__all__ = [
    "run_expression_review",
    "_extract_user_phrases",
]
