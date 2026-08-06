"""文生视频服务测试（Issue 32）。

使用内存 sqlite + 可编程 Wan 适配器验证：生成→提交→轮询→下载→账户
资产（提示/模型/供应商任务标识/说明文字/消息投影）；取消全流程（取消中
→worker 收敛为已取消，云端尽力取消）；迟到结果隔离（条件发布拒绝，已建
对象回收）；提交期间取消仍记录云端任务标识（供取消收敛通知云端）；重启
恢复（租约过期重领）；云端超时；失败重试同一输入同一快照；轮询不消耗
自动重试预算；说明文字修改（prompt→manual）；删除影响（消息引用/对象
清理/幂等）；账户隔离（跨账户任务/资产/字节一律 404）。审计 spy 验证
视频字节与提示词正文不进日志。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bridges.ai import ModelGateway
from bridges.ai.adapters import AdapterResult, TransientError
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.chat.repository import ConversationRepository, MessageRecord
from bridges.contracts.ai import CapabilityKind, CapabilityRecord
from bridges.contracts.chat import ChatMessageRole, ChatMessageStatus
from bridges.contracts.video import (
    VideoDescriptionSource,
    VideoError,
    VideoTaskStatus,
)
from bridges.storage.database import BridgesDatabase
from bridges.storage.object_store import EncryptedFileObjectStore
from bridges.storage.repository import BridgesObjectRepository
from bridges.video.service import VideoService

VIDEO_MODEL = "wan2.7-t2v-2026-06-12"
_RESULT_URL = "http://video.local/result.mp4"
_VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


class _RecordingObservability:
    """记录 log_audit 调用的测试替身（验证审计不含视频字节与提示词）。"""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def log_audit(self, **kwargs: Any) -> None:
        self.events.append(kwargs)


class _ProgrammableWanAdapter:
    """可编程 Wan 适配器：按脚本逐次消费 submit/poll/fetch/cancel 调用。

    脚本元素：``{"output": {...}}``、``{"error": Exception}`` 或带
    ``callback``（在返回前执行，用于注入竞态窗口）。
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
            actual_model_id=VIDEO_MODEL,
            output=dict(step.get("output") or {}),
        )


def _succeeded_script() -> list[dict[str, Any]]:
    """一轮完整的成功脚本：提交 → 运行中 → 完成 → 下载。"""
    return [
        {"output": {"cloud_task_id": "cloud-1"}},
        {"output": {"cloud_status": "RUNNING"}},
        {"output": {"cloud_status": "SUCCEEDED", "result_url": _RESULT_URL}},
        {"output": {"video_bytes": _VIDEO_BYTES, "media_type": "video/mp4"}},
    ]


