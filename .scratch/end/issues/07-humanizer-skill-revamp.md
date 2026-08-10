# 07 — 重写 humanizer SKILL：补方法体系、接入两处运行时、升级全局表达策略
Status: ready-for-agent
Blocked by: 无
Covered requirements: 三次改进#4

## 背景与根因

当前 `src/bridges/skills/humanizer/skill/SKILL.md`（76 行）只覆盖"合同"不覆盖"方法"。它定义了事实锁六类（数值/单位、限定条件、结论强度、公式、引用、对象关系）、输出合同五要素、四体裁合同与编排流水线，但全文没有任何"怎么写得像人"的正向写作方法——无句式规则、无 AI 腔模式清单、无改写前后对照。结果是系统有完备的"事实不漂移"护栏，却没有"自然可信有分寸"的表达指导。

运行时与文档存在结构性漂移。经全仓核实，SKILL.md 本体不被任何运行时代码读取，仅是治理/审计文档；真正生效的系统提示词在 Python 中硬编码组装：`HumanizerService._build_system_prompt`（`src/bridges/skills/humanizer/service.py:682-722`）只拼接版本号、任务边界、体裁必含/禁止（来自 `genre_rules.py` 的 Python 常量）、事实锁清单与证据合同，同样没有方法层内容。另一处运行时是全局轻量表达策略 `src/bridges/chat/global_writing_policy.py`（版本 `global-humanized-writing-v1`，指令常量见 :33-38），注入所有主聊天生成的 system block，内容仅一段笼统的"清楚、具体、少空话"。漂移的具体证据：既有改进引入的两级质量门（硬门停止交付、软门一次定向修复 `_repair_once`（`service.py:883`）、带警告交付 WARN）未回写 SKILL.md 的"## 编排"一节；编排第 5 步仍只写 done/needs_human 两终态。

第三个短板是无 frontmatter。SKILL.md 头部是松散 bullet 列表，不符合本仓库自己的 SKILL.md 约定：`src/bridges/plugins/checker.py:155`（`_parse_frontmatter`）要求 `---` YAML frontmatter，:248-264 强制 `plugin_id`/`name`/`version` 必填，合法样例见 `tests/plugins/zip_builder.py:12-31`（`VALID_SKILL_MD`）。humanizer 虽是内置 SKILL 不走插件 zip 校验，但头部约定应统一，便于治理与未来校验接入。

根因一句话：SKILL 停留在"可测试的合同"，缺"可操作的方法"；且文档与代码各自演进、无交叉校验，导致任何文档改进不落地。本 Issue 因此要求"文档 + 代码同步改"，并新增一致性校验防止再次漂移。

参考素材：三个参考项目原文在仓库外只读目录 `C:/Users/33755/Desktop/参考资料/`（`Humanizer-zh-main`、`human-writing-main`、`scientific-humanization-main`）。三者互补：Humanizer-zh 提供 24 类 AI 痕迹模式库（检测面最全）；human-writing 提供中文句法与节奏的正向写作观（改写面最深）；scientific-humanization 提供事实锁与 claim 强度降级（事实护栏最强）。净室约束不变：方法体系必须用本项目自己的语言重写，禁止复制参考项目整段文字（见 `skill/CLEAN_ROOM.md`）。

## What to build

重写 `src/bridges/skills/humanizer/skill/SKILL.md`，在保留现有合同层（边界、证据边界、输出合同、体裁合同、编排、失败恢复）的基础上，新增原创表述的方法体系，并同步接入两处运行时。建议的方法体系结构如下（标题与措辞均由实现者自拟，以下是要点而非文案）：

1. **检测面（AI 腔模式清单）**：从 24 类模式中筛选适合聊天与文章场景的子集，每条给出"可检测锚点 + 改写前后对照"（沿用 `skill/genres/popular_science.md` 的"可检测：…"惯例）。应收录：协作痕迹（"希望这对您有帮助"类收尾）、谄媚开场（"好问题"类）、填充短语、通用积极结论、AI 高频词（"此外""至关重要""深入探讨"类）、否定式排比与三段式滥用、同义词循环、虚假范围（"从 X 到 Y"）、夸大意义（"标志着""见证了"类）、模糊归因（"专家认为"）、过度限定、宣传腔。注明中文不适用项（如英文标题大写规则）不收录。
2. **改写面（中文句法与节奏规则）**：主干先行（先谁做了什么，再补条件）；顺势接话（前句尾巴交给后句）；长定语拆小句；连词删半；动词名词化还原（"进行了优化"→"改顺了"类）；允许正常重复，不强行换同义词；长短句拉开节奏；逗号句号分工；判断从正面下——禁"不是 A 而是 B""看似 A 实则 B"类翻案动作（禁动作而非禁字面，换皮仍算命中）；不写抽象名词配具体动词的抒情。
3. **事实护栏面**：沿用现有六类事实锁（`factlock.py`）不动，新增 claim 强度降级规则：相关不等于因果（写"提示/可能有关"，不写"证明/导致"）；无统计检验不写"显著"；预测准确不等于因果成立；指标提升不等于真实场景有效；样本内不等于外推成立；"关键因子/首次证明/系统揭示"类强词证据不足时降级。工作顺序明确为"先锁事实 → 先改论证顺序（做了什么 → 看到什么 → 能说明什么 → 还不能说明什么）再改句子 → 改后回读核查"。
4. **分场景档位**：聊天回复、文章改写、文章生成三档，各列允许与禁止的修辞动作。聊天档：破折号、提示性冒号、三项排比等长文硬禁令降级为**频率告警**（偶发允许、成段出现才算问题），允许适度结构化（加粗关键步骤、短列表），材料门槛改为"每次讲解至少落到一个具体例子或一道题"。文章改写/生成档：维持体裁合同的"必含/禁止"硬门，新方法规则作为改写指导进入提示词。
5. **个性与分寸**：有明确观点并把依据放在附近；承认复杂性与不确定；不谄媚、不替用户编造经历；协作痕迹清零；知识边界诚实但以一次性自然说明表达而非免责声明模板；去助手腔（讲稿场景默认讲者本人在讲）；不伪造精确时间、天气、神态等"假细节"。

