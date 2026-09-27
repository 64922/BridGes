# 02 — 从聊天界面进入真实设置并完成凭据配置恢复

**What to build:** 用户从左下角账户菜单找到真实账户设置及密钥与模型管理；通勤因缺少高德凭据失败时，可直接进入配置页，验证保存后返回原会话继续操作。

**Blocked by:** None — 可立即开始。

**Status:** ready-for-human

## 问题与证据

当前菜单只提供切换账号、个人资料和退出登录，个人资料跳转模板设置；真实账户设置与密钥模型管理页面已存在。通勤提示用户前往设置，却没有可发现的入口。运行记录确认 `amap_not_configured`，不是路线服务已经配置后返回无路线。

## 任务内容

1. 从实际聊天主界面追踪菜单路由、登录守卫和真实设置页面，确认生产构建与开发构建使用同一条可访问业务路径。复用已有设置和凭据组件，不再创建第二套密钥存储或模板设置。
2. 为账户菜单提供清晰的“设置”入口，设置页可进入“密钥与模型管理”；将个人资料入口接到真实账户资料功能。保留切换账号、退出登录的现有行为及权限边界。
3. 在通勤缺凭据结果中提供直接配置操作，导向高德凭据区域。用户完成配置后能返回原会话，保留原消息及失败结果；重试由用户明确触发，不在保存密钥时暗中执行路线调用。
4. 清楚区分高德路线 Web 服务 Key 与浏览器地图凭据。前者服务路线和地点查询，后者服务地图显示；已获得路线但底图不可用时仍保留路线信息，不能提示成整条路线失败。
5. 保持“验证成功再保存、失败保留旧配置”的行为。输入框不回显已有密钥，页面刷新、异常摘要、浏览器存储和日志均不泄露密钥。Qwen 主模型仍按现有真实验证与运行级锁合同处理，不在本票改变模型选择规则。
6. 将桌面 UI 回归从实际聊天页开始，不能直接打开设置 URL 就判定入口修复。覆盖鼠标、键盘、返回聊天、刷新与会话过期路径。

## 验收标准

- [x] 登录后从聊天侧边栏能够进入真实设置，再进入密钥与模型管理；不跳转模板页。
- [x] 个人资料、切换账号及退出登录保持真实业务行为，未授权访问仍被阻止。
- [x] 通勤缺 Key 时出现可操作配置链接，返回聊天后原会话与消息保持完整。
- [x] 有效候选凭据验证后保存，无效候选失败时旧值保持有效，页面准确展示配置状态。
- [x] 路线凭据与地图凭据状态独立呈现；只有底图失败时路线信息仍可使用。
- [x] 无凭据明文出现在 DOM 回显、日志、截图证据或浏览器存储中。
- [x] 生产构建路径及桌面键盘操作完成验证，测试从主界面入口开始。

## 范围与协作

只修复设置可达性及本次通勤配置恢复，不重构整个账户设置。共享侧边栏、菜单和通勤结果卡中的配置操作由本票负责；03 的解析可独立开发，完整路线体验由 07 联合验收。真实凭据不可用时如实记录外部验收前提，不伪造验证成功。

## 执行与验收记录

**入口复现（改动前）**：真实聊天侧栏的账户菜单只有「切换账号 / 个人资料 / 退出登录」，个人资料指向 `/templates/settings?section=profile`；通勤缺 Key 的失败卡只写「请在设置中配置高德凭据」，没有任何可点入口。生产构建探针证明模板页在生产不可达：`GET /templates/settings?section=profile` → `307 /`。

**最终交互路径**：

- 聊天页 → 左下角账户菜单「设置」→ `/account/settings` → 「密钥与模型管理」→ `/account/settings/models`（高德两组凭据各是一个带 `id` 锚点的分区）。
- 通勤缺 Web 服务 Key → 失败卡内「前往配置高德 Web 服务 Key」→ `/account/settings/models?return_to=/chat/<id>#amap-web-service`（落点分区 `tabIndex={-1}`，进页后自动获得键盘焦点）→ 页首「返回原会话」回原会话，消息与失败结果原样保留。
- 路线已取得但底图凭据缺失 → 地图卡内「前往配置高德浏览器地图凭据」→ `#amap-browser-map`；路线、距离、耗时与路段文字照常保留，不呈现为整条路线失败。
- 个人资料 → `/account/settings/profile`（真实资料页）；切换账号、退出登录保持原行为，未授权访问仍被中间件与服务端会话校验拦下（生产探针：`/account/settings/profile`、`/account/settings/models`、`/chat` 无会话 → `307 /login?return_to=…`，`/login` 200）。
- 开发用模板外壳侧栏同款入口已改指真实账户页；其「切换账号 / 退出登录」仍用模板登录页——该外壳在生产构建整体 `307 /`，不可达，真实账户菜单走真实 AccountSwitcher 与登出 API。

**构建模式**：开发构建（`npm run dev`）与生产构建（`next build` + `next start`）各跑同一套 e2e；生产侧用新增的 `apps/web/playwright.prod.config.ts`（只把前端条目换成 `npm run start`，API / worker / 邮件服务与每 run 数据库沿用主配置），两张配置的差异由 `src/lib/playwright-config.contract.test.ts` 的契约测试守护。

