# Issue 03：扩展意图路由词表并建立金标契约测试

Status: ready-for-agent

Type: task

Priority: P0

User stories: US-01

## 已验证现状与根因

全部意图路由为确定性关键词规则，无模型分类器。发送时在 `ChatService.start_generation`/`start_first_turn`（`src/bridges/chat/service.py:702`、:1277）依次执行人味化 NL 路由（:865-905）、图片 NL 路由（:931）、统一能力分类（`_route_for_turn` :989-1032 → `NaturalLanguageRouter.classify`，`src/bridges/routing/service.py:238`）；流式时按持久化快照分支（`src/bridges/chat/turn.py:1830` 起）。

已在当前 HEAD 实测：

- ✅ 「帮我人味化这篇文章」「给我人味化润色这篇文章：<长文>」命中文味化路由（`src/bridges/skills/humanizer/intent.py:63`，`contract_compiler.py:65-71` 的 `REWRITE_ACTION_RE` + `intent.py:33` 的 `_ARTICLE_RE`/`_DIRECT_REQUEST_RE`）。
- ✅ 「给我找几篇关于 Transformer 的论文」命中 `paper_search`（`routing/service.py:255-304`）。
- ❌ 「给我规划一下我的学习任务」落入普通聊天：`routing/service.py:344-372` 兜底要求「规划」与方向/职业/就业/工作同现；`src/bridges/career/intent.py:43-74` 弱关键词需同句规划语境词，「学习任务」均不覆盖。前端生涯建议卡文案「帮我排一下研究生三年的学习优先级」（`apps/web/src/components/bridges/chat-template.tsx:285`）实测也不触发。
- ❌ 人味化变体「把这篇文章改得有人味一点」不命中（词表有「人味化」无「人味」），静默落入普通聊天且无任何兜底提示。
- 生产截图中三句话全部漏路由，最可能是生产构建早于 2026-08-10 的路由合入提交（f7cabd8、2756448）；本轮以金标契约测试把三类语句钉死在回归里，防止再次静默退化。

### 上下文指针

- `src/bridges/skills/humanizer/intent.py:63-141` 与 `contract_compiler.py:65-71`：人味化触发表。
- `src/bridges/career/intent.py:19-74` 与 `src/bridges/routing/service.py:238-372`：生涯触发与统一分类器。
- `src/bridges/chat/service.py:865-1032`：路由装配与冲突放弃逻辑。
- `tests/chat/test_natural_language_paper_route.py`：既有 NL 路由测试样式。

## What to build

1. 生涯词表扩展：使「给我规划一下我的学习任务」「帮我排一下研究生三年的学习优先级」及其近似变体（规划/安排/排一下 + 学习/任务/复习/课程/考研/读研等组合）触发生涯规划；保持「规划」单独出现或明显非发展语境时不劫持普通聊天。
2. 人味化词表扩展：覆盖「人味」「自然一点」「不像 AI 写的」等近似说法的合理变体；保持否定前缀、纯咨询句的既有防护。
3. 建立金标契约测试集：至少包含——
   - 三条用户点名语句原句：「帮我人味化这篇文章」「给我规划一下……（学习任务变体）」「给我找几篇关于……的论文」；
   - 每条 2-3 个近似变体；
   - 负例：如「这篇论文讲了什么」「你觉得这篇文章哪里写得不好」「今天帮我安排一下学习计划之外的事」等应保持普通聊天/不触发对应模块的语句。
   金标断言发送时持久化的路由快照（skill 载荷/CapabilityRoute），而非仅断言函数返回值。
4. 冲突与 veto 语义保持不变：复合任务 CLARIFY、图片/视频路由优先级、`route.is_paper_search` 防护（`_REWRITE`/`_KNOWLEDGE_QA` 降级）均不回归。
5. 每条新增词项必须附对应测试；词表改动集中在既有正则/常量处，不引入新的路由层。

## 非目标

- 不引入模型意图分类器或任何每轮额外模型调用。
- 不加前端显式功能入口/skill 载荷提交。
- 不改变学习模式的会话级 mode 语义（ADR-0022）。
- 不调整论文搜索执行链路（Issue 05）、人味化画像注入（Issue 04）。
- 不处理 4000 字内容上限等请求合同问题。

## Acceptance criteria

- [ ] 三条用户点名语句原句在发送时即持久化正确路由：人味化 skill 载荷、`paper_search`、生涯路由（含生涯流式分支的现场重判）。
- [ ] 「给我规划一下我的学习任务」「帮我排一下研究生三年的学习优先级」「把这篇文章改得有人味一点」等变体全部按预期路由。
- [ ] 金标负例保持普通聊天，普通陪伴对话不被新词项劫持。
- [ ] 路由结果在重载后一致；复合任务澄清、图片/视频/论文防护逻辑不回归。
- [ ] 金标契约测试进入常规回归命令，任一条目失败即红。

## Test plan

1. 新增（或扩展既有）NL 路由测试文件：参数化金标集，断言 `route_humanizer_message`、`NaturalLanguageRouter.classify`、`is_career_intent` 及 `start_generation` 持久化快照两个层面。
2. 既有路由/聊天测试全量回归，确认无劫持与冲突回归。
3. 前端建议卡文案对应语句纳入金标，保证产品自述能力真实可达。

建议回归命令：

```powershell
python -m pytest tests/chat/test_natural_language_paper_route.py tests/chat tests/career tests/humanizer -q -p no:cacheprovider --basetemp=$env:TEMP\bridges-r7-issue03
```

## Observability & rollback

- 路由判定保留既有持久化快照审计；新增词项命中时审计可区分命中类别。
- 回滚按词项粒度还原；金标测试随词表同进退，不得只回滚代码留下测试或反之。

## Blocked by

无。

## Comments

- 2026-08-14：本轮冻结决策 #4——只扩确定性词表 + 金标契约测试，不引入模型分类器。
- 2026-08-14：生产漏路由的另一嫌疑是构建版本滞后；发布负责人应以金标测试在目标构建上全绿作为部署验收依据。
