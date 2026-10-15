# 工单 01 画像能力与消费者清单（2026-10-15 核查）

范围为当前 `main`（`b3d68967`）可证事实；文件路径省略 `src/bridges/` 前缀。
本清单服务于兼容义务判断：内部旧路径按迁移计划删除，外部契约另行评估。

## 1. 数据模型与写入方

| 存储 | 定义（`storage/database.py`） | 唯一写入方 | 版本常量 |
| --- | --- | --- | --- |
| `profile_four_dimension_records`（旧写入引擎） | 迁移 33 `:1691`，迁移 43 补证据列 `:2091` | `profiles/four_dimensions.py:503`（`save_record`），经 `profiles/commit.py:170-243` 编排 | `profile-four-dimensions-v1`（`four_dimensions.py:43`） |
| `profile_items`（原子条目，用户可见镜像） | 迁移 51 `:2450`，59 修复 `:2588`，64 身份列 `:2915`，67 有效期、68 反馈 | `profiles/atomic.py:1422`（`save_item`）等 | `profile-atomic-v1`（`atomic.py:115`） |
| `profile_extraction_runs/tasks/observations` | 迁移 35 `:1785/:1807/:1826`，44 `outcome`，46 `source` | `profiles/automatic.py:1177/1239/1275` | `profile-auto-v2`（`automatic.py:94`）、`profile-signal-v2`（`profiles/signals.py:12`） |
| `profile_account_controls`（使用控制） | 迁移 62 `:2809` | `automatic.py:3726`（`set_account_controls`） | `profile-controls-v1`（`contracts/profile_extraction.py:284`） |

约定：四维写入即提交，原子镜像在同一提交边界内成对写入，失败整体回滚
（`profiles/commit.py:1-47`）。

## 2. 持久化队列与执行器

- 队列：`TaskQueue`（`runtime/queue.py:107`），表 `task_claims`（`database.py:1328`），
  队列名 `profile-extraction` / `profile-replay-v2`（`automatic.py:106-107`）。
- 入队/领取/重试：`automatic.py:2173-2183`（同事务入队）、`run_retry_tick :2775`、
  `_run_retry_task :2822`、`_schedule_retry :2756`、重启恢复 `_recover_inflight_runs :1658`。
- 执行器：`chat/run_executor.py:82` 的 `GenerationRunExecutor`，后台 tick 调
  `run_retry_tick`（`:194`）；线程由 `api/app_setup_executor.py:28-53` 启动（daemon，
  test 环境不启动）。`AutomaticProfileService` 自身无独立线程。
- 预算：重试 3（`automatic.py:110`）、邻居 2（`:120`）、租约 60s（`:1507`）、
  知识晋升窗口 90 天（`:185`）、可靠性 ≥0.6（`:190`）、切片上限（原子 4，`atomic.py:127`；
  表达策略 6，`chat/lightweight_policy.py:114`）。
- 回放通道：`profiles/replay.py:1-64`、CLI `profile-replay`（`cli/main.py:78-196`）、
  排障脚本 `scripts/retry_exhausted_profile_extractions.py`。

## 3. 画像相关索引（`storage/database.py`）

- 四维：`:1710/:1725/:1742/:1763`；抽取：`:1803/:1822/:1840`、隐私阻止 `:2126`。
- 原子：`:2472/:2610`（account_status）、`:2495/:2633`（migrations）、`:2949`（fact_key）、
  `:3165`（feedback）。启动强制校验清单 `REQUIRED_INDEXES :3722-3739`。
- 表内 UNIQUE：四维 `(account_id, source_record_id, dimension)` `:1706`；
  items `(account_id, identity_key)` `:2468`。

## 4. 外部 API（前缀 `/profiles`，`profiles/api.py:42`，挂载 `api/app_routes.py:72`）

- 四维：`GET /profiles/four-dimensions :91`、`PATCH /{record_id} :170`、
  `POST /{record_id}/withdraw :193`、`DELETE /{record_id} :216`。
