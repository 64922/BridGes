# Task Plan — 架构审查候选逐项修复（M01–M05 审查后深化）

状态：进行中（2026-08-05）

## Issue 30 实施计划（交付听写与单条回答朗读）

状态：已完成（2026-08-05）。全量验证：1639 pytest（+25 新增：speech 服务/API
听写与朗读）、11 条 issue30 E2E（录音状态机/转写回填/取消清理/权限拒绝/设备
不可用/空音频恢复/网络中断重试/朗读播放控制/失败重试/单活动会话/刷新保持/
能力禁用说明/纯键盘路径）、全量 E2E 通过（issue04 附件控件与 issue08 视觉
回归为干净树复现的既有环境 flake、issue12/14 并行 flake 串行通过、doctor
CLI smoke 为环境既有编码 flake）、mypy 干净、改动区域 ruff 干净（observability
UP042 为既有问题）、npm typecheck/build 通过。双轴 code-review 修复：CSS
令牌用错（--font-size-* 未定义）、MIME 白名单私有常量公开化、听写端点补对话
归属校验（404 不泄漏）、fetch 网络错误映射中文、删除未使用 getReadAloud
helper、播放条 aria 角色矛盾、朗读工具栏入口恢复（issue04 设计基线契约）、
E2E 播放竞态（等进度再暂停）与缺失场景补充（设备不可用/网络中断重试/纯键盘
朗读控制）。Issue 30 验收状态已更新为 ready-for-human。

前置依赖：Issue 10（账户级凭据与能力探测：asr/tts 固定快照矩阵与真实探测
已存在）、11（持久化流式聊天）、13（新输入框）均已交付。探索完成：矩阵
已含 ASR_MODEL_ID=qwen3-asr-flash-2025-09-08 与 TTS_MODEL_ID=
qwen3-tts-flash-2025-11-27；QwenAsrAdapter/QwenTtsAdapter、ModelGateway
运行锁、KeyCredentialService 能力快照门控（_ensure_chat_capability_ready
模式）、加密对象库（create_object/delete_object/pending_cleanup 清理轮）
全部可复用；前端 Composer 已有 Web Speech 听写骨架、MessageList
AssistantActions 已有 speechSynthesis 朗读骨架，均需替换为真实链路。

### 目标
在真实聊天链路交付「录完再转写」听写与单条回答按需朗读。听写固定
qwen3-asr-flash-2025-09-08：录音→停止→提交完整音频→可编辑文本→用户
自行决定发送，绝不自动发送。朗读固定 qwen3-tts-flash-2025-11-27：每条
已完成的助手文本回答独立生成朗读，播放/暂停/继续/停止/同条受控重试，
页面单活动播放会话。两项能力共用当前账户百炼密钥与独立真实探测；不可用
时明确停用并说明原因，失败只重试同一快照，不切换模型不模拟成功。听写
音频不落盘（请求体内存直传 ASR，超限/空音频/非白名单 MIME 拒绝）；生成
音频按账户对象库留存并接入最小留存清理。

### 新增模块
1. `contracts/speech.py` — DictationStatus/ReadAloudState(not_generated/
   generating/ready/failed)、DictationProjection（transcript/model_id/
   duration_ms/error_code/error_message/created_at）、ReadAloudProjection
   （state/model_id/audio_ref(对象 ID)/char_count/error_code/error_message/
   generated_at/retryable/完整消息正文快照不存——朗读按消息正文现取）、
   SpeechError
2. `speech/service.py` — SpeechService：transcribe（能力门→固定 ASR 绑定
   经 ModelGateway invoke qwen_asr_short@1（固定模型来自矩阵）→审计
   ASR_TRANSCRIBE（details 不含音频/转写正文）→DictationProjection）；
   generate_read_aloud（校验消息归属/role=assistant/status=done/正文非空
   →正文纯文本化→固定 TTS 绑定 invoke→下载供应商临时 URL→转存账户对象
   库→ReadAloudProjection 快照写回消息 read_aloud 列→审计
   READ_ALOUD_GENERATE）；get_read_aloud / get_audio_bytes（对象授权校验）；
   delete_read_aloud（清理对象与快照，幂等）；失败只重试同一快照
3. `storage/database.py` — SCHEMA_VERSION 18：messages 加 read_aloud
   JSON 列（快照含 state/model_id/audio_ref/error/retryable/created_at）

### 修改
4. `contracts/observability.py` — AuditAction.ASR_TRANSCRIBE /
   READ_ALOUD_GENERATE / READ_ALOUD_DELETE（details 只含时长/字符数/消息
   ID/模型标识，不含音频与正文）
5. `api/speech.py` — 路由：POST /conversations/{id}/dictation（multipart
   音频，能力门 asr）、POST /conversations/{id}/messages/{mid}/read-aloud
   （能力门 tts，返回投影）、GET 投影、GET /audio（流式返回对象字节，
   含 Content-Type/长度，带账户授权校验）、DELETE（停止并清理，幂等）；
   错误码 no_api_key/capability_probing/capability_unavailable 复用
6. `api/main.py` — SpeechService 挂载（gateway/credential_service/
   object_store/object_repository/observability/chat_repository）
7. openapi.json + generated.ts 再生成

