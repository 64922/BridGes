# BridGes 第八轮：人味化交付、Qwen 连接与 arXiv 可靠性修复方案

Decision status: approved

Last updated: 2026-08-15

## 目标

把用户《八次改进》中实际遇到的三个生产问题修到可验证：人味化润色交付成品而非拦截警告、对话瞬时连接抖动自愈且报错准确、论文搜索间歇性超时有兜底。

- 人味化请求的规则在生成端与校验端一致；修订仍失败时，机械可剔除类违规经确定性剔除后交付成品并如实标注，只有破坏原文事实才拦截。
- Qwen 连接类失败在流式建连阶段自动重试一次；DNS/代理/TLS 细分报错给可操作指引；启动时自检预警环境配置问题。
- arXiv 瞬时超时在预算内自动重试；上游持续故障时重复查询回退到标注「可能不是最新」的陈旧缓存；首次搜索不再付 worker 冷启动。

本方案的可靠性定位是：

> **生成与校验同一份规则、能交付成品就不拦截、瞬时失败先自愈、报错必须可操作。**

## 用户故事

- `US-01`：作为人味化用户，我润色文章时拿到的是成品回复；系统新增的违规内容被自动剔除并告知我移除了什么，而不是甩给我一条拦截警告。
- `US-02`：作为人味化用户，我明确允许的假设/第一人称写法在生成和校验两端规则一致；我原文里已有的数字不会被误判为"无来源新增"。
- `US-03`：作为对话用户，瞬时连接抖动不再直接变成报错；真连不上时，我能分清是 DNS、代理还是证书问题并知道怎么处理，而不是一句泛泛的"检查网络"。
- `US-04`：作为论文搜索用户，瞬时超时后系统自动重试；上游持续故障时，重复查询我也能拿到标注了时效性的结果。

## 已验证现状

### 人味化「来源保真硬门」拦截

生产观测（截图 090221）：人味化请求（原文含「Transformer 2017 年谷歌团队……」）思考 119 秒后被拦：「候选使用了「比如/假设/设想」标注的假设内容，但任务契约不允许。；候选新增「2017 年」（数字与单位）没有账本来源。」UI 显示「未交付 / 保真 2 项未通过 / 已定向修订」。

- 硬门为生成后确定性校验（`src/bridges/skills/humanizer/source_ledger.py:621`），ADR-0027 规定「首稿→至多一次定向修订→仍失败停止交付」，写作调用上限 2 次（`service.py:153`）。截图中修订已触发但仍未通过。
- 缺陷 a（权限映射断裂）：`intent.py:89-105` 未把表达契约 `hypothetical_permission`/`first_person_permission` 映射进旧契约 `allow_assumptions`/`allow_first_person`；prompt 权限行（`draft_compiler.py:212-225`）与硬门判定（`service.py:1095`）读两份不同真值。
- 缺陷 b（重试死胡同）：计数经 `humanizer_recovery_state()`（`turn.py:1478-1502`）恢复，2 次用尽后点「重试」撞 `writing_call_limit_reached`（`service.py:692-697`）。
- 缺陷 c（疑似数字匹配）：原文已含「2017 年」仍判 `UNATTRIBUTED_CLAIM`（`source_ledger.py:1017`），疑似规范化键（空白/全半角）或账本编译漏配，需复现定位。

### arXiv 间歇性搜索超时

生产观测（截图 095411）：「arXiv 搜索超时，请重试。」第 7 轮 Issue 05 已落地缓存/节流/冷却，间歇性仍在：Windows worker 冷启动数秒 + 3s 节流排队计占预算 + HTTP 超时=剩余预算（`client.py:136-137`）+ HTTP 层零重试 + 境外上游抖动；超时终态再激活 20s 冷却（`service.py:327-330`），立即重试继续失败，放大感知。

### Qwen「无法连接」误报

生产观测（截图 223443）：思考 2 秒即报「无法连接 Qwen 服务，请检查网络后重试。」，用户网络正常。该文案 = `region_error` = 任意 `httpx.ConnectError`（DNS/TLS/代理/连接重置，`qwen_client.py:255` 等四处）；流式聊天路径零重试（重试策略仅非流式）；无 fallback（`production.py:62-65` 既定策略）；错误文案五处重复硬编码且不对等，`client_error_*` 在聊天路径漏英文内部消息（`turn.py:588-592` vs `errors.py:90-92`）。2 秒失败吻合快速连接失败（失效代理环境变量或 `BRIDGES_QWEN_WORKSPACE_ID` 主机名拼错为两大嫌疑）。

