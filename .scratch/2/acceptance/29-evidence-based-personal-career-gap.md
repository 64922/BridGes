# 工单29独立验收（2026-10-04）

验收依据是工单、仓库规范、固定 main 与实际代码/测试。编码代理报告仅用于定位，原交付初审不通过。固定点为 `809f05a5358568c09f3f656f54357fe971fe66b2`，原交付为 `38ce6ec92c5827b7f7fdd8bef73513fee99a88e8`。前置19/28已有代码、合同和独立验收记录，并通过本次画像、岗位、内核与任务接缝验证复核，不只依赖 Status。

## Standards

code-review 的规范轴独立审查发现2项硬规范缺口，均已修复：

1. 背景输入键漏画像有效性，恢复绕过提供者；违反 `docs/workflow/orchestration.md` 的复用前版本/时效校验、`delivery-and-validation.md` 的 R05。现在每次收据复用前检查背景，内容/版本参加键；最终事务再次检查，变化只修复一次，持续变化拒绝交付。
2. 策略行动标为直接证据；违反 `daily-workflows.md` 的直接证据/推断分开。现在小项目、练习、简历转换、自测、时间安排、兴趣题材及组合需求均标为推断，分类事实保留两侧直接依据。

没有要求额外重构的 Fowler 嗅觉。画像提供者/节点接口保持原所有权，未改变其他任务代码。API沿用既有环境初始化后导入模式，Ruff E402/I001为同类既有诊断（main 111/1，本分支115/1；新增导入处延用此模式），未整体重排相邻导入。

## Spec

需求轴初审确认4类缺陷，并在复验中追查2项接缝，均已修复：

1. 整句否定污染其他技能、旧负面覆盖本轮正面。改为分句与技能谓词绑定；当前自述优先，等层冲突保持待确认；疑问、第三方、愿望、引用和不明确断言不确认能力。新增负例修复前9项失败。
2. 删除/过期背景可能恢复旧结论。有效背景参与恢复键；正常恢复不重复编译同轮切片；失效/损坏重编；公开五节点维持复用。核验后撤回与持续变更均有竞争测试。
3. 个人风险复核门固定PASS且未执行。现在 `career.personal_review` 为必要门，由独立代码规则只读原始样本、背景与待核结论，重新查语义支持、来源、冲突、策略/组合依据；没有独立来源的新能力综合推断阻塞，未知不改成不足。代码复核不是模型语义效果评测。
4. 全技能已有依据时遗漏项目组合需求。资料和项目请求仍输出最小需求及岗位依据；未知能力不写成缺口定论。
5. 19冻结快照接缝。读取既有运行配置 `adopted_profile_slice`，有效时只选子集；失效、损坏或缺失才重编，保存时删除请求及未采用正文。每次核验账户、开关、来源/版本/期限，异常降级。
6. 个人任务续轮丢分支/时间。条件修订从当前任务补齐；当前只岗位要求优先。真实聊天提问后回答“我学过Java，每天30分钟”实际保留个人分支并更新差距。

## 逐项验收

| 工单项 | 独立证据 | 结论 |
| --- | --- | --- |
| 任务1：登记个人背景/差距/建议/核验，只个人分支读背景 | 九节点配方；只岗位API提供者零调用；当前陈述/任务原话与19切片来源分开；任务依据绑定真实任务ID/版本和文本序号 | 通过 |
| 验收1/任务2：未知不等于不足，确认项双侧定位 | 技能混合肯否、意愿/第三方/疑问、同层冲突、当前覆盖旧画像；必要门拒绝伪引文及错分类 | 通过 |
| 验收2：背景不足交付真实岗位部分再问一个问题 | API无画像/失败/撤回场景仍有实际页面替身读取的岗位样本、统计和边界；个人关键问题一个；真实续轮有效 | 通过 |
| 验收3/任务3：时间/资源改变实际可执行动作 | 10分钟先读例子，30分钟单功能练习，90分钟最小项目；只有手机先读写思路暂缓电脑项目；当前时间优先，超预算时间不退回画像默认 | 通过 |
| 验收4/任务6：背景生命周期与公开复用 | 同消息恢复删除背景只重算个人四节点；正常恢复冻结版本；最终交付竞争及持续变更有限修复；整条预算与overridden排除 | 通过 |
| 验收5/任务4：私有原文不进公网与必要复核 | 查询从公开岗位锚点编译，背景提供者不进入search/read参数；原始证据独立复核必要门、综合推断闭锁、最多一轮修复 | 通过 |
| 任务5：37需求与禁止写回 | 已有/待提升/待确认三类下资料与项目最小需求均有原岗位依据；无画像事实写接口 | 通过 |
| 不变量：账户、模式、附件/知识库分域、历史和持久生命周期 | 共享内核/守卫与现有持久投影；相关回归及全量基线对账；无表/列迁移，既有运行配置和消息/产物路径覆盖导出、删除与恢复 | 通过 |

