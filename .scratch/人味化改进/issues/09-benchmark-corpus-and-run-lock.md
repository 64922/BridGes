# Issue 09：建立分层语料与真实对照运行锁

Status: ready-for-agent

Type: task

Priority: P0

Parent: [BridGes 有人味表达改进方案](../README.md)

User stories: US-01、US-02、US-04、US-06、US-07

## What to build

在 Issue 01 的真实 tracer bullet 上建立版本化 development corpus、冻结 holdout 和统一运行锁。聊天与文章分别建集、分别报告；每个案例包含足够的任务、来源和许可元数据，使 current production、candidate、plain model 和 `Humanizer-zh` reference 能在同一输入与基础模型参数下配对运行并重放。

现有 30 条原创自然语言样例可作为开发集种子，但必须转换为可执行数据合同并补足普通聊天、来源对抗、不伤害和高事实密度场景。holdout 在实现调优前冻结哈希，首次解封后不得用来逐案修规则。

## 已验证现状与根因摘要

- 现有 30 条 Markdown fixture 覆盖多类文章，但没有真实执行、候选输出、盲评或发布门；普通聊天没有对应自然度语料。
- 内置评测只有 4 个左右 humanizer 案例，且 scripted “好答案”会新增无来源方法、局限和例子。
- 当前所谓 open-source reference 是仓库自写确定性实现，不是 `Humanizer-zh`。
- 现有运行锁记录 seed 和重复执行，但 scripted 输出不随 seed 变化，无法证明模型质量或稳定性。
- 没有冻结用户这次失败文章及“无亲历输入不得加亲历”的对抗变体，容易按几个短语过拟合。

## 非目标

- 不把未经许可的外部文章大规模复制为语料。
- 不用生产私人对话直接填充 benchmark。
- 不用单一总分混合聊天与文章，或混合硬保真与软风格。
- 不承诺 seed 能让不支持确定性采样的外部模型逐 token 重现；必须真实记录这种限制。
- 不把 holdout 当作日常 prompt 调试集。

## Acceptance criteria

- [ ] 建立版本化 case schema，至少包含：case ID、surface、operation、conversation mode、genre/profile、risk、length、user request、source/context、audience、channel、task contract、source ledger/protected items、允许/禁止新增 claim、do-no-harm 标志、slice tags、license/provenance、内容哈希和 development/holdout 分区。
- [ ] `chat-naturalness` 与 `article-humanization` 分别至少 40 个有效案例；若首次实施采用更小样本，发布结论必须为 `inconclusive`，并在本 Issue 完成前补足下限。
- [ ] 聊天覆盖短答、解释、建议、纠错/不同意、多轮承接、情绪适配、澄清、工具结果、错误/拒答、代码公式引用和真实学习课时；日常陪伴与学习模式分层均衡。
- [ ] 文章覆盖改写与生成、三级强度、邮件、报告、教程、观点、演讲、科普、科研技术、材料不足和原文已自然；长短与高/低事实密度分层。
- [ ] 对抗案例覆盖商业/PPT 抽象词、抽象名词链、强行生活场景、假经验/假情绪、机械三段式、每段金句、过度设问、居高临下、无必要第一人称、引语含“禁词”、合法术语、必要冒号/破折号/列表。
- [ ] 把用户描述的时间管理失败型文章重建为原创、无私人信息的冻结案例，并增加“输入无亲历，禁止新增我以前/上周朋友/半小时前/刷短视频”等变体。
- [ ] SUT 至少包括 current production、candidate、plain model/no humanizer 和真实 `Humanizer-zh` reference；同一 case 尽可能使用相同基础模型、系统边界和采样参数，无法一致的差异必须进入运行锁和报告限制。
- [ ] `Humanizer-zh` reference 固定 SKILL 快照路径、内容哈希、模型、参数、运行时间和许可证记录；若外部智能体无法完全重放，则保存冻结输出并明确标记 `frozen_external_reference`，不得伪称完全可复现。
- [ ] 运行锁记录 git/build、语料/契约/来源账本/策略/SKILL 哈希、模型快照、temperature/top_p/seed、重试、匿名种子、执行次数、环境和裁判版本；缺少关键字段即拒绝比较。
- [ ] 原始运行输出 append-only，按 case/SUT/run 唯一定位；报告和匿名包引用哈希，不覆盖旧输出。
- [ ] holdout 清单、哈希和解封事件有审计；策略开发只能查看 development 输出，发布 Issue 12 才可解封 holdout。
- [ ] 所有语料、参考快照和派生资产符合 ADR-0011 的净室和许可证边界，并生成数据卡与来源清洁记录。

## Test plan

1. 对 schema、必填字段、枚举、哈希、分区、许可证和来源绑定做严格校验，错误案例不得进入运行。
2. 编写切片覆盖测试，验证每条路径、模式、强度、体裁、风险和对抗类型达到预定数量且没有同源重复泄漏到 development/holdout 两侧。
3. 用同一运行锁重放 development 子集，验证 case/SUT/run 对齐、append-only 和比较前完整性检查。
4. 模拟模型参数不同、SKILL 哈希变化、缺少来源账本、参考输出不可重放和 holdout 提前读取，验证拒绝或明确降级。
5. 对所有外部/派生文本运行来源清洁扫描、精确/近似片段比对和许可证清单校验；任何无法自动确认来源清洁的资产不得进入 corpus，而不是转交人工抽查。
6. 运行一次 current/candidate/plain/reference 的小规模真实 development 对照，验证运行时长、失败恢复和脱敏摘要。

## Observability & rollback

- 仪表记录案例数与切片分布、运行成功率、SUT/模型版本、耗时、token、重试、锁不完整原因和 holdout 访问审计，不记录正文。
- 长文本和真实模型运行与普通 CI 分离，但 schema、许可、哈希和小型假适配器重放进入 CI。
- corpus 版本不可原地覆盖。回滚选择上一版本和运行锁；已经解封的 holdout 永久标记为已使用，不能重新伪装成未见数据。

## Blocked by

- [Issue 01：跑通真实人味评测 tracer bullet](01-real-humanization-tracer-bullet.md)

## Comments

- 2026-08-12：用户批准聊天和文章分别验收，`Humanizer-zh` 只作为自然度参照，必须与来源保真硬门共同使用。
