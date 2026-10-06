# 工单 38：需求轴与正式路径复验

独立验收以工单原始需求、正式图及实际代码为依据，实施记录只作为定位线索。

## 原始缺陷

- 逐步交付缺失：流式消息原先不返回结果投影，复合结果只在整体终态提交后出现；主审负责修复与复验。
- 学习回合未纳入结果分类：辅导证据缺口、出题等待、已判定反馈与总结失败未进入公共结果面。
- 刷新终态消息时未读取其运行等待缘由；历史读取只为 streaming 消息提供 run_view。
- 新公共结果投影复制外部 error_message，扩大了未经筛选的工具错误原文暴露面。
- 模型失败码 timeout 未登记恢复；真实总结超时时，已判定反馈有效但没有恢复入口。

## 修复与边界

学习投影仅使用公开 StudyState，并按本条运行的消息 ID 匹配已提交的辅导或判定。
不按会话最新阶段重写历史消息，不将未来题、私有评分依据或提前答案复制到结果卡。
辅导缺证据显示部分交付；出题/判定等待显示待输入；总结失败保留反馈并单独恢复总结。
历史读取补回该消息运行的等待缘由。新公共投影使用登记失败文案，不复制外部错误原文。
timeout 按真实模型请求超时登记重试方式，study_summary_invalid 登记单独恢复总结。

## 验证

环境：Windows / PowerShell，`C:/Users/33755/anaconda3/envs/agent/python.exe`。

- `test_issue38_study_result.py`、原 `test_issue38_turn_result.py` 与 `tests/state_copy`：53 passed。
- `test_issue38_acceptance_paths.py`、`test_issue38_study_result.py` 与 `tests/state_copy`：43 passed，14.72 秒。
- 所改 turn_result、service、catalog 与新增测试 Ruff 通过。

正式路径测试使用真实 HTTP、SQLite、后台执行器及现行图，仅替换模型/检索/读取外部边界，无外呼。
覆盖复盘出题等待、未来题/评分依据不外发、暂停模板、辅导缺口与来源层次、总结失败后只恢复总结、论文/GitHub 澄清与续接；成功路径 SSE done、重复历史读取、存储正文及结果投影一致。
总结超时回归最初失败为 recovery=None；登记真实超时恢复后通过。领域 module_id 断言修正为用户请求消息上的历史标识，助手消息原合同为空。

## 执行限制

默认沙箱的 Windows asyncio socketpair 自连接在 TestClient 启动时阻塞，faulthandler 堆栈定位到 `socket.accept`，尚未进入业务。
按已授权测试范围提权运行后正常完成。独立 basetemp 避免并行 pytest 清理互相锁住的数据库。
浏览器 `v2-17/18/19/20-study-*.spec.ts` 使用受控 API，证明展示交互；不能替代上述真实执行证据。
真实普通聊天断线重放另由 `issue06-stream-replay-consistency.spec.ts` 覆盖。真实模型体验和外部来源可得性不在确定性测试结论内。

## 资料可信状态与渐进发布复验

真实反例：资料正式图收到「零基础，快速了解深度学习，只要1本书」，取得真实结构书目，简介注明需要线性代数基础；领域仍返回 success 与候选 items，但 path_verified=false。
`resources/kernel.py` 的交付产物在此条件下为 EVIDENCE_BOUND，公共结果原先只看 success 却标为 qualified。
修复仅根据显式 false 将资料结果分类为 partial/evidence_bound；历史缺字段不猜测降级，论文只要求摘要时未通读全文也不任意降级。
新增 `test_issue38_resources_result.py`：5 passed（含真实 HTTP/执行器/现行图与 SSE done 一致性）；Ruff 通过。

复审渐进发布：EvidenceVerifier 从持久节点产物的公开投影重建 Claim，论文/资料 Claim 保留标题、URL 和读取范围，GitHub 明确文档/静态证据且未验证运行。
发布者仅取前三条已过门 Claim，不复制 summary 或原始 evidence；快照与事件同事务，提交前检查停止、租约、任务版本，以模块去重。
`test_issue38_progressive_result.py` 与原结果投影测试：22 passed。未发现可实际复现的新增守卫、去重或私有字段泄漏缺陷。

## 学习历史快照与自然错误文案

真实 HTTP 复现：已完成总结或总结超时后，替换学习领域状态为新小节，再读取旧消息，旧 delivered/trust 消失；失败总结由 partial 漂移为 failed。
追加页与重新规划的现行入口实际保留已判题，因此该入口没有证明“删除旧判定”的猜测；回归仍验证其历史结果不变。

修复在 `study/turn_result.py` 封装学习终态事务接缝：原 persist_learning 回调成功之后，从账户隔离的已提交公开领域状态派生并冻结 message.turn_result，与正文及终态同事务提交。StudyWorkflow 四个终态入口统一接入；GET 继续只读。回调或快照失败整笔回滚，已终态守卫不重复写入。
本次 study.summarize 成功且确有总结才加入「学习总结」合格块；明确继续仅生成总结时，整体可信状态同样为 qualified，不依赖同轮是否另有反馈。
学习失败使用登记的自然错误模板，未知码使用 study_internal_error；控制节点保留在运行记录，用户 error_message 与错误事件不复制节点名或外部异常原文。

- 快照、正式 API 路径、工单 34 提交故障、工单 36 总结与状态文案：64 passed，47.18 秒。
- 最后新增明确继续仅总结与未知外部异常脱敏回归后，`test_issue38_study_snapshot.py`：6 passed，15.26 秒。
- 所改学习模块与新回归 Ruff 通过。真实浏览器截图另由规范轴复核。
