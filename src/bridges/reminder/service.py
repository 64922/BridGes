"""QQ SMTP 任务提醒编排服务（Issue 33，ADR-0004/0013/0015/0019）。

账户配置并验证 QQ SMTP 授权码（自发自收：SMTP 发送测试邮件 → IMAP 确认
到达），授权码按账户加密保存在凭据存储的 ``smtp`` 命名空间，绝不进入
日志、模型、导出或本服务的任何投影。收件人/发件人固定为当前账户注册的
同一 QQ 邮箱，跨账户或第三方收件人在入口即拒绝。

自然语言经确定性解析器转为带时区结构化日程（同时保存账户时区规则与
UTC 执行时间），措辞只调用用户授权的最小画像切片（reminder 模式），
确认后才持久化冻结快照。调度（process_due）由 scheduler 进程调用：

- 一次性提醒：恢复运行后 24 小时内补发并标记延迟，超窗记为错过；
- 重复提醒：最多补发最近一次错过，跳过更早积压并继续下一正常周期；
- SMTP 临时失败：有限退避重试（默认 3 次，30s/60s 递增），超过后
  一次性提醒暂停待手动补发、重复提醒跳过本次继续下一周期；
- 授权失效（535/登录拒绝）：立即暂停相关提醒并写入验证失败原因，
  页面给出重新验证路径；
- 投递记录准确区分发送/失败/跳过/补发/手动重试。

全部读写走账户作用域；``process_due`` 是系统级清扫（按行本身归属账户），
其余操作通过 ``scoped()`` 强制隔离。时钟与邮件网关可注入（测试确定性）。
"""

from __future__ import annotations

import contextlib
import json
import re
import secrets
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import SecretStr

from bridges.contracts.observability import AuditAction, AuditResult
from bridges.contracts.profiles import ProfileSlice
from bridges.contracts.reminder import (
    ParsedReminderPreview,
    ReminderCreateRequest,
    ReminderDeliveryKind,
    ReminderDeliveryOutcome,
    ReminderDeliveryProjection,
    ReminderError,
    ReminderProfileUsage,
    ReminderProjection,
    ReminderRepeatRule,
    ReminderSchedule,
    ReminderSettingsProjection,
    ReminderSettingsUpdateRequest,
    ReminderStatus,
    ReminderUpdateRequest,
    SmtpAttemptState,
    SmtpSettingsProjection,
    SmtpStatus,
)
from bridges.credentials.store import CredentialStoreError, CredentialStorePort
from bridges.observability.service import ObservabilityService
from bridges.profiles.adapters import ProfileError
from bridges.profiles.service import ProfileService
from bridges.reminder.adaptation import adapt_reminder_body
from bridges.reminder.parser import ReminderParseError, parse_reminder_text
from bridges.reminder.rules import (
    ReminderRuleError,
    next_occurrence,
    validate_timezone,
)
from bridges.reminder.smtp import QqMailGateway, SmtpError
from bridges.runtime.queue import RetryKind, TaskQueue
from bridges.storage import BridgesDatabase

#: 授权码格式：QQ 邮箱授权码为 16 位字母数字（容忍 10-32 位）。
_AUTH_CODE_RE = re.compile(r"^[A-Za-z0-9]{10,32}$")

#: 发送尝试上限与退避秒数（首次失败后 30s、60s 递增，封顶 300s）。
MAX_SEND_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 30
#: 退避上限（秒）：与队列 EXPONENTIAL 公式的 cap 对齐。
_RETRY_CAP_SECONDS = 300

#: 自发自收验证的收件确认窗口（秒，Issue 10）：默认 120 秒覆盖正常
#: 投递延迟，超时终态为 ``receipt_timeout``（可配置覆盖，测试用短窗口）。
VERIFY_WINDOW_SECONDS = 120.0

#: 验证 attempt 的活跃阶段（可被 supersede / 可推进）。
_ACTIVE_ATTEMPT_STATES = (
    SmtpAttemptState.SMTP_CONNECTING.value,
    SmtpAttemptState.MAIL_SENT.value,
    SmtpAttemptState.WAITING_RECEIPT.value,
)

#: 活跃阶段 SQL 占位符（参数化拼接，无注入风险）。
_ACTIVE_PLACEHOLDERS = ", ".join("?" for _ in _ACTIVE_ATTEMPT_STATES)

#: 到期判定的宽松阈值：调度器轮询粒度（60s）内的正常延迟不算补发，
#: 只有超过该阈值的到期才进入 24 小时补发/错过语义。
LATE_GRACE_SECONDS = 300

#: ADR-0019：24 小时有限补发窗口。
CATCH_UP_WINDOW = timedelta(hours=24)

#: 投递记录列表默认上限。
_DELIVERIES_LIMIT = 100

