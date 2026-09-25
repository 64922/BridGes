# 13 — 学习资料推荐

**What to build:** 用户显式选择学习资料推荐后，可按本轮要学的技术方向收到有顺序、有直达链接的图书与视频清单。

**Blocked by:** 11 — 论文搜索

**Status:** ready-for-human

**实施前必读：** [ADR-0030](../../../docs/adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md)、[V2 设计索引](../../../docs/v2/README.md)及索引所列六份设计文档；先核对阻塞票已验收。

**编排合同：** 显式 `resources` 进入 `resources.parse → resources.search_books / resources.search_videos → resources.rank → resources.present`。先保留本轮主题、目标和可靠的学习阶段；确实缺少影响推荐的层次时持久化澄清。图书核对书目、ISBN 或出版社信息，视频由公开检索发现后核对可得元数据；只将验证成功的条目送入排序与生成。普通聊天只提供明确的一键建议。

- [x] 菜单与 chip 可选择资料模块；检索主题保留本轮原始专业名词，学习层次确实影响推荐且上下文不足时只追问这一项。
- [x] 默认尝试提供两本书和三条哔哩哔哩视频，逐项核对书目或视频可取得的元数据、链接及适用阶段。
- [x] 展示由浅入深的顺序和选择理由；未看过的视频不描述不可验证的具体内容，条目不足时说明实际数量。
- [x] 可见真实检索词、来源和失败位置；代表性图书与视频来源经实际可得性验证。

## Comments

### 实现摘要（2026-09-25，分支 `v2/13-learning-resources`，worktree `../BridGes-13-learning-resources`）

提交：`8443e8c`（实现）→ `7658850`（合并 main 的 Issue 06 聊天文件附件）→ `9f3c3cc`
（code-review 复核修复）→ `75afd12` 及其后一笔 docs 提交（本工单记录、来源常量单源化与迁移撞号警告）。
阻塞票 Issue 11 已在 main 上 `ready-for-human`，本分支已并到 main 当前状态。

**架构：第二个接入日常父图的显式模块，不新增合同。**
父图 `select_explicit_module → invoke_subgraph_or_chat` 只按随用户消息持久化的
`module_id` 派发，`ChatModuleId.RESOURCES` 加入 `CONNECTED_MODULE_IDS`；其余四个模块
仍显式拒绝（`module_not_available`）。证据 / 等待 / 失败与停止三份合同全部复用
`src/bridges/contracts/modules.py`，没有为本票新增合同类型。

**后端**（`src/bridges/resources/`，`tests/resources/` 55 例：核心 25 + 来源 18 + 端到端 12）：

- `parsing.py` + `lexicon.py`：本轮专业名词逐字保留为原词，译名/同义词只作扩展；
  学习层次（零基础／有基础／进阶）先看本轮再看前文，**确实缺层次且层次会影响推荐时
  只追问这一项**（`ModuleWaitState` 随助手消息落库，下一轮从该处恢复，并保留已给出的
  主题与学习目的）；学习目的（考试／项目／科研／兴趣）同样解析并进入推荐理由。
- `planning.py`：默认目标 **2 本书 + 3 条视频**，候选各 8 条（核对会淘汰）；图书查询只发
  主题词，视频查询额外带层次后缀，两条查询都保留原词。
- `sources.py`：图书书目来自 Open Library（标题/作者/年份/出版社/ISBN）与 OpenAlex
  图书记录；视频由公网搜索（Tavily）发现哔哩哔哩直达页（**按视频 id 去重**，跟踪参数
  链接不会重复占位），再经哔哩哔哩 `view` 公开接口**逐条核对**标题、作者、发布时间、
  时长、简介与公开计数，核对不通过的条目直接移除并记账。每次外发请求写账户归属的
  脱敏披露审计（`AuditAction.LEARNING_RESOURCE_LOOKUP`，只记来源/查询词指纹/长度/
  状态/耗时，不记查询正文）；失败分类稳定（`*_timeout` / `*_offline` / `*_rate_limit` /
  `*_http_4xx` / `*_parse` / `*_unavailable`）且不外泄上游细节；两条链路各有 8s 墙钟预算。