**创新点（至少落地第一个，其余鼓励实现）**：

- **人味分档**：识别用户赶时间/求快信号（"快点""直接说""赶时间"及画像中的对应偏好）时自动收敛修饰，只保留事实护栏与清晰直答；从容场景才放开节奏与个性。三档场景表需给出该信号的口径。
- **教学人味规则**：学习模式下每次讲解至少落到一个具体例子或一道题，杜绝纯抽象解释连发——这是对长文"材料门槛"的聊天化改造，本项目首创。
- **文档-代码一致性校验**：新增测试把 SKILL.md 方法条目与运行时提示词常量交叉校验（仿 `tests/humanizer/test_genre_rules.py:91` 对 `genre_rules.skill_doc()`（`genre_rules.py:262-267`）的校验方式），从机制上消灭文档与代码再次漂移的可能。

**不采纳清单（附理由，写进 SKILL.md 设计说明或本 Issue 评论均可）**：

- human-writing 的"五件材料门槛 / 一千二百字规则 / 材料不足就缩短或停笔"：长文创作工序，聊天是多轮短消息，已按上文改造为教学人味规则。
- 破折号、冒号仅引原话、三项排比"命中即失败"：聊天中冒号列选项、适度排比有助于扫读，聊天档降级为频率告警；文章档是否保留为体裁规则由体裁合同决定，不做全局硬禁。
- 粗体、emoji、标题类禁令"清零"：学习界面适量结构化对学生有用，按场景定阈值而非清零。
- "允许跑题/半成型想法/允许混乱"的个性注入：学习对话中克制使用，不能影响知识主线，赶时间时完全关闭。
- 知识截止免责声明整段删除：在学习产品中部分合理（诚实边界），改为一次性自然说明。
- human-writing 改稿七遍法与 `check_prose.py` 逐条拦截：属成稿后工序，聊天是实时生成；硬规则前置为生成时约束（system prompt），检测脚本思路只用于离线评测语料，不做逐条回复拦截。
- scientific-humanization 的论文/基金/投资人场景改法：与学习聊天无关，只取"讲稿/答辩"两档（先判断后证据、先答问题再说依据与限制）。
- Humanizer-zh 五维质量评分直接进运行时：作为离线评测口径参考即可，不增加运行时复杂度。

## Implementation notes

