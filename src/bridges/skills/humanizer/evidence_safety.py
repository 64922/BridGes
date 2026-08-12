"""证据安全修订模式（人味化改造 Issue 06）。

默认普通人味化只改善表达并提示证据风险，不静默把「证明」改成「支持」、
把因果改成相关或补入原文没有的限制。本模块是文章 profile 上的证据安全
叠加层：确定性识别 claim 类型与当前强度（观察/相关/预测/比较/机制/因果/
显著性/泛化/真实场景有效性），默认模式只生成独立风险项；只有契约进入
``EvidenceRevisionMode.EVIDENCE_SAFE`` 时，才允许按来源账本降级结论措辞，
并为每项实质变化保存可重建的前后 span、变化类型、来源条目与理由。

全部逻辑确定性、可测试，不调用模型；修订提示编译与修订后 diff 都由
程序重建，模型自述不能代替差异证据。本模块不复制任何无许可参考项目的
文本、结构或示例。
"""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass

from bridges.contracts.evidence_safety import (
    ClaimStrengthKind,
    ClaimStrengthTier,
    EvidenceClaim,
    EvidenceRevisionChange,
    EvidenceRevisionStatus,
    EvidenceRiskCode,
    EvidenceRiskItem,
    EvidenceSafeReport,
    EvidenceSafeSummary,
    RevisionChangeType,
)
from bridges.contracts.expression_task import ExpressionTaskContract
from bridges.contracts.humanizer import SourceLedger, SpanLocation
from bridges.skills.humanizer.factlock import _STRENGTH_TIER

#: 证据安全报告 Schema 版本。
EVIDENCE_SAFETY_VERSION = "evidence-safety-v1"

# ---------------------------------------------------------------------------
# 原创 claim 类型标记词（句子级识别；只做标记识别，不做统计推断）
# ---------------------------------------------------------------------------

_OBSERVATION_RE = re.compile(
    r"观察(?:到|了|的|性)?|结果(?:显示|证明)|数据表明|结果表明|"
    r"我们(?:测量|记录|跟踪|采集|统计|比较|监测)(?:了|到)?|测得|记录到|"
    r"样本量|收集了?|招募了?|覆盖了?|平均|中位数|实验中|实验中我们发现|"
    r"实验组|对照组"
)
_CORRELATION_RE = re.compile(
    r"与[^，。；！？\n]{0,16}(?:相关|有关|关联)|呈[^，。；！？\n]{0,8}相关|"
    r"相关系数|相关性|关联性|正相关|负相关|correlation",
    re.IGNORECASE,
)
_PREDICTION_RE = re.compile(
    r"预测|准确率|AUC|ROC|F1|精确率|召回率|分类(?:准确|性能)|模型(?:预测|输出|评估)|"
    r"将(?:会|可能|有望)(?:发生|出现|增加|下降|提升|改善|持续)|未来.{0,8}(?:将|会)",
    re.IGNORECASE,
)
_COMPARISON_RE = re.compile(
    r"优于|高于|低于|超过|不及|比[^，。；！？\n]{0,10}(?:高|低|快|慢|强|弱|好|差|多|少|有效)"
    r"|对照(?:组|实验).{0,10}(?:更好|更快|更慢|更高|更低)|显著[^，。；！？\n]{0,6}"
    r"(?:高于|低于|优于|快于)"
)
_MECHANISM_RE = re.compile(
    r"机制|机理|通路|介导|驱动|调控|通过[^，。；！？\n]{0,20}(?:实现|导致|发挥作用|起作用)|"
    r"起[^，。；！？\n]{0,6}作用|途径|干预[^，。；！？\n]{0,6}(?:影响|改变)"
)
_CAUSALITY_RE = re.compile(
    r"导致|引起|促进|抑制|诱发|决定|使得|归因于|因果|因此|所以|从而|由此|"
    r"证明[^，。；！？\n]{0,12}(?:导致|影响|引起|成立|有效)|"
    r"使[^，。；！？\n]{0,8}(?:出现|增加|下降|改善)"
)
_SIGNIFICANCE_RE = re.compile(
    r"显著|显著性|p\s*[<≤]\s*0?\.?\d*|P\s*[<≤]|p值|P值|"
    r"差异[^，。；！？\n]{0,8}(?:显著|不显著)|统计(?:学)?(?:上)?(?:显著|不显著)|检验结果"
)
_GENERALIZATION_RE = re.compile(
    r"普遍|一般[^，。；！？\n]{0,6}适用|适用于(?:所有|任何|各类|一切)|均适用|推广到|普适|"
    r"放之四海|所有[^，。；！？\n]{0,8}(?:都|均)"
)
_REAL_WORLD_RE = re.compile(
    r"真实(?:场景|世界|环境|临床场景)|实际(?:应用|场景|部署|使用)|"
    r"临床(?:实践|效果|应用|证据|获益|场景)|落地|现实世界"
)

