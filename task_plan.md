# 任务规划：架构加深修复（architecture deepening）

状态：候选 1 已完成；候选 2–8 已拆分为独立 issue（含详细解决方案），
按用户指示暂停实现，逐个实施前先评审对应 issue。

来源：`/improve-codebase-architecture` 评审报告（8 候选 + 4 小摩擦），按报告 Top 推荐顺序。

## 候选清单

1. **加深聊天回合管线** ✅ 已完成（2026-08-06）
   - 新模块 `chat/turn.py`：`TurnOrchestrator`（单一 stream_turn 接口、模式路由内部
     seam、`_run_retrieval` 检索单点、`assemble_payload` 组装单点、thinking 类型化为
     `ChatThinkingSummary`）；`ChatService` 瘦身 4188 → ~1390 行并委托
   - 验收：146 chat 测试 + 全量 2148 pytest 通过、ruff 干净、公开导入面不变
   - 详见 `.scratch/bridges-improvement/issues/42-architecture-deepening.md`

2. **统一领取型任务契约** → Issue 43（已完成）
3. **对象授权收拢** → Issue 44（已完成）
4. **服务不再直读不属于自己的表** → Issue 45（已完成）
5. **前端数据获取 module** → Issue 46（解决方案已写，待实施）
6. **单一流事件 adapter** → Issue 47（解决方案已写，待实施）
7. **密钥环 seam** → Issue 48（解决方案已写，待实施）
8. **跨缝展示知识收拢 + 小摩擦** → Issue 49（解决方案已写，待实施）

## 里程碑

- [x] 候选 1 完成：tests/chat 全绿 + 全量回归通过 + ruff 干净
- [x] 候选 2 完成（Issue 43）：runtime/queue.py 深模块 + 5 子系统迁移 +
      workflows 持久化 + 存量回填，全量 2180 pytest 通过、ruff 干净
- [x] 候选 3 完成（Issue 44）：scope 收拢共享项目成员判定 + service_subject 工厂 +
      projects/media/workflows 预过滤删除 + sharing/_require_member 走 enforcer +
      MCP 接入 enforcer + media/science 项目源域编码修正（PERSONAL_VAULT），
      全量 2190 pytest 通过、ruff 干净（双轴审查修复 4 处后提交）
- [x] 候选 4 完成（Issue 45）：retrieval/chat.attachments 直连收编进属主
      repository（新增 AttachmentRepository、ConversationRepository 复用
      get_conversation、BridgesObjectRepository 补 mark_pending_cleanup/
      object_status/object_metas、RetrievalRepository 补检索域读方法），
      docs/table-owners.md 表属主清单 + 遗留直连声明，
      全量 2190 pytest 通过、ruff 干净（改动文件）
- [x] 候选 5 完成（Issue 46）：前端数据获取深 module（data.ts + ApiClient
      seam + vitest 首套单测 19 条 + 两张任务卡轮询迁移 + 6 个高频组件迁移），
      双轴审查修复：首帧 loading 竞态（isFetching 初始 true）、轮询首载失败
      置错（不再无限转圈）、reload 可等待（变更后先等数据再继续）；e2e 全量
      269+2 通过、build/lint/tsc 干净（issue04/08 基线既有失败已 A/B 验证）
- [ ] 候选 6 完成（Issue 47）
- [ ] 候选 7 完成（Issue 48）
- [ ] 候选 8 + 小摩擦完成（Issue 49）
- [ ] 提交 + 验收证据汇总

## GQ-05：知识库向量化、摄取与检索只使用全局运行凭据（2026-08-08）

来源：`.scratch/收尾/全局千问密钥整改-Issue计划.md` GQ-05（ready-for-agent）。

- [x] T1 改造 QwenEmbeddingPort：全局 Secret 构造、删除账户凭据依赖与探测门函数 → verify: tests/ingestion 45 通过
- [x] T2 服务层移除账户探测门（ingestion/retrieval service）→ verify: tests/ingestion tests/retrieval 全绿
- [x] T3 组合根接线（api/main.py + runtime/executor.py 同构构造 + 生产录制禁令）→ verify: 相关集成测试全绿
- [x] T4 测试夹具清理（conftest/kb_support/lp 等移除逐账户探测播种）→ verify: 五大测试目录全绿
- [x] T5 新增多账户隔离回归（同一全局 Embedding 端口两账户互不可见）→ verify: 新测试绿
- [x] T6 全量回归 + 安全披露 + ruff/mypy → verify: 2209 pytest 全绿，ruff/mypy 与基线一致
- [x] T7 双轴代码审查 + 提交 → verify: c724553（审查修复：embed 失败改关键词降级+恢复、检索侧可操作原因、合同描述与前端信号同步）

## GQ-06：删除账户级百炼密钥用户面与公开 API 合同（2026-08-08）

