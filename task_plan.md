# 任务规划：Issue 41 — 退役旧实现并通过发布门

状态：已完成并提交（3e2789e，2026-08-06）

## 交付内容

### A. 前端退役
- 删除旧项目工作台 `(app)/projects/[projectId]/`（7 页 + layout）、`account/eval` 空壳、
  components/project/**、ProjectLayout/InspectorPanel/SidebarNav；AppShell 仅保留
  account mode；account 首页改拉真实学习项目、移除「全局科学伙伴」旧术语与
  「评测与运行中心」空壳入口；清理前端旧 /api/projects 客户端函数
- e2e：删除 t004-projects；t002/issue14 改新路径；issue09 慢响应 mock 迁移到
  /api/learning-projects；check_frontend_completeness 扫描范围扩展
- /templates 设计基线模板保留（开发专用、生产 middleware 不可达、issue04 E2E 锁定）

### B. 后端退役
- 移除 qwen_force_stub 配置与 StubQwenAdapter 生产注册（仅 environment==test 门控，
  与 /_test/* 同一模式）；/_test/recovery-token、/_test/runs/{id}/advance test 门控；
  媒体表格硬编码示例退役；删除 src/science_companion 兼容层 + pyproject 旧入口 +
  对应测试；doctor 提示改真实停用语义
- 修复：空密钥误注册真实适配器（_has_real_qwen_key）、Stub 驱动媒体提取/旁白合成
  （无真实密钥时接确定性路径）

### C. 发布门
- 修复 science-medical-boundary 评测夹具（答案与断言标记不一致）→ 9 项阈值检查全过、
  blockers 为空；评测 74 测试通过；发布候选报告
  `.scratch/bridges-improvement/41-release-candidate-report.md`

### D. 部署验收
- Conda 实测 BridGes start 生产 profile：迁移 26 → 四进程 → health ready → 单实例锁
  拒绝重复启动 → 强杀后锁自动释放二次启动成功、无孤儿；全新 .venv 安装 + doctor/
  migrate 关键合同通过；Docker/Podman 由契约测试覆盖（本机无二进制，如实记录）

### E. 文档
- README（零 .env、能力停用、账户秘密、备份恢复、发布门、品牌迁移完成）、
  infra/manual；旧 Wayfinder 与 tickets.md 保持只读原样

## 测试与验收

- 后端全量 pytest 2148 passed / 6 skipped / 0 failed
- E2E 两次全量 271 passed，4 例为本机既有环境问题（issue04 附件模板、issue08 视觉
  快照漂移，干净树同失败）与负载型 flaky（issue35/issue38，单跑通过）；本提交改动
  spec 4 轮全过
- 静态扫描：前端完整性通过；后端无 Stub/force_stub/旧包名/离线桩实际残留
- /code-review 双轴审查并修复 7 项（issue09 mock 悬空、FORCE_STUB 残留、test_registry
  环境混用、注释/README 措辞、gates 表述、t002 标题、account-page 注释）
- 已知边界（预存在，如实披露）：模型适配器由全局 BRIDGES_QWEN_API_KEY 注册，账户级
  密钥驱动探测与 Embedding；README 与 doctor 已如实表述

## 里程碑

- [x] 前端旧面删除 + e2e 迁移
- [x] 后端 Stub/测试端点/兼容层退役
- [x] 发布门通过（含评测夹具修复）
- [x] 四部署验收 + 文档更新
- [x] 全量测试 + 静态扫描
- [x] /code-review 双轴审查并修复
- [x] Issue 41 AC 全部勾选 + 提交（3e2789e）