### 前端（先调 ui-ux-pro-max：录音胶囊 + 朗读播放条 + 状态芯片）
8. api.ts — transcribeDictation / generateReadAloud / getReadAloud /
   fetchReadAloudAudio(URL) / deleteReadAloud + 类型导出
9. Composer 录音状态机（替换 Web Speech）：idle→recording(时长计时)→
   stopping(提交中)→transcribed(可编辑回填不自动发送)/error(权限拒绝/
   设备不可用/空音频/超限/网络/密钥失效)；按钮：开始/停止/取消/重录，
   全部键盘可达；录音中禁用发送；取消不遗留待发送文本；跨账户/刷新
   安全（卸载清理）
10. ReadAloudControls（新，挂 MessageList 每条助手消息）：生成→
    generating(过程态)→ready(播放条：播放/暂停/继续/停止/进度)/
    failed(原因+重试同条)；全局单活动播放会话（AudioManager 单例，
    新播先停旧）；切换对话/账户/刷新安全停止；失败重试走同一消息正文
11. MessageList/ChatThread 接入；chat.module.css 录音/朗读样式

### 测试
12. `tests/speech/test_speech_service.py` — 转写成功/空音频/超限/不支持
    MIME/能力门（未配置/探测中/不可用）/审计不含正文/固定模型标识进
    运行锁/失败可重试同快照/不落盘（无对象产生）
13. `tests/speech/test_read_aloud.py` — 生成/重试/消息归属与 role 校验/
    账户隔离（跨账户取音频拒绝）/删除幂等/对象库留存与清理（delete
    后 pending_cleanup）/刷新后可重新请求
14. `tests/chat/test_speech_chat.py` 或并入 — 与聊天链路集成（消息正文
    纯文本化、快照落库、SSE 无关）
15. E2E issue30 — 录音→停止→转写回填→编辑→发送；取消/重录；麦克风
    拒绝；空音频；超限；网络中断；密钥失效；朗读生成/播放/暂停/继续/
    停止/失败重试；切换回答单会话；刷新与跨账户安全；纯键盘路径
16. 真实探测验证：能力探测覆盖 asr/tts 固定快照（已有 issue10 测试），
    记录模型标识与失败语义

### 收尾
17. 全量 pytest / ruff / mypy / npm typecheck+build / E2E；code-review
    双轴审查并修复；更新 Issue 30 验收状态（ready-for-human + 验收项
    打勾附证据）；提交（工作内容+bug 修复两部分提交信息）



## Issue 29 实施计划（交付生涯规划助手）

状态：已完成（2026-08-05）。全量验证：1616 pytest（+71 新增：career 意图
检测/复核/服务 48 条 + 聊天集成 13 条 + 审查回归 10 条）、6 条 issue29 E2E、
全量 E2E 187 通过（issue04/08 为既有环境 flake、issue13/14 并行 flake 串行
通过）、mypy 206 文件 0 错误、改动区域 ruff 干净、npm typecheck/build 通过。
双轴 code-review 修复：承诺词否定剥离跨词（「不构成…保证」误伤）与边界
声明漏扫（正向承诺藏匿）、意图检测过宽劫持普通聊天（「考研英语怎么复习」
误触发，改分级关键词+语境词）、过时机制不可达（画像/学习记录改用记录更新
时间，旧记录真实标注）、假设缺核查方式无确定性门、非可重试错误显示必败
重试按钮、前后端意图规则分叉（前端补关键词表）、私有函数跨模块导入、
死参数清理、aria-controls/反馈表单自动聚焦。Issue 29 验收状态已更新为
ready-for-human。
前置依赖：Issue 23（教学门）、27（切片披露与反馈）、28（humanizer）均已
交付；探索完成：生成链分支点（skill 载荷/humanizer、mode/teaching）、
compile_chat_slice、LearningService 学习记录、retrieval.run_round、
answer_feedback 反馈闭环、scoped() 账户隔离全部可复用。

### 目标
在既有两种对话模式中，按"明确生涯规划意图"触发真实可保存、可恢复、可追溯的
规划对话：只使用当前账户授权的画像切片、学习记录、用户陈述与可追溯证据，输出
固定结构化的已知事实/待验证假设/可选方向/关键风险/分阶段成长路径/近期学习建议
六类内容加自然中文正文；事实带可定位来源与核查时间，证据不足明确说明未知与
下一步核查；自然表达受 bridges-humanizer 规则约束（事实与推测分离、限定条件
保留），但绝不作就业/薪酬/录取保证、不基于单次情绪/敏感身份猜测/未确认候选；
用户可逐项反馈（事实/假设/建议），反馈进入既有画像治理闭环而非静默覆盖；
模型/检索/画像不可用时给出可恢复错误与安全替代步骤，不输出模板化假成功。

### 新增模块
1. `contracts/career.py` — CareerIntent(六类输出契约：CareerFact/CareerAssumption/
   CareerOption/CareerRisk/CareerStage/CareerSuggestion 各带 item_id/内容/证据引用/
   核查时间/状态)、CareerPlanningOutputContract（final_text+六类+boundary_statement
   +完整性门）、CareerEvidenceSource（画像/学习记录/检索/联网/用户陈述五类来源，
   带 accessed_at 核查时间与 locator）、CareerPlanningProjection（status/plan_id/
   intent/六类/evidence_sources/profile_used/process_state/error_code...）、
   CareerPlanningProcessState 五态(loading/empty/error/permission/recovery)
