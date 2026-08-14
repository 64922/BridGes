"""分层检索服务测试：作用域、融合排序、配额去重、隔离授权与充足性（Issue 20）。

验证验收标准：三层作用域只查当前账户资源、固定融合合同、独立候选配额
与去重、关闭知识库后请求/引用不含其候选、引用可精确打开且删除/权限
变化显示安全状态、无命中/冲突/覆盖不足/索引不可用结构化输出、刷新后
引用稳定、索引重建不漂移。
"""

from __future__ import annotations

import secrets
from typing import Any

import pytest
from pydantic import SecretStr

from bridges.ai.adapters import AuthError
from bridges.ai.ports import EmbeddingContext
from bridges.contracts.ai import ModelCallStatus
from bridges.contracts.retrieval import (
    CitationAccessStatus,
    RetrievalLayerStatus,
    RetrievalSourceLayer,
    RetrievalSufficiency,
)
from bridges.ingestion.embedding import (
    DeterministicEmbeddingPort,
    EmbeddingError,
)
from bridges.ingestion.index import VersionedIndex
from bridges.retrieval.service import LayeredRetrievalService, RetrievalError
from bridges.storage.database import BridgesDatabase
from tests.retrieval.conftest import (
    add_material,
    add_user_message,
    seed_conversation,
    seed_project,
)


class _KeywordOnlyEmbeddingPort:
    """查询向量化必然失败的替身（GQ-05 诚实降级路径）。

    替代原「账户探测不可用」门：向量调用失败时检索如实回退关键词检索，
    不伪装向量就绪。
    """

    def embed(
        self,
        account_id: str,
        texts: list[str],
        *,
        context: EmbeddingContext | None = None,
    ) -> list[list[float]]:
        raise EmbeddingError(
            "向量化失败：全局百炼凭据无效或没有该模型权限，"
            "请检查启动服务的全局百炼配置与权限。",
            retryable=False,
        )


def _run(
    env: dict[str, Any],
    account_id: str,
    conversation_id: str,
    assistant_message_id: str,
    query: str,
    *,
    user_message_id: str | None = None,
    use_knowledge_base: bool = True,
    retrieval: LayeredRetrievalService | None = None,
):
    service = retrieval if retrieval is not None else env["retrieval"]
    return service.run_round(
        account_id,
        conversation_id,
        assistant_message_id,
        user_message_id,
        query,
        use_knowledge_base=use_knowledge_base,
    )


def _layer(round_, layer: RetrievalSourceLayer):
    return next(item for item in round_.layers if item.layer == layer)


# ---------------------------------------------------------------------------
# 作用域与三层检索
# ---------------------------------------------------------------------------


def test_scope_attachment_then_project_then_kb(env: dict[str, Any]) -> None:
    account = env["account_a"]
    project_id = seed_project(env, account)
    conversation_id = seed_conversation(env, account, project_id=project_id)
    user_message_id = add_user_message(env, account, conversation_id, "热力学问题")
    add_material(
        env, account, "附件笔记.txt", "热力学第二定律：熵永不减少。",
        layer="attachment", conversation_id=conversation_id,
        user_message_id=user_message_id,
    )
    add_material(
        env, account, "项目讲义.md", "熵增原理与统计力学。",
        layer="project", project_id=project_id,
    )
    add_material(
        env, account, "知识库材料.txt", "孤立系统熵增。",
        layer="knowledge_base",
    )

    round_ = _run(env, account, conversation_id, "assistant-1", "热力学第二定律",
                  user_message_id=user_message_id)
    assert round_ is not None
    assert _layer(round_, RetrievalSourceLayer.ATTACHMENT).status == RetrievalLayerStatus.OK
    assert _layer(round_, RetrievalSourceLayer.PROJECT).status == RetrievalLayerStatus.OK
    assert (
        _layer(round_, RetrievalSourceLayer.KNOWLEDGE_BASE).status
        == RetrievalLayerStatus.OK
    )
    layers = [item.layer for item in round_.layers]
    assert layers == [
        RetrievalSourceLayer.ATTACHMENT,
        RetrievalSourceLayer.PROJECT,
        RetrievalSourceLayer.KNOWLEDGE_BASE,
    ]
    # 附件层候选优先于项目与知识库（作用域顺序即优先级）
    assert round_.citations[0].source_layer == RetrievalSourceLayer.ATTACHMENT
    assert round_.citations[0].filename == "附件笔记.txt"


