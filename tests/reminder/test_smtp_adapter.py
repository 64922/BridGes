"""QQ 邮箱 SMTP 发送与自发自收验证适配器测试（Issue 33）。

用进程内假 SMTP+IMAP 服务器（共享邮箱）驱动真实 ``smtplib``/
``imaplib`` 适配器：发送成功、授权失败分类（535 → auth_failed）、
收件人拒绝（550 → rejected）、验证自发自收成功、验证超时（邮件未
到达）、验证收件确认失败。授权码只经 AUTH LOGIN 传输，不进入任何
记录。
"""

from __future__ import annotations

import pytest
from fake_mail import FakeMailbox, FakeMailServers

from bridges.reminder.smtp import QqMailGateway, SmtpError

EMAIL = "123456@qq.com"
VALID_CODE = "ABCDEFGHIJKLMNOP"


@pytest.fixture()
def servers() -> FakeMailServers:
    with FakeMailServers(
        FakeMailbox(auth_codes={VALID_CODE})
    ) as fake:
        yield fake


def _gateway(servers: FakeMailServers, **kwargs: object) -> QqMailGateway:
    return QqMailGateway(
        smtp_host="127.0.0.1",
        smtp_port=servers.smtp_port,
        smtp_plain=True,
        imap_host="127.0.0.1",
        imap_port=servers.imap_port,
        imap_plain=True,
        **kwargs,
    )


def test_send_mail_success_records_message(servers: FakeMailServers) -> None:
    """真实 SMTP 发送成功：邮件进入共享邮箱且携带主题与 Message-ID。"""
    message_id = _gateway(servers).send_mail(
        from_addr=EMAIL,
        to_addr=EMAIL,
        auth_code=VALID_CODE,
        subject="复习 transformer",
        body="提醒正文",
    )
    assert message_id.startswith("<bridges-")
    stored = servers.mailbox.messages
    assert len(stored) == 1
    assert stored[0].sender == EMAIL
    assert stored[0].recipients == [EMAIL]
    assert stored[0].subject == "复习 transformer"
    assert "提醒正文" in stored[0].body


def test_send_mail_auth_failure_classified(servers: FakeMailServers) -> None:
    """错误授权码 → 535 → smtp_auth_failed（不可重试）。"""
    with pytest.raises(SmtpError) as exc_info:
        _gateway(servers).send_mail(
            from_addr=EMAIL,
            to_addr=EMAIL,
            auth_code="WRONGCODE1234",
            subject="s",
            body="b",
        )
    assert exc_info.value.code == "smtp_auth_failed"
    assert not exc_info.value.retryable
    assert "授权码" in exc_info.value.message


def test_send_mail_recipient_rejected_classified(servers: FakeMailServers) -> None:
    """服务器 550 拒绝收件人 → smtp_rejected（不可重试）。"""
    with FakeMailServers(
        FakeMailbox(auth_codes={VALID_CODE}, reject_rcpt={EMAIL})
    ) as rejecting, pytest.raises(SmtpError) as exc_info:
        _gateway(rejecting).send_mail(
            from_addr=EMAIL,
            to_addr=EMAIL,
            auth_code=VALID_CODE,
            subject="s",
            body="b",
        )
    assert exc_info.value.code == "smtp_rejected"
    assert not exc_info.value.retryable


def test_verify_self_send_receive_success(servers: FakeMailServers) -> None:
    """自发自收验证：SMTP 发送 → IMAP 轮询确认到达 → 成功返回 Message-ID。"""
    message_id = _gateway(servers).verify_self_send_receive(
        email=EMAIL, auth_code=VALID_CODE
    )
    assert message_id.startswith("<bridges-verify-")
    assert len(servers.mailbox.messages) == 1


def test_verify_self_send_receive_auth_failure(servers: FakeMailServers) -> None:
    """IMAP 登录拒绝 → smtp_auth_failed（授权失效语义）。"""
    with pytest.raises(SmtpError) as exc_info:
        _gateway(servers).verify_self_send_receive(
            email=EMAIL, auth_code="WRONGCODE1234"
        )
    assert exc_info.value.code == "smtp_auth_failed"


def test_imap_login_rejection_classified_as_auth_failed(
    servers: FakeMailServers,
) -> None:
    """IMAP 单独登录被拒（NO LOGIN failed）→ smtp_auth_failed。

    覆盖 IMAP-only 失败路径（未先走 SMTP）：SMTP 接受但 IMAP 拒绝时
    必须立即失败，而不是空转到收件窗口超时（issue 10 实施步骤 8）。
    """
    with pytest.raises(SmtpError) as exc_info:
        _gateway(servers).check_verification_receipt(
            email=EMAIL, auth_code="WRONGCODE1234", token="deadbeef00"
        )
    assert exc_info.value.code == "smtp_auth_failed"
    assert "授权码" in exc_info.value.message


def test_receipt_found_by_message_id(servers: FakeMailServers) -> None:
    """收件确认以 Message-ID 头为主键搜索（主题为兼容回退）。"""
    gateway = _gateway(servers)
    token = gateway.send_verification_mail(email=EMAIL, auth_code=VALID_CODE)
    assert (
        gateway.check_verification_receipt(
            email=EMAIL, auth_code=VALID_CODE, token=token
        )
        is True
    )


def test_verify_receipt_timeout_when_mail_never_arrives() -> None:
    """测试邮件未到达收件箱 → 轮询超时 → verification_failed。"""
    with FakeMailServers(
        FakeMailbox(auth_codes={VALID_CODE}, drop_all_messages=True)
    ) as dropping, pytest.raises(SmtpError) as exc_info:
        _gateway(dropping, verify_window_seconds=2.0).verify_self_send_receive(
            email=EMAIL, auth_code=VALID_CODE
        )
    assert exc_info.value.code == "verification_failed"
    assert "未在预期时间内" in exc_info.value.message


def test_verify_receipt_with_delivery_delay(servers: FakeMailServers) -> None:
    """投递有延迟时轮询仍能等到（在超时窗口内成功）。"""
    with FakeMailServers(
        FakeMailbox(
            auth_codes={VALID_CODE}, deliver_delay_seconds=0.5
        )
    ) as delayed:
        message_id = _gateway(delayed).verify_self_send_receive(
            email=EMAIL, auth_code=VALID_CODE
        )
    assert message_id.startswith("<bridges-verify-")
