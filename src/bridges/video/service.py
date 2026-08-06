"""文生视频编排服务（Issue 32）。

固定使用 wan2.7-t2v-2026-06-12（ADR-0007：Wan 是模型矩阵唯一非 Qwen
系列例外，仍使用同一账户级百炼密钥）。用户从聊天提交视频要求后，任务
进入 ``video_tasks`` 状态机并关联助手消息：

- API 进程只做提交/查询/取消/重试/资产管理（任务与消息投影在同一事务
  内落库，不存在孤儿任务）；
- 后台执行器进程按租约领取任务：queued/failed 先提交（POST DashScope
  video-synthesis——请求只携带提示词，不发送完整聊天、画像、项目目录
  或任何账户秘密），有云端任务单次轮询，取消中的任务先尽力通知云端再
  收敛为已取消；终态成功后把真实视频字节转存账户对象库、建立账户隔离
  资产（提示/模型/供应商任务标识/创建时间/可访问文字说明）并条件更新
  任务与消息投影；
- 取消后 worker 的条件发布（``WHERE status IN ('submitting','generating')``）
  保证迟到结果不会进入对话或资产库：被取消的任务即使云端已生成，本地
  不建资产、不更新消息，已建对象立即回收。

失败只重试同一模型快照（经 ModelGateway 运行锁固化模型标识），自动重试
上限由 ``MAX_AUTO_RETRIES`` 约束、手动重试重置计数；绝不切换模型或模拟
成功；审计日志不复制视频字节与提示词正文。
"""

from __future__ import annotations

import contextlib
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from bridges.ai.model_gateway import ModelGateway
from bridges.ai.qwen_wan_adapter import DEFAULT_VIDEO_SIZE
from bridges.chat.repository import ConversationRepository
from bridges.contracts.ai import ModelCallStatus
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.video import (
    VideoAssetProjection,
    VideoDeletionProjection,
    VideoDescriptionSource,
    VideoError,
    VideoTaskProjection,
    VideoTaskStatus,
)
from bridges.contracts.workflows import RunContextEnvelope
from bridges.observability.service import ObservabilityService
from bridges.runtime.queue import (
    Claim,
    RetryKind,
    TaskPermanentError,
    TaskQueue,
    TaskRetryError,
    TaskWorker,
)
from bridges.storage.database import BridgesDatabase
from bridges.storage.errors import StorageError
from bridges.storage.repository import BridgesObjectRepository

#: 视频任务队列名（task_claims 调度表）。
_VIDEO_QUEUE = "video"

#: 每账户每轮最多领取的任务数（视频生成昂贵且需逐轮轮询云端）。
CLAIM_BATCH_SIZE = 1
#: 云端轮询租约：领取后在该时间内必须完成下一个步骤（提交/轮询/取消
#: 收敛），超过视为中断可重新领取。
POLL_LEASE_SECONDS = 300
#: 云端任务轮询次数上限（每次 tick 一次，视频生成分钟级，约 1 小时）；
#: 超过判定超时，可重试后重新提交。
MAX_CLOUD_POLLS = 60
#: 失败任务的自动重试上限：超过后不再自动领取，只等用户手动重试
#: （retry 重置计数）。
MAX_AUTO_RETRIES = 3
#: 提示词长度上限（与消息正文上限一致的保守值）。
PROMPT_MAX_LENGTH = 2000
#: 可访问文字说明确定性降级模板的提示词摘要长度。
_DESCRIPTION_PROMPT_SUMMARY = 80
#: 永久失败错误码：重试不会因输入变化而成功（如供应商完成但结果缺失），
#: 投影与前端据此隐藏重试入口。
_PERMANENT_FAILURE_CODES = {"empty_result"}


