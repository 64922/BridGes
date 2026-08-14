"""Issue 16：图片/视频边缘动作（替代文本 / 供应商取消）的运行锁合同。

验证：

- 图片替代文本每次真实 ``qwen_vision`` 调用恰好新增一条锁（成功/模型
  失败/空输出/锁落库失败/手动修改），来源语义诚实（model/fallback/
  manual，失败不得冒充模型理解）；
- 图片/视频存在 cloud task 的每次真实供应商 cancel 请求恰好新增一条
  对应能力锁（用户调用与 worker 收敛/重试按取消序号区分，历史失败锁
  不被覆盖）；无 cloud task、终态幂等、纯本地路径零调用零锁；
- 供应商取消失败时本地取消权威与迟到结果抑制保持有效，审计明确
  "云端通知未确认"（``media_cancel_provider_unconfirmed``）；
- 远端请求返回后、领域审计前崩溃：模型锁已持久化，重启后仍可查询；
- recorder 幂等、attempt 顺序与跨账户隔离（临时 SQLite 文件）。

不使用 fixture/cassette/录制响应：全部经可编程 fake adapter 走真实
网关路径（锁由网关不可变生成）。
"""

from __future__ import annotations

import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bridges.ai import ModelGateway
from bridges.ai.adapters import (
    AdapterResult,
    AuthError,
    TransientError,
)
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.ai.errors import ModelRunLockPersistError
from bridges.ai.fixed_models import IMAGE_MODEL_ID, VIDEO_MODEL_ID, VISION_MODEL_ID
from bridges.ai.ports import ModelRunLockRecorder
from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder
from bridges.chat.repository import ConversationRepository, MessageRecord
from bridges.contracts.ai import (
    BusinessRef,
    CapabilityKind,
    CapabilityRecord,
    ModelCallStatus,
    ModelRunLock,
)
from bridges.contracts.chat import ChatMessageRole, ChatMessageStatus
from bridges.contracts.image import ImageAltTextSource, ImageTaskStatus
from bridges.contracts.observability import (
    MEDIA_EDGE_CANCEL_PROVIDER_UNCONFIRMED,
    MEDIA_EDGE_CALL_COUNT_MISMATCH,
    MEDIA_EDGE_LOCK_PERSIST_FAILED,
)
from bridges.contracts.video import VideoTaskStatus
from bridges.image.service import ImageService
from bridges.storage.database import BridgesDatabase
from bridges.storage.object_store import EncryptedFileObjectStore
from bridges.storage.repository import BridgesObjectRepository
from bridges.video.service import VideoService

_RESULT_URL = "http://img.local/result.png"
_IMAGE_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


class _RecordingObservability:
    """记录 log_audit 的测试替身；可注入"审计阶段崩溃"。"""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.raise_on_audit = False

    def log_audit(self, **kwargs: Any) -> None:
        if self.raise_on_audit:
            raise RuntimeError("模拟审计阶段崩溃")
        self.events.append(kwargs)


class _FailingRecorder(ModelRunLockRecorder):
    """注入锁落库失败的 recorder（替代文本必须降级，不冒充模型来源）。"""

    def record(
        self, lock: ModelRunLock, *, business_ref: BusinessRef
    ) -> Any:
        raise ModelRunLockPersistError("注入落库失败")

    def record_many(self, requests: list[Any]) -> list[Any]:
        raise ModelRunLockPersistError("注入落库失败")

    def get_lock(self, lock_id: str, account_id: str) -> Any:
        return None

    def list_locks_by_run(self, account_id: str, run_id: str) -> list[Any]:
        return []

    def list_locks_by_business_ref(
        self, account_id: str, object_type: str, object_id: str
    ) -> list[Any]:
        return []


class _ProgrammableImageAdapter:
    """可编程图片适配器：按脚本逐次消费 submit/poll/fetch/cancel 调用。"""

    def __init__(self, script: list[dict[str, Any]] | None = None) -> None:
        self.script = list(script or [])
        self.calls: list[dict[str, Any]] = []

    def call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        self.calls.append(payload)
        step = self.script.pop(0) if self.script else {"error": TransientError("脚本耗尽")}
        if step.get("error") is not None:
            raise step["error"]
        callback = step.get("callback")
        if callback is not None:
            callback()
        return AdapterResult(
            actual_model_id=IMAGE_MODEL_ID,
            output=dict(step.get("output") or {}),
        )


class _ProgrammableVisionAdapter:
    """可编程视觉适配器：返回替代文本或抛出可分类错误。"""

    def __init__(
        self,
        content: str | None = "图中有一座桥。",
        error: Exception | None = None,
    ) -> None:
        self._content = content
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        self.calls.append(payload)
        if self._error is not None:
            raise self._error
        return AdapterResult(
            actual_model_id=VISION_MODEL_ID,
            output={"content": self._content or ""},
        )


class _ProgrammableWanAdapter:
    """可编程 Wan 适配器：按脚本逐次消费 submit/poll/fetch/cancel 调用。"""

    def __init__(self, script: list[dict[str, Any]] | None = None) -> None:
        self.script = list(script or [])
        self.calls: list[dict[str, Any]] = []

    def call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        self.calls.append(payload)
        step = self.script.pop(0) if self.script else {"error": TransientError("脚本耗尽")}
        if step.get("error") is not None:
            raise step["error"]
        callback = step.get("callback")
        if callback is not None:
            callback()
        return AdapterResult(
            actual_model_id=VIDEO_MODEL_ID,
            output=dict(step.get("output") or {}),
        )


