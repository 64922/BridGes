# Issue 08：退役旧 Media 写入口，保留现代聊天图片与视频主链

Status: ready-for-agent

Type: task

Priority: P1

User stories: US-RETIRE-MEDIA-01、US-RETIRE-MEDIA-02、US-QWEN-TRUTH-02

## 根因与证据

- `src/bridges/api/media.py` 仍挂载 T030–T035 的旧项目媒体、图表/科学图、分镜、沙箱、无障碍包和发布命令；`src/bridges/api/main.py` 无条件构造相关 service 并 `include_router(media_router)`。
- `src/bridges/media/storyboard_service.py` 默认使用 `DeterministicStoryboardGenerator`，明确声明“不依赖模型”；`src/bridges/media/generation.py` 也默认使用 `DeterministicSpecGenerator`。这些本地结果本身可以是合法工具输出，但旧产品入口若继续呈现为云端智能生成，会与“真实使用全局 Qwen Key”的产品承诺混淆。
- `GET /media/storyboards/{storyboard_id}/validate` 调用的验证服务会更新分镜状态，是以读取方法包装写副作用的特殊旧合同；仅按 HTTP 方法筛选会漏退役。
- 现代聊天图片/视频已有独立的图片生成、编辑和视频任务主链，并使用固定 Qwen Image/Wan 能力；退役旧 `/media` 不应影响这些现行入口。
- ADR-0026 要求旧写 API 在兼容窗口稳定返回 410，并保留历史对象的只读/导出和兼容观测，直至 Contract 门禁允许最终删除。

## What to build

1. 建立一份代码内可测试的旧 Media 命令清单，将 `src/bridges/api/media.py` 下所有会创建、修改、执行或发布旧对象的入口改为稳定 `410 Gone`。最低覆盖：
   - 旧资产：`POST /media/projects/{project_id}/assets`、`POST /media/assets`、`POST /media/assets/{asset_id}/derived`、`POST /media/assets/{asset_id}/claim-graph`、`POST /media/assets/{asset_id}/revoke`；
   - 图表/图形：`POST /media/charts`、`POST /media/figures`、`PUT /media/objects/{object_id}/spec`、`POST /media/validate-spec`；
   - 分镜/沙箱：`POST /media/storyboards`、`PUT /media/storyboards/{storyboard_id}`、`POST /media/storyboards/{storyboard_id}/code`、`POST /media/storyboards/{storyboard_id}/sandbox`、`POST /media/sandbox-runs/{run_id}/repair`，以及带写副作用的 `GET /media/storyboards/{storyboard_id}/validate`；
   - 无障碍/发布：`POST /media/accessibility/bundles`、`POST /media/accessibility/bundles/{bundle_id}/playback`、`POST /media/cross-media/consistency`、`POST /media/publish/check`、`POST /media/publish`。
2. 复用统一 retirement 合同，错误码固定为 `legacy_media_retired`，中文说明区分现代替代路径：上传/检索材料转向全局知识库，图片生成/编辑与视频生成转向聊天入口；不得承诺旧分镜、图表或沙箱存在一比一替代功能。
3. 退役 handler 不注入旧 media services、不声明/解析旧请求体、不读取路径对象；认证之后合法、畸形、空正文以及未知 ID 均得到同构 410。
4. 审核剩余 GET 路由：只有真正只读且符合历史读取/导出政策的入口可暂时保留。发现任何状态变化、任务触发、模型调用或文件写入时，必须加入退役清单。
5. 从生产组合根移除只服务于已退役命令的 service 构造和 adapter 注册；仍服务历史只读/导出的 repository 不删除。不得误删现代图片、视频、ASR/TTS、OCR 或知识库能力。
6. 为每个稳定 endpoint ID 记录隐私安全的真实/探针 410 计数，供 ADR-0026 兼容窗口使用。

## 非目标

