# 搜索提供方切换收口与发布门运行手册（Issue 07）

本手册对应 `.scratch/7/issues/07-search-provider-release-gate.md`，是第 7 轮
收口后「通用网页搜索 = Tavily」的安装、部署、故障处理与发布门运行说明。
决策权威是 [ADR-0029](../adr/0029-tavily-web-search-provider-and-release-gate.md)；
DDG-only 旧决策（ADR-0028、ADR-0026 注记）只保留历史审计含义。

## 1. 安装：依次输入 Qwen Key 与 Tavily Key

首次交互式启动（`BridGes start`，desktop profile）的凭据顺序：

1. 隐藏询问 **Qwen API Key** → 保存到操作系统凭据库；
2. 隐藏询问 **Tavily API Key**（通用网页搜索提供方）→ 保存到操作系统
   凭据库的独立项（`global-tavily-api-key`）；
3. 后续启动复用凭据库，不再询问。

非交互/显式 profile（`--profile production|development`、容器、CI）在启动前
设置环境变量或文件引用：

```powershell
$env:BRIDGES_QWEN_API_KEY = "<从密钥管理系统注入>"
$env:BRIDGES_TAVILY_API_KEY = "<从密钥管理系统注入>"
# 或文件引用：
$env:BRIDGES_TAVILY_API_KEY_FILE = "C:\secure\tavily-key.txt"
```

**缺 Key 行为**：

- 缺 Qwen Key：启动硬门失败关闭；
- 缺 Tavily Key（运行时）：应用正常启动，联网搜索入口返回「未配置搜索
  凭据」投影，前端如实标注，不伪装成功、不静默回退 DDG 或其他提供方；
  学习模式联网失败走 Issue 02 降级语义（带「本轮未联网核实」标注的模型
  知识回答，本地材料冲突时保持拒绝）。
- 注意：首次交互式安装的 Tavily 提示要求输入，留空会取消本次启动；
  已保存凭据或已配置环境变量的后续启动不再询问。

## 2. 限流 / 401 故障处理

| 症状 | 稳定错误码 | 处置 |
| --- | --- | --- |
| 401/403 | `web_search_configuration` | 凭据无效，不可重试；检查 `BRIDGES_TAVILY_API_KEY(_FILE)` 或凭据库中 Tavily Key 是否过期/被轮换，修正后重启相关进程 |
| 429 | `web_search_rate_limit` | Tavily 限流；不立即重试，服务层进入冷却，界面按剩余等待提示；按 Tavily 配额等待后显式重试 |
| 超时/DNS/连接失败 | `web_search_timeout`/`web_search_dns`/`web_search_offline`/`web_search_connect` | 外部网络故障；确认网络与 `api.tavily.com` 可达性后重试 |
| 5xx | `web_search_provider` | Tavily 上游暂时不可用；稍后重试，必要时查看 Tavily 状态页 |
| 解析契约不符 | `web_search_contract` | 产品解析契约漂移（`failed` 责任）；先升级代码再发布 |

运维诊断单次真实探针：

```powershell
.venv\Scripts\python.exe scripts\release_gate.py --web-health
# 退出码：0=READY，1=失败，2=无法判定（缺 Key/外部不可达/未授权）
```

任何错误投影、日志与报告都不回显 Key 值；疑似泄漏先用
`scripts\artifact_secret_scan.py` 排查（已覆盖 `tvly-` 与 Qwen Key 形态）。

## 3. 发布门单命令

```powershell
.venv\Scripts\python.exe scripts\release_gate.py --real-probes --qwen-authenticity --report .tmp\release-gate\report.json
```

组成项（任一硬门失败非零退出）：

1. **能力分类完整**：`PRODUCTION_CAPABILITY_MANIFEST` 唯一分类全部公开
   能力；通用网页搜索（`tavily_web_search`）恰好一次且为
   `external_non_qwen`（带自有凭据、不继承 Qwen Key）。
2. **生产组合 Tavily-only**：生产通用网页搜索提供方清单恰好只有
   `tavily`；备用提供方 Key/开关出现即 `unexpected_search_provider`
   失败关闭。
3. **金标路由**（Issue 03）：`tests/chat/test_golden_intent_routes.py`
   全部通过（三类用户点名语句及其变体路由到对应模块、负例不被误劫持）。
4. **降级语义抽查**（Issue 02）：`tests/learning/test_teaching_gate.py`
   与学习证据聊天测试——本地材料不足 + 搜索失败 → 带「本轮未联网核实」
   标注的降级回答且零进度推进。
5. **密钥零泄漏**：`scripts\artifact_secret_scan.py`（含 `tvly-` 形态）
   扫描日志、运行锁、消息投影、SSE 记录与发布报告产物；发布报告本身也
   经进程内扫描复核；真实性门另对探针数据库的落库表（运行锁/消息/SSE
   记录投影）做存储级 Key 形态扫描（`secret_leak_in_store` 失败关闭）。
6. **真实 Tavily smoke（opt-in）**：`--real-probes` 下执行最小成本真实
   搜索 + 正文获取，输出 `passed`/`failed`/`inconclusive`；缺 Key/无网络
   为 `inconclusive` 且不计入通过，不得伪通过；结果以脱敏字段归档进
   报告（provider、状态、延迟、结果数、错误类别）。
7. **Qwen 真实性门**（`--qwen-authenticity`）：Issue 17 能力清单/静态
   扫描/生产组合/live suite/重启锁复查；Tavily smoke 与 Qwen live suite
   并列运行、互不继承凭据。

离线回归（不联网）用同一命令去掉 `--real-probes`，报告会带
`real_provider_probes_not_run` 风险，不代表外部提供方已验证。

## 4. 报告与回滚

- 报告（JSON/Markdown）只含允许列表字段：build、capability、类别、
  provider、状态、延迟、脱敏错误类别；不含任何 Key 与用户内容。
- 发布门新增项可单独临时豁免并记录理由；ADR-0029 一经合并不回滚，只以
  新 ADR 修订。
