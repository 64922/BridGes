# 42 — 验证工作流质量、预算和外部真实可得性

**What to build:** 同任务配对比较旧 V2 与新编排，记录质量/成本/延迟；外部未通过的读取层次保持如实降级。

**Blocked by:** 36 — 按本次表现总结并单独恢复总结失败；37 — 执行跨模块依赖计划并统一核验综合结果；38 — 在正式界面展示真实进度、可信结果与恢复操作

**Status:** ready-for-agent

**优先级：** P1

## 背景与需求

新增专业/核验调用需换来实际收益，静态审查和模拟工具不能证明全文、校内路线、贴吧回复、岗位与视频真实可用。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/workflow/README.md](../../../docs/workflow/README.md)。
- [docs/workflow/review.md](../../../docs/workflow/review.md)。
- [docs/workflow/orchestration.md](../../../docs/workflow/orchestration.md)。
- [docs/workflow/daily-workflows.md](../../../docs/workflow/daily-workflows.md)。
- [docs/workflow/study-workflow.md](../../../docs/workflow/study-workflow.md)。
- [docs/workflow/delivery-and-validation.md](../../../docs/workflow/delivery-and-validation.md)。
- [docs/v2/feasibility.md](../../../docs/v2/feasibility.md)。

## 任务内容

1. 执行 delivery-and-validation 的全部 A01–A18、L01–L13、R01–R08，先固定工具/模型响应验证不变量，再真实模型验证语义匹配/教学/核验。
2. 故障注入收据与检查点、判定/反馈/总结与最终提交边界，重复请求/旧租约/停止/限流/恢复/不兼容配方/撤权重点检查。
3. 真实语义案例含同义/跨语种术语、关键词假阳性、稀疏来源、错误书页和争议等价答案；关键结论支持与读取诚实、需求必要覆盖、教学范围/评分/总结分别量表。
4. 配对锁定任务数据，记录代码/配方/模型/提示/Schema/来源/时钟与重复次数；轻量对话不增加规划/裁判，新增专业核验成本需可观测收益。
5. 测正确路由/任务归属/错误启动/澄清、证据支持、推荐/教学质量、重复效果/错误推进/晚覆盖、调用/token/读取/重试/补证；按任务分组首真实进度/首可用/完整结果 P50/P95。
6. 最小真实探针验证学术全文、高德校内各允许方式与地图覆盖、目标贴吧实际回复、公开岗位详情、视频介绍/内容、GitHub 文件/许可、用户配置模型结构化/图片/上下文能力。只用已获授权配置/必要查询，密钥不进入产物。
7. 仅开放实测通过的能力层次，未通过不宣称深读/代码已运行/全部回复已总结；超现有允许降级的范围收缩提出具体实测证据供用户审查。
8. 校准 09 的时间/候选/读取/并发/调用初值和语义/延迟阈值，保留单轮补证与控制硬门。

## 跨票接缝与责任

拥有跨工作流配对与外部上线证据；各票已做自己确定性验证，本票不替他们补生命周期/质量门。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [ ] 39 个 A/L/R 场景均有行为断言/执行证据或真实外部门限制，不能仅出现节点名。
- [ ] 账户越权、硬条件绕过、系统失败跳题、重复判定、停止非法写入零容忍。
- [ ] 真实模型与外部探针分开报告，可得性层次与界面可用状态一致。
- [ ] 质量与成本/延迟配对报告可重现，轻量路径无不必要额外模型。
- [ ] 预算初值与阈值通过基线校准，无未测性能承诺；真实不足按允许合同降级。

## 验证与交付证据

完整确定性故障集、真实模型重复配对、获授权最小外部探针和正式桌面测试；不能用 stub 成功作为外部上线门。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## 实施记录（2026-10-06）

分支 `codex/42-workflow-evaluation-and-external-capability-gates`（基于 main `1c6f9edd`）。本票以评测代码与产物为主；为满足独立需求审查提出的行为修复与场景/探针证据，同步修正了五处运行时接缝（见「接口/迁移变化」），无持久状态迁移。

### 交付内容

- `src/bridges/evaluation/workflow_scenarios.py`：39 个 A/L/R 场景清单（真实节点、外部门、故障注入、零容忍、真实模型配对覆盖），`validate_workflow_scenarios` 静态校验；引用的 132 个节点全部存在于真实图中。
- `src/bridges/evaluation/workflow_evidence.py` + `scripts/run_issue42_workflow_evidence.py`：执行场景断言并把 JUnit 归并成逐场景证据（参数化用例按基础名归并）。
- `src/bridges/evaluation/external_probes.py` + `scripts/run_issue42_external_probes.py`：8 个真实探针（arxiv 全文、高德三方式、tavily、贴吧、岗位、视频、GitHub、生效模型四能力），外部门宣称/降级合同与一致性检查；密钥不进报告。
- `scripts/run_issue42_workflow_pairing.py`：旧 V2（`7818c34b`）与新编排同任务数据、同模型配置配对；父进程对每棵树自重启子进程，隔离 `sys.path` 与临时库，记录质量检查点、调用/token 与整答/首字 P50/P95。
- 测试：`tests/evaluation/test_issue42_scenario_gaps.py`、`test_issue42_scenario_manifest.py`、`test_issue42_external_probes.py`、`test_issue42_workflow_evidence.py`、`test_issue42_workflow_pairing.py`、`test_issue42_budget_calibration.py`。
- 产物（验收归档）：`.scratch/2/validation/42-acceptance/`（pairing/final.json+md、semantics-final、paper-role-pairing、external-final、deterministic）；历史过程产物在 `.scratch/2/validation/42-workflow-evaluation/`。

### 验证结果