- `ranking.py`：主题门**只认标题**（简介不参与，否则终态覆盖门与排序门会互相矛盾）→
  层次相容（阶段冲突降权并把原因写进理由）→ 由浅入深排序；公开计数只在**同阶段内**
  做弱证据平局打破，理由里明确写「平台计数，不代表质量结论」；同阶段内来源顺序稳定
  （Open Library 优先于 OpenAlex）；结果必须覆盖原词，否则停止生成
  （`resources_topic_mismatch`，不凑数量）。
- `presenting.py`：正文完全由真实证据渲染、**不调用模型**（原词、实际查询词、逐条直达
  链接/适用阶段/选择理由/核对依据、实际数量与证据边界）；未看过的视频只写公开元数据，
  不描述内容；条目不足时同时说明实际数量与目标数量。
- `service.py`：`resources.parse → search_books / search_videos → rank → present` 五节点
  各自发真实 `started/completed` + 耗时（前端显示中文节点名）；停止在节点边界生效并把
  「已停止」写回同一条消息；硬失败写回真实错误码与失败节点；终态与投影同事务收敛
  （`finalize_message`）。
- `suggestion.py`：普通聊天出现学习诉求词且有可检索主题时才给「使用学习资料推荐」一键
  建议（随消息持久化），建议本身不发起任何外部调用；歧义词（「随便」「都行」）不给建议。

**前端**（vitest **121/121（18 文件）**，`tsc --noEmit` 与 `next lint` 干净）：

- `chat-modules.ts` 注册「学习资料推荐」（图标 `learningProject`）；`pendingClarificationModule`
  同时识别论文与资料的等待状态，重开对话时恢复可移除 chip。
- `LearningResourcesCard`：澄清／检索中／成功／空／失败／停止六态在同一条消息内渲染，
  含原词与实际查询词、学习层次与目的、每次外部调用记录、由浅入深的条目（阶段／书目或
  视频元数据／理由／核对依据／直达链接）、实际数量与证据边界、可重试入口。
- `QueryRecordList`（本轮评审新增）：论文卡与资料卡共用的「本次外部调用记录」，缓存命中／
  上游次数／冷却等内部日志一律不呈现。
- `MessageList` / `chat-thread` / `api.ts` / `openapi.json` 与生成契约同步。

**code-review 双轴（Standards / Spec）复核与修复**（提交 `9f3c3cc`）：

- 停止正文此前会回退到原词，现由查询计划渲染**真实图书查询词**；`resources.present` 也成为
  真实节点（整理清单的耗时与完成事件可见，此前这一步没有进度）。
- 证据正文不再把 `record.detail`（缓存命中、上游请求次数）写进用户可见文本
  （`docs/v2/interaction.md` §4「不把检索内部日志显示给用户」）。
- 视频主题门只认标题（此前简介也参与，会让终态覆盖门与排序门互相矛盾）。
- 单源化：来源中文标签 `SOURCE_LABELS`、来源常量（不再写字面量 `"bilibili"`）、层次标签
  复用 `LEVEL_LABELS`；删除未被读取的 `ResourcesRunOutcome.queries`、未使用的 `now` 形参、
  `render_stopped_content` 的死分支。
- 前端抽出共享的 `QueryRecordList`（此前资料卡整段复制论文卡的调用记录组件）。
- 修 `sources.py` 的 `retryable` 判定（mypy `union-attr`：`bool(x) and x.endswith(...)`
  改为显式 `is not None`）。

### 全量回归与基线比对（2026-09-25）

两侧同 flags：`--ignore=tests/humanize_eval`、deselect 三个 `start` 冒烟用例、
`-p no:randomly`、各自独立 `--basetemp`；两侧都**不**设 `PYTHONPATH`（子进程导入问题
对两边同等生效）：

