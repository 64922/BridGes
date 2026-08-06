"""BridGes 正式电脑端路由的静态完整性扫描（Issue 38，范围由 Issue 41 扩展）。

扫描范围（正式路由；开发模板 templates/ 为设计基线验收专用、生产不可达，
不在范围内）：
- apps/web/src/app/(public)/           登录 / 注册 / 公共入口
- apps/web/src/app/(app)/              账户首页 / 模块 / 聊天 / 账户子路由
- apps/web/src/components/             共享组件

旧 Science Companion 工作台（projects/[projectId]、components/project、
ProjectLayout/SidebarNav/InspectorPanel）与空壳页（account/eval）已由
Issue 41 退役，因此全部纳入扫描。

检查项：
1. 品牌黑名单：不再向普通用户显示 Science Companion 品牌
2. 占位文案黑名单：「将在这里呈现」「敬请期待」「尚未实现」「开发中」
3. 空链接 / 假按钮：href="#"、href=""、空 onClick
4. 硬编码成功：写死的「已成功」「已完成」「运行中」字面量（只报疑似，人工复核）

用法：python scripts/check_frontend_completeness.py
存在违规时退出码非零并打印明细。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_SRC = ROOT / "apps" / "web" / "src"

# 扫描范围：正式路由目录 + 共享组件（templates/ 为开发环境设计基线模板、
# 生产不可达，不扫描；domain-packs 为领域包专家工作台，专家专用面另行核对）
SCOPE_DIRS = [
    WEB_SRC / "app" / "(public)",
    WEB_SRC / "app" / "(app)" / "(modules)",
    WEB_SRC / "app" / "(app)" / "chat",
    WEB_SRC / "app" / "(app)" / "account",
    WEB_SRC / "components" / "account",
    WEB_SRC / "components" / "bridges",
    WEB_SRC / "components" / "design-system",
    WEB_SRC / "components" / "learning-projects",
    WEB_SRC / "components" / "mcp",
    WEB_SRC / "components" / "plugins",
]

BRAND_BLACKLIST = ["Science Companion", "science_companion"]
PLACEHOLDER_BLACKLIST = ["将在这里呈现", "敬请期待", "尚未实现", "开发中", "占位符"]
EMPTY_HREF_PATTERN = re.compile(r'href\s*=\s*["\'](?:#|)["\']')
EMPTY_ONCLICK_PATTERN = re.compile(r"onClick\s*=\s*\{\s*\(\s*\)\s*=>\s*\{\s*\}\s*\}")
# 疑似硬编码状态：字符串字面量里的完成/运行断言（排除映射表与注释场景，
# 只列疑似行供人工复核）
HARDCODED_STATUS_PATTERN = re.compile(
    r'["\'](?:已成功|已完成|运行中|正在运行)["\']'
)

# 状态映射表白名单：按 status prop 动态选择标签/图标/颜色的合法映射表，
# 不是写死的显示状态（人工逐一核验过）
ALLOWED_HARDCODED_FILES = {
    WEB_SRC / "components" / "design-system" / "StatusBadge.tsx",
    WEB_SRC / "components" / "account" / "TaskSchedule.tsx",
    WEB_SRC / "components" / "bridges" / "chat" / "ImageTaskCard.tsx",
    WEB_SRC / "components" / "bridges" / "chat" / "VideoTaskCard.tsx",
    WEB_SRC / "components" / "mcp" / "McpCenter.tsx",
}


def iter_target_files() -> list[Path]:
    files: list[Path] = []
    for directory in SCOPE_DIRS:
        if not directory.exists():
            continue
        files.extend(directory.rglob("*.tsx"))
        files.extend(directory.rglob("*.ts"))
    return sorted(set(files))


def scan() -> list[str]:
    findings: list[str] = []
    for file in iter_target_files():
        try:
            text = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = file.relative_to(WEB_SRC)
        for line_no, line in enumerate(text.splitlines(), start=1):
            for bad in BRAND_BLACKLIST:
                if bad in line:
                    findings.append(f"品牌黑名单 [{bad}] {rel}:{line_no}  {line.strip()}")
            for bad in PLACEHOLDER_BLACKLIST:
                if bad in line:
                    findings.append(f"占位文案 [{bad}] {rel}:{line_no}  {line.strip()}")
            if EMPTY_HREF_PATTERN.search(line):
                findings.append(f"空链接 {rel}:{line_no}  {line.strip()}")
            if EMPTY_ONCLICK_PATTERN.search(line):
                findings.append(f"无 handler 按钮 {rel}:{line_no}  {line.strip()}")
            if file not in ALLOWED_HARDCODED_FILES and HARDCODED_STATUS_PATTERN.search(line):
                findings.append(f"疑似硬编码状态（人工复核）{rel}:{line_no}  {line.strip()}")
    return findings


def main() -> int:
    findings = scan()
    if findings:
        print(f"发现 {len(findings)} 处疑似违规：")
        for item in findings:
            print(f"  - {item}")
        return 1
    print("扫描通过：正式路由无品牌泄漏、占位文案、空链接或假按钮。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
