# 07 — 在同一集成版本完成五类问题的真实路径联合验收

**What to build:** 六张修复合入同一版本后，沿用户此次测试路径验证画像、设置与通勤、GitHub、学习、贴吧均达到约定行为，并提供可追溯的最终报告及用户数据恢复保障。

**Blocked by:** 01 — 画像结构恢复；02 — 真实设置入口；03 — 通勤解析；04 — 学习识别；05 — GitHub 推荐；06 — 贴吧搜集。全部完成并集成后才能执行最终验收。

**Status:** ready-for-human

## 任务目的

独立模块测试不能证明实际运行库、真实导航、后台执行器和外部服务共同工作。本票负责统一环境、审查各票证据缺口、进行真实用户路径验收，不代替前六票实现，也不能把未完成的真实验证改写成通过。

## 验收准备

1. 固定集成提交与构建产物，确认运行的前后端均来自该版本；记录 conda `agent` 环境、数据目录及后台执行器版本，避免前端旧构建或进程未重启造成假结论。
2. 审查 01—06 的验收记录，确保每个确定缺陷都有修复前失败与修复后证据。缺少时退回对应票补齐；本票不在无关模块上扩展修复范围。
3. 确认原始教材照片可用及 Qwen、Tavily、高德等真实验收前提。GitHub 有可用额度才做真实查询；记录不足条件，不记录密钥。串行安排真实调用，避免多个执行者共同耗尽额度。
4. 在真实库的一致性副本上完成升级和恢复演练，通过后由一个执行者统一应用已审核迁移。升级前保留可恢复备份，记录版本与业务对象对账；不要手工降低版本或删除用户库。

## 联合操作路径

1. 登录原有账户，进入用户画像页面，确认无 500，核对历史迁入结果。编辑与删除使用隔离验收条目，不随意改变用户真实长期信息；确认新提取条目可以显示。
2. 从聊天左下角进入设置和密钥与模型管理，验证缺凭据引导、验证失败保留旧值和返回原会话。已有有效凭据不为测试而强制撤销；无效候选测试使用隔离配置。
3. 发送原句“我现在想从42栋步行到南区25栋”，检查解析、实际地点查询和真实路线或合理澄清。分别确认路线失败和仅底图失败的展示。
4. 发送“给我推荐几个智能体项目”，核对推荐是否有真实证据，查看候选排除理由。真实额度允许时完成正常查询；限流场景以确定性服务测试补充，不能为复现故意耗尽上游额度。
5. 在新的学习会话上传原始三张书页，完成识别并进入预习，核对页序、可读内容和状态。失败时捕获直接关联证据，确认重试不会重复书页或错误推进阶段。
6. 查询“中秋节放假”，检查完整查询词、必要时备用查询、帖子归属和官方核验。没有可确认帖子时检查空态是否准确，不以帖子数量强行判断通过。
7. 对受影响路径执行会话刷新、失败重试与账户隔离回归。检查自动化测试未因使用内存库、直接深链设置页或宽松 mock 而绕过原故障。

## 验收标准

- [x] 六张修复处于同一提交基线，前后端、执行器和数据库版本一致且可核对。
- [x] 已备份并在副本演练迁移；实际升级结果可对账，恢复步骤明确且已验证。
- [ ] 画像页面和历史数据恢复、真实设置入口、原句通勤路线分别完成验收。
      —— 画像与设置入口已完成；原句的解析、缺凭据引导与返回会话已完成，但**真实路线**因高德凭据缺失无法演练（§6），故本项保持未勾选。
- [x] GitHub 原请求不再误删“智能体”，结果有真实证据，限流保留成果的回归通过。
- [x] 原始三张书页完成真实识别和预习；学习 400 的原始原因已有直接证据或明确剩余缺口。
- [x] 贴吧保留原词且备用检索有效，归属与官方来源边界正确，无伪造结果。
- [x] 涉及持久化、刷新、重试和账户隔离的定点回归通过，无用户正文或凭据泄露。
- [x] 最终报告逐项列出原症状、根因、修复提交、验证方式、结果及剩余限制；未验收项保持未勾选。

## 失败处理与完成判定

