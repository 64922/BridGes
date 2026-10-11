# 论文搜索独立需求审查与复验

日期：2026-10-11（Asia/Shanghai）。初审固定点：`65e5b63720a05520f7721a4e81b2959f0432edb2`；审查对象是未提交的论文代码变更及新增文件。交接报告仅作为定位线索，结论来自 PRD、真实实现和行为回归。

## 初审发现与修复

| PRD 原句 | 独立发现 | 修复后的证据 |
| --- | --- | --- |
| §5.2「模型依据研究意图和已登记来源能力提出查询」；§5.3「反馈包含实际查询……语义匹配样本及缺失子问题」 | 查询原来只是拼接词并复制给各来源，补搜只是补同义词。 | `intent.py` 共用登记网关与预算的 `plan`；`query_planning.py` 校验每个来源、概念与英文表达；`nodes_planning.py`、`nodes_screening.py` 将来源专用查询及真实反馈接到实际检索。 |
| §6「沿第二篇找后续工作……明确条件或产物引用」 | 未向论文模块传递上轮选定论文身份，扩展固定使用新候选前 3 篇。 | `continuation.py` 从当前账户/会话、本轮以前的实际 `selected` 解析序号；`service.py` 注入上下文；`nodes_screening.py` 仅以该身份扩展并移除旧论文推荐，保留真实引用方向。无效引用在模型与来源调用前明确失败。 |
| §2「完整候选目标 20–30 篇」 | 默认每来源 8 条，两来源最多 16 条，推荐够 8 篇即不补搜。 | `planning.py` 默认每来源 10 条，重点目标仍为 8；筛选依据完整候选缺口决定一次有界补搜，显式篇数独立保留。 |
| §5.4「引用量……不直接认定……奠基地位」 | 旧排序按年份/引用中位数，甚至无引用时直接贴奠基角色。 | `ranking.py` 已撤销以辅助信号直接认定奠基地位的路径；保留可证实的综述、教程、较新与相关研究类别。 |
| §6「未经相关性和证据核验的候选不能充作已核验推荐」 | 专业评估失败仍能凭关键词交付成功推荐。 | `screening.py` 拒绝缺失语义裁决与无法定位的证据；`nodes_screening.py` 无可交付候选时返回可重试失败并保留查询；`relevance_cache.py` 仅在节点生命周期复用成功或失败裁决，不跨账户共享。 |

## 独立回归验证

使用 `C:/Users/33755/anaconda3/envs/agent/python.exe`（conda `agent` 环境）执行：

```text
python -m pytest tests/paper/test_paper_model_flow_acceptance.py tests/paper/test_paper_planning_acceptance.py tests/paper/test_paper_search_redesign.py -q
22 passed
```

新增回归检查真实 `parse → plan → search → screen` 的下游查询、来源差异、反馈原文、硬条件、无效模型查询拒绝、第二篇身份引用、仅目标引用扩展、旧目标不冒充后续推荐，以及语义失败的可重试状态和查询保留。缓存回归检查不重复判断未变候选、新候选与实际摘要改变会重新判断。

首次意图失败停搜、CNN/Transformer/轴承/小样本多概念、年份与来源限制、默认数量分别有行为回归；没有仅以提示词文本或交接声明证明功能。新增修复未增加依赖或独立运行系统。

定向 Ruff 已通过。业务文件 `query_planning.py`、`continuation.py`、`intent.py` 定向 mypy 已通过。完整仓库检查、主干基线归因、外部来源及前端合同验证由本轮主验收记录汇总。

## 剩余验证限制

- 上述模型响应为受控夹具，用于验证结构化产物、代码校验和实际下游行为，不能算真实模型语义质量或真实联网成功。
- 60–90 秒仍为设计预算目标，来源/模型总耗时需要生产配置实测；本记录没有将候选缓存回归视为性能承诺。
- 无法得到目标论文的 OpenAlex 身份时明确说明引用扩展缺口；同主题检索结果不会冒充已证实的后续引用工作。
- 本进程全论文测试在 Starlette 本地 socket 测试阶段阻塞；已停止该进程，交由主验收在允许本地 socket 的环境完成完整复验。`conda` 启动器遇到系统 DLL 重定位错误，环境内 Python 本身可正常运行，未改系统配置。

需求审查结论：上述 5 项初审缺陷已完成代码修复及定向行为复验；发布达标还须以主验收的全套检查、基线归因及真实来源记录为准。

## 完整检查后的复合集成回归复验

仅论文快照相对 `main` 新增两个复合集成失败：`test_composite_dispatch_commits_all_projections_once` 与 `test_formal_graph_publishes_qualified_snapshots_before_terminal`。独立定位发现，两者共用的 `tests/orchestration/issue37_chains.py` 将旧形状的推荐和身份字典直接写进评估产物，交付投影却经当前 Pydantic 合同补入新增默认字段；生产证据门因逐字段不一致正确拒绝将论文标成合格。

修复夹具在持久化前分别通过 `PaperRecommendation`、`PaperIdentity` 生成当前合同载荷，让产物链和投影使用同一份规范化数据。未放宽生产证据核验，也未把混合工作区结果作为归因证据。

将修复文件同步到 `.tmp/paper-acceptance-snapshot`，在该隔离副本使用 conda `agent` 环境 Python 复验：两个原失败节点 **2 passed**；两个完整集成文件及 `tests/orchestration` **60 passed**（11.16 秒），涵盖产物哈希篡改、能力版本错误和未合格投影拒绝等负向证据门回归。夹具定向 Ruff 通过。
