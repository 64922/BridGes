"""声明式 SKILL 包安装检查器（Issue 34）。

用户上传包只允许声明式 SKILL.md、静态参考资料、模板与资源。检查器在
安装前对 zip 包执行确定性安全闭锁：拒绝脚本/可执行文件、符号链接、
路径穿越、越界引用、不受支持的文件与损坏或超限的包，并为每一项拒绝
给出可操作的具体中文原因。检查器是纯函数式模块：不落库、不落盘、不
产生任何副作用，同一输入永远得到同一结果。
"""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
from dataclasses import dataclass, field

from bridges.contracts.plugins import (
    PluginCheckResult,
    PluginFileEntry,
    PluginFileKind,
)

# 包体与解压上限（zip 炸弹防护：压缩后 5MB、解压后 20MB、条目 500）。
MAX_PLUGIN_BYTES = 5 * 1024 * 1024
MAX_UNPACKED_BYTES = 20 * 1024 * 1024
MAX_ENTRIES = 500

# 受支持文件扩展名白名单：SKILL.md 之外的静态参考、模板与资源。
ALLOWED_EXTENSIONS = frozenset(
    {
        # 文档与数据（声明式内容）
        ".md",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".csv",
        ".html",
        ".css",
        ".xml",
        # 静态图片
        ".png",
        ".jpg",
        ".jpeg",
        ".svg",
        ".gif",
        ".webp",
        ".ico",
        # 字体
        ".woff",
        ".woff2",
    }
)

# 明确命名的脚本/可执行类别（拒绝时给出类别名而非泛泛「不支持」）。
_SCRIPT_EXTENSIONS = frozenset(
    {
        ".py",
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".jsx",
        ".sh",
        ".bash",
        ".zsh",
        ".bat",
        ".cmd",
        ".ps1",
        ".vbs",
        ".rb",
        ".pl",
        ".php",
        ".lua",
        ".r",
    }
)
_EXECUTABLE_EXTENSIONS = frozenset(
    {
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".bin",
        ".jar",
        ".class",
        ".o",
        ".obj",
        ".msi",
        ".apk",
        ".com",
        ".scr",
        ".pif",
        ".wasm",
        ".swf",
        ".hta",
        ".lnk",
        ".reg",
    }
)
_EXTERNAL_SCHEMES = ("http://", "https://", "mailto:", "tel:", "data:", "file://")


def _normalize_zip_path(name: str) -> str | None:
    """把 zip 条目名规范化为包内相对路径；非法返回 None。

    zip 条目名使用 POSIX 分隔；防御性兼容反斜杠并拒绝盘符、绝对路径
    与 .. 逃逸。
    """
    name = name.replace("\\", "/")
    if name.startswith("/"):
        return None
    if re.match(r"^[A-Za-z]:", name):
        return None
    cleaned = posixpath.normpath(name)
    if cleaned == ".." or cleaned.startswith("../"):
        return None
    if cleaned.startswith("/"):
        return None
    return cleaned


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    """符号链接条目标志：UNIX 文件模式 S_IFLNK（0o120000）。"""
    mode = (info.external_attr >> 16) & 0o170000
    return mode == 0o120000


def _entry_kind(path: str) -> PluginFileKind:
    """按路径与扩展名给内容清单分类（SKILL.md/参考/模板/资源）。"""
    lowered = path.lower()
    if lowered == "skill.md":
        return PluginFileKind.SKILL_MD
    if lowered.startswith("templates/"):
        return PluginFileKind.TEMPLATE
    if lowered.startswith(("references/", "docs/")):
        return PluginFileKind.REFERENCE
    return PluginFileKind.RESOURCE


def _rejected_classification(path: str) -> str | None:
    """对不受支持的文件给出具体类别名；支持则返回 None。"""
    lowered = path.lower()
    dot = lowered.rfind(".")
    ext = lowered[dot:] if dot >= 0 else ""
    if ext in _SCRIPT_EXTENSIONS:
        return f"脚本文件（{ext}）"
    if ext in _EXECUTABLE_EXTENSIONS:
        return f"可执行或二进制文件（{ext}）"
    return None