class _VideoHarness:
    """组装内存数据库、对象库、网关与视频服务的测试台。"""

    def __init__(self, tmp_path: Path) -> None:
        self.db = BridgesDatabase(":memory:")
        self.db.initialize()
        self.repo = ConversationRepository(self.db)
        self.object_repo = BridgesObjectRepository(
            self.db,
            EncryptedFileObjectStore(tmp_path, encryption_key="video-test-key"),
        )
        self.registry = CapabilityRegistry()
        self.registry.register(
            CapabilityRecord(
                name="qwen_wan",
                version="1",
                kind=CapabilityKind.MODEL,
                vendor="wan",
                region="cn-beijing",
                model_id=VIDEO_MODEL,
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
        return record.video if record is not None else None

    def task_row(self, task_id: str) -> Any:
        return self.db.scoped(self.account_id).execute(
            "SELECT * FROM video_tasks WHERE task_id = ? AND account_id = ?",
            (task_id, self.account_id),
        ).fetchone()

    def run_pending(self) -> None:
        """连续执行 worker 处理轮直到不再推进（脚本耗尽）。"""
        for _ in range(60):
            before = len(self.wan.calls)
            self.service.process_pending()
            if len(self.wan.calls) == before:
                return


# ---------------------------------------------------------------------------
# 生成成功路径
# ---------------------------------------------------------------------------


def test_generate_success_creates_asset_and_message_projection(tmp_path: Path) -> None:
    h = _VideoHarness(tmp_path)
    h.wan.script = _succeeded_script()
    task = h.service.submit(
        h.account_id, h.conversation_id, h.message_id, "一条静谧的河"
    )
    assert task.status == VideoTaskStatus.QUEUED

    h.run_pending()

    projection = h.message_projection(h.message_id)
    assert projection is not None
    assert projection["status"] == "succeeded"
    assert projection["prompt"] == "一条静谧的河"
    assert projection["model_id"] == VIDEO_MODEL
    asset_id = projection["asset_id"]
    assert asset_id is not None

    asset = h.service.get_asset(h.account_id, h.conversation_id, asset_id)
    assert asset.model_id == VIDEO_MODEL
    assert asset.cloud_task_id == "cloud-1"
    assert asset.media_type == "video/mp4"
    assert asset.content_length == len(_VIDEO_BYTES)
    # 可访问文字说明：默认由提示词确定性生成，来源为 prompt。
    assert asset.description_source == VideoDescriptionSource.PROMPT
    assert "一条静谧的河" in asset.description

    content, media_type, length = h.service.get_video_bytes(
        h.account_id, h.conversation_id, asset_id
    )
    assert content == _VIDEO_BYTES
    assert media_type == "video/mp4"
    assert length == len(_VIDEO_BYTES)
    # 固定模型快照进运行锁（真实模型标识，非 Stub；每轮 invoke 各落一条）。
    assert h.lock_model_ids()
    assert set(h.lock_model_ids()) == {VIDEO_MODEL}


def test_generate_message_content_converges(tmp_path: Path) -> None:
    h = _VideoHarness(tmp_path)
    h.wan.script = _succeeded_script()
    h.service.submit(h.account_id, h.conversation_id, h.message_id, "一条静谧的河")
    h.run_pending()
    record = h.repo.get_message(h.account_id, h.message_id)
    assert record is not None
    assert record.content == "视频生成完成。"


# ---------------------------------------------------------------------------
# 取消全流程与迟到结果隔离
# ---------------------------------------------------------------------------


def test_cancel_flows_through_cancelling_to_cancelled(tmp_path: Path) -> None:
    """取消 → 取消中 → worker 收敛为已取消，且尽力通知云端取消。"""
    h = _VideoHarness(tmp_path)
    h.wan.script = [
        {"output": {"cloud_task_id": "cloud-1"}},
        {"output": {"cancelled": True}},
    ]
    task = h.service.submit(h.account_id, h.conversation_id, h.message_id, "一条静谧的河")
    h.service.process_pending()  # 提交 → generating
    assert h.task_row(task.task_id)["status"] == "generating"

    cancelled = h.service.cancel(h.account_id, h.conversation_id, task.task_id)
    assert cancelled.status == VideoTaskStatus.CANCELLING

    # worker 收敛：尽力云端取消后标记已取消，不发布任何资产。取消接口
    # 与 worker 收敛步骤各做一次尽力云端取消（幂等，覆盖 API 进程在
    # 标记与通知之间中断的场景）。
    h.service.process_pending()
    final = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert final.status == VideoTaskStatus.CANCELLED
    cancel_calls = [c for c in h.wan.calls if c.get("kind") == "cancel"]
    assert cancel_calls == [
        {"kind": "cancel", "cloud_task_id": "cloud-1"},
        {"kind": "cancel", "cloud_task_id": "cloud-1"},
    ]
    assert h.object_count(h.account_id) == 0
    projection = h.message_projection(h.message_id)
    assert projection["status"] == "cancelled"


def test_cancel_queued_task_never_submits(tmp_path: Path) -> None:
    h = _VideoHarness(tmp_path)
    task = h.service.submit(h.account_id, h.conversation_id, h.message_id, "一条静谧的河")
    cancelled = h.service.cancel(h.account_id, h.conversation_id, task.task_id)
    assert cancelled.status == VideoTaskStatus.CANCELLING
    h.run_pending()
    final = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert final.status == VideoTaskStatus.CANCELLED
    # 从未提交云端：无 submit 调用、无云端取消调用。
    assert [c.get("kind") for c in h.wan.calls] == []


def test_cancel_during_submit_records_cloud_task_id_for_cancel_step(tmp_path: Path) -> None:
    """提交期间取消：云端任务标识仍落库，取消收敛步骤能尽力通知云端。"""
    h = _VideoHarness(tmp_path)
    task = h.service.submit(h.account_id, h.conversation_id, h.message_id, "一条静谧的河")
    h.wan.script = [
        {
            "output": {"cloud_task_id": "cloud-1"},
            "callback": lambda: h.service.cancel(h.account_id, h.conversation_id, task.task_id),
        },
        {"output": {"cancelled": True}},
    ]
    h.service.process_pending()  # 提交（期间被取消）：云端任务标识仍落库
    row = h.task_row(task.task_id)
    assert row["status"] == "cancelling"
    assert row["cloud_task_id"] == "cloud-1"
    h.service.process_pending()  # 取消收敛步骤：云端取消 + 标记已取消
    final = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert final.status == VideoTaskStatus.CANCELLED
    # 提交期间取消时云端标识尚不存在，取消接口未内联通知；由 worker 收敛
    # 步骤完成唯一一次云端取消。
    cancel_calls = [c for c in h.wan.calls if c.get("kind") == "cancel"]
    assert cancel_calls == [{"kind": "cancel", "cloud_task_id": "cloud-1"}]
    assert h.object_count(h.account_id) == 0


def test_cancel_race_during_finalize_reclaims_object(tmp_path: Path) -> None:
    """下载完成后、条件发布前被取消：已建对象立即回收，不发布资产。"""
    h = _VideoHarness(tmp_path)
    task = h.service.submit(h.account_id, h.conversation_id, h.message_id, "一条静谧的河")
    h.wan.script = [
        {"output": {"cloud_task_id": "cloud-1"}},
        {"output": {"cloud_status": "SUCCEEDED", "result_url": _RESULT_URL}},
        {
            "output": {"video_bytes": _VIDEO_BYTES, "media_type": "video/mp4"},
            "callback": lambda: h.service.cancel(h.account_id, h.conversation_id, task.task_id),
        },
        {"output": {"cancelled": True}},
    ]
    h.service.process_pending()  # 提交 → generating
    h.service.process_pending()  # 轮询完成 → 下载（期间被取消）→ 条件发布拒绝
    row = h.task_row(task.task_id)
    assert row["status"] == "cancelling"
    # 资产不存在：条件发布被取消拒绝，对象已回收。
    assert row["asset_id"] is None
    assert h.object_count(h.account_id) == 0
    projection = h.message_projection(h.message_id)
    assert projection["status"] == "cancelling"
    h.service.process_pending()  # 取消收敛步骤 → 已取消
    final = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert final.status == VideoTaskStatus.CANCELLED


def test_cancel_already_terminal_is_blocked_idempotent(tmp_path: Path) -> None:
    h = _VideoHarness(tmp_path)
    h.wan.script = _succeeded_script()
    task = h.service.submit(h.account_id, h.conversation_id, h.message_id, "一条静谧的河")
    h.run_pending()
    final = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert final.status == VideoTaskStatus.SUCCEEDED
    # 已成功任务的取消是未生效的幂等尝试（审计 BLOCKED，状态不变）。
    result = h.service.cancel(h.account_id, h.conversation_id, task.task_id)
    assert result.status == VideoTaskStatus.SUCCEEDED
    blocked = [e for e in h.audit.events if e.get("result", "") == "blocked"]
    assert len(blocked) == 1


# ---------------------------------------------------------------------------
# 重启恢复 / 超时 / 失败重试
# ---------------------------------------------------------------------------


def test_restart_recovers_running_task_with_expired_lease(tmp_path: Path) -> None:
    h = _VideoHarness(tmp_path)
    h.wan.script = [
        {"output": {"cloud_task_id": "cloud-1"}},
        {"output": {"cloud_status": "SUCCEEDED", "result_url": _RESULT_URL}},
        {"output": {"video_bytes": _VIDEO_BYTES, "media_type": "video/mp4"}},
    ]
    task = h.service.submit(h.account_id, h.conversation_id, h.message_id, "一条静谧的河")
    h.service.process_pending()  # 提交 → generating
    assert h.task_row(task.task_id)["status"] == "generating"
    assert h.task_row(task.task_id)["cloud_task_id"] == "cloud-1"

    # 模拟进程中断：租约过期后重启，同一任务继续轮询，不重复提交。
    h.db.scoped(h.account_id).execute(
        "UPDATE video_tasks SET lease_expires_at = ? WHERE task_id = ?"
        " AND account_id = ?",
        (
            (datetime.now(UTC) - timedelta(seconds=1)).isoformat(timespec="seconds"),
            task.task_id,
            h.account_id,
        ),
    )
    h.run_pending()
    final = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert final.status == VideoTaskStatus.SUCCEEDED
    submits = [c for c in h.wan.calls if c.get("kind") == "submit"]
    assert len(submits) == 1  # 未重复提交同一供应商任务


def test_lease_expired_running_task_shows_recovery(tmp_path: Path) -> None:
    h = _VideoHarness(tmp_path)
    h.wan.script = [{"output": {"cloud_task_id": "cloud-1"}}]
    task = h.service.submit(h.account_id, h.conversation_id, h.message_id, "一条静谧的河")
    h.service.process_pending()  # 提交 → generating
    h.db.scoped(h.account_id).execute(
        "UPDATE video_tasks SET lease_expires_at = ? WHERE task_id = ?"
        " AND account_id = ?",
        (
            (datetime.now(UTC) - timedelta(seconds=1)).isoformat(timespec="seconds"),
            task.task_id,
            h.account_id,
        ),
    )
    projection = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert projection.status == VideoTaskStatus.RECOVERY


def test_cloud_timeout_marks_failed_retryable(tmp_path: Path) -> None:
    h = _VideoHarness(tmp_path)
    h.wan.script = [{"output": {"cloud_task_id": "cloud-1"}}]
    h.wan.script += [
        {"output": {"cloud_status": "RUNNING"}}
        for _ in range(61)  # MAX_CLOUD_POLLS=60，超一轮即超时
    ]
    task = h.service.submit(h.account_id, h.conversation_id, h.message_id, "一条静谧的河")
    # 手动推进：1 次提交 + 60 次轮询后判定超时（自动重试前的首次失败）。
    for _ in range(62):
        h.service.process_pending()
    row = h.task_row(task.task_id)
    assert row["status"] == "failed"
    assert row["error_code"] == "cloud_timeout"
    assert row["cloud_task_id"] is None  # 云端引用清理，重试将重新提交
    projection = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert projection.retryable is True


def test_cloud_failure_retry_uses_same_input_and_snapshot(tmp_path: Path) -> None:
    h = _VideoHarness(tmp_path)
    h.wan.script = [
        {"output": {"cloud_task_id": "cloud-1"}},
        {"output": {"cloud_status": "FAILED", "error_message": "云端拒绝"}},
    ]
    task = h.service.submit(h.account_id, h.conversation_id, h.message_id, "一条静谧的河")
    h.service.process_pending()  # 提交 → generating
    h.service.process_pending()  # 轮询 → 云端失败
    failed = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert failed.status == VideoTaskStatus.FAILED
    assert failed.error_code == "cloud_failed"
    assert failed.retryable is True

    # 手动重试：同输入重新入队；新一轮成功链路用同一提示词与固定模型。
    h.wan.script = _succeeded_script()
    retried = h.service.retry(h.account_id, h.conversation_id, task.task_id)
    assert retried.status == VideoTaskStatus.QUEUED
    h.run_pending()
    final = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert final.status == VideoTaskStatus.SUCCEEDED
    submits = [c for c in h.wan.calls if c.get("kind") == "submit"]
    assert len(submits) == 2
    assert submits[0]["prompt"] == submits[1]["prompt"] == "一条静谧的河"
    assert submits[0]["size"] == submits[1]["size"] == "1280*720"


def test_transient_submit_failure_auto_retries_same_input(tmp_path: Path) -> None:
    """瞬态提交失败在自动重试预算内以同一输入重新提交并收敛成功。"""
    h = _VideoHarness(tmp_path)
    h.wan.script = [
        {"error": TransientError("网络抖动")},
        {"output": {"cloud_task_id": "cloud-1"}},
        {"output": {"cloud_status": "SUCCEEDED", "result_url": _RESULT_URL}},
        {"output": {"video_bytes": _VIDEO_BYTES, "media_type": "video/mp4"}},
    ]
    task = h.service.submit(h.account_id, h.conversation_id, h.message_id, "一条静谧的河")
    h.run_pending()
    final = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert final.status == VideoTaskStatus.SUCCEEDED
    submits = [c for c in h.wan.calls if c.get("kind") == "submit"]
    assert len(submits) == 2
    assert submits[0]["prompt"] == submits[1]["prompt"] == "一条静谧的河"


def test_retry_only_failed_tasks(tmp_path: Path) -> None:
    h = _VideoHarness(tmp_path)
    task = h.service.submit(h.account_id, h.conversation_id, h.message_id, "一条静谧的河")
    try:
        h.service.retry(h.account_id, h.conversation_id, task.task_id)
    except VideoError as exc:
        assert exc.code == "task_not_retryable"
    else:
        raise AssertionError("非失败任务重试应被拒绝")


def test_poll_rounds_do_not_consume_retry_budget(tmp_path: Path) -> None:
    h = _VideoHarness(tmp_path)
    h.wan.script = [
        {"output": {"cloud_task_id": "cloud-1"}},
        *([{"output": {"cloud_status": "RUNNING"}}] * 4),
        {"output": {"cloud_status": "FAILED", "error_message": "云端拒绝"}},
    ]
    task = h.service.submit(h.account_id, h.conversation_id, h.message_id, "一条静谧的河")
    # 手动推进：1 次提交 + 4 次轮询 + 1 次云端失败。
    for _ in range(6):
        h.service.process_pending()
    row = h.task_row(task.task_id)
    assert row["status"] == "failed"
    assert int(row["retry_count"]) == 0  # 轮询轮不消耗自动重试预算


def test_permanent_failure_not_auto_reclaimed(tmp_path: Path) -> None:
    """供应商完成但结果缺失（empty_result）：不自动重领，避免白耗配额。

    永久失败码同时隐藏前端重试入口与 worker 自动重领，语义一致。
    """
    h = _VideoHarness(tmp_path)
    h.wan.script = [
        {"output": {"cloud_task_id": "cloud-1"}},
        {"output": {"cloud_status": "SUCCEEDED"}},  # 完成但没有结果地址
    ]
    task = h.service.submit(h.account_id, h.conversation_id, h.message_id, "一条静谧的河")
    h.service.process_pending()  # 提交 → generating
    h.service.process_pending()  # 轮询完成但无结果 → empty_result 失败
    row = h.task_row(task.task_id)
    assert row["status"] == "failed"
    assert row["error_code"] == "empty_result"

    # 永久失败不自动重领：更多处理轮不产生新的提交（重试预算不消耗）。
    h.wan.script = []
    h.run_pending()
    row = h.task_row(task.task_id)
    assert int(row["retry_count"]) == 0
    submits = [c for c in h.wan.calls if c.get("kind") == "submit"]
    assert len(submits) == 1
    projection = h.service.get_task(h.account_id, h.conversation_id, task.task_id)
    assert projection.retryable is False


def test_failed_auto_retry_budget_bounded(tmp_path: Path) -> None:
    h = _VideoHarness(tmp_path)
    task = h.service.submit(h.account_id, h.conversation_id, h.message_id, "一条静谧的河")
    # 前三次失败自动重试后不再自动领取，任务保持 failed 等待用户手动重试。
    h.wan.script = [{"error": TransientError("瞬态失败")}] * 3
    h.run_pending()
    h.wan.script = [{"error": TransientError("瞬态失败")}] * 5
    h.run_pending()
    row = h.task_row(task.task_id)
    assert row["status"] == "failed"
    # Issue 43：自动重试预算由队列 attempt 管理（业务行 retry_count 不再
    # 反映自动重试次数）；预算耗尽后任务驻留等待手动重试。
    assert h.service._task_queue.pending_count("video") == 1  # noqa: SLF001
    assert h.service._task_queue.claim_next("video", "probe") is None  # noqa: SLF001


# ---------------------------------------------------------------------------
# 说明文字修改与删除
# ---------------------------------------------------------------------------


def test_description_manual_update_and_delete_impact(tmp_path: Path) -> None:
    h = _VideoHarness(tmp_path)
    h.wan.script = _succeeded_script()
    h.service.submit(h.account_id, h.conversation_id, h.message_id, "一条静谧的河")
    h.run_pending()
    projection = h.message_projection(h.message_id)
    asset_id = projection["asset_id"]

    updated = h.service.update_description(
        h.account_id, h.conversation_id, asset_id, "晨雾中的河流，画面缓缓推进。"
    )
    assert updated.description == "晨雾中的河流，画面缓缓推进。"
    assert updated.description_source == VideoDescriptionSource.MANUAL

    # 删除：消息引用投影 deleted 标记 + 对象清理。
    result = h.service.delete_asset(h.account_id, h.conversation_id, asset_id)
    assert result.removed_objects == 1
    assert result.updated_messages == 1
    assert result.object_status == "cleaned"
    projection = h.message_projection(h.message_id)
    assert projection["deleted"] is True
    try:
        h.service.get_asset(h.account_id, h.conversation_id, asset_id)
    except VideoError as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("已删除资产应 404")

    # 幂等：再次删除返回零计数投影。
    again = h.service.delete_asset(h.account_id, h.conversation_id, asset_id)
    assert again.removed_objects == 0
    assert again.updated_messages == 0


def test_delete_object_pending_cleanup_retries(tmp_path: Path) -> None:
    h = _VideoHarness(tmp_path)
    h.wan.script = _succeeded_script()
    h.service.submit(h.account_id, h.conversation_id, h.message_id, "一条静谧的河")
    h.run_pending()
    projection = h.message_projection(h.message_id)
    asset_id = projection["asset_id"]
    result = h.service.delete_asset(h.account_id, h.conversation_id, asset_id)
    # 对象已标记待清理（物理文件已移除则立刻清理；此处断言无残留资产）。
    assert result.object_status in ("cleaned", "pending_cleanup")
    # 清理轮可重试且不报错。
    cleaned = h.object_repo.run_pending_cleanups()
    assert cleaned >= 0


def test_empty_description_rejected(tmp_path: Path) -> None:
    h = _VideoHarness(tmp_path)
    h.wan.script = _succeeded_script()
    h.service.submit(h.account_id, h.conversation_id, h.message_id, "一条静谧的河")
    h.run_pending()
    projection = h.message_projection(h.message_id)
    asset_id = projection["asset_id"]
    try:
        h.service.update_description(h.account_id, h.conversation_id, asset_id, "  ")
    except VideoError as exc:
        assert exc.code == "invalid_description"
        assert exc.status_code == 422
    else:
        raise AssertionError("空说明文字应被拒绝")


# ---------------------------------------------------------------------------
# 账户隔离
# ---------------------------------------------------------------------------


def test_cross_account_isolation(tmp_path: Path) -> None:
    h = _VideoHarness(tmp_path)
    h.wan.script = _succeeded_script()
    task = h.service.submit(h.account_id, h.conversation_id, h.message_id, "一条静谧的河")
    h.run_pending()
    projection = h.message_projection(h.message_id)
    asset_id = projection["asset_id"]

    other = h.seed_other_account()
    # 跨账户任务/资产/字节/取消/重试一律 404，不泄漏存在性。
    for method in (
        lambda: h.service.get_task(other, "conv-b", task.task_id),
        lambda: h.service.cancel(other, "conv-b", task.task_id),
        lambda: h.service.retry(other, "conv-b", task.task_id),
        lambda: h.service.get_asset(other, "conv-b", asset_id),
        lambda: h.service.get_video_bytes(other, "conv-b", asset_id),
        lambda: h.service.update_description(other, "conv-b", asset_id, "x"),
        lambda: h.service.delete_asset(other, "conv-b", asset_id),
    ):
        try:
            method()
        except VideoError as exc:
            assert exc.status_code == 404
        else:
            raise AssertionError("跨账户访问应 404")
    # 其他账户看不到任何对象。
    assert h.object_count(other) == 0


def test_submit_requires_owned_message(tmp_path: Path) -> None:
    h = _VideoHarness(tmp_path)
    other = h.seed_other_account()
    try:
        h.service.submit(other, "conv-b", h.message_id, "一条静谧的河")
    except VideoError as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("跨账户消息提交应 404")


def test_prompt_length_validation(tmp_path: Path) -> None:
    h = _VideoHarness(tmp_path)
    try:
        h.service.submit(h.account_id, h.conversation_id, h.message_id, " " * 10)
    except VideoError as exc:
        assert exc.code == "missing_prompt"
    else:
        raise AssertionError("空提示词应被拒绝")
    try:
        h.service.submit(h.account_id, h.conversation_id, h.message_id, "长" * 2001)
    except VideoError as exc:
        assert exc.code == "prompt_too_long"
    else:
        raise AssertionError("超长提示词应被拒绝")


def test_audit_contains_no_video_bytes_or_prompt(tmp_path: Path) -> None:
    h = _VideoHarness(tmp_path)
    h.wan.script = _succeeded_script()
    h.service.submit(h.account_id, h.conversation_id, h.message_id, "一条静谧的河")
    h.run_pending()
    assert len(h.audit.events) >= 2  # submit + complete
    serialized = "".join(
        str(event) for event in h.audit.events if isinstance(event, dict)
    )
    assert "一条静谧的河" not in serialized
    assert str(_VIDEO_BYTES) not in serialized
    for event in h.audit.events:
        assert event["action"] in ("video_task_submit", "video_task_complete")