- main（`3272835`，合并 Issue 06 后）：**274 失败 / 3740 通过 / 40 跳过 / 3 deselected / 2 错误**（12:51）。
- 分支（`9f3c3cc`）：**273 失败 / 3794 通过 / 42 跳过 / 3 deselected / 2 错误**（12:17）。

失败用例名集合双向 `comm` 差集：

- **仅分支独有 2 项**：`tests/closeout/test_api_boot.py` 的两个用例。工作树没有仓库
  `.venv`，收尾夹具回退到 conda Python，而本机 conda `agent` 环境没有可用的 `bridges`
  安装（pytest 进程内能导入靠仓库 ini 的 `pythonpath = src`，该设置不传给子进程）。已实证：
  分支上带 `PYTHONPATH=src` 复跑该文件 **2 passed**。与 Issue 08/11 工单记录的同款环境产物。
- **仅 main 独有 3 项**，都不是被本分支掩盖的回归（它们在 main 上失败、在本分支通过）：
  - `tests/runtime/test_runtime_contract.py` 两例 `@NEEDS_WEB_BUILD`：main 仓库里存在
    `apps/web/.next/standalone/server.js` 生产构建产物，因此会真跑并失败；工作树没有该
    产物，按标记跳过——两侧「跳过」数 42 vs 40 的差额正是这两项。
  - `tests/learning/test_teaching_progress.py::test_answer_assessment_drives_remedial_next_action`：
    时钟刻度/负载相关的抖动（本机 Windows 时钟粒度约 15.6ms），两侧单独复跑各 **3/3 通过**。
- 通过数对账：3740 → 3794 = **+54** = 本票新增 55 例 − 2 项工作树产物 + 1 项 main 侧抖动；
  失败数 274 → 273 是同一笔对账的另一面，无未归因增量。
- 两侧 2 个 ERROR 完全相同（`tests/integration/test_runtime_smoke.py` 的
  `test_health_endpoints_return_unified_projection` 与 `test_api_gracefully_shuts_down`）。
- 跑全量的代码状态即 `9f3c3cc`；其后本分支只追加了一次取值等价的来源常量替换
  （`ranking.py` 里写死的 `"bilibili"` 改用 `BILIBILI_SOURCE`，字符串取值为同一个值）
  与这份工单更新，行为不变，`tests/resources` 复跑 55 例全绿。

静态检查同口径比对：

- mypy：两侧均 **115 处 / 23 文件**，错误集合双向差集为空（本分支 393 个源文件、main 383 个，
  差额即本票新增的 10 个源文件）。
- ruff：findings 集合双向差集只剩 `src/bridges/api/main.py` 新增 **2 处 E402**，即本票的
  `from bridges.resources.service/sources import ...` 两行——它们紧挨 Issue 11 的
  `bridges.paper.*` 三行放在同一位置（该文件已有 88 处同类），为保持模块级装配惯例；
  其余逐条一致（578 → 580）。
- 前端：`tsc --noEmit` 干净、`next lint` 零 Error（仅仓库既有的 `<img>`/hook 警告）、
  vitest 分支 **121/121（18 文件）**、main 侧同命令 **111/111（17 文件）**，即净增 10 例
  1 文件（新卡 6 例 + `chat-modules` 4→7 + `MessageList` 6→7）。
- 契约：`tests/contracts` 3 例通过（`test_openapi_sync.py` 确认 `openapi.json` 与当前 API 同步）；
  合并 main 后契约按合并源码重新生成，未采信文本自动合并。

### 迁移升级实测（`SCHEMA_VERSION` 53 → 54）

本票新增迁移 `54: ALTER TABLE messages ADD COLUMN learning_resources TEXT`，main 为 53
（Issue 06 的 `study_states`）。实测（本地 SQLite）：

- main 代码新建库 → **v53**，`messages` 无 `learning_resources`（29 列）；
- 同一个库文件用分支代码 `initialize()` → **v54**，列被补上（30 列）；
- 迁移键连续 `1..54`，无空洞。

方向提醒（与 Issue 11 同款）：v54 的库不能再由 main 版程序启动（会报库版本高于程序），
合并前不要用 main 版程序打开已升级的桌面库。

