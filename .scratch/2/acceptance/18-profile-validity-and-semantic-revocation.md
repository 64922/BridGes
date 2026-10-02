# 工单 18 独立验收

日期：2026-10-02。交付提交 `a4a817ba`（基点 `main@83c27f2f`），验收修复提交 `bca045cb`，合并提交 `c5437716`。开发和测试使用 conda `agent`（Windows，Python 3.11.15）。

## Standards 轴

- 未发现规范违规。`git diff --check` 通过；变更实现文件 ruff 零诊断（`automatic.py` 13 项 E501 与 main 完全相同）；`atomic.py` 无 mypy 错误，整项目 98 errors 与 main 基线一致，无受改文件新增。

## Spec 轴

对照票面验收与[画像规格与验收合同](../../../docs/用户画像/画像规格与验收合同.md)复核，发现并修复 3 项正确性问题（提交 `bca045cb`）：

1. 通用完成动词无对象时被推测为唯一目标完成：「完成了/结束了/搞定了/终于搞定了」会命中完成信号并把账户下唯一活动目标标记完成，违反 R02「只按明确暂停、完成、替代、继续信号变更」。修复：只有「考完了/考砸了」可无对象指向目标，通用完成动词必须点名对象。
2. 写入时已过去的绝对日期被当作有效期：「我2023年5月1日入职了A公司」会被判为已过期、不再召回，把历史事件时间误当期限。修复：来源消息当时已经结束的区间不构成期限；同句同时存在未来表达（如「下周」）时取未来表达。
3. 切片条目与撤回版本分别查询：编译先列条目、后另查版本，两步之间发生的撤回会让版本看似已更新而旧切片漏检。修复：条目与版本取自同一仓库快照，写入要么已反映、要么使核对失败，运行中撤回不再有窗口。

其余验收项复核通过：相对时间按来源消息锚一次解析且不重解释「下周」；长期偏好无统一 TTL；删除后旧证据重放、普通近义提及不复活，明确重新记住保留新授权来源；编辑换身份后旧值被抑制；暂停/完成/恢复按明确信号执行且否定措辞不误判；GOAL 严格定位不误伤考研/考公；LOW/迁移条目不作为确定事实召回而用户编辑权威独立；撤回监听在事务提交后按来源消息传播并失效会话摘要；v67 迁移幂等回补期限与目标状态；墓碑审计无正文且账户隔离；导出走 `SELECT *` 覆盖新增列。

## 验证

- 本票测试：交付时 59 passed；追加 10 项验收回归后 `test_issue18_*.py` + `test_schema_v67.py` 共 **69 passed**（内存与 SQLite 双跑）。
- 画像/存储/合同回归：`tests/profiles tests/storage tests/contracts` **586 passed / 1 failed**；失败项 `test_chat_correction_uses_latest_record_and_is_idempotent` 为时间相关的既有环境失败，合并前基线 `main@83c27f2f` 单独复跑 5/5 失败，与本次修复无关。
- API/生命周期/失效回归：`tests/api tests/lifecycle tests/invalidation` **144 passed / 5 failed**；5 项均为 `tests/lifecycle/test_lifecycle_api.py` 创建会话 409，在 main 独立复跑同名同因。
- 修改的聊天子集（V2-08/摘要缓存/画像切片/使用控制/意图保护/纠正载荷/画像意图）**101 passed / 1 failed**，失败项在 main 同样失败。
- `openapi.json` 重生成后与提交版本一致（309 paths）；`packages/contracts/src/generated.ts` 新增字段与 schema 对应。

## 合入与清理

- 修复提交 `bca045cb`；合并提交 `c5437716` 已推送到 `origin`（`64922/BridGes`），远端 `refs/heads/main` 与本地 HEAD SHA 一致。
- 本地 Issue 分支已删除；工作树登记与物理目录均移除（`Test-Path .worktrees/18-profile-validity-and-semantic-revocation` 为 False）；`git worktree prune --dry-run --verbose` 与正式 prune 均无失效记录，其他活跃工作树保留。

## 验收边界

确定性规则与桩只证明机制；真实模型抽取质量、外部可得性按 17/41 评测票。嵌套否定（「我不认为我考完了」）与非 GOAL 近义字符重合判据仍为已知限制，已在实施记录声明。
