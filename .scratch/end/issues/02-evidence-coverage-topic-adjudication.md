# Issue 02：修正证据门覆盖判定的主题词提取与匹配

Status: resolved

Type: task

Priority: P0

User stories: US-02

## What to build

修复学习模式证据门的覆盖误判：联网搜索实际返回了覆盖本轮学习目标的已抓取来源时，证据门应判证据充足——教学卡显示正常教学状态、回复不带"本轮未联网核实"前缀、引用可绑定到已接受来源、学习进度正常推进。

具体做三件事，全部保持纯确定性：

1. **必需主题词来源修正**：覆盖裁决的必需维度改从本轮学习目标/规范主题提取（学习目标生成处 `teaching_gate.py:1276-1279` 的原始目标主题，而非脱敏、8 字截断后的搜索查询碎片 `query_summary`）。
2. **CJK 归一化**：匹配前对目标词与来源文本做归一化——去除尾部"的"等助词、剥离"相关基础知识/基础/简介/入门"等模板噪声、统一全半角与大小写，使"卷积神经网络（CNN）基础知识入门"可命中目标"卷积神经网络"。
3. **版本化别名表**：内置小型、显式、可审计的中英文别名映射（首期至少含 `CNN ⇔ 卷积神经网络`，沿用并保留现有 Transformer 三维特例语义），使 "Convolutional Neural Network" 等英文权威来源可命中。

匹配本质不变：仍是归一化后的字面包含判定，通过门槛不变（≥1 条已抓取、可安全引用的来源覆盖必需维度），不引入 embedding 或模型语义判定。

## 已验证复现与根因摘要

- 复现输入（学习模式）："我想学习卷积神经网络的相关基础知识"。实测链路：目标句"学习'卷积神经网络的相关基础知识'并理解其核心机制" → `LocalQueryPlanner._scrub_with_categories`（`src/bridges/web_search/service.py:313-342`）按 2-8 字贪心切成 `学习 | 卷积神经网络的相关 | 基础知识 | 并理解其核心机` → DuckDuckGo 返回 5 条来源（部分抓取失败，整体 PARTIAL，单条 `verified`+`content_summary` 齐全，即 UI 的"已抓取核验"）。
- 覆盖裁决 `_required_dimensions`（`src/bridges/learning/evidence_coverage.py:116-132`）从 `query_summary` 去噪后取**最长 token**，实测得到 `("topic:卷积神经网络的",)`——尾部黏连"的"字；`_decide_source`（135-174 行）要求该字串字面出现在 `title[:600]+snippet[:600]+content_summary[:2400]` 中，"卷积神经网络（CNN）"、"Convolutional Neural Network" 类来源全部 `topic_mismatch` → `accepted_count=0`。
- `teaching_gate.py:344-349` 的 `coverage_insufficient` 分支据此返回 `status=INSUFFICIENT`、`search_status=EMPTY`、`gap="已搜索但未覆盖本轮目标，暂不能可靠断言关键结论。"`，并由 `ensure_unverified_teaching_prefix`（`src/bridges/chat/turn.py:1137-1148`）给回复加"本轮未联网核实："前缀——与三张现场截图逐字吻合。
- 该行为被既有测试有意锁定：`tests/chat/test_issue03_learning_evidence_chat.py:173-206`、`tests/learning/test_issue03_learning_evidence_consistency.py:94-116`。这些测试锁定的是"真不覆盖要诚实"的安全合同，本轮修复误判时必须保留其安全语义、更新其误判前提。
- 次要缝隙：证据收集 `_web_sources()`（`teaching_gate.py:196`）接受 `{"verified","cross_verified","structured"}`，而覆盖裁决只接受 `{"verified","cross_verified"}`，备用结构化来源永远判不覆盖；修复时统一两处集合语义。

## 非目标

- 不引入 embedding、模型语义相关性判定或任何新的外部调用；裁决在公网搜索截止时间之后不得启动新外部请求，公网阶段仍在 8 秒内收敛。
- 不放宽"≥1 条已接受来源"的二元门槛，不引入覆盖率阈值或来源数量配置项。
- 不新增或更换搜索提供方，不改查询脱敏与预算合同（ADR-0020/0025/0026 不变）。
- 真实不覆盖时的"已搜索但未覆盖本轮目标"状态、降级话术与学习进度阻断全部保留，不得借本次修复隐藏真实缺口。
- 不改写历史消息的证据投影、引用与教学结论；新规则只影响新运行。

## Acceptance criteria

