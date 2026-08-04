# Task Plan — 架构审查候选逐项修复（M01–M05 审查后深化）

状态：进行中（2026-08-04）

## Issue 26 实施计划（画像候选与分级许可更新）

状态：实施中（2026-08-05）。

### 目标
把聊天观察转化为可治理的画像候选与更新流程：明确"记住/不要记住/只在
本对话使用"意图按可见类别/范围/证据确定性映射；低风险目标/兴趣/表达
习惯仅在用户预开的类别×场景许可内自动写入（默认关闭、模型不能代开），
每次写入有中文通知、来源与一键撤回；情绪趋势/重要经历/当前问题与敏感
推断只进候选箱，未确认不得跨会话使用；单次情绪仅作为会话情境信号；
冻结类别拒绝自动写入、撤回许可只停未来更新；稳定去重与证据合并；
全部按账户隔离并写入审计；画像/许可/通知 SQLite 持久化，重启可追溯。

### 新增模块
1. `profiles/extraction.py` — 确定性记忆意图提取器（记住/不记/仅会话/
   低风险观察/单次情绪；类别关键词表；显式意图优先、否定式防护、
   无类别不猜测）
2. `profiles/sqlite_repository.py` — 全端口 SQLite 实现（观察/候选/断言/
   版本/切片/许可/通知，scoped() 账户强制隔离）
3. `contracts/profiles.py` — ProfilePermission / ProfileNotification /
   AUTO_WRITABLE_DIMENSIONS / 批量决策契约
4. `contracts/chat.py` — ChatStreamEventKind.PROFILE + ChatStreamProfileData
5. `contracts/observability.py` — 6 个新审计动作（许可开关/自动写入/
   一键撤回/意图/候选提出）
6. `storage/database.py` — SCHEMA_VERSION 14：7 张 profile_* 表

### 修改
7. `profiles/service.py` — 许可门/冻结门/去重门、process_conversation_message
   管线、一键撤回（幂等）、批量决策（幂等）、通知读写
8. `chat/service.py` — start_generation 挂载画像处理（失败静默不阻断）、
   profile_notifications_for_message 透传
9. `api/chat.py` — started 后下发 profile SSE 事件
10. `api/main.py` — 有数据库时挂 SQLite 画像仓库；ChatService 接入
11. `profiles/api.py` — 许可 GET/PUT、通知 GET/read/recall/unread-count、
   候选 batch-decision

### 前端
12. api.ts 新函数 + 类型导出；ProfilePermissionPanel（开关网格，乐观更新
    失败回滚）；ProfileNotificationList（未读/已读/一键撤回/标记已读）；
    ProfileCenter 候选卡增强（为何提出/来源消息/适用范围/编辑后确认/
    复选框批量确认拒绝）；ChatProfileNotificationCards（聊天内即时通知
    + 一键撤回 + 错误恢复）；chat.module.css / ProfileCenter.module.css
13. openapi.json + generated.ts 再生成；npm typecheck/build

### 测试
14. `tests/profiles/test_memory_intent.py` — 35 条：标注对话集（明确记忆/
    低风险许可/敏感候选/单次情绪/禁止推断授权）、幂等去重、冻结门、
    许可撤回、一键撤回、批量决策、跨账户、SQLite 重启持久化
15. `tests/chat/test_profile_intent_chat.py` — 5 条：聊天集成、失败不阻断
16. E2E issue26 — 5 条：许可开关持久化、候选卡增强与编辑后确认、
    批量拒绝、通知空态+聊天内通知+一键撤回错误恢复、单次情绪提示

### 收尾
17. 全量 pytest / mypy / ruff / npm typecheck+build / E2E 全量；
    code-review 双轴审查并修复；更新 Issue 26 验收状态；提交


## Issue 20 实施计划（分层本地检索、融合排序与引用）

