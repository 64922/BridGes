"""文本级事实锁引擎（Issue 28，确定性、可测试）。

从源文本/硬约束中确定性提取七类事实锁（数值、单位、对象关系、限定条件、
公式、引用、结论强度），对改写前后文本做规范化比较，输出
``FactLockCheckResult``。冲突按严重度分级：阻断冲突必须停止或人工确认，
需人工事项明确标注披露。本模块不调用模型，保证规则可复现、可测试。
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterable
from dataclasses import dataclass

from bridges.contracts.humanizer import (
    FactLockCheckResult,
    FactLockEntry,
    FactLockKind,
    FactLockSeverity,
    FactLockStatus,
)

# ---------------------------------------------------------------------------
# 规范化
# ---------------------------------------------------------------------------

_FULLWIDTH = str.maketrans(
    "０１２３４５６７８９．，：；（）％",
    "0123456789.,:;()%",
)
_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-")
_SUBSCRIPT = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")


def _normalize_text(value: str) -> str:
    """全半角与上下标统一、空白折叠，便于正则与比较。"""
    value = value.translate(_FULLWIDTH).translate(_SUPERSCRIPT).translate(_SUBSCRIPT)
    return re.sub(r"\s+", " ", value.strip())


def _canonical_number(value: str) -> str:
    """数值规范化：小数尾零去掉、范围连接符统一（25.0 ≡ 25；8-10 ≡ 8 到 10）。"""
    value = value.replace("－", "-").replace("—", "-")
    value = re.sub(r"到|至", "-", value)
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"(\d+\.\d*?)0+($|\D)", r"\1\2", value)
    value = re.sub(r"\.$", "", value)
    return value


def _canonical_unit(value: str) -> str:
    """单位规范化：只保留字母与汉字，忽略分隔符与指数位。

    呈现形式等价（μmol·m⁻²·s⁻¹ 与 μmol/(m²·s)）归并为同一键；真正不同
    的单位（°C 与 K、s 与 h）保持区分。
    """
    value = _normalize_text(value)
    value = re.sub(r"[^A-Za-z一-鿿]", "", value)
    return value.lower()


def _canonical_text_key(value: str) -> str:
    """普通文本键：去标点空白与语气助词后小写（了/着/过等不影响语义）。"""
    value = _normalize_text(value)
    value = re.sub(r"[了着过地得]", "", value)
    return re.sub(r"[^\w一-鿿-]", "", value.lower())


def _canonical_formula(value: str) -> str:
    """公式规范化：去空白/乘点、箭头统一。"""
    value = _normalize_text(value)
    value = re.sub(r"[·•×⁻±\s]", "", value)
    value = re.sub(r"→|->|⇒", ">", value)
    value = value.replace("⇌", "=")
    return value.lower()


def _canonical_key(
    kind: FactLockKind, surface: str, *, number: str = "", unit: str = ""
) -> str:
    """事实锁的规范化可比键。"""
    if kind == FactLockKind.NUMBER:
        return f"num:{_canonical_number(surface)}"
    if kind == FactLockKind.UNIT:
        return f"unit:{_canonical_number(number)}|{_canonical_unit(unit)}"
    if kind == FactLockKind.FORMULA:
        return f"formula:{_canonical_formula(surface)}"
    if kind == FactLockKind.CITATION:
        return f"cite:{_canonical_text_key(surface)}"
    if kind == FactLockKind.QUALIFIER:
        return f"qual:{_canonical_text_key(surface)}"
    if kind == FactLockKind.CONCLUSION_STRENGTH:
        return f"strength:{_canonical_text_key(surface)}"
    if kind == FactLockKind.OBJECT_RELATION:
        return f"rel:{_canonical_text_key(surface)}"
    return f"other:{_canonical_text_key(surface)}"


# ---------------------------------------------------------------------------
# 提取正则（统一在 _normalize_text 之后运行，token 全部使用 ASCII 化写法）
# ---------------------------------------------------------------------------

# 顺序契约：长 token 必须排在短 token 之前（μmol·m⁻²·s⁻¹ 复合体在 μmol 前、
# mmol·L⁻¹ 在 mmol 前、min 在 m 前），调整顺序会静默改变提取结果——由
# tests/humanizer/test_factlock.py 的固定语料锁定行为。
_UNIT_TOKENS = (
    r"μmol·?m[-0-9]*·?s[-0-9]*|μmol·?m[-0-9]*|mol·?L[-0-9]*|mmol·?L[-0-9]*|"
    r"μg|mg|g|kg|t|mL|L|μL|hPa|kPa|MPa|GPa|Pa|Hz|kHz|MHz|GHz|°C|℃|℉|K|%|‰|"
    r"eV|keV|MeV|GeV|kJ|MJ|J|kW|MW|W|kWh|Wh|mV|kV|V|mA|μA|A|kΩ|MΩ|Ω|"
    r"mol|mmol|μmol|kmol|cm|mm|nm|μm|km|m|ms|min|h|d|"
    r"万|亿|千|百|个|人|年|月|周|天|小时|分钟|秒|倍|次|例|篇|份|项|位|组|元|岁|字"
)

_NUMBER_CORE = r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?:\s*[-–至到]\s*\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)?"
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9一-鿿])(" + _NUMBER_CORE + r")")
# 单位 token 后允许吸收单位片段（如 μmol/(m²·s)、μmol·m⁻²·s⁻¹ 的余部），
# 使呈现形式等价的不同写法归并为同一键。
_NUMBER_UNIT_RE = re.compile(
    r"(?<![A-Za-z0-9一-鿿])(" + _NUMBER_CORE + r")\s*(" + _UNIT_TOKENS + r")"
    r"([A-Za-zμΩ°℃℉%0-9\-()/·（）\s]+)?(?![A-Za-z0-9])"
)
_FRACTION_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(一半|三分之一|三分之二|四分之一|半数|绝大部分|绝大多数|少数|多数)"
    r"(?![A-Za-z0-9])"
)

# 化学式实体：系数 + 一个或多个元素 token（CO₂ = C+O+2、H₂O = H+2+O）
_CHEM_ELEMENT = r"(?:\d+\s*)?(?:[A-Z][a-z]?\d*)+"
_FORMULA_RE = re.compile(
    r"(?:" + _CHEM_ELEMENT + r"(?:\s*\+\s*" + _CHEM_ELEMENT + r")*\s*(?:→|->|⇌|=|≈)\s*"
    r"" + _CHEM_ELEMENT + r"(?:\s*\+\s*" + _CHEM_ELEMENT + r")*)"
    r"|\b[A-Za-z_][A-Za-z0-9_]*\s*=\s*[A-Za-z0-9_+*/()\-.\s]+"
)

_CITATION_RE = re.compile(
    r"(?:[（(][^（）()]{0,40}?(?:19|20)\d{2}[）)]"
    r"|参见[^。；]{0,30}?(?:19|20)\d{2}"
    r"|\[(?:\d+(?:[-–,，]\d+)*)\]"
    r"|\b[A-Z][a-z]+\s+et\s+al\.,?\s*(?:19|20)\d{2})"
)

_QUALIFIER_RE = re.compile(
    r"(?<![A-Za-z0-9])("
    r"约|大约|接近|左右|上下|将近|可能|或许|也许|大致|大概|部分|某些|个别|"
    r"通常|一般|往往|基本|主要|初步|目前|暂时|尚|仍|仅|只|至少|至多|不超过|"
    r"限于|局限于|只能|不能直接|尚不能|还需|有待|预计|据估计|据统计|初步结果|"
    r"在[^。；，]{1,24}?条件下|当[^。；，]{1,24}?时|随着[^。；，]{1,24}?的?(?:增加|减少|变化)|"
    r"仅适用于|仅限|仅对"
    r")(?![A-Za-z0-9])"
)

_STRENGTH_RE = re.compile(
    r"(?<![A-Za-z0-9])("
    r"证明|证实|确证|必然|毫无疑问|决定性|"
    r"表明|显示|说明|揭示|验证|支持[^。；，]{0,12}?判断|"
    r"可能|或许|推测|假设|倾向于|有待验证|尚不能证明|初步支持"
    r")(?![A-Za-z0-9])"
)

_RELATION_RE = re.compile(
    r"([一-鿿A-Za-z]{2,18}?)"
    r"(?:显著|明显|急剧|缓慢|直接|间接|长期|短期)?"
    r"(影响|导致|引起|促进|抑制|降低|增加|提高|改变|决定|减缓|加速|诱发|"
    r"超过|下降|上升|升高|催化|与[^。；，]{1,16}?相关)"
    r"([\s一-鿿A-Za-z0-9]{1,20})"
)

# 结论强度分级（用于升级检测）
_STRENGTH_TIER: list[tuple[str, tuple[str, ...]]] = [
    ("strong", ("证明", "证实", "确证", "必然", "毫无疑问", "决定性")),
    ("medium", ("表明", "显示", "说明", "揭示", "验证", "支持")),
    ("weak", ("可能", "或许", "推测", "假设", "倾向于", "有待验证", "尚不能证明", "初步")),
]

# 对象关系反义映射（方向反转用于阻断检测）
_RELATION_OPPOSITES: dict[str, str] = {
    "影响": "不影响",
    "导致": "防止",
    "引起": "避免",
    "促进": "抑制",
    "抑制": "促进",
    "降低": "增加",
    "增加": "降低",
    "提高": "降低",
    "减缓": "加速",
    "加速": "减缓",
}

_RELATION_VERBS = (
    "影响|导致|引起|促进|抑制|降低|增加|提高|改变|决定|减缓|加速|诱发"
)

_KIND_LABEL_CN: dict[str, str] = {
    "number": "数值",
    "unit": "数值与单位",
    "object_relation": "对象关系",
    "qualifier": "限定条件",
    "formula": "公式",
    "citation": "引用",
    "conclusion_strength": "结论强度",
}


def _strength_tier(word: str) -> str | None:
    """结论强度分级：最长词优先，避免「证明」误判「尚不能证明」。"""
    candidates = [(w, tier) for tier, words in _STRENGTH_TIER for w in words]
    candidates.sort(key=lambda c: len(c[0]), reverse=True)
    for w, tier in candidates:
        if w in word:
            return tier
    return None


def _tier_rank(tier: str) -> int:
    return {"weak": 0, "medium": 1, "strong": 2}.get(tier, 1)


def _relation_direction_flipped(before_surface: str, after_surface: str) -> bool:
    """对象关系方向是否反转（阻断）。"""
    before_verb = re.search(f"({_RELATION_VERBS})", before_surface)
    after_verb = re.search(f"({_RELATION_VERBS})", after_surface)
    if before_verb is None or after_verb is None:
        return False
    return _RELATION_OPPOSITES.get(before_verb.group(1), "") == after_verb.group(1)


@dataclass
class _LockDraft:
    kind: FactLockKind
    surface: str
    canonical: str
    note: str = ""


def _masked_text(normalized: str, matches: Iterable[re.Match[str]]) -> str:
    """把公式/引用匹配跨度替换为空格，避免其内部数字产生独立数值锁。"""
    chars = list(normalized)
    for match in matches:
        for index in range(match.start(), match.end()):
            chars[index] = " "
    return "".join(chars)


def extract_locks(text: str) -> list[_LockDraft]:
    """从一段文本确定性提取全部事实锁草稿（去重后）。"""
    normalized = _normalize_text(text)
    drafts: list[_LockDraft] = []
    seen: set[str] = set()

    def add(kind: FactLockKind, surface: str, canonical: str, note: str) -> None:
        if canonical in seen:
            return
        seen.add(canonical)
        drafts.append(_LockDraft(kind, surface, canonical, note))

    # 公式与引用先提取，并在数值提取前掩码其跨度
    formula_matches = list(_FORMULA_RE.finditer(normalized))
    citation_matches = list(_CITATION_RE.finditer(normalized))
    for match in formula_matches:
        surface = match.group(0).strip()
        add(
            FactLockKind.FORMULA,
            surface,
            _canonical_key(FactLockKind.FORMULA, surface),
            "公式锁定：变量、系数与反应方向不得改写。",
        )
    for match in citation_matches:
        surface = match.group(0).strip()
        add(
            FactLockKind.CITATION,
            surface,
            _canonical_key(FactLockKind.CITATION, surface),
            "引用锁定：作者-年份/编号不得删改；新增引用须有来源。",
        )
    numbers_text = _masked_text(normalized, formula_matches + citation_matches)

    # 数值+单位（合并为 UNIT 锁：数值与单位共同受保护；单位片段如
    # 「/(m²·s)」「·m⁻²·s⁻¹」一并纳入表面与规范化键）
    unit_matches = list(_NUMBER_UNIT_RE.finditer(numbers_text))
    for match in unit_matches:
        number, unit = match.group(1), match.group(2)
        unit = (unit + (match.group(3) or "")).strip()
        surface = f"{number} {unit}"
        add(
            FactLockKind.UNIT,
            surface,
            _canonical_key(FactLockKind.UNIT, surface, number=number, unit=unit),
            "数值与单位共同锁定：改写不得改变数值或更换单位。",
        )

    # 无单位的数值（计数/编号等仍受保护；单位串内部数字先掩码）
    numbers_text = _masked_text(numbers_text, unit_matches)
    for match in _NUMBER_RE.finditer(numbers_text):
        number = match.group(1)
        add(
            FactLockKind.NUMBER,
            number,
            _canonical_key(FactLockKind.NUMBER, number),
            "数值锁定：不得改动（含小数位与量级）。",
        )

    # 中文分数/比例词
    for match in _FRACTION_RE.finditer(numbers_text):
        word = match.group(1)
        add(
            FactLockKind.NUMBER,
            word,
            _canonical_key(FactLockKind.NUMBER, word),
            "比例表述锁定。",
        )

    # 限定条件
    for match in _QUALIFIER_RE.finditer(normalized):
        surface = match.group(1).strip()
        add(
            FactLockKind.QUALIFIER,
            surface,
            _canonical_key(FactLockKind.QUALIFIER, surface),
            "限定条件锁定：删除或弱化需人工确认。",
        )

    # 结论强度
    for match in _STRENGTH_RE.finditer(normalized):
        surface = match.group(1).strip()
        add(
            FactLockKind.CONCLUSION_STRENGTH,
            surface,
            _canonical_key(FactLockKind.CONCLUSION_STRENGTH, surface),
            "结论强度锁定：升级视为阻断冲突。",
        )

    # 对象关系
    for match in _RELATION_RE.finditer(normalized):
        subject, verb, obj = match.group(1), match.group(2), match.group(3)
        surface = f"{subject}{verb}{obj}"
        add(
            FactLockKind.OBJECT_RELATION,
            surface,
            _canonical_key(FactLockKind.OBJECT_RELATION, surface),
            "对象关系锁定：方向与对象不得改变。",
        )

    return drafts


def _severity_for_change(
    kind: FactLockKind, before: _LockDraft, after: _LockDraft | None
) -> FactLockSeverity:
    """按类别判定变更严重度。"""
    if kind in (FactLockKind.NUMBER, FactLockKind.UNIT, FactLockKind.FORMULA):
        return FactLockSeverity.BLOCKING
    if kind == FactLockKind.CONCLUSION_STRENGTH:
        if after is not None:
            before_tier = _strength_tier(before.surface)
            after_tier = _strength_tier(after.surface)
            if (
                before_tier is not None
                and after_tier is not None
                and _tier_rank(after_tier) > _tier_rank(before_tier)
            ):
                return FactLockSeverity.BLOCKING
        return FactLockSeverity.NEEDS_HUMAN
    if kind == FactLockKind.OBJECT_RELATION:
        return FactLockSeverity.BLOCKING
    if kind in (FactLockKind.CITATION, FactLockKind.QUALIFIER):
        return FactLockSeverity.NEEDS_HUMAN
    return FactLockSeverity.NEEDS_HUMAN


def compare_locks(
    before_text: str, after_text: str, *, source_label: str = "改写前后"
) -> FactLockCheckResult:
    """对改写前后文本做事实锁比较（改写路径）。"""
    before_drafts = extract_locks(before_text)
    after_drafts = extract_locks(after_text)
    before_by_key = {d.canonical: d for d in before_drafts}
    after_by_key = {d.canonical: d for d in after_drafts}
    matched_after: set[str] = set()

    entries: list[FactLockEntry] = []
    blocking: list[str] = []
    needs_human: list[str] = []

    for key, before in before_by_key.items():
        after = after_by_key.get(key)
        if after is not None:
            matched_after.add(key)
            entries.append(
                FactLockEntry(
                    entry_id=f"fl-{secrets.token_urlsafe(8)}",
                    kind=before.kind,
                    surface_before=before.surface,
                    surface_after=after.surface,
                    canonical=key,
                    status=FactLockStatus.PRESERVED,
                    severity=FactLockSeverity.INFO,
                    note=before.note,
                )
            )
            continue
        changed_after = _find_partial_match(before, after_by_key, matched_after)
        status = (
            FactLockStatus.REMOVED if changed_after is None else FactLockStatus.CHANGED
        )
        surface_after = changed_after.surface if changed_after is not None else None
        if changed_after is not None:
            matched_after.add(changed_after.canonical)
        severity = _severity_for_change(before.kind, before, changed_after)
        kind_label = _KIND_LABEL_CN.get(before.kind.value, before.kind.value)
        note = f"[{kind_label}] 原文「{before.surface}」在结果中未保持"
        if changed_after is not None:
            note += f"，被改写为「{changed_after.surface}」"
            if (
                before.kind == FactLockKind.CONCLUSION_STRENGTH
                and _severity_for_change(before.kind, before, changed_after)
                == FactLockSeverity.BLOCKING
            ):
                note += "（结论强度被升级）"
            elif before.kind == FactLockKind.OBJECT_RELATION and _relation_direction_flipped(
                before.surface, changed_after.surface
            ):
                note += "（对象关系方向反转）"
        note += "。"
        entries.append(
            FactLockEntry(
                entry_id=f"fl-{secrets.token_urlsafe(8)}",
                kind=before.kind,
                surface_before=before.surface,
                surface_after=surface_after,
                canonical=key,
                status=status,
                severity=severity,
                note=note,
            )
        )
        if severity == FactLockSeverity.BLOCKING:
            blocking.append(note)
        else:
            needs_human.append(note)

    # 新增事实锁（原文没有、结果新增）——不阻断，但提示须人工核对来源
    for key, after in after_by_key.items():
        if key in before_by_key or key in matched_after:
            continue
        entries.append(
            FactLockEntry(
                entry_id=f"fl-{secrets.token_urlsafe(8)}",
                kind=after.kind,
                surface_before=None,
                surface_after=after.surface,
                canonical=key,
                status=FactLockStatus.ADDED,
                severity=FactLockSeverity.INFO,
                note=f"结果新增「{after.surface}」：未经原文事实锁覆盖，须人工核对来源。",
            )
        )

    return FactLockCheckResult(
        check_id=f"flc-{secrets.token_urlsafe(8)}",
        source_text=source_label,
        entries=entries,
        blocking_conflicts=blocking,
        needs_human=needs_human,
        passed=not blocking,
    )


def _find_partial_match(
    before: _LockDraft,
    after_by_key: dict[str, _LockDraft],
    matched_after: set[str],
) -> _LockDraft | None:
    """在同一类别内查找被改写（键不同）的条目，用于给出更准确的冲突说明。

    候选必须与原文表面形式共享子串（如「25 μmol·m⁻²·s⁻¹」→「30 μmol·m⁻²·s⁻¹」、
    「尚不能证明」→「证明」），避免把无关的同类别条目误配成「改写」。
    """
    for key, after in after_by_key.items():
        if key in matched_after:
            continue
        if after.kind != before.kind:
            continue
        # 数值+单位类：单位部分一致而数值变化（25→30 μmol·m⁻²·s⁻¹）判为改写
        if after.kind == FactLockKind.UNIT:
            before_unit = (
                before.surface.split(" ", 1)[1] if " " in before.surface else before.surface
            )
            after_unit = (
                after.surface.split(" ", 1)[1] if " " in after.surface else after.surface
            )
            if _canonical_unit(before_unit) == _canonical_unit(after_unit):
                return after
            continue
        if (
            before.surface in after.surface
            or after.surface in before.surface
            or _canonical_text_key(before.surface)[:6]
            == _canonical_text_key(after.surface)[:6]
        ):
            return after
    return None


_NEGATION_PREFIX_RE = re.compile(
    r"^(?:不得|不要|禁止|不应|不能|不可以|不允许|切勿|严禁|勿|别)"
    r"(?:声称|宣称|说|写|写道|写为|表述为|写成|把[^。；，]{0,10}?写)?"
)
# 正向约束包装词（剥离后检查实质内容，如「必须保留…」「请给出…」）
_POSITIVE_WRAPPER_RE = re.compile(
    r"^(?:必须|需要|需|请|应|要)(?:给出|包含|保留|写清|体现|列出|包括|呈现)?"
)
# 约束中的括号解释不影响实质（如「典型睡眠时长（数值+单位）」）
_PARENTHETICAL_RE = re.compile(r"[（(][^（）()]*[）)]")

# 长度硬约束：可数值化判定（「全文不超过 1200 字」「至少 500 字」）
_LENGTH_CAP_RE = re.compile(r"(?:不超过|至多|少于|小于|不高于|不能超过)\s*(\d+)\s*(?:字|字符)")
_LENGTH_FLOOR_RE = re.compile(r"(?:不少于|至少|多于|大于|高于)\s*(\d+)\s*(?:字|字符)")


def check_requirements(
    constraints: Iterable[str], result_text: str, *, source_label: str = "硬约束"
) -> FactLockCheckResult:
    """生成路径：把硬约束编译为必须保留（或禁止出现）的事实锁。

    - 正向约束（「必须保留 X」）：限定条件、引用、结论强度与对象关系必须
      在结果中以相同规范化键出现；无法解析为事实锁的自由文本按整体包含
      判定。数值/单位约束属于「必须给出/不得超过」类要求，由体裁规则与
      长度检查覆盖，此处不判缺失。
    - 否定式约束（「不得声称/不要写 X」）：剥离否定前缀后提取的事实锁
      若在结果中出现即视为违背硬约束（阻断）。
    """
    normalized_result = _normalize_text(result_text)
    result_keys = {d.canonical for d in extract_locks(normalized_result)}

    entries: list[FactLockEntry] = []
    blocking: list[str] = []
    needs_human: list[str] = []
    seen: set[str] = set()

    for raw in constraints:
        raw = raw.strip()
        if not raw:
            continue
        # 长度硬约束按数值判定，不进入事实锁比较
        length_entry = _length_constraint_entry(raw, result_text)
        if length_entry is not None:
            entries.append(length_entry)
            if length_entry.severity == FactLockSeverity.BLOCKING:
                blocking.append(length_entry.note)
            elif length_entry.severity == FactLockSeverity.NEEDS_HUMAN:
                needs_human.append(length_entry.note)
            continue
        negation = _NEGATION_PREFIX_RE.match(raw)
        content = _NEGATION_PREFIX_RE.sub("", raw, count=1).strip()
        if not negation:
            content = _POSITIVE_WRAPPER_RE.sub("", content, count=1).strip()
        content = _PARENTHETICAL_RE.sub("", content).strip()
        drafts = [
            d
            for d in extract_locks(content)
            if d.kind not in (FactLockKind.NUMBER, FactLockKind.UNIT)
        ]
        if not drafts:
            # 无法解析为事实锁的自由文本：按整体包含判定
            canonical = _canonical_text_key(content)
            if canonical in seen:
                continue
            seen.add(canonical)
            present = canonical in _canonical_text_key(normalized_result)
            violated = present if negation else not present
            note = (
                f"约束「{content}」在结果中"
                + (
                    "出现（违背禁止性约束）"
                    if negation and present
                    else "保持"
                    if present
                    else "未体现"
                )
            )
            if violated:
                # 否定式约束违背阻断；正向自由文本约束无法确定性验证时标注人工确认
                if negation:
                    blocking.append(f"硬约束「{raw}」未得到遵守：" + note + "。")
                else:
                    needs_human.append(f"硬约束「{raw}」未得到遵守：" + note + "。")
            entries.append(
                FactLockEntry(
                    entry_id=f"fl-{secrets.token_urlsafe(8)}",
                    kind=FactLockKind.QUALIFIER,
                    surface_before=raw,
                    surface_after=None if violated else content,
                    canonical=canonical,
                    status=FactLockStatus.REMOVED if violated else FactLockStatus.PRESERVED,
                    severity=FactLockSeverity.BLOCKING if violated else FactLockSeverity.INFO,
                    note=note + "。",
                )
            )
            continue
        for draft in drafts:
            if draft.canonical in seen:
                continue
            seen.add(draft.canonical)
            present = draft.canonical in result_keys
            violated = present if negation else not present
            note = (
                f"约束「{draft.surface}」在结果中"
                + (
                    "出现（违背禁止性约束）"
                    if negation and present
                    else "保持"
                    if present
                    else "未体现"
                )
            )
            if violated:
                if negation or draft.kind in (
                    FactLockKind.CONCLUSION_STRENGTH,
                    FactLockKind.OBJECT_RELATION,
                ):
                    blocking.append(f"硬约束「{raw}」未得到遵守：" + note + "。")
                else:
                    needs_human.append(f"硬约束「{raw}」未得到遵守：" + note + "。")
            entries.append(
                FactLockEntry(
                    entry_id=f"fl-{secrets.token_urlsafe(8)}",
                    kind=draft.kind,
                    surface_before=raw,
                    surface_after=None if violated else draft.surface,
                    canonical=draft.canonical,
                    status=FactLockStatus.REMOVED if violated else FactLockStatus.PRESERVED,
                    severity=(
                        FactLockSeverity.BLOCKING
                        if violated
                        else FactLockSeverity.INFO
                    ),
                    note=note + "。",
                )
            )

    return FactLockCheckResult(
        check_id=f"flc-{secrets.token_urlsafe(8)}",
        source_text=source_label,
        entries=entries,
        blocking_conflicts=blocking,
        needs_human=needs_human,
        passed=not blocking,
    )


def _length_constraint_entry(
    raw: str, result_text: str
) -> FactLockEntry | None:
    """把长度硬约束数值化判定为一条事实锁条目（不适用返回 None）。"""
    cap = _LENGTH_CAP_RE.search(raw)
    floor = _LENGTH_FLOOR_RE.search(raw)
    if cap is None and floor is None:
        return None
    match = cap if cap is not None else floor
    assert match is not None
    limit = int(match.group(1))
    actual = len(_normalize_text(result_text))
    satisfied = actual <= limit if cap is not None else actual >= limit
    note = (
        f"长度约束「{raw}」满足（当前约 {actual} 字）。"
        if satisfied
        else (
            f"长度约束「{raw}」未满足：当前约 {actual} 字，"
            f"要求{'不超过' if cap else '至少'} {limit} 字。"
        )
    )
    return FactLockEntry(
        entry_id=f"fl-{secrets.token_urlsafe(8)}",
        kind=FactLockKind.QUALIFIER,
        surface_before=raw,
        surface_after=None if not satisfied else raw,
        canonical=f"length:{_canonical_text_key(raw)}",
        status=FactLockStatus.PRESERVED if satisfied else FactLockStatus.REMOVED,
        severity=FactLockSeverity.INFO if satisfied else FactLockSeverity.BLOCKING,
        note=note,
    )


def surface_summary(text: str) -> list[str]:
    """人类可读的事实锁清单（测试断言与结果详情用）。"""
    return [f"[{draft.kind.value}] {draft.surface}" for draft in extract_locks(text)]


__all__ = [
    "extract_locks",
    "compare_locks",
    "check_requirements",
    "surface_summary",
    "_LockDraft",
]