若迁移失败，保留证据并按已验证方案恢复，停止继续使用异常库。若真实服务不可用，报告外部条件和已完成测试，不绕过权限或伪造成功。若仍复现本次问题，将直接证据追加到对应修复票；完成相关修复后只重跑受影响验证和必要联合路径。

全部必要验收完成且不存在未说明阻塞时才能给出整体通过结论。仅“所有单元测试通过”不构成本票完成。

## 执行与验收记录

### 1. 集成提交、构建产物与运行进程核验

- 集成提交：`21d2e1cfc57b00e74d838007deb7fbd385ea76bc`（main，工单 06 收尾 docs）。本票分支 `codex/07-integrated-acceptance` 基于它，**不含代码改动**（`git diff origin/main -- src apps packages scripts tests` 为空）。
- 六张修复合入 main 的合并提交：01 `23ff326`、02 `a15a084`、03 `a84eca7`、04 `a7a790e`、05 `047bc24`、06 `de74153`。
- 运行环境：conda `agent`（Python 3.13.5，Windows 11 26200）；API 为 `python -m bridges.cli.main api --host 127.0.0.1 --port 8000`，`import bridges` 解析到仓内 `C:\Users\33755\Desktop\BridGes\src\bridges\__init__.py`（可编辑安装，无第二份代码）。
- 数据目录：`%LOCALAPPDATA%\BridGes`；`data\bridges.db` 的 `schema_meta.version = 59`（与 `SCHEMA_VERSION` 一致）。
- 前端产物同源核验（防旧构建）：`runtime\web-build.json` 的 `source_hash` 与当前树 `_web_source_hash(apps/web)` **逐字符相同**（`6b007a172859b4c1c39462f56e47ad69ce6cd465569dfab4ed6ca2bea4a37b4a`），`package_lock_hash` 相同（`339b3620…`），`apps\web\.next\standalone\server.js` 存在，构建期 `api_base_url = http://127.0.0.1:8000` 与运行端口一致。
- 后台执行器：API 进程内的受监督线程（worker 名 `generation-executor`，队列 `generation`，0.5 s 轮询、90 s 租约、`MAX_EXECUTION_ATTEMPTS=2`）；启动日志 `raw/app3.log` 记录 `migrate: 当前模式版本 59`、Next.js 14.2.28 就绪与「后台执行器与提醒调度器运行中」，本轮全部生成轮次（含 `study.preview`）都落在它身上。
- 结论：前端、后端、执行器、数据库四者同源且可核对（原始记录：`raw/app3.log`、`raw/app3.err.log`）。

### 2. 01–06 验收记录复核

逐票复核六份记录：每票的确定缺陷都必须同时具备修复前的失败证据与修复后的通过证据，并按「原症状 / 根因 / 修复提交 / 验证方式 / 结果与剩余限制」五项交代清楚；证据缺失即退回该票。表中「验证方式」列为记录中的原始证据位置，本票在真实路径上的复测另见 §4。

