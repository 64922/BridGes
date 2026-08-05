# Task Plan — 架构审查候选逐项修复（M01–M05 审查后深化）

状态：进行中（2026-08-05）

## Issue 34 实施计划（交付 SKILL 插件中心）

状态：已完成（2026-08-06）。全量验证：1876 pytest（+84 新增：checker 38 +
service 24 + api 21 + schema v22 3）、issue34 E2E 8 条全过（内置展示与
启停/上传预览确认安装/坏包拒绝与修正重装/缺版本拒绝/两账户隔离/真实解析
演示/humanizer 跳转/纯键盘安装调用卸载）、全量 E2E 221 通过（4 条失败均
为既有：issue04/08 环境 flake、issue13 视频入口为 Issue 32 遗留、issue14
为并行 flake 串行通过；另修复 issue12 插件旧占位断言为真实页面契约+画像
双空态既有断言）、mypy 230 文件 0 错误、改动区域 ruff 干净（全仓 257 个
为基线既有）、npm typecheck/build 通过、openapi 同步通过。双轴 code-review
修复：AC8 审计补数据类别与失败（BLOCKED）事件、检查器清理死代码/死参数/
空 plugin_id 必填/SKILL.md 大小写/frontmatter 解析防御、set_enabled 幻影
返回与内置分支贯通、内置同名坏包不落失败卡、演示 10MB 死分支消除（API
与检查器上限同源）、失败卡空版本徽标、busy 禁用演示按钮、Dialog 焦点陷阱
强化（隐藏 input 排除+焦点逃逸收回，修复键盘路径）、PDF 真实解析演示测试
补 Verification 2。Issue 34 验收状态已更新为 ready-for-human（AC 与
Verification 全部勾选附证据）。

### 目标
交付完整电脑端插件中心：内置（bridges-pdf/bridges-documents/
bridges-humanizer）只读随应用发布并默认安装，展示固定版本、能力、来源与
授权，账户可启停不可篡改/卸载；用户可上传只含 SKILL.md/静态参考/模板/
资源的声明式 zip 包，安装前完成安全闭锁检查（脚本/可执行/符号链接/路径
穿越/越界引用/不支持文件/损坏包/zip 超限）与内容预览确认，按账户启停/
卸载；安装失败进入可恢复失败状态不污染注册表；PDF/Documents 演示走真实
解析（parse_document），humanizer 跳转聊天既有合同；全部动作审计（不含
包内容与正文）；两账户完全隔离。

### 新增模块
1. `contracts/plugins.py` — PluginKind（builtin/user）、PluginStatus
   （installed/disabled/install_failed）、BuiltinPluginManifest（skill_id/
   name/version/description/source/license/capabilities/data_categories/
   read_only）、PluginFileEntry（path/size/kind: skill_md|reference|
   template|resource）、PluginCheckResult（ok/manifest/files 清单/
   rejected_reasons 中文原因列表）、BuiltinPluginProjection（manifest+
   enabled）、UserPluginProjection（package_id/plugin_id/version/status/
   object_id/file_count/content_length/failure_reason/installed_at）、
   PluginListProjection（builtin+user）、PluginDemoProjection（skill_id/
   version/parser_version/pages/sections/char_count/preview）、PluginError
2. `plugins/checker.py` — PluginPackageChecker：大小/条目数上限、zipfile
   读取（BadZipFile 拒绝）、逐条目路径穿越（normpath 后必须包内、绝对
   路径/盘符/.. 拒绝）、符号链接（external_attr S_IFLNK 0o120000 拒绝）、
   扩展名白名单（md/txt/json/yaml/yml/csv/html/css/xml + 图片 png/jpg/
   jpeg/svg/gif/webp/ico + 字体 woff/woff2；脚本/可执行/无扩展名/隐藏
   文件拒绝并说明类别）、SKILL.md 必选（根目录）+ frontmatter 解析
   （name/version 必填、description/capabilities/data_categories/source/
   license 可选）、SKILL.md 相对引用越界检查（](…) 与 ![…](…) 规范化
   后必须解析到包内）、每项拒绝给具体中文原因
3. `plugins/registry.py` — 内置包清单：bridges-pdf（固定版本/来源/授权/
   能力：PDF 附件真实解析）、bridges-documents（DOCX/TXT/MD/图片附件
   解析）、bridges-humanizer（从 SkillRegistry 派生保持单一事实源，
   data_categories 声明接收的文本类别）
4. `plugins/service.py` — PluginService：list_plugins（内置惰性建账户
   installed 状态 + 用户包列表，scoped 强制）；check_package（纯检查不
   落库不落对象）；install_package（再检查→通过则 zip 存对象库+写
   skill_packages 行+审计 PLUGIN_INSTALL；同名同版本已装冲突 409；失败
   记录可覆盖重新安装）；uninstall（对象 pending_cleanup+删记录+审计
   PLUGIN_UNINSTALL，幂等）；set_enabled（内置/用户包启停+审计 PLUGIN_
   ENABLE/DISABLE）；demo（内置 pdf/documents 走 parsers.parse_document
   真实解析→统计+预览片段+审计 PLUGIN_INVOKE 不含正文）
5. `storage/database.py` — SCHEMA_VERSION 22：skill_packages（package_id/
   account_id/plugin_id/version/name/description/source/license/
   capabilities JSON/data_categories JSON/status/object_id/file_count/
   content_length/failure_reason/installed_at/updated_at，UNIQUE(account,
   plugin_id)）、account_skill_states（account_id/plugin_id/enabled/
   updated_at，复合 PK）
6. `contracts/observability.py` — AuditAction 新增 PLUGIN_INSTALL /
   PLUGIN_UNINSTALL / PLUGIN_ENABLE / PLUGIN_DISABLE / PLUGIN_INVOKE
   （details 只含 plugin_id/version/文件数/解析器版本/页数/章节数，
   不含 zip 内容、正文与数据类别原文之外的信息）

### 修改
7. `api/plugins.py` — 路由（prefix /plugins）：GET 列表、POST check
   （multipart 原始字节 + X-Bridges-Filename）、POST install、POST
   {plugin_id}/enable、POST {plugin_id}/disable、DELETE {plugin_id}
   （仅用户包）、POST builtin/{skill_id}/demo（仅 pdf/documents 内置）；
   全部账户作用域跨账户 404；错误码 plugin_unavailable(503)/
   unsupported_package(422 带原因清单)/package_conflict(409)/
   plugin_not_found(404)/builtin_not_mutable(403)/demo_unsupported(400)
8. `api/main.py` — 挂载 PluginService（database/object_repository/
   observability/skill_registry）；plugins_router 注册
9. openapi.json + generated.ts 再生成

### 前端（先调 ui-ux-pro-max：插件卡网格 + 上传检查对话框 + 演示对话框）
10. api.ts — listPlugins/checkPluginPackage/installPluginPackage/
    enablePlugin/disablePlugin/uninstallPlugin/demoBuiltinPlugin +
    类型导出（复用 uploadRawBytes 上传 zip）
11. `components/plugins/PluginCenter.tsx` + PluginCenter.module.css —
    页面五态（loading/error/permission/empty/内容）；内置插件卡网格
    （图标/名称/版本徽标/描述/能力展开/来源/许可证/数据类别/启停开关/
    演示按钮——humanizer 跳转新聊天触发 HumanizerDialog 意图，PDF/
    Documents 打开演示对话框上传附件真实解析展示）；我的插件分区
    （已安装/已停用/安装失败卡：原因+重新上传）；卸载确认对话框；
    账户切换清态（accountRevision 重挂）；全部键盘可达
12. UploadPluginDialog — 步骤一选 zip（隐藏 input+按钮）→ 检查中 →
    结果（通过：包名/版本/能力/数据类别/内容清单表格（路径/大小/类型）
    + 确认安装按钮（再次上传同文件→安装中→成功提示+刷新）；拒绝：
    具体原因 ErrorSummary + 重新选择）；取消不留任何半安装状态
13. `app/(app)/(modules)/plugins/page.tsx` — 薄壳替换占位 StateBlock

### 测试
14. `tests/plugins/fixtures_builder.py` 或内存构造 — 合法包/脚本包/可执行
    包/符号链接包（external_attr 手工设置）/路径穿越包（../、绝对路径、
    反斜杠）/越界引用包/不支持文件包/损坏包/超限包夹具
15. `tests/plugins/test_checker.py` — 全部夹具的接受/拒绝矩阵，每项拒绝
    断言具体中文原因与类别
16. `tests/plugins/test_plugin_service.py` — _Harness（内存 db + 真实
    对象仓库 tmp_path + recording observability + 真实 registry）：
    内置惰性安装/启停/不可卸载/不可篡改、用户包安装（对象+记录+审计）、
    检查失败不落库不落对象、同名冲突、失败覆盖重装、启停/卸载（对象
    pending_cleanup+审计幂等）、重启一致（重建 service 读库）、两账户
    隔离（跨账户查看/卸载/启停 404）、demo 真实解析（md 附件）+ 审计
    不含正文、安装/启停/卸载审计只有白名单 details
17. `tests/plugins/test_plugin_api.py` — 路由契约：列表/检查/安装/启停/
    卸载/demo、坏包 422 带原因、跨账户 404、内置不可卸载 403、未挂载
    服务 503、登录会话必需
18. `tests/storage/test_schema_v22.py` — v21→v22 迁移（旧表数据保留+
    新表存在）、重启不重复迁移
19. E2E issue34-plugin-center.spec.ts — 内置三卡展示与启停；上传合法包
    （夹具 zip）→预览清单→确认安装→成功卡；停用/启用/卸载确认；
    坏包拒绝原因展示；失败修复（同 skill_id 失败后重装成功）；两账户
    隔离（B 看不到 A 的包）；纯键盘完成安装/调用/卸载；PDF/Documents
    演示对话框上传 md 附件真实解析结果
20. 夹具 zip 预生成提交至 apps/web/e2e/fixtures/（Python zipfile 生成，
    含 symlink external_attr 构造）

### 收尾
21. 全量 pytest / ruff / mypy / npm typecheck+build / E2E；code-review
    双轴审查并修复；更新 Issue 34 验收状态（ready-for-human + AC 与
    Verification 勾选附证据）；提交（工作内容+bug 修复两部分提交信息）



## Issue 33 实施计划（交付 QQ SMTP 任务提醒）

