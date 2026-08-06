"""Issue 37 数据生命周期测试夹具：多账户数据建造器与秘密金丝雀。

Harness 使用真实文件数据库（备份/恢复需要快照与原子替换语义），对象库
落在 tmp_path；两个账户共享同一数据目录，供导出/删除/备份的隔离断言。
金丝雀写入全部凭据类别（百炼 Key/SMTP 授权码），测试断言导出与备份
字节绝不包含它们。
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

#: 秘密金丝雀：写入全部凭据类别后，导出/备份必须完全不包含这些正文。
QWEN_CANARY = "sk-test-canary-qwen-1234567890abcdef1234567890abcdef"
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
        self.credentials = InMemoryCredentialStore()
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
            credential_store=self.credentials,
            smtp_credential_store=self.smtp_credentials,
            observability_service=self.observability,  # type: ignore
        )
        self.backup = BackupService(
            database=self.database,
            object_repository=self.objects,
            object_store=self.object_store,
            identity_service=self.identity,
            credential_store=self.credentials,
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

    def inject_canaries(self) -> None:
        """写入全部凭据类别的金丝雀（百炼 Key 与 SMTP 授权码）。"""
        self.credentials.save(self.acc1, SecretStr(QWEN_CANARY))
        self.credentials.save(self.acc2, SecretStr(QWEN_CANARY + "-b"))
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