来源：`.scratch/收尾/全局千问密钥整改-Issue计划.md` GQ-06（ready-for-agent）。

- [x] T1 删除前端密钥用户面（页面/组件/菜单项/设置中心卡片/API 客户端方法/模板入口/DataPrivacy 文案）→ verify: typecheck 干净、issue10 负向 E2E 4 条通过
- [x] T2 删除后端公开路由（/auth/key-settings* 与 _test/capabilities），RecentAuthRequired 迁至 auth.py → verify: test_auth_api 负向测试 404 断言通过
- [x] T3 重新生成 OpenAPI 与 TypeScript 合同（账户 Qwen Key 投影/探测状态清零）→ verify: test_openapi_sync 通过
- [x] T4 E2E 负向回归替换（issue10 重写；issue08/12/04 菜单三项；issue30/36 移除探测替身）→ verify: 60 相关 E2E 通过（1 项 issue04 附件为基线既有失败）
- [x] T5 双轴代码审查修复（issue36 sed 误删导航行、contracts 描述、陈旧注释、临时产物清理、issue08 视觉 cookie 注入修复）→ verify: 全量 2204 pytest + 构建 + ruff/mypy 与基线一致
- [x] T6 提交 → verify: adaa6d1

## GQ-07：安全清退历史账户百炼秘密、探测状态与旧实现（2026-08-08）

来源：`.scratch/收尾/全局千问密钥整改-Issue计划.md` GQ-07（ready-for-agent）。

- [x] T1 固定模型常量迁移到 AI 领域所有者（ai/fixed_models.py 单一事实源，含 api/main.py 与 evaluation 全量收敛）→ verify: ruff/mypy + 相关测试
- [x] T2 删除账户 Qwen 凭据服务/探测模块/契约与生命周期接线（service/probes/matrix/contracts.credentials；组合根、deletion、backup、executor、测试夹具）→ verify: tests/lifecycle tests/reminder tests/security tests/chat + rg 无活跃命中
- [x] T3 一次性幂等秘密清退模块 + CLI 启动硬门（StateStore.delete、has_credential_backend、retire.py、start/api/worker 三处接线；枚举并入 key_metadata 残留账户）→ verify: tests/credentials/test_retire.py 9 条 + 子进程级成功/失败/幂等验证
- [x] T4 文档同步（ADR-0005/0007/0009/0016/0018/0024、CONTEXT.md、README.md、审计枚举 legacy 注明、contracts/video.py）→ verify: 最终 rg 扫描仅命中允许项（9 处，全部为遗留注明）
- [x] T5 全量回归 + 双轴代码审查 + 提交 → verify: 全量 pytest 2192 通过 + ruff/mypy 与基线持平（审查修复：模型常量全量收敛、残留账户枚举、sqlite 损坏中文包装）

## GQ-08：完成全载体文档、黄金路径与发布门验收（2026-08-08）

来源：`.scratch/收尾/全局千问密钥整改-Issue计划.md` GQ-08（ready-for-agent）。

- [x] T1 现状核查（README/manual/Compose/CLI 帮助/黄金路径测试/脚本）→ verify: 差距清单（manual 旧账户密钥文案、Compose 未注入 Key、CLI start 帮助缺说明、三个冒烟脚本引用已删除模块）
- [x] T2 全载体文档修复（README 启动/能力/FAQ/密钥段落、manual、compose 注入 + 文件挂载示例、CLI start docstring、pyproject 补 tzdata）→ verify: rg 残留仅命中遗留注明
- [x] T3 黄金路径纵向测试（全新数据目录 + 零 Key 配置：注册→聊天→知识库向量化/检索→听写→朗读→图片→视频全成功）→ verify: tests/integration/test_gq08_golden_path.py 通过
- [x] T4 启动失败三态显式测试（缺失/空串/不可读文件 → 中文错误 + 非零退出 + 无孤儿进程）→ verify: test_runtime_smoke 12 通过
- [x] T5 冒烟脚本收口（删除 smoke_key_probes 死代码、ingestion/image/video 迁到全局凭据与 fixed_models、计费提示）→ verify: 编译通过
- [x] T6 修复 issue37 陈旧断言（导出范围表"不包含百炼 Key"→ 现文案）→ verify: 单跑 6/6 通过
- [x] T7 全量验证（openapi 幂等 / lint 0 error / typecheck / unit 19 / build / ruff·mypy 变更文件干净 / pytest 全量）→ verify: 见发布报告
- [x] T8 发布报告 + 计划状态 + 提交 → verify: .scratch/收尾/GQ-08-发布验收报告.md

## 收尾 Issue 02：持久化后台生成运行（2026-08-08）

来源：`.scratch/收尾/issues/02-durable-generation-runtime.md`（ready-for-agent）。

