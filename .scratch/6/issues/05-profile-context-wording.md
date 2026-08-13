# Issue 05：将画像信息文案明确为已授权用户背景

Status: resolved

Type: task

Priority: P2

User stories: US-PROFILE-DISCLOSURE-01、US-PROFILE-DISCLOSURE-02、US-WEB-TRUST-01

## 已验证现状与根因

- `src/bridges/chat/turn.py::context_note_thinking()` 在画像上下文可用时写入“已参考 N 条相关信息（仅限当前任务）”。这里的计数实际是 `profile_item_count`，来源是用户已授权的画像/记忆切片，不是网络搜索结果。
- 同一轮聊天还可能显示 DDG 来源、知识库命中或 arXiv 论文。统一使用“相关信息”会让用户误以为系统已联网并参考了 N 条网页，尤其在 DDG 失败时会与“本轮未联网核实”产生直接信任冲突。
- 画像披露的其他状态也使用“此前提供的信息”“相关信息暂时无法整理”等模糊称呼，没有持续说明来源、授权和用途边界。
- 现有上下文正文摘要已经能描述“已授权信息”，但公开 thinking/tools 标签没有沿用同一术语，形成同一数据在不同 UI 位置含义不一致的问题。
- 用户已确认画像能力保留本地规则 + Qwen 歧义分支的混合实现。本 issue 只要求诚实披露实际使用的画像来源，不把本地规则结果冒充 Qwen，也不要求所有画像处理都调用模型。

### 上下文指针

- `src/bridges/chat/turn.py:1255-1273`：画像上下文摘要与 thinking/tools 文案。
- `src/bridges/chat/turn.py` 中 `ContextNoteProjection` 的创建、持久化与错误分支。
- `tests/chat/test_profile_slice_chat.py`、`tests/chat/test_profile_intent_chat.py`：画像切片进入聊天的测试。
- `tests/profiles/test_chat_slice_compiler.py`、`tests/profiles/test_memory_slice.py`：授权切片与来源契约。
- `apps/web/e2e/issue27-profile-slices-disclosure-feedback.spec.ts`：画像使用披露和反馈入口。

## What to build

1. 建立统一的中文来源术语：画像上下文使用“你已授权的用户背景/画像信息”，DDG 使用“联网来源”，知识库使用“知识库材料”，arXiv 使用“论文来源”。公开文案不得再用无法判断来源的裸“相关信息”。
2. 将 READY 文案改为明确含义，例如“已参考 N 条你授权的用户背景信息（仅用于当前任务）”；N 必须继续来自实际 `profile_item_count`，不能把本地信号数量、候选数量或联网来源混入。
3. 将 OFF、EMPTY、ERROR 状态一起改为同一术语体系，分别说明本轮未使用、没有匹配和暂时无法整理已授权用户背景；ERROR 不得暗示整个回答失败或联网失败。
4. 如果投影已有来源/提取方式字段，在用户可见的详细披露中诚实区分本地规则与 Qwen 歧义提取；若当前投影无法安全暴露该细节，至少保证计数和“已授权画像”来源准确，不新增未经批准的推断标签。
5. 确保 DDG 失败 + 画像 READY 的组合文案可以同时出现且不矛盾：一条说明“使用了你授权的用户背景”，另一条说明“本轮未联网核实”；引用区域仍为空。
6. 更新 API/前端契约测试、可访问名称和必要快照，避免仅修改某个页面的硬编码字符串。

## 非目标

- 不修改画像抽取算法、阈值、候选审核、撤回/更正机制或存储结构。
- 不强制所有画像信号走 Qwen；明确、自述、学习目标与更正等本地规则路径继续保留。
- 不改变 DDG、arXiv 或知识库的检索逻辑，不伪造联网成功。
- 不展示隐藏 prompt、思维链、完整画像正文或未授权候选。
- 不重做 thinking 面板、画像中心或聊天卡片的视觉设计。

## Acceptance criteria

