# 14 — 华东交通大学吧信息搜集

**What to build:** 用户显式选择贴吧信息搜集后，可看到确属华东交通大学吧的公开帖子，以及实际可读范围内的回复信息。

**Blocked by:** 11 — 论文搜索

**Status:** ready-for-human

**实施前必读：** [ADR-0030](../../../docs/adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md)、[V2 设计索引](../../../docs/v2/README.md)及索引所列六份设计文档；先核对阻塞票已验收。

**编排合同：** 显式 `tieba` 进入 `tieba.parse → tieba.search → tieba.read → tieba.summarize → 必要时 tieba.verify_official`。搜索发现的帖子先核对贴吧归属；读取节点分别记录主帖、实际取得的回复范围和失败原因，摘要只能消费已读文本。涉及校规、费用、开放时间或流程时追加官方核验并分开展示；受登录或访问限制则按“仅帖链”降级。普通聊天只提供明确的一键建议。

- [x] 菜单与 chip 可选择贴吧模块；保留原始问题与时间条件，只纳入有证据确认属于目标贴吧的帖子。
- [x] 仅对实际取得的主帖和回复进行概括，记录可读楼层与时间；拿不到回复时只给帖链并明确未取得回复内容。
- [x] 涉及校规、费用或办事流程时核对学校官方页面，明确区分官方规定与个人经历。
- [x] 其他贴吧同名帖子、仅有搜索摘要、分页受限和登录墙均有反例验收；公开可得性以真实样本验证。

## Comments

### 实现摘要（2026-09-26，分支 `v2/14-tieba-research`，worktree `../BridGes-14-tieba-research`）

**设计前提（本机真实样本实测，见下节）：** 贴吧帖子页在本机直读一律 403
「百度安全验证」（桌面端、移动端、带本机代理都试过），搜索服务对贴吧链接的
正文抓取同样不成立。因此本模块不承诺「抓到回复」，而是把**归属确认**与
**回复取得**都建立在真的读到页面上：读到页面且页面自身声明该吧才算确认，
拿不到就按「仅帖链」降级并明确标注。搜索摘要只用来**排除**他吧同名帖，
绝不当作确认依据（实测同一链接在不同查询下摘要里出现的吧名会变）。

**后端**（`tests/tieba/` 44 例）：

- `lexicon.py` / `parsing.py`（`tieba.parse`）：原词逐字保留（只做去模块用语与
  尾缀修饰的确定性提取，最多 4 个原词）；时间条件分「绝对年份」与「相对说法」
  记录，并单独记录**有没有真的用上**（`applied` + 中文说明），相对说法不臆造
  年份；主题词不足时只问一个问题，并把恢复载荷（原始问题）写回消息，下一轮从
  该处继续，原始问题与时间条件仍以首次提问为准。
- `searching.py`（`tieba.search`）：查询词由模块按原词自己拼装
  （`tieba.baidu.com 华东交通大学吧 <原词…>`）并逐字发送——实测该域名提示被通用
  planner 分词后贴吧帖不再被召回；调用仍走允许的搜索服务（复用其缓存、预算与
  审计），召回为零时用一条放宽词再试一次。归属剔除只认明确指向他吧的证据
  （标题就是他吧吧名、摘要吧头指向他吧），文本里出现本校写法（含简称）一律
  保留：误剔除会让真实候选连同帖链一起消失，误保留最多多读一次页面。
- `reading.py`（`tieba.read`）：普通公开 GET（固定 UA，无 Cookie、登录或签名
  绕过），每帖 ≤2 页、8s 预算、800KB 上限、≤40 层；分类为
  `read／partial／access_restricted／unrecognized／not_found／timeout／error／
  cancelled`；403/401 只发一次请求即记受限；访问限制只按**整句墙页文案**识别，
  避免把提到「验证码」的正常帖子误判成登录墙；解析按页面公开的 `PageData`
  结构防御式进行，找不到结构如实报不可识别。
- `presenting.py`（`tieba.summarize`）：正文与分区全部由真实证据渲染，本模块
  不调用模型（流程测试断言 `adapter.calls == 0`）；分区只由已读楼层构成
  （可核验的个人经历／不同看法／不确定点），每条引用都带
  「帖子标题 · 第 N 楼 · 时间」；时间条件作为真实过滤，并说明过滤掉多少层。
- `official.py`（`tieba.verify_official`）：只在问题涉及校规／费用／开放时间／
  办事流程时触发；`site:ecjtu.edu.cn` 精确查询加一条放宽查询，只接受
  `ecjtu.edu.cn` 域，≤2 次抓取；命中原文片段与命中原词逐条记录，官方页面与
  吧友经历分区展示，页面取不到时如实记 `fetch_failed` / `excerpt_not_found`。
