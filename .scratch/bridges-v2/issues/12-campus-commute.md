# 12 — 校园通勤

**What to build:** 用户显式选择校园通勤后，可取得华东交通大学校内或校门到校内的可核验路线、时间与桌面地图卡。

**Blocked by:** 10 — Tavily 与高德凭据管理；11 — 论文搜索

**Status:** ready-for-human

**实施前必读：** [ADR-0030](../../../docs/adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md)、[V2 设计索引](../../../docs/v2/README.md)及索引所列六份设计文档；先核对阻塞票已验收。

**编排合同：** 显式 `commute` 进入 `route.parse → route.resolve → route.request → route.buffer → route.present`。解析保留原话并只澄清缺少的一项；地点先经校内别名和真实 POI 解析，候选冲突时持久化等待用户选择。路线节点保存对应方式的原始距离、基础耗时、路径点和步骤；核验失败则停止生成地图线。普通聊天可建议用户一键以原文启动通勤，但不得后台执行。

- [x] 菜单与 chip 可选择通勤；起点、终点、方式缺失或含糊时只追问必要项，无法定位“我这里”时不猜坐标。
- [x] 步行、自行车、电动车分别使用对应高德结果，展示地点、距离、基础耗时、文字步骤和可缩放地图，不串用其他方式耗时。
- [x] 按 `Asia/Shanghai` 判断八个课间时间点前后十分钟的边界及五分钟规则缓冲，并明确这不是实时人流数据。
- [ ] 用代表性校园地点实测 POI、楼门和道路；路径不可核验时只展示已证实的最近点与局限，不绘制猜测路线。
- [ ] 无凭据、地点解析失败或路线服务失败有可见降级；路线卡在三个目标桌面尺寸保持关键信息可读。

## Comments

### 实现摘要（2026-09-26，分支 `v2/12-campus-commute`，worktree `../BridGes-12-campus-commute`）

两个提交：`843ceab`（主体，35 文件）、`d7da3b9`（两轴复核后的修复）。相对分支点
`d1acc83` 共 36 文件 / +7543 / -11。编排合同按票面落地，节点名与设计文档一致：
显式 `commute` 进入 `route.parse → route.resolve → route.request → route.buffer →
route.present`，五个节点各自上报 `started/completed` 进度，前端 `MessageList`
已为这五个节点名配上中文标签。模块派发沿用 Issue 11 固化的三份合同
（`ModuleQueryRecord` / `ModuleWaitState` / 失败与停止），未新增第四种机制。

**后端（`src/bridges/commute/`，`tests/commute/` 72 例全通过）**

- `parsing.py` + `lexicon.py`：保留用户原话，缺哪项只问哪一项；「我这里」这类无法
  定位的指代只追问具体楼名或入口，**绝不猜坐标**；等待载荷随助手消息持久化，
  下一轮回答并回后仍缺项时继续只问一项。恢复澄清时以本轮**完整复述**为准——用户
  一次说全「起点+终点+方式」就直接开工，不再拿旧问句逐项套。只有**最近一条**通勤
  投影能决定等待状态，上一轮的澄清不会劫持之后的新请求（两轴复核发现并修复）。
- `resolving.py`：校内别名表先命中，未命中才发真实高德 POI 检索
  （`/v3/place/text`，`city=南昌`、`citylimit=true`，只发完成任务所需的最小检索词）；
  校内多候选时持久化等待用户选择，选定后**不再重复检索该侧**；只命中校外地名时
  如实说明「不规划校外路线」并**不**发起路线规划。证据里记的检索词是**实际发送给
  高德**的那一个（沿用上一轮候选时标注产生该候选的检索词），不是事后重推的词。
- `sources.py`：步行/自行车/电动车分别调用 `/v5/direction/walking|bicycling|electrobike`，
  保存各自原始距离、基础耗时、路径点与文字步骤，不与其他方式串用；停止、超时、
  非 JSON 响应、`infocode` 失败分类各自如实归档（响应体不是 JSON 归入
  `amap_bad_response`，不报成「连不上高德」）；外发请求逐次写账户归属的脱敏审计
  （只记来源/查询指纹/长度/状态/耗时，不记查询正文）。核验失败（缺距离/耗时或
  路径点为空）时如实标注并**停止生成地图线**。
- `buffer.py`：按 `Asia/Shanghai` 判断八个课间时间点前后各十分钟的边界（含端点），
  叠加五分钟规则缓冲；文案始终写明「这是规则估计，不是实时人流数据」，并按
  **当前时刻**（不是抵达时刻）判断。平台耗时只作为规则缓冲叠加，不改写基础耗时。
