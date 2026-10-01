"""运行级模型额度快照与版本兼容（改进工单 03）。

V2 Issue 09 只把主模型 **ID** 写进运行配置（``run_model_id``）；当排队期间
用户切换运行配置时，旧自定义模型既没有 ID 匹配的当前快照，也没有随运行
保存的已验证额度，编译只能回退到 32,768 缺省窗口——旧运行的真实额度无法
可靠恢复（``docs/上下文工程/审查与改进建议.md`` §5）。

本模块把「一次运行实际使用的模型额度」做成**入队时原子保存**的完整快照：

- 实际模型 ID、已验证上下文窗口、最大输入额度、配置 revision、元数据合同
  版本与**验证依据**（出厂矩阵 / 设置页激活 / 可审计兼容补齐 / 未验证）；
- 快照随运行配置（``generation_runs.config``）单事务写入，与运行创建同源，
  跨进程恢复读同一份数据；
- 编译器和网关都只读这份快照解析可用输入上界，配置切换只影响之后创建的
  运行；旧运行缺失真实额度时**明确闭锁**（``resolved=False``），或经
  ``RUNTIME_CONFIG_SNAPSHOT`` 可审计兼容流程补齐，绝不用任意常数冒充已验证
  额度。

快照只含 ID、额度、revision 与版本标识，**不含凭据，也不含任何提示词或
私人正文**；``redaction_audit`` 显式列出被排除的敏感字段，供导出与审计复核。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from bridges.ai.model_metadata import MODEL_METADATA_VERSION
from bridges.ai.run_model_config import (
    ModelConfigSource,
    RunModelConfigSnapshot,
    factory_run_model_config,
)

#: 运行级额度快照合同版本（字段或语义变化时递增）。
MODEL_QUOTA_VERSION = "model-quota-v1"

#: 运行配置（``generation_runs.config``）中承载额度快照的键。
RUN_MODEL_QUOTA_CONFIG_KEY = "model_quota"

#: 快照**绝不出现**的敏感字段（脱敏审计与导出据此声明排除范围）。
QUOTA_FORBIDDEN_FIELDS: tuple[str, ...] = (
    "api_key",
    "authorization",
    "auth_token",
    "access_token",
    "secret",
    "password",
    "credential",
    "prompt",
    "messages",
    "content",
    "response",
    "image",
    "video",
    "audio",
    "ocr",
)


class QuotaVerificationBasis(StrEnum):
    """额度快照的验证依据：谁、用什么证据给出了这些额度。"""

    #: 出厂批准矩阵（``fixed_models``）的已验证快照。
    FACTORY_MATRIX = "factory_matrix"
    #: 用户在设置页经「元数据核对 + 真实探测」激活的运行配置。
    SETTINGS_ACTIVATION = "settings_activation"
    #: 旧运行只锁定了模型 ID、缺少随运行额度时，用当前仍生效的同一模型
    #: 配置补齐——这是可审计的兼容流程，basis 如实标注来源。
    RUNTIME_CONFIG_SNAPSHOT = "runtime_config_snapshot"
    #: 额度未知：快照存在但缺少已验证窗口，或版本不可读。
    UNVERIFIED = "unverified"


class RunModelQuota(BaseModel):
    """一次运行实际使用的模型额度快照（入队时原子保存）。"""

    model_id: str = Field(description="该运行实际使用的主模型 ID。")
    context_window: int | None = Field(
        default=None, description="已验证上下文窗口（token）；未知为 None。"
    )
    max_input_tokens: int | None = Field(
        default=None, description="已验证最大输入额度（token）；未知为 None。"
    )
    config_revision: int = Field(
        default=0, description="来源运行配置的激活序号（0 表示出厂快照）。"
    )
    metadata_version: str = Field(
        default=MODEL_METADATA_VERSION, description="元数据解析合同版本。"
    )
    verification_basis: QuotaVerificationBasis = Field(
        default=QuotaVerificationBasis.UNVERIFIED, description="额度验证依据。"
    )
    quota_version: str = Field(
        default=MODEL_QUOTA_VERSION, description="本快照的合同版本。"
    )

    @property
    def is_verified(self) -> bool:
        """是否持有可用的已验证上下文窗口。"""
        return (
            self.quota_version == MODEL_QUOTA_VERSION
            and self.verification_basis is not QuotaVerificationBasis.UNVERIFIED
            and self.context_window is not None
            and self.context_window > 0
        )

    def input_upper_bound(self) -> int | None:
        """可用输入上界：已验证窗口与最大输入额度取较小者。

        两者都未知时返回 None——调用方必须闭锁，不能假定缺省窗口。
        """
        if not self.is_verified:
            return None
        assert self.context_window is not None
        if self.max_input_tokens is None:
            return self.context_window
        return min(self.context_window, self.max_input_tokens)

    def to_config(self) -> dict[str, Any]:
        """序列化为可写入运行配置的脱敏字典。"""
        return self.model_dump(mode="json")

    @classmethod
    def from_config(cls, payload: Any) -> RunModelQuota | None:
        """从运行配置读回快照；版本不兼容或结构不可读时返回 None。

        版本兼容策略：只接受 :data:`MODEL_QUOTA_VERSION`；未知版本不猜测
        字段语义，返回 None 让调用方闭锁（绝不把不可读快照当已验证额度）。
        """
        if not isinstance(payload, Mapping):
            return None
        if payload.get("quota_version") != MODEL_QUOTA_VERSION:
            return None
        try:
            return cls.model_validate(dict(payload))
        except ValueError:
            return None


@dataclass(frozen=True)
class QuotaResolution:
    """运行额度解析结果（编译与网关共用同一判定）。"""

    #: 解析出的额度快照（闭锁时为 None）。
    quota: RunModelQuota | None
    #: 是否解析出可用的已验证额度。
    resolved: bool
    #: 生效的验证依据。
    basis: QuotaVerificationBasis | None
    #: 闭锁时的稳定原因码（``resolved`` 为 True 时 None）。
    reason: str | None = None
    #: 是否走了「旧运行兼容补齐」流程（供审计区分）。
    compat_applied: bool = False


#: 闭锁原因码（稳定字符串，供审计与排障）。
QUOTA_REASON_UNREADABLE = "quota_snapshot_unreadable"
QUOTA_REASON_UNVERIFIED = "quota_snapshot_unverified"
QUOTA_REASON_LEGACY_UNVERIFIED = "legacy_run_quota_unverified"


def build_run_model_quota(snapshot: RunModelConfigSnapshot) -> RunModelQuota:
    """由当前运行配置构造入队时保存的额度快照。"""
    if snapshot.source is ModelConfigSource.SETTINGS:
        basis = QuotaVerificationBasis.SETTINGS_ACTIVATION
    else:
        basis = QuotaVerificationBasis.FACTORY_MATRIX
    if snapshot.context_window is None:
        basis = QuotaVerificationBasis.UNVERIFIED
    return RunModelQuota(
        model_id=snapshot.model_id,
        context_window=snapshot.context_window,
        max_input_tokens=snapshot.max_input_tokens,
        config_revision=snapshot.revision,
        metadata_version=snapshot.metadata_version,
        verification_basis=basis,
    )


def resolve_run_quota(
    run_config: Mapping[str, Any] | None,
    *,
    current_snapshot: RunModelConfigSnapshot | None,
) -> QuotaResolution:
    """解析一次运行应使用的模型额度快照。

    优先级：

    1. 运行配置自带 ``model_quota``（新运行）→ 直接采用；快照不可读或未验证
       时闭锁；
    2. 旧运行只有 ``run_model_id``（本票之前的运行）→ 可审计兼容：仅当当前
       生效配置的模型 ID 与该锁定 ID 相同、且配置带已验证窗口时，用当前配置
       补齐并标注 :data:`QuotaVerificationBasis.RUNTIME_CONFIG_SNAPSHOT`；
    3. 旧运行锁定模型已不再是当前配置、或没有验证窗口 → **闭锁**，绝不用
       32,768 之类任意常数冒充该模型真实额度；
    4. 运行未锁定模型（未装配提供者）→ 出厂矩阵快照。
    """
    config: Mapping[str, Any] = run_config or {}
    raw = config.get(RUN_MODEL_QUOTA_CONFIG_KEY)
    if RUN_MODEL_QUOTA_CONFIG_KEY in config:
        quota = RunModelQuota.from_config(raw)
        if quota is None:
            return QuotaResolution(
                None, False, None, QUOTA_REASON_UNREADABLE
            )
        if config.get("run_model_id") not in (None, quota.model_id):
            return QuotaResolution(None, False, None, QUOTA_REASON_UNREADABLE)
        if not quota.is_verified:
            return QuotaResolution(
                quota, False, quota.verification_basis, QUOTA_REASON_UNVERIFIED
            )
        return QuotaResolution(quota, True, quota.verification_basis)

    locked_model_id = config.get("run_model_id")
    if locked_model_id is None:
        quota = build_run_model_quota(
            current_snapshot or factory_run_model_config()
        )
        return QuotaResolution(
            quota if quota.is_verified else None,
            quota.is_verified,
            quota.verification_basis,
            None if quota.is_verified else QUOTA_REASON_UNVERIFIED,
        )

    if (
        current_snapshot is not None
        and current_snapshot.model_id == locked_model_id
        and current_snapshot.context_window is not None
    ):
        compat = RunModelQuota(
            model_id=locked_model_id,
            context_window=current_snapshot.context_window,
            max_input_tokens=current_snapshot.max_input_tokens,
            config_revision=current_snapshot.revision,
            metadata_version=current_snapshot.metadata_version,
            verification_basis=QuotaVerificationBasis.RUNTIME_CONFIG_SNAPSHOT,
        )
        return QuotaResolution(
            compat,
            compat.is_verified,
            QuotaVerificationBasis.RUNTIME_CONFIG_SNAPSHOT,
            None if compat.is_verified else QUOTA_REASON_UNVERIFIED,
            compat_applied=True,
        )

    return QuotaResolution(
        None, False, None, QUOTA_REASON_LEGACY_UNVERIFIED
    )


def export_run_model_quota(quota: RunModelQuota) -> dict[str, Any]:
    """导出额度快照为可审计的脱敏字典（不含凭据与私人正文）。"""
    exported = quota.model_dump(mode="json")
    exported["redacted_fields"] = list(QUOTA_FORBIDDEN_FIELDS)
    return exported


def redaction_audit() -> dict[str, Any]:
    """脱敏审计声明：快照合同保证不携带的敏感字段与保证内容。

    用于导出/审计报告自证「运行锁与额度快照不含凭据或完整私人提示正文」。
    """
    return {
        "quota_version": MODEL_QUOTA_VERSION,
        "excluded_fields": list(QUOTA_FORBIDDEN_FIELDS),
        "contains_credentials": False,
        "contains_prompt_body": False,
        "notes": (
            "额度快照只记录模型 ID、已验证窗口、最大输入额度、配置 revision "
            "与元数据合同版本；提示词、消息正文与凭据不进入该快照。"
        ),
    }


__all__ = [
    "MODEL_QUOTA_VERSION",
    "QUOTA_FORBIDDEN_FIELDS",
    "QUOTA_REASON_LEGACY_UNVERIFIED",
    "QUOTA_REASON_UNREADABLE",
    "QUOTA_REASON_UNVERIFIED",
    "RUN_MODEL_QUOTA_CONFIG_KEY",
    "QuotaResolution",
    "QuotaVerificationBasis",
    "RunModelQuota",
    "build_run_model_quota",
    "export_run_model_quota",
    "redaction_audit",
    "resolve_run_quota",
]