#: 检验信息标记：句内出现即认为该显著 claim 自带检验依据（不构成风险）。
#: 同时匹配规范化文本中的 p 值形态（如「p = 0.02」规范化后为「p002」）。
_TEST_MARK_RE = re.compile(
    r"p\s*(?:[<≤=])\s*0?\.?\d*|p\s*\d|t\s*=|F\s*=|χ²|卡方|置信区间|CI\b|方差分析|ANOVA|"
    r"t检验|F检验|回归|假设检验|显著性检验|显著性水平|检验",
    re.IGNORECASE,
)
#: 拟合/聚类/消融类方法标记：与方法性机制词同句时构成「机制宣称」风险。
_FIT_MARK_RE = re.compile(
    r"拟合|聚类|消融|降维|嵌入|embedding|训练[^，。；！？\n]{0,6}模型|"
    r"特征(?:重要性|选择)",
    re.IGNORECASE,
)

#: 类型优先级：每句只记最高优先级类型，风险检测独立扫描组合。
#: 顺序同时作为「claim 敏感性」基准：修订把高敏感类型（因果/机制/显著性）
#: 改写为低敏感类型（相关/观察）时记 downgraded。
_KIND_PRIORITY: tuple[tuple[ClaimStrengthKind, re.Pattern[str]], ...] = (
    (ClaimStrengthKind.MECHANISM, _MECHANISM_RE),
    (ClaimStrengthKind.CAUSALITY, _CAUSALITY_RE),
    (ClaimStrengthKind.COMPARISON, _COMPARISON_RE),
    (ClaimStrengthKind.SIGNIFICANCE, _SIGNIFICANCE_RE),
    (ClaimStrengthKind.GENERALIZATION, _GENERALIZATION_RE),
    (ClaimStrengthKind.REAL_WORLD_EFFECTIVENESS, _REAL_WORLD_RE),
    (ClaimStrengthKind.CORRELATION, _CORRELATION_RE),
    (ClaimStrengthKind.PREDICTION, _PREDICTION_RE),
    (ClaimStrengthKind.OBSERVATION, _OBSERVATION_RE),
)
#: claim 敏感性序号（用于 kind 变化的方向判定，index 越大越弱）。
_KIND_RANK = {kind: index for index, (kind, _) in enumerate(_KIND_PRIORITY)}

#: 每类 claim 的提示词说明（中文，供修订提示与理由生成）。
_KIND_CN: dict[ClaimStrengthKind, str] = {
    ClaimStrengthKind.OBSERVATION: "观察（做了什么、看到什么）",
    ClaimStrengthKind.CORRELATION: "相关关系",
    ClaimStrengthKind.PREDICTION: "预测性能",
    ClaimStrengthKind.COMPARISON: "比较结论",
    ClaimStrengthKind.MECHANISM: "机制结论",
    ClaimStrengthKind.CAUSALITY: "因果结论",
    ClaimStrengthKind.SIGNIFICANCE: "统计显著性",
    ClaimStrengthKind.GENERALIZATION: "泛化结论",
    ClaimStrengthKind.REAL_WORLD_EFFECTIVENESS: "真实场景有效性",
}

_RISK_CN: dict[EvidenceRiskCode, str] = {
    EvidenceRiskCode.CORRELATION_AS_CAUSALITY: "相关写成因果",
    EvidenceRiskCode.SIGNIFICANCE_WITHOUT_TEST: "无检验写显著",
    EvidenceRiskCode.MECHANISM_FROM_FIT: "拟合/聚类/消融宣称机制",
    EvidenceRiskCode.PREDICTION_AS_CAUSALITY: "预测准确等同因果",
    EvidenceRiskCode.PREDICTION_AS_REAL_WORLD: "预测等同真实场景有效",
}