## 修复后的版本

配方 `career-job-sample-recipe-v3`；能力 parse-v4、plan-v2、collect-v2、filter-v3、analyze-v4、background-v2、gap-v2、advise-v3、verify-v3。改变的语义升级版本，避免原交付收据误复用。

API/OpenAPI合同仍为本票新增个人投影字段，`openapi.json`/`generated.ts`同步检查通过。背景采用上限8条/8000字符，整条不截断；未实际采用的正文、时间和被覆盖背景不影响建议。新增独立复核模块和回归文件；不新增数据库迁移或画像事实写入口，冻结快照复用既有配置。

## 验证结果

开发/验证使用 Windows、conda `agent` 的 Python 3.11.15；正式冻结复验先用conda hook激活，解释器为 `C:/Users/33755/anaconda3/envs/agent/python.exe`。首次普通沙箱启动失败，用户已授权范围内以沙箱外执行；UTF-8处理避免conda GBK输出错误，临时目录隔离避免并行文件占用。

- 原交付聚焦：117 passed。新增回归修复前9 failed，修复后对应全部通过。
- 关联组合 `tests/career_plan tests/kernel tests/routing` + OpenAPI同步 + Issue12执行守卫、Issue15任务材料/模块验收：273 passed。之后任务依据引用签名最后修复，全部职业测试151 passed。
- 规范：职业领域及新测试Ruff通过，`git diff --check`通过；API既有导入风格诊断如上。`mypy src --no-incremental` 分支/main均100 errors / 20 files，按文件与诊断内容去行号逐条一致，新增/消失均0。
- 验收期间 main 前进：验收开始时固定点、本地 main 与 `origin/main` 同为 `809f05a5`；提交验收修复 `b3c4402c` 后，并行会话已把工单32合入 main（`f2b50516`，`origin/main` 一致）。随后把 `f2b50516` 合入本票分支（合并提交 `0894f5fa`），合并无冲突，`openapi.json` 自动合并且 OpenAPI 同步检查通过。
- 全量对账（口径：`python -m pytest tests -q -n 4 --ignore=tests/integration/test_runtime_smoke.py`，子进程 `PYTHONPATH=src`；烟测单独有界运行）：
  - 首次分支全量 `branch-full.xml`：5133 passed / 212 failed / 39 skipped；唯一分支独有失败为旧测试替身（加载了修复过程中的中间态，整数撤回版本不满足字符串合同），已随替身修复消失。
  - 冻结全量 `branch-frozen-full.xml`：5134 passed / 212 failed / 39 skipped；对账原 main 基线 `main-full.xml`（Desktop 检出，5084/211/39）共享 211 项、无 main 独有失败；唯一分支独有失败 `tests.chat.test_terminal_recovery_and_replay::test_lost_worker_recovers_within_cap_then_converges_with_partial_content`（StorageError「数据库当前不可写」）在 main 与首次分支全量均通过，冻结工作树隔离复跑 1 passed（17.9s），判定为全量并发下的存储瞬时波动，不计工单通过项；冻结前生成的 19 个文件 SHA-256 清单（`frozen-manifest.json`）与运行前工作树逐一一致，冻结结果对本代码有效。
  - 同口径复核发现：原 main 基线与冻结运行均未给 CLI 子进程设置 `PYTHONPATH=src`，共享失败中有 11 项为子进程 `No module named 'bridges'` 环境失败，非代码失败。
  - 同口径重跑：main `main-final-full.xml`（`f2b50516`，含工单32）**5131 passed / 200 failed / 39 skipped**（5370 用例）；分支 `branch-final-full.xml`（`b3c4402c`，旧固定点+本票）**5146 passed / 200 failed / 39 skipped**（5385 用例）；两者共享 200 项失败，0 分支独有、0 main 独有、0 状态差异。
  - 合入工单32后的合并树 `branch-merged-full.xml`（`0894f5fa`）：**5182 passed / 200 failed / 39 skipped**（5421 用例）；对账 `main-final-full.xml`：共享 200 项失败，0 分支独有、0 main 独有、0 状态差异，本票净增 51 个用例全部通过。聚焦组合（职业 151 ＋ OpenAPI 同步 ＋ 工单32 相关）238 passed。
  - 结论：本票相对 `809f05a5` 与 `f2b50516` 两个 main 状态均无新增失败；200 项失败全部为 main 既有，不称为通过。
