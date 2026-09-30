"""Issue 05：按保留意图绑定事实片段，修复正则盲替换。

本模块是「可绑定片段协议」的单一事实源，供 Issue 06（流式正文/终态一致性）
与 Issue 21（保留意图）复用。它把确定性保护区从「按同类第一个候选盲替换」
改为「按保留意图 + 对象锚点/顺序 + 来源哈希的精确逐一配对」：

- **原样保留项**：用户自有正文里的代码、公式、链接、JSON、引用与带单位数值。
  仅在有保留意图时，按对象锚点（同标签如「甲/乙」）或明确顺序逐一绑定到候选
  中的对应片段；已消费的候选片段不能再配给其他源片段；候选盈余时拒绝猜测替换。
- **允许纠正项**：用户明确要求「改为/纠正/计算」的对象不再当作事实锁，保留
  用户授权的纠正结果与任务计算出的新值。
- **可引用来源**（``additional_sources``）：只限定引用资格，不按清单顺序强制
  换链接；候选已合法选中的来源不会被恢复成另一个来源。
- **语义参考判断**：裸数字、中文单位、否定、条件、因果与结论强度另列判断，
  依靠已有科学/任务核验，不建立声称保证全部事实的正则系统，也不做静默替换。

关键不一致（缺失片段、清单外引用、无法安全定位）通过
:class:`ProtectionResult.inconsistencies` 上报，交由调用方采用现有失败/降级
语义。修复是确定性代码，不随提示策略快照回滚，因此旧快照重试不会保留该
确定性缺陷，也不新增任何模型调用（净室边界与已退役文章人味化保持不变）。
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum

#: 可绑定片段协议版本（Issue 06 流式重放与 Issue 21 保留意图复用同一协议）。
FACT_PROTECTION_PROTOCOL_VERSION = "intent-bound-fragment-v1"


class ProtectionIntent(StrEnum):
    """当前任务对用户正文片段的确定性保留意图。"""

    #: 原样保留：用户自有正文中的受保护片段不得被表达调整改写。
    VERBATIM = "verbatim"
    #: 允许纠正/计算：用户明确要求改动或计算，原片段是输入而非事实锁。
    CORRECTION = "correction"


class FragmentKind(StrEnum):
    """可绑定片段类别（与旧保护区固定清单保持一致）。"""

    CODE_FENCE = "code_fence"
    INLINE_CODE = "inline_code"
    FORMULA = "formula"
    URL = "url"
    JSON = "json"
    CITATION = "citation"
    NUMBER_WITH_UNIT = "number_with_unit"


#: 受保护片段正则。只负责识别「可绑定对象」，不是全局硬词表或风格评分器；
#: 带单位数值不跨行匹配，避免把换行后的词误并入数值。
_FRAGMENT_PATTERNS: tuple[tuple[FragmentKind, re.Pattern[str]], ...] = (
    (FragmentKind.CODE_FENCE, re.compile(r"```[\s\S]*?```")),
    (FragmentKind.INLINE_CODE, re.compile(r"`[^`\n]+`")),
    (FragmentKind.FORMULA, re.compile(r"\$[^$\n]+\$")),
    (FragmentKind.URL, re.compile(r"https?://[^\s<>\]})]+")),
    (FragmentKind.JSON, re.compile(r"\{\s*\"[^{}\n]+\"\s*:\s*[^{}\n]+\}")),
    (FragmentKind.CITATION, re.compile(r"(?<!\w)\[[0-9]+(?:[-,][0-9]+)*\]")),
    (
        FragmentKind.NUMBER_WITH_UNIT,
        re.compile(r"(?<!\w)\d+(?:\.\d+)?[ \t]*[A-Za-zμΩ%°][^\s，。；;)]*"),
    ),
)

#: 明确的「改动/计算」指令 → 关闭原样保留（原片段是输入，不是事实锁）。
#: 这是确定性启发，用于区分「原样保留」与「允许纠正」；不覆盖用户当前要求。
_TRANSFORM_INTENT_RE = re.compile(
    r"改为|改成|改到|修改为|修改成|纠正为|更正为|修正为|替换为|换成|订正为|"
    r"重写为|改写为|计算出|计算|算出|重算|换算|求值"
)

# ---------------------------------------------------------------------------
# 语义参考判断：只报告，不替换。
# ---------------------------------------------------------------------------

_NEGATION_RE = re.compile(
    r"不(?:是|会|能|可|应|得|再|被|存在|属于|显著|成立|正确|真实|够|同|一样|一致|符)"
    r"|未(?:发现|见|能|曾|必|有|知|完成|达到|出现|显著|变)"
    r"|没(?:有|发现|出现|看到|能|变化)"
    r"|无(?:法|明显|显著|证据|差异|从)"
    r"|并非|尚未|从未|并未|不存在"
)
_CONDITION_RE = re.compile(
    r"如果|假如|假设|若|倘若|一旦|当.{0,8}时|在.{0,12}条件下|前提是|除非|仅在"
)
_CAUSAL_RE = re.compile(r"因为|由于|因此|所以|因而|从而|导致|引起|致使|使得|造成")
_CONCLUSION_STRENGTH_RE = re.compile(
    r"不显著|显著|可能|或许|也许|大概|似乎|表明|说明|提示|证明|证实|"
    r"一定|必然|肯定|绝对|完全|全部|所有|总是|从不"
)
_BARE_NUMBER_RE = re.compile(r"(?<![0-9A-Za-z_.])\d+(?:\.\d+)?(?![0-9A-Za-z_])")

_CJK_UNITS: tuple[str, ...] = (
    "平方公里",
    "平方厘米",
    "平方米",
    "立方米",
    "摄氏度",
    "百分点",
    "公里",
    "千米",
    "厘米",
    "毫米",
    "分米",
    "纳米",
    "微米",
    "千克",
    "公斤",
    "毫克",
    "毫升",
    "毫秒",
    "分钟",
    "小时",
    "个月",
    "千帕",
    "千焦",
    "千瓦",
    "开尔文",
    "米",
    "克",
    "吨",
    "斤",
    "两",
    "升",
    "秒",
    "分",
    "天",
    "年",
    "周",
    "月",
    "度",
    "帕",
    "焦",
    "瓦",
    "个",
    "次",
    "倍",
    "页",
    "本",
    "元",
    "人",
    "项",
    "种",
)
_CJK_UNIT_RE = re.compile(
    r"\d+(?:\.\d+)?[ \t]*(?:" + "|".join(sorted(_CJK_UNITS, key=len, reverse=True)) + r")"
)

_ANCHOR_WINDOW = 16
_ANCHOR_MAX = 8


@dataclass(frozen=True)
class SemanticReferenceReport:
    """裸数字/中文单位/否定/条件/因果/结论强度的语义参考判断。

    只描述源正文与候选正文之间的确定性差异，供调用方按任务合同决定失败或
    降级；不做替换，也不声称已保证全部事实正确。
    """

    bare_number_changes: tuple[tuple[str, str], ...] = ()
    chinese_unit_changes: tuple[tuple[str, str], ...] = ()
    negation_changed: bool = False
    condition_changed: bool = False
    causal_changed: bool = False
    conclusion_strength_changed: bool = False

    @property
    def is_consistent(self) -> bool:
        """是否存在任一语义参考差异（差异不等于错误，需任务另行裁决）。"""
        return not (
            self.bare_number_changes
            or self.chinese_unit_changes
            or self.negation_changed
            or self.condition_changed
            or self.causal_changed
            or self.conclusion_strength_changed
        )


@dataclass(frozen=True)
class ProtectedFragment:
    """源正文中一个可绑定片段，带稳定位置、对象锚点与来源哈希。"""

    kind: FragmentKind
    text: str
    start: int
    end: int
    anchor: str
    digest: str


@dataclass(frozen=True)
class ProtectionInconsistency:
    """无法安全修复的绑定不一致（不含正文，只带片段本身与确定性原因）。"""

    kind: str
    detail: str
    fragment: str = ""


@dataclass(frozen=True)
class ProtectionResult:
    """一次保护区规划的结果：正文、意图、绑定明细与不一致清单。"""

    content: str
    intent: ProtectionIntent
    bindings: tuple[tuple[str, str, str], ...] = ()
    inconsistencies: tuple[ProtectionInconsistency, ...] = ()
    semantic: SemanticReferenceReport | None = None
    protocol_version: str = FACT_PROTECTION_PROTOCOL_VERSION


# ---------------------------------------------------------------------------
# 片段识别与配对
# ---------------------------------------------------------------------------


def _anchor_for(text: str, start: int, previous_end: int) -> str:
    """取片段与上一个片段之间的对象标签作为锚点（如「甲」「乙」）。

    只在两个相邻受保护片段之间取窗口，避免把前一个片段的数值并进锚点。
    """
    window = text[max(previous_end, start - _ANCHOR_WINDOW) : start]
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "", window)
    return cleaned[-_ANCHOR_MAX:]


def compile_protected_fragments(text: str) -> list[ProtectedFragment]:
    """识别文本中的可绑定片段，去掉被更大片段包含的重叠匹配。

    返回按出现顺序排列的片段；每个片段带位置、对象锚点与来源哈希，供精确
    绑定使用。识别只依赖确定性正则，不调用模型。
    """
    if not text:
        return []
    matches: list[tuple[int, int, FragmentKind, str]] = []
    for kind, pattern in _FRAGMENT_PATTERNS:
        for match in pattern.finditer(text):
            matches.append((match.start(), match.end(), kind, match.group(0)))

    kept: list[tuple[int, int, FragmentKind, str]] = []
    for index, (start, end, kind, value) in enumerate(matches):
        contained = any(
            other_start <= start
            and end <= other_end
            and (other_start, other_end) != (start, end)
            for other_index, (other_start, other_end, _, _) in enumerate(matches)
            if other_index != index
        )
        if not contained:
            kept.append((start, end, kind, value))
    kept.sort(key=lambda item: (item[0], -(item[1] - item[0])))

    fragments: list[ProtectedFragment] = []
    seen_spans: set[tuple[int, int]] = set()
    previous_end = 0
    for start, end, kind, value in kept:
        if (start, end) in seen_spans:
            continue
        seen_spans.add((start, end))
        fragments.append(
            ProtectedFragment(
                kind=kind,
                text=value,
                start=start,
                end=end,
                anchor=_anchor_for(text, start, previous_end),
                digest=hashlib.sha1(value.encode("utf-8")).hexdigest(),
            )
        )
        previous_end = end
    return fragments


def _pair_fragments(
    sources: list[ProtectedFragment],
    candidates: list[ProtectedFragment],
) -> dict[int, int]:
    """把同类源片段与候选片段做注入式逐一配对，返回 ``{源下标: 候选下标}``。

    三段式，均为确定性：
    1. 文本与锚点都一致的精确匹配（已逐字保留）；
    2. 锚点一致而文本漂移（同一对象标签，允许换写后回填）；
    3. 剩余片段按顺序明确逐一配对；候选盈余的源片段保持未配对（拒绝猜测）。
    """
    pairs: dict[int, int] = {}
    used: set[int] = set()

    for source_index, source in enumerate(sources):
        for candidate_index, candidate in enumerate(candidates):
            if candidate_index in used:
                continue
            if candidate.text == source.text and candidate.anchor == source.anchor:
                pairs[source_index] = candidate_index
                used.add(candidate_index)
                break

    for source_index, source in enumerate(sources):
        if source_index in pairs or not source.anchor:
            continue
        for candidate_index, candidate in enumerate(candidates):
            if candidate_index in used:
                continue
            if candidate.anchor == source.anchor:
                pairs[source_index] = candidate_index
                used.add(candidate_index)
                break

    remaining_sources = [index for index in range(len(sources)) if index not in pairs]
    remaining_candidates = [
        index for index in range(len(candidates)) if index not in used
    ]
    for source_index, candidate_index in zip(
        remaining_sources, remaining_candidates, strict=False
    ):
        pairs[source_index] = candidate_index
        used.add(candidate_index)
    return pairs


def _apply_replacements(
    text: str, replacements: list[tuple[int, int, str]]
) -> str:
    """按位置从右到左替换，保持左侧位置有效。"""
    for start, end, new_text in sorted(
        replacements, key=lambda item: item[0], reverse=True
    ):
        text = text[:start] + new_text + text[end:]
    return text


# ---------------------------------------------------------------------------
# 意图与规划
# ---------------------------------------------------------------------------


def detect_protection_intent(text: str) -> ProtectionIntent:
    """从用户正文判定保留意图；默认原样保留，明确改动/计算指令则允许纠正。"""
    if text and _TRANSFORM_INTENT_RE.search(text):
        return ProtectionIntent.CORRECTION
    return ProtectionIntent.VERBATIM


def _apply_citation_eligibility(
    candidate: str, additional_sources: tuple[str, ...]
) -> tuple[str, tuple[ProtectionInconsistency, ...]]:
    """只按引用资格处理候选链接，不按清单顺序强制换链接。

    - 候选链接已在合法来源清单内 → 保留（不再换成另一个来源）。
    - 候选链接在清单外 → 仅在存在未消费的合法来源时按顺序精确绑定；否则
      记为不一致，交由调用方降级，不伪造来源。
    """
    eligible = [source for source in additional_sources if source]
    if not eligible:
        return candidate, ()
    fragments = [
        fragment
        for fragment in compile_protected_fragments(candidate)
        if fragment.kind is FragmentKind.URL
    ]
    consumed: set[str] = set()
    replacements: list[tuple[int, int, str]] = []
    inconsistencies: list[ProtectionInconsistency] = []
    for fragment in fragments:
        if fragment.text in eligible:
            consumed.add(fragment.text)
            continue
        target = next((source for source in eligible if source not in consumed), None)
        if target is None:
            inconsistencies.append(
                ProtectionInconsistency(
                    kind="ineligible_citation",
                    detail=(
                        "候选引用了来源清单外的链接且无未使用的合法来源，"
                        f"按现有降级语义保留：{fragment.text}"
                    ),
                    fragment=fragment.text,
                )
            )
            continue
        consumed.add(target)
        replacements.append((fragment.start, fragment.end, target))
    return _apply_replacements(candidate, replacements), tuple(inconsistencies)


def _apply_verbatim(
    original: str, candidate: str, *, append_missing: bool
) -> tuple[str, tuple[ProtectionInconsistency, ...], tuple[tuple[str, str, str], ...]]:
    """按保留意图精确绑定原正文片段；缺失片段仅在任务要求时补尾。"""
    sources = compile_protected_fragments(original)
    if not sources:
        return candidate, (), ()
    candidates = compile_protected_fragments(candidate)

    sources_by_kind: dict[FragmentKind, list[ProtectedFragment]] = defaultdict(list)
    candidates_by_kind: dict[FragmentKind, list[ProtectedFragment]] = defaultdict(list)
    for fragment in sources:
        sources_by_kind[fragment.kind].append(fragment)
    for fragment in candidates:
        candidates_by_kind[fragment.kind].append(fragment)

    replacements: list[tuple[int, int, str]] = []
    bindings: list[tuple[str, str, str]] = []
    inconsistencies: list[ProtectionInconsistency] = []
    missing: list[ProtectedFragment] = []

    for kind, kind_sources in sources_by_kind.items():
        kind_candidates = candidates_by_kind.get(kind, [])
        pairs = _pair_fragments(kind_sources, kind_candidates)
        for source_index, source in enumerate(kind_sources):
            candidate_index = pairs.get(source_index)
            if candidate_index is None:
                missing.append(source)
                if not append_missing:
                    inconsistencies.append(
                        ProtectionInconsistency(
                            kind="missing_fragment",
                            detail=(
                                "原样保留片段在候选正文中缺失，非任务必需时不强行补尾："
                                f"{source.text}"
                            ),
                            fragment=source.text,
                        )
                    )
                continue
            target = kind_candidates[candidate_index]
            bindings.append((kind.value, source.text, target.text))
            if target.text != source.text:
                replacements.append((target.start, target.end, source.text))

    restored = _apply_replacements(candidate, replacements)
    if append_missing and missing:
        separator = "" if not restored or restored.endswith("\n") else "\n"
        restored += separator + "\n".join(fragment.text for fragment in missing)
    return restored, tuple(inconsistencies), tuple(bindings)


def plan_fragment_protection(
    original: str,
    candidate: str,
    *,
    append_missing: bool = False,
    additional_sources: tuple[str, ...] = (),
    intent: ProtectionIntent | None = None,
    semantic_check: bool = False,
) -> ProtectionResult:
    """规划候选正文的保护区恢复。

    ``original`` 是用户自有正文（受保护片段的权威来源），``candidate`` 是
    模型候选正文。默认原样保留；命中改动/计算指令时按 ``CORRECTION`` 跳过
    原样保留。``additional_sources`` 只限定引用资格。``append_missing`` 由
    任务合同决定，默认不强行补尾。``semantic_check`` 打开时附带语义参考判断。
    """
    resolved_intent = intent or detect_protection_intent(original)
    inconsistencies: list[ProtectionInconsistency] = []
    bindings: list[tuple[str, str, str]] = []

    content = candidate
    content, citation_inconsistencies = _apply_citation_eligibility(
        content, additional_sources
    )
    inconsistencies.extend(citation_inconsistencies)

    if resolved_intent is ProtectionIntent.VERBATIM and original:
        content, verbatim_inconsistencies, verbatim_bindings = _apply_verbatim(
            original, content, append_missing=append_missing
        )
        inconsistencies.extend(verbatim_inconsistencies)
        bindings.extend(verbatim_bindings)

    semantic = (
        assess_semantic_reference(original, content) if semantic_check else None
    )
    return ProtectionResult(
        content=content,
        intent=resolved_intent,
        bindings=tuple(bindings),
        inconsistencies=tuple(inconsistencies),
        semantic=semantic,
    )


# ---------------------------------------------------------------------------
# 语义参考判断
# ---------------------------------------------------------------------------


def _rank_pairs(left: list[str], right: list[str]) -> tuple[tuple[str, str], ...]:
    """按出现顺序配对两串标记，返回不同的配对（含盈余的空白占位）。"""
    changes: list[tuple[str, str]] = []
    for index in range(max(len(left), len(right))):
        left_item = left[index] if index < len(left) else ""
        right_item = right[index] if index < len(right) else ""
        if left_item != right_item:
            changes.append((left_item, right_item))
    return tuple(changes)


def _protected_spans(text: str) -> list[tuple[int, int]]:
    return [(fragment.start, fragment.end) for fragment in compile_protected_fragments(text)]


def _bare_numbers(text: str) -> list[str]:
    """受保护片段之外的裸数字（不含单位、不在代码/公式/引用内）。"""
    spans = _protected_spans(text)
    numbers: list[str] = []
    for match in _BARE_NUMBER_RE.finditer(text):
        if any(start <= match.start() and match.end() <= end for start, end in spans):
            continue
        numbers.append(match.group(0))
    return numbers


def _cjk_units(text: str) -> list[str]:
    return _CJK_UNIT_RE.findall(text)


def assess_semantic_reference(source: str, candidate: str) -> SemanticReferenceReport:
    """比较源正文与候选正文的语义参考标记，只报告差异，不做替换。"""
    return SemanticReferenceReport(
        bare_number_changes=_rank_pairs(_bare_numbers(source), _bare_numbers(candidate)),
        chinese_unit_changes=_rank_pairs(_cjk_units(source), _cjk_units(candidate)),
        negation_changed=sorted(_NEGATION_RE.findall(source))
        != sorted(_NEGATION_RE.findall(candidate)),
        condition_changed=sorted(_CONDITION_RE.findall(source))
        != sorted(_CONDITION_RE.findall(candidate)),
        causal_changed=sorted(_CAUSAL_RE.findall(source))
        != sorted(_CAUSAL_RE.findall(candidate)),
        conclusion_strength_changed=sorted(_CONCLUSION_STRENGTH_RE.findall(source))
        != sorted(_CONCLUSION_STRENGTH_RE.findall(candidate)),
    )


__all__ = [
    "FACT_PROTECTION_PROTOCOL_VERSION",
    "FragmentKind",
    "ProtectedFragment",
    "ProtectionInconsistency",
    "ProtectionIntent",
    "ProtectionResult",
    "SemanticReferenceReport",
    "assess_semantic_reference",
    "compile_protected_fragments",
    "detect_protection_intent",
    "plan_fragment_protection",
]
