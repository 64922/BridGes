# 工单 38 正式浏览器专业路径独立验收

2026-10-06，Windows，conda `agent`，Chromium；桌面视口为
1280×720、1440×900、1920×1080。

## 验证边界

使用正式 Web、真实 HTTP、实际后台执行器和现行聊天图；不拦截浏览器
API 响应，不新增测试控制 API。测试服务只固定模型和公开来源边界，
复用 `test_issue38_acceptance_paths.py` 的论文/GitHub 来源及学习模型替身。
API 进程禁止连接非 loopback 地址，不执行真实外部来源或模型请求。
固定响应只证明流程机制，不能证明真实模型体验、照片识别质量或外部可得性。

独立配置继承已有 Playwright 服务器拓扑，只替换第一个 API 启动命令，
同时指定本工作树 Python 模块路径。端口为 Web 3038、API 8038；假邮件
服务和后台摄取 worker 沿用现有配置端口，运行时应避免与其他 E2E 冲突。

## 已验证行为

- 论文/GitHub：正式菜单选择、澄清待输入、保留模块标签、续接实际执行，
  界面显示查询记录、来源与读取边界，刷新后领域卡文本及完整消息投影一致。
- 学习：上传有效 PNG、预习、开始复盘、仅发布当前单题，私有答案和
  评分要点不发布；回答判定先提交、总结首次超时保留有效部分；通过键盘
  Enter 点击结果卡恢复，创建新运行，仅重做总结，判定记录不变。
- 三个视口均无页面横向溢出；刷新后消息正文及 `turn_result` 与持久化历史一致。

## 运行结果与命令

全部 9 项正式浏览器用例：**9 passed（49.8 秒）**。
自然错误文案、学习结果快照及部分失败保留正文修复后，针对受影响的
三视口学习路径最终复验：**3 passed（30.8 秒）**。界面实际显示已提交的
“回答正确”反馈、部分交付卡和可执行恢复，不显示 `study.summarize` 内部步骤。
Python 测试服务 `ruff check` 通过。前期浏览器验收发现新会话首次读取
streaming 后不会恢复澄清模块标签，修复为按最新助手终态消息标识处理一次，
修复后三视口六项专业用例全部通过。
一次学习复验在界面尚未处理首轮完成事件时过早填写下一轮，输入被首轮
收尾清空，发送按钮保持禁用；用例已改为同时等待正式界面完成与停止按钮
消失，再进行下一轮用户交互。最终三项复验通过。

在工作树的 `apps/web` 下执行 PowerShell：

```powershell
$env:BRIDGES_PYTHON = 'C:/Users/33755/anaconda3/envs/agent/python.exe'
$env:CONDA_PREFIX = 'C:/Users/33755/anaconda3/envs/agent'
$env:CONDA_DEFAULT_ENV = 'agent'
$env:PATH = "$env:CONDA_PREFIX;$env:CONDA_PREFIX/Scripts;$env:CONDA_PREFIX/Library/bin;$env:PATH"
$env:PORT = '3038'
$env:API_PORT = '8038'
$env:API_BASE_URL = 'http://127.0.0.1:8038'
npm run test:e2e -- --config playwright.issue38.config.ts
```

仅重验学习流程及生成截图：在最后一行增加 `--grep '学习照片'`。
本目录 `partial-<宽>x<高>.png` 为三视口真实部分交付界面截图；账户和
教材内容均为本轮合成夹具，不使用真实个人资料。
