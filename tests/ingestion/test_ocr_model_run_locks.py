"""Issue 14：知识库 OCR 页级运行锁——服务与端口全链路合同。

用真实 ``ModelGateway``（注册 ``qwen_ocr`` 能力 + 可编程适配器）与真实
SQLite recorder 构造生产形态的 ``QwenOcrPort``，再注入摄取服务，断言：

- 实际 adapter 调用数与页级锁数一一对应，阶段/序号与业务关联正确；
- 多页部分失败：每页独立锁与状态，锁数等于实际请求数；
- 相同内容缓存命中：Qwen 调用增量与模型锁增量均为 0，来源可追溯；
- 解析版本失效后的重新识别新增锁，原锁不被覆盖；
- OCR 返回后、解析结果保存前崩溃：锁仍持久化，恢复重跑产生新 run 新锁；
- 跨账户隔离：缓存与锁严格按账户策略隔离，共享全局 Key 不串户；
- recorder 写失败：失败关闭，绝不形成「已 OCR」投影。
"""

from __future__ import annotations

from typing import Any

from conftest import make_ingestion

from bridges.ai import CapabilityRegistry, ModelGateway
from bridges.ai.adapters import AdapterError, AdapterResult
from bridges.ai.fixed_models import OCR_MODEL_ID
from bridges.ai.production import register_builtin_capabilities
from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder
from bridges.contracts.ai import CapabilityRecord, ModelCallStatus
from bridges.contracts.ingestion import DocumentIngestionStatus
from bridges.contracts.workflows import RunContextEnvelope
from bridges.ingestion.ocr import OcrError, QwenOcrPort
from bridges.storage import BridgesDatabase
from bridges.storage.database import SCHEMA_VERSION

IMAGE_CONTENT = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\x0dIHDR"
    b"\x00\x00\x03\x20\x00\x00\x02\x58"
    b"\x08\x06\x00\x00\x00"
    b"\x00" * 40
)


class _ProgrammableAdapter:
    """按调用顺序返回预设结果或抛出错误（记录真实调用次数）。"""

    def __init__(self, responses: list[AdapterResult | AdapterError]) -> None:
        self.responses = list(responses)
        self.call_count = 0

    def call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> AdapterResult:
        self.call_count += 1
        if not self.responses:
            raise AdapterError(
                code="missing_image", message="exhausted", retryable=False
            )
        item = self.responses.pop(0)
        if isinstance(item, AdapterError):
            raise item
        return item


def _success(content: str = "锁测试可检索文字") -> AdapterResult:
    return AdapterResult(
        actual_model_id=OCR_MODEL_ID,
        output={"content": content},
    )


def _build_port(storage: dict[str, Any], adapter: _ProgrammableAdapter) -> QwenOcrPort:
    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_ocr", "1", adapter)
    return QwenOcrPort(
        gateway=gateway,
        recorder=SqliteModelRunLockRecorder(storage["database"]),
    )


def _upload_image(storage: dict[str, Any], account_id: str, filename: str) -> str:
    stored = storage["repository"].create_object(
        account_id, filename, IMAGE_CONTENT, media_type="image/png"
    )
    return stored.object_id


def _lock_rows(storage: dict[str, Any]) -> list[Any]:
    return storage["database"].connection.execute(
        "SELECT * FROM model_run_locks ORDER BY lock_id", ()
    ).fetchall()


def _link_rows(storage: dict[str, Any]) -> list[Any]:
    return storage["database"].connection.execute(
        "SELECT * FROM model_run_lock_links ORDER BY lock_id", ()
    ).fetchall()