def test_scope_skipped_when_nothing_searchable(env: dict[str, Any]) -> None:
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    # 无附件、未归属项目、知识库无材料 → 无检索作用域
    round_ = _run(env, account, conversation_id, "assistant-1", "热力学")
    assert round_ is None


def test_disabled_project_and_attachment_layers_show_honest_status(
    env: dict[str, Any],
) -> None:
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    add_material(env, account, "知识库.txt", "热力学第二定律。", layer="knowledge_base")
    round_ = _run(env, account, conversation_id, "assistant-1", "热力学第二定律")
    assert round_ is not None
    assert _layer(round_, RetrievalSourceLayer.ATTACHMENT).status == RetrievalLayerStatus.DISABLED
    assert _layer(round_, RetrievalSourceLayer.PROJECT).status == RetrievalLayerStatus.DISABLED
    assert _layer(round_, RetrievalSourceLayer.KNOWLEDGE_BASE).status == RetrievalLayerStatus.OK


# ---------------------------------------------------------------------------
# 关闭知识库开关
# ---------------------------------------------------------------------------


def test_use_knowledge_base_false_excludes_kb_candidates(env: dict[str, Any]) -> None:
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    user_message_id = add_user_message(env, account, conversation_id, "热力学")
    add_material(
        env, account, "附件.txt", "热力学第二定律内容。",
        layer="attachment", conversation_id=conversation_id,
        user_message_id=user_message_id,
    )
    add_material(env, account, "知识库.txt", "热力学第二定律知识库内容。", layer="knowledge_base")

    closed = _run(
        env, account, conversation_id, "assistant-1", "热力学第二定律",
        user_message_id=user_message_id, use_knowledge_base=False,
    )
    assert closed is not None
    assert (
        _layer(closed, RetrievalSourceLayer.KNOWLEDGE_BASE).status
        == RetrievalLayerStatus.DISABLED
    )
    assert all(
        c.source_layer != RetrievalSourceLayer.KNOWLEDGE_BASE
        for c in closed.citations
    )
    # 轮次记录如实标记关闭状态（供前端展示与审计）
    assert closed.use_knowledge_base is False

    opened = _run(
        env, account, conversation_id, "assistant-2", "热力学第二定律",
        user_message_id=user_message_id, use_knowledge_base=True,
    )
    assert opened is not None
    assert any(
        c.source_layer == RetrievalSourceLayer.KNOWLEDGE_BASE
        for c in opened.citations
    )


# ---------------------------------------------------------------------------
# 账户隔离：相同哈希文件跨账户
# ---------------------------------------------------------------------------


def test_cross_account_same_content_is_isolated(env: dict[str, Any]) -> None:
    account_a = env["account_a"]
    account_b = env["account_b"]
    conversation_a = seed_conversation(env, account_a)
    conversation_b = seed_conversation(env, account_b)
    add_material(
        env, account_a, "共享内容.txt", "热力学第二定律：熵永不减少。",
        layer="knowledge_base",
    )
    # 账户 B 上传相同内容（同内容哈希），归属 B
    add_material(
        env, account_b, "共享内容.txt", "热力学第二定律：熵永不减少。",
        layer="knowledge_base",
    )

    round_a = _run(env, account_a, conversation_a, "assistant-a", "热力学第二定律")
    assert round_a is not None
    assert len(round_a.citations) == 1
    assert round_a.citations[0].filename == "共享内容.txt"

    round_b = _run(env, account_b, conversation_b, "assistant-b", "热力学第二定律")
    assert round_b is not None
    assert len(round_b.citations) == 1
    # 各自只看到自己的对象（对象标识不同），绝不读取其他账户相同哈希文件
    assert round_b.citations[0].object_id != round_a.citations[0].object_id
    assert round_b.citations[0].citation_id != round_a.citations[0].citation_id

    # 跨账户读取引用详情 → 统一 404
    with pytest.raises(RetrievalError) as excinfo:
        env["retrieval"].citation_detail(
            account_b, conversation_a, "assistant-a", round_a.citations[0].citation_id
        )
    assert excinfo.value.code == "citation_not_found"


