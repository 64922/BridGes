# 工单 36 独立验收（2026-10-05）

验收对象：`44a41e0f` 及本次验收修复；固定基线：`b1da9afcec4ce62d8b50b7f45282dc9180188d28`。
验收依据为工单全文、AGENTS.md、CONTEXT.md、工作流与画像/表达规范、实际调用链和本次独立运行。
编码代理的临时交接仅用于定位，旧验证目录另存为 `36-review-preserved/36-review`，不代替本次证据。
前置 32、34、35 的实现提交已用祖先检查确认在基线，实际消费者接缝通过本次学习回归复核。

## Standards

初审 1 项 P1：合法未判题 ID 搭配“第2题答错，仍需补救”可以通过质量门，
渲染正文与系统标注的未作答状态矛盾。违反 orchestration §9 的证据支持规则和
delivery-and-validation §5 的关键断言门。独立 agent Python 调用已复现。
修复后未答/未判正文由真实 answer 状态逐题生成；复审无未解决阻断或新增气味。
完整证据预算、停止、租约、预期状态版本及独立提交守卫保持有效。

## Spec

初审 3 项需求缺口，无范围扩张：未答正文可误称答对/掌握；依据更新的旧判定可被误写为新理解漏洞；
正常总结路径只建立不完整策略种子，实际未接通 21/22 采用与表达快照。
对应工单任务 2、5、6；三项均已修复。未答条目确定性说明实际状态；依据更新条目仅说明重新确认，
混合条目逐题拆开保留实际判定；总结复用现有辅导采用/策略接缝，发送前撤回或关闭使用时
同时清除画像数据和偏好规则，以基线重新经过最终预算门。重试沿用有效快照，失效则重新采用。
提示词明确限制为本次表现、禁止长期掌握推断及追加题/补救课程/下一节任务。

两轴原始发现数：Standards 1（最高 P1），Spec 3（最高 P1）；复验剩余阻断均为 0。

## 逐项验收证据

| 工单要求 | 实际代码/本次验证 | 结论 |
| --- | --- | --- |
| 实际范围与逐题评分依据，四类分离 | summary.build_summary 输入当前书页、辅导、已问题、逐项 checks 与题目范围版本；质量门核验题 ID/判定/有效依据；summary_separates 测试 | 通过 |
| 未答/未判单列，无伪掌握百分比 | 四段后端与 Web 展示；status_acceptance 四种恶意正文组合；伪百分比、能力标签、learned 掌握宣称拒绝测试 | 通过 |
| 不自动补题/长期画像/下一节，主动追问才辅导 | creates_no_followup_work、tutoring_without_review、v2_20 followup 测试；真实学习写路径不建立长期掌握画像 | 通过 |
| 最后题先提交，失败只重试总结 | failure_persists_pending、v2_20 unverified/failed_summary 测试及策略重试：判定调用数保持 1，反馈、重放和导出一致 | 通过 |
| 新页使当前总结失效，历史范围版本保留 | service.finish_pages 使用总结自身 scope_version_id；appended_pages 测试覆盖旧历史、新范围、新总结、导出；沿用领域预期版本事务守卫 | 通过 |
| 21/22 表达与采用，04/09 预算及停止 | policy_acceptance 三项真实 API 测试；最终证据完整采用与预算门，调用走既有统一网关/账本；v2_20 stop_during_summary | 通过 |

## 接口、持久化与回滚

`StudySummaryPoint.kind` 扩展 `unanswered`，`StudySummary.scope_version_id` 默认空字符串；
OpenAPI 与 TypeScript 合同再生一致。既有 JSON 缺省读取保持兼容，数据库 DDL 无变化。
沿用学习状态表与生命周期目录完成历史读取、备份/导出、账户删除、状态复制和审计；
待总结由已完成 review + 空 summary 表达，无新权威状态机或数据搬迁。
ADR-0033 已补充合同替代、客户端部署及回滚约束。当前 Web 与合同需同步部署；
旧客户端可能漏展示新增类别，不能作为新总结的验收客户端。

## 本次独立验证

