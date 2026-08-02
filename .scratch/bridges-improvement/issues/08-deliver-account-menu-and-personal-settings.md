# 08 — 交付账户上拉菜单与个人资料设置

Status: ready-for-human
Blocked by: [04 — 建立 ChatGPT 桌面交互基线与 BridGes 原创视觉资产](./04-establish-desktop-design-baseline-and-brand-assets.md), [07 — 交付用户名与 QQ 邮箱注册登录页面](./07-deliver-qq-username-auth-pages.md)
Covered requirements: UI-04, UI-05, NAV-04, NAV-05, AUTH-03, ACCOUNT-01, DESKTOP-01
ADRs: [ADR-0003](../../../docs/adr/0003-stable-account-id-and-login-identifiers.md), [ADR-0018](../../../docs/adr/0018-device-account-switching-and-reauthentication.md), [ADR-0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

在电脑端侧栏底部交付与 ChatGPT 交互方式一致、视觉为 BridGes 原创的用户信息入口和向上展开菜单。菜单按固定顺序提供“切换账号、密钥设置、个人资料、退出登录”。本 Issue 同时交付可实际修改头像和用户名的个人资料页、受保护的密钥设置入口、正确退出并跳转登录页的会话闭环；尚未由 Issue 10 接入的模型探测必须显示真实“尚未配置”状态，不能假装能力可用。

## Acceptance criteria

- [x] 侧栏底部显示当前账户头像、用户名和可访问名称；点击或键盘激活后菜单向上展开。
- [x] 菜单顺序严格为“切换账号、密钥设置、个人资料、退出登录”，无额外“更多”入口。
- [x] 菜单支持方向键、Home/End、Enter/Space、Esc 和点击外部关闭；关闭后焦点回到触发按钮。
- [x] 个人资料页可上传或选择静态头像、修改用户名，并在保存后同步刷新侧栏与当前会话展示；稳定账户 ID 和 QQ 邮箱归属不变化。
- [x] 头像上传校验真实类型、大小和账户归属，通过授权接口读取，不暴露宿主文件路径，也不据头像推断身份、性格或情绪。
- [x] 密钥设置入口打开受保护页面；在 Issue 10 完成前仅显示准确的“尚未配置”状态和下一步说明，不展示 Stub 成功。
- [x] 退出登录会撤销当前会话 Cookie、清理当前活动账户状态并跳转登录页；浏览器后退不能重新进入受保护内容。
- [x] 菜单、个人资料页和密钥设置页完整覆盖 loading、empty/unconfigured、error、permission/reauth required 和成功状态，中文文案可操作且不使用占位内容。
- [x] 非当前账户不能通过猜测 ID 修改头像、用户名或读取设置；权限拒绝不会短暂渲染受保护子页面。
- [x] 电脑端仅用键盘可完成打开菜单、进入设置、上传头像、修改用户名、保存和退出；视觉回归符合 Issue 04。

## Verification

```powershell
conda run -n agent python -m pytest -k "profile or avatar or session or authorization"
conda run -n agent python -m ruff check .
conda run -n agent python -m mypy src
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
npm --prefix apps/web run test:e2e -- --project=chromium
```

## Non-goals

- 不在本 Issue 完成多账户会话切换规则；由 Issue 09 完成。
- 不在本 Issue 保存或探测真实百炼 Key；由 Issue 10 完成。
- 不实现动画数字人、声音克隆或移动端账户菜单。

## Blocked by

- [04 — 建立 ChatGPT 桌面交互基线与 BridGes 原创视觉资产](./04-establish-desktop-design-baseline-and-brand-assets.md)
- [07 — 交付用户名与 QQ 邮箱注册登录页面](./07-deliver-qq-username-auth-pages.md)

## Comments

“尚未配置”是有明确含义和操作入口的真实空状态，不是占位页。Issue 10 完成后应在相同页面内替换为真实能力探测流程。

2026-08-03 交付完成，等待人工视觉验收。

**工作内容总结**

- 侧栏底部接入真实账户头像、用户名与向上展开菜单，菜单固定为“切换账号 / 密钥设置 / 个人资料 / 退出登录”，完整实现 WAI-ARIA 键盘和焦点合同。
- 交付个人资料与密钥设置受保护页面：用户名与头像更新会同步当前会话；头像经账户隔离加密对象库保存和授权读取；密钥页在 Issue 10 前只呈现真实“尚未配置”状态，并要求近期密码重认证。
- 补齐资料、头像、重认证、密钥状态和退出 API，稳定账户 ID 与 QQ 邮箱保持不变；失效会话由服务端原子清除 Cookie，前端在鉴权完成前不渲染受保护子页面。
- 覆盖 loading、empty/unconfigured、error、permission、reauth required、success 与退出/后退闭环；新增 10 条 Issue 08 E2E 场景和 9 张三视口视觉快照。

**代码审查与 Bug 修复汇总**

- 修复头像二进制误存 Identity 状态库的问题，改为仅保存对象元数据，内容进入账户隔离加密对象库；补齐跨账户授权测试。
- 合并注册与资料更新的用户名规范化/唯一性校验，防止规则漂移；强化 PNG/JPEG 完整解码、尺寸、CRC 和 APNG 拒绝校验。
- 修复 401 会话失效等待异步退出导致受保护内容短暂保留、注册与旧 Cookie 清理竞态，以及密钥页把所有 401 误报为密码错误的问题。
- 修复退出时 `Clear-Site-Data: storage` 误清设备级偏好的范围错误；现在只撤销当前会话并清缓存，设备偏好保持不变。
- 修复菜单延迟回焦覆盖输入区或对话框焦点、登录后根页导航缺少原生回退、重复跳转链接及文件选择器 E2E 焦点波动。
- code-review 双轴复核最终结果：Standards 0 项未解决缺陷，Spec 0 项未解决缺陷。

**验证结果**

- `conda run -n agent python -m pytest`：1075 passed，1 条第三方弃用警告。
- 改动范围 `ruff check`：通过；全仓 `ruff check .` 仍为前置 Issue 已记录的 231 个预存错误，本 Issue 改动文件为零错误。
- `conda run -n agent python -m mypy src`：138 个源文件零问题。
- `npm --prefix apps/web run typecheck`、`npm --prefix apps/web run build`：通过。
- Chromium 全量 E2E 75 个场景均通过；纯键盘头像上传稳定性专项连续 5 次通过；Issue 08 三视口视觉快照通过。