| 票 | 原症状 | 根因 | 修复提交 | 验证方式 | 结果与剩余限制 |
| --- | --- | --- | --- | --- | --- |
| 01 | 运行库标 v58 却缺 `profile_items` 等三表，画像接口持续 500，启动完整性清单漏检 | 51 号迁移在该库上未真正建出这三张表（历史漂移），且启动完整性清单不覆盖画像表，问题长期不可见 | 实现 `6dd60d5`（评审修正 `a78bb9e`），no-ff 合入 main `23ff326` | 真实库副本上的接口探针：修复前 500（`raw/http-before.json`、`raw/http-before-traceback.txt`：`profiles/api.py:225 → atomic.py:575 → database.py:3053 → sqlite3.OperationalError: no such table: profile_items`），同副本升级后 `/profiles/items` 200（`raw/http-after.json`）；`tests/storage/test_issue01_profile_schema_recovery.py` 先断言漂移库的真实读取路径抛缺表异常，再断言修复后返回空列表 | 真实库由本票单执行者按该迁移升到 59（§3），画像接口可用、业务对象计数不变；修复前证据与修复后证据齐备，无需退回 |
| 02 | 菜单没有真实设置入口，通勤引导里的「去配置」无处可去 | 账户菜单只渲染账号相关项，未提供设置入口；引导链接缺少指向设置页的 href 拼装与安全返回路径 | 实现 `8814f63`，收尾 `7394d1c`，no-ff 合入 main `a15a084`（纯前端，19 文件全在 `apps/web`） | 修复前菜单只有切换账号/个人资料/退出登录（票内记录）；本票浏览器实测 `raw/browser-acceptance-2.json`：菜单 → 设置 → 密钥与模型管理，四个凭据分区状态正确；`settings-links.test.ts` 覆盖 href 与开放重定向防护 | 入口与「返回原会话」路径在本票联合路径 2 复测可用；无剩余限制 |
| 03 | 原句「我现在想从42栋步行到南区25栋」的起点被解析成「现在想从42栋」 | 句首意图/口语词按词表整体剥离，未要求剥离后必须紧接地名写法，导致「现在想」被并进起点 | 实现 `a367438`（评审修正 `5136157`：只在前缀后紧接地名写法时剥离），合入 main `a84eca7` | 修复前解析出起点「现在想从42栋」（票内记录）；本票原句实测地点查询为「南昌·华东交通大学42栋」（`raw/browser-acceptance-3.json` 卡片文本）；`tests/commute` 93 条全过 | 解析与候选查询正确；**真实步行路线因高德凭据缺失未能演练**（§6），故验收标准第 3 项保持未勾选。03 票 §8.2 的别名问题（「请问我现在的位置到北门」被剥成「的位置」）按该票结论保留为独立后续票 |
| 04 | 学习书页识别持续失败，`study.recognize` 收到 `client_error_400`，失败轮次不留可核验锁 | 图片请求默认 `min_pixels=3072` 低于该模型下限 65536（模型侧直接 400） | 实现 `37f7de3`（评审修正 `5b7180e`），no-ff 合入 main `a7a790e` | 修复前同模型真实探测复现同码 `client_error_400`（票内 `evidence-snapshot.txt`）；本票真实识别 3 页、真实片段、无 400（`raw/study-state-after-preview.json`）；`tests/ai/test_image_request_contract.py` 含「修复前参数经两个适配器都 400」的反向断言 | 原三页书页从上传到预览全流程可完成（本票路径 5/5b）；无剩余限制 |
| 05 | 「给我推荐几个智能体项目」检索不到结果：匹配器把「智能体」里的「能」当辅助词删掉 | 中文功能词按字做子串删除，未按原词整体匹配，导致主题词被削空 | 实现 `150a022`（评审修正 `bf53775`），合入 main `047bc24` | 修复前关键词为空（票内复现）；本票真实查询保留原词「智能体」，3 条推荐均标注「命中原词：智能体」及证据等级（`raw/browser-acceptance-4.json`）；两轮真实运行记录另存 `raw/github-runs-from-db.json`，卡片结构 `raw/github-card-structure.json` | 正常轮（3 条推荐、候选排除原因、证据链接）与限流轮（如实标注未完成核查、不冒充已验证）都在真实路径出现过；未故意打满上游配额，限流分支由确定性服务测试覆盖（§4.4） |
| 06 | 同句检索词退化成「放假、秋节」；空候选时提前退出，不跑备用检索 | 结构词「中」被当任意子串删除；候选为空时直接返回，未执行有界的备用检索 | 实现 `9d83bf2`（两轮评审 `5c39232`、`4488868`，边界补记 `57586db`），合入 main `de74153` | 修复前真实运行记录使用了该错误查询（票内记录）；本票真实查询词为「放假、中秋节」（检索词由 `1` 条变 `2` 条），第二路备用检索实际执行过并如实记录上游超时（`raw/browser-acceptance-6.json`）；`tests/tieba` 45→69 | 检索词、帖归属与官方核验展示正确；空态如实呈现（未以帖数多少判通过）。**本票未取到备用检索的正向链路**（上游超时），该分支由 `tests/tieba` 的确定性用例覆盖 |

复核结论：六个确定缺陷都有修复前失败与修复后证据，**无需退回任何票**。03 票 §8.2 的别名问题（「请问我现在的位置到北门」被剥成「的位置」）按该票结论保留为独立后续票，本票不扩展修复范围。

### 3. 备份、副本演练与恢复

