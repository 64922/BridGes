"""Issue 16 可选真实 smoke：真实视觉替代文本锁与真实供应商取消锁。

仅在显式开关 ``BRIDGES_MEDIA_EDGE_REAL_SMOKE=1`` 且已配置安装级全局
Qwen Key（``BRIDGES_QWEN_API_KEY`` / ``_FILE``）时运行；否则明确
skip/inconclusive，不伪造成功。禁用 Stub/fixture/cassette 与录制：

- 真实最小图片生成完成后断言视觉替代文本锁已落库（失败锁也计数）；
- 只对 smoke 自己刚创建且仍可安全取消的视频任务执行真实取消并断言
  取消锁；没有可取消任务时显式 skip；
- 代码不读取或输出 Key、提示词或媒体内容（凭据由生产组合根装配，
  本文件不触碰 Key 正文）。
"""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bridges.ai.composition import validate_production_composition
from bridges.ai.production import build_production_composition
from bridges.chat.repository import ConversationRepository, MessageRecord
from bridges.config import Settings
from bridges.contracts.chat import ChatMessageRole, ChatMessageStatus
from bridges.credentials.global_credential import is_global_qwen_key_configured
from bridges.image.service import ImageService
from bridges.storage.database import BridgesDatabase
from bridges.storage.object_store import EncryptedFileObjectStore
from bridges.storage.repository import BridgesObjectRepository
from bridges.video.service import VideoService

SMOKE_ENV = "BRIDGES_MEDIA_EDGE_REAL_SMOKE"


def _settings() -> Settings | None:
    try:
        return Settings()
    except Exception:  # noqa: BLE001 - 配置损坏按未配置处理
        return None


class _RealSmokeHarness:
    """真实组合上的最小测试台：内存库 + 生产网关（真实适配器）。"""

    def __init__(self, tmp_path: Path, gateway: Any) -> None:
        self.db = BridgesDatabase(":memory:")
        self.db.initialize()
        self.repo = ConversationRepository(self.db)
        self.object_repo = BridgesObjectRepository(
            self.db,
            EncryptedFileObjectStore(tmp_path, encryption_key="real-smoke-key"),
        )
        self.image_service = ImageService(
            database=self.db,
            gateway=gateway,
            object_repository=self.object_repo,
            chat_repository=self.repo,
        )
        self.video_service = VideoService(
            database=self.db,
            gateway=gateway,
            object_repository=self.object_repo,
            chat_repository=self.repo,
        )
        self.account_id = "account-smoke"
        self.conversation_id = "conv-smoke"
        self.object_repo.ensure_account(self.account_id, "smoke@example.com")
        self.repo.create_conversation(
            account_id=self.account_id,
            conversation_id=self.conversation_id,
            title="smoke",
            mode="companion",
            created_at=datetime.now(UTC),
        )
        self._message_seq = 0

    def seed_message(self) -> str:
        self._message_seq += 1
        message_id = f"msg-smoke-{self._message_seq}"
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
        return message_id

    def image_task_row(self, task_id: str) -> sqlite3.Row | None:
        return self.db.scoped(self.account_id).execute(
            "SELECT * FROM image_tasks WHERE task_id = ? AND account_id = ?",
            (task_id, self.account_id),
        ).fetchone()

    def video_task_row(self, task_id: str) -> sqlite3.Row | None:
        return self.db.scoped(self.account_id).execute(
            "SELECT * FROM video_tasks WHERE task_id = ? AND account_id = ?",
            (task_id, self.account_id),
        ).fetchone()

    def lock_rows(self, *, capability: str) -> list[sqlite3.Row]:
        return self.db.connection.execute(
            "SELECT * FROM model_run_locks WHERE capability_name = ?"
            " ORDER BY created_at, lock_id",
            (capability,),
        ).fetchall()

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
            " WHERE r.operation = ? ORDER BY l.created_at, l.lock_id",
            (operation,),
        ).fetchall()

    def drain_image(
        self, task_id: str, *, max_steps: int = 24, timeout_seconds: float = 300.0
    ) -> bool:
        """推进图片 worker 直到任务终态或超时；返回是否终态。"""
        deadline = time.monotonic() + timeout_seconds
        for _ in range(max_steps):
            row = self.image_task_row(task_id)
            if row is None or str(row["status"]) in (
                "succeeded",
                "failed",
                "cancelled",
            ):
                return row is not None and str(row["status"]) == "succeeded"
            self.image_service.process_pending()
            if time.monotonic() > deadline:
                return False
            time.sleep(2.0)
        return False


