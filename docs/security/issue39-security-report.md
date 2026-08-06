# Issue 39 安全加固报告（可复现攻击矩阵与修复证据）

- 日期：2026-08-06
- 范围：Cookie 与会话、CSRF、跨账户缓存与后台串号、上传与解包路径、日志与错误脱敏、Qwen/搜索最小云披露、备份恢复边界、SKILL/MCP 权限提升与秘密读取
- 原则：失败时默认拒绝并给用户可恢复说明；不隐藏前端入口，不记录告警后继续执行。

## 攻击矩阵总览

| # | 攻击面 | 攻击步骤 | 预期拒绝 | 实际证据 | 修复回归 | 剩余风险 |
|---|--------|---------|---------|---------|---------|---------|
| A1 | 会话固定 | 攻击者预置 Cookie，诱使登录 | 登录后旧令牌立即失效 | `tests/security/test_session_cookies.py::test_login_revokes_previous_session_fixes_fixation` | ✅ | 无（每登录重新签发令牌，服务端撤销旧令牌） |
| A2 | 失效会话重放 | 用过期/撤销/伪造令牌请求 | 401 + 清除 Cookie 的 Set-Cookie | `test_expired_session_cleared_without_redirect_loop`、`test_revoked_session_cleared`、`test_invalid_cookie_cleared_without_redirect_loop` | ✅ | 无 |
| A3 | 登录重定向循环 | 伪造 Cookie 访问 /login | 中间件跳首页 → 401 清 Cookie → 可恢复登录页 | `apps/web/e2e/issue39-security.spec.ts`「无效会话 Cookie 不形成登录重定向循环」 | ✅ | 无 |
| A4 | Cookie 属性缺失 | 注入脚本读取令牌 | HttpOnly/SameSite=Lax/路径/生命周期/Secure 按传输环境设置 | `test_session_cookie_attributes_are_secure`、`test_session_cookie_secure_follows_https_scheme` + E2E「会话 Cookie 为 HttpOnly」 | ✅ | Secure 在反代 TLS 终止场景依赖 `BRIDGES_SESSION_COOKIE_SECURE` 显式配置 |
| B1 | 跨站表单/脚本请求 | 恶意页面 POST 携 Cookie（SameSite=Lax 兜底） | 来源校验 403 | `tests/security/test_csrf.py`（11 条：跨站 Origin/Referer 拒绝、同源/环回/转发/显式清单放行、只读请求不校验） | ✅ | 无 |
| B2 | 来源校验绕过 | 伪装 X-Forwarded-* | 转发来源与 Origin 不一致仍拒绝 | `test_forwarded_origin_mismatch_still_rejected` | ✅ | 无 |
| C1 | 账户切换串号 | 切账户后旧页面缓存/暂存被新账户消费 | 清 sessionStorage（bridges: 前缀）+ Clear-Site-Data | E2E「登出账户时清空账户作用域的本地临时数据」；`AuthContext.clearAccountLocalState` | ✅ | localStorage 设备级偏好（侧栏/主题）跨账户共享，属设计选择 |
| C2 | 旧流式响应续跑 | 生成中切账户 | 卸载时先 stopChatMessage 收敛服务端，再 abort 客户端流 | `apps/web/src/app/(app)/chat/[conversationId]/page.tsx` 卸载 effect；`AppShell` 按 accountRevision dispose 朗读会话 | ✅ | 服务端单次模型调用无法中途取消（断流在下一事件边界收敛），消息终态为 stopped |
| C3 | 播放续放 | 切账户后旧音频继续播 | 朗读会话 dispose | `AppShell` `useEffect([accountRevision])` → `readAloudSession.dispose()` | ✅ | 无 |
| D1 | 路径穿越上传 | 恶意文件名 | `validate_filename` 拒绝斜杠/控制字符/保留名 | 既有 `tests/chat`/`tests/knowledge_base`；本次对 media 上传补同款校验（`media/service.py`） | ✅ | 无 |
| D2 | 归档解包逃逸 | zip/tar 条目越界 | 白名单 + resolve + is_relative_to（备份恢复）；SKILL 包多层闭锁后以 blob 存储不落盘 | 既有 `tests/lifecycle/test_backup.py`、`tests/plugins` | ✅ | 无 |
| D3 | 对象库路径注入 | 文件名入路径 | 内容哈希路径，用户文件名仅为元数据 | 既有 `tests/storage` | ✅ | 无 |
| E1 | 日志/错误泄漏秘密 | 金丝雀注入后扫描出站与落盘 | 审计 scrubber 对 sk-/LTAI/授权码/正文键一律 `<redacted>`；错误响应使用稳定中文 | `tests/security/test_disclosure_scrubbing.py`（scrubber 金丝雀/LTAI/元数据保留） | ✅ | `details` 键名防不了未来调用方使用全新自定义键携带秘密——scrubber 只按已知键名与值模式脱敏 |
| E2 | 完整对话正文落盘 | cassette 录制 | 生产环境强制禁止录制 | `test_production_disables_cassette_recording` | ✅ | 开发/测试环境录制仍是明文（默认关闭，目录由显式配置指定） |
| E3 | 秘密扫描盲区 | LTAI AccessKey 入库 | 扫描模式覆盖 | `tests/security/test_secret_scan.py` 新增 aliyun_key 模式 | ✅ | 无 |
| F1 | 搜索携带身份 | 私人正文/邮箱/手机/身份证入查询 | 本地去身份 + 8 token/80 字符上限 | `test_search_scrub_removes_phone_and_id_card`、`test_search_scrub_removes_email_and_url`、`test_search_scrub_keeps_legitimate_numeric_terms` | ✅ | 正则无法穷尽所有 PII 变体；句子级模式兜底 |
| F2 | 披露审计缺失授权快照 | 搜索/画像披露不可审计 | 审计含 data_categories + authorization_snapshot | `test_search_audit_has_data_categories_and_authorization_snapshot` | ✅ | 无 |
| G1 | 后台任务跨账户 | 删除账户后任务继续产出 | 事务删除全部账户行；执行器按 account_id 领取 | `tests/security/test_background_account_binding.py`（调度器/执行器/查询三向） | ✅ | 无 |
| H1 | MCP 符号链接逃逸 | 允许目录内链接指向外部 | `_path_allowed` 只按真实路径（real vs real_dir）前缀匹配 | `test_symlink_read_escape_rejected`、`test_symlink_write_escape_rejected`（真实链接，Windows 跳过）+ `test_symlink_escape_rejected_with_resolved_realpath`、`test_symlink_write_escape_rejected_with_resolved_realpath`（全平台 mock realpath 模拟） | ✅ | 无 |
| H2 | MCP 重定向 SSRF | 允许域名 302 到内网 | 禁止跟随重定向，302 即拒绝 | `test_redirect_following_rejected`、`test_url_allowed_requires_https` | ✅ | 无 |
| H3 | MCP 进程环境/工作区继承 | 读宿主秘密/文件 | clean env 白名单 + cwd 固定受限目录（进程与外部命令均为独立随机目录） | `test_mcp_process_cwd_is_restricted`、`test_runtime_passes_pid_dir_as_cwd` + 既有 `tests/mcp` 秘密隔离用例 | ✅ | 无 |
| I1 | 猜测对象标识 | 复用他人媒体对象/分镜/沙箱运行 ID | 服务端账户授权，统一 404 | `tests/security/test_object_authorization.py`（7 条跨账户用例） | ✅ | media 域为内存存储（重启即失），持久化后需在存储层复核作用域 |

