# 通用网页搜索唯一生产提供方收口为 Tavily（自带凭据）与发布门

> 决策日期：2026-08-14（第 7 轮 grilling 冻结决策 #1）；本 ADR 由 Issue 07
> 于 2026-08-15 正式收口。
>
> 取代：
>
> - 第 6 轮冻结决策 #2（DDG-only：通用搜索只用 DuckDuckGo、不接备用 Key）；
> - [ADR-0028](0028-ddg-health-probe-and-release-gate.md)《DDG 健康证据闭环》
>   的唯一提供方、分层探针与发布语义条款（保留历史审计含义）；
> - [ADR-0026](0026-frozen-product-contracts-and-migration-gates.md)
>   Issue 02 节的 DDG-only 更新注记（2026-08-13）。
>
> 本 ADR 一经合并不回滚，后续变更只以新 ADR 修订。

## 背景

第 6 轮冻结决策 #2 规定通用搜索只用 DuckDuckGo（DDG）、不申请任何备用
Key。实测该网络环境下 `html.duckduckgo.com` 与 `duckduckgo.com` 均连接
超时，DDG-only 合同永远无法成功；Tavily API 实测可达并返回真实结果，用户
已提供 Tavily Key。第 7 轮冻结决策 #1 决定以 Tavily 取代 DDG，本 ADR 正式
记录该决策、凭据纪律、DDG 代码去留与发布门合同，避免文档与生产行为分叉。

## 决策

1. **唯一生产提供方**：通用网页搜索（学习模式强制联网、日常按需联网与
   运维健康探针）的唯一生产提供方是 **Tavily**（Tavily Search/Extract
   REST API，产品后端直连，不引入 MCP 形态）。生产组合注册表不再登记
   DuckDuckGo；任何第二提供方（Brave/Bing/Serper/SearXNG 等）的备用
   客户端、备用 Key 或 fallback 开关都视为配置漂移，发布门以稳定错误码
   `unexpected_search_provider` 失败关闭，不存在静默 fallback。
2. **自带凭据、与 Qwen Key 同等纪律**：Tavily 属于 `external_non_qwen`
   能力，但**自带凭据**。安装流程（`BridGes start`）在 Qwen Key 之后
   依次提示输入 Tavily Key，保存到操作系统凭据库的独立 credential id
   （`global-tavily-api-key`）；运行合同为
   `BRIDGES_TAVILY_API_KEY` / `BRIDGES_TAVILY_API_KEY_FILE`
   （含旧兼容别名 `SCIENCE_COMPANION_TAVILY_API_KEY(_FILE)`）。
   Key 值不进入仓库、票据、日志、运行锁、消息投影、SSE 事件或发布报告；
   检索阶段不读取、不继承、不发送 Qwen Key。
3. **缺 Key 行为**：缺 Tavily Key 时应用正常启动，联网搜索入口返回
   「未配置搜索凭据」投影，前端如实标注不可用，不伪装成功、不静默退回
   DDG。学习模式联网失败按第 7 轮 Issue 02 降级语义走「本轮未联网核实」
   的带标注模型知识回答（本地材料冲突时保持拒绝）。
4. **限流/401 运维处置**：
   - 401/403：配置错误（`web_search_configuration`），不可重试，中文
     文案提示检查 Tavily API Key；运维先复核凭据配置再重试；
   - 429：限流（`web_search_rate_limit`），不立即重试，服务层进入冷却；
     运维按 Tavily 配额等待后重试；
   - timeout/connect/DNS/5xx：可重试的外部故障，运维先确认网络与上游
     状态；
   - 任何错误投影不得包含 Key、完整请求或响应正文。
5. **DDG 代码去留：保留为测试夹具，生产不注册**。理由：
   - `src/bridges/web_search/client.py` 承载共享检索工具
     （`WebSearchError`、连接/DNS 分类、有界读取、URL 安全校验等），
     生产 Tavily 路径仍依赖这些工具，直接删除需要搬迁共享件；
   - `DuckDuckGoClient` 与其合同测试（`tests/web_search/
     test_duckduckgo_service.py`）作为冻结契约锚点，证明历史能力已
     冻结且不会复活到生产组合（与 Brave 客户端的合同锚点处理一致）；
   - 生产注册表、组合根与健康聚合不出现 `duckduckgo`；不保留任何静默
     fallback。静态扫描与发布门确保上述不变量。
6. **发布门合同**：本 ADR 生效后，发布门（`scripts/release_gate.py`
   Issue 07 总装）断言：
   - 能力清单中通用网页搜索恰好分类一次且为 `external_non_qwen`；
   - 生产组合通用网页搜索提供方清单**恰好只有 `tavily`**；
   - 产物扫描覆盖 Tavily Key 形态（`tvly-` 前缀）与 Qwen Key 形态，
     日志/运行锁/消息投影/SSE 记录/发布报告零泄漏，并对持久化落库表
     （运行锁、消息、SSE 记录投影）做存储级 Key 形态扫描；
   - opt-in 真实 smoke（搜索 + 正文获取）输出 `passed`/`failed`/
     `inconclusive`，缺 Key/无网络为 `inconclusive`，不伪通过；
   - 金标路由（Issue 03）与降级语义（Issue 02）抽查作为门禁组成部分
     执行，任一硬门失败非零退出。

## 影响

- 文档：CONTEXT.md、README 安装流程与 runbooks 更新为 Tavily 流程
  （安装时依次输入 Qwen Key 与 Tavily Key、缺 Key 行为、限流/401 处置）。
- 清单：`PRODUCTION_CAPABILITY_MANIFEST` 中 `tavily_web_search` 唯一
  分类为 `external_non_qwen`（带自有凭据、不继承 Qwen Key）。
- 历史：ADR-0028 与 ADR-0026 的 DDG-only 条款只保留历史审计含义，不再
  作为当前实现依据。

## 回滚

- 发布门新增项可单独临时豁免并记录理由（Observability 合同允许列表
  不变：build、capability、类别、provider、状态、延迟、脱敏错误类别；
  不含任何 Key 与用户内容）。
- 本 ADR 一经合并不回滚，只以新 ADR 修订。
