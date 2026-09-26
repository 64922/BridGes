"""Issue 37 数据生命周期测试夹具：多账户数据建造器与秘密金丝雀。

Harness 使用真实文件数据库（备份/恢复需要快照与原子替换语义），对象库
落在 tmp_path；两个账户共享同一数据目录，供导出/删除/备份的隔离断言。
GQ-07 后账户 Qwen Key 已整体清退，金丝雀只覆盖剩余账户级凭据类别
（QQ SMTP 授权码），测试断言导出与备份字节绝不包含它们。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import SecretStr

from bridges.contracts.identity import AccountRegistration
from bridges.credentials.store import InMemoryCredentialStore
from bridges.identity.service import IdentityService
from bridges.lifecycle.backup import BackupService
from bridges.lifecycle.deletion import DeletionService
from bridges.lifecycle.exports import ExportService
from bridges.persistence import SqliteStateStore
from bridges.storage.database import BridgesDatabase
from bridges.storage.object_store import EncryptedFileObjectStore
from bridges.storage.repository import BridgesObjectRepository

#: 秘密金丝雀：写入剩余账户级凭据类别后，导出/备份必须完全不包含这些正文。
SMTP_CANARY = "qqsmtp-canary-abcdef123456"
PASSWORD_CANARY = "canary-password-987654321"


class _RecordingObservability:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def log_audit(self, **kwargs: Any) -> None:
        self.events.append(kwargs)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Harness:
    """真实文件数据库 + 对象库 + 身份 + 凭据 + 三个生命周期服务的沙箱。"""

    def __init__(self, tmp_path: Any) -> None:
        self.root = tmp_path
        self.database = BridgesDatabase(tmp_path / "bridges.db")
        self.database.initialize()
        self.object_store = EncryptedFileObjectStore(
            tmp_path / "objects", encryption_key="test-key"
        )
        self.objects = BridgesObjectRepository(self.database, self.object_store)
        self.state = SqliteStateStore(
            tmp_path / "application_state.db", encryption_key="test-key"
        )
        self.identity = IdentityService(
            state_store=self.state, object_repository=self.objects
        )
        self.observability = _RecordingObservability()
        # GQ-07 后账户 Qwen 命名空间凭据已清退，生命周期服务只持有 SMTP。
        self.smtp_credentials = InMemoryCredentialStore(namespace="smtp")
        self.acc1 = self.register_account("alice", "10001@qq.com")
        self.acc2 = self.register_account("bob", "10002@qq.com")
        self.export = ExportService(
            self.database, self.identity, self.observability  # type: ignore
        )
        self.deletion = DeletionService(
            database=self.database,
            object_repository=self.objects,
            identity_service=self.identity,
            smtp_credential_store=self.smtp_credentials,
            observability_service=self.observability,  # type: ignore
        )
        self.backup = BackupService(
            database=self.database,
            object_repository=self.objects,
            object_store=self.object_store,
            identity_service=self.identity,
            smtp_credential_store=self.smtp_credentials,
            observability_service=self.observability,  # type: ignore
            state_store=self.state,
        )

    def register_account(self, username: str, qq_email: str) -> str:
        result = self.identity.register(
            AccountRegistration(
                username=username,
                qq_email=qq_email,
                password=SecretStr(PASSWORD_CANARY + username),
            )
        )
        return result.account.id

    # ------------------------------------------------------------------
    # 数据建造器
    # ------------------------------------------------------------------

    def create_conversation(self, account_id: str, title: str, message_count: int = 2) -> str:
        conversation_id = f"conv-{title}-{account_id[:4]}"
        self.database.scoped(account_id).execute(
            "INSERT INTO conversations(conversation_id, account_id, title, mode,"
            " pinned, created_at, updated_at) VALUES (?, ?, ?, 'companion', 0, ?, ?)",
            (conversation_id, account_id, title, _now(), _now()),
        )
        for index in range(1, message_count + 1):
            self.database.scoped(account_id).execute(
                "INSERT INTO messages(message_id, conversation_id, account_id, role,"
                " attempt_number, status, content, created_at, updated_at)"
                " VALUES (?, ?, ?, 'user', 1, 'done', ?, ?, ?)",
                (f"msg-{conversation_id}-{index}", conversation_id, account_id,
                 f"第 {index} 条消息，主题 {title}", _now(), _now()),
            )
        return conversation_id

    def create_object(self, account_id: str, filename: str, content: bytes) -> str:
        return self.objects.create_object(
            account_id, filename, content, "text/plain"
        ).object_id

    def seed_profile_assertion(self, account_id: str, dimension: str, value: str) -> None:
        self.database.scoped(account_id).execute(
            "INSERT INTO profile_assertions(assertion_id, account_id, canonical_dimension,"
            " value_or_rule, authorization_scope, status, sensitivity_class, version,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, 'self', 'active', 'preference', 1, ?, ?)",
            (f"assert-{dimension}-{account_id[:4]}", account_id, dimension, value, _now(), _now()),
        )

    def seed_reminder(self, account_id: str, subject: str) -> None:
        self.database.scoped(account_id).execute(
            "INSERT INTO reminders(reminder_id, account_id, qq_email, timezone, raw_text,"
            " schedule_json, subject, body, use_profile, status, next_run_at,"
            " created_at, updated_at)"
            " VALUES (?, ?, '10001@qq.com', 'Asia/Shanghai', ?, ?, ?, '正文', 0, 'enabled',"
            " '2099-01-01T00:00:00+00:00', ?, ?)",
            (f"remind-{account_id[:4]}", account_id, subject, "{}", subject, _now(), _now()),
        )

    def seed_plugin_state(self, account_id: str) -> None:
        self.database.scoped(account_id).execute(
            "INSERT INTO skill_packages(package_id, account_id, plugin_id, version, name,"
            " source, license, capabilities, data_categories, status, installed_at, updated_at)"
            " VALUES (?, ?, 'demo-pack', '1.0.0', '演示包', 'local', 'MIT', '[]', '[]',"
            " 'installed', ?, ?)",
            (f"pkg-{account_id[:4]}", account_id, _now(), _now()),
        )
        self.database.scoped(account_id).execute(
            "INSERT INTO account_skill_states(account_id, plugin_id, enabled, updated_at)"
            " VALUES (?, 'bridges-pdf', 1, ?)",
            (account_id, _now()),
        )

    def seed_mcp_server(self, account_id: str) -> None:
        self.database.scoped(account_id).execute(
            "INSERT INTO mcp_servers(mcp_id, account_id, name, version, description,"
            " source, integrity, integrity_sha256, command, permissions, status,"
            " enabled, installed_at, updated_at)"
            " VALUES (?, ?, '演示 MCP', '1.0.0', '描述', 'local', 'sha256:abc',"
            " 'sha256:abc', '[]', '{}', 'healthy', 1, ?, ?)",
            (f"mcp-{account_id[:4]}", account_id, _now(), _now()),
        )

    def seed_retired_and_v2_state(self, account_id: str) -> str:
        """V2 图状态与退役能力的历史结果（Issue 21 删除/导出/恢复回归数据）。

        覆盖新增登记表：图检查点（含写入）、生成运行与事件流、检索决策、
        学习小节状态、账户级 SMTP 验证尝试、工作流运行、OCR 解析缓存、
        附件草稿、画像墓碑，以及已退役的图片/视频生成结果（任务、资产、
        版本元数据与对象）。行标识与正文都带完整账户 ID，多账户数据不共用键。
        """
        conversation_id = self.create_conversation(account_id, f"历史-{account_id}", 1)
        message_id = f"msg-{conversation_id}-1"
        image_object_id = self.create_object(
            account_id,
            "legacy.png",
            f"legacy-image-{account_id}".encode(),
        )
        draft_object_id = self.create_object(
            account_id,
            "draft.png",
            f"legacy-draft-{account_id}".encode(),
        )
        now = _now()
        with self.database.transaction():
            scoped = self.database.scoped(account_id)
            scoped.execute(
                "INSERT INTO image_tasks(task_id, account_id, conversation_id, message_id,"
                " kind, prompt, status, model_id, asset_id, result_version_id,"
                " created_at, updated_at)"
                " VALUES (?, ?, ?, ?, 'generate', ?, 'done', 'wanx2.1-t2i-turbo', ?, ?, ?, ?)",
                (
                    f"img-task-{account_id}", account_id, conversation_id, message_id,
                    f"历史图片任务：{account_id}", f"img-asset-{account_id}",
                    f"img-ver-{account_id}", now, now,
                ),
            )
            scoped.execute(
                "INSERT INTO image_assets(asset_id, account_id, conversation_id, alt_text,"
                " alt_text_source, current_version_id, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, 'model', ?, ?, ?)",
                (
                    f"img-asset-{account_id}", account_id, conversation_id,
                    f"历史图片替代文本：{account_id}", f"img-ver-{account_id}", now, now,
                ),
            )
            scoped.execute(
                "INSERT INTO image_versions(version_id, asset_id, account_id, kind, prompt,"
                " model_id, object_id, media_type, content_length, created_at)"
                " VALUES (?, ?, ?, 'generate', ?, 'wanx2.1-t2i-turbo', ?, 'image/png', 18, ?)",
                (
                    f"img-ver-{account_id}", f"img-asset-{account_id}", account_id,
                    f"历史图片任务：{account_id}", image_object_id, now,
                ),
            )
            scoped.execute(
                "INSERT INTO video_tasks(task_id, account_id, conversation_id, message_id,"
                " prompt, status, model_id, asset_id, result_object_id, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, 'done', 'wan2.2-t2v-plus', ?, ?, ?, ?)",
                (
                    f"video-task-{account_id}", account_id, conversation_id, message_id,
                    f"历史视频任务：{account_id}", f"video-asset-{account_id}",
                    image_object_id, now, now,
                ),
            )
            scoped.execute(
                "INSERT INTO video_assets(asset_id, account_id, conversation_id, description,"
                " description_source, object_id, prompt, model_id, media_type, content_length,"
                " created_at, updated_at)"
                " VALUES (?, ?, ?, ?, 'prompt', ?, ?, 'wan2.2-t2v-plus', 'video/mp4', 18, ?, ?)",
                (
                    f"video-asset-{account_id}", account_id, conversation_id,
                    f"历史视频说明：{account_id}", image_object_id,
                    f"历史视频任务：{account_id}", now, now,
                ),
            )
            scoped.execute(
                "INSERT INTO study_states(account_id, conversation_id, state_json, updated_at)"
                " VALUES (?, ?, ?, ?)",
                (
                    account_id, conversation_id,
                    '{"stage": "summary", "owner": "' + account_id + '"}', now,
                ),
            )
            scoped.execute(
                "INSERT INTO generation_runs(run_id, account_id, conversation_id,"
                " user_message_id, assistant_message_id, status, stage, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, 'done', 'completed', ?, ?)",
                (
                    f"gen-{account_id}", account_id, conversation_id, message_id,
                    f"assistant-{account_id}", now, now,
                ),
            )
            scoped.execute(
                "INSERT INTO generation_events(run_id, seq, account_id, kind, payload, created_at)"
                " VALUES (?, 1, ?, 'delta', ?, ?)",
                (f"gen-{account_id}", account_id, f'{{"text": "历史回答 {account_id}"}}', now),
            )
            scoped.execute(
                "INSERT INTO graph_checkpoints(thread_id, checkpoint_ns, checkpoint_id,"
                " account_id, conversation_id, run_id, type, checkpoint, created_at)"
                " VALUES (?, '', 'ckpt-1', ?, ?, ?, 'langgraph', ?, ?)",
                (
                    f"thread-{account_id}", account_id, conversation_id,
                    f"gen-{account_id}", b"checkpoint-bytes", now,
                ),
            )
            scoped.execute(
                "INSERT INTO graph_checkpoint_writes(thread_id, checkpoint_ns, checkpoint_id,"
                " task_id, task_path, idx, channel, type, value, account_id)"
                " VALUES (?, '', 'ckpt-1', 'task-1', '', 0, 'messages', 'json', ?, ?)",
                (f"thread-{account_id}", b"write-bytes", account_id),
            )
            scoped.execute(
                "INSERT INTO retrieval_decisions(decision_id, assistant_message_id,"
                " user_message_id, conversation_id, account_id, action, reason, rules_version,"
                " capability_route, mode, query_fingerprint, created_at)"
                " VALUES (?, ?, ?, ?, ?, 'skip', '无需检索', 'v1', 'daily', 'companion', ?, ?)",
                (
                    f"decision-{account_id}", f"assistant-{account_id}", message_id,
                    conversation_id, account_id, f"fp-{account_id}", now,
                ),
            )
            scoped.execute(
                "INSERT INTO smtp_verification_attempts(attempt_id, account_id, state,"
                " message_token, deadline_at, created_at, updated_at)"
                " VALUES (?, ?, 'verified', ?, ?, ?, ?)",
                (f"smtp-{account_id}", account_id, f"token-{account_id}", now, now, now),
            )
            scoped.execute(
                "INSERT INTO workflow_runs(run_id, account_id, context_json, work_order_json,"
                " status, artifact_trust_status, updated_at)"
                " VALUES (?, ?, '{}', '{}', 'completed', 'verified', ?)",
                (f"workflow-{account_id}", account_id, now),
            )
            scoped.execute(
                "INSERT INTO document_parse_cache(account_id, content_hash, parser_version,"
                " parsed_json, created_at, ocr_run_id)"
                " VALUES (?, ?, 'v1', '{}', ?, ?)",
                (account_id, f"parse-{account_id}", now, f"ocr-{account_id}"),
            )
            scoped.execute(
                "INSERT INTO chat_attachment_drafts(object_id, account_id, upload_id,"
                " original_filename, media_type, content_length, content_hash, created_at,"
                " updated_at) VALUES (?, ?, ?, '草稿.png', 'image/png', 17, ?, ?, ?)",
                (
                    draft_object_id,
                    account_id,
                    f"upload-{account_id}",
                    f"hash-{account_id}",
                    now,
                    now,
                ),
            )
            scoped.execute(
                "INSERT INTO profile_extraction_tombstones(account_id, message_id, created_at)"
                " VALUES (?, ?, ?)",
                (account_id, message_id, now),
            )
        return conversation_id


    def inject_canaries(self) -> None:
        """写入剩余账户级凭据类别的金丝雀（SMTP 授权码）。"""
        self.smtp_credentials.save(self.acc1, SecretStr(SMTP_CANARY))
        self.smtp_credentials.save(self.acc2, SecretStr(SMTP_CANARY + "-b"))

    def seed_everything(self) -> None:
        """为 acc1 铺设全部数据类别（对话/画像/提醒/插件/MCP/对象）。"""
        self.create_conversation(self.acc1, "我的对话", 3)
        self.create_conversation(self.acc2, "B 的对话", 1)
        self.create_object(self.acc1, "素材.txt", b"alice material bytes")
        self.create_object(self.acc2, "b.txt", b"bob material bytes")
        self.seed_profile_assertion(self.acc1, "BASIC_INFORMATION", "我是一名研究生")
        self.seed_reminder(self.acc1, "每周交作业")
        self.seed_plugin_state(self.acc1)
        self.seed_mcp_server(self.acc1)
        self.inject_canaries()

    def delete_account(self, account_id: str) -> Any:
        """便捷调用删除服务（返回状态投影或抛错）。"""
        return self.deletion.delete_account(account_id)

    # ------------------------------------------------------------------
    # 断言助手
    # ------------------------------------------------------------------

    def audit_actions(self) -> list[str]:
        return [event["action"].value for event in self.observability.events]

    def audit_details(self) -> list[dict[str, Any]]:
        return [event.get("details") or {} for event in self.observability.events]


def logical_summary(harness: Harness, account_id: str) -> dict[str, int]:
    """按表统计账户数据条数（导出/恢复一致性比对用）。"""
    tables = (
        "conversations",
        "messages",
        "profile_assertions",
        "reminders",
        "skill_packages",
        "account_skill_states",
        "mcp_servers",
        "objects",
    )
    return {
        table: int(
            harness.database.scoped(account_id)
            .execute(
                f"SELECT COUNT(*) AS count FROM {table} WHERE account_id = ?",
                (account_id,),
            )
            .fetchone()["count"]
        )
        for table in tables
    }
