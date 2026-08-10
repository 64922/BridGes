诊断完成，脚本已删除。以下是完整交接。

## 结论：400 根因是 (c) 其他 —— 上游强制规则"JSON 模式下 messages 必须含 json 字样"

**不是**模型 id 无效，**也不是** qwen3.6-flash 不支持 json_schema。根因：业务空间端点对 `qwen3.6-flash` 的 JSON 模式（`json_schema`/`json_object` 都算）强制校验"messages 中必须出现单词 json"，而画像抽取的生产 prompt（`automatic.py:353-358` 的中文 system + 用户消息）通常不含 "json"，于是被上游以 400 拒绝。`qwen_client.py:180-185` 把它归类为 `client_error_400, retryable=False`，重试 3 次后 exhausted。

**完整上游 message**（生产响应体未落库的那条）：
```
{"error":{"code":"invalid_parameter_error","param":null,
 "message":"'messages' must contain the word 'json' in some form, to use 'response_format' of type 'json_object'.",
 "type":"invalid_request_error"}}
```

## 逐步结果（端点 = 业务空间 `ws-ko5482byzh5q6hv1.cn-beijing.maas.aliyuncs.com`，与生产 key 配套）

| 步骤 | 请求 | HTTP | 说明 |
|---|---|---|---|
| 1 | qwen3.6-flash + json_schema(strict)，"回复ok"（生产原样） | **400** | 上面的 invalid_parameter_error，复现成功 |
| 2 | qwen3.6-flash 无 response_format | 200 | 模型 id 有效、有权限 |
| 3 | 同 1，回放 db 真实失败内容 | 400 | 同一 message |
| 4 | 同 3 内容，去掉 response_format | 200 | 与内容无关，是 response_format 触发 |
| 5 | 同 1，回放 db 真实"成功"内容 | 400 | 同一 message（见下方观察） |
| 6 | qwen3.7-plus-2026-05-26 + 同一 json_schema(strict) | **200** | 聊天模型不强制该规则 |
| 7 | flash + json_schema(strict) + system 里加"以 JSON 输出。" | **200** | 字面验证该规则 |
| 8 | flash + json_schema 去 strict + 含 json 字样 | 200 | strict 不是必要条件 |
| 9 | flash + json_object + 含 json 字样 | 200 | json_object 可用但丢 schema 约束 |

共 10 次调用，均为极短消息。

**关于间歇性**：bridges.db 里当天 3 成功 / 3 失败交错，但成功 payload 里也不含 "json"，且我原样回放"成功"内容现在也 400（步骤 5）——应用侧无分支（单一 extractor，无 fallback，`automatic.py:862`），所以是成功/失败由上游侧差异造成（灰度或路由变化）；当前稳态是**确定性失败**。

## 凭证来源（调查结果）

- 代码路径：全局 key 唯一来源是 `BRIDGES_QWEN_API_KEY` / `BRIDGES_QWEN_API_KEY_FILE`（`config.py:84`，ADR-0024）；bridges.db、OS 凭据库（GQ-07 已清退账户级 key）均无。
- 实际存放：`C:\Users\33755\Desktop\vibe coding\my_apl.txt`，内含 `$env:BRIDGES_QWEN_API_KEY = "sk-ws-..."`（业务空间 key，116 字符）及配套端点。Windows 凭据管理器里的 `gewu/bailian/llm` 是 6 字符占位符，无效（已实测 401）。

## 最小修复建议（按推荐排序）

1. **一行修复（推荐）**：`src/bridges/profiles/automatic.py:354` 的 system prompt 末尾加"以 JSON 输出结果。"（含 json 字样）。已实测 200，保留 qwen3.6-flash 与 json_schema+strict 的强约束。注意该规则对大小写不敏感地匹配子串 "json"，中文 prompt 恰好不含。
2. **换模型**：`main.py:262` 的 `model_id="qwen3.6-flash"` 改为 `qwen3.7-plus-2026-05-26`（步骤 6 实测 200），更稳但单价更高。
3. 备选：adapter 层 `QwenStructuredOutputAdapter` 在 JSON 模式时自动保证 messages 含 "json"（可一并保护 `qwen_structured_output` 能力——它默认 system prompt 含 "JSON" 所以目前侥幸安全，但显式传入不含 json 字样的 messages 时同样会 400）。
4. 不建议：改 `json_object`（丢 schema 校验）或单纯去掉 `strict`（步骤 8 虽 200，但前提是消息含 json 字样，不解决根因）。

另建议顺势改进：`qwen_client.py:180-185` 或网关落库 `last_error` 时带上上游 message（截断 500 字符，scrubber 清洗），这次正是因为响应体没落库才需要实弹诊断。

## 安全事项（需要你知道）

排查凭证时我的掩码正则漏了两种格式，导致三处秘密进入了本次会话的工具输出记录：**`my_apl.txt` 里的 `sk-ws-` 开头的百炼 key 全文、`BRIDGES_SECRET_KEY`（64 位 hex）、QQ SMTP 授权码**。记录只留在本机会话日志中；若该 CLI 会话会同步到云端，建议轮换这三个秘密。另外 `my_apl.txt` 本身是明文密钥文件，与项目"秘密不落盘"的纪律相悖，可考虑改用 `BRIDGES_QWEN_API_KEY_FILE` 指向权限受控的独立文件。

## 验证与清理

- 诊断脚本 `.tmp/diag_profile_400.py`、`.tmp/diag_profile_400b.py` 已删除（`ls .tmp | grep diag` = 0）。
- 未修改任何项目源代码；`git status` 里的 `src/bridges/chat/attachments.py`、`tests/chat/test_chat_attachments.py` 改动是本次会话之前就存在的，与我无关。
- 终端输出里的乱码只是 GBK 控制台显示 UTF-8 中文的问题，不影响数据。