def _parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """解析 SKILL.md 的 YAML 子集 frontmatter。

    支持 name/version/description/source/license 标量与
    capabilities/data_categories 列表（- 前缀行）。返回（字段, 正文）。
    """
    fields: dict[str, object] = {}
    body = text
    if not text.startswith("---"):
        return fields, body
    lines = text.splitlines()
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end is None:
        return fields, body
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
        if value:
            fields[key] = value
        else:
            fields[key] = []
            current = key
    body = "\n".join(lines[end + 1 :])
    return fields, body


# 引用提取：markdown 链接与图片的 () 目标（不跨行）。
_REFERENCE_RE = re.compile(r"!?\[[^\]]*\]\(([^)]*)\)")


def _is_external_reference(target: str) -> bool:
    target = target.strip()
    if not target:
        return True  # 空引用视为无害
    if target.startswith("#"):
        return True  # 页内锚点
    if target.startswith("/"):
        return False  # 绝对路径引用：不指向包内文件，属于越界
    lowered = target.lower()
    if any(lowered.startswith(scheme) for scheme in _EXTERNAL_SCHEMES):
        return True
    return ":" in target.split("/", 1)[0]  # 其他 scheme（如 wiki:）


@dataclass
class _Inspection:
    """一次检查的中间结果。"""

    reasons: list[str] = field(default_factory=list)
    files: list[PluginFileEntry] = field(default_factory=list)
    names: set[str] = field(default_factory=set)
    total_bytes: int = 0
    skill_md_text: str | None = None