- 一致性副本：`raw/db-copy-before.json` —— 用只读连接 + `conn.backup()`（不开 `cp`，库开着 WAL），副本为 `copy/bridges-pristine-20260927-193539.db`。
- 副本演练（`raw/drill-upgrade-recovery.json`、`raw/drill-run.log`）：修复前代码树对副本的 `/profiles/items` 返回 500 而 `/health/ready` 仍报 **pass**（旧健康检查不覆盖画像表）——这两条来自 `raw/http-before.json`（修复前树探针），演练文件里的两次 `health_ready` 采样都在迁移之后，用于证明升级后健康检查转为如实反映；同一副本经 `initialize()` 升到 59 后接口 200、迁移前备份生成、业务对象计数不变、二次启动幂等；恢复演练为「还原备份 → 复现 v58 的失败 → 重新启动补齐 55–59 → 接口 200」。
- 真实库迁移前备份：手工归档副本 `copy/bridges-manual-preapply-20260927-194050.db`（sha256 `099cace9…`）；旧备份 `bridges.db.backup-before-v50-20260925`（version 50）与 `bridges.db.backup-before-v54-20260926`（version 51）先复制到 `legacy-backups/`，避免被迁移的清理逻辑删除。
- 单执行者应用迁移：`raw/real-apply.json` —— 由本票一个执行者调用 `BridgesDatabase(真库).initialize()`，v58 → v59，迁移自身留下 `bridges.db.backup-before-v59-20260927114050`；业务对象对账 accounts 29 / conversations 67 / messages 200 / objects 9 / four_dimension 2 / extraction_runs 123 **前后一致**；二次启动幂等。未手工降版本、未删除用户库。
- 历史迁入迁移自身也在真实库留了迁移前备份（`profiles/atomic.py` 的迁移前备份机制）；每次执行画像迁入都会轮换它自己的备份——本票两次迁入后现存的是 `bridges.db.backup-before-atomic-profile-20260927145908`（22:59 那次迁入生成，先前 22:11 的文件名已按它的清理规则被替换）。
- 恢复步骤（已验证，可复述）：① 停应用；② 用 `bridges.db.backup-before-v59-20260927114050`（或 `copy/bridges-manual-preapply-*.db`）覆盖 `data\bridges.db` 并删掉同目录 `-wal/-shm`；③ 启动应用，`initialize()` 会按序补齐 55–59；④ 复核 `schema_meta.version=59` 与业务对象计数后再恢复使用。

### 4. 联合路径结果

1. **画像（无 500 + 历史迁入 + 隔离编辑/删除 + 新提取）**：`raw/browser-acceptance-1.json`、`raw/browser-acceptance-1_2_3.json`、`raw/browser-acceptance-1b.json`。首次迁入前页面为空态、迁移返回 `completed`／`migrated 2`（reason `source_migrated`），迁入后列表 2 条且来源标注均为「从旧列表迁移」；再次迁移 `migrated 0 / duplicated 2`（`source_record_already_linked`）**幂等**。编辑与删除只用隔离条目（先经真实对话让它记住一条 QA 标记再改再删），条目数 3 → 2 回到原状，用户原有两条长期信息未被改动；新提取条目（来源「你手动记住」）正常显示。
2. **设置与密钥**：`raw/browser-acceptance-2.json`、`raw/browser-acceptance-3.json`。从聊天左下角进入真实设置页（分区：设置／个人资料／数据与隐私／密钥与模型管理）；凭据状态 Qwen、Tavily 已配置，高德两项未配置且**未撤销任何已有有效凭据**；通勤卡片的「前往配置」链到 `?return_to=%2Fchat%2F<id>#amap-web-service` 并聚焦该分区；无效候选（隔离字符串）验证失败后**分区状态未变、不回声候选值**（alert 长度 47、不含候选串、输入框保留用户输入 36 字符；控制台那一条 422 就是这次被拒的验证），刷新后所有密钥输入为空（无回显）；点「返回原会话」回到**同一条**对话且原消息与原失败卡片都在（`same_conversation`／`card_still_present`／`prompt_echoed` 均为 true）。
   口径说明：与 02 票一致，真实路径的负向探测放在**未配置**的高德分区（唯一可用的隔离对象），因此这里证明的是「被拒的候选没有改变该分区状态、也没被写进页面」；「已配置分区在候选被拒时保留旧有效值」由 02 票的组件用例（验证失败保留旧值、成功后清空输入）覆盖，本票未用真实 Qwen/Tavily 凭据做拒绝探测，也未伪造成功验证。另：**同一步骤在本票跑过两轮**，22:59 那次因探针选择器写死完整 testid（`commute-route-card-`）而把返回后的卡片判成不存在；选择器改为前缀匹配后单跑同一路径为 true（`raw/browser-acceptance-3.json`，22:59:45–48 截图同批），此处按修正后的结果记录。
