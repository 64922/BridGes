# BridGes 设计令牌（Issue 04，2026-08-02）

> 美术方向：Claude-inspired 的温暖、克制、高可读性——暖纸面底色、暖墨色文字、
> 暖赭石（terracotta 方向）主强调色、衬线标题。**全部取值为 BridGes 原创选择，
> 不复制 Claude / Anthropic 的任何配色值或品牌资产。**
>
> 实现位置：`apps/web/src/styles/globals.css`（`@layer base` 中的 `:root` 与
> `[data-theme="dark"]`）。深色主题通过 `<html data-theme="dark">` 启用，
> 设置模板（`/templates/settings`）提供真实切换并持久化到 localStorage。

## 颜色（浅色 / 深色）

| 令牌 | 浅色 | 深色 | 用途 |
|------|------|------|------|
| `--color-bg-primary` | `#FAF9F5` | `#201E19` | 页面底色（暖纸面 / 暖墨） |
| `--color-bg-secondary` | `#F2EFE6` | `#26231D` | 侧栏、次级区块 |
| `--color-surface` | `#FFFFFF` | `#2A2822` | 卡片、输入区 |
| `--color-surface-elevated` | `#FFFFFF` | `#34312A` | 浮层 |
| `--color-border` | `#E4E0D5` | `#413C31` | 分隔边框 |
| `--color-border-strong` | `#C7C2B2` | `#57503F` | 输入框等强边框 |
| `--color-text-primary` | `#1F1E1A` | `#F5F2EA` | 正文 |
| `--color-text-secondary` | `#4A463C` | `#CFC9B8` | 次要文字 |
| `--color-text-tertiary` | `#6B6659` | `#A8A291` | 辅助文字 |
| `--color-text-on-accent` | `#FFFFFF` | `#241207` | 强调色上的文字 |
| `--color-accent-primary` | `#A84B2A` | `#E1936B` | 主强调（按钮、品牌） |
| `--color-accent-primary-hover` | `#8C3E22` | `#EAA582` | 主强调悬停 |
| `--color-accent-primary-soft` | `#F3E2D8` | `#3D2B22` | 选中态柔和底 |
| `--color-accent-secondary` | `#1D4ED8` | `#93B4FD` | 链接 |
| `--color-accent-secondary-hover` | `#1E40AF` | `#B6CBFE` | 链接悬停 |
| `--color-status-success` / `-bg` | `#166534` / `#DCFCE7` | `#86EFAC` / `#14532D` | 成功 |
| `--color-status-wait` / `-bg` | `#92400E` / `#FEF3C7` | `#FCD34D` / `#713F12` | 警告 / 等待 |
| `--color-status-error` / `-bg` | `#B91C1C` / `#FEE2E2` | `#FCA5A5` / `#7F1D1D` | 错误 |
| `--color-status-info` / `-bg` | `#1D4ED8` / `#DBEAFE` | `#93B4FD` / `#1E3A8A` | 信息 |
| `--color-status-unknown` / `-bg` | `#525252` / `#F5F5F5` | `#D4D4D4` / `#404040` | 未知 |
| `--color-focus-ring` | `#1D4ED8` | `#93B4FD` | 可见焦点环 |
| `--color-focus-ring-offset` | `#FAF9F5` | `#201E19` | 焦点环衬底 |

**规则**：颜色只作冗余编码——所有状态必须同时有图标与中文文字
（`StatusBadge`、`StateBlock` 强制此约定）。

## 字体与层级

| 令牌 | 值 | 用途 |
|------|-----|------|
| `--font-sans` | system-ui / PingFang SC / Microsoft YaHei 栈 | 正文与界面 |
| `--font-serif` | Songti SC / SimSun / Noto Serif CJK 栈 | 标题、公式、字标 |
| `--font-mono` | ui-monospace / Consolas 栈 | 代码 |
| `--text-xs` … `--text-3xl` | 0.75 / 0.875 / 1 / 1.125 / 1.25 / 1.5 / 1.875 rem | 字号阶梯（全部 rem，支持文本缩放） |
| `--line-height-tight/normal/relaxed` | 1.25 / 1.5 / 1.625 | 行高 |
| 层级 | h1=2xl、h2=xl、h3=lg（衬线 600） | 页面与卡片标题 |

## 留白、圆角、阴影、边框

| 令牌 | 值 |
|------|-----|
| `--space-1` … `--space-16` | 0.25 / 0.5 / 0.75 / 1 / 1.25 / 1.5 / 2 / 2.5 / 3 / 4 rem |
| `--radius-sm/md/lg/xl/full` | 0.25 / 0.375 / 0.5 / 0.75 rem / 9999px |
| `--shadow-sm/md/lg` | 单层浅影 → 双层浮层影（深色主题不透明度提高） |
| 边框 | 统一 `1px solid var(--color-border[-strong])`，无 2px+ 装饰边框 |

## 动效

| 令牌 | 值 |
|------|-----|
| `--motion-duration-fast/base/slow` | 150 / 200 / 300 ms |
| `--motion-easing` | `cubic-bezier(0.4, 0, 0.2, 1)` |
| `prefers-reduced-motion` | 全部动画 / 过渡时长归零（0.001ms），E2E 覆盖 |

## 焦点

`:focus-visible` 统一 `0.1875rem solid var(--color-focus-ring)` + `0.125rem` 偏移，
覆盖链接、按钮、输入、菜单项与 `[tabindex]` 元素；对话框与菜单的焦点管理见
`apps/web/src/components/bridges/Dialog.tsx`、`Menu.tsx`。

## 对比度检查

校验脚本：`python scripts/check_contrast.py`（WCAG 2.1 相对亮度公式，CI 可重复执行）。
2026-08-02 结果：**37/37 对全部通过**，关键值：

| 组合 | 比值 | 要求 |
|------|------|------|
| 浅色 正文 / 页面底 | 15.83:1 | ≥ 4.5:1 |
| 浅色 次要文字 / 页面底 | 8.93:1 | ≥ 4.5:1 |
| 浅色 辅助文字 / 卡片 | 5.72:1 | ≥ 4.5:1 |
| 浅色 白字 / 主强调（按钮） | 5.67:1 | ≥ 4.5:1 |
| 浅色 链接蓝 / 卡片 | 6.70:1 | ≥ 4.5:1 |
| 浅色 焦点环 / 页面底 | 6.36:1 | ≥ 3:1 |
| 浅色 成功 / 成功底 | 6.49:1 | ≥ 4.5:1 |
| 浅色 警告 / 警告底 | 6.37:1 | ≥ 4.5:1 |
| 浅色 错误 / 错误底 | 5.30:1 | ≥ 4.5:1 |
| 深色 正文 / 页面底 | 14.88:1 | ≥ 4.5:1 |
| 深色 深字 / 主强调（按钮） | 7.37:1 | ≥ 4.5:1 |
| 深色 链接 / 卡片 | 7.15:1 | ≥ 4.5:1 |
| 深色 错误 / 错误底 | 5.28:1 | ≥ 4.5:1 |

（完整 37 对见脚本输出；任一对失败脚本以非零码退出。）

## 布局

| 令牌 | 值 | 用途 |
|------|-----|------|
| `--sidebar-width` / `--sidebar-width-collapsed` | 16rem / 4rem | 侧栏展开 / 折叠窄轨 |
| `--chat-column-width` | 48rem | 消息列限宽 |
| `--topbar-height` | 3.5rem | 顶条 |
| `--max-content-width` | 80rem | 内容上限 |
| `--target-size` | 2.75rem（44px） | 最小可点目标 |
