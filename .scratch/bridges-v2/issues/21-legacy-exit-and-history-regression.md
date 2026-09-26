# 21 — 旧入口退出与历史回归

**What to build:** V2 全部正式入口可用后，用户只看到获批的桌面体验；旧结果仍能查看与导出，退役能力不再产生新写入。

**Blocked by:** 04 — 自然表达与专用人味化退出；06 — 聊天文件附件；07 — 知识库检索与通用 OCR；08 — 原子画像；09 — Qwen 凭据与主模型配置；10 — Tavily 与高德凭据管理；12 — 校园通勤；13 — 学习资料推荐；14 — 华东交通大学吧信息搜集；15 — 职业规划；16 — GitHub 项目推荐；20 — 学习总结

**Status:** ready-for-human

**实施前必读：** [ADR-0030](../../../docs/adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md)、[V2 设计索引](../../../docs/v2/README.md)及索引所列六份设计文档；先核对阻塞票已验收。

- [x] 旧图片／视频生成、科学文章创作、旧学习计划、朗读及无关工作台入口和写入／重试 API 关闭；历史链接不会误触发新执行。
- [x] 旧会话、附件及生成结果仍可按账户查看和导出；版本化迁移有备份、数量对账、部分失败恢复与回滚验证。
- [x] 注册登录、账户隔离、数据删除、备份和审计等保留能力在新旧数据上回归；新检查点、OCR 缓存、附件草稿和画像墓碑按各自归属参与删除与恢复。
- [x] 获批桌面交互脚本在 1280×720、1440×900 和 1920×1080 通过，模式、六模块、附件、学习阶段、画像及设置均连接真实服务。
- [x] 核对全部旧调用方后移除无用编排与测试，只保留历史只读所需兼容层；正式界面验收后移除一次性原型代码及演示数据。
- [x] 横向回归覆盖跨模块上下文、模式锁、学习错题、画像删除、密钥失败保留和外部证据不可得的降级。
- [x] 桌面回归核对完整 `+` 菜单顺序与说明、输入框和菜单键盘操作、消息来源行、学习阶段条、画像空态及模型验证结果；未通过的模块不显示为可用。

## Comments

### 2026-09-26 实现

- 分支 `v2/21-legacy-exit-and-history-regression`（工作树 `BridGes-21-legacy-exit`），提交：
  `df2114f` 退役旧生成入口并移除人味化编排、`b6f6b55` 迁移前备份与图状态删除归属、
  `7cd31fa` 修 tieba 搜索合同、`319ebb4` 三视口桌面验收脚本、`d7993d1` 验收依据改指脚本并移除一次性原型、
  `d592b1b` 编排层收口退役媒体提交、`f2b1c00` 备份成功后再清理旧还原点、
  `bfe0af5` 清理孤儿导入与过期注释、`a04e06b` ADR-0011 补记、`7182ec5` 内置清单调用修复与失效测试清理。
- **AC1 关闭旧入口**：图片/视频生成、回答朗读、科学文章创作、表达（人味化）的公开写入口全部稳定 410 与稳定错误码；
  历史任务/资产/朗读投影仍按账户只读可查；历史消息的消息级重试同样 410，不重新入队
  （`tests/retirement/test_legacy_generation_exit.py` 8 项）。
  编排层同时收口：升级前遗留的、带图片/视频载荷的排队运行在聊天编排里只收敛为
  `legacy_image_retired`／`legacy_video_retired`，不再创建生成任务；退役的提交编排
  （`_stream_image_request`／`_stream_video_request` 与两个 Orchestrator 接缝及其注入）已移除，
  只保留只读面与供应商适配器——能力清单 v3 明示「适配器与发布门探针保留，历史结果按只读面继续可查可导出」。
