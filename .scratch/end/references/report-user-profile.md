调查完成。以下是完整报告。

# BridGes 用户画像模块调查报告

## 数据流总览

```
聊天消息 → ChatService._process_profile_effects (src/bridges/chat/service.py:1153)
  → AutomaticProfileService.preprocess_message (src/bridges/profiles/automatic.py:735)
    → 信号预检 has_probable_profile_signal (automatic.py:203)
    → 抽取器 (test 环境=规则, 其他=网关) → _commit_output (automatic.py:1084)
      → 写 profile_extraction_observations + upsert profile_four_dimension_records
    → 失败 → _schedule_retry (automatic.py:867) → 队列
      → run_executor.run_retry_tick (src/bridges/chat/run_executor.py:138,153) 重试≤3次→EXHAUSTED
页面读取: /account/profile → GET /profiles/four-dimensions (apps/web/src/lib/api.ts:1366)
  → src/bridges/profiles/api.py:62 → list_records(account_id, status='active')
```

## Bug A：页面文字几乎看不见 — 根因

**根因：`FourDimensionProfileCenter.module.css` 整套是按深色背景硬编码的配色，但应用外壳默认是浅色主题，且该文件完全没有使用设计 token。**

- 页面：`apps/web/src/app/(app)/account/profile/page.tsx` → 组件 `apps/web/src/components/account/profile/FourDimensionProfileCenter.tsx`，样式 `FourDimensionProfileCenter.module.css`。
- 全局默认浅色主题：`apps/web/src/styles/globals.css:80` `--color-bg-primary: #faf9f5`（近白米色），`globals.css:239` body 背景用之；深色需 `<html data-theme="dark">`（`globals.css:171`）。
- 样式文件的关键行（用项目自带 `scripts/check_contrast.py` 的算法实测对比度）：
  - `:5` `.page { color: #f4f6f8; }` — 近白文字，**不设置背景** → 落在 `#faf9f5` 上，对比度 **1.03:1**（标题"四维画像" h1 和各分区 h2 都继承此色，即"看不见的标题"）。
  - `:15` `.eyebrow { color: #91c8ba; }` — "用户画像"眉题，**1.78:1**。
  - `:61` `.empty, .time { color: #8f9ba3; }` — "暂无记录"/时间戳，**2.70:1**。
  - `:32-37` `.group, .record` 边框 `rgba(255,255,255,0.11)`、背景 `rgba(255,255,255,0.045)` — 白透明叠在浅底上，卡片轮廓同样不可见。
  - `:102` 按钮文字 `#e8efee`，**1.11:1**。
- 佐证这是孤例：`AccountSettings.module.css`、`chat.module.css`（43 处）、`DataPrivacy.module.css`（28 处）全部使用 `var(--color-text-primary)` 等 token；`FourDimensionProfileCenter.module.css` **0 处 token 引用**。`welcome.module.css` 虽也硬编码深色，但自带深色背景（`:8 background-color:#14130f`），自洽不出错；画像模块只设浅色文字、不设背景，是直接缺陷。
- 为何漏网：`scripts/check_contrast.py` 只校验 `PAIRS` 里的 token 组合（`check_contrast.py:27` 起），不扫描组件 CSS。

页面分区（`FourDimensionProfileCenter.tsx:15-20`）：学业情况 `academic_status`、感兴趣的知识 `knowledge_interest`、兴趣爱好 `hobby`、阶段目标 `stage_goal`；空态文案"暂无记录"在 `:126`。数据源：`GET /profiles/four-dimensions`（`api.ts:1365-1372`）。

## Bug B：画像不记录 — 根因

**主根因（有数据库铁证）：生产/开发环境的抽取器走模型网关，上游 HTTP 400 失败，重试 3 次后 EXHAUSTED，四维表从未写入任何行。**

链路细节：