## 修复清单（代码级）

1. **新增 `src/bridges/api/csrf.py`**：CSRF 来源校验中间件（AC2）。改变状态请求校验 Origin/Referer；允许来源顺序 = 显式 `BRIDGES_ALLOWED_ORIGINS` → X-Forwarded-* → Host → 环回兜底；403 响应携带稳定错误码与可恢复中文说明。
2. **`src/bridges/config.py`**：新增 `BRIDGES_ALLOWED_ORIGINS`（逗号分隔，NoDecode + field_validator 解析）。
3. **`infra/compose/docker-compose.yml` / `infra/manual/README.md`**：容器部署显式配置允许来源；文档补充变量说明。
4. **`src/bridges/api/auth.py` / `data.py`**：Cookie 清除保持与设置一致的 HttpOnly/Secure/SameSite 属性（AC1）。
5. **`src/bridges/media/`（generation / storyboard_service / service / publish / accessibility / api/media.py）**：媒体对象、分镜、场景规格、沙箱运行、验证报告全部按账户隔离；跨账户统一「不存在」（AC9）。
6. **`src/bridges/mcp/service.py`**：`_path_allowed` realpath 符号链接解析；`http_get` 禁止重定向跟随；`run_command` cwd 固定受限工作目录（AC8）。
7. **`src/bridges/mcp/process.py` / `runtime.py`**：子进程 cwd 参数与 MCP 专用工作目录（AC8）。
8. **`src/bridges/media/service.py`**：上传文件名复用 `validate_filename`（AC4）。
9. **`src/bridges/web_search/service.py`**：手机号/身份证号脱敏；审计补 `authorization_snapshot`（AC6）。
10. **`src/bridges/observability/scrubber.py`**：新增正文/凭据类禁止键与 LTAI 敏感子串（AC5）。
11. **`src/bridges/api/credentials.py`**：错误响应不再透传底层异常文本，改稳定中文（AC5）。
12. **`src/bridges/api/main.py`**：生产环境强制禁止 cassette 录制（AC5）。
13. **`tests/security/test_secret_scan.py`**：新增 LTAI AccessKey 扫描模式（AC5）。
14. **前端（AC3）**：`AuthContext` 新增 `clearAccountLocalState`（bridges: 前缀 sessionStorage 清理，logout 与设备切换共用）；`AppShell` 按 accountRevision dispose 朗读；聊天页卸载时 stopChatMessage + abort 流式请求（Composer 上传/录音/ASR 卸载清理为既有实现）。
15. **E2E `apps/web/e2e/issue39-security.spec.ts`**：4 条浏览器可观察契约（HttpOnly/重定向循环/暂存清理/正常流程）。

