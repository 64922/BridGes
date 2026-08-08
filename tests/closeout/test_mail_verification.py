"""收尾邮件边界 smoke（issue 01 AC6 + 反馈环 smoke 3 + issue 10）。

真实链路：真实 API 子进程 + 真实假邮件服务子进程（SMTP/IMAP 均走真实
套接字协议）→ 注册账户 → 保存授权码（触发受监督验证 attempt 状态机）
→ 轮询 SMTP 状态直到收敛。三种投递边界：

1. 0 秒：立即投递 → 最终 ``verified``（绿，fixture 能力验证）；
2. 10 秒：SMTP 秒收、IMAP 晚到 → issue 10 修复前必红（旧实现收件
   轮询约 6-8 秒即写失败）；修复后 attempt 状态机在默认 120 秒窗口内
   持续有界退避，晚到邮件到达后最终收敛 ``verified``；
3. 超时：命中丢弃令牌 → 邮件永不入箱 → 窗口（测试注入 10 秒）到期
   终态 ``receipt_timeout``。
"""

from __future__ import annotations

import time

from conftest import TEST_AUTH_CODE, sanitize

_VERIFY_DEADLINE_SECONDS = 40.0
_POLL_INTERVAL_SECONDS = 1.0


def _save_and_poll(
    api, client, *, expect_failed: bool = False
) -> dict[str, object]:
    save = client.put(
        "/reminders/smtp",
        json={"authorization_code": TEST_AUTH_CODE},
    )
    assert save.status_code == 200, sanitize(save.text)
    deadline = time.monotonic() + _VERIFY_DEADLINE_SECONDS
    projection: dict[str, object] = {}
    while time.monotonic() < deadline:
        projection = client.get("/reminders/smtp").json()
        status = projection.get("status")
        if status == "verified" or (expect_failed and status == "failed"):
            return projection
        if status == "failed":
            raise AssertionError(
                "验证在收件窗口内提前失败："
                f"{projection.get('error_code')} {projection.get('error_message')}"
                "（issue 10 状态机应在窗口内持续有界退避，"
                "晚到邮件到达后最终收敛为 verified）"
            )
        time.sleep(_POLL_INTERVAL_SECONDS)
    raise AssertionError(
        f"{_VERIFY_DEADLINE_SECONDS:.0f} 秒内未收敛，最后状态：{projection.get('status')}"
    )


def test_immediate_delivery_verifies(api_server, unique_data_dir, fake_mail_server) -> None:
    """0 秒投递：验证最终收敛为 verified（fixture 边界能力验证）。"""
    mail = fake_mail_server()
    api = api_server(unique_data_dir, mail=mail)
    client = api.register_account()
    projection = _save_and_poll(api, client)
    assert projection.get("status") == "verified"


def test_mail_verification_converges_to_verified_after_10_second_delivery(
    api_server, unique_data_dir, fake_mail_server
) -> None:
    """10 秒晚到邮件：验证必须最终收敛为 verified（现状必红，issue 10）。"""
    mail = fake_mail_server(deliver_delay_seconds=10.0)
    api = api_server(unique_data_dir, mail=mail)
    client = api.register_account()
    projection = _save_and_poll(api, client)
    assert projection.get("status") == "verified"


def test_dropped_mail_times_out_to_failed(
    api_server, unique_data_dir, fake_mail_server
) -> None:
    """命中丢弃令牌：邮件永不入箱，窗口到期终态 receipt_timeout。

    注入 10 秒收件窗口：测试在 40 秒轮询期限内必然看到 failed；
    issue 10 默认窗口为 120 秒（覆盖正常投递延迟）。
    """
    mail = fake_mail_server(drop_subject_tokens=("BridGes 验证邮件",))
    api = api_server(
        unique_data_dir, mail=mail, smtp_verify_window_seconds=10.0
    )
    client = api.register_account()
    projection = _save_and_poll(api, client, expect_failed=True)
    assert projection.get("status") == "failed"
    assert projection.get("error_code") == "receipt_timeout"
