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

#: 同步组合路径的收件确认轮询间隔（秒）；异步路径由服务层受监督
#: 循环按固定 tick 推进（同一 IMAP 会话 + NOOP 保活）。
_VERIFY_POLL_INTERVAL_SECONDS = 2.0

#: IMAP 会话 NOOP 保活间隔（每 N 次查询发送一次）。
_NOOP_EVERY = 5


class SmtpError(Exception):
    """邮件域错误；code 决定重试语义，message 为面向用户的中文原因。"""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(message)


def _is_imap_login_rejected(exc: imaplib.IMAP4.error) -> bool:
    """判断 IMAP 异常是否为登录阶段被拒（区别于已登录后的服务器错误）。

    imaplib 对 ``LOGIN`` 被拒（NO/BAD 响应）抛 ``IMAP4.error``，消息形如
    ``LOGIN command error: NO [AUTHENTICATIONFAILED] ...`` 或
    ``LOGIN command error: NO LOGIN failed``；已登录会话内的
    SELECT/SEARCH 错误不包含 ``LOGIN`` 标记。
    """
    lowered = str(exc).lower()
    if "login" not in lowered:
        return False
    # imaplib 提取的拒绝文本形如 "LOGIN failed"（假服务器 NO 响应）或
    # "LOGIN command error: NO [AUTHENTICATIONFAILED] ..."（真实服务器）
    return (
        "failed" in lowered
        or "authentication" in lowered
        or "no " in lowered
        or "bad " in lowered
    )