def test_shared_global_embedding_port_keeps_accounts_isolated(
    env: dict[str, Any],
) -> None:
    """GQ-05 多账户回归：同一全局 Embedding 端口为两账户建索引，查询互不可见。

    全局凭据共享绝不演变为共享知识库：向量行按账户写入、候选/引用严格
    作用域过滤；同一端口实例被摄取与检索共用（与生产组合根同构）。
    """
    account_a = env["account_a"]
    account_b = env["account_b"]
    conversation_a = seed_conversation(env, account_a)
    conversation_b = seed_conversation(env, account_b)
    object_a = add_material(
        env, account_a, "甲的量子材料.txt", "量子力学波函数坍缩与叠加态。",
        layer="knowledge_base",
    )
    object_b = add_material(
        env, account_b, "乙的热力材料.txt", "热力学第二定律熵增原理。",
        layer="knowledge_base",
    )
    # 同一全局端口被摄取（两账户）与检索共用：单实例、单模型合同
    assert env["ingestion"]._embedding is env["embedding"]  # type: ignore[attr-defined]
    assert env["retrieval"]._embedding is env["embedding"]  # type: ignore[attr-defined]

    rows = env["database"].connection.execute(
        "SELECT account_id, COUNT(*) AS count FROM index_vectors"
        " GROUP BY account_id"
    ).fetchall()
    assert sorted((str(r["account_id"]), int(r["count"])) for r in rows) == sorted(
        [(account_a, 1), (account_b, 1)]
    )  # 向量行严格按账户归属：每账户各一行

    round_a = _run(env, account_a, conversation_a, "assistant-a", "量子力学")
    assert round_a is not None
    assert [c.object_id for c in round_a.citations] == [object_a]
    assert all(c.filename.startswith("甲的") for c in round_a.citations)

    round_b = _run(env, account_b, conversation_b, "assistant-b", "热力学")
    assert round_b is not None
    assert [c.object_id for c in round_b.citations] == [object_b]
    assert all(c.filename.startswith("乙的") for c in round_b.citations)


# ---------------------------------------------------------------------------
# 充足性信号
# ---------------------------------------------------------------------------


def test_no_hits_is_structured_not_silent_success(env: dict[str, Any]) -> None:
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    add_material(env, account, "材料.txt", "量子力学波函数坍缩。", layer="knowledge_base")
    # GQ-05：查询向量化调用失败（无全局 Key/权限等）→ 诚实回退关键词
    # 检索；关键词无命中时即结构化 no_hits，不以空候选表示成功。
    keyword_only = LayeredRetrievalService(
        database=env["database"],
        embedding=_KeywordOnlyEmbeddingPort(),
        object_repository=env["repository"],
    )
    round_ = _run(
        env, account, conversation_id, "assistant-1", "完全无关的问题内容",
        retrieval=keyword_only,
    )
    assert round_ is not None
    assert round_.sufficiency == RetrievalSufficiency.NO_HITS
    assert round_.citations == []
    assert "没有找到" in (round_.note or "")
    assert "关键词检索" in (round_.note or "")


def test_unrelated_query_does_not_emit_vector_only_citations(env: dict[str, Any]) -> None:
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    add_material(env, account, "量子材料.txt", "量子力学波函数坍缩。", layer="knowledge_base")

    round_ = _run(env, account, conversation_id, "assistant-1", "古典音乐作品分析")

    assert round_ is not None
    assert round_.sufficiency == RetrievalSufficiency.NO_HITS
    assert round_.citations == []


def test_conflict_signal_when_keyword_and_vector_disagree(env: dict[str, Any]) -> None:
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    # 三份同主题但细节不同的材料：确定性 Embedding 下关键词顶级与向量
    # 顶级命中不一致（经实测固定），冲突信号确定性触发（AC-07）。
    for index in range(3):
        add_material(
            env, account, f"材料{index}.txt",
            f"热力学第二定律的第 {index} 条详细阐述内容。",
            layer="knowledge_base",
        )
    round_ = _run(env, account, conversation_id, "assistant-1", "热力学第二定律")
    assert round_ is not None
    assert round_.sufficiency == RetrievalSufficiency.CONFLICT
    assert len(round_.citations) >= 2  # 冲突仍展示候选，不以空候选表示成功