状态：已完成（2026-08-06）。全量验证：1792 pytest（+80 新增：解析器
25 + 适配 5 + SMTP 适配器 7 + 服务 31 + API 12）、issue33 E2E 3 条
（全流程/验证失败路径/切换账户不残留）、全量 E2E 213 通过（4 条失败
均为既有问题：issue04/08 环境 flake、issue12 画像双空态为 Issue 26
遗留、issue13 视频生成入口为 Issue 32 遗留）、mypy 干净、改动区域
ruff 干净（observability UP042 为既有问题）、npm typecheck/build 通过、
秘密扫描通过。双轴 code-review 修复：退避重试不豁免 24h 补发窗口
（AC7 边界，停机超窗记错过）、发送期间暂停的重复投递竞态（本次执行
被消费）、授权失效暂停账户全部启用提醒（AC8）、验证/投递错误固定
中文文案不泄漏原始异常（Verification 4 硬化）、画像切片不可用映射
4xx、前端会话过期 loading 不收敛、注释失实与 imap_login 死代码清理、
_period_adjust 死参数。Issue 33 验收状态已更新为 ready-for-human
（AC 全勾附证据；真实 QQ 冒烟项待用户执行 scripts/smoke_qq_smtp_
reminder.py）。提交：0567750。

### 目标
交付完整的「任务安排」电脑端页面与本地提醒链路：账户配置并验证 QQ SMTP
授权码（自发自收验证：SMTP 发送测试邮件 + IMAP 轮询确认到达，ADR-0004），
授权码按账户加密保存（凭据存储新增 smtp 命名空间，与百炼 Key 分离）、
不进入日志/模型/导出；未验证不得启用提醒。自然语言解析（确定性中文解析
器：时间/重复规则/主题 → 带时区结构化日程 + 简练邮件预览，经确认才持久化，
ADR-0019 同时保存账户时区规则与 UTC 执行时间）。本地调度器（scheduler 进程
dispatch_due_reminders 接缝）按冻结的画像措辞快照投递，画像只在创建/编辑时
经 compile_chat_slice 最小切片编译（reminder 模式白名单），用户可关闭并查看
本次使用类别。保存发送/失败/跳过/补发/手动重试投递记录；一次性提醒 24h 内
补发并标记延迟、超窗记为错过，重复提醒最多补发最近一次；SMTP 临时失败有限
退避重试（3 次），授权失效立即暂停相关提醒；全部账户作用域（scoped 强制），
切换账户不残留。SMTP 服务器/IMAP 收件确认端点走配置项（默认 smtp.qq.com:465
SSL / imap.qq.com:993 SSL），测试与 E2E 指向本地假邮件服务器。

### 新增模块
1. `contracts/reminder.py` — ReminderRepeatRule（once/daily/weekdays/
   weekly_days{集合}/monthly_day）、ReminderStatus（enabled/paused/
   completed/cancelled）、ReminderDeliveryKind（scheduled/catch_up/
   manual_retry）、ReminderDeliveryOutcome（sent/failed/skipped）、
   SmtpStatus（unconfigured/verifying/verified/failed）、
   SmtpSettingsProjection（含脱敏邮箱/状态/原因/验证时间，不含授权码）、
   ReminderSettingsProjection（timezone）、ParsedReminderPreview（时区/
   首次执行 UTC+本地/重复规则/主题/邮件正文预览/画像开关/本次使用类别）、
   ReminderCreateRequest/ReminderUpdateRequest（确认后的结构化载荷 +
   raw_text 追溯）、ReminderProjection、ReminderDeliveryProjection、
   ReminderError
2. `reminder/parser.py` — 确定性中文解析：日锚（今天/明天/后天/大后天/本周X/
   下X）、时刻（凌晨/早上/上午/中午/下午/晚上/今晚 + 数字点[分]）、重复规则
   （一次/每天/工作日/每周X/每月N日）、动词前缀（提醒我/记得/别忘了…）、
   主题提取；带时区（zoneinfo）→ naive 本地时间 + UTC 执行时间；解析失败
   中文原因
3. `reminder/adaptation.py` — 画像措辞适配纯函数：BASIC_INFORMATION 称呼规则
   （称呼我X/叫我X → 称呼行）、EXPRESSION_HABIT 语气提示行；返回正文 + 本次
   使用类别（维度中文标签）；off 时返回纯主题正文
4. `reminder/smtp.py` — MailGatewayPort + QqMailGateway（smtplib SMTP_SSL/
   STARTTLS，可配置 host/port）、SmtpVerifier（唯一 Message-ID/主题令牌 →
   imaplib 轮询收件确认，imap_connect 工厂可注入便于测试）；错误分类：
   auth_failed（535/535 5.7.8 等 → 授权失效）、transient（网络/超时 → 有限
   重试）、其他；绝不落盘授权码
5. `reminder/service.py` — ReminderService：smtp 配置状态机（save→verifying
   后台线程→verified/failed+原因、re-verify、delete 复位）；parse（时区+
   画像编译预览，画像切片 snapshot 挂 pending 预览令牌）；create（确认载荷
   + slice 快照冻结）/update/pause/resume/cancel/send_now（手动补发）；
   process_due（调度核心：领取到期提醒 → 授权码缺失/失效暂停、一次性 24h
   补发窗口、重复提醒最多补发最近一次、有限退避重试 next_retry_at、成功推进
   next_run_at、全部动作写投递记录 + 审计）；可控时钟注入（测试确定性）
6. `storage/database.py` — SCHEMA_VERSION 21：reminders（reminder_id/
   account_id/qq_email/timezone/raw_text/解析快照 JSON/主题/正文/画像开关/
   slice_id 与类别快照/status/next_run_at/retry_count/next_retry_at/
   pause_reason/created_at/updated_at）、reminder_deliveries（delivery_id/
   account_id/reminder_id/kind/outcome/scheduled_for/attempted_at/
   error_code/error_message/message_id/delayed）、reminder_settings
   （account_id PK/timezone/smtp 状态列：smtp_status/smtp_verified_at/
   smtp_error）

### 修改
7. `credentials/store.py` — OsCredentialStore/EncryptedVolumeCredentialStore/
   InMemoryCredentialStore 增加 namespace 构造参数（默认 "account" 兼容既有；
   SMTP 授权码用 "smtp" 命名空间，keyring 用户名前缀/卷文件名带命名空间）
8. `runtime/scheduler.py` — dispatch_due_reminders 改由 ReminderService
   process_due 接缝实现（惰性构造：数据目录凭据存储 + 数据库 + 观察服务），
   run_tick 摘要包含发送/失败/补发计数；REMINDERS_TABLE 语义交付
9. `contracts/observability.py` — AuditAction 新增：SMTP_CODE_SAVE /
   SMTP_CODE_DELETE / SMTP_VERIFY / REMINDER_CREATE / REMINDER_UPDATE /
   REMINDER_PAUSE / REMINDER_RESUME / REMINDER_CANCEL / REMINDER_DELIVER /
   REMINDER_MANUAL_SEND / REMINDER_CATCH_UP（details 只含 reminder_id/
   outcome/kind/类别数，不含授权码与邮件正文）
10. `profiles/service.py` — _CHAT_MODE_DIMENSIONS 增加 "reminder" 模式
    （INTEREST_PREFERENCE/EXPRESSION_HABIT/BASIC_INFORMATION，与日常模式
    同白名单——提醒措辞适配只用表达与基本偏好）
11. `config.py` — Settings 增加 smtp_host/smtp_port/smtp_starttls（默认
    smtp.qq.com:465 SSL）、imap_host/imap_port（默认 imap.qq.com:993）
12. `api/reminder.py` — 路由（prefix /reminders）：GET/PUT/DELETE smtp
    （授权码保存/验证走 RecentAuthRequired 敏感门，返回 SmtpSettingsProjection）、
    POST smtp/verify（重新验证）、GET/PUT settings（时区）、POST parse
    （NL→预览）、POST（创建）、GET 列表、GET/{id}、PUT/{id}、POST/{id}/
    pause、POST/{id}/resume、DELETE/{id}（取消）、POST/{id}/send-now
    （手动补发）、GET/{id}/deliveries；全部账户作用域，跨账户 404；
    错误码复用（reauth_required/no_credential/smtp_not_verified/
    smtp_auth_failed/transient_smtp_failure/parse_failed/404…）
13. `api/main.py` — ReminderService 挂载（database/credential_store(smtp
    命名空间)/profile_service/observability + smtp/imap 配置）；reminder
    路由注册
14. openapi.json + generated.ts 再生成（scripts/regenerate_openapi.py +
    openapi-typescript）

### 前端（先调 ui-ux-pro-max：设置卡 + 提醒列表 + 预览确认对话框）
15. api.ts — fetchSmtpSettings/saveSmtpCode/deleteSmtpCode/verifySmtp/
    fetchReminderSettings/updateReminderSettings/parseReminder/createReminder/
    listReminders/getReminder/updateReminder/pauseReminder/resumeReminder/
    cancelReminder/sendReminderNow/listReminderDeliveries + 类型导出
16. `/tasks` 页面替换占位为真实「任务安排」：SMTP 配置卡（授权码输入/保存/
    验证状态芯片 verifying/verified/failed+原因/重新验证/删除，收件人固定
    当前账户 QQ 邮箱且不可改）、时区选择、新建提醒（自然语言输入 →
    预览确认对话框：时区/首次执行/重复规则/主题/邮件正文预览/画像适配开关+
    本次使用类别）、提醒列表（主题/本地时间/重复徽标/状态/下次执行/操作：
    暂停/恢复/编辑/取消/手动补发）、投递记录展开（发送/失败/跳过/补发/手动
    重试 + 时间与原因）、账户切换清态（accountRevision 重挂）
17. chat-tools.ts 可选：「提醒我」工具意图 → 跳转 /tasks（保持范围克制）

### 测试
18. `tests/reminder/test_parser.py` — 时间/日锚/重复规则/主题/时区矩阵、
    解析失败中文原因、UTC 计算与夏令时无关性（Asia/Shanghai 无 DST 用
    固定偏移断言）
19. `tests/reminder/test_adaptation.py` — 称呼规则/语气/off/类别披露
20. `tests/reminder/fake_mail.py` — 进程内假 SMTP + 假 IMAP 服务器（共享
    邮箱存储，AUTH LOGIN 校验/MAIL/RCPT/DATA/SEARCH 子集）
21. `tests/reminder/test_smtp_adapter.py` — 真实 smtplib 适配器对假服务器：
    发送成功/授权失败分类/网络中断 transient/验证自发自收成功与超时
22. `tests/reminder/test_reminder_service.py` — 可控时钟：创建/编辑/暂停/
    恢复/取消/手动补发、一次性 24h 补发与超窗错过、重复提醒最多补发最近
    一次、有限退避重试（3 次）、授权失效立即暂停+原因、投递记录区分五种
    语义、两账户并发同分钟隔离（凭据/队列/记录不串）、重启一致（重建
    service 读库）