- [x] 给定学习目标"学习'卷积神经网络的相关基础知识'并理解其核心机制"，来源集含已抓取核验的中文 CNN 入门页（标题/摘要含"卷积神经网络"）时：至少一条通过覆盖裁决，证据门判充足，教学卡不显示"已降级为模型知识回答"，回复不带"本轮未联网核实："前缀，回复引用可反向绑定到该已接受来源，学习进度按现有规则最多推进一次。
- [x] 同一目标下来源为纯英文已抓取页（含 "Convolutional Neural Network" 或 "CNN"）时：经别名表命中判覆盖；别名表未收录的英文术语来源不命中，不产生意外通过。
- [x] 目标词归一化确定性：从查询碎片"卷积神经网络的相关"派生的匹配键不再包含"的"尾；"…相关基础知识/入门/简介"类模板噪声不参与必需维度。
- [x] 安全合同回归：对 AI Transformer 学习目标，仅讨论电力变压器的已抓取页面仍判 `topic_mismatch`，证据门保持不足、回复保留未核实提示、不推进学习进度（沿用 `tests/learning/test_issue03_learning_evidence_consistency.py` 的反例结构）。
- [x] 真实不覆盖（如搜索返回全部抓取失败或全部主题无关）时：UI 仍准确显示"已搜索但未覆盖本轮目标"，提示只出现一次，不伪造引用，不写学习进度。
- [x] 覆盖规则版本升版（`EVIDENCE_COVERAGE_RULES_VERSION` 从 `learning-evidence-coverage-v1` 升至 v2），版本字符串随裁决结果入审计；旧版本规则可通过配置/常量回滚。
- [x] 证据收集与覆盖裁决对 `verification` 的接受集合语义统一，并在测试中锁定；`structured` 来源的处置（接受或拒绝）有明确注释与测试。
- [x] 裁决输入仍只含脱敏学习目标主题与本轮已安全抓取的有界来源内容，不混入完整会话、画像、账户标识或凭据；耗时与拒绝原因码入审计（脱敏）。
- [x] 多来源混合（部分覆盖、部分无关、部分抓取失败）时判定结果只取决于已接受集合，部分成功搜索卡与教学卡状态不被错误合并。

## Test plan

- 更新两个锁定误判前提的既有测试文件（`test_issue03_learning_evidence_chat.py`、`test_issue03_learning_evidence_consistency.py`）：保留"真不覆盖→诚实缺口"断言，修正其夹具使来源确为不覆盖；新增"覆盖→判充足"正例。
- 覆盖裁决单元矩阵扩充：带"的"尾目标词、CJK 归一化（全半角/大小写/模板噪声）、CNN 中英文别名、别名表外术语不命中、纯英文来源、中英混合来源、多来源部分覆盖。
- 目标词来源断言：必需维度取自学习目标主题而非 `query_summary`；构造查询碎片与目标主题分叉的用例（如目标"卷积神经网络"，碎片最长 token 为"其核心机"）验证取前者。
- 端到端学习模式测试：精确输入"我想学习卷积神经网络的相关基础知识"，以确定性假来源（中文已抓取页）驱动全链路，断言证据门充足、无前缀、引用绑定、进度推进一次；重启与幂等重放不重复推进。
- 前端契约/e2e：区分"证据充足""搜索成功但覆盖不足""搜索失败"三态渲染；`apps/web/e2e/issue23-learning-mode.spec.ts` 等相关用例同步更新。
- 受控时钟验证裁决不开启新的无界外部调用，8 秒公网预算与 120 秒前台终态不变。
- 回归：`tests/learning/`、`tests/chat/` 全量，web_search 与教学门相关套件不得变红。

## Observability & rollback

- 记录脱敏指标：候选来源数、已抓取数、已接受数、拒绝原因码分布（含 `topic_mismatch`）、归一化后必需维度数量、裁决耗时、规则版本；不记录目标正文、来源正文、用户消息或画像。
- `web_search_coverage_insufficient` 结果码保留，用于区分"真不覆盖"；新增维度级拒绝分布便于发现新的系统性误判。
- 回滚路径：规则版本常量切回 v1 即恢复旧裁决；回滚不得改写历史消息、引用、课时或审计。
- 若别名表引发意外通过，支持单独清空/缩减别名表而不影响归一化与目标词修正。

## Blocked by

- None

## Answer

- 2026-08-12：已在 `codex/issue-02-evidence-coverage-topic-adjudication` 分支的独立 worktree 中完成实现。覆盖裁决现在使用稳定学习目标，而不是 `query_summary` 碎片；新增 NFKC/大小写/CJK 模板噪声归一化及显式 `CNN`、`卷积神经网络`、`Convolutional Neural Network` 别名表。
- 保留 Transformer 的三维覆盖语义与字面匹配安全边界；`verified`、`cross_verified`、`structured` 共用可引用来源集合，结构化备用源只使用其有界摘要，不伪造页面抓取时间。
- 规则升至 `learning-evidence-coverage-v2`，别名表版本、候选/抓取/接受计数、拒绝原因和裁决耗时进入脱敏审计；`learning-evidence-coverage-v1` 保留为常量回滚点。OpenAPI 与 TypeScript 契约已同步。
- 已补充覆盖裁决单元矩阵、学习门/聊天正反例、结构化来源和前端三态渲染测试。通过：`tests/learning` 81 项、本次聊天链路 4 项、相关覆盖/搜索/契约套件 41 项、前端 typecheck 和 unit 28 项。
- 定向 Playwright E2E 已尝试启动真实 API/前端，但浏览器测试在启动阶段退出并导致服务清理超时；仓库 `tests/chat` 全量还会触发既有已退役 `legacy.learning_projects.create` 测试（410），两者均非本次实现断言失败。

## Comments

- 2026-08-12：用户选定"目标词修正 + CJK 归一化 + 别名表"方案，否决"仅修截断 bug"与"语义匹配"。上轮冻结决策（未联网核实是真不覆盖时的正确安全状态）继续有效，本 Issue 只修误判。
- 后续讨论、实施证据和验收结果追加于本节。