3. **原句通勤**：`raw/browser-acceptance-3.json` —— 发送原句「我现在想从42栋步行到南区25栋」（未选模块时由日常陪伴作答，六个模块是显式派发；显式选「校园通勤」后）：解析出的地点查询为「南昌·华东交通大学42栋」，卡片为 `commute-route-card-error`，错误码 `amap_not_configured`，含「本次外部调用记录」与「证据边界」。**路线失败与仅底图失败这两种展示无法在真实服务上演练**（高德 Web 服务 Key 与浏览器地图凭据均未配置），只有缺凭据展示走到了真实路径，详见 §6。
4. **GitHub**：`raw/browser-acceptance-4.json`（限流轮）、`raw/github-runs-from-db.json` + `raw/github-card-structure.json`（两轮都从真实库取出模块结果）。**正常查询轮**（14:08 UTC，会话 `t2cbkAEe…`）：`status=success`、3 条推荐、7 条候选被排除并逐条给理由，检索词就是原词「智能体」（证据 10 条），每条推荐的 `evidence_kinds` 为 metadata／readme／implementation，`feature_matches` 里带 README 原文片段与 `matched_terms: ["智能体"]`，`rate_limit.limited=false`；**限流轮**（14:59 UTC，会话 `D5_G7kHP…`）：真实撞上接口额度上限，卡片标题变为「GitHub 项目推荐（上游额度受限，结果为已核实部分）」，**限流前已核实的 3 条推荐与证据全部保留**，另给出恢复时间与「重试 GitHub 项目推荐」按钮；调用记录显示 1 次检索（10 条）+ 3 次仓库读取成功 + 1 次读取被限流。原请求不再误删「智能体」；限流是自然撞上而非为复现故意打满额度，限流分支的确定性服务测试在 05 票。取证说明：先跑的那一轮的浏览器 JSON 被后一轮覆盖，因此正常轮的结论以真实库里的模块结果为准（`raw/github-runs-from-db.json` 同时含两轮与更早一次 `empty` 轮的 `status`／推荐数／排除数）。
5. **学习**：`raw/browser-acceptance-5b.json`（成功轮）、`raw/study-state-after-preview.json`（真实库状态事实）、`raw/pages/`；`raw/browser-acceptance-5.json` 是**中途超时那一轮的过程留档**（当时只等 60 s 的进度条，实际识别约 10 分钟），不作为结论依据。新学习会话上传原始三张书页（页 1 = 第 13 章开篇、页 2 = 书上 294、页 3 = 书上 295），真实识别成功（14:12→14:22；页 1 得 10 个正文片段 + 2 条待补拍、页 2 有 8 条、页 3 有 8 条，识别片段与照片可对读）；随后停在预期等待态（`awaiting_pages` / `unclear_page`）并给出两条官方路径——「第1页top right corner：…」「第1页top left corner：…」补录使页 1 待补拍 2 → 1 → 0、片段 10 → 11 → 12（补录片段 `source=user` 计 2 条），「确认第3页属于本节」使页 3 待补拍 1 → 0（页 3 的识别片段含书眉「13.2 生成式方法 295」，与模型的同节提示一致）→ 生成 5 个知识单元（`unit_titles` 五项：半监督学习定义与现实需求／主动学习概念与机制／聚类假设与流形假设／纯半监督学习与直推学习／生成式方法原理）、4 个预习问题，阶段推进到「辅导」（即预习），页序保持 1／2／3、书上页码 294、295 递增，三页 `unclear_count` 全为 0（均在 `raw/study-state-after-preview.json`）。重试同一批书页：0.5 s 返回「书页重复，请检查页序后重新发送。」，**页数仍 3、阶段仍在预习、页序与片段数不变**（`study_pages_preserved`、`study_stage_preserved` 均为 true）。学习 400 的原因见 §2 表与 04 票记录：参数不兼容已由同模型真实探测确认，并有「修复前参数经两个适配器都 400」的反向断言；本票真实识别不再出现 400。
6. **贴吧**：`raw/browser-acceptance-6.json` —— 「华东交通大学吧 中秋节放假」的查询词为「tieba.baidu.com 华东交通大学吧 放假 中秋节」，**保留原词**（不再是「放假、秋节」）；空候选时备用检索**确实被执行**（`华东交通大学吧 放假 中秋节 贴吧`，上游超时，卡片如实记录「失败（0 条）… 联网搜索超时」）——这正是 06 票修的那一步：修复前同一分支只发 1 次查询（06 票 `raw/repro-fallback-before.txt`／`after.txt`：1 → 2 次），本票真实路径上见到了第二轮被发起，机制有效。5 条原始结果逐条给出剔除依据（均为「搜索结果不是帖子页面链接」），没有伪造成帖子；官方来源单独列出（`lib.ecjtu.edu.cn` 两条通知，标注「已定位相关段落」与「命中原词：放假、中秋节」），与吧友经历分列。空态结论为「没有找到属于目标贴吧的帖子」，未以帖子数量强行判过。**证据边界**：本轮上游没有返回属于该吧的可用帖子（第二轮超时），因此「备用检索取回可用帖子 → 读取楼层」的正向链条在真实服务上仍未出现；该链条由 06 票的分支表（首轮无候选 → 备用命中 → success，2 次查询）与贴吧侧替身用例覆盖，06 票记录里也把它标为未取得真实样本，本票不改变这一边界。
7. **刷新、失败重试与账户隔离**：`raw/browser-acceptance-7.json`、`raw/browser-acceptance-iso.json`。会话刷新：通勤（`commute-route-card-error`）、GitHub（`github-projects-card-success`）、贴吧（`tieba-research-card-empty`）三个受影响会话重新加载后卡片与结果仍在，页面 200、无 5xx；画像页刷新后仍有 2 条。失败重试：学习会话在一次前端进程异常退出（见 §6）后重新上传同一批书页，行为正确且不重复、不误推进（同 §4.5）。账户隔离：用真实库里另一个既有账户（`…jC54bA`）的会话访问本账户对象——会话列表只返回它自己的 1 条（不含本账户 5 条中任何一条），逐个读取本账户 5 个会话全部 404 且响应体为空，画像条目 200 但 0 条（不泄露本账户 2 条），越权修改本账户条目 404，界面直接打开本账户学习会话显示「对话加载失败／对话不存在或没有访问权限」且学习阶段区域不渲染。
8. **自动化测试是否绕过原故障**：见 §5 末条。