## 已冻结的 grilling 决策

1. **人味化硬门失败后确定性剔除交付**：修订仍失败且剩余 blocking 全属机械可剔除码（`UNATTRIBUTED_CLAIM`/`ASSUMPTION_NOT_ALLOWED`/`ASSUMPTION_CARRIES_FACT`/`FIRST_PERSON_UNBOUND`）时，确定性整句剔除后重检交付并标注移除项；破坏原文事实类（引语/数字篡改、因果反转等）维持拦截；零额外模型调用。
2. **arXiv 可靠性组合包**：HTTP 层 1 次预算门控自动重试（429/4xx 不重试）+ 过期缓存 stale 兜底（标注「结果可能不是最新」）+ 应用启动预热常驻 worker + 阶段预算 15s→20s；不引入镜像端点。此决策推翻第 7 轮 Issue 05 的「轮内不自动重试」非目标。
3. **Qwen 重试+分类+诊断**：流式建连阶段 ConnectError 自动重试 1 次；ConnectError 细分为 `region_dns`/`region_proxy`/`region_tls` 并配可操作文案；五处错误文案映射统一为单一来源并补 `client_error_` 分支；启动期 Qwen 连通性自检（非阻断、不记敏感信息）。不加备用模型 fallback。
4. **按 4 张 ticket 执行，缺陷先行**：01 人味化缺陷 → 02 剔除交付（依赖 01 结论）→ 03 Qwen → 04 arXiv；每张独立验收。

## Issue 地图

| # | Issue | Priority | Status | Blocked by |
| --- | --- | --- | --- | --- |
| 01 | [人味化保真缺陷修复——权限映射、数字账本匹配与重试死胡同](issues/01-humanizer-permission-mapping-retry-defects.md) | P0 | ready-for-agent | None |
| 02 | [人味化确定性剔除交付——机械违规剔除后交付成品](issues/02-humanizer-deterministic-excision-delivery.md) | P0 | ready-for-agent | 01 |
| 03 | [Qwen 连接失败修复——流式重试、错误细分与启动诊断](issues/03-qwen-connect-retry-error-taxonomy.md) | P0 | ready-for-agent | None |
| 04 | [arXiv 搜索可靠性组合包——有界重试、陈旧缓存兜底与 worker 预热](issues/04-arxiv-retry-stale-cache-warmup.md) | P1 | ready-for-agent | None |

### 推荐执行波次

1. 波次 A：01、03、04 并行（三者代码面基本不交叉）。
2. 波次 B：01 合入后执行 02（剔除交付建立在校准后的硬门之上）。

## 全局完成定义

- 4 张 Issue 的验收标准、测试计划与依赖全部完成。
- 人味化：授权假设/第一人称两端规则一致；原文数字不再误判无来源；机械违规剔除后交付成品并标注；破坏事实仍拦截；重试不再死胡同；ADR-0027 addendum 合入。
- Qwen：流式建连瞬时失败自动重试一次；DNS/代理/TLS 报错可操作且不再泛泛"检查网络"；错误文案单一来源；启动自检预警；`client_error_*` 不漏英文。
- arXiv：瞬时超时预算内自动重试；上游故障时 stale 缓存兜底并标注；首搜无冷启动；阶段预算 20s；三开关独立可回滚。

## 风险与处理

- **剔除交付可能改变文章原意**：粒度整句 + 剔除后重跑全套检查 + 占比超 40% 或空稿即拦截 + 移除清单如实披露；破坏事实类永不剔除。
- **流式重试放大上游压力**：仅限建连阶段、单次、短退避；delta 已下发不重试；重试次数入遥测。
- **stale 缓存结果陈旧**：仅失败时兜底、24h 保留窗口、显著标注时效性；正常路径仍是真实 Atom 响应。
- **错误细分误判**：无法判定时一律回落 `region_error` 原文案；细分依据异常因果链，测试矩阵钉住映射。
- **预算上调挤压整轮**：arxiv 阶段 20s 仍在 120s 整轮预算内，与 web 并行场景交接预留不变。

## Comments

- 2026-08-15：基于用户《八次改进》清单、3 张生产截图与三路代码只读探查（explore 子代理）完成诊断；grilling 冻结 4 项决策（剔除交付 / arXiv 组合包 / Qwen 重试分类诊断 / 4 张 ticket 缺陷先行）。
- 2026-08-15：本 README 汇总批准决策与执行地图；单张 Issue 的验收标准是实现权威，发生冲突时先更新本 README、相关 ADR 与 Issue，再修改生产合同。
