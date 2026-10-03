# Issue 23 独立验收记录

日期：2026-10-03。审查基点：`main@bd014bd6300ad500dcd67d5da759e60d60ad620d`；原交付：`8c63dfc0`。环境：Windows，conda `agent`，Python 3.11。

## 验收结论

原交付有 5 类问题，不能按原报告直接通过；修复后，本票负责的公共状态文案、错误说明与正式来源清单通过验收。领域模板优化、前端全来源投影以及真实体验评测仍按工单明确分工由 24–36、38/43、39/40/42 验证。本次未执行正式桌面浏览器视觉验收，也未重跑整个仓库的全量测试；不把原代理报告的全量通过数当作本次独立证据。

## 标准轴

没有发现阻断性仓库规范违规；注释、文案使用中文，注册表只依赖标准库及自身模块，无新增模型调用或持久状态。

判断项 1：恢复语义与聊天 `_RETRYABLE_CODES` 两份投影存在差异，例如 `web_search_connect/dns/provider` 登记重试或等待，而聊天重试资格为假；`arxiv_permission` 登记调整请求而聊天资格为真。工单明确将前端恢复投影收敛交给 38，本次不整表重构，不以登记语义宣称点击按钮已经可用。

功能缺陷 1：路径校验的 `any(part ... if not part)` 不能拒绝空路径段；新增负向测试先复现 `.chat`、`chat.`、`chat..error` 三项失败，再修复为 `any(not part ...)`。空路径一并保持拒绝。

## 规格轴

发现并修复 4 类问题：

1. 论文主题不匹配实际是 `clarification`，资料主题不匹配实际是 `empty`，两者原登记为 `partial`。改为 `module.paper.mismatch` 与 `module.resources.mismatch`，状态及说明与既有消费者一致，不虚构有效部分交付。
2. 学习 `next_question` 没有停止分支，无下一题表示 `complete`。纠正登记；新增 `study.stopped` 并让学习服务原有五处停止信号使用同一中文常量，用户可见字面量保持一致。
3. 补登学习书页等待/页序/识别缺口、范围与预习问题、追加材料、暂停复盘及当前题等待来源。渲染入口定位到 `StudyWorkflow.run`，备注标明各内嵌函数，不重复实现领域流程。修复后清单共 158 条。
4. 备用搜索缺少部署凭据的聊天优先映射丢失恢复指引。恢复“请联系管理员”，保留 `NOT_CONFIGURED/RECONFIGURE`，没有承诺无需配置即可重试恢复。

没有发现模型额外调用、机器字段改写、退役入口恢复或新增工作台。清单修订不改变路线、引用、价格、时间、岗位等领域数据渲染器。

## 独立验证

所有测试均在 conda `agent` 运行，禁用 pytest 缓存。中文失败输出使用进程级 UTF-8，避免 conda 的 GBK 输出异常。

- 最终公共路径：`tests/state_copy tests/routing tests/ai tests/web_search tests/chat/test_error_message_mapping.py`：**380 passed / 2 skipped**。其中本票注册表用例 **30 项**，原 22 项加本次 8 项负向/真实性回归。
- 父图/终态/前置表达：`tests/retrieval`、三份 `test_terminal_*`、四份 `test_improvement12_*`、`test_improvement21_contextual_expression.py`：**242 passed / 2 failed**。
- 学习实际入口：全部 `test_v2_17_study_pages.py`、`test_v2_18_study_tutoring.py`、`test_v2_19_study_review.py`，以及论文/资料主题不匹配测试：**58 passed / 1 failed**（注册表新用例单独在最终公共路径命令全部通过）。
- 上述三个失败逐项在 `main@bd014bd6` 同命令复现：两个检索用例仍尝试已退役附件/项目来源，论文用例未得到投影而索引 `None`。主线对照为 **58 passed / 3 failed**，没有本票新增行为失败。资料不匹配、学习等待、预习、追加、暂停和用户停止实际入口均通过。
- `ruff check src/bridges/state_copy tests/state_copy` 及 `src/bridges/study/service.py` 全部通过；`git diff --check` 通过。
- `mypy src/bridges/state_copy src/bridges/study/service.py`：20 个既有依赖错误；main 同环境 `mypy src/bridges/study/service.py` 也是 20 个。忽略移动行号后错误文本与文件逐条相同，0 新增。

## 合入与清理

按用户授权，修复提交后以显式合并提交合入 main；网络操作使用系统已启用代理 `http://127.0.0.1:7890`，临时传给 Git，不修改全局代理配置。推送后比对远端 main，再删除 Issue 23 工作树、本地 Issue 分支并执行 worktree prune；具体合并提交和实际完成结果追加在工单中。