## 双轴 code-review 修复（2026-08-06，Standards + Spec 并行子代理）

- **Spec 阻断项（AC8 符号链接逃逸防护失效）**：`_path_allowed` 词法/真实路径交叉乘积会放过「允许目录内链接指向外部」的逃逸 → 改为只比较真实路径（real vs real_dir），并新增全平台回归测试（monkeypatch realpath 模拟链接，Windows 也可运行）。
- **Spec 项（外部命令工作目录）**：全局共享固定 temp 目录可被预置符号链接 → 改为每服务实例 `tempfile.mkdtemp` 独立随机目录。
- **Standards 项**：`_origin_of` 默认端口剥离死条件（Host 携带 :80/:443 时合法同源请求被误拒）→ 修复并保持测试；`mcp/service.py` 导入置于模块级；删除未使用的 `csrf_origin_middleware` 工厂；`update_storyboard` 收紧为必填 `account_id`（AC9 不留可选路径）；新增文件 ruff 全清（E501/F401/E402/SIM103/F841）；并发测试补交叉读取断言（对方 404、本方 200）。
- **Spec 复核纠偏**：Composer 上传/录音/ASR 的卸载清理为既有实现（审查误报，复核确认存在）；对话投影不含 account_id 为设计（不泄漏归属），串号验证改用交叉读取。

## 发布门

- 高危/中危用例全部有回归测试与修复证据（上表「修复回归」列）；未关闭高风险项：无。
- 剩余风险为文档化说明（见各「剩余风险」列），均为设计权衡而非未修复漏洞：
  - 单次模型调用不可中途取消（断流收敛在事件边界）；
  - scrubber 依赖已知键名/值模式（新增键持续演进）；
  - 设备级 localStorage 偏好跨账户共享（无敏感数据）。

## 复现方式

```bash
# 全部安全回归（AC1-AC9）
python -m pytest tests/security tests/mcp tests/credentials -q
# 全量
python -m pytest tests -q
# E2E（需 Next dev + API + 假邮件服务器）
cd apps/web && npx playwright test e2e/issue39-security.spec.ts
```
