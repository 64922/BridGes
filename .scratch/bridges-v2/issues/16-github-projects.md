# 16 — GitHub 项目推荐

**What to build:** 用户显式选择 GitHub 项目推荐后，可找到与完整 idea 功能接近的公开项目，并理解每项推荐的覆盖范围与局限。

**Blocked by:** 11 — 论文搜索

**Status:** ready-for-human

**实施前必读：** [ADR-0030](../../../docs/adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md)、[V2 设计索引](../../../docs/v2/README.md)及索引所列六份设计文档；先核对阻塞票已验收。

**编排合同：** 显式 `github` 进入 `github.parse → github.search → github.inspect → github.rank → github.present`。解析保留完整 idea 的用户场景与必要功能；先查整体项目，缺少时再查组件并记录覆盖范围。检查节点区分 API 元数据、README 自述、许可证和实际读取的实现文件，排序优先看功能匹配。限流采用有限缓存与退避，达到额度即保留可重试状态。普通聊天只提供明确的一键建议。

- [x] 菜单与 chip 可选择 GitHub 模块；提取用户 idea 的核心场景和功能，优先搜索整体相似项目。
      （`chat-modules.ts` 的 `github` 项与 `Composer` chip；`parse` 保留场景与要点原词，
      第一条查询是压缩后的场景，候选按身份相关度排序后再花额度读取。）
- [x] 默认尝试给出两三个仓库，逐项展示直达链接、功能匹配、借鉴角度、维护与许可证据；组件项目明确标出只覆盖哪一部分。
      （`INSPECT_LIMIT=3`／`DEFAULT_RECOMMENDATION_COUNT=3`；卡片与正文逐条给出链接、要点命中与否及依据、
      借鉴角度、维护与许可；`GithubCoverage` 决定「整体项目／组件项目（只覆盖一部分）」并列出只覆盖哪几项。）
- [x] README 仅作为项目自述；未读取实现文件不作内部架构断言，未见许可证不声称代码可自由复用。
      （证据分三级，`borrow_note`／`limitations`／`evidence_boundary` 都按取得的证据写；
      许可优先读正文，只有元数据时才写「元数据标注」，未读到就写「未见」。）
- [x] 限流、文件缺失或匹配项目不足时展示实际结果和局限；完整项目与组件两类输入经过真实 API 验证。
      （限流：保留可重试状态 + 边界说明 + 逐条候选理由；README 缺失与空仓库是「事实」不是错误；
      真实 API 三类输入实测见下。）
- [x] 同一日常会话可在论文搜索后切到 GitHub，用“找实现它的项目”指向可追溯的前文原词；移除 chip 后普通聊天不误启动 GitHub。
      （`_prior_context` 取上一轮论文搜索的原始词并带回消息 ID，场景与检索词与它逐字一致；
      流程用例覆盖「论文 → 找实现它的项目」与「移除 chip 后普通聊天不进入 GitHub」。）

## Comments

**实施（2026-09-26，worktree `BridGes-16-github-projects`，分支 `v2/16-github-projects`）**

- 后端 `src/bridges/github/`：`lexicon`（原词抽取与检索词压缩）、`parsing`（parse：场景／要点／
  前文指代／澄清）、`searching`（search：查询计划与候选归一）、`inspecting`（inspect：README／
  许可／根目录清单／嵌套路径读取）、`ranking`（rank：功能匹配、身份闸门、覆盖判定）、
  `presenting`（present：正文渲染与可选的借鉴角度门控）、`service`（五节点编排、投影、落库、
  等待恢复）、`client`（有限缓存、search/core 双额度桶、脱敏披露）、`contracts`、`suggestion`。
- 接入：父图 `chat/graph.py` 的模块派发（`module_id=github` 与五个节点标签）、`api/main.py`
  的服务注册与 shutdown、`AVAILABLE_MODULE_IDS` 加入 `github`。
- 前端：`chat-modules.ts` 菜单／chip、`Composer` 移除、`MessageList` 节点标签与卡片、
  `GithubProjectsCard`、`api.ts` 类型别名。
- 存储：`messages.github_projects`；`SCHEMA_VERSION` 56 → **57**（54/55/56 已被先合并的
  Issue 12/13/14 占用，迁移键必须连续）。