- 不退役现代聊天中的图片生成/编辑、视频生成、ASR、TTS 和视觉替代文本功能。
- 不把旧 storyboard/chart/figure 生成器改接 Qwen；用户已确认这些旧 API 直接退役。
- 不物理删除旧媒体文件、对象、发布记录、表或导出资料。
- 不把历史 GET 入口立刻变成 404；最终删除仍受 ADR-0026 Contract 门禁约束。
- 不实现新的图表、分镜或沙箱产品，也不把静态图表错误标记为 Qwen 输出。
- 不改动固定模型矩阵；现代媒体模型一致性与边缘调用锁分别由 Issue 09、16 负责。

## Acceptance criteria

- [ ] 命令清单中的全部入口对已认证账户稳定返回 410，响应包含 `legacy_media_retired`、中文说明和适用的替代路径。
- [ ] 路由覆盖检查能发现新增的 `/media` POST/PUT/PATCH/DELETE，或具有已知写副作用的 GET；未分类命令使测试失败。
- [ ] 退役入口不解析正文、不查询路径对象，不调用 ingestion、generation、storyboard、sandbox、accessibility、publish 或 claim service。
- [ ] 调用前后旧对象、文件、任务、发布记录和 `model_run_locks` 均无新增/更新；重复调用幂等。
- [ ] `GET /media/storyboards/{storyboard_id}/validate` 不再改变分镜状态，而是走 410 退役合同。
- [ ] 保留的 `/media` GET 路由经 spy/数据库快照证明只读；若不能证明，则一并退役而不是保留隐式写入。
- [ ] 现代聊天图片生成、图片编辑、视频生成及其查询/取消回归通过，且不会路由到旧 `/media` service。
- [ ] 生产组合不再实例化仅供退役写命令使用的 deterministic generator；但不会删除现代媒体真实 adapter。
- [ ] 兼容观测不记录账户、asset/object/storyboard/run/bundle ID、请求正文、媒体内容、提示词或供应商结果。

## Test plan

1. 新增 `tests/integration/test_media_retirement.py`，从命令清单参数化所有方法/路径，断言认证、410 与稳定响应合同。
2. 对带正文入口发送合法 JSON、畸形 JSON、空正文和超大但在测试限额内的正文；断言没有 Pydantic 422 或 service 访问先于 410。
3. 为所有旧 service 注入抛错 spy，并快照数据库表、对象存储和任务队列，证明请求零副作用。
4. 增加路由枚举测试：`/media` 下任何未列明的非 GET 路由，以及明确有副作用的 GET 都使测试失败。
5. 对暂留 GET 做只读合同测试；重点验证读取不存在对象仍按历史读取规则处理，而不会触发修复/生成。
6. 运行现代图片/视频/语音聊天回归，确保退役边界没有误伤。

建议验证命令：

```powershell
python -m pytest `
  tests/integration/test_media_retirement.py `
  tests/chat/test_image_chat.py `
  tests/chat/test_video_chat.py `
  tests/image `
  tests/video `
  tests/speech `
  -q -p no:cacheprovider `
  --basetemp=$env:TEMP\bridges-issue08
```

## Observability & rollback

- 指标只按稳定 endpoint ID 聚合 `real_410_total` 和 `probe_410_total`；替代路径类别可以是低基数标签，业务对象 ID 绝不入指标。
- 发布看板分别展示旧资产、图表/图形、分镜/沙箱、无障碍/发布四组真实流量，避免一个高流量路由掩盖其他入口。
- 若错误退役了现代入口，只回滚对应路由映射；不得恢复旧 deterministic 假成功或整体撤销 410。历史数据未删除，无数据恢复动作。
- 兼容观察窗口完成前保留历史读取/导出；最终 404 和 service/表删除必须另走 ADR-0026 Contract 签核。

## Blocked by

- 无。

## Comments

- 2026-08-13：用户确认旧 `/media/storyboards`、`/media/charts` 等写 API 退役；只有当前聊天产品中的图片、视频与语音智能功能需要继续保证真实使用全局 Qwen Key。
- 2026-08-13：旧媒体确定性能力不等于模型造假；缺陷在于退役产品边界与能力声明不诚实。本 issue 用 410 消除歧义，不强行把本地工具改造成模型调用。