def _classify(exc: Exception) -> SmtpError:
    """把 smtplib/imaplib/网络异常归类为领域错误。"""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return SmtpError(
            "smtp_auth_failed",
            "邮箱授权码无效或已失效，请检查后重新验证（不是 QQ 登录密码）。",
        )
    # IMAP 登录被拒（LOGIN ... NO [AUTHENTICATIONFAILED] / NO LOGIN failed /
    # 含 authentication 的服务器回显）→ 授权码失效语义，不可重试。
    # imaplib 对登录失败抛 IMAP4.error（"LOGIN command error: NO ..."），
    # 与已登录会话内的服务器错误（SELECT/SEARCH NO）区分开。
    if isinstance(exc, imaplib.IMAP4.error) and _is_imap_login_rejected(exc):
        return SmtpError(
            "smtp_auth_failed",
            "邮箱授权码无法登录收件服务器，请检查后重新验证（不是 QQ 登录密码）。",
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
        verify_window_seconds: float = 120.0,
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
        #: 同步组合路径的收件确认窗口（秒）；异步路径由服务层 attempt
        #: 状态机按同一窗口推进（默认 120 秒，测试注入短窗口）。
        self._verify_window_seconds = verify_window_seconds
        #: 收件确认的 IMAP 会话缓存（email → 连接）：轮询复用同一
        #: 会话 + NOOP 保活，断线时丢弃并由下一次查询重建（Issue 10）。
        self._imap_sessions: dict[str, Any] = {}
        self._imap_checks: dict[str, int] = {}

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
    # 自发自收验证（分阶段：发送 / 收件确认，供 attempt 状态机推进）
    # ------------------------------------------------------------------

    def _connect_imap(self) -> Any:
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
        return connect()

    def _imap_login(self, email: str, auth_code: str) -> Any:
        """建立并登录一个 IMAP 会话（失败抛 :class:`SmtpError`）。

        imaplib 对登录被拒（NO/BAD）抛 ``IMAP4.error``，经 ``_classify``
        归类为 ``smtp_auth_failed``（与已登录会话内的服务器错误区分）。
        """
        imap = self._connect_imap()
        try:
            imap.login(email, auth_code)
        except (imaplib.IMAP4.error, OSError, TimeoutError) as exc:
            with contextlib.suppress(Exception):  # noqa: BLE001
                imap.logout()
            raise _classify(exc) from exc
        return imap

    def _discard_imap_session(self, email: str, imap: Any) -> None:
        with contextlib.suppress(Exception):  # noqa: BLE001 - 收尾失败不影响结果
            imap.logout()
        if self._imap_sessions.get(email) is imap:
            self._imap_sessions.pop(email, None)
        self._imap_checks.pop(email, None)

    def close_imap_session(self, email: str) -> None:
        """显式关闭某邮箱的轮询会话（验证终态后调用，避免空闲连接常驻）。"""
        imap = self._imap_sessions.get(email)
        if imap is not None:
            self._discard_imap_session(email, imap)

    def send_verification_mail(
        self, *, email: str, auth_code: str, token: str | None = None
    ) -> str:
        """SMTP 发送验证邮件并返回不可变验证令牌（Message-ID 用令牌派生）。

        只完成发送；收件确认由调用方按窗口分阶段执行
        :meth:`check_verification_receipt`（ADR-0017：仅 SMTP 接受邮件
        不能直接判定邮箱控制权已验证）。``token`` 由调用方（验证
        attempt）预先生成并持久化，保证重启后可按同一令牌恢复轮询。
        """
        token = token or secrets.token_hex(6)
        self.send_mail(
            from_addr=email,
            to_addr=email,
            auth_code=auth_code,
            subject=f"BridGes 验证邮件 {token}",
            body="这是一封 BridGes 发送的验证邮件，用于确认你的 QQ 邮箱可以正常收发。",
            message_id=f"<bridges-verify-{token}@bridges.local>",
        )
        return token

    def check_verification_receipt(
        self, *, email: str, auth_code: str, token: str
    ) -> bool:
        """单次 IMAP 收件确认：按主题令牌搜索 INBOX，返回是否到达。

        复用同一 IMAP 会话（按邮箱缓存）+ NOOP 保活；会话断线
        （网络/服务器断开）自动丢弃并重建一次再查询；仍失败抛
        :class:`SmtpError`（transient），由调用方保持等待下轮再试。
        """
        imap = self._imap_sessions.get(email)
        if imap is None:
            imap = self._imap_login(email, auth_code)
            self._imap_sessions[email] = imap
        try:
            checks = self._imap_checks.get(email, 0) + 1
            self._imap_checks[email] = checks
            if checks % _NOOP_EVERY == 0:
                # NOOP 保活：部分服务器空闲超时会断开；失败按断线重建
                imap.noop()
            imap.select("INBOX")
            # 以不可变 token 派生的 Message-ID 为主（重启恢复后仍指向同一
            # 封邮件），主题为兼容回退（部分服务器不支持 HEADER 搜索）。
            message_id = f"<bridges-verify-{token}@bridges.local>"
            status, data = imap.search(None, "HEADER", "Message-ID", message_id)
            if status == "OK" and data and data[0]:
                return True
            status, data = imap.search(None, f"SUBJECT {token}")
            return bool(status == "OK" and data and data[0])
        except (imaplib.IMAP4.abort, OSError, TimeoutError) as exc:
            # 会话断线：丢弃缓存，下一次调用重建（IMAP 临时断线恢复）。
            # 登录阶段（_imap_login）的认证失败已归类为 smtp_auth_failed；
            # 已建立会话后的断线属于临时网络问题，保持轮询不失败。
            self._discard_imap_session(email, imap)
            raise SmtpError(
                "smtp_transient",
                "收件服务器连接中断，将继续尝试确认收件。",
                retryable=True,
            ) from exc
        except imaplib.IMAP4.error as exc:
            # 已登录会话内的服务器错误（SELECT/SEARCH 拒绝等）：临时，
            # 保持等待下轮再试，不把一次服务器抖动写成不可逆失败。
            if "authentication" in str(exc).lower():
                raise _classify(exc) from exc
            raise SmtpError(
                "smtp_transient",
                "收件确认暂时失败，将继续尝试确认收件。",
                retryable=True,
            ) from exc

    def verify_self_send_receive(self, *, email: str, auth_code: str) -> str:
        """同步组合：SMTP 发送测试邮件 → 有界轮询 IMAP 确认到达。

        同步快路径由服务层在 ``verify_async=False``（测试）时使用；
        生产异步路径使用分阶段的 :meth:`send_verification_mail` 与
        :meth:`check_verification_receipt`（attempt 状态机推进）。
        失败抛 :class:`SmtpError`（auth_failed / transient / failed），
        调用方据其更新验证状态并给出重新验证路径。
        """
        token = self.send_verification_mail(email=email, auth_code=auth_code)
        deadline = time.monotonic() + self._verify_window_seconds
        while time.monotonic() < deadline:
            if self.check_verification_receipt(
                email=email, auth_code=auth_code, token=token
            ):
                return f"<bridges-verify-{token}@bridges.local>"
            time.sleep(_VERIFY_POLL_INTERVAL_SECONDS)
        raise SmtpError(
            "verification_failed",
            "验证邮件未在预期时间内到达收件箱，请稍后重试或检查邮箱设置。",
        )


__all__ = [
    "QqMailGateway",
    "SmtpError",
]