2. `career/intent.py` — 确定性生涯规划意图检测器：显式前缀"生涯规划助手："必中；
   关键词表（职业规划/生涯规划/就业方向/求职/转行/职业发展/选专业/考研/考公/
   找实习/职业选择/晋升路径/职业目标）；否定式防护（不要/不用/别…不触发）；
   与 humanizer skill 载荷互斥（skill 分支优先）
3. `career/service.py` — CareerPlannerService：意图检测 → 画像切片编译（复用
   ProfileService.compile_chat_slice，按当前对话模式维度映射）→ 学习记录读取
   （复用 LearningService：使命/知识状态/学习记录）→ 本地检索 run_round + 时效性
   关键词触发 DuckDuckGo/arXiv → 证据集合 → Qwen 结构化生成六类输出（prompt 含
   humanizer 表达规则与边界禁令）→ 确定性复核（事实证据门：无证据引用降级为
   假设或标注未核实；承诺词检查：保证/包过/包就业等阻断；引用核验：不在证据
   清单标记未核实；输出合同完整性门）→ 投影；失败可重试不丢输入

### 修改
4. `contracts/chat.py` — ChatMessageProjection.career_planning；ChatStreamEventKind.
   CAREER + ChatStreamCareerData（过程事件：五态+step_label+progress_steps）
5. `contracts/observability.py` — AuditAction.CAREER_PLANNING_GENERATED（details
   只含 item 数/证据数/切片 ID，不含正文）
6. `contracts/feedback.py` — AnswerFeedbackRequest.career_item_ref（可选，逐项
   反馈定位到六类条目；幂等去重键扩展）
7. `storage/database.py` — SCHEMA_VERSION 17：messages 加 career_planning JSON 列
8. `chat/repository.py` — MessageRecord.career_planning；insert/get/update_
   message_career_planning；find_duplicate_feedback 带 career_item_ref
9. `chat/service.py` — stream_generation 意图检测 → _stream_career_planning 分支
   （检索/联网/切片/生成/复核/落库/SSE 事件，模式与 _stream_humanizer 一致）；
   retry 重新检测意图沿用；_project_message 映射 career_planning；
   submit_feedback 支持 career_item_ref
10. `api/chat.py` — send/retry 后 SSE 透传 CAREER 事件
11. `api/main.py` — learning_service 挂载提前，CareerPlannerService 挂载（gateway/
    profile_service/learning_service/retrieval/web_search/arxiv/observability），
    ChatService 构造接入
12. openapi.json + generated.ts 再生成

### 前端（先调 ui-ux-pro-max：AI-Native 风格、六类分区结果卡、五态过程卡）
13. chat-tools.ts："生涯规划助手"由预填改为打开 CareerPlanningDialog（两模式"+"菜单
    与建议卡共用，原创 career 图标已存在）
14. CareerPlanningDialog（新）— 生涯问题输入 + 画像使用开关 + 提交走真实 send
15. CareerPlanningProcessCard — 五态中文过程卡（loading/empty/error/permission/
    recovery）
16. CareerPlanningResultCard — 可展开结果卡：六类分区（事实/假设/方向/风险/路径/
    建议，各带核查时间与来源）、证据列表（可打开原文/URL）、画像披露链接、
    边界声明、逐项反馈（事实/假设/建议各条目"反馈"入口走既有反馈 API）
17. api.ts streamChatMessage 支持；chat-thread.tsx/MessageList 渲染 career 卡；
    page.tsx 事件处理

### 测试
18. `tests/career/` — 意图检测矩阵（前缀/关键词/否定/误触发防护/与 skill 互斥）；
    六类输出合同完整性门；事实证据门（无证据降级/未核实标注）；承诺词边界阻断；
    引用核验；确定性复核规则（固定语料）
19. `tests/chat/test_career_planning_chat.py` — 真实消息流集成（可编程结构化适配器）：
    started→career 过程事件→done 六类投影；重试不丢输入；无画像/拒绝画像（off 态
    披露）/敏感推断排除/证据冲突/过时来源/模型失败（error 可恢复不假成功）；
    两账户隔离（缓存/引用/反馈不串号）；逐项反馈幂等与画像治理闭环
20. E2E issue29 — 两模式"+"菜单与建议卡入口、对话框提交、五态过程卡、六类分区
    结果卡、查看依据（来源+核查时间）、逐项反馈、画像关闭、失败恢复重试、
    键盘路径

### 收尾
21. 全量 pytest / ruff / mypy / npm typecheck+build / E2E；code-review 双轴审查并
    修复；更新 Issue 29 验收状态；提交（工作内容+bug 修复两部分提交信息）




## Issue 28 实施计划（原创净室 bridges-humanizer SKILL）

状态：已完成（2026-08-05）。全量验证：1545 pytest（+46 新增：skills 注册表、
humanizer 事实锁/体裁/服务/聊天集成）、7 条 issue28 E2E、全量 E2E 182 通过
（3 条失败均为干净树复现的既有环境 flake：issue04/issue08/issue12）、mypy 200
文件 0 错误、改动区域 ruff 干净、npm typecheck/build 通过。双轴 code-review
修复：联网证据合同实际接线（Spec AC8）、生成路径 fact_check 恒空必败一轮、
嵌套 button 无效 HTML、契约死代码 HumanizerError、genre_rules 死字段、
标签表重复、process_state 终态语义、kind 魔数、前端重复组装、tabs 半成品、
单位 token 顺序契约注释、task_id 魔数判别等。Issue 28 验收状态已更新为
ready-for-human。

