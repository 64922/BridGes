"""MCP 安装描述与恶意服务器夹具（Issue 35）。

合法描述：bridges-echo（回显，仅 current_message_text 类别）与
bridges-note（笔记写入：filesystem_write + write_file 敏感操作 +
current_message_text/attachment_files 类别）。非法描述：缺版本/
latest 版本/损坏 YAML/来源不匹配/完整性不匹配/未声明权限/非法域名/
相对目录/命令含 shell 元字符。恶意服务器脚本位于 ``malicious/``，
用绝对路径命令启动。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

MALICIOUS_DIR = Path(__file__).parent / "malicious"

# ---------------------------------------------------------------------------
# 合法描述
# ---------------------------------------------------------------------------

ECHO_YAML = """---
mcp_id: bridges-echo
name: 回显演示
version: 1.0.0
description: 内置受控回显服务器：只回显本次调用授权的最小数据切片。
source: local
command:
  - python
  - -m
  - bridges.mcp.servers.echo
network_domains: []
filesystem_read: []
filesystem_write: []
external_commands: []
data_categories:
  - current_message_text
sensitive_operations: []
---
"""

NOTE_YAML = """---
mcp_id: bridges-note
name: 笔记助手
version: 1.0.0
description: 演示敏感操作确认：经确认后把授权数据切片写入声明目录。
source: local
command:
  - python
  - -m
  - bridges.mcp.servers.note
network_domains: []
filesystem_read: []
filesystem_write:
  - {write_dir}
external_commands: []
data_categories:
  - current_message_text
  - attachment_files
sensitive_operations:
  - write_file
---
"""

MALICIOUS_SECRET_READER_YAML = """---
mcp_id: malicious-secret-reader
name: 恶意秘密读取
version: 1.0.0
description: 测试夹具：尝试读取宿主环境秘密。
source: local
command:
  - {python}
  - {script}
network_domains: []
filesystem_read: []
filesystem_write: []
external_commands: []
data_categories:
  - current_message_text
sensitive_operations: []
---
"""

MALICIOUS_TOOL_YAML = """---
mcp_id: malicious-{name}
name: 恶意夹具-{name}
version: 1.0.0
description: 测试夹具：尝试越权工具调用。
source: local
command:
  - {python}
  - {script}
network_domains: []
filesystem_read: []
filesystem_write: []
external_commands: []
data_categories:
  - current_message_text
sensitive_operations: []
---
"""

MALICIOUS_ZOMBIE_YAML = """---
mcp_id: malicious-zombie
name: 崩溃夹具
version: 1.0.0
description: 测试夹具：调用时崩溃。
source: local
command:
  - {python}
  - {script}
network_domains: []
filesystem_read: []
filesystem_write: []
external_commands: []
data_categories:
  - current_message_text
sensitive_operations: []
---
"""


def echo_yaml() -> str:
    return ECHO_YAML


def note_yaml(write_dir: str) -> str:
    return NOTE_YAML.format(write_dir=write_dir)


def secret_reader_yaml(python: str) -> str:
    script = str(MALICIOUS_DIR / "secret_reader.py")
    return MALICIOUS_SECRET_READER_YAML.format(python=python, script=script)


def tool_yaml(name: str, script_name: str, python: str) -> str:
    script = str(MALICIOUS_DIR / script_name)
    return MALICIOUS_TOOL_YAML.format(name=name, python=python, script=script)


def zombie_yaml(python: str) -> str:
    script = str(MALICIOUS_DIR / "zombie.py")
    return MALICIOUS_ZOMBIE_YAML.format(python=python, script=script)


def slow_start_yaml(python: str) -> str:
    script = str(MALICIOUS_DIR / "slow_start.py")
    return MALICIOUS_ZOMBIE_YAML.format(python=python, script=script).replace(
        "mcp_id: malicious-zombie", "mcp_id: malicious-slow-start"
    )


def with_integrity(text: str) -> str:
    """给描述附加与自身内容匹配的完整性声明。

    完整性声明的值等于「去除 integrity 行后」内容的 sha256（用户可先
    写描述、算哈希、再补声明行）。
    """
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return text.replace(
        "sensitive_operations:",
        f"integrity: sha256:{digest}\nsensitive_operations:",
    )


# ---------------------------------------------------------------------------
# 非法描述
# ---------------------------------------------------------------------------

MISSING_VERSION_YAML = ECHO_YAML.replace("version: 1.0.0\n", "")
LATEST_VERSION_YAML = ECHO_YAML.replace("version: 1.0.0\n", "version: latest\n")
WILDCARD_VERSION_YAML = ECHO_YAML.replace("version: 1.0.0\n", "version: 1.*\n")
BAD_SOURCE_YAML = ECHO_YAML.replace("source: local", "source: ftp://evil.example.com/x")
BAD_INTEGRITY_YAML = ECHO_YAML.replace(
    "sensitive_operations: []", "integrity: md5:abc\nsensitive_operations: []"
)
MISMATCH_INTEGRITY_YAML = ECHO_YAML.replace(
    "sensitive_operations: []",
    "integrity: sha256:" + "0" * 64 + "\nsensitive_operations: []",
)
BAD_DOMAIN_YAML = ECHO_YAML.replace(
    "network_domains: []", "network_domains:\n  - https://evil.example.com"
)
RELATIVE_DIR_YAML = ECHO_YAML.replace("filesystem_write: []", "filesystem_write:\n  - notes")
SHELL_COMMAND_YAML = ECHO_YAML.replace("  - bridges.mcp.servers.echo", "  - python -m x; curl evil")
UNKNOWN_CATEGORY_YAML = ECHO_YAML.replace("  - current_message_text", "  - full_chat_history")
BROKEN_YAML = "---\nmcp_id: bridges-echo\nversion: [unclosed\n---\n"
CORRUPT_BYTES = b"\xff\xfe\x00broken\x80"


def echo_with_category(category: str) -> str:
    return ECHO_YAML.replace("  - current_message_text", f"  - {category}")


# ---------------------------------------------------------------------------
# 恶意夹具命令（PYTHONPATH 指向 tests 目录使模块可导入）
# ---------------------------------------------------------------------------


def malicious_command(script_name: str, python: str) -> list[str]:
    script = MALICIOUS_DIR / script_name
    return [python, str(script)]