def test_insufficient_coverage_below_threshold(env: dict[str, Any]) -> None:
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    add_material(env, account, "单材料.txt", "热力学第二定律的一句话。", layer="knowledge_base")
    round_ = _run(env, account, conversation_id, "assistant-1", "热力学第二定律")
    assert round_ is not None
    assert len(round_.citations) < 3
    assert round_.sufficiency == RetrievalSufficiency.INSUFFICIENT_COVERAGE


def test_sufficient_coverage(env: dict[str, Any]) -> None:
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    for index in range(3):
        add_material(
            env, account, f"材料{index}.txt",
            f"热力学第二定律的第 {index} 条详细阐述内容。",
            layer="knowledge_base",
        )
    # GQ-05：向量化调用失败 → 纯关键词路径无冲突可能，覆盖达标即充足
    keyword_only = LayeredRetrievalService(
        database=env["database"],
        embedding=_KeywordOnlyEmbeddingPort(),
        object_repository=env["repository"],
    )
    round_ = _run(
        env, account, conversation_id, "assistant-1", "热力学第二定律",
        retrieval=keyword_only,
    )
    assert round_ is not None
    assert round_.sufficiency == RetrievalSufficiency.SUFFICIENT
    assert len(round_.citations) >= 3


def test_index_unavailable_when_no_active_version(env: dict[str, Any]) -> None:
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    # 材料已就绪（有索引版本），随后索引活跃指针被移除（模拟索引失效的
    # 降级状态）：有可检索材料但没有可服务版本 → 结构化 index_unavailable
    add_material(env, account, "材料.txt", "热力学内容。", layer="knowledge_base")
    env["database"].connection.execute(
        "DELETE FROM index_active WHERE account_id = ?", (account,)
    )
    env["database"].connection.execute(
        "UPDATE index_versions SET status = 'obsolete' WHERE account_id = ?",
        (account,),
    )
    round_ = _run(env, account, conversation_id, "assistant-1", "热力学")
    assert round_ is not None
    assert round_.sufficiency == RetrievalSufficiency.INDEX_UNAVAILABLE
    assert _layer(round_, RetrievalSourceLayer.KNOWLEDGE_BASE).status == (
        RetrievalLayerStatus.INDEX_UNAVAILABLE
    )


# ---------------------------------------------------------------------------
# 引用详情：授权打开与安全状态
# ---------------------------------------------------------------------------


def test_citation_detail_accessible_with_download_entry(env: dict[str, Any]) -> None:
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    user_message_id = add_user_message(env, account, conversation_id, "热力学")
    add_material(
        env, account, "附件.pdf.txt", "热力学第二定律正文内容。", layer="attachment",
        conversation_id=conversation_id, user_message_id=user_message_id,
    )
    round_ = _run(env, account, conversation_id, "assistant-1", "热力学第二定律",
                  user_message_id=user_message_id)
    assert round_ is not None and round_.citations
    citation = round_.citations[0]
    detail = env["retrieval"].citation_detail(
        account, conversation_id, "assistant-1", citation.citation_id
    )
    assert detail.access_status == CitationAccessStatus.ACCESSIBLE
    assert detail.citation.filename == "附件.pdf.txt"
    assert detail.citation.snippet
    assert detail.download_url == (
        f"/chat/conversations/{conversation_id}/attachments/"
        f"{citation.object_id}/download"
    )


def test_citation_detail_deleted_shows_safe_status(env: dict[str, Any]) -> None:
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    add_material(env, account, "知识库.txt", "热力学内容。", layer="knowledge_base")
    round_ = _run(env, account, conversation_id, "assistant-1", "热力学")
    assert round_ is not None and round_.citations
    citation_id = round_.citations[0].citation_id
    # 删除对象（对象进入清理状态）
    env["repository"].delete_object(account, round_.citations[0].object_id)
    detail = env["retrieval"].citation_detail(
        account, conversation_id, "assistant-1", citation_id
    )
    assert detail.access_status == CitationAccessStatus.DELETED
    assert "已删除" in detail.access_message
    assert detail.download_url is None