@pytest.mark.skipif(
    os.environ.get(SMOKE_ENV) != "1",
    reason="真实 smoke 需要显式 BRIDGES_MEDIA_EDGE_REAL_SMOKE=1。",
)
def test_real_alt_text_and_cancel_locks(tmp_path: Path) -> None:
    settings = _settings()
    if settings is None or not is_global_qwen_key_configured(settings):
        pytest.skip("未配置安装级全局 Qwen Key，结果 inconclusive（不伪造成功）。")
    composition = build_production_composition(settings)
    violations = validate_production_composition(
        composition.registry,
        composition.gateway,
        global_key_configured=composition.global_key_configured,
        cassette_enabled=composition.cassette_enabled,
        vision_ocr_compatibility_required=False,
    )
    if violations:
        pytest.skip(
            "生产组合门禁未通过（"
            + ",".join(sorted({v.code for v in violations}))
            + "），结果 inconclusive。"
        )
    h = _RealSmokeHarness(tmp_path, composition.gateway)

    # 1) 真实最小图片生成 → 替代文本锁（每次真实 qwen_vision 调用一条）。
    message_id = h.seed_message()
    task = h.image_service.submit_generation(
        h.account_id, h.conversation_id, message_id, "一朵简单的红色花"
    )
    succeeded = h.drain_image(task.task_id)
    row = h.image_task_row(task.task_id)
    if not succeeded or row is None or str(row["status"]) != "succeeded":
        pytest.skip(
            "真实图片生成未完成（status="
            + str(row["status"] if row is not None else "none")
            + "），结果 inconclusive。"
        )
    vision_locks = h.lock_rows(capability="qwen_vision")
    assert len(vision_locks) >= 1, "真实视觉调用必须产生运行锁"
    assert any(
        str(link["operation"]) == "image_alt_text"
        and str(link["object_id"]) == task.task_id
        for link in h.links(str(vision_locks[0]["lock_id"]))
    ), "替代文本锁必须关联图片任务"
    assert all(
        str(lock["account_id"]) == h.account_id for lock in vision_locks
    )

    # 2) 视频取消锁：只对 smoke 自己刚创建且仍可安全取消的任务执行。
    video_message = h.seed_message()
    video_task = h.video_service.submit(
        h.account_id, h.conversation_id, video_message, "一只猫安静地走过"
    )
    video_row = h.video_task_row(video_task.task_id)
    for _ in range(6):
        h.video_service.process_pending()
        video_row = h.video_task_row(video_task.task_id)
        if video_row is not None and video_row["cloud_task_id"]:
            break
    if video_row is None or not video_row["cloud_task_id"]:
        pytest.skip("视频任务没有可取消的云端任务，结果 inconclusive。")
    if str(video_row["status"]) in ("succeeded", "cancelled"):
        pytest.skip("视频任务已终态，不可安全取消，结果 inconclusive。")
    h.video_service.cancel(h.account_id, h.conversation_id, video_task.task_id)
    cancel_locks = h.locks_by_operation("video_cancel")
    assert len(cancel_locks) >= 1, "真实供应商取消请求必须产生运行锁"
    assert cancel_locks[0]["capability_name"] == "qwen_wan"
    assert cancel_locks[0]["account_id"] == h.account_id
