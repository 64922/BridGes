# Issue 42 — 架构加深修复

Status: resolved（候选 1 完成；候选 2–8 拆分为 Issue 43–49）

来源：`/improve-codebase-architecture` 评审（architecture-review-20260806-231807.html），
按严重程度逐个修复 8 个候选 + 4 项小摩擦。

## 候选 1：加深聊天回合管线（Strong）✅ 已完成

- **目标**：回合编排收进深 module。对外只有 stream 接口；模式路由、
  检索、切片编译、提示词组装全部隐藏在其后。
- **验收**：
  - `chat/turn.py` 出现，`ChatService` 委托；公开接口不变 ✅
  - 检索调用只有一个实现位置（消除 service.py:1185/1500/2019/2223 四处逐字重复）✅
  - thinking 由裸 dict 改为类型化记录，14 个 `_*_thinking()` 收敛 ✅
  - 提示词组装集中一处，不再散落 `payload["messages"].insert(1, ...)` ✅
  - tests/chat 全部通过 + 全量 pytest 无回归 ✅

### 实施记录（2026-08-06）

- 新模块 `src/bridges/chat/turn.py`（~1980 行）：`TurnOrchestrator` 承载全部回合管线
  （stream_turn + 6 条内部编排路径 + 切片编译/披露/审计 + `_run_retrieval` 检索单点 +
  `assemble_payload` 组装单点 + 类型化 thinking 辅助函数 + 模式合同/错误映射/上下文
  构建器 + `finalize_message` 收敛函数）
- `ChatService` 4188 → ~1390 行：保留对话/消息持久化、投影、反馈、停止/重试、载荷
  校验；`stream_generation` 改为 3 行委托（guard 与停止信号语义不变）
- 关键设计决策：
  - gateway 每次回合传入（`stream_turn(*, gateway=...)`）——测试面以
    `service._gateway = ...` 替换传输，每回合注入保持该 seam 有效
  - `_model_history` 在 ChatService 保留委托方法（测试直接调用 `service._model_history`）
  - `finalize_message(repo, ...)` 收敛为模块函数，接受 `ChatThinkingSummary | dict`
  - 切片编译器不再改动 payload：返回 `(note, profile_context)` 元组，注入交给
    `assemble_payload`
  - `error_is_retryable` / `user_facing_error` 在 service re-export（api/chat.py 导入面不变）
- 验收证据：`pytest tests/chat` 146 passed；全量 `pytest` 2148 passed / 6 skipped；
  `ruff check src/bridges/chat/` 全过

## 候选 2：统一领取型任务契约（Strong）

- **目标**：租约+轮询+重启恢复收敛为一个深队列模块。
- **验收**：ingestion/image/video/deletion/reminder 共享同一领取契约；
  workflows 崩溃恢复语义与其余一致。

## 候选 3：对象授权收拢（Strong）

- **目标**：所有对象访问经 ScopeEnforcer；预过滤删除；MCP 接入；
  _subject 工厂化。
- **验收**：projects/media/sharing/workflows/mcp 不再自带判据副本。

## 候选 4：服务不再直读不属于自己的表（Worth exploring）

## 候选 5：前端数据获取 module（Worth exploring）

## 候选 6：单一流事件 adapter（Worth exploring）

## 候选 7：密钥环 seam（Worth exploring）

## 候选 8：跨缝展示知识收拢（Worth exploring）+ 小摩擦项

## Comments

- 2026-08-06：评审完成，用户指示按严重程度逐个修复；task_plan.md 已同步。
