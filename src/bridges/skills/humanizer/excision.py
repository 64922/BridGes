"""确定性句子级剔除（人味化改造第八次改进 Issue 02）。

定向修订后重跑全套检查仍 blocking、且全部 blocking findings 属于机械
可剔除集合（无来源新增类）时，对最新候选正文按 finding 定位违规条目
所在**句子**并整句剔除；调用方对剔除稿重跑同一版本全套检查，通过后以
「已剔除交付」终态交付成品并如实标注移除清单。

设计约束：

- 纯确定性、零模型调用，不占用写作调用额度（首稿 + 修订仍 ≤ 2 次）。
- 粒度整句：不做 span 级（词级）剔除；同一句子命中的多条 finding 只
  剔除一次。
- 破坏原文事实类（引语/URL/公式/代码/引用/数字/日期/专名被破坏、因果
  方向反转、否定删除、结论强度升级、亲历丢失等）永不剔除——它们不在此
  集合内，或虽同码但无位置（如保留检查的 ``FIRST_PERSON_UNBOUND``，
  亲历丢失时无违规条目可定位），剔除函数返回 None，调用方维持停止交付。
- 安全护栏：剔除后正文为空、剔除句数占比超过 ``EXCISION_SENTENCE_RATIO_LIMIT``
  或任一 finding 无法定位（无位置/归一化映射不一致）时放弃剔除。
- finding 的位置是**归一化文本**（``_normalize_text`` 全半角/上下标统一
  与空白折叠后）中的偏移，本模块建立归一化偏移 → 原文偏移的映射后再定位
  句子，避免空白折叠造成错位。

审计约定：移除清单只含失败码/类别/说明与句子序号，不把正文写入审计。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from bridges.contracts.humanizer import FidelityFailure, FidelityFailureCode
from bridges.skills.humanizer.factlock import (
    _FULLWIDTH,
    _SUBSCRIPT,
    _SUPERSCRIPT,
    _normalize_text,
)

#: 剔除句数占比上限（单一常量）：超过该比例放弃剔除、维持停止交付。
#: 调用方无需另行配置；改阈值即改此处。
EXCISION_SENTENCE_RATIO_LIMIT = 0.4

#: 机械可剔除错误码集合（无来源新增类，剔除即消除风险；Issue 02 冻结决策）。
#: 不在此集合的 blocking 码一律不剔除。注意 ``FIRST_PERSON_UNBOUND``
#: 同时出现在保留检查（原文亲历未保持，无位置）与新增检查（无来源新增
#: 亲历，有位置）——保留检查形态通过「无位置不可剔除」规则天然拦截。
EXCISABLE_CODES = frozenset(
    {
        FidelityFailureCode.UNATTRIBUTED_CLAIM,
        FidelityFailureCode.ASSUMPTION_NOT_ALLOWED,
        FidelityFailureCode.ASSUMPTION_CARRIES_FACT,
        FidelityFailureCode.FIRST_PERSON_UNBOUND,
    }
)

#: 句子边界：中英文句末/分号与换行；标点并入前一句（与 source_ledger 的
#: ``_SENTENCE_END_RE`` 一致并补充半角边界）。
_SENTENCE_BOUNDARY_RE = re.compile(r"[。！？!?；;\n]+")


@dataclass(frozen=True)
class ExcisionItem:
    """一条被剔除的违规条目（脱敏：不含正文，审计可存）。"""

    code: str = ""
    category: str = ""
    note: str = ""


@dataclass(frozen=True)
class ExcisionRecord:
    """一次确定性剔除的结果：剔除后正文 + 移除清单（供审计与投影）。"""

    text: str = ""
    removed_count: int = 0
    removed_sentence_count: int = 0
    sentence_ratio: float = 0.0
    items: tuple[ExcisionItem, ...] = ()


def split_sentences(text: str) -> list[tuple[int, int]]:
    """按句末标点/换行切分原文，返回每句的 [start, end) 偏移（含标点）。

    空段（纯空白）不产生句子；无边界时整段为一句。
    """
    spans: list[tuple[int, int]] = []
    start = 0
    for match in _SENTENCE_BOUNDARY_RE.finditer(text):
        end = match.end()
        if text[start:end].strip():
            spans.append((start, end))
        start = end
    if text[start:].strip():
        spans.append((start, len(text)))
    return spans


def _norm_offset_map(original: str) -> tuple[str, list[int]] | None:
    """归一化原文并建立「归一化字符位置 → 原文位置」映射。

    与 ``_normalize_text`` 语义一致（全半角/上下标 1:1 翻译 + 空白折叠）；
    映射不一致（防御性）返回 None，调用方放弃剔除。
    """
    norm_chars: list[str] = []
    mapping: list[int] = []
    pending_space = False
    for index, char in enumerate(original):
        mapped = char.translate(_FULLWIDTH).translate(_SUPERSCRIPT).translate(_SUBSCRIPT)
        if mapped.isspace():
            pending_space = True
            continue
        if pending_space and norm_chars:
            norm_chars.append(" ")
            mapping.append(index - 1)
        pending_space = False
        norm_chars.append(mapped)
        mapping.append(index)
    normalized = "".join(norm_chars)
    if normalized != _normalize_text(original):
        return None
    return normalized, mapping


def _locate_sentence(
    location: object,
    mapping: list[int],
    spans: list[tuple[int, int]],
) -> int | None:
    """把归一化偏移定位到原文句子（返回句子序号；无法定位返回 None）。"""
    start = int(getattr(location, "start", -1))
    end = int(getattr(location, "end", -1))
    if start < 0 or end < start or end > len(mapping):
        return None
    if end == 0:
        return None
    orig_start = mapping[start]
    for index, (s, e) in enumerate(spans):
        if s <= orig_start < e:
            return index
    return None


def can_excise(findings: Sequence[FidelityFailure]) -> bool:
    """是否具备剔除前提：非空且全部为机械可剔除类、均带可定位位置。

    调用方在启动剔除流程（审计标记/过程事件）前先以此把关，避免对注定
    失败的混合码场景做无效尝试；模块内 ``excise_blocking_content`` 仍
    会二次校验（防御纵深）。
    """
    return bool(findings) and all(
        finding.code in EXCISABLE_CODES and finding.location is not None
        for finding in findings
    )


def excise_blocking_content(
    candidate: str,
    findings: Sequence[FidelityFailure],
) -> ExcisionRecord | None:
    """对最新候选正文执行确定性句子级剔除。

    仅当全部 blocking findings 属于机械可剔除集合且均可定位时执行；剔除
    后正文为空或剔除句数占比超过阈值时返回 None（调用方维持停止交付，
    不交付残稿）。返回的 ``ExcisionRecord.text`` 需要调用方重跑同一版本
    全套检查决定最终交付（重检不通过仍停止交付）。
    """
    if not candidate.strip():
        return None
    if not can_excise(findings):
        return None

    mapped = _norm_offset_map(candidate)
    if mapped is None:
        return None
    _, mapping = mapped
    spans = split_sentences(candidate)
    if not spans:
        return None

    remove_indices: set[int] = set()
    items: list[ExcisionItem] = []
    for finding in findings:
        location = finding.location
        if location is None:
            return None
        index = _locate_sentence(location, mapping, spans)
        if index is None:
            return None
        remove_indices.add(index)
        items.append(
            ExcisionItem(
                code=finding.code.value,
                category=finding.category,
                note=finding.note,
            )
        )

    sentence_ratio = len(remove_indices) / len(spans)
    if sentence_ratio > EXCISION_SENTENCE_RATIO_LIMIT:
        return None

    remaining = "".join(
        candidate[start:end]
        for index, (start, end) in enumerate(spans)
        if index not in remove_indices
    ).strip()
    if not remaining:
        return None

    return ExcisionRecord(
        text=remaining,
        removed_count=len(items),
        removed_sentence_count=len(remove_indices),
        sentence_ratio=sentence_ratio,
        items=tuple(items),
    )


__all__ = [
    "EXCISION_SENTENCE_RATIO_LIMIT",
    "EXCISABLE_CODES",
    "ExcisionItem",
    "ExcisionRecord",
    "can_excise",
    "excise_blocking_content",
    "split_sentences",
]