环境：Windows / PowerShell，Python `C:/Users/33755/anaconda3/envs/agent/python.exe`；
Web 子进程设定 agent 环境路径。conda 启动器/DLL 与沙箱临时 rename EPERM、进程停滞
使初轮执行无效，停止后在正常权限环境重跑；无效运行不计入结果。

- 本票及新增验收：18 passed；新增状态验收 5 项，采用/撤回验收 3 项。
- 学习30–36、合同、生命周期、状态复制聚焦：283 passed / 5 failed；
  main 单独生命周期文件 9 passed / 5 failed，同五项均为 companion 会话创建 409。
- 前端 StudyProgress：8 passed；Playwright 三个桌面尺寸：3 passed，已查看截图。
  浏览器使用受控 API 响应，领域持久性和失败恢复由上述 API 测试证明。
- Web lint 通过（既有 warning），生产 build 通过；typecheck 分支 14 错误、main 25，
  分支余错均在本票未修改文件。mypy 两侧 108 errors / 22 files，输出逐字一致。
- 仓库 ruff 两侧 517 项，规范化文件路径后的错误集合一致；本票改动文件 ruff 全通过。
- OpenAPI 再生无差异，openapi-typescript 7.13.0 再生合同与提交版本逐行相同。

- 全量同条件 `pytest tests -q -n 4`：分支 5378 passed / 224 failed / 37 skipped / 2 errors；
  main 5359 passed / 225 failed / 37 skipped / 2 errors。224 项失败与 2 项错误均共有，
  分支独有失败 0；新增 18 项通过，其余 +1 来自 main 独有 secret-scan 失败。
  比较脚本及完整失败节点列表见 `36-acceptance/comparison.json`。
- main 独有 secret-scan 串行复跑仍失败：扫描旧工单34日志以及并发生成的本轮测试日志中的
  凭据式样，位置在未跟踪的验收归档而非本票源码；本轮新增归档的凭据式样已移除，
  保留失败节点、位置及结果。工单34原有文件未修改。
- 最终合并前串行核心回归（两个新验收文件、v2_20 总结含停止、OpenAPI 同步）：21 passed。

有效日志、JUnit、截图保存在 `.scratch/2/validation/36-acceptance/`；
报告、比较结果及通过的关键检查随提交保留，大体积原始日志另保留在主工作区，不全部提交。

验收结论：工单36通过；两轴修复完成，未发现本次引入的测试失败。允许按授权合入 main。

## 剩余限制

本票机制验收不代表仓库全量检查已全绿。既有失败按 main 对照记录，不扩大本票修改范围。
确定性替身能证明恢复、证据绑定与生命周期，不证明真实模型的评分语义概括或自然表达质量。
除系统状态正文外，自然语言结论仍受提示词、可确定识别规则及工单 42 实测约束；
真实模型体验、外部服务可得性与语义越界率须由 42 验证后判断可开放范围。

## 合并、推送与清理

- 验收修复提交 `2533de9e`，实现提交 `44a41e0f`；无冲突合并为
  `1f7ddb857a579531a9b3d040864a825ca17fa4fb`。
- main 与已验收 Issue 分支内容树同为 `2eff38efc6ced124dcb495b56ecddc99ade8371e`，
  因合并没有引入额外代码变更，沿用已执行的完整检查与21项串行合并前验证。
- 经现有 `socks5h://127.0.0.1:7890` 代理推送 origin；`ls-remote` 确认远端 main 为合并提交。
- 工作树无未提交或暂存的跟踪文件；全部28个未跟踪文件在主工作区另存并逐项校验 SHA256，
  清单见 `36-acceptance/cleanup-manifest.json`；截图与测试结果也另存。
- 自动审批首次拒绝删除，原因是未充分证明全部未提交内容已保留；补充完整清单与哈希后通过。
  Git 删除遇到 Windows 文件占用，确认并终止仅运行本票新验收测试的遗留 pytest 进程后，
  删除工作树残余目录及本地 `codex/36-evidence-bound-study-summary` 分支，并执行 worktree prune。
- 其他分支、原有未跟踪文件和主工作区保留。清理记录通过后续文档提交同步远端，最终提交见 Git 历史。
