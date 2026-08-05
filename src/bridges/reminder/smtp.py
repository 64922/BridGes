"""QQ 邮箱 SMTP 发送与自发自收验证适配器（Issue 33，ADR-0004）。

发送使用 smtplib（默认 ``smtp.qq.com:465`` SSL，可配置 STARTTLS 端口），
验证流程是「自发自收」：先用授权码经 SMTP 发送一封带唯一令牌的测试邮件，
再经 IMAP（默认 ``imap.qq.com:993`` SSL）确认该邮件到达同一邮箱。整个
流程只使用当前账户的 QQ 邮箱与其授权码，绝不接触其他收件人。

错误分类（决定退避重试与授权失效语义）：

- ``smtp_auth_failed``（SMTP/IMAP 登录被拒，如 535/5.7.8）：授权码失效，
  不可重试，应立即暂停相关提醒并提示重新验证；
- ``smtp_rejected``（服务器 5xx 拒绝）：不可重试的投递失败；
- ``smtp_transient``（网络/超时/临时拒绝）：可重试，由调度器做有限
  退避重试。

授权码只存在于进程内参数，不写入任何日志、审计或持久化字段。
"""

from __future__ import annotations

import contextlib
import imaplib
import secrets
import smtplib
import time
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any

#: 自发自收验证的 IMAP 轮询次数与间隔（秒）。
_VERIFY_POLLS = 4
_VERIFY_POLL_INTERVAL_SECONDS = 2.0


class SmtpError(Exception):
    """邮件域错误；code 决定重试语义，message 为面向用户的中文原因。"""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(message)


def _classify(exc: Exception) -> SmtpError:
    """把 smtplib/imaplib/网络异常归类为领域错误。"""
    if isinstance(exc, smtplib.SMTPAuthenticationError) or (
        isinstance(exc, imaplib.IMAP4.error) and "authentication" in str(exc).lower()
    ):
        return SmtpError(
            "smtp_auth_failed",
            "邮箱授权码无效或已失效，请检查后重新验证（不是 QQ 登录密码）。",
        )
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return SmtpError(
            "smtp_rejected",
            "邮件服务器拒绝了收件人，请检查邮箱地址后重试。",
        )
    if isinstance(exc, smtplib.SMTPResponseException) and exc.smtp_code >= 500:
        return SmtpError(
            "smtp_rejected",
            f"邮件服务器拒绝了投递（{exc.smtp_code}），请稍后手动重试。",
        )
    if isinstance(exc, smtplib.SMTPException):
        return SmtpError(
            "smtp_transient",
            "邮件服务器暂时不可用，稍后将自动重试。",
            retryable=True,
        )
    if isinstance(exc, imaplib.IMAP4.error):
        return SmtpError(
            "verification_failed",
            "收件确认失败，请检查邮箱授权码后重新验证。",
        )
    if isinstance(exc, (TimeoutError, OSError)):
        return SmtpError(
            "smtp_transient",
            "连接邮件服务器超时或网络不可用，稍后将自动重试。",
            retryable=True,
        )
    # 兜底不携带原始异常文本：异常正文可能含服务器回显细节，绝不落入
    # 投递记录、API 响应或审计（Verification 4 硬化）。
    return SmtpError(
        "smtp_failed",
        "邮件发送失败，请稍后重试。",
        retryable=True,
    )


