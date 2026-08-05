"""进程内假 SMTP + 假 IMAP 服务器（Issue 33 测试用）。

实现 SMTP（``smtplib`` 客户端协议子集：EHLO/AUTH LOGIN/MAIL/RCPT/DATA/
QUIT）与 IMAP（``imaplib`` 客户端协议子集：LOGIN/SELECT/SEARCH/LOGOUT）
的最小服务器，共享同一个内存邮箱存储，模拟 QQ 邮箱的「自发自收」：

- SMTP ``DATA`` 收到的邮件写入共享邮箱（按收件人 + 主题令牌索引）；
- IMAP ``SEARCH SUBJECT <token>`` 从共享邮箱查找，验证流程据此确认
  收件到达。

可控性：
- ``auth_codes``：合法授权码集合；登录拒绝（535/NO）用于授权失效路径；
- ``reject_rcpt``：RCPT 550 拒绝路径（smtp_rejected）；
- ``deliver_delay_seconds``：投递延迟模拟（验证超时路径）；
- ``drop_subject_tokens``：指定主题令牌不投递（验证失败路径）。

只用于测试与本地 E2E 假邮件服务器（``scripts/e2e_mail_server.py``
保持同样协议，供 playwright 启动）。
"""

from __future__ import annotations

import base64
import email
import re
import socket
import socketserver
import threading
import time
from dataclasses import dataclass, field
from email.header import decode_header

_LINE_RE = re.compile(r"^([^\s:]+)(?:\s+(.*))?$")


@dataclass
class StoredMessage:
    """SMTP 接收并进入共享邮箱的一封邮件。"""

    sender: str
    recipients: list[str]
    subject: str
    body: str
    message_id: str | None = None
    received_at: float = field(default_factory=time.time)


class FakeMailbox:
    """SMTP/IMAP 共享的内存邮箱（按邮箱地址与主题令牌索引）。"""

    def __init__(
        self,
        *,
        auth_codes: set[str] | None = None,
        reject_rcpt: set[str] | None = None,
        deliver_delay_seconds: float = 0.0,
        drop_subject_tokens: set[str] | None = None,
        drop_all_messages: bool = False,
    ) -> None:
        self.auth_codes = auth_codes or {"VALIDCODE"}
        self.reject_rcpt = reject_rcpt or set()
        self.deliver_delay_seconds = deliver_delay_seconds
        self.drop_subject_tokens = drop_subject_tokens or set()
        self.drop_all_messages = drop_all_messages
        self.messages: list[StoredMessage] = []
        self._lock = threading.RLock()

    def add(self, message: StoredMessage) -> None:
        with self._lock:
            self.messages.append(message)

    def find_by_subject(self, email: str, token: str) -> list[StoredMessage]:
        with self._lock:
            return [
                message
                for message in self.messages
                if email in message.recipients and token in message.subject
            ]


def _decode_header_value(value: str) -> str:
    """解码 RFC 2047 编码的邮件头值（如 ``=?utf-8?b?...?=``）。"""
    parts: list[str] = []
    for chunk, encoding in decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(encoding or "utf-8", errors="replace"))
        else:
            parts.append(str(chunk))
    return "".join(parts)


def _parse_auth_arg(raw: str) -> str | None:
    """解析 AUTH LOGIN 的 base64 参数；非法返回 None。"""
    try:
        return base64.b64decode(raw).decode("utf-8")
    except Exception:  # noqa: BLE001 - 客户端畸形输入直接拒绝
        return None