#: 图表编号保护：修订文本与原文本的图表编号必须一致。
_FIGURE_TABLE_RE = re.compile(
    r"(?:图|表|Figure|Table|Fig\.?|Tab\.?)\s*\d+[a-z]?",
    re.IGNORECASE,
)
#: 模型/数据集名保护（中文名后缀；英文名由账本专名提取覆盖）。
_MODEL_DATASET_RE = re.compile(
    r"[一-鿿A-Za-z0-9]{2,20}(?:模型|数据集|语料库|基准)"
)

_SENTENCE_END_RE = re.compile(r"[。；！？\n]")


@dataclass(frozen=True)
class _Sentence:
    """一个带位置信息的句子（位置基于规范化文本）。"""

    text: str
    start: int
    end: int


def _split_sentences(text: str) -> list[_Sentence]:
    """按中文句末标点切分句子，保留原文位置（空句忽略）。"""
    sentences: list[_Sentence] = []
    start = 0
    for match in _SENTENCE_END_RE.finditer(text):
        seg = text[start : match.end()].strip()
        if seg:
            sentences.append(_Sentence(seg, start, match.end()))
        start = match.end()
    tail = text[start:].strip()
    if tail:
        sentences.append(_Sentence(tail, start, len(text)))
    return sentences


def _norm_key(text: str) -> str:
    """句子归一化键：只保留汉字、字母与数字，用于稳定比较。"""
    return re.sub(r"[^一-鿿A-Za-z0-9]", "", text)


def _token(prefix: str, *parts: str) -> str:
    """确定性标识：同一输入产出同一 ID，保证 diff 可重建（模型自述不参与）。"""
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _sentence_strength(sentence: str) -> ClaimStrengthTier:
    """句子当前强度档位：出现的最强档词汇（与事实锁同源分级）。"""
    candidates = [
        (word, tier)
        for tier, words in _STRENGTH_TIER
        for word in words
        if word in sentence
    ]
    candidates.sort(key=lambda c: len(c[0]), reverse=True)
    if not candidates:
        return ClaimStrengthTier.MEDIUM
    return ClaimStrengthTier(candidates[0][1])


def _source_bound(
    sentence: str, ledger: SourceLedger | None
) -> str | None:
    """claim 句到账本条目的绑定：句子中的数字/限定词/专名命中条目即绑定。

    只做确定性包含匹配；绑定失败返回 None（无法判定，不是放行）。
    """
    if ledger is None:
        return None
    keys = set()
    for match in re.finditer(r"\d+(?:\.\d+)?", sentence):
        keys.add(match.group(0))
    for entry in ledger.entries:
        for number in entry.numbers:
            if number.split("|")[0] in keys:
                return entry.entry_id
        for qualifier in entry.qualifiers:
            if qualifier and qualifier in sentence:
                return entry.entry_id
        for noun in entry.proper_nouns:
            if noun and noun in sentence:
                return entry.entry_id
    return None


# ---------------------------------------------------------------------------
# claim 分类
# ---------------------------------------------------------------------------


def classify_claims(text: str, ledger: SourceLedger | None = None) -> list[EvidenceClaim]:
    """对文本做句子级 claim 分类：每句一条最高优先级类型 + 当前强度。

    只识别类型标记与强度词汇，不做统计推断；位置基于原文（非规范化）。
    强度词汇与事实锁同源（证明/表明/可能 三档）。
    """
    claims: list[EvidenceClaim] = []
    for sentence in _split_sentences(text):
        kind: ClaimStrengthKind | None = None
        for candidate, pattern in _KIND_PRIORITY:
            if pattern.search(sentence.text):
                kind = candidate
                break
        if kind is None:
            continue
        claims.append(
            EvidenceClaim(
                claim_id=_token("clm", kind.value, sentence.text),
                kind=kind,
                tier=_sentence_strength(sentence.text),
                surface=sentence.text,
                location=SpanLocation(start=sentence.start, end=sentence.end),
                source_entry_id=_source_bound(sentence.text, ledger),
                note=(
                    f"识别为「{_KIND_CN[kind]}」claim，当前强度 "
                    f"{_tier_label(_sentence_strength(sentence.text))}。"
                ),
            )
        )
    return claims