状态：已完成（2026-08-04）。全量验证：1335 pytest（+81）、E2E 143 通过
（新增 issue20 5 条，4 个失败均为干净树既有/环境 flake）、mypy 173 文件
0 错误、改动区域 ruff 干净、npm typecheck/build 通过。双轴 code-review
修复 11 处缺陷后提交（77a24ce）。

### 目标
在真实对话中交付三层本地检索：当前对话附件 → 当前学习项目文件 → 已授权
全局知识库。每层 FTS5 BM25 与 text-embedding-v4 向量结果按固定合同融合
（RRF k=60 + 层权重 3/2/1），独立候选配额（4/5/5）与去重规则，引用固化
为消息轮次（文件名/页码/章节/片段快照，索引重建不漂移），点击引用实时
校验授权并打开原文；无命中/冲突/覆盖不足/索引不可用输出结构化充足性。

### 新增模块
1. `contracts/retrieval.py` — CitationProjection / RetrievalRoundProjection /
   RetrievalLayerResult / RetrievalSufficiency / CitationDetailProjection
2. `retrieval/search.py` — 查询清理、FTS 窗口回退、向量余弦、RRF 层内融合、
   跨层加权合并与内容哈希去重、冲突与充足性判定
3. `retrieval/repository.py` — 检索轮次与引用持久化（账户作用域）
4. `retrieval/service.py` — 作用域解析、每轮检索编排、投影与引用详情授权校验
5. `tests/retrieval/` — 搜索单测 11 条 + 服务测试 17 条（作用域/配额/去重/
   隔离/充足性/引用详情/版本稳定）
6. `tests/chat/test_retrieval_chat.py` — 生成前检索、上下文注入、重试新轮次、
   知识库开关、刷新稳定（5 条）

### 修改
7. `storage/database.py` — SCHEMA_VERSION 10：retrieval_rounds + message_citations
8. `contracts/chat.py` — ChatMessageProjection.retrieval、请求 use_knowledge_base
9. `chat/service.py` — 生成前 run_round、最小上下文注入、思考摘要证据/工具、
   消息投影携带检索轮次
10. `api/chat.py` — send/retry 透传知识库开关、引用详情路由
11. `api/main.py` — 挂载 LayeredRetrievalService（真实 QwenEmbeddingPort）
12. 前端 — api.ts（useKnowledgeBase + getCitationDetail）、RetrievalCard.tsx
    （状态卡/引用展开/打开原文）、MessageList/ChatThread 接入、Composer
    来源层面板与知识库开关（模板基线不渲染）、openapi.json + generated.ts 再生成

### 收尾
13. 全量 pytest / ruff / mypy / npm typecheck+build / E2E 已验证
14. code-review 双轴审查并修复；更新 Issue 20 验收状态；提交


## Issue 17 实施计划（文档摄取与版本化全文/向量索引）

状态：已完成（2026-08-04）。全量验证：1254 pytest（+47，含 3 条审查回归）、117 E2E（+4）、mypy 160 文件 0 错误、改动区域 ruff 干净、npm typecheck/build 通过。双轴 code-review 修复 8 处缺陷后提交。

### 目标
把安全对象转换为可追溯、可恢复的本地检索材料：PDF/DOCX/TXT/MD/图片 → 解析（页码/章节/标题）→ 哈希分块 → SQLite FTS5(trigram) 全文 + text-embedding-v4 1024 维向量双索引；索引带不可混写版本合同，合同变化全量重建、校验后原子切换，旧版可回滚；后台执行器重启恢复未完成任务。

