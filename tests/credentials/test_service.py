"""KeyCredentialService 门面测试：审计、状态与存储错误边界。"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from bridges.contracts.credentials import ProbeStatus
from bridges.contracts.observability import AuditAction
from bridges.credentials.probes import CapabilityProbeService, ProbeOutcome, ProbeRunnerPort
from bridges.credentials.service import KeyCredentialService
from bridges.credentials.store import CredentialStoreError, InMemoryCredentialStore
from bridges.observability.service import ObservabilityService


class _OkRunner(ProbeRunnerPort):
    def probe(self, binding, client) -> ProbeOutcome:
        return ProbeOutcome(success=True, message="探测成功。")


class _FailingStore(InMemoryCredentialStore):
    """get 时抛存储错误的替身，模拟 keyring 不可用。"""

    def get(self, account_id: str):
        raise CredentialStoreError("操作系统凭据库不可用，请检查系统凭据管理器状态。")


def _service(store=None, *, sync=True) -> KeyCredentialService:
    return KeyCredentialService(
        credential_store=store or InMemoryCredentialStore(),
        probe_service=CapabilityProbeService(runner=_OkRunner()),
        observability_service=ObservabilityService(),
        sync_probes=sync,
    )


def test_save_emits_audit_without_secret() -> None:
    service = _service()
    projection = service.save("alice", SecretStr("sk-audit-1234567890"))
    assert projection.configured is True
    assert projection.key_tail == "…7890"

    events = service._observability.list_audit_events(account_id="alice")
    assert [e.action for e in events] == [
        AuditAction.KEY_SAVE,
        AuditAction.KEY_PROBE,
    ]
    assert all("sk-audit" not in e.model_dump_json() for e in events)


def test_delete_emits_audit_and_resets() -> None:
    service = _service()
    service.save("alice", SecretStr("sk-audit-1234567890"))
    projection = service.delete("alice")
    assert projection.configured is False
    assert all(c.status == ProbeStatus.NOT_PROBED for c in projection.capabilities)

    events = service._observability.list_audit_events(account_id="alice")
    assert AuditAction.KEY_DELETE in [e.action for e in events]


def test_store_failure_marks_capabilities_unavailable_not_stuck() -> None:
    """凭据存储不可用时：保存抛存储错误（API 层转 503），
    且探测状态被标记为不可用而不是卡在探测中。"""
    service = _service(_FailingStore())
    with pytest.raises(CredentialStoreError):
        service.save("alice", SecretStr("sk-audit-1234567890"))
    snapshot = service._probes.status_snapshot("alice")
    assert all(c.status == ProbeStatus.UNAVAILABLE for c in snapshot)
    assert all("凭据库不可用" in c.message for c in snapshot)
