# Issue 07：搜索引擎切换收口与发布门

Status: ready-for-agent

Type: task

Priority: P1 release gate

User stories: US-07、US-08

## 已验证现状与根因

- 第 6 轮冻结决策 #2（通用搜索只用 DuckDuckGo、不接备用 Key）已被本轮冻结决策 #1（Tavily 取代 DDG）取代；相关 ADR、运行手册与能力清单仍描述 DDG，需要正式收口，避免文档与生产行为分叉。
- 第 6 轮建立的真实性发布门（`scripts/release_gate.py`、`scripts/qwen_authenticity_gate.py`、`scripts/artifact_secret_scan.py`、`src/bridges/closeout/`）按四类能力合同收口；Tavily 属于 `external_non_qwen` 但**自带凭据**，需要把「Tavily Key 与 Qwen Key 同等纪律」纳入发布门，否则切换后存在密钥泄漏与能力清单失真的风险。
- DDG 实现代码的去留（保留为测试夹具或删除）尚未决策落地。

### 上下文指针

- `docs/adr/`：搜索与预算相关 ADR（含 ADR-0025 公网预算）；`docs/runbooks/`、`CONTEXT.md`。
- `scripts/release_gate.py`、`scripts/qwen_authenticity_gate.py`、`scripts/artifact_secret_scan.py:26`（Key 形态扫描表）。
- `src/bridges/closeout/`：能力清单与真实性门实现。
- `src/bridges/web_search/providers.py`：生产提供方注册点（Issue 01 改造后）。

## What to build

1. **ADR 更新**：新增或修订 ADR，正式记录「通用网页搜索提供方为 Tavily（自带 Key，安装期凭据库收集）」，废止 DDG-only 决策并注明取代关系与日期；明确 DDG 代码去留（生产不注册；测试夹具保留或删除，二选一并记录理由）。
2. **文档同步**：`CONTEXT.md`、runbooks（安装/部署/故障处理）更新为 Tavily 流程：安装时依次输入 Qwen Key 与 Tavily Key；缺 Key 行为；限流/401 的运维处置。`docs/agents/` 涉及搜索的描述同步。
3. **能力清单与分类收口**：通用网页搜索在机器可读清单中分类为 `external_non_qwen`（带自有凭据、不继承 Qwen Key）；发布门断言生产提供方清单恰好只有 `tavily`。
4. **密钥泄漏硬门**：`artifact_secret_scan.py` 增加 Tavily Key 形态（`tvly-` 前缀）扫描；发布门断言日志、运行锁、消息投影、SSE 记录与发布报告不含 Tavily/Qwen Key 值。
5. **真实 smoke（opt-in）**：使用安装级凭据对 Tavily 执行最小成本真实搜索 + 正文获取，输出 `passed`/`failed`/`inconclusive`；缺 Key/无网络为 `inconclusive`，不得伪通过。与既有 Qwen live suite 并列运行、互不继承凭据。
6. **发布门总装**：一条命令组合——能力分类完整、生产组合 Tavily-only、Issue 03 金标路由全绿、Issue 02 降级语义抽查（本地不足 + 搜索失败 → 带标注降级且零进度推进）、密钥零泄漏、真实 smoke 结果归档（脱敏）。

## 非目标

- 不重复实现 Issue 01–06 的功能代码；本 issue 只做收口、文档与发布门。
- 不接入任何第三搜索提供方；不为 DDG 保留生产 fallback。
- 不在任何文档/票据/报告中记录真实 Key 值。

## Acceptance criteria

- [ ] ADR 记录 Tavily 决策与 DDG 废止，并被 `CONTEXT.md`/runbooks 引用一致；DDG 代码去留有明确结论。
- [ ] 能力清单中通用网页搜索恰好分类一次且为 `external_non_qwen`；生产组合断言通过。
- [ ] 密钥扫描覆盖 `tvly-` 形态；发布门对泄漏失败关闭。
- [ ] 真实 Tavily smoke 三态输出正确，`inconclusive` 不计入通过。
- [ ] 发布门单命令运行，任一硬门失败非零退出；报告只含脱敏证据。
- [ ] 金标路由与降级语义作为发布门组成部分被执行（非仅引用）。

## Test plan

1. 发布门各组成项的单测/组合测试（清单分类、提供方断言、扫描规则、三态 smoke 判定）。
2. 用注入式假 Key/假日志验证泄漏门失败关闭。
3. 显式联网环境完整执行一次发布门并归档脱敏报告。

建议回归命令（实现者可按最终命令名等价调整）：

```powershell
python scripts/release_gate.py --real-probes --qwen-authenticity
python -m pytest tests/closeout tests/contracts -q -p no:cacheprovider --basetemp=$env:TEMP\bridges-r7-issue07
```

## Observability & rollback

- 发布报告字段允许列表：build、capability、类别、provider、状态、延迟、脱敏错误类别；无任何 Key 与用户内容。
- 回滚：发布门新增项可单独临时豁免并记录理由；ADR 一经合并不回滚，只以新 ADR 修订。

## Blocked by

01、02、03、04、05、06。

## Comments

- 2026-08-14：本 issue 是本轮唯一发布门；波次 B 在 01–06 全部完成后执行。
- 2026-08-14：DDG-only 冻结（第 6 轮决策 #2）由本轮决策 #1 正式取代，以此 issue 的 ADR 为准。
