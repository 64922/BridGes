"""本地假邮件服务器（Issue 33 E2E 专用）。

在单个进程内提供：

- 明文 SMTP 服务器（127.0.0.1:SMTP_PORT，默认 8025）：支持
  EHLO/AUTH PLAIN/MAIL/RCPT/DATA/QUIT，接受的邮件进入共享内存邮箱
  （``--deliver-delay-seconds`` 模拟「SMTP 秒收、IMAP 晚到」的晚到邮件：
  立即回 250 OK，延迟后再入箱；``--drop-subject-tokens`` 使命中主题的
  邮件永不入箱，模拟验证超时）；
- 明文 IMAP 服务器（127.0.0.1:IMAP_PORT，默认 8143）：支持
  CAPABILITY/LOGIN/SELECT/SEARCH/LOGOUT，从同一共享邮箱检索主题令牌
  （自发自收验证的收件确认）；
- HTTP 健康端点（127.0.0.1:HTTP_PORT，默认 8026）：``GET /health``
  返回 200，供 Playwright webServer 就绪探测使用。

任一端口的授权码固定为 ``e2etestauthcode33``（与 E2E 测试约定一致，
含 test 标记供秘密扫描白名单识别）。仅用于本地 E2E；生产走 QQ 官方
SSL 端点。
"""

from __future__ import annotations

import argparse
import base64
import email
import http.server
import re
import socketserver
import threading
import time
from email.header import decode_header

MAILBOX: list[dict[str, object]] = []
MAILBOX_LOCK = threading.RLock()
# 测试固定授权码（含 test 标记，秘密扫描白名单；仅本地 E2E 使用）
AUTH_CODES = {"e2etestauthcode33"}

#: 投递延迟（秒）与丢弃令牌：由命令行参数设置，用于模拟晚到邮件与
#: 验证超时（收尾 smoke 的 0 秒/10 秒/超时三种投递边界）。
DELIVER_DELAY_SECONDS = 0.0
DROP_SUBJECT_TOKENS: set[str] = set()

_LINE_RE = re.compile(r"^([^\s:]+)(?:\s+(.*))?$")


def _decode_header_value(value: str) -> str:
    parts: list[str] = []
    for chunk, encoding in decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(encoding or "utf-8", errors="replace"))
        else:
            parts.append(str(chunk))
    return "".join(parts)


