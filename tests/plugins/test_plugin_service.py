"""插件中心服务测试（Issue 34）。

覆盖内置插件惰性安装/启停/不可卸载/不可篡改、用户包安装/冲突/失败
恢复/启停/卸载（对象待清理）、重启一致、两账户隔离、内置能力真实
解析演示与审计白名单。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from bridges.contracts.observability import AuditAction, AuditResult
from bridges.contracts.plugins import PluginError, PluginStatus
from bridges.plugins.registry import BUILTIN_PLUGIN_IDS
from bridges.plugins.service import PluginService
from bridges.skills.registry import SkillRegistry, create_builtin_registry
from bridges.storage.database import BridgesDatabase
from bridges.storage.object_store import EncryptedFileObjectStore
from bridges.storage.repository import BridgesObjectRepository

from zip_builder import VALID_SKILL_MD, build_corrupt_zip, build_valid_zip, build_zip_with


class _RecordingObservability:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def log_audit(self, **kwargs: Any) -> None:
        self.events.append(kwargs)


class _Clock:
    def __init__(self, start: datetime) -> None:
        self.value = start

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **delta: Any) -> None:
        self.value = self.value + timedelta(**delta)


class _Harness:
    def __init__(self, tmp_path: Any) -> None:
        self.clock = _Clock(datetime(2026, 8, 6, 8, 0, tzinfo=UTC))
        self.database = BridgesDatabase(":memory:")
        self.database.initialize()
        self.objects = BridgesObjectRepository(
            self.database,
            EncryptedFileObjectStore(tmp_path / "objects", encryption_key="test-key"),
        )
        self.acc1 = self.objects.register_account("acc1@example.com")
        self.acc2 = self.objects.register_account("acc2@example.com")
        self.observability = _RecordingObservability()
        self.registry: SkillRegistry = create_builtin_registry()
        self.service = PluginService(
            database=self.database,
            object_repository=self.objects,
            observability_service=self.observability,  # type: ignore
            skill_registry=self.registry,
            clock=self.clock,
        )

    def audit_actions(self) -> list[str]:
        return [event["action"].value for event in self.observability.events]

    def audit_details(self) -> list[dict[str, Any]]:
        return [event.get("details") or {} for event in self.observability.events]


# ---------------------------------------------------------------------------
# 内置插件
# ---------------------------------------------------------------------------


def test_builtin_plugins_present_and_default_enabled(tmp_path) -> None:
    harness = _Harness(tmp_path)
    projection = harness.service.list_plugins(harness.acc1)
    assert [p.skill_id for p in projection.builtin] == list(BUILTIN_PLUGIN_IDS)
    assert all(p.enabled for p in projection.builtin)
    assert all(p.read_only for p in projection.builtin)
    assert all(p.version for p in projection.builtin)
    assert all(p.capabilities for p in projection.builtin)
    assert all(p.data_categories for p in projection.builtin)
    assert all(p.source and p.license for p in projection.builtin)


def test_builtin_humanizer_matches_skill_registry(tmp_path) -> None:
    harness = _Harness(tmp_path)
    projection = harness.service.list_plugins(harness.acc1)
    humanizer = next(
        p for p in projection.builtin if p.skill_id == "bridges-humanizer"
    )
    manifest = harness.registry.get("bridges-humanizer")
    assert humanizer.version == manifest.version
    assert humanizer.name == manifest.name
    assert humanizer.capabilities == list(manifest.capabilities)
    assert humanizer.demo_kind == "chat"


def test_builtin_toggle_persists_per_account(tmp_path) -> None:
    harness = _Harness(tmp_path)
    harness.service.set_enabled(harness.acc1, "bridges-pdf", enabled=False)
    harness.service.set_enabled(harness.acc2, "bridges-pdf", enabled=True)
    acc1 = harness.service.list_plugins(harness.acc1)
    acc2 = harness.service.list_plugins(harness.acc2)
    assert not next(p for p in acc1.builtin if p.skill_id == "bridges-pdf").enabled
    assert next(p for p in acc2.builtin if p.skill_id == "bridges-pdf").enabled
    actions = harness.audit_actions()
    assert actions.count("plugin_disable") == 1
    assert actions.count("plugin_enable") == 1


def test_builtin_cannot_be_uninstalled(tmp_path) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(PluginError) as excinfo:
        harness.service.uninstall(harness.acc1, "bridges-pdf")
    assert excinfo.value.code == "builtin_not_mutable"
    assert "不能卸载" in excinfo.value.message


def test_user_package_cannot_collide_with_builtin(tmp_path) -> None:
    harness = _Harness(tmp_path)
    skill_md = "---\nplugin_id: bridges-pdf\nname: 冒充内置\nversion: 9.9.9\n---\n"
    with pytest.raises(PluginError) as excinfo:
        harness.service.install_package(
            harness.acc1, "fake.zip", build_valid_zip(skill_md=skill_md, extra={})
        )
    assert excinfo.value.code == "package_conflict"
    assert "内置插件冲突" in excinfo.value.message


def test_builtin_named_bad_package_leaves_no_failure_card(tmp_path) -> None:
    """声明内置标识的坏包不落失败记录（避免「我的插件」出现内置同名卡）。"""
    harness = _Harness(tmp_path)
    bad = build_zip_with(
        entries=[
            (
                "SKILL.md",
                "---\nplugin_id: bridges-pdf\nname: 冒充内置\nversion: 1.0.0\n---\n",
            ),
            ("evil.py", b"x"),
        ]
    )
    with pytest.raises(PluginError):
        harness.service.install_package(harness.acc1, "fake.zip", bad)
    assert harness.service.list_plugins(harness.acc1).user == []


# ---------------------------------------------------------------------------
# 用户包安装 / 检查 / 冲突 / 失败恢复
# ---------------------------------------------------------------------------


def test_install_success_stores_object_and_record_and_audit(tmp_path) -> None:
    harness = _Harness(tmp_path)
    projection = harness.service.install_package(harness.acc1, "todo.zip", build_valid_zip())
    assert projection.plugin_id == "test-todo"
    assert projection.name == "待办整理助手"
    assert projection.version == "1.2.3"
    assert projection.status == PluginStatus.INSTALLED
    assert projection.enabled
    assert projection.file_count == 5
    assert projection.object_id
    # 对象确实落库（账户隔离）
    stored = harness.objects.get_object(harness.acc1, projection.object_id)
    assert stored.status == "active"
    assert harness.audit_actions()[-1] == "plugin_install"
    details = harness.audit_details()[-1]
    assert details["plugin_id"] == "test-todo"
    assert details["version"] == "1.2.3"
    assert "zip" not in str(details)


def test_check_package_is_side_effect_free(tmp_path) -> None:
    harness = _Harness(tmp_path)
    result = harness.service.check_package("todo.zip", build_valid_zip())
    assert result.ok
    assert harness.service.list_plugins(harness.acc1).user == []
    # 对象库无任何新文件（纯检查无副作用）
    store = EncryptedFileObjectStore(tmp_path / "objects", encryption_key="test-key")
    assert store.list_files() == set()


def test_install_rejected_creates_recoverable_failure_not_running_entry(tmp_path) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(PluginError) as excinfo:
        harness.service.install_package(harness.acc1, "evil.zip", build_corrupt_zip())
    assert excinfo.value.code == "unsupported_package"
    user = harness.service.list_plugins(harness.acc1).user
    assert len(user) == 1
    assert user[0].status == PluginStatus.INSTALL_FAILED
    assert user[0].enabled is False
    assert user[0].object_id is None
    assert user[0].failure_reason and "损坏" in user[0].failure_reason
    # 拒绝也写审计（AC8：结果必须记录），但不含包内容
    rejected = [
        e for e in harness.observability.events
        if e["action"].value == "plugin_install" and e["result"].value == "blocked"
    ]
    assert len(rejected) == 1
    assert "损坏" in rejected[0]["details"]["reason"]
    assert "待办整理助手" not in str(rejected)


def test_duplicate_install_same_version_conflicts(tmp_path) -> None:
    harness = _Harness(tmp_path)
    harness.service.install_package(harness.acc1, "todo.zip", build_valid_zip())
    with pytest.raises(PluginError) as excinfo:
        harness.service.install_package(harness.acc1, "todo.zip", build_valid_zip())
    assert excinfo.value.code == "package_conflict"
    assert "请先卸载" in excinfo.value.message


def test_failed_package_can_be_fixed_and_reinstalled(tmp_path) -> None:
    harness = _Harness(tmp_path)
    # 第一次：声明了合法标识但含脚本的包 → 安装被拒，留下可恢复失败记录
    from zip_builder import build_zip_with

    bad = build_zip_with(entries=[("SKILL.md", VALID_SKILL_MD), ("evil.py", b"x")])
    with pytest.raises(PluginError):
        harness.service.install_package(harness.acc1, "x.zip", bad)
    failed = harness.service.list_plugins(harness.acc1).user
    assert failed[0].status == PluginStatus.INSTALL_FAILED
    assert failed[0].plugin_id == "test-todo"
    # 修正后的同标识包 → 覆盖失败记录并安装成功
    fixed = harness.service.install_package(harness.acc1, "x.zip", build_valid_zip())
    assert fixed.status == PluginStatus.INSTALLED
    assert fixed.plugin_id == "test-todo"
    user = harness.service.list_plugins(harness.acc1).user
    assert len(user) == 1
    assert user[0].status == PluginStatus.INSTALLED


def test_failed_install_does_not_enter_running_registry(tmp_path) -> None:
    """install_failed 绝不混入已安装集合：只有 installed 才计入运行注册。"""
    harness = _Harness(tmp_path)
    harness.service.install_package(harness.acc1, "todo.zip", build_valid_zip())
    with pytest.raises(PluginError):
        harness.service.install_package(harness.acc1, "evil.zip", build_corrupt_zip())
    running = [
        p for p in harness.service.list_plugins(harness.acc1).user
        if p.status == PluginStatus.INSTALLED
    ]
    assert len(running) == 1
    assert running[0].plugin_id == "test-todo"


# ---------------------------------------------------------------------------
# 启停 / 卸载
# ---------------------------------------------------------------------------


def test_user_package_toggle_and_uninstall(tmp_path) -> None:
    harness = _Harness(tmp_path)
    installed = harness.service.install_package(harness.acc1, "todo.zip", build_valid_zip())
    harness.service.set_enabled(harness.acc1, "test-todo", enabled=False)
    listed = harness.service.list_plugins(harness.acc1).user
    assert listed[0].status == PluginStatus.DISABLED
    assert listed[0].enabled is False
    harness.service.set_enabled(harness.acc1, "test-todo", enabled=True)
    assert harness.service.list_plugins(harness.acc1).user[0].status == PluginStatus.INSTALLED
    # 卸载：记录删除 + 对象物理清理（待清理回收轮立即执行）
    harness.service.uninstall(harness.acc1, "test-todo")
    assert harness.service.list_plugins(harness.acc1).user == []
    with pytest.raises(Exception) as excinfo:
        harness.objects.get_object(harness.acc1, installed.object_id)
    assert "对象不存在" in str(excinfo.value)
    actions = harness.audit_actions()
    assert actions[-3:] == ["plugin_disable", "plugin_enable", "plugin_uninstall"]


def test_toggle_failed_package_rejected(tmp_path) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(PluginError):
        harness.service.install_package(harness.acc1, "x.zip", build_corrupt_zip())
    with pytest.raises(PluginError) as excinfo:
        harness.service.set_enabled(harness.acc1, "unknown-package", enabled=True)
    assert excinfo.value.code == "install_failed"
    assert "重新上传" in excinfo.value.message


def test_uninstall_unknown_package_404(tmp_path) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(PluginError) as excinfo:
        harness.service.uninstall(harness.acc1, "nope")
    assert excinfo.value.code == "plugin_not_found"


# ---------------------------------------------------------------------------
# 重启一致
# ---------------------------------------------------------------------------


def test_state_survives_service_recreation(tmp_path) -> None:
    harness = _Harness(tmp_path)
    installed = harness.service.install_package(harness.acc1, "todo.zip", build_valid_zip())
    harness.service.set_enabled(harness.acc1, "test-todo", enabled=False)
    harness.service.set_enabled(harness.acc1, "bridges-documents", enabled=False)
    harness.clock.advance(hours=1)
    # 模拟应用重启：全新 service（同一数据库与对象目录）
    restarted = PluginService(
        database=harness.database,
        object_repository=harness.objects,
        observability_service=_RecordingObservability(),  # type: ignore
        skill_registry=create_builtin_registry(),
    )
    projection = restarted.list_plugins(harness.acc1)
    user = projection.user
    assert len(user) == 1
    assert user[0].plugin_id == "test-todo"
    assert user[0].status == PluginStatus.DISABLED
    assert user[0].version == "1.2.3"
    assert user[0].package_id == installed.package_id
    assert not next(p for p in projection.builtin if p.skill_id == "bridges-documents").enabled
    assert next(p for p in projection.builtin if p.skill_id == "bridges-humanizer").enabled


# ---------------------------------------------------------------------------
# 账户隔离
# ---------------------------------------------------------------------------


def test_account_isolation_for_all_operations(tmp_path) -> None:
    harness = _Harness(tmp_path)
    harness.service.install_package(harness.acc1, "todo.zip", build_valid_zip())
    # 其他账户看不到
    assert harness.service.list_plugins(harness.acc2).user == []
    # 其他账户无法启停 / 卸载
    with pytest.raises(PluginError) as excinfo:
        harness.service.set_enabled(harness.acc2, "test-todo", enabled=False)
    assert excinfo.value.code == "plugin_not_found"
    with pytest.raises(PluginError) as excinfo:
        harness.service.uninstall(harness.acc2, "test-todo")
    assert excinfo.value.code == "plugin_not_found"
    # 内置启停按账户隔离（见 test_builtin_toggle_persists_per_account）


# ---------------------------------------------------------------------------
# 内置能力演示（真实解析路径）
# ---------------------------------------------------------------------------


def test_demo_parses_markdown_attachment_for_real(tmp_path) -> None:
    harness = _Harness(tmp_path)
    markdown = "# 演示章节\n\n这是演示文本。\n".encode("utf-8")
    demo = harness.service.demo(harness.acc1, "bridges-documents", "demo.md", markdown)
    assert demo.skill_id == "bridges-documents"
    assert demo.version == "1.0.0"
    assert demo.parser_version == "markdown-v1"
    assert demo.sections >= 1
    assert demo.char_count >= 14  # 归一文本长度（尾部换行由解析器收敛）
    assert demo.media_type == "text/markdown"
    assert "演示文本" in demo.preview
    # 审计：只有白名单字段，不含正文
    event = harness.observability.events[-1]
    assert event["action"].value == "plugin_invoke"
    details = event["details"]
    assert "演示文本" not in str(details)
    assert details["plugin_id"] == "bridges-documents"
    assert details["parser_version"] == "markdown-v1"


def test_demo_rejects_chat_kind_skill(tmp_path) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(PluginError) as excinfo:
        harness.service.demo(harness.acc1, "bridges-humanizer", "a.md", b"# x")
    assert excinfo.value.code == "demo_unsupported"


def test_demo_rejects_disabled_builtin(tmp_path) -> None:
    harness = _Harness(tmp_path)
    harness.service.set_enabled(harness.acc1, "bridges-documents", enabled=False)
    with pytest.raises(PluginError) as excinfo:
        harness.service.demo(harness.acc1, "bridges-documents", "a.md", b"# x")
    assert excinfo.value.code == "plugin_disabled"


def test_demo_parse_failure_reports_chinese_reason(tmp_path) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(PluginError) as excinfo:
        harness.service.demo(harness.acc1, "bridges-documents", "bad.pdf", b"%PDF-broken")
    assert excinfo.value.code == "demo_parse_failed"
    # 失败也写审计（AC8：结果必须记录），不含附件内容
    blocked = [
        e for e in harness.observability.events
        if e["action"].value == "plugin_invoke" and e["result"].value == "blocked"
    ]
    assert len(blocked) == 1
    assert "解析失败" in blocked[0]["details"]["reason"]
    assert "bad.pdf" not in str(blocked)


def test_demo_parses_real_pdf(tmp_path) -> None:
    """Verification 2：PDF 内置能力对真实 PDF 附件执行解析（非固定样例）。"""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        pytest.skip("PyMuPDF 不可用")
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "BridGes PDF demo page")
    pdf_bytes = document.tobytes()
    harness = _Harness(tmp_path)
    demo = harness.service.demo(harness.acc1, "bridges-pdf", "demo.pdf", pdf_bytes)
    assert demo.skill_id == "bridges-pdf"
    assert demo.parser_version.startswith("pdf-")
    assert demo.pages >= 1
    assert demo.char_count > 0
    assert "BridGes PDF demo" in demo.preview
    event = harness.observability.events[-1]
    assert event["action"].value == "plugin_invoke"
    assert "demo.pdf" not in str(event["details"])
    assert demo.preview not in str(event["details"])


def test_audit_never_contains_package_content(tmp_path) -> None:
    harness = _Harness(tmp_path)
    harness.service.install_package(harness.acc1, "todo.zip", build_valid_zip())
    harness.service.set_enabled(harness.acc1, "test-todo", enabled=False)
    harness.service.uninstall(harness.acc1, "test-todo")
    for event in harness.observability.events:
        # 包正文与 SKILL.md 全文绝不进入审计
        assert "把文本中的待办事项整理成清单" not in str(event)
        assert "SKILL.md" not in str(event.get("details") or {})
    for details in harness.audit_details():
        assert set(details.keys()) <= {
            "plugin_id", "version", "file_count", "content_length",
            "data_categories", "parser_version", "pages", "sections",
            "char_count", "reason",
        }
    # 审计记录声明的数据类别（AC8），但绝不携带包正文与文件名
    assert any("用户粘贴的待办文本" in str(d["data_categories"]) for d in harness.audit_details())
