# Task Plan — Issue 10：交付账户级 Qwen 凭据与固定能力真实探测

状态：已完成（2026-08-03）

## 目标

实现 `.scratch/bridges-improvement/issues/10-deliver-account-qwen-credentials-and-probes.md`
全部验收标准：每账户独立保存百炼 Key（源码用 OS 凭据库 keyring，容器用自动生成主密钥
保护的加密凭据卷）；Key 不落入 .env/SQLite 明文/日志/API 响应/浏览器存储；保存/替换/删除
要求近期密码确认并有去秘密审计事件；能力矩阵固定为 ADR-0009 唯一绑定（六能力：核心对话、
知识库向量化、语音转写、语音朗读、图片生成与编辑、视频生成），保存后以非用户数据逐项真实
探测，显示未探测/探测中/可用/不可用与中文原因和同模型重试入口；失败只禁用对应能力；生产
注册表无真实模型 ID 的 Stub 成功；前端密钥设置页全状态 + 纯键盘操作 + Issue 04 视觉回归。

## 任务清单

1. [x] 摸底：读 Issue/ADR-0005/0006/0009/0013/0023、Issue 05/07/08/09、模型路由草稿、
   现有 registry/网关/适配器/再认证/审计/前端密钥页
   → 验证：六能力固定矩阵与探测合同、reauth TTL 5 分钟、stub 无条件注册问题定位
2. [x] 依赖与契约：pyproject 增加 keyring>=25；contracts 扩展 ProbeStatus、ProbeRecord、
   CapabilityProbeSummary、KeySettingsProjection、KeySaveRequest
   → 验证：keyring 25.7.0 已装入 conda env
3. [x] 凭据存储 `src/bridges/credentials/store.py`：CredentialStorePort；OsCredentialStore
   （keyring，Windows 无 keyring 时 DPAPI ctypes 兜底）；EncryptedVolumeCredentialStore
   （主密钥自动生成 + Fernet 每账户密文）；InMemoryCredentialStore 测试替身
   → 验证：单测覆盖保存/替换/删除/跨账户隔离/主密钥自举/损坏处理（8 条）
4. [x] 固定矩阵与探测 `matrix.py` + `probes.py`：FIXED_CAPABILITY_MATRIX 六绑定；
   chat/embedding/asr/tts/image/video 真实探测（非用户数据、同绑定重试、不可变记录含
   追踪标识 probe_id/模型/区域/参数）；CapabilityProbeService（PROBING→可用/不可用，
   状态持久化 StateStore，单失败不拖累他项，中断探测回退未探测）
   → 验证：18 条单测覆盖六能力成功/失败/重试/维度校验/记录不可变/持久化
5. [x] 服务与审计 `service.py`：KeyCredentialService（projection/save/delete/retry_probe/
   run_all/schedule_retry），保存/替换/删除/探测审计事件（无秘密正文）
   → 验证：审计事件 details 白名单 + 集成测试断言不含 Key
6. [x] Registry 对齐 ADR-0009（main.py）：chat/asr/tts 固定快照 ID；移除 chat/tts 备用
   模型记录；Stub 注册门改为仅 qwen_force_stub 或 model_id=deterministic 的内置 TOOL
   → 验证：3 条注册表合同测试（生产无 Stub 伪装、矩阵固定、force_stub 测试路径）
7. [x] API 路由 `src/bridges/api/credentials.py`：GET/PUT/DELETE key-settings、POST
   probes、POST probes/{capability}/retry；全部强制近期密码确认；更新原占位测试
   → 验证：7 条 Issue 10 集成测试（隔离/替换/删除/重试/无 Key 泄漏/部分失败）
8. [x] 前端：ui-ux-pro-max 技能 → 重建 KeySettings.tsx（loading/空态表单/探测中/部分
   成功/错误/reauth/完成），显示/隐藏、保存、逐项重试、两步删除确认、返回全键盘；
   api.ts 新端点；重新生成 openapi.json 与 generated.ts
   → 验证：npm typecheck + build 通过
9. [x] E2E：更新 issue08 密钥页断言与快照 mock，新增 issue10 键盘路径 spec（5 场景），
   更新三视口视觉快照
   → 验证：全量 chromium E2E 83 通过（含新 5 条）
10. [x] 全量验证：pytest 全套（1113 passed）、ruff 新代码 0 错误、mypy src 0 错误、
    npm typecheck/build、E2E 83 通过
    → 验证：基线 1037 + 76 新增，无回归
11. [x] /code-review 双轴审查 → 修复缺陷 → 复跑验证
    → 验证：修复 6 项（线程安全/后台异常/ProbeError/删除确认 Dialog/契约收敛/
   参数单一来源），1118 passed、mypy 0、E2E 83、改动文件 ruff 0
12. [x] 更新 issue 10 AC 勾选 + Comments + 状态 ready-for-human，提交 git
    → 验证：提交信息含工作总结与 bug 汇总

## 验收命令

```powershell
conda run -n agent python -m pytest -k "credential or capability or probe or model"
conda run -n agent python -m ruff check .
conda run -n agent python -m mypy src
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
npm --prefix apps/web run test:e2e -- --project=chromium
```