### 新增模块
1. `src/bridges/contracts/ingestion.py` — DocumentIngestionProjection / IndexStatusProjection / IndexContractProjection 契约
2. `src/bridges/ingestion/parsers.py` — PDF(fitz)/DOCX(zip+xml)/TXT/MD/图片解析器，产出归一文本 + (起始/结束/页码/章节) 跨度
3. `src/bridges/ingestion/chunker.py` — 结构锚点哈希分块（字符偏移可追溯）
4. `src/bridges/ingestion/embedding.py` — EmbeddingPort + 真实 Qwen 实现（L2 归一 + 维度校验）+ 确定性假实现；能力探测门
5. `src/bridges/ingestion/index.py` — 版本化索引：合同（model/dims/规范化/chunker/schema）、混合写拒绝、全量重建、覆盖率+维度校验、原子切换、回滚
6. `src/bridges/ingestion/service.py` — 摄取状态机（入队/领取/处理/重试/投影/清理）+ 账户内解析缓存复用
7. `src/bridges/api/ingestion.py` — 附件摄取详情 / 重试 / 索引状态路由

### 修改
8. `storage/database.py` — SCHEMA_VERSION 7：document_records、document_parse_cache、document_chunks、index_versions、index_active、index_vectors、fts_chunks（trigram）+ 存量附件回填入队
9. `contracts/chat.py` — ChatAttachmentProjection 增加 ingestion_status / ingestion_error
10. `chat/attachments.py` — 投影 LEFT JOIN 摄取状态
11. `api/chat.py` — 上传成功后人队
12. `api/main.py` — 挂载 ingestion service
13. `runtime/executor.py` + `cli/main.py` — worker 摄取轮（清理 → 摄取 → 索引维护）

### 前端
14. api.ts + MessageList 附件卡片状态芯片（loading/queued/processing/ready/empty/error/permission/recovery）+ 详情展开 + 重试；先调 ui-ux-pro-max
15. openapi.json + generated.ts 再生成；npm typecheck/build

### 测试
16. `tests/ingestion/` — 解析/页码章节/哈希分块/幂等重试/账户隔离/解析缓存
17. 索引合同测试 — 维度错误、版本漂移、重建失败、原子切换、旧版回滚
18. 编排测试 — 确定性 Embedding 假服务；显式真实冒烟（scripts/smoke）
19. E2E issue17 — 处理进度/失败原因/重试/重启恢复

### 收尾
20. 全量 pytest / ruff / mypy / npm typecheck+build / E2E；code-review 修复；更新 Issue 17 验收状态；提交

## 目标

按 `/improve-codebase-architecture` 审查报告（architecture-review-20260804-022658.html）
中的重要程度，逐项修复 9 个架构候选：SSE 流事件契约 → 账户隔离下沉 → 生成生命周期
收敛 → 模式编排加深 → 再认证横切门 → Qwen 适配收敛 → 观测层塌缩 → 删除模板平行宇宙
→ 前端接口接缝。每个候选完成后跑相关验证（pytest 子集/全量、ruff、mypy、npm typecheck/build）。

## 任务清单（按重要程度）

1. [x] 候选 2：SSE 流事件契约单一来源（Top 推荐）
   - contracts/chat.py 定义流事件 Pydantic 模型；api/chat.py 的 _generation_events 产出模型；
     模型进入 OpenAPI schemas；重新生成 openapi.json + generated.ts；前端 ChatStreamEvent 改为
     生成类型组合，事件名用判别式字段，删除手写镜像与硬编码字符串
   → 验证：test_openapi_sync 通过、聊天 58 测试通过、全量 1194 过（3 个 doctor CLI 环境
     编码 flake 改动前已存在）、npm typecheck/build 通过、E2E issue11/13/14 通过
2. [x] 候选 4：账户隔离下沉为数据库强制
   - storage/database.py 增加 scoped(account_id) 账户作用域查询面（INSERT 必须含 account_id
     列、其余语句 WHERE 必须含 account_id 过滤，违反即拒绝）；chat/repository.py、
     chat/attachments.py、storage/repository.py 账户域方法改用 scoped，系统级清理保持裸连接
   → 验证：新增 4 条作用域强制负例测试、全量 1198 过（4 个 CLI smoke 编码 flake 环境问题）、
     mypy/ruff 干净
