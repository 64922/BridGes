"""收尾验收：产物级秘密/正文扫描（issue 11 验收标准第 9 条）。

扫描测试日志、失败产物与验收报告，确认不含：
- 高置信度秘密（sk-/AKIA/LTAI/PEM/键名关联赋值）；
- 用户消息正文与附件正文（按 e2e/closeout 确定性替身文案特征标记）。

只输出命中位置（文件 + 行号 + 类型），绝不回显命中值本身。
退出码：0 = 干净；1 = 有命中。

用法：
    .venv/Scripts/python.exe scripts/artifact_secret_scan.py [目标目录 ...]
默认目标：.tmp/closeout-rounds/、test-results/、apps/web/test-results/
与 .scratch/收尾/closeout-acceptance-report.md。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 高置信度秘密模式（与 tests/security/test_secret_scan.py 保持一致）。
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
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
]

#: 白名单标记：值中含这些标记视为测试夹具或文档示例。
_FAKE_MARKERS = (
    "test",
    "example",
    "dummy",
    "fake",
    "placeholder",
    "correct-horse",
    "redacted",
)

#: 用户正文/附件正文特征（仅 e2e/closeout 确定性替身文本；真实用户正文
#: 不落盘到这些产物）。命中即视为正文泄漏。
_BODY_MARKERS = (
    "这是一条来自本地替身模式的确定性测试回答",
    "压力轮次",
    "焦点与跳转链接测试",
    "生成中途我会离开会话",
)

#: 二进制/压缩产物不参与文本扫描（Playwright 合成 PNG/zip 无用户元数据）。
_SKIP_SUFFIXES = {".png", ".webp", ".jpg", ".zip", ".pyc"}

DEFAULT_TARGETS = [
    REPO_ROOT / ".tmp" / "closeout-rounds",
    REPO_ROOT / "test-results",
    REPO_ROOT / "apps" / "web" / "test-results",
    REPO_ROOT / ".scratch" / "收尾" / "closeout-acceptance-report.md",
]


def scan_text(text: str) -> list[tuple[str, int]]:
    """返回 [(命中类型, 行号)]；不包含命中值本身，避免回显秘密正文。"""
    findings: list[tuple[str, int]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in SECRET_PATTERNS:
            match = pattern.search(line)
            if match is None:
                continue
            matched = match.group(0)
            if any(marker in matched.lower() for marker in _FAKE_MARKERS):
                continue
            findings.append((kind, line_no))
        for marker in _BODY_MARKERS:
            if marker in line:
                findings.append((f"body:{marker[:16]}…", line_no))
    return findings


def main() -> int:
    targets = [
        Path(arg) if Path(arg).is_absolute() else REPO_ROOT / arg
        for arg in sys.argv[1:]
    ] or DEFAULT_TARGETS

    total = 0
    for target in targets:
        if target.is_file():
            files = [target]
        elif target.is_dir():
            files = sorted(
                p for p in target.rglob("*") if p.is_file()
            )
        else:
            continue
        for path in files:
            if path.suffix.lower() in _SKIP_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            hits = scan_text(text)
            if hits:
                total += len(hits)
                print(f"{path.relative_to(REPO_ROOT)}:")
                for kind, line_no in hits:
                    print(f"  L{line_no} [{kind}]")
    print(f"产物扫描完成：命中 {total} 处（0 = 干净）。")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
