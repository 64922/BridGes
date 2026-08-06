"""Issue 39 AC8：MCP 权限提升与秘密读取防护回归测试。

覆盖新增加固点：
1. 符号链接逃逸：允许目录内放置指向外部的链接，read/write 必须拒绝；
2. 重定向跟随：允许域名内的 URL 被 302 到未授权目标，必须拒绝跟随；
3. 工作目录隔离：MCP 子进程 cwd 固定到受限目录，不继承宿主工作区。
"""

from __future__ import annotations

import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from bridges.contracts.mcp import McpPermissionManifest
from bridges.mcp.process import McpProcessClient
from bridges.mcp.runtime import McpRuntime
from bridges.mcp.service import McpService, _no_redirect_opener
from bridges.storage.database import BridgesDatabase
from bridges.storage.object_store import EncryptedFileObjectStore
from bridges.storage.repository import BridgesObjectRepository

PYTHON = sys.executable

# 环境秘密：验证任何路径都不泄漏。
os.environ["BRIDGES_QWEN_API_KEY"] = "sk-mcp-hardening-secret"
os.environ["BRIDGES_SMTP_AUTH_CODE"] = "qq-mcp-hardening-code"
os.environ["MCP_INTERNAL_MASTER_KEY"] = "internal-hardening-key"


class _RecordingObservability:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def log_audit(self, **kwargs: Any) -> None:
        self.events.append(kwargs)


def _make_service(tmp_path: Any) -> tuple[McpService, str]:
    database = BridgesDatabase(":memory:")
    database.initialize()
    objects = BridgesObjectRepository(
        database,
        EncryptedFileObjectStore(tmp_path / "objects", encryption_key="hardening-key"),
    )
    account_id = objects.register_account("acc@example.com")
    service = McpService(
        database=database,
        object_repository=objects,
        observability_service=_RecordingObservability(),  # type: ignore[arg-type]
        runtime=McpRuntime(pid_dir=tmp_path / "mcp-pids"),
    )
    return service, account_id


def _manifest(read_dirs: list[str], write_dirs: list[str] | None = None) -> McpPermissionManifest:
    return McpPermissionManifest(
        network_domains=[],
        filesystem_read=read_dirs,
        filesystem_write=write_dirs or [],
        external_commands=[],
        data_categories=["current_message_text"],
        sensitive_operations=[],
    )


# ---------------------------------------------------------------------------
# 1. 符号链接逃逸
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="符号链接在 Windows 需要管理员权限")
def test_symlink_read_escape_rejected(tmp_path: Any) -> None:
    """允许目录内的符号链接指向外部文件：read_file 必须拒绝。"""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("top-secret-content", encoding="utf-8")
    link = allowed / "escape.txt"
    link.symlink_to(outside)

    service, account_id = _make_service(tmp_path)
    outcome = service._tool_handler(
        account_id,
        "mcp-test",
        "1",
        _manifest([str(allowed)]),
        "read_file",
        {"path": str(link)},
        False,
    )
    assert not outcome.ok
    assert outcome.error_code == "permission_denied"


@pytest.mark.skipif(os.name == "nt", reason="符号链接在 Windows 需要管理员权限")
def test_symlink_write_escape_rejected(tmp_path: Any) -> None:
    """允许写入目录内的符号链接指向外部：write_file 必须拒绝。"""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "target"
    outside.mkdir()
    victim = outside / "victim.txt"
    victim.write_text("original", encoding="utf-8")
    link = allowed / "escape"
    link.symlink_to(outside, target_is_directory=True)

    service, account_id = _make_service(tmp_path)
    outcome = service._tool_handler(
        account_id,
        "mcp-test",
        "1",
        _manifest([], [str(allowed)]),
        "write_file",
        {"path": str(link / "victim.txt"), "content": "overwritten"},
        True,  # force=确认后也必须拒绝（路径闭锁先于敏感确认执行）
    )
    assert not outcome.ok
    assert outcome.error_code == "permission_denied"
    assert victim.read_text(encoding="utf-8") == "original"