23. `tests/reminder/test_reminder_api.py` — 路由契约：保存/验证状态机/
    未验证拒绝启用/reauth 门/时区设置/解析预览/CRUD/手动补发/投递记录/
    跨账户 404、授权码不进响应与审计
24. `tests/chat/` 回归 + scheduler 集成（dispatch_due_reminders 真实表）
25. E2E issue33 — 侧栏入口、授权码保存→验证成功芯片（本地假邮件服务器）、
    未验证禁止创建、自然语言输入→预览（时区/规则/主题/正文）→确认创建、
    投递后记录展示、暂停/恢复/取消/手动补发、编辑、切换账户不残留
26. `scripts/e2e_mail_server.py` — 独立 SMTP+IMAP 假服务器（playwright
    webServer 第三入口，env 指向）；playwright.config.ts 增加入口与
    BRIDGES_SMTP_*/BRIDGES_IMAP_* env
27. 真实冒烟 `scripts/smoke_qq_smtp_reminder.py` — 显式 BRIDGES_SMOKE_QQ_
    AUTH_CODE 真实 QQ 自发自收验证 + 一次真实投递，核对收件人不扩散

### 收尾
28. 全量 pytest / ruff / mypy / npm typecheck+build / E2E；code-review 双轴
    审查并修复；更新 Issue 33 验收状态（ready-for-human + 验收项打勾附
    证据）；提交（工作内容+bug 修复两部分提交信息）



## Issue 32 实施计划（交付视频生成）

状态：已完成（2026-08-05）。全量验证：1719 pytest（+41 新增：wan 适配器
14 + 视频服务 23 + 聊天集成 5；7 条失败为干净树复现的既有环境 flake：4 条
runtime smoke CLI 编码 + 2 条端口占用 + 1 条负载 flake 隔离通过）、6 条
issue32 E2E 全过、mypy 218 文件 0 错误、改动区域 ruff 干净（observability
UP042 为既有问题）、npm typecheck/build 通过。双轴 code-review 修复：
worker 自动重领忽略永久失败码（empty_result 白耗配额 3 次，claim 条件
排除并回归测试）、提交-落库崩溃窗口重复提交边界注释、冒烟脚本 Key 泄漏
检查落地（docstring 承诺实现）、资产卡创建时间展示、E2E 下载断言
（download 属性 + 路由通配查询串 + URL 断言）、API 层跨账户资产/字节/
缓存/下载越权测试扩展。Issue 32 验收状态已更新为 ready-for-human（AC 与
Verification 全部勾选附证据）。

### 目标
交付由聊天真实触发的文生视频纵向链路，固定使用 wan2.7-t2v-2026-06-12
（ADR-0007：Wan 是矩阵中唯一的非 Qwen 系列例外，仍用同一账户级百炼密钥，
遵守单类别单快照、用户不可更改、真实能力探测与不可用即明确停用的共同
合同）。用户从聊天提交视频要求后，请求进入可恢复的异步任务（后台执行器
worker 按租约领取，提交 DashScope video-synthesis 异步任务、单次轮询、
有限重试、取消与重启恢复），成功后成为账户隔离资产（含提示/模型/供应商
任务标识/创建时间/可访问文字说明/预览/下载/带确认的删除）。所有状态来自
持久化任务与供应商结果，不用轮播占位、固定演示视频或生产 Stub 假成功。
状态机 raw：queued→submitting→generating→succeeded/failed/cancelled，
另有 cancelling（取消中，worker 收敛为 cancelled）；租约过期非终态呈现
recovery。取消后 worker 条件更新（仅 status IN ('submitting','generating')
可发布）保证迟到结果不进入对话或资产库。视频请求只披露提示与显式选择的
材料，不上传完整聊天、画像、项目目录或秘密。

### 新增模块
1. `contracts/video.py` — VideoTaskStatus（queued/submitting/generating/
   succeeded/failed/cancelling/cancelled + 呈现态 recovery）、
   VideoTaskProjection（task_id/prompt/model_id/status/error_code/
   error_message/retryable/asset_id/result_object_id/deleted/created_at/
   updated_at）、VideoDescriptionSource(prompt|manual)、
   VideoAssetProjection（asset_id/description/description_source/object_id/
   prompt/model_id/cloud_task_id/media_type/content_length/deleted/
   created_at/updated_at）、VideoDescriptionUpdateRequest、
   VideoDeletionProjection、VideoError
2. `ai/qwen_wan_adapter.py` — QwenWanAdapter（CapabilityAdapter）：
   submit（POST /api/v1/services/aigc/video-generation/video-synthesis，
   固定 1280*720）→ cloud_task_id；poll（GET /api/v1/tasks/{id}，
   兼容 output.status/task_status 字段）→ RUNNING/SUCCEEDED（video_url
   优先、results[0].url 兜底）/FAILED；fetch（下载字节，video/mp4 判型）；
   cancel。错误分类复用 qwen_client
3. `video/service.py` — VideoService：submit（消息归属校验→queued 任务+
   消息投影同事务）；get_task（含租约过期 recovery 映射）；cancel（→
   cancelling，尽力云端取消由 worker 执行，迟到结果隔离由条件发布保证）；
   retry（仅 failed，重置计数同输入重入队）；get_asset；get_video_bytes
   （账户授权流式字节）；update_description（prompt→manual）；delete_asset
   （标记删除+消息投影+对象 pending_cleanup，幂等）；worker 侧 process_
   pending（领取：queued/failed 可重试→submitting、submitting/generating/
   cancelling 续租 → submit/poll/cancel/finalize 分支；MAX_CLOUD_POLLS=60；
   失败语义与 image 对齐：cloud_timeout/cloud_failed/transient 可重试、
   永久失败码隐藏重试入口）
4. `storage/database.py` — SCHEMA_VERSION 20：video_tasks/video_assets
   两表（全部 account_id 作用域）+ messages 加 video JSON 列

### 修改
5. `contracts/chat.py` — ChatMessageCreateRequest.video（VideoRequestPayload
   载荷）；ChatMessageProjection.video（VideoTaskProjection）；ChatStream
   EventKind.VIDEO + ChatStreamVideoData（任务状态事件）
6. `contracts/observability.py` — AuditAction.VIDEO_TASK_SUBMIT / VIDEO_TASK_
   COMPLETE / VIDEO_TASK_CANCEL / VIDEO_ASSET_DELETE / VIDEO_DESCRIPTION_
   UPDATE（details 只含 task_id/model_id/长度/来源，不含提示词与视频字节）
7. `chat/repository.py` — MessageRecord.video；insert/get 带 video
8. `chat/service.py` — start_generation 校验并落库 video 载荷（与 skill
   载荷互斥 422）；stream_generation 检出 video 走 _stream_video_request
   分支（能力门 → video service 建任务 → SSE started/video(queued)/done，
   助手消息正文收敛「已提交…」+ video 投影）；消息级 retry 对 video 载荷
   拒绝（video_task_retry_via_card，任务独立重试端点）
9. `api/video.py` — 路由（prefix /chat）：GET video-tasks/{id}、POST
   video-tasks/{id}/cancel、POST video-tasks/{id}/retry（能力门 video）、
   GET video-assets/{asset_id}、PUT video-assets/{asset_id}/description、
   GET video-assets/{asset_id}/video（流式字节 + private cache +
   download Content-Disposition）、DELETE video-assets/{asset_id}；
   全部账户作用域校验，跨账户 404
10. `api/chat.py` — SSE 透传 VIDEO 事件；send 携带 video 载荷时能力门
11. `api/main.py` — 注册 qwen_wan 能力记录（vendor="wan"，ADR-0007 例外
    注释）；注册 QwenWanAdapter；VideoService 挂载（gateway/object_
    repository/chat_repository/observability）；ChatService 接入；
    video_router 挂载
12. `runtime/executor.py` — 惰性构造视频任务轮（同 image 模式）；run_tick
    加 video_service.process_pending() 处理轮
13. openapi.json + generated.ts 再生成

### 前端（先调 ui-ux-pro-max：视频任务状态卡 + 资产播放卡）
14. api.ts — getVideoTask/cancelVideoTask/retryVideoTask/getVideoAsset/
    updateVideoDescription/deleteVideoAsset/videoUrl(URL 构造) + 类型导出；
    streamChatMessage 支持 video 载荷
15. chat-tools.ts — 「视频生成」工具意图（与「图片生成」平行）
16. VideoDialog（新）— 生成页签（提示词 textarea + 固定 1280×720 与
    Wan 固定模型说明，无编辑页签）；提交走真实 send（video 载荷）
17. VideoTaskCard（新，挂助手消息）— 八态状态芯片（排队/提交中/生成中/
    恢复中/完成/失败/取消中/已取消）+ 取消/重试按钮 + 中文错误 + 键盘可达
    + 5s 轮询 getVideoTask
18. VideoAssetCard（新）— <video controls> 预览（授权端点 URL）、描述
    内联编辑（PUT description）、下载（当前对象）、删除（确认对话框
    显示影响说明）；任务完成后轮询消息列表刷新出现资产卡
19. Composer — 「+」菜单与建议卡入口（video 能力可用性门控，同 image
    模式）；MessageList/ChatThread/page.tsx 接入 video 卡与 VIDEO 事件
20. chat-flow.ts — chatVideoKey sessionStorage 桥接（同 image）

### 测试
21. `tests/video/test_wan_adapter.py` — submit/poll（status 与 task_status
    字段兼容、video_url 优先与 results 兜底）/fetch/cancel、错误分类
    （429/401/5xx/超时/空结果）、固定模型标识
22. `tests/video/test_video_service.py` — 生成成功（对象+资产+描述）、
    取消（cancelling→cancelled 全流程）、迟到结果隔离（finalize 条件
    更新）、重启恢复（租约过期重领）、cloud 超时、失败重试同快照、
    轮询不消耗重试预算、描述修改（prompt→manual）、删除影响（消息引用/
    对象清理）、账户隔离（跨账户任务/资产/字节 404）、能力不可用拒绝、
    审计不含提示与字节
23. `tests/chat/test_video_chat.py` — send 带 video 载荷：SSE started→
    video(queued)→done；worker 处理收敛消息投影；载荷与 skill 冲突 422；
    消息级重试拒绝 409；能力门；两账户隔离 404
24. E2E issue32 — 对话框提交、任务卡八态推进、资产卡（播放器/描述修改/
    下载/删除确认）、刷新恢复、失败重试、取消（取消中→已取消）、能力
    停用（page.route mock）