#: 明细 JSON 字段（绝不包含授权码与邮件正文）。
_AUDIT_DETAIL_KEYS = frozenset(
    {
        "reminder_id", "kind", "outcome", "delayed", "repeat",
        "use_profile", "category_count", "error_code", "retry_count",
    }
)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _encode_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _decode_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class ReminderService:
    """提醒编排；除 ``process_due`` 外全部操作限定在传入账户内。"""

    def __init__(
        self,
        *,
        database: BridgesDatabase,
        credential_store: CredentialStorePort,
        observability_service: ObservabilityService,
        qq_email_provider: Callable[[str], str],
        profile_service: ProfileService | None = None,
        gateway: QqMailGateway | None = None,
        clock: Callable[[], datetime] | None = None,
        verify_async: bool = True,
        max_send_attempts: int = MAX_SEND_ATTEMPTS,
        catch_up_window: timedelta = CATCH_UP_WINDOW,
        verify_window_seconds: float = VERIFY_WINDOW_SECONDS,
        supervisor_tick_seconds: float = 2.0,
        supervisor_enabled: bool = True,
    ) -> None:
        self._database = database
        self._credentials = credential_store
        self._observability = observability_service
        self._qq_email = qq_email_provider
        self._profiles = profile_service
        self._gateway = gateway or QqMailGateway()
        self._clock = clock or _now_utc
        self._verify_async = verify_async
        self._max_send_attempts = max_send_attempts
        self._catch_up_window = catch_up_window
        # Issue 10：验证 attempt 状态机参数——收件确认窗口（超时终态
        # receipt_timeout）与受监督轮询的 tick 间隔。
        self._verify_window_seconds = verify_window_seconds
        self._supervisor_tick_seconds = supervisor_tick_seconds
        self._stop_event = threading.Event()
        if verify_async and supervisor_enabled:
            threading.Thread(
                target=self._supervisor_loop,
                name="smtp-verification-supervisor",
                daemon=True,
            ).start()

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _now(self) -> datetime:
        return self._clock().astimezone(UTC)

    def _audit(
        self,
        *,
        account_id: str,
        action: AuditAction,
        result: AuditResult,
        details: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> None:
        safe = {k: v for k, v in (details or {}).items() if k in _AUDIT_DETAIL_KEYS}
        self._observability.log_audit(
            actor_account_id=account_id,
            actor_session_id=session_id,
            action=action,
            result=result,
            reason=None,
            details=safe,
        )

    def _require_qq_email(self, account_id: str) -> str:
        try:
            email = self._qq_email(account_id)
        except Exception as exc:  # noqa: BLE001 - 身份提供者错误统一映射
            raise ReminderError(
                "identity_unavailable", "身份服务不可用，无法读取账户邮箱。", 503
            ) from exc
        if not email:
            raise ReminderError(
                "identity_unavailable", "当前账户缺少 QQ 邮箱，无法使用邮件提醒。", 409
            )
        return email

    def _settings_row(
        self, account_id: str, *, create: bool = True
    ) -> dict[str, Any] | None:
        """读取/创建账户提醒设置行（含 SMTP 验证状态机）。"""
        scoped = self._database.scoped(account_id)
        row = scoped.execute(
            "SELECT * FROM reminder_settings WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if row is not None:
            return dict(row)
        if not create:
            return None
        with self._database.transaction():
            scoped.execute(
                "INSERT INTO reminder_settings (account_id) VALUES (?)",
                (account_id,),
            )
        row = scoped.execute(
            "SELECT * FROM reminder_settings WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        assert row is not None
        return dict(row)

    def _update_settings_fields(
        self, account_id: str, **fields: str | None
    ) -> None:
        self._settings_row(account_id)  # 确保行存在（UPDATE 不隐式建行）
        scoped = self._database.scoped(account_id)
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self._database.transaction():
            scoped.execute(
                f"UPDATE reminder_settings SET {assignments} WHERE account_id = ?",
                (*fields.values(), account_id),
            )

    def _smtp_projection(self, account_id: str, settings: dict[str, Any]) -> SmtpSettingsProjection:
        attempt_state: SmtpAttemptState | None = None
        attempt_deadline_at: datetime | None = None
        attempt_id = settings.get("smtp_attempt_id")
        if settings.get("smtp_status") == SmtpStatus.VERIFYING.value and attempt_id:
            attempt = self._attempt_row(account_id, str(attempt_id))
            if attempt is not None and attempt["state"] in _ACTIVE_ATTEMPT_STATES:
                attempt_state = SmtpAttemptState(str(attempt["state"]))
                attempt_deadline_at = _decode_dt(attempt["deadline_at"])
        return SmtpSettingsProjection(
            status=SmtpStatus(settings.get("smtp_status") or SmtpStatus.UNCONFIGURED.value),
            qq_email=self._require_qq_email(account_id),
            verified_at=_decode_dt(settings.get("smtp_verified_at")),
            error_code=settings.get("smtp_error_code"),
            error_message=settings.get("smtp_error_message"),
            attempt_state=attempt_state,
            attempt_deadline_at=attempt_deadline_at,
            updated_at=_decode_dt(settings.get("smtp_updated_at")),
        )

    # ------------------------------------------------------------------
    # SMTP 配置与验证
    # ------------------------------------------------------------------

    def get_smtp_settings(self, account_id: str) -> SmtpSettingsProjection:
        """返回账户 SMTP 配置投影（不含授权码正文）。"""
        settings = self._settings_row(account_id)
        assert settings is not None
        return self._smtp_projection(account_id, settings)

    def save_smtp_code(
        self,
        account_id: str,
        authorization_code: str,
        *,
        session_id: str | None = None,
    ) -> SmtpSettingsProjection:
        """保存/替换授权码并创建验证 attempt（不接收 QQ 登录密码）。

        授权码只写入凭据存储；attempt 表只保存随机验证令牌（非秘密）。
        旧 attempt 在同一事务内被 supersede，晚到结果不得覆盖新配置。
        """
        if not _AUTH_CODE_RE.match(authorization_code):
            raise ReminderError(
                "invalid_auth_code",
                "请输入 QQ 邮箱授权码（16 位字母数字），而不是 QQ 登录密码。",
            )
        self._require_qq_email(account_id)
        try:
            self._credentials.save(account_id, SecretStr(authorization_code))
        except CredentialStoreError as exc:
            raise ReminderError(
                "credential_store_unavailable", str(exc), 503
            ) from exc
        attempt_id = self._new_attempt(account_id, session_id=session_id)
        self._audit(
            account_id=account_id,
            action=AuditAction.SMTP_CODE_SAVE,
            result=AuditResult.SUCCESS,
            session_id=session_id,
        )
        self._advance_or_sync(account_id, attempt_id, session_id=session_id)
        return self.get_smtp_settings(account_id)

    def verify_smtp_now(
        self,
        account_id: str,
        *,
        session_id: str | None = None,
    ) -> SmtpSettingsProjection:
        """重新执行自发自收验证（授权码仍保存时有效）。"""
        self._require_qq_email(account_id)
        self._load_auth_code(account_id)
        attempt_id = self._new_attempt(account_id, session_id=session_id)
        self._advance_or_sync(account_id, attempt_id, session_id=session_id)
        return self.get_smtp_settings(account_id)

    def delete_smtp_code(
        self,
        account_id: str,
        *,
        session_id: str | None = None,
    ) -> SmtpSettingsProjection:
        """删除授权码并复位验证状态；暂停依赖它的启用中提醒。

        同一事务内把全部活跃 attempt 标 superseded：删除后任何旧
        attempt 的迟到成功/失败都不得恢复 verified。
        """
        email = self._require_qq_email(account_id)
        with contextlib.suppress(CredentialStoreError):
            self._credentials.delete(account_id)
        now = self._now()
        with self._database.transaction():
            scoped = self._database.scoped(account_id)
            scoped.execute(
                "UPDATE smtp_verification_attempts SET state = ?, updated_at = ?"
                " WHERE account_id = ? AND state IN ("
                + _ACTIVE_PLACEHOLDERS + ")",
                (SmtpAttemptState.SUPERSEDED.value, _encode_dt(now), account_id,
                 *_ACTIVE_ATTEMPT_STATES),
            )
            scoped.execute(
                "UPDATE reminder_settings SET smtp_status = ?, smtp_verified_at = NULL,"
                " smtp_error_code = NULL, smtp_error_message = NULL,"
                " smtp_attempt_id = NULL, smtp_updated_at = ? WHERE account_id = ?",
                (SmtpStatus.UNCONFIGURED.value, _encode_dt(now), account_id),
            )
        self._pause_reminders_without_credential(account_id)
        self._audit(
            account_id=account_id,
            action=AuditAction.SMTP_CODE_DELETE,
            result=AuditResult.SUCCESS,
            session_id=session_id,
        )
        return SmtpSettingsProjection(
            status=SmtpStatus.UNCONFIGURED,
            qq_email=email,
            updated_at=self._now(),
        )

    def _load_auth_code(self, account_id: str) -> str:
        try:
            secret = self._credentials.get(account_id)
        except CredentialStoreError as exc:
            raise ReminderError(
                "credential_store_unavailable", str(exc), 503
            ) from exc
        if secret is None:
            raise ReminderError(
                "smtp_not_configured", "当前账户尚未保存 QQ 邮箱授权码。", 409
            )
        return secret.get_secret_value()

    # ------------------------------------------------------------------
    # 验证 attempt 状态机（Issue 10）
    #
    # 每次保存/重新验证创建唯一 attempt（smtp_verification_attempts 表），
    # 只有 ``reminder_settings.smtp_attempt_id`` 指向的当前 attempt 可以
    # 提交账户 SMTP 终态；更换授权码、重新验证、删除凭据都会 supersede
    # 旧 attempt，旧 attempt 的迟到成功/失败一律丢弃。推进由受监督循环
    # （``_supervisor_loop``）按固定 tick 驱动：SMTP 发送 → mail_sent
    # （收件截止计时）→ waiting_receipt（IMAP 单次查询，同一会话 + NOOP
    # 保活，断线自动恢复）→ verified | failed；超过收件窗口 → 终态
    # ``receipt_timeout``。授权码正文绝不进入 attempt 表或任何投影。
    # ------------------------------------------------------------------

    def _attempt_row(
        self, account_id: str, attempt_id: str
    ) -> dict[str, Any] | None:
        scoped = self._database.scoped(account_id)
        row = scoped.execute(
            "SELECT * FROM smtp_verification_attempts"
            " WHERE account_id = ? AND attempt_id = ?",
            (account_id, attempt_id),
        ).fetchone()
        return dict(row) if row is not None else None

    def _new_attempt(
        self, account_id: str, *, session_id: str | None
    ) -> str:
        """创建唯一验证 attempt 并 supersede 旧活跃 attempt，返回 attempt ID。"""
        self._settings_row(account_id)  # 确保 settings 行存在（UPDATE 不隐式建行）
        attempt_id = f"av-{secrets.token_hex(8)}"
        token = secrets.token_hex(6)
        now = self._now()
        deadline = now + timedelta(seconds=self._verify_window_seconds)
        with self._database.transaction():
            scoped = self._database.scoped(account_id)
            scoped.execute(
                "UPDATE smtp_verification_attempts SET state = ?, updated_at = ?"
                " WHERE account_id = ? AND state IN ("
                + _ACTIVE_PLACEHOLDERS + ")",
                (SmtpAttemptState.SUPERSEDED.value, _encode_dt(now), account_id,
                 *_ACTIVE_ATTEMPT_STATES),
            )
            scoped.execute(
                "INSERT INTO smtp_verification_attempts ("
                " attempt_id, account_id, state, message_token, deadline_at,"
                " created_at, updated_at, session_id"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (attempt_id, account_id, SmtpAttemptState.SMTP_CONNECTING.value,
                 token, _encode_dt(deadline), _encode_dt(now), _encode_dt(now),
                 session_id),
            )
            scoped.execute(
                "UPDATE reminder_settings SET smtp_status = ?, smtp_verified_at = NULL,"
                " smtp_error_code = NULL, smtp_error_message = NULL,"
                " smtp_attempt_id = ?, smtp_updated_at = ? WHERE account_id = ?",
                (SmtpStatus.VERIFYING.value, attempt_id, _encode_dt(now), account_id),
            )
        return attempt_id

    def _attempt_update(
        self,
        account_id: str,
        attempt_id: str,
        *,
        state: str,
        deadline_at: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """推进 attempt 状态；已被 supersede 的 attempt 不再改写。"""
        now = self._now()
        with self._database.transaction():
            self._database.scoped(account_id).execute(
                "UPDATE smtp_verification_attempts SET state = ?, deadline_at = ?,"
                " error_code = ?, error_message = ?, updated_at = ?"
                " WHERE account_id = ? AND attempt_id = ? AND state IN ("
                + _ACTIVE_PLACEHOLDERS + ")",
                (state, deadline_at, error_code, error_message, _encode_dt(now),
                 account_id, attempt_id, *_ACTIVE_ATTEMPT_STATES),
            )

    def _commit_verified(
        self,
        account_id: str,
        attempt_id: str,
        *,
        session_id: str | None = None,
    ) -> None:
        """提交 verified 终态；只有当前 attempt 能提交账户 SMTP 状态。

        ``UPDATE ... WHERE smtp_attempt_id = ?`` 是原子 supersede 检查：
        提交影响行数为 0 说明 attempt 已被取代/凭据已删除，迟到成功
        不得覆盖新配置，attempt 保持 superseded。
        """
        now = self._now()
        with self._database.transaction():
            scoped = self._database.scoped(account_id)
            cursor = scoped.execute(
                "UPDATE reminder_settings SET smtp_status = ?, smtp_verified_at = ?,"
                " smtp_error_code = NULL, smtp_error_message = NULL,"
                " smtp_updated_at = ? WHERE account_id = ? AND smtp_attempt_id = ?",
                (SmtpStatus.VERIFIED.value, _encode_dt(now), _encode_dt(now),
                 account_id, attempt_id),
            )
            committed = cursor.rowcount == 1
            scoped.execute(
                "UPDATE smtp_verification_attempts SET state = ?, updated_at = ?"
                " WHERE account_id = ? AND attempt_id = ? AND state IN ("
                + _ACTIVE_PLACEHOLDERS + ")",
                (SmtpAttemptState.VERIFIED.value, _encode_dt(now), account_id,
                 attempt_id, *_ACTIVE_ATTEMPT_STATES),
            )
        # 验证已到终态：关闭该邮箱的轮询会话，避免空闲连接常驻
        with contextlib.suppress(Exception):  # noqa: BLE001 - 收尾失败无碍
            self._gateway.close_imap_session(self._require_qq_email(account_id))
        self._audit(
            account_id=account_id,
            action=AuditAction.SMTP_VERIFY,
            result=AuditResult.SUCCESS
            if committed
            else AuditResult.BLOCKED,
            details={} if committed else {"error_code": "superseded"},
            session_id=session_id,
        )

    def _commit_failed(
        self,
        account_id: str,
        attempt_id: str,
        error_code: str,
        error_message: str,
        *,
        session_id: str | None = None,
    ) -> None:
        """提交 failed 终态；同样只有当前 attempt 能写账户 SMTP 状态。"""
        now = self._now()
        with self._database.transaction():
            scoped = self._database.scoped(account_id)
            scoped.execute(
                "UPDATE reminder_settings SET smtp_status = ?, smtp_error_code = ?,"
                " smtp_error_message = ?, smtp_updated_at = ?"
                " WHERE account_id = ? AND smtp_attempt_id = ?",
                (SmtpStatus.FAILED.value, error_code, error_message,
                 _encode_dt(now), account_id, attempt_id),
            )
            scoped.execute(
                "UPDATE smtp_verification_attempts SET state = ?, error_code = ?,"
                " error_message = ?, updated_at = ?"
                " WHERE account_id = ? AND attempt_id = ? AND state IN ("
                + _ACTIVE_PLACEHOLDERS + ")",
                (SmtpAttemptState.FAILED.value, error_code, error_message,
                 _encode_dt(now), account_id, attempt_id, *_ACTIVE_ATTEMPT_STATES),
            )
        # 验证已到终态：关闭该邮箱的轮询会话，避免空闲连接常驻
        with contextlib.suppress(Exception):  # noqa: BLE001 - 收尾失败无碍
            self._gateway.close_imap_session(self._require_qq_email(account_id))
        self._audit(
            account_id=account_id,
            action=AuditAction.SMTP_VERIFY,
            result=AuditResult.BLOCKED,
            details={"error_code": error_code},
            session_id=session_id,
        )

    def _advance_or_sync(
        self,
        account_id: str,
        attempt_id: str,
        *,
        session_id: str | None,
    ) -> None:
        """异步：attempt 已创建，由受监督循环推进；同步：立即完成验证。"""
        if self._verify_async:
            return
        email = self._require_qq_email(account_id)
        code = self._load_auth_code(account_id)
        try:
            self._gateway.verify_self_send_receive(email=email, auth_code=code)
        except SmtpError as exc:
            self._commit_failed(
                account_id, attempt_id, exc.code, exc.message, session_id=session_id
            )
            return
        except Exception:  # noqa: BLE001 - 不可预期错误如实落为 failed
            # 固定中文文案：不携带原始异常文本（可能含服务器回显细节）
            self._commit_failed(
                account_id,
                attempt_id,
                "verification_failed",
                "验证过程出现未知错误，请稍后重试。",
                session_id=session_id,
            )
            return
        self._commit_verified(account_id, attempt_id, session_id=session_id)

    def _advance_verification(self, account_id: str) -> None:
        """单步推进当前验证 attempt（受监督轮询的推进原语）。"""
        settings = self._settings_row(account_id, create=False)
        if settings is None or settings.get("smtp_status") != SmtpStatus.VERIFYING.value:
            return
        attempt_id = settings.get("smtp_attempt_id")
        if not attempt_id:
            return
        attempt = self._attempt_row(account_id, str(attempt_id))
        if attempt is None or attempt["state"] not in _ACTIVE_ATTEMPT_STATES:
            return
        now = self._now()
        if attempt["state"] == SmtpAttemptState.SMTP_CONNECTING.value:
            # 发送阶段同样受窗口约束：创建时已记录截止，网络临时失败在
            # 窗口内有界重试，超窗才以 smtp_transient 终态失败。
            deadline = _decode_dt(attempt["deadline_at"])
            if deadline is not None and now >= deadline:
                self._commit_failed(
                    account_id,
                    str(attempt_id),
                    "smtp_transient",
                    "验证邮件发送暂时失败，请稍后重新验证。",
                )
                return
            self._advance_send(account_id, str(attempt_id), now)
            return
        self._advance_poll(account_id, str(attempt_id), attempt, now)

    def _advance_send(
        self, account_id: str, attempt_id: str, now: datetime
    ) -> None:
        """SMTP 发送阶段：成功后记录收件截止并进入 mail_sent。

        认证失败/拒绝（smtp_auth_failed/smtp_rejected）与凭据不可用
        如实终态失败；网络临时失败（smtp_transient）保持 smtp_connecting
        由受监督循环在窗口内有界重试（不写失败、不向用户承诺未发生的
        自动重试）。
        """
        try:
            email = self._require_qq_email(account_id)
            code = self._load_auth_code(account_id)
            attempt = self._attempt_row(account_id, attempt_id)
            assert attempt is not None
            self._gateway.send_verification_mail(
                email=email, auth_code=code, token=str(attempt["message_token"])
            )
        except SmtpError as exc:
            if exc.retryable:
                # 网络临时失败：保留 smtp_connecting，下轮 tick 重试；
                # 超窗由 _advance_verification 以 smtp_transient 终态。
                return
            self._commit_failed(account_id, attempt_id, exc.code, exc.message)
            return
        except ReminderError as exc:
            self._commit_failed(account_id, attempt_id, exc.code, exc.message)
            return
        deadline = now + timedelta(seconds=self._verify_window_seconds)
        self._attempt_update(
            account_id,
            attempt_id,
            state=SmtpAttemptState.MAIL_SENT.value,
            deadline_at=_encode_dt(deadline),
        )

    def _advance_poll(
        self,
        account_id: str,
        attempt_id: str,
        attempt: dict[str, Any],
        now: datetime,
    ) -> None:
        """收件确认阶段：超时 → receipt_timeout；否则单次 IMAP 查询。"""
        deadline = _decode_dt(attempt["deadline_at"])
        if deadline is not None and now >= deadline:
            self._commit_failed(
                account_id,
                attempt_id,
                "receipt_timeout",
                f"验证邮件未在 {self._verify_window_seconds:.0f} 秒内到达收件箱，"
                "请稍后重试或重新验证（邮件可能延迟到达）。",
            )
            return
        # 回传 deadline：attempt_update 的 deadline_at 是覆盖语义，
        # 缺省会清空已记录的收件截止（导致永不超时）。
        self._attempt_update(
            account_id,
            attempt_id,
            state=SmtpAttemptState.WAITING_RECEIPT.value,
            deadline_at=_encode_dt(deadline),
        )
        try:
            email = self._require_qq_email(account_id)
            code = self._load_auth_code(account_id)
            received = self._gateway.check_verification_receipt(
                email=email, auth_code=code, token=str(attempt["message_token"])
            )
        except SmtpError as exc:
            if exc.code == "smtp_auth_failed":
                # IMAP 登录被拒：不可重试，立即失败并提供重新验证路径
                self._commit_failed(account_id, attempt_id, exc.code, exc.message)
            # 其余（smtp_transient 等）：保持 waiting_receipt 下轮再试
            return
        except ReminderError as exc:
            self._commit_failed(account_id, attempt_id, exc.code, exc.message)
            return
        if received:
            self._commit_verified(account_id, attempt_id)

    def _supervisor_tick(self) -> None:
        """推进全部验证中账户的当前 attempt（有界退避一步）。"""
        rows = self._database.connection.execute(
            "SELECT account_id FROM reminder_settings WHERE smtp_status = ?",
            (SmtpStatus.VERIFYING.value,),
        ).fetchall()
        for row in rows:
            try:
                self._advance_verification(str(row["account_id"]))
            except Exception:  # noqa: BLE001 - 单账户推进失败不中断整轮
                continue

    def _supervisor_loop(self) -> None:
        """受监督轮询循环：固定 tick 推进验证；重启后从数据库恢复。"""
        while not self._stop_event.wait(self._supervisor_tick_seconds):
            try:
                self._supervisor_tick()
            except Exception:  # noqa: BLE001 - 单轮失败不退出循环
                continue

    def stop(self) -> None:
        """停止受监督轮询（进程关闭时调用；daemon 线程不阻塞退出）。"""
        self._stop_event.set()

    # ------------------------------------------------------------------
    # 账户设置（时区）
    # ------------------------------------------------------------------

    def get_settings(self, account_id: str) -> ReminderSettingsProjection:
        settings = self._settings_row(account_id)
        assert settings is not None
        return ReminderSettingsProjection(
            timezone=settings.get("timezone") or "Asia/Shanghai"
        )

    def update_settings(
        self, account_id: str, request: ReminderSettingsUpdateRequest
    ) -> ReminderSettingsProjection:
        try:
            validate_timezone(request.timezone)
        except ReminderRuleError as exc:
            raise ReminderError("invalid_timezone", str(exc)) from exc
        self._update_settings_fields(account_id, timezone=request.timezone)
        return self.get_settings(account_id)

    # ------------------------------------------------------------------
    # 解析与创建
    # ------------------------------------------------------------------

    def _compile_profile_slice(
        self, account_id: str, *, run_token: str
    ) -> ProfileSlice:
        if self._profiles is None:
            raise ReminderError(
                "profile_unavailable",
                "画像服务不可用，暂时无法使用画像适配；请关闭画像适配后重试。",
            )
        try:
            return self._profiles.compile_chat_slice(
                account_id, mode="reminder", run_id=f"reminder:{run_token}"
            )
        except ReminderRuleError:
            raise
        except Exception as exc:  # noqa: BLE001 - 画像编译失败如实报错不假成功
            raise ReminderError(
                "profile_unavailable", f"画像切片编译失败：{exc}", 503
            ) from exc

    def _load_profile_slice(self, account_id: str, slice_id: str) -> ProfileSlice:
        if self._profiles is None:
            raise ReminderError("profile_unavailable", "画像服务不可用。", 503)
        try:
            slice_ = self._profiles.get_slice(account_id, slice_id)
            if slice_ is None:
                raise ReminderError(
                    "profile_slice_invalid",
                    "画像切片不存在或不属于当前账户，请重新解析。",
                )
            self._profiles.check_slice_usable(slice_)
        except ProfileError as exc:
            raise ReminderError("profile_slice_invalid", str(exc)) from exc
        return slice_

    def parse(
        self,
        account_id: str,
        raw_text: str,
        *,
        timezone: str,
        use_profile: bool,
        session_id: str | None = None,
    ) -> ParsedReminderPreview:
        """解析自然语言并生成预览（未持久化；确认后调用 create）。"""
        try:
            schedule, subject = parse_reminder_text(
                raw_text, timezone=timezone, now_utc=self._now()
            )
        except (ReminderParseError, ReminderRuleError) as exc:
            raise ReminderError("parse_failed", str(exc)) from exc
        if use_profile:
            token = secrets.token_hex(6)
            slice_ = self._compile_profile_slice(account_id, run_token=token)
            adapted = adapt_reminder_body(subject, list(slice_.included_items))
            profile_usage = ReminderProfileUsage(
                enabled=True,
                categories=adapted.categories,
                item_count=adapted.item_count,
                slice_id=slice_.slice_id,
            )
        else:
            adapted = adapt_reminder_body(subject, [])
            profile_usage = ReminderProfileUsage(enabled=False)
        return ParsedReminderPreview(
            raw_text=raw_text,
            schedule=schedule,
            subject=subject,
            body_preview=adapted.body,
            profile_usage=profile_usage,
            parsed_at=self._now(),
        )

    def _derive_confirmed(
        self,
        account_id: str,
        *,
        raw_text: str,
        timezone: str,
        subject: str,
        schedule: ReminderSchedule,
    ) -> tuple[ReminderSchedule, str]:
        """复核确认载荷与确定性解析结果一致（防篡改，所见即所存）。"""
        try:
            derived, derived_subject = parse_reminder_text(
                raw_text, timezone=timezone, now_utc=self._now()
            )
        except (ReminderParseError, ReminderRuleError) as exc:
            raise ReminderError("parse_failed", str(exc)) from exc
        mismatches: list[str] = []
        if derived.first_run_at != schedule.first_run_at:
            mismatches.append("首次执行时间")
        if derived.repeat is not schedule.repeat:
            mismatches.append("重复规则")
        if derived.repeat_weekdays != schedule.repeat_weekdays:
            mismatches.append("每周星期")
        if derived.repeat_month_day != schedule.repeat_month_day:
            mismatches.append("每月日期")
        if derived_subject != subject:
            mismatches.append("主题")
        if mismatches:
            raise ReminderError(
                "preview_mismatch",
                "确认内容与解析预览不一致（" + "、".join(mismatches) + "），请重新解析。",
            )
        return derived, derived_subject

    def _require_verified_smtp(self, account_id: str) -> None:
        settings = self._settings_row(account_id)
        assert settings is not None
        status = SmtpStatus(settings.get("smtp_status") or SmtpStatus.UNCONFIGURED.value)
        if status is not SmtpStatus.VERIFIED:
            raise ReminderError(
                "smtp_not_verified",
                "请先完成 QQ 邮箱自发自收验证，才能启用邮件提醒。",
                409,
            )

    def _frozen_profile_usage(
        self,
        account_id: str,
        *,
        use_profile: bool,
        slice_id: str | None,
        subject: str,
    ) -> tuple[ReminderProfileUsage, str]:
        """冻结画像措辞快照：按确认时的切片重新派生正文（确定性一致）。"""
        if not use_profile:
            return ReminderProfileUsage(enabled=False), adapt_reminder_body(subject, []).body
        if not slice_id:
            raise ReminderError(
                "profile_slice_invalid", "开启画像适配时必须携带预览返回的切片标识。"
            )
        slice_ = self._load_profile_slice(account_id, slice_id)
        adapted = adapt_reminder_body(subject, list(slice_.included_items))
        usage = ReminderProfileUsage(
            enabled=True,
            categories=adapted.categories,
            item_count=adapted.item_count,
            slice_id=slice_.slice_id,
        )
        return usage, adapted.body

    def create(
        self,
        account_id: str,
        request: ReminderCreateRequest,
        *,
        session_id: str | None = None,
    ) -> ReminderProjection:
        """创建提醒（要求已验证 SMTP；预览内容经确定性复核）。"""
        self._require_verified_smtp(account_id)
        email = self._require_qq_email(account_id)
        schedule, subject = self._derive_confirmed(
            account_id,
            raw_text=request.raw_text,
            timezone=request.schedule.timezone,
            subject=request.subject,
            schedule=request.schedule,
        )
        usage, body = self._frozen_profile_usage(
            account_id,
            use_profile=request.use_profile,
            slice_id=request.profile_slice_id,
            subject=subject,
        )
        now = self._now()
        reminder_id = f"rem-{secrets.token_hex(8)}"
        with self._database.transaction():
            scoped = self._database.scoped(account_id)
            scoped.execute(
                "INSERT INTO reminders ("
                " reminder_id, account_id, qq_email, timezone, raw_text,"
                " schedule_json, subject, body, use_profile, profile_slice_id,"
                " profile_categories, profile_item_count, status, next_run_at,"
                " created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'enabled', ?, ?, ?)",
                (
                    reminder_id, account_id, email, schedule.timezone, request.raw_text,
                    schedule.model_dump_json(), subject, body,
                    1 if request.use_profile else 0,
                    usage.slice_id, json.dumps(usage.categories, ensure_ascii=False),
                    usage.item_count, _encode_dt(schedule.first_run_at),
                    _encode_dt(now), _encode_dt(now),
                ),
            )
        self._audit(
            account_id=account_id,
            action=AuditAction.REMINDER_CREATE,
            result=AuditResult.SUCCESS,
            details={
                "reminder_id": reminder_id,
                "repeat": schedule.repeat.value,
                "use_profile": request.use_profile,
                "category_count": usage.item_count,
            },
            session_id=session_id,
        )
        return self.get_reminder(account_id, reminder_id)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def _row_to_projection(
        self, account_id: str, row: Any
    ) -> ReminderProjection:
        schedule = ReminderSchedule.model_validate_json(str(row["schedule_json"]))
        last = self._last_delivery(account_id, str(row["reminder_id"]))
        return ReminderProjection(
            reminder_id=str(row["reminder_id"]),
            qq_email=str(row["qq_email"]),
            timezone=str(row["timezone"]),
            raw_text=str(row["raw_text"]),
            subject=str(row["subject"]),
            body=str(row["body"]),
            schedule=schedule,
            status=ReminderStatus(str(row["status"])),
            pause_reason=row["pause_reason"],
            profile_usage=ReminderProfileUsage(
                enabled=bool(row["use_profile"]),
                categories=json.loads(str(row["profile_categories"] or "[]")),
                item_count=int(row["profile_item_count"]),
                slice_id=row["profile_slice_id"],
            ),
            next_run_at=_decode_dt(row["next_run_at"]),
            next_retry_at=_decode_dt(row["next_retry_at"]),
            retry_count=int(row["retry_count"]),
            last_delivery=last,
            created_at=_decode_dt(row["created_at"]) or self._now(),
            updated_at=_decode_dt(row["updated_at"]) or self._now(),
        )

    def _last_delivery(
        self, account_id: str, reminder_id: str
    ) -> ReminderDeliveryProjection | None:
        scoped = self._database.scoped(account_id)
        row = scoped.execute(
            "SELECT * FROM reminder_deliveries"
            " WHERE account_id = ? AND reminder_id = ?"
            " ORDER BY attempted_at DESC LIMIT 1",
            (account_id, reminder_id),
        ).fetchone()
        if row is None:
            return None
        return self._delivery_projection(row)

    def _delivery_projection(self, row: Any) -> ReminderDeliveryProjection:
        return ReminderDeliveryProjection(
            delivery_id=str(row["delivery_id"]),
            reminder_id=str(row["reminder_id"]),
            kind=ReminderDeliveryKind(str(row["kind"])),
            outcome=ReminderDeliveryOutcome(str(row["outcome"])),
            scheduled_for=_decode_dt(row["scheduled_for"]) or self._now(),
            attempted_at=_decode_dt(row["attempted_at"]),
            delayed=bool(row["delayed"]),
            error_code=row["error_code"],
            error_message=row["error_message"],
            message_id=row["message_id"],
        )

    def list_reminders(self, account_id: str) -> list[ReminderProjection]:
        scoped = self._database.scoped(account_id)
        rows = scoped.execute(
            "SELECT * FROM reminders WHERE account_id = ?"
            " ORDER BY created_at DESC, reminder_id DESC",
            (account_id,),
        ).fetchall()
        return [self._row_to_projection(account_id, row) for row in rows]

    def get_reminder(self, account_id: str, reminder_id: str) -> ReminderProjection:
        scoped = self._database.scoped(account_id)
        row = scoped.execute(
            "SELECT * FROM reminders WHERE account_id = ? AND reminder_id = ?",
            (account_id, reminder_id),
        ).fetchone()
        if row is None:
            raise ReminderError("reminder_not_found", "提醒不存在或不属于当前账户。", 404)
        return self._row_to_projection(account_id, row)

    def _require_reminder_row(self, account_id: str, reminder_id: str) -> dict[str, Any]:
        scoped = self._database.scoped(account_id)
        row = scoped.execute(
            "SELECT * FROM reminders WHERE account_id = ? AND reminder_id = ?",
            (account_id, reminder_id),
        ).fetchone()
        if row is None:
            raise ReminderError("reminder_not_found", "提醒不存在或不属于当前账户。", 404)
        return dict(row)

    # ------------------------------------------------------------------
    # 编辑 / 暂停 / 恢复 / 取消 / 手动补发
    # ------------------------------------------------------------------

    def update(
        self,
        account_id: str,
        reminder_id: str,
        request: ReminderUpdateRequest,
        *,
        session_id: str | None = None,
    ) -> ReminderProjection:
        row = self._require_reminder_row(account_id, reminder_id)
        status = ReminderStatus(str(row["status"]))
        if status not in {ReminderStatus.ENABLED, ReminderStatus.PAUSED}:
            raise ReminderError(
                "reminder_not_editable",
                "已取消或已完成的提醒不能编辑，请创建新提醒。",
                409,
            )
        schedule, subject = self._derive_confirmed(
            account_id,
            raw_text=request.raw_text,
            timezone=request.schedule.timezone,
            subject=request.subject,
            schedule=request.schedule,
        )
        usage, body = self._frozen_profile_usage(
            account_id,
            use_profile=request.use_profile,
            slice_id=request.profile_slice_id,
            subject=subject,
        )
        now = self._now()
        with self._database.transaction():
            scoped = self._database.scoped(account_id)
            scoped.execute(
                "UPDATE reminders SET"
                " timezone = ?, raw_text = ?, schedule_json = ?, subject = ?,"
                " body = ?, use_profile = ?, profile_slice_id = ?,"
                " profile_categories = ?, profile_item_count = ?,"
                " next_run_at = ?, next_retry_at = NULL, retry_count = 0,"
                " updated_at = ?"
                " WHERE account_id = ? AND reminder_id = ?",
                (
                    schedule.timezone, request.raw_text, schedule.model_dump_json(),
                    subject, body, 1 if request.use_profile else 0, usage.slice_id,
                    json.dumps(usage.categories, ensure_ascii=False), usage.item_count,
                    _encode_dt(schedule.first_run_at), _encode_dt(now),
                    account_id, reminder_id,
                ),
            )
        self._audit(
            account_id=account_id,
            action=AuditAction.REMINDER_UPDATE,
            result=AuditResult.SUCCESS,
            details={
                "reminder_id": reminder_id,
                "repeat": schedule.repeat.value,
                "use_profile": request.use_profile,
                "category_count": usage.item_count,
            },
            session_id=session_id,
        )
        return self.get_reminder(account_id, reminder_id)

    def pause(
        self,
        account_id: str,
        reminder_id: str,
        *,
        session_id: str | None = None,
    ) -> ReminderProjection:
        row = self._require_reminder_row(account_id, reminder_id)
        if ReminderStatus(str(row["status"])) is not ReminderStatus.ENABLED:
            raise ReminderError(
                "reminder_not_pausable", "只有启用中的提醒可以暂停。", 409
            )
        now = self._now()
        with self._database.transaction():
            self._database.scoped(account_id).execute(
                "UPDATE reminders SET status = 'paused', pause_reason = ?,"
                " updated_at = ? WHERE account_id = ? AND reminder_id = ?",
                ("用户已手动暂停", _encode_dt(now), account_id, reminder_id),
            )
        self._audit(
            account_id=account_id,
            action=AuditAction.REMINDER_PAUSE,
            result=AuditResult.SUCCESS,
            details={"reminder_id": reminder_id},
            session_id=session_id,
        )
        return self.get_reminder(account_id, reminder_id)

    def resume(
        self,
        account_id: str,
        reminder_id: str,
        *,
        session_id: str | None = None,
    ) -> ReminderProjection:
        row = self._require_reminder_row(account_id, reminder_id)
        if ReminderStatus(str(row["status"])) is not ReminderStatus.PAUSED:
            raise ReminderError("reminder_not_resumable", "只有已暂停的提醒可以恢复。", 409)
        self._require_verified_smtp(account_id)
        now = self._now()
        with self._database.transaction():
            self._database.scoped(account_id).execute(
                "UPDATE reminders SET status = 'enabled', pause_reason = NULL,"
                " updated_at = ? WHERE account_id = ? AND reminder_id = ?",
                (_encode_dt(now), account_id, reminder_id),
            )
        self._audit(
            account_id=account_id,
            action=AuditAction.REMINDER_RESUME,
            result=AuditResult.SUCCESS,
            details={"reminder_id": reminder_id},
            session_id=session_id,
        )
        return self.get_reminder(account_id, reminder_id)

    def cancel(
        self,
        account_id: str,
        reminder_id: str,
        *,
        session_id: str | None = None,
    ) -> ReminderProjection:
        row = self._require_reminder_row(account_id, reminder_id)
        if ReminderStatus(str(row["status"])) is ReminderStatus.CANCELLED:
            raise ReminderError("reminder_not_cancellable", "该提醒已取消。", 409)
        now = self._now()
        with self._database.transaction():
            self._database.scoped(account_id).execute(
                "UPDATE reminders SET status = 'cancelled',"
                " updated_at = ? WHERE account_id = ? AND reminder_id = ?",
                (_encode_dt(now), account_id, reminder_id),
            )
        self._audit(
            account_id=account_id,
            action=AuditAction.REMINDER_CANCEL,
            result=AuditResult.SUCCESS,
            details={"reminder_id": reminder_id},
            session_id=session_id,
        )
        return self.get_reminder(account_id, reminder_id)

    def send_now(
        self,
        account_id: str,
        reminder_id: str,
        *,
        session_id: str | None = None,
    ) -> ReminderDeliveryProjection:
        """手动补发：立即发送当前提醒内容，不改变既有日程。"""
        row = self._require_reminder_row(account_id, reminder_id)
        if ReminderStatus(str(row["status"])) is ReminderStatus.CANCELLED:
            raise ReminderError("reminder_not_sendable", "已取消的提醒不能手动补发。", 409)
        self._require_verified_smtp(account_id)
        email = str(row["qq_email"])
        code = self._load_auth_code(account_id)
        scheduled_for = _decode_dt(row["next_run_at"]) or self._now()
        delivery_id = f"del-{secrets.token_hex(8)}"
        try:
            message_id = self._gateway.send_mail(
                from_addr=email,
                to_addr=email,
                auth_code=code,
                subject=str(row["subject"]),
                body=str(row["body"]),
                headers={"X-BridGes-Reminder-Id": reminder_id},
            )
        except SmtpError as exc:
            self._record_delivery(
                account_id,
                delivery_id=delivery_id,
                reminder_id=reminder_id,
                kind=ReminderDeliveryKind.MANUAL_RETRY,
                outcome=ReminderDeliveryOutcome.FAILED,
                scheduled_for=scheduled_for,
                error_code=exc.code,
                error_message=exc.message,
            )
            self._audit(
                account_id=account_id,
                action=AuditAction.REMINDER_MANUAL_SEND,
                result=AuditResult.BLOCKED,
                details={"reminder_id": reminder_id, "error_code": exc.code},
                session_id=session_id,
            )
            if exc.code == "smtp_auth_failed":
                self._fail_auth(account_id)
            raise ReminderError(
                "send_failed", exc.message, 502 if exc.retryable else 400, exc.retryable
            ) from exc
        self._record_delivery(
            account_id,
            delivery_id=delivery_id,
            reminder_id=reminder_id,
            kind=ReminderDeliveryKind.MANUAL_RETRY,
            outcome=ReminderDeliveryOutcome.SENT,
            scheduled_for=scheduled_for,
            message_id=message_id,
        )
        self._audit(
            account_id=account_id,
            action=AuditAction.REMINDER_MANUAL_SEND,
            result=AuditResult.SUCCESS,
            details={"reminder_id": reminder_id},
            session_id=session_id,
        )
        return self._get_delivery(account_id, delivery_id)

    def list_deliveries(
        self, account_id: str, reminder_id: str
    ) -> list[ReminderDeliveryProjection]:
        self._require_reminder_row(account_id, reminder_id)
        scoped = self._database.scoped(account_id)
        rows = scoped.execute(
            "SELECT * FROM reminder_deliveries"
            " WHERE account_id = ? AND reminder_id = ?"
            " ORDER BY attempted_at DESC, delivery_id DESC LIMIT ?",
            (account_id, reminder_id, _DELIVERIES_LIMIT),
        ).fetchall()
        return [self._delivery_projection(row) for row in rows]

    def _get_delivery(
        self, account_id: str, delivery_id: str
    ) -> ReminderDeliveryProjection:
        scoped = self._database.scoped(account_id)
        row = scoped.execute(
            "SELECT * FROM reminder_deliveries"
            " WHERE account_id = ? AND delivery_id = ?",
            (account_id, delivery_id),
        ).fetchone()
        if row is None:
            raise ReminderError("delivery_not_found", "投递记录不存在。", 404)
        return self._delivery_projection(row)

    def _record_delivery(
        self,
        account_id: str,
        *,
        delivery_id: str,
        reminder_id: str,
        kind: ReminderDeliveryKind,
        outcome: ReminderDeliveryOutcome,
        scheduled_for: datetime,
        error_code: str | None = None,
        error_message: str | None = None,
        message_id: str | None = None,
        delayed: bool = False,
    ) -> None:
        now = self._now()
        with self._database.transaction():
            self._database.scoped(account_id).execute(
                "INSERT INTO reminder_deliveries ("
                " delivery_id, account_id, reminder_id, kind, outcome,"
                " scheduled_for, attempted_at, delayed, error_code,"
                " error_message, message_id"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    delivery_id, account_id, reminder_id, kind.value, outcome.value,
                    _encode_dt(scheduled_for), _encode_dt(now),
                    1 if delayed else 0, error_code, error_message, message_id,
                ),
            )

    def _pause_reminders_without_credential(self, account_id: str) -> None:
        now = self._now()
        with self._database.transaction():
            self._database.scoped(account_id).execute(
                "UPDATE reminders SET status = 'paused', pause_reason = ?,"
                " updated_at = ? WHERE account_id = ? AND status = 'enabled'",
                ("SMTP 授权码已删除，请重新配置并完成验证。", _encode_dt(now), account_id),
            )

    def _fail_auth(self, account_id: str) -> None:
        """授权失效：暂停该账户全部启用提醒 + SMTP 状态 failed。

        AC8「授权失效立即暂停相关提醒」：同一授权码服务于账户全部提醒，
        失效必须整体暂停，不能只停正在投递的一条（其余保持"启用中"会与
        SMTP 验证失败卡片矛盾）。
        """
        now = self._now()
        with self._database.transaction():
            scoped = self._database.scoped(account_id)
            scoped.execute(
                "UPDATE reminders SET status = 'paused', pause_reason = ?,"
                " updated_at = ? WHERE account_id = ? AND status = 'enabled'",
                ("邮箱授权码已失效，请重新验证后再恢复提醒。", _encode_dt(now),
                 account_id),
            )
            scoped.execute(
                "UPDATE reminder_settings SET smtp_status = 'failed',"
                " smtp_error_code = 'smtp_auth_failed',"
                " smtp_error_message = ?, smtp_verified_at = NULL,"
                " smtp_updated_at = ? WHERE account_id = ?",
                ("邮箱授权码已失效，请重新验证。", _encode_dt(now), account_id),
            )

    # ------------------------------------------------------------------
    # 调度（scheduler 进程调用）
    # ------------------------------------------------------------------

    def process_due(self) -> str:
        """处理全部到期提醒并返回中文摘要（供 scheduler 心跳输出）。

        一轮处理：一次性 24 小时补发/错过、重复提醒最多补发最近一次、
        有限退避重试、授权失效立即暂停。可重试失败只影响本提醒，
        不中断循环。
        """
        now = self._now()
        rows = self._database.connection.execute(
            "SELECT * FROM reminders WHERE status = 'enabled'"
            " AND next_run_at IS NOT NULL AND next_run_at <= ?"
            " AND (next_retry_at IS NULL OR next_retry_at <= ?)"
            " ORDER BY next_run_at",
            (_encode_dt(now), _encode_dt(now)),
        ).fetchall()
        sent = failed = skipped = catch_up = 0
        for row in rows:
            account_id = str(row["account_id"])
            reminder_id = str(row["reminder_id"])
            try:
                kind, scheduled_for, delayed = self._plan_attempt(
                    account_id, row, now
                )
            except Exception:  # noqa: BLE001 - 单条调度错误不中断整轮
                failed += 1
                continue
            if kind is None:
                skipped += 1
                continue
            email = str(row["qq_email"])
            try:
                code = self._load_auth_code(account_id)
            except ReminderError as exc:
                self._handle_dispatch_failure(
                    account_id, reminder_id, scheduled_for,
                    code_=exc.code, message=exc.message,
                    kind=kind, delayed=delayed,
                )
                skipped += 1
                continue
            try:
                message_id = self._gateway.send_mail(
                    from_addr=email,
                    to_addr=email,
                    auth_code=code,
                    subject=str(row["subject"]),
                    body=str(row["body"]),
                    headers={"X-BridGes-Reminder-Id": reminder_id},
                )
            except SmtpError as exc:
                self._handle_dispatch_failure(
                    account_id, reminder_id, scheduled_for,
                    code_=exc.code, message=exc.message, kind=kind, delayed=delayed,
                )
                failed += 1
                continue
            self._handle_dispatch_success(
                account_id, row, kind=kind, scheduled_for=scheduled_for,
                delayed=delayed, message_id=message_id, now=now,
            )
            if kind is ReminderDeliveryKind.CATCH_UP:
                catch_up += 1
            sent += 1
        return (
            f"scheduler: 本轮投递 {sent} 个（含补发 {catch_up}），"
            f"失败 {failed}，跳过 {skipped}。"
        )

    def _advance_past(
        self, schedule: ReminderSchedule, *, from_utc: datetime, now_utc: datetime
    ) -> datetime | None:
        """从已计划时刻起按规则推进到第一个严格大于 now_utc 的执行时刻。

        参考时刻必须是规则的合法执行时刻（投递/跳过的计划时刻），这样
        推进保持原有时刻（时分）不漂移；`once` 无下一次。
        """
        if schedule.repeat is ReminderRepeatRule.ONCE:
            return None
        current = from_utc
        for _ in range(1000):
            following = next_occurrence(
                schedule.repeat,
                reference_utc=current,
                timezone=schedule.timezone,
                weekdays=schedule.repeat_weekdays or None,
                month_day=schedule.repeat_month_day,
            )
            if following is None or following > now_utc:
                return following
            current = following
        raise ReminderRuleError("重复规则推进异常，请重新编辑提醒。")

    def _plan_attempt(
        self, account_id: str, row: Any, now: datetime
    ) -> tuple[ReminderDeliveryKind | None, datetime, bool]:
        """决定本次到期处理的方式：正常投递/补发/跳过。

        返回 (kind 或 None=跳过, 计划执行时刻, 是否延迟标记)。一次性
        提醒在 24 小时窗口内补发，超窗记为错过；重复提醒最多补发最近
        一次错过，更早积压跳过并继续下一正常周期。
        """
        schedule = ReminderSchedule.model_validate_json(str(row["schedule_json"]))
        next_run = _decode_dt(row["next_run_at"])
        assert next_run is not None
        # 退避重试不豁免迟到语义：临时失败后停机超过 24 小时再恢复，
        # 重试也必须在窗口内才能补发（ADR-0019：超窗记为错过）。
        is_late = (now - next_run).total_seconds() > LATE_GRACE_SECONDS
        if not is_late:
            return ReminderDeliveryKind.SCHEDULED, next_run, False
        # 迟到：计算最近一次错过（重复提醒向前推进规则直到不超过 now）
        if schedule.repeat is ReminderRepeatRule.ONCE:
            if now - next_run <= self._catch_up_window:
                return ReminderDeliveryKind.CATCH_UP, next_run, True
            self._skip_missed(account_id, str(row["reminder_id"]), next_run, now)
            return None, next_run, False
        occurrence = next_run
        steps = 0
        while steps < 1000:
            following = next_occurrence(
                schedule.repeat,
                reference_utc=occurrence,
                timezone=schedule.timezone,
                weekdays=schedule.repeat_weekdays or None,
                month_day=schedule.repeat_month_day,
            )
            if following is None or following > now:
                break
            occurrence = following
            steps += 1
        if now - occurrence <= self._catch_up_window:
            return ReminderDeliveryKind.CATCH_UP, occurrence, True
        self._skip_missed(account_id, str(row["reminder_id"]), occurrence, now)
        return None, occurrence, False

    def _skip_missed(
        self, account_id: str, reminder_id: str, occurrence: datetime, now: datetime
    ) -> None:
        """跳过错过的执行（24 小时窗口已过），并推进下一次计划。"""
        row = self._require_reminder_row(account_id, reminder_id)
        schedule = ReminderSchedule.model_validate_json(str(row["schedule_json"]))
        next_run = self._advance_past(
            schedule, from_utc=occurrence, now_utc=now
        )
        self._record_delivery(
            account_id,
            delivery_id=f"del-{secrets.token_hex(8)}",
            reminder_id=reminder_id,
            kind=ReminderDeliveryKind.SCHEDULED,
            outcome=ReminderDeliveryOutcome.SKIPPED,
            scheduled_for=occurrence,
            error_code="catch_up_window_exceeded",
            error_message="补发窗口（24 小时）已过，本次提醒记为错过。",
        )
        status = "completed" if next_run is None else "enabled"
        with self._database.transaction():
            self._database.scoped(account_id).execute(
                "UPDATE reminders SET status = ?, next_run_at = ?,"
                " next_retry_at = NULL, retry_count = 0, updated_at = ?"
                " WHERE account_id = ? AND reminder_id = ?",
                (status, _encode_dt(next_run), _encode_dt(now), account_id, reminder_id),
            )
        self._audit(
            account_id=account_id,
            action=AuditAction.REMINDER_DELIVER,
            result=AuditResult.SUCCESS,
            details={
                "reminder_id": reminder_id,
                "kind": ReminderDeliveryKind.SCHEDULED.value,
                "outcome": ReminderDeliveryOutcome.SKIPPED.value,
            },
        )

    def _handle_dispatch_success(
        self,
        account_id: str,
        row: Any,
        *,
        kind: ReminderDeliveryKind,
        scheduled_for: datetime,
        delayed: bool,
        message_id: str,
        now: datetime,
    ) -> None:
        reminder_id = str(row["reminder_id"])
        schedule = ReminderSchedule.model_validate_json(str(row["schedule_json"]))
        # 发送已实际完成，本次执行被消费：无论发送期间是否被暂停/取消，
        # 都推进下一次计划，避免恢复后同一执行时刻重复投递；状态只在
        # 仍启用时更新（暂停/取消保留其状态，恢复后从下一次开始）。
        next_run = self._advance_past(
            schedule, from_utc=scheduled_for, now_utc=now
        )
        with self._database.transaction():
            self._database.scoped(account_id).execute(
                "UPDATE reminders SET next_run_at = ?,"
                " next_retry_at = NULL, retry_count = 0, updated_at = ?"
                " WHERE account_id = ? AND reminder_id = ?",
                (_encode_dt(next_run), _encode_dt(now), account_id, reminder_id),
            )
            if next_run is None:
                # 一次性提醒：本次执行后自然完成（无论是否仍启用）
                self._database.scoped(account_id).execute(
                    "UPDATE reminders SET status = 'completed'"
                    " WHERE account_id = ? AND reminder_id = ? AND status = 'enabled'",
                    (account_id, reminder_id),
                )
        self._record_delivery(
            account_id,
            delivery_id=f"del-{secrets.token_hex(8)}",
            reminder_id=reminder_id,
            kind=kind, outcome=ReminderDeliveryOutcome.SENT,
            scheduled_for=scheduled_for, message_id=message_id, delayed=delayed,
        )
        action = (
            AuditAction.REMINDER_CATCH_UP
            if kind is ReminderDeliveryKind.CATCH_UP
            else AuditAction.REMINDER_DELIVER
        )
        self._audit(
            account_id=account_id,
            action=action,
            result=AuditResult.SUCCESS,
            details={
                "reminder_id": reminder_id,
                "kind": kind.value,
                "outcome": ReminderDeliveryOutcome.SENT.value,
                "delayed": delayed,
            },
        )

    def _handle_dispatch_failure(
        self,
        account_id: str,
        reminder_id: str,
        scheduled_for: datetime,
        *,
        code_: str,
        message: str,
        kind: ReminderDeliveryKind,
        delayed: bool,
    ) -> None:
        """投递失败：授权失效立即暂停；临时失败有限退避重试。"""
        now = self._now()
        row = self._require_reminder_row(account_id, reminder_id)
        if code_ in {"smtp_auth_failed"}:
            self._record_delivery(
                account_id,
                delivery_id=f"del-{secrets.token_hex(8)}",
                reminder_id=reminder_id,
                kind=kind, outcome=ReminderDeliveryOutcome.FAILED,
                scheduled_for=scheduled_for,
                error_code=code_, error_message=message, delayed=delayed,
            )
            self._fail_auth(account_id)
            self._audit(
                account_id=account_id,
                action=AuditAction.REMINDER_DELIVER,
                result=AuditResult.BLOCKED,
                details={
                    "reminder_id": reminder_id,
                    "kind": kind.value,
                    "outcome": ReminderDeliveryOutcome.FAILED.value,
                    "error_code": code_,
                },
            )
            return
        if code_ == "smtp_not_configured":
            self._record_delivery(
                account_id,
                delivery_id=f"del-{secrets.token_hex(8)}",
                reminder_id=reminder_id,
                kind=kind, outcome=ReminderDeliveryOutcome.SKIPPED,
                scheduled_for=scheduled_for,
                error_code=code_, error_message=message, delayed=delayed,
            )
            self._pause_missing_credential(account_id, reminder_id, message)
            return
        # 临时/其他失败：有限退避重试（退避公式单一实现于
        # runtime.queue，Issue 43 统一契约；提醒的补发窗口、重复任务
        # 最近一次补发、窗口外记错过等业务语义保留在本域）。
        retry_count = int(row["retry_count"]) + 1
        exhausted = retry_count >= self._max_send_attempts
        next_retry_at: datetime | None = None
        if not exhausted:
            backoff = TaskQueue.compute_backoff(
                RetryKind.EXPONENTIAL,
                retry_count,
                base_seconds=_RETRY_BACKOFF_SECONDS,
                cap_seconds=_RETRY_CAP_SECONDS,
            )
            next_retry_at = now + timedelta(seconds=backoff)
        self._record_delivery(
            account_id,
            delivery_id=f"del-{secrets.token_hex(8)}",
            reminder_id=reminder_id,
            kind=kind, outcome=ReminderDeliveryOutcome.FAILED,
            scheduled_for=scheduled_for,
            error_code=code_, error_message=message, delayed=delayed,
        )
        if exhausted:
            # 重试预算耗尽：一次性提醒暂停待手动补发；重复提醒跳过本次
            schedule = ReminderSchedule.model_validate_json(str(row["schedule_json"]))
            if schedule.repeat is ReminderRepeatRule.ONCE:
                with self._database.transaction():
                    self._database.scoped(account_id).execute(
                        "UPDATE reminders SET status = 'paused',"
                        " pause_reason = ?, next_retry_at = NULL,"
                        " updated_at = ? WHERE account_id = ? AND reminder_id = ?",
                        ("邮件发送多次失败，已暂停；可手动补发或稍后恢复。",
                         _encode_dt(now), account_id, reminder_id),
                    )
            else:
                following = self._advance_past(
                    schedule, from_utc=scheduled_for, now_utc=now
                )
                with self._database.transaction():
                    self._database.scoped(account_id).execute(
                        "UPDATE reminders SET next_run_at = ?, next_retry_at = NULL,"
                        " retry_count = 0, updated_at = ?"
                        " WHERE account_id = ? AND reminder_id = ?",
                        (_encode_dt(following), _encode_dt(now), account_id, reminder_id),
                    )
        else:
            with self._database.transaction():
                self._database.scoped(account_id).execute(
                    "UPDATE reminders SET retry_count = ?, next_retry_at = ?,"
                    " updated_at = ? WHERE account_id = ? AND reminder_id = ?",
                    (retry_count, _encode_dt(next_retry_at), _encode_dt(now),
                     account_id, reminder_id),
                )
        self._audit(
            account_id=account_id,
            action=AuditAction.REMINDER_DELIVER,
            result=AuditResult.RETRYABLE_FAIL,
            details={
                "reminder_id": reminder_id,
                "kind": kind.value,
                "outcome": ReminderDeliveryOutcome.FAILED.value,
                "error_code": code_,
                "retry_count": retry_count,
            },
        )

    def _pause_missing_credential(
        self, account_id: str, reminder_id: str, message: str
    ) -> None:
        now = self._now()
        with self._database.transaction():
            self._database.scoped(account_id).execute(
                "UPDATE reminders SET status = 'paused', pause_reason = ?,"
                " updated_at = ? WHERE account_id = ? AND reminder_id = ?",
                (message, _encode_dt(now), account_id, reminder_id),
            )


__all__ = [
    "CATCH_UP_WINDOW",
    "LATE_GRACE_SECONDS",
    "MAX_SEND_ATTEMPTS",
    "ReminderService",
]
