# 10 — 交付账户级 Qwen 凭据与固定能力真实探测

Status: ready-for-human
Blocked by: [05 — 建立干净的 `bridges.db` 与账户隔离加密对象库](./05-build-clean-sqlite-and-object-storage.md), [07 — 交付用户名与 QQ 邮箱注册登录页面](./07-deliver-qq-username-auth-pages.md)
Covered requirements: UI-04, MODEL-01, MODEL-02, MODEL-03, ACCOUNT-01, IMP-03, DESKTOP-01
ADRs: [ADR-0005](../../../docs/adr/0005-per-account-qwen-key-and-capability-probes.md), [ADR-0006](../../../docs/adr/0006-one-model-snapshot-per-capability.md), [ADR-0009](../../../docs/adr/0009-fixed-model-and-provider-matrix.md), [ADR-0013](../../../docs/adr/0013-single-machine-storage-and-background-runtime.md), [ADR-0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

把密钥设置页接入账户级受保护凭据存储和固定能力矩阵。用户登录并通过近期密码确认后保存自己的百炼 Key，系统使用非用户数据分别真实探测核心对话、Embedding、ASR、TTS、图片和视频能力，并展示每项状态与错误。用户不能修改模型；失败只允许重试同一绑定，不得以 Stub、其他模型或静默降级伪装成功。

## Acceptance criteria

- [x] 每个账户独立保存百炼 Key；源码环境使用操作系统凭据库，容器使用自动生成主密钥保护的加密凭据卷。
- [x] Key 不写入 `.env`、SQLite 明文字段、日志、API 响应、浏览器存储、普通导出或其他账户作用域。
- [x] 保存、替换和删除 Key 均要求当前账户近期密码确认，并有不包含秘密正文的审计事件。
- [x] 能力矩阵固定为 ADR-0009 的唯一绑定，页面不提供模型选择、更名、自动更新或备用模型控件。
- [x] 保存 Key 后以非用户数据逐项执行真实探测；每项显示“未探测、探测中、可用、不可用”及中文原因和同模型重试入口。
- [x] 某项探测失败只禁用对应能力；登录、资料、密钥修改和不依赖 AI 的本地功能仍可用。
- [x] 生产能力注册表中不存在使用真实模型 ID 的 Stub 成功；自动测试替身必须明确仅限测试环境。
- [x] 请求重试保持同一模型与能力绑定，没有隐藏 fallback；每次探测记录不可变模型、区域、参数和追踪标识。
- [x] 密钥设置页完整覆盖 loading、empty/unconfigured、probing、partial success、error、permission/reauth required 和完成状态，中文文案不会回显 Key。
- [x] 电脑端仅用键盘可完成输入、显示/隐藏、保存、逐项重试、删除和返回；视觉回归符合 Issue 04。

## Verification

```powershell
conda run -n agent python -m pytest -k "credential or capability or probe or model"
conda run -n agent python -m ruff check .
conda run -n agent python -m mypy src
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
npm --prefix apps/web run test:e2e -- --project=chromium
```

在显式提供测试账户 Key 的人工冒烟环境中逐项探测一次，并检查日志与网络响应不包含完整 Key；自动化测试不得要求开发者把 Key 写入仓库或 `.env`。

## Non-goals

- 不允许用户更换模型或配置每能力不同 Key。
- 不实现跨账户共享凭据。
- 不用 Stub 代替供应商真实可用性结论。
- 不实现移动端密钥设置页。

## Blocked by

- [05 — 建立干净的 `bridges.db` 与账户隔离加密对象库](./05-build-clean-sqlite-and-object-storage.md)
- [07 — 交付用户名与 QQ 邮箱注册登录页面](./07-deliver-qq-username-auth-pages.md)

## Comments

DuckDuckGo 不使用 Key；Wan 视频是已批准的同一百炼生态例外，仍使用当前账户同一个 Key 和相同探测/无 fallback 合同。

2026-08-03 交付完成，等待人工视觉验收与真实 Key 冒烟。

**工作内容总结**

- 新增 `src/bridges/credentials/` 模块：`CredentialStorePort` 三实现——OS 凭据库
  （keyring：Windows 凭据管理器 / macOS 钥匙串 / Linux Secret Service；精简环境
  Windows 退回 DPAPI 系统级保护）、容器加密凭据卷（Fernet 主密钥首次启动自动
  生成、0600 权限、每账户独立密文、文件名用账户 ID 哈希）、内存测试替身；Compose
  显式启用 `BRIDGES_CREDENTIAL_BACKEND=encrypted-volume`。
- ADR-0009 固定能力矩阵 `matrix.py`（单一事实源）：核心对话 qwen3.7-plus-2026-05-26、
  向量化 text-embedding-v4（1024 维）、ASR qwen3-asr-flash-2025-09-08、TTS
  qwen3-tts-flash-2025-11-27、图片 qwen-image-2.0-pro-2026-06-22、视频
  wan2.7-t2v-2026-06-12；`probes.py` 六能力真实探测（非用户固定数据、同绑定重试、
  不可变 ProbeRecord 含追踪标识/模型/区域/参数快照、中断探测回退未探测、逐账户
  执行锁 + 全局状态锁并发安全），图片/视频以"任务提交被接受"为真实可用结论。
- `KeyCredentialService` 门面：保存/替换/删除/全量探测/单项同模型重试；KEY_SAVE、
  KEY_DELETE、KEY_PROBE 审计事件（details 白名单，绝不携带 Key 正文）；凭据存储
  不可用时探测状态标记为不可用并给出中文原因，绝不卡"探测中"。
- API：替换 Issue 08 占位端点，交付 GET/PUT/DELETE `/auth/key-settings`、POST
  `/auth/key-settings/probes`、POST `/auth/key-settings/probes/{capability}/retry`，
  全部强制近期密码确认（403 reauth_required）；响应只含脱敏尾号。
- 注册表对齐 ADR-0009：chat/asr/tts 改为固定快照 ID，移除 qwen_text_chat_fallback
  与 qwen_tts_instruct 备用模型；Stub 注册门改为仅 `qwen_force_stub` 或内置
  deterministic 工具能力——生产环境不再存在"真实模型 ID 的 Stub 成功"。
- 前端密钥设置页重建（ui-ux-pro-max 指导）：录入/替换表单（显示/隐藏、保存并逐项
  探测）、脱敏尾号摘要、六能力状态列表（未探测/探测中/可用/不可用 + 中文原因 +
  同模型重试）、全量重新探测、两步删除确认（无障碍 Dialog 焦点陷阱）、返回设置
  中心；探测中自动轮询直至完成；全部操作纯键盘可达。
- 测试：store 8 条、probes 20 条、service 3 条、注册表合同 3 条、API 集成 7 条、
  并发安全 2 条；E2E 新增 issue10 键盘路径 5 场景，issue08 密钥页断言与三视口
  视觉快照更新。全量 pytest 1118 passed（基线 1037 + 81 新增），mypy 0 错误，
  改动文件 ruff 0 错误，E2E 83 passed。

**代码审查与 Bug 修复汇总**

- 修复探测服务并发缺陷：后台探测线程与请求线程共享记录映射，原逐账户锁不足以
  保护结构本身（保存/替换时可能字典变更迭代崩溃）——改为全局状态锁 + 逐账户
  执行锁，锁创建加互斥。
- 修复后台线程异常静默：凭据存储不可用时探测线程静默死亡、页面卡"探测中"直到
  600s 回退——`run_all_probes`/`retry_probe` 捕获 `CredentialStoreError` 并
  `mark_unavailable` 给出中文原因。
- 修复 `ProbeError` 缺 `message` 属性导致错误映射崩溃；未知能力重试返回中文
  400 而非 KeyError 500。
- 修复删除确认伪模态：`role="alertdialog"` 无焦点管理——改用仓库无障碍 Dialog
  （焦点陷阱 + Esc + 焦点归还），E2E 同步。
- 修复 `KeySettingsProjection`/`KeySettingsStatus` 双契约漂移（identity 与
  credentials 各一份）——统一收敛到 `contracts/credentials.py`。
- 探测参数改为从矩阵单一事实源读取（voice/size/duration/max_tokens 不再与
  `binding.parameters` 重复硬编码）；DPAPI 兜底文档与实际可达性对齐；
  `dashscope_native` 区域无关性补充说明。
- code-review 双轴复核最终结果：Standards 0 项未解决缺陷，Spec 0 项未解决缺陷。

**验证结果**

- `conda run -n agent python -m pytest`：1118 passed，2 skipped。
- `conda run -n agent python -m mypy src`：145 个源文件 0 错误。
- `conda run -n agent python -m ruff check .`：仍为前置 Issue 已记录的预存错误
  （233 个，均为历史文件）；本 Issue 改动文件零错误。
- `npm --prefix apps/web run typecheck`、`npm --prefix apps/web run build`：通过。
- Chromium 全量 E2E 83 个场景通过（含 issue10 新增 5 个键盘场景与更新后的
  issue08 密钥页断言与三视口视觉快照）。
- 人工冒烟（待执行）：`BRIDGES_SMOKE_QWEN_KEY=sk-... conda run -n agent python
  scripts/smoke_key_probes.py`——显式提供测试账户 Key 后逐项真实探测，脚本自动
  校验 API 投影与审计事件不含完整 Key。自动化测试不要求也不允许把 Key 写入仓库
  或 `.env`。
- 边界说明：探测状态目前由密钥设置页消费；探测为"不可用"的能力在运行时对工作流
  的停用（网关按账户 Key 与探测状态路由）随 Issue 11 接通真实聊天时落地。