def test_single_image_success_records_exactly_one_page_lock(storage) -> None:
    adapter = _ProgrammableAdapter([_success()])
    port = _build_port(storage, adapter)
    service, _ = make_ingestion(storage, ocr=port)
    account_id = storage["account_a"]
    object_id = _upload_image(storage, account_id, "题目.png")

    service.enqueue(account_id, object_id)
    service.process_pending()

    projection = service.projection(account_id, object_id)
    assert projection is not None
    assert projection.status == DocumentIngestionStatus.READY
    # 1 次真实 adapter 调用 == 1 条页级锁。
    assert adapter.call_count == 1
    locks = _lock_rows(storage)
    assert len(locks) == 1
    lock = locks[0]
    assert str(lock["account_id"]) == account_id
    assert str(lock["capability_name"]) == "qwen_ocr"
    assert str(lock["actual_model_id"]) == OCR_MODEL_ID
    assert str(lock["status"]) == ModelCallStatus.SUCCESS.value
    assert str(lock["run_id"]) == projection.ocr_evidence_run_id
    assert str(lock["run_id"]).startswith(f"ingestion-ocr:{account_id}:")
    assert "knowledge-base-ocr-" not in str(lock["run_id"])

    links = _link_rows(storage)
    assert len(links) == 1
    assert str(links[0]["object_type"]) == "document"
    assert str(links[0]["object_id"]) == projection.document_id
    assert str(links[0]["operation"]) == "ocr_page:1"
    assert int(links[0]["attempt_ordinal"]) == 1
    assert int(links[0]["is_primary"]) == 1
    # 锁参数绝不含图片 base64、prompt 或 OCR 文本。
    assert "image_base64" not in str(lock["parameters"])
    assert "prompt" not in str(lock["parameters"])
    assert "OCR" not in str(lock["parameters"])


def test_multi_page_partial_failure_records_independent_page_locks(
    storage,
) -> None:
    """多页部分失败：锁数 == 实际请求数，各页状态独立、可排序查询。"""
    # 页 2 使用非重试错误（失败关闭），请求与响应一一对应：
    # 页 1 成功、页 2 适配器失败、页 3 成功。
    adapter = _ProgrammableAdapter(
        [
            _success(content="页一文字"),
            AdapterError(code="missing_image", message="no image", retryable=False),
            _success(content="页三文字"),
        ]
    )
    port = _build_port(storage, adapter)
    account_id = storage["account_a"]
    recorder = SqliteModelRunLockRecorder(storage["database"])
    run_id = f"ingestion-ocr:{account_id}:doc-obj-1:claim-1:tok"

    texts: list[str | None] = []
    for page, call in ((1, 1), (2, 2), (3, 3)):
        try:
            texts.append(port.extract(_request(account_id, run_id, page, call)))
        except OcrError:
            texts.append(None)

    assert texts == ["页一文字", None, "页三文字"]
    assert adapter.call_count == 3
    locks = _lock_rows(storage)
    assert len(locks) == 3
    statuses = {str(row["lock_id"]): str(row["status"]) for row in locks}
    assert list(statuses) == sorted(statuses)
    assert str(locks[0]["status"]) == ModelCallStatus.SUCCESS.value
    assert str(locks[1]["status"]) == ModelCallStatus.BLOCKED.value
    assert str(locks[1]["error_code"]) == "missing_image"
    assert str(locks[2]["status"]) == ModelCallStatus.SUCCESS.value
    # 锁按 run + 页序号可查询：业务关联页序号在 operation 中，调用序号在
    # attempt ordinal 中，重启后仍可查。
    persisted = recorder.list_locks_by_run(account_id, run_id)
    assert [ref.attempt_ordinal for lock in persisted for ref in lock.business_refs] == [
        1,
        2,
        3,
    ]
    operations = [
        ref.operation for lock in persisted for ref in lock.business_refs
    ]
    assert operations == ["ocr_page:1", "ocr_page:2", "ocr_page:3"]
    assert all(lock.account_id == account_id for lock in persisted)


def _request(
    account_id: str,
    run_id: str,
    page_ordinal: int,
    call_ordinal: int,
):
    from bridges.ingestion.ocr import OcrPageRequest

    return OcrPageRequest(
        account_id=account_id,
        object_id="obj-1",
        document_id="doc-obj-1",
        run_id=run_id,
        page_ordinal=page_ordinal,
        call_ordinal=call_ordinal,
        media_type="image/png",
        content_hash="hash-1",
        content=IMAGE_CONTENT,
    )


