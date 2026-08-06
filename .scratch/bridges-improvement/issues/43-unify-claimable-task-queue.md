# Issue 43 — 统一「领取型任务」契约：租约 + 轮询 + 重启恢复只写一次

Status: completed
Type: task
来源：架构评审候选 2（Strong）。评审报告：architecture-review-20260806-231807.html
词汇：module / interface / depth / seam / adapter / leverage / locality

## 问题（当前状态，含证据）

「租约 + 轮询 + 重启恢复」这一后台任务契约被重写 5 次，各自列名、退避公式、
跳过/补发语义不同；崩溃后「这个任务会怎样」每个 module 一个答案：

| 实现 | 文件 | 租约/状态列 | 退避 | 语义 |
|---|---|---|---|---|
| 文档摄取 | `ingestion/service.py` `process_pending()` | `document_records.claimed_at` / `lease_expires_at` | 自建 | queued + 租约过期即重领 |
| 图片生成 | `image/service.py` `process_pending()`（~1.5K 行） | `image_task.claimed_at` 等 | `retry_policy` | 自建轮询/进度/恢复 |
| 视频生成 | `video/service.py` `process_pending()` | 同上模式 | `retry_policy` | 自建 |
| 删除重试 | `lifecycle/deletion.py` `process_pending_retries()` | `account_deletions.status='failed'` | 线性 | 自建 |
| 提醒补发 | `reminder/service.py` `process_due()`（1043 行） | `reminders` 状态 | 30-60-300s | 24h 补发窗口/重复任务最近一次/窗口外记错过 |

- 外层 `supervised_loop()`（`runtime/loop.py:15-31`）只有「poll-until-signaled」30 行，
  是深骨架，**内层恢复语义才是重复点**。
- `workflows/service.py` 的编排运行状态在内存 `self._runs: dict[str, _RunRecord]`
  （line 106），崩溃即失；`evaluation/` 的 `EvaluationRunLock` 同样在内存
  （repository.py）；只有 `lifecycle/deletion.py` 把状态机持久化到 SQLite。
- 删除测试（对共享队列模块）：删掉它，复杂度回到 5 个调用方——正向，值得加深。

## 方案：一个深队列 module，子系统变薄 adapter

### 目标接口（小 interface，大实现）

新模块 `src/bridges/runtime/queue.py`：

```python
class Claim:                     # 领取到的一件活儿（不可变）
    claim_id: str
    task_key: str                # 子系统+对象标识（如 "ingestion:doc-42"）
    attempt: int
    lease_expires_at: datetime

class TaskQueue:                 # 深 module：租约/退避/恢复全部在此
    def claim_next(self, queue_name: str, worker: str) -> Claim | None: ...
    def complete(self, claim: Claim, result: dict | None = None) -> None: ...
    def requeue(self, claim: Claim, *, retry_kind: RetryKind, reason: str) -> None: ...
    def lease_expiry_seconds(self, queue_name: str) -> float: ...
```

- `claim_next`：原子领取（`claimed_at=now, lease_expires_at=now+lease`），
  过期租约视为可重领（崩溃恢复的唯一规则）。
- `requeue(retry_kind=FIXED|LINEAR|EXPONENTIAL, ...)`：退避公式单一实现
  （`next_retry_at = now + backoff(attempt)`），不允许各子系统自带公式。
- 队列表一张：`task_claims(queue_name, task_key, worker, attempt, claimed_at,
  lease_expires_at, next_retry_at, payload_json, result_json, status)`。
  存量表（document_records/image_task/…）保留业务列，新增 `task_claims` 只做
  **领取与调度**：子系统业务表仍是其自身的权威状态（不迁移存量 schema）。
- `TaskWorker(queue, handler)` 薄封装：`run_once()` = claim → handler(claim) →
  complete/requeue；`handler` 由子系统提供，只管「干这一件活儿」。

### 边界（哪些**不**进队列）

- 提醒补发窗口（24h）、重复任务最近一次补发、窗口外记错过 —— 这些是
  `reminder` 域的业务语义，**留在 reminder**；队列只统一「何时重试」的机械规则。
- 教学证据门、任务卡状态机等业务状态——不进队列。
- 现有 `supervised_loop` 保留：队列只替换每个子系统里「自建 lease/poll/backoff」的部分。

### 子系统迁移步骤

1. `runtime/queue.py` 落地 + 单元测试（原子领取、租约过期重领、退避公式、
   崩溃后恢复、complete/requeue 幂等）。
2. **试点：`lifecycle/deletion.py`**（最简：failed 状态重试，无进度流）→
   删掉自建重试循环，改用 `TaskWorker`。验证：`tests/lifecycle/` 全绿。
3. **摄取 `ingestion/service.py`**：`process_pending()` 改为 worker handler；
   `claimed_at/lease_expires_at` 语义迁到队列（SQL 条件保留但由队列参数化）。
4. **图片/视频**：`process_pending()` 的轮询/进度骨架替换；云端状态回写逻辑
   保留在 handler 内。这两个文件大（~1.5K/~1.2K），分两步做，每步全量回归。
5. **提醒**：`process_due()` 中「何时该试」的部分走队列（attempt/退避），
   「该不该发、发几次、错过怎么记」留在 reminder。
6. **workflows 状态持久化**（同批）：`_RunRecord` 落 SQLite（`workflow_runs` 表），
   崩溃恢复语义与 lifecycle/deletion 一致；`evaluation` 经既有接口无感。

### 验收标准

- [x] `grep -rn "next_retry_at\|retry_policy\|lease_expires_at" src/bridges/` 只剩
      `runtime/queue.py` 定义处与存量表列名（剩余为模型网关调用重试概念
      RetryPolicy 与存量表列名引用，非领取型任务契约）
- [x] `tests/runtime/`（新）覆盖：领取原子性、租约过期重领、三种退避、崩溃恢复
      （24 测试）
- [x] 原 5 个子系统测试全绿 + 全量 pytest 2179 通过（基线 2148+，零回归）
- [x] workflows 重启恢复新增测试：进程重建后运行状态可从 SQLite 恢复
      （test_workflow_persistence.py，5 测试）
- [x] ruff 干净（改动文件；全量存量 104 项非本 issue 引入）

### 风险与开放问题

- 迁移顺序错了会大面积回归：试点 deletion 是缓冲垫（单点、语义最简单）。
- image/video 的进度回写依赖业务表列，队列只调度不转移——若 handler 需要
  「进度」语义，队列提供 `touch(claim)` 续租接口即可，不新增进度模型。
- 并发：后台执行器与提醒调度是同一进程内两个线程，队列必须用单条
  `UPDATE ... WHERE lease_expires_at IS NULL` 原子领取（SQLite 写锁天然串行）。
- ADR-0013（本地后台执行器）不冲突：本方案仍在单机进程内，不引入外部调度。