class _SmtpHandler(socketserver.StreamRequestHandler):
    """SMTP 会话（AUTH PLAIN / MAIL / RCPT / DATA / QUIT 子集）。"""

    def handle(self) -> None:  # noqa: C901 - 协议状态机分支较多
        self._send("220 fake-smtp BridGes e2e server")
        mail_from: str | None = None
        rcpt_to: list[str] = []
        data_mode = False
        data_buffer: list[str] = []
        authenticated = False
        self._auth_user: str | None = None
        while True:
            raw = self.rfile.readline()
            if not raw:
                break
            if data_mode:
                if raw == b".\r\n":
                    data_mode = False
                    raw_message = "".join(data_buffer)
                    parsed = email.message_from_string(raw_message)
                    subject = _decode_header_value(parsed.get("Subject", "") or "")
                    message_id = parsed.get("Message-ID")
                    # 快照当前会话参数（值绑定，避免异步入箱读取循环内变量）
                    sender = mail_from or ""
                    recipients = list(rcpt_to)
                    delivered_subject = subject
                    delivered_message_id = message_id

                    def _deliver(
                        sender_: str,
                        recipients_: list[str],
                        subject_: str,
                        message_id_: str | None,
                    ) -> None:
                        if DELIVER_DELAY_SECONDS > 0:
                            time.sleep(DELIVER_DELAY_SECONDS)
                        drop = any(
                            token in subject_ for token in DROP_SUBJECT_TOKENS
                        )
                        if not drop:
                            with MAILBOX_LOCK:
                                MAILBOX.append(
                                    {
                                        "sender": sender_,
                                        "recipients": recipients_,
                                        "subject": subject_,
                                        "message_id": message_id_,
                                    }
                                )

                    if DELIVER_DELAY_SECONDS > 0:
                        # 晚到语义：SMTP 立即回 250 OK（投递被接受），入箱按
                        # 延迟在独立线程完成——会话（含后续 QUIT）不因延迟
                        # 阻塞，验证侧收件轮询决定成败。
                        self._send("250 OK queued")
                        threading.Thread(
                            target=_deliver,
                            args=(sender, recipients, delivered_subject, delivered_message_id),
                            daemon=True,
                        ).start()
                    else:
                        # 立即投递：与历史行为完全一致（先入箱再回 250 OK），
                        # 保证 SMTP 客户端返回时邮件已在箱中（无竞态）。
                        _deliver(sender, recipients, delivered_subject, delivered_message_id)
                        self._send("250 OK queued")
                else:
                    data_buffer.append(raw.decode("utf-8", errors="replace"))
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            command, _, arg = line.partition(" ")
            upper = command.upper()
            # AUTH LOGIN 的密码响应是独立 base64 行（无命令前缀）
            if (
                self._auth_user is not None
                and upper not in {"AUTH", "QUIT", "EHLO", "HELO", "RSET"}
            ):
                try:
                    password = base64.b64decode(line).decode("utf-8")
                except Exception:  # noqa: BLE001 - 畸形输入拒绝
                    password = ""
                if password in AUTH_CODES:
                    self._send("235 2.7.0 Authentication successful")
                    authenticated = True
                else:
                    self._send("535 5.7.8 Authentication credentials invalid")
                self._auth_user = None
                continue
            if upper in {"EHLO", "HELO"}:
                self._send("250-fake-smtp")
                self._send("250-AUTH LOGIN PLAIN")
                self._send("250 SIZE 10485760")
            elif upper == "AUTH":
                if arg.upper().startswith("PLAIN"):
                    try:
                        decoded = base64.b64decode(
                            arg[len("PLAIN"):].strip()
                        ).decode("utf-8")
                    except Exception:  # noqa: BLE001 - 畸形输入拒绝
                        decoded = ""
                    parts = decoded.split("\0")
                    if len(parts) == 3 and parts[2] in AUTH_CODES:
                        self._send("235 2.7.0 Authentication successful")
                        authenticated = True
                    else:
                        self._send("535 5.7.8 Authentication credentials invalid")
                elif arg.upper().startswith("LOGIN"):
                    # smtplib 在 PLAIN 被拒后回退 AUTH LOGIN（带初始响应）
                    try:
                        user = base64.b64decode(
                            arg[len("LOGIN"):].strip()
                        ).decode("utf-8")
                    except Exception:  # noqa: BLE001 - 畸形输入拒绝
                        user = ""
                    self._auth_user = user
                    self._send("334 UGFzc3dvcmQ6")  # base64 "Password:"
                elif self._auth_user is not None:
                    try:
                        password = base64.b64decode(arg).decode("utf-8")
                    except Exception:  # noqa: BLE001 - 畸形输入拒绝
                        password = ""
                    if password in AUTH_CODES:
                        self._send("235 2.7.0 Authentication successful")
                        authenticated = True
                    else:
                        self._send("535 5.7.8 Authentication credentials invalid")
                    self._auth_user = None
                else:
                    self._send("503 bad sequence")
            elif upper == "MAIL":
                match = re.match(r"FROM:<([^>]*)>", arg, re.IGNORECASE)
                if authenticated and match:
                    mail_from = match.group(1)
                    self._send("250 OK")
                else:
                    self._send("530 5.7.0 Authentication required")
            elif upper == "RCPT":
                match = re.match(r"TO:<([^>]*)>", arg, re.IGNORECASE)
                if match:
                    rcpt_to.append(match.group(1))
                    self._send("250 OK")
                else:
                    self._send("501 syntax error")
            elif upper == "DATA":
                if not rcpt_to:
                    self._send("503 no recipients")
                else:
                    self._send("354 End data with <CR><LF>.<CR><LF>")
                    data_mode = True
                    data_buffer = []
            elif upper == "QUIT":
                self._send("221 Bye")
                break
            else:
                self._send("502 command not implemented")

    def _send(self, text: str) -> None:
        self.wfile.write(f"{text}\r\n".encode())
        self.wfile.flush()


def _unquote_args(text: str) -> list[str]:
    args: list[str] = []
    current: list[str] = []
    quoted = False
    for char in text:
        if char == '"':
            quoted = not quoted
        elif char.isspace() and not quoted:
            if current:
                args.append("".join(current))
                current = []
        else:
            current.append(char)
    if current:
        args.append("".join(current))
    return args


