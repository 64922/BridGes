# 06 — 保留贴吧检索原词并恢复空候选时的备用检索

**What to build:** 用户查询“中秋节放假”时，系统保留完整主题词，在首轮没有可用帖子时执行有界备用查询，清楚展示帖子归属、过滤原因和相关官方放假核验。

**Blocked by:** None — 可立即开始。

**Status:** ready-for-human

## 问题与证据

解析函数把“中秋节放假”输出为“放假、秋节”，因为对任意子串删除结构词“中”。真实运行记录使用了该错误查询。搜索返回 5 条原始结果但没有可用帖子，且首轮成功记录的 `retryable=false` 导致编排提前退出；模拟该分支只执行 1 次搜索，没有运行已有备用查询。

当时原始 5 条链接未完整保存，所以不能声称已确认它们分别属于其他贴吧、非帖子页或无法读取。还原此次结果应诚实区分确定缺陷与不可恢复证据。

## 任务内容

1. 建立从原问题到查询计划的回归，明确断言“中秋节”完整保留，不接受“秋节”。修正中文结构词处理边界，覆盖其他含“中、上、下”等字的真实短语，避免只增加一个节日特例掩盖任意删字问题。
2. 将“检索成功但没有可用候选”与“请求失败是否可重试”分开判断。前者应执行现有计划内的备用查询；永久错误、取消或超出预算应停止；不引入无限查询或无界扩大范围。
3. 覆盖原始结果非空但全部不可用、完全空结果、备用查询命中、两轮均空、鉴权错误、临时错误及取消。请求数遵守已有有界计划；去重和已有截止时间仍有效。
4. 对候选判定增加足够的脱敏诊断：区分非帖子链接、明确他吧、页面不可读、归属未确认等原因。保留实际公开查询和必要链接证据，不收集完整私人对话。原始命中数不能当作确认帖子数。
5. 继续要求读取帖子自身并验证目标贴吧归属；搜索摘要只作候选线索。不能通过放宽归属校验或将摘要冒充正文解决“空结果”。无确认帖子但有合法候选链接时准确说明不确定性。
6. 将放假安排纳入现有官方核验路径，限定学校官方来源，与贴吧讨论分别呈现。保留节日语义，不凭空添加用户未指定的年份；涉及本次安排且年份不明确时，沿用可解释的时间规则或提出必要澄清，避免旧帖冒充当前通知。
7. 用原句进行有界真实检索，记录查询词、候选数、确认数及官方核验结果。真实没有结果允许返回准确空态，不把“必须搜出帖子”作为验收条件。

## 验收标准

- [x] 原句查询完整保留“中秋节”和“放假”，其他合法中文短语不被结构词误删。
- [x] 首轮成功但无可用候选时执行备用查询；备用命中可进入读取及呈现路径。
- [x] 两轮均空、永久错误、取消和超时均有界终止，不无条件重试。
- [x] 结果数、过滤理由、未确认候选与确认帖子可区分，诊断记录可解释空态。
- [x] 他吧帖子不被纳入，摘要不冒充已读取正文，归属未确认时明确标注。
- [x] 放假官方核验与贴吧讨论分开；年份不明、历史信息和来源缺失不被伪装成当前事实。
- [x] 提供原句真实检索证据及模拟边界测试结果，真实无结果不被报告成修复失败或虚构成功。

## 范围与协作

复用现有搜索、帖子读取和官方核验能力，不新建爬虫平台，不切换搜索提供方，不与 GitHub 票共同重写通用中文解析。搜索和结果卡归本票所有，共享模块类型变更须协调。

## 执行与验收记录

留档：脚本在主仓 `.tmp/issue06/scripts/`，原始输出在 `.tmp/issue06/raw/`（清单见 `.tmp/issue06/README.md`）。所有脚本首行打印实际导入的 `bridges` 路径——本机 conda 环境里 `bridges` 以 editable 方式装在主仓，`PYTHONPATH` 写错会静默跑主仓代码。

**复现（改动前）**

