# 05 — 修复用户画像管线：页面配色、抽取 400、规则句型与脏数据清理
Status: ready-for-agent
Blocked by: 无
Covered requirements: 三次改进#3

## 背景与根因

用户报告两个现象：画像页文字颜色淡到几乎看不见；聊过"我想学习卷积神经网络相关知识"和"我是一名大三的人工智能专业学生，给我规划一下考研进度"之后，画像页"感兴趣的知识""阶段目标"等分区仍一片空白，系统没有越来越懂用户。两个现象各有独立根因，本 Issue 一并修复。

**Bug A（页面文字不可见）**：`apps/web/src/components/account/profile/FourDimensionProfileCenter.module.css` 整套按深色背景硬编码配色，但应用外壳默认浅色主题（`apps/web/src/styles/globals.css:80` `--color-bg-primary: #faf9f5`，body 应用于 `globals.css:239`；深色需 `<html data-theme="dark">`，见 `globals.css:170-171`），且该文件 0 处设计 token 引用。实测对比度（算法同 `scripts/check_contrast.py`）：`.page` 文字 `#f4f6f8`（:5）落在浅底上 **1.03:1**——h1"四维画像"与各分区 h2 都继承此色，即"看不见的标题"；`.eyebrow` `#91c8ba`（:15）1.78:1；`.empty,.time` `#8f9ba3`（:59-63，空态"暂无记录"与时间戳）2.70:1；`.group,.record` 白透明边框/背景（:32-37）在浅底上同样不可见；按钮文字 `#e8efee`（:102）1.11:1。同目录其他模块全部使用 token（如 `AccountSwitcher.module.css:25` 用 `var(--color-text-primary)`）；`apps/web/src/components/bridges/welcome.module.css` 虽也硬编码深色，但自带深色背景（:8 `background-color:#14130f`），自洽不出错——画像模块只设浅色文字、不设背景，是直接缺陷。漏网原因：`scripts/check_contrast.py` 只校验自身 `PAIRS` 清单（`check_contrast.py:27` 起）里的 token 组合，不扫描组件 CSS。

**Bug B（画像不记录）**：主根因是生产/开发环境抽取链路整体失败，有数据库铁证。链路：每条用户消息经 `ChatService._process_profile_effects`（`src/bridges/chat/service.py:1153`，调用包在 `contextlib.suppress(Exception)` 里，:1172-1181，失败用户无感知）→ `AutomaticProfileService.preprocess_message`（`src/bridges/profiles/automatic.py:735`）→ 抽取器选择（`src/bridges/api/main.py:1043-1045`：仅 `environment=="test"` 用规则抽取器，其余环境走 `GatewayAutomaticProfileExtractor`，能力 `qwen_profile_extraction@1` 注册于 `main.py:257` 起，`model_id="qwen3.6-flash"` 见 :262，适配器绑定 :995）。适配器对 `qwen3.6-flash` 发送 `response_format={"type":"json_schema",...,"strict":True}`（`src/bridges/ai/qwen_adapters.py:139-145`、:191-199）。

400 根因已实弹确诊：业务空间端点对 `qwen3.6-flash` 的 JSON 模式（`json_schema`/`json_object` 均算）强制校验"messages 中必须出现单词 json"（大小写不敏感的子串匹配），而画像生产 prompt（`automatic.py:353-358` 的全中文 system + 用户消息）通常不含 "json"，被上游以 400 拒绝（`invalid_parameter_error`："'messages' must contain the word 'json' ..."）。已实测对照：原样请求 400；system 里加"以 JSON 输出。"即 200；去掉 response_format 200；同内容换聊天模型 `qwen3.7-plus-2026-05-26` 200——排除了模型 id 无效与内容触发两种假设。失败在 `qwen_client.py:180-185` 归为 `client_error_400, retryable=False`，随后被压扁为错误码字符串（`automatic.py:367-370`），持久重试不区分 retryable 标志，3 次后 `_exhaust_task`（`automatic.py:1050` 起）置 EXHAUSTED。bridges.db 实证：`profile_four_dimension_records` 全表 0 行；两条种子消息在 `profile_extraction_runs` 中均为 `status=exhausted, last_error=client_error_400, attempts=3, record_ids=[]`；`profile_extraction_tasks` 3 行全部同状态。上游响应体未落库（只存错误码），所以这次根因只能靠实弹回放确诊。

