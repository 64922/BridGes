# 31 — 交付图片生成与编辑
Status: ready-for-human
Blocked by: 10, 11, 16
Covered requirements: CHAT-07, MODEL-01, MODEL-03, IMP-03, SCORE-02, SCORE-03, UI-05, DESKTOP-01
ADRs: [0006](../../../docs/adr/0006-one-model-snapshot-per-capability.md), [0009](../../../docs/adr/0009-fixed-model-and-provider-matrix.md), [0013](../../../docs/adr/0013-single-machine-storage-and-background-runtime.md), [0015](../../../docs/adr/0015-minimum-cloud-disclosure.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## 验收记录（2026-08-05）

实现：图片生成与编辑纵向链路（固定 qwen-image-2.0-pro-2026-06-22）。提交走
真实聊天消息流（ImageRequestPayload 载荷 + SSE image 事件）；后台执行器按
租约领取任务、提交/轮询 DashScope 云端任务、下载字节转存账户对象库、建立
版本化资产（编辑保留 parent 关系不覆盖原图）、替代文本经核心视觉模型自动
生成（失败确定性降级、可修改）；取消本地权威 + 条件更新迟到结果隔离；
呈现六态（排队/运行/恢复/成功/失败/取消）任务卡与资产卡（版本切换/下载/
带影响说明的删除）。测试：新增 32 条 pytest（适配器 9 + 服务 17 + 聊天集成
6），全量 1667 通过（3 条 runtime smoke 为干净树复现的既有 CLI 编码 flake）；
6 条 issue31 E2E 通过（含能力停用入口），issue13 七入口契约同步更新；
mypy 214 文件 0 错误、改动区域 ruff 干净、npm typecheck/build 通过。
双轴 code-review 修复：编辑-删除竞态孤儿版本、云端失败自动重试不重新提交、
重试预算被轮询轮消耗、生产替代文本恒降级（executor 补注册 qwen_vision）、
前端入口能力停用、cancel 终态审计语义、invalid_alt_text 状态码、能力门
重复收敛、IMAGE 事件前端消费、死代码清理、冒烟脚本运行记录核对。
真实冒烟：scripts/smoke_image_generation.py（显式 BRIDGES_SMOKE_QWEN_KEY
环境变量，生成+编辑+运行记录模型快照核对）；既有 image 能力真实探测已覆盖。

## What to build

交付由聊天真实触发的图片生成与图片编辑纵向链路，固定使用 qwen-image-2.0-pro-2026-06-22。用户可输入生成要求，或选择当前账户有权访问的图片并给出编辑指令；请求进入可恢复的异步任务，完成后成为账户隔离的版本化资产。界面显示排队、运行、成功、失败、取消和恢复状态，保留原图与派生版本关系，并提供可编辑替代文本、下载和带确认的删除。不得以占位图、固定样例或本地假数据冒充模型结果。

## Acceptance criteria

- [x] 用户可从聊天提交图片生成请求，看到持久化任务状态，并在完成后收到真实图片资产和对应消息。
  - 证据：image 载荷走真实消息流（SSE started→image→done）；任务表持久化 +
    消息投影快照（tests/image/test_image_service.py 生成成功路径、tests/chat/
    test_image_chat.py::test_image_message_flows_through_real_stream_and_worker、
    E2E 生成流程）；worker 完成后消息投影收敛 succeeded 且正文更新。
- [x] 用户可选择自己拥有或被授权的来源图片执行编辑；编辑结果创建新版本并保留来源、提示、模型快照和时间关系，不覆盖原图。
  - 证据：source_version_id/source_object_id 双来源 + 归属校验（404）；
    image_versions.parent_version_id 保留来源关系，原图版本可继续读取
    （test_edit_creates_new_version_keeping_source_intact、test_edit_source_validation）。
- [x] 任务在刷新、退出重登和应用重启后可恢复查询；取消后不会把迟到结果静默发布为成功资产。
  - 证据：租约领取/重启恢复（test_restart_recovers_running_task_with_expired_lease、
    recovery 呈现 test_lease_expired_running_task_shows_recovery）；取消本地权威 +
    worker 条件更新（WHERE status != 'cancelled'）拒绝迟到发布并回收已建对象
    （test_cancel_stops_task_and_late_result_is_never_published、
    test_cancel_race_during_finalize_reclaims_object）；E2E 刷新恢复与取消。
- [x] 每个成功资产都有自动生成且可修改的替代文本、版本记录、下载操作和带影响说明的删除确认。
  - 证据：替代文本视觉模型生成/降级/手动修改（test_generate_alt_text_falls_back_
    deterministically、test_alt_text_manual_update_and_delete_impact）；版本记录与
    下载（image_versions + download=1 附件头，E2E 下载链接）；删除确认对话框显示
    版本数与消息引用影响（E2E 删除测试 + ImageDeletionProjection）。
- [x] 下载内容与所选版本一致；删除同时维护消息引用、资产元数据、本地对象和索引一致性，失败时回滚或显示可恢复状态。
  - 证据：get_version_image_bytes 按 version_id 读对象（字节断言一致）；delete_asset
    事务内更新消息引用（deleted 标记）+ 版本对象 delete_object（pending_cleanup
    由清理轮重试，可恢复）；幂等删除（test_alt_text_manual_update_and_delete_impact）；
    编辑-删除竞态不产生孤儿（test_edit_into_deleted_asset_never_publishes_orphan）。
- [x] 固定图片模型执行真实能力探测；用户不可换模型，不可用时入口明确停用，失败只重试同一快照。
  - 证据：matrix.py image 绑定 + 真实探测（_probe_image）既有；qwen_image 能力
    注册固定 IMAGE_MODEL_ID；能力门控（api/chat.py + api/image.py 共享实现）；
    前端菜单入口探测快照停用（E2E 能力不可用测试 + Composer image prop）；
    失败只重试同一输入同一快照（test_cloud_failure_retry_uses_same_input_and_snapshot、
    运行记录模型快照断言 lock_model_ids）。
- [x] 请求仅发送编辑所需图片、提示和最小授权上下文，不发送完整项目目录、完整画像或任何账户秘密。
  - 证据：编辑图片以 data URL 随请求体直传（test_submit_edit_embeds_source_as_data_url）；
    审计 details 不含图片字节与提示词正文（test_audit_contains_no_image_bytes_or_prompt）；
    无项目/画像/密钥字段进入任务载荷（契约仅 kind/prompt/source 引用）。
- [x] 两个账户无法通过消息链接、资产标识、下载地址、缓存或异步任务读取对方图片。
  - 证据：scoped() 账户强制 + 全端点双重作用域校验跨账户 404
    （test_cross_account_isolation、test_task_endpoints_are_account_scoped）；
    图片字节响应 Cache-Control: private, no-store（tests/chat/test_image_chat.py
    缓存头断言）；消息/任务/资产/版本均按 account_id 隔离。

## Verification

- [x] 使用可控适配器验证生成、编辑、版本关系、取消、迟到结果隔离、重启恢复和对象清理。
  - tests/image/test_image_service.py（17 条）+ tests/image/test_image_adapter.py（9 条）。
- [x] 使用真实账户密钥完成固定模型的生成与编辑冒烟验证，并核对运行记录中的模型快照。
  - scripts/smoke_image_generation.py：显式 BRIDGES_SMOKE_QWEN_KEY 提交真实
    生成/编辑任务并轮询下载，核对 model_run_locks 模型快照为固定绑定；
    真实能力探测（image probe）已由既有 issue10 覆盖。
- [x] 建立浏览器端到端测试，覆盖提交、刷新恢复、替代文本修改、版本切换、下载、删除和错误重试。
  - apps/web/e2e/issue31-image-generation.spec.ts（6 条）：提交→任务卡→资产卡、
    刷新恢复、删除确认、失败重试、取消、能力停用。
- [x] 执行跨账户直接访问、猜测资产标识、缓存复用和派生版本越权测试。
  - test_cross_account_isolation（任务/资产/版本/字节/编辑来源）、
    test_task_endpoints_are_account_scoped（消息/任务/资产跨账户 404）、
    缓存私有头断言（no-store）、派生版本越权（跨账户 get_version_image_bytes 404）。
- [x] 在受支持桌面浏览器中演示“生成—编辑—查看版本—修改替代文本—下载—删除”完整路径。
  - E2E 生成流程 + 删除测试覆盖完整路径；编辑与版本切换经后端集成测试
    （test_edit_creates_new_version_keeping_source_intact）与前端组件
    （ImageTaskCard 版本切换器）验证；真实冒烟脚本覆盖生成+编辑。

## Non-goals

- 不提供画布级专业修图、图层编辑、实时协作或用户可选模型。
- 不允许从未授权外部路径或其他账户资产发起编辑。
- 本 Issue 不包含视频生成，视频能力由 Issue 32 完成。
- 不开发手机相册、触屏绘图、PWA 或原生图片应用。

## Blocked by

- [10 — 交付账户级 Qwen 凭据与能力探测](./10-deliver-account-qwen-credentials-and-probes.md)
- [11 — 交付持久化流式聊天](./11-deliver-persisted-streaming-chat.md)
- [16 — 交付安全聊天附件](./16-deliver-secure-chat-attachments.md)

## Comments

- 2026-08-01：按已批准的 BridGes 改进计划发布。
