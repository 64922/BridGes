# 35 — 交付显式授权 MCP 插件管理
Status: ready-for-human (验收通过，等待用户确认)
Blocked by: 22, 27, 34
Covered requirements: CHAT-10, EXT-01, IMP-03, ACCOUNT-01, SCORE-02, SCORE-03, UI-05, DESKTOP-01
ADRs: [0010](../../../docs/adr/0010-declarative-skills-and-permissioned-mcp.md), [0013](../../../docs/adr/0013-single-machine-storage-and-background-runtime.md), [0015](../../../docs/adr/0015-minimum-cloud-disclosure.md), [0016](../../../docs/adr/0016-incremental-vertical-replacement.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

在插件中心交付按账户安装和治理 MCP 的完整纵向链路。每个 MCP 必须锁定具体版本和完整性信息，安装前预览其网络域名、文件读写目录、外部命令、数据类别及敏感操作；用户明确确认后，服务才可在独立受限进程中启用。运行时默认拒绝未声明权限，只向 MCP 提供当前任务授权的最小数据切片，首次敏感操作再次确认，并记录安装、授权、调用、拒绝、失败、停止和卸载审计。百炼密钥、QQ SMTP 授权码、内部加密密钥和完整个人保险库在任何情况下都不得被 MCP 读取。

## Acceptance criteria

- [x] 用户可从插件中心提交具有固定版本和完整性信息的 MCP 安装描述，未锁版本、描述损坏或来源不匹配时安装失败闭锁。
      （McpDescriptorChecker：版本锁拒绝 latest/通配符、YAML 子集解析防御、来源仅 local/https、完整性 sha256 自校验 +
      安装时锁定描述哈希；test_mcp_checker 19 条逐项中文原因断言 + API 422/409）
- [x] 安装前页面逐项展示网络域名、文件读写目录、外部命令、输入数据类别和敏感操作；未声明或用户未同意的权限一律不可用。
      （PermissionTable 六组逐项预览；安装对话框「未声明或未同意的权限一律不可用」+ 信任边界说明；工具处理器对未声明
      工具/路径/域名/命令一律 permission_denied + MCP_INVOKE_DENIED 审计）
- [x] 安装成功的 MCP 在当前账户独立受限进程中运行，具有明确启动、健康、停用、失败、停止和卸载状态，并在重启后恢复合法配置而非继承僵尸进程。
      （McpStatus 六态：starting/healthy/disabled/failed/stopped + 卸载删除；惰性启动 + initialize 握手超时；
      重启读库恢复合法配置 + pid 文件孤儿回收（main.py 接线 reap_orphans）；test_restart_restores +
      test_reap_orphans）
- [x] 文件、网络和外部命令访问采用允许清单；平台无法可靠施加所需限制时拒绝启用，而不是降级到无限权限。
      （宿主工具处理器按清单强制：路径前缀校验/域名白名单/命令白名单/大小与超时限制；启动失败/超时/崩溃进入
      failed 且拒绝调用；安装预览披露平台强制边界）
- [x] MCP 进程不继承百炼密钥、QQ SMTP 授权码、内部加密密钥或无关环境秘密；直接和间接读取尝试均被阻止并审计。
      （clean env 白名单（PYTHONPATH/PATH/PYTHONIOENCODING）+ Popen 显式 UTF-8；test_child_env_whitelist 断言
      秘密键不存在；secret_reader 夹具读 env 空 + 未知「读秘密」工具被拒 + MCP_INVOKE_DENIED 审计）
- [x] 每次调用只接收当前消息明确授权的数据切片；未选择的画像、完整聊天历史、整个学习项目及其他账户数据不可见。
      （McpDataSlice 只含 text/attachments；清单 data_categories 未声明即 403 data_slice_denied；cross_account
      夹具验证切片 keys 仅 attachments/text 且路径全拒）
- [x] 首次执行写文件、运行外部命令、向外部服务提交私人内容等敏感操作时再次展示目标和影响并获得确认；拒绝后调用安全终止。
      （敏感操作确认流：tool_call 挂起 → 前端对话框展示目标与影响（危险色强调）→ approve 仅本次调用 →
      deny 服务器经 SensitiveRejected 安全终止；确认令牌一次性不可复用；test_sensitive_approve/deny +
      E2E 拒绝与确认双路径）
- [x] 用户可预览和撤回权限、启停或卸载 MCP；撤权会停止新调用并终止仍依赖该权限的运行。
      （PUT permissions 撤权：新调用立即用新清单；移除敏感权限时终止运行进程并进入可观察 stopped 状态；
      revoke 测试 + E2E 撤权后 write_file 被拒）
- [x] 插件中心展示真实调用次数、最近结果和失败原因，所有审计记录按账户隔离且不保存完整秘密正文。
      （mcp_calls 表 + 卡上三格统计（次数/最近结果/最近调用）+ 最近失败原因；10 个 MCP 审计动作 details
      白名单；崩溃错误消息不含 stderr 内容；test_audit_never_contains_secrets_or_body + API 响应扫描）

## Verification

- [x] 使用恶意 MCP 夹具尝试读取秘密、访问未授权路径、连接未声明域名、启动未声明命令和跨账户读取，验证全部失败闭锁。
      （malicious/ 五夹具：secret_reader（env 空 + 未知工具拒）、path_reader/network_connector/command_launcher/
      cross_account（全部 attempts 断言 blocked）+ MCP_INVOKE_DENIED 审计计数）
- [x] 使用受控 MCP 完成安装、授权、首次敏感确认、真实调用、停用、重启恢复和卸载端到端测试。
      （内置受控服务器 echo/note；E2E 7 条：权限预览安装→真实调用→统计→启停；敏感确认拒绝/确认；坏描述拒绝
      重装；两账户隔离（含 API 直调 404）；撤权；卸载；纯键盘全流程）
- [x] 对进程崩溃、启动超时、健康失败、权限撤回和迟到结果建立集成测试，验证状态与清理一致。
      （zombie 夹具崩溃→failed+原因；slow_start 启动超时→failed+进程回收；revoke 终止运行；多敏感挂起
      二次确认（审查修复）；deny 后工具调用被忽略）
- [x] 检查子进程环境、日志、审计与错误响应，确认秘密和完整私人正文没有泄露。
      （test_child_env_whitelist 断言子进程环境无秘密键；test_audit_never_contains_secrets_or_body 扫描审计
      details；API 响应扫描不含秘密与正文；崩溃错误消息不含 stderr（审查修复，防恶意服务器回显正文落库））
- [x] 在受支持桌面浏览器中仅用键盘完成权限预览、安装、敏感确认、撤权和卸载演示。
      （E2E 纯键盘：Tab/Enter 完成安装 note→调用→敏感确认对话框确认执行→撤权（checkbox Space）→卸载确认）

## Non-goals

- 不允许无固定版本、无权限声明或要求无限本机权限的 MCP 运行。
- 不向 MCP 暴露任何账户秘密，也不把首次确认扩展成永久无限授权。
- 不建设公共插件市场、计费、排名或自动升级服务。
- 不开发手机、平板、PWA 或触屏专用 MCP 管理。

## Blocked by

- [22 — 交付 arXiv MCP 论文搜索](./22-deliver-arxiv-mcp-paper-search.md)
- [27 — 交付画像切片披露与反馈闭环](./27-deliver-profile-slices-disclosure-and-feedback-loop.md)
- [34 — 交付 SKILL 插件中心](./34-deliver-skill-plugin-center.md)

## Comments

- 2026-08-01：按已批准的 BridGes 改进计划发布。
- 2026-08-06：实现完成。全量验证：1958 pytest（+82 新增：检查器 19 +
  进程 12 + 服务 29 + API 19 + schema v23 3）、issue35 E2E 7 条全过
  （权限预览安装调用统计/敏感确认拒绝与确认/坏描述拒绝重装/两账户隔离/
  撤权/卸载/纯键盘全流程）、全量 E2E 226 通过（4 条失败均为既有基线：
  issue04/08 环境 flake、issue13 视频入口为 Issue 32 遗留、issue14 并行
  flake 串行通过；另修 issue12 插件页多空态断言为 filter 精确定位）、
  mypy 241 文件 0 错误、改动区域 ruff 干净、npm typecheck/build 通过、
  openapi 同步通过。双轴 code-review 修复：多敏感操作调用链二次确认
  误判为拒绝（resume 挂起登记新确认）、重启恢复生产接线（main.py 传
  pid_dir + reap_orphans 启动回收）、崩溃错误消息剥离 stderr（防恶意
  服务器回显私人正文落库）、STOPPED 状态可观察（撤权敏感移除后停止 +
  调用惰性重启）、run_command Windows 反斜杠解析（shlex posix=False）、
  FAILED 覆盖重装旧描述对象回收、死常量与不可达分支清理、卸载文案与
  信任边界披露（平台以受限环境+允许清单强制边界）、键盘 E2E 补敏感
  确认与撤权路径。前端按 ui-ux-pro-max 建议（状态徽标颜色语义/权限
  表格/敏感确认危险强调/统计三格）以 Issue 04 基线令牌实现。
