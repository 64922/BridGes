---
name: t037-completed
description: T037 完成机构管理域与管理员边界
metadata:
  type: project
---

# T037 完成：机构管理域与管理员边界

## 工作内容

- **新增机构合同：** 创建 `contracts/institution.py`，定义 `Institution`、`Membership`、
  `InstitutionRole`（admin/member/security_admin/billing_admin）、`InstitutionPolicy`、
  `SeatPolicy`、`InstitutionOwnedProjectDisclosure`、`ControlledContentAccessRequest`、
  `ControlledContentAccessCreateRequest` 等关键模型。
- **扩展现有合同：**
  - `contracts/projects.py`：`Project` 增加 `tenant_id`，用于标识机构自有项目。
  - `contracts/identity.py`：`SubjectContext` 增加 `memberships: list[MembershipContext]`，
    让 API 层可将机构关系注入主体。
  - `contracts/scope.py`：`ScopeAction` 增加 `ADMINISTER`。
- **实现 InstitutionService：** 新增 `institution/service.py`（内存实现），负责：
  - 机构创建与列表、成员邀请/接受/移除/角色更新。
  - 机构策略（席位、MFA、恢复密钥、保留）维护。
  - 机构自有项目创建，创建前强制返回并确认所有权/保留/恢复边界披露。
  - 受控正文访问申请、双人批准、撤销、审计日志与自动过期。
  - 始终禁止管理员通过成员关系读取成员个人保险库。
- **扩展 ScopeEnforcer：** 支持 `INSTITUTION_OWNED` 对象域：通过可注入的
  `institution_membership_provider` 与 `project_tenant_provider` 判断调用者是否为机构成员；
  `PERSONAL_VAULT` 仍严格仅本人可访问。
- **扩展 SharingService：** `create_shared_project` 支持 `institution_id` 参数，创建
  `INSTITUTION_OWNED` 域项目；新增 `get_project_institution_id` 与
  `list_projects_by_institution` 供作用域与机构服务使用。
- **新增机构 API 路由：** 创建 `api/institution.py`，覆盖：
  - `/institutions` 创建/列出机构
  - `/institutions/{id}/invites` 邀请、`/institutions/invites/accept` 接受
  - `/institutions/{id}/members` 成员列表、`/institutions/{id}/members/{account_id}` 角色更新/移除
  - `/institutions/{id}/policy` 策略查看/更新
  - `/institutions/{id}/project-disclosure` 项目披露
  - `/institutions/{id}/projects` 机构项目创建/列表
  - `/institutions/{id}/content-access` 受控正文访问申请/批准/列表
- **应用装配：** 在 `create_app` 中创建 `InstitutionService`，绑定 `SharingService`，并将机构
  provider 注入 `ScopeEnforcer`；注册 `institution.router`。
- **认证依赖增强：** `api/auth.py` 的 `require_subject` 在解析会话后从
  `institution_service` 填充 `SubjectContext.memberships`。
- **OpenAPI 同步：** 重新生成 `openapi.json`，使其包含新增机构路由的合同。
- **修复 runtime smoke test：** 将 `tests/integration/test_runtime_smoke.py` 中子进程健康检查
  从 `httpx` 改为 `urllib.request`，避免 Windows 环境下 `httpx` 访问本机子进程服务超时的问题。

## 阻塞项验证

- T036（显式共享项目与最小项目副本）：已复用 `SharingService` 的项目/成员/邀请/对象授权模型；
  机构项目作为 `INSTITUTION_OWNED` 域的共享项目实现，个人保险库对象不会被自动共享。

## 关键实现位置

- `src/science_companion/contracts/institution.py`
- `src/science_companion/contracts/projects.py`
- `src/science_companion/contracts/identity.py`
- `src/science_companion/contracts/scope.py`
- `src/science_companion/institution/service.py`
- `src/science_companion/institution/__init__.py`
- `src/science_companion/scope/service.py`
- `src/science_companion/sharing/service.py`
- `src/science_companion/api/institution.py`
- `src/science_companion/api/auth.py`
- `src/science_companion/api/main.py`
- `src/science_companion/contracts/__init__.py`
- `openapi.json`
- `tests/institution/test_institution_service.py`
- `tests/integration/test_institution_api.py`
- `tests/integration/conftest.py`
- `tests/integration/test_runtime_smoke.py`

## 测试结果

- 新增测试：21 passed（14 个服务单测 + 7 个 API 集成测试）
- 受影响回归测试：74 passed
- 全量测试：778 passed
- 类型检查：针对 T037 改动相关文件全部通过

## 后续衔接

- T038（配对个人保险库并保存加密本地对象）可在机构策略中进一步集成设备合规与恢复密钥。
- T039（因果同步、冲突分支、撤权和删除墓碑）可复用机构成员移除与项目密钥时期轮换。
