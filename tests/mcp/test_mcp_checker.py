"""MCP 安装描述检查器测试（Issue 35）。

逐项断言：合法描述通过并携带权限清单与锁定哈希；缺版本/latest 版本/
损坏 YAML/来源不匹配/完整性不匹配或格式非法/未声明权限/非法域名/
相对目录/命令含 shell 元字符/未知数据类别全部拒绝并给出可操作的具体
中文原因；检查为纯函数（不落库不落对象）。
"""

from __future__ import annotations

import pytest
from fixtures import (
    BAD_DOMAIN_YAML,
    BAD_INTEGRITY_YAML,
    BAD_SOURCE_YAML,
    BROKEN_YAML,
    CORRUPT_BYTES,
    LATEST_VERSION_YAML,
    MISMATCH_INTEGRITY_YAML,
    MISSING_VERSION_YAML,
    RELATIVE_DIR_YAML,
    SHELL_COMMAND_YAML,
    UNKNOWN_CATEGORY_YAML,
    WILDCARD_VERSION_YAML,
    echo_with_category,
    echo_yaml,
    note_yaml,
    with_integrity,
)

from bridges.contracts.mcp import McpSensitiveKind
from bridges.mcp.checker import McpDescriptorChecker

checker = McpDescriptorChecker()


# ---------------------------------------------------------------------------
# 合法描述
# ---------------------------------------------------------------------------


def test_valid_echo_descriptor_passes() -> None:
    result = checker.check(echo_yaml().encode("utf-8"))
    assert result.ok is True
    assert result.mcp_id == "bridges-echo"
    assert result.name == "回显演示"
    assert result.version == "1.0.0"
    assert result.source == "local"
    assert result.command == ["python", "-m", "bridges.mcp.servers.echo"]
    assert result.permissions is not None
    assert result.permissions.data_categories == ["current_message_text"]
    assert result.permissions.sensitive_operations == []
    assert result.integrity_sha256 is not None and len(result.integrity_sha256) == 64


def test_valid_note_descriptor_carries_sensitive_operations(tmp_path) -> None:
    result = checker.check(note_yaml(str(tmp_path)).encode("utf-8"))
    assert result.ok is True
    assert result.permissions is not None
    assert result.permissions.filesystem_write == [str(tmp_path)]
    assert result.permissions.sensitive_operations == [McpSensitiveKind.WRITE_FILE]
    assert set(result.permissions.data_categories) == {
        "current_message_text",
        "attachment_files",
    }


def test_matching_integrity_passes() -> None:
    text = with_integrity(echo_yaml())
    result = checker.check(text.encode("utf-8"))
    assert result.ok is True


# ---------------------------------------------------------------------------
# 拒绝矩阵：每项断言具体中文原因
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("description", "keyword"),
    [
        (MISSING_VERSION_YAML, "缺少固定版本"),
        (LATEST_VERSION_YAML, "未锁定"),
        (WILDCARD_VERSION_YAML, "未锁定"),
    ],
)
def test_unpinned_version_rejected(description: str, keyword: str) -> None:
    result = checker.check(description.encode("utf-8"))
    assert result.ok is False
    assert any(keyword in reason for reason in result.rejected_reasons)


def test_corrupt_bytes_rejected() -> None:
    result = checker.check(CORRUPT_BYTES)
    assert result.ok is False
    assert any("UTF-8" in reason for reason in result.rejected_reasons)


def test_broken_yaml_rejected() -> None:
    """字段残缺/未闭合的描述整体拒绝（不进入安装流程）。"""
    result = checker.check(BROKEN_YAML.encode("utf-8"))
    assert result.ok is False
    assert result.rejected_reasons


def test_bad_source_rejected() -> None:
    result = checker.check(BAD_SOURCE_YAML.encode("utf-8"))
    assert result.ok is False
    assert any("来源" in reason and "不匹配" in reason for reason in result.rejected_reasons)


def test_bad_integrity_format_rejected() -> None:
    result = checker.check(BAD_INTEGRITY_YAML.encode("utf-8"))
    assert result.ok is False
    assert any("完整性信息格式非法" in reason for reason in result.rejected_reasons)


def test_mismatched_integrity_rejected() -> None:
    result = checker.check(MISMATCH_INTEGRITY_YAML.encode("utf-8"))
    assert result.ok is False
    assert any("不匹配" in reason for reason in result.rejected_reasons)


def test_domain_with_scheme_rejected() -> None:
    result = checker.check(BAD_DOMAIN_YAML.encode("utf-8"))
    assert result.ok is False
    assert any("网络域名" in reason and "格式非法" in reason for reason in result.rejected_reasons)


def test_relative_directory_rejected() -> None:
    result = checker.check(RELATIVE_DIR_YAML.encode("utf-8"))
    assert result.ok is False
    assert any("不是绝对路径" in reason for reason in result.rejected_reasons)


def test_shell_command_rejected() -> None:
    result = checker.check(SHELL_COMMAND_YAML.encode("utf-8"))
    assert result.ok is False
    assert any("shell 元字符" in reason for reason in result.rejected_reasons)


def test_unknown_data_category_rejected() -> None:
    result = checker.check(UNKNOWN_CATEGORY_YAML.encode("utf-8"))
    assert result.ok is False
    assert any("不在允许集合内" in reason for reason in result.rejected_reasons)


def test_sensitive_without_write_dir_rejected() -> None:
    text = echo_with_category("current_message_text").replace(
        "sensitive_operations: []",
        "sensitive_operations:\n  - write_file",
    )
    result = checker.check(text.encode("utf-8"))
    assert result.ok is False
    assert any("未声明任何可写目录" in reason for reason in result.rejected_reasons)


def test_unknown_sensitive_operation_rejected() -> None:
    text = echo_yaml().replace(
        "sensitive_operations: []", "sensitive_operations:\n  - delete_everything"
    )
    result = checker.check(text.encode("utf-8"))
    assert result.ok is False
    assert any("不在允许集合内" in reason for reason in result.rejected_reasons)


def test_oversized_descriptor_rejected() -> None:
    result = checker.check(b"---\nmcp_id: x\n" + b" " * 300 * 1024)
    assert result.ok is False
    assert any("KB 上限" in reason for reason in result.rejected_reasons)


# ---------------------------------------------------------------------------
# 纯函数语义
# ---------------------------------------------------------------------------


def test_check_is_side_effect_free() -> None:
    """检查不落库不落对象（取消安装不留任何半安装状态）。"""
    result = checker.check(echo_yaml().encode("utf-8"))
    assert result.ok is True
    result2 = checker.check(LATEST_VERSION_YAML.encode("utf-8"))
    assert result2.ok is False
    # 再次检查相同内容结果确定且一致。
    assert checker.check(echo_yaml().encode("utf-8")).integrity_sha256 == result.integrity_sha256
