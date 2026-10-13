# 贴吧话题研究独立验收（2026-10-12）

> 本报告记录 Tieba 模块拆分前的独立验收快照；其中的行数和验证计数不代表当前工作区。后续状态以工单“2026-10-12 后续实施与复验”补充记录为准。

**结论：不通过，保留修复，未提交、未推送。** 局部实现和回归已改善，但认证确认、回复及楼中楼分页、真实身份、逐请求账本和生产模型完整旅程仍缺少达标实现或证据。不得将工单改为完整实施，也不得用本地 HTML 或假模型的通过数代替真实验收。

本轮以[工单](issues/01-topic-driven-tieba-research.md)、[ADR-0040](../../docs/adr/0040-topic-driven-tieba-collection-and-auto-login.md)、用户提供的全局规范及仓库 AGENTS.md 为依据。交接报告仅用于定位。审查固定点是实现前远端 main `11623228dd1f382f91b9677d89589afe0a79aa83`；初审对象为 `65d00aa1`、`d1bbc94d`，复审包括本轮工作区及新增文件。

Python 开发、静态检查和测试使用 `C:/Users/33755/anaconda3/envs/agent/python.exe`。前端验证把 agent 与 Scripts 加入 PATH。HTTP 子进程验收须另外设置 `BRIDGES_PYTHON` 为上述解释器；否则本仓库已有 `.venv` 会被部分测试辅助脚本优先选中。

## Standards

code-review 规范轴由独立子代理初审及只读复审，初审 3 项硬违规、1 项判断性气味，分别保留判断：

1. 文件规模硬违规：新增 `summary.py` 原为 518 行，已按素材转换与执行职责拆为 410 行和 `summary_material.py` 135 行。其他超长文件仍未关闭，详见下表。
2. 模型审计硬违规已关闭：网关返回锁原先未持久化；现在每次调用经运行独立回调保存现有模型锁与消息业务引用，绑定账户及运行、保留调用序号。真实 SQLite + 完整节点链的 3 项测试确认三阶段锁落库及错误账户、错误运行锁拒绝。预调用拒绝且无锁时不制造审计锁。
3. JSON 原始字符串截断硬违规已关闭：移除 `line[:100_000]`，超过 100k 字符的有效 JSON 不再整份失效。原文预算仍在采集/投影边界控制。
4. 判断性气味仍保留：Possible Divergent Change，`kernel.py` 同时承担预算、引用追溯、模型调用和认证恢复。该气味不是工具可直接判定的硬违规。

原实现前 main 与当前行数独立统计如下，旧超限不全部归因本票，但新增职责也不能据此豁免：

| 文件 | 基线 | 当前 |
| --- | ---: | ---: |
| api/main.py | 2343 | 2344 |
| closeout/manifest.py | 944 | 944 |
| kernel/executor.py | 664 | 673 |
| tieba/evidence.py | 1194 | 1543 |
| tieba/kernel.py | 1456 | 1612 |
| tieba/service.py | 753 | 931 |
| tieba/summary.py | 无 | 410 |
| tests/kernel/test_node_kernel.py | 827 | 878 |
| tests/tieba/test_browser_retrieval.py | 127 | 509 |
| tests/tieba/test_improvement27_question_driven.py | 625 | 644 |
| tests/tieba/test_tieba_core.py | 476 | 509 |

规范轴：2 项硬违规完整关闭，文件规模项部分关闭，1 项判断性气味保留；最重的剩余硬缺口为文件规模与职责边界。

## Spec

code-review 需求轴独立初审 7 项具体缺陷，复审确认其中 3 项关闭、4 项未关闭，保持与规范轴分开：

1. A1/A3 未关闭：`browser_worker.official_login` 仍依据返回贴吧且出现可读 DOM 判断成功，没有独立真实认证响应；正常搜索也没有先取得独立认证状态。公开可读页面不能证明已认证。
2. B2 具体校区/别称缺陷已关闭：读取边界过滤明确其他校区及无依据发言，保留无冲突父上下文；检索别称用于复核实际内容，校区名本身不能充当话题命中。完整语义筛选仍须真实旅程验收。
3. B6 默认来源缺陷已关闭：搜集讨论不再因“转专业”“住宿费”等词自动追加官网，明确确认/核实请求仍保留官网路径。
4. B4/B5 未关闭：搜索仍每个标签第一页至多 12 条；帖子仍最多 8 轮滚动，没有回复或楼中楼分页读取、折叠补充读取和可靠完整状态。真实嵌套发言存在缺失身份。
5. D2 未关闭：账本按一次搜索/读取端口登记，不能对应 worker 内各次页面请求、跳转和加载请求，也没有经真实验证校准的逐请求节奏。
6. E1 未关闭：真实搜索、回复搜索和原帖 DOM 证明局部路径；没有专用会话真实认证、真实模型总结和默认/明确时间、多帖、回复独有、纠正楼中楼完整场景矩阵。
7. B3 猜年份缺陷已关闭：缺少年份保持未知，不再以检索日猜为今年；真实原始日期保留。