- `service.py`：五节点顺序执行，停止在节点边界生效；终态
  `success／links_only／empty／error／stopped` 与澄清等待都由真实证据决定；
  失败与终态都写回同一条消息（迁移 54 的 `messages.tieba_research`，
  `finalize_message` 同事务收敛，与 Issue 11 的 `paper_search` 同构）。
- 父图与 API：`AVAILABLE_MODULE_IDS` 加入 `tieba`，未接入模块仍明确拒绝
  （`module_not_available`），普通聊天只给一键建议（不点不检索任何外部来源）。

**前端**（vitest 125 例全绿；`tsc --noEmit` 与 `next lint` 干净）：

- `+` 菜单与 chip 新增「贴吧信息搜集」（复用既有可访问 `Menu` 与 chip 机制，
  新增 `tiebaThread` 图标）；父图节点进度补 `tieba.*` 五项中文标签。
- `TiebaResearchCard`：澄清／检索中／成功／仅帖链／空／失败／停止七态在同一条
  消息内渲染，含所在贴吧、原始问题、时间条件执行状态、每次外部调用记录、已确认
  帖子的读页范围与楼层时间、被剔除候选与依据、官方核验分区、证据边界与重试；
  拿不到回复时逐帖打印「未取得回复内容」与原因。
- 等待恢复 `pendingClarificationModule`：只看**最新一条携带模块投影的消息**；
  它带等待就恢复该模块 chip，否则不恢复（`success／links_only／empty／失败／
  停止` 都算本轮已有结论，不会再冒领更早的澄清）。

**code-review 双轴（Standards/Spec）复核与修复：**

- 归属剔除过宽：原实现「标题以『吧』结尾即当他是吧名」会把
  「有谁还记得当年在华东交大吧」这类真实候选丢掉，还写下错误依据；现只在标题
  **本身就是他吧吧名**且不含本校写法时才剔除（新增 2 例单测）。
- 页面吧名兜底：`kw=` 是弱信号（可能命中页面里指向其他吧的链接），原先任意取值
  都当作页面声明；现只在恰好等于目标吧时采信（确认归属是危险方向，宁可不确认）。
- 访问限制特征过宽：原「验证码」「需要登录」会把提到这些词的正常帖子误判成
  受限；现只留整句墙页文案。
- 前端等待恢复的终态判断（见上）：贴吧的正常真实结局常常是 `links_only`，原实现
  只把 `success/empty` 当终态，会把已结束的一轮当成「还在等回答」。
- 清理死代码与重复：`hasPendingPaperClarification`（被 `pendingClarificationModule`
  取代）、`candidate_links`、`replies_missing_note`、`TIEBA_SUGGESTION_REASON`、
  只写不读的 `PENDING_TIME_REQUIREMENT`、搜索状态映射里不可达的
  `rate_limited/timeout` 分支；`detect_official_topics` 重复调用合一；失败状态
  集合提为 `FAILED_QUERY_STATUSES`。
- Issue 02 的两条验收用例改用仍未接入的 `resources` 模块（贴吧接入后不再是
  「未接入」反例），沿用 Issue 11 接入论文模块时的同一做法。
- 未采纳（记录在案）：`_Run`／`_finalize`／`_pending_wait` 等与论文模块同形的
  编排骨架目前是两处并行实现；抽共享实现会同时改动已上线的论文模块，留作后续
  重构。`run_context`／`run_model_id` 保留是为了与父图对其他模块的子图派发签名
  一致（父图对每个模块统一传参）。

### 真实样本验证（2026-09-26，本机实测）

| 样本 | 结果 |
| --- | --- |
| 帖子页直读 `https://tieba.baidu.com/p/10745250786` | HTTP 403 · 0.2s · 正文即「百度安全验证」页 |
| 移动端帖子页 `https://c.tieba.baidu.com/p/10745250786` | HTTP 403 · 0.1s · 同上 |
| 学校官方页 `https://www.ecjtu.edu.cn/` | HTTP 200 · 52,872 字符（官方核验可用） |
| 教务处 `https://jwc.ecjtu.edu.cn/` | 连接失败（本机不可达；模块按 `fetch_failed` 如实记录） |
| 允许的搜索服务 + 模块实际查询词 `tieba.baidu.com 华东交通大学吧 宿舍 条件` | HTTP 200 · 5 条：1 条贴吧帖且是**上海交通大学研究生吧**（他吧同名帖真实存在），其余 4 条为非帖链接（学校官网、资料站、用户主页） |

结论：读取侧在本机必然降级为「仅帖链」，模块按设计如实降级、不伪造回复；
归属剔除与非帖丢弃两条判定都用这次真实返回验证过（`上海交通大学研究生吧`
→ 剔除；`nani.baidu.com` 用户主页 → 非帖丢弃）。探针脚本只发最小公开查询词，
不含任何私人上下文，也不打印或落盘凭据。