- [x] T1 数据层：schema v29（generation_runs/queued→running→done|failed|stopped、租约/尝试号/终态原因/脱敏耗时；generation_events 游标事件）+ repository 全量方法（含租约原子领取/续期/收尸/账户隔离）→ verify: 冒烟脚本断言通过
- [x] T2 ChatService 发送/重试同事务创建消息+queued 运行+started/profile 事件+入队统一领取队列；stop 写 stop_requested+等待收敛；读取收敛改运行表判定 → verify: tests/chat 服务层全绿
- [x] T3 GenerationRunExecutor 后台执行器（受监督循环：收尸→领取→回合编排→事件持久化+心跳续租+停止看门狗→终态补发；崩溃恢复上限 2 次）→ verify: 竞争/失联/恢复反馈环测试
- [x] T4 API 改造：POST 返回创建响应（run_id/cursor/消息投影），新增 GET events 游标订阅端点（回放+心跳+终态结束），跨账户 404 → verify: test_chat_api 全绿
- [x] T5 前端改造：创建响应建 ActiveRun+游标订阅+断线重连+页面重开恢复（卸载不再调用停止，绝不重复发送/调用模型）→ verify: typecheck 干净
- [x] T6 e2e 协议替身适配 14 个文件（POST 返回 run + events 回放）+ 共享 helper → verify: issue11/14/21 等冒烟通过
- [x] T7 反馈环测试（真实 HTTP+SQLite+可控慢模型：发送→切会话→断开→重连→单一 done 运行；刷新不重复；双执行器竞争；worker 失联恢复+收尸）→ verify: 4 条反馈环测试通过
- [x] T8 全量回归 + 双轴代码审查 + 提交 → verify: 全量 2200 pytest + e2e 265 通过（审查修复：收尸补发终态事件/消息收敛、generation_worker_lost 登记可重试、停止 thinking 语义、死代码清理、重复收敛）

## 收尾 Issue 05：修复 arXiv worker 的 Windows UTF-8、启动握手和错误分类（2026-08-08）

来源：`.scratch/收尾/issues/05-arxiv-worker-reliability.md`（ready-for-agent，Blocked by 01 已完）。
分支：`05-arxiv-worker-reliability`（基于 c3682480，不动主线）。

- [x] T1 协议 UTF-8 化：父进程管道显式 `encoding="utf-8"` + 子进程 `PYTHONIOENCODING/PYTHONUTF8` 受控模式 + worker 启动 reconfigure 标准流 → verify: 真实 worker 集成测试（emoji/非断行连字符往返，原必红 smoke 转绿）
- [x] T2 健康握手：worker 启动写 `ready` 行，父进程握手/单次响应均设截止时间；stderr 有上限脱敏采集 → verify: 握手超时/启动即退/中途退出测试
- [x] T3 错误分类：arxiv_startup/arxiv_handshake/arxiv_worker_exit/arxiv_parse/arxiv_timeout/arxiv_permission/arxiv_cancelled 互不混淆，取消投影 CANCELLED 而非 startup → verify: 各故障模式稳定错误码断言
- [x] T4 一次安全重启：崩溃关旧句柄、重启一次仍失败才终态、无无限循环/僵尸/句柄泄漏 → verify: 崩溃计数测试（恰 2 次 spawn）
- [x] T5 取消 2 秒回收：stop_event 穿透 process client，取消期间终止当前请求 → verify: 取消耗时断言
- [x] T6 诊断日志：阶段/退出码/耗时/重启次数可见，不含查询正文/摘要/环境秘密；最小环境白名单（代理 + Windows 必需）→ verify: 环境契约单测 + 日志内容检查
- [x] T7 全量回归 + 双轴代码审查 + 提交 → verify: 全量 pytest + 相关 e2e 通过

## 收尾 Issue 06：端到端时延预算、阶段埋点和有界降级（2026-08-08）

来源：`.scratch/收尾/issues/06-latency-budgets-observability.md`（ready-for-agent，Blocked by 01/02 均已完）。
分支：`06-latency-budgets-observability`（基于 c3682480，不动主线）。

- [ ] T1 统一阶段时钟与预算控制器模块（阶段枚举 queued/local_retrieval/public_search/model_generation/quality_check/repair/finalizing、总预算 120s、外部调用 timeout 集中配置、剩余预算重试门、脱敏指标）→ verify: 单元测试覆盖阶段转换与预算耗尽
- [ ] T2 回合编排接线：阶段事件发射 + 预算重试 → verify: 阶段顺序断言 + 重试预算边界测试
- [ ] T3 独立公开搜索并行执行 → verify: 并行墙钟断言 + 顺序确定性断言
- [ ] T4 前端阶段展示与首事件时效（非流式 1s 内首真实阶段）→ verify: e2e 断言阶段文案与时效
- [ ] T5 超预算有界降级终态（草稿带警告交付/失败阶段说明可重试；注入 30s 慢搜索不等待）→ verify: 反馈环测试
- [ ] T6 本地性能摘要 p50/p95/超时率/阶段占比/重试次数（防回归：本地适配器 p95 首 token ≤2s、终态 ≤5s；无遥测外传、日志无用户内容）→ verify: 摘要单测 + 脱敏扫描
- [ ] T7 可控时钟反馈环测试（快速/慢/超时/一次失败后成功四态）→ verify: 反馈环测试全绿
- [ ] T8 全量回归 + 双轴代码审查 + 提交 → verify: 全量 pytest + e2e 通过