- 解析（`scripts/repro_parse.py`，同一脚本分别对 main 与分支 `src` 跑）：main 上 `parse_tieba_request("华东交通大学吧 中秋节放假")` → `topic_terms=['放假','秋节']`，查询词 `tieba.baidu.com 华东交通大学吧 放假 秋节`；同一缺陷在其他真实短语上同样成立——`宿舍 上铺 下铺 尺寸` → `['宿舍','尺寸']`（上铺／下铺被删）、`食堂 中午 人多吗` → 中午丢失、`学校 中秋 放假 通知` → 中秋丢失。对照输出 `raw/repro-parse-before.txt` 与 `raw/repro-parse-after.txt`。
- 检索（`scripts/repro_fallback.py`）：计划 2 条查询，模拟首轮「成功但 5 条原始结果都不是帖子页」→ main 上**实际只调用 1 次**、剔除记录 0 条（5 条被静默丢弃）；分支上同一脚本 2 次、剔除记录 10 条。对照输出 `raw/repro-fallback-before.txt` 与 `raw/repro-fallback-after.txt`。

**改动**

| 文件 | 改动 |
| --- | --- |
| `lexicon.py` | 方位类单字（中／上／下／里／内／外）不再参与按字删词（它们是中秋／上铺／下铺／中午／家里／外语的成分），单独成词时由长度过滤丢弃；新增 `HOLIDAY_ARRANGEMENT_TERMS`（放假／假期／校历／调课／补课／调休）并入 `OFFICIAL_TRIGGERS`，只收放假安排本身的词，不把官方核验扩到返校／开学一类学期节奏问题 |
| `searching.py` | 新增 `classify_hits_with_diagnostics`：非帖子链接、他吧证据、重复、可用候选分别计数；非帖子链接不再静默丢弃，逐条进入剔除记录；`SearchOutcome` 带 `HitDiagnostics` |
| `service.py` | 检索循环把「成功但无可用候选」（继续计划内备用词）与「请求失败」（仅可重试继续；取消／永久错误／预算耗尽终止）分开；剔除记录**跨轮去重**（同一链接只留一条，多出来的那次按重复链接计数，`_deduped_diagnostics` 保证「各计数之和 = 原始命中数」）；逐轮说明与证据边界写明查询词、原始条数、可用候选与剔除构成；读取失败逐条写明原因（页面不可读：访问受限…／未读取：超出上限），帖链被上限截断时另给确认总数；空态给出原始命中构成；失败分类只描述本轮真实落点；官方候选按「标题点题 + 目标年份（问题里的年份，问题没写就用当前年份）」优先排序；年份未指定时写入不假定年份的说明 |
| `presenting.py` | 候选帖链逐条按「来源；未确认原因；归属未确认」分列（原因不再塞进来源字段）；剔除段标题改为「已剔除的候选（每条附剔除依据）」；官方核验段抽成 `_official_lines`，成功态与空态共用（原先空态缺官方段） |
| `contracts.py` | `TiebaCandidateLink` 新增 `unconfirmed_reason`（归属未确认的具体原因）；`needs_official_check` 的描述补上「放假安排」 |
| `TiebaResearchCard.tsx` | 帖链逐条显示来源、未确认原因与「归属未确认」（原先硬编码「归属未确认（仅有搜索摘要）」）；剔除段标题同步 |
| `openapi.json`、`packages/contracts/src/generated.ts` | 按新契约重新生成（`PYTHONPATH=src scripts/regenerate_openapi.py` → 299 paths；`npx openapi-typescript`），前端类型随生成文件更新 |

**各分支请求数（模拟边界测试，`tests/tieba/test_tieba_module_flow.py`）**

| 分支 | 计划查询 | 实际查询 | 终态 |
| --- | --- | --- | --- |
| 首轮成功但 5 条都不是帖子页 → 备用命中 | 2 | **2** | success（进入读取与呈现） |
| 两轮均无可用候选（5 条非帖子链接 + 另一批 3 条） | 2 | 2 | empty，retryable=false，空态给出「共取得 8 条原始搜索结果（非帖子链接 8 条）」 |
| 两轮返回同一批 5 条非帖子链接 | 2 | 2 | empty，剔除记录 5 条（唯一），空态给出「共取得 10 条原始搜索结果（非帖子链接 5 条、重复链接 5 条）」 |
| 两轮完全零结果 | 2 | 2 | empty，空态写「本轮检索没有返回可确认属于「华东交通大学吧」的公开帖子」 |
| 永久错误（鉴权无效，不可重试） | 2 | 1 | error，retryable=false，`error_code` 透出 |
| 端口终止态（cancelled／timeout／rate_limited／error 且不可重试） | 2 | 1 | error（有界终止） |
| 临时错误（可重试）→ 备用命中 | 2 | 2 | success |
| 超出搜索预算（`search_deadline_seconds=0`） | 2 | 1 | 备用查询注明「因超出本轮检索预算而未执行」 |
| 首轮之后用户停止 | 2 | 1 | 备用查询注明「因你已停止而未执行」，随后走既有停止路径 |