def test_cache_hit_adds_zero_calls_and_zero_locks(storage) -> None:
    adapter = _ProgrammableAdapter([_success()])
    port = _build_port(storage, adapter)
    service, _ = make_ingestion(storage, ocr=port)
    account_id = storage["account_a"]
    object_1 = _upload_image(storage, account_id, "第一张.png")
    object_2 = _upload_image(storage, account_id, "第二张.png")

    service.enqueue(account_id, object_1)
    service.process_pending()
    first_calls = adapter.call_count
    assert first_calls == 1

    service.enqueue(account_id, object_2)
    service.process_pending()
    # 缓存命中：Qwen 调用增量 0，模型锁增量 0。
    assert adapter.call_count == first_calls
    assert len(_lock_rows(storage)) == 1

    second = service.projection(account_id, object_2)
    first = service.projection(account_id, object_1)
    assert first is not None and second is not None
    assert second.status == DocumentIngestionStatus.READY
    assert first.parse_cache_hit is False
    assert second.parse_cache_hit is True
    assert second.ocr_evidence_run_id == first.ocr_evidence_run_id
    assert second.ocr_pages_total == 0


def test_cache_version_expiry_reidentifies_with_new_locks_keeping_old(
    storage,
) -> None:
    """解析版本失效 → 重新识别新增锁（新 run），原锁不被覆盖。"""
    import json

    adapter = _ProgrammableAdapter([_success(), _success()])
    port = _build_port(storage, adapter)
    service, _ = make_ingestion(storage, ocr=port)
    account_id = storage["account_a"]
    object_id = _upload_image(storage, account_id, "题目.png")
    service.enqueue(account_id, object_id)
    service.process_pending()
    assert len(_lock_rows(storage)) == 1
    original_run = service.projection(account_id, object_id).ocr_evidence_run_id
    assert original_run is not None

    # 把缓存行解析器版本改旧（模拟解析版本升级导致失效）。
    legacy_text = "图片：题目.png\n类型：image/png\n"
    legacy = {
        "title": "题目",
        "text": legacy_text,
        "spans": [{"start": 0, "end": len(legacy_text), "page": None, "section": None}],
        "page_count": 0,
        "section_count": 0,
        "parser_version": "image-metadata-v1",
    }
    with storage["database"].transaction():
        storage["database"].connection.execute(
            "UPDATE document_parse_cache SET parser_version = 'image-metadata-v1',"
            " parsed_json = ? WHERE account_id = ?",
            (json.dumps(legacy, ensure_ascii=False), account_id),
        )
    # 重建触发重新处理（新领取 → 新 run → 新锁）。
    service.rebuild_material(account_id, object_id)
    service.process_pending()

    assert adapter.call_count == 2
    locks = _lock_rows(storage)
    assert len(locks) == 2
    runs = {str(row["run_id"]) for row in locks}
    assert len(runs) == 2
    assert original_run in runs  # 原锁不被覆盖、不被删除
    projection = service.projection(account_id, object_id)
    assert projection is not None
    assert projection.parse_cache_hit is False
    assert projection.ocr_evidence_run_id != original_run