次级缺口：即使网关修好，`RuleBasedAutomaticProfileExtractor`（`automatic.py:241-317`）对"我想学习卷积神经网络相关知识"仍产出 0 条——阶段目标模式要"目标/我计划/我打算"（:263-266），兴趣模式要"我对X感兴趣/我喜欢X"（:277-280），学业模式要"我在读/就读/是"（:296），观察兜底还要疑问词（:305-312）。"我想学习X"不在任何模式内；"给我规划一下考研进度"也不是"我计划/打算"句式（第二条种子消息能命中学业模式，所以只要网关可用它本应被记录一条学业）。另有三处配套缺口，修复句型时必须同步处理，否则验收仍不过：

- 兴趣维度的明确自述判定 `_is_explicit_self_statement`（`automatic.py:1177-1182`）只认"我对…感兴趣/我喜欢"；不扩展的话，"我想学习X"首次提及会落入"90 天内 ≥2 条不同消息观察才提升为记录"的收紧路径（`automatic.py:1125-1135`），首次发送后分区仍为空。
- 信号预检 `_PROFILE_SIGNAL`（`automatic.py:54-57`）不含"给我规划/帮我规划"类起手——独立成句时抽取根本不会触发（"我想学习…"因含"我想"可通过预检）。
- 规则抽取器当前是 if/else 链，每条消息最多产出 1 条（:260-317）；复合句"我是大三学生 + 给我规划考研"只能落一条，而合同允许最多 4 条（`src/bridges/contracts/profile_extraction.py:54`）。

## What to build

四项修复，均为最小改动：

**(a) 画像页 CSS token 化**：把 `FourDimensionProfileCenter.module.css` 全套硬编码深色配色改为设计 token（文字、边框、背景、状态色全部走 `globals.css:80-112` 浅色组 / :171-186 深色组变量），浅色与深色主题下都清晰可读。参照同目录 `AccountSwitcher.module.css` 等模块的 token 用法。只换颜色来源，不改布局结构（页面结构改版属 Issue 06）。验收按 `scripts/check_contrast.py` 的思路逐组合校验。

**(b) 抽取 400 修复**：`automatic.py:353-358` 的 system prompt 末尾加"以 JSON 输出结果。"（含 "JSON" 字样；上游规则按大小写不敏感子串匹配）。已实测 200，保留 `qwen3.6-flash` 与 json_schema+strict 强约束。同时 `qwen_client.py:180-185` 构造 `AdapterError` 时带上截断（约 500 字符）并清洗后的上游 message，让 `last_error` 今后能直接排障，不再依赖实弹回放。

**(c) 规则兜底抽取器句型扩展**：`RuleBasedAutomaticProfileExtractor` 覆盖高频句式——"我想学习/我想学 X"→`knowledge_interest`；"给我规划/帮我规划 X"→`stage_goal`。同步处理背景与根因中列出的三处配套缺口（自述判定、信号预检、多条产出）。

**(d) exhausted 历史脏数据处理**：给出 `profile_extraction_runs`/`profile_extraction_tasks` 中 `exhausted/client_error_400` 存量行的清理策略并落地：默认重跑（重置为 pending，由 `run_retry_tick` 自动重处理），已 tombstone 的消息跳过；重跑后仍失败的行保留 exhausted 并备注。执行结果可复查（处理行数、跳过行数）。

## Implementation notes