1. 每条用户消息都会触发抽取（无模式限制）：`service.py:1172-1181`，异常被 `contextlib.suppress(Exception)` 吞掉，用户无感知。
2. 抽取器选择（`src/bridges/api/main.py:1042-1046`）：**仅 `environment=="test"` 用规则抽取器**；其他环境用 `GatewayAutomaticProfileExtractor` → 能力 `qwen_profile_extraction@1`（注册于 `main.py:255-269`，`model_id="qwen3.6-flash"`；适配器绑定 `main.py:994-996`）。
3. 适配器构造的请求含 `"model":"qwen3.6-flash"` 和 `response_format={"type":"json_schema",...,"strict":True}`（`src/bridges/ai/qwen_adapters.py:139-145, 191-199`）。DashScope 返回 400 → `AdapterError(code="client_error_400", retryable=False)`（`src/bridges/ai/qwen_client.py:180-185`）→ `automatic.py:367-370` 抛错 → 进持久重试（重试不区分 retryable 标志）→ 3 次后 `_exhaust_task`（`automatic.py:1050-1062`）。
4. **bridges.db 实证**（只读查询）：
   - `profile_four_dimension_records`：**全表 0 行**（不是 user_id 不匹配——读写都用同一 `subject.account_id`；也不是 status 过滤问题——表里根本没有数据）。
   - `profile_extraction_runs`：那两条消息原样在列（账户 `xosCatPVlBeGHCKqzRE4OQ`）——"我想学习卷积神经网络相关知识"（2026-08-10T11:20:29）和"我是一名大三的人工智能专业学生…"（04:57:12）均为 `status=exhausted, last_error=client_error_400, attempts=3, record_ids=[]`。
   - `profile_extraction_tasks`：3 行全部 `exhausted/client_error_400`。
   - `model_run_locks`：`qwen_text_chat` 大量 success（`actual_model_id=qwen3.7-plus-2026-05-26`）——聊天主链路凭证和网络都正常，400 是画像抽取这个能力特有的：其 `model_id` 用的是 `qwen3.6-flash` 而非聊天用的 `qwen3.7-plus-2026-05-26`。**无法从落库数据区分** 400 是"模型 ID 不存在/无权限"还是"该模型拒绝 json_schema response_format"（响应体未持久化，只存了错误码），两者之一或皆有。
5. **次级隐患（实跑验证）**：即使网关修好、或在 test 环境用规则抽取器，`RuleBasedAutomaticProfileExtractor`（`automatic.py:241-317`）对"我想学习卷积神经网络相关知识"仍产出 **0 条**——目标模式要"目标/我计划/我打算"（`:264-265`），兴趣模式要"我对X感兴趣/我喜欢X"（`:278-280`），学业模式要"我在读/就读/是"（`:296`），观察兜底还要疑问词（`:305`）。"我想学习X"不在任何模式内，"感兴趣的知识"分区照样空。第二条消息规则抽取器能命中 `academic_status="一名大三的人工智能专业学生"`（已实测），所以只要网关可用它本应被记录；"给我规划一下考研进度"不是"我计划/打算"句式，不会进阶段目标。
6. `_commit_output` 的进一步收紧（`automatic.py:1123-1135`）：OBSERVE 或 reliability<0.6 不写；非明确自述的知识兴趣需 90 天内 ≥2 条不同消息观察才提升为记录。

结论：不是"只在特定模式触发"（调用点无模式门），也不是查询条件不匹配；是**生产抽取链路整体失败（client_error_400 → 重试耗尽）+ 规则抽取器句型覆盖缺口**双重问题。

## 画像注入聊天提示词（用于个性化）

会注入。编译在 `src/bridges/chat/turn.py:4740-4920`（按模式白名单 `_CHAT_MODE_DIMENSIONS`，`four_dimensions.py:43-66`；或带问题相关性过滤的版本 `automatic.py:1184-1277`）；渲染为固定格式文本 `profile_slice_context`（`turn.py:940-958`，"以下是本轮为你使用的画像切片…"）；由唯一组装点 `assemble_payload`（`turn.py:1196-1252`）作为 system 块插入 `messages[1]`（顺序：工具→检索→公网→arXiv→教学→画像）。主管线接线 `turn.py:2579-2636`。受 `use_profile` 开关控制（`service.py:1221` 默认 True；关闭走 OFF 披露 `turn.py:4773-4799`）。由于表为空，实际注入内容恒为空。

## 相关测试

- `tests/profiles/`（共 8 个文件）：`test_automatic_profile_extraction.py`(14)、`test_chat_slice_compiler.py`(14)、`test_four_dimension_profile.py`(8)、`test_memory_intent.py`(39)、`test_memory_slice.py`(11)、`test_profile_center.py`(17)、`test_profile_governance.py`(16)、`test_profile_service.py`(16)。
- E2E：`apps/web/e2e/issue25-profile-center.spec.ts`、`issue26-profile-candidates-permissions.spec.ts:75`、`issue38-visual.spec.ts:106`、`issue38-a11y.spec.ts:176`、`issue38-zoom.spec.ts:177`。
- 测试全绿而生产失败的结构性原因：测试环境走规则抽取器（`main.py:1042-1046`），且对比度脚本不覆盖组件 CSS——两个 bug 都恰好落在测试盲区。

## 备注

- 未修改任何文件；数据库仅用只读连接查询。
- 唯一未能定论的点：400 的具体上游原因（模型 ID `qwen3.6-flash` 无效 vs `json_schema` 不被该模型接受），因响应体未落库。可用同一 key 手工发一次该请求确认。