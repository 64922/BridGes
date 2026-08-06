# 40 — 建立可复现 A/B 科学评测
Status: completed
Blocked by: 23, 27, 28, 29, 30, 31, 32, 33, 39
Covered requirements: MODEL-01, MODEL-03, IMP-01, IMP-02, A-01, A-02, B-01, BONUS-01, BONUS-02, BONUS-03, SCORE-01, SCORE-02, SCORE-03, DESKTOP-01
ADRs: [0006](../../../docs/adr/0006-one-model-snapshot-per-capability.md), [0007](../../../docs/adr/0007-wan-video-generation-exception.md), [0009](../../../docs/adr/0009-fixed-model-and-provider-matrix.md), [0011](../../../docs/adr/0011-clean-room-humanizer-and-license-boundary.md), [0015](../../../docs/adr/0015-minimum-cloud-disclosure.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

建立一套可在同一环境中重放的 BridGes A/B 科学评测，锁定数据集、模型快照、提示、工作流、SKILL、工具、随机种子、运行次数和评分版本。评测同时覆盖 A 数字分身画像闭环、B 原创人味表达、科学事实准确与低幻觉、风险识别、因材施教、生涯规划、ASR/TTS、图片、视频和提醒等首版关键能力，并与基础 Qwen、合法开源参考方法及必要消融进行公平对比。结果采用自动指标与盲评结合，发布可复现报告、置信区间、失败案例和剩余风险；没有证据时不得宣称全面优于基线。

## Acceptance criteria

- [x] 版本化评测包具有固定数据清单、授权与许可证记录、任务定义、运行矩阵、模型与 SKILL 版本、随机种子、评分量表和预期产物 Schema。
- [x] 数据覆盖多轮画像记录—调用—回答—反馈—修正闭环，并量化画像正确性、越界写入、后续个性化收益、自然度和跨轮稳定性。
- [x] 人味评测覆盖科普文案、课程讲稿、科研汇报和论文写作，测量模板腔、机翻感、AI 味、任务适配度和事实不变性。
- [x] 科学评测测量事实准确、引用支持、校准、主题完整一致、幻觉和证据冲突处理，并单列高风险失败案例。
- [x] 教学评测覆盖先备诊断、步骤规划、适当测验、知识库不足时强制联网及学习结果，不以对话长度代替学习增益。
- [x] 生涯规划和陪伴评测验证事实/假设区分、风险信号边界、敏感推断抑制和有分寸表达，不把情绪识别当心理诊断。
- [x] 多模态评测覆盖 ASR 转写、TTS 可懂度、图片/视频提示遵循、资产可用性、替代说明、失败恢复和固定模型合同。
- [x] 对比至少包含完整 BridGes、基础 Qwen 和合法开源参考方法，并包含移除画像切片、移除 bridges-humanizer、移除证据检索等可解释消融。
- [x] 盲评隐藏系统身份并随机化顺序，记录评审一致性和分歧；自动裁判不能成为唯一结论来源。
- [x] 报告展示样本量、点估计、区间、显著性或不确定性、成本、时延、失败率、逐切片结果和代表性失败案例。
- [x] 任一固定快照、数据或评分逻辑变化都会生成新的运行锁和报告版本，旧结果仍可追溯且不会被静默覆盖。

## Verification

- [x] 在干净环境中连续运行两次固定评测，比较运行锁、样本集合、结构化产物和允许随机范围内的指标差异。
- [x] 对故意注入的事实错误、画像越界、模板腔、风险误判、教学跳步和多模态失败验证指标能检出回归。
- [x] 检查所有外部参考的许可证、来源和使用范围，确认未复制无许可证项目的文本、结构或示例。
- [x] 由不知道系统身份的评审者完成配对盲评，并对低一致性项目执行复核而非强行合并。
- [x] 从报告中的失败案例追溯到固定输入、运行锁、工具记录和输出，证明可一键重放同一案例。
- [x] 将发布阈值接入最终发布门，完整系统未达到最低事实、安全和稳定性阈值时阻止发行。

## Non-goals

- 不用单一主观分数证明产品“更有人味”或“更懂用户”，也不隐藏受损用户切片。
- 不收集真实用户私人聊天作为默认评测数据，不为追逐公开榜单泄露本地数据。
- 不把模型自评或同源自动裁判作为唯一证据。
- 不评测手机、平板、PWA、移动浏览器或原生应用体验。

## Blocked by

- [23 — 交付学习模式教学门](./23-deliver-learning-mode-teaching-gate.md)
- [27 — 交付画像切片披露与反馈闭环](./27-deliver-profile-slices-disclosure-and-feedback-loop.md)
- [28 — 交付净室原创人味化 SKILL](./28-deliver-clean-room-humanizer-skill.md)
- [29 — 交付生涯规划助手](./29-deliver-career-planning-assistant.md)
- [30 — 交付听写与单条回答朗读](./30-deliver-asr-dictation-and-tts-readaloud.md)
- [31 — 交付图片生成与编辑](./31-deliver-image-generation-and-editing.md)
- [32 — 交付视频生成](./32-deliver-video-generation.md)
- [33 — 交付 QQ SMTP 任务提醒](./33-deliver-qq-smtp-reminders.md)
- [39 — 加固安全、隐私与账户隔离](./39-harden-security-privacy-and-account-isolation.md)

## Comments

- 2026-08-01：按已批准的 BridGes 改进计划发布。
- 2026-08-06：完成并提交。交付内容：`src/bridges/evaluation/` 新增评测套件
  注册表、内置 science-baseline@1.0.0 套件（24 案例 × 7 维度）、SUT 注册表
  （完整/基础 Qwen/原创开源参考/三消融）、案例执行器（真实生产缝 +
  可编程网关）、确定性指标与自动断言、注入回归、盲评、报告聚合（Welch
  检验 + t 区间）、发布阈值判定、SQLite 持久化（迁移 26）与
  `BridGes evaluate` CLI（run/replay/blind-review/report/gates）。测试
  74 条（双跑可复现、六类注入检出、许可证审计、匿名盲评、一键重放）。
