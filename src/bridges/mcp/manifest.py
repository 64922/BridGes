"""MCP 安装描述（MCP.yaml）的解析与权限清单校验（Issue 35）。

安装描述是单个 YAML 子集文件，采用与 SKILL.md frontmatter 相同的
扁平风格：``mcp_id``/``name``/``version``/``description``/``source``/
``integrity`` 标量，``command`` 与五个权限列表（network_domains/
filesystem_read/filesystem_write/external_commands/data_categories/
sensitive_operations）为 ``- 前缀`` 列表项。解析是纯函数、无副作用；
任何一项格式非法都给出可操作的具体中文原因，未声明权限一律不可用
（空集合即默认拒绝）。
"""

from __future__ import annotations

import re
from typing import Any

from bridges.contracts.mcp import (
    ALLOWED_DATA_CATEGORIES,
    ALLOWED_SENSITIVE_OPERATIONS,
    McpPermissionManifest,
    McpSensitiveKind,
)

_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,63}$")
_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_DOMAIN_RE = re.compile(
    r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
_COMMAND_ARG_RE = re.compile(r"^[^\s|;&`$<>\"'()]+$")
#: 版本锁：拒绝空、latest/any/dev/master/main、含 * 或空格的模糊版本。
_UNPINNED_VERSION = re.compile(r"^(?:latest|any|dev|master|main|.*\*.*|.*[ \t].*)$", re.IGNORECASE)


def parse_descriptor(text: str) -> tuple[dict[str, Any], str]:
    """解析 MCP.yaml 的 YAML 子集，返回（字段, 剩余正文）。

    支持标量键与 ``- 前缀`` 列表项；解析失败返回空字段（由调用方给出
    中文原因）。
    """
    fields: dict[str, Any] = {}
    if not text.startswith("---"):
        return fields, text
    lines = text.splitlines()
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end is None:
        return fields, text
    current: str | None = None
    for line in lines[1:end]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and current is not None:
            item = stripped[2:].strip()
            existing = fields.get(current)
            if isinstance(existing, list):
                existing.append(item)
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if value in ("[]", "[ ]", "{}"):
            # 内联空列表/空字典：语义与空值相同（空集合即默认拒绝）。
            fields[key] = []
            current = key
        elif value:
            fields[key] = value
        else:
            fields[key] = []
            current = key
    return fields, "\n".join(lines[end + 1 :])


def _string_list(fields: dict[str, Any], key: str) -> list[str]:
    """提取列表字段：列表直接返回，标量按单项返回，缺失返回空。"""
    raw = fields.get(key)
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def _string_scalar(fields: dict[str, Any], key: str) -> str | None:
    raw = fields.get(key)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def build_permissions(fields: dict[str, Any]) -> McpPermissionManifest:
    """从解析字段构造权限清单（宽松容错：缺失键即空集合）。"""
    return McpPermissionManifest(
        network_domains=_string_list(fields, "network_domains"),
        filesystem_read=_string_list(fields, "filesystem_read"),
        filesystem_write=_string_list(fields, "filesystem_write"),
        external_commands=_string_list(fields, "external_commands"),
        data_categories=_string_list(fields, "data_categories"),
        sensitive_operations=[
            McpSensitiveKind(item)
            for item in _string_list(fields, "sensitive_operations")
            if item in ALLOWED_SENSITIVE_OPERATIONS
        ],
    )


def validate_permissions(manifest: McpPermissionManifest, reasons: list[str]) -> None:
    """逐项校验权限清单；任何一项非法都给出具体中文原因。"""
    for domain in manifest.network_domains:
        if not _DOMAIN_RE.match(domain):
            reasons.append(
                f"网络域名「{domain}」格式非法：只允许裸域名（如 export.arxiv.org），"
                "不允许协议、路径或端口。"
            )
    for directory in manifest.filesystem_read + manifest.filesystem_write:
        if not directory.startswith("/") and not re.match(r"^[A-Za-z]:[\\/]", directory):
            reasons.append(f"文件目录「{directory}」不是绝对路径：请使用 / 或盘符开头的绝对路径。")
    for command in manifest.external_commands:
        if not _COMMAND_ARG_RE.match(command):
            reasons.append(
                f"外部命令「{command}」格式非法：不允许空格或 shell 元字符（|;&`$<>\"'()）。"
            )
    for category in manifest.data_categories:
        if category not in ALLOWED_DATA_CATEGORIES:
            reasons.append(
                f"数据类别「{category}」不在允许集合内（允许："
                + "、".join(sorted(ALLOWED_DATA_CATEGORIES))
                + "）。"
            )
    for operation in manifest.sensitive_operations:
        if operation not in ALLOWED_SENSITIVE_OPERATIONS:
            reasons.append(
                f"敏感操作「{operation}」不在允许集合内（允许：write_file、"
                "run_command、send_external）。"
            )
    # 敏感操作类别与权限联动：声明写文件却无写入目录等。
    if (
        McpSensitiveKind.WRITE_FILE in manifest.sensitive_operations
        and not manifest.filesystem_write
    ):
        reasons.append("声明了 write_file 敏感操作但未声明任何可写目录（filesystem_write）。")
    if (
        McpSensitiveKind.RUN_COMMAND in manifest.sensitive_operations
        and not manifest.external_commands
    ):
        reasons.append("声明了 run_command 敏感操作但未声明任何外部命令（external_commands）。")


def validate_version(version: str | None, reasons: list[str]) -> None:
    """版本锁校验：必须是精确版本；未锁版本直接拒绝。"""
    if not version:
        reasons.append("缺少固定版本（version）：MCP 必须锁定精确版本，禁止 latest/通配符。")
        return
    if _UNPINNED_VERSION.match(version):
        reasons.append(
            f"版本「{version}」未锁定：必须使用精确版本号，禁止 latest/any/* 等模糊版本。"
        )
        return
    if not _VERSION_RE.match(version):
        reasons.append(f"版本「{version}」格式非法：只能包含字母、数字、点、下划线、加号与连字符。")


def validate_integrity(integrity: str | None, reasons: list[str]) -> None:
    """完整性声明校验：提供时必须是 sha256:<hex> 格式。"""
    if integrity is not None and integrity and not _SHA256_RE.match(integrity):
        reasons.append(
            "完整性信息格式非法：必须为 sha256: 后跟 64 位十六进制摘要，"
            "或省略该字段由系统在安装时锁定描述哈希。"
        )


def validate_source(source: str | None, reasons: list[str]) -> None:
    """来源校验：必须为 local 或 https URL（来源不匹配拒绝安装）。"""
    if source == "local":
        return
    if source is not None and re.match(
        r"^https://[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
        r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+/?$",
        source,
    ):
        return
    reasons.append(
        f"来源「{source or '（缺失）'}」不匹配：只允许 local 或 https:// 开头的来源地址。"
    )


def validate_command(command: list[str], reasons: list[str]) -> None:
    """启动命令校验：非空、每项无 shell 元字符。"""
    if not command:
        reasons.append("缺少启动命令（command）：必须声明要启动的程序与参数。")
        return
    for arg in command:
        if not arg:
            reasons.append("启动命令包含空参数。")
            continue
        if not _COMMAND_ARG_RE.match(arg):
            reasons.append(
                f"启动命令参数「{arg}」含 shell 元字符（空格、|;&`$<>\"'()）："
                "平台以非 shell 方式启动，请移除这些字符。"
            )


def validate_id(mcp_id: str | None, reasons: list[str]) -> None:
    """标识校验：必填且格式合法。"""
    if not mcp_id:
        reasons.append("缺少 MCP 标识（mcp_id）：请声明稳定且唯一的标识。")
        return
    if not _ID_RE.match(mcp_id):
        reasons.append(
            f"MCP 标识「{mcp_id}」格式非法：2-64 位字母、数字、点、下划线或连字符，"
            "且以字母或数字开头。"
        )


__all__ = [
    "build_permissions",
    "parse_descriptor",
    "validate_command",
    "validate_id",
    "validate_integrity",
    "validate_permissions",
    "validate_source",
    "validate_version",
]
