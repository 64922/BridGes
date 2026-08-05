"""Issue 34 测试夹具：在内存中构造各种声明式 SKILL zip 包。

合法包与各类违规包（脚本/可执行/符号链接/路径穿越/越界引用/隐藏文件/
超限/损坏）都由 zipfile 确定性构造，不依赖磁盘夹具。
"""

from __future__ import annotations

import io
import zipfile

VALID_SKILL_MD = """---
plugin_id: test-todo
name: 待办整理助手
version: 1.2.3
description: 把聊天中的待办整理成清单的声明式 SKILL。
source: 用户自制
license: 仅供个人使用
capabilities:
  - 提取待办事项
  - 输出清单
data_categories:
  - 用户粘贴的待办文本
---

# 待办整理助手

把文本中的待办事项整理成清单。

参见 [说明文档](docs/usage.md) 与模板 [templates/checklist.html](templates/checklist.html)。
"""

VALID_EXTRA = {
    "docs/usage.md": "# 用法\n见 [README](../SKILL.md)。\n",
    "templates/checklist.html": "<ul><!-- 清单 --></ul>\n",
    "resources/icon.svg": "<svg xmlns='http://www.w3.org/2000/svg'/>\n",
    "references/notes.txt": "设计备注。\n",
}


def build_valid_zip(skill_md: str = VALID_SKILL_MD, extra: dict[str, bytes | str] | None = None) -> bytes:
    """构造一个通过安全检查的合法声明式包。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SKILL.md", skill_md)
        for name, content in (extra if extra is not None else VALID_EXTRA).items():
            archive.writestr(name, content)
    return buffer.getvalue()


def build_zip_with(*, entries: list[tuple[str, bytes]], skill_md: str | None = None) -> bytes:
    """按给定条目构造 zip（条目为 (路径, 内容)）。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        if skill_md is not None:
            archive.writestr("SKILL.md", skill_md)
        for name, content in entries:
            archive.writestr(name, content)
    return buffer.getvalue()


def build_symlink_zip(target: str = "SKILL.md") -> bytes:
    """构造含符号链接条目的 zip（UNIX S_IFLNK 模式标志）。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SKILL.md", VALID_SKILL_MD)
        info = zipfile.ZipInfo("link.txt")
        info.external_attr = (0o120000 << 16) | 0o777  # S_IFLNK
        archive.writestr(info, target)
    return buffer.getvalue()


def build_corrupt_zip() -> bytes:
    return b"not-a-real-zip-archive-bytes"


def build_too_large_zip() -> bytes:
    """解压后超过上限的包（受支持类型的重复内容，压缩比高）。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SKILL.md", VALID_SKILL_MD)
        payload = b"x" * (21 * 1024 * 1024)
        archive.writestr("resources/blob.txt", payload)
    return buffer.getvalue()