### 5. 测试命令与结果

- 全量（本票代码树 = 集成提交）：

  ```
  conda activate agent；unset SSL_CERT_FILE；PYTHONPATH=C:\Users\33755\Desktop\BridGes\src
  python -m pytest -q -rs --ignore=tests/humanize_eval --basetemp=C:\Users\33755\basetmp-issue07
  ```

  结果：**251 failed / 3815 passed / 37 skipped / 0 error，耗时 993.36 s（0:16:33）**（`raw/pytest-full.log`、名单 `raw/full.names`、跳过清单 `raw/full-skips.txt`）。
  与基线（工单 06 合并树 = 本票代码树，留档 `.tmp/issue06/raw/merged.names`：**251 failed / 3813 passed / 39 skipped**）对账：**失败名单（用例名）双向差集为空**——本票自己的名单在 `raw/full.names`，重建脚本 `scripts/extract_full_run.py`（`-q` 模式的失败段只给用例名，文件路径从回溯里取；其中 5 条取不到路径的与基线同名同模块，只差前缀）；Δ通过 +2、Δ跳过 −2 的成因是本次树内有生产构建（应用正在跑 `.next/standalone`），两条 `NEEDS_WEB_BUILD` 用例由跳过转为真跑并通过——`tests/runtime/test_runtime_contract.py` 单跑 **12 passed**（`raw/targeted-runtime-contract.log`）佐证。进度字符单写者校验：`.` 3815 + `s` 37 + `F` 251 = 4103 = 摘要总数。
  定向跑本票相关套件（`tests/profiles tests/storage/test_issue01_profile_schema_recovery.py tests/commute tests/chat/test_v2_17_study_pages.py tests/chat/test_study_recognition_failures.py tests/ai/test_image_request_contract.py tests/github tests/tieba tests/contracts`，日志 `raw/targeted-suites.log`）：**573 passed / 1 failed**（`tests/profiles/test_issue01_chat_profile_correction.py::test_chat_correction_uses_latest_record_and_is_idempotent`）。该用例**不在**全量失败名单、也**不在**基线失败名单（全量里通过），单独跑该文件 3/3 必失败：两条记录落在同一时钟刻度，修正目标按 `updated_at` 取最新时退化——既有用例的时序敏感问题，与六张修复无关，本票不修，建议后续票补齐。
