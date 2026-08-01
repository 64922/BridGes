# 08 — 交付账户上拉菜单与个人资料设置

Status: ready-for-agent
Blocked by: [04 — 建立 ChatGPT 桌面交互基线与 BridGes 原创视觉资产](./04-establish-desktop-design-baseline-and-brand-assets.md), [07 — 交付用户名与 QQ 邮箱注册登录页面](./07-deliver-qq-username-auth-pages.md)
Covered requirements: UI-04, UI-05, NAV-04, NAV-05, AUTH-03, ACCOUNT-01, DESKTOP-01
ADRs: [ADR-0003](../../../docs/adr/0003-stable-account-id-and-login-identifiers.md), [ADR-0018](../../../docs/adr/0018-device-account-switching-and-reauthentication.md), [ADR-0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

在电脑端侧栏底部交付与 ChatGPT 交互方式一致、视觉为 BridGes 原创的用户信息入口和向上展开菜单。菜单按固定顺序提供“切换账号、密钥设置、个人资料、退出登录”。本 Issue 同时交付可实际修改头像和用户名的个人资料页、受保护的密钥设置入口、正确退出并跳转登录页的会话闭环；尚未由 Issue 10 接入的模型探测必须显示真实“尚未配置”状态，不能假装能力可用。

## Acceptance criteria

- [ ] 侧栏底部显示当前账户头像、用户名和可访问名称；点击或键盘激活后菜单向上展开。
- [ ] 菜单顺序严格为“切换账号、密钥设置、个人资料、退出登录”，无额外“更多”入口。
- [ ] 菜单支持方向键、Home/End、Enter/Space、Esc 和点击外部关闭；关闭后焦点回到触发按钮。
- [ ] 个人资料页可上传或选择静态头像、修改用户名，并在保存后同步刷新侧栏与当前会话展示；稳定账户 ID 和 QQ 邮箱归属不变化。
- [ ] 头像上传校验真实类型、大小和账户归属，通过授权接口读取，不暴露宿主文件路径，也不据头像推断身份、性格或情绪。
- [ ] 密钥设置入口打开受保护页面；在 Issue 10 完成前仅显示准确的“尚未配置”状态和下一步说明，不展示 Stub 成功。
- [ ] 退出登录会撤销当前会话 Cookie、清理当前活动账户状态并跳转登录页；浏览器后退不能重新进入受保护内容。
- [ ] 菜单、个人资料页和密钥设置页完整覆盖 loading、empty/unconfigured、error、permission/reauth required 和成功状态，中文文案可操作且不使用占位内容。
- [ ] 非当前账户不能通过猜测 ID 修改头像、用户名或读取设置；权限拒绝不会短暂渲染受保护子页面。
- [ ] 电脑端仅用键盘可完成打开菜单、进入设置、上传头像、修改用户名、保存和退出；视觉回归符合 Issue 04。

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
