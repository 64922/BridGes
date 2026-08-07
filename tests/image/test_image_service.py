"""图片生成与编辑服务测试（Issue 31）。

使用内存 sqlite + 可编程图片适配器验证：生成→轮询→下载→版本化资产
（替代文本模型生成/降级/手动修改）；编辑版本链（parent 关系，不覆盖
原图）；取消与迟到结果隔离（worker 条件更新拒绝发布，已建对象回收）；
重启恢复（租约过期重领）；失败重试同一输入同一快照；云端超时；
删除影响（版本对象/消息引用/幂等）；账户隔离（跨账户任务/资产/版本/
字节一律 404）。审计 spy 验证图片字节与提示词正文不进日志。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bridges.ai import ModelGateway
from bridges.ai.adapters import AdapterResult, AuthError, RateLimitError, TransientError
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.chat.repository import ConversationRepository, MessageRecord
from bridges.contracts.ai import CapabilityKind, CapabilityRecord
from bridges.contracts.chat import ChatMessageRole, ChatMessageStatus
from bridges.contracts.image import (
    ImageAltTextSource,
    ImageError,
    ImageTaskStatus,
)
from bridges.image.service import ImageService
from bridges.storage.database import BridgesDatabase
from bridges.storage.object_store import EncryptedFileObjectStore
from bridges.storage.repository import BridgesObjectRepository

IMAGE_MODEL = "qwen-image-2.0-pro-2026-06-22"
VISION_MODEL = "qwen3-vl-plus"
_RESULT_URL = "http://img.local/result.png"
_IMAGE_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class _RecordingObservability:
    """记录 log_audit 调用的测试替身（验证审计不含图片字节与提示词）。"""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def log_audit(self, **kwargs: Any) -> None:
        self.events.append(kwargs)


class _ProgrammableImageAdapter:
    """可编程图片适配器：按脚本逐次消费 submit/poll/fetch 调用。

    脚本元素：``{"output": {...}}`` 或 ``{"error": Exception}``。
    """

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
        # 空脚本保持失败语义（网关可能因重试策略再次调用）。
        step = self.script.pop(0) if self.script else {"error": TransientError("脚本耗尽")}
        if step.get("error") is not None:
            raise step["error"]
        callback = step.get("callback")
        if callback is not None:
            callback()
        return AdapterResult(
            actual_model_id=IMAGE_MODEL,
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
            actual_model_id=VISION_MODEL,
            output={"content": self._content or ""},
        )


def _succeeded_script() -> list[dict[str, Any]]:
    """一轮完整的成功脚本：提交 → 运行中 → 完成 → 下载。"""
    return [
        {"output": {"cloud_task_id": "cloud-1"}},
        {"output": {"cloud_status": "RUNNING"}},
        {"output": {"cloud_status": "SUCCEEDED", "result_url": _RESULT_URL}},
        {"output": {"image_bytes": _IMAGE_BYTES, "media_type": "image/png"}},
    ]


class _ImageHarness:
    """组装内存数据库、对象库、网关与图片服务的测试台。"""

    def __init__(self, tmp_path: Path, *, vision: bool = True) -> None:
        self.db = BridgesDatabase(":memory:")
        self.db.initialize()
        self.repo = ConversationRepository(self.db)
        self.object_repo = BridgesObjectRepository(
            self.db,
            EncryptedFileObjectStore(tmp_path, encryption_key="image-test-key"),
        )
        self.registry = CapabilityRegistry()
        self.registry.register(
            CapabilityRecord(
                name="qwen_image",
                version="1",
                kind=CapabilityKind.MODEL,
                vendor="qwen",
                region="cn-beijing",
                model_id=IMAGE_MODEL,
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
                    model_id=VISION_MODEL,
                    input_schema_version="image-vision-v1",
                    output_schema_version="vision-text-v1",
                )
            )
        self.gateway = ModelGateway(self.registry)
        self.image = _ProgrammableImageAdapter()
        self.gateway.register_adapter("qwen_image", "1", self.image)
        self.vision = _ProgrammableVisionAdapter()
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

    def seed_assistant_message(self, message_id: str, *, account_id: str | None = None) -> None:
        now = datetime.now(UTC)
        self.repo.insert_message(
            MessageRecord(
                message_id=message_id,
                conversation_id=self.conversation_id,
                account_id=account_id or self.account_id,
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

    def seed_other_account(self) -> str:
        other = "account-b"
        self.object_repo.ensure_account(other, "bob@example.com")
        self.repo.create_conversation(
            account_id=other,
            conversation_id="conv-b",
            title="其他账户对话",
            mode="companion",
            created_at=datetime.now(UTC),
        )
        return other

    def lock_model_ids(self) -> list[str]:
        rows = self.db.connection.execute(
            "SELECT actual_model_id FROM model_run_locks"
        ).fetchall()
        return [str(row["actual_model_id"]) for row in rows]

    def object_count(self, account_id: str) -> int:
        return len(self.object_repo.list_objects(account_id))

    def message_projection(self, message_id: str) -> dict[str, Any] | None:
        record = self.repo.get_message(self.account_id, message_id)
        return record.image if record is not None else None

    def run_pending(self) -> None:
        """连续执行 worker 处理轮直到不再推进（脚本耗尽）。"""
        for _ in range(60):
            before = len(self.image.calls)
            self.service.process_pending()
            if len(self.image.calls) == before:
                return


# ---------------------------------------------------------------------------
# 生成成功路径
# ---------------------------------------------------------------------------


def test_generate_success_creates_asset_version_and_message_projection(
    tmp_path: Path,
) -> None:
    h = _ImageHarness(tmp_path)
    h.image.script = _succeeded_script()
    task = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    assert task.status == ImageTaskStatus.QUEUED
    assert task.kind.value == "generate"
    # 任务与消息投影原子落库（提交后立即可见，无孤儿任务）。
    queued_projection = h.message_projection(h.message_id)
    assert queued_projection is not None
    assert queued_projection["status"] == "queued"

    h.run_pending()

    latest = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert latest.status == ImageTaskStatus.SUCCEEDED
    assert latest.model_id == IMAGE_MODEL
    assert latest.asset_id is not None
    assert latest.result_version_id is not None
    # 固定模型标识进入运行记录（submit/poll/fetch 各一次 + 替代文本视觉）。
    assert IMAGE_MODEL in h.lock_model_ids()
    # 资产：版本链 + 自动替代文本（视觉模型生成）。
    asset = h.service.get_asset(h.account_id, h.conversation_id, latest.asset_id)
    assert asset.version_count == 1
    assert asset.current_version_id == latest.result_version_id
    assert asset.versions[0].kind.value == "generate"
    assert asset.versions[0].parent_version_id is None
    assert asset.versions[0].model_id == IMAGE_MODEL
    assert asset.alt_text_source == ImageAltTextSource.MODEL
    assert asset.alt_text == "图中有一座桥。"
    # 消息投影更新为成功态，正文收敛为完成摘要。
    projection = h.message_projection(h.message_id)
    assert projection is not None
    assert projection["status"] == "succeeded"
    assert projection["asset_id"] == latest.asset_id
    record = h.repo.get_message(h.account_id, h.message_id)
    assert record is not None
    assert record.content == "图片生成完成。"
    # 字节可读取且与所选版本一致。
    content, media_type, length = h.service.get_version_image_bytes(
        h.account_id, h.conversation_id, latest.asset_id, latest.result_version_id
    )
    assert content == _IMAGE_BYTES
    assert media_type == "image/png"
    assert length == len(_IMAGE_BYTES)


def test_generate_alt_text_falls_back_deterministically(tmp_path: Path) -> None:
    """视觉模型不可用时替代文本确定性降级，不阻断生成完成。"""
    h = _ImageHarness(tmp_path, vision=False)
    h.image.script = _succeeded_script()
    task = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    h.run_pending()
    latest = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert latest.status == ImageTaskStatus.SUCCEEDED
    asset = h.service.get_asset(h.account_id, h.conversation_id, latest.asset_id)
    assert asset.alt_text_source == ImageAltTextSource.FALLBACK
    assert "一座桥的素描" in asset.alt_text


# ---------------------------------------------------------------------------
# 编辑与版本链
# ---------------------------------------------------------------------------


def test_edit_creates_new_version_keeping_source_intact(tmp_path: Path) -> None:
    h = _ImageHarness(tmp_path)
    h.image.script = _succeeded_script()
    first = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    h.run_pending()
    first_latest = h.service.get_task(h.account_id, h.conversation_id, first.task_id)
    source_version_id = first_latest.result_version_id
    assert source_version_id is not None

    # 编辑：同一资产下追加新版本，保留来源关系，不覆盖原图。
    edit_message = "msg-2"
    h.seed_assistant_message(edit_message)
    h.image.script = _succeeded_script()
    edit = h.service.submit_edit(
        h.account_id,
        h.conversation_id,
        edit_message,
        "把背景改为夜空",
        source_version_id=source_version_id,
    )
    assert edit.status == ImageTaskStatus.QUEUED
    assert edit.asset_id == first_latest.asset_id
    h.run_pending()

    edit_latest = h.service.get_task(h.account_id, h.conversation_id, edit.task_id)
    assert edit_latest.status == ImageTaskStatus.SUCCEEDED
    asset = h.service.get_asset(h.account_id, h.conversation_id, first_latest.asset_id)
    assert asset.version_count == 2
    assert asset.current_version_id == edit_latest.result_version_id
    edited = next(
        v for v in asset.versions if v.version_id == edit_latest.result_version_id
    )
    assert edited.kind.value == "edit"
    assert edited.parent_version_id == source_version_id
    assert edited.prompt == "把背景改为夜空"
    # 原图版本仍可读取（未被覆盖）。
    original_bytes, _, _ = h.service.get_version_image_bytes(
        h.account_id, h.conversation_id, first_latest.asset_id, source_version_id
    )
    assert original_bytes == _IMAGE_BYTES
    # 两个版本各一个对象。
    assert h.object_count(h.account_id) == 2


def test_edit_source_validation(tmp_path: Path) -> None:
    h = _ImageHarness(tmp_path)
    # 两个来源同时提供 → 拒绝。
    with pytest.raises(ImageError) as exc:
        h.service.submit_edit(
            h.account_id,
            h.conversation_id,
            h.message_id,
            "编辑指令",
            source_version_id="v-1",
            source_object_id="o-1",
        )
    assert exc.value.code == "invalid_source"
    # 来源不存在（跨账户/随机标识）→ 404 不泄漏存在性。
    with pytest.raises(ImageError) as exc:
        h.service.submit_edit(
            h.account_id,
            h.conversation_id,
            h.message_id,
            "编辑指令",
            source_version_id="v-nonexistent",
        )
    assert exc.value.code == "source_not_found"
    assert exc.value.status_code == 404
    # 来源附件不是图片 → 404。
    obj = h.object_repo.create_object(
        h.account_id,
        original_filename="notes.txt",
        content=b"text",
        media_type="text/plain",
    )
    with pytest.raises(ImageError) as exc:
        h.service.submit_edit(
            h.account_id,
            h.conversation_id,
            h.message_id,
            "编辑指令",
            source_object_id=obj.object_id,
        )
    assert exc.value.code == "source_not_found"


# ---------------------------------------------------------------------------
# 取消与迟到结果隔离
# ---------------------------------------------------------------------------


def test_cancel_stops_task_and_late_result_is_never_published(tmp_path: Path) -> None:
    h = _ImageHarness(tmp_path)
    # 提交成功、云端运行中。
    h.image.script = [
        {"output": {"cloud_task_id": "cloud-1"}},
        {"output": {"cloud_status": "RUNNING"}},
    ]
    task = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    h.service.process_pending()
    h.service.process_pending()

    cancelled = h.service.cancel(h.account_id, h.conversation_id, task.task_id)
    assert cancelled.status == ImageTaskStatus.CANCELLED
    # 消息投影同步为已取消。
    assert h.message_projection(h.message_id)["status"] == "cancelled"

    # 云端之后才完成：worker 条件更新拒绝发布，对象不产生、消息不更新。
    h.image.script = [
        {"output": {"cloud_status": "SUCCEEDED", "result_url": _RESULT_URL}},
        {"output": {"image_bytes": _IMAGE_BYTES, "media_type": "image/png"}},
    ]
    h.run_pending()
    latest = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert latest.status == ImageTaskStatus.CANCELLED
    assert h.object_count(h.account_id) == 0
    assert h.message_projection(h.message_id)["status"] == "cancelled"


def test_cancel_race_during_finalize_reclaims_object(tmp_path: Path) -> None:
    """取消与完成竞态：fetch 完成后、条件更新前被取消，已建对象立即回收。"""
    h = _ImageHarness(tmp_path)

    def on_fetch() -> None:
        # 模拟下载期间用户取消（竞态窗口：对象已建、条件更新尚未执行）；
        # cancel API 本地标记为权威并同步消息投影。
        h.service.cancel(h.account_id, h.conversation_id, task.task_id)

    h.image.script = [
        {"output": {"cloud_task_id": "cloud-1"}},
        {"output": {"cloud_status": "SUCCEEDED", "result_url": _RESULT_URL}},
        {
            "output": {"image_bytes": _IMAGE_BYTES, "media_type": "image/png"},
            "callback": on_fetch,
        },
    ]
    task = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    h.service.process_pending()  # submit
    h.service.process_pending()  # poll SUCCEEDED → finalize（fetch 回调中取消）
    latest = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert latest.status == ImageTaskStatus.CANCELLED
    # 迟到结果不发布：没有资产/版本，已建对象被回收。
    assert h.object_count(h.account_id) == 0
    assert h.message_projection(h.message_id)["status"] == "cancelled"


# ---------------------------------------------------------------------------
# 重启恢复与轮询
# ---------------------------------------------------------------------------


def test_restart_recovers_running_task_with_expired_lease(tmp_path: Path) -> None:
    h = _ImageHarness(tmp_path)
    h.image.script = [
        {"output": {"cloud_task_id": "cloud-1"}},
        {"output": {"cloud_status": "SUCCEEDED", "result_url": _RESULT_URL}},
        {"output": {"image_bytes": _IMAGE_BYTES, "media_type": "image/png"}},
    ]
    task = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    h.service.process_pending()  # submit → running（租约 300 秒）
    # 模拟进程重启：租约过期。
    h.db.connection.execute(
        "UPDATE image_tasks SET lease_expires_at = ? WHERE task_id = ?",
        ("2000-01-01T00:00:00", task.task_id),
    )
    h.db.connection.commit()
    h.run_pending()
    latest = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert latest.status == ImageTaskStatus.SUCCEEDED


def test_cloud_timeout_marks_failed_retryable(tmp_path: Path) -> None:
    h = _ImageHarness(tmp_path)
    h.image.script = [
        {"output": {"cloud_task_id": "cloud-1"}},
        *([{"output": {"cloud_status": "RUNNING"}}] * 40),
    ]
    task = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    # 手动推进：1 次提交 + 36 次轮询后判定超时（自动重试前的首次失败）。
    for _ in range(38):
        h.service.process_pending()
    latest = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert latest.status == ImageTaskStatus.FAILED
    assert latest.error_code == "cloud_timeout"
    assert latest.retryable is True
    # 失败清空云端引用：重试会重新提交同一输入（而非轮询旧云端任务）。
    row = h.db.connection.execute(
        "SELECT cloud_task_id FROM image_tasks WHERE task_id = ?", (task.task_id,)
    ).fetchone()
    assert row["cloud_task_id"] is None


def test_lease_expired_running_task_shows_recovery(tmp_path: Path) -> None:
    """呈现态 recovery：租约过期且未终态的任务显示恢复中（进程中断）。"""
    h = _ImageHarness(tmp_path)
    h.image.script = [{"output": {"cloud_task_id": "cloud-1"}}]
    task = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    h.service.process_pending()  # submit → running
    h.db.connection.execute(
        "UPDATE image_tasks SET lease_expires_at = ? WHERE task_id = ?",
        ("2000-01-01T00:00:00+00:00", task.task_id),
    )
    h.db.connection.commit()
    recovery = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert recovery.status == ImageTaskStatus.RECOVERY


# ---------------------------------------------------------------------------
# 失败与重试（同一输入同一快照）
# ---------------------------------------------------------------------------


def test_cloud_failure_retry_uses_same_input_and_snapshot(tmp_path: Path) -> None:
    h = _ImageHarness(tmp_path)
    h.image.script = [
        {"output": {"cloud_task_id": "cloud-1"}},
        {"output": {"cloud_status": "FAILED", "error_message": "云端拒绝"}},
    ]
    task = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    h.service.process_pending()  # submit
    h.service.process_pending()  # poll FAILED → cloud_failed
    failed = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert failed.status == ImageTaskStatus.FAILED
    assert failed.error_message == "云端拒绝"
    assert failed.retryable is True
    assert h.message_projection(h.message_id)["status"] == "failed"

    # 手动重试：同输入重新入队（重置计数与云端引用），不换模型。
    h.image.script = _succeeded_script()
    retried = h.service.retry(h.account_id, h.conversation_id, task.task_id)
    assert retried.status == ImageTaskStatus.QUEUED
    h.service.process_pending()  # 重新提交
    h.service.process_pending()  # poll RUNNING
    h.service.process_pending()  # poll SUCCEEDED → finalize（含下载）
    done = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert done.status == ImageTaskStatus.SUCCEEDED
    submitted = [
        call for call in h.image.calls if call.get("kind") == "submit"
    ]
    assert len(submitted) == 2
    assert submitted[0]["prompt"] == submitted[1]["prompt"] == "一座桥的素描"
    assert all(call.get("size") == "1024*1024" for call in submitted)


def test_transient_submit_failure_is_retryable(tmp_path: Path) -> None:
    h = _ImageHarness(tmp_path)
    h.image.script = [{"error": TransientError("网络抖动")}]
    task = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    h.run_pending()
    failed = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert failed.status == ImageTaskStatus.FAILED
    assert failed.retryable is True
    assert failed.error_code == "transient"


def test_submit_auth_error_maps_to_global_config_hint(tmp_path: Path) -> None:
    """GQ-04：鉴权失败折叠为指向全局运行配置的中文提示，不输出供应商原文。"""
    h = _ImageHarness(tmp_path)
    h.image.script = [{"error": AuthError("Qwen authentication/authorization failed.")}]
    task = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    h.service.process_pending()  # 提交 → AuthError → 稳定失败投影
    failed = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert failed.status == ImageTaskStatus.FAILED
    assert failed.error_code == "auth_error"
    assert "全局百炼配置" in failed.error_message
    assert "authentication" not in failed.error_message
    # 消息投影同步同一中文原因。
    projection = h.message_projection(h.message_id)
    assert projection is not None
    assert "全局百炼配置" in projection["error_message"]


def test_submit_rate_limit_maps_to_retry_hint(tmp_path: Path) -> None:
    """GQ-04：限流折叠为中文稍后重试提示，错误码保持稳定。"""
    h = _ImageHarness(tmp_path)
    h.image.script = [{"error": RateLimitError("Qwen rate limit (429).")}]
    task = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    h.service.process_pending()  # 提交 → RateLimitError → 稳定失败投影
    failed = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert failed.status == ImageTaskStatus.FAILED
    assert failed.error_code == "rate_limit"
    assert failed.retryable is True
    assert "限流" in failed.error_message


def test_retry_only_failed_tasks(tmp_path: Path) -> None:
    h = _ImageHarness(tmp_path)
    task = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    with pytest.raises(ImageError) as exc:
        h.service.retry(h.account_id, h.conversation_id, task.task_id)
    assert exc.value.code == "task_not_retryable"


# ---------------------------------------------------------------------------
# 替代文本修改与删除
# ---------------------------------------------------------------------------


def test_alt_text_manual_update_and_delete_impact(tmp_path: Path) -> None:
    h = _ImageHarness(tmp_path)
    h.image.script = _succeeded_script()
    task = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    h.run_pending()
    latest = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    asset_id = latest.asset_id
    assert asset_id is not None

    # 手动修改替代文本 → manual 来源。
    updated = h.service.update_alt_text(
        h.account_id, h.conversation_id, asset_id, "深夜里的跨江大桥"
    )
    assert updated.alt_text == "深夜里的跨江大桥"
    assert updated.alt_text_source == ImageAltTextSource.MANUAL

    # 删除：版本对象待清理 + 消息引用标记 + 幂等。
    deletion = h.service.delete_asset(h.account_id, h.conversation_id, asset_id)
    assert deletion.removed_versions == 1
    assert deletion.updated_messages == 1
    assert deletion.object_status in ("cleaned", "pending_cleanup")
    projection = h.message_projection(h.message_id)
    assert projection is not None
    assert projection["deleted"] is True
    # 已删除资产查询 404。
    with pytest.raises(ImageError) as exc:
        h.service.get_asset(h.account_id, h.conversation_id, asset_id)
    assert exc.value.status_code == 404
    # 幂等删除：零计数。
    again = h.service.delete_asset(h.account_id, h.conversation_id, asset_id)
    assert again.removed_versions == 0
    assert again.updated_messages == 0
    # 对象最终被清理（pending_cleanup 轮完成物理删除）。
    h.object_repo.run_pending_cleanups()
    assert h.object_count(h.account_id) == 0


# ---------------------------------------------------------------------------
# 账户隔离
# ---------------------------------------------------------------------------


def test_cross_account_isolation(tmp_path: Path) -> None:
    h = _ImageHarness(tmp_path)
    h.image.script = _succeeded_script()
    task = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    h.run_pending()
    latest = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    other = h.seed_other_account()

    with pytest.raises(ImageError) as exc:
        h.service.get_task(other, "conv-b", task.task_id)
    assert exc.value.status_code == 404
    with pytest.raises(ImageError) as exc:
        h.service.cancel(other, "conv-b", task.task_id)
    assert exc.value.status_code == 404
    with pytest.raises(ImageError) as exc:
        h.service.get_asset(other, "conv-b", latest.asset_id)
    assert exc.value.status_code == 404
    with pytest.raises(ImageError) as exc:
        h.service.get_version_image_bytes(
            other, "conv-b", latest.asset_id, latest.result_version_id
        )
    assert exc.value.status_code == 404
    # 跨账户编辑来源 → 404。
    with pytest.raises(ImageError) as exc:
        h.service.submit_edit(
            other,
            "conv-b",
            "msg-b",
            "编辑指令",
            source_version_id=latest.result_version_id,
        )
    assert exc.value.status_code == 404
    # 跨账户消息归属校验 → 404。
    with pytest.raises(ImageError) as exc:
        h.service.submit_generation(other, "conv-b", h.message_id, "提示词")
    assert exc.value.code == "message_not_found"


def test_edit_into_deleted_asset_never_publishes_orphan(tmp_path: Path) -> None:
    """编辑期间来源资产被删除：结果不发布为新版本，对象被回收。"""
    h = _ImageHarness(tmp_path)
    h.image.script = _succeeded_script()
    first = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    h.run_pending()
    source_version_id = h.service.get_task(
        h.account_id, h.conversation_id, first.task_id
    ).result_version_id
    assert source_version_id is not None
    asset_id = h.service.get_task(
        h.account_id, h.conversation_id, first.task_id
    ).asset_id
    assert asset_id is not None

    # 编辑任务提交后、完成前删除来源资产。
    edit_message = "msg-edit"
    h.seed_assistant_message(edit_message)
    h.image.script = [
        {"output": {"cloud_task_id": "cloud-2"}},
        {"output": {"cloud_status": "SUCCEEDED", "result_url": _RESULT_URL}},
        {"output": {"image_bytes": _IMAGE_BYTES, "media_type": "image/png"}},
    ]
    edit = h.service.submit_edit(
        h.account_id,
        h.conversation_id,
        edit_message,
        "把背景改为夜空",
        source_version_id=source_version_id,
    )
    h.service.process_pending()  # submit
    h.service.delete_asset(h.account_id, h.conversation_id, asset_id)
    h.service.process_pending()  # poll SUCCEEDED → finalize（资产已删除）

    edit_latest = h.service.get_task(h.account_id, h.conversation_id, edit.task_id)
    assert edit_latest.status == ImageTaskStatus.FAILED
    assert edit_latest.error_code == "asset_deleted"
    assert edit_latest.retryable is False
    # 不产生孤儿版本/对象：原图对象已随删除清理，编辑对象被回收。
    assert h.object_count(h.account_id) == 0
    assert h.message_projection(edit_message)["status"] == "failed"


def test_poll_rounds_do_not_consume_retry_budget(tmp_path: Path) -> None:
    """轮询轮不消耗自动重试预算：提交后长时间轮询再失败仍可自动重试。"""
    h = _ImageHarness(tmp_path)
    # 提交 + 3 轮轮询（RUNNING）+ 云端失败：若轮询消耗预算，重试计数
    # 会超过 MAX_AUTO_RETRIES 导致失败后不再自动领取。
    h.image.script = [
        {"output": {"cloud_task_id": "cloud-1"}},
        {"output": {"cloud_status": "RUNNING"}},
        {"output": {"cloud_status": "RUNNING"}},
        {"output": {"cloud_status": "RUNNING"}},
        {"output": {"cloud_status": "FAILED", "error_message": "云端拒绝"}},
        {"output": {"cloud_task_id": "cloud-2"}},
        {"output": {"cloud_status": "SUCCEEDED", "result_url": _RESULT_URL}},
        {"output": {"image_bytes": _IMAGE_BYTES, "media_type": "image/png"}},
    ]
    task = h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    for _ in range(4):
        h.service.process_pending()
    row = h.db.connection.execute(
        "SELECT retry_count FROM image_tasks WHERE task_id = ?", (task.task_id,)
    ).fetchone()
    # 提交/轮询领取均不消耗自动重试预算（budget 只在失败后重领时消耗）。
    assert int(row["retry_count"]) == 0

    h.run_pending()  # 失败后自动重领（预算未耗尽）→ 重提交 → 成功
    done = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert done.status == ImageTaskStatus.SUCCEEDED
    submits = [call for call in h.image.calls if call.get("kind") == "submit"]
    assert len(submits) == 2


def test_audit_contains_no_image_bytes_or_prompt(tmp_path: Path) -> None:
    h = _ImageHarness(tmp_path)
    h.image.script = _succeeded_script()
    h.service.submit_generation(
        h.account_id, h.conversation_id, h.message_id, "一座桥的素描"
    )
    h.run_pending()
    details_blob = "".join(
        str(event.get("details", {})) for event in h.audit.events
    )
    reason_blob = "".join(str(event.get("reason", "")) for event in h.audit.events)
    assert "一座桥的素描" not in details_blob
    assert "一座桥的素描" not in reason_blob
    assert "PNG" not in details_blob
    assert any(event["action"] == "image_task_submit" for event in h.audit.events)
    assert any(event["action"] == "image_task_complete" for event in h.audit.events)
