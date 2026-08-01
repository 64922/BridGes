# BridGes 品牌资产清单与使用规范（Issue 04）

## 原创概念

BridGes 标识表达“连接用户与知识之桥”：左端圆点代表用户，右端四芒星代表被发现和验证的知识，水平桥面与下方拱桥把两端连接起来。图形与字标均为本项目原创绘制，不使用 ChatGPT、OpenAI、Claude、Anthropic 或第三方图标库的品牌路径。

源资产位于 `apps/web/public/brand/`。SVG 是设计源文件，PNG 由 `apps/web/scripts/rasterize-brand.mjs` 从 SVG 重复生成，不应手工修改 PNG。

## 横向字标

| 文件 | 用途 | 背景 |
|------|------|------|
| `bridges-logo-horizontal.svg` | 默认横向完整版 | 浅色 / 透明 |
| `bridges-logo-horizontal-dark.svg` | 深色主题横向完整版 | 深色 / 透明 |
| `bridges-logo-horizontal-mono.svg` | 单色印刷、盖章和不可使用品牌色的场景 | 透明 |
| `bridges-logo-on-light.svg` | 带安全留白与浅色底的展示版 | 自带浅色底 |
| `bridges-logo-on-dark.svg` | 带安全留白与深色底的展示版 | 自带深色底 |

横向字标的 SVG 画布为 196×48；带背景展示版为 260×80。应用内建议显示宽度为 118、140、150 或 180px，最小建议宽度 96px。

## 独立图标

| 文件 | 规格 / 用途 |
|------|-------------|
| `bridges-logo-icon.svg` | 48×48 矢量主图标，浅色主题 |
| `bridges-logo-icon-dark.svg` | 48×48 矢量图标，深色主题 |
| `bridges-logo-icon-mono.svg` | 48×48 单色矢量图标 |
| `bridges-logo-icon-16.png` | 浏览器小图标 |
| `bridges-logo-icon-24.png` | 紧凑工具区 |
| `bridges-logo-icon-32.png` | 桌面导航与 Windows 常用尺寸 |
| `bridges-logo-icon-48.png` | 桌面快捷方式 |
| `bridges-logo-icon-64.png` | 高分屏桌面导航 |
| `bridges-logo-icon-128.png` | 桌面应用和安装器 |
| `bridges-logo-icon-256.png` | Windows / Linux 大图标 |
| `bridges-logo-icon-512.png` | 高分辨率主图标 |
| `bridges-logo-icon-dark-32.png` / `-256.png` | 深色主题常用导出 |
| `bridges-logo-icon-mono-32.png` / `-256.png` | 单色常用导出 |

Next.js 页签图标使用 `apps/web/src/app/icon.svg`，它复用同一图形语言并增加不透明底板，避免浏览器主题导致轮廓消失。

## 使用规则

- 图标四周至少保留一个左端圆点直径（8 个 SVG 单位）的净空。
- 不拉伸、不旋转、不改变图形各部分比例，不在标识上叠加阴影、渐变或其他品牌图形。
- 浅色背景使用默认版，深色背景使用 `-dark` 版；需要单色输出时只使用 `-mono` 版。
- 小于 16px 时不再缩小独立图标；横向字标小于 96px 时改用独立图标。
- 应用组件通过 `BrandLogo` 选择横向 / 独立和浅色 / 深色变体，不在页面内复制 SVG 路径。

## 可重复导出

在仓库根目录执行：

```powershell
node apps/web/scripts/rasterize-brand.mjs
```

脚本使用项目锁定的 Playwright Chromium 逐项生成 PNG。导出后应运行 Issue 04 的视觉回归，确认尺寸、透明背景和深浅色变体没有漂移。
