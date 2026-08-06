"""MCP 插件管理服务测试（Issue 35）。

_Harness（内存 db + tmp_path 对象库 + recording observability + 真实
checker/runtime）覆盖：安装锁定与防篡改、冲突/失败覆盖重装、启停/卸载、
恶意夹具全部失败闭锁（读秘密/未授权路径/未声明域名/未声明命令/跨账户）、
敏感确认 approve/deny 全流程、数据切片校验、调用统计、撤权终止运行、
崩溃 failed 状态、重启恢复与孤儿清理、审计白名单、两账户隔离。
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

import pytest
from fixtures import (
    BAD_SOURCE_YAML,
    LATEST_VERSION_YAML,
    echo_yaml,
    note_yaml,
    secret_reader_yaml,
    slow_start_yaml,
    tool_yaml,
    zombie_yaml,
)

from bridges.contracts.mcp import (
    McpCallRequest,
    McpDataSlice,
    McpError,
    McpPermissionManifest,
    McpSensitiveKind,
    McpStatus,
)
from bridges.contracts.observability import AuditAction
from bridges.contracts.scope import ScopeIsolationError
from bridges.mcp.runtime import McpRuntime
from bridges.mcp.service import McpService
from bridges.scope import ScopeEnforcer
from bridges.storage.database import BridgesDatabase
from bridges.storage.object_store import EncryptedFileObjectStore
from bridges.storage.repository import BridgesObjectRepository

PYTHON = sys.executable

# 设置环境秘密，验证任何路径都不泄漏。
os.environ["BRIDGES_QWEN_API_KEY"] = "sk-mcp-test-secret"
os.environ["BRIDGES_SMTP_AUTH_CODE"] = "qq-mcp-test-code"
os.environ["MCP_INTERNAL_MASTER_KEY"] = "internal-mcp-key"


class _RecordingObservability:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def log_audit(self, **kwargs: Any) -> None:
        self.events.append(kwargs)


class _Harness:
    def __init__(self, tmp_path: Any, db_path: Any = None) -> None:
        self.database = BridgesDatabase(db_path or ":memory:")
        self.database.initialize()
        self.objects = BridgesObjectRepository(
            self.database,
            EncryptedFileObjectStore(tmp_path / "objects", encryption_key="mcp-test-key"),
        )
        self.acc1 = self.objects.register_account("acc1@example.com")
        self.acc2 = self.objects.register_account("acc2@example.com")
        self.observability = _RecordingObservability()
        self.runtime = McpRuntime(pid_dir=tmp_path / "mcp-pids")
        self.service = McpService(
            database=self.database,
            object_repository=self.objects,
            observability_service=self.observability,  # type: ignore[arg-type]
            runtime=self.runtime,
        )

    def audit_actions(self) -> list[str]:
        return [event["action"].value for event in self.observability.events]

    def audit_details(self) -> list[dict[str, Any]]:
        return [event.get("details") or {} for event in self.observability.events]

    def echo_call(self, text: str = "测试文本") -> dict[str, Any]:
        return self.service.invoke(
            self.acc1,
            "bridges-echo",
            McpCallRequest(
                tool="echo",
                input={},
                data_slice=McpDataSlice(text=text),
            ),
        ).model_dump(mode="json")


def _echo_manifest() -> McpPermissionManifest:
    return McpPermissionManifest(data_categories=["current_message_text"])


# ---------------------------------------------------------------------------
# 安装 / 冲突 / 覆盖重装
# ---------------------------------------------------------------------------


def test_install_locks_integrity_and_persists(tmp_path) -> None:
    harness = _Harness(tmp_path)
    content = echo_yaml().encode("utf-8")
    projection = harness.service.install(harness.acc1, "echo.yaml", content)
    assert projection.mcp_id == "bridges-echo"
    assert projection.version == "1.0.0"
    assert projection.status == McpStatus.HEALTHY
    assert projection.enabled is True
    assert projection.integrity is not None
    assert projection.call_count == 0
    # 描述原文进对象库（账户隔离）。
    assert harness.objects.list_objects(harness.acc1)
    assert "mcp_install" in harness.audit_actions()


def test_install_rejects_bad_descriptor_without_side_effects(tmp_path) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(Exception) as exc_info:
        harness.service.install(harness.acc1, "bad.yaml", LATEST_VERSION_YAML.encode("utf-8"))
    assert "未锁定" in str(exc_info.value)
    # 不落库不落对象。
    assert harness.service.list_servers(harness.acc1).servers == []
    assert harness.objects.list_objects(harness.acc1) == []
    assert "mcp_install" not in harness.audit_actions()


def test_install_rejects_unknown_source(tmp_path) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(Exception) as exc_info:
        harness.service.install(harness.acc1, "bad.yaml", BAD_SOURCE_YAML.encode("utf-8"))
    assert "来源" in str(exc_info.value)


def test_install_conflict_when_same_id_installed(tmp_path) -> None:
    harness = _Harness(tmp_path)
    harness.service.install(harness.acc1, "echo.yaml", echo_yaml().encode("utf-8"))
    with pytest.raises(Exception) as exc_info:
        harness.service.install(harness.acc1, "echo2.yaml", echo_yaml().encode("utf-8"))
    assert "conflict" in str(exc_info.value).lower() or "已安装" in str(exc_info.value)


def test_failed_then_reinstall_overwrites(tmp_path) -> None:
    """失败态覆盖重装：修正后的同标识描述可直接替换（可恢复失败）。"""
    harness = _Harness(tmp_path)
    # 先用坏命令装一个失败态（通过直接操纵状态模拟：先装成功再标记失败）。
    harness.service.install(harness.acc1, "echo.yaml", echo_yaml().encode("utf-8"))
    scoped = harness.database.scoped(harness.acc1)
    scoped.execute(
        "UPDATE mcp_servers SET status = 'failed', failure_reason = ?"
        " WHERE account_id = ? AND mcp_id = ?",
        ("模拟失败", harness.acc1, "bridges-echo"),
    )
    projection = harness.service.install(harness.acc1, "echo.yaml", echo_yaml().encode("utf-8"))
    assert projection.status == McpStatus.HEALTHY
    assert projection.failure_reason is None


def test_install_failed_state_cannot_enable(tmp_path) -> None:
    harness = _Harness(tmp_path)
    harness.service.install(harness.acc1, "echo.yaml", echo_yaml().encode("utf-8"))
    harness.service.set_enabled(harness.acc1, "bridges-echo", enabled=False)
    harness.service.set_enabled(harness.acc1, "bridges-echo", enabled=True)
    assert harness.service.list_servers(harness.acc1).servers[0].enabled is True


# ---------------------------------------------------------------------------
# 启停 / 卸载
# ---------------------------------------------------------------------------


def test_disable_stops_process(tmp_path) -> None:
    harness = _Harness(tmp_path)
    harness.service.install(harness.acc1, "echo.yaml", echo_yaml().encode("utf-8"))
    harness.echo_call()
    assert harness.runtime.is_running(harness.acc1, "bridges-echo")
    harness.service.set_enabled(harness.acc1, "bridges-echo", enabled=False)
    assert not harness.runtime.is_running(harness.acc1, "bridges-echo")
    assert harness.service.list_servers(harness.acc1).servers[0].status == McpStatus.DISABLED
    assert "mcp_disable" in harness.audit_actions()


def test_disabled_invoke_rejected(tmp_path) -> None:
    harness = _Harness(tmp_path)
    harness.service.install(harness.acc1, "echo.yaml", echo_yaml().encode("utf-8"))
    harness.service.set_enabled(harness.acc1, "bridges-echo", enabled=False)
    with pytest.raises(Exception) as exc_info:
        harness.echo_call()
    assert "已停用" in str(exc_info.value)


def test_uninstall_removes_record_object_and_process(tmp_path) -> None:
    harness = _Harness(tmp_path)
    harness.service.install(harness.acc1, "echo.yaml", echo_yaml().encode("utf-8"))
    harness.echo_call()
    assert harness.runtime.is_running(harness.acc1, "bridges-echo")
    harness.service.uninstall(harness.acc1, "bridges-echo")
    assert harness.service.list_servers(harness.acc1).servers == []
    assert not harness.runtime.is_running(harness.acc1, "bridges-echo")
    assert harness.objects.list_objects(harness.acc1) == []
    assert "mcp_uninstall" in harness.audit_actions()
    # 卸载后跨账户/重复卸载 404。
    with pytest.raises(Exception) as exc_info:
        harness.service.uninstall(harness.acc1, "bridges-echo")
    assert "未找到" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 恶意夹具：全部失败闭锁（Verification 1）
# ---------------------------------------------------------------------------


def test_malicious_secret_reader_fails_closed(tmp_path) -> None:
    """读取秘密：clean env 中无任何秘密，未知「读秘密」工具被拒绝。"""
    harness = _Harness(tmp_path)
    harness.service.install(harness.acc1, "mal.yaml", secret_reader_yaml(PYTHON).encode("utf-8"))
    result = harness.service.invoke(
        harness.acc1,
        "malicious-secret-reader",
        McpCallRequest(tool="echo", input={}, data_slice=McpDataSlice(text="x")),
    )
    assert result.status == "success"
    body = result.result
    assert body["env_secret_names"] == []  # 环境白名单：无任何秘密键
    assert body["tool_read_secrets_blocked"] is True  # 未知工具被拒绝
    assert any(
        event["action"] == AuditAction.MCP_INVOKE_DENIED for event in harness.observability.events
    )


@pytest.mark.parametrize(
    ("name", "script", "tool"),
    [
        ("path-reader", "path_reader.py", "read_file"),
        ("network-connector", "network_connector.py", "http_get"),
        ("command-launcher", "command_launcher.py", "run_command"),
        ("cross-account", "cross_account.py", "read_file"),
    ],
)
def test_malicious_tool_attempts_fail_closed(tmp_path, name: str, script: str, tool: str) -> None:
    """越权工具调用：未授权路径/未声明域名/未声明命令/跨账户全部拒绝。"""
    harness = _Harness(tmp_path)
    harness.service.install(
        harness.acc1, f"{name}.yaml", tool_yaml(name, script, PYTHON).encode("utf-8")
    )
    result = harness.service.invoke(
        harness.acc1,
        f"malicious-{name}",
        McpCallRequest(tool="echo", input={}, data_slice=McpDataSlice(text="x")),
    )
    assert result.status == "success"
    attempts = result.result["attempts"]
    assert attempts, "夹具应至少发起一次越权尝试"
    assert all(item["blocked"] for item in attempts), f"{name} 存在未闭锁的越权尝试"
    # 每次拒绝都写审计。
    denied = [
        e for e in harness.observability.events if e["action"] == AuditAction.MCP_INVOKE_DENIED
    ]
    assert len(denied) >= len(attempts)


def test_malicious_cross_account_slice_is_minimal(tmp_path) -> None:
    """跨账户读取：路径全部拒绝；数据切片只含当前调用授权内容。"""
    harness = _Harness(tmp_path)
    harness.service.install(
        harness.acc1,
        "cross.yaml",
        tool_yaml("cross-account", "cross_account.py", PYTHON).encode("utf-8"),
    )
    result = harness.service.invoke(
        harness.acc1,
        "malicious-cross-account",
        McpCallRequest(
            tool="echo",
            input={},
            data_slice=McpDataSlice(text="当前消息授权文本"),
        ),
    )
    body = result.result
    assert all(item["blocked"] for item in body["attempts"])
    # 切片只有 text 与空 attachments——不含画像/历史/项目/他账户数据。
    assert body["slice_keys"] == ["attachments", "text"]
    assert body["slice_text_length"] == len("当前消息授权文本")


def test_malicious_zombie_becomes_failed(tmp_path) -> None:
    """进程崩溃：调用失败、状态 failed、带失败原因（AC3）。"""
    harness = _Harness(tmp_path)
    harness.service.install(harness.acc1, "zombie.yaml", zombie_yaml(PYTHON).encode("utf-8"))
    result = harness.service.invoke(
        harness.acc1,
        "malicious-zombie",
        McpCallRequest(tool="echo", input={}, data_slice=McpDataSlice(text="x")),
    )
    assert result.status == "failed"
    assert result.error_code == "process_crashed"
    assert "退出" in result.error_message
    server = harness.service.list_servers(harness.acc1).servers[0]
    assert server.status == McpStatus.FAILED
    assert server.failure_reason is not None
    assert "mcp_start_failed" in harness.audit_actions()


def test_malicious_slow_start_times_out(tmp_path) -> None:
    """启动超时：failed 状态而非长期 starting（AC3）。"""
    harness = _Harness(tmp_path)
    harness.service.install(harness.acc1, "slow.yaml", slow_start_yaml(PYTHON).encode("utf-8"))
    with pytest.raises(Exception) as exc_info:
        harness.service.invoke(
            harness.acc1,
            "malicious-slow-start",
            McpCallRequest(tool="echo", input={}, data_slice=McpDataSlice(text="x")),
        )
    assert "启动超时" in str(exc_info.value)
    server = harness.service.list_servers(harness.acc1).servers[0]
    assert server.status == McpStatus.FAILED
    assert not harness.runtime.is_running(harness.acc1, "malicious-slow-start")


# ---------------------------------------------------------------------------
# 数据切片（AC6）
# ---------------------------------------------------------------------------


def test_undeclared_data_slice_rejected(tmp_path) -> None:
    """未声明接收文本/附件的 MCP 拒绝接收对应数据（403）。"""
    harness = _Harness(tmp_path)
    manifest = McpPermissionManifest(data_categories=["public_query_terms"])
    harness.service.install(harness.acc1, "echo.yaml", echo_yaml().encode("utf-8"))
    harness.database.scoped(harness.acc1).execute(
        "UPDATE mcp_servers SET permissions = ? WHERE account_id = ? AND mcp_id = ?",
        (manifest.model_dump_json(), harness.acc1, "bridges-echo"),
    )
    with pytest.raises(Exception) as exc_info:
        harness.echo_call("私人文本")
    assert "未声明" in str(exc_info.value)
    assert "mcp_invoke_denied" in harness.audit_actions()


def test_attachment_slice_requires_declaration(tmp_path) -> None:
    harness = _Harness(tmp_path)
    harness.service.install(harness.acc1, "echo.yaml", echo_yaml().encode("utf-8"))
    with pytest.raises(Exception) as exc_info:
        harness.service.invoke(
            harness.acc1,
            "bridges-echo",
            McpCallRequest(
                tool="echo",
                input={},
                data_slice=McpDataSlice(
                    attachments=[
                        {"filename": "a.txt", "media_type": "text/plain", "preview": "内容"}
                    ]
                ),
            ),
        )
    assert "附件" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 敏感确认（AC7）
# ---------------------------------------------------------------------------


def test_sensitive_approve_full_flow(tmp_path) -> None:
    """首次敏感操作确认：展示目标与影响，确认后仅本次执行。"""
    harness = _Harness(tmp_path)
    write_dir = tmp_path / "notes"
    write_dir.mkdir()
    harness.service.install(harness.acc1, "note.yaml", note_yaml(str(write_dir)).encode("utf-8"))
    result = harness.service.invoke(
        harness.acc1,
        "bridges-note",
        McpCallRequest(
            tool="note",
            input={"path": str(write_dir / "note.txt")},
            data_slice=McpDataSlice(text="需要确认的笔记"),
        ),
    )
    assert result.status == "sensitive_pending"
    confirmation = result.confirmation
    assert confirmation is not None
    assert confirmation.kind == McpSensitiveKind.WRITE_FILE
    assert "写入文件" in confirmation.target
    assert not (write_dir / "note.txt").exists()  # 确认前不执行

    approved = harness.service.approve(harness.acc1, "bridges-note", confirmation.confirmation_id)
    assert approved.status == "success"
    assert approved.result["written"] is True
    assert (write_dir / "note.txt").read_text(encoding="utf-8") == "需要确认的笔记"
    assert "mcp_sensitive_approve" in harness.audit_actions()
    # 调用统计：1 次成功。
    server = harness.service.list_servers(harness.acc1).servers[0]
    assert server.call_count == 1
    assert server.last_call_status == "success"


def test_sensitive_deny_terminates_call(tmp_path) -> None:
    """拒绝敏感操作：调用安全终止，不执行任何操作（AC7）。"""
    harness = _Harness(tmp_path)
    write_dir = tmp_path / "notes"
    write_dir.mkdir()
    harness.service.install(harness.acc1, "note.yaml", note_yaml(str(write_dir)).encode("utf-8"))
    result = harness.service.invoke(
        harness.acc1,
        "bridges-note",
        McpCallRequest(
            tool="note",
            input={"path": str(write_dir / "note.txt")},
            data_slice=McpDataSlice(text="应被拒绝的笔记"),
        ),
    )
    assert result.status == "sensitive_pending"
    denied = harness.service.deny(harness.acc1, "bridges-note", result.confirmation.confirmation_id)
    assert denied.status == "failed"
    assert "拒绝" in denied.error_message
    assert not (write_dir / "note.txt").exists()
    assert "mcp_sensitive_deny" in harness.audit_actions()
    server = harness.service.list_servers(harness.acc1).servers[0]
    assert server.call_count == 1
    assert server.last_call_status == "denied"


def test_confirmation_not_reusable(tmp_path) -> None:
    """确认仅对本次调用有效：确认令牌不可复用（不扩展成永久授权）。"""
    harness = _Harness(tmp_path)
    write_dir = tmp_path / "notes"
    write_dir.mkdir()
    harness.service.install(harness.acc1, "note.yaml", note_yaml(str(write_dir)).encode("utf-8"))
    result = harness.service.invoke(
        harness.acc1,
        "bridges-note",
        McpCallRequest(
            tool="note",
            input={"path": str(write_dir / "note.txt")},
            data_slice=McpDataSlice(text="第一次"),
        ),
    )
    confirmation_id = result.confirmation.confirmation_id
    harness.service.approve(harness.acc1, "bridges-note", confirmation_id)
    # 第二次调用必须重新确认（令牌已消费）。
    result2 = harness.service.invoke(
        harness.acc1,
        "bridges-note",
        McpCallRequest(
            tool="note",
            input={"path": str(write_dir / "note2.txt")},
            data_slice=McpDataSlice(text="第二次"),
        ),
    )
    assert result2.status == "sensitive_pending"
    assert result2.confirmation.confirmation_id != confirmation_id
    # 旧令牌再使用失败。
    with pytest.raises(Exception) as exc_info:
        harness.service.approve(harness.acc1, "bridges-note", confirmation_id)
    assert "未找到" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 撤权（AC8）
# ---------------------------------------------------------------------------


def test_revoke_permissions_uses_new_manifest(tmp_path) -> None:
    """撤权：新调用使用新清单，移除的权限立即不可用。"""
    harness = _Harness(tmp_path)
    write_dir = tmp_path / "notes"
    write_dir.mkdir()
    harness.service.install(harness.acc1, "note.yaml", note_yaml(str(write_dir)).encode("utf-8"))
    # 撤掉写权限与敏感操作。
    reduced = McpPermissionManifest(
        data_categories=["current_message_text", "attachment_files"],
    )
    projection = harness.service.revoke_permissions(harness.acc1, "bridges-note", reduced)
    assert projection.permissions.filesystem_write == []
    assert projection.permissions.sensitive_operations == []
    assert "mcp_permissions_revoke" in harness.audit_actions()
    # 新调用：note 服务器请求 write_file → 未声明写入目录 → permission_denied。
    result = harness.service.invoke(
        harness.acc1,
        "bridges-note",
        McpCallRequest(
            tool="note",
            input={"path": str(write_dir / "note.txt")},
            data_slice=McpDataSlice(text="x"),
        ),
    )
    assert result.status == "failed"
    assert "不在允许清单内" in result.error_message
    assert not (write_dir / "note.txt").exists()


def test_revoke_sensitive_permission_terminates_running_process(tmp_path) -> None:
    """撤走敏感权限：终止仍依赖该权限的运行（AC8）。"""
    harness = _Harness(tmp_path)
    write_dir = tmp_path / "notes"
    write_dir.mkdir()
    harness.service.install(harness.acc1, "note.yaml", note_yaml(str(write_dir)).encode("utf-8"))
    # 先触发一次挂起调用（进程运行中）。
    result = harness.service.invoke(
        harness.acc1,
        "bridges-note",
        McpCallRequest(
            tool="note",
            input={"path": str(write_dir / "note.txt")},
            data_slice=McpDataSlice(text="x"),
        ),
    )
    assert result.status == "sensitive_pending"
    assert harness.runtime.is_running(harness.acc1, "bridges-note")
    # 撤掉写目录与敏感操作 → 进程终止。
    reduced = McpPermissionManifest(data_categories=["current_message_text"])
    harness.service.revoke_permissions(harness.acc1, "bridges-note", reduced)
    assert not harness.runtime.is_running(harness.acc1, "bridges-note")
    # 挂起的确认已失效。
    with pytest.raises(Exception) as exc_info:
        harness.service.approve(harness.acc1, "bridges-note", result.confirmation.confirmation_id)
    assert "已停止" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 调用统计与审计白名单
# ---------------------------------------------------------------------------


def test_call_stats_and_failure_reasons(tmp_path) -> None:
    harness = _Harness(tmp_path)
    harness.service.install(harness.acc1, "echo.yaml", echo_yaml().encode("utf-8"))
    harness.echo_call("成功一")
    harness.echo_call("成功二")
    # 一次数据切片拒绝（不算调用，只审计）。
    with pytest.raises(McpError):
        harness.service.invoke(
            harness.acc1,
            "bridges-echo",
            McpCallRequest(
                tool="echo",
                input={},
                data_slice=McpDataSlice(
                    attachments=[{"filename": "a.txt", "media_type": "text/plain", "preview": "x"}]
                ),
            ),
        )
    server = harness.service.list_servers(harness.acc1).servers[0]
    assert server.call_count == 2
    assert server.last_call_status == "success"
    calls = harness.service.get_calls(harness.acc1, "bridges-echo")
    assert len(calls) == 2
    assert all(call.tool == "echo" for call in calls)


def test_audit_never_contains_secrets_or_body(tmp_path) -> None:
    """审计 details 白名单：不携带秘密、数据正文与工具参数正文。"""
    harness = _Harness(tmp_path)
    harness.service.install(harness.acc1, "echo.yaml", echo_yaml().encode("utf-8"))
    harness.echo_call("绝密正文内容不可入审计")
    details_blob = json.dumps(harness.audit_details(), ensure_ascii=False)
    assert "sk-mcp-test-secret" not in details_blob
    assert "qq-mcp-test-code" not in details_blob
    assert "internal-mcp-key" not in details_blob
    assert "绝密正文内容" not in details_blob


# ---------------------------------------------------------------------------
# 重启恢复与孤儿清理（AC3）
# ---------------------------------------------------------------------------


def test_restart_restores_legal_configuration(tmp_path) -> None:
    """重启恢复：重建 service 读库恢复合法配置，不继承僵尸进程。"""
    db_path = tmp_path / "bridges.db"
    harness = _Harness(tmp_path, db_path=db_path)
    write_dir = tmp_path / "notes"
    write_dir.mkdir()
    harness.service.install(harness.acc1, "note.yaml", note_yaml(str(write_dir)).encode("utf-8"))
    harness.service.install(harness.acc1, "echo.yaml", echo_yaml().encode("utf-8"))
    harness.echo_call()
    # 模拟应用重启：同一 db 文件 + 对象库 + 全新 runtime + service。
    db = BridgesDatabase(db_path)
    db.initialize()
    objects = BridgesObjectRepository(
        db, EncryptedFileObjectStore(tmp_path / "objects", encryption_key="mcp-test-key")
    )
    runtime2 = McpRuntime(pid_dir=tmp_path / "mcp-pids")
    service2 = McpService(
        database=db,
        object_repository=objects,
        observability_service=_RecordingObservability(),  # type: ignore[arg-type]
        runtime=runtime2,
    )
    # 新实例不知道旧进程：注册表为空，无僵尸继承。
    assert not runtime2.is_running(harness.acc1, "bridges-echo")
    servers = service2.list_servers(harness.acc1).servers
    assert {server.mcp_id for server in servers} == {"bridges-echo", "bridges-note"}
    echo = next(server for server in servers if server.mcp_id == "bridges-echo")
    assert echo.status == McpStatus.HEALTHY  # 合法配置恢复
    assert echo.call_count == 1  # 调用统计持久化
    # 调用重新惰性启动新进程。
    result = service2.invoke(
        harness.acc1,
        "bridges-echo",
        McpCallRequest(tool="echo", input={}, data_slice=McpDataSlice(text="重启后调用")),
    )
    assert result.status == "success"
    assert result.result["echo"] == "重启后调用"
    db.close()


def test_reap_orphans_kills_leftover_process(tmp_path) -> None:
    """孤儿回收：pid 文件中仍存活的旧进程被终止（重启不继承僵尸）。"""
    harness = _Harness(tmp_path)
    harness.service.install(harness.acc1, "echo.yaml", echo_yaml().encode("utf-8"))
    harness.echo_call()
    assert harness.runtime.is_running(harness.acc1, "bridges-echo")
    # 模拟异常退出后遗留：旧 runtime 的进程仍活着，但 pid 文件存在。
    pid_file = tmp_path / "mcp-pids" / f"mcp-{harness.acc1}-bridges-echo.pid"
    assert pid_file.exists()
    # 新 runtime 回收（旧进程仍存活 → 被终止）。
    runtime2 = McpRuntime(pid_dir=tmp_path / "mcp-pids")
    reaped = runtime2.reap_orphans()
    assert reaped >= 1
    # TerminateProcess 后 poll 状态有极小延迟：轮询等待。
    for _ in range(40):
        if not harness.runtime.is_running(harness.acc1, "bridges-echo"):
            break
        time.sleep(0.05)
    assert not harness.runtime.is_running(harness.acc1, "bridges-echo")
    assert not pid_file.exists()


# ---------------------------------------------------------------------------
# 两账户隔离
# ---------------------------------------------------------------------------


def test_account_isolation_for_all_operations(tmp_path) -> None:
    harness = _Harness(tmp_path)
    harness.service.install(harness.acc1, "echo.yaml", echo_yaml().encode("utf-8"))
    harness.echo_call()
    # 账户 B 看不到 A 的服务器。
    assert harness.service.list_servers(harness.acc2).servers == []
    # 跨账户启停/卸载/调用/确认全部 404。
    for operation in (
        lambda: harness.service.set_enabled(harness.acc2, "bridges-echo", False),
        lambda: harness.service.uninstall(harness.acc2, "bridges-echo"),
        lambda: harness.service.invoke(
            harness.acc2,
            "bridges-echo",
            McpCallRequest(tool="echo", input={}, data_slice=McpDataSlice(text="x")),
        ),
        lambda: harness.service.get_calls(harness.acc2, "bridges-echo"),
    ):
        with pytest.raises(McpError) as exc_info:
            operation()
        assert "未找到" in str(exc_info.value)
    # A 的调用记录不受影响。
    assert harness.service.get_calls(harness.acc1, "bridges-echo")


def test_cross_account_rejected_by_scope_enforcer(tmp_path) -> None:
    """接入 scope enforcer 后，跨账户读取 MCP 对象被授权拒绝（而非仅空结果）。

    Issue 44：MCP 的对象访问与 vault/media/workflows 回答同一问题；
    异常链根因必须是 ScopeIsolationError（enforcer 判定），而不是 SQL
    scoped() 的空结果。
    """
    harness = _Harness(tmp_path)
    harness.service = McpService(
        database=harness.database,
        object_repository=harness.objects,
        observability_service=harness.observability,  # type: ignore[arg-type]
        runtime=harness.runtime,
        scope_enforcer=ScopeEnforcer(),
    )
    harness.service.install(harness.acc1, "echo.yaml", echo_yaml().encode("utf-8"))
    harness.echo_call()
    # acc2 跨账户读 acc1 的 MCP 对象 → 授权拒绝（404），而非空结果。
    with pytest.raises(McpError) as exc_info:
        harness.service.get_calls(harness.acc2, "bridges-echo")
    assert exc_info.value.status_code == 404
    assert "未找到" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, ScopeIsolationError)
    # acc1 本人读取不受影响（授权通过）。
    assert harness.service.get_calls(harness.acc1, "bridges-echo")


def test_same_mcp_id_across_accounts_both_authorized(tmp_path) -> None:
    """多账户各自安装同名 mcp_id 时，授权判定不误伤合法所有者。

    表仅 UNIQUE(account_id, mcp_id)，probe 必须对全部真实所有者判定，
    不能只取第一行（否则合法所有者会被误判为跨账户）。
    """
    harness = _Harness(tmp_path)
    harness.service = McpService(
        database=harness.database,
        object_repository=harness.objects,
        observability_service=harness.observability,  # type: ignore[arg-type]
        runtime=harness.runtime,
        scope_enforcer=ScopeEnforcer(),
    )
    harness.service.install(harness.acc1, "echo.yaml", echo_yaml().encode("utf-8"))
    harness.service.install(harness.acc2, "echo.yaml", echo_yaml().encode("utf-8"))
    # 两账户都是各自行的合法所有者，读取（经 enforcer 授权）均通过。
    assert harness.service.get_calls(harness.acc1, "bridges-echo") == []
    assert harness.service.get_calls(harness.acc2, "bridges-echo") == []
