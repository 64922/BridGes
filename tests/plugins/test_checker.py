"""插件安装检查器测试（Issue 34 Verification 1）。

合法声明式包通过并给出内容清单；脚本、可执行文件、符号链接、路径
穿越、越界引用、不受支持文件与损坏/超限包全部拒绝，并断言每项拒绝
给出可操作的具体中文原因。
"""

from __future__ import annotations

import pytest

from bridges.contracts.plugins import PluginFileKind
from bridges.plugins.checker import PluginPackageChecker

from zip_builder import (
    VALID_SKILL_MD,
    build_corrupt_zip,
    build_symlink_zip,
    build_too_large_zip,
    build_valid_zip,
    build_zip_with,
)

checker = PluginPackageChecker()


# ---------------------------------------------------------------------------
# 合法包
# ---------------------------------------------------------------------------


def test_valid_package_passes_with_manifest_and_manifest_list() -> None:
    result = checker.check(build_valid_zip())
    assert result.ok
    assert result.skill_id == "test-todo"
    assert result.name == "待办整理助手"
    assert result.version == "1.2.3"
    assert result.description.startswith("把聊天中的待办")
    assert result.source == "用户自制"
    assert result.license == "仅供个人使用"
    assert result.capabilities == ["提取待办事项", "输出清单"]
    assert result.data_categories == ["用户粘贴的待办文本"]
    assert result.file_count == 5
    assert result.total_bytes > 0
    paths = {entry.path: entry for entry in result.files}
    assert paths["SKILL.md"].kind == PluginFileKind.SKILL_MD
    assert paths["docs/usage.md"].kind == PluginFileKind.REFERENCE
    assert paths["templates/checklist.html"].kind == PluginFileKind.TEMPLATE
    assert paths["resources/icon.svg"].kind == PluginFileKind.RESOURCE
    assert paths["references/notes.txt"].kind == PluginFileKind.REFERENCE


def test_valid_package_without_optional_declarations() -> None:
    skill_md = """---
plugin_id: minimal
name: 极简包
version: 0.9.0
---

# 极简包
"""
    result = checker.check(build_valid_zip(skill_md=skill_md, extra={}))
    assert result.ok
    assert result.name == "极简包"
    assert result.version == "0.9.0"
    assert result.capabilities == []
    assert result.data_categories == []
    assert result.file_count == 1


def test_package_with_skill_id_from_plugin_id_alias() -> None:
    skill_md = """---
plugin_id: alias-id
name: 别名包
version: 1.0.0
---
"""
    result = checker.check(build_valid_zip(skill_md=skill_md, extra={}))
    assert result.ok
    assert result.skill_id == "alias-id"


# ---------------------------------------------------------------------------
# 脚本与可执行文件
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["run.py", "scripts/tool.py", "hook.js", "install.sh", "go.bat", "x.ps1",
     "evil.exe", "lib.dll", "payload.so", "nested/app.jar", "module.wasm"],
)
def test_script_and_executable_files_rejected_with_reason(path: str) -> None:
    result = checker.check(build_zip_with(entries=[(path, b"evil")]))
    assert not result.ok
    assert any(path in reason and ("脚本" in reason or "可执行" in reason) for reason in result.rejected_reasons)


def test_multiple_rejections_all_collected() -> None:
    result = checker.check(
        build_zip_with(entries=[("a.py", b"1"), ("b.exe", b"2"), ("c.sh", b"3")]))
    assert not result.ok
    assert len(result.rejected_reasons) == 3


# ---------------------------------------------------------------------------
# 符号链接
# ---------------------------------------------------------------------------


def test_symlink_entry_rejected() -> None:
    result = checker.check(build_symlink_zip())
    assert not result.ok
    assert any("符号链接" in reason for reason in result.rejected_reasons)


# ---------------------------------------------------------------------------
# 路径穿越
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["../outside.md", "a/../../escape.txt", "/absolute/path.md", "C:/windows/evil.txt",
     "c:\\windows\\evil.txt", "..\\backslash.txt"],
)
def test_path_traversal_rejected(path: str) -> None:
    result = checker.check(
        build_zip_with(entries=[("SKILL.md", VALID_SKILL_MD), (path, b"x")]))
    assert not result.ok
    assert any("路径非法" in reason or "路径不规范" in reason for reason in result.rejected_reasons)


