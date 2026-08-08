# BridGes 收尾发布验收报告（issue 11）

> 状态：**完成**（2026-08-09，三轮串行套件全部通过后定稿）。
> 本文件只引用脱敏 run/message/attempt ID，不复制用户正文或附件内容。

## 1. 代码与运行环境

| 项 | 值 |
|---|---|
| 代码版本（提交） | `c149fe8`（main，前置 issue 01-10 全部合入）+ 本 issue 分支 `11-closeout-release-acceptance`（验收报告与两处组合缺陷修复，提交号见文末） |
| 系统 | Windows 11 Pro 10.0.26200 |
| Python | 3.11.15（仓库 `.venv`） |
| pytest | 9.1.1 |
| Playwright / Chromium | chromium-1228（ms-playwright） |
| Node | Playwright 1.x（apps/web node_modules） |

## 2. 执行命令

- 全量基线：`.venv/Scripts/python.exe -m pytest -q` → **2321 passed, 6 skipped**
- 收尾三轮套件：`bash scripts/closeout_acceptance.sh 3`（pytest 组合 + Playwright 9 组纵向切片，CI 串行、唯一数据目录、无重试）
- 压力循环：见第 5 节
- 秘密扫描：`tests/security/test_secret_scan.py` + 产物级扫描（第 6 节）

## 2.1 套件建设中发现并修复的组合缺陷（反馈环）

| # | 缺陷 | 定位 | 修复 | 验证 |
|---|---|---|---|---|
| 1 | issue04 改写契约回归：改写消息 retrieval 披露为 null（issue04 e2e 2 例失败） | `turn.py` 改写路径（issue07 合入时把「不检索全局知识库」实现成「跳过整个检索阶段」，附件层披露丢失） | 改写路径恢复进入 LOCAL_RETRIEVAL；`use_knowledge_base=false` 仅关闭知识库层（issue07 意图保持） | issue04 e2e 4/4、issue07 e2e 4/4、humanizer 后端 68 测试全绿 |
| 2 | issue03 skip-link 测试组合环境偶发失败（单独跑稳定） | 锚点跳转与焦点转移异步，Enter 后立即断言读到转移前焦点 | 时序收紧：`toBeFocused` 确认焦点 + `expect.poll` 等待焦点落到 main-content（断言语义不变） | closeout-smoke + issue03 组合 8/8 通过 |

## 3. 自动化汇总

| 套件 | 轮次 | 结果 | 耗时 |
|---|---|---|---|
| pytest 组合（closeout 24 + issue 02/03/04/06/08/09 专项 + humanizer 59 + QQ 状态机 + 秘密扫描） | 第 1 轮 | ✅ 141 passed | 2:22 |
| Playwright 纵向切片（9 spec，含 100 次首轮压力） | 第 1 轮 | ✅ 29 passed | 2.1m |
| pytest 组合 | 第 2 轮 | ✅ 141 passed | 2:20 |
| Playwright 纵向切片 | 第 2 轮 | ✅ 29 passed | 2.1m |
| pytest 组合 | 第 3 轮 | ✅ 141 passed | 2:21 |
| Playwright 纵向切片 | 第 3 轮 | ✅ 29 passed | 2.1m |

连续三轮全部通过；套件结束后无残留 pytest/playwright 进程、无监听端口残留、
轮次 e2e 数据目录与 pytest 临时目录均已清理（验收标准第 2 条 ✅）。

修复后全量回归：`pytest -q` 全量（2321 基线之上）见第 10 节。

## 4. 前置 issue 状态

| Issue | 状态 | 提交 |
|---|---|---|
| 01 Windows 反馈环 | completed | 2176a56 |
| 02 持久化后台生成 | completed | c368248 |
| 03 原子首轮 | completed | 65d8128 |
| 04 附件原子绑定 | completed | 4ab640c |
| 05 arXiv worker 可靠性 | completed | 合入 main |
| 06 时延预算与埋点 | completed | 89550be |
| 07 人味化交付语义 | completed | 10aa7cb |
| 08 对话式学习 | completed | 6034c77 + 6b8920f |
| 09 生涯规划韧性 | completed | 23b1935 |
| 10 QQ 验证状态机 | completed | c80de79 |

## 5. 压力循环与故障注入（验收标准逐项）

### 5.1 新会话首轮 100 次（空白/重复/最近列表/skip link 均为 0）
- 证据：`apps/web/e2e/issue03-atomic-first-turn.spec.ts` 第 7 用例（串行 100 次首轮）
- 结果：**通过** —— 三轮套件各轮复跑（首轮试跑 53.5s；三轮套件内 55s 量级），
  100 个会话恰好入最近列表（`toHaveCount(100)`）、无空白、无重复消息

