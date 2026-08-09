"""图片生成与编辑编排服务（Issue 31）。

固定使用 qwen-image-2.0-pro-2026-06-22（ADR-0009）。用户从聊天提交
生成或编辑请求后，任务进入 ``image_tasks`` 状态机并关联助手消息：

- API 进程只做提交/查询/取消/重试/资产管理（任务与消息投影在同一
  事务内落库，不存在孤儿任务）；
- 后台执行器进程按租约领取任务：无云端任务先提交（编辑图片以 data
  URL 随请求体直传——只发送编辑所需图片与提示），有云端任务单次
  轮询；终态成功后把真实图片字节转存账户对象库、建立版本化资产
  （编辑创建新版本并保留来源/提示/模型快照/时间关系，不覆盖原图）、
  生成替代文本（核心视觉模型自动生成，失败确定性降级）、条件更新
  任务与消息投影；
- 取消后 worker 的条件更新（``WHERE status != 'cancelled'``）保证
  迟到结果不会发布为成功资产：被取消的任务即使云端已生成，本地
  不建资产、不更新消息，已建对象立即回收。

失败只重试同一模型快照（经 ModelGateway 运行锁固化模型标识），
绝不切换模型或模拟成功；审计日志不复制图片字节与提示词正文。
"""

from __future__ import annotations

import base64
import contextlib
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from bridges.ai.model_gateway import ModelGateway
from bridges.ai.qwen_image_adapter import DEFAULT_IMAGE_SIZE, image_data_url
from bridges.chat.repository import ConversationRepository
from bridges.contracts.ai import ModelCallStatus
from bridges.contracts.image import (
    ImageAltTextSource,
    ImageAssetProjection,
    ImageDeletionProjection,
    ImageError,
    ImageTaskKind,
    ImageTaskProjection,
    ImageTaskStatus,
    ImageVersionProjection,
)
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.workflows import RunContextEnvelope
from bridges.observability.service import ObservabilityService
from bridges.runtime.queue import (
    Claim,
    RetryKind,
    TaskQueue,
    TaskRetryError,
    TaskWorker,
)
from bridges.storage.database import BridgesDatabase
from bridges.storage.errors import StorageError
from bridges.storage.repository import BridgesObjectRepository

#: 图片任务队列名（task_claims 调度表）。
_IMAGE_QUEUE = "image"
#: 每账户每轮最多领取的任务数（图片生成昂贵且需逐轮轮询云端）。
CLAIM_BATCH_SIZE = 1
#: 云端轮询租约：领取后在该时间内必须完成下一个步骤（提交/轮询），
#: 超过视为中断可重新领取。
POLL_LEASE_SECONDS = 300
#: 云端任务轮询次数上限（每次 tick 一次，约 3 小时）；超过判定超时，
#: 可重试后重新提交。
MAX_CLOUD_POLLS = 36
#: error 任务的自动重试上限：超过后不再自动领取，只等用户手动重试
#: （retry 重置计数）。
MAX_AUTO_RETRIES = 3
#: 提示词长度上限（与消息正文上限一致的保守值）。
PROMPT_MAX_LENGTH = 2000
#: 替代文本确定性降级模板的提示词摘要长度。
_ALT_TEXT_PROMPT_SUMMARY = 80
#: 永久失败错误码：重试不会因输入/来源变化而成功（如来源已删除），
#: 投影与前端据此隐藏重试入口。
_PERMANENT_FAILURE_CODES = {"asset_deleted"}

#: 稳定错误码 → 可操作中文提示（GQ-04：与主对话/语音同源，指向服务
#: 运行配置或稍后重试；只收录图片链路实际可能产生的供应商错误分类，
#: 未命中的错误码回退网关原始消息，与 chat/turn.py 的 user_facing_error
#: 语义一致，不伪造分类）。
_USER_FACING_ERRORS: dict[str, str] = {
    "rate_limit": "请求过于频繁（已触发限流），请稍后重试。",
    "transient": "连接中断或服务暂时不可用，请检查网络后重试。",
    "region_error": "无法连接 Qwen 服务，请检查网络后重试。",
    "auth_error": "Qwen API Key 无效或已失效，请检查启动服务的全局百炼配置与权限。",
    "capability_not_verified": "图片能力未通过验证，请检查启动服务的全局百炼配置与权限。",
}


def _user_facing_error(
    error_code: str | None, fallback_message: str | None, default_message: str
) -> str:
    """把网关错误码折叠为面向用户的中文说明。

    ``fallback_message`` 是网关返回的原始消息（可能为供应商英文原文），
    只在映射未命中时保留，保证分类错误仍给出可读中文。
    """
    mapped = _USER_FACING_ERRORS.get(error_code or "")
    if mapped is not None:
        return mapped
    return fallback_message or default_message

#: 云端任务取消端点（尽力而为；本地取消是权威，迟到结果由条件更新隔离）。
_CLOUD_CANCEL_PATH = "/api/v1/tasks/{task_id}?action=cancel"


class _CancelledRaceError(Exception):
    """任务在 worker 处理期间被取消；用于回滚本次未发布的结果。"""


