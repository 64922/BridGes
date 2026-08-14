# Issue 16：补齐图片替代文本及图片/视频供应商取消调用锁

Status: ready-for-agent

Type: task

Priority: P1

User stories: US-QWEN-01、US-QWEN-02、US-MEDIA-01

## 已验证现状与根因

- `src/bridges/image/service.py` 的主图片生成/编辑调用经 `_invoke_image` 保存 `qwen_image` 运行锁；`src/bridges/video/service.py` 的主视频调用也经 `_invoke_video` 保存 `qwen_wan` 锁。缺口集中在边缘动作，不应重写已经工作的主链。
- 图片生成成功后的 `_generate_alt_text` 直接调用 `ModelGateway.invoke("qwen_vision", ...)`，成功时返回模型替代文本，失败时降级为明确的提示词摘要；无论成功还是失败，返回的 `result.lock` 都未持久化。
- 图片 `cancel` 在存在 `cloud_task_id` 时直接调用 `qwen_image` 的 cancel 动作，视频 `cancel` 直接调用 `qwen_wan`；两者使用 `contextlib.suppress(Exception)`，既丢弃网关锁，也无法区分“仅本地取消成功”“供应商取消成功”与“供应商通知失败”。
- worker 收敛阶段可能再次尝试供应商取消。实现时必须枚举图片/视频所有 `kind=cancel` 调用点，不能只修用户 API 路径；每次真正发往供应商的动作都应有独立序号和锁。
- 本地状态仍是取消权威：供应商取消失败不能让迟到结果重新发布资产。补齐模型锁的目的是诚实审计外部动作，不是把云端成功变成取消接口的硬依赖。
- 凭据范围已经确认：图片、视觉与 Wan 能力继续使用安装级全局 Qwen Key；账户、会话、任务和资产必须严格隔离，锁中不能出现 Key。

## What to build

1. 将图片替代文本的 `qwen_vision` 调用接入 Issue 10 recorder；每次真实调用保存独立锁，并关联账户、会话、图片任务、资产/对象（可用时）、阶段 `image_alt_text` 和调用序号。
2. 保持替代文本的诚实降级：模型成功且内容有效时来源为 `model`；模型失败、空输出或锁持久化失败时不得标成模型来源，继续使用明确的 `fallback` 文本和稳定原因。手动替代文本不调用模型、不建锁。
3. 将图片和视频所有真实供应商取消尝试接入统一 `_invoke_image`/`_invoke_video` 或等价 recorder 接缝；用户请求与 worker 重试分别保留 action/attempt/sequence，不吞掉调用锁。
4. 供应商取消成功、失败、超时、鉴权、限流及未知任务均持久化准确锁和脱敏错误码。外部失败可继续按当前“本地取消权威”返回，但领域审计/任务投影应能说明“本地已取消，云端通知未确认”，不能冒充供应商成功。
5. 没有 `cloud_task_id`、任务已成功/已取消的幂等返回、仅本地条件更新、迟到结果抑制和资产回收均不是供应商调用，不创建模型锁。
6. 取消重试真正再次发远端请求时新增锁；重复保存同一调用结果幂等。图片/视频主链已有锁行为及调用次数保持不变。
7. 所有能力、模型和 adapter 取自 Issue 09 单一事实源；图片视觉模型兼容性结论由该 issue 决定，不在本任务中静默切换或 fallback 到未批准模型。

## 上下文指针

- `src/bridges/image/service.py`：`cancel`、worker 取消收敛调用、`_generate_alt_text`、`_invoke_image`、`_run_context`。
- `src/bridges/video/service.py`：`cancel`、worker 取消收敛调用、`_invoke_video`、`_run_context`。
- `src/bridges/contracts/image.py`：`ImageAltTextSource` 与任务/资产投影。
- `tests/image/`、`tests/video/`：生成、轮询、取消、迟到结果、替代文本与账户隔离合同。
- `src/bridges/ai/model_gateway.py`：网关状态与 `ModelRunLock`。

## 非目标