- 契约：`openapi.json`（299 paths、14 个 `Github*` schema）与 `packages/contracts/src/generated.ts`
  重新生成，`tests/contracts/test_openapi_sync.py` 2 passed。

**测试与静态检查**

- `tests/github/` **45 例全绿**：`test_github_core.py` 23（原词抽取、查询计划、功能匹配分级、
  覆盖判定、身份闸门、空仓库理由、片段合并、正文渲染）、`test_github_sources.py` 15（client 的
  缓存／限流／错误分类／审计指纹、检索适配、检查读取与路径存在性核对）、
  `test_github_module_flow.py` 7（澄清与恢复、论文→GitHub 指向前文、停止、失败、
  chip 移除不误启动）。全部不发起外网请求（`httpx.MockTransport`）。
- 前端 vitest **21 文件 / 165 例**全绿；`tsc --noEmit` 与 `next lint` 干净（lint 仅 3 条既有
  warning，均不在本票文件）。
- mypy 两侧 **115 处 / 23 文件**逐条相同（分支多检 11 个新文件，新包内 0 处）；ruff 差集只有
  `src/bridges/api/main.py` 新增 5 行模块导入的既有 E402 惯例（该文件已有 100+ 条），
  本票新代码 0 条。

**实测暴露并修复的缺陷**（每条都有对应用例）

1. 「实际查询词」曾把仓库读取记录混进来 → 只列检索接口发出的词（`_search_queries`）。
2. 上游简介无界（长简介撑爆展示）→ 检索层截断到 300 字符。
3. 中文查询在上游是**按字模糊匹配**：整句 idea 会把无关仓库一起召回 → 场景压缩后先查整体，
   候选按身份相关度排序后再花额度读取（额度有限，先读最像的）。
4. 整体项目的误召回：实测「搜索想要的书」里的辅助词「想要」让「更快找到你想要的答案」
   这类新标签页插件在身份上也算命中，于是书签插件被当整体项目推荐 → 身份闸门只认
   **核心场景**的词（名称／简介／话题），不再把要点辅助词算作身份重合。
5. 命中片段按 n-gram 展示（「联邦学、邦学习」「登录认、录认证」）→ 重叠片段合并回完整词
   （「联邦学习」「登录认证」）。
6. 限流分支上的 `_rate_limited_response(params=...)` TypeError（真实代码路径上的 bug）。
7. 披露记录缺查询指纹与长度 → 补 `query_fingerprint`／`query_length`（仍不含查询正文）。
8. 空仓库与「读了但没命中」的剔除理由混为一谈 → 分开表述（空仓库写「没有读到 README，
   也没有读到实现文件」，读到了只是没命中才写「证据里没有出现你 idea 的要点」）。
9. 缓存命中等检索内部日志曾进入查询记录并被卡片渲染 → 查询记录不再携带；卡片改用共享
   `QueryRecordList`（来源按模块词汇表呈现），内部日志与从未被填充的剩余额度／重置时刻字段
   一并清除。

**code-review 双轴（Standards/Spec）复核**

- Standards 轴命中并已修：上述第 9 条（内部日志进界面，违反 `docs/v2/interaction.md` §4 与共享
  `QueryRecordList` 的成文规则）、死代码（无人调用的 `GithubProjectsService.close()`、
  `searching.py` 中重复且未被使用的 `SEARCH_DEADLINE_SECONDS`）、与行为不符的 docstring
  （`_rate_limited_response` 声称「留下披露记录」，实际被阻塞的请求不外发、按设计不留披露记录）。
- Spec 轴：5 条验收标准逐条核对通过；**未采纳项记录在案**——① `_Run`／`_pending_wait`／
  `_finalize` 与论文／贴吧模块同形的编排骨架目前是第 5 份拷贝，抽共享实现会同时改动三个已上线
  模块，留作后续重构；② `_pending_wait`「只把 success/metadata_only/empty 当终态、跨过
  error/stopped 继续往前找」与已合并的 paper／tieba／resources 完全一致，本票照抄既有约定、
  不另立规则（前端 `pendingClarificationModule` 只管高亮哪种 chip，与它职责不同）；
  ③ 整体 idea 的身份闸门对「只把要点写在英文简介、场景词全中文」的仓库偏严，属设计取舍
  （上游中文检索本就是模糊匹配，误召回比漏召回代价高），剔除理由里已写明原因。