本轮另发现并关闭锁等待期间取消、浏览器启动期间取消后仍可能发送旧请求的竞态；先复现 3 项失败，再修复，相关浏览器 22 项通过。另修复真实搜索新容器 `.thread-new3`，同步等待谓词及标题/摘要提取；本机 Chrome 执行本地脱敏 HTML 的 3 项回归通过，另有真实页面证据如下。

需求轴：初审 3 项关闭、4 项仍未关闭；最重剩余问题为独立认证确认与分页/身份采集。

## 工单逐项结论

“代码/夹具通过”仅说明可重复行为验证，不表示真实端到端通过。

| 项 | 独立结论 | 证据与限制 |
| --- | --- | --- |
| A1 专用会话与隔离 | 部分 | 账户绑定 reader、哈希目录、账户串行锁有代码/回归；未先独立核验认证。Edge 个人会话不替代专用会话。 |
| A2 自动登录与续接 | 不通过 | 自动打开/等待路径存在，但认证成功判定不足；完整真实专用会话旅程未取得。 |
| A3 认证与采集分开、凭据保护 | 部分 | 凭据未进入本轮公开材料；认证与采集可读性未独立记录。 |
| A4 验证、未知状态与重试 | 部分 | 超时/取消有稳定错误码；验证路径仍打开登录页，真实验证矩阵未通过。 |
| A5 并发入口、关闭、继续、停止 | 部分 | 账户互斥、恢复/预算及取消回归；窗口关闭、并发认证实际行为未完整验证。 |
| B1 固定目标吧、真实确认 | 部分 | 真实原帖 DOM 确认目标吧；归属过滤有回归，覆盖为有限样本。 |
| B2 扩词、回复命中、明确条件 | 部分 | 校区/同义词具体缺陷关闭，真实回复检索取得 5 个候选；完整语义筛选未实测。 |
| B3 默认/明确时间 | 部分 | 时间窗口及缺年未知回归通过；真实原帖月日缺年，不能据此证明明确时间旅程成功。 |
| B4 多帖覆盖与深读 | 不通过 | 真实主题搜索 9 个候选；仍无回复/楼中楼分页，不能替代多帖完整旅程。 |
| B5 身份、关系、原文与纠正 | 不通过 | 真实父子内容可读，12 条嵌套发言缺稳定 ID，折叠的补充未取得；去重不能以文本回退冒充真实身份。 |
| B6 官网仅按明确意图追加 | 代码/夹具通过 | 13 项新增条件回归及关联时间/核心/检索合计 53 项通过；来源合同 3 项集成复验通过。 |
| C1 连贯中文语义总结 | 部分 | 有网关整合及支持核验回归；未用真实模型完成本票旅程。 |
| C2 额度、分批、关联与隔离 | 部分 | 额度门、分批及审计锁回归通过；真实模型额度、分批质量未实测。 |
| C3 可追溯引用与支持核验 | 部分 | 越界/身份/原文匹配及假模型核验回归；真实嵌套 ID、时间缺失，生产支持度未验收。 |
| C4 折叠原文、范围与进度 | 代码/夹具通过 | 结果卡回归及前端全部单测/类型/构建通过；依赖采集真实状态的完整旅程仍待验证。 |
| D1 五分钟累计与人工等待 | 代码/夹具通过，真实部分 | 共享预算及恢复回归通过；真实登录/模型旅程未校准总耗时。 |
| D2 每次请求账本与节奏 | 不通过 | 目前是端口操作账本粒度，多次浏览器请求未逐次登记。 |
| D3 部分失败、总结失败与证据 | 代码/夹具通过，真实部分 | 长 JSON、失败保留、无总结及支持核验闭锁验证；真实模型失败未复验。 |
| D4 守卫、停止、条件变更 | 代码/夹具通过，真实部分 | 内核与预算复验、两处取消竞态已修；独立认证等待的实际完整矩阵尚缺。 |
| D5 版本与历史合同 | 代码/夹具通过 | 配方 v5；parse/plan v5、search v8、read v7、synthesize v6；模型锁配方同源 v5；节点恢复/合同回归通过。 |
| E1 完整真实记录 | 不通过 | 只有有限真实 DOM，未将其声称为完整专用会话/真实模型成功。 |
| E2 可重复失败/恢复矩阵 | 部分 | 相关复验 254 通过；认证协议/分页/身份仍有缺口，夹具不能覆盖不存在的真实行为。 |
| E3 环境、依赖、范围和规模 | 部分 | agent 环境、无新增运行依赖、改动限本票；超长文件约束仍不通过。 |
| E4 独立业务实现 | 审查未发现违规 | 抽查差异/依赖未发现引入参考业务客户端或搬运函数。参考仅核对接口事实，不能靠审查绝对证明原创历史。 |
| E5 完整检查、构建和合同 | 执行完成，结果部分 | 完整检查非全绿，失败基线分析见下；前端与 OpenAPI 同步通过，无数据库改动，不需迁移。 |