- 烟测（受限环境）：main 与分支均 **10 passed / 2 failed**，失败 ID 相同（`test_start_fails_with_empty_global_key_before_spawning`、`test_start_fails_when_global_key_env_absent`）：desktop profile 触发 `npm run build`，本机未安装 Next.js：main 检出报 `'next' is not recognized`，分支检出在 15 秒有界超时内未结束；属工作树路径/前端构建环境限制，不是本票代码失败，也不声称烟测通过。

## 剩余限制

确定性页面/画像替身证明机制与错误边界，不能证明真实招聘来源可得性、全部自然语言语义正确率或真实模型个性化体验；按工单留给41/42。证据解析使用已登记词/可明确识别的断言，不明确时保持待确认；新的综合能力判断缺少独立复核来源时阻塞。资料/项目实际调度归37，本票交付最小需求。

全量既有失败不得称为通过，最终会保留全部共有失败ID与烟测限制。测试通过数与历史设计记录不代替本票逐项证据。

## 合并、推送与清理

合并前已复核：验收开始时固定点与 `origin/main` 一致；验收期间 main 前进至 `f2b50516`（工单32），已先合入本分支并完成合并树全量复核（见上）。其他工作树、分支及未跟踪文件均保留。main 合并、推送与清理结果在完成后追加。

### 执行完成（2026-10-04）

- 验收修复提交 `b3c4402c`，同口径对账补充 `27aac2a6`；main 合并提交 `aea34b43`（`merge: 合入工单29个人职业差距依据化验收修复`，父提交为含工单32的 `f2b50516`）。合并无冲突，`openapi.json` 自动合并。
- 合并后在最前 main 复跑聚焦组合（职业 151 ＋ OpenAPI 同步 ＋ 工单32 相关）**238 passed**；`git diff` 核对合并树与本票分支 HEAD 树内容一致。
- 推送：`git -c http.proxy=http://127.0.0.1:7890 push origin main`（`f2b50516..aea34b43`）；`git ls-remote` 核对 `main == origin/main == aea34b43af73167044d7486ce6d3366c2acfc393`。
- 确认 Issue 工作树无未提交改动后删除 `.worktrees/29-evidence-based-personal-career-gap` 与分支 `codex/29-evidence-based-personal-career-gap`（原 `27aac2a6`）；`git worktree prune` 无额外失效记录。分支侧原始日志已复制到主工作区 `.scratch/2/validation/29-review/*.log`（未跟踪保留）；提交的 XML/JSON 证据已随合并进入 main。
- 保留：工单33工作树与分支、主工作区未跟踪验证文件（`24-*`、`27-review/main-*`、`25-main-baseline.xml`、`29-review/main-*`、`32-review/`）。Issue 29 之外的内容未触碰。
