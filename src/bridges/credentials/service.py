"""账户级模型凭据服务门面。

编排凭据存储、固定能力探测与审计事件：保存/替换/删除要求近期密码确认
（由 API 层强制），每次变更产生不含秘密正文的审计事件；保存或删除后
探测状态立即复位，探测结果只停用对应能力，不影响登录、资料与本地功能。
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any

from pydantic import SecretStr

from bridges.contracts.credentials import (
    KeySettingsProjection,
    KeySettingsStatus,
    ProbeRecord,
    ProbeStatus,
)
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.credentials.matrix import get_binding
from bridges.credentials.probes import CapabilityProbeService
from bridges.credentials.store import CredentialStoreError, CredentialStorePort
from bridges.observability.service import ObservabilityService
from bridges.persistence import StateStore

#: Key 元数据（非秘密）持久化命名空间：只存更新时间与操作类型。
_METADATA_NAMESPACE = "key_metadata"


class KeyCredentialError(Exception):
    """凭据服务领域错误；消息为面向用户的中文原因。"""


class KeyCredentialService:
    """按账户保存/替换/删除百炼 Key，并驱动固定能力真实探测。"""

    def __init__(
        self,
        *,
        credential_store: CredentialStorePort,
        probe_service: CapabilityProbeService,
        observability_service: ObservabilityService,
        state_store: StateStore | None = None,
        sync_probes: bool = False,
    ) -> None:
        self._store = credential_store
        self._probes = probe_service
        self._observability = observability_service
        self._state_store = state_store
        #: True 时探测同步执行（测试确定性）；False 时后台线程执行。
        self._sync_probes = sync_probes
        self._metadata: dict[str, dict[str, Any]] = {}
        self._load_metadata()

    # ------------------------------------------------------------------
    # 元数据（非秘密）
    # ------------------------------------------------------------------

    def _load_metadata(self) -> None:
        if self._state_store is None:
            return
        state = self._state_store.load(_METADATA_NAMESPACE) or {}
        raw = state.get("accounts")
        if isinstance(raw, dict):
            self._metadata = {
                account_id: dict(value)
                for account_id, value in raw.items()
                if isinstance(value, dict)
            }

    def _persist_metadata(self) -> None:
        if self._state_store is None:
            return
        self._state_store.save(
            _METADATA_NAMESPACE, {"accounts": self._metadata}
        )

    def _record_change(self, account_id: str, operation: str) -> None:
        self._metadata[account_id] = {
            "updated_at": datetime.now(UTC).isoformat(),
            "operation": operation,
        }
        self._persist_metadata()

    def _updated_at(self, account_id: str) -> datetime | None:
        raw = self._metadata.get(account_id, {}).get("updated_at")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw))
        except ValueError:
            return None

    # ------------------------------------------------------------------
    # 审计
    # ------------------------------------------------------------------

    def _audit(
        self,
        *,
        account_id: str,
        action: AuditAction,
        result: AuditResult,
        details: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> None:
        # 审计事件绝不携带 Key 正文；details 只允许白名单字段。
        safe_details = {
            key: value
            for key, value in (details or {}).items()
            if key in {"operation", "capability", "capabilities", "probe_id"}
        }
        self._observability.log_audit(
            actor_account_id=account_id,
            actor_session_id=session_id,
            action=action,
            result=result,
            reason=None,
            details=safe_details,
        )

    # ------------------------------------------------------------------
    # 状态投影
    # ------------------------------------------------------------------

    def get_projection(self, account_id: str) -> KeySettingsProjection:
        """返回不含秘密正文的密钥设置投影。"""
        key = self._store.get(account_id)
        capabilities = self._probes.status_snapshot(account_id)
        updated_at = self._updated_at(account_id)
        if key is None:
            return KeySettingsProjection(
                status=KeySettingsStatus.UNCONFIGURED,
                configured=False,
                capabilities=capabilities,
                updated_at=updated_at,
                message="尚未配置百炼密钥。",
                next_step=(
                    "录入百炼 Key 后，系统将用非用户数据逐项真实探测固定能力；"
                    "未配置前不会显示任何能力可用。"
                ),
            )
        return KeySettingsProjection(
            status=KeySettingsStatus.CONFIGURED,
            configured=True,
            key_tail=KeySettingsProjection.masked_tail(key),
            capabilities=capabilities,
            updated_at=updated_at,
            message="已保存百炼 Key。",
            next_step="探测失败只会停用对应能力，不影响登录、资料与本地功能；可对单项执行同模型重试。",
        )

    # ------------------------------------------------------------------
    # 保存 / 替换 / 删除
    # ------------------------------------------------------------------

    def save(
        self,
        account_id: str,
        key: SecretStr,
        *,
        session_id: str | None = None,
    ) -> KeySettingsProjection:
        """保存或替换当前账户 Key，复位探测并触发全量真实探测。"""
        self._store.save(account_id, key)
        self._probes.reset(account_id)
        self._probes.mark_probing(account_id)
        self._record_change(account_id, "save")
        self._audit(
            account_id=account_id,
            action=AuditAction.KEY_SAVE,
            result=AuditResult.SUCCESS,
            details={"operation": "save"},
            session_id=session_id,
        )
        self.schedule_probes(account_id, session_id=session_id)
        return self.get_projection(account_id)

    def delete(
        self,
        account_id: str,
        *,
        session_id: str | None = None,
    ) -> KeySettingsProjection:
        """删除当前账户 Key，复位探测状态。"""
        self._store.delete(account_id)
        self._probes.reset(account_id)
        self._record_change(account_id, "delete")
        self._audit(
            account_id=account_id,
            action=AuditAction.KEY_DELETE,
            result=AuditResult.SUCCESS,
            details={"operation": "delete"},
            session_id=session_id,
        )
        return self.get_projection(account_id)

    # ------------------------------------------------------------------
    # 探测
    # ------------------------------------------------------------------

    def schedule_probes(
        self, account_id: str, *, session_id: str | None = None
    ) -> None:
        """触发全量探测：同步（测试/人工冒烟）或后台线程。"""
        if self._sync_probes:
            self.run_all_probes(account_id, session_id=session_id)
            return
        worker = threading.Thread(
            target=self.run_all_probes,
            args=(account_id,),
            kwargs={"session_id": session_id},
            daemon=True,
        )
        worker.start()

    def run_all_probes(
        self, account_id: str, *, session_id: str | None = None
    ) -> list[ProbeRecord]:
        """逐项真实探测固定矩阵；Key 缺失时不执行并保持未探测。"""
        try:
            key = self._store.get(account_id)
        except CredentialStoreError as exc:
            # 后台线程内不能静默失败：把状态标记为不可用并给出中文原因，
            # 页面不会停留在"探测中"。
            self._probes.mark_unavailable(account_id, str(exc))
            return []
        if key is None:
            self._probes.reset(account_id)
            return []
        records = self._probes.run_all(account_id, key)
        self._audit_probe_run(account_id, records, session_id=session_id)
        return records

    @staticmethod
    def _require_binding(capability_id: str) -> None:
        """校验能力标识属于固定矩阵，未知标识返回中文错误。"""
        try:
            get_binding(capability_id)
        except KeyError as exc:
            raise KeyCredentialError(
                f"未知能力标识：{capability_id}，无法重试探测。"
            ) from exc

    def schedule_retry(
        self,
        account_id: str,
        capability_id: str,
        *,
        session_id: str | None = None,
    ) -> None:
        """触发单项同模型重试：同步（测试/冒烟）或后台线程。"""
        self._require_binding(capability_id)
        key = self._store.get(account_id)
        if key is None:
            raise KeyCredentialError("当前账户尚未保存百炼 Key。")
        self._probes.mark_probing(account_id, capability_ids=[capability_id])
        if self._sync_probes:
            self.retry_probe(account_id, capability_id, session_id=session_id)
            return
        worker = threading.Thread(
            target=self.retry_probe,
            args=(account_id, capability_id),
            kwargs={"session_id": session_id},
            daemon=True,
        )
        worker.start()

    def retry_probe(
        self,
        account_id: str,
        capability_id: str,
        *,
        session_id: str | None = None,
    ) -> ProbeRecord:
        """对单项能力执行同模型重试（无备用模型、无 Stub）。"""
        self._require_binding(capability_id)
        try:
            key = self._store.get(account_id)
        except CredentialStoreError as exc:
            self._probes.mark_unavailable(
                account_id, str(exc), capability_ids=[capability_id]
            )
            raise
        if key is None:
            raise KeyCredentialError("当前账户尚未保存百炼 Key。")
        record = self._probes.run_single(account_id, capability_id, key)
        self._audit(
            account_id=account_id,
            action=AuditAction.KEY_PROBE,
            result=_audit_result_for(record),
            details={
                "capability": capability_id,
                "probe_id": record.probe_id,
            },
            session_id=session_id,
        )
        return record

    def _audit_probe_run(
        self,
        account_id: str,
        records: list[ProbeRecord],
        *,
        session_id: str | None,
    ) -> None:
        if not records:
            return
        statuses = {record.capability_id: record.status.value for record in records}
        if all(record.status == ProbeStatus.AVAILABLE for record in records):
            result = AuditResult.SUCCESS
        elif any(record.status == ProbeStatus.AVAILABLE for record in records):
            result = AuditResult.DEGRADED
        else:
            result = AuditResult.BLOCKED
        self._audit(
            account_id=account_id,
            action=AuditAction.KEY_PROBE,
            result=result,
            details={"capabilities": statuses},
            session_id=session_id,
        )


def _audit_result_for(record: ProbeRecord) -> AuditResult:
    return (
        AuditResult.SUCCESS
        if record.status == ProbeStatus.AVAILABLE
        else AuditResult.BLOCKED
    )