def test_citation_detail_permission_changed_when_attachment_unbound(
    env: dict[str, Any],
) -> None:
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    user_message_id = add_user_message(env, account, conversation_id, "热力学")
    object_id = add_material(
        env, account, "附件.txt", "热力学内容。", layer="attachment",
        conversation_id=conversation_id, user_message_id=user_message_id,
    )
    round_ = _run(env, account, conversation_id, "assistant-1", "热力学",
                  user_message_id=user_message_id)
    assert round_ is not None and round_.citations
    citation_id = round_.citations[0].citation_id
    # 解绑附件（消息引用被删除）→ 权限变化
    env["database"].connection.execute(
        "DELETE FROM chat_attachments WHERE object_id = ? AND account_id = ?",
        (object_id, account),
    )
    detail = env["retrieval"].citation_detail(
        account, conversation_id, "assistant-1", citation_id
    )
    assert detail.access_status == CitationAccessStatus.PERMISSION_CHANGED
    assert "已从消息中移除" in detail.access_message
    assert detail.download_url is None


def test_citation_detail_project_layer_uses_project_download_entry(
    env: dict[str, Any],
) -> None:
    account = env["account_a"]
    project_id = seed_project(env, account)
    conversation_id = seed_conversation(env, account, project_id=project_id)
    add_material(
        env, account, "项目讲义.md", "热力学第二定律。",
        layer="project", project_id=project_id,
    )
    round_ = _run(env, account, conversation_id, "assistant-1", "热力学第二定律")
    assert round_ is not None and round_.citations
    detail = env["retrieval"].citation_detail(
        account, conversation_id, "assistant-1", round_.citations[0].citation_id
    )
    assert detail.access_status == CitationAccessStatus.ACCESSIBLE
    assert detail.download_url == (
        f"/learning-projects/{project_id}/files/"
        f"{round_.citations[0].object_id}/download"
    )


# ---------------------------------------------------------------------------
# 持久化与版本稳定性
# ---------------------------------------------------------------------------


def test_round_survives_reload_and_index_rebuild(env: dict[str, Any]) -> None:
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    add_material(env, account, "知识库.txt", "热力学第二定律详细内容。", layer="knowledge_base")
    round_ = _run(env, account, conversation_id, "assistant-1", "热力学第二定律")
    assert round_ is not None and round_.citations

    # 刷新后引用仍指向相同展示数据（重新读取轮次投影）
    reloaded = env["retrieval"].round_projection(account, "assistant-1")
    assert reloaded is not None
    assert reloaded.citations == round_.citations
    assert reloaded.index_version_id == round_.index_version_id

    # 索引重建（合同变化触发全量重建 → 新版本）后历史引用不漂移
    database: BridgesDatabase = env["database"]
    VersionedIndex(database, env["embedding"]).rebuild(account, embedding_available=True)
    rebuilt = env["retrieval"].round_projection(account, "assistant-1")
    assert rebuilt is not None
    assert rebuilt.citations == round_.citations
    assert rebuilt.index_version_id == round_.index_version_id


def test_multi_token_query_searches_all_terms(env: dict[str, Any]) -> None:
    """多词查询逐词合并：第一个词命中不阻断后续词的检索（窗口回退同理）。"""
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    add_material(env, account, "甲.txt", "entropy 概念介绍。", layer="knowledge_base")
    add_material(env, account, "乙.txt", "second law 相关论述。", layer="knowledge_base")
    round_ = _run(env, account, conversation_id, "assistant-1", "entropy second law")
    assert round_ is not None
    # 两个词都命中各自的材料（关键词路径逐词合并，不以第一个词结果代替）
    assert {c.filename for c in round_.citations} >= {"甲.txt", "乙.txt"}


def test_multi_chunk_document_yields_multiple_citations(env: dict[str, Any]) -> None:
    """同文档多分块是独立引用：跨层去重不得把不同页/章节折叠为一条。"""
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    # 超过最大块长（2000 字符）的长段落 → 硬切成多个分块
    long_content = "热力学第二定律的核心内容。" + ("熵增原理详细阐述。" * 300)
    add_material(env, account, "长讲义.txt", long_content, layer="knowledge_base")
    round_ = _run(env, account, conversation_id, "assistant-1", "热力学第二定律")
    assert round_ is not None
    # 同一文档多分块全部保留为独立引用，绝不折叠为 1 条
    assert len(round_.citations) >= 2
    assert {c.filename for c in round_.citations} == {"长讲义.txt"}


