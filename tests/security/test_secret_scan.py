"""Issue 01 防回归：秘密扫描等价检查。

扫描仓库源码、配置、脚本与文档（排除依赖、构建产物与历史封存区），确认
没有百炼 Key、SMTP 授权码、私钥或会话令牌被提交。扫描器只输出匹配位置
（文件 + 行号 + 秘密类型），绝不回显秘密值本身——任何检查输出都不含秘密
正文。测试夹具中的假值（含 test/example/dummy 等标记）属于显式白名单，
不算提交的秘密。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 排除目录：依赖、构建产物、版本控制与历史封存区（旧 Wayfinder 规划与
#: 旧数据库只读保留，不在扫描范围；.tmp 为 gitignore 的临时根目录，含
#: pytest basetemp、历史 worktree 与发布门产物，不属于提交内容）。
EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    ".next",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "build",
    "dist",
    ".tmp",
    ".scratch/science-companion-plan",
}

#: 仅扫描文本扩展名，二进制数据库与图片一律跳过。
TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".sh",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".txt",
    ".html",
    ".css",
    ".ini",
    ".cfg",
}

#: 高置信度秘密模式（避免误伤测试夹具与文档示例）。
#:   - 百炼/OpenAI 风格 API Key：sk- 后至少 32 位字符（测试假值仅 16 位，不命中）
#:   - AWS Access Key
#:   - PEM 私钥块
#:   - 键名关联的高熵秘密（SMTP 授权码、密码、令牌等赋值形式）；
#:     键名前缀用 (?<![A-Za-z0-9_.]) 排除 errors.password 这类 UI 字段
#:     与 BRIDGES_ 环境变量声明
#:   - Tavily Key（Issue 07，ADR-0029）：tvly- 前缀 + 至少 16 位连续字母数字；
#:     测试假值（tvly-test-key-123、tvly-probe-test-key 等）含白名单标记
#:     或连字符/短值，不会命中
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    # Issue 39 AC5：百炼/阿里云 AccessKey（LTAI 前缀 + 20 位字母数字）
    ("aliyun_key", re.compile(r"\bLTAI[0-9A-Za-z]{20}\b")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "credential",
        re.compile(
            r"""(?i)(?<![A-Za-z0-9_.])(api[_-]?key|secret|password|token|"""
            r"""access[_-]?code|auth[_-]?code|session[_-]?token|smtp[_-]?code)\b"""
            r"""\s*[=:]\s*['"][^'"]{12,}['"]"""
        ),
    ),
    ("tavily_key", re.compile(r"\btvly-[A-Za-z0-9]{16,}\b")),
]

#: 键名关联模式豁免目录：测试夹具使用假密码/假令牌做断言是合法用法
#: （sk-/AWS/PEM 高置信度模式仍对全仓生效，不做豁免）。
CREDENTIAL_EXEMPT_DIRS = {"tests"}

#: 白名单标记：值中含这些标记视为测试夹具或文档示例，不视为真实秘密。
_FAKE_VALUE_MARKERS = (
    "test",
    "example",
    "dummy",
    "fake",
    "placeholder",
    "correct-horse",
    "redacted",
)


def _scan_text(text: str, *, is_credential_exempt: bool) -> list[tuple[str, int]]:
    """返回 [(秘密类型, 行号)]；不包含匹配值本身，避免回显秘密正文。"""
    findings: list[tuple[str, int]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in _SECRET_PATTERNS:
            if is_credential_exempt and kind == "credential":
                continue
            match = pattern.search(line)
            if match is None:
                continue
            matched = match.group(0)
            if any(marker in matched.lower() for marker in _FAKE_VALUE_MARKERS):
                continue
            findings.append((kind, line_no))
    return findings


def _iter_text_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        files.append(path)
    return files


def test_repository_contains_no_committed_secrets() -> None:
    """全仓库（排除依赖/构建/封存区）不得出现可识别的秘密格式。"""
    findings: list[str] = []
    for path in _iter_text_files(REPO_ROOT):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        is_credential_exempt = any(part in CREDENTIAL_EXEMPT_DIRS for part in path.parts)
        for kind, line_no in _scan_text(text, is_credential_exempt=is_credential_exempt):
            relative = path.relative_to(REPO_ROOT)
            findings.append(f"{relative}:{line_no} [{kind}]")

    assert findings == [], (
        "发现疑似被提交的秘密（只报告位置，不回显值）：\n" + "\n".join(findings)
    )