- 原子条目：`GET /profiles/items :271`、`PATCH /{item_id} :285`、`DELETE /{item_id} :309`、
  `GET /{item_id}/evidence :338`、`POST /{item_id}/feedback :359`。
- 使用控制与状态：`GET /profiles/controls :128`、`PUT /profiles/controls :142`、
  `GET /profiles/status :104`。
- 迁移：`POST/GET /profiles/items/migration :387/:409`、回滚 `:432`。
- 退役旧九维：`profiles/legacy_api.py:36-215` 全部 410 Gone（替代路径字符串
  `/account/profile` 无实现路由，`legacy_api.py:21`）。
- 明示授权入口在聊天内同步指令：`automatic.py:1825`（由 `chat/service_profiles.py:51` 调用），
  无独立 HTTP 端点；隐私说明 `profile_privacy_disclosures`（`database.py:1844`）无专用路由。

## 5. 读取消费者

- 回答生成切片编译（主消费者）：`chat/turn.py:6792` `_compile_profile_slice`，
  优先原子 `compile_adopted_slice`（`atomic.py:2719/:2793`），否则四维/automatic/旧九维。
  切片上限与「与当前问题无关」排除逻辑在此路径。
- 学习路径画像读取：`chat/turn.py:3587-3659`（academic → level_assumption）；
  `learning/progress.py` 仅保存切片引用。`learning/teaching_gate.py:880` 的
  `compose_first_plan_and_lesson` 全仓无调用方（疑死代码）。
- 聊天摘要：`chat/summary.py` 不读画像正文，仅注册撤回监听 `:758`
  （`api/app_setup_chat.py:62-64`），实现派生摘要失效。
- 检索/知识库：无引用——画像不进入索引/检索（`ingestion/knowledge_base/search/retrieval` 零匹配）；
  `MaterialDomain.PROFILE` 无消费方（`chat/task_materials.py:87`）。
- 其他：健康检查降级态（`api/app_health.py:85-123`）、迁移对账
  （`atomic.py:2963/:3504`、`replay_repository.py:308-315`）、发布探针
  （`closeout/authenticity_gate.py:468-486`）。
- 评估消费者：`evaluation/profile_quality.py:342`、`expression_deterministic.py:138`、
  `rollback_rehearsal.py:142`、本工单 `evaluation/profile_journey_runner.py`（直接 SQL）。

## 6. 退役与兼容义务

- 双写/镜像桥接：`atomic.py:1-5`、`:2555-2560`（旧正文键兼容）、`:13/:185`（旧墓碑键）；
  四维仍是写入引擎，原子为镜像（`CONTEXT.md:9`）。
- 迁移钩子：四维合同门 `four_dimensions.py:143-219`；旧九维→四维
  `migrate_account :1262-1411`；原子迁移/回滚/对账 `atomic.py:2942/:3192`。
- 旧接口窗口：仅保留失败语义（`legacy_api.py:1-33`），`openapi.json` 仍收录 410 路径。
- 文档义务：ADR-0043（四维与镜像将退出、旧运行不得重启旧写入；外部 API 兼容
  与内部删除分开处理）、`CONTEXT.md:213-238`。
- 未接线能力（当前无生产调用方）：`career_plan/background.py` 的
  `AdoptedProfileSlice` 消费（`api/app_setup_research.py:41-47` 未注入 provider）、
  `teaching_gate.compose_first_plan_and_lesson`。

## 7. 真实模型能力与缺口（本次实测）

- 凭据：OS 凭据库存在 Qwen 与 Tavily（`credential_store`），真实基线可运行；
  运行锁只记录存在来源，不记录正文。
- 真实基线成本/时延见 `baseline-report.md`（10 次模型调用，4251 输入 / 720 输出 token）。
- 缺口（如实记录，供 18 复核）：真实模式下 `adopted_in_next_turn` 的因果收益无法
  与「会话历史直接可见」区分；本次以切片内容 + 探测记录证据，不做收益归因。
