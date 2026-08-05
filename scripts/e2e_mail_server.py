"""本地假邮件服务器（Issue 33 E2E 专用）。

在单个进程内提供：

- 明文 SMTP 服务器（127.0.0.1:SMTP_PORT，默认 8025）：支持
  EHLO/AUTH PLAIN/MAIL/RCPT/DATA/QUIT，接受的邮件进入共享内存邮箱；
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
from email.header import decode_header

MAILBOX: list[dict[str, object]] = []
MAILBOX_LOCK = threading.RLock()
# 测试固定授权码（含 test 标记，秘密扫描白名单；仅本地 E2E 使用）
AUTH_CODES = {"e2etestauthcode33"}

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
                    with MAILBOX_LOCK:
                        MAILBOX.append(
                            {
                                "sender": mail_from or "",
                                "recipients": list(rcpt_to),
                                "subject": subject,
                                "message_id": message_id,
                            }
                        )
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
                token = None
                for index, part in enumerate(args):
                    if part.upper() == "SUBJECT" and index + 1 < len(args):
                        token = args[index + 1]
                matches: list[int] = []
                if token is not None and email_addr is not None:
                    with MAILBOX_LOCK:
                        matches = [
                            index + 1
                            for index, message in enumerate(MAILBOX)
                            if email_addr in message["recipients"]
                            and token in str(message.get("subject", ""))
                        ]
                self._send(f"* SEARCH {' '.join(str(i) for i in matches)}")
                self._tagged(tag, "OK", "SEARCH completed")
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
    parser = argparse.ArgumentParser(description="本地假邮件服务器（E2E）")
    parser.add_argument("--smtp-port", type=int, default=8025)
    parser.add_argument("--imap-port", type=int, default=8143)
    parser.add_argument("--http-port", type=int, default=8026)
    args = parser.parse_args()

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
        f"imap=127.0.0.1:{args.imap_port} health=127.0.0.1:{args.http_port}",
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