def test_crash_after_ocr_before_commit_keeps_lock_and_recovery_creates_new_run(
    storage,
) -> None:
    """OCR 返回后、解析结果保存前崩溃：锁已持久化；恢复重跑是新 run 新锁。"""
    adapter = _ProgrammableAdapter([_success(), _success()])
    port = _build_port(storage, adapter)
    service, _ = make_ingestion(storage, ocr=port)
    account_id = storage["account_a"]
    object_id = _upload_image(storage, account_id, "题目.png")
    service.enqueue(account_id, object_id)

    # 模拟崩溃：只执行到 OCR（真实调用 + 锁落库），解析结果与缓存尚未提交。
    port.extract(
        _request(account_id, "ingestion-ocr:crash-round", page_ordinal=1, call_ordinal=1)
    )
    # 崩溃点：锁已持久化，业务状态尚未收敛（仍 queued，未写解析缓存）。
    locks = _lock_rows(storage)
    assert len(locks) == 1
    assert str(locks[0]["run_id"]) == "ingestion-ocr:crash-round"
    assert str(locks[0]["status"]) == ModelCallStatus.SUCCESS.value
    raw_status = storage["database"].connection.execute(
        "SELECT status FROM document_records WHERE object_id = ?", (object_id,)
    ).fetchone()
    assert str(raw_status["status"]) != "ready"
    cache_rows = storage["database"].connection.execute(
        "SELECT count(*) AS count FROM document_parse_cache", ()
    ).fetchone()
    assert int(cache_rows["count"]) == 0  # 解析结果保存前崩溃：无缓存

    # 恢复：worker 重跑同一文档 → 新 run、新调用序号；旧锁保留。
    service.process_pending()
    assert adapter.call_count == 2
    locks = _lock_rows(storage)
    assert len(locks) == 2
    runs = {str(row["run_id"]) for row in locks}
    assert len(runs) == 2
    assert "ingestion-ocr:crash-round" in runs  # 崩溃轮旧锁保留
    projection = service.projection(account_id, object_id)
    assert projection is not None and projection.status == DocumentIngestionStatus.READY
    assert projection.ocr_evidence_run_id != "ingestion-ocr:crash-round"
    assert projection.ocr_evidence_run_id in runs
    assert projection.parse_cache_hit is False


def test_same_file_two_accounts_strictly_isolated(storage) -> None:
    """两账户上传相同文件：缓存与锁严格按账户隔离，共享全局 Key 不串户。"""
    adapter = _ProgrammableAdapter([_success("甲"), _success("乙")])
    port = _build_port(storage, adapter)
    service, _ = make_ingestion(storage, ocr=port)
    account_a = storage["account_a"]
    account_b = storage["account_b"]
    object_a = _upload_image(storage, account_a, "题目.png")
    object_b = _upload_image(storage, account_b, "题目.png")

    service.enqueue(account_a, object_a)
    service.process_pending()
    service.enqueue(account_b, object_b)
    service.process_pending()

    # 账户 B 不能命中账户 A 的解析缓存：各自真实调用一次。
    assert adapter.call_count == 2
    rows = storage["database"].connection.execute(
        "SELECT account_id, count(*) AS count FROM model_run_locks"
        " GROUP BY account_id ORDER BY account_id",
        (),
    ).fetchall()
    assert {str(r["account_id"]): int(r["count"]) for r in rows} == {
        account_a: 1,
        account_b: 1,
    }
    # 账户作用域查询不能读取其他账户锁：A 查不到 B 的 run。
    recorder = SqliteModelRunLockRecorder(storage["database"])
    b_projection = service.projection(account_b, object_b)
    assert b_projection is not None and b_projection.ocr_evidence_run_id is not None
    assert recorder.list_locks_by_run(account_a, b_projection.ocr_evidence_run_id) == []
    assert (
        recorder.get_lock(
            str(storage["database"].connection.execute(
                "SELECT lock_id FROM model_run_locks WHERE account_id = ?",
                (account_b,),
            ).fetchone()["lock_id"]),
            account_a,
        )
        is None
    )
    # 各自的锁引用各自账户的文档。
    links = _link_rows(storage)
    assert {str(row["account_id"]) for row in links} == {account_a, account_b}