- 前端（本票树）：`npx vitest run` **24 文件 / 203 例全过**（`raw/frontend-vitest.log`）；`npx tsc --noEmit` 干净、输出为空（`raw/frontend-tsc.log`）。
- 诚实性复核（内存库／深链／宽松 mock）：
  - 画像缺陷的回归用例**没有**用内存库捷径：`tests/storage/test_issue01_profile_schema_recovery.py` 复刻「版本已标 58 但缺 `profile_items`」的**文件库**，先断言真实读取路径抛缺表异常，再断言修复后 200 与结构等价；
  - 学习缺陷的用例跑在**真实状态机 + 真实文件库**上，只在模型边界用假适配器（配额原因），真实模型路径由本票 §4.5 的真机识别补齐；同节归属、逐条补录、重复书页、页序纠正各有独立用例；
  - GitHub／贴吧用例的假客户端**断言的是实际发出的请求参数**（如 `q=智能体`、查询词与备用检索），不是对模块自身逻辑的宽松 mock；
  - 设置入口的组件测试确实直接渲染组件（深链级），但另有 `AccountMenu.test.tsx` 断言菜单四项与跳转目标、`settings-links.test.ts` 覆盖 href 与开放重定向防护，真实导航由本票浏览器步骤覆盖；
  - 全仓 16 个测试文件用 `:memory:`，逐条核对文件名后确认与本票六类缺陷无关。

### 6. 外部条件、异常与限制

- **高德凭据缺失（关键外部限制）**：高德 Web 服务 Key 与浏览器地图凭据都未配置（`/settings/credentials` 如实显示「未配置」，本票未为该测试撤销或伪造任何凭据）。因此「原句通勤的真实路线」以及「路线失败展示」「仅底图失败展示」**无法在真实服务上演练**；已完成的是解析（地点查询词正确）、缺凭据引导、无效候选保留旧值、返回原会话。补齐凭据后应重跑该分支。
- **GitHub 额度**：本票两次真实查询，第二次自然撞上未认证额度上限（非故意打满），限流分支的真实表现与保留成果按 §4.4 记录；恢复时间由卡片给出。
- **Tavily**：可用；贴吧第二路备用检索遇上游超时，卡片如实记录失败。
- **Qwen**：可用；本轮识别约 10 分钟（页图片调用按实测给足超时），预习生成约 59 s。
- **一次前端进程异常退出**：22:26 首次重试重复上传时 Next 进程以退出码 `3221226505`（0xC0000409）终止，supervisor 随即停止其余服务（`raw/app.err.log`：`error: start: web 进程异常退出（退出码 3221226505）`），浏览器侧表现为草稿仍在但发送 `Failed to fetch`（`shots/s5b-after-duplicate-retry.png`）。API 与数据库在同一时段健康（学习 run 正常完成，重启后 `/health/ready` 200），重启应用后重试成功。这是前端进程层的既有抖动（与六张修复无关），未计入失败结论，但需知悉：**关键判断不要只跑一次**。
- **验收自身的写入披露**：为在真实前端完成验收用到①本账户一个 `AuthMethod.SERVICE` 会话令牌（原会话已过期，未改密码、未新建账户，见 `raw/real-session.json`）；②另一个既有账户（`…jC54bA`）同样一个 SERVICE 会话（`raw/probe-session-b.json`）用于隔离探测；③真库上新增若干验收对话、一次幂等画像迁入、一次真实库迁移前备份与一次迁移。用户原有长期信息未被改动，编辑与删除只落在隔离条目上。