class QqMailGateway:
    """QQ 邮箱 SMTP 发送与自发自收验证（可配置端点供测试指向本地服务器）。

    ``imap_connect`` 是 IMAP 连接工厂（默认真实 ``imaplib.IMAP4_SSL``），
    测试注入假 IMAP 对象验证轮询逻辑。
    """

    def __init__(
        self,
        *,
        smtp_host: str = "smtp.qq.com",
        smtp_port: int = 465,
        smtp_starttls: bool = False,
        smtp_plain: bool = False,
        imap_host: str = "imap.qq.com",
        imap_port: int = 993,
        imap_plain: bool = False,
        timeout: float = 15.0,
        imap_connect: Callable[[], Any] | None = None,
    ) -> None:
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._smtp_starttls = smtp_starttls
        #: 明文模式仅供本地测试/假邮件服务器使用；生产默认 QQ 官方端口。
        self._smtp_plain = smtp_plain
        self._imap_host = imap_host
        self._imap_port = imap_port
        self._imap_plain = imap_plain
        self._timeout = timeout
        self._imap_connect = imap_connect

    # ------------------------------------------------------------------
    # 发送
    # ------------------------------------------------------------------

    def send_mail(
        self,
        *,
        from_addr: str,
        to_addr: str,
        auth_code: str,
        subject: str,
        body: str,
        message_id: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        """发送一封邮件并返回服务器接受的 Message-ID；失败抛 :class:`SmtpError`。

        发件人固定为账户 QQ 邮箱，``to_addr`` 必须与 ``from_addr`` 相同
        （ADR-0004：只允许自发），由服务层强制；本层只负责传输。
        """
        mid = message_id or f"<bridges-{secrets.token_hex(8)}@bridges.local>"
        message = EmailMessage()
        message["From"] = from_addr
        message["To"] = to_addr
        message["Subject"] = subject
        message["Message-ID"] = mid
        message["Date"] = time.strftime("%a, %d %b %Y %H:%M:%S %z")
        for key, value in (headers or {}).items():
            message[key] = value
        message.set_content(body)
        try:
            with self._connect_smtp() as smtp:
                smtp.login(from_addr, auth_code)
                smtp.send_message(message, from_addr=from_addr, to_addrs=[to_addr])
        except (smtplib.SMTPException, OSError, TimeoutError) as exc:
            raise _classify(exc) from exc
        return mid

    def _connect_smtp(self) -> smtplib.SMTP:
        if self._smtp_plain:
            return smtplib.SMTP(
                self._smtp_host, self._smtp_port, timeout=self._timeout
            )
        if self._smtp_starttls:
            smtp = smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=self._timeout)
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            return smtp
        return smtplib.SMTP_SSL(self._smtp_host, self._smtp_port, timeout=self._timeout)

    # ------------------------------------------------------------------
    # 自发自收验证
    # ------------------------------------------------------------------

    def verify_self_send_receive(self, *, email: str, auth_code: str) -> str:
        """自发自收验证：SMTP 发送测试邮件 → IMAP 确认到达；返回 Message-ID。

        失败抛 :class:`SmtpError`（auth_failed / transient / failed），
        调用方据其更新验证状态并给出重新验证路径。
        """
        token = secrets.token_hex(6)
        subject = f"BridGes 验证邮件 {token}"
        mid = f"<bridges-verify-{token}@bridges.local>"
        self.send_mail(
            from_addr=email,
            to_addr=email,
            auth_code=auth_code,
            subject=subject,
            body="这是一封 BridGes 发送的验证邮件，用于确认你的 QQ 邮箱可以正常收发。",
            message_id=mid,
        )
        self._confirm_receipt(email=email, auth_code=auth_code, token=token)
        return mid

    def _confirm_receipt(
        self, *, email: str, auth_code: str, token: str
    ) -> None:
        """轮询 IMAP INBOX 直到出现带令牌的验证邮件或超时。"""
        connect = self._imap_connect
        if connect is None:
            def _default() -> Any:
                if self._imap_plain:
                    return imaplib.IMAP4(
                        self._imap_host, self._imap_port, timeout=self._timeout
                    )
                return imaplib.IMAP4_SSL(
                    self._imap_host, self._imap_port, timeout=self._timeout
                )
            connect = _default
        imap = connect()
        try:
            for attempt in range(_VERIFY_POLLS):
                if attempt > 0:
                    time.sleep(_VERIFY_POLL_INTERVAL_SECONDS)
                try:
                    status, _ = imap.login(email, auth_code)
                    if status != "OK":
                        raise SmtpError(
                            "smtp_auth_failed",
                            "邮箱授权码无法登录收件服务器，请检查后重新验证。",
                        )
                    imap.select("INBOX")
                    status, data = imap.search(None, f"SUBJECT {token}")
                    if status == "OK" and data and data[0]:
                        return
                    imap.logout()
                    imap = connect()
                except SmtpError:
                    raise
                except (imaplib.IMAP4.error, OSError, TimeoutError) as exc:
                    raise _classify(exc) from exc
            raise SmtpError(
                "verification_failed",
                "验证邮件未在预期时间内到达收件箱，请稍后重试或检查邮箱设置。",
            )
        finally:
            with contextlib.suppress(Exception):  # noqa: BLE001 - 收尾失败不影响结果
                imap.logout()


__all__ = [
    "QqMailGateway",
    "SmtpError",
]