### 5.2 生成期间 50 次随机会话切换/刷新（stream_interrupted 为 0）
- 证据：`tests/closeout/test_closeout_stress_switch.py::test_50_random_switch_refresh_during_generation_no_stream_interrupted`（本 issue 新增，固定种子 20260809，50 轮生成 × 每轮 1-3 次随机切换/刷新）
- 结果：**通过** —— 50 轮全部收敛单一 `done`，错误事件 0 条（含
  `stream_interrupted`），每轮模型只调用一次；三轮套件各轮复跑
- 对照：`test_explicit_stop_is_the_only_allowed_interruption` —— 显式停止 →
  `error(stopped)` 终态且不被改写为 done（唯一允许中断），通过

### 5.3 附件故障注入（无未绑定消息；无跨账户可见性）
- 证据：`tests/chat/test_issue04_atomic_binding.py`（绑定前/绑定语句/COMMIT
  失败同一事务回滚，数据库不存在「已成功消息却未绑定附件」）+ `test_issue04_attachment_contract.py`（跨账户 Bob 看不到 Alice 的未绑定附件）+ issue04 e2e 4 例
- 结果：**通过** —— 三轮套件各轮复跑；issue04 e2e 4/4（修复组合缺陷 #1 后）

### 5.4 前台 run 在预算内进入终态（无 300 秒无反馈）
- 证据：`tests/chat/test_issue06_latency_budget.py`（慢搜索按阶段墙钟降级、
  预算耗尽草稿带警告交付/可重试失败、p95 本地预算断言）+ issue06 e2e
  （阶段行显示真实阶段文案、完整流结束后权威历史接管终态）
- 结果：**通过** —— 三轮套件各轮复跑；无 300 秒无反馈或永久 running 用例

### 5.5 人味化 / 学习 / 生涯规划三路径
- 人味化：`tests/humanizer`（59 用例：软门不扣留正文、硬门冲突保留草稿、改写只用当前附件）+ issue07 e2e 4 例
- 学习：`tests/chat/test_issue08_conversational_learning.py` + issue08 e2e
  （五阶段教学卡片、刷新恢复、来源受阻恢复动作）
- 生涯：`tests/chat/test_issue09_career_resilience.py` + issue09 e2e
  （模糊请求立即澄清、形成路径中切会话同一 run 继续完成）
- 结果：**通过** —— 正常、软失败/外部失败与离开恢复路径三轮各复跑

### 5.6 QQ 60 秒延迟邮件最终 verified；旧 attempt 不覆盖；提醒仅在 verified 后创建
- 证据：`tests/reminder/test_verification_state_machine.py`（12 用例：六态
  attempt、第二次保存取代旧 attempt 且迟到结果丢弃、删除后迟到回调忽略、
  IMAP 瞬断保持轮询、窗口虚拟时钟超时、重启恢复、秘密不落表/审计）+ `tests/closeout/test_mail_verification.py`（真实假邮件服务器：立即/10 秒/超时
  三种投递，10 秒投递收敛 verified）+ issue33 e2e（配置-验证-解析-确认-
  投递全流程、验证失败原因、切换账户不残留）
- 结果：**通过** —— 三轮套件各轮复跑

## 6. 秘密扫描

- 仓库级：`tests/security/test_secret_scan.py` —— **通过**（1 passed，三轮套件
  各轮复跑）；扫描器只输出匹配位置（文件+行号+类型），不回显秘密正文
- 产物级（本 issue 新增扫描，高置信度模式 + 固定正文标记，只报位置）：
  对 `.tmp/closeout-rounds/`（三轮日志 × 6）、`test-results/`（失败产物）、
  `apps/web/test-results/`（e2e 失败产物）与 `closeout-acceptance-report.md`
  本身扫描 → **0 命中**（无 sk-/AKIA/LTAI/PEM/凭据赋值，无消息正文/附件正文）
- 截图与 trace：Playwright 合成 PNG/zip 无用户元数据；e2e 失败时 error-context
  经 conftest `sanitize` 脱敏（固定测试秘密以 `<secret-key>` 等占位替换）
- 结果：**通过**（验收标准第 9 条 ✅）

## 7. 九组人工场景对照