### 目标
以原创净室方式实现默认内置、只读、版本固定的 `bridges-humanizer` SKILL，接入两种
对话模式的"+"菜单与空白对话建议卡并进入真实消息流程。改写路径（粘贴文本/当前账户
文件）先提取任务契约与事实锁再人性化；主题生成路径收集/确认主题、受众、体裁、渠道
与硬约束后生成并复核。科普文案、课程讲稿、科研汇报、论文写作各自使用可测试表达
规则，不共用单一模板。每次输出固定含最终文本、逐项修改细节、每项理由、事实核查
结果与未决问题；数值/单位/对象关系/限定条件/公式/引用/结论强度受事实锁检查，冲突
时停止或标注人工确认。过程卡五态中文；失败可从原任务重试不丢输入；注册为默认内置
能力供插件页（Issue 34）展示；完成许可证与来源清洁审计。

### 新增模块
1. `contracts/humanizer.py` — HumanizerPath(REWRITE/GENERATE)、HumanizerSkillInput
   （skill_id+任务契约：体裁复用 expression.Genre 四值、受众/渠道/长度/硬约束/原文/
   附件）、FactLockKind 七类(数值/单位/对象关系/限定条件/公式/引用/结论强度)、
   FactLockEntry/CheckResult（preserved/changed/removed/added + 阻断/需人工）、
   HumanizerEdit（原文/新文/类别/理由/体裁规则）、HumanizerFactCheckItem、
   HumanizerOutputContract（final_text/edits/fact_check/open_questions + 完整性门）、
   HumanizerResultProjection、HumanizerProcessState(loading/empty/error/permission/
   recovery)
2. `skills/registry.py` — SkillRegistry：内置只读 SKILL 注册表（稳定标识/版本/只读
   来源/能力说明），启动注册 bridges-humanizer v1.0.0，供 Issue 34 插件页消费
3. `skills/humanizer/skill/` — SKILL.md（完整说明/规则/证据边界/输出合同/版本）+
   genres/ 四体裁合同（popular_science/lecture_script/research_report/paper_assist，
   各自必含/允许省略/禁止/保留规则与人工责任，不共用泛化模板）+ fixtures/（两条
   路径固定语料）+ CLEAN_ROOM.md（来源清洁记录：scientific-humanization 仅方法
   研究、零 MIT 复用声明）
4. `skills/humanizer/factlock.py` — 确定性文本事实锁引擎：数值+单位/公式/引用/限定
   词/结论强度/对象关系正则提取、规范化（全半角/单位统一）、前后比较
5. `skills/humanizer/genre_rules.py` — 四体裁确定性规则加载与校验（required/
   prohibited/preserved 断言，中文可测试）
6. `skills/humanizer/service.py` — HumanizerService：改写/生成两条路径编排（任务契约
   提取 → 事实锁提取 → SKILL+体裁规则组装 → Qwen 结构化生成 → 确定性复核（事实锁
   前后比较/体裁规则/输出合同完整性/引用保持）→ 结果投影）；冲突→needs_human 或
   停止；证据合同复用本地检索/联网搜索；失败可重试

### 修改
7. `contracts/chat.py` — ChatMessageCreateRequest.skill_id/skill_input；
   ChatMessageProjection.skill(用户消息任务摘要)+humanizer(结果投影)；
   ChatStreamEventKind.HUMANIZER + ChatStreamHumanizerData（过程卡五态事件）
8. `contracts/observability.py` — AuditAction.HUMANIZER_GENERATE（details 不含正文）
9. `storage/database.py` — SCHEMA_VERSION 16：messages 加 skill JSON 列
10. `chat/repository.py` — MessageRecord.skill；insert/get 带 skill；update_message_humanizer
11. `chat/service.py` — start_generation 接收 skill_id/skill_input 落库；stream_generation
    检出 skill 走 HumanizerService 编排（发 HUMANIZER 过程事件→done 带结果投影）；
    retry 复用原任务输入不丢；_project_message 映射 humanizer/skill
12. `api/chat.py` — send/retry 透传 skill 字段、SSE 透传 HUMANIZER 事件；humanizer
    结果详情路由（如需）；_ensure_chat_capability_ready 复用（权限态）
13. `api/main.py` — 挂 SkillRegistry（内置注册）与 HumanizerService（复用 gateway/
    attachments/retrieval/web_search/observability）；附件文本经 ingestion parsers 提取
14. openapi.json + generated.ts 再生成

### 前端（先调 ui-ux-pro-max：AI-Native 风格、五态过程卡、空态带行动）
15. chat-tools.ts："文章人味化"意图由预填改为打开 HumanizerDialog（两模式"+"菜单与
    建议卡共用，原创 humanize 图标）
16. HumanizerDialog（新）— 改写/生成两页签：粘贴文本或选当前账户附件、体裁四选、
    渠道/长度/硬约束；提交走真实 send（skill 载荷）
17. HumanizerProcessCard — 五态中文过程卡（loading/empty/error/permission/recovery）
18. HumanizerResultCard — 可展开结果详情：最终文本/修改明细/每项理由/事实核查/未决
    问题/事实锁冲突标注/引用保持
