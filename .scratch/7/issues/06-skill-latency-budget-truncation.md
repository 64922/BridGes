# Issue 06：结构化技能调用的预算截断、部分交付与错误透传

Status: ready-for-agent

Type: task

Priority: P0

User stories: US-06

## 已验证现状与根因

生产观测：人味化长文、生涯规划两轮均「已思考（用时 123/124 秒）」后显示「本次生成超过时延预算，已停止继续执行；请重试」；另一次人味化 156 秒后才回复。

- 两条链路均走非流式 `gateway.invoke("qwen_structured_output")`：人味化首稿（`src/bridges/skills/humanizer/service.py:960-1020`，`max_tokens=4096`）+ 定向修订（:1083、:1233，调用上限 2，`WRITING_CALL_LIMIT=2` :150）；生涯一次结构化生成 + 有界修复（`src/bridges/career/service.py:515-582`、:610-689）。
- 耗时算术与观测吻合：上游静默被 httpx 60 秒掐断（`src/bridges/ai/qwen_client.py:146`，`timeout=60.0`）→ 网关非流式重试第 2 次（`src/bridges/ai/production.py:79,94`，`model_gateway.py:437-448`）≈ 121 秒 + 前置检索/embedding ≈ 123/124 秒。
- 预算只在事件边界检查（`src/bridges/chat/turn.py:4181-4209` 人味化、:4723-4749 生涯），进行中的阻塞调用不可中断，单次 invoke 最长约 121 秒可吃光 120 秒总预算（`src/bridges/chat/budget.py:30`，`TOTAL_BUDGET_MS=120_000`）。
- `budget_exceeded` 检查排在 result 事件处理之前：即使 run_task 已产出真实错误（如上游超时/限流），也被「超过时延预算」文案掩盖（`turn.py:524`）。
- 两条链路不下发 delta，用户全程只看到「已思考」；本 issue 不改变非流式架构，只保证有界与诚实。
- 已冻结决策（本轮 #6）：按剩余预算截断每次调用 + 预算不足不重试 + 人味化首稿部分交付 + 真实错误透传；120 秒总预算不变。

### 上下文指针

- `src/bridges/chat/budget.py:30`、:148-313：总预算与 `RunBudget`（`can_retry` 等接缝）。
- `src/bridges/ai/model_gateway.py:437-448`：非流式重试循环；`src/bridges/ai/qwen_client.py:146`：httpx 超时。
- `src/bridges/skills/humanizer/service.py:150-154`、:960-1020、:1083-1233：调用上限、首稿、修订与 `REVISION_BUDGET_MS=30_000`。
- `src/bridges/career/service.py:515-689`：生成与修复。
- `src/bridges/chat/turn.py:4181-4209`、:4723-4749、:524：预算检查点与文案。
- `tests/chat/test_issue06_latency_budget.py`、`tests/chat/test_issue06_budget.py`、`tests/chat/test_issue09_career_resilience.py`：既有预算测试。

## What to build

1. **按剩余预算截断**：每次非流式结构化调用的 httpx 超时取 `min(默认 60s, budget 剩余 − 交接预留)`，交接预留为单一预算常量来源；任何单次调用不再可能吃光整轮预算。预留与截断逻辑集中在预算模块/网关一处，不散落魔法数。
2. **预算不足不重试**：网关重试循环在每次重试前检查 `budget.can_retry()`（或等价接缝），剩余预算放不下「退避 + 一次最小调用窗口 + 交接预留」时直接以真实错误终态收尾。
3. **人味化部分交付**：首稿完成但修订所需预算（`REVISION_BUDGET_MS`）不足、或修订调用失败时，交付带明确标注的首稿（消息中说明「已交付首稿，未完成修订」类文案），不再以 `budget_exceeded` 抹掉已完成工作；部分交付在投影与审计中如实标记。
4. **真实错误透传**：模型调用本身失败（上游超时/瞬断/限流/权限）时，终态使用对应真实错误码与中文文案；只有确实「预算耗尽且无任何草稿/真实错误可交付」时才使用 `budget_exceeded`。调整 `turn.py` 两处事件循环中预算检查与 result 事件的处理顺序，先消费真实结果再判定预算。
5. 生涯路径同样的截断与重试约束；其修复调用已有 `budget.can_retry()` 检查（`career/service.py:555`），与网关新语义对齐去重。
6. 阶段/过程卡事件保持现状（非流式架构不变），但部分交付与真实错误都要有对应用户可见状态。

## Acceptance criteria

- [ ] 注入式慢适配器（单次调用挂起）场景：人味化/生涯整轮墙钟不超过 120 秒 + 测量容差，且终态在预算内形成。
- [ ] 上游两次超时场景：终态为真实超时/瞬断错误码而非 `budget_exceeded`；剩余预算不足时网关重试次数为 0。
- [ ] 首稿成功 + 修订预算不足场景：交付带标注首稿，消息为成功（部分）终态而非错误。
- [ ] 普通聊天流式路径与既有预算测试（阶段顺序、8 秒搜索墙钟、有草稿带警告交付等）全部不回归。
- [ ] 审计可区分：完整交付、部分交付、真实上游错误、预算耗尽四种终态；每次模型调用仍有独立运行锁（成功/失败/被截断）。

## Test plan

1. fake budget/clock + 可控慢适配器：断言截断后的单次调用超时 ≤ 剩余预算、重试预算门槛、整轮墙钟上限。
2. 人味化：首稿成功/修订超时 → 部分交付断言；首稿即超时 → 真实错误透传断言。
3. 生涯：生成超时 × 2 → 真实错误码断言；修复预算不足 → 不重试断言。
4. 运行锁断言：被截断/失败的调用也有对应状态锁（与第 6 轮 Issue 10/11/12 合同一致）。
5. 既有预算与生涯/人味化测试全量回归。

建议回归命令：

```powershell
python -m pytest tests/chat/test_issue06_latency_budget.py tests/chat/test_issue06_budget.py tests/chat/test_issue09_career_resilience.py tests/humanizer tests/career -q -p no:cacheprovider --basetemp=$env:TEMP\bridges-r7-issue06
```

## Observability & rollback

- 指标：每次结构化调用的截断后超时值、实际耗时、重试是否被预算抑制、部分交付率、真实错误码分布。
- 不变量告警：单次调用超过其截断超时；真实错误被改写为 `budget_exceeded`；部分交付未标注。
- 回滚：截断/部分交付/透传各自独立可关；回滚不得恢复「单次调用吃光整轮预算」的旧行为。

## Blocked by

无。

## Comments

- 2026-08-14：本轮冻结决策 #6 全包范围即本 issue 的 What to build 1-4。
- 2026-08-14：非流式架构保持不变；若未来要把人味化/生涯改成流式，另行立项。