def test_backslash_traversal_normalized_to_escape() -> None:
    """白盒：..\ 组合（Windows 风格）必须被判为路径穿越。

    Python 3.11 zipfile 写入时自动把 \\ 转成 /，无法用标准库构造
    反斜杠条目 zip；防御逻辑以纯函数形式验证。
    """
    from bridges.plugins.checker import _normalize_zip_path

    assert _normalize_zip_path("..\\escape.txt") is None
    assert _normalize_zip_path("sub\\..\\..\\escape.txt") is None


# ---------------------------------------------------------------------------
# 越界引用
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    ["../outside.md", "../../secret.txt", "/etc/passwd"],
)
def test_skill_md_escaping_reference_rejected(target: str) -> None:
    skill_md = f"---\nname: 引用包\nversion: 1.0.0\n---\n\n见 [外部]({target})。\n"
    result = checker.check(build_valid_zip(skill_md=skill_md, extra={}))
    assert not result.ok
    assert any("越界" in reason for reason in result.rejected_reasons)


def test_internal_http_and_anchor_references_accepted() -> None:
    skill_md = (
        "---\nplugin_id: ref-ok\nname: 引用包\nversion: 1.0.0\n---\n\n"
        "见 [内部](docs/usage.md)、[锚点](#section) 与 "
        "[链接](https://example.com)。\n"
    )
    result = checker.check(build_valid_zip(skill_md=skill_md))
    assert result.ok


# ---------------------------------------------------------------------------
# 不支持文件 / 隐藏文件 / 损坏包 / 超限
# ---------------------------------------------------------------------------


def test_unsupported_extension_rejected_with_reason() -> None:
    result = checker.check(
        build_zip_with(entries=[("SKILL.md", VALID_SKILL_MD), ("data.dat", b"x")]))
    assert not result.ok
    assert any("不受支持的文件类型" in reason for reason in result.rejected_reasons)


def test_extensionless_file_rejected() -> None:
    result = checker.check(
        build_zip_with(entries=[("SKILL.md", VALID_SKILL_MD), ("LICENSE", b"x")]))
    assert not result.ok


def test_hidden_file_rejected() -> None:
    result = checker.check(
        build_zip_with(entries=[("SKILL.md", VALID_SKILL_MD), (".env", b"x")]))
    assert not result.ok
    assert any("隐藏文件" in reason for reason in result.rejected_reasons)


def test_corrupt_zip_rejected_with_reason() -> None:
    result = checker.check(build_corrupt_zip())
    assert not result.ok
    assert any("损坏" in reason for reason in result.rejected_reasons)


def test_unpacked_overflow_rejected() -> None:
    result = checker.check(build_too_large_zip())
    assert not result.ok
    assert any("解压后总大小超过上限" in reason for reason in result.rejected_reasons)


def test_missing_skill_md_rejected() -> None:
    result = checker.check(build_zip_with(entries=[("docs/readme.md", b"x")]))
    assert not result.ok
    assert any("缺少声明" in reason for reason in result.rejected_reasons)


def test_missing_version_declaration_rejected() -> None:
    skill_md = "---\nname: 无版本包\n---\n"
    result = checker.check(build_valid_zip(skill_md=skill_md, extra={}))
    assert not result.ok
    assert any("固定版本（version）" in reason for reason in result.rejected_reasons)


def test_illegal_skill_id_rejected() -> None:
    skill_md = "---\nplugin_id: 中文标识!\nname: 非法标识\nversion: 1.0.0\n---\n"
    result = checker.check(build_valid_zip(skill_md=skill_md, extra={}))
    assert not result.ok
    assert any("插件标识不合法" in reason for reason in result.rejected_reasons)


def test_non_zip_content_rejected_as_corrupt() -> None:
    result = checker.check(b"anything")
    assert not result.ok
    assert any("损坏" in reason for reason in result.rejected_reasons)


def test_check_is_deterministic() -> None:
    content = build_valid_zip()
    first = checker.check(content)
    second = checker.check(content)
    assert first.ok == second.ok
    assert [entry.path for entry in first.files] == [entry.path for entry in second.files]