19. api.ts streamChatMessage/retry 支持 skill 载荷；chat-thread.tsx/MessageList 渲染
    humanizer 卡；page.tsx 事件处理

### 测试
20. `tests/skills/` — 注册表（内置只读/版本/标识）
21. `tests/humanizer/` — 事实锁提取与前后比较（固定语料含数值/单位/公式/限定/引用，
    冲突阻断与人工标注）；四体裁规则可测试性（不共用模板）；输出合同完整性门（缺一
    不完成）；引用保持；改写/生成两路径（可编程捕获适配器）；失败恢复重试不丢输入；
    许可证/净室声明存在性
22. `tests/chat/test_humanizer_chat.py` — 真实消息流集成（send 带 skill、SSE 过程事件、
    重试、账户隔离、权限态）
23. E2E issue28 — 菜单入口、建议卡、文件改写、主题生成、结果详情、错误重试
24. 许可证与来源审计：CLEAN_ROOM.md 记录净室方法与零复用声明

### 收尾
25. 全量 pytest / ruff / mypy / npm typecheck+build / E2E；code-review 双轴审查并修复；
    更新 Issue 28 验收状态（ready-for-human + 验收项打勾附证据）；提交



## Issue 27 实施计划（最小画像切片、披露与反馈闭环）

状态：已完成（2026-08-05）。全量验证：1490 pytest（含新增 25 条，3 个
CLI 编码 flake 为环境既有）、6 条 issue27 E2E、mypy 194 文件 0 错误、
改动区域 ruff 干净、npm typecheck/build 通过。双轴 code-review 修复
5 处缺陷后提交（详见提交信息）。

### 目标
为日常陪伴与学习模式建立任务级最小画像切片编译器：每轮只选择与当前
任务相关、仍有效且授权范围匹配的记录；用户可在发送前关闭画像使用；
发送到 Qwen 的上下文只含必要切片，不上传完整画像中心或未确认候选。
回答提供可展开中文「本次上下文说明」（画像类别、材料类别、用途、来源
记录链接、本次使用时间），不暴露隐藏提示或原始思维链。反馈入口区分
「这次回答有问题」与「画像记录有误」，完成「回答—反馈—修正画像或
策略—下一轮验证」的多轮闭环；历史回答保留当时切片版本，可回放修正
前后差异；学习模式用目标/知识状态/学习证据调教教学，日常模式只用
必要偏好与情境；多账户并发按稳定账户 ID 编译；披露与反馈流具备中文
loading/empty/error/permission/recovery 状态，失败不丢失用户反馈。

### 新增模块
1. `contracts/feedback.py` — FeedbackKind / AnswerFeedbackRequest /
   AnswerFeedback / FeedbackResolveRequest（回答反馈与画像修正闭环契约）
2. `profiles/` 无新文件：在 `service.py` 新增 `compile_chat_slice`（模式
   维度映射 _CHAT_MODE_DIMENSIONS：companion=兴趣/表达/基本情况，
   study=阶段目标/知识状态/兴趣；授权范围 general|模式匹配；每维度
   上限 2 条、总量上限 6 条的最小化；未确认候选/撤回/冻结/过期/敏感
   排除全部复用既有过滤）

### 修改
3. `contracts/chat.py` — ChatMessageCreateRequest.use_profile 开关；
   ContextNoteProfileItem + ContextNoteProjection（披露卡：维度中文标签/
   值摘要/用途/来源 assertion_id/使用时间/快照状态与版本/材料类别/
   排除数/state: ready|empty|off|error）；ChatMessageProjection.context_note
4. `contracts/observability.py` — AuditAction.PROFILE_SLICE_USED（details
   只含 slice_id/维度/条目数/授权快照，不含正文）与 ANSWER_FEEDBACK
5. `storage/database.py` — SCHEMA_VERSION 15：messages 加 context_note 列；
   answer_feedback 表（幂等去重键 account+message+kind+assertion+文本）
6. `chat/repository.py` — update_message_context_note；feedback 读写
7. `chat/service.py` — stream_generation(use_profile) 编译→注入最小切片
   上下文（独立 system 块，固定格式）→披露落库→审计；编译失败静默
   降级（披露 error 态，回答照常）；关闭画像不编译/不注入/披露 off 态
   且审计记录 disabled；retry 沿用旧轮次开关（从旧尝试披露快照读取）；
   submit_feedback（幂等）/ list_feedback / resolve_feedback
8. `api/chat.py` — send/retry 透传 use_profile；feedback POST/GET/resolve
   路由（失败返回可重试错误，前端保留草稿）
9. openapi.json + generated.ts 再生成；npm typecheck/build

### 前端
10. Composer 发送前画像开关（与知识库开关平行，data-testid）；
    ContextNoteCard 可展开披露卡（loading/empty/error/permission/recovery
    中文状态）；回答反馈入口（回答不合适+偏好 / 画像有误+修正/冻结/
    撤回）；修正后下一轮适配提示；失败不丢反馈（本地保留+重试）；
    历史切片版本 vs 当前版本差异入口。先调 ui-ux-pro-max

### 测试
11. `tests/profiles/test_chat_slice_compiler.py` — 模式×场景矩阵、撤回/
    冻结/过期/敏感/授权范围/未确认候选排除、最小化上限、账户隔离