- **旧学习计划核对结论（不新增守卫）**：① `learning_projects` 的创建/更新/删除/文件上传/删除仍是 410
  `legacy_file_source_retired`（更早的退役票完成，本票核对未变）；② 教学计划只由显式学习模式创建
  （`mode == ChatMode.STUDY`），日常陪伴模式不写 `teaching` 投影，模式锁由既有测试固定，历史消息的
  `teaching` 仍按只读卡片渲染。本票关闭的是「从日常对话自动启动旧学习计划」这条路径与 learning_projects 写入口。
- **AC2 历史与迁移**：新增 `_backup_before_migration`（升级既有库前按 WAL 快照落一份一致性备份，备份失败则不迁移；
  首次建库与已最新版本的重启不产生文件；新备份成功后才清理旧还原点，清理失败只告警）、`legacy_media` 导出类别
  （退役能力的历史图片/视频任务与资产元数据按账户导出）。`tests/storage/test_v2_21_migration_gate.py` 7 项覆盖备份、
  数量对账、备份失败拒绝迁移、中途失败整体回滚、用迁移前备份回到可读旧数据，以及旧还原点在失败/成功两条路径上的存留。
- **AC3 删除与恢复归属**：「账户数据」清单补齐 V2 图状态与生成运行（`generation_events` 先于父表、
  `graph_checkpoint_writes`/`graph_checkpoints`、`generation_runs`、`retrieval_decisions`、`study_states`、
  `workflow_runs`、`smtp_verification_attempts`），新检查点、OCR 缓存、附件草稿与画像墓碑按各自归属参与删除与恢复
  （`tests/lifecycle/test_v2_21_retired_history_lifecycle.py`）。注册登录、账户隔离、删除、备份与审计等保留能力的回归
  由分支/main 全量失败名单双向比对证明（见下）。
- **AC5 清理**：文章人味化编排（`skills/humanizer` 的 SKILL、首稿/修订、`humanize_eval` 评测框架）与其专用测试整体移除，
  只保留历史只读所需兼容层（`contracts/humanizer` 只读投影、`messages.skill` 历史解析、410 守卫、历史结果卡）；
  确定性表达任务契约编译器迁至无环叶子包 `bridges.expression_task`；评测中心收敛为 6 任务/20 用例/5 被测系统
  （`humanization` 维度与任务→维度映射保留给历史报告与旧锁重放，已补注释）；能力清单升到 v4（`humanizer` 由
  `qwen_model` 改判 `retired`，路由覆盖率仍为 14）。正式界面验收后移除一次性原型 `apps/web/prototypes/v2-desktop/**`
  与演示数据，README、docs/v2 与 ADR-0030 的验收依据改指获批脚本；ADR-0011 补记净室记录文件已随编排移除。
  失效测试一并清理：三个只能靠退役科学写入口建数据的 `tests/integration` 模块（`test_science_api`、
  `test_claim_evidence_api`、`test_fact_lock_api`）删除，插件测试去掉人味化内置的断言与用例。
- **AC4/AC7 桌面验收**：新增 `apps/web/e2e/issue21-desktop-acceptance.spec.ts`，连真实 API、后台执行器与前端，
  零 page route mock，三视口各四项：首页模式与 `+` 菜单（完整顺序与说明、菜单与输入键盘操作、六模块选择与移除）、
  六模块派发到真实服务、附件草稿与学习阶段、画像空态与设置（含一次真实失败的模型验证，断言「最近一次验证：未通过」
  与失败模型标识）。对「未通过的模块不显示为可用」的核对方式：逐模块用真实服务派发并断言终态（成功或如实降级卡），
  同时断言退役能力不出现在任何入口；模型验证未通过时界面不得显示为可用。

### 验证与审查

