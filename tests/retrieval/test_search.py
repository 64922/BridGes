"""检索内核单元测试：查询清理、FTS 窗口回退、RRF 融合、配额与去重（Issue 20）。

这些测试不依赖数据库：直接对纯函数（``clean_query`` / ``query_tokens`` /
``fuse_layer`` / ``merge_layers`` / ``layer_has_conflict`` /
``compute_sufficiency``）验证固定融合合同。
"""

from __future__ import annotations

from bridges.contracts.retrieval import RetrievalSourceLayer, RetrievalSufficiency
from bridges.retrieval.search import (
    LAYER_QUOTAS,
    ChunkHit,
    FusedCandidate,
    clean_query,
    compute_sufficiency,
    fuse_layer,
    layer_has_conflict,
    merge_layers,
    query_tokens,
)


def _hit(
    chunk_id: str,
    content_hash: str = "hash-a",
    chunk_content_hash: str = "chunk-hash-a",
) -> ChunkHit:
    return ChunkHit(
        chunk_id=chunk_id,
        document_id=f"doc-{chunk_id}",
        object_id=f"obj-{chunk_id}",
        content=f"内容 {chunk_id}",
        section_title=None,
        page_number=None,
        content_hash=content_hash,
        chunk_content_hash=chunk_content_hash,
    )


# ---------------------------------------------------------------------------
# 查询清理
# ---------------------------------------------------------------------------


def test_clean_query_strips_punctuation_and_truncates() -> None:
    cleaned = clean_query("热力学，第二定律是什么？——请解释！")
    assert "，" not in cleaned
    assert "？" not in cleaned
    assert "——" not in cleaned
    assert "热力学" in cleaned and "第二定律" in cleaned


def test_query_tokens_splits_whitespace_and_drops_short_terms() -> None:
    assert query_tokens("热力学第二定律是什么") == ["热力学第二定律是什么"]
    assert query_tokens("entropy second law") == ["entropy", "second", "law"]
    # 引号剥离、过短词（不足 3 字符）丢弃
    assert query_tokens('"ab" cde') == ["cde"]
    assert query_tokens("") == []


# ---------------------------------------------------------------------------
# 层内融合（RRF + 层内去重）
# ---------------------------------------------------------------------------


def test_fuse_layer_merges_keyword_and_vector_duplicates() -> None:
    a = _hit("a")
    b = _hit("b")
    c = _hit("c")
    fused = fuse_layer(
        [a, b, c],
        [b, a, c],  # 两路同时命中 a/b：RRF 合并为单条，层内去重
        quota=2,
    )
    assert [candidate.hit.chunk_id for candidate in fused] == ["a", "b"]
    assert fused[0].keyword_rank == 1
    assert fused[0].vector_rank == 2
    assert fused[1].keyword_rank == 2
    assert fused[1].vector_rank == 1


def test_fuse_layer_respects_quota_and_is_deterministic() -> None:
    hits = [_hit(f"c{i}") for i in range(10)]
    fused = fuse_layer(hits, [], quota=3)
    assert len(fused) == 3
    assert [candidate.hit.chunk_id for candidate in fused] == [
        "c0",
        "c1",
        "c2",
    ]


def test_fuse_layer_single_route_when_vector_empty() -> None:
    hits = [_hit(f"k{i}") for i in range(5)]
    fused = fuse_layer(hits, [], quota=4)
    assert len(fused) == 4
    assert all(candidate.vector_rank is None for candidate in fused)
    assert all(candidate.keyword_rank is not None for candidate in fused)


# ---------------------------------------------------------------------------
# 跨层合并（作用域权重 + 内容哈希去重 + 上限）
# ---------------------------------------------------------------------------


def test_merge_layers_applies_layer_priority_weights() -> None:
    attach = [_hit("attach-1", "hash-1")]
    project = [_hit("project-1", "hash-2")]
    kb = [_hit("kb-1", "hash-3")]
    merged = merge_layers(
        [
            (RetrievalSourceLayer.KNOWLEDGE_BASE, fuse_layer(kb, [], quota=1)),
            (RetrievalSourceLayer.PROJECT, fuse_layer(project, [], quota=1)),
            (RetrievalSourceLayer.ATTACHMENT, fuse_layer(attach, [], quota=1)),
        ]
    )
    layers = [layer for layer, _ in merged]
    # 附件层权重最高，排在项目与知识库之前（作用域顺序即优先级）
    assert layers == [
        RetrievalSourceLayer.ATTACHMENT,
        RetrievalSourceLayer.PROJECT,
        RetrievalSourceLayer.KNOWLEDGE_BASE,
    ]


