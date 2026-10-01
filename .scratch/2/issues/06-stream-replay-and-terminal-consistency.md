# 06 — 统一流式正文、终态存储与断线重放

**What to build:** 用户流式阅读、停止、重连和重新加载看到一致正文，受保护片段跨 chunk 不出现重复或错接。

**Blocked by:** 05 — 按保留意图绑定事实片段，修复盲替换

**Status:** ready-for-human

**优先级：** P0

## 背景与需求

后端把整段修复正文当 delta，前端持续追加，导致屏幕正文与落库正文不同；当前证据仅是算法模拟。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/人味化/实施方案.md](../../../docs/人味化/实施方案.md)。
- [docs/人味化/审查与改进建议.md](../../../docs/人味化/审查与改进建议.md)。
- [docs/人味化/复核脚本.py](../../../docs/人味化/复核脚本.py)。
- [docs/人味化/复核结果.json](../../../docs/人味化/复核结果.json)。
- [docs/workflow/orchestration.md](../../../docs/workflow/orchestration.md)。
- [docs/workflow/delivery-and-validation.md](../../../docs/workflow/delivery-and-validation.md)。

## 任务内容

1. 根据 05 的实际绑定需求选择一种最小方案并记录理由：闭合确认前缓冲片段，或确有修改已发正文需求时定义明确替换事件。避免维护两条并行重写通道。
2. delta 始终只表示追加新内容；若有替换事件，持久事件、前端 reducer、游标重放和重连统一处理。
3. 流式、终态消息和恢复协议具有版本兼容，历史事件仍可读。终态重新拉取不能作为掩盖错误增量的办法。
4. 停止与租约守卫覆盖修复路径；停止后不追加后台润色调用、不重新获得额度，迟到数据不能推进正文。
5. 后续 38 只负责可信结果投影，不另造正文协议。

## 跨票接缝与责任

本票拥有正文事件协议和前端拼接；38 在此协议上接入通过质量门的结果块；统一终态提交服务保留权威。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [ ] 复核的跨 chunk 代码案例中 UI、事件重放和落库正文逐字一致，无整段伪 delta。
- [ ] 覆盖改变已发前缀、多个保护片段、空 chunk、断线游标重放和终态重新加载。
- [ ] 真实浏览器生命周期验收，不能只用模拟拼接声称界面已验证。
- [ ] 停止、旧租约晚返回和并发重连不重复正文或发起新调用。
- [ ] 历史 SSE 和旧消息兼容可读，不破坏已有终态唯一性。

## 验证与交付证据

用正式 API/执行器驱动浏览器并对三种正文比较；故障注入 chunk 边界、停止与重连。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## Comments

### 2026-10-01 — 实现完成（agent，工作树 `BridGes-06`，分支 `codex/06-stream-replay-and-terminal-consistency`）

**方案选择（任务内容 1）：** 采用「闭合确认前缓冲片段」，不新增替换事件——05 的绑定只做原样保留与确定性替换，没有「必须改写已发正文」的合法需求，避免维护第二条并行重写通道。

**实现合同与接口**

- 新模块 `src/bridges/chat/stream_protection.py`：`StreamProtectionAssembler`（`update`/`finish`/`emitted`）保证 delta 恒为追加；`safe_append_boundary` 暂缓「未与源片段精确匹配的候选片段、未闭合起始符、仍可能增长的 URL/带单位数值、降级前处理会剥离的未闭合引用记号」，闭合或终态才下发。协议版本 `STREAM_CONSISTENCY_PROTOCOL_VERSION = "append-only-delta-v1"`；无新事件类型、无事件 payload/schema 迁移。
- `src/bridges/chat/turn.py`：降级前缀、流式循环、预算到期与终态收敛统一走装配器；终态正文 = 已发增量拼接，UI/重放/落库逐字一致；`finish()` 对「终态正文不以已发正文为前缀」显式断言，绝不整段回发伪增量。
- `src/bridges/chat/fact_protection.py`：`compile_protected_fragments` 先遮蔽代码围栏再匹配其余类别（修复跨围栏伪片段）；新增 `binding_sources` 与 `same_fragment_identity`（三段式配对第 1 段与流式装配共用同一判定）。
- `src/bridges/chat/repository.py`：终态事件后拒绝一切 delta/stage 等非 node 事件；`node` 进度仅在 **done** 终态后允许补记（日常父图 verify_output/persist_result 在图内 done 之后仍上报，见 `graph.emit` 注释），错误/停止终态后同样拒绝。
- `src/bridges/api/chat.py`：SSE 响应 `Cache-Control` 增加 `no-transform`——修复前 Next 前端 compression 中间件对整个流做 gzip，浏览器只能等运行终态一次性收到全部事件（e2e 直连 API vs 经代理的时序探针确认）。
- 测试专用设施（仅 `BRIDGES_ENVIRONMENT=test`）：`src/bridges/ai/stream_script_fixture.py` 的 `ScriptedChatStreamAdapter` + `POST /_test/chat-stream-script`，注入 chunk 边界与块间延迟。
- 复核文档同步：`docs/人味化/复核脚本.py` 流式案例改用正式装配器并记录协议版本；`复核结果.json` 重生成（发送增量 `["值 ", "`x = 1`。"]`，前端累加 == 落库；修复前为 `值 `x = 0值 `x = 1`。`）。

**验证（conda `agent`，Windows，Python 3.11.15）**

- `tests/chat/test_issue06_stream_replay_consistency.py` + `tests/ai/test_stream_script_fixture.py`：**30 passed**；两轴复审加固后受影响集合（issue02/03/05/06、terminal recovery、chat api、ai）：**299 passed**。
- 核心套件 `tests/chat tests/ai tests/api`：**884 passed / 100 failed / 1 xfailed**；与干净基点 `1b35c02` 的基线工作树逐名比对，失败名单**零新增**（100 项为环境既有的搜索/教学夹具链失败，基线同样复现）。
- append-only 不变量 fuzz：套内 600 seeds 通过；本地重型脚本（`.scratch/issue06_fuzz.py`，20000 轮，未纳入提交）**0 mismatches**，终态断言未触发。
- 真实浏览器 E2E（Playwright + 真实 API/后台执行器/SQLite/SSE，经 Next 代理，零 mock）：`apps/web/e2e/issue06-stream-replay-consistency.spec.ts` **3 passed**——跨 chunk 保护片段三正文逐字一致；中途刷新按游标续读不重复；停止后迟到数据不推进正文且不新增模型调用。
- `ruff` 改动文件无新增问题；`mypy src` 与改动前同为 98 处既有报错（0 差异）；`apps/web` `npm run typecheck` 通过。

**限制与设计取舍**

- 并发重连以「刷新断线续读 + 游标 0 重放」覆盖；未构造两个浏览器会话的真并发订阅（执行器旧租约迟到由仓库/链路级用例覆盖）。
- 历史兼容 = 事件 schema 未变 + 载荷字段断言 + 既有历史记录可读；本票未新增持久状态，无需迁移/备份变更。
- 停止路径不 `finish()`：未闭合片段按设计丢弃（终态前不下发无法撤回的片段），e2e 与单测固定该边界。
- 确定性脚本适配器只证明机制；真实模型体验与外部可得性按评测票验证。

