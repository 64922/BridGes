"""生产组合校验器：冻结模型矩阵与真实 adapter 启动门禁（Issue 09）。

校验对象是一次生产组合的 (registry, gateway) 快照（见
``bridges.ai.production``）。规则（ADR-0009 / Issue 09）：

1. 缺少安装级全局 Qwen Key：``missing_global_qwen_key`` 失败关闭；
2. 活跃 MODEL capability 必须出现在批准矩阵中且绑定批准模型快照：
   - capability 不在矩阵 -> ``unknown_capability``（含已退役能力复活）；
   - 同名 capability 存在多个活跃版本 -> ``duplicate_binding``；
   - model ID 与矩阵不一致 -> ``model_matrix_drift``；
   - model ID 不属于批准集合 -> ``unapproved_model_id``；
   - vendor/region 与合同不一致 -> ``model_matrix_drift``；
   - MODEL capability 配置了 fallback -> ``model_fallback_configured``；
3. 每个活跃 MODEL capability 恰好绑定一个真实适配器：
   - 未绑定 -> ``missing_adapter``；
   - Stub/closeout/deterministic 适配器或 cassette 回放 ->
     ``production_test_adapter``；
4. vision/OCR 与核心对话对齐同一快照，但真实兼容 smoke 未证实前 ->
   ``vision_ocr_compatibility_unproven`` 失败关闭（不允许未经 ADR 的
   视觉/OCR 生产例外）。

所有 violation 只携带 capability 名、批准/实际 model ID 与稳定错误码，
绝不包含 Key、Authorization、请求正文、图像内容或完整模型输出。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from bridges.ai.adapters import StubQwenAdapter
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.ai.fixed_models import MODEL_BY_CAPABILITY
from bridges.ai.model_gateway import ModelGateway
from bridges.contracts.ai import CapabilityKind

#: 稳定门禁错误码（Issue 09 Observability 合同）。
MISSING_GLOBAL_QWEN_KEY = "missing_global_qwen_key"
UNAPPROVED_MODEL_ID = "unapproved_model_id"
MODEL_MATRIX_DRIFT = "model_matrix_drift"
MISSING_ADAPTER = "missing_adapter"
PRODUCTION_TEST_ADAPTER = "production_test_adapter"
ACTUAL_MODEL_MISMATCH = "actual_model_mismatch"
VISION_OCR_COMPATIBILITY_UNPROVEN = "vision_ocr_compatibility_unproven"
#: 扩展稳定错误码（"至少包括"清单之外，同样写入门禁报告）。
UNKNOWN_CAPABILITY = "unknown_capability"
DUPLICATE_BINDING = "duplicate_binding"
MODEL_FALLBACK_CONFIGURED = "model_fallback_configured"

#: 生产矩阵约定的区域（与 registry 注册一致）。
EXPECTED_REGION = "cn-beijing"
#: ADR-0007：Wan 是模型矩阵唯一非 Qwen 系列例外。
WAN_VENDOR = "wan"
QWEN_VENDOR = "qwen"

#: 门禁覆盖的视觉/OCR capability（真实兼容 smoke 的对象）。
VISION_OCR_CAPABILITIES = frozenset({"qwen_vision", "qwen_ocr"})


@dataclass(frozen=True)
class CompositionViolation:
    """一条门禁违规：稳定错误码 + capability + 脱敏说明。"""

    code: str
    capability: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "capability": self.capability,
            "detail": self.detail,
        }


class ProductionCompositionError(Exception):
    """生产组合门禁失败：携带稳定错误码列表（不含秘密正文）。"""

    def __init__(self, violations: Sequence[CompositionViolation]) -> None:
        self.violations = list(violations)
        codes = sorted({violation.code for violation in self.violations})
        summary = ", ".join(codes)
        super().__init__(f"生产组合门禁失败：{summary}。")


def _is_prohibited_test_adapter(adapter: Any) -> bool:
    """Stub/closeout/deterministic 适配器一律禁止出现在生产组合。

    ``CloseoutQwenAdapter`` 继承 ``StubQwenAdapter``，isinstance 已覆盖；
    其余按类名特征识别（类名含 stub/deterministic），避免测试替身混入
    生产接线。生产装配只绑定 ``Qwen*Adapter`` 真实适配器类。
    """
    if isinstance(adapter, StubQwenAdapter):
        return True
    name = type(adapter).__name__.casefold()
    return "stub" in name or "deterministic" in name


def validate_production_composition(
    registry: CapabilityRegistry,
    gateway: ModelGateway,
    *,
    global_key_configured: bool,
    cassette_enabled: bool = False,
    vision_ocr_compatibility_proven: bool = False,
    vision_ocr_compatibility_required: bool = True,
    approved_matrix: Mapping[str, str] | None = None,
) -> list[CompositionViolation]:
    """枚举生产组合的全部门禁违规；无违规返回空列表。

    ``vision_ocr_compatibility_required=False`` 用于阶段 1 的 production-
    like 启动门（Issue 09 Observability：先在 production-like 启动与
    release gate 强制执行，再进入正式生产启动）：启动门检查组合完整性
    （矩阵漂移/Stub/cassette/missing adapter/缺 Key），vision/OCR 真实
    兼容证据由发布门（``scripts/model_matrix_gate.py --real-probes``）
    强制要求，不提供任何可被普通环境变量绕过的警告模式。
    """
    matrix = dict(approved_matrix) if approved_matrix is not None else dict(MODEL_BY_CAPABILITY)
    violations: list[CompositionViolation] = []

    if not global_key_configured:
        violations.append(
            CompositionViolation(
                MISSING_GLOBAL_QWEN_KEY,
                "*",
                "未配置安装级全局 Qwen Key（BRIDGES_QWEN_API_KEY/_FILE），生产启动失败关闭。",
            )
        )

    active = [
        capability
        for capability in registry.list_active()
        if capability.kind == CapabilityKind.MODEL and capability.model_id
    ]
    active_names = [capability.name for capability in active]
    for name in sorted({name for name in active_names if active_names.count(name) > 1}):
        violations.append(
            CompositionViolation(
                DUPLICATE_BINDING,
                name,
                "同一 capability 存在多个活跃版本绑定，绑定歧义阻止生产启动。",
            )
        )

    approved_ids = set(matrix.values())
    for capability in active:
        name = capability.name
        if name not in matrix:
            violations.append(
                CompositionViolation(
                    UNKNOWN_CAPABILITY,
                    name,
                    "capability 不在批准矩阵中（含已退役能力复活），阻止生产启动。",
                )
            )
            continue
        approved = matrix[name]
        if capability.model_id != approved:
            violations.append(
                CompositionViolation(
                    MODEL_MATRIX_DRIFT,
                    name,
                    f"model id 漂移：注册 {capability.model_id}，批准 {approved}。",
                )
            )
        if capability.model_id not in approved_ids:
            violations.append(
                CompositionViolation(
                    UNAPPROVED_MODEL_ID,
                    name,
                    f"model id {capability.model_id} 不在批准矩阵中。",
                )
            )
        expected_vendor = WAN_VENDOR if name == "qwen_wan" else QWEN_VENDOR
        if capability.vendor != expected_vendor:
            violations.append(
                CompositionViolation(
                    MODEL_MATRIX_DRIFT,
                    name,
                    f"vendor {capability.vendor} 与批准矩阵不一致（应为 {expected_vendor}）。",
                )
            )
        if capability.region != EXPECTED_REGION:
            violations.append(
                CompositionViolation(
                    MODEL_MATRIX_DRIFT,
                    name,
                    f"region {capability.region} 与批准矩阵不一致（应为 {EXPECTED_REGION}）。",
                )
            )
        if capability.fallback_policy.fallback_capability_name:
            violations.append(
                CompositionViolation(
                    MODEL_FALLBACK_CONFIGURED,
                    name,
                    "MODEL capability 配置了 fallback，禁止静默换能力/换模型。",
                )
            )

    for capability in active:
        name, version = capability.name, capability.version
        adapter = gateway.get_adapter(name, version)
        if adapter is None:
            violations.append(
                CompositionViolation(
                    MISSING_ADAPTER,
                    name,
                    "活跃 MODEL capability 未绑定适配器，阻止生产启动。",
                )
            )
            continue
        if _is_prohibited_test_adapter(adapter):
            violations.append(
                CompositionViolation(
                    PRODUCTION_TEST_ADAPTER,
                    name,
                    "绑定 Stub/cassette/deterministic 测试适配器，阻止生产启动。",
                )
            )

    if cassette_enabled:
        violations.append(
            CompositionViolation(
                PRODUCTION_TEST_ADAPTER,
                "*",
                "cassette 回放已启用，生产组合禁止使用录制的供应商响应。",
            )
        )

    active_names_set = set(active_names)
    if (
        vision_ocr_compatibility_required
        and not vision_ocr_compatibility_proven
        and VISION_OCR_CAPABILITIES & active_names_set
    ):
        violations.append(
            CompositionViolation(
                VISION_OCR_COMPATIBILITY_UNPROVEN,
                "qwen_vision/qwen_ocr",
                "视觉/OCR 对批准快照的真实兼容 smoke 未证实，失败关闭。",
            )
        )
    return violations


def enforce_production_composition(
    registry: CapabilityRegistry,
    gateway: ModelGateway,
    *,
    global_key_configured: bool,
    cassette_enabled: bool = False,
    vision_ocr_compatibility_proven: bool = False,
    vision_ocr_compatibility_required: bool = True,
    approved_matrix: Mapping[str, str] | None = None,
) -> None:
    """校验并失败关闭：存在任何违规时抛出 ``ProductionCompositionError``。"""
    violations = validate_production_composition(
        registry,
        gateway,
        global_key_configured=global_key_configured,
        cassette_enabled=cassette_enabled,
        vision_ocr_compatibility_proven=vision_ocr_compatibility_proven,
        vision_ocr_compatibility_required=vision_ocr_compatibility_required,
        approved_matrix=approved_matrix,
    )
    if violations:
        raise ProductionCompositionError(violations)