def test_merge_layers_dedup_same_content_hash_keeps_higher_layer() -> None:
    # 同一内容同时存在于附件与知识库：只保留附件层候选（跨层去重规则）
    attach = [_hit("attach-1", "same-hash", "chunk-hash-1")]
    kb = [_hit("kb-1", "same-hash", "chunk-hash-1")]
    merged = merge_layers(
        [
            (RetrievalSourceLayer.KNOWLEDGE_BASE, fuse_layer(kb, [], quota=1)),
            (RetrievalSourceLayer.ATTACHMENT, fuse_layer(attach, [], quota=1)),
        ]
    )
    assert [(layer, c.hit.chunk_id) for layer, c in merged] == [
        (RetrievalSourceLayer.ATTACHMENT, "attach-1")
    ]


def test_merge_layers_keeps_distinct_chunks_of_same_document() -> None:
    # 同一文档（同文档哈希）的多个分块（不同分块哈希）是独立引用：
    # 跨层合并绝不把同文档不同页/章节折叠为一条。
    attach = [
        _hit("attach-1", "doc-hash", "chunk-hash-1"),
        _hit("attach-2", "doc-hash", "chunk-hash-2"),
        _hit("attach-3", "doc-hash", "chunk-hash-3"),
    ]
    kb = [
        _hit("kb-1", "other-doc", "chunk-hash-k1"),
    ]
    merged = merge_layers(
        [
            (RetrievalSourceLayer.ATTACHMENT, fuse_layer(attach, [], quota=4)),
            (RetrievalSourceLayer.KNOWLEDGE_BASE, fuse_layer(kb, [], quota=1)),
        ]
    )
    assert {c.hit.chunk_id for _, c in merged} == {
        "attach-1",
        "attach-2",
        "attach-3",
        "kb-1",
    }


def test_merge_layers_respects_max_citations() -> None:
    kb = [
        FusedCandidate(
            _hit(f"kb-{i}", content_hash=f"hash-{i}"), 0.1, i + 1, None
        )
        for i in range(12)
    ]
    merged = merge_layers([(RetrievalSourceLayer.KNOWLEDGE_BASE, kb)], max_citations=5)
    assert len(merged) == 5


def test_layer_quotas_are_independent_per_source() -> None:
    # 独立候选配额：各层互不挤占（附件 4 / 项目 5 / 知识库 5）
    assert LAYER_QUOTAS[RetrievalSourceLayer.ATTACHMENT] == 4
    assert LAYER_QUOTAS[RetrievalSourceLayer.PROJECT] == 5
    assert LAYER_QUOTAS[RetrievalSourceLayer.KNOWLEDGE_BASE] == 5


# ---------------------------------------------------------------------------
# 冲突与充足性
# ---------------------------------------------------------------------------


def test_layer_has_conflict_when_top_hits_differ() -> None:
    assert layer_has_conflict([_hit("a")], [_hit("b")])
    # 顶级一致或单路缺失都不算冲突
    assert not layer_has_conflict([_hit("a")], [_hit("a")])
    assert not layer_has_conflict([_hit("a")], [])
    assert not layer_has_conflict([], [_hit("b")])


def test_compute_sufficiency_priority_contract() -> None:
    # 索引不可用 > 无命中 > 冲突 > 覆盖不足 > 充足
    assert (
        compute_sufficiency(index_unavailable=True, total_candidates=5, conflicts=False)
        == RetrievalSufficiency.INDEX_UNAVAILABLE
    )
    assert (
        compute_sufficiency(index_unavailable=False, total_candidates=0, conflicts=False)
        == RetrievalSufficiency.NO_HITS
    )
    assert (
        compute_sufficiency(index_unavailable=False, total_candidates=5, conflicts=True)
        == RetrievalSufficiency.CONFLICT
    )
    assert (
        compute_sufficiency(
            index_unavailable=False, total_candidates=2, conflicts=False
        )
        == RetrievalSufficiency.INSUFFICIENT_COVERAGE
    )
    assert (
        compute_sufficiency(
            index_unavailable=False, total_candidates=3, conflicts=False
        )
        == RetrievalSufficiency.SUFFICIENT
    )
