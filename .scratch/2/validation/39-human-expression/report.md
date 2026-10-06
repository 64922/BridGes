# 工单 39 人味表达盲评报告

- 生成时间：2026-10-06T05:47:44.986552+00:00
- 代码提交：efe94f33f395233f49d0dc26d5469e591177ed01
- 模型：qwen3.7-plus-2026-05-26
- 语料摘要：6d55b5ce93bd1685
- 确定性机制：通过
- 放行结论：inconclusive

## 真实测量（按臂）

| 策略臂 | 回合 | 调用 | 输入token | 输出token | 首字延迟ms | 总延迟ms | 重试 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| current-v4 | 17 | 17 | 9198 | 8540 | 9011 | 9524 | 0 |
| legacy-v2 | 17 | 17 | 7687 | 7686 | 8210 | 8752 | 0 |
| concise-baseline | 17 | 17 | 3833 | 7976 | 7210 | 8779 | 0 |

- 人味专属新增调用为零：是

## 场景分布

| 类别 | 场景数 | 真实配对 | 多轮 |
| --- | --- | --- | --- |
| venting | 5 | 5 | 5 |
| mixed_troubleshooting | 4 | 4 | 4 |
| quoted_emotion | 4 | 4 | 4 |
| thanks_closing | 4 | 4 | 2 |
| correction | 4 | 3 | 4 |
| continuation | 2 | 1 | 2 |
| long_task | 4 | 4 | 4 |
| tool_failure | 4 | 0 | 4 |
| boundary | 4 | 4 | 4 |
| preference | 3 | 3 | 3 |
| formal_path | 21 | 0 | 14 |

- 部署参照（简洁基线实测）：调用 17、输入 3833 token、输出 7976 token、首字延迟均值 7210ms；部署门槛由发布票按本测量与预注册策略设定。

## 硬门（独立于温暖感得分）

- boundary_violation: 通过 51 / 失败 0
- fabricated_experience: 通过 51 / 失败 0
- fact_drift: 通过 51 / 失败 0
- failure_disguised: 通过 51 / 失败 0
- over_proactive: 通过 51 / 失败 0
- task_incomplete: 通过 49 / 失败 2

## 盲评放行结论

- 状态：inconclusive
- 尚无人工盲评提交：不放行，也不宣称自然度提升。

## 限制

- 真实配对覆盖可运行的日常聊天场景；学习/模块/固定文案路径由确定性覆盖矩阵与消费者验收记录核对。
- 无人工盲评提交时状态为 inconclusive：不放行，不宣称自然度提升。
- 确定性模型/工具响应只证明机制，真实模型体验以本报告实测为准。