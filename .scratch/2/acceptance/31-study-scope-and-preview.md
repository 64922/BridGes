# 工单 31 独立验收

日期：2026-10-03。原交付 `2451610088a82c4f2f4e1ac0eb3d4631aee31a78`，固定基线 `main@68357a208f3e22b6fdea0445377ea383a80a9fcb`。

结论：原交付有缺陷，修复后通过本票确定性验收，工单转 ready-for-human。依据为实际工单、AGENTS.md、CONTEXT.md、批次 README、学习工作流、公共编排、交付验证与人味化实施方案；编码代理报告只用于定位。前置 21/30 的实际接线和独立验收记录均在本基线，识别与表达接缝已运行复验。未把合成网关响应当成真实模型教学质量证据。

## Standards

code-review 规范轴由独立只读代理审查 `git diff 68357a20...24516100` 并复审修复，初审 3 项，复审无剩余阻断项。

1. 预习复用忽略表达条件，违反公共编排的输入条件复核要求。现输入摘要含 policy_block；表达变化只重做预习，映射核验继续复用。
2. 新节点 input_deps 为空，违反公共编排的来源依赖审计合同。现核验记录映射 artifact_id/content_hash；映射和预习记录材料或范围摘要依赖。
3. 新错误未登记固定文案类别和恢复方式，违反 CONTEXT 的文案清单合同。现登记 10 个实际错误码与动态核验说明路径，保留 contextual 详情。兼容增加目录项，整体文案版本不变。

未发现需额外重构的 smell 阻断项；没有为文件长度或相似产物封装引入无关重构。

## Spec

需求轴由另一独立代理核对工单和代码，原始与复审共发现 6 项，全部修复，无剩余阻断项。

1. 排除理由只检查长度，被排除的公式从内容门消失。现同一次独立核验接收被排除片段原文和理由；冲突或遗漏核验阻塞预习，exclusion_checks 随范围持久化。
2. 旧复盘按标题取首个 ID，串用同名知识点依据。现按题目 fragment_ids 匹配；无片段消歧的旧预习保留原标题，未知旧引用保留，不伪造身份。
3. scope_history 截断为 8 版，导致旧预习版本丢失。现完整保留历史；超过 8 版追加页回归验证原问题及其范围仍可导出。
4. 自然语言定义漏词法门。现一次内容核验覆盖全部映射，不依赖知识点类别或特定定义词法。
5. 范围收据完成后领域写入无事务内守卫。现同一写事务复核停止、消息状态和租约，收据后取消不能写入 preview。
6. 重复排除核验可让后一个 consistent 覆盖先前 conflict。现拒绝重复或越界核验 ID，按无法核实处理，有回归反例。

## 主审补充修复

- 生产预习提示缺少 questions/unit_ids JSON 合同，现在实际 system message 中明确声明并验证载荷。
- 预习正文先于终态单独更新，现正文、问题与 tutoring 阶段同一终态事务提交。领域保存后故障注入证明回滚，重试复用已完成节点。
- 自动映射修复未消耗公共持久调整额度，现接入 begin_adjustment/end_adjustment；额度拒绝时不追加修复调用。
- 旧新学习图共用版本，现新图 study-scope-v2，旧 study-pages-v1 安全结束并提示显式重试，历史保留。协议、配方与三项能力同步 v2。
- StudyState JSON 合同版本 2；数据库 SCHEMA_VERSION 68，无新表。OpenAPI 仅同步本票学习合同 Schema，保留无关基线漂移。

## 逐项验收