def _tier_label(tier: ClaimStrengthTier) -> str:
    return {"weak": "弱", "medium": "中", "strong": "强"}[tier.value]


def _risk(
    code: EvidenceRiskCode,
    sentence: _Sentence,
    explanation: str,
) -> EvidenceRiskItem:
    return EvidenceRiskItem(
        risk_id=_token("risk", code.value, sentence.text),
        code=code,
        category=_RISK_CN[code],
        surface=sentence.text,
        location=SpanLocation(start=sentence.start, end=sentence.end),
        explanation=explanation,
    )


def _ledger_has_test_info(ledger: SourceLedger | None) -> bool:
    """账本材料是否包含检验/统计信息（p 值、检验、置信区间等）。

    无账本时视为「无法判定」，按无检验信息处理（宁可提示，不默认放行）。
    """
    if ledger is None:
        return False
    return any(
        _TEST_MARK_RE.search(entry.text_key) for entry in ledger.entries
    )


def detect_evidence_risks(
    candidate: str,
    ledger: SourceLedger | None = None,
) -> list[EvidenceRiskItem]:
    """默认模式：检测候选正文中的证据风险，只生成独立风险项。

    五种风险类型：相关写成因果、无检验写显著、拟合/聚类/消融宣称机制、
    预测准确等同因果、预测等同真实场景有效。检测只读，不改变正文。
    """
    risks: list[EvidenceRiskItem] = []
    ledger_has_test = _ledger_has_test_info(ledger)

    for sentence in _split_sentences(candidate):
        has_correlation = bool(_CORRELATION_RE.search(sentence.text))
        has_causality = bool(_CAUSALITY_RE.search(sentence.text))
        has_prediction = bool(_PREDICTION_RE.search(sentence.text))
        has_real_world = bool(_REAL_WORLD_RE.search(sentence.text))
        has_mechanism = bool(_MECHANISM_RE.search(sentence.text))
        has_significance = bool(_SIGNIFICANCE_RE.search(sentence.text))

        if has_correlation and has_causality:
            risks.append(
                _risk(
                    EvidenceRiskCode.CORRELATION_AS_CAUSALITY,
                    sentence,
                    "同一句既出现相关标记又出现因果动词，结论强度超出材料支持；"
                    "默认模式保持原文，仅提示风险。",
                )
            )
        if (
            has_significance
            and not _TEST_MARK_RE.search(sentence.text)
            and not ledger_has_test
        ):
            risks.append(
                _risk(
                    EvidenceRiskCode.SIGNIFICANCE_WITHOUT_TEST,
                    sentence,
                    "句子断言统计显著，但句内与账本材料都没有检验信息（p 值、"
                    "检验、置信区间），显著性无法判定；默认模式保持原文。",
                )
            )
        if _FIT_MARK_RE.search(sentence.text) and has_mechanism:
            risks.append(
                _risk(
                    EvidenceRiskCode.MECHANISM_FROM_FIT,
                    sentence,
                    "拟合/聚类/消融类方法结果被写成机制结论，方法无法证明机制；"
                    "默认模式保持原文，仅提示风险。",
                )
            )
        if has_prediction and has_causality:
            risks.append(
                _risk(
                    EvidenceRiskCode.PREDICTION_AS_CAUSALITY,
                    sentence,
                    "预测性能/准确率被写成因果结论，预测指标不能说明因果关系；"
                    "默认模式保持原文，仅提示风险。",
                )
            )
        if has_prediction and has_real_world:
            risks.append(
                _risk(
                    EvidenceRiskCode.PREDICTION_AS_REAL_WORLD,
                    sentence,
                    "预测或指标结果被写成真实场景有效，缺少真实场景证据；"
                    "默认模式保持原文，仅提示风险。",
                )
            )
    risks.sort(key=lambda r: (r.location.start, r.location.end))
    return risks


# ---------------------------------------------------------------------------
# 证据安全修订（仅 EVIDENCE_SAFE 模式）
# ---------------------------------------------------------------------------