def test_recorder_write_failure_fails_closed_without_ocr_projection(
    storage, monkeypatch
) -> None:
    """recorder 写失败：失败关闭，材料不得形成「已 OCR」投影。"""
    from bridges.ai.errors import ModelRunLockPersistError

    adapter = _ProgrammableAdapter([_success()])
    port = _build_port(storage, adapter)
    service, _ = make_ingestion(storage, ocr=port)
    account_id = storage["account_a"]
    object_id = _upload_image(storage, account_id, "题目.png")

    def broken_record(*_args: Any, **_kwargs: Any) -> None:
        raise ModelRunLockPersistError("disk full")

    monkeypatch.setattr(port._recorder, "record", broken_record)  # noqa: SLF001
    service.enqueue(account_id, object_id)
    service.process_pending()

    # 诚实降级：材料继续（未识别说明），但绝不能标成 OCR 成功。
    projection = service.projection(account_id, object_id)
    assert projection is not None
    assert projection.status == DocumentIngestionStatus.READY
    assert projection.parse_cache_hit is False
    assert projection.ocr_pages_succeeded == 0
    assert projection.ocr_pages_failed == 1
    row = storage["database"].connection.execute(
        "SELECT content FROM document_chunks WHERE document_id = ?",
        (projection.document_id,),
    ).fetchone()
    assert row is not None
    assert "图片内容未做文字识别" in str(row["content"])
    assert "锁测试可检索文字" not in str(row["content"])


def test_page_count_mismatch_fails_closed_at_service_level(
    storage, monkeypatch
) -> None:
    """OCR 成功但页级锁核对失败（审计接缝丢失）→ 诚实降级，不标已识别。"""
    from bridges.ingestion.ocr import OCR_ERR_PAGE_COUNT_MISMATCH, OcrError

    adapter = _ProgrammableAdapter([_success()])
    port = _build_port(storage, adapter)
    service, _ = make_ingestion(storage, ocr=port)
    account_id = storage["account_a"]
    object_id = _upload_image(storage, account_id, "题目.png")

    def broken_verify(_account_id: str, _run_id: str, _expected: int) -> None:
        raise OcrError(
            "图片文字识别失败：OCR 页级锁数量与页请求数不一致，本轮结果未计入。",
            retryable=True,
            code=OCR_ERR_PAGE_COUNT_MISMATCH,
        )

    monkeypatch.setattr(port, "verify_run_locks", broken_verify)
    service.enqueue(account_id, object_id)
    service.process_pending()

    # 供应商调用真实发生且成功，但锁核对失败 → 本轮不得形成「已 OCR」。
    assert adapter.call_count == 1
    projection = service.projection(account_id, object_id)
    assert projection is not None
    assert projection.status == DocumentIngestionStatus.READY
    assert projection.ocr_pages_succeeded == 0
    assert projection.ocr_pages_failed == 1
    row = storage["database"].connection.execute(
        "SELECT content FROM document_chunks WHERE document_id = ?",
        (projection.document_id,),
    ).fetchone()
    assert row is not None
    assert "图片内容未做文字识别" in str(row["content"])
    assert "锁测试可检索文字" not in str(row["content"])


def test_locks_survive_restart_and_remain_queryable(storage) -> None:
    """临时 SQLite：锁跨重启可查（重启后仍能按 run/业务引用定位到页）。"""
    adapter = _ProgrammableAdapter([_success()])
    port = _build_port(storage, adapter)
    service, _ = make_ingestion(storage, ocr=port)
    account_id = storage["account_a"]
    object_id = _upload_image(storage, account_id, "题目.png")
    service.enqueue(account_id, object_id)
    service.process_pending()
    projection = service.projection(account_id, object_id)
    assert projection is not None
    run_id = projection.ocr_evidence_run_id
    assert run_id is not None

    storage["database"].close()
    reopened = BridgesDatabase(storage["path"] / "bridges.db")
    # 重启后初始化到当前 schema 版本（Issue 16 合入后为 48；用常量断言
    # 避免后续迁移再次硬编码失效）。
    assert reopened.initialize() == SCHEMA_VERSION
    recorder = SqliteModelRunLockRecorder(reopened)
    locks = recorder.list_locks_by_run(account_id, run_id)
    assert len(locks) == 1
    assert locks[0].capability_name == "qwen_ocr"
    assert locks[0].actual_model_id == OCR_MODEL_ID
    assert locks[0].business_refs[0].operation == "ocr_page:1"
    # 按业务引用（文档）也能查回页级锁。
    by_ref = recorder.list_locks_by_business_ref(
        account_id, "document", projection.document_id
    )
    assert [lock.lock_id for lock in by_ref] == [lock.lock_id for lock in locks]
    reopened.close()
