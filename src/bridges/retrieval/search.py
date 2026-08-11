"""分层检索的搜索内核：关键词（FTS5 BM25）与向量（余弦）的固定融合合同。

融合合同（Issue 20，固定且可测试）：
1. 每层独立检索：FTS5 trigram BM25 与 text-embedding-v4 向量余弦各产生一个
   有序候选列表（查询先按整词匹配，整词无命中时按长度递减的滑动窗口回退，
   保证「查询长于原文短语」也能命中）；
2. 层内融合用 Reciprocal Rank Fusion（固定 k=60）：同一分块同时命中关键词
   与向量时合并为一条（层内去重规则）；
3. 每层按独立候选配额取前 N（附件 4 / 项目 5 / 知识库 5），避免某一来源
   淹没其余材料；
4. 跨层合并按作用域优先级加权（附件 3 / 项目 2 / 知识库 1），相同内容哈希
   只保留优先级更高层的候选（跨层去重规则），最终列表上限 10 条。

充足性信号按固定优先级判定：索引不可用 > 无命中 > 命中冲突（层内关键词
顶级与向量顶级不一致）> 覆盖不足（去重后候选 < 3）> 充足。
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from bridges.contracts.retrieval import (
    RetrievalSourceLayer,
    RetrievalSufficiency,
)

#: 层内融合的 RRF 常数（固定，可测试）。
RRF_K = 60
#: 每层独立候选配额（作用域顺序附件 → 项目 → 知识库）。
LAYER_QUOTAS: dict[RetrievalSourceLayer, int] = {
    RetrievalSourceLayer.ATTACHMENT: 4,
    RetrievalSourceLayer.PROJECT: 5,
    RetrievalSourceLayer.KNOWLEDGE_BASE: 5,
}
#: 跨层合并的作用域优先级权重（来源顺序表达优先级，不机械拼接全部来源）。
LAYER_WEIGHTS: dict[RetrievalSourceLayer, float] = {
    RetrievalSourceLayer.ATTACHMENT: 3.0,
    RetrievalSourceLayer.PROJECT: 2.0,
    RetrievalSourceLayer.KNOWLEDGE_BASE: 1.0,
}
#: 融合后最终引用列表上限（模型上下文取前 CONTEXT_MAX_CHUNKS 条）。
MAX_CITATIONS = 10
#: 证据充足的最低候选数（去重后）。
MIN_COVERAGE = 3
#: 检索查询最大长度（超出截断，防止超长问题词）。
QUERY_MAX_LEN = 200
#: 关键词检索单层抓取上限（远高于配额，供配额与冲突判定使用）。
KEYWORD_FETCH_LIMIT = 40
#: 向量检索单层抓取上限。
VECTOR_FETCH_LIMIT = 40
#: 向量候选进入融合前的最小余弦相似度。
VECTOR_MIN_SIMILARITY = 0.89
#: 整词无命中时的回退窗口长度（递减，滑动步长 3）。
WINDOW_LENGTHS = (18, 15, 12, 9, 6)
#: 查询清理时剥离的常见标点（保留汉字/字母/数字用于检索）。
_PUNCTUATION = re.compile(r"[，。！？；：、''（）【】《》〈〉…·/|—–-]")
#: FTS5 trigram 匹配要求 3 字符以上。
_MIN_TOKEN_LEN = 3


@dataclass(frozen=True)
class ChunkHit:
    """一条检索命中的领域记录（融合前按层内列表保持顺序）。

    ``content_hash`` 是文档级内容哈希（判断跨层同内容）；``chunk_content_hash``
    是分块级哈希（document_chunks.content_hash，区分同文档的不同分块）。
    跨层去重必须用两个键的组合：同文档不同分块是独立引用，跨层同内容
    文档才互相折叠。
    """

    chunk_id: str
    document_id: str
    object_id: str
    content: str
    section_title: str | None
    page_number: int | None
    content_hash: str
    chunk_content_hash: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row | dict[str, Any]) -> ChunkHit:
        return cls(
            chunk_id=str(row["chunk_id"]),
            document_id=str(row["document_id"]),
            object_id=str(row["object_id"]),
            content=str(row["content"]),
            section_title=(
                str(row["section_title"]) if row["section_title"] is not None else None
            ),
            page_number=(
                int(row["page_number"]) if row["page_number"] is not None else None
            ),
            content_hash=str(row["content_hash"]),
            chunk_content_hash=(
                str(row["chunk_content_hash"])
                if row["chunk_content_hash"] is not None
                else ""
            ),
        )


@dataclass(frozen=True)
class LayerSearchResult:
    """单层两路检索的原始结果（关键词与向量各自的排序列表）。"""

    layer: RetrievalSourceLayer
    keyword_hits: list[ChunkHit]
    vector_hits: list[ChunkHit]
    #: 向量检索不可用的原因（None 表示已成功检索）。
    vector_unavailable_reason: str | None = None


@dataclass(frozen=True)
class FusedCandidate:
    """层内融合后的候选（已去重，带融合分数与两路位次）。"""

    hit: ChunkHit
    score: float
    keyword_rank: int | None
    vector_rank: int | None


def clean_query(content: str) -> str:
    """清理检索查询：剥离常见标点、折叠空白并截断到固定长度。"""
    cleaned = _PUNCTUATION.sub("", content)
    cleaned = " ".join(cleaned.split())
    return cleaned[:QUERY_MAX_LEN]


def query_tokens(query: str) -> list[str]:
    """把查询拆为检索词：按空白切分、去引号、丢弃过短词。

    中文无空格的整句保持为单个词（trigram 子串匹配天然支持），
    拉丁语按单词拆分。空查询返回空列表（调用方跳过关键词检索）。
    """
    tokens: list[str] = []
    for raw in query.split():
        token = raw.replace('"', "")
        if token and len(token) >= _MIN_TOKEN_LEN:
            tokens.append(token)
    return tokens


def _fts_match_expression(token: str) -> str:
    """构造单个检索词的 FTS5 表达式（引号包裹，字面匹配）。"""
    return f'"{token}"'


def _window_variants(token: str) -> list[str]:
    """整词无命中时的回退变体：长度递减的滑动窗口（含整词本身）。

    返回顺序即回退优先级：先整词，再按长度从长到短滑动（步长 3）。
    """
    variants = [token]
    for window_len in WINDOW_LENGTHS:
        if window_len >= len(token):
            continue
        windows: list[str] = []
        for start in range(0, len(token) - window_len + 1, 3):
            window = token[start : start + window_len]
            if window not in windows:
                windows.append(window)
        variants.extend(windows)
    return variants


def search_keyword(
    connection: sqlite3.Connection,
    *,
    account_id: str,
    version_id: str,
    document_ids: Sequence[str],
    query: str,
    limit: int = KEYWORD_FETCH_LIMIT,
) -> list[ChunkHit]:
    """FTS5 trigram BM25 关键词检索：按查询词列表逐词匹配后合并排序。

    每个词先整词匹配；整词无命中时回退到滑动窗口（查询长于原文短语时
    仍能命中）。多词查询按出现顺序逐个检索，命中按「最早命中的词」
    稳定排序（位次即融合输入，不混用窗口长度）。
    """
    if not document_ids:
        return []
    placeholders = ",".join("?" for _ in document_ids)
    base_sql = (
        "SELECT c.chunk_id, c.document_id, c.content, c.section_title, c.page_number,"
        " r.object_id, r.content_hash, c.content_hash AS chunk_content_hash"
        " FROM fts_chunks f"
        " JOIN document_chunks c ON c.chunk_id = f.chunk_id"
        " JOIN document_records r ON r.document_id = c.document_id"
        " WHERE f.version_id = ? AND f.content MATCH ?"
        " AND r.account_id = ? AND r.status = 'ready'"
        f" AND r.document_id IN ({placeholders})"
        " ORDER BY bm25(fts_chunks, 0.0, 0.0, 0.0, 1.0)"
        " LIMIT ?"
    )
    merged: list[ChunkHit] = []
    seen: set[str] = set()
    for token in query_tokens(query):
        token_hit = False
        for variant in _window_variants(token):
            rows = connection.execute(
                base_sql,
                (version_id, _fts_match_expression(variant), account_id)
                + tuple(document_ids)
                + (limit,),
            ).fetchall()
            for row in rows:
                chunk_id = str(row["chunk_id"])
                if chunk_id in seen:
                    continue
                seen.add(chunk_id)
                merged.append(ChunkHit.from_row(row))
                token_hit = True
            # 当前检索词已有命中：停止该词的窗口回退；后续检索词仍参与
            # 检索（逐词合并），命中按「最早命中的词」稳定排序。
            if token_hit:
                break
    return merged[:limit]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """两个 L2 规范化向量的余弦相似度（点积）。"""
    return sum(x * y for x, y in zip(a, b, strict=True))


def search_vectors(
    vector_rows: Iterable[dict[str, Any]],
    query_vector: Sequence[float],
    limit: int = VECTOR_FETCH_LIMIT,
    *,
    min_similarity: float = VECTOR_MIN_SIMILARITY,
) -> list[ChunkHit]:
    """向量余弦检索：按相似度降序返回前 limit 条命中。

    查询向量与存量向量都已按索引合同 L2 规范化，余弦即点积。
    低于 ``min_similarity`` 的候选在排序截断前丢弃；位次按
    (相似度降序, chunk_id 升序) 稳定排序。
    """
    scored: list[tuple[float, ChunkHit]] = []
    for row in vector_rows:
        chunk = ChunkHit.from_row(row)
        vector = row.get("vector_json")
        try:
            values = [float(value) for value in json.loads(str(vector))]
        except (TypeError, ValueError):
            continue
        try:
            similarity = _cosine(values, query_vector)
        except ValueError:
            # 维度与合同不符（脏数据）：该分块不参与排序，不阻断整层检索
            continue
        if similarity < min_similarity:
            continue
        scored.append((similarity, chunk))
    scored.sort(key=lambda item: (-item[0], item[1].chunk_id))
    return [chunk for _, chunk in scored[:limit]]


def fuse_layer(
    keyword_hits: list[ChunkHit],
    vector_hits: list[ChunkHit],
    *,
    quota: int,
    k: int = RRF_K,
) -> list[FusedCandidate]:
    """层内融合：两路位次做 RRF，同一分块合并为一条（层内去重规则）。

    返回按融合分数降序的前 ``quota`` 条候选；并列按关键词位次优先、
    再按 chunk_id 稳定排序。
    """
    scores: dict[str, float] = {}
    keyword_ranks: dict[str, int] = {}
    vector_ranks: dict[str, int] = {}
    for rank, hit in enumerate(keyword_hits, start=1):
        scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)
        keyword_ranks.setdefault(hit.chunk_id, rank)
    for rank, hit in enumerate(vector_hits, start=1):
        scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)
        vector_ranks.setdefault(hit.chunk_id, rank)
    by_id = {hit.chunk_id: hit for hit in [*keyword_hits, *vector_hits]}
    ordered = sorted(
        scores,
        key=lambda chunk_id: (
            -scores[chunk_id],
            keyword_ranks.get(chunk_id, 10**9),
            chunk_id,
        ),
    )
    return [
        FusedCandidate(
            hit=by_id[chunk_id],
            score=scores[chunk_id],
            keyword_rank=keyword_ranks.get(chunk_id),
            vector_rank=vector_ranks.get(chunk_id),
        )
        for chunk_id in ordered[:quota]
    ]


def merge_layers(
    layer_candidates: Sequence[tuple[RetrievalSourceLayer, list[FusedCandidate]]],
    *,
    max_citations: int = MAX_CITATIONS,
) -> list[tuple[RetrievalSourceLayer, FusedCandidate]]:
    """跨层合并：作用域权重加权 → 内容去重 → 排序 → 取上限。

    跨层去重规则：不同层出现相同内容（同一文档内容哈希 + 同一分块哈希，
    即同一内容的同一片段上传到多个来源）时，保留作用域优先级更高层的
    候选，低层同内容候选丢弃；同一文档的不同分块是独立引用，绝不互相
    折叠（页码/章节证据多样性保留）。
    """
    weighted: list[tuple[float, RetrievalSourceLayer, FusedCandidate]] = []
    for layer, candidates in layer_candidates:
        weight = LAYER_WEIGHTS[layer]
        for candidate in candidates:
            weighted.append((candidate.score * weight, layer, candidate))
    merged: list[tuple[RetrievalSourceLayer, FusedCandidate]] = []
    seen_content: set[tuple[str, str]] = set()
    for _, layer, candidate in sorted(
        weighted,
        key=lambda item: (
            -item[0],
            LAYER_WEIGHTS[item[1]],
            item[2].hit.chunk_id,
        ),
    ):
        hit = candidate.hit
        content_key = (hit.content_hash, hit.chunk_content_hash)
        if content_key in seen_content:
            continue
        seen_content.add(content_key)
        merged.append((layer, candidate))
        if len(merged) >= max_citations:
            break
    return merged


def layer_has_conflict(keyword_hits: list[ChunkHit], vector_hits: list[ChunkHit]) -> bool:
    """命中冲突判定：关键词顶级与向量顶级命中不一致（层内歧义）。

    两路都成功检索且顶级分块不同时视为冲突——同一问题两路给出不同
    答案源，是"命中冲突"的结构化信号。
    """
    if not keyword_hits or not vector_hits:
        return False
    return keyword_hits[0].chunk_id != vector_hits[0].chunk_id


def compute_sufficiency(
    *,
    index_unavailable: bool,
    total_candidates: int,
    conflicts: bool,
    min_coverage: int = MIN_COVERAGE,
) -> RetrievalSufficiency:
    """按固定优先级计算整体充足性信号（供教学门消费）。"""
    if index_unavailable:
        return RetrievalSufficiency.INDEX_UNAVAILABLE
    if total_candidates == 0:
        return RetrievalSufficiency.NO_HITS
    if conflicts:
        return RetrievalSufficiency.CONFLICT
    if total_candidates < min_coverage:
        return RetrievalSufficiency.INSUFFICIENT_COVERAGE
    return RetrievalSufficiency.SUFFICIENT
