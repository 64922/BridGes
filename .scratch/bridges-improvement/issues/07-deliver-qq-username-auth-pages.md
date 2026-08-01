# 07 — 交付用户名与 QQ 邮箱注册登录页面

Status: ready-for-agent
Blocked by: [04 — 建立 ChatGPT 桌面交互基线与 BridGes 原创视觉资产](./04-establish-desktop-design-baseline-and-brand-assets.md), [05 — 建立干净的 `bridges.db` 与账户隔离加密对象库](./05-build-clean-sqlite-and-object-storage.md), [06 — 交付源码与容器统一运行合同](./06-deliver-unified-source-and-container-runtime.md)
Covered requirements: UI-04, UI-05, AUTH-01, AUTH-02, AUTH-03, DESKTOP-01
ADRs: [ADR-0003](../../../docs/adr/0003-stable-account-id-and-login-identifiers.md), [ADR-0017](../../../docs/adr/0017-verify-qq-mailbox-through-personal-smtp.md), [ADR-0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

交付可直接使用的电脑端注册与登录纵向切片。注册页要求用户名、纯数字 `@qq.com` 邮箱和密码；登录页接受“当前用户名或 QQ 邮箱”之一加密码。账户用稳定内部 ID 持久化，用户名按规范化值大小写不敏感唯一且以后可修改。认证完成后只通过安全 HttpOnly Cookie 建立会话，登录成功进入新聊天，未登录用户访问受保护页面回到登录页且不会形成重定向循环。

## Acceptance criteria

- [ ] 注册页是完整 BridGes 桌面页面，显示原创 Logo、用户名、QQ 邮箱、密码、密码显隐、提交按钮和前往登录入口。
- [ ] 仅接受本地部分全为数字且域名严格为 `qq.com` 的邮箱；用户名规范化后大小写不敏感唯一；QQ 邮箱全局唯一。
- [ ] 登录页只提供一个“用户名或 QQ 邮箱”标识字段和密码字段，可分别用当前用户名或 QQ 邮箱登录同一账户。
- [ ] QQ 邮箱注册时不发送验证码；页面准确说明只有在后续个人 SMTP 自发自收验证成功后才能启用邮件提醒。
- [ ] 认证失败使用统一中文错误，不泄露用户名或 QQ 邮箱是否存在；重复提交、网络失败和服务异常可恢复。
- [ ] 会话令牌只通过安全 HttpOnly Cookie 传递，不出现在 JSON、URL、本地存储或日志；Cookie 的 SameSite、Secure、过期和撤销合同有测试。
- [ ] 登录与注册页各自完整覆盖 loading、empty/初始、validation error、server error、authenticated/unauthenticated permission 和成功状态，不出现英文占位或“将在这里呈现”。
- [ ] 已登录用户访问登录/注册页会按明确规则进入新聊天；未登录用户访问受保护路由只跳转一次到登录页。
- [ ] 电脑端仅用键盘可以完成字段输入、密码显隐、错误定位、登录/注册互链和提交；标签、错误关联和焦点移动正确。
- [ ] 页面使用 Issue 04 的 Claude-inspired BridGes 设计令牌，并在约定电脑端视口通过视觉回归。

## Verification

```powershell
conda run -n agent python -m pytest -k "auth or account or cookie or qq"
conda run -n agent python -m ruff check .
conda run -n agent python -m mypy src
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
npm --prefix apps/web run test:e2e -- --project=chromium
```

## Non-goals

- 不在注册时发送 QQ 邮箱验证码。
- 不允许 SMTP 授权码代替登录密码。
- 不设计或验收移动端登录/注册页面。
- 不在本 Issue 实现多账户切换或百炼 Key 探测。

## Blocked by

- [04 — 建立 ChatGPT 桌面交互基线与 BridGes 原创视觉资产](./04-establish-desktop-design-baseline-and-brand-assets.md)
- [05 — 建立干净的 `bridges.db` 与账户隔离加密对象库](./05-build-clean-sqlite-and-object-storage.md)
- [06 — 交付源码与容器统一运行合同](./06-deliver-unified-source-and-container-runtime.md)

## Comments

用户名和 QQ 邮箱是可登录标识，不能作为数据所有权键。所有后续数据关系必须绑定稳定内部账户 ID。