## 真实观察与模拟区分

2026-10-12 用户提供 Edge 远程调试 `127.0.0.1:60918`。Codex Edge 连接器未取得标签页；通过 web-access 调试代理 `localhost:3456` 后取得本票新建页面，未复制个人 Cookie、未把个人会话作为 BridGes 专用账户认证。

- 主题“宿舍”的真实搜索页有新容器、无旧 `.tbm-post-thread`；修复后生产 `SEARCH_DOM` 实际取得 9 个候选。
- 回复搜索真实取得 5 个候选，包括[宿舍讨论帖](https://tieba.baidu.com/p/9866537003)等；这只证明候选发现，不把摘要计作正文证据。
- [传奇五栋六栋](https://tieba.baidu.com/p/11084772666)的生产 `THREAD_DOM` 取得 24 条发言：12 条普通楼层、12 条嵌套发言；页面声明 28 条回复，存在“展开 2 条回复”，未完整读取。嵌套 DOM 无 `data-id`/`data-spid`，12 条缺 ID；日期为 `10-07` 等，不猜年。发言包含“住一年后搬走”与“住四年未搬”的不同说法，但没有据此提交生产模型总结。
- 对工单引用 `/c/s/pc/sync` 的**空 POST 探测**返回 HTTP 200、业务错误 `110001`。该探测缺少参考协议所述 GET 参数与签名，不能据此宣布接口整体不可用，也不能作为认证证据。签名调用及认证信号仍待独立验证。
- 本机 Chrome 本地 HTML 路由夹具、假模型返回和真实 SQLite 持久化测试均明确是自动化测试，不是实际百度认证或生产模型调用。

有限真实观察保存在 [browser-observations.json](acceptance-evidence-2026-10-12/browser-observations.json)，包含观察范围与未验证标志，不含 Cookie、令牌或账户认证值。

## 验证与基线归因

全仓测试在修改前启动；修复后用全部相关范围复验，不能把初轮全仓结果称为最终修复树的全仓结果。基线是 `git archive 11623228` 的隔离快照；单独 pytest 来源断言证实加载快照的 `src/bridges/tieba/parsing.py`，不是 editable 安装指向的当前源码。

| 检查 | 本轮结果 | 实现前 main / 归因 |
| --- | --- | --- |
| 初轮全仓 pytest | 5801 passed / 206 failed / 37 skipped，25:42 | 5761 passed / 206 failed / 39 skipped，25:13 |
| 失败标识对比 | 204 个共同失败；当前独有 2、基线独有 2 | 当前独有为两个 API 启动用例；显式 `BRIDGES_PYTHON=agent` 后均通过。基线独有为两项证据报告用例，当前初轮通过。不能仅按同样 206 总数豁免。 |
| API/邮件的 agent 子进程复验 | 2 passed / 3 failed | 两项启动通过；邮件 3 项恢复为与基线相同的提醒 API 已退役 410 断言失败。 |
| 修复后全部相关 pytest | 254 passed / 3 failed，51.58 秒 | 两项贴吧聊天模块路由断言与 22 个未分类路由在两轮完整测试均失败；本票相关暂无已确认新增代码测试失败。 |
| 全仓 mypy | 154 errors / 25 files | 基线同为 154/25；忽略因改动产生的行号后诊断集合一致。 |
| 全仓 Ruff | 520 errors | 基线原始检查同为 520；按路径/规则/消息/重复次数比较无本次新增。基线来源断言探针不属于原快照，比较时明确排除。 |
| 贴吧源码静态检查 | Ruff、mypy 19 个源码文件、compileall 通过 | 新回归文件 Ruff 通过。 |
| 环境依赖 | pip check 通过 | No broken requirements found。 |
| 前端 | 29 文件 / 253 单测；typecheck、build 通过 | 3 条既有 Next 警告，非本票文件。 |
| OpenAPI | 同步测试通过 | 本轮未变公开字段，不需重生成 API/TS；无 schema/存量数据迁移。 |

完整测试共同失败并非每个诊断字符串都相同：邮件最初子解释器选择错误已复验；秘密扫描还受工作区既有 `.codex-test-tmp` 与本轮临时副本影响；报告清单版本断言原期望 4，基线实际 5、本票实际 6；其余已抽查的差异含随机临时路径、对象地址、集合显示次序。秘密扫描临时副本清理后复验结果另记工单，不删除用户既有未跟踪目录。

清理后秘密扫描仍失败，仅剩两处既有命中位置：`.codex-test-tmp/pytest-all-followup-20261011/test_report_scan_fails_closed_0/report.json:1`（既有测试残留）与 `scripts/issue42_pairing_runtime.py:33`（实现前快照也命中）。本轮基线副本及测试目录产生的两处额外命中已消失，没有输出凭据值。结果见 [security-final.txt](acceptance-evidence-2026-10-12/security-final.txt)。原始全仓 XML/诊断保留于 gitignore 的 `.tmp/tieba-independent-acceptance-20261012/`，脱敏证据留在本目录，便于复核且避免把测试临时材料混入提交。

复验命令（PowerShell，根目录执行；每个并行 pytest 必须使用不同 basetemp）：

```powershell
$env:BRIDGES_PYTHON = 'C:\Users\33755\anaconda3\envs\agent\python.exe'
& $env:BRIDGES_PYTHON -m pip check
& $env:BRIDGES_PYTHON -m pytest -q --basetemp=.tmp/tieba-full-recheck
& $env:BRIDGES_PYTHON -m pytest -q tests/tieba tests/kernel/test_node_kernel.py tests/chat/test_improvement09_run_budget_ledger.py tests/contracts/test_openapi_sync.py tests/closeout/test_capability_manifest.py --basetemp=.tmp/tieba-related-recheck --tb=short
& $env:BRIDGES_PYTHON -m mypy src
& $env:BRIDGES_PYTHON -m ruff check src tests
& $env:BRIDGES_PYTHON -m mypy src/bridges/tieba
& $env:BRIDGES_PYTHON -m compileall -q src/bridges/tieba
```

前端在 agent PATH 下从 `apps/web` 执行 `npm run test:unit`、`npm run typecheck`、`npm run build`。真实 DOM 观察不由上述 pytest 自动取得，重验时必须重新接通调试代理并区分专用会话与个人 Edge 会话。

脱敏清单与比较结果保存在 [验收证据目录](acceptance-evidence-2026-10-12/)。首次基线运行因快照不含 `.tmp` 父目录产生 2032 个环境错误，另有并行默认 basetemp 冲突；这些轮次作废，未计入上述通过数。之后均显式使用独立临时目录。

## 修复清单与尚需实施

本轮已修复明确校区与同义词筛选、默认来源意图、缺年日期、长 JSON、两处取消竞态、模型逐次审计、新搜索 DOM，并升级语义版本。新增回归验证边界行为及实际 SQLite 持久化；三个旧来源测试按当前工单更新意图与断言，保留官网明确核实路径覆盖。

继续实施必须闭合：独立真实认证及验证状态；搜索/回复/楼中楼分页和稳定身份、完整时间；逐请求账本/节奏；当前 BridGes 账户的专用会话与生产模型场景矩阵；本票范围模块/测试的 500 行边界。当前缺口既包含未实施代码，也包含外部真实会话/模型验收资源，不能全部归为“浏览器连不上”。

**提交/推送状态：** 本地 HEAD 保持 `d1bbc94d2636d7251171198df8280775aa4675a8`，原有两个实现提交仍未推送，本轮修复在工作区。通过系统代理 `http://127.0.0.1:7890` 两次 `git ls-remote origin refs/heads/main` 均核对为 `11623228dd1f382f91b9677d89589afe0a79aa83`。工单尚未达标，未触发用户“确认达标后提交并推送”的条件。

## 2026-10-12 后续 Edge DOM 观察

用户确认 Edge 远程调试已开启后，本轮通过显式 CDP 会话 `codex-tieba-acceptance` 连接 `127.0.0.1:60918`，读取公开 Tieba 搜索页和公开帖子 `https://tieba.baidu.com/p/10915738722`。个人 Edge 页面不等同于 Bridges 专用会话；未读取 Cookie、存储或请求头。此前未取得标签页的记录是连接成功前状态，由本节更新。

帖子回复的滚动容器为 `.pc-pb-box.styled-scrollbar.deep`，其内部 `scrollHeight` 约 2741、可见高度约 524。滚动内层容器到底后，观察到 5 个父楼 ID 与 11 条楼中楼行，往返不同滚动位置时样本集合没有变化；页面仍显示“全部回复 (24)”。嵌套行没有可用的 `data-id` / `data-spid`，父楼有 `data-id`。这证明应滚动内层容器，但无法证明页面已加载全部 24 条；没有发现已实现的分页读取或折叠回复补充机制。

为此新增 `test_thread_scrolling_targets_the_inner_reply_container`，在本机 Chrome 回归中通过。后续又观察到 `.show-more-lzl` 可展开隐藏回复，worker 已增加有进展检查的展开循环，并新增 `test_thread_expands_collapsed_nested_replies`；本机 Chrome 的搜索 DOM、内滚和展开测试共 **5 passed**。此修复读取可见的折叠组，不证明分页/总量完整，也不补造缺失身份。真实认证、完整回复身份、专用会话及生产模型旅程仍未验收。

### 后续复验结果

后续改动后的相关范围（`tests/tieba`、节点内核、运行预算、OpenAPI 同步、能力清单）**284 passed**。当前改动范围 Ruff 通过，Tieba 与拆分后的 manifest 严格 mypy **38 个源文件无问题**，`compileall` 及 `git diff --check` 通过。另一次全仓运行报 **5831 passed / 203 failed / 37 skipped（26:07）**；它在最后的校区主题筛选、测试支持解耦和楼中楼展开改动前启动，不能代表最终树全仓通过，失败仍待完整归因。

展开公开帖子后，页面声明“全部回复 (24)”且页脚显示“已加载全部评论”，但 DOM 仅含楼层 3–9 的 7 条父楼和 14 条楼中楼；可见总数与声明值不一致。嵌套行仍无稳定 `data-id` / `data-spid`。该观察保留为采集不完整/页面计数待解释的证据，不能据此标记分页完成。

## 2026-10-12 后续实现与验证补记

账户删除增加了持久化 profile fence：删除开始及后台重试均先写 `.account-deleting`，运行时和 worker 在账户锁边界复核；worker 空闲时也检查标记。profile 清理保留 `.bridges.lock` 和 fence，避免旧 worker 重建 profile 与 OS 锁文件被删除后 inode 分裂；fence 或清理失败时保留账户数据供重试。模型审计记录的预算测试改为核对实际调用结果状态。服务审计失败会持久化失败投影后继续向调用方抛出 `TiebaModuleError`；回归测试现覆盖该合同。

本轮最终相关范围使用 `agent` 环境并在沙箱外通过：`tests/tieba` **195 passed**，`tests/lifecycle` **74 passed**，Tieba、生命周期、节点内核、预算账本、OpenAPI 同步和能力清单联合 **352 passed**。其中 5 个既有生命周期 API 用例仍调用已退役的空会话创建端点；仅将测试 fixture 改为当前服务层创建，未改动产品 API。定向浏览器删除与预算回归 **30 passed**。当前改动范围 Ruff、Tieba/删除严格 mypy（34 个源文件）、`compileall`、`pip check` 与 `git diff --check` 通过。前端 29 文件/253 单测与 typecheck 通过，生产 build 本轮复验通过；构建需沙箱外执行，因为沙箱内 Node 的 `fs.promises.realpath` 对 TypeScript 包返回 `EPERM`，阻断 Next 加载 tsconfig 路径。构建有 3 条既有 Next lint 警告。

全仓 pytest 当前最终复验仍在运行；此前 **5836 passed / 206 failed / 37 skipped** 的全仓结果早于本次删除 fence 与测试 fixture 修复，不能代表最终树。最终是否有新增全仓失败须按本轮完整结果与固定 main 基线比较。

仍未通过的内容：没有独立上游认证信号和专用会话真实验收；公开页面不能证明搜索、回复和楼中楼分页完整，也没有稳定嵌套发言身份/时间；逐请求账本的代码已补、真实节奏和任务守卫尚未验收；没有真实生产模型/额度的聊天闭环；API、后台执行器和通用账本仍有遗留超长文件。后续用户确认贴吧账号与生产模型已准备，但当前 Edge/CDP 连接器未返回标签页，本轮无法完成登录、模型和真实采集验收。工作未提交或推送。

## 2026-10-12 本轮逐请求账本与生命周期接线

Tieba worker 现在通过双向事件协议对每个百度 `document`、`xhr`、`fetch` 请求先请求账本放行，再在结束时登记 HTTP 结果与耗时。账本仍使用当前账户和运行的 `RunBudgetLedgerRepository`；原始 URL、参数、请求头、正文留在 worker，静态脚本、图片等资源按非采集数据请求排除。新增浏览器事件门控、拒绝、结果、时长落库及请求预算不重复计数回归。真实页面节奏、并发、人工认证等待、停止与任务版本守卫仍未实测，D2 不标记通过。

账户删除的 Tieba 会话生命周期从 API 与后台执行器的内联回调改为 `TiebaAccountSessionLifecycle` 适配器，API 使用活动运行时，后台重试使用相同数据目录的持久化 profile fence。新增适配文件均低于 500 行；遗留共享文件 `api/main.py`（2351）、`runtime/executor.py`（637）、`chat/run_budget_ledger.py`（1207）仍超过上限，因此 E3 仍部分通过。

agent 环境相关联合回归 **353 passed**；应用启动/生命周期接口另 **16 passed**；全仓 Ruff 与固定 main 基线归一化比较没有新增诊断（基线 520、当前 519），全仓 mypy 与基线相同为 154 项且诊断集合一致；`pip check`、`compileall src`、`git diff --check` 通过。前端单测 **29 文件/253 项** 通过，typecheck 已完成，生产构建待本轮最终结果。最终全仓 pytest 正在运行，失败数待完整归因。

## 2026-10-12 实际验收续测与搜索预算调整

用户确认贴吧账号和生产模型已准备；该确认不替代专用 worker 会话的上游认证证据。Edge 远程调试 60918 经 web-access 3456 正常工作，本轮只在自己创建的后台标签读取公开 DOM，没有读取 Cookie、站点存储或请求头。精确搜索词 `宿舍 收费 热水 洗澡` 在公开页得到 0 个帖子；计划放宽词 `宿舍 热水 洗澡 贴吧` 得到 7 个帖子，页尾显示“暂无更多数据”。可见例子为 `10405678421`（2026-01，“花椒为啥洗澡热水这么贵”）和 `10018550387`（2025-09，“求助怎样热水洗澡”，超出默认近 12 个月）。脱敏记录见 [edge-run-recheck.json](acceptance-evidence-2026-10-12/edge-run-recheck.json)。这只证明公开候选发现，不证明搜索分页完整或专用会话已认证。

本地 BridGes 同源 `/api/auth/session` 返回 HTTP 200，但页面 UI 仍停在“正在验证当前账户”。一次首轮只读验收返回 HTTP 201，运行 `z6LEhDBH6TVTEUcIZTVgPw`、对话 `EfcyGSa6HZQLysJ-4G1emg` 完成；其中第一轮搜索成功但 0 个候选，第二轮 `tieba_browser_timeout`，最终 Tieba 状态为空，`model_id` 和模型运行锁均为空。模型没有被调用，所以真实生产模型仍未验收。

按公开 Edge 页面验证，放宽轮确有候选，而该次 BridGes 运行在抵达放宽轮前超时。搜索阶段上限由 25 秒改为 50 秒，使可重试超时后仍有机会执行放宽搜索，并为 5 分钟任务其余阶段保留至多 250 秒。新增的时间预算回归模拟每轮 15 秒，证明成功空结果与可重试超时后仍进入放宽轮。`tests/tieba` 在沙箱外最终 **205 passed / 47.17 秒**；Tieba 严格 mypy **37 个源文件无问题**、Tieba Ruff、Tieba `compileall` 通过。沙箱内测试会在 Windows asyncio/Playwright 建立本地 socketpair 时挂起，沙箱外同一测试通过。

同幂等键额外 POST 被自动审查拒绝，因为首个请求已返回 201，重复提交可能新建对话/运行并消耗模型额度。之后只通过 GET 查询状态，没有重发；若要做新的生产模型请求，需要用户明确批准一条新的独立验收请求。当前真实模型、专用认证、回复分页/稳定身份、真实节奏及任务版本守卫仍不通过。

目前改动的 Tieba 专用源码与测试都低于 500 行，但 3 个修改过的共享文件仍超限：`src/bridges/api/main.py`（2351）、`src/bridges/chat/run_budget_ledger.py`（1207）、`src/bridges/runtime/executor.py`（637）。E3 仍部分未通过。没有前端源码改动；本轮生产构建重跑长时间无完成输出且没有生成新 `.next/BUILD_ID`，已中断本次尝试；前一轮 build 已通过的记录保持为此前证据，不作为本轮新结果。

## 2026-10-12 用户批准的一次新模型验收尝试与模块拆分

用户批准“批准一次”后，使用新幂等键 `codex-tieba-e2e-20261012-04` 发起了**恰好一次**新请求，主题词为“宿舍／热水／洗澡”，目标仍为华东交通大学吧。BridGes 返回 HTTP 201，对话 `5hwe2A1vxFswGEKXGgTXcg`、运行 `KloFg3I6WfnGAA1fvD63KQ`。只读 GET 确认助手消息结束，`error_code=tieba_browser_timeout`、`model_id=null`；没有证据表明进入模型阶段。尝试读取更细的查询投影时，任务标签的 `Runtime.evaluate` 超时，故本次候选数和具体失败查询无法确认。未重发 POST。

为闭合 E3 中可在仓库内完成的文件规模项，后台执行器已将媒体服务构建和过期附件清理拆到独立模块；运行预算账本现由纯类型/计划、存储、调用记账、批次记账模块组成，`run_budget_ledger.py` 保留稳定公开导入入口。现在这些文件均小于 500 行：`runtime/executor.py` 491、`runtime/executor_media.py` 138、`runtime/executor_cleanup.py` 27、`chat/run_budget_ledger.py` 59、`chat/run_budget_ledger_types.py` 429、`chat/run_budget_ledger_storage.py` 323、`chat/run_budget_ledger_calls.py` 398、`chat/run_budget_ledger_batches.py` 93。`src/bridges/api/main.py`（2351 行）仍超过约束，E3 仍部分未通过。

拆分后的预算账本、后台执行器与附件清理定向回归 **54 passed**；拆分账本 5 个 Python 模块严格 mypy 通过，Ruff 通过。真实生产模型仍未调用；专用贴吧认证、完整搜索/回复分页和稳定发言身份、实际请求节奏及任务版本守卫仍待真实专用会话验证。已批准的次数已用完，本轮不再发起外部模型请求。

### 2026-10-12 E3 文件边界闭合与回归结果

继续将 API 组合根拆为基础装配、健康检查、路由和领域服务配置模块。`src/bridges/api/main.py` 现为 38 行；新拆分模块最大 396 行。预算账本模块最大 429 行，后台执行器为 494 行，媒体构建与附件清理模块分别为 145 和 27 行。本票修改/新增的 API、预算账本、执行器与 Tieba 模块均低于 500 行，没有增加依赖。E3 的文件规模项现通过。

完整 API、Tieba、预算账本、后台执行器及 OpenAPI 相关回归 **323 passed / 65.46 秒**。变更的 API 组合模块、预算账本与媒体/清理构建模块合计 **21 个源文件严格 mypy 通过**；变更模块 Ruff、相关目录 `compileall`、JSON 证据校验和 `git diff --check` 通过。另对 `runtime/executor.py` 单独运行 mypy 时，报告第 250 行把 `SecretStr | None` 传给要求 `SecretStr | str` 的参数；该行不属于本轮提取范围，因此没有把这个单文件检查计为通过。全仓历史检查记录仍有与基线相同的诊断，未将其表述为全仓 mypy 全绿。

真实验收方面，用户批准的一次全新请求仍以 `tieba_browser_timeout` 结束，`model_id` 为空；不再重发。状态投影的后续 GET 在浏览器代理 `Runtime.evaluate` 超时，因此没有候选/查询明细。故真实生产模型调用、专用贴吧认证、完整分页和稳定身份、真实请求节奏及任务版本守卫尚未通过。工单继续保持 `ready-for-agent`；未提交、未推送。


## 2026-10-13 Edge 会话重连复查

恢复后的桌面会话起初没有 `msedge` 进程，端口 60918 与 CDP 代理 3456 不可连接。打开 Edge 远程调试页后，web-access `check-deps.mjs --browser edge` 仍未发现调试端点；Edge 扩展也没有返回标签页。没有执行新网页/贴吧请求，也没有创建新的验收运行或读取 Cookie。此前获批的单次生产验收已于 10-12 使用并以 `tieba_browser_timeout` 结束，不重试。当前活跃 Edge 实例的调试连接尚未恢复，因此认证与生产模型等真实验收门保持未通过。

## 2026-10-13 文件边界复验与定向回归

交接所列八个超长文件当前行数为 `api/main.py` 38、`chat/budget.py` 53、`chat/graph.py` 212、`chat/run_budget_ledger.py` 59、`chat/service.py` 211、`kernel/executor.py` 91、`test_improvement09_run_budget_ledger.py` 197、`test_node_kernel.py` 464；其他相关拆分模块也低于 500 行。原 988 行聊天服务测试现拆为 413、254、272 行三个模块和 118 行共享支持模块，全部 36 个测试均保留。E3 文件规模项在当前工作区关闭。预算、账本、内核和请求跟踪 **68 passed**；聊天服务 **36 passed**；图公开要求和执行守卫 **41 passed**。相关 33 个源文件严格 mypy 通过，变更范围 Ruff 通过。更深的图恢复/终态用例无输出运行后中断，不计通过。

用户说明 Edge 远程调试已开启；初检时没有 `msedge` 进程和 60918 监听。按技能打开 `edge://inspect/#remote-debugging` 后启动 Edge，但 `check-deps.mjs --browser edge` 仍无法连接；没有读取页面、Cookie 或发起贴吧/模型请求。认证协议、实际分页/稳定身份、逐业务 HTTP 请求记账、任务版本守卫和生产模型旅程仍未完成；已批准的唯一一次模型验收已使用且超时，不重试。整体结论仍为**不通过，未提交、未推送**。

### 2026-10-13 Tavily 状态账本补充复核

新增 429/503 回归先复现了 Tavily 业务异常码覆盖已记录物理 HTTP 状态的问题，随后修复状态优先级；回归确认调用方仍收到对应错误分类，而外部请求账本保留 `http_429`/`http_503`。HTTP 超时保持单独分类。定向回归为 **199 passed / 2 skipped**，15 个相关源码严格 mypy、Ruff、`compileall` 和 `git diff --check` 通过。交接联合验收范围在沙箱内卡于 FastAPI `TestClient`；单测在沙箱外 **2.70 秒通过**，完整相关范围为 **268 passed / 48.62 秒**。前端生产构建在沙箱内外都停在 Next.js 启动行，未生成 `.next/BUILD_ID`，E5 构建项仍未完成。真实请求节奏/并发、专用认证、完整分页和稳定嵌套身份、生产模型旅程仍未验收。获批模型请求已用完；不提交、不推送。

之后的规范复审补上了慢速最终响应晚于截止时间时的拒绝分支，并增加物理账本记为 `timeout` 的回归；Tavily 2xx 正文读取失败也会留下失败结局。最终联合范围扩展为 `tests/web_search`、全部 `tests/tieba`、节点内核、账本、OpenAPI 与能力清单，共 **414 passed / 2 skipped / 54.21 秒**；相关 Ruff 和严格 mypy 通过。此前 268 项为不含新增 Web Search 全范围的子集。前端生产构建仍无完成输出或 `BUILD_ID`，D2 真实节奏/并发校准、专用认证、完整分页/稳定身份与生产模型旅程保持未验收；结论仍不通过，未提交、未推送。

截止时间复审的最终结论仍留一项约束：晚到的最终响应会被拒绝，但同步 HTTPX 的 read timeout 允许持续有数据的连接超过配置时间；当前实现不能保证墙钟 deadline 到点取消这种底层调用。故 D1 五分钟预算在代码计时与响应处理上有回归，但硬截止及真实五分钟运行未验收，不能标全通过。