def _ledger_material_summary(ledger: SourceLedger | None) -> str:
    """从账本提取数据/限定/统计信息摘要（只消费账本已有信息）。

    条目全文键 ``text_key`` 用于判断是否存在检验/统计信息；提取不到时
    明确写「无」，修订提示据此不得新增论据。
    """
    if ledger is None:
        return "无（未配置来源账本，不得新增任何方法与统计表述）"
    lines: list[str] = []
    for entry in ledger.entries:
        items: list[str] = []
        if entry.numbers:
            items.append("数据：" + "、".join(entry.numbers[:8]))
        if entry.qualifiers:
            items.append("限定：" + "、".join(entry.qualifiers[:6]))
        if entry.conclusion_strength:
            items.append(f"结论强度基线：{entry.conclusion_strength}")
        items.append(
            "含统计检验信息：" + ("是" if _TEST_MARK_RE.search(entry.text_key) else "否")
        )
        label = entry.source_label or entry.source_type.value
        lines.append(f"- {label}：{'；'.join(items)}")
    return "\n".join(lines) if lines else "无（材料中没有方法、统计或数据信息）"


def build_revision_prompt(
    contract: ExpressionTaskContract,
    candidate: str,
    risks: list[EvidenceRiskItem],
    ledger: SourceLedger | None,
) -> str:
    """编译证据安全修订提示（确定性文本；只允许降级结论措辞）。

    模型只能消费账本已有方法、统计、数据和限定信息；材料只支持较弱结论
    时降级措辞，不得新增论据、统计方法说明、研究局限、未来工作或新引用，
    也不得改动风险句子之外的句子与任何受保护项。
    """
    if not risks:
        raise ValueError("证据安全修订至少需要一条风险项。")
    risk_lines = "\n".join(
        f"{index}. 「{risk.surface}」（{risk.category}：{risk.explanation}）"
        for index, risk in enumerate(risks, start=1)
    )
    material = _ledger_material_summary(ledger)
    return f"""你是 BridGes 文章表达助手，执行一次证据安全修订
（提示编译版本 {EVIDENCE_SAFETY_VERSION}，用户已显式授权证据安全修订模式）。

【任务】只修订下方列出的风险句子，把结论强度降级到来源账本材料支持的档位；
其余句子一字不改。

【风险清单】
{risk_lines}

【来源账本材料】（只能消费以下已有信息，不得补充材料之外的内容）
{material}

【修订要求】
- 材料只支持相关关系时，因果措辞改为相关措辞（如「导致」改为「与……相关」）；
- 材料没有检验信息时，「显著」类断言改为观察描述（如「显著提升」改为「观察到提升」）；
- 材料没有机制证据时，机制断言改为作用效果描述，不解释机制；
- 材料没有真实场景证据时，不写真实场景有效；
- 不新增论据、统计方法说明、研究局限、未来工作或新引用；
- 数字、单位、日期、专名、模型/数据集名、样本、变量、实验条件、图表编号、
  公式、精确引语和引用关系一律保持原样；
- 除风险句子外，正文其他句子必须与原文逐字一致。

【输出要求】只输出 JSON，不得输出 JSON 之外的任何内容：
{{"final_text": 修订后的候选正文全文}}"""


# ---------------------------------------------------------------------------
# 确定性 diff：重建结论强度变化
# ---------------------------------------------------------------------------


def _sentence_map(sentences: list[_Sentence]) -> dict[str, list[int]]:
    mapping: dict[str, list[int]] = {}
    for index, sentence in enumerate(sentences):
        mapping.setdefault(_norm_key(sentence.text), []).append(index)
    return mapping


def _find_related(
    key: str, revised: list[_Sentence], taken: set[int]
) -> int | None:
    """在修订文本中找与原文句子最相似的句子（精确键优先，相似度兜底）。"""
    exact = [i for i in _sentence_map(revised).get(key, []) if i not in taken]
    if exact:
        return exact[0]
    best_index: int | None = None
    best_ratio = 0.0
    for index, sentence in enumerate(revised):
        if index in taken:
            continue
        ratio = difflib.SequenceMatcher(None, key, _norm_key(sentence.text)).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_index = index
    if best_index is not None and best_ratio >= 0.55:
        return best_index
    return None


