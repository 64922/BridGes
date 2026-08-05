# 32 — 交付视频生成
Status: ready-for-human
Blocked by: 31
Covered requirements: MODEL-01, MODEL-03, IMP-03, SCORE-02, SCORE-03, UI-05, DESKTOP-01
ADRs: [0006](../../../docs/adr/0006-one-model-snapshot-per-capability.md), [0007](../../../docs/adr/0007-wan-video-generation-exception.md), [0009](../../../docs/adr/0009-fixed-model-and-provider-matrix.md), [0013](../../../docs/adr/0013-single-machine-storage-and-background-runtime.md), [0015](../../../docs/adr/0015-minimum-cloud-disclosure.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

交付真实的文生视频纵向链路，并把 Wan 明确实现为模型矩阵中唯一的非 Qwen 系列例外。用户从聊天提交视频要求后，系统固定调用 wan2.7-t2v-2026-06-12，通过受监督后台执行器完成异步提交、状态查询、有限重试、取消和应用重启后的恢复；成功结果进入账户隔离的本地资产库，可预览、下载、查看生成说明和删除。所有状态必须来自持久化任务与供应商结果，不得用轮播占位、固定演示视频或生产 Stub 假成功。

## Acceptance criteria

- [x] 用户可从聊天提交文生视频请求，并看到排队、提交、生成中、成功、失败、取消中和已取消等明确状态。
  - VideoTaskCard 八态芯片（queued/submitting/generating/recovery/succeeded/failed/cancelling/cancelled），
    SSE video 事件 + 5s 轮询 getVideoTask；E2E 断言排队中→提交中→已取消 全流程
    （apps/web/e2e/issue32-video-generation.spec.ts 6 条全过）。
- [x] 所有请求固定绑定 wan2.7-t2v-2026-06-12 和当前账户百炼密钥，界面不提供模型选择，运行记录保存固定快照。
  - matrix.py VIDEO_MODEL_ID 单一事实源；界面无模型选择（VideoDialog 固定说明）；
    适配器测试断言 model=固定标识；运行锁快照核对（test_video_service 与冒烟脚本）。
- [x] 刷新、退出重登或重启 BridGes 后，未完成任务会继续由同一账户恢复查询，不重复提交同一供应商任务。
  - 任务表是权威、消息投影是快照；租约过期呈现 recovery 并重领续轮询；
    test_restart_recovers_running_task_with_expired_lease 断言同一任务继续轮询、
    只提交一次；E2E 刷新恢复用例。
- [x] 用户可取消未完成任务；若供应商迟到返回结果，该结果不得越过取消决定自动出现在对话或资产库中。
  - 取消→cancelling（worker 收敛为 cancelled，尽力云端取消）；条件发布
    （status IN ('submitting','generating')）+ _CancelledRaceError 对象回收；
    test_cancel_race_during_finalize_reclaims_object / test_cancel_during_submit_
    records_cloud_task_id；E2E 取消中→已取消。
- [x] 成功视频保存为账户隔离资产，包含提示、模型、供应商任务标识、创建时间、可访问文字说明、预览、下载和带确认的删除操作。
  - VideoAssetProjection 全字段（prompt/model_id/cloud_task_id/created_at/description/
    media_type/content_length）；资产卡 <video> 预览、说明内联编辑（PUT）、下载
    （download 属性 + Content-Disposition）、带影响说明的删除确认；E2E 覆盖。
- [x] 供应商拒绝、超时、配额不足、密钥失效和结果过期都有明确失败原因、安全重试边界和清理行为。
  - 错误分类复用 qwen_client（429/401/5xx/超时）；cloud_timeout（60 轮上限）、
    cloud_failed、empty_result 永久失败不自动重领（安全重试边界测试）；
    失败清空 cloud_task_id 重试重新提交；清理行为由对象 pending_cleanup 轮。
- [x] 视频请求只披露当前生成所需提示和显式选择的材料，不上传完整聊天、画像、项目目录或秘密。
  - 适配器请求体只含 prompt + 固定 size；审计不含提示词与字节
    （test_audit_contains_no_video_bytes_or_prompt）；冒烟脚本 Key 泄漏检查。
- [x] 其他账户不能通过任务标识、资产地址、预览缓存或下载接口访问该视频。
  - 全部端点 scoped(account_id)+对话双查跨账户 404；缓存私有头 no-store；
    test_cross_account_isolation（服务层 7 端点）+ test_task_endpoints_are_account_scoped
    （API 层任务/资产/说明/字节/下载 8 端点）。

## Verification

- [x] 用供应商状态夹具验证提交、轮询、有限重试、取消、迟到结果、重启恢复和过期结果处理。
  - tests/video/test_wan_adapter.py（14 条）+ tests/video/test_video_service.py（23 条）：
    提交/轮询（status/task_status 兼容、video_url 提取）/fetch/cancel、取消全流程、
    迟到结果隔离、提交期间取消、重启恢复、cloud 超时、瞬态自动重试、永久失败不重领、
    轮询不消耗重试预算、删除/清理、账户隔离、审计。
- [x] 使用真实账户密钥完成 Wan 固定快照能力探测及一次低成本文生视频冒烟验证。
  - scripts/smoke_video_generation.py（BRIDGES_SMOKE_QWEN_KEY / *_FILE 显式提供）：
    真实提交→轮询→下载，核对运行记录模型快照 = VIDEO_MODEL_ID，Key 泄漏检查；
    真实能力探测（video probe）由既有 issue10 覆盖。
- [x] 建立浏览器端到端测试，覆盖提交、状态刷新、取消、恢复、预览、下载、删除和失败重试。
  - apps/web/e2e/issue32-video-generation.spec.ts（6 条）：生成流程（任务卡→资产卡→
    说明修改→下载事件）、刷新恢复、删除确认、失败重试、取消（取消中→已取消）、能力停用。
- [x] 执行跨账户任务查询、资产访问、缓存复用和下载越权测试。
  - 服务层 test_cross_account_isolation（任务/资产/字节/说明/删除 7 端点 404）；
    API 层 test_task_endpoints_are_account_scoped（任务 3 + 资产 5 端点 404）；
    缓存私有头断言（no-store）与下载越权 404（test_video_chat.py）。
- [x] 在受支持桌面浏览器中演示从聊天提示到可下载视频资产的完整路径。
  - E2E 生成流程覆盖提交→任务卡→资产卡→说明修改→下载；删除确认与刷新恢复
    独立用例覆盖；真实冒烟脚本覆盖完整供应商链路。

## Non-goals

- 不提供视频剪辑、时间线、配音合成、实时视频生成或用户选择其他视频模型。
- 不把 Wan 例外扩展到文本、Embedding、语音或图片类别。
- 不承诺应用完全关闭期间继续轮询或完成本地清理。
- 不开发移动视频编辑、触屏手势、PWA 或原生视频应用。

## Blocked by

- [31 — 交付图片生成与编辑](./31-deliver-image-generation-and-editing.md)

## Comments

- 2026-08-01：按已批准的 BridGes 改进计划发布。
