# Issue 04 独立验收记录

日期：2026-10-01。原交付：`be30290`（实现）、`7137dbe`（两轴评审修正），由另一编码代理完成、未推送未合并。本次独立验收修正：`2ccb74a`。验收比较点为当前主线 `2cd1375`，实现分支 `codex/04-final-payload-budget-and-data-boundary`（基点 `1b35c02`，阻塞票 03 已验收合入），工作树 `.worktrees/04-final-payload-budget`；开发与验证使用 conda `agent`（Python 3.11.15）。

## Standards

复核实现、测试与票据记录，未发现阻止合并的规范硬违规；发现并修正 3 处文档不一致：

1. `turn.py::assemble_payload_within_budget` docstring 把上界写成 `min(窗口, 最大输入额度) − 输出预留 − 安全余量`，与规格和实现 `min(最大输入额度, 窗口 − 输出预留 − 安全余量)` 不符——按规格更正（本次修复的正是评审表声称已修但漏改的最后一处）。
2. 票据「实现说明」中 `payload_input_upper_bound` 的公式同错，并漏加最外层 `min`——已与 `CONTEXT.md`、代码统一为规格公式。
3. 票据交付评论仍写「22 项新测试」，实际为 25（本次补 1 项后 26）——已据实更正；网关说明中「未验证返回 `payload_budget_unverified`」改为如实描述：额度未验证在更早的 `model_quota_unverified` 守卫闭锁，`payload_budget_unverified` 仅为 `_payload_gate_error` 兜底口径。

静态检查：`ruff check` 本票改动文件无新增违规（仅 `contracts/observability.py` 既有 5 项 `UP042`，未触碰相关行）；`mypy src`（strict）主线与分支均为 98 项既有错误，逐条名称一致（仅既有错误的行号位移：`turn.py` 的 `dict.get(str|None)`、`api/main.py` 健康返回值各一处），无新增。

## Spec

按工单验收标准逐项独立核实：

1. 复核反例（预算 408 追加工具块 1013、1 token 画像、121 条历史）均为最终网关发送断言，超限时适配器零调用；另补图片载荷网关断言（照片轮不再绕过最终门）。通过。
2. 中英/代码/公式/长 URL 估算、多模态内容部件（图片 `IMAGE_PART_COST_TOKENS=1024`/张）、输出预留随 payload 真实 `max_tokens` 变化均有确定性测试；上界公式有针对性用例（`max_input` 更小时不被重复扣减）。通过。
3. 放不下必要材料时统一收敛为明确受限结果 `payload_budget_exceeded`（用户文案说明可缩小范围），编译触底 `budget_floor_exceeded` 与最终门失败同语义；`REQUIRED` 证据与工具声明不被静默裁剪（仅画像切片为 `OPTIONAL`）。通过。
4. 材料统一注入数据边界声明（含编译历史自带 summary/证据 system 块），「忽略规则」不取得执行权限；修正块先于历史摘要；跨账户由既有仓库作用域隔离并有用例。通过（真实模型服从度按评测票验证）。
5. 实际发送载荷与采用/排除清单一致（固定封装与历史均入清单与预算）；清单只含 ID/类别/必要性/版本/计数/原因，测试断言不含私人正文；未新增调试工作台。通过。

## 本次实际验证

环境：`C:/Users/33755/anaconda3/envs/agent/python.exe`，`PYTHONUTF8=1`，测试子进程加 `CODEBUDDY_SAFE_DELETE_ENABLED=0`（沙箱 safe-delete shim 假 ERROR 规避，不改仓库/系统配置），各轮 `--basetemp` 独立目录。

- 工单定向：`pytest tests/chat/test_improvement04_payload_budget.py` → **26 passed**（新增图片载荷网关用例 1 项）。
- 邻接域：`pytest tests/ai tests/contracts` → **178 passed**。
- 全量 `tests/chat` 名称级对照：分支 **100 failed / 641 passed / 1 xfailed**，主线 **100 failed / 616 passed / 1 xfailed**；失败名单逐条一致，分支多出的 26 项即本票新增用例。既有失败为 `legacy_file_source_retired` 等环境/基线原因。
- 其他域 `tests/profiles tests/lifecycle tests/observability tests/evaluation`：分支 **12 failed / 513 passed**，主线 **12 failed / 516 passed**（多出 3 项为 Issue 08 新增 `tests/lifecycle/test_task_state_lifecycle.py`），失败名单逐条一致。
- 合并后主线复测：`pytest tests/chat/test_improvement04_payload_budget.py tests/ai tests/contracts tests/tasks` → **253 passed**。

## 验证限制

- 真实模型体验、真实 tokenizer 用量与外部可得性不在本票证明范围；确定性替身只证明机制，按评测票 40/42 校准。
- 生产聊天输出额度当前为 1024（与 03 同限制）；「按真实参数变化」由网关读 payload `max_tokens` 与测试覆盖。
- 清单 `actual_input_tokens` / `actual_output_tokens` 保留由 03 运行锁 `usage` 回填；有界分批的**执行**属 09；模块调用（14/15/19 等）各自接入真实最终输入，不得在门后追加材料。
- 未传运行额度快照的旧式/评测调用不做门（由 03 的额度解析在编译阶段闭锁），与票据限制一节一致。

## 合并与清理

- 独立验收修正提交 `2ccb74a`；`main` 从 `2cd1375` 以 `--no-ff` 合入，合并提交 `dec5751`，**无冲突**；合并后复测 253 passed。
- `git push origin main` 成功（`2cd1375..dec5751`），远端 `https://github.com/64922/BridGes.git`。
- `git worktree remove .worktrees/04-final-payload-budget` 成功，目录已不存在。
- `git branch -d codex/04-final-payload-budget-and-data-boundary` 安全删除成功（`2ccb74a` 经主线合并可达）。
- `git worktree prune --expire now --dry-run --verbose` 无失效记录，无需清理；最终保留主工作区与 Issue 06 的两个有效工作树（`BridGes-06`、`BridGes-06-base`，属其他会话，未触碰）。

结论：本票最终载荷预算门与材料权威边界按验收标准独立核实通过，修正确认无误，已合入主线并推送。
