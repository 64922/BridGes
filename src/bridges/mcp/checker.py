"""MCP 安装描述的确定性安装检查器（Issue 35）。

对单个 MCP.yaml 原文执行：YAML 子集解析 → 必填与格式校验（标识/名称/
版本锁/来源/完整性/命令）→ 权限清单逐项校验。全部检查是无副作用的
纯函数；任何一项未通过都给出可操作的具体中文原因，未声明权限一律
不可用。检查通过时计算描述原文的 sha256 作为安装锁定哈希（运行时防
篡改的完整性信息）。
"""

from __future__ import annotations

import hashlib
import re

from bridges.contracts.mcp import (
    ALLOWED_SENSITIVE_OPERATIONS,
    McpCheckResult,
    McpError,
)
from bridges.mcp.manifest import (
    build_permissions,
    parse_descriptor,
    validate_command,
    validate_id,
    validate_integrity,
    validate_permissions,
    validate_source,
    validate_version,
)

#: 描述原文大小上限（与插件中心包上限同量级）。
MAX_DESCRIPTOR_BYTES = 256 * 1024


class McpDescriptorChecker:
    """MCP.yaml 安装描述检查器（无副作用）。"""

    def check(self, content: bytes) -> McpCheckResult:
        """检查描述原文；通过时携带清单与锁定哈希，拒绝时携带具体原因。"""
        if len(content) > MAX_DESCRIPTOR_BYTES:
            return McpCheckResult(
                ok=False,
                rejected_reasons=[
                    f"安装描述超过 {MAX_DESCRIPTOR_BYTES // 1024} KB 上限，请精简后重试。"
                ],
            )
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return McpCheckResult(
                ok=False,
                rejected_reasons=["安装描述不是有效的 UTF-8 文本，请检查编码。"],
            )
        fields, _ = parse_descriptor(text)
        reasons: list[str] = []
        mcp_id = _scalar(fields, "mcp_id")
        name = _scalar(fields, "name")
        version = _scalar(fields, "version")
        description = _scalar(fields, "description")
        source = _scalar(fields, "source")
        integrity = _scalar(fields, "integrity")

        validate_id(mcp_id, reasons)
        if not name:
            reasons.append("缺少显示名（name）：请声明 MCP 的中文显示名。")
        validate_version(version, reasons)
        validate_source(source, reasons)
        validate_integrity(integrity, reasons)

        command: list[str] = []
        raw_command = fields.get("command")
        if isinstance(raw_command, list):
            command = [str(item).strip() for item in raw_command if str(item).strip()]
        elif isinstance(raw_command, str) and raw_command.strip():
            command = [raw_command.strip()]
        validate_command(command, reasons)

        # 敏感操作按原始声明逐项校验（非法项在构造清单前报出，而不是
        # 被静默过滤成"未声明"）。
        raw_sensitive = fields.get("sensitive_operations")
        if isinstance(raw_sensitive, list):
            for operation in raw_sensitive:
                item = str(operation).strip()
                if item and item not in ALLOWED_SENSITIVE_OPERATIONS:
                    reasons.append(
                        f"敏感操作「{item}」不在允许集合内（允许：write_file、"
                        "run_command、send_external）。"
                    )
        permissions = build_permissions(fields)
        validate_permissions(permissions, reasons)

        # 完整性自校验：声明的 sha256 必须等于"去除 integrity 行后"的
        # 描述哈希（用户可先写描述、算哈希、再补声明行；防篡改）。
        if (
            integrity is not None
            and integrity
            and _valid_hex_hash(integrity)
            and not reasons
            and integrity.lower() != f"sha256:{_content_hash_without_integrity(text)}"
        ):
            reasons.append(
                "完整性信息与描述原文不匹配：声明的 sha256 与当前描述内容不一致，"
                "请修正后重试（描述一经发布不可篡改）。"
            )

        if reasons:
            return McpCheckResult(
                ok=False,
                mcp_id=mcp_id,
                name=name,
                version=version,
                source=source,
                rejected_reasons=reasons,
            )
        return McpCheckResult(
            ok=True,
            mcp_id=mcp_id,
            name=name,
            version=version,
            description=description,
            source=source,
            integrity=integrity,
            command=command,
            permissions=permissions,
            integrity_sha256=hashlib.sha256(content).hexdigest(),
        )


def _scalar(fields: dict[str, object], key: str) -> str | None:
    value = fields.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _valid_hex_hash(integrity: str) -> bool:
    if not integrity.startswith("sha256:"):
        return False
    digest = integrity[len("sha256:") :]
    return len(digest) == 64 and all(c in "0123456789abcdefABCDEF" for c in digest)


def _content_hash_without_integrity(text: str) -> str:
    """计算去除 integrity 行后的描述内容哈希（保留原始换行结构）。"""
    cleaned = re.sub(r"(?m)^integrity:.*$\n?", "", text)
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


def require_checker_error(result: McpCheckResult) -> None:
    """检查未通过时抛统一错误（供 API 层复用）。"""
    if not result.ok:
        reason = "；".join(result.rejected_reasons) or "安装检查未通过。"
        raise McpError("invalid_descriptor", reason, status_code=422, retryable=True)


__all__ = ["MAX_DESCRIPTOR_BYTES", "McpDescriptorChecker", "require_checker_error"]
