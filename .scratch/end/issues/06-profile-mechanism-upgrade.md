# 06 — 画像机制升级：把握度与证据、稳固规则、页面改版与召回分寸
Status: claimed
Blocked by: [05](./05-profile-pipeline-fixes.md)
Covered requirements: 三次改进#3

## 背景与根因

Issue 05 修复后管线恢复"能记"，但机制仍是"一句自述即定论"：记录没有把握度、没有证据原话、没有来源消息；画像页只展示内容和时间（`apps/web/src/components/account/profile/FourDimensionProfileCenter.tsx`）；聊天注入侧只有模式白名单与问题相关性过滤（`src/bridges/profiles/four_dimensions.py:43` `_CHAT_MODE_DIMENSIONS`、:768 编译过滤；`src/bridges/profiles/automatic.py:1184-1277`），没有"稳不稳"的概念。现有唯一接近的机制是 `_commit_output` 里"非明确自述的知识兴趣需 90 天内 ≥2 条不同消息观察才提升为记录"（`automatic.py:1125-1135`），只覆盖一个维度的一种情形。这导致两类后果：单句随口一说与反复出现的稳定偏好被同等对待；记录被纠错后系统没有"这条还信不信"的处置，下次照样注入。

架构定调（已与用户确认）：**"四维骨架 + 认知画像机制"**——保留现有四维分区（学业情况/感兴趣的知识/兴趣爱好/阶段目标）与表结构，借鉴 cognitive-profile 给每条记录增加把握度（低/中/高）+ 证据原话 + 来源消息；借鉴 nuwa 的三重验证思想，改造为"跨场景/跨消息出现 ≥2 次才判定为稳固偏好"；证据强度：用户纠错 > 用户确认 > 多轮复现 > 用户自述 > 推测，用户纠错优先级最高；画像召回侧"只用最稳的几条、不暴露内部术语"。本 Issue 落地这套机制。参考项目原文在仓库外只读目录（见 Comments），只借鉴机制思想，代码与页面的全部文案必须自拟，不复制参考项目原文。

## What to build

1. **记录的把握度与证据（schema 演进）**：四维记录新增把握度档位（低/中/高）、证据原话、来源消息引用、最近改动说明，内部保留纠错计数。存量记录迁移给默认档位。新字段进入 API 投影与画像页；"内部审计字段不进投影"的既有约定（`src/bridges/contracts/profiles.py:461-466` docstring）继续保持。
2. **稳固规则与把握度升降**：
   - 同一偏好跨消息/跨场景出现 ≥2 次才判定稳固（高把握的路径之一）；单次自述按证据强度给中或低，推测性信号给低或不落记录。
   - 合并与冲突裁决按证据强度链：用户纠错 > 用户确认 > 多轮复现 > 用户自述 > 推测。
   - 反复纠错量化降级：同一记录第 1 次被纠错正常修正；第 2 次降一档并标注反复纠错；第 3 次降至低、标记"当前不可信"，召回侧停用该条——系统不再猜，改为在合适时机直接向用户确认。
   - 自述标签与行为矛盾时按行为走，不纠正用户的自我认知；用户明确改口（"我现在更喜欢 X"）按替换处理，不按矛盾处理。
3. **召回侧按把握度过滤**：聊天注入只用最稳的几条（高把握优先，条数预算沿用现有上限）；"当前不可信"与低把握记录不注入。注入模型的切片上下文与用户可见的披露文案都不暴露"画像""把握度"等内部术语；用户问"你记了什么"时用日常语言转述要点，不展示内部格式。
4. **画像页面改版**：整齐美观地展示每条记录的把握度、证据原话/来源、最近改动；分区空态友好（说明"聊过相关内容后会出现在这里"类口径，而不是干巴巴一句"暂无记录"）；沿用 Issue 05 的 token 化样式，深浅主题对比度均达标。
5. **隐私边界**：聊天中说"不要记录"即停（全局停止产生新观察与新记录）；"这个不用记"只停该内容（局部限制）；用户要求删除即真删——记录连同其观察从表中物理删除，不留副本、不进切片、不复活。

## Implementation notes

