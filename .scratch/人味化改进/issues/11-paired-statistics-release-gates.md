# Issue 11：建立配对统计与发布质量门

Status: ready-for-agent

Type: task

Priority: P0

Parent: [BridGes 有人味表达改进方案](../README.md)

User stories: US-01、US-02、US-04、US-06、US-07

## What to build

为普通聊天和文章人味化建立预注册、按案例配对的统计报告与发布质量门。报告先验证运行锁、来源保真和系统裁判完整性，再计算自动 panel 的匿名偏好和分维度差异；聊天与文章分别裁决。新版必须显著优于当前 BridGes，文章还必须在预注册的 5 个百分点边界内不劣于 `Humanizer-zh`。

统计单位是 case，而不是把同一 case 的 seed、重复执行、双向顺序或多个系统裁判误当成独立样本。自动风格词表只提供失败诊断，关键事实、保护区和虚构亲历是零容忍硬门。

## 已验证现状与根因摘要

- 当前评测把按 case/seed/execution 对齐的数据交给非配对 Welch 检验，重复执行会造成伪精确。
- 当前 humanization 自动指标主要依赖少量短语，`task_fit` 和 `audience_fit` 近似只看流程状态；`fact_invariance` 在检查缺失时可能默认 True。
- 当前发布 gates 关注科学、安全、职业和教学等维度，没有普通聊天或文章人味质量门。
- 当前 runner 报告以简单确定性裁判为主，没有隔离多模型裁判摘要、非劣效结论或不足数据的 `inconclusive` 门。
- 用户最初批准人工盲评，随后明确覆盖该决定并要求完全取消人工评审；保留相对当前显著改善、文章相对 `Humanizer-zh` 5pp 非劣效及聊天/文章独立通过，裁决改由至少三个合格系统裁判自动完成。

## 非目标

- 不用 p 值单独决定产品质量。
- 不把 seed 或执行次数当作独立用户案例扩大样本。
- 不让聊天高分抵消文章退化，或让某体裁高分掩盖高风险切片失败。
- 不把自动 AI 短语分作为主要发布裁判。
- 不在看完 holdout 后修改非劣效边界或成功公式。

## Acceptance criteria

- [ ] 在运行 holdout 前把统计计划、偏好公式、置信区间、非劣效边界、最小样本、系统裁判 panel、模型多样性、双向一致性、canary/漂移阈值、硬失败定义和多重切片报告写入版本化配置并锁定哈希。
- [ ] 每个 case 先在 seed/execution 内聚合，再聚合同一裁判的 A/B 与 B/A，最后按预注册规则聚合有效系统裁判；candidate 与 peer 按同一 case 做成对差值，使用固定种子的 case-cluster bootstrap 95% CI 或经验证的成对随机化方法。
- [ ] 主偏好分固定为 `win + 0.5 × tie`；`无法判断`不计胜负但计入有效性报告，过高时触发 `inconclusive`。
- [ ] candidate 相对 current production 的聊天和文章偏好分 95% CI 下界分别大于 50%，才能声称显著改善。
- [ ] 文章 candidate 相对 `Humanizer-zh` 的偏好分 95% CI 下界至少 45%，即相对 50% 基线不差超过 5 个百分点；聊天不以文章参考结果代替自己的门。
- [ ] 分别报告七个维度的均值/中位数、成对差、置信区间和裁判间分歧，不把事实忠实与自然度平均后隐藏；事实忠实仍受硬门约束。
- [ ] 关键数字/单位/日期/专名/公式/引语/URL/因果/结论强度、protected spans 和虚构亲历为零严重失败；任一严重失败阻止对应 surface 发布。非关键保真通过率至少 99%。
- [ ] 自动风格指标扩展为带场景、位置和证据的诊断，并 fail closed 读取保真结果；不得用单词命中直接阻止发布，除非属于稳定的协议/助手身份泄漏。
- [ ] 聊天和文章分别输出通过、失败或 `inconclusive`；同时按模式、强度、体裁、长度、风险和 do-no-harm 切片报告。任何预注册关键切片明显退化直接失败或 `inconclusive`，不进入人工裁决。
- [ ] 未满足最少 case、至少三个合格系统裁判、裁判多样性、双向一致性、canary 校准、漂移、运行锁、参考哈希或硬门完整性时固定为 `inconclusive`，不得沿用当前自动成功语义。
- [ ] 报告展示 win/tie/loss、效应量、CI、样本量、系统裁判版本与分歧、缺失、失败案例 ID、运行成本和延迟；不在统计报告复制私人正文。
- [ ] 报告标题和机器可读元数据明确标注 `automated_system_judges_only=true` 与 `human_validated=false`；结论不得解释为真实用户偏好已获验证。
- [ ] 发布 gate 接入现有 runner/CLI，能在候选未通过时返回非零退出状态和稳定中文原因；普通非人味评测不受错误门控。

## Test plan

1. 用合成配对数据验证当前实现的非配对算法会产生错误结论，新算法按 case 聚合并得到预期 CI。
2. 用重复 seed/execution 数量极不均衡的数据验证不会给某一 case 额外权重。
3. 验证 current 改善门、Humanizer 非劣效门在边界上下、全部 tie、有效样本不足、裁判位置偏差、panel 分歧和大量无法判断时的精确状态。
4. 注入一个关键事实失败或虚构亲历，验证即使自然度全为 5 分仍阻止发布；缺少保真检查同样失败关闭。
5. 验证聊天通过/文章失败、文章通过/聊天失败、某关键体裁退化时整体不误报通过且不请求人工裁决。
6. 用固定 seed 重跑 bootstrap，验证报告哈希稳定；改变预注册配置后必须创建新评测运行，不得覆盖旧报告。
7. 运行现有 evaluation runner、CLI、gates、metrics 和 blind review 回归，验证其他质量维度保持。

## Observability & rollback

- 保存预注册配置哈希、case 数、有效系统裁判数/版本/家族、双向一致性、canary/漂移、缺失原因、硬失败、CI、门禁状态、成本和延迟；原始正文仍引用 append-only 输出哈希。
- 监控 `inconclusive` 比例和原因，不能通过放宽裁判多样性、校准或缺失来降低该比例。
- 新人味门先作为 shadow report，再转为阻断发布。回滚可撤下阻断，但必须继续显示失败报告；不得删除历史统计或把失败改写为通过。

## Blocked by

- [Issue 09：建立分层语料与真实对照运行锁](09-benchmark-corpus-and-run-lock.md)
- [Issue 10：修复并扩展多维匿名盲评](10-blind-multidimensional-review.md)

## Comments

- 2026-08-12：用户批准 current 显著改善、`Humanizer-zh` 5pp 非劣效及聊天/文章独立门禁；随后要求完全取消人工评审，裁决改为至少三个隔离系统裁判。