def test_symlink_escape_rejected_with_resolved_realpath(tmp_path: Any, monkeypatch: Any) -> None:
    """符号链接逃逸（全平台模拟）：词法路径在允许目录内、真实目标越界必须拒绝。

    不依赖真实符号链接（Windows 无管理员权限不可用）：monkeypatch
    ``os.path.realpath`` 精确模拟「允许目录内的链接指向外部文件」的解析
    结果，验证只按真实路径比较的闭锁逻辑。
    """
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("top-secret-content", encoding="utf-8")
    link = str(allowed / "escape.txt")
    real_outside = os.path.normcase(str(outside.resolve()))

    original_realpath = os.path.realpath

    def _fake_realpath(path: str) -> str:
        # 模拟链接：escape.txt 词法上在 allowed 内，解析后指向外部文件
        if os.path.normcase(str(path)) == os.path.normcase(link):
            return real_outside
        return os.path.normcase(original_realpath(path))

    monkeypatch.setattr(os.path, "realpath", _fake_realpath)

    service, account_id = _make_service(tmp_path)
    outcome = service._tool_handler(
        account_id,
        "mcp-test",
        "1",
        _manifest([str(allowed)]),
        "read_file",
        {"path": link},
        False,
    )
    assert not outcome.ok
    assert outcome.error_code == "permission_denied"


def test_symlink_write_escape_rejected_with_resolved_realpath(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """符号链接逃逸（写入方向）：真实目标越界必须拒绝且不产生写入。"""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "target"
    outside.mkdir()
    victim = outside / "victim.txt"
    victim.write_text("original", encoding="utf-8")
    link_path = str(allowed / "escape" / "victim.txt")
    real_victim = os.path.normcase(str(victim.resolve()))

    original_realpath = os.path.realpath

    def _fake_realpath(path: str) -> str:
        if os.path.normcase(str(path)) == os.path.normcase(link_path):
            return real_victim
        return os.path.normcase(original_realpath(path))

    monkeypatch.setattr(os.path, "realpath", _fake_realpath)

    service, account_id = _make_service(tmp_path)
    outcome = service._tool_handler(
        account_id,
        "mcp-test",
        "1",
        _manifest([], [str(allowed)]),
        "write_file",
        {"path": link_path, "content": "overwritten"},
        True,
    )
    assert not outcome.ok
    assert outcome.error_code == "permission_denied"
    assert victim.read_text(encoding="utf-8") == "original"


def test_direct_path_outside_allowed_dir_rejected(tmp_path: Any) -> None:
    """绝对路径但不在允许目录内：拒绝（基线防护不回归）。"""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "other"
    outside.mkdir()
    (outside / "f.txt").write_text("x", encoding="utf-8")

    service, account_id = _make_service(tmp_path)
    outcome = service._tool_handler(
        account_id,
        "mcp-test",
        "1",
        _manifest([str(allowed)]),
        "read_file",
        {"path": str(outside / "f.txt")},
        False,
    )
    assert not outcome.ok
    assert outcome.error_code == "permission_denied"


def test_allowed_directory_inside_manifest_still_works(tmp_path: Any) -> None:
    """允许目录内的正常文件读写不受影响（加固不误伤合法路径）。"""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "ok.txt").write_text("fine", encoding="utf-8")

    service, account_id = _make_service(tmp_path)
    outcome = service._tool_handler(
        account_id,
        "mcp-test",
        "1",
        _manifest([str(allowed)]),
        "read_file",
        {"path": str(allowed / "ok.txt")},
        False,
    )
    assert outcome.ok
    assert outcome.result["content"] == "fine"


# ---------------------------------------------------------------------------
# 2. 重定向跟随闭锁
# ---------------------------------------------------------------------------


class _RedirectHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server 命名
        self.send_response(302)
        self.send_header("Location", "http://127.0.0.1:1/unreachable")
        self.end_headers()

    def log_message(self, *args: Any) -> None:  # 静默测试服务器日志
        pass


