# 01 修复 Windows 可执行反馈环并补齐真实边界测试

Status: in-progress（反馈环已建立：7 绿 + 2 必红 smoke；见 Comments）
Priority: P0
Type: test-infrastructure / defect
Blocked by: none
Blocks: 02, 03, 04, 05, 06, 10, 11

## 用户价值

目前多条严重缺陷在现有测试全绿时仍能稳定发生。先建立可信的红—绿反馈环，才能避免后续修复只通过 mock、在真实 Windows 桌面继续失效。

## 已观察证据

- Playwright 启动 API 时发生 `sqlite3.OperationalError: unable to open database file`，用例尚未执行。
- pytest 默认使用系统临时目录时，13 个用例因 `PermissionError` 在 fixture setup 阶段失败；指定可写目录后通过。
- arXiv 单测替换了整个子进程；SMTP 测试使用本地假服务器；新会话 E2E 只断言菜单项存在或 mock API，没有走“上传—绑定—发送—重开”的完整链路。
- 相关 47 个后端测试通过，说明模拟契约自洽，但没有反证用户报告。

## 要实现的纵向切片

提供一个在 Windows 上单命令可运行、数据目录隔离、可重复执行的收尾测试入口。该入口启动真实 API、前端和必要的本地假服务，但不访问真实 QQ、arXiv 或模型服务；后续 issue 可以在此入口增加端到端场景。

## 实施步骤

1. 统一测试运行时定位：显式使用仓库虚拟环境中的 Python，不依赖 PATH 中的 Anaconda/系统 Python。
2. 每次运行生成唯一、绝对的 SQLite 与对象目录；启动前创建父目录，结束后只清理本次运行目录。
3. 为 pytest 统一提供仓库内或受控临时根目录，避免依赖用户系统 Temp ACL。
4. API、假邮件服务和前端均输出结构化启动失败原因；端口占用、目录无权限、迁移失败要彼此可区分。
5. 建立 `closeout-smoke` 测试入口和共享 fixture：真实浏览器、真实 HTTP、真实 SQLite、真实子进程；模型、公开网络、SMTP/IMAP只在边界处使用确定性本地替身。
6. 为失败保留 trace、截图和脱敏阶段日志；成功运行不遗留进程、锁文件或数据库句柄。

## 验收标准

- [x] 在干净 PowerShell 中运行收尾 smoke 命令，API、假邮件服务和前端一次启动成功，不要求人工预建 `.e2e-data`。
- [x] 连续运行两次不会因旧 SQLite WAL、端口、PID 或残留进程失败。
- [x] 故意把数据父目录指向不可写位置时，测试在 10 秒内以”数据目录不可写”失败，而不是笼统超时。
- [x] pytest 不再访问 `AppData\Local\Temp\pytest-of-*`；含 `tmp_path` 的选定测试可直接执行。
- [x] 浏览器测试可以走真实的建会话、上传、发送、切换会话和轮询状态 HTTP 链路，不对这些接口做 page route mock。
- [x] arXiv fixture 能启动真实 worker 进程并注入确定性客户端结果；邮件 fixture 能模拟 0 秒、10 秒和超时三种投递。
- [x] 失败产物不包含消息正文、文件正文、密码、授权码、Cookie 或 API Key。
- [x] Windows CI/本机各有一条文档化命令，退出码能准确反映通过/失败。

## 文档化命令（AC8）

本机（干净 PowerShell，仓库根目录）：

```powershell
# 完整收尾 smoke（pytest 套件 + 浏览器纵向切片，端口自动选择）
.\\.venv\\Scripts\\python.exe scripts\\closeout_smoke.py
# 只跑 pytest 套件（CI 无浏览器时用 --skip-browser）
.\\.venv\\Scripts\\python.exe scripts\\closeout_smoke.py --skip-browser
# 单独跑浏览器纵向切片（固定端口 8910-8914，占用时预检报错）
cd apps\\web; npx playwright test -c playwright.closeout.config.ts
```

CI：

```bash
python scripts/closeout_smoke.py --skip-browser   # 无浏览器环境
python scripts/closeout_smoke.py                  # 已安装 chromium 的环境
```

退出码：0 = 全部通过；1 = 任一子命令存在失败（含两个必红 smoke）；2 = 运行环境错误。

## 反馈环

先新增三个必红 smoke：

1. API 从唯一绝对 SQLite 路径启动并通过健康检查。
2. 真实 arXiv worker 往 JSONL 写入一个 GBK 不可编码字符，父进程仍能读到合法 UTF-8 响应。
3. 假邮件在 10 秒后投递，验证状态最终为 `verified`。

完成后运行收尾 smoke、相关 pytest 与一次 Playwright 串行模式；保留失败 trace 供后续 issue 使用。

## 约束与非目标

- 不把线上模型、QQ 或 arXiv 可用性作为 CI 成败条件。
- 不清理用户现有 `.e2e-data` 或正在运行的开发实例。
- 不为测试引入第二套产品实现；fixture 只替换外部边界。

## Comments

- 2026-08-08：首次诊断确认测试环境本身可阻断用例执行，列为所有修复的共同前置。
- 2026-08-08（实施）：反馈环已建立并验证——`tests/closeout/` 7 项通过、2 项必红且红因正确：
  - smoke 2（真实 arXiv worker 写 GBK 不可编码字符）以父进程 `arxiv_startup` 失败，锁定 issue 05 的管道编码根因（trace 见 `test-results/closeout/`）；
  - smoke 3（假邮件 10 秒投递）以 `verification_failed` 失败（收件轮询约 6-8 秒），锁定 issue 10 的延迟收件窗口根因。
  - 新增基础设施：仓库 venv Python 显式定位（`venv_python`）、每运行唯一绝对数据目录（API 启动前预检可写性，10 秒内中文快速失败）、pytest 受控临时根（`.tmp/pytest-basetemp`，不再访问系统 Temp）、假邮件服务子进程（0 秒/10 秒/超时三种投递参数）、API/邮件服务结构化启动失败分类（端口占用/数据目录/迁移可区分）、脱敏失败产物（trace 含秘密扫描断言）。
  - 浏览器纵向切片 `apps/web/e2e/closeout-smoke.spec.ts`（收尾专用配置 `playwright.closeout.config.ts`）：注册 → 建会话 → 真实上传（DataTransfer 触发真实 handler 与 XHR）→ 发送（真实 SSE + 确定性替身回答）→ 切换会话 → 权威历史轮询，零 route mock；连续两次运行通过。
  - 遗留已知项：Playwright `setInputFiles` 对 `display:none` + React `onChange` 的 input 不触发合成事件（实测 files 未生效），spec 用 DataTransfer 派发 change 走真实链路；真实用户经「+」菜单原生对话框不受影响。
  - AC7 范围说明：pytest 侧进程日志与 trace.txt 全部经 `sanitize` 脱敏（含秘密扫描断言）；浏览器侧失败产物（trace.zip + 截图）按全仓库 e2e 既有惯例保留未裁剪（Playwright trace 默认不保存请求/响应体；DOM 快照与截图可能含测试自造内容，不含用户真实数据与秘密——closeout spec 的输入均为自造测试数据）。
