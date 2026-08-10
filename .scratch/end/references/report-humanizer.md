调查完成。以下是报告。

---

# BridGes skills 体系调查报告（重点：humanizer）

## 1. humanizer SKILL.md 分析

文件：`src/bridges/skills/humanizer/skill/SKILL.md`（76 行）

**内容结构**（无 YAML frontmatter，用 bullet 列表做元信息）：
- 头部元信息：标识 `bridges-humanizer`、版本 1.0.0、来源（净室，指向 `CLEAN_ROOM.md`）、许可证
- `## 用途`：两条端到端路径——改写路径（先提取任务契约与事实锁再改写）与生成路径（收集主题/受众/体裁/渠道/硬约束后生成）
- `## 边界（不可逾越）`：不规避 AI 检测、不冒充真人、不虚构引用、不以牺牲事实换口语化、净室零复用
- `## 证据边界`：六类事实锁（数值/单位、限定条件、结论强度、公式、引用、对象关系）及各自冲突分级
- `## 输出合同`：固定五项（`final_text`/`edits`/`fact_check`/`open_questions` 等）
- `## 体裁合同`：四体裁指向 `genres/*.md`
- `## 编排`：5 步流水线（解析契约 → 事实锁 → 组装指令 → 确定性复核 → 终态 done/needs_human）
- `## 失败恢复`：过程卡五态