| 票面验收项 | 实际代码与测试证据 | 结果 |
| --- | --- | --- |
| 同名概念/公式不串 ID、依据，实质片段覆盖或有依据排除 | assign_unit_id、逐片段矩阵；跨页同名测试、旧复盘片段消歧、遗漏修复及排除冲突/遗漏/重复反例 | 通过 |
| 定义/公式原文核对，结构不替代内容 | 独立 verify_scope 内容调用；原票两项公式冲突、自然语言定义及四种 kind 均核验反例 | 通过 |
| 数量随密度、核心覆盖、不即时作答 | preview_bounds 和覆盖门；密度失败后重试、正式正文“暂不需要作答”、实际 JSON/表达载荷断言 | 通过 |
| 仅提交成功进入辅导、停止/失败/重试正确 | 正式终态事务写后故障回滚；相同策略复用/变化策略只重预习；收据后取消；旧图结束后新图重试 | 通过 |
| 新旧范围/预习/来源导出恢复，预算/表达一致 | v1/v2 共存真实备份恢复和账户导出；超过 8 版历史；正式账户删除隔离；共享调整额度；学习和文案接缝回归 | 通过 |

## 实际验证

全部使用 conda agent 的 `C:/Users/33755/anaconda3/envs/agent/python.exe`，`PYTHONUTF8=1`。pytest 仓库 pythonpath 指向本工作树 src。

- 原交付独立复验：学习全链路 109 passed；生命周期/文案 75 passed。
- 修复后学习全链路、生命周期和文案综合复验 **150 passed**（pytest 两个 worker，不是编码子代理）。最后核验简化、四种类别反例及旧未知引用保留后，核心复验 **20 passed**，其中原票 6 项、新独立故障回归 13 项、真实生命周期 1 项；与前组有重叠，不相加。
- 图版本/预算/恢复接缝 57 passed；更新两处调用次数断言后定向组 69 passed。
- 内核/合同/工作流/架构 **69 passed / 1 failed**。唯一失败 test_committed_openapi_matches_current_api，在原 main 独立复现。本票学习 Schema 已同步，剩余 11 项漂移集合与 main 逐项一致，0 新增漂移，证据为两个 openapi-drift.json。
- mypy 修改源码及其依赖：**main 22 项 = Issue 22 项**，Compare-Object 逐项一致。改动源码无新增诊断；一次新引入的变量类型冲突已修复。
- 改动文件 Ruff 与 git diff --check 通过。
- 中间失败已修复：两处漏核验时的调用次数断言；新测试循环导入、作用域 INSERT 列清单和固定文案缺口标记。失败日志保留，不计作最终通过结果。

证据在 [validation](../validation/) 的 `31-*` 文件；本次生成的 main 基线证据已收拢到 Issue 交付，其他任务文件未动。主要复跑命令（仓库根目录）：

```powershell
$env:PYTHONUTF8='1'
& C:\Users\33755\anaconda3\envs\agent\python.exe -m pytest tests/chat/test_improvement31_study_scope_preview.py tests/chat/test_issue31_independent_acceptance.py tests/lifecycle/test_issue31_scope_lifecycle.py -q -p no:cacheprovider --basetemp=.tmp/31-core
& C:\Users\33755\anaconda3\envs\agent\python.exe -m pytest tests/kernel tests/contracts tests/workflows tests/architecture -q -p no:cacheprovider --basetemp=.tmp/31-contract
```

## 剩余限制

- 未调用真实模型；OCR/视觉、映射、误排除和表达体验质量由票 42 评测。
- 已注入节点收据→领域提交、领域写入→终态事务故障；未真实杀死并重启整个 worker，不宣称完成系统级崩溃演练。
- 跨配方范围依赖使用摘要而无独立产物 ID，审计颗粒度有限；旧模糊预习仅保留原引用，不声称消歧。
- 历史完整保留，状态大小随更新增长；本票没有擅自引入淘汰策略。
- 未重跑 tests/chat 全量，不对原代理报告的 49 项失败作独立背书；本次实际失败对 OpenAPI/mypy 作 main 比对。

## 合并、推送与清理

合并前通过用户既有代理 `http://127.0.0.1:7890` fetch origin，当时远端与本地 main 均为 68357a20。使用命令级代理，不改持久配置。最终执行结果在完成后追加。