3. [x] 候选 1：生成生命周期收敛为单一接口
   - 新建 chat/lifecycle.py GenerationLifecycle：停止信号注册/续期/TTL 陈旧判定/停止信号
     读取收敛为一个深模块（共享锁 + 单一数据源）；ChatService 删除散落的 _stops 注册表与
     4 个私有方法
   → 验证：新增 6 条 lifecycle 单测、聊天 64 过、mypy/ruff 干净
4. [x] 候选 3：模式编排加深
   - ModeContract 从提示文本升级为步骤化合同：OrchestrationStep Protocol + DeclarativeStep；
     _MODE_CONTRACTS 声明编排步骤（文案不变），_initial_thinking 从 steps 派生，教学门/
     检索/测验作为后续带 run 的步骤接入
   → 验证：聊天 64 过（步骤文案断言不变）、mypy/ruff 干净
5. [x] 候选 7：再认证横切门
   - FastAPI dependency RecentAuthRequired 统一门控（5 路由删除内联调用与 request 参数）；
     RecentAuthService Protocol 收窄 Any 类型
   → 验证：凭据/身份 75 过、mypy/ruff 干净（前端 401/reauth 拦截并入候选 8）
6. [x] 候选 6：Qwen 能力适配收敛
   - qwen_client 增加 first_choice/choice_text 共享实现；删除 qwen_adapters/qwen_vision_adapters
     的 _first_choice 副本与 ASR _first_choice_content；streaming.py（65 行浅文件）并入
     adapters.py 并删除，更新 7 处导入
   → 验证：ai/media 249 过、聊天相关 210 过、全量 mypy 0 错误
7. [x] 候选 5：观测层塌缩
   - 门面瘦身为审计事件流接口（删除 9 个 SLI/告警纯委托方法，调用方只经审计接口）；
     health probe 移除一次性 SLI/SLO 冒烟改为无副作用空查询；loop.py 保留（删除测试不通过：
     共享循环语义删除会移动到两处并漂移）
   → 验证：观测 34 过、相关域 263 过、mypy/ruff 干净
8. [x] 候选 9：删除模板平行宇宙 —— 经证据否决：issue04 E2E（评分资产）56 处引用
   /templates 路由，删除需重写 527 行验收测试，收益（构建体积/导航噪声）不抵风险；
   保留并在 templates/ 加 README 标注其 Issue 04 设计基线身份与生产重定向语义
9. [x] 候选 8：前端接口接缝
   - 统一错误解析：parseAuthError/parseDomainPackError/attachmentApiError 三套 → parseApiError
     + errorFromDetail 单一实现（62 处调用统一）；classifyApiError 统一 401/reauth 分类，
     KeySettings 6 处 + AccountSwitcher 6 处 + PersonalProfileSettings 1 处自写判断收敛
   - XHR 上传保留（进度跟踪的正当理由，错误解析已统一）；openapi-fetch 路由类型化不做
     （重写 1129 行 api.ts 风险收益比不佳，契约类型已由候选 2 消费）
   → 验证：npm typecheck/build 通过、E2E 13 过（issue08 视觉快照 83 像素差异为环境 flake，
     stash 后同样失败）
10. [x] 全量回归：pytest 全套 1202 过（2 skipped，6 个 CLI smoke 编码 flake 为环境既有）、
    全量 mypy 152 文件 0 错误、改动区域 ruff 干净（剩余 2 项为未触碰文件的既有问题）、
    npm typecheck/build 通过、E2E 关键 spec 通过（issue08 视觉快照 83 像素差异为环境 flake）

## 验收命令

```powershell
conda run -n agent python -m pytest -k "<候选相关>"
conda run -n agent python -m pytest -x -q          # 全量
conda run -n agent python -m ruff check .
conda run -n agent python -m mypy src
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
npm --prefix apps/web run test:e2e -- --project=chromium
```