- 不重做已能持久锁的图片生成、编辑、视频生成主链，也不改变其轮询、重试和发布语义。
- 不让供应商取消结果取代本地取消权威；云端失败不能重新开放已取消任务。
- 不要求提示词摘要 fallback、手动替代文本、本地状态更新、资产清理或迟到结果抑制调用 Qwen。
- 不为不存在的 `cloud_task_id` 或终态幂等返回伪造 cancel 锁。
- 不把安装级全局 Qwen Key 改成账号级 Key；不读取、打印、哈希、回显或持久化 Key。
- 不以 fake、fixture、cassette、预置图像/视频任务或录制响应证明真实供应商调用。

## Acceptance criteria

- [ ] 图片替代文本每次真实 `qwen_vision` 调用恰好新增一条锁，关联账户、会话、任务/资产、阶段和固定模型；静态检查不存在直接调用后丢锁路径。
- [ ] 视觉模型成功且返回有效内容时替代文本来源为 `model`，锁状态成功；模型失败或空输出时失败锁仍保留，投影来源为 `fallback` 且不得声称模型理解图片。
- [ ] 用户手动修改替代文本不调用 Qwen、不新增模型锁，并保持来源 `manual`。
- [ ] 图片/视频存在 cloud task 的每次真实供应商 cancel 请求均新增一条对应能力锁；用户调用和 worker 重试按 attempt/sequence 区分，历史失败锁不被覆盖。
- [ ] 供应商 cancel 失败时本地取消/取消中状态和迟到结果抑制保持有效，同时领域审计明确“云端通知未确认”并保留失败锁；不能记录供应商取消成功。
- [ ] 无 cloud task、已成功/已取消幂等返回及纯本地清理的网关调用增量和模型锁增量均为 0。
- [ ] 图片/视频主生成链原有调用数、锁数和任务状态不变；新锁只覆盖 alt-text 和真实 cancel 边缘动作。
- [ ] recorder 写入幂等；进程重启后可按账户、任务、动作和 attempt 查询；两账户任务与锁严格隔离。
- [ ] 锁、日志和指标不包含 Key、Authorization、图片 bytes/base64、提示词、替代文本正文、视频内容、供应商完整响应或用户材料。
- [ ] production-like 组合中视觉/图片/Wan 能力若绑定 Stub、fixture/cassette、缺真实 adapter 或 Issue 09 模型漂移则失败关闭对应远端动作，不造伪锁。
- [ ] 安装级全局 Qwen Key 只由生产组合根注入；共享凭据不改变账户/任务隔离和属主检查。

## Test plan

1. 扩展图片 fake adapter 合同：alt-text 成功、失败、空输出、手动修改，断言调用数、锁数、来源和稳定错误码；失败仍有 fallback 且不冒充模型。
2. 扩展图片 cancel 测试：有/无 cloud task、远端成功/失败、终态幂等、用户取消后迟到成功、worker 重试，断言每次真实供应商请求一条锁且本地状态权威。
3. 对视频运行同样 cancel 矩阵，覆盖 `cancelling` 收敛、远端失败和 worker 重试；主视频生成锁数量不得变化。
4. 临时 SQLite 测试边缘锁幂等、attempt 顺序、重启后查询和跨账户隔离；模拟远端 cancel 返回后、领域审计前崩溃，模型锁仍存在。
5. 架构测试扫描图片/视频所有 `gateway.invoke` 和 `kind=cancel` 调用点，禁止绕过 recorder；确保模型来自 Issue 09 单一事实源。
6. 可选真实 smoke：仅在显式开关、已配置安装级全局 Qwen Key 且测试账户有相应模型权限时，禁用 Stub、fixture、cassette 与录制，用最小成本真实图片验证 alt-text 锁；只对 smoke 自己刚创建且仍可安全取消的图片/视频任务测试 cancel。若没有可取消 task，测试必须明确 `inconclusive/skip`，不得伪造成功。代码不能读取或输出 Key、提示词或媒体内容。

建议回归命令：

```powershell
python -m pytest `
  tests/image `
  tests/video `
  tests/image/test_media_edge_model_run_locks.py `
  -q -p no:cacheprovider `
  --basetemp=$env:TEMP\bridges-issue16-contract