- [ ] READY 状态不再出现裸“已参考 N 条相关信息”，而是明确显示 N 条“你已授权的用户背景/画像信息”及仅用于当前任务的范围。
- [ ] N 与 `ContextNoteProjection.profile_item_count` 完全一致；DDG 来源数、arXiv 论文数、知识库命中数和未采用候选不计入 N。
- [ ] OFF、EMPTY、ERROR 四种状态使用一致、可区分的画像来源术语，用户不会把画像整理错误理解为联网错误。
- [ ] DDG 失败且画像 READY 时，同时显示“使用了已授权用户背景”与“本轮未联网核实”，正文无联网引用，两条状态不存在语义冲突。
- [ ] DDG 成功、画像 OFF 时，UI 能明确显示真实联网来源且说明本轮未使用画像信息，二者互不替代。
- [ ] 本地规则分支不会被标为“Qwen 已分析”；真实 Qwen 歧义分支若对外披露，标签与实际运行记录一致。
- [ ] 文案不暴露画像条目正文、隐藏推断、prompt、模型思维链或其他账户数据。
- [ ] API 序列化/重载后文案语义保持一致，屏幕阅读器可访问名称与视觉文本表达同一来源。
- [ ] 现有画像授权、关闭、撤回、更正和反馈旅程回归通过。

## Test plan

1. 参数化测试 `context_note_thinking()` 的 READY/OFF/EMPTY/ERROR，断言精确来源术语、计数与无敏感内容。
2. 添加组合测试矩阵：画像 READY/OFF × DDG success/error × knowledge/arXiv present/absent，断言各计数不会串线。
3. 在聊天持久化测试中保存并重载投影，确认 thinking/tools 与上下文披露仍一致。
4. 使用本地规则和 Qwen 歧义分支的受控 fake，验证公开来源标签与实际分支一致且模型调用数正确。
5. 扩展 `apps/web/e2e/issue27-profile-slices-disclosure-feedback.spec.ts`，检查视觉文案、可访问名称、关闭画像和 DDG 失败组合。

建议回归命令：

```powershell
python -m pytest tests/chat/test_profile_slice_chat.py tests/chat/test_profile_intent_chat.py tests/profiles/test_chat_slice_compiler.py tests/profiles/test_memory_slice.py -q -p no:cacheprovider --basetemp=$env:TEMP\bridges-issue05
cd apps/web
npx playwright test e2e/issue27-profile-slices-disclosure-feedback.spec.ts --project=chromium
```

## Observability & rollback

- 生产指标只需要状态、计数、来源类别和投影版本；不得记录画像正文、候选内容或用户消息。
- 增加契约断言/测试，防止 `profile_item_count` 被联网来源数覆盖；若来源字段缺失，显示保守的“用户背景信息不可用”，不要回退到“相关信息”。
- 文案发布可直接回滚，但不得回滚为会暗示联网成功的旧措辞。发现本地/Qwen 来源标记不可靠时，先隐藏提取方式细分，保留“已授权用户背景”这一确定事实。

## Blocked by

无。

## Comments

- 2026-08-13：已确认当前“4 条相关信息”实际是 4 条画像上下文，不是 4 条网页来源；这是文案语义缺陷，不是 DDG 成功证据。
- 2026-08-13：用户确认保留画像混合模式；本 issue 只做真实来源披露，不把本地规则改造成不必要的模型调用。

## Answer

- 已在 `codex/issue-05-profile-context-wording` 分支的 worktree 中统一上下文说明、thinking/tools、API 契约和前端卡片文案，明确使用“你已授权的用户背景信息”，并区分“知识库材料”“联网来源”和“论文来源”。
- `profile_item_count` 继续只统计实际注入的已授权用户背景信息条目；联网、论文、知识库来源只进入独立的来源类别字段。
- 已补充 READY/OFF/EMPTY/ERROR 状态、DDG 失败与 READY 组合、前端可访问名称及序列化契约测试。
- 验证通过：后端目标测试 50 项，额外 arXiv 测试 3 项，前端单测 44 项、TypeScript 检查、OpenAPI 类型生成一致性检查。完整 Python 回归受仓库既有同名测试模块和目录级 `conftest` 导入冲突阻断；Playwright 受当前环境 Chromium `spawn EPERM` 阻断。