- 场景证据：159 passed；39/39 场景通过、0 问题；外部门探针另行报告（不混入确定性通过）。
- 零容忍：cross_account / hard_condition_bypass / system_failure_as_student_error / duplicate_judgement / write_after_stop 全部有守卫并通过。
- 配对（repeats=2，corpus sha256 `4cffaaf0…`）：质量检查点旧 24/34 vs 新 36/36；模型调用旧 14 vs 新 6；整答 P50 旧 19.8s vs 新 4.0s；A02 旧树仍出论文结果、新树正确；A11 新树按硬条件阻断；R07 新树无自动续跑、继续创建新运行。
- 预算校准（同一次配对，产物 `pairing/final.json`、`final.md`）：lightweight 12 样本、deep 4 样本、normal 2 样本，均为真实模型运行。lightweight 完整 P95 11.6s ≤120s；deep 完整 P95 36.9s ≤120s（工作截止 90s 内），候选初筛 10/20、模型调用 1/8；normal 完整 P95 4.1s ≤60s，外部调用累计 3、并发峰值 2/2。并发峰值由账本流水重放；deep 深读 0/5（产品按合同保持摘要层，全文未接线，不作深读承诺）。`test_issue42_budget_calibration.py` 锁定 `run-budget-v1` 初值，实测均在初值内，未上调。
- C01（deep 论文）与 C02（normal 跨模块）为真实预算校准样本；C02 见证并发位被姊妹分支占满时被拒分支按合同如实降级。
- 外部探针（canonical `external-final/`，2026-10-06T08:37Z）：arxiv 全文技术实测通过（产品按合同保持摘要层，declared degraded）；高德校内三方式 3/3 通过（declared partial）；tavily 通过；video 元数据通过；model 四能力通过；tieba 读取受限（failed/unavailable，如实降级）；jobs 未能定位公开详情（inconclusive，列入 `inconclusive_gates`）；github 三项失败（共享出口配额，不代表产品能力）。`consistency_problems` 为空。

### 发现与限制（跨票，本票只留证据）

- F1（已修复，待提交）：新树理解/继续调用曾出现 `output_budget_exceeded`（R07 停止后继续 2/2、A03 间歇；旧树无）。根因：`src/bridges/ai/qwen_adapters.py` 输出额度被思考耗尽。修复：对已核实混合型号的小额/结构化请求显式 `enable_thinking=false`，`finish_reason=length` 时抛显式 `output_budget_exceeded`。最终配对 A03 澄清 2/2、R07 三项 2/2。
- F2（已修复）：问候含「最近」曾触发不必要 web 搜索计划并以 `web_search_citation_invalid` 结束；`web_search/service.py` 只剔除助手近况问候。最终配对 `no_unnecessary_search` 新树 2/2 通过、旧树 0/2，检查点已固化为回归。
- F3：A03 澄清行为随模型波动；修复后新树最终配对 2/2 追问、旧树 0/2，配对报告如实记录，不宣称稳定。
- F4（已修复）：高德 bicycling/electrobike 把时长放 `path.duration` 曾被判 `amap_route_unusable`；`commute/sources.py` 骑行类接受该位置，高德校内探针 3/3 通过，新增 `tests/commute/test_issue42_route_duration.py`。
- F5：复合运行中姊妹分支占满 `external_parallel_max=2` 时，论文分支首次检索即被并发门拒绝并给出「运行预算已用尽」误导文案，整轮按合同降级为阻塞交付（C02 两次重复均复现：normal 类、外部调用 3、并发峰值 2/2、模型调用 0、约 4s；旧 V2 同样提示可交付 3 篇论文）。根因线索：`paper/kernel.py` 把 `register_external` 拒绝一概归因为 `run_budget_exhausted`，且首查不等待/重试；复合调度各分支共享同一并发位。需后续工单在调度或论文首查上处理并区分拒绝原因。
- 既有失败（非本票）：`tests/evaluation/test_runner_reproducibility.py` 4 项在 main `1c6f9edd` 同样失败（旧评测底座 `executors.py` 仍向 `ChatService` 传 `image_service`），本票未处理。`tests/chat` 全量 49 项在 main 同样失败（career/MCP/selections/附件等旧模块），逐项对账后仅 `test_improvement14_material_reads.py::test_old_photo_question_rereads_original_and_manifest_scope` 属运行顺序波动，单跑两树均通过。
- 真实模型配对每任务 2 次，只证明机制与方向，不宣称统计准确率；tieba/GitHub 外部不可控因素已如实记录。

### 接口/迁移变化

- 新增 `bridges.evaluation` 只读评测模块与 `scripts/` 脚本入口；新增 `bridges.chat.topic_clarification` 资料领域澄清服务；无数据库/合同迁移。
- 运行时行为修复（独立审查、场景或探针证据驱动的最小接缝）：`ai/qwen_adapters.py` 混合型号小额/结构化请求关闭思考并在 `length` 时显式报错（F1）；`chat/understanding.py` 明确「找资料」入口与资料领域消歧（A03）；`web_search/service.py` 只把助手近况问候排除出时效检索（F2/A01）；`commute/sources.py` 骑行类接受 `path.duration`（F4/A02）；`career_plan/collecting.py` 识别猎聘列表路由与 `ItemList`，避免列表页冒充岗位详情（岗位探针）。

### 复现

- 环境：conda `agent`；联网走系统代理 `http://127.0.0.1:7890`；凭据只从 OS 凭据库读取。
- 命令：`python scripts/run_issue42_workflow_evidence.py`；`python scripts/run_issue42_external_probes.py --proxy http://127.0.0.1:7890`；`python scripts/run_issue42_workflow_pairing.py --repeats 2`（自动创建旧树 worktree）。