def _succeeded_image_script() -> list[dict[str, Any]]:
    return [
        {"output": {"cloud_task_id": "cloud-1"}},
        {"output": {"cloud_status": "RUNNING"}},
        {"output": {"cloud_status": "SUCCEEDED", "result_url": _RESULT_URL}},
        {"output": {"image_bytes": _IMAGE_BYTES, "media_type": "image/png"}},
    ]


def _succeeded_video_script() -> list[dict[str, Any]]:
    return [
        {"output": {"cloud_task_id": "cloud-1"}},
        {"output": {"cloud_status": "RUNNING"}},
        {"output": {"cloud_status": "SUCCEEDED", "result_url": _RESULT_URL}},
        {"output": {"video_bytes": _VIDEO_BYTES, "media_type": "video/mp4"}},
    ]


class _LockQueryMixin:
    """model_run_locks / model_run_lock_links 的只读查询面。"""

    db: BridgesDatabase
    account_id: str

    def lock_rows(self, *, capability: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM model_run_locks"
        conds: list[str] = []
        args: list[Any] = []
        if capability is not None:
            conds.append("capability_name = ?")
            args.append(capability)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY created_at, lock_id"
        return self.db.connection.execute(sql, args).fetchall()

    def links(self, lock_id: str) -> list[sqlite3.Row]:
        return self.db.connection.execute(
            "SELECT * FROM model_run_lock_links WHERE lock_id = ?"
            " ORDER BY attempt_ordinal, created_at",
            (lock_id,),
        ).fetchall()

    def locks_by_operation(self, operation: str) -> list[sqlite3.Row]:
        return self.db.connection.execute(
            "SELECT l.* FROM model_run_locks l"
            " JOIN model_run_lock_links r"
            " ON l.lock_id = r.lock_id AND l.account_id = r.account_id"
            " WHERE r.operation = ?"
            " ORDER BY l.created_at, l.lock_id",
            (operation,),
        ).fetchall()

    def links_by_operation(self, operation: str) -> list[sqlite3.Row]:
        return self.db.connection.execute(
            "SELECT * FROM model_run_lock_links WHERE operation = ?"
            " ORDER BY attempt_ordinal, created_at, link_id",
            (operation,),
        ).fetchall()


class _ImageEdgeHarness(_LockQueryMixin):
    """图片服务测试台：内存 sqlite + 可编程图片/视觉适配器。"""

    def __init__(
        self,
        tmp_path: Path,
        *,
        vision: bool = True,
        vision_content: str | None = "图中有一座桥。",
        vision_error: Exception | None = None,
        recorder: ModelRunLockRecorder | None = None,
    ) -> None:
        self.db = BridgesDatabase(":memory:")
        self.db.initialize()
        self.repo = ConversationRepository(self.db, run_lock_recorder=recorder)
        self.object_repo = BridgesObjectRepository(
            self.db,
            EncryptedFileObjectStore(tmp_path, encryption_key="image-edge-test-key"),
        )
        self.registry = CapabilityRegistry()
        self.registry.register(
            CapabilityRecord(
                name="qwen_image",
                version="1",
                kind=CapabilityKind.MODEL,
                vendor="qwen",
                region="cn-beijing",
                model_id=IMAGE_MODEL_ID,
                input_schema_version="image-prompt-v1",
                output_schema_version="image-task-v1",
            )
        )
        if vision:
            self.registry.register(
                CapabilityRecord(
                    name="qwen_vision",
                    version="1",
                    kind=CapabilityKind.MODEL,
                    vendor="qwen",
                    region="cn-beijing",
                    model_id=VISION_MODEL_ID,
                    input_schema_version="image-vision-v1",
                    output_schema_version="vision-text-v1",
                )
            )
        self.gateway = ModelGateway(self.registry)
        self.image = _ProgrammableImageAdapter()
        self.gateway.register_adapter("qwen_image", "1", self.image)
        self.vision = _ProgrammableVisionAdapter(
            content=vision_content, error=vision_error
        )
        if vision:
            self.gateway.register_adapter("qwen_vision", "1", self.vision)
        self.audit = _RecordingObservability()
        self.service = ImageService(
            database=self.db,
            gateway=self.gateway,
            object_repository=self.object_repo,
            chat_repository=self.repo,
            observability_service=self.audit,  # type: ignore[arg-type]
        )
        self.account_id = "account-a"
        self.conversation_id = "conv-1"
        self.message_id = "msg-1"
        self.object_repo.ensure_account(self.account_id, "alice@example.com")
        self.repo.create_conversation(
            account_id=self.account_id,
            conversation_id=self.conversation_id,
            title="测试对话",
            mode="companion",
            created_at=datetime.now(UTC),
        )
        self.seed_assistant_message(self.message_id)

    def seed_assistant_message(self, message_id: str) -> None:
        now = datetime.now(UTC)
        self.repo.insert_message(
            MessageRecord(
                message_id=message_id,
                conversation_id=self.conversation_id,
                account_id=self.account_id,
                role=ChatMessageRole.ASSISTANT,
                attempt_number=1,
                status=ChatMessageStatus.STREAMING,
                content="",
                thinking=None,
                error_code=None,
                error_message=None,
                duration_ms=None,
                model_id=None,
                run_lock_id=None,
                created_at=now,
                updated_at=now,
            )
        )

    def task_row(self, task_id: str) -> sqlite3.Row | None:
        return self.db.scoped(self.account_id).execute(
            "SELECT * FROM image_tasks WHERE task_id = ? AND account_id = ?",
            (task_id, self.account_id),
        ).fetchone()

    def object_count(self, account_id: str) -> int:
        return len(self.object_repo.list_objects(account_id))

    def run_pending(self) -> None:
        for _ in range(60):
            before = len(self.image.calls)
            self.service.process_pending()
            if len(self.image.calls) == before:
                return


class _VideoEdgeHarness(_LockQueryMixin):
    """视频服务测试台：内存 sqlite + 可编程 Wan 适配器。"""

    def __init__(self, tmp_path: Path) -> None:
        self.db = BridgesDatabase(":memory:")
        self.db.initialize()
        self.repo = ConversationRepository(self.db)
        self.object_repo = BridgesObjectRepository(
            self.db,
            EncryptedFileObjectStore(tmp_path, encryption_key="video-edge-test-key"),
        )
        self.registry = CapabilityRegistry()
        self.registry.register(
            CapabilityRecord(
                name="qwen_wan",
                version="1",
                kind=CapabilityKind.MODEL,
                vendor="wan",
                region="cn-beijing",
                model_id=VIDEO_MODEL_ID,
                input_schema_version="video-prompt-v1",
                output_schema_version="video-task-v1",
            )
        )
        self.gateway = ModelGateway(self.registry)
        self.wan = _ProgrammableWanAdapter()
        self.gateway.register_adapter("qwen_wan", "1", self.wan)
        self.audit = _RecordingObservability()
        self.service = VideoService(
            database=self.db,
            gateway=self.gateway,
            object_repository=self.object_repo,
            chat_repository=self.repo,
            observability_service=self.audit,  # type: ignore[arg-type]
        )
        self.account_id = "account-a"
        self.conversation_id = "conv-1"
        self.message_id = "msg-1"
        self.object_repo.ensure_account(self.account_id, "alice@example.com")
        self.repo.create_conversation(
            account_id=self.account_id,
            conversation_id=self.conversation_id,
            title="测试对话",
            mode="companion",
            created_at=datetime.now(UTC),
        )
        self.seed_assistant_message(self.message_id)

    def seed_assistant_message(self, message_id: str) -> None:
        now = datetime.now(UTC)
        self.repo.insert_message(
            MessageRecord(
                message_id=message_id,
                conversation_id=self.conversation_id,
                account_id=self.account_id,
                role=ChatMessageRole.ASSISTANT,
                attempt_number=1,
                status=ChatMessageStatus.STREAMING,
                content="",
                thinking=None,
                error_code=None,
                error_message=None,
                duration_ms=None,
                model_id=None,
                run_lock_id=None,
                created_at=now,
                updated_at=now,
            )
        )

    def task_row(self, task_id: str) -> sqlite3.Row | None:
        return self.db.scoped(self.account_id).execute(
            "SELECT * FROM video_tasks WHERE task_id = ? AND account_id = ?",
            (task_id, self.account_id),
        ).fetchone()

    def object_count(self, account_id: str) -> int:
        return len(self.object_repo.list_objects(account_id))

    def run_pending(self) -> None:
        for _ in range(60):
            before = len(self.wan.calls)
            self.service.process_pending()
            if len(self.wan.calls) == before:
                return


def _make_lock(
    *,
    account_id: str,
    run_id: str,
    lock_id: str | None = None,
    capability: str = "qwen_image",
    status: ModelCallStatus = ModelCallStatus.SUCCESS,
    error_code: str | None = None,
) -> ModelRunLock:
    return ModelRunLock(
        lock_id=lock_id or secrets.token_urlsafe(16),
        run_id=run_id,
        account_id=account_id,
        project_id="conv-1",
        capability_name=capability,
        capability_version="1",
        actual_model_id=IMAGE_MODEL_ID,
        region="cn-beijing",
        parameters={},
        prompt_version="1",
        input_output_contract="image-task-v1",
        fallback_path=[f"{capability}@1"],
        status=status,
        retry_count=0,
        degradation_reason=None,
        error_code=error_code,
        error_message=None,
        created_at=datetime.now(UTC),
        usage=None,
        cost_estimate=None,
    )


# ---------------------------------------------------------------------------
# 图片替代文本：每次真实 qwen_vision 调用恰好一条锁
# ---------------------------------------------------------------------------


def test_alt_text_success_records_exactly_one_vision_lock(tmp_path: Path) -> None:
    h = _ImageEdgeHarness(tmp_path)
    h.image.script = _succeeded_image_script()
    task = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    h.run_pending()
    latest = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert latest.status == ImageTaskStatus.SUCCEEDED

    # 主生成链调用数不变（submit/poll/fetch），视觉恰好一次真实调用。
    assert len(h.image.calls) == 4
    assert len(h.vision.calls) == 1
    vision_locks = h.lock_rows(capability="qwen_vision")
    assert len(vision_locks) == 1
    lock = vision_locks[0]
    assert lock["status"] == ModelCallStatus.SUCCESS.value
    assert lock["actual_model_id"] == VISION_MODEL_ID
    assert lock["account_id"] == h.account_id
    assert lock["project_id"] == h.conversation_id
    # 业务关联：图片任务主关联（阶段 image_alt_text，序号 1）。
    links = h.links(str(lock["lock_id"]))
    assert any(
        str(link["object_type"]) == "image_task"
        and str(link["object_id"]) == task.task_id
        and str(link["operation"]) == "image_alt_text"
        and int(link["attempt_ordinal"]) == 1
        and int(link["is_primary"]) == 1
        for link in links
    )
    # 来源为模型；锁总数 = 主链 4 + 视觉 1。
    asset = h.service.get_asset(h.account_id, h.conversation_id, latest.asset_id)
    assert asset.alt_text_source == ImageAltTextSource.MODEL
    assert asset.alt_text == "图中有一座桥。"
    assert len(h.lock_rows()) == 5


def test_alt_text_edit_links_task_and_existing_asset(tmp_path: Path) -> None:
    h = _ImageEdgeHarness(tmp_path)
    h.image.script = _succeeded_image_script()
    first = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    h.run_pending()
    first_latest = h.service.get_task(h.account_id, h.conversation_id, first.task_id)
    source_version_id = first_latest.result_version_id
    first_asset_id = first_latest.asset_id
    assert source_version_id is not None and first_asset_id is not None

    edit_message = "msg-2"
    h.seed_assistant_message(edit_message)
    h.image.script = _succeeded_image_script()
    edit = h.service.submit_edit(
        h.account_id,
        h.conversation_id,
        edit_message,
        "把背景改为夜空",
        source_version_id=source_version_id,
    )
    h.run_pending()
    edit_latest = h.service.get_task(h.account_id, h.conversation_id, edit.task_id)
    assert edit_latest.status == ImageTaskStatus.SUCCEEDED

    vision_locks = h.lock_rows(capability="qwen_vision")
    assert len(vision_locks) == 2  # 生成 + 编辑各一次真实调用
    edit_lock = vision_locks[1]
    links = h.links(str(edit_lock["lock_id"]))
    assert any(
        str(link["object_type"]) == "image_task"
        and str(link["object_id"]) == edit.task_id
        and str(link["operation"]) == "image_alt_text"
        for link in links
    )
    # 编辑时来源资产已存在：同一锁追加资产关联。
    assert any(
        str(link["object_type"]) == "image_asset"
        and str(link["object_id"]) == first_asset_id
        and str(link["operation"]) == "image_alt_text"
        for link in links
    )


def test_alt_text_model_failure_keeps_failure_lock_and_fallback(
    tmp_path: Path,
) -> None:
    h = _ImageEdgeHarness(tmp_path, vision_error=TransientError("视觉服务超时"))
    h.image.script = _succeeded_image_script()
    task = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    h.run_pending()
    latest = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert latest.status == ImageTaskStatus.SUCCEEDED  # 降级不阻断生成

    # 失败锁仍保留：状态 retryable_fail + 稳定错误码 transient。
    vision_locks = h.lock_rows(capability="qwen_vision")
    assert len(vision_locks) == 1
    assert vision_locks[0]["status"] == ModelCallStatus.RETRYABLE_FAIL.value
    assert vision_locks[0]["error_code"] == "transient"
    # 来源为 fallback 且不冒充模型理解。
    asset = h.service.get_asset(h.account_id, h.conversation_id, latest.asset_id)
    assert asset.alt_text_source == ImageAltTextSource.FALLBACK
    assert "由提示词" in asset.alt_text
    assert "一座桥的素描" in asset.alt_text


def test_alt_text_empty_output_keeps_lock_and_fallback(tmp_path: Path) -> None:
    h = _ImageEdgeHarness(tmp_path, vision_content="")
    h.image.script = _succeeded_image_script()
    task = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    h.run_pending()
    latest = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert latest.status == ImageTaskStatus.SUCCEEDED
    vision_locks = h.lock_rows(capability="qwen_vision")
    assert len(vision_locks) == 1  # 调用发生了，锁保留
    asset = h.service.get_asset(h.account_id, h.conversation_id, latest.asset_id)
    assert asset.alt_text_source == ImageAltTextSource.FALLBACK
    assert "由提示词" in asset.alt_text


def test_alt_text_lock_persist_failure_falls_back_and_audits_stable_code(
    tmp_path: Path,
) -> None:
    h = _ImageEdgeHarness(tmp_path, recorder=_FailingRecorder())
    h.image.script = _succeeded_image_script()
    task = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    h.run_pending()
    latest = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert latest.status == ImageTaskStatus.SUCCEEDED
    # 锁落库失败：不得标成模型来源；也没有伪造锁行。
    assert h.lock_rows(capability="qwen_vision") == []
    asset = h.service.get_asset(h.account_id, h.conversation_id, latest.asset_id)
    assert asset.alt_text_source == ImageAltTextSource.FALLBACK
    # 稳定错误码审计：落库失败 + 请求/锁计数不一致。
    codes = {
        str(event.get("details", {}).get("code") or "")
        or str(event.get("details", {}).get("edge_code") or "")
        for event in h.audit.events
        if event.get("action") == "image_alt_text_generate"
    }
    assert MEDIA_EDGE_LOCK_PERSIST_FAILED in codes
    assert MEDIA_EDGE_CALL_COUNT_MISMATCH in codes


def test_manual_alt_text_update_calls_no_vision_and_adds_no_lock(
    tmp_path: Path,
) -> None:
    h = _ImageEdgeHarness(tmp_path)
    h.image.script = _succeeded_image_script()
    task = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    h.run_pending()
    latest = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    asset_id = latest.asset_id
    assert asset_id is not None
    vision_calls = len(h.vision.calls)
    vision_locks = len(h.lock_rows(capability="qwen_vision"))

    updated = h.service.update_alt_text(
        h.account_id, h.conversation_id, asset_id, "深夜里的跨江大桥"
    )
    assert updated.alt_text == "深夜里的跨江大桥"
    assert updated.alt_text_source == ImageAltTextSource.MANUAL
    assert len(h.vision.calls) == vision_calls  # 不调用模型
    assert len(h.lock_rows(capability="qwen_vision")) == vision_locks  # 不新增锁


# ---------------------------------------------------------------------------
# 图片供应商取消：每次真实请求恰好一条锁，本地取消权威
# ---------------------------------------------------------------------------


def test_image_cancel_with_cloud_task_records_exactly_one_cancel_lock(
    tmp_path: Path,
) -> None:
    h = _ImageEdgeHarness(tmp_path)
    h.image.script = [
        {"output": {"cloud_task_id": "cloud-1"}},
        {"output": {"cloud_status": "RUNNING"}},
        {"output": {"cancelled": True}},
    ]
    task = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    h.service.process_pending()  # submit → running
    h.service.process_pending()  # poll RUNNING
    cancelled = h.service.cancel(h.account_id, h.conversation_id, task.task_id)
    assert cancelled.status == ImageTaskStatus.CANCELLED

    cancel_calls = [c for c in h.image.calls if c.get("kind") == "cancel"]
    assert len(cancel_calls) == 1
    assert cancel_calls[0]["cloud_task_id"] == "cloud-1"
    cancel_locks = h.locks_by_operation("image_cancel")
    assert len(cancel_locks) == 1
    lock = cancel_locks[0]
    assert lock["capability_name"] == "qwen_image"
    assert lock["actual_model_id"] == IMAGE_MODEL_ID
    assert lock["status"] == ModelCallStatus.SUCCESS.value
    assert lock["account_id"] == h.account_id
    assert lock["project_id"] == h.conversation_id
    links = h.links(str(lock["lock_id"]))
    assert any(
        str(link["object_type"]) == "image_task"
        and str(link["object_id"]) == task.task_id
        and str(link["operation"]) == "image_cancel"
        and int(link["attempt_ordinal"]) == 1
        for link in links
    )
    # 审计明确云端取消已确认。
    cancel_audits = [
        e for e in h.audit.events if e.get("action") == "image_task_cancel"
    ]
    assert cancel_audits[-1]["result"] == "success"
    assert cancel_audits[-1]["details"]["provider_cancel_confirmed"] is True
    # 取消序号落库。
    row = h.task_row(task.task_id)
    assert row is not None and int(row["cancel_attempt"]) == 1


def test_image_cancel_provider_failure_keeps_local_authority_and_failure_lock(
    tmp_path: Path,
) -> None:
    h = _ImageEdgeHarness(tmp_path)
    h.image.script = [
        {"output": {"cloud_task_id": "cloud-1"}},
        {"output": {"cloud_status": "RUNNING"}},
        {"error": AuthError("Qwen authentication/authorization failed.")},
    ]
    task = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    h.service.process_pending()
    h.service.process_pending()
    cancelled = h.service.cancel(h.account_id, h.conversation_id, task.task_id)
    assert cancelled.status == ImageTaskStatus.CANCELLED  # 本地取消权威

    cancel_locks = h.locks_by_operation("image_cancel")
    assert len(cancel_locks) == 1
    assert cancel_locks[0]["status"] == ModelCallStatus.BLOCKED.value
    assert cancel_locks[0]["error_code"] == "auth_error"
    # 锁正文脱敏：供应商原文不进入审计库，稳定错误码保留。
    assert "authorization" not in str(cancel_locks[0]["error_message"]).lower()
    assert "authorization" not in str(cancel_locks[0]["degradation_reason"]).lower()
    # 审计如实区分：本地已取消、云端通知未确认，不冒充供应商成功。
    cancel_audits = [
        e for e in h.audit.events if e.get("action") == "image_task_cancel"
    ]
    last = cancel_audits[-1]
    assert last["result"] == "degraded"
    assert last["details"]["provider_cancel_confirmed"] is False
    assert last["details"]["edge_code"] == MEDIA_EDGE_CANCEL_PROVIDER_UNCONFIRMED
    assert last["details"]["provider_error_code"] == "auth_error"

    # 迟到结果仍被抑制：云端之后才完成，不发布资产。
    h.image.script = [
        {"output": {"cloud_status": "SUCCEEDED", "result_url": _RESULT_URL}},
        {"output": {"image_bytes": _IMAGE_BYTES, "media_type": "image/png"}},
    ]
    h.run_pending()
    assert h.object_count(h.account_id) == 0


def test_image_cancel_without_cloud_task_is_pure_local_no_call_no_lock(
    tmp_path: Path,
) -> None:
    h = _ImageEdgeHarness(tmp_path)
    task = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    calls_before = len(h.image.calls)
    locks_before = len(h.lock_rows())
    cancelled = h.service.cancel(h.account_id, h.conversation_id, task.task_id)
    assert cancelled.status == ImageTaskStatus.CANCELLED
    # 无 cloud task：纯本地取消，网关调用增量与锁增量均为 0。
    assert len(h.image.calls) == calls_before
    assert len(h.lock_rows()) == locks_before
    assert h.locks_by_operation("image_cancel") == []
    cancel_audits = [
        e for e in h.audit.events if e.get("action") == "image_task_cancel"
    ]
    assert cancel_audits[-1]["details"]["provider_cancel_confirmed"] is None


def test_image_cancel_terminal_idempotent_adds_no_call_no_lock(
    tmp_path: Path,
) -> None:
    h = _ImageEdgeHarness(tmp_path)
    h.image.script = _succeeded_image_script()
    task = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    h.run_pending()
    final = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert final.status == ImageTaskStatus.SUCCEEDED
    calls_before = len(h.image.calls)
    locks_before = len(h.lock_rows())
    result = h.service.cancel(h.account_id, h.conversation_id, task.task_id)
    assert result.status == ImageTaskStatus.SUCCEEDED  # 幂等返回当前投影
    assert len(h.image.calls) == calls_before
    assert len(h.lock_rows()) == locks_before
    assert h.locks_by_operation("image_cancel") == []
    # 审计 BLOCKED：取消未生效，不冒充取消成功。
    blocked = [
        e
        for e in h.audit.events
        if e.get("action") == "image_task_cancel" and e.get("result") == "blocked"
    ]
    assert len(blocked) == 1


def test_image_cancel_crash_after_provider_return_keeps_lock(tmp_path: Path) -> None:
    """远端 cancel 返回并落锁后、领域审计前崩溃：模型锁仍持久化。"""
    h = _ImageEdgeHarness(tmp_path)
    h.image.script = [
        {"output": {"cloud_task_id": "cloud-1"}},
        {"output": {"cloud_status": "RUNNING"}},
        {"output": {"cancelled": True}},
    ]
    task = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    h.service.process_pending()
    h.service.process_pending()
    h.audit.raise_on_audit = True
    with pytest.raises(RuntimeError):
        h.service.cancel(h.account_id, h.conversation_id, task.task_id)
    cancel_locks = h.locks_by_operation("image_cancel")
    assert len(cancel_locks) == 1
    assert cancel_locks[0]["status"] == ModelCallStatus.SUCCESS.value


# ---------------------------------------------------------------------------
# 视频供应商取消：用户调用与 worker 收敛/重试各自独立锁
# ---------------------------------------------------------------------------


def test_video_cancel_user_and_worker_record_distinct_locks_with_sequence(
    tmp_path: Path,
) -> None:
    h = _VideoEdgeHarness(tmp_path)
    h.wan.script = [
        {"output": {"cloud_task_id": "cloud-1"}},
        {"output": {"cancelled": True}},  # 用户取消通知
        {"output": {"cancelled": True}},  # worker 收敛通知
    ]
    task = h.service.submit(h.account_id, h.conversation_id, h.message_id, "一条静谧的河")
    h.service.process_pending()  # 提交 → generating
    assert h.task_row(task.task_id)["cloud_task_id"] == "cloud-1"
    cancelled = h.service.cancel(h.account_id, h.conversation_id, task.task_id)
    assert cancelled.status == VideoTaskStatus.CANCELLING
    h.service.process_pending()  # worker 收敛 → cancelled
    final = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert final.status == VideoTaskStatus.CANCELLED

    cancel_calls = [c for c in h.wan.calls if c.get("kind") == "cancel"]
    assert len(cancel_calls) == 2
    cancel_locks = h.locks_by_operation("video_cancel")
    assert len(cancel_locks) == 2
    assert {str(r["status"]) for r in cancel_locks} == {
        ModelCallStatus.SUCCESS.value
    }
    assert len({str(r["lock_id"]) for r in cancel_locks}) == 2  # 独立锁
    # 用户调用与 worker 收敛按取消序号区分：1、2。
    links = h.links_by_operation("video_cancel")
    assert [int(link["attempt_ordinal"]) for link in links] == [1, 2]
    assert all(
        str(link["object_type"]) == "video_task"
        and str(link["object_id"]) == task.task_id
        for link in links
    )
    # 收敛审计确认云端取消。
    cancel_audits = [
        e for e in h.audit.events if e.get("action") == "video_task_cancel"
    ]
    assert cancel_audits[-1]["result"] == "success"
    assert cancel_audits[-1]["details"]["provider_cancel_confirmed"] is True
    assert h.object_count(h.account_id) == 0


def test_video_cancel_worker_retry_after_crash_records_new_lock(
    tmp_path: Path,
) -> None:
    """worker 通知后、收敛事务前崩溃：重跑再次通知并新增锁，不覆盖历史锁。"""
    h = _VideoEdgeHarness(tmp_path)
    h.wan.script = [
        {"output": {"cloud_task_id": "cloud-1"}},
        {"output": {"cancelled": True}},  # 用户取消通知
        {"output": {"cancelled": True}},  # worker 第一次通知
        {"output": {"cancelled": True}},  # worker 崩溃重试通知
    ]
    task = h.service.submit(h.account_id, h.conversation_id, h.message_id, "一条静谧的河")
    h.service.process_pending()  # submit → generating
    h.service.cancel(h.account_id, h.conversation_id, task.task_id)
    h.service.process_pending()  # worker 第一次收敛 → cancelled

    # 模拟崩溃：通知已发出但收敛事务未提交（状态回退 cancelling、序号未递增）。
    h.db.scoped(h.account_id).execute(
        "UPDATE video_tasks SET status = 'cancelling', cancel_attempt = 1"
        " WHERE task_id = ? AND account_id = ?",
        (task.task_id, h.account_id),
    )
    # 崩溃后任务重新入队（等价于中断恢复后的重领）。
    h.service._task_queue.enqueue(  # noqa: SLF001
        "video", f"video:{h.account_id}:{task.task_id}"
    )
    h.service.process_pending()  # worker 重试收敛 → cancelled
    final = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert final.status == VideoTaskStatus.CANCELLED

    cancel_calls = [c for c in h.wan.calls if c.get("kind") == "cancel"]
    assert len(cancel_calls) == 3  # 每次真实请求都发生
    links = h.links_by_operation("video_cancel")
    assert len(links) == 3
    # 每次真实请求都是独立新锁（历史失败锁不被覆盖）；崩溃重试复用序号 2。
    assert [int(link["attempt_ordinal"]) for link in links] == [1, 2, 2]
    assert len({str(link["lock_id"]) for link in links}) == 3


def test_video_cancel_during_submit_only_worker_notifies_once(tmp_path: Path) -> None:
    """提交期间取消：云端标识落库后由 worker 收敛完成唯一一次真实取消。"""
    h = _VideoEdgeHarness(tmp_path)
    task = h.service.submit(h.account_id, h.conversation_id, h.message_id, "一条静谧的河")
    h.wan.script = [
        {
            "output": {"cloud_task_id": "cloud-1"},
            "callback": lambda: h.service.cancel(
                h.account_id, h.conversation_id, task.task_id
            ),
        },
        {"output": {"cancelled": True}},
    ]
    h.service.process_pending()  # 提交（期间被取消）
    row = h.task_row(task.task_id)
    assert row is not None
    assert row["status"] == "cancelling"
    assert row["cloud_task_id"] == "cloud-1"
    h.service.process_pending()  # worker 收敛
    final = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert final.status == VideoTaskStatus.CANCELLED
    cancel_calls = [c for c in h.wan.calls if c.get("kind") == "cancel"]
    assert cancel_calls == [{"kind": "cancel", "cloud_task_id": "cloud-1"}]
    links = h.links_by_operation("video_cancel")
    assert [int(link["attempt_ordinal"]) for link in links] == [1]
    assert h.object_count(h.account_id) == 0


def test_video_cancel_provider_failure_keeps_local_cancelled_with_failure_locks(
    tmp_path: Path,
) -> None:
    h = _VideoEdgeHarness(tmp_path)
    h.wan.script = [
        {"output": {"cloud_task_id": "cloud-1"}},
        {"error": AuthError("Qwen authentication/authorization failed.")},
        {"error": AuthError("Qwen authentication/authorization failed.")},
    ]
    task = h.service.submit(h.account_id, h.conversation_id, h.message_id, "一条静谧的河")
    h.service.process_pending()  # submit → generating
    cancelled = h.service.cancel(h.account_id, h.conversation_id, task.task_id)
    assert cancelled.status == VideoTaskStatus.CANCELLING
    h.service.process_pending()  # worker 收敛
    final = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert final.status == VideoTaskStatus.CANCELLED
    assert h.object_count(h.account_id) == 0

    cancel_locks = h.locks_by_operation("video_cancel")
    assert len(cancel_locks) == 2  # 用户 + worker 各一次真实请求、各一条失败锁
    assert {str(r["status"]) for r in cancel_locks} == {ModelCallStatus.BLOCKED.value}
    assert {str(r["error_code"]) for r in cancel_locks} == {"auth_error"}
    # 锁正文脱敏：供应商原文不进入审计库，稳定错误码保留。
    assert all(
        "authorization" not in str(r["error_message"]).lower()
        and "authorization" not in str(r["degradation_reason"]).lower()
        for r in cancel_locks
    )
    cancel_audits = [
        e for e in h.audit.events if e.get("action") == "video_task_cancel"
    ]
    assert all(
        e["details"].get("edge_code") == MEDIA_EDGE_CANCEL_PROVIDER_UNCONFIRMED
        for e in cancel_audits
        if e["details"].get("provider_cancel_confirmed") is False
    )


def test_video_cancel_without_cloud_task_is_pure_local_no_call_no_lock(
    tmp_path: Path,
) -> None:
    h = _VideoEdgeHarness(tmp_path)
    task = h.service.submit(h.account_id, h.conversation_id, h.message_id, "一条静谧的河")
    cancelled = h.service.cancel(h.account_id, h.conversation_id, task.task_id)
    assert cancelled.status == VideoTaskStatus.CANCELLING
    h.run_pending()
    final = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert final.status == VideoTaskStatus.CANCELLED
    # 从未提交云端：无 submit、无 cancel 调用、无模型锁。
    assert [c.get("kind") for c in h.wan.calls] == []
    assert h.lock_rows() == []


def test_video_cancel_already_succeeded_adds_no_call_no_lock(tmp_path: Path) -> None:
    h = _VideoEdgeHarness(tmp_path)
    h.wan.script = _succeeded_video_script()
    task = h.service.submit(h.account_id, h.conversation_id, h.message_id, "一条静谧的河")
    h.run_pending()
    final = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert final.status == VideoTaskStatus.SUCCEEDED
    calls_before = len(h.wan.calls)
    locks_before = len(h.lock_rows())
    result = h.service.cancel(h.account_id, h.conversation_id, task.task_id)
    assert result.status == VideoTaskStatus.SUCCEEDED
    assert len(h.wan.calls) == calls_before
    assert len(h.lock_rows()) == locks_before
    assert h.locks_by_operation("video_cancel") == []


# ---------------------------------------------------------------------------
# 锁落库前脱敏（供应商原文不进入审计库）
# ---------------------------------------------------------------------------


def test_lock_scrub_replaces_forbidden_vendor_text_keeps_error_code() -> None:
    from bridges.ai.lock_scrub import scrub_lock_text

    raw = _make_lock(
        account_id="a1",
        run_id="run-1",
        status=ModelCallStatus.BLOCKED,
        error_code="auth_error",
    )
    raw = raw.model_copy(
        update={
            "error_message": "Qwen authentication/authorization failed.",
            "degradation_reason": "Qwen authentication/authorization failed.",
        }
    )
    scrubbed = scrub_lock_text(raw)
    assert scrubbed.lock_id == raw.lock_id
    assert scrubbed.error_code == "auth_error"
    assert scrubbed.status == ModelCallStatus.BLOCKED
    assert "authorization" not in str(scrubbed.error_message).lower()
    assert "authorization" not in str(scrubbed.degradation_reason).lower()
    assert "auth_error" in str(scrubbed.error_message)
    # 未命中凭据形态关键词时原样返回同一对象（零拷贝）。
    clean = _make_lock(account_id="a1", run_id="run-1")
    assert scrub_lock_text(clean) is clean


# ---------------------------------------------------------------------------
# recorder 幂等 / attempt 顺序 / 重启查询 / 跨账户隔离
# ---------------------------------------------------------------------------


def test_edge_lock_recording_is_idempotent_and_attempt_ordered(tmp_path: Path) -> None:
    path = tmp_path / "locks.db"
    db = BridgesDatabase(path)
    db.initialize()
    recorder = SqliteModelRunLockRecorder(db)
    lock = _make_lock(account_id="a1", run_id="run-1")
    ref = BusinessRef(
        object_type="image_task",
        object_id="task-1",
        operation="image_cancel",
        attempt_ordinal=1,
        is_primary=False,
    )
    recorder.record(lock, business_ref=ref)
    recorder.record(lock, business_ref=ref)  # 同一调用结果重复保存：幂等
    persisted = recorder.get_lock(lock.lock_id, "a1")
    assert persisted is not None
    assert len(persisted.business_refs) == 1
    assert len(recorder.list_locks_by_run("a1", "run-1")) == 1

    # 不同调用按 attempt 序号稳定排序。
    for ordinal in (3, 1, 2):
        recorder.record(
            _make_lock(account_id="a1", run_id="run-2"),
            business_ref=BusinessRef(
                object_type="image_task",
                object_id="task-2",
                operation="image_cancel",
                attempt_ordinal=ordinal,
                is_primary=False,
            ),
        )
    ordered = recorder.list_locks_by_run("a1", "run-2")
    assert [
        ref.attempt_ordinal for lock in ordered for ref in lock.business_refs
    ] == [1, 2, 3]


def test_edge_locks_survive_restart_and_are_account_isolated(
    tmp_path: Path,
) -> None:
    path = tmp_path / "locks.db"
    db = BridgesDatabase(path)
    db.initialize()
    recorder = SqliteModelRunLockRecorder(db)
    lock = _make_lock(account_id="a1", run_id="run-1")
    recorder.record(
        lock,
        business_ref=BusinessRef(
            object_type="image_task",
            object_id="task-1",
            operation="image_cancel",
            attempt_ordinal=1,
            is_primary=False,
        ),
    )
    # 模拟进程重启：重新打开同一数据库文件查询。
    restarted = BridgesDatabase(path)
    restarted.initialize()
    recorder2 = SqliteModelRunLockRecorder(restarted)
    got = recorder2.get_lock(lock.lock_id, "a1")
    assert got is not None
    assert got.status == ModelCallStatus.SUCCESS
    assert got.capability_name == "qwen_image"
    by_ref = recorder2.list_locks_by_business_ref("a1", "image_task", "task-1")
    assert len(by_ref) == 1
    # 跨账户严格隔离：其他账户查不到任何锁或关联。
    assert recorder2.get_lock(lock.lock_id, "b2") is None
    assert recorder2.list_locks_by_run("b2", "run-1") == []
    assert recorder2.list_locks_by_business_ref("b2", "image_task", "task-1") == []


def test_edge_lock_conflict_never_overwrites_history(tmp_path: Path) -> None:
    """同一 lock_id 携带不同内容：拒绝且原锁不被覆盖（历史失败锁安全）。"""
    from bridges.ai.errors import ModelRunLockConflictError

    path = tmp_path / "locks.db"
    db = BridgesDatabase(path)
    db.initialize()
    recorder = SqliteModelRunLockRecorder(db)
    lock = _make_lock(account_id="a1", run_id="run-1", status=ModelCallStatus.BLOCKED)
    recorder.record(
        lock,
        business_ref=BusinessRef(
            object_type="video_task",
            object_id="task-1",
            operation="video_cancel",
            attempt_ordinal=1,
            is_primary=False,
        ),
    )
    conflicting = _make_lock(
        account_id="a1", run_id="run-1", lock_id=lock.lock_id,
        status=ModelCallStatus.SUCCESS,
    )
    with pytest.raises(ModelRunLockConflictError):
        recorder.record(
            conflicting,
            business_ref=BusinessRef(
                object_type="video_task",
                object_id="task-1",
                operation="video_cancel",
                attempt_ordinal=1,
                is_primary=False,
            ),
        )
    got = recorder.get_lock(lock.lock_id, "a1")
    assert got is not None
    assert got.status == ModelCallStatus.BLOCKED  # 原失败锁未被覆盖
