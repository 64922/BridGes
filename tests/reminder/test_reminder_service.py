"""QQ SMTP 任务提醒服务测试（Issue 33，可控时钟 + 可编程网关）。

覆盖：SMTP 配置状态机（保存/验证/失败/删除）；未验证禁止创建；解析
预览与确认复核（防篡改）；画像措辞冻结；暂停/恢复/取消/编辑/手动补发；
调度核心（24 小时补发/错过、重复提醒最多补发最近一次、有限退避重试、
授权失效立即暂停）；两账户隔离；重启一致（重建服务读库）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from bridges.contracts.profiles import (
    ProfileSlice,
    ProfileSliceItem,
)
from bridges.contracts.reminder import (
    ReminderCreateRequest,
    ReminderDeliveryKind,
    ReminderDeliveryOutcome,
    ReminderError,
    ReminderSettingsUpdateRequest,
    ReminderStatus,
    ReminderUpdateRequest,
    SmtpStatus,
)
from bridges.credentials.store import InMemoryCredentialStore
from bridges.reminder.service import ReminderService
from bridges.reminder.smtp import SmtpError
from bridges.storage.database import BridgesDatabase

EMAIL = "123456@qq.com"
AUTH_CODE = "ABCDEFGHIJKLMNOP"

T0 = datetime(2026, 8, 5, 2, 0, tzinfo=UTC)  # 北京 10:00 周三


class _Clock:
    """可控时钟：测试推进 time 字段。"""

    def __init__(self, start: datetime) -> None:
        self.time = start

    def advance(self, **delta: float) -> None:
        self.time = self.time + timedelta(**delta)

    def __call__(self) -> datetime:
        return self.time


class _ProgrammableGateway:
    """可编程邮件网关：记录发送并可注入连续失败（fail_times 次）。"""

    def __init__(
        self,
        *,
        send_error: SmtpError | None = None,
        verify_error: Exception | None = None,
        on_send: Any = None,
    ) -> None:
        self._send_error = send_error
        self.fail_times = 1 if send_error is not None else 0
        self.verify_error = verify_error
        self.on_send = on_send
        self.sends: list[dict[str, Any]] = []

    @property
    def send_error(self) -> SmtpError | None:
        return self._send_error

    @send_error.setter
    def send_error(self, value: SmtpError | None) -> None:
        self._send_error = value
        self.fail_times = 1 if value is not None else 0

    def send_mail(self, **kwargs: Any) -> str:
        self.sends.append(kwargs)
        if self._send_error is not None and self.fail_times > 0:
            self.fail_times -= 1
            raise self._send_error
        if self.on_send is not None:
            self.on_send()
        return f"<bridges-{len(self.sends)}@test>"

    def verify_self_send_receive(self, *, email: str, auth_code: str) -> str:
        if self.verify_error is not None:
            error = self.verify_error
            self.verify_error = None
            raise error
        self.send_mail(
            from_addr=email,
            to_addr=email,
            auth_code=auth_code,
            subject="BridGes 验证邮件",
            body="验证正文",
            message_id="<bridges-verify@test>",
        )
        return "<bridges-verify@test>"


class _StubProfileService:
    """画像服务替身：返回固定切片，验证编译调用。"""

    def __init__(
        self,
        items: list[ProfileSliceItem] | None = None,
        *,
        unusable_reason: str | None = None,
    ) -> None:
        self.items = items or []
        self.unusable_reason = unusable_reason
        self.compile_calls: list[dict[str, Any]] = []
        self._slices: dict[str, ProfileSlice] = {}
        self.compiled_slice: ProfileSlice | None = None

    def compile_chat_slice(
        self, account_id: str, *, mode: str, run_id: str, **_: Any
    ) -> ProfileSlice:
        self.compile_calls.append({"account_id": account_id, "mode": mode, "run_id": run_id})
        slice_ = ProfileSlice(
            slice_id=f"slice-{len(self.compile_calls)}",
            owner_account_id=account_id,
            run_id=run_id,
            purpose=mode,
            included_items=self.items,
            compiled_at=datetime.now(UTC),
        )
        self._slices[slice_.slice_id] = slice_
        self.compiled_slice = slice_
        return slice_

    def get_slice(self, account_id: str, slice_id: str) -> ProfileSlice | None:
        slice_ = self._slices.get(slice_id)
        if slice_ is None or slice_.owner_account_id != account_id:
            return None
        return slice_

    def check_slice_usable(self, slice_: ProfileSlice) -> None:
        if self.unusable_reason is not None:
            from bridges.profiles.adapters import ProfileError

            raise ProfileError(self.unusable_reason)


class _RecordingObservability:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def log_audit(self, **kwargs: Any) -> None:
        self.events.append(kwargs)


def _greeting_item() -> ProfileSliceItem:
    return ProfileSliceItem(
        assertion_id="a1",
        dimension="basic_information",
        value_or_rule="称呼我为小谷",
        inclusion_reason="r",
        sensitivity_class="preference",
    )


class _Harness:
    """组装内存库 + 可控时钟 + 可编程网关 + 画像替身。"""

    def __init__(
        self,
        *,
        items: list[ProfileSliceItem] | None = None,
        unusable_reason: str | None = None,
    ) -> None:
        self.clock = _Clock(T0)
        self.database = BridgesDatabase(":memory:")
        self.database.initialize()
        self.credentials = InMemoryCredentialStore(namespace="smtp")
        self.gateway = _ProgrammableGateway()
        self.observability = _RecordingObservability()
        self.profiles = _StubProfileService(items=items, unusable_reason=unusable_reason)
        self.service = self._build()
        self.emails: dict[str, str] = {}

    def _build(self) -> ReminderService:
        return ReminderService(
            database=self.database,
            credential_store=self.credentials,
            observability_service=self.observability,  # type: ignore[arg-type]
            qq_email_provider=lambda account_id: self.emails.get(account_id, EMAIL),
            profile_service=self.profiles,  # type: ignore[arg-type]
            gateway=self.gateway,  # type: ignore[arg-type]
            clock=self.clock,
            verify_async=False,
        )

    def rebuild(self) -> ReminderService:
        """重建服务（模拟重启）：共享数据库与凭据，状态保持一致。"""
        return self._build()

    def save_and_verify(self, account_id: str = "acc1") -> None:
        projection = self.service.save_smtp_code(
            account_id, AUTH_CODE, session_id="s1"
        )
        assert projection.status is SmtpStatus.VERIFIED

    def create_daily(self, account_id: str = "acc1") -> str:
        """创建「每天早上八点提醒我喝水」（首跑为次日 00:00 UTC）。"""
        preview = self.service.parse(
            account_id, "每天早上八点提醒我喝水", timezone="Asia/Shanghai",
            use_profile=False,
        )
        reminder = self.service.create(
            account_id,
            ReminderCreateRequest(
                raw_text="每天早上八点提醒我喝水",
                schedule=preview.schedule,
                subject=preview.subject,
            ),
            session_id="s1",
        )
        return reminder.reminder_id


def test_smtp_save_verifies_and_projections() -> None:
    """保存授权码 → 自发自收验证通过 → verified；投影不含授权码。"""
    harness = _Harness()
    projection = harness.service.save_smtp_code("acc1", AUTH_CODE, session_id="s1")
    assert projection.status is SmtpStatus.VERIFIED
    assert projection.qq_email == EMAIL
    assert projection.error_message is None
    assert "授权码" not in projection.model_dump_json()
    assert len(harness.gateway.sends) == 1  # 验证测试邮件已发送


def test_smtp_save_rejects_password_shaped_input() -> None:
    """QQ 登录密码形态（含特殊字符）被拒绝。"""
    harness = _Harness()
    with pytest.raises(ReminderError) as exc:
        harness.service.save_smtp_code("acc1", "MyP@ssw0rd!123", session_id="s1")
    assert exc.value.code == "invalid_auth_code"
    assert "登录密码" in exc.value.message


def test_smtp_verify_failure_records_reason() -> None:
    """验证失败 → failed + 中文原因 + 重新验证路径（可重验成功）。"""
    harness = _Harness()
    harness.gateway.verify_error = SmtpError(
        "smtp_auth_failed", "授权码无效或已失效。"
    )
    projection = harness.service.save_smtp_code("acc1", AUTH_CODE, session_id="s1")
    assert projection.status is SmtpStatus.FAILED
    assert projection.error_code == "smtp_auth_failed"
    assert "授权码" in (projection.error_message or "")
    # 重新验证成功 → verified
    projection2 = harness.service.verify_smtp_now("acc1", session_id="s1")
    assert projection2.status is SmtpStatus.VERIFIED


def test_smtp_delete_resets_and_pauses_enabled_reminders() -> None:
    """删除授权码 → unconfigured + 启用中提醒暂停。"""
    harness = _Harness()
    harness.save_and_verify()
    reminder_id = harness.create_daily()
    harness.service.delete_smtp_code("acc1", session_id="s1")
    reminder = harness.service.get_reminder("acc1", reminder_id)
    assert reminder.status is ReminderStatus.PAUSED
    assert "授权码已删除" in (reminder.pause_reason or "")
    projection = harness.service.get_smtp_settings("acc1")
    assert projection.status is SmtpStatus.UNCONFIGURED


def test_create_requires_verified_smtp() -> None:
    """未验证 SMTP 的账户不能创建提醒。"""
    harness = _Harness()
    preview = harness.service.parse(
        "acc1", "明天早上八点提醒我复习", timezone="Asia/Shanghai", use_profile=False
    )
    with pytest.raises(ReminderError) as exc:
        harness.service.create(
            "acc1",
            ReminderCreateRequest(
                raw_text="明天早上八点提醒我复习",
                schedule=preview.schedule,
                subject=preview.subject,
            ),
        )
    assert exc.value.code == "smtp_not_verified"


def test_create_rejects_tampered_confirmation() -> None:
    """确认载荷与解析结果不一致（篡改首跑时间）→ preview_mismatch。"""
    harness = _Harness()
    harness.save_and_verify()
    preview = harness.service.parse(
        "acc1", "明天早上八点提醒我复习", timezone="Asia/Shanghai", use_profile=False
    )
    forged = preview.schedule.model_copy(
        update={"first_run_at": preview.schedule.first_run_at + timedelta(hours=1)}
    )
    with pytest.raises(ReminderError) as exc:
        harness.service.create(
            "acc1",
            ReminderCreateRequest(
                raw_text="明天早上八点提醒我复习",
                schedule=forged,
                subject=preview.subject,
            ),
        )
    assert exc.value.code == "preview_mismatch"
    assert "首次执行时间" in exc.value.message


def test_create_frozen_profile_snapshot() -> None:
    """画像适配：预览披露类别 → 创建冻结快照；正文含称呼。"""
    harness = _Harness(items=[_greeting_item()])
    harness.save_and_verify()
    preview = harness.service.parse(
        "acc1", "明天早上八点提醒我复习 transformer",
        timezone="Asia/Shanghai", use_profile=True,
    )
    assert preview.profile_usage.enabled
    assert "基本偏好" in preview.profile_usage.categories
    assert preview.profile_usage.slice_id is not None
    assert "你好，小谷" in preview.body_preview
    reminder = harness.service.create(
        "acc1",
        ReminderCreateRequest(
            raw_text="明天早上八点提醒我复习 transformer",
            schedule=preview.schedule,
            subject=preview.subject,
            use_profile=True,
            profile_slice_id=preview.profile_usage.slice_id,
        ),
        session_id="s1",
    )
    assert reminder.profile_usage.enabled
    assert reminder.profile_usage.categories == ["基本偏好"]
    assert "你好，小谷" in reminder.body
    assert reminder.qq_email == EMAIL


def test_create_without_profile_plain_body() -> None:
    """关闭画像适配：正文无个性化措辞。"""
    harness = _Harness(items=[_greeting_item()])
    harness.save_and_verify()
    preview = harness.service.parse(
        "acc1", "明天早上八点提醒我复习", timezone="Asia/Shanghai", use_profile=False
    )
    assert not preview.profile_usage.enabled
    assert "你好" not in preview.body_preview
    reminder = harness.service.create(
        "acc1",
        ReminderCreateRequest(
            raw_text="明天早上八点提醒我复习",
            schedule=preview.schedule,
            subject=preview.subject,
        ),
    )
    assert not reminder.profile_usage.enabled


def test_pause_resume_cancel_lifecycle() -> None:
    """暂停/恢复/取消生命周期；恢复要求已验证 SMTP。"""
    harness = _Harness()
    harness.save_and_verify()
    reminder_id = harness.create_daily()
    assert harness.service.get_reminder("acc1", reminder_id).status is ReminderStatus.ENABLED

    paused = harness.service.pause("acc1", reminder_id, session_id="s1")
    assert paused.status is ReminderStatus.PAUSED
    with pytest.raises(ReminderError) as exc:
        harness.service.pause("acc1", reminder_id)
    assert exc.value.code == "reminder_not_pausable"

    harness.service.delete_smtp_code("acc1")
    with pytest.raises(ReminderError) as exc:
        harness.service.resume("acc1", reminder_id)
    assert exc.value.code == "smtp_not_verified"

    harness.save_and_verify()
    resumed = harness.service.resume("acc1", reminder_id, session_id="s1")
    assert resumed.status is ReminderStatus.ENABLED
    assert resumed.pause_reason is None

    cancelled = harness.service.cancel("acc1", reminder_id, session_id="s1")
    assert cancelled.status is ReminderStatus.CANCELLED
    with pytest.raises(ReminderError):
        harness.service.cancel("acc1", reminder_id)


def test_update_resets_schedule_keeps_deliveries() -> None:
    """编辑提醒：重置日程与冻结快照，保留投递记录。"""
    harness = _Harness()
    harness.save_and_verify()
    reminder_id = harness.create_daily()
    harness.clock.advance(hours=26)
    harness.service.process_due()  # 产生投递记录
    assert len(harness.service.list_deliveries("acc1", reminder_id)) >= 1

    preview = harness.service.parse(
        "acc1", "明天晚上九点提醒我做总结", timezone="Asia/Shanghai", use_profile=False
    )
    updated = harness.service.update(
        "acc1",
        reminder_id,
        ReminderUpdateRequest(
            raw_text="明天晚上九点提醒我做总结",
            schedule=preview.schedule,
            subject=preview.subject,
        ),
        session_id="s1",
    )
    assert updated.subject == "做总结"
    assert updated.status is ReminderStatus.ENABLED
    assert updated.next_run_at == preview.schedule.first_run_at
    assert updated.retry_count == 0
    assert len(harness.service.list_deliveries("acc1", reminder_id)) >= 1


def test_send_now_manual_retry() -> None:
    """手动补发：立即投递（manual_retry/sent），不改变日程。"""
    harness = _Harness()
    harness.save_and_verify()
    reminder_id = harness.create_daily()
    before = harness.service.get_reminder("acc1", reminder_id)
    delivery = harness.service.send_now("acc1", reminder_id, session_id="s1")
    assert delivery.kind is ReminderDeliveryKind.MANUAL_RETRY
    assert delivery.outcome is ReminderDeliveryOutcome.SENT
    assert not delivery.delayed
    after = harness.service.get_reminder("acc1", reminder_id)
    assert after.next_run_at == before.next_run_at


def test_send_now_auth_failure_pauses() -> None:
    """手动补发遇授权失效 → 失败记录 + 提醒暂停 + SMTP failed。"""
    harness = _Harness()
    harness.save_and_verify()
    reminder_id = harness.create_daily()
    harness.gateway.send_error = SmtpError("smtp_auth_failed", "授权码已失效。")
    with pytest.raises(ReminderError) as exc:
        harness.service.send_now("acc1", reminder_id, session_id="s1")
    assert exc.value.code == "send_failed"
    reminder = harness.service.get_reminder("acc1", reminder_id)
    assert reminder.status is ReminderStatus.PAUSED
    assert "授权码已失效" in (reminder.pause_reason or "")
    assert harness.service.get_smtp_settings("acc1").status is SmtpStatus.FAILED


def test_once_reminder_sent_on_time_completes() -> None:
    """一次性提醒按时投递 → sent + completed，无延迟标记。"""
    harness = _Harness()
    harness.save_and_verify()
    preview = harness.service.parse(
        "acc1", "明天早上八点提醒我复习", timezone="Asia/Shanghai", use_profile=False
    )
    reminder = harness.service.create(
        "acc1",
        ReminderCreateRequest(
            raw_text="明天早上八点提醒我复习",
            schedule=preview.schedule,
            subject=preview.subject,
        ),
    )
    # 时钟推进到计划时刻本身（不在补发窗口内）
    harness.clock.time = datetime(2026, 8, 6, 0, 0, tzinfo=UTC)
    summary = harness.service.process_due()
    assert "投递 1 个" in summary
    reminder = harness.service.get_reminder("acc1", reminder.reminder_id)
    assert reminder.status is ReminderStatus.COMPLETED
    assert reminder.next_run_at is None
    deliveries = harness.service.list_deliveries("acc1", reminder.reminder_id)
    assert deliveries[0].kind is ReminderDeliveryKind.SCHEDULED
    assert deliveries[0].outcome is ReminderDeliveryOutcome.SENT
    assert not deliveries[0].delayed
    assert deliveries[0].message_id is not None
    sent = harness.gateway.sends[1]  # sends[0] 是验证邮件
    assert sent["from_addr"] == EMAIL and sent["to_addr"] == EMAIL  # 自发
    assert sent["subject"] == "复习"
    assert "X-BridGes-Reminder-Id" in sent["headers"]


def test_once_reminder_caught_up_within_24h() -> None:
    """一次性提醒错过但 24 小时内 → 补发 + delayed + completed。"""
    harness = _Harness()
    harness.save_and_verify()
    preview = harness.service.parse(
        "acc1", "明天早上八点提醒我复习", timezone="Asia/Shanghai", use_profile=False
    )
    reminder = harness.service.create(
        "acc1",
        ReminderCreateRequest(
            raw_text="明天早上八点提醒我复习",
            schedule=preview.schedule,
            subject=preview.subject,
        ),
    )
    harness.clock.advance(hours=30)  # 错过 6 小时
    harness.service.process_due()
    reminder = harness.service.get_reminder("acc1", reminder.reminder_id)
    assert reminder.status is ReminderStatus.COMPLETED
    deliveries = harness.service.list_deliveries("acc1", reminder.reminder_id)
    assert deliveries[0].kind is ReminderDeliveryKind.CATCH_UP
    assert deliveries[0].outcome is ReminderDeliveryOutcome.SENT
    assert deliveries[0].delayed


def test_once_reminder_missed_after_window() -> None:
    """一次性提醒错过超过 24 小时 → 跳过记为错过 + completed。"""
    harness = _Harness()
    harness.save_and_verify()
    preview = harness.service.parse(
        "acc1", "明天早上八点提醒我复习", timezone="Asia/Shanghai", use_profile=False
    )
    reminder = harness.service.create(
        "acc1",
        ReminderCreateRequest(
            raw_text="明天早上八点提醒我复习",
            schedule=preview.schedule,
            subject=preview.subject,
        ),
    )
    harness.clock.advance(hours=50)
    harness.service.process_due()
    reminder = harness.service.get_reminder("acc1", reminder.reminder_id)
    assert reminder.status is ReminderStatus.COMPLETED
    deliveries = harness.service.list_deliveries("acc1", reminder.reminder_id)
    assert deliveries[0].outcome is ReminderDeliveryOutcome.SKIPPED
    assert deliveries[0].error_code == "catch_up_window_exceeded"


def test_daily_missed_twice_catches_up_most_recent() -> None:
    """重复提醒错过两次：只补发最近一次（delayed），更早积压跳过。"""
    harness = _Harness()
    harness.save_and_verify()
    reminder_id = harness.create_daily()  # 首跑 08-06 00:00 UTC
    harness.clock.advance(hours=54)  # 08-07 08:00 UTC：错过 08-06 与 08-07
    harness.service.process_due()
    reminder = harness.service.get_reminder("acc1", reminder_id)
    assert reminder.status is ReminderStatus.ENABLED
    assert reminder.next_run_at == datetime(2026, 8, 8, 0, 0, tzinfo=UTC)
    deliveries = harness.service.list_deliveries("acc1", reminder_id)
    assert len(deliveries) == 1
    assert deliveries[0].kind is ReminderDeliveryKind.CATCH_UP
    assert deliveries[0].outcome is ReminderDeliveryOutcome.SENT
    assert deliveries[0].delayed
    assert deliveries[0].scheduled_for == datetime(2026, 8, 7, 0, 0, tzinfo=UTC)


def test_weekly_missed_beyond_window_skips_and_continues() -> None:
    """重复提醒（每周）最近一次错过也超过 24 小时 → 跳过并继续下一周期。

    每日规则的最远一次错过必然落在 24 小时内（周期 < 窗口），因此
    用周规则验证超窗跳过：错过 52 小时后处理，最近一次（上周一）已
    超过补发窗口。
    """
    harness = _Harness()
    harness.save_and_verify()
    preview = harness.service.parse(
        "acc1", "每周一早上八点提醒我开会", timezone="Asia/Shanghai", use_profile=False
    )
    reminder = harness.service.create(
        "acc1",
        ReminderCreateRequest(
            raw_text="每周一早上八点提醒我开会",
            schedule=preview.schedule,
            subject=preview.subject,
        ),
    )
    assert preview.schedule.first_run_at == datetime(2026, 8, 10, 0, 0, tzinfo=UTC)
    harness.clock.time = datetime(2026, 8, 12, 4, 0, tzinfo=UTC)  # 错过 52 小时
    harness.service.process_due()
    reminder = harness.service.get_reminder("acc1", reminder.reminder_id)
    assert reminder.status is ReminderStatus.ENABLED
    assert reminder.next_run_at == datetime(2026, 8, 17, 0, 0, tzinfo=UTC)
    deliveries = harness.service.list_deliveries("acc1", reminder.reminder_id)
    assert deliveries[0].outcome is ReminderDeliveryOutcome.SKIPPED
    assert deliveries[0].error_code == "catch_up_window_exceeded"


def test_transient_failure_backoff_then_success() -> None:
    """临时失败：有限退避重试（30s）→ 时钟推进 → 重试成功。"""
    harness = _Harness()
    harness.save_and_verify()
    reminder_id = harness.create_daily()
    harness.clock.advance(hours=24)  # 到期
    harness.gateway.send_error = SmtpError(
        "smtp_transient", "邮件服务器暂时不可用。", retryable=True
    )
    harness.service.process_due()
    reminder = harness.service.get_reminder("acc1", reminder_id)
    assert reminder.retry_count == 1
    assert reminder.next_retry_at is not None
    assert reminder.status is ReminderStatus.ENABLED  # 未暂停
    deliveries = harness.service.list_deliveries("acc1", reminder_id)
    assert deliveries[0].outcome is ReminderDeliveryOutcome.FAILED
    assert deliveries[0].error_code == "smtp_transient"

    harness.clock.advance(seconds=30)  # 退避窗口到期
    harness.service.process_due()
    reminder = harness.service.get_reminder("acc1", reminder_id)
    assert reminder.retry_count == 0
    assert reminder.next_retry_at is None
    deliveries = harness.service.list_deliveries("acc1", reminder_id)
    assert deliveries[0].outcome is ReminderDeliveryOutcome.SENT
    assert len(harness.gateway.sends) == 3  # 验证邮件 + 首次失败 + 重试成功


def test_transient_failure_exhausted_pauses_once() -> None:
    """一次性提醒重试预算耗尽 → 暂停并给出原因，不无限重试。"""
    harness = _Harness()
    harness.save_and_verify()
    preview = harness.service.parse(
        "acc1", "明天早上八点提醒我复习", timezone="Asia/Shanghai", use_profile=False
    )
    reminder = harness.service.create(
        "acc1",
        ReminderCreateRequest(
            raw_text="明天早上八点提醒我复习",
            schedule=preview.schedule,
            subject=preview.subject,
        ),
    )
    harness.clock.advance(hours=24)
    harness.gateway.send_error = SmtpError(
        "smtp_transient", "邮件服务器暂时不可用。", retryable=True
    )
    harness.gateway.fail_times = 3
    for _ in range(3):  # 三次尝试（含退避推进）
        harness.service.process_due()
        harness.clock.advance(seconds=60)
    reminder = harness.service.get_reminder("acc1", reminder.reminder_id)
    assert reminder.status is ReminderStatus.PAUSED
    assert "多次失败" in (reminder.pause_reason or "")
    assert len(harness.service.list_deliveries("acc1", reminder.reminder_id)) == 3


def test_auth_failure_pauses_immediately() -> None:
    """授权失效 → 立即暂停 + 投递失败记录 + SMTP 状态 failed。"""
    harness = _Harness()
    harness.save_and_verify()
    reminder_id = harness.create_daily()
    harness.clock.advance(hours=24)
    harness.gateway.send_error = SmtpError("smtp_auth_failed", "授权码已失效。")
    harness.service.process_due()
    reminder = harness.service.get_reminder("acc1", reminder_id)
    assert reminder.status is ReminderStatus.PAUSED
    assert "重新验证" in (reminder.pause_reason or "")
    assert harness.service.get_smtp_settings("acc1").status is SmtpStatus.FAILED
    deliveries = harness.service.list_deliveries("acc1", reminder_id)
    assert deliveries[0].outcome is ReminderDeliveryOutcome.FAILED
    assert deliveries[0].error_code == "smtp_auth_failed"


def test_missing_credential_skips_and_pauses() -> None:
    """调度时授权码缺失（存储被外部删除）→ 跳过 + 暂停提醒。"""
    harness = _Harness()
    harness.save_and_verify()
    reminder_id = harness.create_daily()
    harness.clock.advance(hours=24)
    # 模拟凭据存储与设置状态不一致（绕过服务删除，如存储损坏/被外部清除）
    harness.credentials.delete("acc1")
    harness.service.process_due()
    reminder = harness.service.get_reminder("acc1", reminder_id)
    assert reminder.status is ReminderStatus.PAUSED
    assert "授权码" in (reminder.pause_reason or "")
    deliveries = harness.service.list_deliveries("acc1", reminder_id)
    assert deliveries[0].outcome is ReminderDeliveryOutcome.SKIPPED
    assert deliveries[0].error_code == "smtp_not_configured"


def test_restart_consistency() -> None:
    """重建服务（重启）：提醒状态、投递记录、SMTP 状态保持一致。"""
    harness = _Harness()
    harness.save_and_verify()
    reminder_id = harness.create_daily()
    harness.clock.advance(hours=30)
    harness.service.process_due()
    deliveries_before = harness.service.list_deliveries("acc1", reminder_id)

    rebuilt = harness.rebuild()
    reminder = rebuilt.get_reminder("acc1", reminder_id)
    assert reminder.status is ReminderStatus.ENABLED
    assert rebuilt.get_smtp_settings("acc1").status is SmtpStatus.VERIFIED
    assert len(rebuilt.list_deliveries("acc1", reminder_id)) == len(deliveries_before)


def test_account_isolation() -> None:
    """两账户完全隔离：提醒/投递/凭据互不可见，同分钟互不干扰。"""
    harness = _Harness()
    harness.emails = {"acc1": EMAIL, "acc2": "999999@qq.com"}
    harness.save_and_verify("acc1")
    harness.save_and_verify("acc2")
    reminder_a = harness.create_daily("acc1")
    harness.clock.advance(hours=24)
    reminder_b = harness.service.parse(
        "acc2", "明天早上八点提醒我开会", timezone="Asia/Shanghai", use_profile=False
    )
    harness.service.create(
        "acc2",
        ReminderCreateRequest(
            raw_text="明天早上八点提醒我开会",
            schedule=reminder_b.schedule,
            subject=reminder_b.subject,
        ),
    )
    harness.clock.advance(hours=25)  # 08-07 03:00 UTC：两账户提醒均到期
    harness.service.process_due()
    # acc1 的提醒不被 acc2 看到
    with pytest.raises(ReminderError) as exc:
        harness.service.get_reminder("acc2", reminder_a)
    assert exc.value.code == "reminder_not_found"
    assert len(harness.service.list_reminders("acc2")) == 1
    # 投递记录按账户隔离
    deliveries_a = harness.service.list_deliveries("acc1", reminder_a)
    assert all(d.reminder_id == reminder_a for d in deliveries_a)
    # 发件凭据不串号：acc1 的提醒邮件用自己的邮箱，acc2 的提醒用 acc2 邮箱
    acc1_sends = [s for s in harness.gateway.sends if s["subject"] == "喝水"]
    assert acc1_sends and all(s["from_addr"] == EMAIL for s in acc1_sends)
    acc2_sends = [s for s in harness.gateway.sends if s["subject"] == "开会"]
    assert acc2_sends and all(s["from_addr"] == "999999@qq.com" for s in acc2_sends)
    # 切换账户后列表无残留
    assert harness.service.list_reminders("acc1")[0].reminder_id == reminder_a


def test_timezone_settings_roundtrip() -> None:
    """时区设置：更新/校验/非法拒绝。"""
    harness = _Harness()
    settings = harness.service.update_settings(
        "acc1", ReminderSettingsUpdateRequest(timezone="America/New_York")
    )
    assert settings.timezone == "America/New_York"
    assert harness.service.get_settings("acc1").timezone == "America/New_York"
    with pytest.raises(ReminderError):
        harness.service.update_settings(
            "acc1", ReminderSettingsUpdateRequest(timezone="Mars/Olympus")
        )


def test_retry_due_does_not_bypass_catch_up_window() -> None:
    """退避重试不豁免 24 小时补发窗口（ADR-0019 审查回归）。

    一次性提醒临时失败后停机超过 24 小时再恢复：残留的 next_retry_at
    不能让它绕过窗口按正常投递发出，应记为错过（skipped + completed）。
    """
    harness = _Harness()
    harness.save_and_verify()
    preview = harness.service.parse(
        "acc1", "明天早上八点提醒我复习", timezone="Asia/Shanghai", use_profile=False
    )
    reminder = harness.service.create(
        "acc1",
        ReminderCreateRequest(
            raw_text="明天早上八点提醒我复习",
            schedule=preview.schedule,
            subject=preview.subject,
        ),
    )
    harness.clock.advance(hours=24)  # 到期
    harness.gateway.send_error = SmtpError(
        "smtp_transient", "邮件服务器暂时不可用。", retryable=True
    )
    harness.service.process_due()  # 首次失败 → retry_count=1, next_retry_at 设置
    sends_after_failure = len(harness.gateway.sends)
    harness.clock.advance(hours=25)  # 停机超过 24 小时（窗口外）
    harness.service.process_due()
    reminder = harness.service.get_reminder("acc1", reminder.reminder_id)
    assert reminder.status is ReminderStatus.COMPLETED  # 记为错过
    assert len(harness.gateway.sends) == sends_after_failure  # 未再次发送
    deliveries = harness.service.list_deliveries("acc1", reminder.reminder_id)
    assert deliveries[0].outcome is ReminderDeliveryOutcome.SKIPPED
    assert deliveries[0].error_code == "catch_up_window_exceeded"


def test_send_while_paused_consumes_occurrence() -> None:
    """发送期间提醒被暂停：本次执行被消费，恢复后不重复投递（审查回归）。"""
    harness = _Harness()
    harness.save_and_verify()
    reminder_id = harness.create_daily()
    harness.clock.advance(hours=24)

    def _pause_during_send() -> None:
        harness.service.pause("acc1", reminder_id, session_id="s1")

    harness.gateway.on_send = _pause_during_send
    harness.service.process_due()
    reminder = harness.service.get_reminder("acc1", reminder_id)
    assert reminder.status is ReminderStatus.PAUSED
    # 恢复后从下一次执行开始（已发送的那次不再重发）
    harness.service.resume("acc1", reminder_id, session_id="s1")
    reminder = harness.service.get_reminder("acc1", reminder_id)
    assert reminder.status is ReminderStatus.ENABLED
    assert reminder.next_run_at == datetime(2026, 8, 7, 0, 0, tzinfo=UTC)
    assert len(harness.gateway.sends) == 2  # 验证邮件 + 本次发送


def test_auth_failure_pauses_all_account_reminders() -> None:
    """授权失效暂停该账户全部启用提醒（AC8 审查回归）。"""
    harness = _Harness()
    harness.save_and_verify()
    first = harness.create_daily("acc1")
    second = harness.service.parse(
        "acc1", "明天早上八点提醒我开会", timezone="Asia/Shanghai", use_profile=False
    )
    second_id = harness.service.create(
        "acc1",
        ReminderCreateRequest(
            raw_text="明天早上八点提醒我开会",
            schedule=second.schedule,
            subject=second.subject,
        ),
    ).reminder_id
    harness.clock.advance(hours=24)
    harness.gateway.send_error = SmtpError("smtp_auth_failed", "授权码已失效。")
    harness.service.process_due()
    assert harness.service.get_reminder("acc1", first).status is ReminderStatus.PAUSED
    assert harness.service.get_reminder("acc1", second_id).status is ReminderStatus.PAUSED
    assert "重新验证" in (
        harness.service.get_reminder("acc1", second_id).pause_reason or ""
    )


def test_profile_slice_unusable_maps_to_4xx() -> None:
    """画像切片过期/撤权 → profile_slice_invalid（4xx），不落 500。"""
    harness = _Harness(items=[_greeting_item()], unusable_reason="切片已过期，无法使用。")
    harness.save_and_verify()
    preview = harness.service.parse(
        "acc1", "明天早上八点提醒我复习", timezone="Asia/Shanghai", use_profile=True
    )
    with pytest.raises(ReminderError) as exc:
        harness.service.create(
            "acc1",
            ReminderCreateRequest(
                raw_text="明天早上八点提醒我复习",
                schedule=preview.schedule,
                subject=preview.subject,
                use_profile=True,
                profile_slice_id=preview.profile_usage.slice_id,
            ),
        )
    assert exc.value.code == "profile_slice_invalid"
    assert exc.value.status_code == 400


def test_unknown_verify_error_uses_fixed_message() -> None:
    """验证过程未知异常 → 固定中文文案，不泄漏原始异常文本（审查回归）。"""
    harness = _Harness()
    harness.gateway.verify_error = RuntimeError("smtp 内部机密细节 ABC")
    projection = harness.service.save_smtp_code("acc1", AUTH_CODE, session_id="s1")
    assert projection.status is SmtpStatus.FAILED
    assert projection.error_code == "verification_failed"
    assert "ABC" not in (projection.error_message or "")
    assert "未知错误" in (projection.error_message or "")


def test_audit_never_contains_auth_code_or_body() -> None:
    """审计事件不含授权码、邮件正文与完整主题以外的敏感字段。"""
    harness = _Harness()
    harness.save_and_verify("acc1")
    harness.create_daily("acc1")
    harness.clock.advance(hours=24)
    harness.service.process_due()
    for event in harness.observability.events:
        details = event.get("details") or {}
        assert AUTH_CODE not in str(details)
        assert "提醒我喝水" not in str(details)
    actions = [e["action"].value for e in harness.observability.events]
    assert any(a in {"reminder_deliver", "reminder_catch_up"} for a in actions)