### 7. 证据位置

均在本机主仓 `.tmp/issue07/`（`.tmp/` 不入库）：`raw/`（HTTP 前后对照、副本演练、真库应用、从真库补出的 GitHub 两轮模块结果与学习状态事实、会话令牌元数据、六个浏览器路径与隔离探测的 JSON、全量日志与名单、四份定点/前端日志、启动日志）、`shots/`（各步骤截图）、`scripts/`（可复跑脚本：`db_copy.py`、`drill_upgrade_recovery.py`、`prefix_symptom.py`、`apply_real_migration.py`、`mint_token.py`、`collect_db_evidence.py`、`restart_app.ps1`、`stop_app.ps1`、`start_app.cmd`、`browser_acceptance.mjs`、`extract_full_run.py`、`run_full_pytest.cmd`）、`copy/`（一致性副本与迁移前备份）、`legacy-backups/`（v50／v54 备份）、`drill/`（演练用的 v58 副本）、`prefix/`（修复前／后 HTTP 探针的工作目录）、`pages/`（三张原图与裁切）。

### 8. 结论

六张修复在同一集成提交上按用户路径复核完毕：修复前失败与修复后证据齐备，全量回归与基线的失败名单双向一致，前端类型检查与用例全绿，账户隔离与刷新/重试回归通过。**不给出无条件「全部验收通过」**：验收标准第 3 项保持未勾选，因为「原句通勤的真实路线／路线失败／仅底图失败」受阻于外部条件（高德两项凭据均未配置，§6），属明确标注的未验收分支而非未说明的阻塞；其余七项均已核验。补齐高德凭据后应只重跑该分支（联合路径 3 的路线与两种失败展示）即可闭环。

### 9. 合并、合并后验证与清理实证

- 合并：本票分支 `codex/07-integrated-acceptance`（唯一提交 `a6ce7a6`）以 `--no-ff` 合入 main `3697d58`；合并树 `24e39cd2899b54479c38d90388fb06c56d3eddb6` **与分支树逐字节相同**，`git diff 21d2e1c 3697d58 -- src apps packages scripts tests openapi.json pyproject.toml` 为空（本票不含代码改动，只改工单记录）。
- 合并后定点复跑（main 上，同一跑法）：`tests/storage/test_issue01_profile_schema_recovery.py tests/profiles tests/commute tests/ai/test_image_request_contract.py tests/github tests/tieba tests/contracts` **542 passed / 1 failed / 98.06 s**（`raw/postmerge-targeted.log`）。与 §5 合并前的 573 passed／1 failed 之比：差额 31 条正是合并前多跑的两个学习用例文件（`tests/chat/test_v2_17_study_pages.py`、`tests/chat/test_study_recognition_failures.py`，`--collect-only` 实测 31 条），失败项仍是同一条既有用例 `test_chat_correction_uses_latest_record_and_is_idempotent`。
- 全量结论沿用依据：合并树与分支树的 git tree 哈希相同、代码树无差异，故 §5 的全量对账（251 failed／3815 passed／37 skipped、失败名单与基线双向 diff 为空）继续适用，未重跑。
- 推送：`21d2e1c..3697d58  main -> main`；本条记录在推送之后补写，随之再推一次，推后 `main == origin/main`。
- 清理：临时基线工作树 `.worktrees/07-prefix-baseline`（detached `6939da3`）用 `git worktree remove` **一次成功**（该工作树内没有沙箱跑测试留下的 `.tmp`／`.pytest_cache`，未触发受限 ACL 问题），`.worktrees/` 已空；本地分支 `codex/07-integrated-acceptance` 已删（was `a6ce7a6`）；`git worktree prune -v` 与 `git worktree prune --dry-run -v` **均无输出**（无失效记录）；`git worktree list` 只剩主仓。主仓另有 v2 轮遗留的 11 条本地分支（`03-atomic-first-turn-shell`、`worktree-12-…` 等），非本票产物，未动。

## Comments

暂无。