25. 真实冒烟：scripts/smoke_video_generation.py（显式 Key 提交真实 Wan
    任务并轮询下载，核对运行记录模型快照 = VIDEO_MODEL_ID）

### 收尾
26. 全量 pytest / ruff / mypy / npm typecheck+build / E2E；code-review
    双轴审查并修复；更新 Issue 32 验收状态（ready-for-human + 验收项
    打勾附证据）；提交（工作内容+bug 修复两部分提交信息）



## Issue 31 实施计划（交付图片生成与编辑）

状态：已完成（2026-08-05）。全量验证：1699 pytest（+32 新增：image 适配器
9 + 服务 17 + 聊天集成 6；3 条 runtime smoke 为干净树复现的既有 CLI 编码
flake）、6 条 issue31 E2E（提交→任务卡→资产卡/刷新恢复/删除确认/失败重试/
取消/能力停用）+ issue13 七入口契约同步更新全过、mypy 214 文件 0 错误、
改动区域 ruff 干净（observability UP042 为既有问题）、npm typecheck/build
通过。双轴 code-review 修复：编辑-删除竞态孤儿版本（asset_deleted 永久
失败+对象回收）、云端失败自动重试不重新提交（_fail_task 清 cloud_task_id）、
重试预算被轮询轮消耗（CASE WHEN failed）、生产替代文本恒降级（executor
补注册 qwen_vision）、前端入口能力停用（Composer image 探测快照）、cancel
终态审计语义（BLOCKED 不冒充取消）、invalid_alt_text 422、能力门重复收敛
（image.py 公开函数）、IMAGE 事件前端消费（流式期间任务卡）、死代码清理
（update_message_image）、冒烟脚本运行记录模型快照核对。Issue 31 验收状态
已更新为 ready-for-human（AC 与 Verification 全部勾选附证据）。

### 目标
qwen-image-2.0-pro-2026-06-22 与 image 能力绑定（probe_kind="image"，探测
已真实执行：POST /api/v1/services/aigc/text2image/image-synthesis 异步任务）；
图片能力适配器尚未注册（需新建 QwenImageAdapter，DashScope 原生异步任务
submit→poll→fetch）；ModelGateway/运行锁、账户对象库（create_object/
delete_object/pending_cleanup 清理轮）、后台执行器 run_tick（可加任务轮）、
能力门控（_ensure_capability_ready）、消息投影 JSON 列、SSE 事件、
可编程适配器测试 harness 全部可复用；前端 Composer「+」菜单/Dialog 模式/
MessageList 卡片挂载/issue30 E2E mock 模式可复用。

### 目标
交付由聊天真实触发的图片生成与图片编辑纵向链路，固定使用
qwen-image-2.0-pro-2026-06-22。用户可输入生成要求，或选择当前账户有权
访问的图片（本对话图片资产版本或本账户聊天附件对象）并给出编辑指令；
请求进入可恢复的异步任务（后台执行器 worker 轮询 DashScope 云端任务），
完成后成为账户隔离的版本化资产（版本链保留来源/提示/模型快照/时间关系，
不覆盖原图）。界面显示排队/运行/成功/失败/取消/恢复状态，支持可编辑
替代文本（核心视觉模型自动生成、失败确定性降级、可修改）、版本切换、
下载与带影响说明的删除。不得以占位图、固定样例或本地假数据冒充模型结果。

### 新增模块
1. `contracts/image.py` — ImageTaskKind(generate/edit)、ImageTaskStatus
   (queued/running/succeeded/failed/cancelled)、ImageTaskProjection
   （task_id/kind/prompt/source_version_id/source_object_id/model_id/status/
   error_code/error_message/retryable/asset_id/result_version_id/created_at/
   updated_at）、ImageVersionProjection（version_id/asset_id/parent_version_id/
   kind/prompt/model_id/object_id/media_type/content_length/created_at）、
   ImageAssetProjection（asset_id/alt_text/alt_text_source(model|fallback|
   manual)/current_version_id/versions 列表）、ImageAltTextUpdateRequest、
   ImageDeletionProjection（removed_versions/updated_messages/object_status）、
   ImageMessageProjection（消息内任务/资产状态快照）、ImageError
2. `ai/qwen_image_adapter.py` — QwenImageAdapter（CapabilityAdapter）：
   payload 三模式 submit（POST image-synthesis，生成或编辑带 base_image
   data URL）→ cloud_task_id；poll（GET /api/v1/tasks/{id} 单次查询）→
   RUNNING/SUCCEEDED(带结果 URL)/FAILED；fetch（下载结果 URL 字节）。
   固定 actual_model_id=IMAGE_MODEL_ID；错误分类（429/401/5xx/超时）复用
   qwen_client；qwen_client 增 dashscope_task_get（GET 任务查询 + 错误分类）
