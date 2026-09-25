"""知识库 OCR 端口（Issue 14）：统一网关 + 持久化页级锁合同。

端口只依赖注册的 ``qwen_ocr`` 能力与运行记录接缝：测试用可编程适配器
经真实 ``ModelGateway`` 驱动，锁经真实 SQLite recorder 持久化，断言
实际 adapter 调用数与页级锁一一对应、幂等与失败关闭语义。
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from bridges.ai import CapabilityRegistry, ModelGateway
from bridges.ai.adapters import (
    AdapterError,
    AdapterResult,
    AuthError,
    RateLimitError,
    RegionError,
    TransientError,
)
from bridges.ai.fixed_models import OCR_MODEL_ID
from bridges.ai.production import register_builtin_capabilities
from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder
from bridges.contracts.ai import CapabilityRecord, ModelCallStatus
from bridges.contracts.workflows import RunContextEnvelope
from bridges.ingestion.ocr import (
    OCR_IMAGE_PROMPT,
    OcrError,
    OcrPageRequest,
    QwenOcrPort,
)
from bridges.storage import BridgesDatabase


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
            raise TransientError("exhausted")
        item = self.responses.pop(0)
        if isinstance(item, AdapterError):
            raise item
        return item


def _success(content: str = "图片中的可检索文字") -> AdapterResult:
    return AdapterResult(
        actual_model_id=OCR_MODEL_ID,
        output={"content": content},
        usage={"input_tokens": 10, "output_tokens": 5},
    )


def _request(**overrides: Any) -> OcrPageRequest:
    values: dict[str, Any] = {
        "account_id": "account-a",
        "object_id": "obj-1",
        "document_id": "doc-obj-1",
        "run_id": "ingestion-ocr:account-a:doc-obj-1:claim-1",
        "page_ordinal": 1,
        "call_ordinal": 1,
        "media_type": "image/png",
        "content_hash": "hash-1",
        "content": b"image-bytes",
    }
    values.update(overrides)
    return OcrPageRequest(**values)


@pytest.fixture()
def db(tmp_path: Path) -> BridgesDatabase:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    return database


def _build_port(
    db: BridgesDatabase, responses: list[AdapterResult | AdapterError]
) -> tuple[QwenOcrPort, _ProgrammableAdapter]:
    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    gateway = ModelGateway(registry)
    adapter = _ProgrammableAdapter(responses)
    gateway.register_adapter("qwen_ocr", "1", adapter)
    port = QwenOcrPort(
        gateway=gateway,
        recorder=SqliteModelRunLockRecorder(db),
    )
    return port, adapter


def test_success_records_exactly_one_page_lock_with_business_refs(
    db: BridgesDatabase,
) -> None:
    port, adapter = _build_port(db, [_success()])
    result = port.extract(_request())

    assert result == "图片中的可检索文字"
    assert adapter.call_count == 1
    locks = db.connection.execute(
        "SELECT * FROM model_run_locks", ()
    ).fetchall()
    assert len(locks) == 1
    lock_id = str(locks[0]["lock_id"])
    assert lock_id == (
        "kb-ocr:ingestion-ocr:account-a:doc-obj-1:claim-1:p1:c1"
    )
    assert str(locks[0]["account_id"]) == "account-a"
    assert str(locks[0]["run_id"]) == "ingestion-ocr:account-a:doc-obj-1:claim-1"
    assert str(locks[0]["capability_name"]) == "qwen_ocr"
    assert str(locks[0]["actual_model_id"]) == OCR_MODEL_ID
    assert str(locks[0]["status"]) == ModelCallStatus.SUCCESS.value
    # 锁参数只含脱敏调用参数，绝不含图片 base64、prompt 或 OCR 文本。
    params = locks[0]["parameters"]
    assert "image_base64" not in params
    assert "prompt" not in params
    assert "temperature" in params and "max_tokens" in params

    links = db.connection.execute(
        "SELECT * FROM model_run_lock_links", ()
    ).fetchall()
    assert len(links) == 1
    assert str(links[0]["object_type"]) == "document"
    assert str(links[0]["object_id"]) == "doc-obj-1"
    assert str(links[0]["operation"]) == "ocr_page:1"
    assert int(links[0]["attempt_ordinal"]) == 1
    assert int(links[0]["is_primary"]) == 1


def test_no_adapter_fails_closed_without_fake_lock(db: BridgesDatabase) -> None:
    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    gateway = ModelGateway(registry)  # 不绑定任何适配器（缺全局 Key 的生产形态）
    port = QwenOcrPort(
        gateway=gateway, recorder=SqliteModelRunLockRecorder(db)
    )

    with pytest.raises(OcrError) as exc_info:
        port.extract(_request())

    assert "未配置全局百炼运行凭据" in str(exc_info.value)
    assert exc_info.value.code == "ocr_direct_client_bypass"
    assert db.connection.execute(
        "SELECT count(*) AS count FROM model_run_locks", ()
    ).fetchone()["count"] == 0


def test_missing_page_context_fails_closed_without_calling_adapter(
    db: BridgesDatabase,
) -> None:
    """缺页级上下文（账户/对象/文档/摄取 run）直接失败关闭，不发起调用。"""
    port, adapter = _build_port(db, [_success()])

    for missing in (
        _request(account_id=""),
        _request(object_id=""),
        _request(document_id=""),
        _request(run_id=""),
        _request(page_ordinal=0),
        _request(call_ordinal=0),
    ):
        with pytest.raises(OcrError) as exc_info:
            port.extract(missing)
        assert exc_info.value.code == "ocr_missing_page_context"

    assert adapter.call_count == 0
    assert db.connection.execute(
        "SELECT count(*) AS count FROM model_run_locks", ()
    ).fetchone()["count"] == 0


def test_verify_run_locks_reconciles_page_count(db: BridgesDatabase) -> None:
    """灰度核对合同：页请求数 == 页级锁数时通过；锁缺失时失败关闭。"""
    port, _adapter = _build_port(db, [_success()])
    request = _request()
    port.extract(request)

    port.verify_run_locks(request.account_id, request.run_id, expected_pages=1)

    with pytest.raises(OcrError) as exc_info:
        port.verify_run_locks(request.account_id, request.run_id, expected_pages=2)
    assert exc_info.value.code == "ocr_page_count_mismatch"
    assert exc_info.value.retryable is True


def test_port_capability_matches_registered_capability() -> None:
    """端口的能力名/版本与 Issue 09 注册表一致（防漂移，AC 单一事实源）。"""
    from bridges.ingestion.ocr import (
        OCR_CAPABILITY_NAME,
        OCR_CAPABILITY_VERSION,
    )

    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    capability = registry.get(OCR_CAPABILITY_NAME, OCR_CAPABILITY_VERSION)
    assert capability is not None
    assert capability.model_id == OCR_MODEL_ID


def test_empty_output_records_failure_lock_and_raises(db: BridgesDatabase) -> None:
    port, adapter = _build_port(db, [_success(content="   \n")])

    with pytest.raises(OcrError) as exc_info:
        port.extract(_request())

    assert "未返回可用文字" in str(exc_info.value)
    assert exc_info.value.code == "ocr_empty_output"
    assert adapter.call_count == 1
    lock = db.connection.execute(
        "SELECT * FROM model_run_locks", ()
    ).fetchone()
    assert lock is not None
    assert str(lock["status"]) == ModelCallStatus.BLOCKED.value
    assert str(lock["error_code"]) == "ocr_empty_output"


@pytest.mark.parametrize(
    ("error", "expected_code", "expected_status", "retryable", "message_fragment"),
    [
        (AuthError("bad key"), "auth_error", "blocked", False, "凭据无效"),
        (RegionError("no region"), "region_error", "blocked", False, "区域接入点"),
        (RateLimitError("too many"), "rate_limit", "retryable_fail", True, "限流"),
        (TransientError("network"), "transient", "retryable_fail", True, "网络异常"),
        (
            AdapterError(code="missing_image", message="no image", retryable=False),
            "missing_image",
            "blocked",
            False,
            "未成功",
        ),
    ],
)
def test_failure_paths_record_accurate_failure_locks(
    db: BridgesDatabase,
    error: AdapterError,
    expected_code: str,
    expected_status: str,
    retryable: bool,
    message_fragment: str,
) -> None:
    # 注册的 qwen_ocr 重试政策为 3 次，但只有限流/瞬态错误会重试；
    # 鉴权/区域/适配器错误失败关闭、立即返回。同一次调用内的网关重试
    # 仍是一条锁，retry_count 反映实际重试次数。
    port, adapter = _build_port(db, [error] * 3)

    with pytest.raises(OcrError) as exc_info:
        port.extract(_request())

    assert exc_info.value.retryable is retryable
    assert message_fragment in str(exc_info.value)
    assert exc_info.value.code == expected_code
    assert adapter.call_count == (3 if retryable else 1)
    lock = db.connection.execute(
        "SELECT * FROM model_run_locks", ()
    ).fetchone()
    assert lock is not None
    assert str(lock["status"]) == expected_status
    assert str(lock["error_code"]) == expected_code
    assert str(lock["actual_model_id"]) == OCR_MODEL_ID
    assert int(lock["retry_count"]) == (2 if retryable else 0)


def test_same_page_call_replay_is_idempotent(db: BridgesDatabase) -> None:
    """recorder 重复提交同一个 page-call 锁：只保留一行，不产生冲突。"""
    # 同一 page-call 重放得到相同供应商结果（相同 canonical 内容）：
    # recorder 幂等合并，不产生重复行、不报冲突。
    port, _adapter = _build_port(db, [_success(), _success()])
    request = _request()

    first = port.extract(request)
    second = port.extract(request)

    assert first == second
    count = db.connection.execute(
        "SELECT count(*) AS count FROM model_run_locks", ()
    ).fetchone()["count"]
    assert count == 1
    links = db.connection.execute(
        "SELECT count(*) AS count FROM model_run_lock_links", ()
    ).fetchone()["count"]
    assert links == 1


def test_genuine_retry_uses_new_call_ordinal_and_keeps_old_lock(
    db: BridgesDatabase,
) -> None:
    """真正重新发起供应商请求 → 新调用序号 → 新锁行，旧失败锁保留。"""
    port, adapter = _build_port(db, [RateLimitError("busy")] * 3)
    with pytest.raises(OcrError):
        port.extract(_request(call_ordinal=1))

    adapter.responses.append(_success())
    text = port.extract(_request(call_ordinal=2))

    assert text == "图片中的可检索文字"
    rows = db.connection.execute(
        "SELECT lock_id, status, error_code FROM model_run_locks"
        " ORDER BY lock_id",
        (),
    ).fetchall()
    assert len(rows) == 2
    assert str(rows[0]["status"]) == ModelCallStatus.RETRYABLE_FAIL.value
    assert str(rows[1]["status"]) == ModelCallStatus.SUCCESS.value
    # 两条锁按调用序号可排序查询（list_locks_by_run）。
    recorder = SqliteModelRunLockRecorder(db)
    ordered = recorder.list_locks_by_run(
        "account-a", "ingestion-ocr:account-a:doc-obj-1:claim-1"
    )
    assert [ref.attempt_ordinal for lock in ordered for ref in lock.business_refs] == [
        1,
        2,
    ]


def test_recorder_failure_fails_closed_without_ocr_claim(
    db: BridgesDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """锁写不进审计 → 失败关闭：绝不把无审计结果当作「已 OCR」。"""
    from bridges.ai.errors import ModelRunLockPersistError

    port, _adapter = _build_port(db, [_success()])

    def broken_record(*_args: Any, **_kwargs: Any) -> None:
        raise ModelRunLockPersistError("disk full")

    monkeypatch.setattr(port._recorder, "record", broken_record)  # noqa: SLF001

    with pytest.raises(OcrError) as exc_info:
        port.extract(_request())

    assert exc_info.value.retryable is True
    assert exc_info.value.code == "ocr_lock_persist_failed"
    assert "审计记录写入失败" in str(exc_info.value)
    assert db.connection.execute(
        "SELECT count(*) AS count FROM model_run_locks", ()
    ).fetchone()["count"] == 0


def test_run_context_is_business_scoped(db: BridgesDatabase) -> None:
    """run_id 不再复用账户级 ``knowledge-base-ocr-{account_id}``。"""
    port, _adapter = _build_port(db, [_success()])
    port.extract(
        _request(run_id="ingestion-ocr:account-a:doc-obj-1:claim-99")
    )
    lock = db.connection.execute(
        "SELECT run_id FROM model_run_locks", ()
    ).fetchone()
    assert str(lock["run_id"]) == "ingestion-ocr:account-a:doc-obj-1:claim-99"
    assert "knowledge-base-ocr-account-a" not in str(lock["run_id"])


def test_payload_contract_sends_only_image_and_fixed_prompt(
    db: BridgesDatabase,
) -> None:
    captured: list[dict[str, Any]] = []

    class _CapturingAdapter(_ProgrammableAdapter):
        def call(
            self,
            capability: CapabilityRecord,
            run_context: RunContextEnvelope,
            payload: dict[str, Any],
        ) -> AdapterResult:
            captured.append(payload)
            return super().call(capability, run_context, payload)

    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    gateway = ModelGateway(registry)
    adapter = _CapturingAdapter([_success()])
    gateway.register_adapter("qwen_ocr", "1", adapter)
    port = QwenOcrPort(
        gateway=gateway, recorder=SqliteModelRunLockRecorder(db)
    )

    port.extract(_request())

    assert len(captured) == 1
    payload = captured[0]
    assert set(payload) == {
        "image_base64",
        "mime_type",
        "prompt",
        "temperature",
        "max_tokens",
    }
    assert payload["image_base64"] == base64.b64encode(b"image-bytes").decode("ascii")
    assert payload["mime_type"] == "image/png"
    assert payload["prompt"] == OCR_IMAGE_PROMPT
    assert payload["temperature"] == 0.01
    assert payload["max_tokens"] == 4096


def test_ocr_prompt_is_general_document_task_without_discipline_preset() -> None:
    """Issue 07：知识库材料不分学科，OCR 提示词也不带任何学科预设。"""
    lowered = OCR_IMAGE_PROMPT.lower()
    assert "scientific" not in lowered
    # 输出边界：只抽取可见文字，不添加评论、不猜测不可辨认内容
    # （不可辨认时返回空输出，由空输出失败锁如实记录，不编造正文）。
    assert "commentary" in lowered
    assert "unreadable" in lowered
    assert "visible text" in lowered
