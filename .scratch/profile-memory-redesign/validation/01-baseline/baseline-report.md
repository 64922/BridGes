# 工单 01 画像闭环基线报告（2026-10-15）

方法与判定口径见 `measurement-method.md`；能力与消费者见 `capability-inventory.md`。
本报告只记录实测结果；替身与真实响应分开标记。

## 1. 运行身份（运行锁摘要）

| 项 | stub | real |
| --- | --- | --- |
| 证据文件 | `baseline-stub-20261015T061452Z.*` | `baseline-real-20261015T061452Z.*` |
| 运行锁 | `run-lock-stub-20261015T061452Z.json` | `run-lock-real-20261015T061452Z.json` |
| 代码 | `b3d68967`（dirty=False；评测代码身份锚点为下述摘要） | 同左 |
| 语料 | `profile-loop-corpus-v1` / `3b97aa9a…` | 同左 |
| pipeline/evaluation 摘要 | `0f94e9e4…`（pipeline）、`86abbe5b…`（evaluation） | 同左 |
| 模型 | 本地替身（聊天 + 网关抽取） | `qwen3.7-plus-2026-05-26`（真实） |
| 凭据 | 不需要（InMemory） | `qwen/tavily = credential_store` |
| 合同版本 | `profile-auto-v2`、`profile-signal-v2`、`profile-extraction-prompt-v4`、`profile-atomic-v1` | 同左 |

抽跑证据（验收要求「重跑一个已通过 + 一个已知失败」）：
`baseline-stub-20261015T061538Z.*`（`positive-academic-control` + `t01-name`），
结论与全量一致。

## 2. 总览

| 旅程 | 问题类 | 期望 | stub 实测 | real 实测 | 一致 |
| --- | --- | --- | --- | --- | --- |
| t01-name | name | known_failing | known_failing | known_failing | ✓ |
| t01-pet | pet | known_failing | known_failing | known_failing | ✓ |
| t01-habit | habit | known_failing | known_failing | known_failing | ✓ |
| t02-psychology | sensitive_keyword | known_failing | known_failing | known_failing | ✓ |
| t03-time-limit | time_constraint | known_failing | partial | partial | ✓ |
| t04-cross-turn | neighbor_messages | known_failing | known_failing | known_failing | ✓ |
| positive-academic-control | positive_control | pass | pass | pass | ✓ |
| recall-academic-control | recall_control | known_failing | partial | partial | ✓ |

全部旅程 `baseline_match=True`；每条旅程独立账户，快照与修订链无跨旅程污染。

## 3. 六类问题复现（入口级证据）

1. **姓名（t01-name）**：`我叫小林。` → 无提取运行、无四维记录、无原子条目；
   真实模式回复「你好，小林」只说明模型复述当前输入，称呼不进入画像。
2. **宠物（t01-pet）**：`我养了一只猫叫团子。` → 三表均无写入；真实模式回复
   讨论团子的性格，纯对话行为，无持久记忆。
3. **习惯（t01-habit）**：`周末一般会去爬山。` → 三表均无写入。
4. **心理学（t02-psychology）**：`我喜欢心理学。` → 抽取运行 0（关键词规则判
   forbidden/sensitive_content 拦截），学科兴趣未入库。
5. **时间限制（t03-time-limit）**：`我在学线性代数，每晚只能抽出半小时。` →
   规则抽取 `succeeded_written`，条目 `我在学线性代数`（active v1）；「半小时」
   时间约束在任何记录/观察/条目中均不存在（partial）。
6. **邻近消息（t04-cross-turn）**：两轮真实会话后：
   - stub 模式录制载荷：网关抽取调用 `project_id="retry"`、`neighbor_hint_present=false`；
   - 真实模式：网关抽取 run `source=qwen_model, outcome=succeeded_empty`（无邻居上下文可解析）；
   - 应用级探测（真实会话 ID + 生产邻居读取函数）：`[]`（逆序读取遇当前消息即退出）。
   指代与偏好（「以后先给我结论」）均未捕获。

## 4. 正向对照与召回对照

- **positive-academic-control（pass）**：`我是计算机专业的大二学生。` →
  提取 `succeeded_written`，四维记录 1 条 + 原子条目 1 条成对写入，
  条目 `计算机专业的大二学生` 保持 active。写入路径当前可用，后续票不得破坏。
- **recall-academic-control（partial，记为 known_failing）**：同上自述后问
  「你记得我在读什么专业吗？」→ 下一轮 `adopted_profile_slice.adopted_items=[]`，
  原事实被排除为「与当前问题无关」（stub 证据两轮均为该原因）；回答中的事实
  来自会话历史而非画像采用。D11「召回不设门槛」在当前实现不成立。

## 5. 基线期间额外观察（如实记录，不属六类但影响后续票）

1. **问句被误判为自述并污染修订链**：recall 对照第二轮问句由规则抽取器
   `succeeded_written`，产出杂项条目 `什么专业吗`（active），原事实
   `计算机专业的大二学生` 被 supersede（v2）。属 04/05 的误归属与修订范围，
   stub 与 real 均复现（条目状态见两份证据 JSON）。
2. **撤回传播→摘要失效在嵌套事务下告警**：上述 supersede 触发
   `chat/summary.py:786`「画像撤回传播失效摘要失败」，根因是监听器内
   `database.transaction()` 重入（`storage/database.py:4516 cannot start a
   transaction within a transaction`）。该告警在 main 已存在（本次未改任何
   生产代码），撤回结果不受影响（best-effort 传播被吞并记录）；建议 11/18
   复核失效传播与摘要失效路径。
3. **真实模式成本与延迟（本次实测）**：10 次模型调用，输入 4251 / 输出 720 token；
   回答耗时样本 1406–6656 ms；抽取耗时样本 0/0/2000/1000/0/0 ms（规则路径同事务
   完成，网关路径 2 次共 3000 ms）。stub 模式 token 为 0 并已在证据中标注不代表成本。
4. **真实模型缺口**：本次凭据齐全，无 inconclusive；唯一缺口是「回答收益」的
   因果归因（回答正确与会话历史可见无法区分），留给 18 的真实效果复核。

## 6. 对照与追溯

- 重跑：同一语料两次运行（全量 `061452` + 抽跑 `061538`）结论一致；
  所有证据含运行锁（语料/代码摘要/合同版本/预算常量/凭据来源）。
- 已知失败单独记录在各证据文件与 `measurement-method.md` 的期望列，
  未使用无条件跳过、改预期或长期整体标红。
- 无真实私人原文、凭据或生产数据库进入仓库；语料全部为合成数据。