1. **CSS**（`apps/web/src/components/account/profile/FourDimensionProfileCenter.module.css`，全文件 184 行逐处替换）：`.page` 文字 `#f4f6f8`（:5）→ `var(--color-text-primary)`；`.eyebrow` `#91c8ba`（:15）→ `var(--color-text-secondary)`（或 accent 系 token，按视觉定）；`.group,.record` 边框 `rgba(255,255,255,0.11)` 与背景 `rgba(255,255,255,0.045)`（:32-37）→ `var(--color-border)` / `var(--color-surface)`；`.record` 内层背景 `rgba(0,0,0,0.12)`（:77）→ `var(--color-bg-secondary)`；`.empty,.time` `#8f9ba3`（:59-63）→ `var(--color-text-tertiary)`；按钮组（:96-105）文字 → `var(--color-text-primary)`、边框 → `var(--color-border-strong)`，hover 边框 `#91c8ba`（:107-111）→ `var(--color-accent-primary)`；`.withdraw` `#e8aaa0`（:113-115）→ `var(--color-status-error)`；`.state` `#aeb8c0`（:117-119）→ `var(--color-text-secondary)`；`.error`（:121-127）→ `var(--color-status-error)` / `var(--color-status-error-bg)`；`.dialog` 背景 `#172027`（:139-146）→ `var(--color-surface-elevated)`，其边框同步 token 化；`.dialog textarea`（:153-163）文字/边框/背景同步；`.dialogActions .primary`（:170-174）→ `var(--color-accent-primary)` + `var(--color-text-on-accent)`；`.dialogBackdrop` 半透明深色遮罩（:136）两个主题通用，可保留。替换后用 `check_contrast.py` 的算法（正文 ≥4.5:1、图标/边框类 ≥3:1，阈值口径见 `check_contrast.py:27-67`）逐一复核所有文字/背景组合，深浅主题各核一遍；可把新组合补进 `PAIRS` 常量化，或附手工计算记录，验收以算法结果为准。
2. **400 修复**（`src/bridges/profiles/automatic.py:353-358`）：在 system prompt 字符串末尾追加"以 JSON 输出结果。"。保持 `json_schema` 载荷（:362）与 strict（`qwen_adapters.py:191-199`）不变；不要改用 `json_object`（丢 schema 校验），也不要只去掉 strict（实测 200 的前提是消息含 json 字样，不解决根因）。
3. **错误落库增强**（`src/bridges/ai/qwen_client.py:180-185`）：读取上游错误响应体，`AdapterError.message` 带上截断约 500 字符、经 `src/bridges/observability/scrubber.py`（`scrub_value`/`scrub_payload`）清洗的上游 `error.message`。下游落库链路（`automatic.py:367-370` → run/task 的 `last_error` 列）无需改结构，字符串自然带上。注意不要回传 Authorization 头等敏感信息。
4. **规则句型**（`automatic.py:241-317`）：阶段目标模式组（:263-266）加"(?:给|帮)我规划…"类；新增"我想学(习)?…"→`KNOWLEDGE_INTEREST`（直接判知识兴趣，不要走 :283-287 的 `_KNOWLEDGE_TERMS` 分流——"卷积神经网络"不在词表内会被错判为 hobby）；`_is_explicit_self_statement`（:1177-1182）兴趣分支补"我想学(习)"，否则触发 :1125-1135 的二次观察门槛；`_PROFILE_SIGNAL`（:54-57）补"(?:给|帮)我规划"类起手；把 :260-317 的 if/else 链改为逐维度匹配、可产出多条 item（上限沿用合同 4 条），`_FORBIDDEN_SIGNAL` 门禁（:257）保持不变。网关抽取器路径不改。句型表只加本项列出的高频句式，不顺手扩面。
5. **脏数据**（表定义 `src/bridges/storage/database.py:1782-1820`；仓储 SQL `automatic.py:549-558`、:583-592；重试循环 `automatic.py:918` `run_retry_tick`，由 `src/bridges/chat/run_executor.py:138`、:153 驱动）：做一次性重跑——把 `status=exhausted AND last_error LIKE 'client_error_400%'` 的 runs/tasks 重置为 `pending`（`attempts` 保留或清零均可，在脚本注释里写明选择），tombstone 消息跳过（判定语义见 `automatic.py:1136` 的 `is_message_tombstoned` 调用）。建议放 `scripts/` 下一次性脚本，打印处理/跳过行数；不要用 schema 迁移静默改业务数据。重跑必须在 (b) 修复上线后执行，否则再次耗尽。
6. **测试**（`tests/profiles/test_automatic_profile_extraction.py` 等）：新增用例——"我想学习卷积神经网络相关知识"→ `knowledge_interest` CREATE 且首次提及即落记录；"我是一名大三的人工智能专业学生，给我规划一下考研进度"→ 同时落 `academic_status` 与 `stage_goal` 两条；"给我规划一下考研进度"独立成句也能触发并落 `stage_goal`；断言生产 system prompt 含 "JSON" 字样（防回归）。`tests/profiles/` 现有 8 个测试文件保持绿色。前端本项只动颜色值、不动选择器与 DOM 结构，tsx 无需改。

