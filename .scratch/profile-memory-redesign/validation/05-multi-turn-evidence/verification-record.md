# 05 — 理解跨轮省略并展示可核验依据：验证记录

**分支：** `codex/issue-05-multi-turn-evidence`（基点 `b924c47c`）。
**环境：** conda `agent`（Python 3.11.15）；真实主模型
`qwen3.7-plus-2026-05-26`（OS 凭据库，`credential_store`）。本轮真实
运行直连 DashScope（本机未运行 7890 代理；`--proxy` 仅在需要时使用）。
**证据：** 本文 + `baseline-stub-20261015T150641Z.{json,md}`（确定性）+
`baseline-real-20261015T150907Z.{json,md}`（真实模型）+ 两份 `run-lock-*`
+ `evidence/demo-walkthrough-20261015.txt` 与 `-model-outputs.json`
（真实模型留出措辞走查）+ `tools/walk_cross_turn_demo.py`（可复跑）。
**可复跑：**
`python scripts/run_issue01_profile_baseline.py --mode both --output .scratch/profile-memory-redesign/validation/05-multi-turn-evidence`
（真实模式需要凭据库中的 Qwen Key；网络受限时加 `--proxy http://127.0.0.1:7890`）
`python .scratch/profile-memory-redesign/validation/05-multi-turn-evidence/tools/walk_cross_turn_demo.py --out ...`

## 1. 基线语料对照（stub 与 real 同结论）

| 旅程 | 预期 | 实测（real） | 关键证据 |
| --- | --- | --- | --- |
| t01-name | pass | pass | `succeeded_written`，原子条目「我叫小林。」 |
| t01-pet | pass | pass | 原子条目「我养了一只猫叫团子。」 |
| t01-habit | pass | pass | 原子条目「周末一般会去爬山。」 |
| t02-psychology | pass | pass | 「心理学」写入，正文字段 0 条（无推断） |
| t03-time-limit | pass | pass | 两条条目：学习内容 + 每晚半小时 |
| t04-cross-turn | pass（05 修复） | pass | 探测窗含邻句用户消息**与助手回复**；两次提取 run 的载荷均携带真实会话标识；第二轮写入偏好条目「以后先给我结论，再展开解释」 |
| positive-academic-control | pass | pass | `succeeded_written`，active |
| recall-academic-control | known_failing | partial | 写入成功；采用切片仍排除（D11 缺口，与 04 相同） |

- stub 与 real 逐条结论一致（8/8），t04 探测窗按位置给出
  `['用户：我在读计算机专业，大二。', '助手：…']`；真实运行 10 次模型
  调用、输入 4264 / 输出 1263 token。
- 运行锁：抽取器 `profile-auto-v2`、信号 `profile-signal-v3`、提示词
  `profile-extraction-prompt-v6`、输出合同 `profile-extraction-v4`、
  邻居上限 4。

## 2. 真实模型留出措辞走查（跨轮省略与来源竞态）

走查语料与测试替身、基线语料均不同（留出）：

1. 「我把软件工程填成了第一志愿的专业。」→ `succeeded_written`，条目
   写入，来源投影原话 `recorded`。
2. 「这个专业我打算一直读到硕士。」→ 模型结合窗口消解「这个专业」，
   输出片段证据 `{ref=邻句消息, quote=软件工程}`；服务端核验通过并写入
   「我打算把软件工程这个专业一直读到硕士。」，依据投影**两条来源**：
   当前消息原话（主引用）与邻句片段 `软件工程`，均 `recorded`——多片段
   引用完整、逐条可核对。
3. 来源竞态：直接改写第 1 条用户消息正文后重读依据，邻句来源
   `quote_status=source_unavailable`、原话不再展示；当前消息主引用不受
   影响。
4. 孤立「同意」：真实模型把上一轮的旧候选再次提出；主引用在本消息中
   找不到、在窗口中找到 → **暂缓**（零写入、不误归属、不计失败），
   新增条目 0——迟到/重复候选不会因「同意」被误存为计划。
5. 引用守卫实测：「我把『软件工程』填成了第一志愿的专业。」整条被判
   `quoted_or_relayed_text` → **不进入语义队列、零模型调用**（引用文本
   不被当作自述）。

模型原始输出（含逐条片段引用）存于
`evidence/demo-walkthrough-20261015-model-outputs.json`，可逐字复核。

## 3. 自动化测试