- `presenting.py` / `service.py`：正文由真实证据渲染（地点、距离、基础耗时、步骤、
  检索词、局限），投影与终态同事务收敛；停止在节点边界与在途请求处生效，如实显示
  「已停止」，不写成失败或「没有结果」。
- `suggestion.py`：普通聊天里出现通勤请求词时才给「一键以原文启动通勤」的建议
  （随消息持久化），建议本身**不产生任何外部调用**，也不后台执行。
- 凭据：高德 Web 服务 Key（`AMAP_WEB_SERVICE_CREDENTIAL_ID`）与 JS API Key/安全密钥
  按 Issue 10 的凭据库存取，接口只回**配置状态与遮罩**，从不回显明文；浏览器安全
  密钥按高德官方代理方案由后端保护——前端只拿到代理路径（`/commute/amap-proxy`，
  相对 API 基地址，前端据此拼绝对地址），安全码与 Key 都不下发到静态 JS。
- 落库：迁移 v54（`messages.commute_route`），本票从分支点的 v53 递增，与 main 上
  Issue 06 未新增迁移的现状不冲突，不需要改号。

**前端（`apps/web`）**

- 菜单与 chip 均可选择通勤（`chat-modules.ts` 新增 `commute` 选项，图标复用新增的
  `route`）；重开对话时若最后一条通勤消息仍在等澄清，输入区恢复该标签。
- `CommuteRouteCard`：澄清/规划中/成功/停止/无凭据/失败六态同一条消息渲染，含地点、
  距离、基础耗时、文字步骤、候选选择、证据检索词、局限说明与重试入口；距离与耗时
  的格式与后端 `presenting.py` 逐字一致（复核时发现并修正了口径不一致）。
- `CommuteRouteMap`：可缩放高德地图卡，`serviceHost` 指向同源后端代理；
  无可绘制路径点（核验失败）时**不画线**，只显示已证实的起终点与局限。

### 全量回归与基线比对（2026-09-26）

- 分支全量（`--ignore=tests/closeout --ignore=tests/humanize_eval -p no:randomly`，
  两侧同 flags、各自独立 basetemp）：**257 失败 / 3698 通过 / 42 跳过 / 0 错误**；
  同期 main（`3272835`，合并 Issue 06 后）**252 失败 / 3649 通过 / 40 跳过 / 0 错误**。
  失败名称集合双向 `comm` 差集：**仅分支独有 5 项，仅 main 独有 0 项**。
- 上述 5 项全部落在 `tests/retrieval/test_retrieval_service.py`（3）与
  `tests/chat/test_retrieval_chat.py`（2），成因是 Issue 06 在本分支点**之后**并入
  main：`git diff d1acc83..main` 显示 `src/bridges/ingestion/service.py`（+19/-…）与
  `tests/retrieval/conftest.py`（+60/-…）正是被改的两处——Issue 06 让聊天附件摄取
  退役，而这两个文件仍走旧路径（单跑时报 `IngestionError: 聊天附件和项目文件已退役`）。
  即：与运动模块无关，按分支点重做对照验证（下一条）。
- **以分支点 `d1acc83` 为基线的对照**（受影响套件：`tests/chat tests/retrieval
  tests/storage tests/persistence tests/integration tests/contracts tests/api tests/paper`，
  两侧同 flags、`-p no:randomly`、外部 basetemp）：
  - 分支：**113 失败 / 963 通过 / 27 跳过 / 0 错误**；分支点：**114 失败 / 890 通过 /
    27 跳过 / 0 错误**。
  - 失败名称集合：**仅分支独有 0 项**（不存在被掩盖的回归）；仅分支点独有 1 项
    `tests/chat/test_chat_api.py::test_stop_generation_via_api`（停止计时的抖动，
    在分支点单跑 3/3 通过、在本轮分支侧通过）。
  - 通过数对账：963 − 890 = **+73**，等于本票新增 72 例加上分支点侧抖动 1 例，
    无未归因增量。
- `tests/closeout` 单独跑（外部 basetemp）：分支 **6 失败 / 112 通过**，分支点
  **6 失败 / 112 通过**，失败名称集合**完全相同**（该套件起真实子进程与数据库，
  两侧同款环境性失败）。
