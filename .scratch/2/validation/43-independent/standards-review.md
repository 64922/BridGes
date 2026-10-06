# Standards 独立审查与复验

固定点：main `a9dda10af5ebe11fb4c7ba33fd74ab0cb8d2bf8a`；审查命令 `git diff main...HEAD`，原提交 `a93a215c`、`5ecb959c`、`12d94c89`、`fde48350`。依据：用户 AGENTS 全部规则、仓库 AGENTS/CONTEXT、docs/agents、delivery-and-validation 与 orchestration，以及 code-review 的完整 smell baseline；工具已强制项目不重复报告。

原 Standards 不通过：

1. 原硬违反 P1 已关闭：原 `rollback_rehearsal.py:199` 没有实际停新写验证。最终代码 `:234` 设置 query_only，真实 UPDATE 被 SQLITE_READONLY 拒绝，finally 恢复连接模式；runbook 明确其只约束隔离连接，生产须停止全部 API/worker 写入者，不冒充已执行生产停机。满足本票演练范围与真实限制要求。
2. 硬违反 P1：`release_adjudication.py:146/157/179` 用验收文档关键词和摘要总数放行。独立 conda agent 反例中三个文档只写 `not_released 不放行 36/36 8/8`，42 JSON 只有场景数和问题数，仍返回通过。违反 delivery-and-validation §3/4 与 AGENTS 原则9。已修复：改读39冻结人工评分、40双侧最终配对/36样本/硬门/逐调用锁、41原始指纹和8真实回答检查点、42最终场景/发布不变量/外部门/配对/语义执行证据。证据缺失、损坏、执行失败时对应票收敛为 not_released，保留限制。
3. 原硬违反 P2 已关闭：`snapshot_content` 对11表账户记录的完整身份/内容排序取摘要，所有退役步骤之后比较。新增等行数正文修改反例能检出；新增真实加密备份恢复被删除产物/损坏正文，双账户内容逐表一致。独立读取 rollback.xml：8 tests，0失败/错误；正式回滚报告15断言全通过。
4. 判断项：脚本与回滚测试重复任务/画像数据构造，可能 Duplicated Code；允许共享真实夹具避免报告和回归漂移，不能按外形相似强制抽象。其他 smell 未发现阻断问题。

最终复审发现的学习入口 P1 已关闭：原底层 compiler 默认候选开启，生产 `study/tutoring.py:266` 和 `study/service.py:497` 可绕过 Global 默认关闭。补正底层 candidate_enabled 默认 False；评测学习范围路径显式 True，不改写历史完整快照。conda agent 独立复编译 STUDY：版本 global-chat-release-baseline-v1，全部候选默认/形态规则与实际 rule_ids 交集为空，发布基线规则在场。新增正式学习预习/讲解测试同时检查持久run快照与实际模型system块，画像/预算守卫保留。

最终结论：Standards 复审通过，无未关闭阻断。裁决反例23 passed，ruff/mypy通过；独立读取学习delta spec-study-release-final.xml：90 tests、0失败/错误。42四份最终脱敏JSON按原字节复制，provenance保存来源/SHA；jobs最终 inconclusive。没有改写旧失败证据。限制：query_only只证明隔离单连接，未执行生产停机；该复审当时只取得学习补正前全量及90项delta；随后主验收在fe432411重跑最终生产源码的完整套件，runtime12及修正smoke12独立执行后按节点汇总，详情见independent-acceptance.md。全量/桌面/ADR和发行结论由主验收汇总。

主验收附记（2026-10-07）：旧凭据启动测试已按原验收意图显式选择development（完整smoke12/12），未改生产CLI；重复夹具注释说明独立演进和暂不抽象原因。无新增规范阻断。
