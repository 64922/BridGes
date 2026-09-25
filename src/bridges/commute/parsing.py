"""``route.parse``：从本轮消息与前文结构化提取起点、终点、方式，保留原话。

解析规则（``.scratch/bridges-v2/issues/12-campus-commute.md`` 编排合同）：

- **原话保留**：``origin_phrase`` / ``destination_phrase`` / ``mode_phrase`` 是
  用户在原文里的写法，绝不替换；
- **缺哪项只问哪项**：缺失或含糊时返回**一个**澄清问题，绝不一次问两项，
  也不替用户猜起点、终点或方式；
- **不猜坐标**：「我这里」等无法定位的指代只追问具体楼名或入口；
- **并列方式进入选择**：同一句里出现多种方式时列出候选请用户选一种。

上下文来源只有两处：本轮用户原文，以及调用方传入的**已确认会话前文**
（最近若干条用户消息）。仍不足以补全时问用户，而不是套用默认值。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime

from bridges.commute.contracts import (
    MISSING_DESTINATION,
    MISSING_DESTINATION_CHOICE,
    MISSING_DESTINATION_UNLOCATABLE,
    MISSING_MODE,
    MISSING_ORIGIN,
    MISSING_ORIGIN_CHOICE,
    MISSING_ORIGIN_UNLOCATABLE,
    MODE_LABELS,
    CommuteClarification,
    CommuteMode,
    CommutePlace,
    CommutePlaceCandidate,
    CommutePlaceRole,
    CommuteRequestAnalysis,
)
from bridges.commute.lexicon import (
    MODE_ALIAS_ORDER,
    PLACE_CONNECTORS,
    PLACE_STOPWORDS,
    UNLOCATABLE_MARKERS,
)
from bridges.contracts.modules import ModuleWaitState

#: 等待状态恢复载荷的稳定字段名（``ModuleWaitState.context`` 的键）。
PENDING_AWAITING = "awaiting"
PENDING_ORIGIN_PHRASE = "origin_phrase"
PENDING_DESTINATION_PHRASE = "destination_phrase"
PENDING_MODE = "mode"
PENDING_MODE_PHRASE = "mode_phrase"
PENDING_ORIGIN_PLACE = "origin_place"
PENDING_DESTINATION_PLACE = "destination_place"
PENDING_ORIGIN_CANDIDATES = "origin_candidates"
PENDING_DESTINATION_CANDIDATES = "destination_candidates"

#: 参与上下文补全的最近用户消息条数上限（够用即止，不把整段历史塞进解析）。
CONTEXT_LOOKBACK_MESSAGES = 6

#: ``从 A 到 B``（含「走到／骑到／前往」等连接词）。
_FROM_TO = re.compile(r"^从\s*(?P<a>.+?)\s*(?:到|去|往|走到|骑到|前往)\s*(?P<b>.+)$")
#: ``去 B`` / ``到 B`` / ``往 B``（只给出终点）。
_TO_ONLY = re.compile(r"^(?:去|到|往|前往|走到|骑到)\s*(?P<b>.+)$")
#: ``A 到 B``（顺序列出，无「从」）。
_A_TO_B = re.compile(r"^(?P<a>.+?)\s*(?:到|去|往|走到|骑到|前往)\s*(?P<b>.+)$")
#: ``从 A``（只给出起点）。
_FROM_ONLY = re.compile(r"^从\s*(?P<a>.+)$")

#: 句首主语／意图前缀（剥离后才能正确识别「去图书馆」这类只有终点的写法）。
_LEADING_INTENT = re.compile(r"^(?:我想|我要|我们|我|帮我|请|麻烦|想问一下|问一下|打算)+")

#: 需要从地点短语两端剥离的符号与助词。
_EDGE_TRIM = " \t，。！？、；：,.!?;:-—~～的了在把和与"

#: 指代判定：命中即视为无法定位（只追问，不猜坐标）。
_WHITESPACE = re.compile(r"\s+")


def parse_commute_request(
    content: str,
    *,
    prior_context: Sequence[str] = (),
    pending: ModuleWaitState | None = None,
    now: datetime | None = None,
) -> CommuteRequestAnalysis:
    """解析一轮通勤请求；缺失或含糊时返回单一澄清问题。

    ``pending`` 非空表示上一轮已提问、本轮 ``content`` 是该问题的回答：解析
    从等待处恢复（已确认的起点／终点／方式沿用等待状态里的记录），而不是把
    回答当成全新请求重新解析。
    """
    current = now or datetime.now(UTC)
    text = content.strip()
    if pending is not None and pending.kind == "clarification":
        return _resume_from_clarification(
            text, pending, prior_context=prior_context, now=current
        )
    return _parse_fresh(text, prior_context=prior_context, now=current)


# ---------------------------------------------------------------------------
# 全新请求
# ---------------------------------------------------------------------------


def _parse_fresh(
    text: str, *, prior_context: Sequence[str], now: datetime
) -> CommuteRequestAnalysis:
    mode, mode_phrase, mode_candidates, without_mode = detect_mode(text)
    origin_phrase, destination_phrase = extract_places(without_mode)
    if origin_phrase is None and destination_phrase is None:
        # 本轮没写成对的地点：用已确认前文补齐缺失的一项（仍不足以补全就问）。
        filled = _for_backfill(prior_context, now=now)
        origin_phrase = filled.origin_phrase
        destination_phrase = filled.destination_phrase
    return _analysis(
        raw_text=text,
        mode=mode,
        mode_phrase=mode_phrase,
        mode_candidates=mode_candidates,
        origin_phrase=origin_phrase,
        destination_phrase=destination_phrase,
        now=now,
    )


def _for_backfill(
    prior_context: Sequence[str], *, now: datetime
) -> CommuteRequestAnalysis:
    """从最近的前文里找一条能补齐地点的用户消息（只取最近一条，够用即止）。"""
    del now
    for message in reversed(list(prior_context)[-CONTEXT_LOOKBACK_MESSAGES:]):
        mode, mode_phrase, mode_candidates, without_mode = detect_mode(message)
        origin, destination = extract_places(without_mode)
        if origin is not None and destination is not None:
            return CommuteRequestAnalysis(
                raw_text=message,
                mode=mode,
                mode_phrase=mode_phrase,
                mode_candidates=mode_candidates,
                origin_phrase=origin,
                destination_phrase=destination,
                confidence=0.5,
            )
    return CommuteRequestAnalysis(raw_text="", confidence=0.0)


# ---------------------------------------------------------------------------
# 从等待状态恢复
# ---------------------------------------------------------------------------


def _resume_from_clarification(
    answer: str,
    pending: ModuleWaitState,
    *,
    prior_context: Sequence[str],
    now: datetime,
) -> CommuteRequestAnalysis:
    """把用户回答并回等待中的解析状态；仍缺项时再问一项。"""
    payload = pending.context
    awaiting = str(payload.get(PENDING_AWAITING) or "")
    origin_phrase = _payload_text(payload, PENDING_ORIGIN_PHRASE)
    destination_phrase = _payload_text(payload, PENDING_DESTINATION_PHRASE)
    mode_phrase = _payload_text(payload, PENDING_MODE_PHRASE)
    mode = _payload_mode(payload)
    origin_place = _payload_place(payload, PENDING_ORIGIN_PLACE, CommutePlaceRole.ORIGIN)
    destination_place = _payload_place(
        payload, PENDING_DESTINATION_PLACE, CommutePlaceRole.DESTINATION
    )
    mode_candidates: list[CommuteMode] = []

    fresh = _parse_fresh(answer, prior_context=prior_context, now=now)

    if awaiting in {MISSING_ORIGIN_CHOICE, MISSING_DESTINATION_CHOICE}:
        role = (
            CommutePlaceRole.ORIGIN
            if awaiting == MISSING_ORIGIN_CHOICE
            else CommutePlaceRole.DESTINATION
        )
        candidates = _payload_candidates(
            payload,
            PENDING_ORIGIN_CANDIDATES
            if role is CommutePlaceRole.ORIGIN
            else PENDING_DESTINATION_CANDIDATES,
        )
        chosen = match_candidate(answer, candidates)
        if chosen is not None:
            place = place_from_candidate(role, chosen, original_phrase=answer)
            if role is CommutePlaceRole.ORIGIN:
                origin_place = place
                origin_phrase = chosen.name
            else:
                destination_place = place
                destination_phrase = chosen.name
        else:
            # 回答没有对上任何候选（例如「不是，是北门」）：按新地点重新解析这一侧。
            phrase = _clean_place(answer) or answer.strip()
            if role is CommutePlaceRole.ORIGIN:
                origin_phrase, origin_place = phrase, None
            else:
                destination_phrase, destination_place = phrase, None
    elif awaiting in {MISSING_ORIGIN, MISSING_ORIGIN_UNLOCATABLE}:
        origin_phrase, origin_place = _answer_for_side(
            answer, fresh.origin_phrase, fresh.destination_phrase
        )
    elif awaiting in {MISSING_DESTINATION, MISSING_DESTINATION_UNLOCATABLE}:
        destination_phrase, destination_place = _answer_for_side(
            answer, fresh.destination_phrase, fresh.origin_phrase
        )
    elif awaiting == MISSING_MODE:
        if fresh.mode is not None:
            mode, mode_phrase = fresh.mode, fresh.mode_phrase
        elif len(fresh.mode_candidates) == 1:
            mode, mode_phrase = fresh.mode_candidates[0], fresh.mode_phrase
        elif len(fresh.mode_candidates) > 1:
            mode_candidates = fresh.mode_candidates
        # 回答里没有可识别的方式：保持缺失，下面再问一次（仍只问一项）。
    else:
        # 等待载荷不可用（构造异常）：按全新请求解析，避免把回答当垃圾吞掉。
        return _merge(fresh, origin_phrase=None, destination_phrase=None)

    return _analysis(
        raw_text=answer,
        mode=mode if mode_candidates == [] else None,
        mode_phrase=mode_phrase,
        mode_candidates=mode_candidates,
        origin_phrase=origin_phrase,
        destination_phrase=destination_phrase,
        origin_place=origin_place,
        destination_place=destination_place,
        now=now,
    )


def _answer_for_side(
    answer: str, same_side: str | None, other_side: str | None
) -> tuple[str | None, CommutePlace | None]:
    """把回答解释为某一侧的地点：优先用本次解析出的同侧短语，其次整句。"""
    if same_side is not None:
        return same_side, None
    if other_side is not None:
        # 回答本身是完整的「从 A 到 B」：另一侧交由上面的同侧判断处理。
        return answer.strip(), None
    return (_clean_place(answer) or answer.strip() or None), None


def _merge(
    fresh: CommuteRequestAnalysis,
    *,
    origin_phrase: str | None,
    destination_phrase: str | None,
) -> CommuteRequestAnalysis:
    return CommuteRequestAnalysis(
        raw_text=fresh.raw_text,
        mode=fresh.mode,
        mode_phrase=fresh.mode_phrase,
        mode_candidates=list(fresh.mode_candidates),
        origin_phrase=origin_phrase or fresh.origin_phrase,
        destination_phrase=destination_phrase or fresh.destination_phrase,
        confidence=fresh.confidence,
    )


# ---------------------------------------------------------------------------
# 方式
# ---------------------------------------------------------------------------


def detect_mode(
    text: str,
) -> tuple[CommuteMode | None, str | None, list[CommuteMode], str]:
    """识别方式：返回 (方式, 原话方式词, 并列候选, 去掉方式词后的文本)。

    同一句里并列出现多种方式时 ``方式`` 为 None、``候选`` 按出现顺序给出，
    由调用方进入澄清，不替用户选一种。
    """
    remaining = text
    hits: list[tuple[int, CommuteMode, str]] = []
    for alias, mode in MODE_ALIAS_ORDER:
        index = remaining.find(alias)
        while index >= 0:
            hits.append((index, mode, alias))
            remaining = remaining[:index] + " " * len(alias) + remaining[index + len(alias) :]
            index = remaining.find(alias)
    if not hits:
        return None, None, [], text
    ordered = sorted(hits, key=lambda item: item[0])
    modes: list[CommuteMode] = []
    for _, mode, _ in ordered:
        if mode not in modes:
            modes.append(mode)
    phrase = ordered[0][2]
    cleaned = _WHITESPACE.sub(" ", remaining).strip()
    if len(modes) > 1:
        return None, phrase, modes, cleaned
    return modes[0], phrase, [modes[0]], cleaned


# ---------------------------------------------------------------------------
# 地点
# ---------------------------------------------------------------------------


def extract_places(text: str) -> tuple[str | None, str | None]:
    """抽出 (起点原话, 终点原话)；识别不出的一侧为 None。"""
    stripped = _LEADING_INTENT.sub("", text).strip()
    match = _FROM_TO.match(stripped)
    if match is not None:
        return _clean_place(match.group("a")), _clean_place(match.group("b"))
    match = _TO_ONLY.match(stripped)
    if match is not None:
        return None, _clean_place(match.group("b"))
    match = _A_TO_B.match(stripped)
    if match is not None:
        return _clean_place(match.group("a")), _clean_place(match.group("b"))
    match = _FROM_ONLY.match(stripped)
    if match is not None:
        return _clean_place(match.group("a")), None
    return None, None


def _clean_place(phrase: str) -> str | None:
    """剥离意图词与连接词后的地点短语；无有效内容时返回 None。"""
    value = phrase
    for _ in range(4):
        before = value
        value = value.strip(_EDGE_TRIM)
        for word in sorted(PLACE_STOPWORDS, key=len, reverse=True):
            value = value.replace(word, " ")
        for word in PLACE_CONNECTORS:
            if value.startswith(word):
                value = value[len(word) :]
            if value.endswith(word):
                value = value[: -len(word)]
        value = _WHITESPACE.sub(" ", value).strip(_EDGE_TRIM)
        if value == before:
            break
    # 剥离过程会留下占位空格：中文地点名按无空格处理（含空格的名字会检索不到，
    # 那时按「找不到地点」请用户补充，而不是把空格当真实名称的一部分）。
    value = _WHITESPACE.sub("", value).strip(_EDGE_TRIM)
    if not value:
        return None
    return value


def _is_unlocatable(phrase: str) -> bool:
    return any(marker in phrase for marker in UNLOCATABLE_MARKERS)


# ---------------------------------------------------------------------------
# 组装解析结果与澄清
# ---------------------------------------------------------------------------


def _analysis(
    *,
    raw_text: str,
    mode: CommuteMode | None,
    mode_phrase: str | None,
    mode_candidates: list[CommuteMode],
    origin_phrase: str | None,
    destination_phrase: str | None,
    now: datetime,
    origin_place: CommutePlace | None = None,
    destination_place: CommutePlace | None = None,
) -> CommuteRequestAnalysis:
    del now
    origin_unlocatable = origin_phrase is not None and _is_unlocatable(origin_phrase)
    destination_unlocatable = destination_phrase is not None and _is_unlocatable(
        destination_phrase
    )
    analysis = CommuteRequestAnalysis(
        raw_text=raw_text,
        mode=mode,
        mode_phrase=mode_phrase,
        mode_candidates=list(mode_candidates),
        origin_phrase=origin_phrase,
        destination_phrase=destination_phrase,
        origin_unlocatable=origin_unlocatable,
        destination_unlocatable=destination_unlocatable,
        origin_place=origin_place,
        destination_place=destination_place,
        confidence=_confidence(
            mode=mode,
            origin_phrase=origin_phrase,
            destination_phrase=destination_phrase,
            has_any_text=bool(raw_text.strip()),
        ),
    )
    analysis.clarification = _clarification_for(analysis)
    return analysis


def _confidence(
    *,
    mode: CommuteMode | None,
    origin_phrase: str | None,
    destination_phrase: str | None,
    has_any_text: bool,
) -> float:
    present = sum(
        1 for item in (mode, origin_phrase, destination_phrase) if item is not None
    )
    if present == 3:
        return 0.9
    if present == 2:
        return 0.6
    if present == 1:
        return 0.4
    return 0.2 if has_any_text else 0.0


def _clarification_for(analysis: CommuteRequestAnalysis) -> CommuteClarification | None:
    """缺哪项只问哪项：固定顺序 起点 → 终点 → 方式（每轮只问一个问题）。"""
    if analysis.origin_place is None and (
        analysis.origin_unlocatable or analysis.origin_phrase is None
    ):
        return CommuteClarification(
            question=_place_question(
                CommutePlaceRole.ORIGIN,
                analysis.origin_phrase,
                why_unlocatable=analysis.origin_unlocatable,
            ),
            missing=(
                MISSING_ORIGIN_UNLOCATABLE if analysis.origin_unlocatable else MISSING_ORIGIN
            ),
            role=CommutePlaceRole.ORIGIN,
        )
    if analysis.destination_place is None and (
        analysis.destination_unlocatable or analysis.destination_phrase is None
    ):
        return CommuteClarification(
            question=_place_question(
                CommutePlaceRole.DESTINATION,
                analysis.destination_phrase,
                why_unlocatable=analysis.destination_unlocatable,
            ),
            missing=(
                MISSING_DESTINATION_UNLOCATABLE
                if analysis.destination_unlocatable
                else MISSING_DESTINATION
            ),
            role=CommutePlaceRole.DESTINATION,
        )
    if analysis.mode is None:
        if len(analysis.mode_candidates) > 1:
            labels = "、".join(MODE_LABELS[item] for item in analysis.mode_candidates)
            return CommuteClarification(
                question=f"你提到了{labels}两种方式，这次想用哪一种？（步行／自行车／电动车）",
                missing=MISSING_MODE,
            )
        return CommuteClarification(
            question="请告诉我这次怎么走：步行、自行车还是电动车？",
            missing=MISSING_MODE,
        )
    return None


def _place_question(
    role: CommutePlaceRole, phrase: str | None, *, why_unlocatable: bool
) -> str:
    label = "起点" if role is CommutePlaceRole.ORIGIN else "终点"
    if why_unlocatable and phrase:
        return (
            f"「{phrase}」这类指代我无法定位，也不会猜坐标；"
            f"请补充{label}的具体楼名或校门（例如「图书馆」「南门」）？"
        )
    return (
        f"本模块只规划华东交通大学校内以及校门到校内的路线，"
        f"请告诉我{label}（校内楼名或校门，例如「图书馆」「南门」）？"
    )


# ---------------------------------------------------------------------------
# 等待状态载荷与候选匹配
# ---------------------------------------------------------------------------


def pending_payload(
    analysis: CommuteRequestAnalysis,
    *,
    awaiting: str,
    origin_candidates: Sequence[CommutePlaceCandidate] = (),
    destination_candidates: Sequence[CommutePlaceCandidate] = (),
) -> dict[str, object]:
    """澄清等待状态的恢复载荷（全部为可序列化值，不含服务或私有材料副本）。"""
    return {
        PENDING_AWAITING: awaiting,
        PENDING_ORIGIN_PHRASE: analysis.origin_phrase,
        PENDING_DESTINATION_PHRASE: analysis.destination_phrase,
        PENDING_MODE: analysis.mode.value if analysis.mode is not None else None,
        PENDING_MODE_PHRASE: analysis.mode_phrase,
        PENDING_ORIGIN_PLACE: (
            analysis.origin_place.model_dump(mode="json")
            if analysis.origin_place is not None
            else None
        ),
        PENDING_DESTINATION_PLACE: (
            analysis.destination_place.model_dump(mode="json")
            if analysis.destination_place is not None
            else None
        ),
        PENDING_ORIGIN_CANDIDATES: [
            item.model_dump(mode="json") for item in origin_candidates
        ],
        PENDING_DESTINATION_CANDIDATES: [
            item.model_dump(mode="json") for item in destination_candidates
        ],
    }


def match_candidate(
    answer: str, candidates: Sequence[CommutePlaceCandidate]
) -> CommutePlaceCandidate | None:
    """把用户回答对上候选：支持序号（``2``／``第2个``）与名称包含。"""
    text = answer.strip()
    if not text or not candidates:
        return None
    digits = re.findall(r"\d+", text)
    for token in digits:
        index = int(token)
        if 1 <= index <= len(candidates):
            return candidates[index - 1]
    best: tuple[int, CommutePlaceCandidate] | None = None
    for candidate in candidates:
        name = candidate.name
        if (
            name
            and (name in text or text in name)
            and (best is None or len(name) > best[0])
        ):
            best = (len(name), candidate)
    return best[1] if best is not None else None


def place_from_candidate(
    role: CommutePlaceRole,
    candidate: CommutePlaceCandidate,
    *,
    original_phrase: str,
) -> CommutePlace:
    """把用户选定的候选固化为已解析地点（坐标沿用该候选的高德返回值）。"""
    return CommutePlace(
        role=role,
        original_phrase=original_phrase,
        query=candidate.name,
        name=candidate.name,
        location=candidate.location or "",
        address=candidate.address,
        poi_id=candidate.poi_id,
        district=candidate.district,
        campus_verified=candidate.campus,
        match_basis="用户从上一轮候选列表中选定，坐标沿用该候选的高德返回值。",
        unverified=[],
    )


def _payload_text(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value
    return None


def _payload_mode(payload: dict[str, object]) -> CommuteMode | None:
    value = payload.get(PENDING_MODE)
    if isinstance(value, str):
        try:
            return CommuteMode(value)
        except ValueError:
            return None
    return None


def _payload_place(
    payload: dict[str, object], key: str, role: CommutePlaceRole
) -> CommutePlace | None:
    value = payload.get(key)
    if not isinstance(value, dict):
        return None
    try:
        place = CommutePlace.model_validate(value)
    except ValueError:
        return None
    return place if place.role is role else place


def _payload_candidates(
    payload: dict[str, object], key: str
) -> list[CommutePlaceCandidate]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    candidates: list[CommutePlaceCandidate] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            candidates.append(CommutePlaceCandidate.model_validate(item))
        except ValueError:
            continue
    return candidates