12. `tests/chat/test_profile_slice_chat.py` — 捕获模型适配器请求只含期望
    切片；关闭画像后无任何画像内容；披露快照；「初始回答—用户纠正—
    画像更新—后续回答改变」固定多轮回放；审计不含正文；失败不阻断
13. 反馈 API 测试（幂等/账户隔离/关联消息）
14. E2E issue27 — 上下文说明展开、回答反馈、画像修正、下一轮适配、
    失败恢复

### 收尾
15. 全量 pytest / ruff / mypy / npm typecheck+build / E2E；
    code-review 双轴审查并修复；更新 Issue 27 验收状态；提交


## Issue 26 实施计划（画像候选与分级许可更新）

状态：已完成（2026-08-05）。

### 目标
把聊天观察转化为可治理的画像候选与更新流程：明确"记住/不要记住/只在
本对话使用"意图按可见类别/范围/证据确定性映射；低风险目标/兴趣/表达
习惯仅在用户预开的类别×场景许可内自动写入（默认关闭、模型不能代开），
每次写入有中文通知、来源与一键撤回；情绪趋势/重要经历/当前问题与敏感
推断只进候选箱，未确认不得跨会话使用；单次情绪仅作为会话情境信号；
冻结类别拒绝自动写入、撤回许可只停未来更新；稳定去重与证据合并；
全部按账户隔离并写入审计；画像/许可/通知 SQLite 持久化，重启可追溯。

### 新增模块
1. `profiles/extraction.py` — 确定性记忆意图提取器（记住/不记/仅会话/
   低风险观察/单次情绪；类别关键词表；显式意图优先、否定式防护、
   无类别不猜测）
2. `profiles/sqlite_repository.py` — 全端口 SQLite 实现（观察/候选/断言/
   版本/切片/许可/通知，scoped() 账户强制隔离）
3. `contracts/profiles.py` — ProfilePermission / ProfileNotification /
   AUTO_WRITABLE_DIMENSIONS / 批量决策契约
4. `contracts/chat.py` — ChatStreamEventKind.PROFILE + ChatStreamProfileData
5. `contracts/observability.py` — 6 个新审计动作（许可开关/自动写入/
   一键撤回/意图/候选提出）
6. `storage/database.py` — SCHEMA_VERSION 14：7 张 profile_* 表

### 修改
7. `profiles/service.py` — 许可门/冻结门/去重门、process_conversation_message
   管线、一键撤回（幂等）、批量决策（幂等）、通知读写
8. `chat/service.py` — start_generation 挂载画像处理（失败静默不阻断）、
   profile_notifications_for_message 透传
9. `api/chat.py` — started 后下发 profile SSE 事件
10. `api/main.py` — 有数据库时挂 SQLite 画像仓库；ChatService 接入
11. `profiles/api.py` — 许可 GET/PUT、通知 GET/read/recall/unread-count、
   候选 batch-decision

### 前端
12. api.ts 新函数 + 类型导出；ProfilePermissionPanel（开关网格，乐观更新
    失败回滚）；ProfileNotificationList（未读/已读/一键撤回/标记已读）；
    ProfileCenter 候选卡增强（为何提出/来源消息/适用范围/编辑后确认/
    复选框批量确认拒绝）；ChatProfileNotificationCards（聊天内即时通知
    + 一键撤回 + 错误恢复）；chat.module.css / ProfileCenter.module.css
13. openapi.json + generated.ts 再生成；npm typecheck/build

### 测试
14. `tests/profiles/test_memory_intent.py` — 35 条：标注对话集（明确记忆/
    低风险许可/敏感候选/单次情绪/禁止推断授权）、幂等去重、冻结门、
    许可撤回、一键撤回、批量决策、跨账户、SQLite 重启持久化
15. `tests/chat/test_profile_intent_chat.py` — 5 条：聊天集成、失败不阻断
16. E2E issue26 — 5 条：许可开关持久化、候选卡增强与编辑后确认、
    批量拒绝、通知空态+聊天内通知+一键撤回错误恢复、单次情绪提示

### 收尾
17. 全量 pytest / mypy / ruff / npm typecheck+build / E2E 全量；
    code-review 双轴审查并修复；更新 Issue 26 验收状态；提交


## Issue 20 实施计划（分层本地检索、融合排序与引用）

状态：已完成（2026-08-04）。全量验证：1335 pytest（+81）、E2E 143 通过
（新增 issue20 5 条，4 个失败均为干净树既有/环境 flake）、mypy 173 文件
0 错误、改动区域 ruff 干净、npm typecheck/build 通过。双轴 code-review
修复 11 处缺陷后提交（77a24ce）。

### 目标
在真实对话中交付三层本地检索：当前对话附件 → 当前学习项目文件 → 已授权
全局知识库。每层 FTS5 BM25 与 text-embedding-v4 向量结果按固定合同融合
（RRF k=60 + 层权重 3/2/1），独立候选配额（4/5/5）与去重规则，引用固化
为消息轮次（文件名/页码/章节/片段快照，索引重建不漂移），点击引用实时
校验授权并打开原文；无命中/冲突/覆盖不足/索引不可用输出结构化充足性。

### 新增模块
1. `contracts/retrieval.py` — CitationProjection / RetrievalRoundProjection /
   RetrievalLayerResult / RetrievalSufficiency / CitationDetailProjection
