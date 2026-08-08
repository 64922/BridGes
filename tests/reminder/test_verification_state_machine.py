"""验证 attempt 状态机测试（Issue 10）。

覆盖收尾 issue 10 的纵向切片：

- 保存创建唯一 attempt；投影携带 attempt_state/deadline（验证进行中）；
- A/B 竞争：先保存 A 立即保存 B，A 的迟到成功不能覆盖 B；
- 删除期间迟到回调：删除凭据后任何旧 attempt 都不能恢复 verified；
- IMAP 临时断线恢复：轮询中瞬断保持 waiting_receipt，恢复后继续；
- 120 秒窗口超时：虚拟时钟推进超过 deadline → receipt_timeout；
- 重启恢复：重建服务（模拟进程重启）后从数据库继续推进；
- 秘密扫描：attempt 表与审计事件不含授权码与密码正文。

网关与时钟可编程（与 test_reminder_service 同构）；受监督循环关闭，
由测试手动 tick 驱动（``_supervisor_tick`` 单步推进）。
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import SecretStr

from bridges.contracts.reminder import (
    ReminderError,
    SmtpAttemptState,
    SmtpSettingsProjection,
    SmtpStatus,
)
from bridges.reminder.service import ReminderService
from bridges.reminder.smtp import SmtpError
from bridges.storage import BridgesDatabase

AUTH_CODE = "ABCDEFGHIJKLMNOP"
EMAIL = "123456@qq.com"
PASSWORD = "Passw0rd123!"
T0 = datetime(2026, 8, 8, 0, 0, 0, tzinfo=UTC)


class _Clock:
    def __init__(self, start: datetime) -> None:
        self.time = start

    def advance(self, **kwargs: Any) -> None:
        self.time += timedelta(**kwargs)

    def __call__(self) -> datetime:
        return self.time


class _ProgrammableGateway:
    """分阶段验证网关替身：按令牌记录发送、控制收件到达与瞬时错误。"""

    def __init__(self) -> None:
        self.sends: list[dict[str, str]] = []
        self.received_tokens: set[str] = set()
        self.check_transient_count = 0
        self.check_imap_auth_failed = False
        self.send_error: SmtpError | None = None
        self.send_error_persistent = False

    def send_verification_mail(
        self, *, email: str, auth_code: str, token: str | None = None
    ) -> str:
        if self.send_error is not None:
            error = self.send_error
            if not self.send_error_persistent:
                self.send_error = None
            raise error
        token = token or secrets.token_hex(6)
        self.sends.append({"email": email, "token": token})
        return token

    def check_verification_receipt(
        self, *, email: str, auth_code: str, token: str
    ) -> bool:
        if self.check_imap_auth_failed:
            raise SmtpError("smtp_auth_failed", "授权码无法登录收件服务器。")
        if self.check_transient_count > 0:
            self.check_transient_count -= 1
            raise SmtpError("smtp_transient", "收件服务器连接中断。", retryable=True)
        return token in self.received_tokens


class _CredentialStore:
    """内存凭据存储替身（smtp 命名空间）。"""

    def __init__(self) -> None:
        self._values: dict[str, SecretStr] = {}

    def save(self, account_id: str, secret: SecretStr) -> None:
        self._values[account_id] = secret

    def get(self, account_id: str) -> SecretStr | None:
        return self._values.get(account_id)

    def delete(self, account_id: str) -> None:
        self._values.pop(account_id, None)


class _Audit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def log_audit(self, **kwargs: Any) -> None:
        self.events.append(kwargs)


class _Harness:
    """内存库 + 虚拟时钟 + 分阶段网关；手动 tick 驱动状态机。"""

    def __init__(
        self, *, window_seconds: float = 120.0
    ) -> None:
        self.clock = _Clock(T0)
        self.database = BridgesDatabase(":memory:")
        self.database.initialize()
        self.credentials = _CredentialStore()
        self.gateway = _ProgrammableGateway()
        self.audit = _Audit()
        self.window_seconds = window_seconds
        self.service = self._build()

    def _build(self) -> ReminderService:
        return ReminderService(
            database=self.database,
            credential_store=self.credentials,  # type: ignore[arg-type]
            observability_service=self.audit,  # type: ignore[arg-type]
            qq_email_provider=lambda _account_id: EMAIL,
            gateway=self.gateway,  # type: ignore[arg-type]
            clock=self.clock,
            verify_async=True,
            supervisor_enabled=False,
            verify_window_seconds=self.window_seconds,
        )

    def rebuild(self) -> ReminderService:
        """重建服务（模拟进程重启）：共享数据库与凭据。"""
        self.service = self._build()
        return self.service

    def save(self, account_id: str = "acc1") -> SmtpSettingsProjection:
        return self.service.save_smtp_code(account_id, AUTH_CODE, session_id="s1")

    def tick(self) -> None:
        self.service._supervisor_tick()

    def attempt_rows(self, account_id: str = "acc1") -> list[dict[str, Any]]:
        # rowid 反映插入顺序：同一虚拟时钟时刻创建多个 attempt 时，
        # created_at 相同，必须按插入顺序断言 supersede 语义。
        return [
            dict(row)
            for row in self.database.scoped(account_id)
            .execute(
                "SELECT * FROM smtp_verification_attempts WHERE account_id = ?"
                " ORDER BY rowid",
                (account_id,),
            )
            .fetchall()
        ]

    def settings_row(self, account_id: str = "acc1") -> dict[str, Any] | None:
        row = (
            self.database.scoped(account_id)
            .execute(
                "SELECT * FROM reminder_settings WHERE account_id = ?",
                (account_id,),
            )
            .fetchone()
        )
        return dict(row) if row is not None else None


def test_save_creates_attempt_with_stage_projection() -> None:
    """保存创建唯一 attempt：投影携带验证中阶段与收件截止，不含授权码。"""
    harness = _Harness()
    projection = harness.save()
    assert projection.status is SmtpStatus.VERIFYING
    assert projection.attempt_state is SmtpAttemptState.SMTP_CONNECTING
    # 发送阶段同样受窗口约束：创建时即有收件截止（网络临时失败有界重试）
    assert projection.attempt_deadline_at is not None
    harness.tick()  # smtp_connecting → mail_sent（收件截止不变）
    projection = harness.service.get_smtp_settings("acc1")
    assert projection.attempt_state is SmtpAttemptState.MAIL_SENT
    assert projection.attempt_deadline_at is not None
    rows = harness.attempt_rows()
    assert len(rows) == 1
    assert rows[0]["state"] == SmtpAttemptState.MAIL_SENT.value
    assert AUTH_CODE not in str(rows)
    assert PASSWORD not in str(rows)
    # 晚到邮件到达 → 轮询确认 → verified
    harness.clock.advance(seconds=10)
    harness.gateway.received_tokens.add(rows[0]["message_token"])
    harness.tick()
    projection = harness.service.get_smtp_settings("acc1")
    assert projection.status is SmtpStatus.VERIFIED
    assert projection.attempt_state is None
    assert projection.attempt_deadline_at is None


def test_second_save_supersedes_first_and_late_result_discarded() -> None:
    """A/B 竞争：先保存 A 立即保存 B，A 的迟到成功不能覆盖 B。"""
    harness = _Harness()
    harness.save()
    harness.tick()  # A: smtp_connecting → mail_sent
    attempt_a = harness.attempt_rows()[0]["attempt_id"]
    token_a = harness.attempt_rows()[0]["message_token"]
    harness.save()  # B 创建并 supersede A
    harness.tick()  # B: smtp_connecting → mail_sent
    rows = harness.attempt_rows()
    assert [row["state"] for row in rows] == [
        SmtpAttemptState.SUPERSEDED.value,
        SmtpAttemptState.MAIL_SENT.value,
    ]
    attempt_b = rows[1]["attempt_id"]
    # A 的邮件晚到：只标记 B 的令牌到达，推进仍应使 B verified
    harness.gateway.received_tokens.add(token_a)
    harness.tick()
    assert harness.service.get_smtp_settings("acc1").status is SmtpStatus.VERIFYING
    harness.gateway.received_tokens.add(rows[1]["message_token"])
    harness.tick()
    projection = harness.service.get_smtp_settings("acc1")
    assert projection.status is SmtpStatus.VERIFIED
    final = {row["attempt_id"]: row["state"] for row in harness.attempt_rows()}
    assert final[attempt_a] == SmtpAttemptState.SUPERSEDED.value
    assert final[attempt_b] == SmtpAttemptState.VERIFIED.value


def test_delete_supersedes_active_attempt_late_callback_ignored() -> None:
    """删除凭据：旧 attempt superseded，迟到回调不能恢复 verified。"""
    harness = _Harness()
    harness.save()
    harness.tick()  # mail_sent
    attempt_id = harness.attempt_rows()[0]["attempt_id"]
    token = harness.attempt_rows()[0]["message_token"]
    harness.service.delete_smtp_code("acc1", session_id="s1")
    assert harness.settings_row()["smtp_status"] == SmtpStatus.UNCONFIGURED.value
    # 邮件迟到到达后推进：attempt 非当前，不得写 verified
    harness.gateway.received_tokens.add(token)
    harness.tick()
    assert harness.settings_row()["smtp_status"] == SmtpStatus.UNCONFIGURED.value
    row = harness.attempt_rows()[0]
    assert row["state"] == SmtpAttemptState.SUPERSEDED.value
    assert row["attempt_id"] == attempt_id


def test_imap_transient_disconnect_keeps_polling_then_verifies() -> None:
    """IMAP 临时断线：轮询中瞬断保持 waiting_receipt，恢复后继续验证。"""
    harness = _Harness()
    harness.save()
    harness.tick()  # mail_sent
    harness.tick()  # waiting_receipt（首次查询，未到达）
    harness.gateway.check_transient_count = 2  # 连续两次瞬断
    harness.tick()
    projection = harness.service.get_smtp_settings("acc1")
    assert projection.status is SmtpStatus.VERIFYING
    assert projection.attempt_state is SmtpAttemptState.WAITING_RECEIPT
    harness.tick()
    assert harness.service.get_smtp_settings("acc1").status is SmtpStatus.VERIFYING
    # 断线恢复 + 邮件到达 → verified（同一轮继续轮询，不失败）
    token = harness.attempt_rows()[0]["message_token"]
    harness.gateway.received_tokens.add(token)
    harness.tick()
    assert harness.service.get_smtp_settings("acc1").status is SmtpStatus.VERIFIED


def test_receipt_timeout_after_window_with_virtual_clock() -> None:
    """超过收件窗口（默认 120 秒）→ receipt_timeout 终态。"""
    harness = _Harness()
    harness.save()
    harness.tick()  # mail_sent，deadline = T0 + 120s
    harness.clock.advance(seconds=10)
    harness.tick()  # waiting_receipt（窗口内）
    assert harness.service.get_smtp_settings("acc1").status is SmtpStatus.VERIFYING
    harness.clock.advance(seconds=111)  # 累计 121s > 窗口
    harness.tick()
    projection = harness.service.get_smtp_settings("acc1")
    assert projection.status is SmtpStatus.FAILED
    assert projection.error_code == "receipt_timeout"
    row = harness.attempt_rows()[0]
    assert row["state"] == SmtpAttemptState.FAILED.value
    assert row["error_code"] == "receipt_timeout"


def test_restart_resumes_verification_from_persisted_attempt() -> None:
    """重启恢复：重建服务后从持久化 attempt 继续推进（无需重新保存）。"""
    harness = _Harness()
    harness.save()
    harness.tick()  # mail_sent（attempt 已持久化）
    harness.rebuild()  # 模拟进程重启
    token = harness.attempt_rows()[0]["message_token"]
    harness.gateway.received_tokens.add(token)
    harness.tick()
    assert harness.service.get_smtp_settings("acc1").status is SmtpStatus.VERIFIED


def test_smtp_auth_failure_during_send_fails_with_code() -> None:
    """SMTP 认证失败（发送阶段）→ 立即 failed，错误码区分。"""
    harness = _Harness()
    harness.gateway.send_error = SmtpError("smtp_auth_failed", "授权码无效。")
    harness.save()
    harness.tick()
    projection = harness.service.get_smtp_settings("acc1")
    assert projection.status is SmtpStatus.FAILED
    assert projection.error_code == "smtp_auth_failed"
    row = harness.attempt_rows()[0]
    assert row["state"] == SmtpAttemptState.FAILED.value


def test_send_transient_retries_within_window_then_verifies() -> None:
    """发送阶段网络临时失败：窗口内有界重试（不写失败），恢复后 verified。"""
    harness = _Harness()
    harness.gateway.send_error = SmtpError(
        "smtp_transient", "邮件服务器暂时不可用。", retryable=True
    )
    harness.save()
    harness.tick()
    assert harness.attempt_rows()[0]["state"] == SmtpAttemptState.SMTP_CONNECTING.value
    harness.tick()  # 恢复：重试成功 → mail_sent
    row = harness.attempt_rows()[0]
    assert row["state"] == SmtpAttemptState.MAIL_SENT.value
    harness.gateway.received_tokens.add(row["message_token"])
    harness.tick()
    assert harness.service.get_smtp_settings("acc1").status is SmtpStatus.VERIFIED


def test_send_transient_exhausts_window_then_fails() -> None:
    """发送阶段网络临时失败持续到窗口到期 → smtp_transient 终态。"""
    harness = _Harness(window_seconds=10.0)
    harness.gateway.send_error = SmtpError(
        "smtp_transient", "邮件服务器暂时不可用。", retryable=True
    )
    harness.gateway.send_error_persistent = True
    harness.save()
    harness.tick()
    harness.tick()
    assert harness.attempt_rows()[0]["state"] == SmtpAttemptState.SMTP_CONNECTING.value
    harness.clock.advance(seconds=11)  # 超过窗口
    harness.tick()
    projection = harness.service.get_smtp_settings("acc1")
    assert projection.status is SmtpStatus.FAILED
    assert projection.error_code == "smtp_transient"
    assert harness.attempt_rows()[0]["state"] == SmtpAttemptState.FAILED.value


def test_imap_auth_failure_during_poll_fails_with_code() -> None:
    """IMAP 登录被拒（收件阶段）→ 立即 smtp_auth_failed，不空转到超时。"""
    harness = _Harness()
    harness.save()
    harness.tick()  # mail_sent（SMTP 已接受）
    harness.gateway.check_imap_auth_failed = True
    harness.tick()
    projection = harness.service.get_smtp_settings("acc1")
    assert projection.status is SmtpStatus.FAILED
    assert projection.error_code == "smtp_auth_failed"
    assert harness.attempt_rows()[0]["state"] == SmtpAttemptState.FAILED.value


def test_secrets_never_in_attempt_table_or_audit() -> None:
    """秘密扫描：attempt 表与审计事件不含授权码与密码正文。"""
    harness = _Harness()
    harness.save()
    harness.tick()
    harness.tick()
    harness.service.delete_smtp_code("acc1", session_id="s1")
    for row in harness.attempt_rows():
        assert AUTH_CODE not in str(row)
        assert PASSWORD not in str(row)
        assert "authorization_code" not in str(row)
    for event in harness.audit.events:
        assert AUTH_CODE not in str(event)
        assert PASSWORD not in str(event)


def test_invalid_code_rejected_before_attempt_created() -> None:
    """非法授权码在创建 attempt 前被拒绝（不留半成品状态）。"""
    harness = _Harness()
    with pytest.raises(ReminderError) as exc:
        harness.service.save_smtp_code("acc1", "P@ssw0rd!123", session_id="s1")
    assert exc.value.code == "invalid_auth_code"
    assert harness.attempt_rows() == []
    settings = harness.settings_row()
    assert settings is None or settings["smtp_status"] == SmtpStatus.UNCONFIGURED.value
