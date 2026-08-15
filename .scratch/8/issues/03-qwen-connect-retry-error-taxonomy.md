# Issue 03：Qwen 连接失败修复——流式重试、错误细分与启动诊断

Status: ready-for-agent

Type: task

Priority: P0

User stories: US-03

## 已验证现状与根因

生产观测（2026-08-15 截图）：普通对话「根据我的学业情况和阶段目标，制定一下学习计划」，思考仅 2 秒即报「无法连接 Qwen 服务，请检查网络后重试。」用户网络正常。代码事实：

- 该文案只对应 `region_error`（`src/bridges/chat/turn.py:471`、`src/bridges/ai/errors.py:70`），`RegionError` 唯一抛出点是 `httpx.ConnectError`（`src/bridges/ai/qwen_client.py:255` 流式、:434 非流式、:356/:523 DashScope）。**ConnectError 覆盖面远大于"断网"**：DNS 解析失败、TLS 证书校验失败（代理/杀软 SSL 审查）、连接重置、代理不可达均归入此码，文案却让用户"检查网络"。
- **2 秒即失败 = 快速连接失败指纹**（TCP RST 毫秒级、TLS 握手失败数百毫秒、DNS 多 resolver 轮换约 1-2s），不是超时（超时 60s，`src/bridges/model_call_budget.py:20`）。
- **流式聊天路径零重试**：`qwen_text_chat` 注册的 `RetryPolicy(max_attempts=3)`（`src/bridges/ai/production.py:79`）只作用于非流式 `invoke`；流式路径（`turn.py:3283` → `model_gateway.py:216` → `qwen_adapters.py:72-112`）无任何重试，`RegionError(retryable=False)` 立即 BLOCKED（`model_gateway.py:525-546`），无 fallback（`production.py:62-65` 明确不注册备用模型）。
- 头号配置嫌疑：httpx 默认 `trust_env=True`，失效的 `HTTPS_PROXY` 等代理环境变量会让连接毫秒级失败；`BRIDGES_QWEN_WORKSPACE_ID` 配错会拼出不存在主机名 → DNS 失败 1-2s（`qwen_client.py:161-172`）。
- 错误文案映射**五处重复硬编码且不对等**：`turn.py:471`、`errors.py:70`、`image/service.py:97`、`video/service.py:103`、`speech/service.py:60`；`turn.py:user_facing_error`（:588-592）缺 `client_error_` 前缀分支，聊天路径会把英文内部消息 "Qwen client error (400). …" 直接漏给用户（`turn.py:3375-3377`），而 `errors.py:90-92` 有该分支。

已冻结决策（本轮 grilling #3）：流式重试 + 错误细分 + 文案统一 + 启动诊断；**不**加备用模型 fallback。

### 上下文指针

- `src/bridges/ai/qwen_client.py:200-259`（流式）、:255/:434/:356/:523（ConnectError→RegionError）、:161-172（base_url 形态）。
- `src/bridges/ai/model_gateway.py:216`（stream 入口）、:525-546（BLOCKED 语义）；`src/bridges/chat/turn.py:3283`（调用点）。
- `src/bridges/ai/errors.py:70,90-92`（共享文案表）、`src/bridges/chat/turn.py:466-471,588-592`（聊天侧映射）。
- 装配与策略：`src/bridges/ai/production.py:62-82,287-312`；模型 ID `src/bridges/ai/fixed_models.py:18`。
- 既有测试：`tests/ai/test_qwen_real_adapter.py:362-401`、`tests/ai/test_model_gateway.py:216-234`、`tests/integration/test_qwen_capability_gateway.py:140-212`、`tests/chat/test_chat_service.py:344-379`。

## What to build