### 全量回归与基线比对（2026-09-26）

跑法两侧完全一致：`pytest tests --ignore=tests/humanize_eval -q --tb=no -rfE
-p no:randomly`，另 `--deselect` 三条本机会挂死的 `test_start_fails_*`，两侧各自
独立 `--basetemp`；**两侧都不设 `PYTHONPATH`**（使子进程导入问题对两边同等生效）。

| 侧 | 提交 | 结果 |
| --- | --- | --- |
| main | `3272835`（含 Issue 06 文件附件） | **274 失败 / 3740 通过 / 40 跳过 / 2 错误** |
| 本分支 | `28a503a` + 合并 main 的 `e8ebc82` | **273 失败 / 3783 通过 / 42 跳过 / 2 错误** |

- 收集总数 4054 → 4098：**+44 恰为本票新增的 `tests/tieba/` 用例数**，无其他增减。
- 失败用例名集合双向比对：**仅分支独有 2 项，仅 main 独有 3 项**，逐项归因如下
  （两侧 `tests/tieba/` 失败数为 0）。
  - 仅分支独有：`tests/closeout/test_api_boot.py` 两个用例。**worktree 环境产物**——
    worktree 里没有仓库 `.venv`，收尾夹具回退到 conda Python，而本机 conda `agent`
    环境没有可用的 `bridges` 安装（pytest 进程内能导入靠仓库 ini 的
    `pythonpath = src`，该设置不传给子进程）。已在合并后的工作树上实测：带
    `PYTHONPATH=src` 跑 `tests/closeout/test_api_boot.py` → **2 passed**。与 Issue 11
    工单记录的是同一产物。
  - 仅 main 独有：`tests/learning/test_teaching_progress.py::test_answer_assessment_drives_remedial_next_action`
    （两侧**单跑都失败**，与本机 Windows 时钟粒度下「最新记录」的顺序假设有关，
    预存在、与整轮调度相关）；`tests/runtime/test_runtime_contract.py` 两个受
    `NEEDS_WEB_BUILD` 控制的 CLI 全流程用例（worktree 无 `apps/web/.next`
    构建产物 → 在分支侧被跳过，在 main 侧运行；本机子进程/端口类抖动用例）。
  - 通过数对账：3783 − 3740 = **+43** = 44（新增贴吧用例）− 2（api_boot 两个
    环境产物）− 2（无 `.next` 而跳过的两个冒烟）+ 3（仅 main 独有、在分支通过的
    三项）。跳过数 +2 即上述两个冒烟用例，账完全对平。
- 本轮之前（基线仍是 `d1acc83`）另有一对跑动，分支侧多出的 2 项失败是
  `tests/chat/test_v2_02_resumable_runs.py` 里以「未接入模块」为反例的两条 Issue 02
  验收用例——贴吧接入后它们不再是反例，已改用仍未接入的 `resources` 模块
  （与 Issue 11 接入论文模块时的处理相同），单独复跑 **10 passed**。
- 静态检查：mypy 两侧均 **115 处**且**文件集合逐一相同**（`tests/tieba` 全绿，
  新增 4 处 tieba 报错已在收尾时修掉）；ruff 分支 **582** vs main **578**，
  差集只有 `src/bridges/api/main.py` 的 E402 从 88 条变 92 条（该文件在 import
  之间有一处 `logger = ...`，本就全是 E402），新增文件 **零 finding**。
- 前端：vitest 分支 **125 通过 / 18 文件**，main 同期 **111 通过 / 17 文件**，
  差 **+14** 恰为本票新增用例（`TiebaResearchCard.test.tsx` 9 例、
  `chat-modules.test.ts` +4、`Composer.test.tsx` +1）；`tsc --noEmit` 与
  `next lint` 干净。
- 工作树里为了跑前端检查临时 `mklink /J` 了 `apps/web/node_modules`（worktree
  不装依赖）；合并前请先删掉这个联接再删工作树，避免删目录时穿透到主检出。

### 合并 main 的接缝处理与合并后验证（2026-09-26）

收尾时 main 已从 `3272835` 前进到 `6f8b15d`（Issue 12 校园通勤 `272c5b8`、Issue 13
学习资料推荐 `bbcc9e1` 均已并入，另有共享库笔记与两条测试/文档提交），分支先
`git merge main` 再验证。共 15 个文件冲突，绝大多数是「两侧各加一条模块登记」，
按**两者都保留**处理；另有三处必须改判定口径：