class _AssetDeletedError(Exception):
    """编辑来源资产在生成期间被删除；结果不得发布为新版本。"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def display_image_task_status(
    raw_status: str | None, lease_expires_at: str | None = None
) -> ImageTaskStatus:
    """把数据库原始状态映射为对外呈现状态。

    ``running`` 的领取租约过期且任务未终态时呈现 ``recovery``（上次
    处理中断，后台恢复中）；取消/成功/失败原样呈现。
    """
    if raw_status is None:
        return ImageTaskStatus.QUEUED
    if raw_status == "running":
        if lease_expires_at is not None:
            try:
                expired = datetime.fromisoformat(lease_expires_at) < datetime.now(UTC)
            except ValueError:
                expired = False
            if expired:
                return ImageTaskStatus.RECOVERY
        return ImageTaskStatus.RUNNING
    if raw_status == "queued":
        return ImageTaskStatus.QUEUED
    if raw_status == "succeeded":
        return ImageTaskStatus.SUCCEEDED
    if raw_status == "failed":
        return ImageTaskStatus.FAILED
    if raw_status == "cancelled":
        return ImageTaskStatus.CANCELLED
    return ImageTaskStatus.QUEUED


class ImageService:
    """图片任务的写模型、资产面与后台处理轮；全部操作限定在账户内。"""

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
        self._task_queue.set_lease_seconds(_IMAGE_QUEUE, POLL_LEASE_SECONDS)
        self._worker = TaskWorker(
            self._task_queue,
            _IMAGE_QUEUE,
            "image-worker",
            self._handle_image_claim,
            default_max_attempts=MAX_AUTO_RETRIES,
        )

    # ------------------------------------------------------------------
    # 提交（API 进程；任务与消息投影同一事务落库）
    # ------------------------------------------------------------------

    def submit_generation(
        self, account_id: str, conversation_id: str, message_id: str, prompt: str
    ) -> ImageTaskProjection:
        """提交一个图片生成任务并关联到当前助手消息。"""
        return self._submit(
            account_id,
            conversation_id,
            message_id,
            ImageTaskKind.GENERATE,
            prompt,
        )

    def submit_edit(
        self,
        account_id: str,
        conversation_id: str,
        message_id: str,
        prompt: str,
        source_version_id: str | None = None,
        source_object_id: str | None = None,
    ) -> ImageTaskProjection:
        """提交一个图片编辑任务；来源必须是当前账户有权访问的图片。

        新请求的 ``source_object_id`` 指向本账户全局知识库中已就绪的图片
        材料；``source_version_id`` 仅供历史任务兼容。两者至多提供一个；
        跨账户来源一律 404，不泄漏存在性。
        """
        return self._submit(
            account_id,
            conversation_id,
            message_id,
            ImageTaskKind.EDIT,
            prompt,
            source_version_id=source_version_id,
            source_object_id=source_object_id,
        )

    def _submit(
        self,
        account_id: str,
        conversation_id: str,
        message_id: str,
        kind: ImageTaskKind,
        prompt: str,
        source_version_id: str | None = None,
        source_object_id: str | None = None,
    ) -> ImageTaskProjection:
        prompt = prompt.strip()
        if not prompt:
            raise ImageError("missing_prompt", "图片请求必须包含提示词。")
        if len(prompt) > PROMPT_MAX_LENGTH:
            raise ImageError(
                "prompt_too_long", f"提示词超过 {PROMPT_MAX_LENGTH} 字限制。"
            )
        # 消息归属校验：任务与消息投影原子落库，跨账户消息一律 404。
        if self._repo is not None:
            record = self._repo.get_message(account_id, message_id)
            if record is None or record.conversation_id != conversation_id:
                raise ImageError(
                    "message_not_found",
                    "消息不存在或没有访问权限。",
                    status_code=404,
                )
        asset_id: str | None = None
        if kind == ImageTaskKind.EDIT:
            if bool(source_version_id) == bool(source_object_id):
                raise ImageError(
                    "invalid_source",
                    "编辑必须且只能选择一个来源图片。",
                )
            if source_version_id:
                version = self._version_row(account_id, source_version_id)
                if version is None:
                    raise ImageError(
                        "source_not_found",
                        "来源图片不存在或没有访问权限。",
                        status_code=404,
                    )
                asset_id = str(version["asset_id"])
            else:
                assert source_object_id is not None
                obj = self._object_meta(account_id, source_object_id)
                if (
                    obj is None
                    or not obj.media_type.startswith("image/")
                    or not self._knowledge_base_material_ready(account_id, source_object_id)
                ):
                    raise ImageError(
                        "source_not_found",
                        "来源图片不存在或没有访问权限。",
                        status_code=404,
                    )

        task_id = secrets.token_urlsafe(16)
        now = _now()
        created = datetime.now(UTC)
        try:
            with self._db.transaction():
                scoped = self._db.scoped(account_id)
                scoped.execute(
                    "INSERT INTO image_tasks(task_id, account_id, conversation_id,"
                    " message_id, kind, prompt, source_version_id, source_object_id,"
                    " status, asset_id, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
                    (
                        task_id,
                        account_id,
                        conversation_id,
                        message_id,
                        kind.value,
                        prompt,
                        source_version_id,
                        source_object_id,
                        asset_id,
                        now,
                        now,
                    ),
                )
                # 助手消息投影与任务同一事务落库：不存在"任务已建但消息
                # 看不到"的孤儿任务。
                projection = self._task_projection(
                    task_id=task_id,
                    kind=kind,
                    prompt=prompt,
                    source_version_id=source_version_id,
                    source_object_id=source_object_id,
                    status=ImageTaskStatus.QUEUED,
                    asset_id=asset_id,
                    created=created,
                    updated=created,
                )
                scoped.execute(
                    "UPDATE messages SET image = ?, updated_at = ?"
                    " WHERE message_id = ? AND account_id = ?",
                    (
                        projection.model_dump_json(),
                        now,
                        message_id,
                        account_id,
                    ),
                )
                # 领取型任务入队（同一事务）：后台执行器按统一队列契约
                # 领取处理，无需每轮全表扫描 queued 行。
                self._task_queue.enqueue(
                    _IMAGE_QUEUE, f"image:{account_id}:{task_id}"
                )
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一转换为领域错误
            raise ImageError("submit_failed", "图片任务提交失败，请重试。") from exc

        self._audit(
            account_id,
            task_id,
            AuditAction.IMAGE_TASK_SUBMIT,
            AuditResult.SUCCESS,
            "图片任务已提交。",
            {
                "kind": kind.value,
                "has_source": bool(source_version_id or source_object_id),
            },
        )
        return projection

    # ------------------------------------------------------------------
    # 任务查询 / 取消 / 重试（API 进程）
    # ------------------------------------------------------------------

    def get_task(
        self, account_id: str, conversation_id: str, task_id: str
    ) -> ImageTaskProjection:
        """返回任务投影；不存在或跨账户一律 404。"""
        row = self._task_row(account_id, task_id)
        if row is None or str(row["conversation_id"]) != conversation_id:
            raise ImageError(
                "task_not_found", "任务不存在或没有访问权限。", status_code=404
            )
        return self._task_projection_from_row(row)

    def cancel(
        self, account_id: str, conversation_id: str, task_id: str
    ) -> ImageTaskProjection:
        """取消任务：本地标记为权威，尽力通知云端。

        取消后 worker 的条件更新拒绝迟到结果：即使云端已生成，本地
        不建资产、不更新消息，已建对象立即回收。
        """
        row = self._task_row(account_id, task_id)
        if row is None or str(row["conversation_id"]) != conversation_id:
            raise ImageError(
                "task_not_found", "任务不存在或没有访问权限。", status_code=404
            )
        now = datetime.now(UTC)
        now_text = _now()
        try:
            with self._db.transaction():
                scoped = self._db.scoped(account_id)
                cursor = scoped.execute(
                    "UPDATE image_tasks SET status = 'cancelled', cancelled_at = ?,"
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
                        AuditAction.IMAGE_TASK_CANCEL,
                        AuditResult.BLOCKED,
                        "任务已终态，取消未生效（幂等返回当前投影）。",
                        {"kind": str(row["kind"])},
                    )
                    return self.get_task(account_id, conversation_id, task_id)
                else:
                    cancelled = self._task_projection(
                        task_id=task_id,
                        kind=ImageTaskKind(str(row["kind"])),
                        prompt=str(row["prompt"]),
                        source_version_id=(
                            str(row["source_version_id"])
                            if row["source_version_id"]
                            else None
                        ),
                        source_object_id=(
                            str(row["source_object_id"])
                            if row["source_object_id"]
                            else None
                        ),
                        status=ImageTaskStatus.CANCELLED,
                        asset_id=(
                            str(row["asset_id"]) if row["asset_id"] else None
                        ),
                        created=datetime.fromisoformat(str(row["created_at"])),
                        updated=now,
                    )
                    if row["message_id"]:
                        scoped.execute(
                            "UPDATE messages SET image = ?, updated_at = ?"
                            " WHERE message_id = ? AND account_id = ?",
                            (
                                cancelled.model_dump_json(),
                                now_text,
                                str(row["message_id"]),
                                account_id,
                            ),
                        )
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ImageError("cancel_failed", "任务取消失败，请重试。") from exc

        cloud_task_id = str(row["cloud_task_id"]) if row["cloud_task_id"] else None
        if cloud_task_id:
            # 尽力通知云端取消；失败静默——本地取消是权威。
            with contextlib.suppress(Exception):
                self._gateway.invoke(
                    "qwen_image",
                    "1",
                    self._run_context(account_id, conversation_id, "image_cancel"),
                    {"kind": "cancel", "cloud_task_id": cloud_task_id},
                )
        self._audit(
            account_id,
            task_id,
            AuditAction.IMAGE_TASK_CANCEL,
            AuditResult.SUCCESS,
            "图片任务已取消。",
            {"kind": str(row["kind"])},
        )
        return self.get_task(account_id, conversation_id, task_id)

    def retry(
        self, account_id: str, conversation_id: str, task_id: str
    ) -> ImageTaskProjection:
        """重试失败任务：同输入（提示/来源不变）重新入队，固定同一快照。

        用户手动重试重置自动重试计数与云端引用，等待后台执行器重新
        提交；成功/取消/运行中任务不可重试。
        """
        row = self._task_row(account_id, task_id)
        if row is None or str(row["conversation_id"]) != conversation_id:
            raise ImageError(
                "task_not_found", "任务不存在或没有访问权限。", status_code=404
            )
        if str(row["status"]) != "failed":
            raise ImageError(
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
                    "UPDATE image_tasks SET status = 'queued', retry_count = 0,"
                    " cloud_task_id = NULL, poll_count = 0, error_code = NULL,"
                    " error_message = NULL, updated_at = ?"
                    " WHERE task_id = ? AND account_id = ? AND status = 'failed'",
                    (now_text, task_id, account_id),
                )
                if row["message_id"]:
                    retried = self._task_projection(
                        task_id=task_id,
                        kind=ImageTaskKind(str(row["kind"])),
                        prompt=str(row["prompt"]),
                        source_version_id=(
                            str(row["source_version_id"])
                            if row["source_version_id"]
                            else None
                        ),
                        source_object_id=(
                            str(row["source_object_id"])
                            if row["source_object_id"]
                            else None
                        ),
                        status=ImageTaskStatus.QUEUED,
                        asset_id=(
                            str(row["asset_id"]) if row["asset_id"] else None
                        ),
                        created=datetime.fromisoformat(str(row["created_at"])),
                        updated=now,
                    )
                    scoped.execute(
                        "UPDATE messages SET image = ?, updated_at = ?"
                        " WHERE message_id = ? AND account_id = ?",
                        (retried.model_dump_json(), now_text, str(row["message_id"]), account_id),
                    )
                # 手动重试重新入队（同一事务）：重置退避计数，恢复自动领取。
                self._task_queue.enqueue(
                    _IMAGE_QUEUE, f"image:{account_id}:{task_id}"
                )
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ImageError("retry_failed", "任务重试失败，请重试。") from exc
        return self.get_task(account_id, conversation_id, task_id)

    # ------------------------------------------------------------------
    # 资产面（版本链 / 替代文本 / 字节 / 删除）
    # ------------------------------------------------------------------

    def get_asset(
        self, account_id: str, conversation_id: str, asset_id: str
    ) -> ImageAssetProjection:
        """返回资产投影（版本列表与替代文本）；删除或跨账户一律 404。"""
        row = self._asset_row(account_id, asset_id)
        if (
            row is None
            or str(row["conversation_id"]) != conversation_id
            or int(row["deleted"]) == 1
        ):
            raise ImageError(
                "asset_not_found", "图片不存在或没有访问权限。", status_code=404
            )
        versions = self._versions(account_id, asset_id)
        return ImageAssetProjection(
            asset_id=asset_id,
            alt_text=str(row["alt_text"]),
            alt_text_source=ImageAltTextSource(str(row["alt_text_source"])),
            current_version_id=(
                str(row["current_version_id"]) if row["current_version_id"] else None
            ),
            version_count=len(versions),
            versions=versions,
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    def get_version_image_bytes(
        self,
        account_id: str,
        conversation_id: str,
        asset_id: str,
        version_id: str,
    ) -> tuple[bytes, str, int]:
        """返回指定版本图片的字节与媒体类型；下载内容与所选版本一致。

        跨账户、已删除资产或版本不存在一律 404；响应经 API 层附加
        私有缓存头，杜绝缓存跨账户复用。
        """
        asset = self._asset_row(account_id, asset_id)
        if (
            asset is None
            or str(asset["conversation_id"]) != conversation_id
            or int(asset["deleted"]) == 1
        ):
            raise ImageError(
                "asset_not_found", "图片不存在或没有访问权限。", status_code=404
            )
        version = self._version_row(account_id, version_id)
        if version is None or str(version["asset_id"]) != asset_id:
            raise ImageError(
                "version_not_found", "版本不存在或没有访问权限。", status_code=404
            )
        try:
            content = self._objects.get_content(account_id, str(version["object_id"]))
        except StorageError as exc:
            raise ImageError(
                "image_unavailable",
                "图片内容当前不可用，请稍后重试。",
                status_code=503,
                retryable=True,
            ) from exc
        return (
            content,
            str(version["media_type"]),
            int(version["content_length"]),
        )

    def update_alt_text(
        self,
        account_id: str,
        conversation_id: str,
        asset_id: str,
        alt_text: str,
    ) -> ImageAssetProjection:
        """修改资产替代文本（来源标记为 manual）；删除或跨账户 404。"""
        alt_text = alt_text.strip()
        if not alt_text:
            raise ImageError(
                "invalid_alt_text", "替代文本不能为空。", status_code=422
            )
        row = self._asset_row(account_id, asset_id)
        if row is None or str(row["conversation_id"]) != conversation_id:
            raise ImageError(
                "asset_not_found", "图片不存在或没有访问权限。", status_code=404
            )
        now = _now()
        with self._db.transaction():
            cursor = self._db.scoped(account_id).execute(
                "UPDATE image_assets SET alt_text = ?, alt_text_source = 'manual',"
                " updated_at = ? WHERE asset_id = ? AND account_id = ?"
                " AND deleted = 0",
                (alt_text, now, asset_id, account_id),
            )
            if cursor.rowcount == 0:
                raise ImageError(
                    "asset_not_found", "图片不存在或没有访问权限。", status_code=404
                )
        self._audit(
            account_id,
            asset_id,
            AuditAction.IMAGE_ALT_TEXT_UPDATE,
            AuditResult.SUCCESS,
            "图片替代文本已更新。",
            {"source": ImageAltTextSource.MANUAL.value},
        )
        return self.get_asset(account_id, conversation_id, asset_id)

    def delete_asset(
        self, account_id: str, conversation_id: str, asset_id: str
    ) -> ImageDeletionProjection:
        """删除资产：消息引用、资产元数据与本地对象保持一致。

        删除不物理清除版本记录（保留审计追溯），而是：标记资产删除 →
        更新引用该资产的消息投影（deleted 标记）→ 版本对象逐个标记
        待清理并立即尝试物理清理（失败保留 pending_cleanup 由清理轮
        重试，可观察可恢复）。幂等：已删除资产返回零计数投影。
        """
        row = self._asset_row(account_id, asset_id)
        if row is None or str(row["conversation_id"]) != conversation_id:
            raise ImageError(
                "asset_not_found", "图片不存在或没有访问权限。", status_code=404
            )
        if int(row["deleted"]) == 1:
            return ImageDeletionProjection(
                asset_id=asset_id,
                removed_versions=0,
                updated_messages=0,
                object_status="cleaned",
                deleted_at=datetime.now(UTC),
            )
        versions = self._versions(account_id, asset_id)
        now = datetime.now(UTC)
        now_text = _now()
        updated_messages = 0
        try:
            with self._db.transaction():
                scoped = self._db.scoped(account_id)
                scoped.execute(
                    "UPDATE image_assets SET deleted = 1, current_version_id = NULL,"
                    " updated_at = ? WHERE asset_id = ? AND account_id = ?",
                    (now_text, asset_id, account_id),
                )
                # 消息引用维护：引用该资产的助手消息投影加 deleted 标记。
                rows = scoped.execute(
                    "SELECT message_id, image FROM messages"
                    " WHERE account_id = ? AND image IS NOT NULL",
                    (account_id,),
                ).fetchall()
                for msg in rows:
                    projection = _load_task_projection(msg["image"])
                    if projection is None or projection.asset_id != asset_id:
                        continue
                    updated = projection.model_copy(update={"deleted": True})
                    scoped.execute(
                        "UPDATE messages SET image = ?, updated_at = ?"
                        " WHERE message_id = ? AND account_id = ?",
                        (updated.model_dump_json(), now_text, str(msg["message_id"]), account_id),
                    )
                    updated_messages += 1
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ImageError("delete_failed", "图片删除失败，请重试。") from exc

        object_status = "cleaned"
        for version in versions:
            with contextlib.suppress(Exception):
                self._objects.delete_object(account_id, version.object_id)
        remaining = [
            v
            for v in versions
            if self._object_status(account_id, v.object_id) == "pending_cleanup"
        ]
        if remaining:
            object_status = "pending_cleanup"
        self._audit(
            account_id,
            asset_id,
            AuditAction.IMAGE_ASSET_DELETE,
            AuditResult.SUCCESS,
            "图片资产已删除。",
            {
                "version_count": len(versions),
                "updated_messages": updated_messages,
                "object_status": object_status,
            },
        )
        return ImageDeletionProjection(
            asset_id=asset_id,
            removed_versions=len(versions),
            updated_messages=updated_messages,
            object_status=object_status,
            deleted_at=now,
        )

    # ------------------------------------------------------------------
    # 后台执行器：领取 / 处理
    # ------------------------------------------------------------------

    def process_pending(self) -> str:
        """执行一轮图片任务处理：队列领取 → 提交或轮询 → 收敛终态。

        Issue 43：领取/租约/退避/崩溃恢复由统一领取型任务队列承担；
        每任务创建/手动重试时入队，worker 按租约领取并逐件处理一步。
        """
        handled, failed = self._worker.drain(max_steps=CLAIM_BATCH_SIZE)
        if handled == 0 and failed == 0:
            return "worker: 图片任务：无待处理任务。"
        return f"worker: 图片任务：本轮处理 {handled} 个任务步骤，失败 {failed} 次。"

    def _handle_image_claim(self, claim: Claim) -> None:
        """队列 handler：处理一个任务的一个步骤（提交或轮询）。

        轮询续轮（running）不消耗重试预算（count_attempt=False）；
        failed 按自动重试上限退避重试；succeeded/cancelled 收敛。
        """
        _, account_id, task_id = claim.task_key.split(":", 2)
        row = self._task_row(account_id, task_id)
        if row is None or str(row["status"]) in ("succeeded", "cancelled"):
            return  # 已收敛：跳过
        # 业务行呈现「运行中」（running + 租约）：租约过期时前端呈现
        # recovery；领取调度本身由队列承担。
        with self._db.transaction():
            self._db.scoped(account_id).execute(
                "UPDATE image_tasks SET status = 'running', claimed_at = ?,"
                " lease_expires_at = ?, updated_at = ?"
                " WHERE task_id = ? AND account_id = ? AND status != 'cancelled'",
                (_now(), self._lease_expires(), _now(), task_id, account_id),
            )
        try:
            self._process_task(account_id, task_id)
        except _CancelledRaceError:
            return
        row = self._task_row(account_id, task_id)
        status = str(row["status"]) if row is not None else "succeeded"
        if status in ("succeeded", "cancelled"):
            return  # 完成/取消：收敛
        if status == "running":
            # 提交成功或云端任务进行中：下一轮继续轮询（不消耗预算）。
            raise TaskRetryError(
                "云端任务轮询中",
                retry_kind=RetryKind.FIXED,
                count_attempt=False,
            )
        # failed：每轮立即自动重试至上限（与旧 claim 条件语义一致；
        # 轮次边界由 drain 的 +1 秒释放控制，不消耗额外退避等待）。
        raise TaskRetryError(
            str(row["error_message"]) if row is not None else "任务失败",
            retry_kind=RetryKind.FIXED,
            max_attempts=MAX_AUTO_RETRIES,
        )

    def _process_task(self, account_id: str, task_id: str) -> bool:
        row = self._task_row(account_id, task_id)
        if row is None or str(row["status"]) == "cancelled":
            # 取消任务不再处理（迟到结果隔离的第一道闸）。
            return False
        if not row["cloud_task_id"]:
            return self._submit_step(account_id, task_id, row)
        return self._poll_step(account_id, task_id, row)

    # ------------------------------------------------------------------
    # 处理步骤：提交 / 轮询 / 收敛
    # ------------------------------------------------------------------

    def _submit_step(self, account_id: str, task_id: str, row: Any) -> bool:
        prompt = str(row["prompt"])
        base_image = self._source_image_data_url(account_id, row)
        result = self._invoke_image(
            account_id,
            str(row["conversation_id"]),
            {
                "kind": "submit",
                "prompt": prompt,
                "size": DEFAULT_IMAGE_SIZE,
                "base_image": base_image,
            },
        )
        if result.status != ModelCallStatus.SUCCESS or result.output is None:
            error_code = result.error_code or "submit_failed"
            error_message = _user_facing_error(
                error_code, result.error_message, "图片任务提交失败，请重试。"
            )
            self._fail_task(
                account_id,
                task_id,
                error_code,
                error_message,
                retryable=result.status == ModelCallStatus.RETRYABLE_FAIL,
            )
            return True
        cloud_task_id = str(result.output.get("cloud_task_id") or "")
        result_url = str(result.output.get("result_url") or "")
        now = _now()
        if cloud_task_id:
            # 提交成功：记录云端任务标识，租约续期等待下一轮轮询。
            with self._db.transaction():
                cursor = self._db.scoped(account_id).execute(
                    "UPDATE image_tasks SET cloud_task_id = ?, status = 'running',"
                    " lease_expires_at = ?, model_id = ?, error_code = NULL,"
                    " error_message = NULL, updated_at = ?"
                    " WHERE task_id = ? AND account_id = ? AND status != 'cancelled'",
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
                    # 提交与取消竞态：任务已取消，云端任务不再被本地轮询，
                    # 结果自然隔离。
                    return True
            return True
        if result_url:
            # 供应商同步返回结果（少见）：直接走下载收敛路径。
            return self._finalize_step(account_id, task_id, row, result_url)
        self._fail_task(
            account_id,
            task_id,
            "empty_response",
            "图片生成接口没有返回任务。",
            retryable=True,
        )
        return True

    def _poll_step(self, account_id: str, task_id: str, row: Any) -> bool:
        cloud_task_id = str(row["cloud_task_id"])
        result = self._invoke_image(
            account_id,
            str(row["conversation_id"]),
            {"kind": "poll", "cloud_task_id": cloud_task_id},
        )
        if result.status != ModelCallStatus.SUCCESS or result.output is None:
            error_code = result.error_code or "poll_failed"
            error_message = _user_facing_error(
                error_code, result.error_message, "图片任务查询失败，请重试。"
            )
            self._fail_task(
                account_id,
                task_id,
                error_code,
                error_message,
                retryable=result.status == ModelCallStatus.RETRYABLE_FAIL,
            )
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
                    retryable=True,
                )
                return True
            with self._db.transaction():
                self._db.scoped(account_id).execute(
                    "UPDATE image_tasks SET status = 'running', lease_expires_at = ?,"
                    " poll_count = ?, model_id = ?, updated_at = ?"
                    " WHERE task_id = ? AND account_id = ? AND status != 'cancelled'",
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
                    "图片任务完成但没有返回图片。",
                    retryable=False,
                )
                return True
            return self._finalize_step(account_id, task_id, row, result_url)
        # 云端失败：同输入重新提交即可（worker 自动重领）。
        self._fail_task(
            account_id,
            task_id,
            "cloud_failed",
            str(result.output.get("error_message") or "云端图片生成失败。"),
            retryable=True,
        )
        return True

    def _finalize_step(
        self, account_id: str, task_id: str, row: Any, result_url: str
    ) -> bool:
        """下载结果字节 → 转存对象库 → 建版本/资产 → 条件发布。

        顺序保证取消隔离：先建对象（事务外），再在单个事务内条件更新
        任务（``WHERE status != 'cancelled'``）并建立资产/版本/消息
        引用；条件更新失败（已被取消）时回滚全部数据库写入并回收
        刚建的对象——迟到结果绝不发布为成功资产。
        """
        result = self._invoke_image(
            account_id,
            str(row["conversation_id"]),
            {"kind": "fetch", "result_url": result_url},
        )
        if result.status != ModelCallStatus.SUCCESS or result.output is None:
            error_code = result.error_code or "download_failed"
            error_message = _user_facing_error(
                error_code, result.error_message, "图片结果下载失败，请重试。"
            )
            self._fail_task(
                account_id,
                task_id,
                error_code,
                error_message,
                retryable=result.status == ModelCallStatus.RETRYABLE_FAIL,
            )
            return True
        image_bytes = result.output.get("image_bytes")
        if not isinstance(image_bytes, bytes) or not image_bytes:
            self._fail_task(
                account_id,
                task_id,
                "empty_image",
                "图片结果为空。",
                retryable=True,
            )
            return True
        media_type = str(result.output.get("media_type") or "image/png")
        stored = self._objects.create_object(
            account_id,
            original_filename=f"image-{task_id}.png",
            content=image_bytes,
            media_type=media_type,
        )

        kind = ImageTaskKind(str(row["kind"]))
        prompt = str(row["prompt"])
        asset_id = str(row["asset_id"]) if row["asset_id"] else secrets.token_urlsafe(16)
        version_id = secrets.token_urlsafe(16)
        now = datetime.now(UTC)
        now_text = _now()
        parent_version_id = (
            str(row["source_version_id"]) if row["source_version_id"] else None
        )
        model_id = result.lock.actual_model_id if result.lock else None
        alt_text, alt_text_source = self._generate_alt_text(
            account_id,
            str(row["conversation_id"]),
            image_bytes,
            media_type,
            prompt,
        )
        message_id = str(row["message_id"]) if row["message_id"] else None
        try:
            with self._db.transaction():
                scoped = self._db.scoped(account_id)
                cursor = scoped.execute(
                    "UPDATE image_tasks SET status = 'succeeded',"
                    " result_version_id = ?, asset_id = ?, model_id = ?,"
                    " error_code = NULL, error_message = NULL, updated_at = ?"
                    " WHERE task_id = ? AND account_id = ? AND status != 'cancelled'",
                    (version_id, asset_id, model_id, now_text, task_id, account_id),
                )
                if cursor.rowcount == 0:
                    raise _CancelledRaceError()
                if kind == ImageTaskKind.EDIT:
                    scoped.execute(
                        "INSERT INTO image_versions(version_id, asset_id, account_id,"
                        " parent_version_id, kind, prompt, model_id, object_id,"
                        " media_type, content_length, created_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            version_id,
                            asset_id,
                            account_id,
                            parent_version_id,
                            kind.value,
                            prompt,
                            model_id,
                            stored.object_id,
                            media_type,
                            stored.content_length,
                            now_text,
                        ),
                    )
                    cursor = scoped.execute(
                        "UPDATE image_assets SET current_version_id = ?,"
                        " alt_text = ?, alt_text_source = ?, updated_at = ?"
                        " WHERE asset_id = ? AND account_id = ? AND deleted = 0",
                        (
                            version_id,
                            alt_text,
                            alt_text_source.value,
                            now_text,
                            asset_id,
                            account_id,
                        ),
                    )
                    if cursor.rowcount == 0:
                        # 编辑来源资产在生成期间被删除：结果不得发布为新
                        # 版本（回滚事务，外层回收刚建的对象），任务按
                        # 不可重试失败收敛。
                        raise _AssetDeletedError()
                else:
                    scoped.execute(
                        "INSERT INTO image_assets(asset_id, account_id,"
                        " conversation_id, alt_text, alt_text_source,"
                        " current_version_id, created_at, updated_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            asset_id,
                            account_id,
                            str(row["conversation_id"]),
                            alt_text,
                            alt_text_source.value,
                            version_id,
                            now_text,
                            now_text,
                        ),
                    )
                    scoped.execute(
                        "INSERT INTO image_versions(version_id, asset_id, account_id,"
                        " parent_version_id, kind, prompt, model_id, object_id,"
                        " media_type, content_length, created_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            version_id,
                            asset_id,
                            account_id,
                            parent_version_id,
                            kind.value,
                            prompt,
                            model_id,
                            stored.object_id,
                            media_type,
                            stored.content_length,
                            now_text,
                        ),
                    )
                if message_id:
                    succeeded = self._task_projection(
                        task_id=task_id,
                        kind=kind,
                        prompt=prompt,
                        source_version_id=(
                            str(row["source_version_id"])
                            if row["source_version_id"]
                            else None
                        ),
                        source_object_id=(
                            str(row["source_object_id"])
                            if row["source_object_id"]
                            else None
                        ),
                        status=ImageTaskStatus.SUCCEEDED,
                        model_id=model_id,
                        asset_id=asset_id,
                        result_version_id=version_id,
                        created=datetime.fromisoformat(str(row["created_at"])),
                        updated=now,
                    )
                    scoped.execute(
                        "UPDATE messages SET image = ?, content = ?, updated_at = ?"
                        " WHERE message_id = ? AND account_id = ?",
                        (
                            succeeded.model_dump_json(),
                            self._completed_message_text(kind),
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
        except _AssetDeletedError:
            # 编辑来源资产已删除：回收刚建的对象，任务按不可重试失败收敛。
            with contextlib.suppress(Exception):
                self._objects.delete_object(account_id, stored.object_id)
            self._fail_task(
                account_id,
                task_id,
                "asset_deleted",
                "来源图片已被删除，请重新选择后再编辑。",
                retryable=False,
            )
            return True
        except (StorageError, sqlite3.Error):
            with contextlib.suppress(Exception):
                self._objects.delete_object(account_id, stored.object_id)
            self._fail_task(
                account_id,
                task_id,
                "storage_error",
                "图片资产保存失败，请重试。",
                retryable=True,
            )
            return True

        self._audit(
            account_id,
            task_id,
            AuditAction.IMAGE_TASK_COMPLETE,
            AuditResult.SUCCESS,
            "图片任务完成。",
            {
                "kind": kind.value,
                "content_length": stored.content_length,
                "model_id": model_id,
                "alt_text_source": alt_text_source.value,
                "version_parent": parent_version_id is not None,
            },
        )
        return True

    def _fail_task(
        self,
        account_id: str,
        task_id: str,
        error_code: str,
        error_message: str,
        *,
        retryable: bool,
    ) -> None:
        """把任务置为失败并同步消息投影（未终态才生效）。"""
        row = self._task_row(account_id, task_id)
        if row is None:
            return
        now = datetime.now(UTC)
        now_text = _now()
        try:
            with self._db.transaction():
                scoped = self._db.scoped(account_id)
                cursor = scoped.execute(
                    "UPDATE image_tasks SET status = 'failed', error_code = ?,"
                    " error_message = ?, cloud_task_id = NULL, poll_count = 0,"
                    " updated_at = ? WHERE task_id = ?"
                    " AND account_id = ? AND status NOT IN ('succeeded',"
                    " 'cancelled', 'failed')",
                    (error_code, error_message, now_text, task_id, account_id),
                )
                if cursor.rowcount == 0:
                    return
                if row["message_id"]:
                    failed = self._task_projection(
                        task_id=task_id,
                        kind=ImageTaskKind(str(row["kind"])),
                        prompt=str(row["prompt"]),
                        source_version_id=(
                            str(row["source_version_id"])
                            if row["source_version_id"]
                            else None
                        ),
                        source_object_id=(
                            str(row["source_object_id"])
                            if row["source_object_id"]
                            else None
                        ),
                        status=ImageTaskStatus.FAILED,
                        asset_id=(
                            str(row["asset_id"]) if row["asset_id"] else None
                        ),
                        error_code=error_code,
                        error_message=error_message,
                        retryable=retryable,
                        created=datetime.fromisoformat(str(row["created_at"])),
                        updated=now,
                    )
                    scoped.execute(
                        "UPDATE messages SET image = ?, updated_at = ?"
                        " WHERE message_id = ? AND account_id = ?",
                        (failed.model_dump_json(), now_text, str(row["message_id"]), account_id),
                    )
        except (StorageError, sqlite3.Error):
            return  # 失败落库不阻断处理循环，下轮重领时再试

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _source_image_data_url(self, account_id: str, row: Any) -> str | None:
        """读取编辑来源图片字节并编码为请求体内联 data URL。

        只读取编辑所需的那一张图片；来源归属在提交时已校验，这里
        再次经对象/版本授权读取，任何路径都不携带完整目录或画像。
        """
        if str(row["kind"]) != ImageTaskKind.EDIT.value:
            return None
        object_id: str | None = None
        if row["source_object_id"]:
            object_id = str(row["source_object_id"])
        elif row["source_version_id"]:
            version = self._version_row(account_id, str(row["source_version_id"]))
            if version is None:
                raise ImageError(
                    "source_not_found",
                    "编辑来源图片已不可用。",
                    status_code=404,
                )
            object_id = str(version["object_id"])
        if object_id is None:
            return None
        try:
            content = self._objects.get_content(account_id, object_id)
        except StorageError as exc:
            raise ImageError(
                "source_unavailable",
                "编辑来源图片读取失败，请重试。",
                status_code=503,
                retryable=True,
            ) from exc
        meta = self._object_meta(account_id, object_id)
        media_type = meta.media_type if meta is not None else "image/png"
        return image_data_url(content, media_type)

    def _generate_alt_text(
        self,
        account_id: str,
        conversation_id: str,
        image_bytes: bytes,
        media_type: str,
        prompt: str,
    ) -> tuple[str, ImageAltTextSource]:
        """生成替代文本：优先核心视觉模型，失败确定性降级。

        降级文本明确说明来源（提示词摘要），不冒充模型理解；用户之后
        可随时手动修改。
        """
        try:
            result = self._gateway.invoke(
                "qwen_vision",
                "1",
                self._run_context(account_id, conversation_id, "image_alt_text"),
                {
                    "image_base64": base64.b64encode(image_bytes).decode("ascii"),
                    "mime_type": media_type,
                    "prompt": (
                        "用一句中文描述这张图片的主要内容，作为无障碍替代"
                        "文本；只描述可见内容，不推测意图。"
                    ),
                    "max_tokens": 120,
                },
            )
            if (
                result.status == ModelCallStatus.SUCCESS
                and result.output is not None
                and isinstance(result.output.get("content"), str)
                and result.output["content"].strip()
            ):
                return result.output["content"].strip(), ImageAltTextSource.MODEL
        except Exception:  # noqa: BLE001 - 降级不阻断生成完成
            pass
        summary = prompt[:_ALT_TEXT_PROMPT_SUMMARY]
        if len(prompt) > _ALT_TEXT_PROMPT_SUMMARY:
            summary += "…"
        return f"由提示词「{summary}」生成的图片", ImageAltTextSource.FALLBACK

    def _invoke_image(
        self,
        account_id: str,
        conversation_id: str,
        payload: dict[str, Any],
    ) -> Any:
        result = self._gateway.invoke(
            "qwen_image",
            "1",
            self._run_context(account_id, conversation_id, "image_generation"),
            payload,
        )
        if result.lock is not None and self._repo is not None:
            with contextlib.suppress(Exception):
                self._repo.insert_run_lock(account_id, result.lock)
        return result

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
        kind: ImageTaskKind,
        prompt: str,
        status: ImageTaskStatus,
        source_version_id: str | None = None,
        source_object_id: str | None = None,
        model_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        retryable: bool = False,
        asset_id: str | None = None,
        result_version_id: str | None = None,
        created: datetime,
        updated: datetime,
    ) -> ImageTaskProjection:
        return ImageTaskProjection(
            task_id=task_id,
            kind=kind,
            prompt=prompt,
            source_version_id=source_version_id,
            source_object_id=source_object_id,
            model_id=model_id,
            status=status,
            error_code=error_code,
            error_message=error_message,
            retryable=retryable,
            asset_id=asset_id,
            result_version_id=result_version_id,
            created_at=created,
            updated_at=updated,
        )

    def _task_projection_from_row(self, row: Any) -> ImageTaskProjection:
        return self._task_projection(
            task_id=str(row["task_id"]),
            kind=ImageTaskKind(str(row["kind"])),
            prompt=str(row["prompt"]),
            source_version_id=(
                str(row["source_version_id"]) if row["source_version_id"] else None
            ),
            source_object_id=(
                str(row["source_object_id"]) if row["source_object_id"] else None
            ),
            model_id=str(row["model_id"]) if row["model_id"] else None,
            status=display_image_task_status(
                str(row["status"]),
                str(row["lease_expires_at"]) if row["lease_expires_at"] else None,
            ),
            error_code=str(row["error_code"]) if row["error_code"] else None,
            error_message=(
                str(row["error_message"]) if row["error_message"] else None
            ),
            retryable=(
                str(row["status"]) == "failed"
                and str(row["error_code"]) not in _PERMANENT_FAILURE_CODES
            ),
            asset_id=str(row["asset_id"]) if row["asset_id"] else None,
            result_version_id=(
                str(row["result_version_id"]) if row["result_version_id"] else None
            ),
            created=datetime.fromisoformat(str(row["created_at"])),
            updated=datetime.fromisoformat(str(row["updated_at"])),
        )

    def _task_row(self, account_id: str, task_id: str) -> sqlite3.Row | None:
        try:
            row = self._db.scoped(account_id).execute(
                "SELECT * FROM image_tasks WHERE task_id = ? AND account_id = ?",
                (task_id, account_id),
            ).fetchone()
        except StorageError:
            return None
        return cast(sqlite3.Row | None, row)

    def _asset_row(self, account_id: str, asset_id: str) -> sqlite3.Row | None:
        try:
            row = self._db.scoped(account_id).execute(
                "SELECT * FROM image_assets WHERE asset_id = ? AND account_id = ?",
                (asset_id, account_id),
            ).fetchone()
        except StorageError:
            return None
        return cast(sqlite3.Row | None, row)

    def _version_row(self, account_id: str, version_id: str) -> sqlite3.Row | None:
        try:
            row = self._db.scoped(account_id).execute(
                "SELECT * FROM image_versions WHERE version_id = ? AND account_id = ?",
                (version_id, account_id),
            ).fetchone()
        except StorageError:
            return None
        return cast(sqlite3.Row | None, row)

    def _versions(self, account_id: str, asset_id: str) -> list[ImageVersionProjection]:
        rows = self._db.scoped(account_id).execute(
            "SELECT * FROM image_versions WHERE asset_id = ? AND account_id = ?"
            " ORDER BY created_at, version_id",
            (asset_id, account_id),
        ).fetchall()
        return [
            ImageVersionProjection(
                version_id=str(row["version_id"]),
                asset_id=str(row["asset_id"]),
                parent_version_id=(
                    str(row["parent_version_id"])
                    if row["parent_version_id"]
                    else None
                ),
                kind=ImageTaskKind(str(row["kind"])),
                prompt=str(row["prompt"]),
                model_id=str(row["model_id"]) if row["model_id"] else None,
                object_id=str(row["object_id"]),
                media_type=str(row["media_type"]),
                content_length=int(row["content_length"]),
                created_at=datetime.fromisoformat(str(row["created_at"])),
            )
            for row in rows
        ]

    def _object_meta(self, account_id: str, object_id: str) -> Any | None:
        try:
            return self._objects.get_object(account_id, object_id)
        except StorageError:
            return None

    def _knowledge_base_material_ready(self, account_id: str, object_id: str) -> bool:
        """图片编辑来源必须是当前账户已完成摄取的知识库材料。"""

        row = self._db.scoped(account_id).execute(
            "SELECT 1 FROM document_records r JOIN objects o"
            " ON o.object_id = r.object_id AND o.account_id = r.account_id"
            " WHERE r.account_id = ? AND r.object_id = ?"
            "   AND r.source = 'knowledge_base' AND r.status = 'ready'"
            "   AND o.status = 'active'",
            (account_id, object_id),
        ).fetchone()
        return row is not None

    def _object_status(self, account_id: str, object_id: str) -> str:
        try:
            return self._objects.get_object(account_id, object_id).status
        except StorageError:
            return "gone"

    @staticmethod
    def _completed_message_text(kind: ImageTaskKind) -> str:
        if kind == ImageTaskKind.EDIT:
            return "图片编辑完成，已生成新版本。"
        return "图片生成完成。"

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


def _load_task_projection(raw: Any) -> ImageTaskProjection | None:
    """从消息 image 列解析任务投影；损坏快照按不可引用处理。"""
    if not raw:
        return None
    try:
        return ImageTaskProjection.model_validate_json(str(raw))
    except Exception:  # noqa: BLE001 - 损坏快照不阻断删除
        return None


__all__ = [
    "ImageService",
    "display_image_task_status",
]
