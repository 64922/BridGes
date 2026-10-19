# 07 — 楼中楼分页保留稳定身份和纠正上下文

**What to build:** 楼中楼首批预览之外的反驳、纠正与补充可以被读取，并与正确父楼层一起呈现在总结依据中。

**Blocked by:** 06 — 普通回复逐页读取并保存可恢复证据

**Status:** ready-for-human

## 实施边界与关键决策

核实楼中楼独立分页及上游身份，按父楼层维护进度。把普通回复、嵌套回复、父子关联和上下文用途清晰建模，沿用逐页证据保存。

## 验收条件

- [x] 真实样本至少读取超过首批预览的一条嵌套发言，核对帖子、父楼层、自己的稳定身份和实际时间。
- [x] 父帖/父楼层不匹配响应被拒绝；被回复对象缺失时保持未知，禁止按展示邻接关系猜作者关系。
- [x] 每个父楼层独立游标，重复页、删除父楼层、部分失败和取消不会混串或无限翻页。
- [x] 一条纠正旧说法的嵌套回复连同必要上下文进入总结，引用能定位已读原文，不推导未知作者立场。
- [x] 身份缺失时如实降级并标记重复组，不造真实ID；预览、已读部分、确认读完的状态可区分。
- [x] 界面能核对嵌套发言与父上下文；历史无身份材料仍按旧证据读取。

## 验证方式

真实楼中楼协议样本 + 多父楼层/错误归属/重复页回归 + 纠正说法端到端样例。

## 不在本票范围

不以文本哈希作为上游发言身份，不要求每个长帖全量读取才能交付。

## 通用执行与交接要求

- 执行前阅读本工作流执行方案及领域合同；复核当前实现，已有通过能力优先复用，不能依据历史问题描述重建已完成模块。
- 使用 conda agent 环境。先建立能捕获本票缺陷/行为的测试，再实现；每票包括所需公开投影、中文界面状态和端到端验证，不以单层完成替代行为交付。
- 外部协议须由真实响应验证；若主路径不能满足合同，说明验证过的备选与剩余差距，不静默缩减范围。不得搬运参考项目源码或引入其业务客户端。
- 共享预算、账户、任务版本、租约与证据边界保持一致；内部旧路径直接移除，不新增长期双实现。稳定API及历史数据按外部合同评估。
- 提交前执行项目完整检查，前端变更额外构建，协议变更同步生成；schema/存量数据变化才执行对应迁移。变更源码及测试单文件不超过500行，必要拆分只限本票职责。
- 交接记录实际行为、测试命令及退出码、真实/模拟证据、未验证项、回滚方法、公开契约与版本变化。完成实现但待人工复核可转 ready-for-human；只有验收证据合格才解除下游阻塞。

## Comments

- 2026-10-18：依据当前代码、既有ADR和本次运行账本故障分析形成待确认草案；未执行开发。


- 2026-10-18：用户确认拆分，正式发布为 ready-for-agent；编号、内容范围和依赖保持已批准方案，未开始业务开发。

- 2026-10-19T09:34:28+08:00：开始实现。负责人：opencode；工作树 `D:\BridGes\.worktrees\07-nested-reply-pagination`；分支 `07-nested-reply-pagination`；基线 `6c395f12`（main，已含 06 合并）。前置 06 状态为 ready-for-human：普通楼层分页代码与离线回归已交付，真实生产模型总结仍待人工复核；其交接明确写入“07 的普通页分页代码依赖已满足”，本票沿用它并继续以真实浏览器样本复核楼中楼协议。

