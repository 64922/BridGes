# 兼容窗口观察门

Status: pending-runtime-evidence

依据：[`ADR-0026`](../../docs/adr/0026-frozen-product-contracts-and-migration-gates.md)。本文件是 Issue 24 的领取前置证据模板，不是可以由单元测试替代的勾选清单。Issue 20 及相关退役 Issue 部署后，发布代理必须用真实兼容层指标填写本文件；在状态变为 `passed` 前，不得删除旧页面重定向或旧 API 的 410 合同。

## 迁移阶段

| 阶段 | 必须成立的合同 | 允许的回滚 |
| --- | --- | --- |
| expand | 新 Schema、读投影、迁移报告和历史对象读取已部署；旧写路径仍可用 | 停止迁移，保留旧数据和 staged 结果，从报告继续 |
| migrate | 新写路径和用户旅程已切换；旧写接口稳定返回 410；账户隔离、幂等、导出和备份恢复通过 | 回滚未发布目标投影或写路径；不得恢复已清除 SMTP 凭据、已停用扩展或已取消提醒 |
| contract | 真实旧调用完整观察窗口为零，迁移和回滚证据复核通过 | 只能从验证过的备份重建新合同，不恢复旧页面/API/扩展执行/提醒发送/模式切换写能力 |

## 通过条件

- 观察从包含全部兼容计数器的迁移版本部署成功后开始，并完整跨过至少一个可观测迁移版本。
- 每个退役页面重定向和旧 API 410 路径都有稳定路由标识、服务版本与调用计数；指标不含用户标识、请求正文、查询或文件名。
- 自动化探针与真实用户流量分开计数。探针必须持续证明重定向/410 合同有效，但不计入“旧调用清零”。
- 在最终连续观察窗口内，所有真实用户流量计数均为零；若出现任何调用，兼容层继续保留，处理调用方后重新开始完整观察窗口。
- 报告包含观察起止时间、部署版本、指标查询、原始结果摘要与证据制品校验值，并由发布负责人复核。

## 最低观测面

| 类别 | 最低路由范围 | 真实调用数 | 探针结果 |
| --- | --- | ---: | --- |
| 旧页面重定向 | 学习项目列表/详情、任务安排、插件与 MCP 管理 | 待填写 | 待填写 |
| 项目与文件 API 410 | 项目创建/关联、项目文件写入、聊天附件写入 | 待填写 | 待填写 |
| 调度 API 410 | 任务、复习计划、提醒与历史读取 | 待填写 | 待填写 |
| 扩展 API 410 | 用户 SKILL、插件、MCP 的管理与调用 | 待填写 | 待填写 |
| 会话/画像 API 410 | 模式切换、旧画像许可/通知/候选/历史/删除 | 待填写 | 待填写 |

实现时应把仓库中实际存在的所有退役路径补入明细，不能只验证本表列出的代表项。

## Issue 05 会话模式切换 410 观测契约

旧路由 `POST /chat/conversations/{conversation_id}/mode` 在兼容窗口内固定返回
`410 conversation_mode_switch_retired`，服务端版本使用 `0.1.0`。运行时只按下表
维度计数，不记录账户、会话标识、请求正文或查询参数；`probe` 由
`X-Bridges-Compatibility-Probe: 1` 标记，其余请求归入 `real`。

| endpoint_id | service_version | traffic_class | status_code | count |
| --- | --- | --- | ---: | ---: |
| `chat.conversation_mode_switch` | `0.1.0` | `real` | 410 | 待运行时观测 |
| `chat.conversation_mode_switch` | `0.1.0` | `probe` | 410 | 待运行时观测 |

应用内 `ObservabilityService.compatibility_gate_snapshot()` 输出上述稳定字段，供发布
代理填入真实运行窗口的计数与证据；单元测试只验证字段和隐私边界，不替代运行时门禁。

## 发布证据

- 兼容版本：待填写
- 观察开始：待填写
- 观察结束：待填写
- 指标查询或仪表盘制品：待填写
- 原始摘要校验值：待填写
- 非零调用及处置：待填写
- 发布负责人复核：待填写

## Issue 03 兼容端点清单

本 Issue 的旧入口统一返回 HTTP 410；稳定端点 ID 用于计数，响应不回显账户、路径参数或请求正文。

- 学习复习：`learning.review-schedule.create/read`、`learning.review-tasks.list/detail/postpone/adjust/cancel/complete/work-order`
- 提醒 SMTP：`reminders.smtp.read/write/verify/delete`
- 提醒设置与解析：`reminders.settings.read/write`、`reminders.parse`
- 提醒生命周期：`reminders.create/list/detail/update/pause/resume/send-now/cancel/deliveries`
- 桌面 `/tasks`、`/templates/list?section=tasks` 和任务详情深链展示退役说明，并提供学习聊天入口。

兼容计数器使用持久化命名空间 `compatibility_gate`，只保存 `service_version`、稳定端点 ID 以及 `real/probe` 两类计数。探针通过 `X-Bridges-Compatibility-Probe: true` 标记；计数不包含账户、路径参数、查询串或请求正文。

提醒清理报告使用 `reminder_retirement` 命名空间，只保存数量、状态、时间和历史投递保留标记。提醒行标记为 `retired`，排程字段清空；SMTP 验证标记为 `superseded`，授权码逐账户清除。历史投递仅通过账户导出保留，不重放未来提醒。

所有通过条件满足后，将 `Status` 改为 `passed`，并在 Issue 24 的 Comments 中引用本报告及证据。任何非零真实调用、缺失计数器或不完整观察周期都会使门禁保持 `pending-runtime-evidence`。
## Issue 04 运行时观察接入

- `service_version`: `0.1.0`
- 观察接口：`GET /compatibility/observations`
- 旧路由标识：`legacy.plugins.list/check/install/enable/disable/uninstall/demo`；`legacy.mcp.list/check/install/enable/disable/permissions/uninstall/invoke/confirmation.approve/confirmation.deny/calls`
- 观察字段只包含 `real`、`probe` 与稳定路由标识；不包含账户、包名、服务器名或请求正文。
- 迁移标记：schema `33`，`extension_retirement` 状态 `completed`。
- 当前状态仍为 `pending-runtime-evidence`：本次仅有自动化探针证据，待真实部署流量观察完成后再改为 `passed`。
