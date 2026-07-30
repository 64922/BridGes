---
name: t038-completed
description: T038 配对个人保险库并保存加密本地对象
metadata:
  type: project
---

# T038 完成：配对个人保险库并保存加密本地对象

## 工作内容

- **新增/扩展合同：** 在 `contracts/vault.py` 中新增：
  - `DeviceCertificate`：绑定账户、设备、公钥、指纹与密钥时期的设备证书。
  - `KeyEpoch`：设备密钥时期，撤销设备时轮换。
  - `VaultRuntime`：设备侧保险库运行时投影。
  - `DevicePairingRequest` / `DevicePairingResponse` / `DeviceRevocationRequest`：配对与撤销流程。
  - `VaultLocalEncryptedObject`：加密本地对象元数据。
  - `DevicePairingStatus` / `DeviceType` 枚举；现有枚举迁移至 `StrEnum`。
  - 为 `VaultObjectCreateRequest` 增加可选的 `content_hash` 与 `content_length` 覆盖，使云投影可在本地加密后仍记录明文哈希。

- **新增端口：** 在 `vault/ports.py` 中新增：
  - `DeviceKeychainPort`：设备私钥与包装密钥的系统密钥库存储端口。
  - `VaultEncryptionPort`：对称加密与密钥包装端口。
  - `DevicePairingRepository`：设备证书与密钥时期持久化端口。
  - 在 `VaultRepository` 上增加 `store_device_local_wrapped_key` / `get_device_local_wrapped_key` 默认方法。

- **新增适配器：** 在 `vault/adapters.py` 中新增：
  - `InMemoryDeviceKeychain`：内存版系统密钥库适配器，绝不把私钥写入普通配置或日志。
  - `FernetVaultEncryptionAdapter`：基于 Fernet 的数据加密与密钥包装实现。
  - `InMemoryDevicePairingRepository`：内存版证书与密钥时期仓库。
  - `DevicePairingService`：配对、撤销与密钥时期轮换服务。

- **扩展 VaultService：** 在 `vault/service.py` 中：
  - 注入可选的 `device_keychain`、`vault_encryption`、`device_pairing_repository`。
  - 新增 `pair_device`、`revoke_device`、`list_device_certificates`。
  - `create_private_object` 在 `DEVICE_LOCAL` 且设备已配对时：生成数据密钥、加密正文、用设备包装密钥包装数据密钥、存储密文与包装密钥、云投影仅保留明文哈希/长度/密钥时期。
  - `get_content` 在 `DEVICE_LOCAL` 时通过设备密钥链解密，返回明文权威；设备缺失或撤销时返回 `DeviceUnavailableState`。

- **新增 API 路由：** 在 `api/vault.py` 中新增：
  - `POST /vault/devices/pair`：配对设备。
  - `GET /vault/devices`：列出当前账户已配对设备。
  - `POST /vault/devices/{device_id}/revoke`：撤销设备并轮换密钥时期。
  - 为 `POST /vault/objects` 增加错误捕获，设备未配对时返回 422。

- **应用装配：** 在 `api/main.py` 中创建 `InMemoryDeviceKeychain`、`FernetVaultEncryptionAdapter`、`InMemoryDevicePairingRepository` 并注入 `VaultService`。

- **更新 OpenAPI：** 重新生成 `openapi.json`，包含新增配对路由与合同。

- **测试：**
  - `tests/vault/test_vault_pairing.py`：9 个单元测试，覆盖配对证书、私钥不落库、设备撤销轮换、加密本地对象、设备明文权威、最小任务胶囊、跨账户隔离。
  - `tests/integration/test_vault_pairing_api.py`：7 个 API 集成测试，覆盖认证要求、配对/列表/撤销、加密本地对象、未配对拒绝、胶囊最小上下文。
  - 更新 `tests/integration/test_vault_api.py`：在创建 `device_local` 对象前先调用设备配对，适配 T038 新行为。

## 阻塞项验证

- T020（画像确认、冻结、删除、导出与回滚）：个人保险库对象归属仍严格限定账户；T038 未改变画像/记忆治理语义。
- T008（四种生产运行合同骨架）：新增加密端口与配对服务不依赖 Conda；CLI 与四种运行合同无变更。

## 关键实现位置

- `src/science_companion/contracts/vault.py`
- `src/science_companion/vault/ports.py`
- `src/science_companion/vault/adapters.py`
- `src/science_companion/vault/service.py`
- `src/science_companion/vault/__init__.py`
- `src/science_companion/api/vault.py`
- `src/science_companion/api/main.py`
- `pyproject.toml`
- `openapi.json`
- `tests/vault/test_vault_pairing.py`
- `tests/integration/test_vault_pairing_api.py`
- `tests/integration/test_vault_api.py`

## 测试结果

- 新增测试：16 passed（9 个服务单测 + 7 个 API 集成测试）
- 受影响回归测试：17 passed
- 全量测试：797 passed
- 类型检查：针对 T038 改动相关文件全部通过
- Ruff：T038 改动相关文件全部通过

## 代码审查修复

- **修复 revoke_capsule 授权顺序：** 在 `vault/service.py` 中将作用域权限检查移到仓库状态变更之前。原实现在 `VaultService.revoke_capsule` 中先调用 `repository.revoke_capsule()` 执行撤销操作，再检查作用域权限，违反了"先授权后执行"的安全原则。修复方案：新增 `VaultRepository.get_capsule()` 只读端口方法，在授权通过后才执行实际撤销。
- **新增端口方法：** 在 `VaultRepository` 端口上增加 `get_capsule(owner_id, capsule_id) -> TemporaryTaskCapsule`，实现于 `InMemoryVaultRepository`，支持只读胶囊查询而不改变状态。

## 后续衔接

- T039（因果同步、冲突分支、撤权和删除墓碑）可复用设备撤销、密钥时期轮换与已加密的设备本地对象元数据。
- 真实生产实现需将 `InMemoryDeviceKeychain` 替换为 OS 原生密钥库（Windows DPAPI / macOS Keychain / Linux Secret Service）实现。