def test_redirect_following_rejected() -> None:
    """允许域名返回 302 重定向：必须拒绝跟随，绝不访问重定向目标。"""
    server = HTTPServer(("127.0.0.1", 0), _RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with pytest.raises(Exception) as exc_info:
            _no_redirect_opener().open(f"http://127.0.0.1:{port}/redirect", timeout=5)
        # HTTPError 302 = 重定向被显式禁止（未跟随到 Location 目标）
        assert "302" in str(exc_info.value)
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_url_allowed_requires_https() -> None:
    """http 明文目标一律拒绝（仅 https 允许，防内网明文嗅探/SSRF）。"""
    from bridges.mcp.service import _url_allowed

    assert _url_allowed("https://example.com/path", ["example.com"]) is True
    assert _url_allowed("http://example.com/path", ["example.com"]) is False
    assert _url_allowed("http://127.0.0.1:8000/", ["127.0.0.1"]) is False
    assert _url_allowed("https://other.com/", ["example.com"]) is False
    assert _url_allowed("file:///etc/passwd", ["file"]) is False


# ---------------------------------------------------------------------------
# 3. 工作目录隔离
# ---------------------------------------------------------------------------


def test_mcp_process_cwd_is_restricted(tmp_path: Any) -> None:
    """MCP 子进程 cwd 必须等于指定的受限目录，而不是宿主工作目录。"""
    server_code = (
        "import json, os, sys\n"
        "print(json.dumps({'ok': True, 'cwd': os.getcwd()}), flush=True)\n"
        "for line in sys.stdin:\n"
        "    msg = json.loads(line)\n"
        "    if msg.get('method') == 'invoke':\n"
        "        print(json.dumps({'id': msg['id'], 'type': 'result',"
        " 'ok': True, 'result': {'cwd': os.getcwd()}}), flush=True)\n"
    )
    script = tmp_path / "cwd_server.py"
    script.write_text(server_code, encoding="utf-8")
    work_dir = tmp_path / "mcp-work"
    work_dir.mkdir()

    client = McpProcessClient(
        command=[PYTHON, str(script)],
        cwd=str(work_dir),
    )
    client.start()
    try:
        status, result = client.invoke(
            request_id="cwd-1",
            tool="echo",
            input_data={},
            data_slice={},
            permissions=McpPermissionManifest(data_categories=[]),
            handler=lambda tool, args, force: None,
        )
        assert status == "success"
        assert result["cwd"] == str(work_dir)
    finally:
        client.close()


def test_runtime_passes_pid_dir_as_cwd(tmp_path: Any) -> None:
    """McpRuntime 启动进程时以 MCP 专用目录为 cwd（数据目录下，非宿主工作区）。"""
    pid_dir = tmp_path / "mcp-pids"
    server_code = (
        "import json, os, sys\n"
        "print(json.dumps({'ok': True, 'cwd': os.getcwd()}), flush=True)\n"
        "for line in sys.stdin:\n"
        "    msg = json.loads(line)\n"
        "    if msg.get('method') == 'invoke':\n"
        "        print(json.dumps({'id': msg['id'], 'type': 'result',"
        " 'ok': True, 'result': {'cwd': os.getcwd()}}), flush=True)\n"
    )
    script = tmp_path / "cwd_server2.py"
    script.write_text(server_code, encoding="utf-8")

    runtime = McpRuntime(pid_dir=pid_dir)
    client = runtime.get_or_start("acc-1", "mcp-1", [PYTHON, str(script)])
    try:
        assert client.is_alive()
        status, result = client.invoke(
            request_id="cwd-2",
            tool="echo",
            input_data={},
            data_slice={},
            permissions=McpPermissionManifest(data_categories=[]),
            handler=lambda tool, args, force: None,
        )
        assert status == "success"
        assert result["cwd"] == str(pid_dir)
    finally:
        runtime.stop("acc-1", "mcp-1")
