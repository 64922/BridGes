# 15 — 以新呈现读取旧结果，保持退役能力关闭

**What to build:** 用户能够在新页面阅读过去的结果和失败尝试，旧数据不会丢失，也不会因重新展示历史而重新启动已经退役的能力。

**Blocked by:** 07 — 内联呈现交付状态、失败与真实恢复；08 — 从真实引用打开右侧资料并继续对话

**Status:** ready-for-human

> 已于 2026-10-17 经用户确认发布。仅在本票所有阻塞项完成验收后开始；无阻塞票可立即领取。设计与原型已批准，无需重新决定视觉方向。

## 当前事实与动机

现存历史投影包含学习资料、职业规划、GitHub 推荐及旧表达任务等。内部过时组件可以删除，但外部历史读取与数据保留是既有合同。

## 工作范围

- 以消息呈现清单逐一覆盖旧投影，将核心结果放进正文，必要细节放入轻量展开或资料入口。
- 保留只读历史数据、来源、旧尝试与导出所需内容；区分历史结果和可启动的新能力。
- 直接替换旧内部渲染实现，调用点清零后删除失去用途的组件，不保留转发 shim。

## 验收标准

- [x] 退役模块历史结果、旧表达结果、旧尝试和缺字段消息仍有可理解的阅读方式。
- [x] 历史空正文但有有效结构化结果时不会变成空白；不得为补齐界面捏造自然语言结论。
- [x] 来源不存在、文件已删除或权限变化时明确说明，不能用当前可用资料冒充原依据。
- [x] 退役模块没有重试、启动或自动恢复入口；重新打开旧消息不触发任何后台运行。
- [x] 复制、导出和既有朗读仍取得历史可交付内容；未通过交付门的候选不被恢复成正文。
- [x] 旧内部呈现路径移除后只有一个有效渲染入口，不为新旧界面双写消息。
- [x] 不改写历史模式、原请求模块、可信状态与尝试关系，不清库或强制回填。
- [x] 变更后的历史说明与真实合同一致，不能通过标记“已完成”隐去当时失败。

## 验证方式

用现有历史契约夹具逐项覆盖退役模块、旧表达、结构化但无正文、失效引用与多次尝试，并通过正式会话读取和禁止重试路径验证。

每票完成受影响行为的测试与项目要求的检查；涉及前端必须完成生产构建。复用已有可用依赖，不例行重装。外部依赖不可用、既有失败和本票新增失败分别记录，未验证项不得标为通过。最终整合票不能替代本票自身的验证。

## 范围外

不恢复退役业务，不清除存量数据，不新增内部兼容层；对外协议兼容按既有合同保留。

## 执行约束

遵循已批准的 ADR-0044、设计原则 0005、本功能 PRD 及仓库准则。在 Conda 的 agent 环境开发。优先复用现有接口与领域能力；只有用例证明现有合同不足时才改协议，并在同票完成服务端、生成类型、消费端和测试。无必要不改 schema，不搬迁数据。

本票必须交付可演示或可验证的完整行为，而不是只有接口、组件或截图。内部路径直接替换，不引入兼容 shim、双写或长期双实现。只修改本票需要的范围；不要在执行中顺便重写业务。

## 完成后的交接

交付历史投影覆盖表、退役入口负例和被移除内部路径清单。

交接记录包含实际变更、验证命令和结果、必要截图／服务证据、遗留限制与回退方式；只有行为验收通过才解除后续票的阻塞。

## Comments

- 2026-10-17：依据已验收原型、设计原则及当前代码只读核对形成草案；未开始业务开发。
- 2026-10-17：用户确认「发布」，按已批准拆分转为 ready-for-agent；未开始业务开发。
- 2026-10-18：实现、定向测试与正式 E2E 完成；行为验收通过，状态转为 ready-for-human，等待人工复核。

## 交接记录（2026-10-18）

**分支与基线：** `15-historical-results`，基于 main `f53db886`，合并时已合入 main `aefc860e`（Issue 14 图片/视频呈现）。本票只改前端与测试，未修改 Python 业务源码、API 合同、数据库 schema、迁移或依赖。

**实际变更**

- 新增 `apps/web/src/lib/historical-results.ts`：只读取得历史可交付文本；过滤 `javascript:` 与协议相对地址，只允许 `http(s)` 或同源相对路径；判断空正文时区分结构化结果与真正空白。
- 新增 `HistoricalResults.tsx` 与 `HistoricalModuleSections.tsx`：学习资料、职业规划、GitHub 推荐统一为“历史结果 · 只读”分区；核心条目直接可见，查询记录、依据、剔除与错误放入 `<details>`；失效链接显示“历史链接不可用”且不生成可点击伪协议。
- 新增 `HistoricalExpression.tsx`：人味化历史结果唯一入口；已交付正文由消息正文承担，结果卡只显示交付状态、复制与审计折叠；失败/未交付时正文区显示“正文未交付”，不恢复候选稿；保留 legacy 徽标、来源保真、表达审稿、修订、剔除、证据、待确认及 `expand`/`copy`/`legacy_read` 遥测。
- 接入 `AssistantMessage`、`MessageBody`、`AssistantActions`、`AttemptHistory`、`chat-message.ts`、`chat-thread.tsx`：复制优先取历史可交付正文；无正文时禁用复制；旧尝试按 `attempt_number` 展示已保存正文，不与最新回答混写。
- `ProjectionCards` 移除退役模块与 humanizer 分支，保留活跃的 `careerPlanning`、teaching、web、paper、tieba、image/video 等投影。
- 合入 main 的 Issue 14 后，`AssistantMessage` 同时保留 `HistoricalResults` 与 `MediaTaskCards`；`ProjectionCards` 使用新的 `ImageMedia`/`VideoMedia`，不再引用已删除的 `ImageTaskCard`/`VideoTaskCard`。
- 删除旧内部路径：
  - `apps/web/src/components/bridges/chat/LearningResourcesCard.tsx` + `.test.tsx`
  - `apps/web/src/components/bridges/chat/CareerPlanCard.tsx` + `.test.tsx`
  - `apps/web/src/components/bridges/chat/GithubProjectsCard.tsx` + `.test.tsx`
  - `apps/web/src/components/bridges/HumanizerResultCard.tsx` + `.test.tsx`
  - `apps/web/src/components/bridges/HumanizerProcessCard.tsx`