def _protected_conflict(
    original: str, revised: str
) -> list[tuple[str, str, str]]:
    """受保护项一致性检查：图表编号与模型/数据集名必须完全一致。

    数字、单位、日期、专名、公式、引语、引用由来源账本保留检查覆盖；
    这里补充账本不覆盖的图表编号与中文模型/数据集名。
    """
    conflicts: list[tuple[str, str, str]] = []

    def collect(text: str, pattern: re.Pattern[str]) -> set[str]:
        return {m.group(0).lower() for m in pattern.finditer(text)}

    for label, pattern in (
        ("图表编号", _FIGURE_TABLE_RE),
        ("模型/数据集名", _MODEL_DATASET_RE),
    ):
        before = collect(original, pattern)
        after = collect(revised, pattern)
        if before != after:
            conflicts.append((label, "、".join(sorted(before)), "、".join(sorted(after))))
    return conflicts


def compare_claim_changes(
    original: str,
    revised: str,
    original_claims: list[EvidenceClaim],
) -> tuple[list[EvidenceRevisionChange], list[str]]:
    """重建结论强度变化：按句对齐原文与修订文本，比较类型与档位。

    返回 (变化记录, 冲突的中文说明)。非 claim 句子必须逐字保持；claim 句
    允许变化并比较：档位变弱记 downgraded，变强或整句消失记
    needs_user_confirmation；修订新增了原文没有的句子、受保护项不一致或
    非 claim 句被改动都返回冲突（修订视为无效，正文保持原文）。
    """
    changes: list[EvidenceRevisionChange] = []
    conflicts: list[str] = []

    original_sentences = _split_sentences(original)
    revised_sentences = _split_sentences(revised)

    claim_sentences = {
        (claim.location.start, claim.location.end): claim
        for claim in original_claims
    }

    taken: set[int] = set()
    for sentence in original_sentences:
        key = _norm_key(sentence.text)
        claim = claim_sentences.get((sentence.start, sentence.end))
        if claim is None:
            # 非 claim 句：修订文本中必须存在逐字相同的句子
            indexes = [
                i
                for i, other in enumerate(revised_sentences)
                if _norm_key(other.text) == key and i not in taken
            ]
            if not indexes:
                conflicts.append(f"非 claim 句子被改动：「{sentence.text[:40]}…」")
                continue
            taken.add(indexes[0])
            continue

        related = _find_related(key, revised_sentences, taken)
        if related is None:
            changes.append(
                EvidenceRevisionChange(
                    change_id=_token(
                        "chg", "removed", claim.kind.value, sentence.text
                    ),
                    change_type=RevisionChangeType.REMOVED,
                    kind=claim.kind,
                    original_span=sentence.text,
                    revised_span="",
                    original_location=SpanLocation(start=sentence.start, end=sentence.end),
                    revised_location=SpanLocation(start=-1, end=-1),
                    source_entry_id=claim.source_entry_id,
                    reason="证据安全修订后该 claim 句在正文中消失，无法核对变化，需用户确认。",
                    needs_user_confirmation=True,
                )
            )
            continue
        taken.add(related)
        revised_sentence = revised_sentences[related]
        if key == _norm_key(revised_sentence.text):
            continue  # 措辞未变，无实质变化
        revised_kind: ClaimStrengthKind | None = None
        for candidate, pattern in _KIND_PRIORITY:
            if pattern.search(revised_sentence.text):
                revised_kind = candidate
                break
        revised_tier = _sentence_strength(revised_sentence.text)
        original_tier = claim.tier
        tier_rank = {"weak": 0, "medium": 1, "strong": 2}
        kind_downgraded = (
            revised_kind is not None
            and _KIND_RANK[revised_kind] > _KIND_RANK[claim.kind]
        )
        if tier_rank[revised_tier.value] < tier_rank[original_tier.value] or (
            tier_rank[revised_tier.value] == tier_rank[original_tier.value]
            and kind_downgraded
        ):
            change_type = RevisionChangeType.DOWNGRADED
            needs_confirmation = False
        elif tier_rank[revised_tier.value] > tier_rank[original_tier.value]:
            change_type = RevisionChangeType.UPGRADED
            needs_confirmation = True
        else:
            change_type = RevisionChangeType.REWORDED
            needs_confirmation = revised_kind is None
        reason = _change_reason(
            change_type, claim, revised_sentence.text,
            revised_kind, revised_tier, needs_confirmation,
        )
        changes.append(
            EvidenceRevisionChange(
                change_id=_token(
                    "chg",
                    change_type.value,
                    claim.kind.value,
                    sentence.text,
                    revised_sentence.text,
                ),
                change_type=change_type,
                kind=claim.kind,
                original_span=sentence.text,
                revised_span=revised_sentence.text,
                original_location=SpanLocation(start=sentence.start, end=sentence.end),
                revised_location=SpanLocation(
                    start=revised_sentence.start, end=revised_sentence.end
                ),
                source_entry_id=claim.source_entry_id,
                reason=reason,
                needs_user_confirmation=needs_confirmation,
            )
        )

    # 修订不得新增句子：原文没有的句子（如补写的局限、论据、未来工作）
    # 即使不带数字或方法词也会被确定性捕获，防止「纯文字补写」绕过硬门。
    for index, sentence in enumerate(revised_sentences):
        if index not in taken:
            conflicts.append(
                f"修订新增了原文没有的句子：「{sentence.text[:40]}…」"
            )

    for label, before, after in _protected_conflict(original, revised):
        conflicts.append(f"{label}不一致：原文「{before}」≠ 修订「{after}」")

    changes.sort(key=lambda c: (c.original_location.start, c.original_location.end))
    return changes, conflicts


