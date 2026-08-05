# 33 — 交付 QQ SMTP 任务提醒
Status: ready-for-human
Blocked by: 06, 07, 27
Covered requirements: TASK-01, ACCOUNT-01, DEPLOY-03, IMP-03, A-01, SCORE-03, UI-05, DESKTOP-01
ADRs: [0004](../../../docs/adr/0004-per-account-qq-smtp-reminders.md), [0013](../../../docs/adr/0013-single-machine-storage-and-background-runtime.md), [0015](../../../docs/adr/0015-minimum-cloud-disclosure.md), [0019](../../../docs/adr/0019-limited-reminder-catch-up.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## 交付记录（2026-08-06）

- 后端：contracts/reminder.py 契约；reminder/ 模块（确定性中文解析器、
  规则推进、画像措辞适配、SMTP/IMAP 自发自收适配器、编排服务）；
  storage v21（reminders/reminder_deliveries/reminder_settings 三表）；
  scheduler 接入真实分发；api/reminder.py 路由；凭据存储 smtp 命名空间；
  reminder 画像模式；审计动作 11 个。
- 前端：/tasks 任务安排页（SMTP 配置卡/时区/新建编辑预览确认对话框/
  提醒列表/投递记录），ui-ux-pro-max 设计建议落地（状态芯片图标+文字、
  内联校验、加载/空态、键盘可达）。
- 验证：pytest 新增 80 条（解析 25/适配 5/适配器 7/服务 31/API 12），
  全量 1792 通过；issue33 E2E 3 条（含切换账户不残留）；假 SMTP/IMAP
  服务器（pytest 与 E2E 共用协议）；真实 QQ 冒烟脚本
  scripts/smoke_qq_smtp_reminder.py。
- code-review 双轴审查修复：退避重试不豁免 24h 补发窗口（AC7 边界）、
  发送期间暂停的重复投递竞态、授权失效暂停账户全部启用提醒、验证/投递
  错误固定中文文案不泄漏原始异常、画像切片不可用映射 4xx、前端会话过期
  loading 不收敛、注释失实与死代码清理。

## What to build

交付完整的“任务安排”电脑端页面和本地提醒链路。用户在自己的账户中配置并验证 QQ SMTP 授权码，系统只允许从该账户注册 QQ 邮箱发往同一邮箱；用户可用自然语言描述时间、重复规则和主题，系统先解析为带时区的结构化日程与简练邮件预览，经用户确认后才启用。本地调度器按授权的最小画像切片适配措辞，保存发送、失败、跳过、补发和手动重试记录，并严格执行 24 小时有限补发。

## Acceptance criteria

- [x] 登录用户可配置 QQ SMTP 授权码并完成真实自发自收验证；系统不接收 QQ 登录密码，授权码按账户加密保存且不进入日志、模型或导出。
  - 证据：凭据存储 smtp 命名空间（keyring/DPAPI/加密卷，credentials/store.py）；自发自收 = SMTP 发送 + IMAP 收件确认（reminder/smtp.py）；审计 details 白名单与 API 响应/投递记录/错误文案均不含授权码（tests/reminder/test_reminder_service.py::test_audit_never_contains_auth_code_or_body、test_smtp_adapter.py、test_reminder_api.py 无泄漏断言）；错误兜底固定中文文案不泄漏原始异常（审查修复回归 test_unknown_verify_error_uses_fixed_message）。
- [x] 未验证 SMTP 的账户不能启用邮件提醒；验证失败、授权失效和网络失败都显示明确原因及重新验证路径。
  - 证据：create/resume 双重 smtp_not_verified 门（409，API 测试 test_create_blocked_without_verified_smtp）；前端未验证禁用「新建提醒」+ 说明；验证状态机 failed + error_code/message + 重新验证按钮与 POST /reminders/smtp/verify（API 测试 test_verify_failure_reports_reason；E2E「验证失败给出明确原因与重新验证路径」）。
- [x] 用户输入“明天早上八点提醒我复习 transformer”等自然语言后，页面展示时区、首次执行时间、重复规则、主题和邮件预览，必须确认后才持久化。
  - 证据：确定性中文解析器（reminder/parser.py，25 条矩阵测试）；parse 预览 → create 确定性复核（preview_mismatch 防篡改，test_create_rejects_tampered_confirmation）；前端预览确认对话框（时区/首次执行/重复规则/主题/正文，E2E 断言预览字段）。
- [x] 收件人和发件人固定为当前账户的同一 QQ 邮箱，任何修改收件人为第三方或跨账户使用授权码的请求都被拒绝。
  - 证据：qq_email 来自 identity 且请求无收件人字段；网关发送 from==to==账户邮箱（test_once_reminder_sent_on_time_completes 断言自发）；跨账户 404（test_cross_account_isolated）；两账户发件凭据不串号（test_account_isolation）。
- [x] 提醒内容保持简练，只调用用户授权的最小画像类别；用户可在确认前关闭画像适配并查看本次使用类别。
  - 证据：reminder 画像模式三类别白名单（profiles/service.py _CHAT_MODE_DIMENSIONS）；适配确定性纯函数 + 类别披露（tests/reminder/test_adaptation.py）；预览/卡片披露类别 chips；use_profile 开关重新解析（前端）；test_create_frozen_profile_snapshot。
- [x] 用户可查看、编辑、暂停、恢复、取消和手动补发自己的提醒，所有操作与后台任务在重启后保持一致。
  - 证据：API CRUD + send-now（test_full_flow...）；编辑重置日程保留投递记录（test_update_resets_schedule_keeps_deliveries）；重启一致重建服务读库（test_restart_consistency）；前端卡片操作按钮组。
- [x] 恢复运行后，24 小时内错过的一次性提醒立即补发并标记延迟；超过窗口记为已错过，重复提醒最多补发最近一次。
  - 证据：可控时钟测试（test_once_reminder_caught_up_within_24h / test_once_reminder_missed_after_window / test_daily_missed_twice_catches_up_most_recent / test_weekly_missed_beyond_window_skips_and_continues）；审查回归 test_retry_due_does_not_bypass_catch_up_window（退避重试不豁免窗口）；delayed 标记与 catch_up 投递记录。
- [x] SMTP 临时失败只做有限退避重试；授权失效立即暂停相关提醒，投递记录准确区分发送、失败、跳过、补发和手动重试。
  - 证据：3 次退避重试（test_transient_failure_backoff_then_success / test_transient_failure_exhausted_pauses_once）；授权失效暂停账户全部启用提醒（审查回归 test_auth_failure_pauses_all_account_reminders）；投递记录 kind×outcome 五语义（test 断言 scheduled/catch_up/manual_retry × sent/failed/skipped）。
- [x] 切换账户后，提醒列表、授权状态、任务队列、投递记录和通知不残留前一账户数据。
  - 证据：全部读写 scoped() 账户作用域（SQL 层强制）；AppShell key={accountRevision} 重挂；E2E「切换账户不残留前一账户数据」（列表空/未配置/新建禁用/前账户提醒不可见）。

## Verification

- [x] 使用可控时钟和 SMTP 测试服务器覆盖一次性、重复、时区、暂停、取消、有限重试和 24 小时补发边界。
  - 证据：tests/reminder/ 共 80 条（可控时钟 _Clock + 可编程网关 + 进程内假 SMTP/IMAP 服务器 fake_mail.py，真实 smtplib/imaplib 经假服务器收发）；test_parser.py 时区换算矩阵（含 America/New_York）。
- [ ] 使用测试 QQ 邮箱完成一次真实授权验证和自发自收冒烟测试，确认没有收件人扩散。
  - 证据：scripts/smoke_qq_smtp_reminder.py（BRIDGES_SMOKE_QQ_EMAIL/AUTH_CODE 显式提供，自发自收验证 + 真实投递 + 收件确认，收件人恒等）；待用户以真实 QQ 邮箱执行。
- [x] 建立浏览器端到端测试，演示“配置—验证—自然语言解析—确认—投递—查看记录—编辑/取消”。
  - 证据：apps/web/e2e/issue33-smtp-reminders.spec.ts 3 条（全流程 + 验证失败路径 + 切换账户），真实后端 + 本地假 SMTP/IMAP（scripts/e2e_mail_server.py，playwright webServer 第三入口）。
- [x] 检查数据库、日志、错误响应、模型请求和导出内容，确认授权码不以明文或可逆调试字段泄露。
  - 证据：秘密扫描通过（tests/security/test_secret_scan.py）；审计 details 白名单（_AUDIT_DETAIL_KEYS 无授权码）；授权码只存在于凭据存储（smtp 命名空间），不进 SQLite（schema 无凭据列）；模型请求不涉及 SMTP 链路；错误文案固定不泄漏（审查回归）；投影无 authorization_code 字段（API 测试断言）。
- [x] 以两个账户并发调度同一分钟提醒，验证发件凭据、画像措辞、队列和投递记录完全隔离。
  - 证据：tests/reminder/test_reminder_service.py::test_account_isolation（两账户同分钟调度，提醒/投递/发件邮箱隔离）+ API 层 test_cross_account_isolated（跨账户 404）。

## Non-goals

- 不注册操作系统常驻服务，也不承诺 BridGes 完全关闭期间准时投递。
- 不支持向第三方、群组或非当前账户邮箱发送邮件，不接入开发者统一邮件账户。
- 不提供短信、即时通讯、日历同步或营销群发。
- 不开发手机通知、PWA 推送或原生提醒应用。

## Blocked by

- [06 — 交付统一源码与容器运行时](./06-deliver-unified-source-and-container-runtime.md)
- [07 — 交付 QQ 邮箱与用户名认证页面](./07-deliver-qq-username-auth-pages.md)
- [27 — 交付画像切片披露与反馈闭环](./27-deliver-profile-slices-disclosure-and-feedback-loop.md)

## Comments

- 2026-08-01：按已批准的 BridGes 改进计划发布。