1. **流式连接重试**：流式聊天路径在**尚未下发任何 delta** 的建连阶段遭遇 `RegionError` 时自动重试 1 次（短退避，约 1s）；已开始下发 delta 后的中断不重试（语义同现有 `stream_interrupted`）。重试次数计入遥测；重试再败按分类后错误码呈现。
2. **ConnectError 细分**：在 `qwen_client.py` 抛出点按异常因果链细分（`__cause__`/消息特征）：DNS 失败 → `region_dns`、代理不可达/被拒 → `region_proxy`、TLS 证书校验失败 → `region_tls`，无法判定时回落 `region_error`。新增码配可操作中文文案（如「无法解析 Qwen 服务域名，请检查 DNS 或代理设置」「连接被代理拒绝，请检查代理配置」「安全证书校验失败，可能存在 SSL 审查软件」）；`RegionError` 携带子类信息，不改变其网关 BLOCKED 语义。
3. **文案映射统一**：以 `errors.py` 的 `MODEL_CALL_ERROR_MESSAGES_ZH` 为唯一来源，`turn.py` 与 image/video/speech 三服务改为引用；`turn.py:user_facing_error` 补 `client_error_` 前缀分支（对齐 `errors.py:90-92`），英文内部消息不再漏给用户。
4. **启动连通性自检**：应用启动时（`src/bridges/api/main.py` 启动钩子或 `production.py` 装配处）做非阻塞检查：解析 base_url 主机名（DNS 预检）、检测代理环境变量可用性、`BRIDGES_QWEN_WORKSPACE_ID` 形态校验；异常仅输出可操作日志警告，不阻断启动，不记录 Key 等敏感信息。

## 非目标

- 不注册备用模型、不做任何形式的模型降级/切换（维持 `production.py:62-65` 策略）。
- 不改变非流式路径既有 `RetryPolicy`（3 次指数退避）。
- 不改变 401/403/429/5xx 的既有分类与文案。
- 不做联网探测之外的环境改写（不自动修改用户代理设置）。

## Acceptance criteria

- [ ] 流式建连阶段 ConnectError 自动重试 1 次；首次重试成功则用户无感知；重试再败呈现细分后文案。delta 已下发后的中断不重试。
- [ ] DNS/代理/TLS 三类失败分别呈现对应可操作文案；无法判定时回落原文案；`region_error` 的网关语义（立即 BLOCKED、不 fallback）不回归。
- [ ] 聊天与 image/video/speech 的错误文案来自同一映射源；`client_error_*` 在聊天路径呈现中文通用文案，不再漏英文内部消息。
- [ ] 启动自检在 DNS 失败/代理失效/workspace 主机名异常时输出可操作警告日志，且不阻断启动、不含敏感信息。
- [ ] `tests/ai/`、`tests/chat/`、`tests/integration/test_qwen_capability_gateway.py` 回归全绿。

## Test plan

1. 单测：异常因果链 → `region_dns`/`region_proxy`/`region_tls`/`region_error` 映射矩阵；turn 与 errors 两处文案一致性（含 `client_error_` 分支）。
2. 流式重试：Mock 建连失败→重试→成功（用户无感知、retry 计数=1）；重试再败→准确错误码；delta 后中断→不重试。
3. 启动自检：fake socket/DNS 模拟各失败形态的日志断言；正常环境无警告。
4. 回归命令：

```powershell
python -m pytest tests/ai tests/chat tests/integration/test_qwen_capability_gateway.py -q -p no:cacheprovider --basetemp=$env:TEMP\bridges-r8-issue03
```

## Observability & rollback

- 指标：流式重试次数/成功率、各 `region_*` 子码分布（判断用户环境主因）；日志不含 Key 与请求正文。
- 回滚：流式重试可用常量开关（如 `STREAM_CONNECT_RETRY_ENABLED`）关闭；细分子码回落 `region_error` 等价旧行为；文案统一为纯重构可整体还原。

## Blocked by

无。

## Comments

- 2026-08-15：用户环境主因需结合子码遥测与启动自检日志确认（失效代理环境变量与 workspace 主机名为两大嫌疑）；本 issue 先把"报得准、抖动能自愈、启动能预警"落地。
