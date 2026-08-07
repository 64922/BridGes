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
