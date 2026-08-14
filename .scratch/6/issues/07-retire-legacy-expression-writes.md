# Issue 07：退役旧 Expression 写入口，停止确定性草稿伪装为 Qwen 成功

Status: resolved

Type: task

Priority: P0

User stories: US-RETIRE-EXPR-01、US-RETIRE-EXPR-02、US-QWEN-TRUTH-01

## 根因与证据

- `src/bridges/api/expression.py` 仍公开创建草稿、风格诊断、补丁应用、反馈、审批、发布和版本比较等旧合同，并把请求交给 `ExpressionService`。
- `src/bridges/expression/service.py` 的默认 `_DeterministicDraftGenerator` 可直接产出正文；生产组合没有与 `expression_draft_generation` 对应的真实 adapter 时，旧入口仍可能返回看似成功的草稿。
- `src/bridges/api/main.py::_register_builtin_capabilities` 又把 `expression_draft_generation` 登记为 Qwen `MODEL` 能力，且硬编码 `qwen3.6-flash`。因此“能力声明、实际执行、运行锁”三者不一致，HTTP 成功不能证明使用了用户确认的全局 Qwen Key。
- ADR-0026 已冻结产品合同：旧写 API 在兼容窗口内应稳定返回 `410 Gone`、中文说明和替代路径；历史对象只能按只读/导出策略保留。

## What to build

1. 将以下旧入口改为兼容期退役路由，认证成功后稳定返回 `410 Gone`：
   - `POST /expression/drafts`
   - `POST /expression/drafts/{draft_id}/style-diagnostic`
   - `POST /expression/drafts/{draft_id}/patches/{patch_id}/apply`
   - `POST /expression/drafts/{draft_id}/feedback`
   - `POST /expression/drafts/{draft_id}/approve`
   - `POST /expression/drafts/{draft_id}/publish`
   - `POST /expression/drafts/compare`（虽为比较操作，但属于旧的正文请求合同，兼容期同样退役）
2. 复用 `bridges.retirement.raise_retired_capability` 与 `RetiredCapabilityError`，为每个入口提供稳定 endpoint ID、错误码 `legacy_expression_retired`、中文说明和现代聊天产品替代路径；不得回显路径参数或正文。
3. 退役 handler 不注入 `ExpressionService`，不声明旧 Pydantic 请求体，也不读取请求流；畸形、超大或字段缺失的正文在通过认证后仍应得到同一 410 合同。
4. 从生产 capability 注册表删除 `expression_draft_generation`，并移除仅为该旧入口存在的生产 adapter/组合代码；测试替身不得再让生产组合看起来可用。
5. 保留 ADR-0026 要求的隐私安全兼容观测：区分真实流量和带 `x-bridges-compatibility-probe` 的探针，只记录 endpoint ID、版本、流量类别和状态码。
6. 审核现代聊天 Humanizer 链路，证明它不依赖这些旧路由；替代路径应指向现有聊天入口，不得新建第二套生成 API。

## 非目标

- 不在本 issue 中删除 Expression 历史表、历史草稿或导出能力。
- 不把旧 deterministic generator 改造成新的 Qwen 产品入口；用户已确认退役旧 API。
- 不重写现代聊天 Humanizer；其真实调用和锁闭环由 Issue 11 负责。
- 不把 GET 历史读取入口直接改成 404；其最终收缩须满足 ADR-0026 Contract 门禁。
- 不修改全局 Qwen Key 的安装级凭据策略，也不在错误或观测中记录 Key、正文、ID 或模型输出。

## Acceptance criteria

- [ ] 上述七个入口对已认证账户均返回 HTTP 410，响应包含稳定错误码、中文退役说明和现代聊天替代路径。
- [ ] 同一入口在正常 JSON、畸形 JSON、空正文和未知对象 ID 下返回同构 410；不会先返回 404/422，也不会读取旧对象。
- [ ] 未认证请求仍遵循统一认证边界，不泄露任何账户或对象是否存在。
- [ ] 调用任一退役入口不会调用 `ExpressionService`、确定性生成器、模型网关或 Qwen 客户端，也不会创建草稿、版本、反馈、审批、发布事件或 `model_run_lock`。
- [ ] production registry 中不存在 `expression_draft_generation`；启动检查发现该能力重新注册时失败关闭。
- [ ] 现代聊天 Humanizer 主链的 API/服务回归通过，且不引用 `/expression/*`。
- [ ] 兼容观测只记录稳定 endpoint ID；真实流量与探针分开计数，日志和响应均不包含请求正文与路径对象 ID。
- [ ] 历史数据及现有只读/导出能力未被物理删除，最终 404 收缩仍由 ADR-0026 门禁控制。

## Test plan