1. **迁移**（`src/bridges/storage/database.py`）：`SCHEMA_VERSION` 42→43（:23），`MIGRATIONS`（:26 起）新增 v43 脚本，对 `profile_four_dimension_records`（表定义 :1688-1704）`ALTER TABLE` 增加：`confidence TEXT NOT NULL DEFAULT 'low'`（low/medium/high 三档，合同层校验枚举）、`evidence_quote TEXT`（证据原话，可空）、`evidence_message_id TEXT`（来源消息，可空）、`correction_count INTEGER NOT NULL DEFAULT 0`、`change_note TEXT`（最近改动说明，可空）。存量行默认 low，change_note 标注迁移来源。账户级删除目录（`src/bridges/lifecycle/catalog.py:55-80`，画像各表见 :65-73）只列表名，新增列无需改动。
2. **合同与 API**（`src/bridges/contracts/profiles.py`）：`FourDimensionProfileRecord`（:428-458）与 `FourDimensionProfileProjection`（:461-476）增加把握度、证据原话、来源消息、最近改动字段（correction_count 属内部字段，不进投影）；`src/bridges/profiles/four_dimensions.py` 的记录模型、仓储 SQL（INSERT :417 起、SELECT :449-472）与 `FourDimensionProfileService`（:736 起）同步。改完运行 `scripts/regenerate_openapi.py` 再生成契约；前端 `apps/web/src/lib/api.ts:147-152` 的类型取自 `components["schemas"]`，随契约更新。
3. **写入侧**（`src/bridges/profiles/automatic.py`）：`_commit_output`（:1084 起）落地新字段——观察表已存 evidence_ref/reliability（合同见 `src/bridges/contracts/profile_extraction.py:90-101`），CREATE/UPDATE 记录时把证据原话（从 run 的 `source_snapshot` 截取用户原话，注意截断长度与脱敏）与来源消息 id 一并写入。把 :1125-1135 的"≥2 观察提升"从单一维度特例推广为全维度稳固规则：首次明确自述 → 中把握；跨消息复现（沿用 :1128-1135 的观察计数思路，窗口口径在代码注释写明）→ 升档；证据强度链决定初始档位与冲突取舍。
4. **纠错与降级**（`src/bridges/profiles/four_dimensions.py` 修改/撤回路径 :879-918 附近；聊天纠错经 `src/bridges/chat/service.py:1153` `_process_profile_effects` 进入）：实现 `correction_count` 递增与降档规则（1 次正常改、2 次降一档并标注、≥3 次置低 + "当前不可信"）；用户在画像页的手工修改视为用户纠错（最高证据强度）；被标"当前不可信"的记录在切片编译时排除，change_note 记录每次升降原因。
5. **召回侧**：`automatic.py:1184-1277` `compile_chat_slice` 与 `four_dimensions.py:768` 附近的编译路径加把握度过滤（高优先，中可在预算内兜底，低/不可信不注入；条数上限沿用 `automatic.py:52` `_MAX_SLICE_ITEMS = 6`）。`src/bridges/chat/turn.py:940-958` `profile_slice_context` 的注入文案保持"用户已授权信息"口径，并在给模型的指令中约束回复不复述内部术语；用户可见披露文案（`turn.py:4902-4913` READY/EMPTY、:4773-4799 OFF）复查不含内部术语。组装顺序与 `assemble_payload`（`turn.py:1196` 起）不变。
6. **页面**（`FourDimensionProfileCenter.tsx` + `.module.css`）：每条记录展示把握度档位（中文低/中/高）、证据原话（引用样式）、来源（"来自某次对话"级口径即可，本期不做消息跳转）、最近改动（change_note + 时间，参考 `first_stable_recorded_at` 的现有格式化 `FourDimensionProfileCenter.tsx:22-28`）；四个分区（:15-20）空态各写一句友好说明。样式只用设计 token，过对比度。
7. **隐私**：记忆意图已有确定性处理（`service.py:1163-1169` docstring 所述 Issue 26 机制，测试在 `tests/profiles/test_memory_intent.py`），本 Issue 对齐两点："不要记录"之后不再产生新观察与新记录（含重试队列中的任务不再复活该消息）；新增真删路径——记录 + 其观察物理删除，区别于现有 WITHDRAWN 墓碑（`four_dimensions.py:918` 附近），画像页的删除入口改走真删；账户级删除仍由 `catalog.py` 目录兜底。注意不要破坏 `profile_extraction_tombstones` 等既有表语义。
8. **测试**（`tests/profiles/`）：新增——迁移 v42→v43 保留存量行与默认档位；首次自述落中把握、跨消息复现升档；纠错 1/2/3 次的升降与"当前不可信"停用；切片编译排除低把握与不可信记录；注入文案与披露文案不含"画像/把握度"等术语（断言 prompt 与 note 文本）；"不要记录"后无新观察；真删后表与 API 均无该记录。e2e 按需更新 `apps/web/e2e/issue25-profile-center.spec.ts`（页面结构变化）。