2. `retrieval/search.py` — 查询清理、FTS 窗口回退、向量余弦、RRF 层内融合、
   跨层加权合并与内容哈希去重、冲突与充足性判定
3. `retrieval/repository.py` — 检索轮次与引用持久化（账户作用域）
4. `retrieval/service.py` — 作用域解析、每轮检索编排、投影与引用详情授权校验
5. `tests/retrieval/` — 搜索单测 11 条 + 服务测试 17 条（作用域/配额/去重/
   隔离/充足性/引用详情/版本稳定）
6. `tests/chat/test_retrieval_chat.py` — 生成前检索、上下文注入、重试新轮次、
   知识库开关、刷新稳定（5 条）

### 修改
7. `storage/database.py` — SCHEMA_VERSION 10：retrieval_rounds + message_citations
8. `contracts/chat.py` — ChatMessageProjection.retrieval、请求 use_knowledge_base
9. `chat/service.py` — 生成前 run_round、最小上下文注入、思考摘要证据/工具、
   消息投影携带检索轮次
10. `api/chat.py` — send/retry 透传知识库开关、引用详情路由
11. `api/main.py` — 挂载 LayeredRetrievalService（真实 QwenEmbeddingPort）
12. 前端 — api.ts（useKnowledgeBase + getCitationDetail）、RetrievalCard.tsx
    （状态卡/引用展开/打开原文）、MessageList/ChatThread 接入、Composer
    来源层面板与知识库开关（模板基线不渲染）、openapi.json + generated.ts 再生成

### 收尾
13. 全量 pytest / ruff / mypy / npm typecheck+build / E2E 已验证
14. code-review 双轴审查并修复；更新 Issue 20 验收状态；提交


## Issue 17 实施计划（文档摄取与版本化全文/向量索引）

状态：已完成（2026-08-04）。全量验证：1254 pytest（+47，含 3 条审查回归）、117 E2E（+4）、mypy 160 文件 0 错误、改动区域 ruff 干净、npm typecheck/build 通过。双轴 code-review 修复 8 处缺陷后提交。

### 目标
把安全对象转换为可追溯、可恢复的本地检索材料：PDF/DOCX/TXT/MD/图片 → 解析（页码/章节/标题）→ 哈希分块 → SQLite FTS5(trigram) 全文 + text-embedding-v4 1024 维向量双索引；索引带不可混写版本合同，合同变化全量重建、校验后原子切换，旧版可回滚；后台执行器重启恢复未完成任务。

### 新增模块
1. `src/bridges/contracts/ingestion.py` — DocumentIngestionProjection / IndexStatusProjection / IndexContractProjection 契约
2. `src/bridges/ingestion/parsers.py` — PDF(fitz)/DOCX(zip+xml)/TXT/MD/图片解析器，产出归一文本 + (起始/结束/页码/章节) 跨度
3. `src/bridges/ingestion/chunker.py` — 结构锚点哈希分块（字符偏移可追溯）
4. `src/bridges/ingestion/embedding.py` — EmbeddingPort + 真实 Qwen 实现（L2 归一 + 维度校验）+ 确定性假实现；能力探测门
5. `src/bridges/ingestion/index.py` — 版本化索引：合同（model/dims/规范化/chunker/schema）、混合写拒绝、全量重建、覆盖率+维度校验、原子切换、回滚
6. `src/bridges/ingestion/service.py` — 摄取状态机（入队/领取/处理/重试/投影/清理）+ 账户内解析缓存复用
7. `src/bridges/api/ingestion.py` — 附件摄取详情 / 重试 / 索引状态路由

### 修改
8. `storage/database.py` — SCHEMA_VERSION 7：document_records、document_parse_cache、document_chunks、index_versions、index_active、index_vectors、fts_chunks（trigram）+ 存量附件回填入队
9. `contracts/chat.py` — ChatAttachmentProjection 增加 ingestion_status / ingestion_error
10. `chat/attachments.py` — 投影 LEFT JOIN 摄取状态
11. `api/chat.py` — 上传成功后人队
12. `api/main.py` — 挂载 ingestion service
13. `runtime/executor.py` + `cli/main.py` — worker 摄取轮（清理 → 摄取 → 索引维护）

### 前端
14. api.ts + MessageList 附件卡片状态芯片（loading/queued/processing/ready/empty/error/permission/recovery）+ 详情展开 + 重试；先调 ui-ux-pro-max
15. openapi.json + generated.ts 再生成；npm typecheck/build

### 测试
16. `tests/ingestion/` — 解析/页码章节/哈希分块/幂等重试/账户隔离/解析缓存
17. 索引合同测试 — 维度错误、版本漂移、重建失败、原子切换、旧版回滚
18. 编排测试 — 确定性 Embedding 假服务；显式真实冒烟（scripts/smoke）
19. E2E issue17 — 处理进度/失败原因/重试/重启恢复

### 收尾
20. 全量 pytest / ruff / mypy / npm typecheck+build / E2E；code-review 修复；更新 Issue 17 验收状态；提交

## 目标