class _SmtpHandler(socketserver.StreamRequestHandler):
    """SMTP 会话处理器（AUTH LOGIN / MAIL / RCPT / DATA / QUIT 子集）。"""

    def handle(self) -> None:  # noqa: C901 - 协议状态机分支较多
        mailbox: FakeMailbox = self.server.mailbox  # type: ignore[attr-defined]
        self._send("220 fake-smtp BridGes test server")
        mail_from: str | None = None
        rcpt_to: list[str] = []
        data_mode = False
        data_buffer: list[str] = []
        authenticated = False
        auth_user: str | None = None
        auth_pending: str | None = None  # "user" | "pass"
        while True:
            raw = self.rfile.readline()
            if not raw:
                break
            if data_mode:
                if raw == b".\r\n":
                    data_mode = False
                    # 每行已含行尾 \r\n（rfile.readline 保留），直接拼接
                    raw_message = "".join(data_buffer)
                    parsed = email.message_from_string(raw_message)
                    subject = _decode_header_value(parsed.get("Subject", "") or "")
                    message_id = parsed.get("Message-ID")
                    if mailbox.deliver_delay_seconds > 0:
                        time.sleep(mailbox.deliver_delay_seconds)
                    drop = mailbox.drop_all_messages or any(
                        token in subject
                        for token in mailbox.drop_subject_tokens
                    )
                    if not drop:
                        mailbox.add(
                            StoredMessage(
                                sender=mail_from or "",
                                recipients=list(rcpt_to),
                                subject=subject,
                                body=raw_message,
                                message_id=message_id,
                            )
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
            if upper == "EHLO" or upper == "HELO":
                self._send("250-fake-smtp")
                self._send("250-AUTH LOGIN PLAIN")
                self._send("250 SIZE 10485760")
            elif upper == "AUTH":
                if arg.upper().startswith("PLAIN"):
                    # smtplib 默认使用 AUTH PLAIN 初始响应：
                    # base64("user\0user\0password")
                    decoded = _parse_auth_arg(arg[len("PLAIN"):].strip())
                    parts = decoded.split("\0") if decoded else []
                    if len(parts) == 3 and parts[2] in mailbox.auth_codes:
                        self._send("235 2.7.0 Authentication successful")
                        authenticated = True
                    else:
                        self._send("535 5.7.8 Authentication credentials invalid")
                elif arg.startswith("LOGIN"):
                    rest = arg[len("LOGIN"):].strip()
                    if rest:
                        auth_user = _parse_auth_arg(rest)
                        self._send("334 UGFzc3dvcmQ6")  # base64 "Password:"
                    else:
                        self._send("334 VXNlcm5hbWU6")  # base64 "Username:"
                        auth_pending = "user"
                elif auth_pending == "user":
                    auth_user = _parse_auth_arg(arg)
                    self._send("334 UGFzc3dvcmQ6")
                    auth_pending = "pass"
                elif auth_pending == "pass":
                    password = _parse_auth_arg(arg)
                    if auth_user is not None and (
                        password in mailbox.auth_codes
                    ):
                        self._send("235 2.7.0 Authentication successful")
                        authenticated = True
                        auth_pending = None
                    else:
                        self._send("535 5.7.8 Authentication credentials invalid")
                        auth_pending = None
                else:
                    self._send("503 bad sequence")
            elif upper == "MAIL":
                from_match = re.match(r"FROM:<([^>]*)>", arg, re.IGNORECASE)
                if authenticated and from_match:
                    mail_from = from_match.group(1)
                    self._send("250 OK")
                else:
                    self._send("530 5.7.0 Authentication required")
            elif upper == "RCPT":
                to_match = re.match(r"TO:<([^>]*)>", arg, re.IGNORECASE)
                if to_match:
                    rcpt = to_match.group(1)
                    if rcpt in mailbox.reject_rcpt:
                        self._send("550 5.1.1 No such recipient")
                    else:
                        rcpt_to.append(rcpt)
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
            elif upper == "RSET":
                mail_from = None
                rcpt_to = []
                data_buffer = []
                self._send("250 OK")
            elif upper == "QUIT":
                self._send("221 Bye")
                break
            else:
                self._send("502 command not implemented")

    def _send(self, text: str) -> None:
        self.wfile.write(f"{text}\r\n".encode())
        self.wfile.flush()


class _ImapHandler(socketserver.StreamRequestHandler):
    """IMAP 会话处理器（LOGIN/SELECT/SEARCH/LOGOUT 子集）。"""

    def _tagged(self, tag: str, code: str, text: str = "") -> None:
        suffix = f" {text}" if text else ""
        self._send(f"{tag} {code} completed{suffix}")

    def handle(self) -> None:  # noqa: C901 - 协议状态机分支较多
        mailbox: FakeMailbox = self.server.mailbox  # type: ignore[attr-defined]
        self._send("* OK [CAPABILITY IMAP4rev1] fake-imap BridGes test server")
        email: str | None = None
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
                if len(args) == 2 and args[1] in mailbox.auth_codes:
                    email = args[0]
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
                matches = []
                if token is not None and email is not None:
                    found = mailbox.find_by_subject(email, token)
                    matches = list(range(1, len(found) + 1))
                self._send(f"* SEARCH {' '.join(str(i) for i in matches)}")
                self._tagged(tag, "OK", "SEARCH completed")
            elif upper.startswith("LOGOUT"):
                self._send("* BYE fake-imap logging out")
                self._tagged(tag, "OK", "LOGOUT completed")
                break
            else:
                self._send(f"{tag} BAD command not implemented")

    def _send(self, text: str) -> None:
        self.wfile.write(f"{text}\r\n".encode())
        self.wfile.flush()


def _unquote_args(text: str) -> list[str]:
    """拆分带引号/不带引号的命令参数（imaplib 的 QUOTED 参数形式）。"""
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


class _SmtpServer(socketserver.ThreadingTCPServer):
    """SMTP TCP 服务器（IPv4 回环，测试专用）。"""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], mailbox: FakeMailbox) -> None:
        super().__init__(address, _SmtpHandler)
        self.mailbox = mailbox


class _ImapServer(socketserver.ThreadingTCPServer):
    """IMAP TCP 服务器（IPv4 回环，测试专用）。"""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], mailbox: FakeMailbox) -> None:
        super().__init__(address, _ImapHandler)
        self.mailbox = mailbox


class FakeMailServers:
    """一组共享邮箱的假 SMTP+IMAP 服务器（with 用法自动启停）。"""

    def __init__(self, mailbox: FakeMailbox | None = None) -> None:
        self.mailbox = mailbox or FakeMailbox()
        self.smtp: _SmtpServer | None = None
        self.imap: _ImapServer | None = None

    @property
    def smtp_port(self) -> int:
        assert self.smtp is not None
        return self.smtp.server_address[1]

    @property
    def imap_port(self) -> int:
        assert self.imap is not None
        return self.imap.server_address[1]

    def __enter__(self) -> FakeMailServers:
        self.smtp = _SmtpServer(("127.0.0.1", 0), self.mailbox)
        self.imap = _ImapServer(("127.0.0.1", 0), self.mailbox)
        self.smtp_thread = threading.Thread(target=self.smtp.serve_forever, daemon=True)
        self.imap_thread = threading.Thread(target=self.imap.serve_forever, daemon=True)
        self.smtp_thread.start()
        self.imap_thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        assert self.smtp is not None and self.imap is not None
        self.smtp.shutdown()
        self.imap.shutdown()
        self.smtp.server_close()
        self.imap.server_close()

    def connectable(self) -> bool:
        """探测两个端口是否可连接（供测试断言服务器就绪）。"""
        try:
            with socket.create_connection(("127.0.0.1", self.smtp_port), 1.0):
                pass
            with socket.create_connection(("127.0.0.1", self.imap_port), 1.0):
                pass
            return True
        except OSError:
            return False


__all__ = [
    "FakeMailServers",
    "FakeMailbox",
    "StoredMessage",
]
