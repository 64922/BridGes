# 形成前端信息架构与关键交互原型

Type: prototype
Status: resolved
Blocked by: 02, 04, 05, 06, 08

## Question

注册、登录、凭据恢复等公共入口，以及认证后的全局科学伙伴、科学项目空间、学习实验室、表达工坊、可视化实验室、证据与校验台、画像与记忆中心及系统设置应如何组织页面、导航、状态和跨工作台流转？用户怎样查看智能体进度、中间产物、引用、画像调用、风险和版本差异，而不被系统复杂度淹没？

## Comments

- 2026-07-24：建立并评议了三案一页式交互原型，分别以项目工作台、伙伴与任务舞台、
  证据对象地图作为第一视觉中心。原型包含认证入口、三域边界、运行与产物双状态、
  引用、画像切片、风险、版本差异和响应式布局。
  - [方案 A 预览](../prototypes/frontend-information-architecture/preview-A.png)
  - [方案 B 预览](../prototypes/frontend-information-architecture/preview-B.png)
  - [方案 C 预览](../prototypes/frontend-information-architecture/preview-C.png)
- 浏览器检查通过三案深链、键盘切换、运行抽屉、认证页签、375px 无横向溢出、
  可见交互目标尺寸与控制台错误检查；确认前保持 `claimed`。

## Answer

用户接受默认组合：以方案 A 的项目工作台作为认证后主壳，吸收方案 B 的全局
科学伙伴与结构化任务舞台分工，把方案 C 的科学出处图作为证据与校验台及复杂
项目的可选专业视图。运行、引用、画像切片、风险和版本采用统一检查器渐进披露，
运行状态与产物可信状态始终并列，三域边界持续可见。

完整决策见
[decisions/09-frontend-information-architecture.md](../decisions/09-frontend-information-architecture.md)。