- 新增 `tests/profiles/test_issue05_multi_turn_evidence.py`（14 项）：
  窗口按位置取当前消息之前（含助手、无未来）；提取携带真实会话标识；
  跨轮片段核验-写入-投影；只带片段的候选暂缓；五类守卫片段（助手消息、
  窗口外引用、原话不在来源、重复原话、第三人内容）零写入；主引用只在
  邻句时**暂缓而非失败**；重复原话跨度消歧后写入/不一致暂缓；来源改写
  或删除后原话不再展示；片段合同形状与上限。
- 先失败后通过证据：临时还原 `app_support.py` 旧读取器 →
  `test_neighbor_reader_takes_position_prefix_including_assistant` 失败
  （旧读取器返回字符串、方向错误）；恢复修复后通过。
- 更新既有测试：04 重复原话改为暂缓语义（26 项通过）；17 邻居替身改为
  结构化消息（18 项通过）；评测运行器跨轮用例断言真实会话标识与窗口
  载荷；`preference_captured` 检查词改为已写入文本「先给我结论」；
  T04 预期改为 pass。
- 收口命令：`pytest tests/profiles tests/contracts tests/api
  test_profile_baseline_journey_runner.py test_profile_journeys.py`
  → **669 项全过**（含契约同步 2 项）。
- `tests/evaluation` 全量：分支 12 失败 / 288 通过；main 同命令同为
  12 失败（逐条同名），**新增 0**。
- 全量 pytest（xdist，`--ignore=tests/career_plan`，与 main 同命令同参数；
  两树 career_plan 收集错误 4 项相同）：
  - 分支：327 failed / 5484 passed / 37 skipped。
  - main：329 failed / 5468 passed / 37 skipped。
  - 差集：**新增 0**；main 有而分支无 2 项为 `test_api_boot` 环境性
    失败（并发端口/路径碰撞，分支复跑通过）。
- 前端：`AtomicProfileCenter.test.tsx` 17 项通过（新增逐来源片段展示
  用例）；`tsc --noEmit` 0 错误；`next lint`（改动文件）0 问题；
  `next build` 成功。
- `ruff`/`mypy`：改动文件无新增问题（automatic.py 余下 13 条 E501 与
  main 同文件同规则逐条一致；`__init__.py` I001 既有）。

## 4. 合同与行为变化

- 新合同：`ProfileNeighborMessage(message_id, role, content)` 结构化
  邻窗；输出合同新增 `evidence_fragments`（≤3，`extra=forbid`），
  `profile-extraction-v4` 与 v1–v3 并存受理；提示词 v6 明示片段规则与
  必须引用邻近用户消息中的**具体指代对象**（实测真实模型按此输出）。
- 窗口：按当前消息位置取之前最多 4 条（时序、含助手、**禁止未来**）；
  抽取期与提交期共用同一读取器；会话标识为真实会话 id（取不到时空串，
  由读取器裁决）。
- 证据判定：主引用必须指向当前消息；原话重复且区间未精确消歧 → 暂缓；
  原话只在邻句（任意角色）→ 暂缓；当前与窗口都找不到 → 永久失败
  （04 语义）；窗口读取失败 → 暂缓。片段须命中当前消息或窗口内**用户**
  消息、原话唯一、通过硬禁止/自述主体/取值核验，否则暂缓；只有片段
  没有主引用一律暂缓。
- 存储与投影：`profile_items.source_quotes_json`（迁移 81，
  `SCHEMA_VERSION=81`）；依据投影逐来源给出 `quote` / `quote_status`；
  来源变化/删除时片段不再展示（不泄露不可核对正文）。
- 前端：依据详情逐来源展示已核验片段（与顶部主原话重复时不重复展示），
  不可核对时显示「该来源保存的片段已不可核对。」；`openapi.json` 与
  `packages/contracts/src/generated.ts` 已随合同重新生成。
- 评测：T04 预期改为 pass；运行器探测与提示词标记按新格式对齐。

## 5. 剩余限制与边界

- 本地信号分类器未覆盖「这个方向」类回指（`no_signal` 分句如实守门、
  零写入保守处理，不会误归属）；「这个专业」等学业回指正常承接。
  是否扩充被指代对象词表留给后续票（避免逐例正则）。
- 真实模型会在后续轮次重复提出旧候选（演示第 3 步捕获）：暂缓语义已
  防止重复写入与误归属；重复候选的模型侧收敛属提示词后续优化。
- 召回采用缺口（recall 快照 `excluded=与当前问题无关`）属 D11，仍未
  修复（与 04 相同，见 01 基线记录）。
- 运行锁 `dirty=True` 为提交前生成所致；提交后 hash 以最终提交为准。
