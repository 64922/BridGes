"""GQ-07 历史账户 Qwen 秘密清退测试（US-04）。

覆盖：两账户夹具证明只清理 Qwen 命名空间（SMTP 授权码不受影响）、
持久化完成标记与幂等重复执行、中途失败不写标记且下次可继续、无凭据
后端环境安全跳过、状态命名空间（key_metadata/key_probes）清理。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from bridges.credentials.retire import (
    QwenKeyRetirementError,
    is_qwen_key_retirement_completed,
    run_qwen_key_retirement,
)
from bridges.credentials.store import (
    CredentialStoreError,
    InMemoryCredentialStore,
    has_credential_backend,
)
from bridges.persistence import SqliteStateStore

QWEN_CANARY = "sk-legacy-qwen-1234567890abcdef"
SMTP_CANARY = "qqsmtp-canary-abcdef123456"


class _Harness:
    """两账户 + 账户命名空间/SMTP 命名空间秘密 + 旧状态命名空间的沙箱。"""

    def __init__(self, tmp_path: Path) -> None:
        self.state = SqliteStateStore(tmp_path / "bridges.db", encryption_key="test-key")
        self.accounts = InMemoryCredentialStore()
        self.smtp = InMemoryCredentialStore(namespace="smtp")
        self.acc1, self.acc2 = "acc-1001", "acc-1002"
        # 身份状态：现有账户注册记录（清退的账户枚举来源）。
        self.state.save("identity", {"accounts": {self.acc1: {}, self.acc2: {}}})
        # 旧账户 Qwen 秘密（account 命名空间）。
        self.accounts.save(self.acc1, SecretStr(QWEN_CANARY))
        self.accounts.save(self.acc2, SecretStr(QWEN_CANARY + "-b"))
        # SMTP 授权码（smtp 命名空间，必须保留）。
        self.smtp.save(self.acc1, SecretStr(SMTP_CANARY))
        self.smtp.save(self.acc2, SecretStr(SMTP_CANARY + "-b"))
        # 旧 Key 元数据与能力探测快照。
        self.state.save("key_metadata", {"accounts": {self.acc1: {"operation": "save"}}})
        self.state.save(
            "key_probes", {"records": {self.acc1: {"chat": {"status": "available"}}}}
        )


def _run(h: _Harness, credential_store=None) -> bool:
    return run_qwen_key_retirement(
        state_store=h.state, credential_store=credential_store
    )


def test_retirement_clears_qwen_namespace_only(
    tmp_path: Path,
) -> None:
    """只清理 Qwen 命名空间秘密与旧状态；SMTP 授权码与提醒不受影响。"""
    h = _Harness(tmp_path)
    assert _run(h, h.accounts) is True
    # Qwen 命名空间秘密全部删除；SMTP 命名空间原样保留。
    assert h.accounts.get(h.acc1) is None
    assert h.accounts.get(h.acc2) is None
    assert h.smtp.get(h.acc1) == SecretStr(SMTP_CANARY)
    assert h.smtp.get(h.acc2) == SecretStr(SMTP_CANARY + "-b")
    # Key 元数据与能力探测快照命名空间已删除。
    assert h.state.load("key_metadata") is None
    assert h.state.load("key_probes") is None
    # 持久化完成标记已写入。
    assert is_qwen_key_retirement_completed(h.state)


def test_retirement_is_idempotent_and_marked(
    tmp_path: Path,
) -> None:
    """完成标记写入后重复执行安全跳过（幂等）。"""
    h = _Harness(tmp_path)
    assert _run(h, h.accounts) is True
    assert _run(h, h.accounts) is False
    assert is_qwen_key_retirement_completed(h.state)
    # 跳过执行不触碰任何状态。
    assert h.accounts.get(h.acc1) is None
    assert h.smtp.get(h.acc1) == SecretStr(SMTP_CANARY)


class _FailingStore(InMemoryCredentialStore):
    """第二个账户删除时模拟旧凭据存储不可用。"""

    def delete(self, account_id: str) -> None:
        if account_id == "acc-1002":
            raise CredentialStoreError("操作系统凭据库读取失败（模拟）")
        super().delete(account_id)


def test_midway_failure_raises_without_marker(
    tmp_path: Path,
) -> None:
    """中途失败抛中文错误、不写完成标记，下次启动继续（不误记完成）。"""
    h = _Harness(tmp_path)
    failing = _FailingStore()
    failing.save(h.acc1, SecretStr(QWEN_CANARY))
    failing.save(h.acc2, SecretStr(QWEN_CANARY + "-b"))
    with pytest.raises(QwenKeyRetirementError) as excinfo:
        _run(h, failing)
    message = str(excinfo.value)
    assert "无法访问旧凭据存储" in message
    assert QWEN_CANARY not in message  # 错误消息不含任何秘密正文
    assert not is_qwen_key_retirement_completed(h.state)
    # 已删除账户不回滚，但未完成标记保证重试继续清掉剩余账户。
    assert failing.get(h.acc1) is None
    assert failing.get(h.acc2) is not None
    # 再次执行（存储恢复）收敛：harness 完整存储清掉全部秘密并标记。
    assert _run(h, h.accounts) is True
    assert h.accounts.get(h.acc2) is None
    assert is_qwen_key_retirement_completed(h.state)


class _FailingVolumeStore(InMemoryCredentialStore):
    """删除时模拟加密凭据卷文件系统拒绝（权限/只读）。"""

    def delete(self, account_id: str) -> None:
        raise PermissionError("拒绝访问凭据卷密文文件")


def test_filesystem_failure_is_wrapped_without_secret(
    tmp_path: Path,
) -> None:
    """文件系统级删除失败同样包装为不含秘密的中文错误且不写标记。"""
    h = _Harness(tmp_path)
    with pytest.raises(QwenKeyRetirementError) as excinfo:
        _run(h, _FailingVolumeStore())
    message = str(excinfo.value)
    assert "无法访问旧凭据存储" in message
    assert QWEN_CANARY not in message
    assert not is_qwen_key_retirement_completed(h.state)


def test_no_credential_backend_skips_secrets_but_cleans_state(
    tmp_path: Path,
) -> None:
    """无凭据后端（credential_store=None）安全跳过秘密删除并标记完成。"""
    h = _Harness(tmp_path)
    # 模拟无后端：不传凭据存储，只清理状态命名空间并标记完成。
    assert _run(h, None) is True
    assert is_qwen_key_retirement_completed(h.state)
    assert h.state.load("key_metadata") is None
    assert h.state.load("key_probes") is None


def test_empty_identity_accounts_still_clean_state(
    tmp_path: Path,
) -> None:
    """身份账户列表缺失时清退自然收敛（空集 + 状态清理 + 标记）。"""
    h = _Harness(tmp_path)
    h.state.save("identity", {})
    assert _run(h, h.accounts) is True
    assert is_qwen_key_retirement_completed(h.state)
    assert h.state.load("key_metadata") is None
    assert h.state.load("key_probes") is None


def test_leftover_account_from_interrupted_deletion_is_covered(
    tmp_path: Path,
) -> None:
    """key_metadata 残留账户（identity 已无记录）的秘密同样被清退。"""
    h = _Harness(tmp_path)
    # 模拟账户删除中途失败：identity 已无该账户记录，但 key_metadata
    # 仍保存过密钥元数据、秘密也仍残留在凭据存储中。
    h.state.save("identity", {"accounts": {h.acc1: {}}})
    leftover = "acc-deleted-1003"
    h.state.save("key_metadata", {"accounts": {h.acc1: {}, leftover: {}}})
    h.accounts.save(leftover, SecretStr(QWEN_CANARY + "-leftover"))
    assert _run(h, h.accounts) is True
    assert h.accounts.get(leftover) is None
    assert h.accounts.get(h.acc1) is None
    assert is_qwen_key_retirement_completed(h.state)


def test_no_state_store_is_noop(tmp_path: Path) -> None:
    """无状态存储（内存模式）时清退为无操作。"""
    assert run_qwen_key_retirement(state_store=None, credential_store=None) is False


def test_has_credential_backend_branches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """凭据后端可用性判定：keyring/DPAPI 任一存在即可，全无则跳过。"""
    from bridges.credentials import store as store_module

    monkeypatch.setattr(store_module, "keyring", None)
    monkeypatch.setattr(store_module, "_dpapi_available", lambda: False)
    assert has_credential_backend(tmp_path) is False
    monkeypatch.setattr(store_module, "_dpapi_available", lambda: True)
    assert has_credential_backend(tmp_path) is True
    assert has_credential_backend(None) is False  # DPAPI 兜底需要数据目录
    monkeypatch.setattr(store_module, "keyring", object())
    assert has_credential_backend(None) is True