- 2026-10-19T10:30:00+08:00：实现与验证完成，转 `ready-for-human`。
  - 实现：新增 `browser_nested_protocol.py`（HTML 片段解析、稳定 `spid`、父楼层分页元数据、登录态 fetch）、`browser_nested.py`（逐父楼层独立游标、预览/已读按 ID 合并、有界停止码）、`browser_nested_enrich.py`（预览标注、父楼层差异优先与上限）和 `browser_read_projection.py`（公开投影、嵌套范围与未读原因）；`browser_thread_read.py` 保留逐页导航与接入。真实单页 pager `total_page=1` 时没有 `tP` 当前页标记，已按“仅一页”确认为第 1 页，避免把完整单页误报为缺分页。
  - 读取边界：每个父楼层默认 2 页、最多 3 页；每页最多尝试 6 个父楼层；单个父楼层最多 60 条；合并后总回复上限由 240 调至 480，并新增 `tieba_browser_reply_limit`。父帖/父楼层不匹配、重复游标、游标错位、空页、认证、取消、计数冲突、缺身份、父楼层缺失均有稳定停止码；缺身份条目不造 ID、不纳入证据并保留原因。
  - 公开合同：新增 `TiebaNestedState`、`TiebaNestedProgress`；`TiebaReply.nested/nested_preview/nested_progress`；`TiebaReadResult` 与 `TiebaPostProjection` 的 `nested_complete/nested_incomplete_count/nested_error_code/nested_error_message`；`TiebaSummaryCitation.nested`。总结材料为楼中楼附带 `parent_author/parent_posted_at/parent_content/parent_context_state`，提示词禁止把父楼层立场归给楼中楼作者；追溯校验要求引用与回复的 `nested` 一致。
  - 真实证据：真实帖 `10897336006`（第 1 页，页面声明 10 页）父楼层 4（`reply_id=153765210147`）读取 2 页 17/17 条；预览 4 条之外的 13 条均保留稳定 ID、父楼层 ID、作者和实际时间，例如 `153765408745` / 説綏 / `2026-7-27 10:53` / “回复 根号八八 :开学晚上别睡太死”。父楼层 14（`reply_id=153766554567`）为单页 7/7 条，验证了无 `tP` 的单页 pager。最终 `nested_complete=true`、`nested_incomplete_count=0`；整体 `status=partial` 仅因普通楼层只读第 1/10 页，符合预期。
  - 测试：`pytest tests/tieba tests/contracts -q` → `376 passed`（退出码 0）；`ruff check src/bridges/tieba tests/tieba` 通过；定向 `mypy` 无本票错误；`openapi.json` 重新生成 324 paths，`tests/contracts/test_openapi_sync.py` 通过。前端 `npm run typecheck` 通过，`npm run test:unit -- --run` → 54 个文件 / 416 passed，`npm run lint` 仅既有 `<img>` 警告，`npm run build` 编译与 23 个静态页生成成功（worktree `node_modules` junction 导致 standalone trace 符号链接 EPERM 警告，不影响编译结果）。
  - 全量检查：`pytest -q` 仍在收集阶段被既有 `tests/career_plan` 4 个 `CareerBackgroundLoader` 导入错误中断；忽略这 4 个文件后的大范围运行仍有大量与本票无关的既有失败并在超时前未完成，因此以 `tests/tieba + tests/contracts` 作为本票完整回归范围。
  - 契约与版本：浏览器协议 `tieba-browser-protocol-v3`、recipe `tieba-recipe-v9`、读取能力 `tieba-read-v10`、总结能力 `tieba-synthesize-v9`、验证 `tieba-verify-v5`、总结提示词 `tieba-summary-v6`、总结输入 schema `tieba-read-evidence-v3`；已同步 `openapi.json` 与 `packages/contracts/src/generated.ts`。无数据库 schema 迁移。
  - 回滚：回退本票提交即可；worker 协议 v3、recipe v9、读取/总结/验证版本必须成组回退，旧 worker 不兼容新增进度与嵌套字段。历史无嵌套字段的持久化材料按 `nested=false` 默认值读取，无数据迁移。
  - 未验证/剩余：真实生产模型总结与引用未在本票重新跑通（06 的真实模型旅程仍待人工复核）；真实楼中楼样本覆盖本线程第 1 页的两个父楼层（含多页与单页），未穷举全部帖型和异常网络条件；浏览器临时 profile 与探测输出在交接后清理。