def test_layer_search_failure_propagates_index_unavailable(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """关键词检索失败（如 FTS 表损坏）必须结构化呈现索引不可用，
    绝不静默当作无命中（AC-07）。"""
    import sqlite3 as sqlite_module

    from bridges.retrieval import service as retrieval_service_module

    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    add_material(env, account, "材料.txt", "热力学第二定律内容。", layer="knowledge_base")

    def _broken_keyword_search(*args: object, **kwargs: object):
        raise sqlite_module.Error("fts broken")

    # 服务模块内已按名称导入 search_keyword：补丁必须打在服务模块引用上
    monkeypatch.setattr(
        retrieval_service_module, "search_keyword", _broken_keyword_search
    )
    round_ = _run(env, account, conversation_id, "assistant-1", "热力学第二定律")
    assert round_ is not None
    assert round_.sufficiency == RetrievalSufficiency.INDEX_UNAVAILABLE
    assert _layer(round_, RetrievalSourceLayer.KNOWLEDGE_BASE).status == (
        RetrievalLayerStatus.INDEX_UNAVAILABLE
    )


def test_round_projection_none_without_round(env: dict[str, Any]) -> None:
    account = env["account_a"]
    assert env["retrieval"].round_projection(account, "no-such-message") is None


def test_citation_detail_cross_message_404(env: dict[str, Any]) -> None:
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    add_material(env, account, "知识库.txt", "热力学内容。", layer="knowledge_base")
    round_ = _run(env, account, conversation_id, "assistant-1", "热力学")
    assert round_ is not None and round_.citations
    with pytest.raises(RetrievalError):
        env["retrieval"].citation_detail(
            account, conversation_id, "other-message", round_.citations[0].citation_id
        )


# ---------------------------------------------------------------------------
# Issue 15：检索查询向量审计锁（真实接缝 + 假客户端，锁与轮次一一对应）
# ---------------------------------------------------------------------------


class _FakeQwenClient:
    """记录 embeddings 请求的假客户端；``fail_with`` 置位时每次调用失败。"""

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_with = fail_with

    def embeddings(self, request_body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(dict(request_body))
        if self.fail_with is not None:
            raise self.fail_with
        count = len(request_body["input"])
        return {
            "model": "text-embedding-v4",
            "data": [
                {"index": i, "embedding": [0.1] * 1024} for i in range(count)
            ],
            "usage": {"total_tokens": count * 4},
        }


def _audited_port(storage: dict[str, Any], client: _FakeQwenClient):
    """真实接缝：注册矩阵 + QwenEmbeddingAdapter + 统一 recorder。"""
    from bridges.ai.capability_registry import CapabilityRegistry
    from bridges.ai.embedding_adapter import QwenEmbeddingAdapter
    from bridges.ai.model_gateway import ModelGateway
    from bridges.ai.production import register_builtin_capabilities
    from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder
    from bridges.ingestion.embedding import (
        EMBEDDING_CAPABILITY_NAME,
        EMBEDDING_CAPABILITY_VERSION,
        QwenEmbeddingPort,
    )

    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    gateway = ModelGateway(registry)
    gateway.register_adapter(
        EMBEDDING_CAPABILITY_NAME,
        EMBEDDING_CAPABILITY_VERSION,
        QwenEmbeddingAdapter(client),
    )
    recorder = SqliteModelRunLockRecorder(storage["database"])
    port = QwenEmbeddingPort(
        api_key=SecretStr("test-global-key"),
        gateway=gateway,
        recorder=recorder,
    )
    return port, recorder


def _audited_env(tmp_path) -> dict[str, Any]:
    """真实接缝驱动的摄取 + 检索环境（与 conftest.make_retrieval_env 同构）。"""
    import secrets as _secrets

    from bridges.chat.repository import ConversationRepository
    from bridges.ingestion.index import VersionedIndex
    from bridges.ingestion.service import IngestionService
    from tests.retrieval.conftest import make_storage

    storage = make_storage(tmp_path)
    client = _FakeQwenClient()
    port, recorder = _audited_port(storage, client)
    ingestion = IngestionService(
        database=storage["database"],
        object_repository=storage["repository"],
        embedding=port,
        index=VersionedIndex(storage["database"], port),
    )
    retrieval = LayeredRetrievalService(
        database=storage["database"],
        embedding=port,
        object_repository=storage["repository"],
    )
    return {
        **storage,
        "embedding": port,
        "recorder": recorder,
        "client": client,
        "ingestion": ingestion,
        "retrieval": retrieval,
        "conversations": ConversationRepository(storage["database"]),
        "_secrets": _secrets,
    }


def test_query_vector_success_records_one_retrieval_query_lock(tmp_path) -> None:
    env = _audited_env(tmp_path)
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    add_material(
        env, account, "材料.txt", "量子计算中的叠加态与纠缠。", layer="knowledge_base"
    )
    assistant_message_id = f"assistant-{env['_secrets'].token_urlsafe(8)}"

    round_ = _run(
        env, account, conversation_id, assistant_message_id, "量子计算"
    )
    assert round_ is not None

    # 单轮成功向量化恰好一条 retrieval_query 锁，关联到本轮 round_id
    locks = env["recorder"].list_locks_by_run(account, round_.round_id)
    assert len(locks) == 1
    lock = locks[0]
    assert lock.status == ModelCallStatus.SUCCESS
    assert lock.business_refs[0].operation == "retrieval_query"
    assert lock.business_refs[0].object_type == "retrieval_round"
    assert lock.business_refs[0].object_id == round_.round_id
    assert lock.parameters["batch_size"] == 1
    assert lock.parameters["dimensions"] == 1024
    # 摄取向量化与查询向量化各一次真实调用；最后一次是查询向量
    assert len(env["client"].calls) == 2
    assert env["client"].calls[-1]["input"] == ["量子计算"]


def test_query_vector_failure_keeps_keyword_results_and_failure_lock(tmp_path) -> None:
    """查询向量化失败：关键词结果仍可用，失败锁保留并给出诚实说明。"""
    from bridges.chat.repository import ConversationRepository
    from bridges.ingestion.index import VersionedIndex
    from bridges.ingestion.service import IngestionService
    from tests.retrieval.conftest import make_storage

    storage = make_storage(tmp_path)
    # 摄取用确定性端口完成索引；检索用真实接缝（客户端必然失败）
    deterministic = DeterministicEmbeddingPort()
    ingestion = IngestionService(
        database=storage["database"],
        object_repository=storage["repository"],
        embedding=deterministic,
        index=VersionedIndex(storage["database"], deterministic),
    )
    env = {
        **storage,
        "ingestion": ingestion,
        "conversations": ConversationRepository(storage["database"]),
    }
    account = storage["account_a"]
    conversation_id = seed_conversation(env, account)
    add_material(
        env, account, "材料.txt", "量子计算中的叠加态与纠缠。", layer="knowledge_base"
    )

    client = _FakeQwenClient(fail_with=AuthError("bad key"))
    port, recorder = _audited_port(storage, client)
    retrieval = LayeredRetrievalService(
        database=storage["database"],
        embedding=port,
        object_repository=storage["repository"],
    )
    round_ = retrieval.run_round(
        account,
        conversation_id,
        f"assistant-{secrets.token_urlsafe(8)}",
        None,
        "量子计算",
        use_knowledge_base=True,
    )
    assert round_ is not None
    # 关键词结果仍可用（诚实降级），说明呈现向量不可用
    assert round_.citations
    assert "向量检索暂不可用" in (round_.note or "")
    # 失败锁保留：状态 blocked、稳定错误码 auth_error
    locks = recorder.list_locks_by_run(account, round_.round_id)
    assert len(locks) == 1
    assert locks[0].status == ModelCallStatus.BLOCKED
    assert locks[0].error_code == "auth_error"
    assert locks[0].business_refs[0].operation == "retrieval_query"


def test_no_embedding_port_no_calls_no_locks(env: dict[str, Any]) -> None:
    """完全不配置 Embedding：无远端调用、无锁，检索给出诚实说明。"""
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    add_material(
        env, account, "材料.txt", "热力学第二定律内容。", layer="knowledge_base"
    )

    retrieval = LayeredRetrievalService(
        database=env["database"],
        embedding=None,
        object_repository=env["repository"],
    )
    round_ = retrieval.run_round(
        account,
        conversation_id,
        f"assistant-{secrets.token_urlsafe(8)}",
        None,
        "热力学",
        use_knowledge_base=True,
    )
    assert round_ is not None
    assert round_.citations  # 关键词路径独立可用
    assert "向量检索暂不可用" in (round_.note or "")
    rows = env["database"].connection.execute(
        "SELECT count(*) AS count FROM model_run_locks"
    ).fetchone()
    assert int(rows["count"]) == 0  # 纯本地步骤不建模型锁
