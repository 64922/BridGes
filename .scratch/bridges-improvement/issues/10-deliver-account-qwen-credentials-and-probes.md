# 10 — 交付账户级 Qwen 凭据与固定能力真实探测

Status: ready-for-agent
Blocked by: [05 — 建立干净的 `bridges.db` 与账户隔离加密对象库](./05-build-clean-sqlite-and-object-storage.md), [07 — 交付用户名与 QQ 邮箱注册登录页面](./07-deliver-qq-username-auth-pages.md)
Covered requirements: UI-04, MODEL-01, MODEL-02, MODEL-03, ACCOUNT-01, IMP-03, DESKTOP-01
ADRs: [ADR-0005](../../../docs/adr/0005-per-account-qwen-key-and-capability-probes.md), [ADR-0006](../../../docs/adr/0006-one-model-snapshot-per-capability.md), [ADR-0009](../../../docs/adr/0009-fixed-model-and-provider-matrix.md), [ADR-0013](../../../docs/adr/0013-single-machine-storage-and-background-runtime.md), [ADR-0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

把密钥设置页接入账户级受保护凭据存储和固定能力矩阵。用户登录并通过近期密码确认后保存自己的百炼 Key，系统使用非用户数据分别真实探测核心对话、Embedding、ASR、TTS、图片和视频能力，并展示每项状态与错误。用户不能修改模型；失败只允许重试同一绑定，不得以 Stub、其他模型或静默降级伪装成功。

## Acceptance criteria

- [ ] 每个账户独立保存百炼 Key；源码环境使用操作系统凭据库，容器使用自动生成主密钥保护的加密凭据卷。
- [ ] Key 不写入 `.env`、SQLite 明文字段、日志、API 响应、浏览器存储、普通导出或其他账户作用域。
- [ ] 保存、替换和删除 Key 均要求当前账户近期密码确认，并有不包含秘密正文的审计事件。
- [ ] 能力矩阵固定为 ADR-0009 的唯一绑定，页面不提供模型选择、更名、自动更新或备用模型控件。
- [ ] 保存 Key 后以非用户数据逐项执行真实探测；每项显示“未探测、探测中、可用、不可用”及中文原因和同模型重试入口。
- [ ] 某项探测失败只禁用对应能力；登录、资料、密钥修改和不依赖 AI 的本地功能仍可用。
- [ ] 生产能力注册表中不存在使用真实模型 ID 的 Stub 成功；自动测试替身必须明确仅限测试环境。
- [ ] 请求重试保持同一模型与能力绑定，没有隐藏 fallback；每次探测记录不可变模型、区域、参数和追踪标识。
- [ ] 密钥设置页完整覆盖 loading、empty/unconfigured、probing、partial success、error、permission/reauth required 和完成状态，中文文案不会回显 Key。
- [ ] 电脑端仅用键盘可完成输入、显示/隐藏、保存、逐项重试、删除和返回；视觉回归符合 Issue 04。

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