**覆盖的"人味化方法"**：严格说它覆盖的不是方法而是**合同**——事实锁分类法、体裁规则（经 genres/*.md 给出"必含/禁止"可检测项，如科普文案要求类比+类比边界句）、输出契约、复核流水线。所有"方法"都表达为可测试断言，而非写作技巧。

**明显短板**：
- **没有教模型"怎么写得像人"**：全文无具体改写技巧（句式节奏、去模板腔词表、before/after 示例）。自然度仅靠体裁规则的正则标记间接约束，实际系统提示词（见下）同样只有规则清单没有方法指导。
- **无 frontmatter**：头部是松散 bullet 列表，不符合本仓库自己的插件 SKILL.md 约定（`checker.py` 要求 `---` frontmatter + `plugin_id`/`name`/`version`）。
- **文档与代码可能漂移**：SKILL.md 本体运行时无人读取，也无测试交叉校验（只有 `genres/*.md` 有）；Issue 07 引入的两级质量门（硬门/软门 + 一次定向修复 + DRAFT 事件）未回写进 `## 编排` 一节。
- 编排第 5 步只写了 done/needs_human 两终态，与代码中 warn 交付（quality_status=WARN）不一致。

## 2. `src/bridges/skills/` 目录清单

只有一个 skill：

- `humanizer/` — 内置只读"文章人味化"SKILL（Issue 28）。Python 编排（`service.py` 编排服务、`intent.py` 自然语言路由、`factlock.py` 七类事实锁引擎、`genre_rules.py` 四体裁规则）+ `skill/` 资产目录（SKILL.md、CLEAN_ROOM.md、4 个体裁合同、3 个测试/评测语料 fixture）。
- `registry.py` — 内置 SKILL 注册表（线程安全内存表，启动时注册 `bridges-humanizer` 清单）。
- `__init__.py` — 导出注册表（ADR 0010/0011）。

## 3. 加载与触发机制（关键发现）

**SKILL.md 本体不被任何运行时代码读取**——全仓 grep 确认。它仅是审计/治理文档。运行时机制如下：

- **注册**：`create_builtin_registry()`（`src/bridges/skills/registry.py:101`）在应用启动时注册固定清单；`HumanizerService` 在 `src/bridges/api/main.py:1189` 装配，`registry.get(skill_id)` 在 `service.py:193`（`resolve_skill`）校验标识与固定版本。
- **系统提示词不是从 SKILL.md 拼的**，而是在 Python 中硬编码组装：`HumanizerService._build_system_prompt`（`service.py:682-722`）——版本号 + 任务边界 + 体裁必含/禁止（来自 `genre_rules.py` 的 Python 常量 `GenreRuleSet`）+ 事实锁清单 + 证据合同，再配 `_OUTPUT_JSON_SCHEMA`（`service.py:67`）走结构化输出（能力名 `qwen_structured_output`，`service.py:63`，temperature 0.4，经 `ModelGateway.invoke`）。
- **markdown 资产只有体裁文件被代码碰**：`skill_doc()`（`genre_rules.py:261-267`）读 `skill/genres/*.md`，但**仅被测试调用**（`tests/humanizer/test_genre_rules.py:91`）做文档与规则交叉校验，不进提示词。
- **触发路径有两条**：
  1. 自然语言路由：`route_humanizer_message()`（`intent.py:74`，纯正则意图识别，宁可漏判），由 `ChatService._apply_natural_language_humanizer_route`（`src/bridges/chat/service.py:841`，调用点 `:864`）在消息无显式载荷时尝试；统一主路由判为 CLARIFY 的复合任务会让路（`service.py:869`）。
  2. 显式 SKILL 载荷：`chat/turn.py:1551-1603` 检测 `skill_id == "bridges-humanizer"` 进入 `_stream_humanizer`（`turn.py:3257`），在 `turn.py:3477` 调 `self._humanizer.run_task(...)` 消费事件流（PROCESS/DRAFT/RESULT），过程卡五态经 SSE 下发。
- **执行**：`HumanizerService.run_task`（`service.py:203`）——解析来源（粘贴文本/知识库材料，`_resolve_source` :388）→ 提取事实锁（`factlock.extract_locks`）→ 模型生成 → 立即发 DRAFT 事件（:299）→ 确定性复核（`_review_checks` :801：事实锁前后比较、体裁规则、引用保持、合同完整性）→ 两级质量门：硬门（事实锁冲突）停止交付；软门（体裁未过）至多一次有预算定向修复（`_repair_once` :883），仍不过则带警告交付。

另有一条独立机制：**全局轻量表达策略**（`src/bridges/chat/global_writing_policy.py`，版本 `global-humanized-writing-v1`）把一小段"有人味"表达合同注入**所有**主聊天生成的 system block，但明确排除 humanizer 任务的最终文章（不二次改写）。

## 4. 风格参照

全仓只有这一个 SKILL.md（glob 确认）。可参照的约定来源：

- **插件 SKILL.md 规范样例**：`tests/plugins/zip_builder.py:12-31`（`VALID_SKILL_MD`）——`---` YAML frontmatter（`plugin_id`/`name`/`version`/`description`/`source`/`license` 标量 + `capabilities`/`data_categories` 列表）+ markdown 正文（可含包内相对链接）。强制校验在 `src/bridges/plugins/checker.py:155`（`_parse_frontmatter`）与 `:248-264`（必填 plugin_id/name/version）。
- **MCP.yaml**（`src/bridges/mcp/manifest.py:1-9`）采用同一扁平 frontmatter 风格。
- **仓库内写得最好的合同文档**是 humanizer 自己的 `skill/genres/popular_science.md`：章节惯例为"必含（缺失即复核不通过）/ 允许省略 / 禁止 / 保留（事实锁）"，每条规则附"可检测：出现「好比/就像」类标记"的检测锚点——文档与断言一一对应，这是该项目 SKILL 文档的核心惯例。

## 5. 写作/文章生成功能入口

没有独立"写作模式"页面，全部收敛在聊天内：

- **前端**：「+」菜单"文章人味化"（`apps/web/src/lib/chat-tools.ts:18`）；空白态建议卡预填 prompt（`apps/web/src/app/templates/chat/chat-template.tsx:284`）；过程卡 `HumanizerProcessCard.tsx`、结果卡 `HumanizerResultCard.tsx`（五要素 + 事实锁/引用/体裁复核展示）。
- **后端生成路径**：`HumanizerTaskContract(path=GENERATE, topic/audience/genre/channel/hard_constraints)`，在 `service.py:404-410`（`_resolve_source`）校验主题非空；自然语言路由目前只编译 REWRITE 路径（`intent.py:89`），生成路径走显式载荷。
- 相关的轻量写作策略：`global_writing_policy.py`（见上）。

**一句话结论**：这个"skill 体系"实质是**单 skill + 硬编码编排**——SKILL.md 是治理/审计文档而非运行时提示词载体；真正的行为由 `service.py`/`genre_rules.py`/`factlock.py` 的 Python 常量与正则决定，SKILL.md 的改进若想生效必须同步改代码（或先把文档接进提示词组装）。