- **迁移号让位 54 → 56**：Issue 12 已占 54（`messages.commute_route`）、Issue 13
  已占 55（`messages.learning_resources`），本票的 `messages.tieba_research` 整体
  改号为 **56**，`SCHEMA_VERSION = 56`，键 53/54/55/56 连续无缺号。改号后实测
  四条路径：① 全新库 → v56 且含 `tieba_research`；② 造一个 v55 旧库（删列并把
  版本戳改回 55）→ `initialize()` 后补回该列、版本戳升到 56；③ 重复
  `initialize()` 幂等（32 列不变）。全部在临时目录执行，未触碰本机共享桌面库。
- **「未接入模块」反例第二次换人**：贴吧接入后，main 上以 tieba 当反例的三处
  （`tests/chat/test_v2_02_resumable_runs.py` 两处、
  `tests/commute/test_commute_module_flow.py` 一处）与分支上以 commute 当反例的
  一处（`tests/tieba/test_tieba_module_flow.py`）全部改用仍未接入的 `career`，
  注释与 docstring 里的「仍未接入的 X 模块」同步改掉。当前未接入的只剩
  `career` 与 `github`。
- **前端等待态恢复的合并语义**：main 的 `pendingClarificationModule` 只看
  `paper_search`/`learning_resources`，且只把 success/empty 当终态；本票版本只看
  最新一条带模块投影的消息、把「没有 pending」一律视为本轮已结束（贴吧的
  `links_only` 是常规结局，必须算结束）。合并后统一采用后者并把 `tieba_research`
  加进投影列表；通勤形态不同，仍走 main 的 `hasPendingCommuteClarification`。
  `chat-modules.test.ts` 按合并后的实现重写（菜单 ID 顺序为
  `paper, commute, resources, tieba`）。
- `openapi.json` 与 `packages/contracts/src/generated.ts` 不手工合并，按合并后的
  代码重新生成（`PYTHONPATH=src scripts/regenerate_openapi.py` → 299 paths / 668
  schemas，再跑 `openapi-typescript`）；`tests/contracts/test_openapi_sync.py` 2 passed。

**合并树全量回归**（同一命令与同一 deselect 集；两侧各自仓外 `--basetemp`，均不设
`PYTHONPATH`）：

| 侧 | 提交 | 结果 |
| --- | --- | --- |
| main | `6f8b15d` | **273 失败 / 3868 通过 / 40 跳过 / 2 错误** |
| 合并树（分支 + main） | `d46ff21` + 合并 main | **272 失败 / 3911 通过 / 42 跳过 / 2 错误** |

- 收集数 4186 → 4230：**+44 恰为 `tests/tieba/` 三个文件的用例数**（16+12+16），
  逐文件收集数比对后无其他增减；这 44 条**全部真跑通过**（无 skip、无失败）。
- 失败名称集合双向比对：**仅合并树独有 2 项、仅 main 独有 3 项**，逐项归因：
  - 仅合并树独有：`tests/closeout/test_api_boot.py` 两例 —— worktree 无仓库
    `.venv` 的环境产物（带 `PYTHONPATH=src` 单跑即 2 passed）。
  - 仅 main 独有：`tests/runtime/test_runtime_contract.py` 两个 `NEEDS_WEB_BUILD`
    用例（worktree 无 `apps/web/.next` → 合并树侧改为跳过）；
    `tests/profiles/test_issue01_chat_profile_correction.py::test_chat_correction_uses_latest_record_and_is_idempotent`
    （本机时钟粒度导致的预存在顺序抖动，两侧单跑都失败）。
- 账目对平：Δ通过 +43 = 新增 44 + 预存在抖动 1 − api_boot 2；Δ跳过 +2 = 两个
  `NEEDS_WEB_BUILD`；Δ失败 −1 = api_boot 2 −（跳过 2 + 抖动 1）。
- 静态检查：mypy 两侧均 **115 处 / 23 文件**，归一到「文件 + 错误码」后**逐条相同**；
  ruff 同一组文件 main 126 条 vs 合并树 129 条，差集只有 `src/bridges/api/main.py`
  的 E402 多 4 条 —— 即本票新增的 4 行模块导入，与该文件既有的「导入写在
  `logger = ...` 之后」惯例同款（main 上该文件已有 93 条 E402）。
- 前端：`tsc --noEmit` 干净；vitest **20 文件 / 150 通过**（合并前 main 侧 17 文件 /
  111、本票分支 18 文件 / 125），本票新增用例与两侧既有用例同时通过。
- 合并后 main 的 `SCHEMA_VERSION` 为 56，本机共享桌面库仍是 v51 且**未被本次合并
  与验证触碰**（只读探针实测）。注意该库的预存在不一致：版本戳 51 但迁移 52 的两列
  已存在，因此下次以 main 启动桌面端会在 migration 52 上报 duplicate column name；
  修法见 Issue 12 工单的共享库笔记（把版本戳手工改到 52）。

