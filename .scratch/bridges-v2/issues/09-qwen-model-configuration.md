# 09 — Qwen 凭据与主模型配置

**What to build:** 用户可在设置中安全更换 Qwen 凭据并手填主模型 ID；验证成功后从下一条消息起使用新配置。

**Blocked by:** 02 — 可恢复的对话运行

**Status:** ready-for-human

**实施前必读：** [ADR-0030](../../../docs/adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md)、[V2 设计索引](../../../docs/v2/README.md)及索引所列六份设计文档；先核对阻塞票已验收。

- [x] 设置仅显示凭据状态与遮罩，更换输入框始终为空；候选 Key 验证失败不覆盖旧凭据，响应、日志和浏览器存储不泄漏明文。
- [x] 模型 ID 通过精确元数据查询核对文本、图片、工具调用、结构化输出和上下文额度，再以实际调用探测；任一能力不通过都不能保存。
- [x] 界面显示实际模型 ID、能力和上下文长度及具体验证失败原因；没有模型预设列表，密钥未配置或不匹配时在字段附近说明操作顺序。
- [x] 通过验证的模型配置原子激活，进行中的轮次维持启动时模型锁，新旧会话的下一轮采用新配置；历史回复的模型记录不改。
- [x] 知识库向量模型和索引版本保持独立；错误 Key、不存在的 ID、元数据缺失和探测失败均有明确中文反馈。
- [x] 主对话、视觉识别和 OCR 的实际调用均采用已验证的运行配置，不留隐藏的固定主模型调用；向量模型继续独立固定。

## Comments

### 2026-09-25 — 实现摘要（agent）

- **AC1 凭据状态与更换**：新增已认证路由 `GET/PUT /settings/credentials/qwen`（`src/bridges/api/credentials.py`）。GET 只返回"是否已配置 + 末次验证时间"，不回显旧值也不返回可逆遮罩；更换输入框始终为空。候选密钥的验证顺序是"能精确查到当前主模型元数据"再"四项最小真实调用探测"，失败按 `key_rejected`／`model_not_matching_key`／`probe_failed` 分类返回 422，凭据库中的旧值原样保留（`tests/api/test_qwen_credential_settings.py`）。明文只出现在 `Authorization` 头：请求正文、响应体、`/settings/credentials`、`/settings/models`、日志与浏览器存储都不含密钥（同文件 `test_probe_request_bodies_never_contain_the_candidate_key`）。验证通过后就地轮换本进程的 Qwen 客户端与 Embedding 端口密钥；在原本没有密钥的进程里首次保存时会补齐当前未绑定的真实适配器，已绑定的确定性适配器不被覆盖（`test_first_key_binds_the_real_adapters_without_restart`）。
- **AC2 两步证据链**：`src/bridges/ai/model_metadata.py` 走百炼「查询模型列表」（`GET /api/v1/models?model=<id>`），按 `model` 字段精确比对，相似名与子串一律不算命中；读 `capabilities`（TG/VU）、`features`（function-calling、structured-outputs）、`inference_metadata.request_modality` 与 `model_info.context_window`／`max_input_tokens`，缺字段即 `model_metadata_incomplete`，模型不存在即 `model_not_found`，网络或供应商不可用按 503 返回。`src/bridges/ai/model_probe.py` 再发四项最小真实请求（文本、图片、工具调用、结构化输出），单请求 30s 超时、`max_tokens=32`，只断言合同形状不判内容。任一项不通过，`PUT /settings/models` 返回 422 且不写运行配置（`tests/ai/test_model_metadata.py`、`tests/ai/test_model_probe.py`、`tests/api/test_model_settings.py`）。
- **AC3 界面披露**：设置页改在 `/account/settings/models`（原 `/account/settings/credentials`；组件 `CredentialSettings` → `KeyAndModelSettings`，标题「密钥与模型管理」），第一张卡是新的「Qwen 凭据」，第二张卡「Qwen 主模型」展示实际模型 ID、上下文长度、最大输入额度、四项能力、配置版本加元数据合同版本与验证时间；失败报告逐能力列出候选值是否通过并给出中文原因（报告里的能力项用 `candidate-capability-*` 前缀，避免与当前配置的能力列表重名）。没有模型预设列表，输入框 `hint` 说明只接受手填的完整模型 ID；密钥未配置或与候选模型不匹配时，字段正下方固定显示操作顺序（先在本页「Qwen 凭据」验证保存密钥，再填写并验证主模型 ID）。
- **AC4 原子激活与模型锁**：`src/bridges/ai/run_model_config.py` 把配置写在账户库 `application_state` 的 `run_model_config` 命名空间，API 与后台执行器共享同一事实源；一次写入包含模型 ID、能力档案、上下文额度、元数据合同版本、修订号与验证时间。`ModelGateway` 在每次调用时把能力名解析为实际模型 ID，优先级为运行锁固定值 > 运行配置（仅限 `CONFIGURABLE_CAPABILITIES`）> 出厂批准矩阵；注册表本身不被改写，生产组合门禁（ADR-0006 一个能力一个模型快照）仍然成立。新建运行（首轮与重试）把当时的模型 ID 写进 `run.config["run_model_id"]`，该运行及其重试的所有调用都带锁定值；历史消息记录的模型标识不被改写，旧会话的下一轮自然采用新配置（`tests/ai/test_gateway_run_model_config.py`、`tests/chat/test_v2_09_run_model_lock.py`）。
- **AC5 独立向量模型与中文反馈**：向量化继续独立固定，`configured_model_id("qwen_embedding", …)` 恒为 `None`，索引版本合同（ADR-0008）未改。四类失败都有稳定错误码与中文原因：鉴权失败、模型不存在、元数据字段缺失、真实探测失败；元数据拿不到时能力状态是「未核对」，界面不替用户断言「不支持」。
- **AC6 无隐藏固定调用**：主对话（`chat/service.py` + `chat/graph.py`）、视觉提取（`media/qwen_extraction.py`）、OCR（`ingestion/ocr.py`）都经 `ModelGateway` 按能力名解析，不存在绕过运行配置的固定主模型调用；Embedding 端口保持独立密钥与固定模型。架构测试（禁止在 `fixed_models.py` 之外出现批准模型 ID 字面量）继续通过。

