# Issue 02：修复学习模式降级空档与画像注入顺序

Status: ready-for-human

Type: task

Priority: P0

User stories: US-05

## 已验证现状与根因

- 生产会话：用户在学习模式发送「我想学习关于transformer相关的知识」，本地授权知识库 docx 有命中（`[reference:1]`）但覆盖不足，DDG 超时，系统只回复「这轮我先不把不确定内容说成结论：公开补充来源当前不可用，因此本轮不能可靠断言关键科学结论。你可以重试检索、上传材料，或让我先解释如何核对来源。」——没有模型知识回答，也没有画像。
- 根因一（降级空档）：`src/bridges/learning/teaching_gate.py:363-373`：
  `allow_model_knowledge = not local and base_status != CONFLICT and search_status in {None, ERROR, PERMISSION, EMPTY}`。本地命中但 `insufficient_coverage` 时 `local` 非空 → 禁止降级 → gap 取 `teaching_gate.py:430` → 拒绝句由 `teaching_gate.py:1227-1231` 拼接 → `src/bridges/chat/turn.py:2647-2687` 短路返回，**不调用 Qwen、不写进度**。
- 根因二（画像缺失）：画像切片编译在 `turn.py:3037+`（`_compile_profile_slice`，`turn.py:5483-5684`），位于上述短路之后；拒绝路径不编译、不注入、不披露画像。「暂按初学者处理」为硬编码字符串（`teaching_gate.py:1235`、`686`），不是画像兜底。
- 已冻结决策（本轮 #2）：本地覆盖不足 + 联网失败时允许带标注降级；仅 CONFLICT 保持拒绝；降级回答基于本地材料与模型知识、显著标注「本轮未联网核实」、不推进教学进度；拒绝/降级路径同样编译并披露画像。
- 现有降级实现（本地零命中路径）可作参照：`turn.py:3173-3174`、`3219-3221`、`3269-3270` 使用 `ensure_unverified_search_prefix`（`turn.py:1276-1287`）强制「本轮未联网核实：」前缀并剥离引用（`turn.py:1296-1304`）；进度仅 `can_answer_reliably` 时提交（`turn.py:3524-3556`）。

### 上下文指针

- `src/bridges/learning/teaching_gate.py:253-534`：`assess` 主流程、本地状态分类（:332-346）、搜索失败分支（:406-443）、降级开关（:363-373）、拒绝文案（:430、:1227-1231）。
- `src/bridges/chat/turn.py:2605-2687`：证据门调用与拒绝短路；`turn.py:3037-3096`：画像编译与 payload 组装；`turn.py:3524-3556`：进度提交条件。
- `src/bridges/contracts/retrieval.py:99`：`INSUFFICIENT_COVERAGE` 信号定义。
- `tests/chat/test_teaching_chat.py`：学习模式终态与降级测试。

## What to build

1. 放宽降级开关：本地状态为 `insufficient_coverage`（有命中但不足）且非 CONFLICT、搜索状态为 None/ERROR/PERMISSION/EMPTY 时，`allow_model_knowledge=True`。本地 SUFFICIENT 时维持正常教学；本地 CONFLICT 时维持拒绝（gap 文案保持不变）。
2. 降级回答的内容合同：以本地命中材料为上下文锚点 + Qwen 模型知识谨慎补充；正文显著标注「本轮未联网核实」；引用只列真实本地来源，不出现任何网络来源、伪 URL 或「已联网/已查到」表述；不创建/推进教学计划、课次或联网证据进度。
3. 画像前移：把画像切片编译移动到证据门短路判定之前（或让降级/拒绝分支各自显式编译），使降级回答能注入已授权画像（如学段、背景）并在「本次上下文说明」如实披露；`use_profile=False` 时行为与现有合同一致。「当前水平假设」在画像可用时以画像为准，缺失时保留现有兜底文案。
4. 拒绝路径（CONFLICT 等仍保留的场景）同样披露画像使用情况；若该路径设计上不调用模型，则只编译披露、不注入生成。
5. 证据门 reason 文案更新：覆盖不足 + 搜索失败时不再输出「不能用模型记忆替代现有材料」，改为如实说明「本地材料不足、联网未完成，本轮为未联网核实的背景回答」。

