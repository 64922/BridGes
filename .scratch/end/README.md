# 三次改进 — Issue 包 README

本目录是「三次改进」反馈的完整执行包：7 个 issue 在 `issues/` 下，调查证据与参考材料在 `references/` 下。反馈原文：`C:\Users\33755\Desktop\vibe coding\三次改进.txt`。

## 根因速览（为什么之前是坏的）

| 反馈 | 根因一句话 |
|------|-----------|
| #1 学习模式拒答 | 联网搜索用的是 DuckDuckGo Instant Answer API，对中文查询结构性返回空；证据门又只认"逐条回抓成功的 verified 来源"并把百科/知乎制度性降级；无兜底，fail-closed 直接拒答 |
| #2 知识库缺陷 | 图片只索引文件名（项目已有的 Qwen OCR 能力未接线）；教学门把图片引用整体丢弃判零命中；日常模式默认不检索知识库；向量检索无相似度阈值等（共 10 项，见 references/report-knowledge-base.md） |
| #3 用户画像空白+看不见字 | 页面 CSS 按深色背景硬编码但落在浅色主题上（标题对比度仅 1.03:1）；生产抽取链路因上游"JSON 模式下 messages 必须含 json 字样"的强制规则确定性 400，重试 3 次耗尽，四维表实际 0 行（数据库实证） |
| #4 人味化不足 | 现有 SKILL 只定义"合同"（事实锁/体裁/输出契约），没有"方法"（怎么写得像人）；且 SKILL.md 运行时不被读取，行为硬编码在 Python 里——只改文档不会有任何效果 |

## Issue 清单

| # | 文件 | 对应反馈 | Blocked by |
|---|------|---------|-----------|
| 01 | [修复学习模式联网搜索通道与证据门降级](issues/01-learning-search-channel.md) | #1 | 无 |
| 02 | [学习模式教学机制改版（混合式）](issues/02-learning-teaching-overhaul.md) | #1 | 01 |
| 03 | [知识库 P0 缺陷修复](issues/03-knowledge-base-p0-fixes.md) | #2 | 无（但与 01/02 有文件重叠，见下） |
| 04 | [知识库图片 OCR 接入](issues/04-knowledge-base-image-ocr.md) | #2 | 无 |
| 05 | [用户画像管线修复（页面+抽取）](issues/05-profile-pipeline-fixes.md) | #3 | 无 |
| 06 | [用户画像机制升级（把握度+证据）](issues/06-profile-mechanism-upgrade.md) | #3 | 05 |
| 07 | [重写 humanizer SKILL 并接入运行时](issues/07-humanizer-skill-revamp.md) | #4 | 无 |

已与用户确认的关键决策：学习模式=混合式（首响一次性全面介绍、废除强制课时、保留轻量进度）；搜索=多查询并行+免费真实端点+聚合站放宽+查询改写重试；兜底=模型知识回答+诚实标注"本轮未联网核实"；画像=四维骨架+认知画像机制（把握度/证据原话/≥2 次稳固/纠错优先）；人味化=重写 SKILL+同步接入 `_build_system_prompt` 与 `global_writing_policy.py` 两处运行时；知识库=P0 先修、OCR 独立。

## 执行建议（并行分组）

三条轨道可**整体并行**，轨道内部严格按序：

- **轨道 A（串行）**：`01 → 02 → 03`
  原因：三者都改 `src/bridges/learning/teaching_gate.py`（01 改门裁决与降级、02 改状态机、03 改图片引用传导），01/02 还同改 `src/bridges/chat/turn.py` 学习模式分支。并行会互相踩。
- **轨道 B（串行）**：`05 → 06`
  原因：06 依赖 05 修复后的可用抽取管线与同一份页面组件。
- **轨道 C（独立）**：`07`
  只碰 `src/bridges/skills/humanizer/` 与 `src/bridges/chat/global_writing_policy.py`，与 A/B 无文件重叠。
- **04 可与任何轨道并行**：只碰 `src/bridges/ingestion/`（parsers/service），与 03 的 retrieval/decision/search 改动不重叠。

推荐排期：A、B、C 三轨同时开，04 挂在任何一条空闲执行资源上。

## 执行环境提示

- 测试命令：`.venv/Scripts/python.exe -m pytest <目标> -q`；改动文件过 `ruff check` 与 `mypy --strict`；前端 `npm run typecheck`（apps/web）。
- **预置失败基线**：`tests/chat/test_chat_attachments.py` 有 3 个用例在 HEAD 即失败（聊天附件上传端点已退役返回 410 `legacy_file_source_retired`，测试未随之更新）。执行各 issue 时不要把它当作自己的回归；建议后续单独立项清理这批过时测试。
- 知识库摄取依赖独立 worker 进程（`bridges worker`）；验证 03/04 的端到端行为时必须先起 worker，否则材料永远停在 queued。
- 画像 400 的最小修复已实弹验证（见 references/report-profile-400-diagnosis.md）：`automatic.py` 的抽取 system prompt 末尾加"以 JSON 输出结果。"即可 200。05 按此执行，不必再实弹打 API。

## 本轮已直接修复（不进 issue）

- **`.markdown` 扩展名前后端不一致**：前端知识库放行 `.markdown` 但后端 `_EXTENSION_TYPES` 只有 `.md`，上传必报"文件类型与扩展名不匹配"。已修 `src/bridges/chat/attachments.py`（补 `.markdown → text/markdown` 映射），并新增 `tests/chat/test_chat_attachments.py::test_sniff_accepts_markdown_extension`（通过；ruff 干净）。

## 安全提醒（请尽快处理）

诊断画像 400 时发现：`C:\Users\33755\Desktop\vibe coding\my_apl.txt` 以明文存放百炼 API key、`BRIDGES_SECRET_KEY`、QQ SMTP 授权码，与项目"秘密不落盘"纪律相悖；且排障过程中这些秘密曾进入本会话的工具输出日志（仅本机）。若你的 CLI 会话会同步云端，建议**轮换这三个秘密**；日常建议改用 `BRIDGES_QWEN_API_KEY_FILE` 指向权限受控的独立文件，而不是明文笔记。

## references/ 内容索引

- `report-learning-mode.md` — 学习模式管线与拒答根因全程调查（file:line 证据）
- `report-knowledge-base.md` — 知识库架构与 10 项缺陷清单
- `report-user-profile.md` — 画像模块双 bug 根因（含数据库实证）
- `report-profile-400-diagnosis.md` — 抽取 400 的实弹确诊（9 步对照实验）
- `report-humanizer.md` — humanizer 现状与"SKILL.md 运行时不生效"的机制说明
- `report-reference-projects.md` — 五个参考项目研读（nuwa/cognitive-profile/三个人味化项目）含适用性筛选
- `deepseek-share-cnn.txt`、`deepseek-share-transformer.txt` — DeepSeek 分享页全文提取（学习模式改版的目标形态参照）
