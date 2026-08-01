# 28 — 交付原创净室 bridges-humanizer SKILL
Status: ready-for-agent
Blocked by: [11](./11-deliver-persisted-streaming-chat.md), [20](./20-deliver-layered-retrieval-and-citations.md), [27](./27-deliver-profile-slices-disclosure-and-feedback-loop.md)
Covered requirements: B-01, CHAT-07, IMP-01, IMP-02, IMP-03, BONUS-03, SCORE-01, SCORE-02, SCORE-03, DESKTOP-01
ADRs: [0001](../../../docs/adr/0001-chat-first-product-surface.md), [0009](../../../docs/adr/0009-fixed-model-and-provider-matrix.md), [0011](../../../docs/adr/0011-clean-room-humanizer-and-license-boundary.md), [0015](../../../docs/adr/0015-minimum-cloud-disclosure.md), [0016](../../../docs/adr/0016-incremental-vertical-replacement.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

以原创净室方式实现默认内置、只读、版本固定的 `bridges-humanizer` SKILL，并接入两种对话模式的“+”菜单和空白对话建议卡。支持两条端到端路径：上传或粘贴现有文章后改写，以及根据主题、受众、体裁和约束生成新文章。智能体编排与 SKILL 规则共同工作，不能退化成一条“写得自然一点”的提示词。

分别为科普文案、课程讲稿、科研汇报和论文写作建立表达合同。每次输出固定包含最终文本、逐项修改细节、修改理由、事实核查结果和尚未解决的问题。事实锁保护数值、单位、对象关系、限定条件、结论强度、公式与引用，并在无法确认时要求人工处理。

## Acceptance criteria

- [ ] `bridges-humanizer` 具有完整 SKILL 说明、规则、证据边界、输出合同、版本信息和覆盖两条路径的测试夹具。
- [ ] 两种模式的“+”菜单和空白对话“文章人味化”建议卡均使用原创图标并进入真实消息流程。
- [ ] 改写路径接受粘贴文本或当前账户文件，先提取任务契约与事实锁，再返回人性化结果。
- [ ] 主题生成路径收集或合理确认主题、受众、体裁、渠道和硬约束，再生成并复核。
- [ ] 科普文案、课程讲稿、科研汇报和论文写作分别使用可测试的表达规则，不共用单一泛化模板。
- [ ] 每次成功结果同时包含最终文本、修改细节、每项理由、事实核查结果和未解决问题，缺一不标记完成。
- [ ] 前后文本中的数值、单位、对象关系、限定条件、公式、引用和结论强度受事实锁检查；冲突时停止或明确标注人工确认。
- [ ] 可引用来源仍通过本地/联网证据合同呈现；人味化不得虚构事实、论文或引用来增强表达。
- [ ] `scientific-humanization` 仅作方法研究且不复制内容；任何实际复用的 MIT 内容都保留许可证、署名和来源清洁记录，默认实现应保持原创。
- [ ] 过程卡具有中文 loading、empty、error、permission 和 recovery 状态，失败后可从原任务重试且不丢失用户输入。
- [ ] SKILL 注册为默认内置能力并准备供插件页展示，不依赖用户手工上传或 `.env`。

## Verification

- 在 Conda `agent` 环境运行两条路径、四类体裁、输出字段完整性、事实锁、引用保持和失败恢复测试。
- 使用含数值、单位、公式、限定条件和引文的固定语料做前后自动比较，并对差异执行人工抽查。
- 运行前端类型检查和桌面 E2E，覆盖菜单、建议卡、文件改写、主题生成、结果详情和错误重试。
- 完成许可证与来源审计，保存净室实现记录；运行成对盲评比较基础 Qwen、仅提示词和完整 SKILL 的人味偏好与事实保真。

## Non-goals

- 不以规避 AI 检测、冒充真人、伪造个人经历或欺骗性代写为目标。
- 不复制无许可证参考项目的文本、提示、目录结构或示例，也不牺牲科学事实换取口语化。
- 不实现或验收移动端布局与交互。

## Blocked by

- [Issue 11：持久化流式聊天](./11-deliver-persisted-streaming-chat.md)
- [Issue 20：分层本地检索与引用](./20-deliver-layered-retrieval-and-citations.md)
- [Issue 27：最小画像切片与反馈闭环](./27-deliver-profile-slices-disclosure-and-feedback-loop.md)

## Comments

“默认内置准备”包括稳定注册标识、版本和只读来源；后续插件治理页面只负责展示与治理，不能改变其净室与事实锁合同。
