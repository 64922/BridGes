"""历史账户级 Qwen 秘密清退（GQ-07 / US-04）。

GQ-06 已删除账户百炼密钥的用户面与公开合同；本模块是旧账户 Qwen 秘密、
Key 元数据与能力探测快照的唯一清退者，为已有安装提供一次性、幂等的
升级清理：

1. **完成标记**：清退结果持久化在 StateStore ``qwen_key_retirement``
   命名空间。已完成的安装再次启动直接跳过（幂等）；中途失败不写标记，
   下次启动继续，绝不把"未清理"误记为完成。
2. **账户枚举**：账户注册记录是身份域（``identity`` 命名空间）的架构
   资产；清退按现有账户 ID 逐账户删除 Qwen 命名空间（``account``）中
   OS 凭据库 / 加密凭据卷的秘密，并并入 ``key_metadata`` 中保存过密钥的
   账户（覆盖"账户删除中途失败、凭据清理未执行"的残留路径）。QQ SMTP
   命名空间（``smtp``）绝不触碰。
3. **状态清理**：删除 ``key_metadata``（Key 元数据）与 ``key_probes``
   （能力探测快照）两个状态命名空间；新版本不再产生这些事件，历史审计
   记录保留作为旧合同行为证据。
4. **失败语义**：无法访问旧秘密存储时抛出不含秘密正文的中文错误，由
   启动硬门失败关闭，避免留下用户无法再管理的孤儿凭据。
"""

from __future__ import annotations

from datetime import UTC, datetime

from bridges.credentials.store import CredentialStoreError, CredentialStorePort
from bridges.persistence import StateStore

#: 清退完成标记的 StateStore 命名空间。
_RETIREMENT_NAMESPACE = "qwen_key_retirement"
#: 旧账户 Qwen 秘密所在状态命名空间（key_metadata / key_probes）。
_KEY_METADATA_NAMESPACE = "key_metadata"
_KEY_PROBES_NAMESPACE = "key_probes"
#: 账户注册记录所在命名空间（身份域架构资产，只读枚举账户 ID）。
_IDENTITY_NAMESPACE = "identity"


class QwenKeyRetirementError(Exception):
    """历史账户 Qwen 秘密清退失败；消息只含中文处置提示，绝不含秘密正文。"""


def is_qwen_key_retirement_completed(state_store: StateStore | None) -> bool:
    """历史账户 Qwen 秘密是否已完成清退（持久化完成标记）。"""
    if state_store is None:
        return False
    state = state_store.load(_RETIREMENT_NAMESPACE) or {}
    return bool(state.get("completed"))


def run_qwen_key_retirement(
    *,
    state_store: StateStore | None,
    credential_store: CredentialStorePort | None,
) -> bool:
    """执行一次性清退，返回是否实际执行（False=已完成/无可执行内容）。

    - ``credential_store`` 为 None 表示当前环境没有可用的旧凭据后端
      （旧实现同样无法写入秘密），跳过秘密删除，只清理状态命名空间；
    - 任一步失败抛 :class:`QwenKeyRetirementError`（不写完成标记），
      下次启动继续；
    - 清退完成后写入持久化标记，重复执行安全且跳过。
    """
    if state_store is None or is_qwen_key_retirement_completed(state_store):
        return False
    account_ids = _account_ids_from_identity(state_store)
    if credential_store is not None:
        for account_id in account_ids:
            try:
                credential_store.delete(account_id)
            except (CredentialStoreError, OSError) as exc:
                # OSError：加密凭据卷删除被文件系统拒绝（权限/只读等）；
                # 统一包装为不含秘密正文的中文错误。
                raise QwenKeyRetirementError(
                    "历史账户百炼密钥清退失败：无法访问旧凭据存储"
                    f"（{exc}）。请检查系统凭据管理器或数据目录凭据卷的"
                    "可用性与权限后重新启动；BRIDGES_QWEN_API_KEY 不受影响。"
                ) from exc
    for namespace in (_KEY_METADATA_NAMESPACE, _KEY_PROBES_NAMESPACE):
        state_store.delete(namespace)
    state_store.save(
        _RETIREMENT_NAMESPACE,
        {
            "completed": True,
            "completed_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "account_count": len(account_ids),
        },
    )
    return True


def _account_ids_from_identity(state_store: StateStore) -> list[str]:
    """枚举需要清退的账户 ID：identity 注册集 ∪ key_metadata 历史集。

    清退是升级迁移关注点，按既有账户 ID 定位旧命名空间秘密；并入
    ``key_metadata`` 中保存过密钥的账户，覆盖"账户删除中途失败、凭据
    清理未执行"后 identity 已无记录但秘密仍残留的路径。两者皆缺失
    （干净数据目录）时为空集，清退自然收敛。
    """
    account_ids: set[str] = set()
    for namespace in (_IDENTITY_NAMESPACE, _KEY_METADATA_NAMESPACE):
        state = state_store.load(namespace) or {}
        raw = state.get("accounts")
        if isinstance(raw, dict):
            account_ids.update(str(account_id) for account_id in raw)
    return sorted(account_ids)