`timeout`／`rate_limited` 两条是把端口状态直接注入替身，用来固定「终止态不再发查询」的边界；适配器不会自己造出这两个状态，真实提供方超时在模块里表现为 `ERROR` + `can_retry=True`，因此会继续跑备用词（由 `test_retryable_transient_failure_still_uses_the_fallback_query` 覆盖）。

**过滤原因样例**：本轮真实检索 10 条原始命中、8 条唯一链接（两轮各 5 条，其中 2 条链接重复出现），**全部**是「非帖子链接」，逐条留痕——优酷播单、`nani.baidu.com` 用户主页、微信公众号文章、贴吧分类页，以及上海交大附中／华东师大／同济／本校图书馆的放假通知页与被命中的官网首页（见 `raw/real-search-final.txt`）；两轮都**没有**出现「他吧证据」类命中（各 0 条）。「他吧同名帖被剔除」的样例来自替身用例 `test_other_forum_threads_are_filtered_with_evidence`（摘要吧头「上海交通大学研究生吧」），不冒充成真实检索所见。原始命中数只作为命中数呈现，正文里明确写「原始命中数不等于确认帖子数」，重复链接只列一次并计入「重复链接 N 条」。

**官方核验策略**：放假与校历类词并入官方触发词 → 走既有 `site:ecjtu.edu.cn` 查询（未命中才用一条去域名限定的备用查询），只取学校官方域名页面；候选按「标题含原始名词且含目标年份」优先排序（只用于挑选顺序，年份不写进用户问题，也不写进官方结论）；每页只呈现页面原文摘录与取得时间；年份未指定时证据边界写明「不假定年份，也不把历史通知或旧帖当作本次安排」；官方段与吧友段分列，空态同样给出官方段。排序规则要有实测依据：`raw/official-probe.txt` 显示 `site:ecjtu.edu.cn 华东交通大学 放假 中秋节` 命中 5 条、全部为学校官方域名，检索顺序第一条是「国庆悦赏山河美，花式表白我的国｜红色景点打卡来啦！」这类活动报道页，排序后它落到第 3，被读取的两条才是图书馆开放安排通知。

**真实检索结果（2026-09-27 本机，探针 `scripts/real_search_probe.py`，输出 `raw/real-search-final.txt`）**

- 解析：原句「华东交通大学吧 中秋节放假」→ 原始名词 `['放假','中秋节']`，计划 2 条查询 `tieba.baidu.com 华东交通大学吧 放假 中秋节` / `华东交通大学吧 放假 中秋节 贴吧`。
- 对照：修复前的错误查询词 `… 放假 秋节` 在本轮同样返回 5 条原始结果，全部非帖子链接、可用候选 0。
- 修复后模块真实编排：**两轮查询都执行**（各 5 条原始结果、全部非帖子链接）→ 确认 0 帖、仅帖链 0 条 → 终态 `empty`、`retryable=false`，正文如实写出 10 条原始结果的去向（8 条唯一剔除记录 + 2 条重复链接）与逐条剔除依据。真实没有帖子结果按准确空态返回，未报告成修复失败，也未虚构帖子。
- 官方核验：`site:ecjtu.edu.cn 华东交通大学 放假 中秋节` 命中官方域名页面 5 条，取前 2 条均 `verified`：`lib.ecjtu.edu.cn/info/1076/7501.htm`（关于2026年中秋节国庆节假期图书馆开放安排的通知，命中原词「中秋节」）与 `lib.ecjtu.edu.cn/info/1076/1825.htm`（命中原词「放假、中秋节」）。
- 探针只发最小公开查询词，不打印、不落盘凭据；本轮只读取公开页面，未绕过任何访问限制（本机对贴吧帖子页一律 403 的既有事实见 Issue 14）。

**测试命令与结果**