## 非目标

- 不改变本地 SUFFICIENT 与公开来源 SUFFICIENT 的正常教学路径。
- 不放宽 CONFLICT（本地冲突/公开冲突）的拒绝语义。
- 不修改覆盖裁决算法（`evidence_coverage.py` 的三维匹配等）与来源验证策略。
- 不让降级轮推进学习进度、不补写联网证据；不把模型知识回填成搜索成功投影。
- 不处理搜索提供方切换（Issue 01）与预算/超时修复（Issue 06）。

## Acceptance criteria

- [ ] 「本地 docx 命中但覆盖不足 + 搜索超时/失败」的学习轮：系统调用真实 Qwen 产出带「本轮未联网核实」显著标注的背景回答，引用仅含真实本地来源，消息终态可重载。
- [ ] 同一轮的教学计划/课次/联网证据进度零推进；`searched_at`、尝试记录等搜索投影如实保留。
- [ ] 降级轮编译并注入画像切片，「本次上下文说明」披露使用条数；无画像时保留「暂按初学者处理」兜底且不构成阻塞。
- [ ] 本地 CONFLICT 轮维持拒绝语义与既有 gap 文案，不调用模型知识补位。
- [ ] 本地零命中 + 搜索失败的既有降级行为不回归（前缀、引用剥离、进度不推进）。
- [ ] 搜索成功路径不受影响：可核验来源进入证据门，正常教学与进度提交。
- [ ] 文案中不再出现覆盖不足场景下的「不能用模型记忆替代现有材料」。

## Test plan

1. `tests/chat/test_teaching_chat.py`（或等价学习模式测试）新增：insufficient_coverage + 搜索 ERROR/PERMISSION/EMPTY/None 四种组合的降级终态断言（前缀标注、引用仅本地、Qwen 调用发生、进度未推进）。
2. CONFLICT 组合断言拒绝路径不变；本地 SUFFICIENT 断言正常路径不变。
3. 画像断言：降级轮 prompt 组装包含画像切片且消息带披露；`use_profile=False` 时不编译。
4. 模型 spy 断言降级轮只有一次真实文本调用、无来源伪造；拒绝轮零模型调用。
5. 重载测试：降级/拒绝终态刷新后投影一致，重试后新尝试记录可区分。

建议回归命令：

```powershell
python -m pytest tests/chat/test_teaching_chat.py tests/learning -q -p no:cacheprovider --basetemp=$env:TEMP\bridges-r7-issue02
```

## Observability & rollback

- 审计区分三种学习终态：可靠回答、带标注降级、拒绝；记录本地充足性信号、搜索状态、是否注入画像（条数，不含内容）。
- 不变量告警：降级轮出现网络来源引用；降级轮写入学习进度；拒绝轮发生模型调用。
- 回滚：降级开关可按本地状态粒度回退到 `not local` 旧语义；回滚不得删除已持久化的消息与披露记录。

## Blocked by

无。与 Issue 01 在学习模式链路汇合时，以两边验收标准共同回归（Tavily 成功/失败 × 本地三种状态）。

## Comments

- 2026-08-14：本轮冻结决策 #2——覆盖不足 + 联网失败允许带标注降级，仅 CONFLICT 保持拒绝。该空档由第 6 轮之前的 58ae7b6 引入，第 6 轮 Issue 03 未触及。
- 2026-08-14：画像前移同时修复用户在截图中抱怨的「没有调用用户画像」。
- 2026-08-15：实现完成（分支 02-learning-fallback-profile）。降级开关放宽至 `insufficient_coverage` + 搜索失败；降级回答保留真实本地引用、剥离网络/论文引用与 URL、前缀「本轮未联网核实」；画像编译前移到证据门短路判定之前（拒绝路径只披露不注入）；「当前水平假设」以画像学业情况为准；reason 文案改为「本地材料不足、联网未完成，本轮为未联网核实的背景回答」；审计记录三种终态 + 本地充足性信号 + 画像条数，并新增降级轮网络引用不变量告警。验收与测试计划全部落地（门级 21 项、聊天级 18 项），`tests/chat` + `tests/learning` 失败集与基线完全一致（44 个均为预先存在）。
