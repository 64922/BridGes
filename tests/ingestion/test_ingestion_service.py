"""Issue 17：摄取状态机——领取、处理、失败重试、租约恢复与账户隔离。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import (
    make_ingestion,
    upload_text,
)

from bridges.ai.ports import EmbeddingContext
from bridges.contracts.ingestion import DocumentIngestionStatus
from bridges.ingestion.embedding import DeterministicEmbeddingPort, EmbeddingError
from bridges.ingestion.ocr import OcrError, OcrPageRequest
from bridges.ingestion.service import IngestionService

TEXT_CONTENT = "这是一份测试文档。\n\n包含两个段落，用于验证分块与索引。\n"
#: TEXT_CONTENT 在目标块长内合并为一个分块。
EXPECTED_CHUNKS = 1
IMAGE_CONTENT = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\x0dIHDR"
    b"\x00\x00\x03\x20\x00\x00\x02\x58"
    b"\x08\x06\x00\x00\x00"
    b"\x00" * 40
)


def upload_image(storage, account_id: str, filename: str = "题目.png") -> str:
    stored = storage["repository"].create_object(
        account_id,
        filename,
        IMAGE_CONTENT,
        media_type="image/png",
    )
    return stored.object_id


class _FakeOcrPort:
    def __init__(self, text: str | None = None, error: bool = False) -> None:
        self.text = text
        self.error = error
        self.calls: list[OcrPageRequest] = []

    def extract(self, request: OcrPageRequest) -> str:
        self.calls.append(request)
        if self.error:
            raise OcrError("图片文字识别失败：服务暂时不可用。")
        assert self.text is not None
        return self.text


def _ready_projection(service: IngestionService, account_id: str, object_id: str):
    projection = service.projection(account_id, object_id)
    assert projection is not None
    return projection


def test_queued_document_becomes_ready_with_structure(storage) -> None:
    service, embedding = make_ingestion(storage, embedding_available=True)
    account_id = storage["account_a"]
    object_id = upload_text(storage, account_id, "材料.txt", TEXT_CONTENT.encode("utf-8"))

    service.enqueue(account_id, object_id)
    queued = _ready_projection(service, account_id, object_id)
    assert queued.status == DocumentIngestionStatus.QUEUED

    summary = service.process_pending()
    assert "处理 1 份文档" in summary
    ready = _ready_projection(service, account_id, object_id)
    assert ready.status == DocumentIngestionStatus.READY
    assert ready.title == "这是一份测试文档。"
    assert ready.chunk_count == EXPECTED_CHUNKS
    assert ready.vector_enabled is True
    assert ready.vector_indexed is True
    assert ready.failure_stage is None
    assert embedding.embed_calls  # 向量化真实发生了

    # 全文索引可检索：FTS 包含分块内容
    row = storage["database"].connection.execute(
        "SELECT count(*) AS count FROM fts_chunks WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    assert row is not None and int(row["count"]) == EXPECTED_CHUNKS


def test_embedding_unavailable_reports_reason_without_faking(storage) -> None:
    """未构造全局 Embedding 端口时如实报告不可用，不伪装向量就绪（GQ-05）。

    Embedding 可用性由运行时是否构造全局端口决定（不再是账户探测门）：
    缺少端口时 worker 待机不处理，索引状态投影给出中文原因；端口已构造
    但调用失败（认证/限流等）的诚实降级由失败重试用例覆盖。
    """
    service, embedding = make_ingestion(storage, embedding_available=False)
    account_id = storage["account_a"]
    object_id = upload_text(storage, account_id, "材料.txt", TEXT_CONTENT.encode("utf-8"))

    service.enqueue(account_id, object_id)
    summary = service.process_pending()
    assert "未启用" in summary  # 缺少向量化组件：worker 待机，不假成功
    projection = _ready_projection(service, account_id, object_id)
    assert projection.status == DocumentIngestionStatus.QUEUED
    status = service.index_status(account_id)
    assert status.embedding_available is False
    assert status.vector_unavailable_reason is not None
    assert "Embedding" in status.vector_unavailable_reason
    assert not embedding.embed_calls  # 不得以 Stub/空向量标记成功
    row = storage["database"].connection.execute(
        "SELECT count(*) AS count FROM index_vectors", ()
    ).fetchone()
    assert int(row["count"]) == 0


def test_parse_failure_keeps_object_and_chinese_reason(storage) -> None:
    service, _ = make_ingestion(storage, embedding_available=True)
    account_id = storage["account_a"]
    stored = storage["repository"].create_object(
        account_id, "坏文件.txt", b"not utf-8: \xff\xfe\x00", media_type="text/plain"
    )
    object_id = stored.object_id
    service.enqueue(account_id, object_id)
    service.process_pending()

    projection = _ready_projection(service, account_id, object_id)
    assert projection.status == DocumentIngestionStatus.ERROR
    assert projection.failure_stage == "parse"
    assert "UTF-8" in (projection.failure_reason or "")
    # 原对象保留，可再次读取
    content = storage["repository"].get_content(account_id, object_id)
    assert content == b"not utf-8: \xff\xfe\x00"


class _FlakyEmbeddingPort(DeterministicEmbeddingPort):
    """前 N 次调用抛 EmbeddingError，之后正常。"""

    def __init__(self, failures: int, dimensions: int = 1024) -> None:
        super().__init__(dimensions=dimensions)
        self._failures = failures

    def embed(
        self,
        account_id: str,
        texts: list[str],
        *,
        context: EmbeddingContext | None = None,
    ) -> list[list[float]]:
        if self._failures > 0:
            self._failures -= 1
            raise EmbeddingError("向量化失败：服务暂时不可用或网络异常，请稍后重试。")
        return super().embed(account_id, texts, context=context)


def test_embed_failure_degrades_to_keyword_and_recovers(storage) -> None:
    """GQ-05：向量化调用失败 → 全文索引仍独立完成（关键词检索降级）。

    文档保持 ready 可检索（不写空向量、不伪装向量就绪）；能力恢复后
    （如重启后全局 Key 生效）索引维护检测向量覆盖不全触发全量重建
    补向量，且不产生重复分块。
    """
    failing = _FlakyEmbeddingPort(failures=999)
    service, _ = make_ingestion(storage, embedding_available=True, embedding=failing)
    account_id = storage["account_a"]
    object_id = upload_text(storage, account_id, "材料.txt", TEXT_CONTENT.encode("utf-8"))

    service.enqueue(account_id, object_id)
    service.process_pending()
    degraded = _ready_projection(service, account_id, object_id)
    assert degraded.status == DocumentIngestionStatus.READY
    assert degraded.vector_enabled is False
    assert degraded.vector_indexed is False
    # 全文索引已独立完成，向量行未写（不写空向量）
    fts = storage["database"].connection.execute(
        "SELECT count(*) AS count FROM fts_chunks WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    assert int(fts["count"]) == EXPECTED_CHUNKS
    vectors = storage["database"].connection.execute(
        "SELECT count(*) AS count FROM index_vectors", ()
    ).fetchone()
    assert int(vectors["count"]) == 0

    # 能力恢复：同一数据目录的新 worker（确定性端口）检测向量覆盖不全
    # → 全量重建补齐并原子切换（模拟重启后全局 Key 生效）。
    recovered, _ = make_ingestion(storage, embedding_available=True)
    recovered.process_pending()
    ready = _ready_projection(recovered, account_id, object_id)
    assert ready.status == DocumentIngestionStatus.READY
    assert ready.vector_indexed is True

    # 无重复分块：文档分块行与向量行数与单次成功路径一致
    chunks = storage["database"].connection.execute(
        "SELECT count(*) AS count FROM document_chunks WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    assert int(chunks["count"]) == EXPECTED_CHUNKS
    vectors = storage["database"].connection.execute(
        "SELECT count(*) AS count FROM index_vectors", ()
    ).fetchone()
    assert int(vectors["count"]) == EXPECTED_CHUNKS


def test_retry_is_idempotent_across_ticks(storage) -> None:
    service, _ = make_ingestion(storage, embedding_available=True)
    account_id = storage["account_a"]
    object_id = upload_text(storage, account_id, "材料.txt", TEXT_CONTENT.encode("utf-8"))
    service.enqueue(account_id, object_id)
    service.process_pending()
    service.process_pending()
    service.process_pending()

    chunks = storage["database"].connection.execute(
        "SELECT count(*) AS count FROM document_chunks WHERE account_id = ?", (account_id,)
    ).fetchone()
    vectors = storage["database"].connection.execute(
        "SELECT count(*) AS count FROM index_vectors", ()
    ).fetchone()
    fts = storage["database"].connection.execute(
        "SELECT count(*) AS count FROM fts_chunks WHERE account_id = ?", (account_id,)
    ).fetchone()
    assert int(chunks["count"]) == EXPECTED_CHUNKS
    assert int(vectors["count"]) == EXPECTED_CHUNKS
    assert int(fts["count"]) == EXPECTED_CHUNKS


def test_interrupted_task_recovered_after_lease_expiry(storage) -> None:
    """Issue 43：进程中断（租约过期）后按队列契约自动恢复重领。"""
    service, _ = make_ingestion(storage, embedding_available=True)
    account_id = storage["account_a"]
    object_id = upload_text(storage, account_id, "材料.txt", TEXT_CONTENT.encode("utf-8"))
    service.enqueue(account_id, object_id)

    # 模拟中断：领取后不完成处理，让队列租约过期（崩溃恢复的唯一规则）。
    claim = service._task_queue.claim_next("ingestion", "test-worker")  # noqa: SLF001
    assert claim is not None
    stale = datetime.now(UTC) - timedelta(seconds=3600)
    storage["database"].connection.execute(
        "UPDATE task_claims SET lease_expires_at = ? WHERE claim_id = ?",
        (stale.isoformat(timespec="seconds"), claim.claim_id),
    )
    storage["database"].connection.commit()

    # 租约过期：任务可被重领（同任务不重复执行——原任务从未完成）。
    service.process_pending()
    ready = _ready_projection(service, account_id, object_id)
    assert ready.status == DocumentIngestionStatus.READY


def test_same_content_different_accounts_isolated(storage) -> None:
    service, embedding = make_ingestion(storage, embedding_available=True)
    account_a = storage["account_a"]
    account_b = storage["account_b"]
    object_a = upload_text(storage, account_a, "共享内容.txt", TEXT_CONTENT.encode("utf-8"))
    object_b = upload_text(storage, account_b, "共享内容.txt", TEXT_CONTENT.encode("utf-8"))

    service.enqueue(account_a, object_a)
    service.enqueue(account_b, object_b)
    service.process_pending()

    ready_a = _ready_projection(service, account_a, object_a)
    ready_b = _ready_projection(service, account_b, object_b)
    assert ready_a.status == DocumentIngestionStatus.READY
    assert ready_b.status == DocumentIngestionStatus.READY

    # 跨账户不可见：B 的查询不能看到 A 的记录
    assert service.projection(account_b, object_a) is None
    assert service.projection(account_a, object_b) is None

    # 向量与全文都按账户隔离
    rows = storage["database"].connection.execute(
        "SELECT account_id, count(*) AS count FROM index_vectors"
        " GROUP BY account_id"
    ).fetchall()
    assert {str(r["account_id"]): int(r["count"]) for r in rows} == {
        account_a: EXPECTED_CHUNKS,
        account_b: EXPECTED_CHUNKS,
    }
    rows = storage["database"].connection.execute(
        "SELECT account_id, count(*) AS count FROM fts_chunks"
        " GROUP BY account_id"
    ).fetchall()
    assert {str(r["account_id"]): int(r["count"]) for r in rows} == {
        account_a: EXPECTED_CHUNKS,
        account_b: EXPECTED_CHUNKS,
    }


def test_parse_cache_reused_within_account(storage) -> None:
    service, _ = make_ingestion(storage, embedding_available=True)
    account_id = storage["account_a"]
    object_1 = upload_text(storage, account_id, "一.txt", TEXT_CONTENT.encode("utf-8"))
    object_2 = upload_text(storage, account_id, "二.txt", TEXT_CONTENT.encode("utf-8"))

    service.enqueue(account_id, object_1)
    service.enqueue(account_id, object_2)
    service.process_pending()

    # 相同内容复用解析缓存：缓存只有一条记录，两份文档各自有分块（不混淆来源）
    cache = storage["database"].connection.execute(
        "SELECT count(*) AS count FROM document_parse_cache WHERE account_id = ?", (account_id,)
    ).fetchone()
    assert int(cache["count"]) == 1
    for object_id in (object_1, object_2):
        ready = _ready_projection(service, account_id, object_id)
        assert ready.status == DocumentIngestionStatus.READY
        assert ready.chunk_count == EXPECTED_CHUNKS


def test_empty_document_marks_empty_status(storage) -> None:
    service, _ = make_ingestion(storage, embedding_available=True)
    account_id = storage["account_a"]
    stored = storage["repository"].create_object(
        account_id, "空白.txt", b"   \n\n  ", media_type="text/plain"
    )
    object_id = stored.object_id
    service.enqueue(account_id, object_id)
    service.process_pending()

    projection = _ready_projection(service, account_id, object_id)
    assert projection.status == DocumentIngestionStatus.EMPTY
    assert projection.chunk_count == 0
    assert "没有可索引" in (projection.failure_reason or "")


def test_object_deletion_purges_ingestion_records(storage) -> None:
    service, _ = make_ingestion(storage, embedding_available=True)
    account_id = storage["account_a"]
    object_id = upload_text(storage, account_id, "材料.txt", TEXT_CONTENT.encode("utf-8"))
    service.enqueue(account_id, object_id)
    service.process_pending()

    storage["repository"].delete_object(account_id, object_id)
    summary = service.process_pending()
    assert "清理 1 条孤立摄取记录" in summary
    assert service.projection(account_id, object_id) is None
    rows = storage["database"].connection.execute(
        "SELECT count(*) AS count FROM document_chunks WHERE account_id = ?", (account_id,)
    ).fetchone()
    assert int(rows["count"]) == 0
    rows = storage["database"].connection.execute(
        "SELECT count(*) AS count FROM fts_chunks", ()
    ).fetchone()
    assert int(rows["count"]) == 0


def test_unsupported_media_type_not_enqueued(storage) -> None:
    service, _ = make_ingestion(storage, embedding_available=True)
    account_id = storage["account_a"]
    stored = storage["repository"].create_object(
        account_id,
        "表格.xlsx",
        b"PK\x03\x04fake",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    service.enqueue(account_id, stored.object_id)
    assert service.projection(account_id, stored.object_id) is None


def test_enqueue_idempotent_does_not_duplicate(storage) -> None:
    service, _ = make_ingestion(storage, embedding_available=True)
    account_id = storage["account_a"]
    object_id = upload_text(storage, account_id, "材料.txt", TEXT_CONTENT.encode("utf-8"))
    service.enqueue(account_id, object_id)
    service.enqueue(account_id, object_id)
    service.enqueue(account_id, object_id)
    rows = storage["database"].connection.execute(
        "SELECT count(*) AS count FROM document_records WHERE account_id = ?", (account_id,)
    ).fetchone()
    assert int(rows["count"]) == 1


def test_claim_does_not_starve_documents_behind_unclaimable_oldest(
    storage,
) -> None:
    """领取饥饿回归：最旧的一批文档不可领取时，后续文档仍会被处理。"""
    service, _ = make_ingestion(storage, embedding_available=True)
    account_id = storage["account_a"]
    object_ids = []
    for index in range(6):
        object_id = upload_text(
            storage, account_id, f"材料{index}.txt",
            f"第 {index} 份文档内容。\n".encode(),
        )
        service.enqueue(account_id, object_id)
        object_ids.append(object_id)

    # 第一轮：最多领取 5 份，其余保持 queued
    service.process_pending()
    ready_count = sum(
        1
        for object_id in object_ids
        if _ready_projection(service, account_id, object_id).status
        == DocumentIngestionStatus.READY
    )
    assert ready_count == 5

    # 把已就绪的 5 份置为最旧、第 6 份置为最新：旧代码会因内层 LIMIT 5
    # 始终选中不可领取的最旧文档而饿死第 6 份。
    storage["database"].connection.execute(
        "UPDATE document_records SET created_at = '2020-01-01T00:00:00+00:00'"
        " WHERE status = 'ready' AND account_id = ?",
        (account_id,),
    )
    storage["database"].connection.execute(
        "UPDATE document_records SET created_at = '2099-01-01T00:00:00+00:00'"
        " WHERE status = 'queued' AND account_id = ?",
        (account_id,),
    )
    storage["database"].connection.commit()

    service.process_pending()
    all_ready = all(
        _ready_projection(service, account_id, object_id).status
        == DocumentIngestionStatus.READY
        for object_id in object_ids
    )
    assert all_ready


def test_permanent_failure_stops_auto_retry_until_manual_retry(storage) -> None:
    """自动重试上限：永久失败（损坏文件）不再被后台自动领取。"""
    service, _ = make_ingestion(storage, embedding_available=True)
    account_id = storage["account_a"]
    stored = storage["repository"].create_object(
        account_id, "坏文件.pdf", b"%PDF-1.7\ngarbage", media_type="application/pdf"
    )
    object_id = stored.object_id
    service.enqueue(account_id, object_id)

    service.process_pending()
    failed = _ready_projection(service, account_id, object_id)
    assert failed.status == DocumentIngestionStatus.ERROR
    assert failed.failure_stage == "parse"
    # 永久失败把计数推到上限：不再自动重试
    assert failed.retry_count >= 3

    service.process_pending()
    service.process_pending()
    still_failed = _ready_projection(service, account_id, object_id)
    assert still_failed.status == DocumentIngestionStatus.ERROR
    assert still_failed.retry_count == failed.retry_count  # 未被重新领取

    # 用户手动重试：计数重置并可再次处理
    service.mark_retry(account_id, object_id)
    retried = _ready_projection(service, account_id, object_id)
    assert retried.status == DocumentIngestionStatus.QUEUED
    assert retried.retry_count == 0


def test_object_deletion_decrements_index_version_counts(storage) -> None:
    """purge 后索引版本计数同步递减（投影不失真）。"""
    service, _ = make_ingestion(storage, embedding_available=True)
    account_id = storage["account_a"]
    object_id = upload_text(storage, account_id, "材料.txt", TEXT_CONTENT.encode("utf-8"))
    service.enqueue(account_id, object_id)
    service.process_pending()

    index = service._index
    assert index is not None
    active = index.active_version(account_id)
    assert active is not None and int(active["chunk_count"]) == EXPECTED_CHUNKS

    storage["repository"].delete_object(account_id, object_id)
    service.process_pending()
    active = index.active_version(account_id)
    assert active is not None
    assert int(active["chunk_count"]) == 0
    assert int(active["vector_count"]) == 0


def test_image_ocr_text_is_indexed_and_material_is_ready(storage) -> None:
    ocr = _FakeOcrPort("三角形面积公式 S=ah/2")
    service, _ = make_ingestion(storage, ocr=ocr)
    account_id = storage["account_a"]
    object_id = upload_image(storage, account_id)

    service.enqueue(account_id, object_id)
    service.process_pending()

    projection = _ready_projection(service, account_id, object_id)
    assert projection.status == DocumentIngestionStatus.READY
    assert projection.failure_reason is None
    assert len(ocr.calls) == 1
    request = ocr.calls[0]
    assert request.account_id == account_id
    assert request.object_id == object_id
    assert request.document_id == projection.document_id
    assert request.page_ordinal == 1
    assert request.call_ordinal == 1
    assert request.media_type == "image/png"
    assert request.content == IMAGE_CONTENT
    assert request.content_hash == projection.content_hash
    assert request.run_id.startswith(f"ingestion-ocr:{account_id}:")
    assert "knowledge-base-ocr-" not in request.run_id
    # 摄取投影给出脱敏汇总与来源证据：1 页真实 OCR 成功，非缓存命中。
    assert projection.ocr_pages_total == 1
    assert projection.ocr_pages_succeeded == 1
    assert projection.ocr_pages_failed == 0
    assert projection.parse_cache_hit is False
    assert projection.ocr_evidence_run_id == request.run_id
    row = storage["database"].connection.execute(
        "SELECT content FROM document_chunks WHERE document_id = ?",
        (projection.document_id,),
    ).fetchone()
    assert row is not None
    assert "三角形面积公式 S=ah/2" in str(row["content"])


def test_image_ocr_failure_falls_back_honestly_and_stays_ready(storage) -> None:
    ocr = _FakeOcrPort(error=True)
    service, _ = make_ingestion(storage, ocr=ocr)
    account_id = storage["account_a"]
    object_id = upload_image(storage, account_id)

    service.enqueue(account_id, object_id)
    service.process_pending()

    projection = _ready_projection(service, account_id, object_id)
    assert projection.status == DocumentIngestionStatus.READY
    assert projection.failure_stage is None
    # 失败页如实计入脱敏汇总，且不被标成「已识别」。
    assert projection.ocr_pages_total == 1
    assert projection.ocr_pages_succeeded == 0
    assert projection.ocr_pages_failed == 1
    assert projection.parse_cache_hit is False
    row = storage["database"].connection.execute(
        "SELECT content FROM document_chunks WHERE document_id = ?",
        (projection.document_id,),
    ).fetchone()
    assert row is not None
    content = str(row["content"])
    assert "图片内容未做文字识别" in content
    assert "题目.png" in content


def test_image_ocr_without_port_falls_back_honestly_and_stays_ready(storage) -> None:
    service, _ = make_ingestion(storage, ocr=None)
    account_id = storage["account_a"]
    object_id = upload_image(storage, account_id)

    service.enqueue(account_id, object_id)
    service.process_pending()

    projection = _ready_projection(service, account_id, object_id)
    assert projection.status == DocumentIngestionStatus.READY
    row = storage["database"].connection.execute(
        "SELECT content FROM document_chunks WHERE document_id = ?",
        (projection.document_id,),
    ).fetchone()
    assert row is not None
    assert "图片内容未做文字识别" in str(row["content"])


@pytest.mark.parametrize(
    "stale_version",
    [
        # 无 OCR 时代的元数据解析缓存。
        "image-metadata-v1",
        # Issue 07 之前的「科学图片」提示词识别结果：提示词变化后必须重新
        # 识别，旧账号不得继续使用带学科预设的文本。
        "image-ocr-v1",
    ],
)
def test_image_parse_cache_version_expiry_reparses_with_ocr(
    storage, stale_version
) -> None:
    ocr = _FakeOcrPort("缓存失效后重新识别")
    service, _ = make_ingestion(storage, ocr=ocr)
    account_id = storage["account_a"]
    object_id = upload_image(storage, account_id)
    service.enqueue(account_id, object_id)

    object_row = storage["database"].connection.execute(
        "SELECT content_hash FROM objects WHERE object_id = ?", (object_id,)
    ).fetchone()
    assert object_row is not None
    legacy_text = "图片：题目.png\n类型：image/png\n尺寸：800×600 像素\n大小：52 字节\n"
    legacy = {
        "title": "题目",
        "text": legacy_text,
        "spans": [{"start": 0, "end": len(legacy_text), "page": None, "section": None}],
        "page_count": 0,
        "section_count": 0,
        "parser_version": stale_version,
    }

    with storage["database"].transaction():
        storage["database"].connection.execute(
            "INSERT INTO document_parse_cache"
            " (account_id, content_hash, parser_version, parsed_json, created_at)"
            " VALUES (?, ?, ?, ?, datetime('now'))",
            (
                account_id,
                str(object_row["content_hash"]),
                stale_version,
                json.dumps(legacy, ensure_ascii=False),
            ),
        )

    service.process_pending()

    projection = _ready_projection(service, account_id, object_id)
    assert projection.status == DocumentIngestionStatus.READY
    # 缓存版本失效 → 重新识别：真实调用发生，非缓存命中，证据指向本轮 run。
    assert len(ocr.calls) == 1
    assert projection.parse_cache_hit is False
    assert projection.ocr_evidence_run_id == ocr.calls[0].run_id
    row = storage["database"].connection.execute(
        "SELECT content FROM document_chunks WHERE document_id = ?",
        (projection.document_id,),
    ).fetchone()
    assert row is not None
    assert "缓存失效后重新识别" in str(row["content"])


def test_same_image_content_reuses_ocr_parse_cache(storage) -> None:
    """相同内容第二次摄取：0 次 Qwen 调用、0 条新锁，缓存来源可追溯。"""
    ocr = _FakeOcrPort("相同图片内容")
    service, _ = make_ingestion(storage, ocr=ocr)
    account_id = storage["account_a"]
    object_1 = upload_image(storage, account_id, "第一张.png")
    object_2 = storage["repository"].create_object(
        account_id,
        "第二张.png",
        IMAGE_CONTENT,
        media_type="image/png",
    ).object_id

    service.enqueue(account_id, object_1)
    service.enqueue(account_id, object_2)
    service.process_pending()

    # 第一次真实调用后第二次直接命中解析缓存：调用增量必须为 0。
    assert len(ocr.calls) == 1
    rows = storage["database"].connection.execute(
        "SELECT COUNT(*) AS count FROM document_chunks WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    assert rows is not None and int(rows["count"]) == 2

    first = _ready_projection(service, account_id, object_1)
    second = _ready_projection(service, account_id, object_2)
    assert first.status == DocumentIngestionStatus.READY
    assert second.status == DocumentIngestionStatus.READY
    # 第一份是真实 OCR（cache_hit=False，证据=原始 run）；第二份是缓存命中
    # （cache_hit=True，证据引用同一原始 run，不伪造本轮模型成功）。
    assert first.parse_cache_hit is False
    assert first.ocr_pages_total == 1 and first.ocr_pages_succeeded == 1
    assert first.ocr_evidence_run_id is not None
    assert second.parse_cache_hit is True
    assert second.ocr_pages_total == 0
    assert second.ocr_evidence_run_id == first.ocr_evidence_run_id
    # 模型锁同样零增量：锁表只有第一次调用留下的一条。
    count = storage["database"].connection.execute(
        "SELECT COUNT(*) AS count FROM model_run_locks WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    assert count is not None and int(count["count"]) == 0
