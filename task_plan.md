# 任务规划：Issue 41 — 退役旧实现并通过发布门

状态：进行中（2026-08-06 启动）

## 交付内容

### A. 前端退役（删除旧 Science Companion 普通用户工作台）
- 删除 `apps/web/src/app/(app)/projects/[projectId]/` 旧项目工作台（7 页 + layout）
- 删除 `apps/web/src/app/(app)/account/eval/` 空壳页
- 删除旧组件：components/project/**（TaskStage/StudyChatEntry/ProjectHeader/WorkbenchNav）、
  components/layout/ProjectLayout、InspectorPanel、SidebarNav（仅 project mode 使用）
- 简化 AppShell：移除 project mode 分支与 mode/projectId props
- account-page：旧「打开项目」链接改指 /account/projects/{id}；「全局科学伙伴」旧术语改
  「账户首页」；移除「评测与运行中心」空壳入口
- e2e：删除 t004-projects.spec.ts；t002-shell 移除旧工作台用例；issue14 学习对话用例改新
  学习项目页；删除 helpers/projects.ts
- check_frontend_completeness.py 扫描范围扩展（旧工作台/eval 退役后纳入扫描）

### B. 后端退役 Stub 与测试端点
- 移除 `qwen_force_stub` 配置与全部使用点（api/main.py 4 处、config.py），
  生产配置不再可能注册 StubQwenAdapter
- `/_test/recovery-token`、`/_test/runs/{id}/advance` 仅 test 环境注册（对齐
  `/_test/capabilities` 既有模式）
- media/extraction.py TableExtractor：CSV 解析失败不再回退硬编码示例表，改为真实失败
- 删除 `src/science_companion/` 兼容层包 + pyproject `science-companion` 入口与 wheel 打包
  + 对应兼容层测试；更新 infra/manual/README 与 README

### C. 四部署路径验收
- Conda / .venv：锁定依赖安装 + `BridGes start` 编排 Web/API/worker/scheduler +
  健康检查 + 停止 + 二次启动无重复任务
- Docker Compose / Podman：同源构建、迁移、数据目录、健康检查、停止语义

### D. 文档
- README 与运行/安装/备份/安全/能力矩阵/故障排查文档更新；
  旧 .scratch/science-companion-plan 与 tickets.md 保持只读原样

### E. 验收
- 全量 pytest + 桌面 Playwright E2E + 契约 + 安全 + 账户隔离 + 备份恢复 + 评测套件
- 静态扫描（check_frontend_completeness + 后端 Stub 扫描）零违规
- 发布门（BridGes evaluate gates）达到锁定阈值
- /code-review 双轴审查并修复
- 更新 Issue 41 AC 勾选状态并提交

## 验收对照

| AC | 落点 | 验证 |
|----|------|------|
| 1 正式导航只留聊天优先面 | 前端删除 + account-page 改链 | e2e 回归 + 静态扫描 |
| 2 无旧品牌/占位/死链 | 删除+术语修正 | check_frontend_completeness |
| 3 生产不注册 Stub | qwen_force_stub 移除 + 表格示例退役 | test_registry + 扫描 |
| 4 真实探测/不可用合同 | 既有 probes/matrix 保留 | 探测测试 |
| 5 Conda/.venv | start 编排验证 | 集成测试 + 手动验收 |
| 6 Docker/Podman | compose 同源 | 集成契约测试 + 手动验收 |
| 7 零 .env | config 无 .env 读取 | config 契约测试 |
| 8 start 编排/重复启动/无僵尸 | DataDirectoryLock + 编排 | runtime 集成测试 |
| 9 三分辨率黄金路径 | E2E 套件 | Playwright |
| 10 安全矩阵+评测达阈值 | gates.py | evaluate gates + 安全测试 |
| 11 README 准确 | 文档更新 | 人工核对 |
| 12 旧 Wayfinder 只读 | git 提交不含旧目录改动 | git diff 检查 |