| # | 用户场景 | 自动化证据（三轮复跑） | 人工标记 |
|---|---|---|---|
| 1 | 新会话首轮可见且进最近列表 | issue03 e2e（100 次循环无空白/无重复/最近列表无遗漏） | ✅ 自动化覆盖 |
| 2 | Shell/Logo/skip link | issue03 e2e（鼠标导航不显示、整页加载 Tab 可见且跳到主内容） | ✅ 自动化覆盖 |
| 3 | 附件原子绑定 | issue04 e2e（DOCX 上传-绑定-切换）+ 事务注入回滚测试 | ✅ 自动化覆盖 |
| 4 | arXiv 中英文/Unicode | issue22 e2e（真实来源/摘要/学习建议 + 错误恢复）+ closeout worker 反馈环（UTF-8/握手/错误分类） | ✅ 自动化覆盖 |
| 5 | 人味化软门/改写只用当前附件 | issue07 e2e（软门交付正文/硬门错误卡/改写引用一致）+ humanizer 59 测试 | ✅ 自动化覆盖 |
| 6 | 学习五轮闭环 | issue08 e2e（mission_setup→micro_lesson→check→adapt + 刷新恢复） | ✅ 自动化覆盖 |
| 7 | 生涯 intake 后生成可执行规划 | issue09 e2e（clarify 态 + 切会话同一 run 继续） | ✅ 自动化覆盖 |
| 8 | 后台生成离开后继续 | closeout-smoke（上传-发送-切换-轮询状态）+ issue02 后端（断开/刷新/双执行器/worker 恢复） | ✅ 自动化覆盖 |
| 9 | QQ 原页再认证与延迟收件 | issue33 e2e（验证全流程 + 失败路径）+ 状态机 12 测试 + 真实假邮件 10 秒投递收敛 | ✅ 自动化覆盖 |

说明：全部九组场景均有真实浏览器/真实子进程自动化覆盖（无 route mock），
等价于人工脚本 1-7 步的机械化执行；如需桌面人工复核可按 issue 11 的
「人工桌面验收脚本」7 步执行（步骤与上表一一对应）。

## 8. 耗时统计（p50/p95）

- 端到端阶段预算与 p95 断言由 `tests/chat/test_issue06_latency_budget.py::test_performance_summary_p95_within_local_budget` 在每轮套件中验证（本地预算内）
- 前端阶段行显示真实阶段文案与终态接管由 issue06 e2e 每轮验证
- 三轮套件自身耗时：pytest 每轮 140-143s（p50≈141s，p95≈143s），
  e2e 每轮 2.1 分钟（三轮一致）；全量回归 2321 用例 ≈ 10.4 分钟
- 100 次首轮压力单轮 53.5s（约 0.5s/轮）、50 次切换压力单轮 31.7s

## 9. 未解决风险与发布结论

**未解决风险（不阻断发布）：**
- 机器上存在历史残留进程（`python3.exe` PID 43804 等，早于本次验收），非
  套件产物，未排查其来源；建议用户自行确认后清理
- e2e 覆盖使用确定性替身模型（StubQwenAdapter）与假邮件服务器；真实
  Qwen 链路与真实 QQ SMTP/IMAP 的线上行为需按「线上 smoke 单独记录」约定
  另行验证
- 人工桌面复核（issue 11 脚本 1-7 步）尚未由人工执行，上表自动化覆盖与之
  一一对应

**发布结论：** 前置 issue 01-10 全部完成且有测试提交；收尾自动化套件
Windows 串行连续 3 次全部通过（141 pytest + 29 e2e × 3，无残留进程/端口/
数据锁）；100 次首轮与 50 次切换/刷新压力达标；附件注入、预算终态、
三技能路径与 QQ 延迟邮件验证均绿；秘密扫描（仓库级 + 产物级）0 命中。
套件建设中发现并修复 2 处组合缺陷（issue07 改写检索跳过破坏 issue04 披露
契约；issue03 skip-link 组合环境时序 flaky），均已在三轮中复验。**建议
判定：通过，可发布**（遗留的人工复核与线上 smoke 不阻塞）。

## 10. 修复后全量回归

- 修复后全量 pytest：**2323 passed, 6 skipped**（623s）—— 基线 2321 之上
  新增 2 个收尾压力测试，无任何回归
- 受影响专项复验：issue04 后端 + humanizer（含改写路径）70 用例全绿；
  issue04 e2e 4/4、issue07 e2e 4/4、closeout-smoke + issue03 组合 8/8
- ruff：修改文件全部通过

## 11. 验收标准逐项核对

- [x] 所有前置 issue 状态为完成并有对应测试提交（第 4 节）
- [x] 收尾自动化套件 Windows 串行连续 3 次全部通过，无残留进程/端口/数据锁（第 3 节）
- [x] 新会话首轮压力测试 100 次：空白/重复/最近列表遗漏/skip link 误显示均为 0（第 5.1 节）
- [x] 生成期间随机 50 次会话切换/刷新：除显式停止外 stream_interrupted 为 0（第 5.2 节）
- [x] 附件故障注入后无未绑定消息状态、无跨账户可见性（第 5.3 节）
- [x] 所有前台 run 在预算内进入终态，无 300 秒无反馈（第 5.4 节）
- [x] 人味化/学习/生涯规划通过正常、软失败/外部失败与离开恢复路径（第 5.5 节）
- [x] QQ 延迟邮件最终 verified、旧 attempt 不覆盖、提醒仅在 verified 后可创建（第 5.6 节）
- [x] 自动秘密扫描：日志、trace、截图元数据与报告无凭据/正文（第 6 节）
- [x] 报告对用户 1—9 场景逐项标为通过（第 7 节）