**撞号警告（合并前必查）**：当前有**三个**并行分支同时声明迁移 **54**，而 main 仍是 53：
issue 12（`messages.commute_route`）、issue 13（本票，`messages.learning_resources`）、
issue 14（`messages.tieba_research`）。迁移执行是
`for version in range(current + 1, SCHEMA_VERSION + 1): MIGRATIONS[version]`——**按键逐个取，
不连续会 KeyError**；而 dict 字面量里出现两个 `54:` 键时 Python 会静默保留后者。因此三票
不能共用 54：**按合并顺序，第一票保留 54、第二票改 55、第三票改 56，并同步各自的
`SCHEMA_VERSION`**（改完都要实测升级路径）。这是 Issue 08/11 撞号事故的同一形态，
`git merge` 在改写位置不相邻时不会报冲突，必须人肉核对键的连续性。

### 真实可得性验证（AC4）

用模块自己的适配器直连真实来源（探针 `.tmp/resources_live_probe.py`，仅网络读取、不写数据；
适配器不注入 `httpx.Client` 时表示「未装配」，会记 `*_unavailable` 并保留查询词）：

- **Open Library**（`q=深度学习`）→ `success`：`深度学习：核心原理与案例分析`（Ahmed Menshawy，
  2024，ISBN 9781836201205，出版社 de Gruyter GmbH, Walter），
  <https://openlibrary.org/works/OL39662157W>。
- **OpenAlex**（`q=deep learning`，`filter=type:book`）→ `success`：`Deep Learning`
  （Goodfellow / Bengio / Courville，2016）、`Neural Networks and Deep Learning`
  （Charu C. Aggarwal，2018）、`Deep Learning: Methods and Applications`（Li Deng，2014），
  均为真实 DOI/出版社链接。
- **哔哩哔哩公开接口核对**（`BV1pu411o7BE`）→ `success`：`Transformer论文逐段精读【论文精读】`，
  作者 `跟李沐学AI`，时长 5225 秒，发布 2021-10-28，播放 1,815,696、点赞 45,781。
- **边界（如实记录）**：本机没有配置公网搜索凭据（`TAVILY_API_KEY` 未设置）。用应用同款装配
  路径实测，发现链路如实返回 `error` / `web_search_credentials` / 可操作中文说明
  （「未配置搜索凭据：请先运行 BridGes start 配置 Tavily API Key，或设置
  BRIDGES_TAVILY_API_KEY。」），**没有发送任何请求、也没有伪造条目**（直达页 0 条）。
  因此本机无法端到端跑通「公开发现 → 逐条核对」的真实视频链路：核对链路已用真实公开接口
  验证，发现链路以注入假实现的单测/流程测试覆盖（含发现失败、无直达页、核对淘汰等分支）。

### 已知边界（未做与取舍）

- 排序实际使用的判据是**主题门（标题覆盖）→ 层次相容 → 由浅入深（阶段标记与视频时长）**，
  公开计数只在同阶段内做弱证据平局打破。设计文档 §4 里提到的「先修要求／版本时效／互补性」
  在实现中的落法是：先修由层次相容与阶段承担，年份与版次如实展示但不参与打分，图书与视频的
  互补由类型分工体现（图书作「读书主干」、视频作「讲解」），没有为它们编造数值权重。
- 图书侧不做逐条二次核对：书目来源本身就是元数据来源（标题/作者/年份/出版社/ISBN 一次取回）；
  逐条核对用在视频侧，因为视频的标题与元数据必须来自公开接口而非搜索结果。
- `ResourcesStatus.SEARCHING` 与 Issue 11 一致地保留在枚举里，但「检索中」状态由节点事件
  （SSE `node`）承载，投影不落 `searching`。
- 澄清重问与陈旧澄清粘性行为与 Issue 11 同源（同一套等待合同），未单独收紧。
- 视频发现依赖公网搜索凭据；缺凭据时本轮图书照常给出，视频缺口在证据边界里如实说明，
  不会用别的来源或模型记忆补条目。