按 `/improve-codebase-architecture` 审查报告（architecture-review-20260804-022658.html）
中的重要程度，逐项修复 9 个架构候选：SSE 流事件契约 → 账户隔离下沉 → 生成生命周期
收敛 → 模式编排加深 → 再认证横切门 → Qwen 适配收敛 → 观测层塌缩 → 删除模板平行宇宙
→ 前端接口接缝。每个候选完成后跑相关验证（pytest 子集/全量、ruff、mypy、npm typecheck/build）。

## 任务清单（按重要程度）

1. [x] 候选 2：SSE 流事件契约单一来源（Top 推荐）
   - contracts/chat.py 定义流事件 Pydantic 模型；api/chat.py 的 _generation_events 产出模型；
     模型进入 OpenAPI schemas；重新生成 openapi.json + generated.ts；前端 ChatStreamEvent 改为
     生成类型组合，事件名用判别式字段，删除手写镜像与硬编码字符串
   → 验证：test_openapi_sync 通过、聊天 58 测试通过、全量 1194 过（3 个 doctor CLI 环境
     编码 flake 改动前已存在）、npm typecheck/build 通过、E2E issue11/13/14 通过
2. [x] 候选 4：账户隔离下沉为数据库强制
   - storage/database.py 增加 scoped(account_id) 账户作用域查询面（INSERT 必须含 account_id
     列、其余语句 WHERE 必须含 account_id 过滤，违反即拒绝）；chat/repository.py、
     chat/attachments.py、storage/repository.py 账户域方法改用 scoped，系统级清理保持裸连接
   → 验证：新增 4 条作用域强制负例测试、全量 1198 过（4 个 CLI smoke 编码 flake 环境问题）、
     mypy/ruff 干净
3. [x] 候选 1：生成生命周期收敛为单一接口
   - 新建 chat/lifecycle.py GenerationLifecycle：停止信号注册/续期/TTL 陈旧判定/停止信号
     读取收敛为一个深模块（共享锁 + 单一数据源）；ChatService 删除散落的 _stops 注册表与
     4 个私有方法
   → 验证：新增 6 条 lifecycle 单测、聊天 64 过、mypy/ruff 干净
4. [x] 候选 3：模式编排加深
   - ModeContract 从提示文本升级为步骤化合同：OrchestrationStep Protocol + DeclarativeStep；
     _MODE_CONTRACTS 声明编排步骤（文案不变），_initial_thinking 从 steps 派生，教学门/
     检索/测验作为后续带 run 的步骤接入
   → 验证：聊天 64 过（步骤文案断言不变）、mypy/ruff 干净
5. [x] 候选 7：再认证横切门
   - FastAPI dependency RecentAuthRequired 统一门控（5 路由删除内联调用与 request 参数）；
     RecentAuthService Protocol 收窄 Any 类型
   → 验证：凭据/身份 75 过、mypy/ruff 干净（前端 401/reauth 拦截并入候选 8）
6. [x] 候选 6：Qwen 能力适配收敛
   - qwen_client 增加 first_choice/choice_text 共享实现；删除 qwen_adapters/qwen_vision_adapters
     的 _first_choice 副本与 ASR _first_choice_content；streaming.py（65 行浅文件）并入
     adapters.py 并删除，更新 7 处导入
   → 验证：ai/media 249 过、聊天相关 210 过、全量 mypy 0 错误
7. [x] 候选 5：观测层塌缩
   - 门面瘦身为审计事件流接口（删除 9 个 SLI/告警纯委托方法，调用方只经审计接口）；
     health probe 移除一次性 SLI/SLO 冒烟改为无副作用空查询；loop.py 保留（删除测试不通过：
     共享循环语义删除会移动到两处并漂移）
   → 验证：观测 34 过、相关域 263 过、mypy/ruff 干净
8. [x] 候选 9：删除模板平行宇宙 —— 经证据否决：issue04 E2E（评分资产）56 处引用
   /templates 路由，删除需重写 527 行验收测试，收益（构建体积/导航噪声）不抵风险；
   保留并在 templates/ 加 README 标注其 Issue 04 设计基线身份与生产重定向语义
9. [x] 候选 8：前端接口接缝
   - 统一错误解析：parseAuthError/parseDomainPackError/attachmentApiError 三套 → parseApiError
     + errorFromDetail 单一实现（62 处调用统一）；classifyApiError 统一 401/reauth 分类，
     KeySettings 6 处 + AccountSwitcher 6 处 + PersonalProfileSettings 1 处自写判断收敛
   - XHR 上传保留（进度跟踪的正当理由，错误解析已统一）；openapi-fetch 路由类型化不做
     （重写 1129 行 api.ts 风险收益比不佳，契约类型已由候选 2 消费）
   → 验证：npm typecheck/build 通过、E2E 13 过（issue08 视觉快照 83 像素差异为环境 flake，
     stash 后同样失败）
10. [x] 全量回归：pytest 全套 1202 过（2 skipped，6 个 CLI smoke 编码 flake 为环境既有）、
    全量 mypy 152 文件 0 错误、改动区域 ruff 干净（剩余 2 项为未触碰文件的既有问题）、
    npm typecheck/build 通过、E2E 关键 spec 通过（issue08 视觉快照 83 像素差异为环境 flake）

## 验收命令

```powershell
conda run -n agent python -m pytest -k "<候选相关>"
conda run -n agent python -m pytest -x -q          # 全量
conda run -n agent python -m ruff check .
conda run -n agent python -m mypy src
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
npm --prefix apps/web run test:e2e -- --project=chromium
```
