"""Issue 05：按保留意图绑定事实片段，修复正则盲替换。

本模块是「可绑定片段协议」的单一事实源，供 Issue 06（流式正文/终态一致性）
与 Issue 21（保留意图）复用。它把确定性保护区从「按同类第一个候选盲替换」
改为「按保留意图 + 可靠对象锚点/静态槽位 + 来源哈希的精确逐一配对」：

- **原样保留项**：用户自有正文里的代码、公式、链接、JSON、引用与带单位数值。
  仅在有保留意图时，按唯一对象锚点（同标签如「甲/乙」）逐一绑定；无标签
  槽位要求全文静态结构一致；已消费目标不再复用，数量相等也不能猜测替换。
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
    #: 普通问答：用户片段是输入参考，不要求答案复述或原样保留。
    REFERENCE = "reference"


class FragmentKind(StrEnum):
    """可绑定片段类别（与旧保护区固定清单保持一致）。"""

    CODE_FENCE = "code_fence"
    INLINE_CODE = "inline_code"
    FORMULA = "formula"
    URL = "url"
    JSON = "json"
    CITATION = "citation"
    NUMBER_WITH_UNIT = "number_with_unit"


class InconsistencyKind(StrEnum):
    """无法安全修复的绑定不一致类别（供调用方按类别降级）。"""

    MISSING_FRAGMENT = "missing_fragment"
    INELIGIBLE_CITATION = "ineligible_citation"
    AMBIGUOUS_BINDING = "ambiguous_binding"


#: 受保护片段正则。只负责识别「可绑定对象」，不是全局硬词表或风格评分器；
#: 带单位数值不跨行匹配，避免把换行后的词误并入数值。
_FRAGMENT_PATTERNS: tuple[tuple[FragmentKind, re.Pattern[str]], ...] = (
    (FragmentKind.CODE_FENCE, re.compile(r"```[\s\S]*?```")),
    (FragmentKind.INLINE_CODE, re.compile(r"`[^`\n]+`")),
    (FragmentKind.FORMULA, re.compile(r"\$[^$\n]+\$")),
    (FragmentKind.URL, re.compile(r"https?://[^\s<>\]})，。；、（）」』”]+")),
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
    r"重写为|改写为|计算出|计算(?!机|器|科学|复杂度)|算出|重算|换算|求值"
)
_VERBATIM_INTENT_RE = re.compile(r"原样|逐字|保留|保持|不要改动事实")
_REFERENCE_TASK_RE = re.compile(
    r"多少|等于|是什么意思|为什么|是否|如何|怎么|解释|讲解|分析|求解|求出|"
    r"表示成|转换成|转成|[？?]"
)
_CLAUSE_BOUNDARY_RE = re.compile(
    r"[，,。；;！？!?\n]|(?:并且|并|且|同时)(?=请|要|原样|逐字|保持|保留|把|将|不要|改|计算)"
)
_DENIED_TRANSFORM_RE = re.compile(r"不要|不得|禁止|无需|不必|不能|不允许")
_BACK_REFERENCE_RE = re.compile(r"这(?:行|段|个|条)|该(?:值|行|段)|数值|前述|上述|它")
_QUOTED_TEXT_RE = re.compile(r'“[^”]*”|「[^」]*」|"[^"\n]*"')

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

    kind: InconsistencyKind
    detail: str
    fragment: str = ""
    required: bool = False


@dataclass(frozen=True)
class ProtectionResult:
    """一次保护区规划的结果：正文、意图、绑定明细与不一致清单。"""

    content: str
    intent: ProtectionIntent
    bindings: tuple[tuple[str, str, str], ...] = ()
    inconsistencies: tuple[ProtectionInconsistency, ...] = ()
    semantic: SemanticReferenceReport | None = None
    protocol_version: str = FACT_PROTECTION_PROTOCOL_VERSION

    @property
    def has_critical_inconsistency(self) -> bool:
        """关键遗漏、歧义或清单外引用须进入调用方的失败终态。"""
        return any(
            item.required or item.kind is not InconsistencyKind.MISSING_FRAGMENT
            for item in self.inconsistencies
        )


# ---------------------------------------------------------------------------
# 片段识别与配对
# ---------------------------------------------------------------------------


def _anchor_for(text: str, start: int, previous_end: int) -> str:
    """取片段与上一个片段之间的对象标签作为锚点（如「甲」「乙」）。

    只在两个相邻受保护片段之间取窗口，避免把前一个片段的数值并进锚点。
    """
    window = text[max(previous_end, start - _ANCHOR_WINDOW) : start]
    window = re.split(r"[，,。；;：:\n]", window.rstrip(" \r\n：:"))[-1]
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
    *,
    reliable_positions: bool,
) -> dict[int, int]:
    """把同类源片段与候选片段做注入式逐一配对，返回 ``{源下标: 候选下标}``。

    三段式，均为确定性：
    1. 来源哈希与对象锚点都一致的精确匹配（已逐字保留，无需替换）；
    2. 锚点在两侧均唯一（无标签时还要求全文静态结构一致）；
    3. 剩余逐字存在的片段只确认保留，不改写；有明确冲突对象时不认可。
    数量相等不证明对象相同，剩余片段拒绝猜测替换。
    """
    pairs: dict[int, int] = {}
    used: set[int] = set()

    for source_index, source in enumerate(sources):
        for candidate_index, candidate in enumerate(candidates):
            if candidate_index in used:
                continue
            if candidate.digest == source.digest and candidate.anchor == source.anchor:
                pairs[source_index] = candidate_index
                used.add(candidate_index)
                break

    for source_index, source in enumerate(sources):
        if source_index in pairs or (not source.anchor and not reliable_positions):
            continue
        if sum(item.anchor == source.anchor for item in sources) != 1:
            continue
        if sum(item.anchor == source.anchor for item in candidates) != 1:
            continue
        for candidate_index, candidate in enumerate(candidates):
            if candidate_index in used:
                continue
            if candidate.anchor == source.anchor:
                pairs[source_index] = candidate_index
                used.add(candidate_index)
                break

    # 可靠对象绑定之后，逐字存在的剩余片段只确认保留，不作任何替换。
    # 若候选锚点明确指向另一源对象，不能靠相同数值认可该候选。
    for source_index, source in enumerate(sources):
        if source_index in pairs:
            continue
        for candidate_index, candidate in enumerate(candidates):
            if candidate_index in used or candidate.digest != source.digest:
                continue
            if candidate.anchor != source.anchor and any(
                item.anchor == candidate.anchor and item.anchor for item in sources
            ):
                continue
            pairs[source_index] = candidate_index
            used.add(candidate_index)
            break
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


def _instruction_text(text: str) -> str:
    """材料内词语不构成授权，遮蔽时保留位置供对象定位使用。"""
    spans = [(item.start, item.end) for item in compile_protected_fragments(text)]
    spans += [(match.start(), match.end()) for match in _QUOTED_TEXT_RE.finditer(text)]
    characters = list(text)
    for start, end in spans:
        characters[start:end] = " " * (end - start)
    return "".join(characters)


def detect_protection_intent(text: str) -> ProtectionIntent:
    """明确改动按对象授权；保留请求优先于普通问答参考。"""
    if _transform_spans(text):
        return ProtectionIntent.CORRECTION
    instructions = _instruction_text(text)
    if not _has_preservation_request(instructions) and _REFERENCE_TASK_RE.search(instructions):
        return ProtectionIntent.REFERENCE
    return ProtectionIntent.VERBATIM


def _has_preservation_request(instructions: str) -> bool:
    """显式保留与禁止改动均属于必需合同；普通否定话语不设门。"""
    if _VERBATIM_INTENT_RE.search(instructions):
        return True
    return any(
        _DENIED_TRANSFORM_RE.search(clause[:match.start()])
        for clause in _CLAUSE_BOUNDARY_RE.split(instructions)
        for match in _TRANSFORM_INTENT_RE.finditer(clause)
    )


def _transform_spans(text: str) -> list[tuple[int, int]]:
    """将授权限定到分句；引语、代码内词语和禁止改动不构成授权。"""
    fragments = compile_protected_fragments(text)
    instructions = _instruction_text(text)
    boundaries = [match.end() for match in _CLAUSE_BOUNDARY_RE.finditer(instructions)]
    spans: list[tuple[int, int]] = []
    for match in _TRANSFORM_INTENT_RE.finditer(instructions):
        start = max((end for end in boundaries if end <= match.start()), default=0)
        end = min((end for end in boundaries if end > match.end()), default=len(text))
        prefix = instructions[start : match.start()]
        if _DENIED_TRANSFORM_RE.search(prefix) or _VERBATIM_INTENT_RE.search(prefix):
            continue
        spans.append((start, end))
        # 「这行代码不对，请改为 2」可明确指回前一分句的唯一对象；计算
        # 指令不释放前一分句里的已知输入值。
        if (
            not any(start <= item.start < match.start() for item in fragments)
            and "算" not in match.group() and match.group() != "求值"
            and _BACK_REFERENCE_RE.search(prefix)
        ):
            previous_start = max((pos for pos in boundaries if pos < start), default=0)
            previous = [item for item in fragments if previous_start <= item.start < start]
            if len(previous) == 1 and not _VERBATIM_INTENT_RE.search(
                instructions[previous_start:start]
            ):
                spans.append((previous[0].start, previous[0].end))
    return spans


def _is_correction_target(
    fragment: ProtectedFragment, spans: list[tuple[int, int]]
) -> bool:
    """片段是否位于已确定的纠正对象范围。"""
    return any(
        start <= fragment.start and fragment.end <= end
        for start, end in spans
    )


def _apply_citation_eligibility(
    candidate: str, additional_sources: tuple[str, ...]
) -> tuple[str, tuple[ProtectionInconsistency, ...]]:
    """只按引用资格处理候选链接，不按清单顺序强制换链接。

    - 候选链接已在合法来源清单内 → 保留（不再换成另一个来源）。
    - 候选链接在清单外 → 记为不一致，交由调用方降级；资格清单无法证明
      论述与证据的对应关系，即使只剩一个来源也不能猜测换链接。
    """
    eligible = [source for source in additional_sources if source]
    if not eligible:
        return candidate, ()
    fragments = [
        fragment
        for fragment in compile_protected_fragments(candidate)
        if fragment.kind is FragmentKind.URL
    ]
    inconsistencies: list[ProtectionInconsistency] = []
    for fragment in fragments:
        if fragment.text in eligible:
            continue
        inconsistencies.append(
            ProtectionInconsistency(
                kind=InconsistencyKind.INELIGIBLE_CITATION,
                detail="候选引用了来源清单外的链接，不能据资格清单猜测证据关系。",
                fragment=fragment.text,
            )
        )
    return candidate, tuple(inconsistencies)


def _apply_verbatim(
    original: str,
    candidate: str,
    *,
    append_missing: bool,
    correction_spans: list[tuple[int, int]],
    require_missing: bool,
) -> tuple[str, tuple[ProtectionInconsistency, ...], tuple[tuple[str, str, str], ...]]:
    """按保留意图精确绑定原正文片段；缺失片段仅在任务要求时补尾。

    已定位到改动/计算子指令的片段是「允许纠正项」，不参与原样保留。
    """
    sources = [
        fragment
        for fragment in compile_protected_fragments(original)
        if not _is_correction_target(fragment, correction_spans)
    ]
    if not sources:
        return candidate, (), ()
    candidates = compile_protected_fragments(candidate)
    # 全文静态结构完全相同才允许绑定无标签的唯一槽位；不能仅凭数量
    # 或相似前缀假定位置可靠，也不能在流式尚未闭合时使用槽位。
    def skeleton(text: str) -> str:
        return _apply_replacements(text, [
            (item.start, item.end, f"<{item.kind.value}>")
            for item in compile_protected_fragments(text)
        ])

    reliable_positions = skeleton(original) == skeleton(candidate)

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
        pairs = _pair_fragments(
            kind_sources, kind_candidates, reliable_positions=reliable_positions
        )
        for source_index, source in enumerate(kind_sources):
            candidate_index = pairs.get(source_index)
            if candidate_index is None:
                ambiguous = any(
                    index not in pairs.values() and item.text != source.text
                    for index, item in enumerate(kind_candidates)
                )
                if not ambiguous:
                    missing.append(source)
                if ambiguous or not append_missing:
                    inconsistencies.append(
                        ProtectionInconsistency(
                            kind=(
                                InconsistencyKind.AMBIGUOUS_BINDING
                                if ambiguous
                                else InconsistencyKind.MISSING_FRAGMENT
                            ),
                            detail=(
                                "原样保留片段在候选正文中缺失，非任务必需时不强行补尾："
                                f"{source.text}"
                            ),
                            fragment=source.text,
                            required=require_missing,
                        )
                    )
                continue
            target = kind_candidates[candidate_index]
            bindings.append((kind.value, source.text, target.text))
            if target.text != source.text:
                replacements.append((target.start, target.end, source.text))

    restored = _apply_replacements(candidate, replacements)
    # 歧义不能通过追加旧值伪装成修复，只补完全缺失且任务明确要求的片段。
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
    semantic_check: bool = True,
) -> ProtectionResult:
    """规划候选正文的保护区恢复。

    ``original`` 是用户自有正文（受保护片段的权威来源），``candidate`` 是
    模型候选正文。``intent`` 为空时自动判定：普通解释/求值以输入为参考，
    保留请求约束原片段，纠正/计算只释放已定位的子指令对象；代码与引语内
    词语不构成授权。未识别的纯正文沿用保留模式；调用方已有任务合同时应
    显式传入 ``intent``。``additional_sources`` 只限定引用资格，不改换链接。
    ``append_missing`` 由任务合同决定，默认不强行补尾。``semantic_check``
    默认附带语义参考判断（只报告，不做替换、不设门）。
    """
    auto = intent is None
    resolved_intent = intent or detect_protection_intent(original)
    inconsistencies: list[ProtectionInconsistency] = []
    bindings: list[tuple[str, str, str]] = []

    content = candidate
    do_verbatim = bool(original) and resolved_intent is not ProtectionIntent.REFERENCE
    correction_spans: list[tuple[int, int]] = []
    if original and resolved_intent is ProtectionIntent.CORRECTION:
        if auto:
            # 自动判定为允许纠正：只释放已定位的子指令对象。
            correction_spans = _transform_spans(original)
        else:
            # 显式传入 CORRECTION：整段跳过原样保留。
            do_verbatim = False
    if do_verbatim:
        content, verbatim_inconsistencies, verbatim_bindings = _apply_verbatim(
            original,
            content,
            append_missing=append_missing,
            correction_spans=correction_spans,
            require_missing=(
                intent is ProtectionIntent.VERBATIM
                or _has_preservation_request(_instruction_text(original))
            ),
        )
        inconsistencies.extend(verbatim_inconsistencies)
        bindings.extend(verbatim_bindings)

    # 先按可靠对象恢复，再核验资格；用户自有链接不是搜索清单外的伪造来源。
    owner_urls = tuple(
        item.text for item in compile_protected_fragments(original)
        if item.kind is FragmentKind.URL
    )
    content, citation_inconsistencies = _apply_citation_eligibility(
        content, additional_sources + owner_urls if additional_sources else ()
    )
    inconsistencies.extend(citation_inconsistencies)

    semantic = assess_semantic_reference(original, content) if semantic_check else None
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


def _semantic_contexts(text: str, pattern: re.Pattern[str]) -> list[str]:
    """参考标记连同所在分句比较，避免对象互换后仅词频相同而漏报。"""
    return sorted(
        re.sub(r"\s+", "", clause)
        for clause in re.split(r"[，,。；;！？!?\n]", text)
        if pattern.search(clause)
    )


def assess_semantic_reference(source: str, candidate: str) -> SemanticReferenceReport:
    """比较源正文与候选正文的语义参考标记，只报告差异，不做替换。"""
    return SemanticReferenceReport(
        bare_number_changes=_rank_pairs(_bare_numbers(source), _bare_numbers(candidate)),
        chinese_unit_changes=_rank_pairs(_cjk_units(source), _cjk_units(candidate)),
        negation_changed=_semantic_contexts(source, _NEGATION_RE)
        != _semantic_contexts(candidate, _NEGATION_RE),
        condition_changed=_semantic_contexts(source, _CONDITION_RE)
        != _semantic_contexts(candidate, _CONDITION_RE),
        causal_changed=_semantic_contexts(source, _CAUSAL_RE)
        != _semantic_contexts(candidate, _CAUSAL_RE),
        conclusion_strength_changed=_semantic_contexts(source, _CONCLUSION_STRENGTH_RE)
        != _semantic_contexts(candidate, _CONCLUSION_STRENGTH_RE),
    )


__all__ = [
    "FACT_PROTECTION_PROTOCOL_VERSION",
    "FragmentKind",
    "InconsistencyKind",
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
