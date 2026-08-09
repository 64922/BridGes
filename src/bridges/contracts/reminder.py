"""QQ SMTP 任务提醒的公开契约（Issue 33）。

每账户配置并验证 QQ SMTP 授权码（ADR-0004：自发自收验证——SMTP 发送
测试邮件后经 IMAP 确认到达同一邮箱），授权码按账户加密保存，绝不进入
日志、模型或导出。收件人与发件人固定为当前账户注册的同一 QQ 邮箱，
任何修改收件人为第三方或跨账户使用授权码的请求都被拒绝。

用户以自然语言描述时间、重复规则与主题，系统先解析为带时区的结构化
日程与简练邮件预览，经用户确认后才持久化（ADR-0019 同时保存账户时区
规则与 UTC 执行时间）。提醒措辞只调用用户授权的最小画像类别
（reminder 模式切片，用户可关闭并查看本次使用类别）；投递记录准确
区分发送、失败、跳过、补发与手动重试；一次性提醒 24 小时内补发并
标记延迟，重复提醒最多补发最近一次。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, SecretStr


class SmtpStatus(StrEnum):
    """账户 SMTP 授权码的验证状态。

    - ``unconfigured``：尚未保存授权码；
    - ``verifying``：授权码已保存，自发自收验证进行中（细粒度进度
      见 ``SmtpSettingsProjection.attempt_state``）；
    - ``verified``：自发自收验证通过，可以启用邮件提醒；
    - ``failed``：验证失败或授权失效，error_code/error_message
      说明原因并提供重新验证路径。
    """

    UNCONFIGURED = "unconfigured"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    FAILED = "failed"


class SmtpAttemptState(StrEnum):
    """验证 attempt 的阶段状态机（Issue 10）。

    - ``smtp_connecting``：attempt 已创建，尚未完成 SMTP 发送；
    - ``mail_sent``：SMTP 已接受测试邮件，收件确认计时开始；
    - ``waiting_receipt``：正在有界退避轮询 IMAP 收件；
    - ``verified``：自发自收验证通过（终态，且账户 SMTP 终态已提交）；
    - ``failed``：验证失败（终态，error_code 说明原因）；
    - ``superseded``：已被新 attempt 取代或凭据已删除（终态，
      迟到结果不得再提交账户 SMTP 状态）。

    只有当前 attempt（``reminder_settings.smtp_attempt_id`` 指向的）
    可以提交账户 SMTP 终态；旧 attempt 的迟到成功/失败一律失效。
    """

    SMTP_CONNECTING = "smtp_connecting"
    MAIL_SENT = "mail_sent"
    WAITING_RECEIPT = "waiting_receipt"
    VERIFIED = "verified"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class SmtpSettingsProjection(BaseModel):
    """账户 SMTP 配置投影；绝不包含授权码正文。

    ``qq_email`` 是当前账户注册的 QQ 邮箱，系统只允许从该邮箱发往
    同一邮箱；页面展示此字段并禁止修改收件人。验证进行中时
    ``attempt_state``/``attempt_deadline_at`` 提供细粒度进度与收件
    截止时间，页面据此持续轮询；attempt 终态后这两个字段为 None。
    """

    status: SmtpStatus = Field(description="授权码保存与验证状态。")
    qq_email: str = Field(description="当前账户注册的 QQ 邮箱（固定收发件人）。")
    verified_at: datetime | None = Field(
        default=None, description="自发自收验证通过时间。"
    )
    error_code: str | None = Field(default=None, description="稳定错误码。")
    error_message: str | None = Field(
        default=None, description="可操作的中文原因与重新验证路径。"
    )
    attempt_state: SmtpAttemptState | None = Field(
        default=None,
        description="当前验证 attempt 的阶段（验证进行中时存在；终态为 None）。",
    )
    attempt_deadline_at: datetime | None = Field(
        default=None, description="收件确认截止时间（UTC）；等待收件时存在。"
    )
    updated_at: datetime | None = Field(
        default=None, description="最近一次配置或验证状态更新时间。"
    )


class SmtpCodeSaveRequest(BaseModel):
    """保存 QQ 邮箱授权码的请求；系统不接受 QQ 登录密码。"""

    authorization_code: SecretStr = Field(
        description="QQ 邮箱授权码（16 位字母数字组合），非 QQ 登录密码。"
    )


class ReminderRepeatRule(StrEnum):
    """重复规则；参数见 :class:`ReminderSchedule` 的 repeat 载荷。

    - ``once``：一次性提醒，发送后完成；
    - ``daily``：每天同一本地时间；
    - ``weekdays``：工作日（周一至周五）同一本地时间；
    - ``weekly_days``：每周指定星期（repeat_weekdays 集合）；
    - ``monthly_day``：每月指定日（repeat_month_day，超出当月天数
      自动收敛到月末）。
    """

    ONCE = "once"
    DAILY = "daily"
    WEEKDAYS = "weekdays"
    WEEKLY_DAYS = "weekly_days"
    MONTHLY_DAY = "monthly_day"


class ReminderSchedule(BaseModel):
    """带时区的结构化日程（解析产物与持久化快照共用）。

    ``first_run_at`` 是 UTC 首次执行时间；``first_run_local`` 是同一
    时刻在账户时区下的可读表示，供页面展示与追溯。重复规则参数与
    规则联合使用，一次性提醒忽略规则参数。
    """

    timezone: str = Field(description="IANA 时区标识（账户时区规则）。")
    first_run_at: datetime = Field(description="首次执行时间（UTC）。")
    first_run_local: str = Field(description="账户时区下的首次执行时间可读表示。")
    repeat: ReminderRepeatRule = Field(description="重复规则。")
    repeat_weekdays: list[int] = Field(
        default_factory=list,
        description="weekly_days 时的星期集合（1=周一 … 7=周日）。",
    )
    repeat_month_day: int | None = Field(
        default=None, description="monthly_day 时的每月日期（1-31）。"
    )


class ReminderProfileUsage(BaseModel):
    """本次提醒的画像适配披露：只含类别中文标签，不含记录正文。

    ``categories`` 是本次使用的画像类别（如「表达习惯」「基本偏好」），
    ``item_count`` 是实际注入措辞的切片条数；用户可在确认前关闭画像
    适配（use_profile=false），关闭后正文不再包含任何个性化措辞。
    """

    enabled: bool = Field(description="本次提醒是否启用画像适配。")
    categories: list[str] = Field(
        default_factory=list, description="本次使用的画像类别中文标签。"
    )
    item_count: int = Field(default=0, description="实际使用的切片条数。")
    slice_id: str | None = Field(
        default=None, description="冻结的画像切片标识（确认后持久化引用）。"
    )


class ParsedReminderPreview(BaseModel):
    """自然语言解析结果：结构化日程 + 简练邮件预览（未持久化）。

    页面展示时区、首次执行时间、重复规则、主题与邮件正文预览，用户
    确认后才调用创建接口持久化；预览内容与创建内容由同一确定性解析
    器与适配函数产生，保证所见即所存。
    """

    raw_text: str = Field(description="用户输入的自然语言原文（追溯用）。")
    schedule: ReminderSchedule = Field(description="带时区的结构化日程。")
    subject: str = Field(description="提醒主题（邮件主题）。")
    body_preview: str = Field(description="确认后将要投递的邮件正文预览。")
    profile_usage: ReminderProfileUsage = Field(description="画像适配披露。")
    parsed_at: datetime = Field(description="解析完成时间（UTC）。")


class ReminderCreateRequest(BaseModel):
    """用户确认后的提醒创建请求（预览内容回传，服务端复核）。

    ``schedule``/``subject``/``use_profile`` 必须与
    :meth:`parse` 返回的预览一致（服务端用同一确定性解析器复核
    raw_text）；``profile_slice_id`` 来自预览的画像切片标识，服务端
    校验归属后冻结。
    """

    raw_text: str = Field(
        min_length=1, max_length=500, description="用户输入的自然语言原文。"
    )
    schedule: ReminderSchedule = Field(description="确认后的结构化日程。")
    subject: str = Field(
        min_length=1, max_length=100, description="确认后的提醒主题。"
    )
    use_profile: bool = Field(
        default=False, description="是否启用确认过的画像措辞适配。"
    )
    profile_slice_id: str | None = Field(
        default=None, description="预览返回的画像切片标识（use_profile 时必填）。"
    )


class ReminderUpdateRequest(BaseModel):
    """编辑提醒：允许修改日程、主题与画像开关。

    编辑后从新的确认载荷重新冻结快照（同创建语义）；投递记录保留
    历史，不被编辑覆盖。
    """

    raw_text: str = Field(
        min_length=1, max_length=500, description="编辑后的自然语言原文（追溯）。"
    )
    schedule: ReminderSchedule = Field(description="编辑后的结构化日程。")
    subject: str = Field(
        min_length=1, max_length=100, description="编辑后的提醒主题。"
    )
    use_profile: bool = Field(
        default=False, description="编辑后是否启用画像措辞适配。"
    )
    profile_slice_id: str | None = Field(
        default=None, description="编辑后使用的画像切片标识。"
    )


class ReminderStatus(StrEnum):
    """提醒生命周期状态。

    - ``enabled``：已启用，调度器按 next_run_at 投递；
    - ``paused``：已暂停（用户暂停或授权失效自动暂停），pause_reason
      说明原因；恢复后从暂停时保留的 next_run_at 继续；
    - ``completed``：一次性提醒已成功投递（自然完成）；
    - ``cancelled``：用户已取消；不再投递，投递记录保留。
    """

    ENABLED = "enabled"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    RETIRED = "retired"


class ReminderProjection(BaseModel):
    """一条提醒的公开投影；不含授权码与画像记录正文。"""

    reminder_id: str = Field(description="提醒标识。")
    qq_email: str = Field(description="固定收件人/发件人（当前账户 QQ 邮箱）。")
    timezone: str = Field(description="账户时区标识。")
    raw_text: str = Field(description="创建时的自然语言原文。")
    subject: str = Field(description="提醒主题。")
    body: str = Field(description="冻结的邮件正文（确认时的快照）。")
    schedule: ReminderSchedule = Field(description="带时区的结构化日程。")
    status: ReminderStatus = Field(description="生命周期状态。")
    pause_reason: str | None = Field(
        default=None, description="暂停原因（暂停时存在，含授权失效原因）。"
    )
    profile_usage: ReminderProfileUsage = Field(
        description="画像适配披露（冻结于确认时）。"
    )
    next_run_at: datetime | None = Field(
        default=None, description="下次计划执行时间（UTC）；一次性已完成时为 None。"
    )
    next_retry_at: datetime | None = Field(
        default=None, description="临时失败后的下次退避重试时间（UTC）。"
    )
    retry_count: int = Field(default=0, description="当前投递尝试的失败次数。")
    last_delivery: ReminderDeliveryProjection | None = Field(
        default=None, description="最近一次投递记录快照。"
    )
    created_at: datetime = Field(description="创建时间（UTC）。")
    updated_at: datetime = Field(description="最近更新时间（UTC）。")


class ReminderDeliveryKind(StrEnum):
    """投递来源语义。

    - ``scheduled``：按计划正常投递；
    - ``catch_up``：恢复运行后的 24 小时有限补发（delayed=True）；
    - ``manual_retry``：用户手动补发。
    """

    SCHEDULED = "scheduled"
    CATCH_UP = "catch_up"
    MANUAL_RETRY = "manual_retry"


class ReminderDeliveryOutcome(StrEnum):
    """投递结果语义。

    - ``sent``：邮件已提交 SMTP 服务器；
    - ``failed``：投递失败，error_code/error_message 说明原因；
    - ``skipped``：跳过（24 小时补发窗口已过、已取消/已完成、无授权码）。
    """

    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


class ReminderDeliveryProjection(BaseModel):
    """一条投递记录；区分发送/失败/跳过/补发/手动重试。"""

    delivery_id: str = Field(description="投递记录标识。")
    reminder_id: str = Field(description="所属提醒标识。")
    kind: ReminderDeliveryKind = Field(description="投递来源语义。")
    outcome: ReminderDeliveryOutcome = Field(description="投递结果语义。")
    scheduled_for: datetime = Field(description="本次计划执行时间（UTC）。")
    attempted_at: datetime | None = Field(
        default=None, description="实际尝试时间（UTC）。"
    )
    delayed: bool = Field(
        default=False, description="是否补发（延迟）标记。"
    )
    error_code: str | None = Field(default=None, description="稳定错误码。")
    error_message: str | None = Field(
        default=None, description="可操作的中文错误说明。"
    )
    message_id: str | None = Field(
        default=None, description="SMTP 服务器接受的 Message-ID（成功时存在）。"
    )


class ReminderSettingsProjection(BaseModel):
    """账户提醒设置投影（当前只有时区）。"""

    timezone: str = Field(description="账户时区标识（IANA）。")


class ReminderSettingsUpdateRequest(BaseModel):
    """更新账户提醒设置（时区）。"""

    timezone: str = Field(
        min_length=1, max_length=64, description="IANA 时区标识，如 Asia/Shanghai。"
    )


class ReminderError(Exception):
    """提醒域错误；message 为面向用户的中文说明。"""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(message)


__all__ = [
    "ParsedReminderPreview",
    "ReminderCreateRequest",
    "ReminderDeliveryKind",
    "ReminderDeliveryOutcome",
    "ReminderDeliveryProjection",
    "ReminderError",
    "ReminderProfileUsage",
    "ReminderProjection",
    "ReminderRepeatRule",
    "ReminderSchedule",
    "ReminderSettingsProjection",
    "ReminderSettingsUpdateRequest",
    "ReminderStatus",
    "ReminderUpdateRequest",
    "SmtpAttemptState",
    "SmtpCodeSaveRequest",
    "SmtpSettingsProjection",
    "SmtpStatus",
]