1. 新增 `tests/integration/test_expression_retirement.py`，参数化七个入口，断言 410、错误码、中文说明和替代路径。
2. 为每个带正文入口至少覆盖合法 JSON 与畸形 JSON；以 service/model spy 断言无 service、generator、gateway、adapter 调用。
3. 在临时数据库前后比较 Expression 相关表和 `model_run_locks` 行数，断言无副作用；重复调用结果幂等。
4. 增加 production registry 合同测试，断言 `expression_draft_generation` 未注册且无法被工作流解析。
5. 保留/调整旧集成测试：成功写入断言必须删除或转换为退役契约测试；不得用 skip 掩盖冲突合同。
6. 执行现代聊天 Humanizer 回归，确认替代旅程可达。

建议验证命令：

```powershell
python -m pytest `
  tests/integration/test_expression_retirement.py `
  tests/humanizer `
  tests/chat/test_humanizer_chat.py `
  -q -p no:cacheprovider `
  --basetemp=$env:TEMP\bridges-issue07
```

## Observability & rollback

- 指标按稳定 endpoint ID 聚合 `real_410_total` 与 `probe_410_total`，禁止 account、draft、patch、正文等高基数字段。
- 发布后观察旧入口真实流量；只有连续观察窗口为零且 ADR-0026 其他门禁全部满足，后续任务才可由 410 收缩为 404。
- 若 410 路由映射错误，可回滚路由层改动，但不得恢复确定性假成功或重新注册伪 Qwen capability；历史数据保持原状，无需数据回滚。
- 建议稳定错误码：`legacy_expression_retired`、`retired_route_active`（门禁发现回归时）。

## Blocked by

- 无。

## Comments

- 2026-08-13：用户确认旧 `/expression` 写 API 退役，不要求为其接入真实 Qwen；现代聊天产品中的 Humanizer 才是继续支持的生成入口。
- 2026-08-13：本 issue 采用 tracer bullet：先关闭可产生“伪模型成功”的完整旧纵向入口，再由 Issue 09、11 和 17验证生产模型真实性。
- 2026-08-14：实现完成。提交 `1737a88`（退役七个写路由、移除 `expression_draft_generation` 注册与 model_gateway 接线、新增退役集成测试）与 `9d88f32`（防复活失败关闭检查 + openapi.json 重新生成）。

## Answer

- 已在 `issue07-retire-legacy-expression-writes` worktree 完成退役：`src/bridges/api/expression.py` 中七个旧写入口（drafts、style-diagnostic、patches/{patch_id}/apply、feedback、approve、publish、compare）统一返回 `410 Gone`，错误码 `legacy_expression_retired`，中文说明与 `/chat` 替代路径；handler 不注入 `ExpressionService`、不声明旧请求体、不读请求流，畸形/空正文与未知对象 ID 均得到同一 410 合同；未认证请求仍按统一认证边界返回 401。
- `expression_draft_generation` 已从生产 capability 注册表移除，`ExpressionService` 不再接收 `model_gateway`、不再产生 `model_run_lock`；新增防复活失败关闭检查（健康依赖 `retired_capability_guard`）：能力被重新注册时 `/health/ready` 返回 FAIL、数据请求 503，运行时与启动时刻同样生效。
- 兼容观测复用 `bridges.retirement.raise_retired_capability`：按稳定 endpoint ID 聚合 real/probe 两类计数，只记录 endpoint ID、服务版本、流量类别与状态码，不记录账户、对象 ID、正文或 Key。
- 测试：`tests/integration/test_expression_retirement.py`（参数化七个入口 × 正常/畸形 JSON、空正文、未知对象 ID、幂等、service/gateway spy 零调用、`model_run_locks` 无副作用、registry 合同与工作流不可解析、启动失败关闭、探针/真实流量分开计数）；旧集成测试改写为退役契约测试；`tests/expression` 50 项、退休相关集成 53 项、Humanizer 主链 345 项通过。
- openapi.json 已按当前 API 重新生成：七个写路由仅暴露 410 合同（无 requestBody），旧写请求/响应 schema 移出 spec；`tests/contracts/test_openapi_sync.py` 通过。
- 全量回归与 main 基线逐目录对比：所有失败集合与 main 一致（含 `test_humanizer_chat.py` 4 项既有失败、chat 附件 410 契约失败、mcp/learning_projects/ingestion 等既有失败），唯一差异 `test_closeout_stress_switch.py::test_50_random_switch_refresh_during_generation_no_stream_interrupted` 为时序性 flaky，单独重跑两分支均通过；Humanizer 源码不引用 `/expression/*`。
- 环境备注：DSH 沙箱下 pytest basetemp 目录需预授权 ACL 才能完成 session 清理；全树收集存在两个既有同名测试模块冲突（`test_release_gate.py`、`test_schema_v33.py`，main 同样存在），分目录运行即可规避。