- 全量对照（分支 vs main，同口径：`PYTHONPATH=src`、仓外 basetemp、`-p no:cacheprovider`、`--tb=no -q -rfE`，
  两侧同样 deselect `tests/integration/test_runtime_smoke.py` 三条 start 冒烟——本机共享数据目录锁被实例占用会挂死；
  main 侧另加 `--ignore=tests/humanize_eval`，该目录分支已删，留在主仓会因 `tests/search/conftest.py` 同名遮蔽
  产生无关导入失败）：
  - 分支 **249 失败 / 3719 通过 / 37 跳过 / 3 deselect**（0 error，878s）；main **255 失败 / 4110 通过 / 40 跳过 / 3 deselect**（0 error，885s）。
  - 失败名称集合双向比对：**仅分支失败 0 条**（分支 249 条失败全部落在 main 的 255 条既有名单内）；仅 main 失败 6 条，
    其中 4 条 `tests/speech`（朗读/听写用例在本票已按退役改写并通过）、2 条人味化演示用例（随能力退役删除）。
  - 用例总数分支比 main 少 400 条，来自人味化编排/评测框架/插件的测试随能力退役删除，以及三个只能靠退役科学写入口
    建数据的 integration 模块。
- 定点：`tests/storage/test_v2_21_migration_gate.py` 7 项、`tests/retirement/test_legacy_generation_exit.py` 8 项、
  `tests/science` 59 项、`tests/tieba` 45 项（含本次发现缺陷的回归用例）全部通过。
- 合并后（`ba0d9da7` 合并树与分支树 `rev-parse ^{tree}` 相同）在 main 上补跑定点并逐目录对基线：
  `tests/chat tests/plugins` → **140 失败／531 通过**（= 基线 chat 101 + plugins 39，逐目录数相同）；
  `tests/storage tests/retirement tests/contracts tests/skills tests/lifecycle` → **16 失败／155 通过**
  （= 基线 retirement 11 + lifecycle 5，storage／contracts／skills 均 0）；本票三个新测试文件
  （`test_v2_21_migration_gate`／`test_legacy_generation_exit`／`test_v2_21_retired_history_lifecycle`）
  定点复跑 **20 项全通过**，且不在全量失败名单内（`tests/retirement` 的 11 条失败全部来自既有的
  `test_user_extensions_retirement.py`）。
- 前端：`vitest run` **22 文件 / 189 用例**通过；`tsc --noEmit` 干净（exit 0，无输出）。
- `ruff`：改动文件与 main 同量（`src/bridges/chat/{turn,service}.py` 与 `src/bridges/api/main.py` 两侧同为 107 条既有告警），
  本票引入的 F401 已清零。
- 桌面验收：`apps/web/e2e/issue21-desktop-acceptance.spec.ts` 三视口 ×4 用例（每视口：首页模式与 `+` 菜单、
  六模块真实派发、附件与学习阶段、画像空态与设置）。逐用例独立起停服务跑了三轮矩阵：1280×720 三轮全绿、
  1440×900 第 1／3 轮全绿、1920×1080 第 2 轮全绿——即 12 个 (视口, 用例) 组合**每个都有通过实证**；
  三轮里的 3 次失败全在「六模块连接真实服务」，证据指向本机 Next dev 掉线而非产品服务端（见「已知边界」），
  对应组合隔离复跑通过（1920×1080 8.1s、1440×900 19.2s／19.4s、1920×1080 19.7s）。脚本与原始日志／截图留档见
  `.tmp/issue21/`（抖动证据汇总：`.tmp/issue21/acceptance-flake-evidence.md`）。
- 桌面验收发现并修复的产品缺陷：贴吧模块首轮以「生成过程出现内部错误」收场，根因是
  `WebSearchServiceAdapter.search_public` 读 `result.content`，而合同 `WebSearchResult` 只有 `snippet`／`content_summary`
  （替身按 `content` 建模，所以只要搜索真的返回结果就抛 AttributeError）。改用 `snippet` 并把替身改成与生产合同同形，
  新增回归用例 `test_adapter_accepts_real_web_search_projection_contract`（证据留档 `.tmp/issue21/tieba-attribute-error-evidence.txt`）。
