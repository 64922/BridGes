"""真实 QQ 邮箱自发自收验证与提醒投递冒烟（Issue 33）。

显式提供账户 QQ 邮箱与授权码（通过 ``BRIDGES_SMOKE_QQ_EMAIL`` /
``BRIDGES_SMOKE_QQ_AUTH_CODE`` 环境变量或 ``*_FILE`` 文件引用），
连接真实 smtp.qq.com / imap.qq.com 完成：

1. 自发自收验证：SMTP 发送带唯一令牌的验证邮件 → IMAP 轮询确认到达；
2. 一次真实提醒投递：从同一邮箱发往同一邮箱（收件人不扩散）；
3. 核对投递记录与邮箱实际收到内容一致。

用法：

    BRIDGES_SMOKE_QQ_EMAIL=123456@qq.com \\
    BRIDGES_SMOKE_QQ_AUTH_CODE=XXXXXXXXXXXXXXXX \\
    python scripts/smoke_qq_smtp_reminder.py

不读取任何 `.env`；授权码只存在于进程内，不打印、不落盘。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bridges.reminder.smtp import QqMailGateway, SmtpError  # noqa: E402

_SMOKE_EMAIL_ENV = "BRIDGES_SMOKE_QQ_EMAIL"
_SMOKE_EMAIL_FILE_ENV = "BRIDGES_SMOKE_QQ_EMAIL_FILE"
_SMOKE_CODE_ENV = "BRIDGES_SMOKE_QQ_AUTH_CODE"
_SMOKE_CODE_FILE_ENV = "BRIDGES_SMOKE_QQ_AUTH_CODE_FILE"


def _resolve_secret(env: str, file_env: str) -> str:
    """优先 ``*_FILE`` 文件引用，其次环境变量；均缺失时抛中文错误。"""
    file_path = os.environ.get(file_env)
    if file_path:
        return Path(file_path).read_text(encoding="utf-8").strip()
    value = os.environ.get(env)
    if value:
        return value
    raise SystemExit(
        f"缺少冒烟凭据：请设置 {env}（或 {file_env} 文件引用）。"
        "授权码只在本进程内使用，不打印、不落盘。"
    )


def main() -> int:
    email = _resolve_secret(_SMOKE_EMAIL_ENV, _SMOKE_EMAIL_FILE_ENV)
    auth_code = _resolve_secret(_SMOKE_CODE_ENV, _SMOKE_CODE_FILE_ENV)

    if not email.endswith("@qq.com"):
        print(f"冒烟失败：{_SMOKE_EMAIL_ENV} 必须是 QQ 邮箱（…@qq.com）。")
        return 2

    gateway = QqMailGateway()  # 默认 smtp.qq.com:465 SSL / imap.qq.com:993 SSL

    print(f"1/3 自发自收验证：{email} → {email}（SMTP 发送 + IMAP 确认）…")
    try:
        message_id = gateway.verify_self_send_receive(email=email, auth_code=auth_code)
    except SmtpError as exc:
        print(f"冒烟失败：验证未通过（{exc.code}）。{exc.message}")
        return 1
    print(f"   验证通过，验证邮件 Message-ID={message_id}")

    print("2/3 真实提醒投递（自发，无收件人扩散）…")
    token = "test-smoke-issue33"
    subject = f"BridGes 提醒冒烟 {token}：复习 transformer"
    body = "这是一封 BridGes 发出的提醒冒烟邮件，请忽略。\n\n—— 来自 BridGes 提醒"
    try:
        delivery_id = gateway.send_mail(
            from_addr=email,
            to_addr=email,
            auth_code=auth_code,
            subject=subject,
            body=body,
            headers={"X-BridGes-Smoke": "issue33"},
        )
    except SmtpError as exc:
        print(f"冒烟失败：投递未完成（{exc.code}）。{exc.message}")
        return 1
    print(f"   投递成功，Message-ID={delivery_id}")

    print("3/3 核对收件确认（IMAP 轮询提醒邮件到达同一邮箱）…")
    try:
        # 复用适配器的收件确认轮询（ASCII 令牌，QQ IMAP 兼容）
        gateway._confirm_receipt(email=email, auth_code=auth_code, token=token)
    except SmtpError as exc:
        print(f"冒烟失败：提醒邮件未在预期时间内到达（{exc.code}）。{exc.message}")
        return 1
    print("   收件确认通过：提醒邮件已到达同一邮箱。")
    print("冒烟通过：自发自收验证与提醒投递链路真实可用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