```

可选真实 smoke：

```powershell
$env:BRIDGES_MEDIA_EDGE_REAL_SMOKE='1'
python -m pytest tests/image/test_media_edge_real_smoke.py -q -p no:cacheprovider
```

## Observability & rollback

- 按 `image_alt_text`、`image_cancel`、`video_cancel`、attempt、状态和稳定错误码统计远端调用数、锁数、缺锁数与延迟；不采集提示词、替代文本或媒体。
- 增加 `media_edge_missing_run_lock`、`media_edge_lock_persist_failed`、`media_cancel_provider_unconfirmed`、`media_edge_call_count_mismatch` 稳定错误码。
- 分开展示本地取消成功率与供应商通知确认率，避免本地成功掩盖远端失败；远端请求数必须等于对应新增锁数。
- 如 recorder 接线导致回归，可暂时停止视觉 alt-text 调用并使用现有明确 fallback；取消仍保持本地权威并可停止远端通知。不得恢复无锁远端调用、删除旧锁或把未确认取消显示为供应商成功。

## Blocked by

- [Issue 09：固化生产模型矩阵与真实 adapter 门禁](./09-freeze-production-model-matrix.md)
- [Issue 10：扩展持久化模型运行审计接口](./10-expand-durable-model-run-audit.md)

## Comments

- 2026-08-13：已验证图片/视频主生成链会保存锁；缺口仅为图片视觉替代文本和图片/视频真实供应商取消动作。
- 2026-08-13：用户确认使用安装级全局 Qwen Key；实现与测试严禁读取或泄露 Key、提示词、替代文本或媒体内容。
- 2026-08-14：实现完成（分支 `worktree-16-media-edge-model-run-locks`）：
  - 替代文本：`_generate_alt_text` 拆出统一接缝 `_invoke_alt_text`（qwen_vision invoke + recorder 落锁），每次真实调用恰好一条锁，业务关联图片任务（主）+ 既有来源资产（追加）；模型失败/空输出/锁落库失败一律 fallback 且不冒充模型来源；手动修改不调用模型、不建锁。
  - 取消：图片/视频统一接缝 `_invoke_image_cancel`/`_invoke_video_cancel`（invoke + 落锁），用户调用与 worker 收敛/重试按 `cancel_attempt` 序号区分（迁移 46 新增 `image_tasks.cancel_attempt`/`video_tasks.cancel_attempt`）；无 cloud task、终态幂等、纯本地路径零调用零锁；供应商失败保留失败锁、本地取消权威与迟到结果抑制不变，审计以 `media_cancel_provider_unconfirmed` 明确"云端通知未确认"。
  - 稳定错误码：`media_edge_missing_run_lock`、`media_edge_lock_persist_failed`、`media_cancel_provider_unconfirmed`、`media_edge_call_count_mismatch`（`bridges.contracts.observability`），写入取消/替代文本审计 details；新增审计动作 `image_alt_text_generate`。
  - 脱敏：供应商原文若携带凭据形态关键词（如鉴权失败消息中的 "authorization"），接缝落库前经 `bridges.ai.lock_scrub.scrub_lock_text` 替换为只含稳定错误码的中文说明（Issue 10 录制器会拒绝含关键词的自由文本，不脱敏则失败锁无法持久化）。
  - 测试：`tests/image/test_media_edge_model_run_locks.py`（替代文本/取消矩阵、崩溃注入、recorder 幂等/顺序/重启/隔离、脱敏）、`tests/architecture/test_media_edge_run_lock_scan.py`（gateway.invoke 与 kind=cancel 调用点必须经接缝、禁止 suppress 包裹 invoke、非空洞白名单校验）、`tests/image/test_media_edge_real_smoke.py`（`BRIDGES_MEDIA_EDGE_REAL_SMOKE=1` 显式 opt-in 真实 smoke，无 Key/门禁失败/无可取消任务时明确 skip）。
  - 验证：`tests/image tests/video tests/architecture tests/storage tests/ai` 全绿（含建议回归命令）；tests/chat 的 44 个失败与 pristine main 完全一致（既有环境性失败）；mypy/ruff 相对 main 无新增告警。
