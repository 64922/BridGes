# 三条旅程收尾发布门运行手册

本手册对应 `.scratch/收尾/issues/09-three-journey-release-gate.md`，用于在发布前重复验证学习、画像和论文三条真实 API/UI 旅程。它遵循 [ADR-0025](../adr/0025-unified-stage-clock-and-latency-budget.md) 的截止时间与取消语义，以及 [ADR-0026](../adr/0026-frozen-product-contracts-and-migration-gates.md) 的当前产品与迁移契约；邮件提醒不属于本发布门。

## 1. 标准运行

在仓库根目录执行：

```powershell
.venv\Scripts\python.exe scripts\release_gate.py --report .tmp\release-gate\report.json
```

这条命令只使用显式的测试 fixture，不访问互联网，运行以下内容：

- SQLite 临时数据库上的三条 API 旅程、重启回放、账户隔离和画像状态断言；
- 学习证据门、论文搜索、arXiv worker 取消/超时、公开搜索主备 provider 和安全回放回归；
- 收尾代码面的 Python lint/typecheck、Web 单测、TypeScript 类型检查和真实 Chromium 浏览器旅程。

诊断单一层级时可以跳过对应层，但跳过项会写入 `risks`，报告不会被当作完整发布证据：

```powershell
.venv\Scripts\python.exe scripts\release_gate.py --skip-browser --skip-web --report .tmp\release-gate\offline.json
```

## 2. 显式真实 provider 探针

只有需要验证当前外部 provider 时才运行网络探针。通用网页搜索的唯一生产
提供方是 Tavily（ADR-0029）：凭据经安装流程或 `BRIDGES_TAVILY_API_KEY`
（或 `_FILE`）注入，**不需要也不允许配置任何备用提供方 Key/开关**（出现
即配置漂移，发布门失败关闭）：

```powershell
# Tavily Key 已配置（安装流程/环境变量）后：
.venv\Scripts\python.exe scripts\release_gate.py --real-probes --report .tmp\release-gate\real-report.json
```

探针固定记录 provider、语义健康、耗时、脱敏错误类别和 worker 清理结果。
命令行输出和报告都不保存响应正文、用户原始问题、profile 正文或 secret。
未配置 Tavily Key 时会明确记录 `environment_misconfigured`（状态
`inconclusive`）；这不是把未验证状态伪装成成功。

失败责任边界只有三类：

- `product_failure`：代码、契约、测试或浏览器行为失败，阻断发布；
- `external_unavailable`：provider 超时、连接失败、限流或语义健康失败，阻断本次真实探针结果，但按可重试外部故障处理；
- `environment_misconfigured`：浏览器、命令、依赖或真实 provider 配置缺失，先修环境再宣称发布门通过。

离线门不含真实探针时会带有 `real_provider_probes_not_run` 风险；这表示“离线回归通过”，不是“外部 provider 已验证”。

## 2.1 第 7 轮发布门总装（Issue 07）

第 7 轮收口后，发布门单命令组合为：

```powershell
.venv\Scripts\python.exe scripts\release_gate.py --real-probes --qwen-authenticity --report .tmp\release-gate\report.json
```

其中 `--qwen-authenticity` 追加 Issue 17 真实性门（能力清单完整性、静态
扫描、生产组合、live suite 与重启锁复查），Issue 07 的组成项包括：

- 能力清单：通用网页搜索恰好分类一次且为 `external_non_qwen`
  （`tavily_web_search`）；
- 生产组合断言：通用网页搜索提供方清单**恰好只有 `tavily`**；
- 金标路由（Issue 03）与降级语义（Issue 02：本地不足 + 搜索失败 → 带
  「本轮未联网核实」标注的降级且零进度推进）作为门禁组成部分被执行；
- 密钥泄漏硬门：`scripts\artifact_secret_scan.py` 覆盖 `tvly-` 与 Qwen
  Key 形态，日志、运行锁、消息投影、SSE 记录与发布报告零泄漏，泄漏即
  失败关闭；
- 真实 Tavily smoke（搜索 + 正文获取）输出 `passed`/`failed`/
  `inconclusive`，缺 Key/无网络为 `inconclusive` 且不计入通过，结果以
  脱敏字段归档进报告。

任一硬门失败都以非零退出；报告字段只含 build、capability、类别、
provider、状态、延迟与脱敏错误类别，不含任何 Key 与用户内容。运维诊断
单次探针用 `python scripts\release_gate.py --web-health`。详见
[search-provider-release-gate 运行手册](search-provider-release-gate.md)。

## 3. 缺陷簇处置

### 学习与公开资料

复现输入：`我想学习Transformer架构的相关知识`。检查 `/health/ready`、搜索 provider 健康和教学卡的证据门；重点错误码为 `web_search_provider_challenge`、`web_search_evidence_insufficient`、`web_search_citation_invalid` 和 `web_search_all_providers_failed`。

同一用户回合显式重试，确认新的请求没有复用旧回合的失败投影；不得以无引用的回答替代证据门。若主 provider 持续异常，按 provider 开关停用故障路径或回滚最近的 provider 配置，保留“未核实”的统一提示。

### 论文与 arXiv worker

复现输入：`给我找几篇Transformer方向相关的论文`。检查论文卡、结果链接、截止时间和 worker 是否退出；重点错误码为 `arxiv_timeout`、`arxiv_worker_exit`、`arxiv_parse` 和 `arxiv_citation_invalid`。

在原会话显式重试，确认并发重试不会串写结果且没有孤儿 worker。若 worker 版本或路由回归，回滚到上一份已验证的 arXiv 路由并保留可重试错误；不要把超时或解析失败投影为“没有论文”。

### 四维画像与安全回放

按以下顺序复现：学习输入、`我是一名人工智能专业的大三学生，目标是考一个211院校的相关专业，给我规划一下考研`、论文输入。检查 `/profiles/four-dimensions` 与 `/profiles/status` 的 `empty`、`pending`、`failed`、`ready` 状态，确认知识兴趣、学业情况和阶段目标有证据，兴趣爱好保持空白，并在刷新/重启后保持一致。

若画像抽取失败，记录 `profile_extraction_contract_invalid` 或对应稳定错误码，先按抽取 worker 的可重试策略重试。涉及数据回滚时，先使用既有 [profile-v2-replay 备份/回放手册](profile-v2-replay.md) 创建并校验备份，再执行 dry-run 回放；恢复后检查 schema version、账户隔离、撤回/删除/停止/隐私状态和幂等回放。报告只记录状态、版本和错误类别，不记录画像原文。

## 4. 发布判定与证据

报告包含 `code_version`、`config_category`、`schema_version`、确定性检查、真实 provider 探针、`risks` 和 `release_ready`。确定性检查、浏览器旅程或类型检查失败时为 `blocked`；真实探针失败时必须携带 provider 和稳定错误类别，不能只写“失败”。

发布前保存 JSON 报告及对应命令、配置类别、数据库 schema 版本和风险摘要。报告应可由另一位执行者在同一代码版本重跑；任何 secret、完整消息或用户 profile 文本一旦出现在 artifact 中，都应先停止发布并运行 `scripts\artifact_secret_scan.py` 排查。
