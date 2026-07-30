---
name: t036-completed
description: T036 完成显式共享项目与最小项目副本（含代码审查修复）
metadata:
  type: project
---

# T036 完成：显式共享项目与最小项目副本

## 工作内容

- **合同扩展：** 在 `contracts/sharing.py` 中补全 `ProjectObjectRef`、`SharePreviewRequest`，明确共享对象引用与预览请求形状。
- **保险库域修正：** 为 `VaultObjectCreateRequest` 增加 `domain` 字段，`InMemoryVaultRepository` 按请求域创建对象，`VaultService.share_as_project_copy` 创建 `SHARED_PROJECT` 域副本，确保项目副本与个人原件在对象域上分离。
- **角色扩展：** 在 `ProjectRole` 中增加 `COMMENTER`，使共享项目角色集与决策矩阵一致。
- **共享服务实现：** `SharingService` 负责：
  - 创建显式共享项目并维护项目成员关系。
  - 生成分享预览，列出包含/排除字段、所有者、用途、期限与独立性说明。
  - 执行分享，通过 `VaultService` 创建最小项目副本，并发放对象级 `ObjectGrant`。
  - 按角色基线校验对象授权：权限不能超出角色最大范围。
  - 创建短时、单用、绑定项目与预期身份的邀请令牌；接受令牌后加入项目。
  - 按"角色 + 对象授权"共同判定共享对象访问权限；支持撤销授权。
- **API 路由：** 新增 `/sharing/*` 路由，覆盖共享项目创建/列表/获取、分享预览/执行、邀请创建/接受、共享对象读取、成员列表与授权撤销。
- **应用装配：** 在 `create_app` 中装配 `SharingService` 并注册 `sharing.router`。

## 代码审查修复

代码审查发现以下问题并已修复：

1. **项目所有者无法读取共享对象（关键 bug）：** `check_object_permission` 要求所有用户包括项目所有者都需要显式对象授权。修复：项目所有者（OWNER 角色）对项目内所有对象拥有隐式权限，无需显式 Grant。非所有者角色（EDITOR/REVIEWER/COMMENTER/VIEWER）仍按"角色基线 + 对象授权"联合判定。
2. **分享预览缺少接收者和权限显示：** `SharePreview` 缺少 `recipient_account_id` 和 `permissions` 字段。修复：添加到模型并通过 `preview_share` 回传。
3. **分享执行结果缺少期限回显：** `ShareExecuteResult` 缺少 `expires_at` 字段。修复：添加到模型并通过 `execute_share` 回传。

## 阻塞项验证

- T007（多用户基础作用域隔离）：已复用 `ScopeEnforcer`、`VaultService` 的作用域检查，跨账户/跨项目访问被拒绝。
- T020（画像确认、冻结、删除、导出与回滚）：个人保险库对象仍归个人所有，共享副本不携带画像/学习记录等私人元数据；预览明确排除这些字段。

## 关键实现位置

- `src/science_companion/contracts/sharing.py`
- `src/science_companion/contracts/vault.py`
- `src/science_companion/contracts/projects.py`
- `src/science_companion/sharing/service.py`
- `src/science_companion/api/sharing.py`
- `src/science_companion/api/main.py`
- `src/science_companion/vault/service.py`
- `src/science_companion/vault/adapters.py`
- `tests/sharing/test_sharing_service.py`
- `tests/integration/test_sharing_api.py`

## 测试结果

- 新增测试：`27 passed`（含 2 个审查修复新增测试）
- 全量测试：`755 passed, 2 errors`
- 剩余 2 个 error 为 `tests/integration/test_runtime_smoke.py` 的子进程健康检查超时，系 Windows 环境下 `httpx` 访问本机子进程服务的已知/环境问题，与本次实现无关。

## 后续衔接

- T037（机构管理域与管理员边界）可在共享项目成员/角色模型上扩展机构租户与管理员边界。
- T039（因果同步、冲突分支、撤权和删除墓碑）可复用 `ObjectGrant` 撤销与项目副本的独立对象域进行离线/冲突处理。