class _ImapHandler(socketserver.StreamRequestHandler):
    """IMAP 会话（CAPABILITY/LOGIN/SELECT/SEARCH/LOGOUT 子集）。"""

    def handle(self) -> None:  # noqa: C901 - 协议状态机分支较多
        self._send("* OK [CAPABILITY IMAP4rev1] fake-imap BridGes e2e server")
        email_addr: str | None = None
        while True:
            raw = self.rfile.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            match = _LINE_RE.match(line)
            if not match:
                self._send("* BAD parse error")
                continue
            tag = match.group(1)
            rest = (match.group(2) or "").strip()
            upper = rest.upper()
            if upper.startswith("CAPABILITY"):
                self._send("* CAPABILITY IMAP4rev1")
                self._tagged(tag, "OK", "CAPABILITY completed")
            elif upper.startswith("LOGIN"):
                args = _unquote_args(rest[len("LOGIN"):].strip())
                if len(args) == 2 and args[1] in AUTH_CODES:
                    email_addr = args[0]
                    self._tagged(tag, "OK", "LOGIN completed")
                else:
                    self._send(f"{tag} NO LOGIN failed")
            elif upper.startswith("SELECT"):
                self._send("* FLAGS (\\Seen \\Answered)")
                self._send("* OK [UIDVALIDITY 1]")
                self._tagged(tag, "OK", "SELECT completed")
            elif upper.startswith("SEARCH"):
                args = _unquote_args(rest[len("SEARCH"):].strip())
                # 支持 HEADER Message-ID <值>（主键搜索）与 SUBJECT <令牌>（回退）
                token = None
                message_id = None
                for index, part in enumerate(args):
                    if part.upper() == "SUBJECT" and index + 1 < len(args):
                        token = args[index + 1]
                    elif (
                        part.upper() == "HEADER"
                        and index + 2 < len(args)
                        and args[index + 1].upper() == "MESSAGE-ID"
                    ):
                        message_id = args[index + 2]
                matches: list[int] = []
                if email_addr is not None:
                    with MAILBOX_LOCK:
                        if message_id is not None:
                            matches = [
                                index + 1
                                for index, message in enumerate(MAILBOX)
                                if email_addr in message["recipients"]
                                and message.get("message_id") == message_id
                            ]
                        elif token is not None:
                            matches = [
                                index + 1
                                for index, message in enumerate(MAILBOX)
                                if email_addr in message["recipients"]
                                and token in str(message.get("subject", ""))
                            ]
                self._send(f"* SEARCH {' '.join(str(i) for i in matches)}")
                self._tagged(tag, "OK", "SEARCH completed")
            elif upper.startswith("NOOP"):
                # 验证轮询的保活命令（issue 10：同一 IMAP 会话 + NOOP）
                self._tagged(tag, "OK", "NOOP completed")
            elif upper.startswith("LOGOUT"):
                self._send("* BYE fake-imap logging out")
                self._tagged(tag, "OK", "LOGOUT completed")
                break
            else:
                self._send(f"{tag} BAD command not implemented")

    def _tagged(self, tag: str, code: str, text: str = "") -> None:
        suffix = f" {text}" if text else ""
        self._send(f"{tag} {code} completed{suffix}")

    def _send(self, text: str) -> None:
        self.wfile.write(f"{text}\r\n".encode())
        self.wfile.flush()


class _SmtpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _ImapServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server 命名约定
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args: object) -> None:  # noqa: A003 - 基类签名
        pass


def main() -> int:
    global DELIVER_DELAY_SECONDS, DROP_SUBJECT_TOKENS
    parser = argparse.ArgumentParser(description="本地假邮件服务器（E2E）")
    parser.add_argument("--smtp-port", type=int, default=8025)
    parser.add_argument("--imap-port", type=int, default=8143)
    parser.add_argument("--http-port", type=int, default=8026)
    parser.add_argument(
        "--deliver-delay-seconds",
        type=float,
        default=0.0,
        help="模拟 SMTP 投递延迟（秒）；0 表示立即投递。",
    )
    parser.add_argument(
        "--drop-subject-tokens",
        default="",
        help="逗号分隔的主题令牌；命中则邮件不进入邮箱（模拟验证超时）。",
    )
    args = parser.parse_args()
    DELIVER_DELAY_SECONDS = args.deliver_delay_seconds
    DROP_SUBJECT_TOKENS = {
        token.strip()
        for token in args.drop_subject_tokens.split(",")
        if token.strip()
    }

    smtp = _SmtpServer(("127.0.0.1", args.smtp_port), _SmtpHandler)
    imap = _ImapServer(("127.0.0.1", args.imap_port), _ImapHandler)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", args.http_port), _HealthHandler)
    threads = [
        threading.Thread(target=smtp.serve_forever, daemon=True),
        threading.Thread(target=imap.serve_forever, daemon=True),
        threading.Thread(target=httpd.serve_forever, daemon=True),
    ]
    for thread in threads:
        thread.start()
    print(
        f"fake mail server: smtp=127.0.0.1:{args.smtp_port} "
        f"imap=127.0.0.1:{args.imap_port} health=127.0.0.1:{args.http_port} "
        f"delay={DELIVER_DELAY_SECONDS}s drop={sorted(DROP_SUBJECT_TOKENS)}",
        flush=True,
    )
    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