- `tests/commute`：72 例全通过（含 8 例地图代理 API 用例）。
- mypy：两侧均 **115 处 / 23 文件**，错误集合双向 `comm` 差集为空。
- ruff `check .`：分支 580 / main 577，差集只剩 `src/bridges/api/main.py` 的 3 处
  `E402`——该文件在 import 之前有 `logger = ...` 赋值，其后所有 import 都被标记
  （main 现有 88 处），Issue 11 的 arxiv/paper import 同样如此；无新增规则类别。
- 前端：`vitest run` 分支 **18 文件 / 118 例全通过**（main 为 17 文件 / 111 例），
  `tsc --noEmit` 退出码 0，`next lint` 对本次新增与改动文件零告警。
- 契约：`openapi.json` 299 条路径，`packages/contracts/src/generated.ts` 按本分支源码
  重新生成（不是在 main 的产物上做文本合并）。

### 两轴（Standards/Spec）复核后的修复（`d7da3b9`）

- 旧澄清劫持后续请求：等待状态此前由任意历史投影决定，现改为只有最近一条通勤投影
  有效；恢复澄清时用户的一次完整复述优先，不再逐项套旧问句。
- 缓冲文案与计算口径不一致：此前写「抵达时间」但按当前时刻判断，已改为当前时刻。
- 地点证据里的检索词此前是事后重推，现记录实际发送给高德的那一个
  （`PlaceResolution.used_query`）。
- 响应体非 JSON 此前按 `httpx.HTTPError` 处理，会报成「无法连接高德」，现单列
  `except ValueError` → `amap_bad_response`，不同响应体混为一类。
- 地图代理路径此前后端返回 `/api/commute/amap-proxy`，前端再拼 API 基地址得到
  `/api/api/...` 双前缀；现后端只返回 `/commute/amap-proxy`（相对 API 基地址），
  前端 `commuteMapServiceHost` 拼成同源绝对地址。
- 路线客户端去掉不可达的兜底返回，改为循环内所有出口都是 `return`（同时消掉
  mypy 的 `return` 告警，使 mypy 结果与 main 逐条一致）。

### 未完成的实测（本机能力不足，需要凭据/真机）

- **第 4 条验收（用代表性校园地点实测 POI、楼门和道路）未做。** 本机未配置高德凭据
  （`AMAP_WEB_SERVICE_CREDENTIAL_ID` / `AMAP_BROWSER_MAP_CREDENTIAL_ID` 均未设置），
  无法对真实校园 POI 与道路发请求。测试用 `httpx.MockTransport` 覆盖了响应形状、
  失败分类、字段缺失与候选冲突，但**真实地名匹配质量、别名表覆盖率、高德吸附到
  远端时的局限文案都没有实测**。验收请在配好凭据的机器上按票面地点清单实测，重点看：
  别名表未覆盖的楼栋、同名 POI 冲突、只命中校外地名时的说辞。本票的
  `_limitations` 已把「起终点被吸附到远处」写成局限说明，但未在真实数据上验证阈值。
- **第 5 条验收的三尺寸实机核对未做。** 降级路径（无凭据、地点解析失败、路线服务
  失败）已有组件级用例覆盖并有可见降级与重试入口；但 V2 模块目前没有 e2e 规格
  （`apps/web` 只有既有 playwright 合同测试），且无凭据时无法产出成功态路线卡，
  因此 1280×720 / 1440×900 / 1920×1080 三个尺寸的浏览器实机核对未做。验收时请在
  配好凭据的机器上按这三个尺寸打开路线卡核对关键信息可读性。

### 环境说明（供后续票参考）

- 同机其它会话的清理脚本会删掉工作树内的 `.tmp`，全量跑若用仓内 basetemp 会在中途
  报 `FileNotFoundError: .tmp/...` 并级联成成百上千个 setup 错误（本轮实测 494 与
  1066 两例）。改用仓外 basetemp（`%LOCALAPPDATA%\Temp\...`）后 0 错误。
- 同机并行跑多个全量会话时，`tests/closeout` 的锁文件会让后续用例在 `tmp_path`
  清理处 `PermissionError` 并级联（本轮实测 442 错误）。需要结论时请单独跑 closeout。
- 分支点的 `tests/retrieval` 在 main 上已被 Issue 06 改好，因此**不能拿当前 main 当
  基线**比对本票；本轮改用 `d1acc83` 独立工作树做对照（用完已移除）。

### 未改动的既有问题（留给后续）

- `_invoke_paper_module` 与 `_invoke_commute_module`、以及 paper/commute 两套 service
  脚手架结构相近；按「只做必要改动」未合并抽象。
- `src/bridges/api/main.py` 的 E402 属该文件既有模式，本次沿用 Issue 11 的放置位置，
  没有顺手重排 import。
