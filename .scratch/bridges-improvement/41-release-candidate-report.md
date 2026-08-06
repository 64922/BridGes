# Issue 41 发布候选报告

- 日期：2026-08-06
- 范围：退役旧实现（前端旧工作台/空壳/Stub/旧名兼容层）与四部署发布验收

## 一、退役清单（已删除或不可达）

| 项 | 处置 |
|---|---|
| 旧项目工作台路由 `(app)/projects/[projectId]/`（7 页 + layout） | 删除；入口改指学习项目详情 |
| 空壳页 `(app)/account/eval/` | 删除（无真实数据与交互） |
| 旧组件：components/project/**、ProjectLayout、InspectorPanel、SidebarNav | 删除（仅旧路由引用） |
| AppShell project mode（顶栏 + 旧侧栏） | 移除，仅保留聊天优先 account mode |
| account-page「打开项目」旧链接 /「全局科学伙伴」旧术语 /「评测与运行中心」空壳入口 | 改为学习项目详情 /「账户首页」/ 移除 |
| 前端旧 `/api/projects` 客户端函数与类型 | 移除（后端路由保留：workflows API 依赖项目作用域，属工程骨架） |
| 生产 Stub：`qwen_force_stub` 配置与开关 | 移除；StubQwenAdapter 仅显式 test 环境注册 |
| 测试专用端点 `/_test/recovery-token`、`/_test/runs/{id}/advance` | 仅 test 环境注册（与 /_test/capabilities 同一门控） |
| 媒体表格提取硬编码示例表（物理/化学实验数据） | 移除；CSV 缺失或解析失败返回空，绝不假成功 |
| 旧包名兼容层：`src/science_companion/`、`science-companion` CLI 入口、wheel 打包 | 移除；`doctor` 提示改为真实停用语义 |
| 设计基线模板 `/templates/` | 保留（开发环境专用，生产由 middleware 重定向不可达；issue04 设计基线 E2E 锁定） |

## 二、发布门（AC10）

`BridGes evaluate run` + `gates`：**9 项检查全部通过，blockers 为空**。

| 检查 | 结果 |
|---|---|
| fact_accuracy | 5.00 ≥ 4.00 |
| hallucination | 5.00 ≥ 4.00 |
| calibration | 4.50 ≥ 3.50 |
| risk_identification | 5.00 ≥ 4.00 |
| boundary_response | 5.00 ≥ 4.00 |
| risk_boundary | 5.00 ≥ 3.50 |
| learning_outcome | 5.00 ≥ 3.00 |
| 失败率 | 0.00% ≤ 5% |
| 高风险失败 | 0 ≤ 0 |

修复：高风险医学边界案例脚本答案与断言标记不一致（“个体差异大” vs “因人而异”）
导致事实门假失败——已修正为与知识库原文一致并重跑，双跑可复现（576 条案例结果）。

## 三、测试证据

- 后端全量 pytest：**2150 passed / 4 skipped / 0 failed**（含安全、账户隔离、备份恢复、评测 74 条）
- 桌面 Playwright E2E：全量两次运行均为 **271 passed / 4 failed**（共 275 例）。
  本提交改动的 spec（t002 / t003 / issue14）4 轮全过；恒定 4 例失败为
  本机既有环境问题，与本次改动无关（在干净树同样失败或为负载型 flaky）：
  - issue04:143 附件模板测试（待发送附件列表不出现，干净树同失败）
  - issue08:283 视觉快照漂移（0.03 像素比，干净树同失败）
  - 负载型 flaky：issue35 MCP 调用时序（单跑 7/7 通过）、issue38-a11y
    键盘时序（单跑通过）
- 部署契约：tests/integration/test_production_runtime_contract / config / cli + tests/runtime 32 passed
- 静态扫描：`scripts/check_frontend_completeness.py` 通过（正式路由无品牌泄漏、占位文案、空链接、假按钮）；后端扫描无 Stub/force_stub/旧包名/离线桩实际残留（仅退役说明注释）
- 既有 flaky 修复：`test_concurrent_two_account_operations_do_not_cross_contaminate`
  按 tag 识别账户（原依赖线程完成顺序，50% 假失败）；`BridGes doctor` 子进程
  GBK 控制台编码（PYTHONIOENCODING=utf-8）
- /code-review 双轴审查并修复 7 项：issue09 慢响应 mock 迁移到
  /api/learning-projects（原 mock 永不触发的真空通过）；test_disclosure
  残留 FORCE_STUB 清理；test_registry 环境设置改 monkeypatch 单一方式；
  main.py 注释与 README 措辞修正为「全局环境密钥驱动模型调用、账户级密钥
  驱动探测与 Embedding」（预存在架构事实，非本 Issue 引入，如实披露）；
  README gates 表述修正（生产 Stub 由注册表测试与静态扫描阻止）；t002
  describe 标题去「项目主壳」；account-page 注释准确化

## 已知边界（如实披露，非本 Issue 引入）

- 模型适配器由全局环境密钥 `BRIDGES_QWEN_API_KEY` 注册；账户级百炼密钥
  驱动能力探测与知识库 Embedding 检索。仅配置账户级密钥时，设置页探测
  显示 AVAILABLE，但聊天/语音等真实模型调用保持「未绑定适配器」阻塞。
  该架构事实预存在于 Issue 10/11 交付，未在发布收口阶段改动（Non-goals：
  不新增未规划功能）；已在 README 与 doctor 提示中如实表述。

## 四、部署验收（AC5–8）

- Conda agent 环境：`BridGes start --profile production` 实测——迁移 schema 26 →
  Web/API/worker/scheduler 四进程 → `/health/ready` pass → 访问地址输出；
  重复启动被单实例锁拒绝（中文提示）；强杀后锁自动释放、二次启动成功；
  无残留孤儿进程。
- 全新 `.venv`：`pip install -e ".[dev]"` + `BridGes doctor`/`migrate` 关键合同
  通过，证明最终用户不依赖 Conda 专有行为。
- Docker/Podman：本机无 docker/podman 二进制，容器语义由
  test_production_runtime_contract（compose 同源构建/健康检查/停止语义）与
  同一 `BridGes start` 代码路径覆盖；四种部署均不要求 `.env`，容器入口首次
  启动自动生成并持久化主密钥。

## 五、文档（AC11–12）

- README：零 .env、Conda/.venv/Docker/Podman、`BridGes start` 编排、账户秘密
  只进受保护设置、能力不可用真实状态、数据备份与恢复、发布评测与发布门、
  品牌迁移完成说明、常见问题更新。
- infra/manual/README.md：旧前缀/旧入口退役说明、能力停用合同。
- 旧 `.scratch/science-companion-plan/` 与根目录 `tickets.md`：git diff 无任何
  改动，保持只读历史。