1. **重写 SKILL.md**（`src/bridges/skills/humanizer/skill/SKILL.md`）：补齐 `---` YAML frontmatter（`plugin_id: bridges-humanizer`、`name`、`version`、`description`、`source`、`license` 标量 + `capabilities`、`data_categories` 列表，字段约定参照 `tests/plugins/zip_builder.py:12-31` 与 `src/bridges/plugins/checker.py:155`、:248-264）。正文新增"方法体系"五面 + 三场景档位 + 创新规则，全部原创表述。同步更新 `skill/CLEAN_ROOM.md`：记录"研读三个参考项目的方法思想、零文本复用"的来源清洁声明。回写"## 编排"一节：补齐两级质量门（硬门/软门 + 一次定向修复 + WARN 交付）与 DRAFT 事件，使文档与 `service.py:203`（`run_task`）实际行为一致。
2. **提示词组装改造**（`service.py:682-722` `_build_system_prompt`）：在任务边界与体裁合同之后注入方法块（检测清单、句法规则、claim 降级、场景档位）。方法规则文本集中为一个 Python 常量模块（建议新增 `src/bridges/skills/humanizer/method_rules.py`），作为单一事实源供 `service.py` 与 `global_writing_policy.py` 共同引用，禁止两处各自复制文案。改写路径与生成路径（`service.py:388` `_resolve_source`、:404-410 主题校验）共用同一方法块，按 `contract.path` 与体裁档位裁剪。
3. **全局表达策略升级**（`src/bridges/chat/global_writing_policy.py`）：把聊天档方法规则并入 `GlobalWritingPolicyResource.instruction`（:33-38），版本号从 `global-humanized-writing-v1`（:20）升至 v2；保留保护区正则（:221-229）、"文章人味化任务的最终文章不经过本策略二次改写"的排除条款（:214-215）与安全基线降级路径（`_fallback_snapshot` :165-186），baseline 文案同步对齐。
4. **一致性校验**：新增测试（放 `tests/humanizer/`）断言：SKILL.md 的每条方法规则在 `method_rules` 常量中有对应项（反之亦然）；`_build_system_prompt` 产物包含方法块关键标识；`GlobalWritingPolicyCompiler.compile` 产物的 `system_block` 包含聊天档规则。与 `genre_rules` 必含/禁止、`factlock` 六类锁的现有交叉校验并存。
5. **不破坏现有合同**：`_OUTPUT_JSON_SCHEMA`（`service.py:67-108`）、输出合同五要素、确定性复核 `_review_checks`（:801）、一次定向修复 `_repair_once`（:883）、两级质量门、过程卡五态、SSE 事件流（`chat/turn.py:3257` `_stream_humanizer`、:3477 `run_task` 消费点）全部保持行为不变。自然语言路由（`intent.py:74` `route_humanizer_message`，目前只编译 REWRITE 路径 :89）与显式载荷入口（`chat/turn.py:1551-1603`）不在本次改动范围。
6. **防复制自查**：实现完成后，将新 SKILL.md 与三个参考项目原文做整段重合比对（人工 + 简单脚本），确认无整段复制；改写前后对照示例必须自拟。

## Acceptance criteria

- [ ] 新 SKILL.md 含合规 frontmatter，正文含检测面、改写面、事实护栏面、分场景档位、个性与分寸五部分，以及至少"人味分档"创新规则；全文原创表述，自查确认不含从三个参考项目复制的整段文字。
- [ ] SKILL.md 编排章节与 `service.py` 实际行为（两级质量门、WARN 交付、DRAFT 事件）一致。
- [ ] 文章人味化改写任务与生成任务的运行时系统提示词均包含新方法规则块，并有测试断言提示词包含关键规则标识。
- [ ] 全局聊天回复受升级后的表达策略约束：`global_writing_policy.py` 版本号升级，`system_block` 包含聊天档方法规则（含频率告警口径），受保护区与 humanizer 最终文章排除条款仍生效。
- [ ] 新增 SKILL.md ↔ 方法规则常量 ↔ 提示词组装的交叉校验测试，防文档与代码再次漂移。
- [ ] 现有 `tests/humanizer/` 全部通过；聊天全局策略相关测试按需更新并通过；新增测试覆盖上述断言。
- [ ] 输出合同五要素、事实锁复核、两级质量门的现有行为不变（回归测试通过）；`mypy --strict` 与 `ruff` 对改动文件干净。

## Verification

- 运行 `tests/humanizer/` 全量与聊天全局策略相关测试（含新增交叉校验测试），并跑一遍相关回归子集确认输出合同与复核流水线不变。
- `mypy --strict` 与 `ruff` 检查改动文件。
- 新 SKILL.md 与三个参考项目原文做整段重合自查（净室）。
- 真实冒烟：各跑一次文章人味化改写任务与生成任务，外加若干普通聊天回复，人工确认表达自然度提升、事实锁无误报、聊天回复无谄媚开场与协作痕迹。

## Non-goals

- 不改输出合同 JSON Schema、不改过程卡五态与 SSE 事件协议、不动前端组件。
- 不引入逐条回复拦截式检测脚本；方法规则只进提示词与离线评测。
- 不做用户画像建模与表达 DNA 抽取（第一组参考项目属另一 Issue 范围）。
- 不扩展自然语言路由到 GENERATE 路径，不改触发机制。
- 不以"通过 AI 检测"为目标或验收标准。

## Blocked by

无。

## Comments

参考项目原文位置：`C:/Users/33755/Desktop/参考资料/Humanizer-zh-main`（24 类模式库 + 5 条核心规则 + 个性注入章节）、`C:/Users/33755/Desktop/参考资料/human-writing-main`（材料门槛、说话位置五问、中文句法规则、按动作禁的硬禁令、`scripts/check_prose.py`、`dist/human-writing-lite.md`）、`C:/Users/33755/Desktop/参考资料/scientific-humanization-main`（工作顺序、Claim Strength 降级表、Human Voice、Scene Moves）。两份前期调查报告存于会话任务日志（agent-cnx5z4cd：humanizer 现状调查；agent-skwez50e：参考项目研读，"第二组：人味化参考"部分）。

本 Issue 的最大风险是"只改文档不改代码"：SKILL.md 运行时不被读取，方法体系若不接入 `_build_system_prompt` 与 `global_writing_policy.py` 就不会产生任何用户可见效果。实现时务必两处运行时同步改造，并用交叉校验测试锁定三者一致。
