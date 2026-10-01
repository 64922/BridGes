# Issue 06 独立验收记录

日期：2026-10-01。原交付：`24c7350`（实现 + 两轴自审修正），由另一编码代理完成、未推送未合并。本次独立验收修正：`2bd3468`。验收比较点为当前主线 `3fc4067`，分支 `codex/06-stream-replay-and-terminal-consistency`（基点 `1b35c02`，阻塞票 05 已验收合入），工作树 `BridGes-06`；开发与验证使用 conda `agent`（Windows，Python 3.11.15）。

## Standards

复核实现、测试与票据记录，未发现阻止合并的规范硬违规；独立验收发现并修正 1 处会阻止浏览器验收成立的测试缺陷：

1. `apps/web/e2e/issue06-stream-replay-consistency.spec.ts` 三个用例共用同一后台生成执行器，Playwright 默认 4 workers 并行时多轮生成按队列串行，后两轮在默认 5s 首块等待内等不到 `delta` 而假失败（实测默认配置 2/3 失败）。修正：文件内 `test.describe.configure({ mode: "serial" })`、首块等待与仓库其他聊天 E2E 统一为 30s，并加长脚本块间延迟（1500/1000ms）保证停止与重连的流式窗口可观测。默认配置连续两次复跑 **3 passed**。

其余复核项：`finish()` 的协议显式断言与仓库既有 78 处 `assert` 口径一致，不另行改动；`ruff` 改动文件与主线逐（文件, 规则）计数一致（112 项 0 差异），`mypy src` 两侧同为 98 项既有错误且逐条消息一致（0 差异），`apps/web` `npm run typecheck` 通过。共享配对判定（`same_fragment_identity`）、仓库 `ChatStreamEventKind` 枚举守卫、降级剥离口径交叉引用等自审修正经复核成立。

## Spec

按工单验收标准逐项独立核实：

1. 跨 chunk 代码案例 UI、事件重放与落库正文逐字一致、无整段伪 delta：`test_issue06_stream_replay_consistency.py` 覆盖逐字符流式、被改写前缀、多片段、空块；E2E 用例对复制出的 UI 纯文本、游标 0 重放 delta 拼接与消息投影 content 三者逐字比对。通过。
2. 改变已发前缀、多个保护片段、空 chunk、断线游标重放、终态重新加载均有确定性用例；追加式边界对 `http:/`、`` `` ``、`12 ` 等未闭合中间态暂缓。通过。
3. 真实浏览器生命周期：Playwright + 真实 API/后台执行器/SQLite/SSE，经 Next 代理，零 mock；脚本适配器仅 test 环境注入 chunk 边界与延迟。`3/3 passed`。通过。
4. 停止、旧租约晚返回、并发重连不重复正文/不发起新调用：E2E 停止用例验证迟到分块不推进正文且消息数/尝试数不变；仓库守卫对终态后 `delta`/`stage` 一律拒绝（`done` 后仅放行图内 `node` 遥测）；重连以刷新续读 + 游标 0 重放覆盖。通过。
5. 历史 SSE 与旧消息兼容：未新增事件类型、载荷结构未变，既有历史事件按原样可解析可回放，终态唯一性守卫保持。通过。

## 本次实际验证

环境：`C:/Users/33755/anaconda3/envs/agent/python.exe`，`PYTHONUTF8=1`，测试子进程 `CODEBUDDY_SAFE_DELETE_ENABLED=0`；各轮 `--basetemp` 独立目录。

- 工单定向：`tests/chat/test_issue06_stream_replay_consistency.py tests/ai/test_stream_script_fixture.py` → **30 passed**；合并后主线连同 `tests/chat/test_chat_api.py` 复测 → **52 passed**。
- 核心套件 `tests/chat tests/ai tests/api`：**884 passed / 100 failed / 1 xfailed**。把 100 项失败所在 17 个文件在分支与合并前主线各跑一遍逐名比对：两侧均 **100 failed / 79 passed**，失败节点集合逐条相同（0 新增）。
- 追加式不变量 fuzz：`.scratch/issue06_fuzz.py` **20000 轮 0 mismatch**（终态断言未触发）。
- 真实浏览器 E2E：验收修正后默认 workers 连续两次 **3 passed**（单测期间 `--workers=1` 亦 3 passed）。E2E 经由 editable 安装时须给 Playwright 注入工作树 `src`（`PYTHONPATH`），否则 API 进程会加载主工作树代码，`/_test/chat-stream-script` 会 404。
- `ruff`（改动文件）、`mypy src`、`npm run typecheck`：与主线 0 差异。

## 验证限制

- 未构造两个浏览器会话的真并发订阅；旧租约迟到由仓库/链路级用例覆盖，重连由刷新续读 + 游标 0 重放覆盖（与原交付记录一致）。
- 停止路径不 `finish()`：未闭合片段按设计丢弃（终态前不下发无法撤回的片段）。
- 历史兼容 = 事件 schema 未变 + 载荷字段断言 + 既有记录可读；本票未新增持久状态，无需迁移/备份变更。
- 确定性脚本适配器只证明机制；真实模型体验与外部可得性按评测票验证。

## 合并与清理

- 独立验收修正提交 `2bd3468`；`main` 从 `3fc4067` 以 `--no-ff` 合入，合并提交 `23306ae`；唯一冲突在 `src/bridges/api/main.py` 导入区，保留主线（Issue 08）的 `tasks` 导入与分支的 `CHAT_CAPABILITY_NAME` 导入，其余文件自动合并。
- 合并后主线定向复测 **52 passed**；`git push origin main` 成功（`3fc4067..23306ae`），远端 `https://github.com/64922/BridGes.git`（首次连接被重置，重试成功）。
- `git worktree remove --force BridGes-06` 成功；目录残留空壳已清空删除，实体不存在。
- `git branch -d codex/06-stream-replay-and-terminal-consistency` 安全删除成功（`2bd3468` 经 `23306ae` 可达）。
- `git worktree prune --expire now --dry-run --verbose` 无失效记录；最终只剩主工作树。

结论：本票追加式正文协议、终态存储与断线重放按验收标准独立核实通过，唯一验收修正（E2E 并行假失败）复验无误，已合入主线并推送。
