# 04 — 自然自述形成可恢复长期记忆：验证记录

**分支：** `codex/issue-04-durable-semantic-extraction`（基点 `ecb6c38c`）。
**环境：** conda `agent`；代理 `127.0.0.1:7890`；真实主模型
`qwen3.7-plus-2026-05-26`（OS 凭据库，`credential_store`）。
**证据：** 本文 + `baseline-stub-20261015T121238Z.{json,md}`（确定性）+
`baseline-real-20261015T121238Z.{json,md}`（真实模型）+ 两份 `run-lock-*`
（含 git 脏标记、语料 SHA、凭据来源、调用合同版本）。
**可复跑：**
`python scripts/run_issue01_profile_baseline.py --mode both --proxy http://127.0.0.1:7890 --output .scratch/profile-memory-redesign/validation/04-semantic-extraction`

## 1. 基线语料对照（stub 与 real 同结果）

| 旅程 | 预期 | 实测（real） | 关键证据 |
| --- | --- | --- | --- |
| t01-name | pass | pass | qwen_model `succeeded_written`，原子条目「我叫小林。」 |
| t01-pet | pass | pass | 原子条目「我养了一只猫叫团子。」 |
| t01-habit | pass | pass | 原子条目「周末一般会去爬山。」（hobby 维度） |
| t02-psychology | pass | pass | 「心理学」不再被 `心理` 关键词误判为敏感，条目写入 |
| t03-time-limit | pass | pass | 两条原子条目：「我在学线性代数」+「我每晚只能抽出半小时」（时间限定保留） |
| t04-cross-turn | known_failing | known_failing | 邻句仍空（逆序读取 bug 属 05）；`preference_captured` 文本已写入但匹配词为「先给我结论」 |
| positive-academic-control | pass | pass | succeeded_written，原子条目 active（04 起语义分支承担） |
| recall-academic-control | known_failing | partial | 首轮写入成功；次轮 adopted 切片仍排除（D11 缺口属 05） |

- 确定性运行与真实运行 `baseline_match=True`（8/8 一致），真实运行 10 次
  模型调用、输入 4256 / 输出 766 token；抽取耗时样本 0.0–6.0s。
- T01–T03 均由实际聊天入口完成后台队列保存；路由日志显示自述消息
  `source=qwen_model`（语义分支），无新增逐例正则。

## 2. 真实模型合同缺陷的发现与修复

首轮真实运行全部 `profile_extraction_contract_invalid`。经契约调试脚本
（`captured` 网关原始输出）定位：模型在条目里自带 `open_fact` 字段
（由提示词「开放事实（姓名、宠物、习惯等）允许为 null」歧义诱发），而
`ProfileExtractionOutput(extra="forbid")` 拒绝额外字段；其余九个字段全部
合规（normalized_value/evidence_quote/区间/action 等）。

修复：收紧提示词——显式冻结字段名清单（含「出现 open_fact、category、
type 等任何其他字段都判为无效输出」）、把「开放事实」表述改为
「dimension 填 null」、`fact_text` 要求第一人称/无主语并给示例。修复后
8 条语料（含「今天好累。」应零写入）逐条通过合同校验，随后完成上述最终
两个运行。提示词版本保持 `profile-extraction-prompt-v5`（本次拆分引入的
版本从未提交，尚在外环迭代中）。

## 3. 自动化测试

- `pytest tests/profiles`（含新增 `test_issue04_semantic_extraction.py`
  26 项）：全过；连同两条 evaluation 文件共 590 项通过。
- `pytest tests/evaluation`（与 main 同命令）：分支 12 失败，main 13 失败，
  **失败集差集：新增 0、修复 1**（`test_positive_academic_control_...`；
  其余 12 项为 main 既有且逐条同名）。
- 全量 `pytest --continue-on-collection-errors`（4 个 career_plan 收集错误
  两树相同）：
  - 分支：342 failed / 5576 passed / 39 skipped。
  - main：345 failed / 5550 passed / 37 skipped。
  - 差集：**新增 0**；main 有而分支无 3 项 = 正向对照 1 项 + main 工作区
    `.venv` 缺 `bridges` 导致的 `test_api_boot` 环境性失败 2 项（分支在
    conda 下运行，回退 `sys.executable` 而通过；两树单独复跑已验证）。
- `ruff check`：改动文件 19 条与 main 同文件同规则完全一致（均既有
  I001/E501）；新增代码 0 条。`mypy`（改动源文件）：0 错误。

## 4. 接口与行为变化

- 合同：`dimension` 可选、`items` 上限 6、输出合同
  `profile-extraction-v3`（`PROFILE_EXTRACTION_CONTRACT_VERSIONS` 三版并存，
  网关按合同映射 `profile_extraction_contract_invalid`）。
- 调度：仅空消息与整条硬禁止跳过；其余（含 NO_SIGNAL、疑问句、情绪短句）
  进入语义队列，模型失败不写入；异步调度新增按账户背压
  （`PROFILE_EXTRACTION_MAX_PENDING_PER_ACCOUNT=32`，
  `PROFILE_GUARD_SCHEDULING_BACKPRESSURE`）。
- 路由：本地规则只承担更正/行为观察/硬禁止；自然自述与学业自述统一走
  语义分支（本机组合仍按同规则走网关替身）。
- 证据核验：证据引用精确子串定位并核验主体（含分句自述守卫与第三方
  排除）、否定双向、时间等价（半小时=30 分钟）、条件词保留；取值锚定
  采用宽容匹配；找不到原话引用为永久性失败，不重投。
- 记忆线索：网关抽取新增相关旧记忆（同账户、有界 5 条、词面匹配），
  仅作表述参考；隐私说明升级为诚实的「语义策略」文案（
  `profile-privacy-v3`）。
- 评测：驻留语料 v2（T01–T03 预期改为 pass）；确定性替身提供回声提案。

## 5. 剩余限制与边界

- T04 邻句范围（逆序读取）未修，按拆分属工单 05；`preference_captured`
  检查词「先给结论」与已写入文本「先给我结论」不同词形，随 05 一并校准。
- 召回采用缺口（recall 快照 `excluded=与当前问题无关`）属 D11/工单 05。
- 「今天好累。」类短期情绪在语义分支返回空提案；是否省一次模型调用属
  成本优化，留给后续票评估。
- 运行锁 `dirty=True` 为提交前生成所致；提交后 hash 以最终提交为准。