def _change_reason(
    change_type: RevisionChangeType,
    claim: EvidenceClaim,
    revised_surface: str,
    revised_kind: ClaimStrengthKind | None,
    revised_tier: ClaimStrengthTier,
    needs_confirmation: bool,
) -> str:
    if change_type == RevisionChangeType.DOWNGRADED:
        return (
            f"证据安全修订：{_KIND_CN[claim.kind]}从「{_tier_label(claim.tier)}」"
            f"降级为「{_tier_label(revised_tier)}」，消费来源账本已有信息"
            + (f"（来源 {claim.source_entry_id}）" if claim.source_entry_id else "")
            + "，不新增论据。"
        )
    if change_type == RevisionChangeType.UPGRADED:
        return "证据安全修订把结论强度升级，超出授权范围，需用户确认。"
    if change_type == RevisionChangeType.REWORDED:
        suffix = "，需用户确认。" if needs_confirmation else "。"
        return "证据安全修订调整了结论措辞但强度未变" + suffix
    return "证据安全修订后该 claim 句在正文中消失，无法核对变化，需用户确认。"


def run_evidence_safety(
    candidate: str,
    *,
    contract: ExpressionTaskContract,
    ledger: SourceLedger | None = None,
) -> EvidenceSafeReport:
    """确定性执行证据安全检查（默认模式路径）。

    分类候选正文的 claim、检测证据风险；不改变正文。修订与 diff 由服务层
    在 EVIDENCE_SAFE 模式下另行编排。
    """
    claims = classify_claims(candidate, ledger)
    risks = detect_evidence_risks(candidate, ledger)
    by_kind: dict[str, int] = {}
    for claim in claims:
        by_kind[claim.kind.value] = by_kind.get(claim.kind.value, 0) + 1
    by_risk: dict[str, int] = {}
    for risk in risks:
        by_risk[risk.code.value] = by_risk.get(risk.code.value, 0) + 1
    return EvidenceSafeReport(
        report_version=EVIDENCE_SAFETY_VERSION,
        contract_hash=contract.version_hash,
        mode=contract.evidence_revision_mode,
        claims=claims,
        risks=risks,
        revisions=[],
        revision_status=EvidenceRevisionStatus.NOT_APPLIED,
        summary=EvidenceSafeSummary(
            claim_count=len(claims),
            by_kind=by_kind,
            risk_count=len(risks),
            by_risk_code=by_risk,
            revision_count=0,
            protected_conflict_count=0,
            hold_for_user_count=0,
        ),
    )


__all__ = [
    "EVIDENCE_SAFETY_VERSION",
    "classify_claims",
    "detect_evidence_risks",
    "build_revision_prompt",
    "compare_claim_changes",
    "run_evidence_safety",
]