- `pytest tests/tieba` → **69 passed**（main 45 → 69；逐名 diff 见 `raw/tieba-names-{main,branch}.txt`：新增 25 条 `test_tieba_core.py` 11 条含 7 条参数化解析、`test_tieba_module_flow.py` 14 条，另有 1 条既有用例被重写——`test_non_thread_and_duplicate_urls_are_dropped` → `test_non_thread_and_duplicate_urls_are_diagnosed`，旧断言是「非帖子链接被静默丢弃」，本票要求逐条留痕，故按新行为改写而不是留着一条断言旧行为的用例）。
- `pytest tests/tieba tests/web_search tests/contracts` → **215 passed / 2 skipped**。
- `pytest tests/chat tests/plugins` → **140 failed / 531 passed**，与该组既有基线一致；其中 `tests/chat` 101 条失败与 main 基线**逐名双向 diff 为空**（`raw/final-chat-plugins.txt` vs `.tmp/issue02/raw/pytest-baseline-main.log`）。
- 全量 `pytest tests`（`PYTHONPATH=src`、仓外 basetemp、`--ignore=tests/humanize_eval`、deselect 三条本机挂死的 `test_start_fails_*`；用 `scripts/run_final_regression.cmd` 脱离 shell 跑，避免 shell 重置把 pytest 杀掉）→ **249 failed / 3741 passed / 39 skipped / 3 deselected**（19:27，日志 `raw/full-branch-4.txt`，进度字符统计与摘要一致：3741 + 39 + 249 = 4029）。与 main 基线（249 failed / 3717 passed / 39 skipped / 3 deselected，选中 4005 条）**失败名称双向 diff 为空**：没有新增失败、也没有消失的失败；本票选中用例 +24，通过数 +24 也全部来自本票新增用例。
- 前端（`apps/web`）：`npm run typecheck` 干净；`npx vitest run` → 24 文件 / 201 用例全过；`npm run lint` 0 error（3 条既有 warning：`search-page-client.tsx`、`ImageTaskCard.tsx` 的 `<img>` 与 `Composer.tsx` 的依赖项提示）。
- 静态：`ruff check src/bridges/tieba tests/tieba` → All checks passed；`mypy src/bridges/tieba` 在本票文件 0 条（同命令下 20 条全部落在既有 `chat/graph.py`、`chat/service.py`、`video/`、`runtime/`、`mcp/`、`image/`、`contracts/career.py`、`chat/turn.py`、`chat/checkpoints.py`）。

**集成注意事项**：无数据库迁移、`SCHEMA_VERSION` 不变（仍 58）。**契约有变更**：`TiebaCandidateLink` 新增 `unconfirmed_reason`，因此 `openapi.json` 与 `packages/contracts/src/generated.ts` 已按新代码重新生成（合并时两者必须一起进）；`needs_official_check` 的描述补上「放假安排」。改动落在 `src/bridges/tieba/`、`tests/tieba/`、贴吧结果卡、上述两个生成文件。**有意的语义变更**：早先一次可重试的查询失败不再把整轮报成失败（只有终态为错误、或没有确认帖子时才 `retryable=True` 并透出 `error_code`／`error_message`），对应任务 2「把『没有可用候选』与『请求失败是否可重试』分开」；两面行为分别由既有 `test_search_failure_keeps_query_and_is_retryable` 与本票新增的 `test_retryable_transient_failure_still_uses_the_fallback_query` 固定。剔除记录现在跨轮去重（同一条链接不列两遍），正文的空态构成写「非帖子链接 N 条、重复链接 M 条」，若前端自行拼接空态文案需同步。

**合并边界（本记录写就时 main 已前进）**：本分支基于 `main` = `a15a084`，而 main 现为 `f77e8e2`（并行票 01／04 已合并，其中 01 占用了迁移号 59，`SCHEMA_VERSION` 现为 **59**）。前面的「仍 58」指本票自己不带迁移、合并后 `SCHEMA_VERSION` 等于主分支当时的值；合并本票前需先 `git merge main` 并把两侧的 `openapi.json` 一起对账（main 侧自分支点起也有 +60 行改动），然后按 `scripts/regenerate_openapi.py`（带 `PYTHONPATH=src`）与 `npx openapi-typescript` 重跑一遍，确认贴吧侧的 `unconfirmed_reason` 与 main 侧契约变更同时存在于同一个生成文件里。