class _CancelledRaceError(Exception):
    """任务在 worker 处理期间被取消；用于回滚本次未发布的结果。"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def display_video_task_status(
    raw_status: str | None, lease_expires_at: str | None = None
) -> VideoTaskStatus:
    """把数据库原始状态映射为对外呈现状态。

    ``submitting``/``generating`` 的领取租约过期且任务未终态时呈现
    ``recovery``（上次处理中断，后台恢复中）；取消中/取消/成功/失败
    原样呈现。
    """
    if raw_status is None:
        return VideoTaskStatus.QUEUED
    if raw_status in ("submitting", "generating"):
        if lease_expires_at is not None:
            try:
                expired = datetime.fromisoformat(lease_expires_at) < datetime.now(UTC)
            except ValueError:
                expired = False
            if expired:
                return VideoTaskStatus.RECOVERY
        return VideoTaskStatus(raw_status)
    if raw_status == "queued":
        return VideoTaskStatus.QUEUED
    if raw_status == "succeeded":
        return VideoTaskStatus.SUCCEEDED
    if raw_status == "failed":
        return VideoTaskStatus.FAILED
    if raw_status == "cancelling":
        return VideoTaskStatus.CANCELLING
    if raw_status == "cancelled":
        return VideoTaskStatus.CANCELLED
    return VideoTaskStatus.QUEUED


class VideoService:
    """视频任务的写模型、资产面与后台处理轮；全部操作限定在账户内。"""

    def __init__(
        self,
        *,
        database: BridgesDatabase,
        gateway: ModelGateway,
        object_repository: BridgesObjectRepository,
        chat_repository: ConversationRepository | None = None,
        observability_service: ObservabilityService | None = None,
        task_queue: TaskQueue | None = None,
    ) -> None:
        self._db = database
        self._gateway = gateway
        self._objects = object_repository
        self._repo = chat_repository
        self._observability = observability_service
        # Issue 43：领取/租约/退避/崩溃恢复由统一任务队列承担；本服务
        # 只提供「处理这一个任务步骤」的 handler（云端状态回写保留在
        # 业务表）。
        self._task_queue = task_queue or TaskQueue(database)
        self._task_queue.set_lease_seconds(_VIDEO_QUEUE, POLL_LEASE_SECONDS)
        self._worker = TaskWorker(
            self._task_queue,
            _VIDEO_QUEUE,
            "video-worker",
            self._handle_video_claim,
            default_max_attempts=MAX_AUTO_RETRIES,
        )

    # ------------------------------------------------------------------
    # 提交（API 进程；任务与消息投影同一事务落库）
    # ------------------------------------------------------------------

    def submit(
        self, account_id: str, conversation_id: str, message_id: str, prompt: str
    ) -> VideoTaskProjection:
        """提交一个文生视频任务并关联到当前助手消息。"""
        prompt = prompt.strip()
        if not prompt:
            raise VideoError("missing_prompt", "视频请求必须包含提示词。")
        if len(prompt) > PROMPT_MAX_LENGTH:
            raise VideoError(
                "prompt_too_long", f"提示词超过 {PROMPT_MAX_LENGTH} 字限制。"
            )
        # 消息归属校验：任务与消息投影原子落库，跨账户消息一律 404。
        if self._repo is not None:
            record = self._repo.get_message(account_id, message_id)
            if record is None or record.conversation_id != conversation_id:
                raise VideoError(
                    "message_not_found",
                    "消息不存在或没有访问权限。",
                    status_code=404,
                )

        task_id = secrets.token_urlsafe(16)
        now = _now()
        created = datetime.now(UTC)
        try:
            with self._db.transaction():
                scoped = self._db.scoped(account_id)
                scoped.execute(
                    "INSERT INTO video_tasks(task_id, account_id, conversation_id,"
                    " message_id, prompt, status, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)",
                    (
                        task_id,
                        account_id,
                        conversation_id,
                        message_id,
                        prompt,
                        now,
                        now,
                    ),
                )
                # 助手消息投影与任务同一事务落库：不存在"任务已建但消息
                # 看不到"的孤儿任务。
                projection = self._task_projection(
                    task_id=task_id,
                    prompt=prompt,
                    status=VideoTaskStatus.QUEUED,
                    created=created,
                    updated=created,
                )
                # 领取型任务入队（同一事务）：后台执行器按统一队列契约
                # 领取处理，无需每轮全表扫描 queued 行。
                self._task_queue.enqueue(
                    _VIDEO_QUEUE, f"video:{account_id}:{task_id}"
                )
                scoped.execute(
                    "UPDATE messages SET video = ?, updated_at = ?"
                    " WHERE message_id = ? AND account_id = ?",
                    (
                        projection.model_dump_json(),
                        now,
                        message_id,
                        account_id,
                    ),
                )
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一转换为领域错误
            raise VideoError("submit_failed", "视频任务提交失败，请重试。") from exc

        self._audit(
            account_id,
            task_id,
            AuditAction.VIDEO_TASK_SUBMIT,
            AuditResult.SUCCESS,
            "视频任务已提交。",
            {"model_id": self._model_id()},
        )
        return projection

    # ------------------------------------------------------------------
    # 任务查询 / 取消 / 重试（API 进程）
    # ------------------------------------------------------------------

    def get_task(
        self, account_id: str, conversation_id: str, task_id: str
    ) -> VideoTaskProjection:
        """返回任务投影；不存在或跨账户一律 404。"""
        row = self._task_row(account_id, task_id)
        if row is None or str(row["conversation_id"]) != conversation_id:
            raise VideoError(
                "task_not_found", "任务不存在或没有访问权限。", status_code=404
            )
        return self._task_projection_from_row(row)

    def cancel(
        self, account_id: str, conversation_id: str, task_id: str
    ) -> VideoTaskProjection:
        """取消任务：本地标记为「取消中」，worker 收敛为已取消。

        标记后尽力通知云端取消（失败静默——本地取消是权威）；worker 的
        条件发布拒绝迟到结果：即使云端已生成，本地不建资产、不更新消息，
        已建对象立即回收。已成功/已取消的任务幂等返回当前投影。
        """
        row = self._task_row(account_id, task_id)
        if row is None or str(row["conversation_id"]) != conversation_id:
            raise VideoError(
                "task_not_found", "任务不存在或没有访问权限。", status_code=404
            )
        now = datetime.now(UTC)
        now_text = _now()
        try:
            with self._db.transaction():
                scoped = self._db.scoped(account_id)
                cursor = scoped.execute(
                    "UPDATE video_tasks SET status = 'cancelling', cancelled_at = ?,"
                    " updated_at = ? WHERE task_id = ? AND account_id = ?"
                    " AND status NOT IN ('succeeded', 'cancelled')",
                    (now_text, now_text, task_id, account_id),
                )
                if cursor.rowcount == 0:
                    # 已终态（成功/取消）：幂等返回当前投影，不重复取消失效；
                    # 也记为一次"未生效的取消尝试"，审计语义不冒充取消成功。
                    self._audit(
                        account_id,
                        task_id,
                        AuditAction.VIDEO_TASK_CANCEL,
                        AuditResult.BLOCKED,
                        "任务已终态，取消未生效（幂等返回当前投影）。",
                        {},
                    )
                    return self.get_task(account_id, conversation_id, task_id)
                cancelling = self._task_projection(
                    task_id=task_id,
                    prompt=str(row["prompt"]),
                    status=VideoTaskStatus.CANCELLING,
                    asset_id=str(row["asset_id"]) if row["asset_id"] else None,
                    result_object_id=(
                        str(row["result_object_id"])
                        if row["result_object_id"]
                        else None
                    ),
                    created=datetime.fromisoformat(str(row["created_at"])),
                    updated=now,
                )
                if row["message_id"]:
                    scoped.execute(
                        "UPDATE messages SET video = ?, updated_at = ?"
                        " WHERE message_id = ? AND account_id = ?",
                        (
                            cancelling.model_dump_json(),
                            now_text,
                            str(row["message_id"]),
                            account_id,
                        ),
                    )
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise VideoError("cancel_failed", "任务取消失败，请重试。") from exc

        cloud_task_id = str(row["cloud_task_id"]) if row["cloud_task_id"] else None
        if cloud_task_id:
            # 尽力通知云端取消；失败静默——本地取消是权威，worker 收敛时
            # 会再尝试一次。
            with contextlib.suppress(Exception):
                self._gateway.invoke(
                    "qwen_wan",
                    "1",
                    self._run_context(account_id, conversation_id, "video_cancel"),
                    {"kind": "cancel", "cloud_task_id": cloud_task_id},
                )
        self._audit(
            account_id,
            task_id,
            AuditAction.VIDEO_TASK_CANCEL,
            AuditResult.SUCCESS,
            "视频任务取消已受理。",
            {},
        )
        return self.get_task(account_id, conversation_id, task_id)

    def retry(
        self, account_id: str, conversation_id: str, task_id: str
    ) -> VideoTaskProjection:
        """重试失败任务：同输入（提示不变）重新入队，固定同一快照。

        用户手动重试重置自动重试计数与云端引用，等待后台执行器重新
        提交；成功/取消/取消中/运行中任务不可重试。
        """
        row = self._task_row(account_id, task_id)
        if row is None or str(row["conversation_id"]) != conversation_id:
            raise VideoError(
                "task_not_found", "任务不存在或没有访问权限。", status_code=404
            )
        if str(row["status"]) != "failed":
            raise VideoError(
                "task_not_retryable",
                "只有失败的任务可以重试。",
                status_code=409,
            )
        now = datetime.now(UTC)
        now_text = _now()
        try:
            with self._db.transaction():
                scoped = self._db.scoped(account_id)
                scoped.execute(
                    "UPDATE video_tasks SET status = 'queued', retry_count = 0,"
                    " cloud_task_id = NULL, poll_count = 0, error_code = NULL,"
                    " error_message = NULL, updated_at = ?"
                    " WHERE task_id = ? AND account_id = ? AND status = 'failed'",
                    (now_text, task_id, account_id),
                )
                if row["message_id"]:
                    retried = self._task_projection(
                        task_id=task_id,
                        prompt=str(row["prompt"]),
                        status=VideoTaskStatus.QUEUED,
                        asset_id=str(row["asset_id"]) if row["asset_id"] else None,
                        result_object_id=(
                            str(row["result_object_id"])
                            if row["result_object_id"]
                            else None
                        ),
                        created=datetime.fromisoformat(str(row["created_at"])),
                        updated=now,
                    )
                    scoped.execute(
                        "UPDATE messages SET video = ?, updated_at = ?"
                        " WHERE message_id = ? AND account_id = ?",
                        (retried.model_dump_json(), now_text, str(row["message_id"]), account_id),
                    )
                # 手动重试重新入队（同一事务）：重置退避计数，恢复自动领取。
                self._task_queue.enqueue(
                    _VIDEO_QUEUE, f"video:{account_id}:{task_id}"
                )
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise VideoError("retry_failed", "任务重试失败，请重试。") from exc
        return self.get_task(account_id, conversation_id, task_id)

    # ------------------------------------------------------------------
    # 资产面（说明文字 / 字节 / 删除）
    # ------------------------------------------------------------------

    def get_asset(
        self, account_id: str, conversation_id: str, asset_id: str
    ) -> VideoAssetProjection:
        """返回资产投影；删除或跨账户一律 404。"""
        row = self._asset_row(account_id, asset_id)
        if (
            row is None
            or str(row["conversation_id"]) != conversation_id
            or int(row["deleted"]) == 1
        ):
            raise VideoError(
                "asset_not_found", "视频不存在或没有访问权限。", status_code=404
            )
        return self._asset_projection_from_row(row)

    def get_video_bytes(
        self,
        account_id: str,
        conversation_id: str,
        asset_id: str,
    ) -> tuple[bytes, str, int]:
        """返回视频字节与媒体类型；跨账户或已删除资产一律 404。

        响应经 API 层附加私有缓存头，杜绝缓存跨账户复用。
        """
        asset = self._asset_row(account_id, asset_id)
        if (
            asset is None
            or str(asset["conversation_id"]) != conversation_id
            or int(asset["deleted"]) == 1
        ):
            raise VideoError(
                "asset_not_found", "视频不存在或没有访问权限。", status_code=404
            )
        try:
            content = self._objects.get_content(account_id, str(asset["object_id"]))
        except StorageError as exc:
            raise VideoError(
                "video_unavailable",
                "视频内容当前不可用，请稍后重试。",
                status_code=503,
                retryable=True,
            ) from exc
        return (
            content,
            str(asset["media_type"]),
            int(asset["content_length"]),
        )

    def update_description(
        self,
        account_id: str,
        conversation_id: str,
        asset_id: str,
        description: str,
    ) -> VideoAssetProjection:
        """修改资产可访问文字说明（来源标记为 manual）；删除或跨账户 404。"""
        description = description.strip()
        if not description:
            raise VideoError(
                "invalid_description", "可访问文字说明不能为空。", status_code=422
            )
        row = self._asset_row(account_id, asset_id)
        if row is None or str(row["conversation_id"]) != conversation_id:
            raise VideoError(
                "asset_not_found", "视频不存在或没有访问权限。", status_code=404
            )
        now = _now()
        with self._db.transaction():
            cursor = self._db.scoped(account_id).execute(
                "UPDATE video_assets SET description = ?, description_source ="
                " 'manual', updated_at = ? WHERE asset_id = ? AND account_id = ?"
                " AND deleted = 0",
                (description, now, asset_id, account_id),
            )
            if cursor.rowcount == 0:
                raise VideoError(
                    "asset_not_found", "视频不存在或没有访问权限。", status_code=404
                )
        self._audit(
            account_id,
            asset_id,
            AuditAction.VIDEO_DESCRIPTION_UPDATE,
            AuditResult.SUCCESS,
            "视频可访问文字说明已更新。",
            {"source": VideoDescriptionSource.MANUAL.value},
        )
        return self.get_asset(account_id, conversation_id, asset_id)

    def delete_asset(
        self, account_id: str, conversation_id: str, asset_id: str
    ) -> VideoDeletionProjection:
        """删除资产：消息引用、资产元数据与本地对象保持一致。

        删除不物理清除资产记录（保留审计追溯），而是：标记资产删除 →
        更新引用该资产的消息投影（deleted 标记）→ 视频对象标记待清理并
        立即尝试物理清理（失败保留 pending_cleanup 由清理轮重试，可观察
        可恢复）。幂等：已删除资产返回零计数投影。
        """
        row = self._asset_row(account_id, asset_id)
        if row is None or str(row["conversation_id"]) != conversation_id:
            raise VideoError(
                "asset_not_found", "视频不存在或没有访问权限。", status_code=404
            )
        if int(row["deleted"]) == 1:
            return VideoDeletionProjection(
                asset_id=asset_id,
                removed_objects=0,
                updated_messages=0,
                object_status="cleaned",
                deleted_at=datetime.now(UTC),
            )
        now = datetime.now(UTC)
        now_text = _now()
        updated_messages = 0
        try:
            with self._db.transaction():
                scoped = self._db.scoped(account_id)
                scoped.execute(
                    "UPDATE video_assets SET deleted = 1, updated_at = ?"
                    " WHERE asset_id = ? AND account_id = ?",
                    (now_text, asset_id, account_id),
                )
                # 消息引用维护：引用该资产的助手消息投影加 deleted 标记。
                rows = scoped.execute(
                    "SELECT message_id, video FROM messages"
                    " WHERE account_id = ? AND video IS NOT NULL",
                    (account_id,),
                ).fetchall()
                for msg in rows:
                    projection = _load_task_projection(msg["video"])
                    if projection is None or projection.asset_id != asset_id:
                        continue
                    updated = projection.model_copy(update={"deleted": True})
                    scoped.execute(
                        "UPDATE messages SET video = ?, updated_at = ?"
                        " WHERE message_id = ? AND account_id = ?",
                        (updated.model_dump_json(), now_text, str(msg["message_id"]), account_id),
                    )
                    updated_messages += 1
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise VideoError("delete_failed", "视频删除失败，请重试。") from exc

        object_status = "cleaned"
        with contextlib.suppress(Exception):
            self._objects.delete_object(account_id, str(row["object_id"]))
        if self._object_status(account_id, str(row["object_id"])) == "pending_cleanup":
            object_status = "pending_cleanup"
        self._audit(
            account_id,
            asset_id,
            AuditAction.VIDEO_ASSET_DELETE,
            AuditResult.SUCCESS,
            "视频资产已删除。",
            {
                "removed_objects": 1,
                "updated_messages": updated_messages,
                "object_status": object_status,
            },
        )
        return VideoDeletionProjection(
            asset_id=asset_id,
            removed_objects=1,
            updated_messages=updated_messages,
            object_status=object_status,
            deleted_at=now,
        )

    # ------------------------------------------------------------------
    # 后台执行器：领取 / 处理
    # ------------------------------------------------------------------

    def process_pending(self) -> str:
        """执行一轮视频任务处理：队列领取 → 提交/轮询/取消收敛。

        Issue 43：领取/租约/退避/崩溃恢复由统一领取型任务队列承担；
        每任务创建/手动重试时入队，worker 按租约领取并逐件处理一步。
        """
        handled, failed = self._worker.drain(max_steps=CLAIM_BATCH_SIZE)
        if handled == 0 and failed == 0:
            return "worker: 视频任务：无待处理任务。"
        return f"worker: 视频任务：本轮处理 {handled} 个任务步骤，失败 {failed} 次。"

    def _handle_video_claim(self, claim: Claim) -> None:
        """队列 handler：处理一个任务的一个步骤（提交/轮询/取消收敛）。

        轮询续轮（submitting/generating/cancelling）不消耗重试预算
        （count_attempt=False）；failed 每轮自动重试至上限，但永久
        失败（error_code=empty_result）不自动重领；终态收敛。
        """
        _, account_id, task_id = claim.task_key.split(":", 2)
        row = self._task_row(account_id, task_id)
        if row is None or str(row["status"]) in ("succeeded", "cancelled"):
            return  # 已收敛：跳过
        # 业务行呈现「进行中」（queued/failed → submitting + 租约）；
        # 领取调度本身由队列承担。
        with self._db.transaction():
            self._db.scoped(account_id).execute(
                "UPDATE video_tasks SET"
                " status = CASE WHEN status IN ('queued', 'failed')"
                " THEN 'submitting' ELSE status END,"
                " claimed_at = ?, lease_expires_at = ?, updated_at = ?"
                " WHERE task_id = ? AND account_id = ? AND status != 'cancelled'",
                (_now(), self._lease_expires(), _now(), task_id, account_id),
            )
        try:
            self._process_task(account_id, task_id)
        except _CancelledRaceError:
            # 处理期间被取消：任务进入 cancelling，仍需下一轮收敛取消
            # （尽力通知云端 + 置 cancelled），不在此 complete。
            row = self._task_row(account_id, task_id)
            if row is not None and str(row["status"]) == "cancelled":
                return
            raise TaskRetryError(
                "取消收敛中",
                retry_kind=RetryKind.FIXED,
                count_attempt=False,
            ) from None
        row = self._task_row(account_id, task_id)
        status = str(row["status"]) if row is not None else "succeeded"
        if status in ("succeeded", "cancelled"):
            return  # 完成/取消：收敛
        if status != "failed":
            # 提交成功或云端任务进行中：下一轮继续（不消耗预算）。
            raise TaskRetryError(
                "云端任务处理中",
                retry_kind=RetryKind.FIXED,
                count_attempt=False,
            )
        # failed：永久失败（供应商完成但结果缺失等）不自动重领；其余
        # 每轮立即自动重试至上限（与旧 claim 条件语义一致）。
        if str(row["error_code"]) == "empty_result":
            raise TaskPermanentError(
                str(row["error_message"]) or "供应商完成但结果缺失"
            )
        raise TaskRetryError(
            str(row["error_message"]) if row is not None else "任务失败",
            retry_kind=RetryKind.FIXED,
            max_attempts=MAX_AUTO_RETRIES,
        )

    def _process_task(self, account_id: str, task_id: str) -> bool:
        row = self._task_row(account_id, task_id)
        if row is None or str(row["status"]) == "cancelled":
            # 已取消任务不再处理（迟到结果隔离的第一道闸）。
            return False
        status = str(row["status"])
        if status == "cancelling":
            return self._cancel_step(account_id, task_id, row)
        if status == "submitting":
            return self._submit_step(account_id, task_id, row)
        return self._poll_step(account_id, task_id, row)

    # ------------------------------------------------------------------
    # 处理步骤：取消收敛 / 提交 / 轮询 / 发布
    # ------------------------------------------------------------------

    def _cancel_step(self, account_id: str, task_id: str, row: Any) -> bool:
        """收敛取消：尽力通知云端（本地取消是权威）后标记已取消。

        取消任务即使云端已生成也不发布：本轮不再轮询，状态直接收敛为
        已取消；已建对象（发布竞态回滚路径）不会到达这里。
        """
        cloud_task_id = str(row["cloud_task_id"]) if row["cloud_task_id"] else None
        if cloud_task_id:
            # 尽力取消云端任务；失败静默——本地取消是权威，云端任务
            # 即使继续生成，结果也不会被本地发布。
            with contextlib.suppress(Exception):
                self._gateway.invoke(
                    "qwen_wan",
                    "1",
                    self._run_context(account_id, str(row["conversation_id"]), "video_cancel"),
                    {"kind": "cancel", "cloud_task_id": cloud_task_id},
                )
        now = datetime.now(UTC)
        now_text = _now()
        try:
            with self._db.transaction():
                scoped = self._db.scoped(account_id)
                cursor = scoped.execute(
                    "UPDATE video_tasks SET status = 'cancelled', cancelled_at = ?,"
                    " updated_at = ? WHERE task_id = ? AND account_id = ?"
                    " AND status = 'cancelling'",
                    (now_text, now_text, task_id, account_id),
                )
                if cursor.rowcount == 0:
                    return True
                if row["message_id"]:
                    cancelled = self._task_projection(
                        task_id=task_id,
                        prompt=str(row["prompt"]),
                        status=VideoTaskStatus.CANCELLED,
                        asset_id=str(row["asset_id"]) if row["asset_id"] else None,
                        result_object_id=(
                            str(row["result_object_id"])
                            if row["result_object_id"]
                            else None
                        ),
                        created=datetime.fromisoformat(str(row["created_at"])),
                        updated=now,
                    )
                    scoped.execute(
                        "UPDATE messages SET video = ?, updated_at = ?"
                        " WHERE message_id = ? AND account_id = ?",
                        (
                            cancelled.model_dump_json(),
                            now_text,
                            str(row["message_id"]),
                            account_id,
                        ),
                    )
        except (StorageError, sqlite3.Error):
            return True  # 失败不阻断循环，下轮重领时再试
        return True

    def _submit_step(self, account_id: str, task_id: str, row: Any) -> bool:
        result = self._invoke_video(
            account_id,
            str(row["conversation_id"]),
            {
                "kind": "submit",
                "prompt": str(row["prompt"]),
                "size": DEFAULT_VIDEO_SIZE,
            },
        )
        if result.status != ModelCallStatus.SUCCESS or result.output is None:
            error_code = result.error_code or "submit_failed"
            error_message = result.error_message or "视频任务提交失败，请重试。"
            self._fail_task(account_id, task_id, error_code, error_message)
            return True
        cloud_task_id = str(result.output.get("cloud_task_id") or "")
        result_url = str(result.output.get("result_url") or "")
        now = _now()
        # 已知边界：若进程在供应商已接受（HTTP 成功）与本地落库之间崩溃，
        # 重启后任务停留在 submitting 且无 cloud_task_id，会重新提交一次
        # 供应商任务（供应商异步任务无幂等键，属可接受边界——本地不产生
        # 重复资产，重复的云端任务由其自身生命周期收敛）。
        if cloud_task_id:
            # 提交成功：记录云端任务标识（即使期间被取消也记录——这样
            # 取消收敛步骤能尽力通知云端），状态仅在未被取消时推进为
            # 生成中。
            with self._db.transaction():
                cursor = self._db.scoped(account_id).execute(
                    "UPDATE video_tasks SET cloud_task_id = ?,"
                    " status = CASE WHEN status = 'submitting' THEN 'generating'"
                    " ELSE status END, lease_expires_at = ?, model_id = ?,"
                    " error_code = NULL, error_message = NULL, updated_at = ?"
                    " WHERE task_id = ? AND account_id = ?"
                    " AND status IN ('submitting', 'cancelling')",
                    (
                        cloud_task_id,
                        self._lease_expires(),
                        result.lock.actual_model_id if result.lock else None,
                        now,
                        task_id,
                        account_id,
                    ),
                )
                if cursor.rowcount == 0:
                    # 提交与取消竞态（任务已终态）：云端任务不再被本地
                    # 轮询，结果自然隔离。
                    return True
            return True
        if result_url:
            # 供应商同步返回结果（少见）：直接走下载收敛路径。
            return self._finalize_step(account_id, task_id, row, result_url)
        self._fail_task(
            account_id,
            task_id,
            "empty_response",
            "视频生成接口没有返回任务。",
        )
        return True

    def _poll_step(self, account_id: str, task_id: str, row: Any) -> bool:
        cloud_task_id = str(row["cloud_task_id"])
        result = self._invoke_video(
            account_id,
            str(row["conversation_id"]),
            {"kind": "poll", "cloud_task_id": cloud_task_id},
        )
        if result.status != ModelCallStatus.SUCCESS or result.output is None:
            error_code = result.error_code or "poll_failed"
            error_message = result.error_message or "视频任务查询失败，请重试。"
            self._fail_task(account_id, task_id, error_code, error_message)
            return True
        cloud_status = str(result.output.get("cloud_status") or "RUNNING").upper()
        if cloud_status == "RUNNING":
            poll_count = int(row["poll_count"]) + 1
            if poll_count > MAX_CLOUD_POLLS:
                self._fail_task(
                    account_id,
                    task_id,
                    "cloud_timeout",
                    "云端生成超时，请重试。",
                )
                return True
            with self._db.transaction():
                self._db.scoped(account_id).execute(
                    "UPDATE video_tasks SET status = 'generating',"
                    " lease_expires_at = ?, poll_count = ?, model_id = ?,"
                    " updated_at = ? WHERE task_id = ? AND account_id = ?"
                    " AND status = 'generating'",
                    (
                        self._lease_expires(),
                        poll_count,
                        result.lock.actual_model_id if result.lock else None,
                        _now(),
                        task_id,
                        account_id,
                    ),
                )
            return True
        if cloud_status == "SUCCEEDED":
            result_url = str(result.output.get("result_url") or "")
            if not result_url:
                self._fail_task(
                    account_id,
                    task_id,
                    "empty_result",
                    "视频任务完成但没有返回视频。",
                )
                return True
            return self._finalize_step(account_id, task_id, row, result_url)
        # 云端失败：同输入重新提交即可（worker 自动重领）。
        self._fail_task(
            account_id,
            task_id,
            "cloud_failed",
            str(result.output.get("error_message") or "云端视频生成失败。"),
        )
        return True

    def _finalize_step(
        self, account_id: str, task_id: str, row: Any, result_url: str
    ) -> bool:
        """下载结果字节 → 转存对象库 → 建资产 → 条件发布。

        顺序保证取消隔离：先建对象（事务外），再在单个事务内条件更新
        任务（``WHERE status IN ('submitting', 'generating')``）并建立
        资产/消息引用；条件更新失败（已被取消）时回滚全部数据库写入并
        回收刚建的对象——迟到结果绝不进入对话或资产库。
        """
        result = self._invoke_video(
            account_id,
            str(row["conversation_id"]),
            {"kind": "fetch", "result_url": result_url},
        )
        if result.status != ModelCallStatus.SUCCESS or result.output is None:
            error_code = result.error_code or "download_failed"
            error_message = result.error_message or "视频结果下载失败，请重试。"
            self._fail_task(account_id, task_id, error_code, error_message)
            return True
        video_bytes = result.output.get("video_bytes")
        if not isinstance(video_bytes, bytes) or not video_bytes:
            self._fail_task(
                account_id,
                task_id,
                "empty_video",
                "视频结果为空。",
            )
            return True
        media_type = str(result.output.get("media_type") or "video/mp4")
        stored = self._objects.create_object(
            account_id,
            original_filename=f"video-{task_id}.mp4",
            content=video_bytes,
            media_type=media_type,
        )

        prompt = str(row["prompt"])
        asset_id = secrets.token_urlsafe(16)
        model_id = result.lock.actual_model_id if result.lock else None
        now = datetime.now(UTC)
        now_text = _now()
        message_id = str(row["message_id"]) if row["message_id"] else None
        try:
            with self._db.transaction():
                scoped = self._db.scoped(account_id)
                cursor = scoped.execute(
                    "UPDATE video_tasks SET status = 'succeeded',"
                    " result_object_id = ?, asset_id = ?, model_id = ?,"
                    " error_code = NULL, error_message = NULL, updated_at = ?"
                    " WHERE task_id = ? AND account_id = ?"
                    " AND status IN ('submitting', 'generating')",
                    (stored.object_id, asset_id, model_id, now_text, task_id, account_id),
                )
                if cursor.rowcount == 0:
                    raise _CancelledRaceError()
                scoped.execute(
                    "INSERT INTO video_assets(asset_id, account_id, conversation_id,"
                    " description, description_source, object_id, prompt, model_id,"
                    " cloud_task_id, media_type, content_length, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        asset_id,
                        account_id,
                        str(row["conversation_id"]),
                        self._default_description(prompt),
                        VideoDescriptionSource.PROMPT.value,
                        stored.object_id,
                        prompt,
                        model_id,
                        str(row["cloud_task_id"]) if row["cloud_task_id"] else None,
                        media_type,
                        stored.content_length,
                        now_text,
                        now_text,
                    ),
                )
                if message_id:
                    succeeded = self._task_projection(
                        task_id=task_id,
                        prompt=prompt,
                        status=VideoTaskStatus.SUCCEEDED,
                        model_id=model_id,
                        asset_id=asset_id,
                        result_object_id=stored.object_id,
                        created=datetime.fromisoformat(str(row["created_at"])),
                        updated=now,
                    )
                    scoped.execute(
                        "UPDATE messages SET video = ?, content = ?, updated_at = ?"
                        " WHERE message_id = ? AND account_id = ?",
                        (
                            succeeded.model_dump_json(),
                            "视频生成完成。",
                            now_text,
                            message_id,
                            account_id,
                        ),
                    )
        except _CancelledRaceError:
            # 迟到结果隔离：任务已取消，回收刚建的对象，不发布资产。
            with contextlib.suppress(Exception):
                self._objects.delete_object(account_id, stored.object_id)
            raise
        except (StorageError, sqlite3.Error):
            with contextlib.suppress(Exception):
                self._objects.delete_object(account_id, stored.object_id)
            self._fail_task(
                account_id,
                task_id,
                "storage_error",
                "视频资产保存失败，请重试。",
            )
            return True

        self._audit(
            account_id,
            task_id,
            AuditAction.VIDEO_TASK_COMPLETE,
            AuditResult.SUCCESS,
            "视频任务完成。",
            {
                "content_length": stored.content_length,
                "model_id": model_id,
                "description_source": VideoDescriptionSource.PROMPT.value,
            },
        )
        return True

    def _fail_task(
        self,
        account_id: str,
        task_id: str,
        error_code: str,
        error_message: str,
    ) -> None:
        """把任务置为失败并同步消息投影（未终态才生效）。

        可重试语义由错误码统一判定：不在永久失败集合内的失败任务在
        自动重试上限内由 worker 自动重领，前端也显示重试入口。
        """
        row = self._task_row(account_id, task_id)
        if row is None:
            return
        now = datetime.now(UTC)
        now_text = _now()
        try:
            with self._db.transaction():
                scoped = self._db.scoped(account_id)
                cursor = scoped.execute(
                    "UPDATE video_tasks SET status = 'failed', error_code = ?,"
                    " error_message = ?, cloud_task_id = NULL, poll_count = 0,"
                    " updated_at = ? WHERE task_id = ?"
                    " AND account_id = ? AND status NOT IN ('succeeded',"
                    " 'cancelled', 'cancelling', 'failed')",
                    (error_code, error_message, now_text, task_id, account_id),
                )
                if cursor.rowcount == 0:
                    return
                if row["message_id"]:
                    failed = self._task_projection(
                        task_id=task_id,
                        prompt=str(row["prompt"]),
                        status=VideoTaskStatus.FAILED,
                        asset_id=str(row["asset_id"]) if row["asset_id"] else None,
                        result_object_id=(
                            str(row["result_object_id"])
                            if row["result_object_id"]
                            else None
                        ),
                        error_code=error_code,
                        error_message=error_message,
                        created=datetime.fromisoformat(str(row["created_at"])),
                        updated=now,
                    )
                    scoped.execute(
                        "UPDATE messages SET video = ?, updated_at = ?"
                        " WHERE message_id = ? AND account_id = ?",
                        (failed.model_dump_json(), now_text, str(row["message_id"]), account_id),
                    )
        except (StorageError, sqlite3.Error):
            return  # 失败落库不阻断处理循环，下轮重领时再试

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _default_description(prompt: str) -> str:
        """可访问文字说明：提示词确定性摘要，不冒充模型理解。

        用户之后可随时手动修改（来源变为 manual）。
        """
        summary = prompt[:_DESCRIPTION_PROMPT_SUMMARY]
        if len(prompt) > _DESCRIPTION_PROMPT_SUMMARY:
            summary += "…"
        return f"由提示词「{summary}」生成的视频"

    def _invoke_video(
        self,
        account_id: str,
        conversation_id: str,
        payload: dict[str, Any],
    ) -> Any:
        result = self._gateway.invoke(
            "qwen_wan",
            "1",
            self._run_context(account_id, conversation_id, "video_generation"),
            payload,
        )
        if result.lock is not None and self._repo is not None:
            with contextlib.suppress(Exception):
                self._repo.insert_run_lock(account_id, result.lock)
        return result

    def _model_id(self) -> str:
        """返回固定视频模型快照标识（ADR-0009 矩阵单一事实源）。"""
        from bridges.credentials.matrix import VIDEO_MODEL_ID

        return VIDEO_MODEL_ID

    def _run_context(
        self, account_id: str, conversation_id: str, workflow_name: str
    ) -> RunContextEnvelope:
        return RunContextEnvelope(
            run_id=f"{workflow_name}-{secrets.token_urlsafe(8)}",
            account_id=account_id,
            project_id=conversation_id,
            workflow_name=workflow_name,
            workflow_version="1",
            object_domain=ObjectDomain.PERSONAL_VAULT,
            submitted_at=datetime.now(UTC),
        )

    def _lease_expires(self) -> str:
        return (
            datetime.now(UTC) + timedelta(seconds=POLL_LEASE_SECONDS)
        ).isoformat(timespec="seconds")

    def _task_projection(
        self,
        *,
        task_id: str,
        prompt: str,
        status: VideoTaskStatus,
        model_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        asset_id: str | None = None,
        result_object_id: str | None = None,
        created: datetime,
        updated: datetime,
    ) -> VideoTaskProjection:
        return VideoTaskProjection(
            task_id=task_id,
            prompt=prompt,
            model_id=model_id,
            status=status,
            error_code=error_code,
            error_message=error_message,
            # 可重试语义由错误码统一判定：不在永久失败集合内的失败任务
            # 前端显示重试入口（自动重试预算由 retry_count 独立约束）。
            retryable=(
                status == VideoTaskStatus.FAILED
                and error_code not in _PERMANENT_FAILURE_CODES
            ),
            asset_id=asset_id,
            result_object_id=result_object_id,
            created_at=created,
            updated_at=updated,
        )

    def _task_projection_from_row(self, row: Any) -> VideoTaskProjection:
        return self._task_projection(
            task_id=str(row["task_id"]),
            prompt=str(row["prompt"]),
            model_id=str(row["model_id"]) if row["model_id"] else None,
            status=display_video_task_status(
                str(row["status"]),
                str(row["lease_expires_at"]) if row["lease_expires_at"] else None,
            ),
            error_code=str(row["error_code"]) if row["error_code"] else None,
            error_message=(
                str(row["error_message"]) if row["error_message"] else None
            ),
            asset_id=str(row["asset_id"]) if row["asset_id"] else None,
            result_object_id=(
                str(row["result_object_id"]) if row["result_object_id"] else None
            ),
            created=datetime.fromisoformat(str(row["created_at"])),
            updated=datetime.fromisoformat(str(row["updated_at"])),
        )

    def _asset_projection_from_row(self, row: Any) -> VideoAssetProjection:
        return VideoAssetProjection(
            asset_id=str(row["asset_id"]),
            description=str(row["description"]),
            description_source=VideoDescriptionSource(
                str(row["description_source"])
            ),
            object_id=str(row["object_id"]),
            prompt=str(row["prompt"]),
            model_id=str(row["model_id"]) if row["model_id"] else None,
            cloud_task_id=str(row["cloud_task_id"]) if row["cloud_task_id"] else None,
            media_type=str(row["media_type"]),
            content_length=int(row["content_length"]),
            deleted=int(row["deleted"]) == 1,
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    def _task_row(self, account_id: str, task_id: str) -> sqlite3.Row | None:
        try:
            row = self._db.scoped(account_id).execute(
                "SELECT * FROM video_tasks WHERE task_id = ? AND account_id = ?",
                (task_id, account_id),
            ).fetchone()
        except StorageError:
            return None
        return cast(sqlite3.Row | None, row)

    def _asset_row(self, account_id: str, asset_id: str) -> sqlite3.Row | None:
        try:
            row = self._db.scoped(account_id).execute(
                "SELECT * FROM video_assets WHERE asset_id = ? AND account_id = ?",
                (asset_id, account_id),
            ).fetchone()
        except StorageError:
            return None
        return cast(sqlite3.Row | None, row)

    def _object_status(self, account_id: str, object_id: str) -> str:
        try:
            return self._objects.get_object(account_id, object_id).status
        except StorageError:
            return "gone"

    def _audit(
        self,
        account_id: str,
        ref: str,
        action: AuditAction,
        result: AuditResult,
        reason: str,
        details: dict[str, Any],
    ) -> None:
        if self._observability is None:
            return
        self._observability.log_audit(
            actor_account_id=account_id,
            action=action,
            result=result,
            object_refs=[ref],
            reason=reason,
            details=details,
        )


def _load_task_projection(raw: Any) -> VideoTaskProjection | None:
    """从消息 video 列解析任务投影；损坏快照按不可引用处理。"""
    if not raw:
        return None
    try:
        return VideoTaskProjection.model_validate_json(str(raw))
    except Exception:  # noqa: BLE001 - 损坏快照不阻断删除
        return None


__all__ = [
    "VideoService",
    "display_video_task_status",
]
