# 工单 40 独立验收

状态：**已达标**（2026-10-05 独立验收第二轮）。最终真实配对、全量回归、两轴复审与证据入库均已完成；授权合并 main、经代理推送并清理 Issue 工作树。

## 依据与快照

- 工单 40、仓库 AGENTS/CONTEXT、工作流交付与上下文工程文档；交接文件仅作定位线索。
- 上下文改善前对照树：`7818c34b1cc425081b54fd65cedf87d61c80fefb`（临时对比工作树，验收后删除）。
- 被评分支提交：`a173bc65ab563056be6e55ac73173f102fba49a1`（最终配对时 working tree `dirty=false`）。
- 当时 main：`9e91d86b9df6c2d342890188a2d099c3242828c7`。
- 开发验证使用 conda `agent`（Windows、Python 3.11）。两轴审查使用 `code-review` 技能。

## 独立修复（编码代理之后新增）

1. `66f2d97f` 收紧长聊摘要硬门：删除 `extractor is None` 空通过，种子长聊必须有非空真实读取；`_provider_bounds` 对带 usage 的未完成调用同样核验，仅无功而返的失败尝试计入 unknown 并披露。
2. `b978d5d6` 摘要读取按本 case 会话消息 ID 归属，避免带退避的后台摘要重试跨 case 误判短聊无摘要。
3. `a173bc65` 修正澄清判定假阴性：合法请求背景但未命中旧词表的正确回答不再被判失败。
4. `334c1d3a` 修正配对报告把"有部分正文的明确受限中间轮"误列为质量门拦截。
每项均有确定性回归；最终 `tests/chat/test_improvement40_evaluation_acceptance.py` 19 passed。

## 最终真实配对（`20261005T075002Z`）

- 双方 `qwen3.7-plus-2026-05-26`，repeats=2，18 场景 × 2 = 36 case/侧；同窗口 16000/16000，两侧统一 `max_completion_tokens` 总输出合同；`dirty=false`，评测/语料 SHA 双方一致。
- 新侧 36/36 通过。旧侧失败：`tail-constraint#1`、`long-history-summary#1/#2`（无摘要读取证据）、`calibration-formula#1/#2`、`calibration-url#1/#2`、`old-photo-detail#1/#2`（旧图重读硬门）、`long-history-batches#1/#2`（无摘要读取证据）、`file-tail-condition#1/#2`。
- 量表（旧→新）：constraint_retention 7/10→10/10、correction_override 4/4→4/4、object_resolution 4/4→4/4、reference_correctness 8/10→10/10、honest_clarification_gap 4/4→4/4、isolation 2/2→2/2。
- 质量门拦截：两侧均无。新侧中间轮出现明确 `output_budget_exceeded`（中文"缩小问题范围"提示）：`tail-constraint#1/#2` turn2、`task-roundtrip#1` turn2、`task-roundtrip#2` turn3；终轮与指定结论均正常完成，无空正文 `done`。
- 成本（旧→新）：调用尝试 64→72（含摘要）；输入 78398→95009；输出 21125→26139；聊天轮 64→64 次、输入 78398→84113、输出 21125→24378；未知用量尝试 0→5；墙钟 422.2s→568.8s。
- 估算校准：旧侧编译估算 69525 vs 场景聊天输入比 1.128（低估）；新侧估算 130322 vs 调用后补记实测 80455（0.617），单次最大超出 -243，256 余量覆盖（仅已配对调用）。
- 摘要：新侧长聊均有真实非空读取（4053/5901 字符，含成功调用的输入/输出 token、来源 SHA 与读取范围），同步超时与校验拒绝如实保留；旧侧无摘要读取证据。
- 5 次未取得用量的尝试全部是失败/超时的摘要调用（无 usage、未完成），按摘要合同上限有界、逐条披露，不当作零成本；`provider_actual_bounds` 对"已完成但缺用量"返回 false，对带用量的未完成调用同样核验。

## 模型波动重放披露（工单任务 3）

- `20261005T071258Z`：`missing-reference#1` 因澄清检查词表过窄把正确请求背景的回答判失败；修复 `a173bc65` 后重放通过。
- `20261005T073125Z`：`task-roundtrip#2` 四轮全部思考耗尽 1024 总输出、正文为空；产品全部标记 `output_budget_exceeded`（无空成功），终轮无法回答故该 run 判失败；重放 `20261005T075002Z` 通过。属供应商思考随机性，产品选择诚实受限而非伪造完成。
- 失败轮原始 JSON 保留在运行目录（未入库以控制体积），结论已在本文件如实记录。

## 回归与静态检查

- focused：`130 passed / 1 skipped`（评测自测、cassette、Qwen 适配器、40 边界与语料、payload 预算、web_search）。
- 全量（`b978d5d6`，`-n auto`）：Issue `223 failed / 5338 passed / 39 skipped / 2 errors`；main 基线 `228 failed / 5317 passed / 37 skipped / 2 errors`。交集失败 222；Issue 独有 1 项 `test_handshake_timeout_maps_to_arxiv_handshake`，同文件串行复跑 14 passed，属并行时序波动；main 独有 6 项（runtime/security/arxiv 等既有环境问题）。其后仅 `a173bc65`/`334c1d3a` 修改评测脚本与其自测，产品代码未变，自测 19 passed。
- ruff：本票生产文件与评测脚本全部通过；既有 `tests/web_search/test_duckduckgo_service.py` 5 处 UP012 在 main 同样存在（基线，未顺手修）。
- mypy（6 个 src 文件）：两树同为 1 个既有错误（`turn.py` 的 dict.get `str | None`，仅行号偏移），无本票新增。

## 两轴复审（code-review 技能，最终轮）

- Standards：无阻断性文档标准违规。判断题坏味道逐条评估：脚本内重复常量/`_run_case` 偏长/评测与产品措辞耦合属于可接受权衡；`web_search` 会话内状态窄规则保留为最小必要接缝（外部时效反例回归保证不误伤），属判断题而非阻断。历史 `20261004` 配对报告为历史证据，最终结论以 `20261005T075002Z` 为准。
- Spec：初审指出的全量量表缺失（对象解析/诚实澄清/隔离 0/0）已由本次 18 场景全量配对补齐并全部通过；`web_search` 范围判断保留说明；`must_ask` 假阴性已修复。无未关闭的需求缺失。

## 剩余限制

- 未取得用量的失败摘要尝试无法核对实际消耗（已按合同上限有界并披露）；跨 case 的后台重试调用可能记入相邻 case 的成本明细，总量不受影响。
- 论文/GitHub 来源为双方一致的确定性端口，只验证正式模块续接，不证明外网可得性。
- token 校准为单一模型有限样本（六类材料），不宣称覆盖所有分词、语言或高分辨率图片。
- 输出额度 1024（思考+正文）下，思考模型偶发整轮耗尽；产品以明确受限与重试建议呈现，终轮失败会如实计入。
- `web_search` 会话内状态抑制是窄规则而非语义完备方案。

## 结论

工单 40 五项验收标准全部满足；Issue 分支可合并 main、经代理推送并清理 Issue 工作树与旧基线工作树。工单 35 的工作树/分支已由其自身任务合并清理完毕，本票未触碰其他任务残留。