**未完成事项**

- 真实检索两轮都没有可用帖子候选（本机 Tavily 对「华东交通大学吧 中秋节放假」返回的全部是官网／其他高校通知／用户主页），因此本轮没有「真实确认帖子 → 读取楼层」的正向证据；该路径由既有替身用例（`test_confirmed_thread_is_read_with_floors_times_and_sections`）与 Issue 14 的真实样本覆盖，不伪造成已通过。
- 本机对贴吧帖子页直读一律 403（Issue 14 已记录），真实帖链降级为「仅帖链」的原因在真实数据上仍只能看到「页面不可读：访问受限」这一类。
- 与 Issue 05（GitHub）同源的「按字删结构词」问题只在本票的贴吧词表内修正（方位类单字），通用中文解析未重写（按票面约定）。

**提交版本**：实现 `9d83bf2`（保留原句主题词 + 有界备用检索）→ 第一轮两轴评审修正 `5c39232` → 第二轮两轴评审修正 `4488868`（候选帖链来源与原因分列、剔除记录跨轮去重、契约重生成）；本记录随 docs 提交一并进库（即本次提交）。分支 `codex/06-tieba-research`，基于 `main` = `a15a084`（本地分支，未合并、未推送）。

### 合并、合并后验证与清理实证（2026-09-27）

- **合并**：分支先并入当时 main `91e6818` 得 `f5c4e36`（**零冲突**：`openapi.json` 与 `generated.ts` 两侧改动由 Git 自动合并成功），随后 **no-ff 入 main `de74153`**；`git rev-parse de74153^{tree}` == `git rev-parse codex/06-tieba-research^{tree}` == `1376f716…`，即合并树与已验证的分支树是同一棵树。
- **契约对账（本记录第 99 行要求的那件事）**：合并后重跑 `PYTHONPATH=src scripts/regenerate_openapi.py`（299 paths）与 `npx openapi-typescript`，**重生成结果与自动合并结果逐字节一致**（`git diff` 无内容差异）；`openapi.json` 同时含 main 侧变更（画像对账 `AtomicProfileReconciliationEntry`／`reconciliation` 等）与本票的 `unconfirmed_reason`，`packages/contracts/src/generated.ts` 同（两侧标记各计数 1／3）。前端 `tsc --noEmit` 干净、`npx vitest run` **24 文件 / 203 例全过**。
- **合并树定点**：`tests/tieba tests/web_search tests/contracts` **215 passed / 2 skipped**；`tests/chat tests/plugins` **140 failed / 541 passed**（与 main 并入工单 04 后的既有基线一致）。
- **合并树全量**（`PYTHONPATH=src`、仓外 basetemp、不 ignore、不 deselect、`-q --tb=no -rf`）：**251 failed / 3813 passed / 39 skipped**（20:53，日志 `.tmp/issue06/raw/full-merged.txt`）。对照取工单 03 分支的全量（其代码树即合并前 main `91e6818` 的代码树）：**251 failed / 3776 passed / 37 skipped**；失败名称**双向 diff 为空**（各 251 条，名单 md5 相同 `53c5978e…`）。账目对平：Δ收集 +39 = 本票 +24（`tests/tieba` 45→69）+ 工单 05 的 +15（`tests/github` 45→60，基线树不含该票；合并树实测 `tests/tieba tests/github` 收集 **129** = 69+60）；Δ跳过 +2 = 两条 `NEEDS_WEB_BUILD`（本工作树无生产构建，基线树有）；Δ通过 +37 = 24 + 15 − 2。
- **合并后 main 定点**（主仓树有 `.venv` 与生产构建）：`tests/tieba tests/web_search tests/contracts tests/closeout/test_api_boot.py tests/chat tests/plugins` **140 failed / 758 passed / 2 skipped**（440 s，日志 `.tmp/issue06/raw/postmerge-main.txt`）。140 条失败与本票合并树全量里的 chat／plugins 子集**逐名双向 diff 为空**，且**全部落在**上述 251 条基线名单内（新增失败 0）；`tests/closeout/test_api_boot.py` 两例在主仓树上通过。
- **推送**：`git -c http.proxy=http://127.0.0.1:7890 push origin main` → `91e6818..de74153`，**main == origin/main `de74153`**，远程仅 `refs/heads/main`。
- **清理**：`apps/web/node_modules` junction 用 `[System.IO.Directory]::Delete($false)` 拆链（主仓 `node_modules` 361 条不变）；`.worktrees/06-tieba-research` 用**普通 `git worktree remove`（未用 --force）一次成功**、目录已消失；本地分支 `codex/06-tieba-research` 已删；`git worktree prune --expire now` 后 `prune --dry-run -v` 无输出、`git worktree list` 只剩主仓、`.git/worktrees` 已不存在、`.worktrees/` 目录为空。**本票运行都用了仓外 basetemp，工作树里没有沙箱 ACL 目录**，所以这次没有触发提权删除。
- **迁移**：本票无迁移，`SCHEMA_VERSION` 仍 **59**、键 1–59 连续（合并后 main 实测）。
- 留档新增：`.tmp/issue06/raw/full-merged.txt`、`merged.names`、`ref-main.names`、`postmerge-main.txt`、`postmerge-main.names`，脚本 `.tmp/issue06/scripts/run_merged_regression.cmd`。

