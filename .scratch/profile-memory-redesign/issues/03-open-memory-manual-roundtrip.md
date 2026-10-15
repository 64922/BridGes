# 03 — 打通开放记忆的手动管理与单一权威写入

**What to build:** 用户能记住、查看、编辑和删除四分类之外的事实，重启后继续管理，相关下一轮回答可采用；新写入统一进入开放原子记忆。

**Blocked by:** [02 — 记忆提交与历史读取边界](02-memory-write-boundary.md)。

**Status:** ready-for-human

**发布状态：** 2026-10-15 用户已确认拆分与依赖，正式发布；尚未实施。

**对应合同：** D01、D09、D11；T01、T10、T17、T20。参见[定稿](../PRD.md)、[共同执行约束](../README.md)。

## 用户演示

说「记住，我养了一只猫叫团子」或主动添加同类事实，在现有页面查看和编辑；下一轮相关提问采用修改后的内容，删除后不再采用，重启状态仍一致。

## 依赖与实施范围

02 提供唯一提交接缝。此票只扩展一条明确事实从输入到采用的完整路径，同时完成写模型的必要一致切换。

- 建立开放事实合同及迁移：正文、主体/关系/对象、证据、权威、版本、范围/时间、生命周期和开放主题；主题可空、多选、扩展。
- 打通显式记住、手动添加、存储、现有条目页面、编辑删除及基本用途采用，不要求先完成概览。
- 活动自动提取和明确更正调用方直接使用新合同，移除四维与原子镜像内部双写，不新建旧类型兼容转发层。自然表达覆盖由 04 扩展。
- 存量只读和导出义务保留；外部协议按 01 清单评估，不能把内部旧维度继续作为新写入约束。
- 基础账户、用户编辑优先、删除失效、记录/使用开关和目标版本检查在本票完成；08 只是加强语义与跨来源抑制。

## 验收标准

- [x] 姓名或宠物等事实可以保存、编辑、删除及重启读取，不要求伪造四维类别。
- [x] 新主题或无主题不拒绝事实，多主题不复制条目，主题修改不改变身份。
- [x] 手动修改高于自动提案，陈旧版本与跨账户访问被拒绝。
- [x] 基本相关采用尊重当前要求、使用开关、删除状态，未保存不能声称记住。
- [x] 新写入不再产生四维记录/镜像副作用，所有活动调用方可运行，历史只读可用。
- [x] 新状态在本票完成迁移、导出、账户删除、备份/恢复和幂等验证，不推迟给最终票。
- [x] 页面空态、保存/取消、删除确认、错误与重试可用，协议、领域和视图独立转换。

## 验证与交付

通过真实聊天和页面走通保存→编辑→相关回答→删除→重启不恢复；核对真实数据库写入目标、隔离和迁移对账。交付开放合同、唯一提交入口、读投影、基本采用/失效接缝与迁移结果。

## 明确不做

不做历史语义重提取、概览生成或通用知识图谱，不以过渡为由维持内部双写。

## Comments

2026-10-15：用户回复「按此发布」，确认本次拆分与依赖。已发布为 ready-for-agent，尚无实施记录。

### 2026-10-15 实施与验证（完成）

**验证版本：** 分支 `codex/issue-03-open-memory-manual-roundtrip`（基点
`801fb589`）；证据与记录随本提交入库。

**交付物：**
- 开放事实合同与迁移：`AtomicProfileItem`/`ProfileFactSubmission`，迁移 80
  增加 `profile_items.topics_json`（主题可空、多选、扩展，不参与身份）。
- 唯一权威写入：`AtomicProfileService.remember/add_item/modify_item/
  delete_item/commit_fact/correct_fact`；自动提取、明确更正、回放与页面
  手动添加全部落入 `profile_items`；移除 `mirror_record`/`_merge_item`
  双写（`_merge_fact` 替代），`ProfileCommit` 参与者为 state+items。
- 读投影与页面：`POST/GET/PATCH/DELETE /profiles/items`（合同已再生）；
  `AtomicProfileCenter` 手动添加/主题 chips/空态/错误重试。
- 验证记录：`validation/03-open-memory/verification-record.md` +
  `evidence/demo-walkthrough-20261015.txt` + 可复跑工具
  `tools/walk_open_memory_demo.py`。
- 测试：新增 `tests/profiles/test_issue03_open_memory_manual_roundtrip.py`
  （4 项）；转换/更新 issue17 异步证据、聊天更正文案、三条旅程与横向回归
  收口门到新权威路径。

**检查记录：**
- 演示走查（真实 SQLite）：记住→编辑→相关采用→删除→重启一致；删除后
  回放不再写入；`profile_four_dimension_records=0`；账户隔离；`schema_version=80`。
- 收口范围（profiles+chat+closeout，与 main 基线同命令）：新增失败 0、
  修复 3（issue01 更正 1 项、api_boot 2 项）；全量 346 failed / 5543 passed
  / 39 skipped，范围外失败抽样在 main 同命令复现（既有）。
- `tests/contracts` 通过；前端 vitest 289 项、`tsc --noEmit`、ruff 全过。

**剩余限制：**
- `correction_count>=3 → PROTECTED` 旧语义放弃；用户权威由
  `write_origin=user` + `user_edited_at` 承担。
- 聊天更正不把条目翻转成 USER origin，连续聊天更正持续生效；
  「你修改过」标记只反映页面编辑/明确记住。
- `parse_fact_identity` 的 MAJOR 启发式对目标句（含「专业」等）加守卫，
  显式目标标记或 STAGE_GOAL 维度优先。
- 旧 `/profiles/four-dimensions` 只读路由保留（历史/存量），退役在 19。

Status 改为 `ready-for-human`。
