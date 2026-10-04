# 工单 41 画像质量评测报告

- 生成时间：2026-10-04T12:07:19.089895+00:00
- 代码提交：2d152638dcc41661fed49866a056c805f2996c1c
- 抽取提示词版本：profile-extraction-prompt-v4
- 模型：qwen3.7-plus-2026-05-26
- 账户模型：synthetic eval account per condition
- 真实探针：已执行（--real-probes 显式开启；缺凭据时记录 inconclusive）

## 样本与不确定性

- 确定性纵向场景：13 个（检查点 41，通过率 100%）
- 真实配对：8 次调用（2 任务 × 4 条件）
- 真实抽取探针：6 条源消息
- 样本量不足以宣称准确率；本报告只给方向性判断与阈值建议。

## 硬门与结果

- 确定性治理总体：通过
- 真实配对总体：不通过；失败：['study-plan/correct_profile']
- 真实抽取探针总体：通过；失败：[]

## 真实抽取探针明细

| 探针 | 分类 | 结果 | 延迟(ms) | 模型调用 | 说明 |
| --- | --- | --- | --- | --- | --- |
| negated_preference | explicit_self | 通过 | 12813 | 1 | retained=True extracted=['我不喜欢长篇回答 不喜欢长篇回答'] evidence_ok=True persisted=['我不喜欢长篇回答'] |
| multi_fact | explicit_self | 通过 | 15061 | 1 | count=2 extracted=['我喜欢跑步 跑步', '也喜欢游泳 游泳'] evidence_ok=True persisted=['我喜欢跑步', '也喜欢游泳'] |
| third_party | forbidden | 通过 | 0 | 0 | extracted=[] evidence_ok=True persisted=[] |
| quoted | forbidden | 通过 | 0 | 0 | extracted=[] evidence_ok=True persisted=[] |
| self_report | explicit_self | 通过 | 14078 | 1 | extracted=['我正在学习概率统计 学习概率统计'] evidence_ok=True persisted=['我正在学习概率统计'] |
| ambiguous_low_confidence | ambiguous | 通过 | 8718 | 1 | extracted=['我可能喜欢摄影 摄影|action=observe|reliability=0.3'] evidence_ok=True persisted=[] |

## 真实配对明细

| 条件 | 次数 | 通过 | 平均延迟(ms) | 输入 token | 输出 token |
| --- | --- | --- | --- | --- | --- |
| no_profile | 2 | 2 | 34241 | 976 | 3662 |
| correct_profile | 2 | 1 | 31132 | 1668 | 3312 |
| wrong_profile | 2 | 2 | 50484 | 1529 | 5581 |
| outdated_profile | 2 | 2 | 33406 | 976 | 3504 |

## 成本与事务边界

- 真实调用总输入 token：6250；总输出 token：18855
- SQLite 事务持有时间与模型阶段事务状态见 deterministic-report.json 的 sqlite_async_transactions。
  该测量为合成内存 SQLite 的机制证据，不代表真实磁盘或生产负载延迟。

## 阈值依据与索引/模型选择建议

- 套话控制与同维度完整事实阈值沿用既有自动断言（注入后可检出），本轮不放宽。
- 抽取路由保持确定性预检 + 固定模型 qwen3.7-plus-2026-05-26；
  探针通过时不建议引入语义索引或更换模型；若后续规模扩大、召回下降，
  再以本报告同一量表复测后决策。

## 限制

- 真实配对每条件样本少（2 任务），只证明机制可用与方向性改善，不宣称准确率。
- 配对回答检查为确定性内容规则，不替代人工盲评；盲评材料见 blind-review.md。
