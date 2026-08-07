# 模板目录（Issue 04 设计基线产物）

本目录是 Issue 04「建立电脑端设计基线与 BridGes 原创视觉资产」的验收载体：
登录/注册/聊天/列表/详情/设置六类页面的独立模板，供 `e2e/issue04-design-baseline.spec.ts`
（56 处引用）做视觉基线验收。

**不是生产代码**：

- `middleware.ts` 在非开发环境把 `/templates` 统一重定向到 `/`，用户不可达；
- 生产页面位于 `src/app/(app)/`、`src/app/(public)/` 与 `src/components/`（AppSidebar、
  Composer 等），模板中的 ChatSidebar / use-template-state 是模板专用实现，
  不要在生产组件中导入；
- 架构审查曾建议删除本目录（减少构建体积与导航噪声），但被 Issue 04 验收测试依赖
  否决：删除需要重写 527 行评分相关 E2E。保留即验收契约，勿随意改动模板行为。