## Comments

**第一轮两轴评审（标准 + 规格）后的收尾**

- 标准轴：① `_official_candidates` 的排序键返回 `(tier, 0)`，第二维恒为 0 —— 改为单层 `int` 排序键，并把参数名 `current_year` 改成 `preferred_year`（它实际是「问题里的年份，问题没写才用当前年份」，旧名会让下一步误当成「当前年份」）；② `classify_hits_with_diagnostics` 用 dict 当计数器 —— 改为普通整数；③ `HOLIDAY_ARRANGEMENT_TERMS` 原收 8 个词（含返校／开学）—— 收窄为放假安排本身的 6 个词，返校／开学不属于本票范围，收窄同时避免把官方核验扩到所有学期节奏问题；④ 补两条边界用例：两轮完全零结果走到空态第三种写法、首轮之后用户停止时备用词不发出并写明「因你已停止」；⑤ 测试文件 import 顺序按 ruff 修正。
- 规格轴：① 记录里「官方命中 5 条」「旧活动页曾排首位」当时没有留档 —— 补做 `scripts/official_probe.py`（输出 `raw/official-probe.txt`）实测补证，并把该反例固定进 `test_official_candidates_prefer_topical_current_year_pages`；② 「过滤原因样例」原按真实检索口吻写，其中他吧证据实际来自替身样本 —— 改为标注来源，本轮真实检索确实没有他吧命中；③ 终止态用例的措辞与适配器实际行为对齐（适配器不会自己造出 `timeout`／`rate_limited`，真实超时是 `ERROR` + 可重试）；④ 测试条数与全量回归数字按重跑实测刷新。

**第二轮两轴评审（对修好的代码再跑一次）后的收尾**

- 标准轴与规格轴同时指出候选帖链把原因文案塞进了 `source` 字段（用户可见文案里还会带出提供方名）—— 新增 `TiebaCandidateLink.unconfirmed_reason`，`source` 回到「检索来源标识」，正文与前端按「来源；未确认原因；归属未确认」分列；顺带补上 `needs_official_check` 的描述（少了「放假安排」），并按新契约重新生成 `openapi.json` 与 `packages/contracts/src/generated.ts`。
- 规格轴指出「去重仍有效」这一条在跨轮时没有生效：同一条非帖子链接会在两轮里各列一次，空态还把重复命中算成两条不同链接（真实检索里确实出现过 `bksy.ecnu.edu.cn/.../page.htm` 列两遍）。改为剔除记录跨轮去重，`_deduped_diagnostics` 让「各计数之和 = 原始命中数」（多出来的记入「重复链接」），并补用例 `test_repeated_links_across_rounds_are_listed_once_and_counted_as_duplicates`；真实检索复跑后剔除记录由 10 条变 8 条、正文写「非帖子链接 8 条、重复链接 2 条」。
- 标准轴另提的三条（`classify_hits` 现只有测试调用、`READ_BLOCK_LABELS.get` 的兜底分支不可达、触发词文案在五处各写一遍）本轮不作改动：前两条属既有代码、改动会超出本票范围且没有行为收益，第三条是稳定的中文说明文案，抽公共常量会把不同界面的措辞绑死；均已在本记录留痕，供后续票判断。
