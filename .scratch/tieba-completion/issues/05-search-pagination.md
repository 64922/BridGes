# 05 — 发现多页候选和仅在回复中出现的话题

**What to build:** 标题不含话题、但回复确实讨论该话题的帖子也能被发现；候选不再局限于每个搜索标签的第一页。

**Blocked by:** 04 — 打通单话题单帖的真实采集与引用总结

**Status:** ready-for-human

## 实施边界与关键决策

实测并接入固定吧内主题/回复搜索分页。候选保留帖子身份、命中类型、可取得的发言定位、查询与页来源；同帖合并发现路径，不丢回复命中线索。复用现有读取与总结验证一个新增候选完整到达结果。

## 验收条件

- [x] 真实验证至少一个仅回复命中的样本，并读取对应原文确认；不能用全站标题搜索或摘要冒充。
- [x] 至少一个多页受控样例和可取得的真实翻页样本；空结果、重复页、异常分页、访问受限分别收敛。
- [x] 同帖多次命中合并为候选但保留全部相关定位；不因首个标题命中覆盖后续回复定位。
- [x] 查询、翻页、跳转逐请求计量并检查预算/取消，达到边界时保存候选和实际搜索范围。
- [x] 至少一个非第一页或回复命中的候选经读取、总结和引用交付；未穷尽范围对用户可见。

## 验证方式

协议夹具分页矩阵 + 真实回复命中检索→原文→总结旅程。

## 不在本票范围

不扩展其他贴吧，不要求全吧穷尽搜索，不以页数或热门排名作为相关性保证。

## 通用执行与交接要求

- 执行前阅读本工作流执行方案及领域合同；复核当前实现，已有通过能力优先复用，不能依据历史问题描述重建已完成模块。
- 使用 conda agent 环境。先建立能捕获本票缺陷/行为的测试，再实现；每票包括所需公开投影、中文界面状态和端到端验证，不以单层完成替代行为交付。
- 外部协议须由真实响应验证；若主路径不能满足合同，说明验证过的备选与剩余差距，不静默缩减范围。不得搬运参考项目源码或引入其业务客户端。
- 共享预算、账户、任务版本、租约与证据边界保持一致；内部旧路径直接移除，不新增长期双实现。稳定API及历史数据按外部合同评估。
- 提交前执行项目完整检查，前端变更额外构建，协议变更同步生成；schema/存量数据变化才执行对应迁移。变更源码及测试单文件不超过500行，必要拆分只限本票职责。
- 交接记录实际行为、测试命令及退出码、真实/模拟证据、未验证项、回滚方法、公开契约与版本变化。完成实现但待人工复核可转 ready-for-human；只有验收证据合格才解除下游阻塞。

## Comments

- 2026-10-18：依据当前代码、既有ADR和本次运行账本故障分析形成待确认草案；未执行开发。


- 2026-10-18：用户确认首发为 ready-for-agent；编号、内容范围和依赖保持已批准方案，未开始业务开发。

- 2026-10-19T12:00:00+08:00：实现交接（worktree `D:\BridGes\.worktrees\05-search-pagination`，分支 `05-search-pagination`）。
  - 实际行为：主题/回复两个页签各读取 Page 1–2；滚动 `.frs-page-wrap` 触发真实 `pn=2` 请求；解析 `current_page`/`has_more`，停止原因覆盖 `pages_limit`、`duplicate_page`、`abnormal_page`、`page_gap`、`abnormal_empty_page`、`timeout`、`cancelled`、`access_restricted`、`dom_fallback`。同一 URL 的主题命中与回复命中合并为一条候选，`discoveries` 保留全部查询词、页签、页码、回复定位；读取阶段优先回复命中；公开投影、运行记录、正文和前端详情均展示未穷尽范围。
  - 真实证据：查询「空调」两个页签均实际读到第 1–2 页并返回 `pages_limit`；回复搜索第 2 页命中 `https://tieba.baidu.com/p/9049536696`（标题不含「空调」），命中回复 `reply_id=150421754724`、作者 `赛车王great`、原文「但是31不能开空调好像是电路撑不住的原因 本身有中央空调」。随后用真实页面读取该帖（structured，读到第 1/2 页、25 条回复），第 12 楼原文可核对；真实模型总结完成，引用 `E14` 精确指向该 URL、第 12 楼、同一 `reply_id`。
  - 测试：`python -m pytest tests/tieba tests/contracts/test_openapi_sync.py -q` → **321 passed**（exit 0）；`ruff check src/bridges/tieba tests/tieba/test_search_pagination.py tests/tieba/test_search_scope_projection.py tests/tieba/test_browser_search_pagination.py tests/tieba/search_pagination_support.py` → **All checks passed**；mypy 对本票 8 个源码文件 → **Success**；前端 `tsc --noEmit`、`vitest run`（54 files / 410 tests）、`next lint` 通过。`next build` 编译与静态页生成成功，standalone 复制阶段因 Windows junction/node_modules 创建 symlink `EPERM` 失败，属本机环境限制。
  - 分页矩阵：真实 Chrome 用例覆盖滚动到第 2 页、同帖发现路径合并、重复页、页码跳号、空页、翻页超时/达到页数上限、取消保留已取得候选、访问受限保留部分候选；协议夹具覆盖缺失分页字段与非法 payload。为保持单文件 ≤500 行，拆出 `browser_search_scope.py`、`search_merge.py`、`search_contracts.py`，测试拆为 `test_search_pagination.py`、`test_search_scope_projection.py` 与共享夹具。
  - 公开契约/版本：新增 `TiebaSearchDiscovery`、`TiebaSearchScope`；`TiebaSearchHit` 增加 `matched_reply_id`/`matched_author`/`search_page`/`discoveries`；`TiebaPostProjection`/`TiebaCandidateLink` 增加 `discoveries`；`TiebaResearchProjection` 增加 `search_scope`；`TiebaRunMetadata` 增加 `search_page_count`。版本 `tieba-recipe-v8`、`tieba-search-v10`、`tieba-read-v9`、`tieba-synthesize-v8`、`tieba-browser-protocol-v2`；OpenAPI 与 TS 契约已重新生成并同步。
  - 未验证/限制：每页签只读前 2 页，不做全吧穷尽；真实总结备注「超过模型素材上限，另 12 条已读原文未进入总结」，但目标回复已进入并成功引用。全量 `pytest -q` 仍受基线 `CareerBackgroundLoader` 缺失与 architecture wiring 失败影响，未作为本票绿色依据；Issue 04 仍为 `ready-for-human`，本票验证使用既有已登录专用 profile，未修改登录态。
  - 回滚：回退本票提交即可；无数据库 schema/存量数据迁移，未改动或导出 Cookie/profile 登录态。
