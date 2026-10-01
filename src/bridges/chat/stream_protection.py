"""Issue 06：统一流式正文、终态存储与断线重放（append-only delta）。

后端此前把整段「保护区恢复」结果当 delta 下发：只要恢复改动了已发送的
前缀，`protected_content` 就不再以已发送正文为前缀，调用方只能把整段正文
重新当作增量发出，前端持续追加后屏幕正文与落库正文分叉。本模块提供唯一的
**追加式正文装配器**，在流式期间保证 ``delta`` 只表示可追加的新内容：

- 终态正文、落库正文与重放正文都由同一条 delta 序列拼成（逐字一致）；
- 与某个源片段「同类 + 同来源哈希 + 同对象锚点」的候选片段已确认不再改动，
  可立即追加；
- 仍需替换的候选片段、可能继续增长的片段（URL / 带单位数值）、未闭合的
  片段起始符以及会被降级前处理剥离的未闭合引用记号一律**暂缓**到闭合或
  终态确认后再输出（``实施方案`` §6：闭合确认前缓冲片段）；
- 不引入第二条并行重写通道，也不新增 SSE 事件类型，历史事件仍按原样可读。

本模块只做确定性缓冲与追加，不调用模型；协议版本
:data:`STREAM_CONSISTENCY_PROTOCOL_VERSION` 记录本次正文事件协议语义。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bridges.chat.fact_protection import (
    FragmentKind,
    ProtectedFragment,
    ProtectionIntent,
    ProtectionResult,
    binding_sources,
    compile_protected_fragments,
    plan_fragment_protection,
    same_fragment_identity,
)

#: 流式正文事件协议版本：delta 恒为追加，终态/重放/落库由同一序列拼成。
STREAM_CONSISTENCY_PROTOCOL_VERSION = "append-only-delta-v1"

#: 会随后续字符继续增长、不能只凭「已匹配」就认为闭合的片段类别。
_GROWING_KINDS = frozenset({FragmentKind.URL, FragmentKind.NUMBER_WITH_UNIT})

#: 尾部「可能成为片段」的起始位置正则（均锚定在字符串末尾，:func:`re.search`
#: 返回最靠左的匹配，即最早可能仍在增长的片段起点）。
#: 1) URL 方案前缀：h / ht / … / http: / http:/ / http:// / https://（单斜杠也是
#:    合法中间态，必须暂缓，否则整段 URL 随后被替换时会破坏追加式协议）。
_URL_SCHEME_PREFIX_RE = re.compile(r"h(?:t(?:t(?:p(?:s)?)?)?)?(?::(?:\/?\/?)?)?$")
#: 2) 带单位数值前缀：数字（可带小数点、尾随空白与部分单位字符）。带单位数值
#:    允许数字与单位之间有空格，故「12」「12.」「12 」「12 m」都可能继续长成
#:    「12 m/s」，一律暂缓。
_NUMBER_UNIT_PREFIX_RE = re.compile(
    r"(?<!\w)\d+(?:\.\d*)?[ \t]*(?:[A-Za-zμΩ%°][^\s，。；;)]*)?$"
)
#: 3) 未闭合的方括号记号前缀（如 ``[web-``、``[arxiv-``、``[reference:1``）。
#:    降级学习路径的确定性前处理会在记号补全后整段剥离网络/论文引用与越界
#:    本地引用；未闭合部分必须先暂缓，否则补全时已发前缀被改短。
_OPEN_TOKEN_TAIL_RE = re.compile(r"\[[^\]\n]*$")


#: 可能开启受保护片段的字符（代码围栏/行内代码/公式/JSON/引用）。
_OPENER_CHARS = "`${["


def _is_exact_source_match(
    fragment: ProtectedFragment, sources: list[ProtectedFragment]
) -> bool:
    """候选片段是否与某个源片段逐字一致（同类 + 同哈希 + 同锚点）。

    这类片段是 Issue 05 三段式配对中的第 1 段精确匹配：既不会被替换，也不会
    被后续片段抢走配对，因此可以在流式期间立即追加。判定与
    :func:`_pair_fragments` 共用 :func:`same_fragment_identity`，避免两处口径漂移。
    """
    return any(same_fragment_identity(fragment, source) for source in sources)


def _earliest_uncovered_opener(
    candidate: str, confirmed_spans: list[tuple[int, int]]
) -> int | None:
    """最早「不在已确认逐字保留片段内」的片段起始符下标（无则 ``None``）。

    只凭片段识别的结果不足以判断闭合：跨块时同一段文本可能先被识别成代码围栏
    （如 ``...```...`` ），随后才被更大的行内代码吸收，使「片段起点」在增长过程
    中前后移动。因此只要某个起始符不在**已确认逐字保留**的片段内，就可能在
    后续被改写或改变归属，必须自该处起暂缓追加。逐起始符前进，复杂度只与
    起始符数量相关。
    """
    index = 0
    length = len(candidate)
    while index < length:
        positions = [
            position
            for position in (candidate.find(char, index) for char in _OPENER_CHARS)
            if position != -1
        ]
        if not positions:
            return None
        index = min(positions)
        covered = next(
            (
                (start, end)
                for start, end in confirmed_spans
                if start <= index < end
            ),
            None,
        )
        if covered is None:
            return index
        index = covered[1]
    return None


def _tail_hold_start(candidate: str, *, hold_growing_values: bool) -> int | None:
    """尾部可能仍在增长/会被前处理改写的片段起始位置（无则 ``None``）。

    URL 方案前缀、未闭合引用记号（降级前处理会整段剥离）无条件暂缓；
    ``hold_growing_values`` 为真（存在原样保留源）时，带单位数值也可能被
    替换，同样暂缓。取各模式最早起点作为边界。

    这些「尾部起始符」必须与降级前处理的剥离口径保持一致，事实源见
    :func:`bridges.chat.turn.ensure_unverified_teaching_prefix` 与
    :func:`bridges.chat.turn.strip_unverified_teaching_references`。
    """
    patterns = [_URL_SCHEME_PREFIX_RE, _OPEN_TOKEN_TAIL_RE]
    if hold_growing_values:
        patterns.append(_NUMBER_UNIT_PREFIX_RE)
    positions: list[int] = []
    for pattern in patterns:
        match = pattern.search(candidate)
        if match is not None:
            positions.append(match.start())
    return min(positions) if positions else None


def safe_append_boundary(
    candidate: str, sources: list[ProtectedFragment]
) -> int:
    """返回 ``candidate`` 中可以安全追加的前缀长度（append-only 边界）。

    - 无原样保留源时保护区不会改动正文，但尾部仍可能被调用方的确定性前处理
      改短（完整 URL / 网络引用 / 越界本地引用会被剥离），故 URL 方案前缀与
      未闭合引用记号仍暂缓到闭合；
    - 遇到第一个「未与源片段逐字一致」的候选片段即停在该片段起点；
    - 任何不在已确认逐字保留片段内的片段起始符都暂缓（跨块时片段归属可能
      前后移动，只有闭合且已确认的片段才允许提前下发）；
    - 最后一个仍可能增长的片段（URL / 带单位数值）与尾部部分片段一并暂缓。
    """
    boundary = len(candidate)
    confirmed_spans: list[tuple[int, int]] = []
    if sources:
        fragments = compile_protected_fragments(candidate)
        for fragment in fragments:
            if _is_exact_source_match(fragment, sources):
                confirmed_spans.append((fragment.start, fragment.end))
            elif boundary == len(candidate):
                boundary = fragment.start
        opener = _earliest_uncovered_opener(candidate, confirmed_spans)
        if opener is not None and opener < boundary:
            boundary = opener
        if fragments:
            last = fragments[-1]
            if (
                last.kind in _GROWING_KINDS
                and last.end == len(candidate)
                and last.start < boundary
            ):
                boundary = last.start
    tail = _tail_hold_start(candidate, hold_growing_values=bool(sources))
    if tail is not None and tail < boundary:
        boundary = tail
    return boundary


@dataclass
class StreamProtectionAssembler:
    """把持续增长的候选正文装配为**追加式** delta 序列。

    ``original`` 是用户自有正文（受保护片段的权威来源），``candidate`` 由调用方
    每次传入「截至目前」的完整候选正文（含任何确定性前处理，如降级前缀）。
    调用方只把 :meth:`update` / :meth:`finish` 返回的增量追加到消息正文，因此
    UI、事件重放与落库正文天然逐字一致。
    """

    original: str
    additional_sources: tuple[str, ...] = ()
    intent: ProtectionIntent | None = None
    append_missing: bool = False

    _candidate: str = field(default="", init=False, repr=False)
    _emitted: str = field(default="", init=False, repr=False)
    _sources: list[ProtectedFragment] = field(default_factory=list, init=False, repr=False)
    _result: ProtectionResult | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._sources = binding_sources(self.original, intent=self.intent)

    @property
    def emitted(self) -> str:
        """已经作为 delta 追加出去的正文（= 前端与落库正文）。"""
        return self._emitted

    @property
    def candidate(self) -> str:
        """截至目前收到的完整候选正文。"""
        return self._candidate

    @property
    def finalized(self) -> bool:
        """是否已按终态正文收敛（收敛后拒绝迟到的增量）。"""
        return self._result is not None

    def update(self, candidate: str) -> str:
        """推入新的完整候选正文，返回可追加的增量（可能为空字符串）。

        终态收敛后调用一律返回空串：迟到数据绝不推进正文。候选正文出现
        非追加变化（例如降级前处理删除了尚未输出的尾部片段）时只重算边界，
        已追加部分保持不变。
        """
        if self._result is not None:
            return ""
        self._candidate = candidate
        boundary = safe_append_boundary(candidate, self._sources)
        if boundary <= len(self._emitted):
            return ""
        target = candidate[:boundary]
        if not target.startswith(self._emitted):
            # 非追加变化落在已输出区域：无法用追加语义回退，保守丢弃本次增量。
            return ""
        delta = target[len(self._emitted) :]
        self._emitted = target
        return delta

    def finish(self) -> tuple[str, ProtectionResult]:
        """按终态正文收敛，返回剩余增量与完整保护区规划结果。

        终态正文即前端最终所见正文与落库正文；返回的增量与之前所有增量
        拼接后逐字等于该正文。
        """
        result = plan_fragment_protection(
            self.original,
            self._candidate,
            append_missing=self.append_missing,
            additional_sources=self.additional_sources,
            intent=self.intent,
        )
        self._result = result
        # 边界保证已追加部分恒为终态正文前缀（含降级前处理与片段替换）；
        # 违背时显式失败，绝不把整段正文当伪增量重发（协议禁止通道）。
        assert result.content.startswith(self._emitted)
        delta = result.content[len(self._emitted) :]
        self._emitted = result.content
        return delta, result


__all__ = [
    "STREAM_CONSISTENCY_PROTOCOL_VERSION",
    "StreamProtectionAssembler",
    "safe_append_boundary",
]
