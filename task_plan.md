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

2. **统一领取型任务契约** → Issue 43（解决方案已写，待实施）
3. **对象授权收拢** → Issue 44（解决方案已写，待实施）
4. **服务不再直读不属于自己的表** → Issue 45（解决方案已写，待实施）
5. **前端数据获取 module** → Issue 46（解决方案已写，待实施）
6. **单一流事件 adapter** → Issue 47（解决方案已写，待实施）
7. **密钥环 seam** → Issue 48（解决方案已写，待实施）
8. **跨缝展示知识收拢 + 小摩擦** → Issue 49（解决方案已写，待实施）

## 里程碑

- [x] 候选 1 完成：tests/chat 全绿 + 全量回归通过 + ruff 干净
- [x] 候选 2 完成（Issue 43）：runtime/queue.py 深模块 + 5 子系统迁移 +
      workflows 持久化 + 存量回填，全量 2180 pytest 通过、ruff 干净
- [ ] 候选 3 完成（Issue 44）
- [ ] 候选 4 完成（Issue 45）
- [ ] 候选 5 完成（Issue 46）
- [ ] 候选 6 完成（Issue 47）
- [ ] 候选 7 完成（Issue 48）
- [ ] 候选 8 + 小摩擦完成（Issue 49）
- [ ] 提交 + 验收证据汇总