## Acceptance criteria

- [ ] 画像页所有文字对比度达标（`check_contrast.py` 同算法复核：正文 ≥4.5:1、图标/边框类 ≥3:1），浅色与深色主题下标题、眉题、空态、时间戳、按钮、对话框均清晰可读。
- [ ] 修复后发送"我想学习卷积神经网络相关知识"，画像页"感兴趣的知识"分区出现对应记录（`profile_four_dimension_records` 有对应行）。
- [ ] 发送"我是一名大三的人工智能专业学生，给我规划一下考研进度"，"学业情况"与"阶段目标"分区均有对应记录。
- [ ] 规则抽取器对上述句式在 test 环境同样落记录；生产网关路径经真实凭证冒烟返回 200 并成功抽取（`profile_extraction_runs` 出现 `succeeded` 行）。
- [ ] 新发生的 4xx 失败在 `last_error` 中带截断清洗后的上游 message。
- [ ] exhausted 脏数据按策略处理完毕：应重跑的已重跑并有终态，跳过的有明确说明，处理/跳过行数可复查。
- [ ] `pytest tests/profiles/` 全绿（含新增用例）；前端 `npm run typecheck` 通过。

## Verification

- `pytest tests/profiles/`（现有 8 个文件 + 新增用例）；改动文件过 `mypy --strict` 与 `ruff`。
- `cd apps/web && npm run typecheck`；必要时跑 `apps/web/e2e/issue25-profile-center.spec.ts`、`issue38-a11y.spec.ts` 相关用例。
- `python scripts/check_contrast.py` 继续全过；画像页新配色组合按同算法复核（深浅两主题）。
- 真实凭证冒烟：用业务空间 key 发一条种子消息，确认网关 200、run 终态 `succeeded`、页面出现记录。
- 手工页面走查：浅色/深色主题各看一遍画像页（空态、有记录、修改/撤回对话框）。

## Non-goals

- 不做画像页面结构改版、把握度/证据/来源展示（属 Issue 06）。
- 不换抽取模型（`qwen3.7-plus-2026-05-26` 是已实测 200 的备选，但更贵，仅在 prompt 修复失效时再考虑）。
- 不改 `json_object`、不动 json_schema strict 约束；不做适配器层自动补 "json" 字样的通用化（可作为后续加固，保护 `qwen_structured_output` 能力）。
- 不重做重试调度策略（如 retryable 标志参与调度），只修复当前确定性失败。
- 不扩展 (c) 所列之外的句型覆盖面，避免误抽取回归。

## Blocked by

无。

## Comments

400 根因结论来自实弹诊断：同一业务空间端点 + 生产 key 下的对照实验（原样 400 / 加"以 JSON 输出。"200 / 去 response_format 200 / 换 qwen3.7-plus 200 / json_object 且含 json 字样 200）。bridges.db 中当天成功/失败交错系上游灰度或路由差异，当前稳态是确定性失败，按确定性失败修。两份调查报告已归档：`.scratch/end/references/report-user-profile.md`（模块调查，含 DB 实证与全部 file:line）、`.scratch/end/references/report-profile-400-diagnosis.md`（实弹确诊）。

安全事项（来自诊断报告，与本 Issue 代码改动无关但需用户知晓）：排查凭证时业务空间 key（`sk-ws-` 开头）、`BRIDGES_SECRET_KEY`、QQ SMTP 授权码曾进入诊断会话日志，建议轮换；明文密钥文件宜改用 `BRIDGES_QWEN_API_KEY_FILE` 指向权限受控文件。
