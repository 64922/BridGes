# 09 — 交付同设备账户切换与敏感操作再认证

Status: ready-for-human
Blocked by: [08 — 交付账户上拉菜单与个人资料设置](./08-deliver-account-menu-and-personal-settings.md)
Covered requirements: NAV-05, AUTH-02, AUTH-03, ACCOUNT-01, DESKTOP-01
ADRs: [ADR-0003](../../../docs/adr/0003-stable-account-id-and-login-identifiers.md), [ADR-0018](../../../docs/adr/0018-device-account-switching-and-reauthentication.md), [ADR-0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

在账户菜单内交付同一设备多账户的完整切换旅程：首次添加账户必须正常登录；多个账户的会话仍有效时可一键切换当前活动账户；会话失效后必须重新输入该账户密码。修改密钥、SMTP 授权码、导出或删除数据等敏感操作统一要求近期密码确认，并支持“退出当前账户”和“退出此设备上的全部账户”。

## Acceptance criteria

- [x] “切换账号”页面或对话框显示已添加账户的头像、用户名和脱敏 QQ 邮箱，并清楚标识当前账户。
- [x] 首次添加账户必须使用用户名或 QQ 邮箱加密码完成登录，不能仅凭本地账户 ID 添加。
- [x] 有效会话之间一键切换后，页面、导航、请求、后台订阅和缓存立即绑定新账户，不闪现上一账户数据。
- [x] 目标账户会话过期、撤销或退出后，切换必须要求该账户密码；错误不会泄露账户是否仍有效。
- [x] 敏感操作进入统一近期密码确认流程；确认有明确有效期、失败限制和审计，且只对当前账户生效。
- [x] “退出当前账户”只撤销当前会话并选择安全落点；“退出此设备上的全部账户”撤销全部本地会话并跳转登录页。
- [x] 切换页面、再认证对话框和退出确认完整覆盖 loading、empty/no-other-account、error、permission/session-expired 和成功状态，全部使用中文文案。
- [x] 两账户攻击测试覆盖并发切换、慢响应返回、猜测 ID、后台请求和浏览器后退，确保对话、文件、画像、密钥及设置不串号。
- [x] 电脑端仅用键盘可完成添加、选择、再认证、取消和退出；焦点圈定、Esc 与焦点归还正确。

## Verification

```powershell
conda run -n agent python -m pytest -k "account_switch or reauth or session or isolation"
conda run -n agent python -m ruff check .
conda run -n agent python -m mypy src
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
npm --prefix apps/web run test:e2e -- --project=chromium
```

## Non-goals

- 不允许不同账户共享百炼 Key、SMTP 授权码或用户数据。
- 不提供无需首次登录的账户发现或自动导入。
- 不实现移动端账户切换界面。

## Blocked by

- [08 — 交付账户上拉菜单与个人资料设置](./08-deliver-account-menu-and-personal-settings.md)

## Comments

账户切换必须改变整个请求作用域，而不是只替换头像或前端标签。慢请求在切换后返回时也不得写入新账户界面。
