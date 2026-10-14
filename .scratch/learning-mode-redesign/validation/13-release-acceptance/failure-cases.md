# 本票运行发现的问题案例与失败记录

本文件记录工单 13（真实模型旅程与发布验收，commit `a51f62a3`，语料 `study-journey-corpus-v1` / `25f082…`）中发现、但按工单边界不在本票修复的问题。每个案例都给出运行证据与责任票。

## F3（本票主证据运行 #123822、#123300、#122751 均出现）：完整讲解轮超出单次输出上限

- 现象：`limit-removable-discontinuity` 的 `l4-skip` 回合（学员明确要求「跳过引导，直接讲讲这道题的完整过程」）以 `assistant_status=error`、`assistant_error_code=output_budget_exceeded` 结束；正文为截断的部分内容，开头/中段正确、结尾不完整。
- 实测（3 次独立运行，同一提交与语料，均复现）：
  - `123822`：首段 38594 ms、输出 2253 token、2 次模型调用、状态 error；
  - `123300`：首段 37663 ms、输出 2206 token、2 次模型调用、状态 error；
  - `122751`：首段 37089 ms、输出 2203 token、2 次模型调用、状态 error。
- 机制定位：聊天轮默认输出上限 `DEFAULT_OUTPUT_TOKENS = 1024`（`src/bridges/chat/lightweight_policy.py:105`，经 `chat/turn.py` 的 `_payload_dict` 传入）；流式适配器在 `finish_reason == "length"` 时产生 `output_budget_exceeded` 错误块（`src/bridges/ai/qwen_adapters.py` 流式分支）。完整讲解需要约 2250 token，超过单次调用上限，第二次调用触发截断。
- 影响：AC04 / D13 的真实模型路径「得到完整说明」不成立（新学习中长讲解会被截断并报错），且错误路径下未自动降级为分段续讲。
- 备注：本票更早的两次完整运行（stamp `110845`、`105836`，运行文件已按保留策略删除）该轮曾正常完成，说明该问题与回复长度相关、并非绝对必现；在固定语料下 3/3 次失败。
- 责任票：04（用户控制与完整讲解）；机制根因位于 02 建立的聊天输出预算路径。

## F2（3 次运行中 2 次出现）：编程情境的误解作答未登记为证据

- 现象：`programming-mutable-default` 中学员第一次作答（c2，明确误解：「第二次输出 [2]……每次调用都会重新创建默认的空列表」）在 `123300`、`123822` 两次运行中未生成证据；同一待答检查最终由学员第三次消息（c3，自我纠正后的正确回答）作答并核验。
- 实测：
  - `122751`：证据来自 c2 消息（`9xzf06…`，outcome=misconception，assistance=independent），误解被正确登记；
  - `123300`：该会话唯一证据来自 c3 消息（`YLhAZ…`，outcome=correct，assistance=independent）；
  - `123822`：该会话唯一证据来自 c3 消息（`6JZn78…`，outcome=correct，assistance=independent），且该消息作答发生在导师已给出完整纠正讲解之后（该轮记录 assistance=independent 是否成立存疑，属 AC02「被提示/被讲解的复述不能算独立完成」的判定风险）。
- 影响：误解漏登记导致漏诊断（同一情境 2/3 次没有形成 misconception 记录）；答案与检查的匹配在“学员先错后改”的连续消息上不稳定。
- 责任票：03（嵌入核验与反馈）；涉及诊断判定时同时知会 05。

## F1（3 次运行均出现，影响面较 F2 小）：跨主题学情记录被采用

- 现象：`programming-mutable-default` 会话中，三个导师回合（c2 `PCTGq…`、c3 `h6TFE1…`、c4 `iigqs5…`）的采用记录里都带上了极限情境的记录 `lr-UnSdGJVk4XvPooeJhrIE_A`（「代数分式化简与定义域分析」，verdict=misconception），与编程内容无关。
- 机制：`src/bridges/study/adaptation.py` 的 `select_relevant_records` 用 2 字词重叠（`_TITLE_TERM_MIN=1`）计算相关性，「函数/定义」等高频词会把极限记录匹配进编程会话。
- 更早一次完整运行中还观察到编程会话回复出现错误本地化表述「之前的核验显示你在这里有一个具体误解」（该运行文件已按保留策略删除）；本 3 次运行的编程回复文本未再出现该措辞，但采用记录污染始终存在。
- 影响：D04/D10 的「学习记忆带实际依据、诊断用于教学」被削弱——采用列表带入无关记录，存在把无关误解反馈进其他主题教学的风险。
- 责任票：09（按学情调整后续教法）。