**历史投影覆盖表**

| 数据/场景 | 呈现 | 验证 |
| --- | --- | --- |
| `learning_resources` 成功/空/停止 | 条目、来源、理由、未核实项；详情含原始说法、查询词、层级、扩展词、假设、证据与调用记录 | 单测、issue15 E2E、后端夹具 |
| `career_plan` 成功/失败/空 | 岗位样本、建议、状态；详情含原始请求、目标、阶段、约束、统计口径、剔除与证据边界 | 单测、issue15 E2E、后端夹具 |
| `github_projects` `metadata_only`/成功 | 仓库、覆盖说明、必要功能计数、仅静态读取；详情含场景、功能、技术词、约束与调用记录 | 单测、issue15 E2E、后端夹具 |
| humanizer `article` 已交付/部分/失败 | 正文只出现一次；交付状态 + 审计折叠；失败显示未交付与错误原因 | issue08 E2E、issue15 E2E、单测 |
| humanizer legacy（无 `article`） | “旧版结果”徽标 + 旧字段审计；复制最终正文 | issue08 E2E、单测、`legacy_read` 遥测单测 |
| 空正文但有结构化结果 | 显示“没有保存可读的自然语言正文”，再展示结构化结果，不捏造结论 | issue15 E2E、单测、后端夹具 |
| 多次尝试 | 最新回答 + “前 N 次尝试”折叠；每次尝试显示自己保存的正文与错误 | issue15 E2E、单测、后端夹具 |
| 失效/不安全链接 | 只显示文本与“历史链接不可用”，不生成 `javascript:` 链接 | 单测 |
| 重开/刷新旧消息 | 不创建消息、不重试、不启动、不订阅运行事件；后端重试返回 `module_not_allowed` | issue15 E2E、后端夹具 |

**退役入口负例**

- 页面不渲染退役模块的重试、启动、重新生成或自动恢复按钮。
- 打开与刷新历史消息不发送 `POST /messages`、`/retry`、`/start` 或运行事件订阅请求；人味化遥测不属于运行请求，已与运行事件断言分开。
- `POST /chat/conversations/{id}/messages/{id}/retry` 对退役模块返回 `module_not_allowed`，不新增消息或运行记录。
- 失败 humanizer 的候选稿不进入正文、不可复制，也不因刷新恢复。

**验证命令与结果**

```powershell
cd apps/web
npm run typecheck   # 通过
npm run test:unit   # 合并 main 后 51 files / 395 tests 通过
npm run lint        # 通过，仅 2 条既有 <img> 警告
npm run build       # 通过 23 个页面；Windows junction 导致 standalone symlink EPERM 警告，命令退出成功
```

```powershell
$env:BRIDGES_PYTHON='C:\Users\33755\anaconda3\envs\agent\python.exe'
pytest tests/chat/test_issue15_historical_results.py tests/chat/test_daily_module_retirement.py tests/chat/test_humanizer_chat.py -q
# 37 passed

ruff check tests/chat/test_issue15_historical_results.py
# 通过

$env:CI='1'
npx playwright test e2e/issue08-article-result-projection.spec.ts e2e/issue14-media-results.spec.ts e2e/issue15-historical-results.spec.ts --workers=1
# 11 passed（issue08 6 + issue14 2 + issue15 3）
```

**既有失败与未验证项**

- 广域 `pytest tests/chat tests/retirement -q` 为 105 failed / 965 passed / 3 skipped。失败集中在既有首轮会话创建合同（`POST /chat/conversations` 返回 409 `first_turn_required`）与已退役用户扩展（410 `user_extensions_retired`）；抽查 `tests/chat/test_career_planning_chat.py::test_career_message_flows_through_real_message_stream` 在 main 上同样失败，非本票引入。未将广域套件作为本票门禁。
- `mypy` 会解析已安装的 `bridges`（指向 main）并报告与本 worktree 无关的既有错误，未作为本票门禁。
- 未采集截图；证据以正式 E2E 断言、持久化读取与服务端禁止重试测试为准。
- 对话朗读入口已随 ADR-0030 退役（`legacy_read_aloud_retired`），当前界面没有可验证的朗读入口；本票未新增导出或朗读能力。

**遗留限制**

- 旧 humanizer 的部分旧字段（如 `fact_lock_check`、`repair_attempts`）未在新审计中原样展开；保留的是正文、编辑、事实核对、质量警告、待确认、引用、文体检查与错误状态。若人工复核要求逐字段完整展示，需在本票后续补充。
- 历史资料的外部来源未重新抓取，界面明确标注“外部来源未重新核对”。
- 广域 Python 套件的既有失败未在本票修复，避免扩大范围。

**回退方式**

- 回退本票分支/合并提交即可恢复旧卡片与旧投影入口；无 schema、迁移或存量数据需要回滚，历史消息本身未被改写。