class PluginPackageChecker:
    """声明式 SKILL 包的确定性安装检查器（无副作用）。"""

    def check(self, content: bytes) -> PluginCheckResult:
        inspection = _Inspection()
        try:
            self._inspect(content, inspection)
        except zipfile.BadZipFile:
            inspection.reasons.append("压缩包损坏：无法作为 zip 解压，请重新打包后上传。")
        if inspection.reasons:
            # 拒绝时仍尽力提取声明的标识：失败记录按真实标识归组，
            # 修正后的同标识包可直接覆盖（Issue 34 AC7 可恢复失败）。
            skill_id: str | None = None
            if inspection.skill_md_text:
                fields, _ = _parse_frontmatter(inspection.skill_md_text)
                skill_id = str(
                    fields.get("plugin_id") or fields.get("skill_id") or ""
                ) or None
            return PluginCheckResult(
                ok=False, skill_id=skill_id, rejected_reasons=inspection.reasons
            )
        skill_text = inspection.skill_md_text or ""
        fields, _ = _parse_frontmatter(skill_text)
        skill_id = str(fields.get("plugin_id") or fields.get("skill_id") or "")
        name = str(fields.get("name") or "")
        version = str(fields.get("version") or "")
        missing = []
        if not skill_id:
            missing.append("插件标识（plugin_id）")
        if not name:
            missing.append("包名（name）")
        if not version:
            missing.append("固定版本（version）")
        if missing:
            return PluginCheckResult(
                ok=False,
                rejected_reasons=[f"SKILL.md 缺少声明：{('、'.join(missing))}。"],
            )
        if skill_id and not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,63}$", skill_id):
            return PluginCheckResult(
                ok=False,
                rejected_reasons=[
                    "SKILL.md 声明的插件标识不合法：只能包含字母、数字、点、下划线与短横线。"
                ],
            )
        capabilities = _string_list(fields.get("capabilities"))
        data_categories = _string_list(fields.get("data_categories"))
        return PluginCheckResult(
            ok=True,
            skill_id=skill_id,
            name=name,
            version=version,
            description=str(fields.get("description") or ""),
            source=str(fields.get("source") or ""),
            license=str(fields.get("license") or ""),
            capabilities=capabilities,
            data_categories=data_categories,
            files=sorted(inspection.files, key=lambda entry: entry.path),
            file_count=len(inspection.files),
            total_bytes=inspection.total_bytes,
        )

    def _inspect(self, content: bytes, result: _Inspection) -> None:
        if len(content) > MAX_PLUGIN_BYTES:
            result.reasons.append(
                f"包体超过上限：允许 {MAX_PLUGIN_BYTES // 1024 // 1024}MB，"
                f"实际 {len(content) / 1024 / 1024:.1f}MB。"
            )
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ENTRIES:
                result.reasons.append(
                    f"条目数超过上限：允许 {MAX_ENTRIES} 条，实际 {len(infos)} 条。"
                )
            for info in infos:
                self._inspect_entry(archive, info, result)

    def _inspect_entry(
        self, archive: zipfile.ZipFile, info: zipfile.ZipInfo, result: _Inspection
    ) -> None:
        raw_name = info.filename
        if info.is_dir():
            return
        if _is_symlink(info):
            result.reasons.append(f"条目 {raw_name} 是符号链接，声明式包不允许链接文件。")
            return
        normalized = _normalize_zip_path(raw_name)
        if normalized is None:
            result.reasons.append(
                f"条目 {raw_name} 路径非法：绝对路径、盘符或 .. 穿越被拒绝。"
            )
            return
        if "\\" in raw_name:
            result.reasons.append(
                f"条目 {raw_name} 路径不规范（含反斜杠），请使用纯正斜杠相对路径。"
            )
            return
        lowered = normalized.lower()
        if lowered.startswith("."):
            result.reasons.append(f"条目 {normalized} 是隐藏文件或位于隐藏目录，不被支持。")
            return
        if lowered != "skill.md":
            classified = _rejected_classification(normalized)
            if classified is not None:
                result.reasons.append(
                    f"条目 {normalized} 是{classified}，声明式包不接受任何可执行内容。"
                )
                return
            if not self._is_allowed(normalized):
                result.reasons.append(
                    f"条目 {normalized} 是不受支持的文件类型：只允许 "
                    "SKILL.md、静态参考、模板与资源（md/txt/json/yaml/csv/"
                    "html/css/xml/图片/字体）。"
                )
                return
        if normalized in result.names:
            result.reasons.append(f"条目 {normalized} 在包内重复出现，请去除重复文件。")
            return
        if result.total_bytes + info.file_size > MAX_UNPACKED_BYTES:
            result.reasons.append(
                f"解压后总大小超过上限：允许 {MAX_UNPACKED_BYTES // 1024 // 1024}MB。"
            )
            return
        if normalized == "SKILL.md":
            content = archive.read(info)
            result.skill_md_text = content.decode("utf-8", errors="replace")
            self._check_skill_md_references(result, content)
        result.names.add(normalized)
        result.files.append(
            PluginFileEntry(
                path=normalized,
                size=info.file_size,
                kind=_entry_kind(normalized),
            )
        )
        result.total_bytes += info.file_size

    def _is_allowed(self, path: str) -> bool:
        lowered = path.lower()
        dot = lowered.rfind(".")
        if dot < 0:
            return False
        return lowered[dot:] in ALLOWED_EXTENSIONS

    def _check_skill_md_references(self, result: _Inspection, content: bytes) -> None:
        """SKILL.md 的相对引用不得逃逸包根（.. 或绝对路径拒绝）。"""
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            result.reasons.append("SKILL.md 必须使用 UTF-8 编码。")
            return
        for match in _REFERENCE_RE.finditer(text):
            target = match.group(1).strip()
            if _is_external_reference(target):
                continue
            normalized = posixpath.normpath(target)
            if normalized.startswith("..") or normalized.startswith("/"):
                result.reasons.append(
                    f"SKILL.md 引用 {target} 越界：引用必须指向包内文件。"
                )


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


__all__ = [
    "ALLOWED_EXTENSIONS",
    "MAX_ENTRIES",
    "MAX_PLUGIN_BYTES",
    "MAX_UNPACKED_BYTES",
    "PluginPackageChecker",
]
