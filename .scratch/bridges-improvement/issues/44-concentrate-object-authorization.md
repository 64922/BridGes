# Issue 44 — 对象授权收拢：同一问题只问一个 module

Status: ready-for-agent
Type: task
来源：架构评审候选 3（Strong）。评审报告：architecture-review-20260806-231807.html
词汇：module / interface / depth / seam / adapter / leverage / locality

## 问题（当前状态，含证据）

「谁能读这个对象」有 5 条判据副本 + 一条完全绕行的路径：

| 位置 | 现状 | 证据 |
|---|---|---|
| `scope/service.py` | 权威实现：`ScopeEnforcer.authorize()` (103) / `authorize_vault()` (187) | ✅ 单一 |
| `projects/service.py` | 先 `project.account_id != account_id` 预过滤，再调 authorize | :133 |
| `media/service.py` | `_authorize_asset()` 先 `account_id != asset.account_id` 再调 authorize（重复两次） | :121 |
| `sharing/service.py` | `_require_member()` 自建 `self._members.get((project_id, account_id))` 字典查询，**绕过** ScopeEnforcer | :122-127 |
| `workflows/service.py` | `_require_record()` 先 `record.context.account_id != account_id` 再单独 authorize 项目级 ObjectRef | :199-213 |
| `mcp/service.py` | **完全绕过**：只靠 `BridgesDatabase.scoped(account_id)` 的 SQL 层隔离（:206/:300/:316） | 旁路 |

- 删除测试（对预过滤）：删掉预过滤不会丢任何行为——`authorize()` 内部
  已含 `subject.account_id != owner_id` 检查。它们是浅层重复。
- 另有 9 个 service 各自复制 6 行伪造 `SubjectContext(auth_method=PASSWORD,
  session_id="service-session"/"media-service"/"sharing-service"/"invalidation-service")`
  （vault:87-95、media:110-115、sharing:113-120、workflows:114-122、projects:87-95、
  profiles:178-183、science:124-129、science/claims.py:177、invalidation:442-446），
  4 种不同 session_id 语义等价。

## 方案

### 1. 删除预过滤（纯删，不迁移逻辑）

- `projects/service.py:133`、`media/service.py:121`、`workflows/service.py:199` 的
  `account_id !=` 前置检查删除，直接调 `authorize()`。
- 逐一跑对应测试确认：授权失败现在由 `ScopeIsolationError` 抛出而非自定义错误。
  **注意错误码/HTTP 映射**：跨账户读取现在必须仍返回安全 404/403 而非 500，
  检查 API 层对 `ScopeIsolationError` 的映射（`api/*.py` 的异常处理）。
- 若个别预过滤带有预过滤独有语义（如「只查本人」的快速路径），保留并注释
  原因——删除测试不成立时才保留。

### 2. sharing 成员检查走同一 seam

- `_require_member()`（sharing/service.py:122-127）改为调用 ScopeEnforcer 的
  共享项目授权（`authorize(object_ref=SHARED_PROJECT)` 或新增
  `authorize_membership(project_id, account_id)` 方法——先核对
  `ScopeEnforcer` 内共享项目的成员判定实现，两者语义必须等价再替换）。
- 若 `_members` 字典是内存缓存的加速层（非权威），保留缓存、删除判据副本；
  若它就是权威，把权威移入 scope。

### 3. MCP 接入同一 seam

- `mcp/service.py` 的读取入口（:206/:300/:316 附近，`scoped(account_id)` 查询前）
  加 `ScopeEnforcer.authorize()` 调用：MCP 服务的对象访问与 vault/media/workflows
  回答同一问题。
- 保持 SQL 层 `scoped(account_id)` 作为纵深防御（不删除），但不再作为唯一机制。
- 新增测试：跨账户调用某 MCP 的对象参数 → 授权拒绝（而非仅空结果）。

### 4. `SubjectContext` 工厂化

- `scope/` 内提供单一构造点：`ScopeEnforcer.subject_for_service(service_name)`（或
  `scope/subject.py` 的 `service_subject(name)`）。
- 9 处复制替换为一行调用；删除 4 种自造 session_id，统一为 `"service"` 前缀 + 服务名。
- 用途注释保留：服务内部特权上下文的语义不变。

### 验收标准

- [ ] `grep -rn "account_id != .*\.account_id\|account_id != owner" src/bridges/`
      仅剩 scope/ 内部判定（若有残余需说明理由）
- [ ] `grep -rn "AuthMethod.PASSWORD, session_id=" src/bridges/` 归零
      （全部走 `subject_for_service`）
- [ ] mcp 测试新增跨账户拒绝用例；sharing/workflows/media/projects 测试全绿
- [ ] 全量 pytest 无回归；ruff 干净
- [ ] 安全语义不变：跨账户一律安全 404/403，不泄漏存在性

### 风险与开放问题

- 最大风险在 sharing：`_require_member` 的成员语义若与 `ScopeEnforcer` 共享项目
  判定不完全等价（如角色级别），替换前必须逐条对拍测试；不等价则先在
  scope 补齐成员判定再替换。
- MCP 的 SQL 隔离是既有防线，接入 enforcer 后可能改变部分调用方的失败形态
  （空结果 → 授权异常），需在 API 层映射为安全错误，别变 500。
- 不新增 ADR：本方案与 ADR-0010（MCP 权限清单）、ADR-0018（账户隔离）一致，
  只是收敛实现。

### 实施记录（2026-08-07）

**实现偏离（评审确认合理，记档）：**

1. **media/science 项目源/资产域编码修正**：`_object_ref_for_asset` /
   `_source_ref` 由 `SHARED_PROJECT + owner=project_id` 修正为
   `PERSONAL_VAULT + owner=account_id`。依据：`projects/service.py` 的
   科学项目空间是 `PERSONAL_VAULT` 域，`SHARED_PROJECT` 语义是「显式共享
   决定」（contracts/projects.py）；旧编码在 enforcer 的 SHARED_PROJECT
   分支原为 `pass` 形同虚设，且与删除预过滤后「本人可读」语义矛盾。
   同步更新 search.py / claims.py / expression/service.py / media
   publish_service.py 中的手工编码副本与相关测试。
2. **共享项目成员判定 fail closed**：`ScopeEnforcer.authorize()` 的
   SHARED_PROJECT 分支由 `pass` 改为成员判定；provider 由 SharingService
   构造时绑定（`is_member`），未绑定 provider 时一律拒绝。
3. **workflows `_require_record` 恒真判定修正**：ObjectRef 的 owner 从
   `account_id`（调用者，恒真）修正为 `record.context.account_id`
   （运行真正所有者）。
4. **sharing `_require_member` 经 `authorize_membership` 判定**：判据唯一
   在 scope，成员数据仍在 `_members`（判定与数据同源）。

**验收 grep 残余说明理由（非对象授权判据副本）：**

- `api/scope.py:94`、`api/vault.py:71/192`、`profiles/api.py:84/155`：
  API 层请求/会话一致性校验（写入侧防伪造、后台任务主体一致性），
  非对象授权判据。
- `invalidation/service.py:318`：失效事件传播的账户一致性（下游 envelope
  与事件 envelope），非对象授权判据。
- `vault/adapters.py:254`：仓库层 owner 过滤（数据层），非授权判据。
- `scope/service.py:161/251`：scope 内部 RLS 上下文一致性判定（豁免）。
- MCP `list_servers` 保持 SQL `scoped(account_id)` 隔离（无对象参数、
  无存在性泄漏），按 mcp_id 读取的入口统一经 `_require_server` 判定。