## 收尾 Issue 10：QQ 授权码原页再认证与延迟收件验证状态机（2026-08-08）

来源：`.scratch/收尾/issues/10-qq-verification-state-machine.md`（ready-for-agent，Blocked by 01 已完）。
分支：`10-qq-verification-state-machine`（基于 c3682480，不动主线）。

- [x] T1 契约与数据层：SmtpAttemptState 六态 + SmtpSettingsProjection 扩展（attempt_state/attempt_deadline_at）+ 迁移 30（smtp_verification_attempts 表、reminder_settings.smtp_attempt_id）→ verify: 契约测试 + 投影不含秘密
- [x] T2 网关分阶段：send_verification_mail/check_verification_receipt + 同一 IMAP 会话/NOOP/断线重连；e2e_mail_server/fake_mail 补 NOOP → verify: 适配器测试（含短窗口超时）通过
- [x] T3 服务层状态机：attempt 创建/supersede、单步推进、120s 窗口（可配置）、终态提交检查当前 attempt（原子 WHERE smtp_attempt_id）、错误码区分、supervisor 循环替代 daemon thread、重启恢复 → verify: 状态机单测 9 条（虚拟时钟）
- [x] T4 反馈环测试：必红 1 reauth→自动重试 save 单 attempt（API 层）；必红 2 10 秒晚到收敛 verified（closeout）；A/B 竞争、删除期间迟到、IMAP 断线、120s 超时、重启恢复、秘密扫描 → verify: 全部通过（closeout 3/3，含 receipt_timeout 短窗口）
- [x] T5 前端原页再认证：reauth_required 不再整页阻断，卡片内密码确认+自动重试+取消清空；verifying 阶段文案（发送中/确认收件）；轮询覆盖 120s；返回恢复权威状态 → verify: typecheck 干净（NewChatHome 错误属外部并行会话）+ issue33 e2e 3/3（修复 sendNow 展开竞态）
- [x] T6 全量回归 + 双轴代码审查 + 提交 → verify: 全量 pytest 2226 通过（2 条外部会话 flaky 重跑绿）+ issue33 e2e 3/3，双轴审查修复 5 处，提交 c80de79
## 收尾 Issue 06：端到端时延预算、阶段埋点和有界降级（2026-08-08）

来源：`.scratch/收尾/issues/06-latency-budgets-observability.md`（ready-for-agent，Blocked by 01/02 均已完）。
分支：`06-latency-budgets-observability`（基于 c3682480，独立 worktree，不动主线）。

- [x] T1 统一阶段时钟与预算控制器模块（阶段枚举 queued/local_retrieval/public_search/model_generation/quality_check/repair/finalizing、总预算 120s、外部调用 timeout 集中配置、剩余预算重试门、脱敏指标）→ verify: 单元测试覆盖阶段转换与预算耗尽
- [x] T2 回合编排接线：阶段事件发射 + 预算重试 → verify: 阶段顺序断言 + 重试预算边界测试
- [x] T3 独立公开搜索并行执行（companion/study/humanizer/career 四路径）→ verify: 并行墙钟断言 + 顺序确定性断言
- [x] T4 前端阶段展示与首事件时效（创建即"排队中"，阶段行真实文案）→ verify: e2e 2 条断言阶段文案与终态接管
- [x] T5 超预算有界降级终态（草稿带警告交付/失败 budget_exceeded 可重试；注入 30s 慢搜索 8s 墙钟降级不等待）→ verify: 反馈环测试
- [x] T6 本地性能摘要 p50/p95/超时率/阶段占比/重试次数（防回归：本地适配器 p95 首 token ≤2s、终态 ≤5s；无遥测外传、日志无用户内容）→ verify: 摘要单测 + 脱敏扫描
- [x] T7 可控时钟反馈环测试（快速/慢/超时/预算耗尽四态）→ verify: 反馈环测试 7 条全绿
- [x] T8 全量回归 + 双轴代码审查 + 提交 → verify: 全量 2212 pytest（2 基线失败为 05/10 未完成工作）+ issue06 e2e 2 条通过（审查修复：exit 幂等/首 token 精确/技能路径完整阶段/摘要 count 语义/on_stage 死代码清理/ADR-0025；提交 89550be）