**验证**：全量 `pytest tests -q`（分支）257 failed / 3806 passed / 42 skipped / 2 errors；main 基线（同期实测）257 failed / 3755 passed / 42 skipped / 2 errors，失败与错误的名称集合逐条相同（双向 `comm` diff 为空），多出的 51 个通过即本票新增测试。`mypy src/` 分支与 main 同为 115 errors 且错误集合逐条一致（仅行号位移）；ruff 在新增与改动文件上无新增告警。前端 `tsc --noEmit` 通过、vitest 65 通过（12 个文件）、`tests/contracts/test_openapi_sync.py` 通过（`openapi.json` 与 `packages/contracts/src/generated.ts` 已重生成）。真实百炼联调验证了两条失败路径：不存在的模型 ID 返回「百炼模型列表中没有精确匹配的模型 ID」，错误密钥返回「百炼拒绝了该 Qwen 密钥（鉴权失败）」并要求先更换密钥，两者都记录进 `last_validation`、原配置继续生效、响应与日志中无明文。

**刻意保留 / 已知边界**：

1. **后台执行器读取新凭据仍需下次启动**：凭据库不是热更新通道，这是 ADR-0024 保留的边界，ADR-0031 与设置页文案都写明；运行配置（模型 ID）无需重启即可对两个进程生效，因为它们共享账户库。
2. **模型锁的边界是"一个对话轮次"**：媒体提取、知识库录入 OCR 与图像替代文本是各自独立的运行，按调用时的运行配置解析，因此会采用落在本次运行中途的激活结果。这是同一份已验证配置的预期效果，ADR-0031 已记录，验收脚本不能假定"激活前后所有调用严格分成两批"。
3. **本票未新增 Playwright 用例**：新页面覆盖为 vitest（`MainModelSettings.test.tsx` 5 例、`KeyAndModelSettings.test.tsx` 3 例）加 pytest（新增 5 个测试模块）；issue 02/04 同样未加 e2e。issue 10 的既有 e2e 只按本次路由与标题变更收窄了 `/密钥/` 链接断言，并补上指向 `/account/settings/models` 的断言。
4. **视觉基线是本机环境特有的**：本次实测 6 张设置页视觉快照中 5 张在 main 上也失败（差异约 2% 像素，来自字体光栅化），因此只按新卡片重生成被本票改动的 3 张 `account-settings-*.png`，其余不重新基线化。
5. **路由迁移**：设置页入口从"凭据"扩为"密钥与模型管理"，`/account/settings/credentials` 不保留重定向（内部页面，桌面应用无外部链接），设置中心卡片与 ADR-0031 已同步。
6. **评审提出但刻意不改**：`tests/chat/test_v2_09_run_model_lock.py` 复用 issue 02 夹具时的 F811 再导出写法与 main 既有 `tests/chat/test_v2_02_resumable_runs.py` 完全一致；两个新增 API 测试模块间的小工具重复（保持各模块自足，与仓库既有测试风格一致）；`MainModelSettings.tsx` 直接引用同目录 `KeyAndModelSettings.module.css`（同一页面同一卡片族）。
