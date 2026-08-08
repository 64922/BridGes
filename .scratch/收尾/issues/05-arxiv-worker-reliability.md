# 05 修复 arXiv worker 的 Windows UTF-8、启动握手和错误分类

Status: completed
Priority: P0
Type: defect
Blocked by: 01
Blocks: 08, 11

## 用户价值

论文搜索应在 Windows 上稳定启动；失败时应告诉用户是启动、网络、协议还是无结果，并提供可重试路径，而不是所有故障都显示“arXiv 搜索服务启动失败”。

## 已观察证据与假设

- 失败记录约 0.4 秒即结束，错误码统一为 `arxiv_startup`，更像本地子进程/协议问题而非外网超时。
- worker 与父进程使用 `text=True` 但未指定 encoding；当前干净子进程的 stdin/stdout 是 GBK/CP936。
- worker 用 `ensure_ascii=False` 输出标题、作者和摘要。相同环境输出 GBK 不可编码字符会触发 `UnicodeEncodeError` 并以空 stdout 退出。
- stderr 当前直接丢弃；父进程把写失败、读到 EOF、worker 退出和非成功 payload 多数压成同一启动错误。
- 现有单测完全 mock `Popen`，没有启动真实 worker，也没有非 GBK Unicode 用例。

UTF-8 是高度可信根因，但实施者仍须先用真实 worker 集成测试把它锁定；若测试显示另有首要原因，应把新证据写入本 issue Comments 后再修。

## 要实现的纵向切片

让 JSONL 协议显式、双向、固定为 UTF-8；增加有时限的健康握手和有限诊断通道；建立不会泄漏用户查询的错误分类。

## 实施步骤

1. 父进程管道显式使用 UTF-8，并为子进程设置受控的 UTF-8 模式；worker 启动时也显式配置标准流。
2. 继续保持最小环境，不继承模型 Key、SMTP 码等秘密；Windows 必需变量和代理需求必须通过明确白名单/配置传入。
3. worker 启动后先完成 `ping/ready` 握手，再接受 search；握手、单次响应都设置截止时间。
4. stderr 改为有上限的诊断采集，防止管道阻塞；只保留退出码、异常类型/阶段和脱敏摘要，不记录查询/论文摘要。
5. 区分错误：spawn/handshake、worker exited、protocol/parse、network timeout、permission、remote response、cancelled、empty。
6. worker 异常退出时关闭旧句柄；一次安全重启后仍失败才返回终态，避免无限重启。
7. UI 根据错误码展示准确中文状态和重试按钮；空结果不是启动失败。

## 验收标准

- [ ] 真实 worker 集成测试返回包含非断行连字符或 emoji 等 GBK 不可编码字符的结果，父进程可正确解析。
- [ ] 中文查询、英文查询和混合查询都通过同一 UTF-8 JSONL 协议。
- [ ] worker 模块不存在、握手超时、进程中途退出、非法 JSON、网络超时分别得到不同稳定错误码。
- [ ] worker 首次崩溃后只自动重启一次；无无限循环、僵尸进程或句柄泄漏。
- [ ] 取消搜索在 2 秒内终止/回收当前请求，并投影为 cancelled 而非 startup。
- [ ] 日志可看到阶段、退出码、耗时和重启次数，但看不到完整用户查询、论文摘要或环境秘密。
- [ ] 论文搜索 UI 对错误、空结果、权限、取消和成功有独立状态。
- [ ] 真实网络人工 smoke 能完成一次搜索；CI 使用确定性边界替身，不依赖 arXiv 在线状态。

## 反馈环

第一条测试必须启动真实 Python worker，而不是 mock `Popen`。通过依赖注入让 worker 返回含 GBK 不可编码字符的固定论文，再验证完整请求/响应。随后参数化进程退出与协议故障。

人工验证时记录脱敏阶段与错误码；不要把真实查询正文或论文摘要提交到报告。

## 约束与非目标

- 不通过 `ensure_ascii=True` 掩盖标准流编码不确定性；协议本身必须明确为 UTF-8。
- 不恢复完整父进程环境继承。
- 不把所有异常继续包装成 `arxiv_startup`。
- 不在本 issue 改造学习模式；只提供可靠搜索能力和错误投影。

## Comments

- 2026-08-08：本机已证实受限子进程为 GBK，且同环境输出不可编码字符会以 UnicodeEncodeError 退出。
- 2026-08-08（完成）：分支 `05-arxiv-worker-reliability`（基于 c3682480，不动主线）。
  纵向切片已交付——协议双向固定 UTF-8（父进程管道 `encoding="utf-8"` + 子进程
  `PYTHONIOENCODING/PYTHONUTF8` 受控模式 + worker 启动 reconfigure 标准流）；
  worker 启动先写 `ready` 握手行，父进程握手/单次响应均设截止时间（reader 线程
  + 队列，超时即关句柄）；stderr 改有上限脱敏采集（防管道阻塞，只留退出码/阶段/
  脱敏摘要）；错误分类互不混淆（arxiv_startup/handshake/worker_exit/parse/timeout/
  permission/cancelled/empty）；崩溃后关闭旧句柄、恰一次安全重启仍失败才终态
  （无无限循环/僵尸/句柄泄漏）；stop_event 穿透 process client，取消 2 秒内终止
  当前请求并投影为 cancelled 而非 startup；turn.py 错误表/可重试集/教学门兜底码
  同步。验证：真实 worker 反馈环测试 12 条覆盖全部故障模式（spawn 计数哨兵 +
  查询往返哨兵），原必红 smoke `test_arxiv_utf8.py` 转绿；相关单测全绿，全量
  2200+ pytest 与 e2e 通过。实现说明见 task_plan.md。