**凭据验证与失败保留结果**：真实高德凭据不可用（外部前提），e2e 只跑负向路径——候选值 `acceptance-probe-not-a-real-amap-key` 经真实 API 探测被拒（422 `credential_invalid`），页面显示「高德 Web 服务验证失败」，该分区状态仍为「未配置」（旧值保持），异常摘要不回显候选值，刷新后 5 个密钥输入框全空。正向「验证通过后保存」由组件单测覆盖（验证成功后写入状态并清空输入、失败保留旧值；新增一例确认保存路线 Key 只翻转 Web 服务分区状态、底图分区保持原状），**未伪造真实凭据的成功验证**。保存凭据不触发路线调用（`src/bridges/api/credentials.py` 只做探测与落库），重试仍由用户点按触发。

**可访问性检查**：键盘可从聊天页打开账户菜单，用 `ArrowDown` / `Home` / `End` 到「设置」并 `Enter` 进入设置页与密钥与模型管理；配置链接落点分区断言 `toBeFocused()`；会话过期后设置入口被守卫拦下，不渲染任何受保护设置内容。issue08/12 既有菜单键盘合同（Home/End/ArrowUp/ArrowDown/Escape 与焦点回归）已同步为四项并全部通过。

**测试命令与结果**（raw 日志见主仓 `.tmp/issue02/raw/`）：

- `npm run typecheck` → 0；`npx vitest run` → 24 文件 / 201 用例全过；`npm run lint` → 0（无本票文件告警）。
- 开发构建 e2e：`npx playwright test -c playwright.config.ts e2e/settings-navigation.spec.ts` → 4 passed (48.0s)。
- 生产构建 e2e：`API_BASE_URL=http://127.0.0.1:8115 npm run build`（退出码 0）+ `PORT=3215 API_PORT=8115 npx playwright test -c playwright.prod.config.ts e2e/settings-navigation.spec.ts` → 4 passed (42.3s)。
- 全量 pytest（`PYTHONPATH=src`、仓外 basetemp、deselect 三条本机挂死的 start 冒烟）：**249 失败 / 3717 通过 / 39 跳过 / 3 deselect**，与同法跑的 main `6939da3` 基线（249 / 3717 / 39 / 3）**失败名称双向 diff 为空**。
- 定点 pytest `tests/chat tests/plugins` → 140 失败 / 531 通过，与既有基线一致。
- 全量 e2e（开发构建）：21 passed / 5 failed，5 条均为既有问题（见下）。
- 密钥不落地旁证：候选值在 e2e 日志、`.next` 产物、`test-results` 与 8 个 per-run 数据库的全部含 key/secret/value 语义列中命中数为 0；本轮无失败重试，未产生 trace 与截图。

**既有失败（非本票引入）**：

- 全量 e2e 的 5 条：`issue08` 桌面视觉快照漂移 1 条（干净树 27,284 px、本改动 27,470 px，两侧都超阈值；快照 2026-08-08 生成、AppSidebar 08-10 改过；未更新快照 PNG 以免掩盖既有漂移）；`issue12` 4 条同一根因——helper `createConversation` 直接 `POST /api/chat/conversations`，而该路由现要求首轮消息（409 `first_turn_required`），断言必然失败，本票未触碰这些用例与其页面代码。
- 全量 pytest 的 249 条与 main 基线逐名一致（见 `raw/failure-diff.txt`）。

**工作树内留有生产构建时的差异（已查明）**：工作树存在 `.next/standalone/server.js` 时，`tests/runtime/test_runtime_contract.py` 两条 `NEEDS_WEB_BUILD` 用例会运行而非跳过（跳过数 37 对 39）。第一轮全量 pytest 正是这一状态，其中 `test_startup_full_journey_start_health_duplicate_reject_stop_restart` 失败（当时并行跑着构建与 `next start`）；隔离复跑 24.8s 通过，删掉 `.next` 后第三轮与基线逐名一致。本票失败集合里没有任何一条由本票改动引起。

**未完成事项**：

- 有效高德凭据的真实正向端到端需要真实 Key（外部验收前提），未执行。
- `issue08` 视觉快照与 `issue12` 的 `createConversation` helper 属既有技术债，本票范围外，未修。
- 模板外壳侧栏的「切换账号 / 退出登录」仍指模板登录页（该外壳生产不可达），未与生产账户菜单合并。

**提交版本**：实现 `8814f63`（分支 `codex/02-settings-navigation`，基于 `main` = `6939da3`）；本工单记录随该分支的 docs 提交更新。

## Comments

- 双轴代码评审（Standards + Spec）已完成并落实：链接色改用 `--color-accent-secondary`（设计令牌规定主强调留给按钮、链接用次级强调）；`credentialSettingsHref` 的锚点参数收窄为 `CredentialAnchor` 联合类型，内部路径常量不再对外导出；新增生产构建配置的契约测试（只替换前端条目、全部条目拒绝复用陈旧进程）；组件单测补一例高德两组凭据状态独立的保存路径。
- 评审提出但**有意保留**的项：进页按锚点自动聚焦（规格要求「直达可操作入口」，键盘与读屏需要焦点上下文，且这是 e2e 断言 `toBeFocused()` 的依据）；新增 `settings-links` 白名单模块与 5 条单测（防 href 漂移与开放重定向，规格未明写但不新增存储、仅 39 行）；底图凭据入口只在取得路线的分支渲染（与「非凭据失败不引导配置」一致）。
