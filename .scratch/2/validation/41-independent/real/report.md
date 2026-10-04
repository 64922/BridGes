# 工单 41 画像质量评测报告

- 生成时间：2026-10-04T11:47:31.848820+00:00
- 代码提交：3514e1cd78463b80ba176c01054c029568e627f5
- 抽取提示词版本：profile-extraction-prompt-v4
- 模型：qwen3.7-plus-2026-05-26
- 账户模型：synthetic eval account per condition
- 真实探针：已执行（--real-probes 显式开启；缺凭据时记录 inconclusive）

## 样本与不确定性

- 确定性纵向场景：12 个（检查点 36，通过率 100%）
- 真实配对：8 次调用（2 任务 × 4 条件）
- 真实抽取探针：6 条源消息
- 样本量不足以宣称准确率；本报告只给方向性判断与阈值建议。

## 硬门与结果

- 确定性治理总体：通过
- 真实配对总体：通过；失败：[]
- 真实抽取探针总体：通过；失败：[]

## 真实抽取探针明细

| 探针 | 分类 | 结果 | 延迟(ms) | 模型调用 | 说明 |
| --- | --- | --- | --- | --- | --- |
| negated_preference | explicit_self | 通过 | 16375 | 1 | retained=True extracted=['我不喜欢长篇回答 不喜欢长篇回答'] |
| multi_fact | explicit_self | 通过 | 17484 | 1 | count=2 extracted=['我喜欢跑步 跑步', '也喜欢游泳 游泳'] |
| third_party | forbidden | 通过 | 0 | 0 | extracted=[] |
| quoted | forbidden | 通过 | 0 | 0 | extracted=[] |
| self_report | explicit_self | 通过 | 10453 | 1 | extracted=['我正在学习概率统计 学习概率统计'] |
| ambiguous_low_confidence | ambiguous | 通过 | 11327 | 1 | extracted=['我可能喜欢摄影 摄影|action=observe|reliability=0.3'] |

## 真实配对明细

| 条件 | 次数 | 通过 | 平均延迟(ms) | 输入 token | 输出 token |
| --- | --- | --- | --- | --- | --- |
| no_profile | 2 | 2 | 33109 | 976 | 3627 |
| correct_profile | 2 | 2 | 36937 | 1668 | 4245 |
| wrong_profile | 2 | 2 | 34703 | 1529 | 3979 |
| outdated_profile | 2 | 2 | 26780 | 976 | 2983 |

## 成本与事务边界

- 真实调用总输入 token：6250；总输出 token：17928
- 治理动作（记住/修改/删除/忘掉）各以单仓库事务提交，读取与采用切片为只读快照；
  本轮未单独插桩事务占用时长，作为已知局限。

## 阈值依据与索引/模型选择建议

- 套话控制与同维度完整事实阈值沿用既有自动断言（注入后可检出），本轮不放宽。
- 抽取路由保持确定性预检 + 固定模型 qwen3.7-plus-2026-05-26；
  探针通过时不建议引入语义索引或更换模型；若后续规模扩大、召回下降，
  再以本报告同一量表复测后决策。

## 限制

- 真实配对每条件样本少（2 任务），只证明机制可用与方向性改善，不宣称准确率。
- 配对回答检查为确定性内容规则，不替代人工盲评；盲评材料见 blind-review.md。
- 环境代理不可达 GitHub，仓库同步受限；DashScope 直连可用。