**真实 API 验证（2026-09-26，本机实测）**

探针 `.tmp/github_live_probe.py` 直连 GitHub REST API（只读网络、不写任何数据），三类输入各跑
一次，走生产同一条 `_by_identity_relevance → rank_candidates → _projection → 渲染` 路径：

| 输入 | 检索 | 读取 | 实际结果 |
| --- | --- | --- | --- |
| 完整项目「我想做一个校园二手书交换平台，学生可以发布想卖的书、搜索想要的书、线下交换」 | 3 条（`校园二手书交换`／`学生可以发布想卖的书`／`搜索想要的书`）→ 20 个去重候选 | 3 个（6 条调用记录） | 1 推荐：`666bears/usedbook`（组件项目，命中 1/3 要点，README 已读、许可未见 → 局限里明说）；19 个候选逐条给出理由，其中 `253936563/huanshu`（简介正是场景，但 0 字节空仓库）写成「没有读到 README 也没有读到实现文件」 |
| 组件「找个登录认证组件」 | 1 条 → 10 个候选 | 3 个（10 条调用记录） | 3 推荐：`opendevops-cn/codo-admin`（GPL-3.0，已读许可正文与 `docs/codo-admin.md`、`docs/deployment.md`）、`zlt2000/microservices-platform`（Apache-2.0）、`qq275860560/security-demo`（未见许可 → 不声称可自由复用）；全部按组件呈现 |
| 指向前文（论文搜索「联邦学习」之后）「帮我找实现它的项目」 | 1 条，与论文原词逐字一致 | 3 个（8 条调用记录） | 3 推荐：`lokinko/Federated-Learning`、`ZeroWangZY/federated-learning`（MIT）、`GalaxyLearning/GFL`（Apache-2.0，根目录读到 `requirements.txt`、`setup.py`）；正文写出「前文依据：上一轮论文搜索的原始词「联邦学习」」 |
| 同上，core 额度耗尽时（未认证 core 60/时 已用尽） | 1 条 | 0 个 | 限流路径真跑：0 推荐 +「本轮撞上 GitHub 接口额度限制…」的边界说明 + 逐条「本轮检查数量或上游额度有限」理由，不用记忆补造条目 |

额度实测：一轮完整项目约 6 次 core 调用（3 个仓库各取元数据／清单／README 等），组件输入约 10 次；
未认证 core 60/小时与 search 10/分钟是两个独立额度桶，一次会话可跑数轮。探针只发最小公开查询词，
不含任何私人上下文。

**全量回归与基线比对**（两侧同一命令、同一 deselect 集、各自仓外 `--basetemp`，
且**两侧都不设 `PYTHONPATH`**，使子进程导入问题对两边同等生效）

| 侧 | 提交 | 结果 |
| --- | --- | --- |
| main（分支点） | `90a06e1` | **277 失败 / 4116 通过 / 43 跳过 / 3 deselect / 2 错误** |
| 本票分支 | `v2/16-github-projects` | **277 失败 / 4161 通过 / 43 跳过 / 3 deselect / 2 错误** |

- 收集数 4441 → 4486：**Δ+45 恰为 `tests/github/` 三文件**（23+15+7），逐文件收集数比对后
  无其他增减；这 45 条**全部真跑通过**。
- **失败名称集合双向比对完全相同**（两侧各 279 个名字逐条一致）：**0 回归、0 未归因差异**。
  Δ通过 +45 即新增用例。既有的 277 失败／2 错误两侧一致，均为本机环境性失败，与本票无关。
- 跑法订正过程记录在案：首次比对时分支侧设了 `PYTHONPATH=src`、基线侧没设，基线侧因此多出 20 项
  子进程导入失败（`ModuleNotFoundError: No module named 'bridges'`）；把这些用例在基线 worktree
  单跑并设 `PYTHONPATH=src` 后全部通过（`test_cli_contract`／`test_api_boot`／`test_runtime_contract`
  19 passed/2 skipped、`test_runtime_smoke` 9 passed/3 deselect），确认是环境注入而非回归，
  随后按同一跑法重跑两侧得到上表。