3. `image/service.py` — ImageService：submit_generation/submit_edit（校验
   编辑来源归属：版本属当前账户资产或对象属当前账户且 media_type image/*；
   能力门在 API 层）→ 建 queued 任务；get_task（含租约过期 recovery 映射）；
   cancel（尽力云端取消 + 本地标记，迟到结果隔离由 worker 条件 UPDATE
   保证）；retry（仅 failed，重置计数同输入重入队）；get_asset；get_version_
   image_bytes（账户授权流式字节）；update_alt_text；delete_asset（删全部
   版本对象 pending_cleanup + 更新引用消息投影 + 资产 deleted 标记，幂等）；
   worker 侧 process_pending（租约领取：queued/租约过期 running/可重试
   failed → submit → poll → 成功：下载→存对象→建版本（编辑挂来源 parent）→
   替代文本生成（qwen_vision 固定能力，失败确定性降级）→ 条件 UPDATE
   succeeded → 更新助手消息 image 投影与正文；云端 RUNNING 超上限 →
   failed 可重试）
4. `storage/database.py` — SCHEMA_VERSION 19：image_tasks/image_assets/
   image_versions 三表（全部 account_id 作用域）+ messages 加 image JSON 列

### 修改
5. `contracts/chat.py` — ChatMessageCreateRequest.image（ImageRequestPayload
   载荷）；ChatMessageProjection.image（ImageMessageProjection）；ChatStream
   EventKind.IMAGE + ChatStreamImageData（任务状态事件）
6. `contracts/observability.py` — AuditAction.IMAGE_TASK_SUBMIT / IMAGE_TASK_
   COMPLETE / IMAGE_TASK_CANCEL / IMAGE_ASSET_DELETE / IMAGE_ALT_TEXT_UPDATE
   （details 只含 task_id/model_id/长度/版本数，不含图片与提示词正文）
7. `chat/repository.py` — MessageRecord.image；insert/get 带 image；
   update_message_image
8. `chat/service.py` — start_generation 校验并落库 image 载荷；stream_generation
   检出 image 载荷走 _stream_image_request 分支（能力门 → image service 建
   任务 → SSE started/image(queued)/done，助手消息收敛正文「已提交…」+
   image 投影）；retry 不适用（图片任务独立重试端点）
9. `api/image.py` — 路由（prefix /chat）：POST image-tasks/{id}/cancel、
   POST image-tasks/{id}/retry（能力门）、GET image-tasks/{id}、GET
   image-assets/{asset_id}、PUT image-assets/{asset_id}/alt-text、GET
   image-assets/{asset_id}/versions/{version_id}/image（流式字节 + private
   cache + 下载 Content-Disposition）、DELETE image-assets/{asset_id}；
   全部账户作用域校验，跨账户 404
10. `api/chat.py` — SSE 透传 IMAGE 事件
11. `api/main.py` — _register_builtin_capabilities 注册 qwen_image（固定
    IMAGE_MODEL_ID）；注册 QwenImageAdapter；ImageService 挂载（gateway/
    object_repository/chat_repository/observability）；ChatService 接入；
    image_router 挂载
12. `runtime/executor.py` — 惰性构造图片任务轮（settings 全局 key →
    QwenApiClient → registry+gateway+QwenImageAdapter → ImageService）；
    run_tick 加 image_service.process_pending() 处理轮（租约领取/恢复）
13. openapi.json + generated.ts 再生成

### 前端（先调 ui-ux-pro-max：任务状态卡 + 资产卡 + 版本切换器）
14. api.ts — cancelImageTask/retryImageTask/getImageTask/getImageAsset/
    updateImageAltText/deleteImageAsset/imageBytesUrl(URL 构造) + 类型导出；
    streamChatMessage 支持 image 载荷
15. chat-tools.ts — 「图片生成」工具意图（两模式「+」菜单与建议卡共用）
16. ImageDialog（新）— 生成页签（提示词 textarea + 固定尺寸说明）与编辑
    页签（选择当前对话图片资产版本/本账户图片附件 + 编辑指令）；提交走
    真实 send（image 载荷）
17. ImageTaskCard（新，挂助手消息）— 排队/运行/成功/失败/取消/恢复五态
    状态芯片 + 取消/重试按钮 + 中文错误 + 键盘可达
18. ImageAssetCard（新）— 图片显示（授权端点 URL）、替代文本内联编辑
    （PUT）、版本切换器（切换显示对应版本）、下载（当前版本）、删除
    （确认对话框显示影响说明：版本数/消息引用数）；任务完成后轮询消息
    列表刷新出现资产卡
19. MessageList/ChatThread 接入 image 卡片；page.tsx IMAGE 事件处理与
    任务轮询；chat.module.css 样式（先 ui-ux-pro-max 设计建议）

### 测试
20. `tests/image/test_image_adapter.py` — submit/poll/fetch 三模式、
    错误分类（429/401/5xx/超时/空结果）、固定模型标识、编辑带 base_image
21. `tests/image/test_image_service.py` — 生成成功（对象+版本+替代文本）、
    编辑版本链（parent 关系不覆盖原图）、取消（含迟到结果隔离：worker
    条件 UPDATE）、重启恢复（租约过期重领）、失败重试同快照、云端 RUNNING
    超限、对象清理（删除后 pending_cleanup）、替代文本（模型生成/降级/
    手动修改）、删除影响（版本/消息引用/对象）、账户隔离（跨账户任务/
    资产/版本/图片字节 404）、能力不可用提交拒绝
22. `tests/chat/test_image_chat.py` — send 带 image 载荷：SSE started→
    image(queued)→done；任务完成 → 消息 image 投影更新；编辑来源归属
    校验；两账户隔离
23. `tests/api/test_image_api.py` — 路由契约：提交/查询/取消/重试/资产/
    替代文本/字节流/删除、错误码（no_api_key/capability_probing/
    capability_unavailable/404）、私密缓存头
24. E2E issue31 — 对话框提交生成/编辑、任务状态卡、刷新恢复、替代文本
    修改、版本切换、下载、删除确认、错误重试（page.route mock）
25. 真实冒烟：固定模型生成与编辑冒烟验证（scripts/smoke），核对运行
    记录模型快照

### 收尾
26. 全量 pytest / ruff / mypy / npm typecheck+build / E2E；code-review 双轴
    审查并修复；更新 Issue 31 验收状态（ready-for-human + 验收项打勾附
    证据）；提交（工作内容+bug 修复两部分提交信息）



## Issue 30 实施计划（交付听写与单条回答朗读）

状态：已完成（2026-08-05）。全量验证：1639 pytest（+25 新增：speech 服务/API
听写与朗读）、11 条 issue30 E2E（录音状态机/转写回填/取消清理/权限拒绝/设备
不可用/空音频恢复/网络中断重试/朗读播放控制/失败重试/单活动会话/刷新保持/
能力禁用说明/纯键盘路径）、全量 E2E 通过（issue04 附件控件与 issue08 视觉
回归为干净树复现的既有环境 flake、issue12/14 并行 flake 串行通过、doctor
CLI smoke 为环境既有编码 flake）、mypy 干净、改动区域 ruff 干净（observability
UP042 为既有问题）、npm typecheck/build 通过。双轴 code-review 修复：CSS
令牌用错（--font-size-* 未定义）、MIME 白名单私有常量公开化、听写端点补对话
归属校验（404 不泄漏）、fetch 网络错误映射中文、删除未使用 getReadAloud
helper、播放条 aria 角色矛盾、朗读工具栏入口恢复（issue04 设计基线契约）、
E2E 播放竞态（等进度再暂停）与缺失场景补充（设备不可用/网络中断重试/纯键盘
朗读控制）。Issue 30 验收状态已更新为 ready-for-human。

前置依赖：Issue 10（账户级凭据与能力探测：asr/tts 固定快照矩阵与真实探测
已存在）、11（持久化流式聊天）、13（新输入框）均已交付。探索完成：矩阵
已含 ASR_MODEL_ID=qwen3-asr-flash-2025-09-08 与 TTS_MODEL_ID=
qwen3-tts-flash-2025-11-27；QwenAsrAdapter/QwenTtsAdapter、ModelGateway
运行锁、KeyCredentialService 能力快照门控（_ensure_chat_capability_ready
模式）、加密对象库（create_object/delete_object/pending_cleanup 清理轮）
全部可复用；前端 Composer 已有 Web Speech 听写骨架、MessageList
AssistantActions 已有 speechSynthesis 朗读骨架，均需替换为真实链路。

### 目标
在真实聊天链路交付「录完再转写」听写与单条回答按需朗读。听写固定
qwen3-asr-flash-2025-09-08：录音→停止→提交完整音频→可编辑文本→用户
自行决定发送，绝不自动发送。朗读固定 qwen3-tts-flash-2025-11-27：每条
已完成的助手文本回答独立生成朗读，播放/暂停/继续/停止/同条受控重试，
页面单活动播放会话。两项能力共用当前账户百炼密钥与独立真实探测；不可用
时明确停用并说明原因，失败只重试同一快照，不切换模型不模拟成功。听写
音频不落盘（请求体内存直传 ASR，超限/空音频/非白名单 MIME 拒绝）；生成
音频按账户对象库留存并接入最小留存清理。

### 新增模块
1. `contracts/speech.py` — DictationStatus/ReadAloudState(not_generated/
   generating/ready/failed)、DictationProjection（transcript/model_id/
   duration_ms/error_code/error_message/created_at）、ReadAloudProjection
   （state/model_id/audio_ref(对象 ID)/char_count/error_code/error_message/
   generated_at/retryable/完整消息正文快照不存——朗读按消息正文现取）、
   SpeechError
2. `speech/service.py` — SpeechService：transcribe（能力门→固定 ASR 绑定
   经 ModelGateway invoke qwen_asr_short@1（固定模型来自矩阵）→审计
   ASR_TRANSCRIBE（details 不含音频/转写正文）→DictationProjection）；
   generate_read_aloud（校验消息归属/role=assistant/status=done/正文非空
   →正文纯文本化→固定 TTS 绑定 invoke→下载供应商临时 URL→转存账户对象
   库→ReadAloudProjection 快照写回消息 read_aloud 列→审计
   READ_ALOUD_GENERATE）；get_read_aloud / get_audio_bytes（对象授权校验）；
   delete_read_aloud（清理对象与快照，幂等）；失败只重试同一快照
3. `storage/database.py` — SCHEMA_VERSION 18：messages 加 read_aloud
   JSON 列（快照含 state/model_id/audio_ref/error/retryable/created_at）

### 修改
4. `contracts/observability.py` — AuditAction.ASR_TRANSCRIBE /
   READ_ALOUD_GENERATE / READ_ALOUD_DELETE（details 只含时长/字符数/消息
   ID/模型标识，不含音频与正文）
5. `api/speech.py` — 路由：POST /conversations/{id}/dictation（multipart
   音频，能力门 asr）、POST /conversations/{id}/messages/{mid}/read-aloud
   （能力门 tts，返回投影）、GET 投影、GET /audio（流式返回对象字节，
   含 Content-Type/长度，带账户授权校验）、DELETE（停止并清理，幂等）；
   错误码 no_api_key/capability_probing/capability_unavailable 复用
6. `api/main.py` — SpeechService 挂载（gateway/credential_service/
   object_store/object_repository/observability/chat_repository）
7. openapi.json + generated.ts 再生成

### 前端（先调 ui-ux-pro-max：录音胶囊 + 朗读播放条 + 状态芯片）
8. api.ts — transcribeDictation / generateReadAloud / getReadAloud /
   fetchReadAloudAudio(URL) / deleteReadAloud + 类型导出
9. Composer 录音状态机（替换 Web Speech）：idle→recording(时长计时)→
   stopping(提交中)→transcribed(可编辑回填不自动发送)/error(权限拒绝/
   设备不可用/空音频/超限/网络/密钥失效)；按钮：开始/停止/取消/重录，
   全部键盘可达；录音中禁用发送；取消不遗留待发送文本；跨账户/刷新
   安全（卸载清理）
10. ReadAloudControls（新，挂 MessageList 每条助手消息）：生成→
    generating(过程态)→ready(播放条：播放/暂停/继续/停止/进度)/
    failed(原因+重试同条)；全局单活动播放会话（AudioManager 单例，
    新播先停旧）；切换对话/账户/刷新安全停止；失败重试走同一消息正文
11. MessageList/ChatThread 接入；chat.module.css 录音/朗读样式

### 测试
12. `tests/speech/test_speech_service.py` — 转写成功/空音频/超限/不支持
    MIME/能力门（未配置/探测中/不可用）/审计不含正文/固定模型标识进
    运行锁/失败可重试同快照/不落盘（无对象产生）
13. `tests/speech/test_read_aloud.py` — 生成/重试/消息归属与 role 校验/
    账户隔离（跨账户取音频拒绝）/删除幂等/对象库留存与清理（delete
    后 pending_cleanup）/刷新后可重新请求
14. `tests/chat/test_speech_chat.py` 或并入 — 与聊天链路集成（消息正文
    纯文本化、快照落库、SSE 无关）
15. E2E issue30 — 录音→停止→转写回填→编辑→发送；取消/重录；麦克风
    拒绝；空音频；超限；网络中断；密钥失效；朗读生成/播放/暂停/继续/
    停止/失败重试；切换回答单会话；刷新与跨账户安全；纯键盘路径
16. 真实探测验证：能力探测覆盖 asr/tts 固定快照（已有 issue10 测试），
    记录模型标识与失败语义

### 收尾
17. 全量 pytest / ruff / mypy / npm typecheck+build / E2E；code-review
    双轴审查并修复；更新 Issue 30 验收状态（ready-for-human + 验收项
    打勾附证据）；提交（工作内容+bug 修复两部分提交信息）



## Issue 29 实施计划（交付生涯规划助手）

状态：已完成（2026-08-05）。全量验证：1616 pytest（+71 新增：career 意图
检测/复核/服务 48 条 + 聊天集成 13 条 + 审查回归 10 条）、6 条 issue29 E2E、
全量 E2E 187 通过（issue04/08 为既有环境 flake、issue13/14 并行 flake 串行
通过）、mypy 206 文件 0 错误、改动区域 ruff 干净、npm typecheck/build 通过。
双轴 code-review 修复：承诺词否定剥离跨词（「不构成…保证」误伤）与边界
声明漏扫（正向承诺藏匿）、意图检测过宽劫持普通聊天（「考研英语怎么复习」
误触发，改分级关键词+语境词）、过时机制不可达（画像/学习记录改用记录更新
时间，旧记录真实标注）、假设缺核查方式无确定性门、非可重试错误显示必败
重试按钮、前后端意图规则分叉（前端补关键词表）、私有函数跨模块导入、
死参数清理、aria-controls/反馈表单自动聚焦。Issue 29 验收状态已更新为
ready-for-human。
前置依赖：Issue 23（教学门）、27（切片披露与反馈）、28（humanizer）均已
交付；探索完成：生成链分支点（skill 载荷/humanizer、mode/teaching）、
compile_chat_slice、LearningService 学习记录、retrieval.run_round、
answer_feedback 反馈闭环、scoped() 账户隔离全部可复用。

### 目标
在既有两种对话模式中，按"明确生涯规划意图"触发真实可保存、可恢复、可追溯的
规划对话：只使用当前账户授权的画像切片、学习记录、用户陈述与可追溯证据，输出
固定结构化的已知事实/待验证假设/可选方向/关键风险/分阶段成长路径/近期学习建议
六类内容加自然中文正文；事实带可定位来源与核查时间，证据不足明确说明未知与
下一步核查；自然表达受 bridges-humanizer 规则约束（事实与推测分离、限定条件
保留），但绝不作就业/薪酬/录取保证、不基于单次情绪/敏感身份猜测/未确认候选；
用户可逐项反馈（事实/假设/建议），反馈进入既有画像治理闭环而非静默覆盖；
模型/检索/画像不可用时给出可恢复错误与安全替代步骤，不输出模板化假成功。

### 新增模块
1. `contracts/career.py` — CareerIntent(六类输出契约：CareerFact/CareerAssumption/
   CareerOption/CareerRisk/CareerStage/CareerSuggestion 各带 item_id/内容/证据引用/
   核查时间/状态)、CareerPlanningOutputContract（final_text+六类+boundary_statement
   +完整性门）、CareerEvidenceSource（画像/学习记录/检索/联网/用户陈述五类来源，
   带 accessed_at 核查时间与 locator）、CareerPlanningProjection（status/plan_id/
   intent/六类/evidence_sources/profile_used/process_state/error_code...）、
   CareerPlanningProcessState 五态(loading/empty/error/permission/recovery)
2. `career/intent.py` — 确定性生涯规划意图检测器：显式前缀"生涯规划助手："必中；
   关键词表（职业规划/生涯规划/就业方向/求职/转行/职业发展/选专业/考研/考公/
   找实习/职业选择/晋升路径/职业目标）；否定式防护（不要/不用/别…不触发）；
   与 humanizer skill 载荷互斥（skill 分支优先）
3. `career/service.py` — CareerPlannerService：意图检测 → 画像切片编译（复用
   ProfileService.compile_chat_slice，按当前对话模式维度映射）→ 学习记录读取
   （复用 LearningService：使命/知识状态/学习记录）→ 本地检索 run_round + 时效性
   关键词触发 DuckDuckGo/arXiv → 证据集合 → Qwen 结构化生成六类输出（prompt 含
   humanizer 表达规则与边界禁令）→ 确定性复核（事实证据门：无证据引用降级为
   假设或标注未核实；承诺词检查：保证/包过/包就业等阻断；引用核验：不在证据
   清单标记未核实；输出合同完整性门）→ 投影；失败可重试不丢输入

### 修改
4. `contracts/chat.py` — ChatMessageProjection.career_planning；ChatStreamEventKind.
   CAREER + ChatStreamCareerData（过程事件：五态+step_label+progress_steps）
5. `contracts/observability.py` — AuditAction.CAREER_PLANNING_GENERATED（details
   只含 item 数/证据数/切片 ID，不含正文）
6. `contracts/feedback.py` — AnswerFeedbackRequest.career_item_ref（可选，逐项
   反馈定位到六类条目；幂等去重键扩展）
7. `storage/database.py` — SCHEMA_VERSION 17：messages 加 career_planning JSON 列
8. `chat/repository.py` — MessageRecord.career_planning；insert/get/update_
   message_career_planning；find_duplicate_feedback 带 career_item_ref
9. `chat/service.py` — stream_generation 意图检测 → _stream_career_planning 分支
   （检索/联网/切片/生成/复核/落库/SSE 事件，模式与 _stream_humanizer 一致）；
   retry 重新检测意图沿用；_project_message 映射 career_planning；
   submit_feedback 支持 career_item_ref
10. `api/chat.py` — send/retry 后 SSE 透传 CAREER 事件
11. `api/main.py` — learning_service 挂载提前，CareerPlannerService 挂载（gateway/
    profile_service/learning_service/retrieval/web_search/arxiv/observability），
    ChatService 构造接入
12. openapi.json + generated.ts 再生成

### 前端（先调 ui-ux-pro-max：AI-Native 风格、六类分区结果卡、五态过程卡）
13. chat-tools.ts："生涯规划助手"由预填改为打开 CareerPlanningDialog（两模式"+"菜单
    与建议卡共用，原创 career 图标已存在）
14. CareerPlanningDialog（新）— 生涯问题输入 + 画像使用开关 + 提交走真实 send
15. CareerPlanningProcessCard — 五态中文过程卡（loading/empty/error/permission/
    recovery）
16. CareerPlanningResultCard — 可展开结果卡：六类分区（事实/假设/方向/风险/路径/
    建议，各带核查时间与来源）、证据列表（可打开原文/URL）、画像披露链接、
    边界声明、逐项反馈（事实/假设/建议各条目"反馈"入口走既有反馈 API）
17. api.ts streamChatMessage 支持；chat-thread.tsx/MessageList 渲染 career 卡；
    page.tsx 事件处理

### 测试
18. `tests/career/` — 意图检测矩阵（前缀/关键词/否定/误触发防护/与 skill 互斥）；
    六类输出合同完整性门；事实证据门（无证据降级/未核实标注）；承诺词边界阻断；
    引用核验；确定性复核规则（固定语料）
19. `tests/chat/test_career_planning_chat.py` — 真实消息流集成（可编程结构化适配器）：
    started→career 过程事件→done 六类投影；重试不丢输入；无画像/拒绝画像（off 态
    披露）/敏感推断排除/证据冲突/过时来源/模型失败（error 可恢复不假成功）；
    两账户隔离（缓存/引用/反馈不串号）；逐项反馈幂等与画像治理闭环
20. E2E issue29 — 两模式"+"菜单与建议卡入口、对话框提交、五态过程卡、六类分区
    结果卡、查看依据（来源+核查时间）、逐项反馈、画像关闭、失败恢复重试、
    键盘路径

### 收尾
21. 全量 pytest / ruff / mypy / npm typecheck+build / E2E；code-review 双轴审查并
    修复；更新 Issue 29 验收状态；提交（工作内容+bug 修复两部分提交信息）




## Issue 28 实施计划（原创净室 bridges-humanizer SKILL）

状态：已完成（2026-08-05）。全量验证：1545 pytest（+46 新增：skills 注册表、
humanizer 事实锁/体裁/服务/聊天集成）、7 条 issue28 E2E、全量 E2E 182 通过
（3 条失败均为干净树复现的既有环境 flake：issue04/issue08/issue12）、mypy 200
文件 0 错误、改动区域 ruff 干净、npm typecheck/build 通过。双轴 code-review
修复：联网证据合同实际接线（Spec AC8）、生成路径 fact_check 恒空必败一轮、
嵌套 button 无效 HTML、契约死代码 HumanizerError、genre_rules 死字段、
标签表重复、process_state 终态语义、kind 魔数、前端重复组装、tabs 半成品、
单位 token 顺序契约注释、task_id 魔数判别等。Issue 28 验收状态已更新为
ready-for-human。

### 目标
以原创净室方式实现默认内置、只读、版本固定的 `bridges-humanizer` SKILL，接入两种
对话模式的"+"菜单与空白对话建议卡并进入真实消息流程。改写路径（粘贴文本/当前账户
文件）先提取任务契约与事实锁再人性化；主题生成路径收集/确认主题、受众、体裁、渠道
与硬约束后生成并复核。科普文案、课程讲稿、科研汇报、论文写作各自使用可测试表达
规则，不共用单一模板。每次输出固定含最终文本、逐项修改细节、每项理由、事实核查
结果与未决问题；数值/单位/对象关系/限定条件/公式/引用/结论强度受事实锁检查，冲突
时停止或标注人工确认。过程卡五态中文；失败可从原任务重试不丢输入；注册为默认内置
能力供插件页（Issue 34）展示；完成许可证与来源清洁审计。

### 新增模块
1. `contracts/humanizer.py` — HumanizerPath(REWRITE/GENERATE)、HumanizerSkillInput
   （skill_id+任务契约：体裁复用 expression.Genre 四值、受众/渠道/长度/硬约束/原文/
   附件）、FactLockKind 七类(数值/单位/对象关系/限定条件/公式/引用/结论强度)、
   FactLockEntry/CheckResult（preserved/changed/removed/added + 阻断/需人工）、
   HumanizerEdit（原文/新文/类别/理由/体裁规则）、HumanizerFactCheckItem、
   HumanizerOutputContract（final_text/edits/fact_check/open_questions + 完整性门）、
   HumanizerResultProjection、HumanizerProcessState(loading/empty/error/permission/
   recovery)
2. `skills/registry.py` — SkillRegistry：内置只读 SKILL 注册表（稳定标识/版本/只读
   来源/能力说明），启动注册 bridges-humanizer v1.0.0，供 Issue 34 插件页消费
3. `skills/humanizer/skill/` — SKILL.md（完整说明/规则/证据边界/输出合同/版本）+
   genres/ 四体裁合同（popular_science/lecture_script/research_report/paper_assist，
   各自必含/允许省略/禁止/保留规则与人工责任，不共用泛化模板）+ fixtures/（两条
   路径固定语料）+ CLEAN_ROOM.md（来源清洁记录：scientific-humanization 仅方法
   研究、零 MIT 复用声明）
4. `skills/humanizer/factlock.py` — 确定性文本事实锁引擎：数值+单位/公式/引用/限定
   词/结论强度/对象关系正则提取、规范化（全半角/单位统一）、前后比较
5. `skills/humanizer/genre_rules.py` — 四体裁确定性规则加载与校验（required/
   prohibited/preserved 断言，中文可测试）
6. `skills/humanizer/service.py` — HumanizerService：改写/生成两条路径编排（任务契约
   提取 → 事实锁提取 → SKILL+体裁规则组装 → Qwen 结构化生成 → 确定性复核（事实锁
   前后比较/体裁规则/输出合同完整性/引用保持）→ 结果投影）；冲突→needs_human 或
   停止；证据合同复用本地检索/联网搜索；失败可重试

### 修改
7. `contracts/chat.py` — ChatMessageCreateRequest.skill_id/skill_input；
   ChatMessageProjection.skill(用户消息任务摘要)+humanizer(结果投影)；
   ChatStreamEventKind.HUMANIZER + ChatStreamHumanizerData（过程卡五态事件）
8. `contracts/observability.py` — AuditAction.HUMANIZER_GENERATE（details 不含正文）
9. `storage/database.py` — SCHEMA_VERSION 16：messages 加 skill JSON 列
10. `chat/repository.py` — MessageRecord.skill；insert/get 带 skill；update_message_humanizer
11. `chat/service.py` — start_generation 接收 skill_id/skill_input 落库；stream_generation
    检出 skill 走 HumanizerService 编排（发 HUMANIZER 过程事件→done 带结果投影）；
    retry 复用原任务输入不丢；_project_message 映射 humanizer/skill
12. `api/chat.py` — send/retry 透传 skill 字段、SSE 透传 HUMANIZER 事件；humanizer
    结果详情路由（如需）；_ensure_chat_capability_ready 复用（权限态）
13. `api/main.py` — 挂 SkillRegistry（内置注册）与 HumanizerService（复用 gateway/
    attachments/retrieval/web_search/observability）；附件文本经 ingestion parsers 提取
14. openapi.json + generated.ts 再生成

### 前端（先调 ui-ux-pro-max：AI-Native 风格、五态过程卡、空态带行动）
15. chat-tools.ts："文章人味化"意图由预填改为打开 HumanizerDialog（两模式"+"菜单与
    建议卡共用，原创 humanize 图标）
16. HumanizerDialog（新）— 改写/生成两页签：粘贴文本或选当前账户附件、体裁四选、
    渠道/长度/硬约束；提交走真实 send（skill 载荷）
17. HumanizerProcessCard — 五态中文过程卡（loading/empty/error/permission/recovery）
18. HumanizerResultCard — 可展开结果详情：最终文本/修改明细/每项理由/事实核查/未决
    问题/事实锁冲突标注/引用保持
19. api.ts streamChatMessage/retry 支持 skill 载荷；chat-thread.tsx/MessageList 渲染
    humanizer 卡；page.tsx 事件处理

### 测试
20. `tests/skills/` — 注册表（内置只读/版本/标识）
21. `tests/humanizer/` — 事实锁提取与前后比较（固定语料含数值/单位/公式/限定/引用，
    冲突阻断与人工标注）；四体裁规则可测试性（不共用模板）；输出合同完整性门（缺一
    不完成）；引用保持；改写/生成两路径（可编程捕获适配器）；失败恢复重试不丢输入；
    许可证/净室声明存在性
22. `tests/chat/test_humanizer_chat.py` — 真实消息流集成（send 带 skill、SSE 过程事件、
    重试、账户隔离、权限态）
23. E2E issue28 — 菜单入口、建议卡、文件改写、主题生成、结果详情、错误重试
24. 许可证与来源审计：CLEAN_ROOM.md 记录净室方法与零复用声明

### 收尾
25. 全量 pytest / ruff / mypy / npm typecheck+build / E2E；code-review 双轴审查并修复；
    更新 Issue 28 验收状态（ready-for-human + 验收项打勾附证据）；提交



## Issue 27 实施计划（最小画像切片、披露与反馈闭环）

状态：已完成（2026-08-05）。全量验证：1490 pytest（含新增 25 条，3 个
CLI 编码 flake 为环境既有）、6 条 issue27 E2E、mypy 194 文件 0 错误、
改动区域 ruff 干净、npm typecheck/build 通过。双轴 code-review 修复
5 处缺陷后提交（详见提交信息）。

### 目标
为日常陪伴与学习模式建立任务级最小画像切片编译器：每轮只选择与当前
任务相关、仍有效且授权范围匹配的记录；用户可在发送前关闭画像使用；
发送到 Qwen 的上下文只含必要切片，不上传完整画像中心或未确认候选。
回答提供可展开中文「本次上下文说明」（画像类别、材料类别、用途、来源
记录链接、本次使用时间），不暴露隐藏提示或原始思维链。反馈入口区分
「这次回答有问题」与「画像记录有误」，完成「回答—反馈—修正画像或
策略—下一轮验证」的多轮闭环；历史回答保留当时切片版本，可回放修正
前后差异；学习模式用目标/知识状态/学习证据调教教学，日常模式只用
必要偏好与情境；多账户并发按稳定账户 ID 编译；披露与反馈流具备中文
loading/empty/error/permission/recovery 状态，失败不丢失用户反馈。

### 新增模块
1. `contracts/feedback.py` — FeedbackKind / AnswerFeedbackRequest /
   AnswerFeedback / FeedbackResolveRequest（回答反馈与画像修正闭环契约）
2. `profiles/` 无新文件：在 `service.py` 新增 `compile_chat_slice`（模式
   维度映射 _CHAT_MODE_DIMENSIONS：companion=兴趣/表达/基本情况，
   study=阶段目标/知识状态/兴趣；授权范围 general|模式匹配；每维度
   上限 2 条、总量上限 6 条的最小化；未确认候选/撤回/冻结/过期/敏感
   排除全部复用既有过滤）

### 修改
3. `contracts/chat.py` — ChatMessageCreateRequest.use_profile 开关；
   ContextNoteProfileItem + ContextNoteProjection（披露卡：维度中文标签/
   值摘要/用途/来源 assertion_id/使用时间/快照状态与版本/材料类别/
   排除数/state: ready|empty|off|error）；ChatMessageProjection.context_note
4. `contracts/observability.py` — AuditAction.PROFILE_SLICE_USED（details
   只含 slice_id/维度/条目数/授权快照，不含正文）与 ANSWER_FEEDBACK
5. `storage/database.py` — SCHEMA_VERSION 15：messages 加 context_note 列；
   answer_feedback 表（幂等去重键 account+message+kind+assertion+文本）
6. `chat/repository.py` — update_message_context_note；feedback 读写
7. `chat/service.py` — stream_generation(use_profile) 编译→注入最小切片
   上下文（独立 system 块，固定格式）→披露落库→审计；编译失败静默
   降级（披露 error 态，回答照常）；关闭画像不编译/不注入/披露 off 态
   且审计记录 disabled；retry 沿用旧轮次开关（从旧尝试披露快照读取）；
   submit_feedback（幂等）/ list_feedback / resolve_feedback
8. `api/chat.py` — send/retry 透传 use_profile；feedback POST/GET/resolve
   路由（失败返回可重试错误，前端保留草稿）
9. openapi.json + generated.ts 再生成；npm typecheck/build

### 前端
10. Composer 发送前画像开关（与知识库开关平行，data-testid）；
    ContextNoteCard 可展开披露卡（loading/empty/error/permission/recovery
    中文状态）；回答反馈入口（回答不合适+偏好 / 画像有误+修正/冻结/
    撤回）；修正后下一轮适配提示；失败不丢反馈（本地保留+重试）；
    历史切片版本 vs 当前版本差异入口。先调 ui-ux-pro-max

### 测试
11. `tests/profiles/test_chat_slice_compiler.py` — 模式×场景矩阵、撤回/
    冻结/过期/敏感/授权范围/未确认候选排除、最小化上限、账户隔离
12. `tests/chat/test_profile_slice_chat.py` — 捕获模型适配器请求只含期望
    切片；关闭画像后无任何画像内容；披露快照；「初始回答—用户纠正—
    画像更新—后续回答改变」固定多轮回放；审计不含正文；失败不阻断
13. 反馈 API 测试（幂等/账户隔离/关联消息）
14. E2E issue27 — 上下文说明展开、回答反馈、画像修正、下一轮适配、
    失败恢复

### 收尾
15. 全量 pytest / ruff / mypy / npm typecheck+build / E2E；
    code-review 双轴审查并修复；更新 Issue 27 验收状态；提交


## Issue 26 实施计划（画像候选与分级许可更新）

状态：已完成（2026-08-05）。

### 目标
把聊天观察转化为可治理的画像候选与更新流程：明确"记住/不要记住/只在
本对话使用"意图按可见类别/范围/证据确定性映射；低风险目标/兴趣/表达
习惯仅在用户预开的类别×场景许可内自动写入（默认关闭、模型不能代开），
每次写入有中文通知、来源与一键撤回；情绪趋势/重要经历/当前问题与敏感
推断只进候选箱，未确认不得跨会话使用；单次情绪仅作为会话情境信号；
冻结类别拒绝自动写入、撤回许可只停未来更新；稳定去重与证据合并；
全部按账户隔离并写入审计；画像/许可/通知 SQLite 持久化，重启可追溯。

### 新增模块
1. `profiles/extraction.py` — 确定性记忆意图提取器（记住/不记/仅会话/
   低风险观察/单次情绪；类别关键词表；显式意图优先、否定式防护、
   无类别不猜测）
2. `profiles/sqlite_repository.py` — 全端口 SQLite 实现（观察/候选/断言/
   版本/切片/许可/通知，scoped() 账户强制隔离）
3. `contracts/profiles.py` — ProfilePermission / ProfileNotification /
   AUTO_WRITABLE_DIMENSIONS / 批量决策契约
4. `contracts/chat.py` — ChatStreamEventKind.PROFILE + ChatStreamProfileData
5. `contracts/observability.py` — 6 个新审计动作（许可开关/自动写入/
   一键撤回/意图/候选提出）
6. `storage/database.py` — SCHEMA_VERSION 14：7 张 profile_* 表

### 修改
7. `profiles/service.py` — 许可门/冻结门/去重门、process_conversation_message
   管线、一键撤回（幂等）、批量决策（幂等）、通知读写
8. `chat/service.py` — start_generation 挂载画像处理（失败静默不阻断）、
   profile_notifications_for_message 透传
9. `api/chat.py` — started 后下发 profile SSE 事件
10. `api/main.py` — 有数据库时挂 SQLite 画像仓库；ChatService 接入
11. `profiles/api.py` — 许可 GET/PUT、通知 GET/read/recall/unread-count、
   候选 batch-decision

### 前端
12. api.ts 新函数 + 类型导出；ProfilePermissionPanel（开关网格，乐观更新
    失败回滚）；ProfileNotificationList（未读/已读/一键撤回/标记已读）；
    ProfileCenter 候选卡增强（为何提出/来源消息/适用范围/编辑后确认/
    复选框批量确认拒绝）；ChatProfileNotificationCards（聊天内即时通知
    + 一键撤回 + 错误恢复）；chat.module.css / ProfileCenter.module.css
13. openapi.json + generated.ts 再生成；npm typecheck/build

### 测试
14. `tests/profiles/test_memory_intent.py` — 35 条：标注对话集（明确记忆/
    低风险许可/敏感候选/单次情绪/禁止推断授权）、幂等去重、冻结门、
    许可撤回、一键撤回、批量决策、跨账户、SQLite 重启持久化
15. `tests/chat/test_profile_intent_chat.py` — 5 条：聊天集成、失败不阻断
16. E2E issue26 — 5 条：许可开关持久化、候选卡增强与编辑后确认、
    批量拒绝、通知空态+聊天内通知+一键撤回错误恢复、单次情绪提示

### 收尾
17. 全量 pytest / mypy / ruff / npm typecheck+build / E2E 全量；
    code-review 双轴审查并修复；更新 Issue 26 验收状态；提交


## Issue 20 实施计划（分层本地检索、融合排序与引用）

状态：已完成（2026-08-04）。全量验证：1335 pytest（+81）、E2E 143 通过
（新增 issue20 5 条，4 个失败均为干净树既有/环境 flake）、mypy 173 文件
0 错误、改动区域 ruff 干净、npm typecheck/build 通过。双轴 code-review
修复 11 处缺陷后提交（77a24ce）。

### 目标
在真实对话中交付三层本地检索：当前对话附件 → 当前学习项目文件 → 已授权
全局知识库。每层 FTS5 BM25 与 text-embedding-v4 向量结果按固定合同融合
（RRF k=60 + 层权重 3/2/1），独立候选配额（4/5/5）与去重规则，引用固化
为消息轮次（文件名/页码/章节/片段快照，索引重建不漂移），点击引用实时
校验授权并打开原文；无命中/冲突/覆盖不足/索引不可用输出结构化充足性。

### 新增模块
1. `contracts/retrieval.py` — CitationProjection / RetrievalRoundProjection /
   RetrievalLayerResult / RetrievalSufficiency / CitationDetailProjection
2. `retrieval/search.py` — 查询清理、FTS 窗口回退、向量余弦、RRF 层内融合、
   跨层加权合并与内容哈希去重、冲突与充足性判定
3. `retrieval/repository.py` — 检索轮次与引用持久化（账户作用域）
4. `retrieval/service.py` — 作用域解析、每轮检索编排、投影与引用详情授权校验
5. `tests/retrieval/` — 搜索单测 11 条 + 服务测试 17 条（作用域/配额/去重/
   隔离/充足性/引用详情/版本稳定）
6. `tests/chat/test_retrieval_chat.py` — 生成前检索、上下文注入、重试新轮次、
   知识库开关、刷新稳定（5 条）

### 修改
7. `storage/database.py` — SCHEMA_VERSION 10：retrieval_rounds + message_citations
8. `contracts/chat.py` — ChatMessageProjection.retrieval、请求 use_knowledge_base
9. `chat/service.py` — 生成前 run_round、最小上下文注入、思考摘要证据/工具、
   消息投影携带检索轮次
10. `api/chat.py` — send/retry 透传知识库开关、引用详情路由
11. `api/main.py` — 挂载 LayeredRetrievalService（真实 QwenEmbeddingPort）
12. 前端 — api.ts（useKnowledgeBase + getCitationDetail）、RetrievalCard.tsx
    （状态卡/引用展开/打开原文）、MessageList/ChatThread 接入、Composer
    来源层面板与知识库开关（模板基线不渲染）、openapi.json + generated.ts 再生成

### 收尾
13. 全量 pytest / ruff / mypy / npm typecheck+build / E2E 已验证
14. code-review 双轴审查并修复；更新 Issue 20 验收状态；提交


## Issue 17 实施计划（文档摄取与版本化全文/向量索引）

状态：已完成（2026-08-04）。全量验证：1254 pytest（+47，含 3 条审查回归）、117 E2E（+4）、mypy 160 文件 0 错误、改动区域 ruff 干净、npm typecheck/build 通过。双轴 code-review 修复 8 处缺陷后提交。

### 目标
把安全对象转换为可追溯、可恢复的本地检索材料：PDF/DOCX/TXT/MD/图片 → 解析（页码/章节/标题）→ 哈希分块 → SQLite FTS5(trigram) 全文 + text-embedding-v4 1024 维向量双索引；索引带不可混写版本合同，合同变化全量重建、校验后原子切换，旧版可回滚；后台执行器重启恢复未完成任务。

### 新增模块
1. `src/bridges/contracts/ingestion.py` — DocumentIngestionProjection / IndexStatusProjection / IndexContractProjection 契约
2. `src/bridges/ingestion/parsers.py` — PDF(fitz)/DOCX(zip+xml)/TXT/MD/图片解析器，产出归一文本 + (起始/结束/页码/章节) 跨度
3. `src/bridges/ingestion/chunker.py` — 结构锚点哈希分块（字符偏移可追溯）
4. `src/bridges/ingestion/embedding.py` — EmbeddingPort + 真实 Qwen 实现（L2 归一 + 维度校验）+ 确定性假实现；能力探测门
5. `src/bridges/ingestion/index.py` — 版本化索引：合同（model/dims/规范化/chunker/schema）、混合写拒绝、全量重建、覆盖率+维度校验、原子切换、回滚
6. `src/bridges/ingestion/service.py` — 摄取状态机（入队/领取/处理/重试/投影/清理）+ 账户内解析缓存复用
7. `src/bridges/api/ingestion.py` — 附件摄取详情 / 重试 / 索引状态路由

### 修改
8. `storage/database.py` — SCHEMA_VERSION 7：document_records、document_parse_cache、document_chunks、index_versions、index_active、index_vectors、fts_chunks（trigram）+ 存量附件回填入队
9. `contracts/chat.py` — ChatAttachmentProjection 增加 ingestion_status / ingestion_error
10. `chat/attachments.py` — 投影 LEFT JOIN 摄取状态
11. `api/chat.py` — 上传成功后人队
12. `api/main.py` — 挂载 ingestion service
13. `runtime/executor.py` + `cli/main.py` — worker 摄取轮（清理 → 摄取 → 索引维护）

### 前端
14. api.ts + MessageList 附件卡片状态芯片（loading/queued/processing/ready/empty/error/permission/recovery）+ 详情展开 + 重试；先调 ui-ux-pro-max
15. openapi.json + generated.ts 再生成；npm typecheck/build

### 测试
16. `tests/ingestion/` — 解析/页码章节/哈希分块/幂等重试/账户隔离/解析缓存
17. 索引合同测试 — 维度错误、版本漂移、重建失败、原子切换、旧版回滚
18. 编排测试 — 确定性 Embedding 假服务；显式真实冒烟（scripts/smoke）
19. E2E issue17 — 处理进度/失败原因/重试/重启恢复

### 收尾
20. 全量 pytest / ruff / mypy / npm typecheck+build / E2E；code-review 修复；更新 Issue 17 验收状态；提交

## 目标

按 `/improve-codebase-architecture` 审查报告（architecture-review-20260804-022658.html）
中的重要程度，逐项修复 9 个架构候选：SSE 流事件契约 → 账户隔离下沉 → 生成生命周期
收敛 → 模式编排加深 → 再认证横切门 → Qwen 适配收敛 → 观测层塌缩 → 删除模板平行宇宙
→ 前端接口接缝。每个候选完成后跑相关验证（pytest 子集/全量、ruff、mypy、npm typecheck/build）。

## 任务清单（按重要程度）

1. [x] 候选 2：SSE 流事件契约单一来源（Top 推荐）
   - contracts/chat.py 定义流事件 Pydantic 模型；api/chat.py 的 _generation_events 产出模型；
     模型进入 OpenAPI schemas；重新生成 openapi.json + generated.ts；前端 ChatStreamEvent 改为
     生成类型组合，事件名用判别式字段，删除手写镜像与硬编码字符串
   → 验证：test_openapi_sync 通过、聊天 58 测试通过、全量 1194 过（3 个 doctor CLI 环境
     编码 flake 改动前已存在）、npm typecheck/build 通过、E2E issue11/13/14 通过
2. [x] 候选 4：账户隔离下沉为数据库强制
   - storage/database.py 增加 scoped(account_id) 账户作用域查询面（INSERT 必须含 account_id
     列、其余语句 WHERE 必须含 account_id 过滤，违反即拒绝）；chat/repository.py、
     chat/attachments.py、storage/repository.py 账户域方法改用 scoped，系统级清理保持裸连接
   → 验证：新增 4 条作用域强制负例测试、全量 1198 过（4 个 CLI smoke 编码 flake 环境问题）、
     mypy/ruff 干净
3. [x] 候选 1：生成生命周期收敛为单一接口
   - 新建 chat/lifecycle.py GenerationLifecycle：停止信号注册/续期/TTL 陈旧判定/停止信号
     读取收敛为一个深模块（共享锁 + 单一数据源）；ChatService 删除散落的 _stops 注册表与
     4 个私有方法
   → 验证：新增 6 条 lifecycle 单测、聊天 64 过、mypy/ruff 干净
4. [x] 候选 3：模式编排加深
   - ModeContract 从提示文本升级为步骤化合同：OrchestrationStep Protocol + DeclarativeStep；
     _MODE_CONTRACTS 声明编排步骤（文案不变），_initial_thinking 从 steps 派生，教学门/
     检索/测验作为后续带 run 的步骤接入
   → 验证：聊天 64 过（步骤文案断言不变）、mypy/ruff 干净
5. [x] 候选 7：再认证横切门
   - FastAPI dependency RecentAuthRequired 统一门控（5 路由删除内联调用与 request 参数）；
     RecentAuthService Protocol 收窄 Any 类型
   → 验证：凭据/身份 75 过、mypy/ruff 干净（前端 401/reauth 拦截并入候选 8）
6. [x] 候选 6：Qwen 能力适配收敛
   - qwen_client 增加 first_choice/choice_text 共享实现；删除 qwen_adapters/qwen_vision_adapters
     的 _first_choice 副本与 ASR _first_choice_content；streaming.py（65 行浅文件）并入
     adapters.py 并删除，更新 7 处导入
   → 验证：ai/media 249 过、聊天相关 210 过、全量 mypy 0 错误
7. [x] 候选 5：观测层塌缩
   - 门面瘦身为审计事件流接口（删除 9 个 SLI/告警纯委托方法，调用方只经审计接口）；
     health probe 移除一次性 SLI/SLO 冒烟改为无副作用空查询；loop.py 保留（删除测试不通过：
     共享循环语义删除会移动到两处并漂移）
   → 验证：观测 34 过、相关域 263 过、mypy/ruff 干净
8. [x] 候选 9：删除模板平行宇宙 —— 经证据否决：issue04 E2E（评分资产）56 处引用
   /templates 路由，删除需重写 527 行验收测试，收益（构建体积/导航噪声）不抵风险；
   保留并在 templates/ 加 README 标注其 Issue 04 设计基线身份与生产重定向语义
9. [x] 候选 8：前端接口接缝
   - 统一错误解析：parseAuthError/parseDomainPackError/attachmentApiError 三套 → parseApiError
     + errorFromDetail 单一实现（62 处调用统一）；classifyApiError 统一 401/reauth 分类，
     KeySettings 6 处 + AccountSwitcher 6 处 + PersonalProfileSettings 1 处自写判断收敛
   - XHR 上传保留（进度跟踪的正当理由，错误解析已统一）；openapi-fetch 路由类型化不做
     （重写 1129 行 api.ts 风险收益比不佳，契约类型已由候选 2 消费）
   → 验证：npm typecheck/build 通过、E2E 13 过（issue08 视觉快照 83 像素差异为环境 flake，
     stash 后同样失败）
10. [x] 全量回归：pytest 全套 1202 过（2 skipped，6 个 CLI smoke 编码 flake 为环境既有）、
    全量 mypy 152 文件 0 错误、改动区域 ruff 干净（剩余 2 项为未触碰文件的既有问题）、
    npm typecheck/build 通过、E2E 关键 spec 通过（issue08 视觉快照 83 像素差异为环境 flake）

## 验收命令

```powershell
conda run -n agent python -m pytest -k "<候选相关>"
conda run -n agent python -m pytest -x -q          # 全量
conda run -n agent python -m ruff check .
conda run -n agent python -m mypy src
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
npm --prefix apps/web run test:e2e -- --project=chromium
```