- `/code-review` 两轴结论与处置：修复了备份清理顺序缺陷（失败路径会先消耗旧还原点，已改为快照成功后再清理并补两条用例）、
  插件内置清单调用仍传注册表的 TypeError、孤儿导入与过期注释（`MessageList.tsx` 的 `useRef`、`ChatThread.tsx` 的
  TTS 注释、`skills/registry.py` 的 `datetime`）、`docs/adr/0011` 引用已删文件、`openapi.json` 契约同步、
  以及 AC5 遗漏的失效测试清理；经评估保留：显式 `replacement_path="/"`（与既有 `_REPLACEMENT_PATH` 风格一致、行为相同）、
  图片/视频供应商适配器与 `ImageService`/`VideoService`（清单 v3 明示保留）、`EvaluationDimension.HUMANIZATION`
  （历史报告与旧锁重放，已补注释）。

### 已知边界

- 桌面验收的外部来源使用确定性替身（`BRIDGES_CLOSEOUT_FIXTURES=true`：arXiv/论文检索与外部平台），真实 Tavily、高德、
  arXiv 与 Qwen/Wan 凭据仍需人工在真环境验证；脚本对未接线来源断言如实降级，不把替身结果当真实可用。
- 本机共享数据目录锁（`%LOCALAPPDATA%\BridGes\data\.bridges.lock`）被运行中的实例占用时，`tests/integration/test_runtime_smoke.py`
  的三条 start 冒烟会挂死或误报，全量口径按既有约定 deselect。
- 桌面验收的 Next dev 掉线抖动（环境，非产品缺陷）：三轮矩阵 36 次调用里 3 次「六模块连接真实服务」失败
  （1920×1080 两次、1440×900 一次），签名有三种且都不在产品服务端——(a) 页面重取会话的 GET 经 Next dev
  重写代理（`/api/*` → `127.0.0.1:8011`）网络层失败，页面如实进入「对话加载失败／Failed to fetch（可重试）」
  终态（失败发生在第 3／4 个模块轮次之后，该轮首轮 POST 已 2xx）；(b) 请求就此挂住，`waitForResponse`
  等满 10 分钟超时；(c) 运行器进程自身退出（`exit=127`，无用例输出）。API 侧逐秒探针：第 3 轮扫描 151 个
  样本在「服务在跑」的窗口内**全部 200**（非 200 只出现在启动窗口与用例结束的 teardown），对一次挂住的
  复跑亦是 60 秒内 48/49 为 200（唯一非 200 是 API 尚未启动的第 2 秒）⇒ 挂住期间 API 全程健康，失败在
  Node／Next dev 主控层。该族问题在本机已有记录（矩阵运行器注释：「单次长跑里 Next dev server
  会在多次编译后掉线」），逐用例独立起停服务是既有缓解；`playwright.config.ts` 的 `retries: process.env.CI ? 2 : 0`
  是仓库既定取舍（CI 重试、本地不重试），本票不改配置，正式验收按逐视口独立运行、失败则以隔离复跑取证
  （证据汇总留档 `.tmp/issue21/acceptance-flake-evidence.md`）。
- 图片/视频的供应商适配器与发布门探针按清单 v3 保留（历史只读面与发布门需要），它们的公开写入口已关闭。
- 既有（本票未引入、也未修复）的收集期循环导入：`tests/plugins` 若在没有任何模块先完整加载 `bridges.chat` 的情况下被收集，
  `test_plugin_api.py`／`test_plugin_service.py` 会抛 `ImportError: cannot import name 'PluginService' from partially
  initialized module`，链路为 `plugins.service` → `bridges.chat.attachments` → `chat/__init__` → `chat.service` →
  `context_compiler` → `chat.turn` → `chat.selections` → `plugins.service`。已用合并前主仓树核对：在 `e22e3f3` 上
  单独跑 `pytest tests/plugins` 报同一错误（该版本 `plugins/service.py` 已有 `from bridges.chat.attachments import
  sniff_media_type`），故属既有顺序依赖，非本票引入；与 `tests/chat` 一起收集（或按全量顺序）即正常收集
  （合并后实测 `tests/chat tests/plugins` 收集无错、结果与基线逐目录一致）。本票不改动该链路。

