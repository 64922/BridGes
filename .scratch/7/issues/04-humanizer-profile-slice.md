# Issue 04：人味化轮次注入已授权画像切片

Status: resolved

Type: task

Priority: P1

User stories: US-02

## 已验证现状与根因

- 用户明确要求人味化请求「查询用户画像」。代码级事实：`_stream_humanizer`（`src/bridges/chat/turn.py:3869-4344`）没有 `use_profile` 参数，从不调用 `_compile_profile_slice`（`turn.py:5483-5684`），不写「本次上下文说明」披露。
- 对照：普通生成主路径在 `turn.py:3052-3063` 编译画像切片并经 `assemble_payload`（`turn.py:3086-3096`）注入；生涯规划路径在 `turn.py:4613-4693` 编译画像并把 `profile_enabled/profile_used/profile_items` 传给生涯编排（截图中生涯那次的「已使用授权用户背景信息」即来源于此，披露文案 `turn.py:1385-1411`）。
- 论文搜索刻意排除画像（`turn.py:3043-3050`），本轮冻结决策 #3 维持该边界不变，本 issue 不涉及。
- 已冻结决策（本轮 #3）：人味化轮次编译最小画像切片、注入 prompt、如实披露；`use_profile=False` 时不编译。

### 上下文指针

- `src/bridges/chat/turn.py:3869-4344`：人味化流式分支；`turn.py:5483-5684`：画像切片编译实现与服务优先级；`turn.py:1385-1411`：披露文案。
- `src/bridges/skills/humanizer/service.py`：`run_task` 输入合同，画像切片的传递接缝。
- `tests/humanizer/`：人味化服务与锁测试。

## What to build

1. 在人味化流式分支进入技能执行前编译最小画像切片（与普通路径同一编译接缝与裁剪规则），经既有 Protocol 接缝传入人味化任务输入；首稿/修订 prompt 以「风格与背景偏好」用途引用，不改变人味化的证据与来源合同。
2. 披露：人味化消息写入与普通/生涯路径一致的「本次上下文说明」（使用条数与授权说明），前端直接复用现有披露 UI。
3. `use_profile=False`、无画像、画像服务不可用三种情况分别保持：不编译不披露、正常兜底、准确降级，均不阻塞人味化主流程。
4. 画像内容不得进入运行锁、日志或人味化语料产物；披露只含条数与类别，不含画像原文。

## 非目标

- 不改变论文搜索的公开证据边界（不注入画像）。
- 不修改画像抽取/编译算法本身，不新增画像维度。
- 不改变人味化首稿/修订/质量检查的模型调用结构（时延问题由 Issue 06 处理）。
- 不为学习模式处理画像（Issue 02 已覆盖）。

## Acceptance criteria

- [ ] 人味化轮次 prompt 组装包含最小画像切片，消息带「已使用授权用户背景信息」类披露，刷新后一致。
- [ ] `use_profile=False` 时人味化轮次零画像编译、零披露，主流程正常。
- [ ] 无画像用户的人味化轮次正常完成且无披露；画像服务异常时人味化准确降级而非失败。
- [ ] 运行锁、日志、SSE 事件与人味化产物中不出现画像原文。

## Test plan

1. 人味化聊天级测试：有画像/无画像/`use_profile=False`/画像服务异常四种矩阵，断言注入、披露与主流程结果。
2. 断言披露条数与编译结果一致；断言产物无画像原文（扫描式断言）。
3. 既有 `tests/humanizer/` 全量回归。

建议回归命令：

```powershell
python -m pytest tests/humanizer tests/chat -k humanizer -q -p no:cacheprovider --basetemp=$env:TEMP\bridges-r7-issue04
```

## Observability & rollback

- 审计记录人味化轮次 `profile_used` 与条数（不含内容）；不变量告警：产物中出现画像原文。
- 回滚：移除注入与披露即恢复现状；无持久化合同变更，可安全回退。

## Blocked by

无。

## Comments

- 2026-08-14：本轮冻结决策 #3——人味化注入画像，论文搜索维持公开证据边界。
- 2026-08-14（实现）：`_stream_humanizer` 在进入技能执行前经 `_compile_profile_slice`
  编译最小画像切片并落库「本次上下文说明」披露；切片上下文经
  `HumanizerOrchestrator.run_task` Protocol 接缝传入（`profile_used` /
  `profile_items` / `profile_context`），首稿/修订/旧显式 SKILL 路径以
  「风格与背景偏好」用途注入 prompt，不进入证据合同；`use_profile=False`
  零编译零画像披露，无画像/画像服务异常分别 empty/error 态降级，均不
  阻塞主流程；审计只记 `profile_used` 与条数。新增
  `tests/chat/test_humanizer_profile_slice.py` 四矩阵 + 扫描式不泄漏断言。
