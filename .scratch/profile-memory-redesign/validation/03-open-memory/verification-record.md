# 03 — 开放记忆手动管理：验证记录

**分支：** `codex/issue-03-open-memory-manual-roundtrip`（基点 `801fb589`）。
**环境：** conda `agent`；`PYTHONPATH=<worktree>/src`。
**证据：** 本文 + `evidence/demo-walkthrough-20261015.txt` +
`tools/walk_open_memory_demo.py`（可重复运行）。

## 1. 演示走查（真实 SQLite，可复跑）

`tools/walk_open_memory_demo.py --out evidence/demo-walkthrough-20261015.txt`：

- 记住（聊天「记住，我养了一只猫叫团子」）：`profile_items` 1 行，
  `write_origin=user`，`profile_four_dimension_records=0`（无双写）。
- 页面编辑（行内改正文）：版本 1→2，`user_edited=True`，旧版本成为
  `withdrawn` 行（撤销留痕，正文脱敏为空）。
- 相关采用（query=圆子）：`adopted=['我养了一只猫叫圆子']`；删除后同一
  查询 `adopted_after_delete=[]`。
- 重启（同一库重装配）：行集合与删除后一致；旧消息回放写入
  `replay_after_delete_writes=False`（墓碑抑制复活）。
- 隔离：`account-bob` 条目为空。
- 迁移对账：`schema_version=80`（迁移 80 = `profile_items.topics_json`）。

## 2. 自动化测试

- `pytest tests/profiles`：548 项全过，其中新增
  `tests/profiles/test_issue03_open_memory_manual_roundtrip.py`（4 项：
  演示走查/主题可空多选扩展/用户权威与陈旧版本与跨账户/删除跨重启抑制）。
- `pytest tests/profiles tests/chat tests/closeout`（与 main 基线同命令）：
  分支 99 failed / 1807 passed，main 基线 102 failed；**失败集差集：新增 0、
  修复 3**（`tests/profiles/test_issue01_chat_profile_correction.py` 1 项、
  `tests/closeout/test_api_boot.py` 2 项）。
- `pytest tests/contracts`：通过（openapi 与生成物同步）。
- 全量 `pytest`（忽略 main 同样存在的 4 个 `tests/career_plan` 收集错误）：
  346 failed / 5543 passed / 39 skipped；范围外失败抽样在 main 上同命令
  复现（`tests/mcp` 等 6 目录 main 85 failed / 45 passed），属既有失败。
- 前端：`npx vitest run` 34 files / 289 tests 全过；`npx tsc --noEmit` 无输出。
- `ruff check` 全部改动文件通过。

## 3. 接口与数据变化

- 新契约：`POST/GET/PATCH/DELETE /profiles/items`（见 `openapi.json` 与
  `packages/contracts/src/generated.ts`，已再生）。
- 唯一写入路径：`AtomicProfileService.remember/add_item/modify_item/
  delete_item/commit_fact/correct_fact`；自动提取、明确更正、回放、页面
  手动添加全部落到 `profile_items`。
- 移除双写：`mirror_record`/`_merge_item` 删除；`ProfileCommit` 参与者为
  state+items，`write_facts`/`write_correction` 直写原子条目。
- 主题：`topics_json` 可空/多选/扩展，只组织浏览，不参与事实身份。

## 4. 剩余限制与边界

- `correction_count>=3 → PROTECTED` 的旧语义放弃；用户权威由
  `write_origin=user` + `user_edited_at` 承担（页面编辑/明确记住才标记）。
- 聊天更正不把条目标记为 USER origin，因此连续不同值的聊天更正可持续
  生效；「你修改过」标记只反映真实用户动作。
- `parse_fact_identity` 的 MAJOR 启发式对「考211相关专业」类目标句加了
  守卫（显式目标标记或 STAGE_GOAL 维度优先），否则会顶掉学业状态。
- 旧 `/profiles/four-dimensions` 只读路由保留（历史/存量读取），新写入
  不再产生四维记录；退役与恢复检查在工单 19。