## Acceptance criteria

- [ ] v43 迁移后记录含把握度/证据原话/来源消息/最近改动字段；存量记录完整保留并有默认档位。
- [ ] "我想学习 X"说一次：落为中把握（或实现口径中的非稳固档）；在另一条消息/另一会话中再次表达同一偏好：升为稳固（高）。
- [ ] 用户纠错后记录内容被更新；同一记录第 2 次纠错降档并标注，第 3 次后不再注入聊天切片，系统转为直接向用户确认而非继续猜。
- [ ] 聊天注入只含稳固记录；注入模型的文案与用户可见披露均不出现"画像""把握度"等内部术语（有测试断言）。
- [ ] 画像页每条记录展示把握度、证据原话/来源、最近改动；空分区有友好空态；深浅主题对比度达标。
- [ ] 聊天中说"不要记录"后不再产生新观察/新记录；用户删除记录后，数据库表与 API 中均不存在该记录（真删，非墓碑）。
- [ ] `pytest tests/profiles/` 全绿（含新增）；`npm run typecheck` 通过；openapi 契约已再生成且前后端类型一致。

## Verification

- `pytest tests/profiles/`（含新增机制测试与迁移测试）；改动文件过 `mypy --strict`、`ruff`。
- 运行 `scripts/regenerate_openapi.py` 再生成契约；`cd apps/web && npm run typecheck`。
- `python scripts/check_contrast.py` 继续全过，页面改版新增配色按同算法复核（深浅两主题）。
- e2e：`issue25-profile-center.spec.ts`、`issue26-profile-candidates-permissions.spec.ts`、`issue27-profile-slices-disclosure-feedback.spec.ts` 按需更新后通过。
- 手工走查：两条种子消息 → 页面出现带把握度的记录；换一条消息再说一次同类偏好 → 升档；连续纠错三次 → 降档、停用并向用户确认；说"不要记录" → 停止累积；删除 → 复查 DB 确认真删；聊天回复全程无内部术语。

## Non-goals

- 不做定时/周期校准任务（cron 式回看），不做"材料厚度"展示，不做六区画像结构（保持四维骨架）。
- 不做人格标签/MBTI 式分类，不做表达 DNA 六维建模与保真度评分卡（nuwa 的厚建模部分本期不取）。
- 不做来源消息跳转（本期来源只到"哪次对话"口径），不做跨账户画像共享。
- 不改聊天组装顺序与模式白名单结构，不新增四维以外的维度，不动 Issue 05 已修的抽取管线本身。

## Blocked by

- [Issue 05：修复用户画像管线](./05-profile-pipeline-fixes.md)——管线不通时本机制没有可作用的数据。

## Comments

参考项目原文（仓库外只读）：`C:/Users/33755/Desktop/参考资料/cognitive-profile-main`（SKILL.md 四步流程；`references/采集证据.md` 的最小观察格式"原话/看到的/影响/把握"与"不要留下"清单；`references/形成画像.md` 的把握度升降、自述标签与行为矛盾按行为走、反复纠错量化降级；`references/召回问答.md` 的"只用最稳的几条、不暴露内部术语、用户问记了什么用日常语言转述"；`references/校准整理.md` 的证据强度排序链）；`C:/Users/33755/Desktop/参考资料/nuwa-skill-main/references/extraction-framework.md`（三重验证——本项目只取"跨域复现"一重，改造为跨消息/跨场景 ≥2 次稳固规则）。机制思想借鉴，落地文案全部自拟。

研读报告已归档 `.scratch/end/references/report-reference-projects.md`（"第一组：用户画像参考"部分）。与 Issue 07（表达策略）无重叠：本 Issue 管"记住什么、多确定、怎么用"，不管"怎么说得自然"。
