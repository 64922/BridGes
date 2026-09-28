"""主模型配置的已认证设置路由（V2 Issue 09）。

``GET /settings/models`` 报告当前生效的运行配置（实际模型 ID、能力、上下文
长度、来源与最近验证）；``PUT /settings/models`` 接受手填模型 ID，按
``docs/v2/interaction.md`` §6.3 的顺序验证后原子激活：

1. **精确元数据查询**：百炼模型列表按 ID 精确命中，核对文本、图片、工具
   调用、结构化输出与上下文额度；元数据缺失、模型不存在、鉴权失败都给出
   具体中文原因；
2. **真实能力探测**：用当前 Qwen 密钥对候选模型做四项最小真实调用，逐项
   证实同一组能力；任一不通过都不保存；
3. **原子激活**：把元数据版本与能力档案连同模型 ID 一次写入运行配置。

没有任何模型预设列表；密钥未配置或不匹配时错误信息明确指出操作顺序。
响应与服务端日志都不包含明文密钥，历史消息的模型记录不被改写。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, SecretStr

from bridges.ai.fixed_models import CHAT_MODEL_ID
from bridges.ai.model_metadata import (
    MODEL_METADATA_ERR_UNAVAILABLE,
    ModelMetadata,
    ModelMetadataError,
)
from bridges.ai.model_probe import (
    CAPABILITY_LABELS,
    PROBED_CAPABILITIES,
    ModelCapabilityProbe,
    ProbeOutcome,
)
from bridges.api.auth import SubjectDep
from bridges.api.qwen_settings import (
    active_qwen_key,
    active_run_model_config,
    apply_qwen_key,
    build_metadata_source,
    build_probe_client,
    credential_store,
    last_validation,
    qwen_key_shadow_notice,
    qwen_key_shadowed,
    record_validation,
    run_model_config_provider,
)
from bridges.contracts.ai import ModelCapabilities
from bridges.credentials.global_credential import QWEN_MODEL_MIGRATION_GUIDANCE
from bridges.credentials.ids import GLOBAL_QWEN_CREDENTIAL_ID, QWEN_ITEM
from bridges.credentials.store import CredentialStoreError

router = APIRouter(prefix="/settings/models", tags=["模型设置"])

#: 稳定错误码（前端据此在字段附近给出操作顺序提示）。
MODEL_ERR_EMPTY_CANDIDATE = "model_id_invalid"
MODEL_ERR_CREDENTIAL_NOT_CONFIGURED = "credential_not_configured"
MODEL_ERR_CAPABILITY_MISSING = "model_capability_missing"
MODEL_ERR_PROBE_FAILED = "model_probe_failed"
MODEL_ERR_METADATA_UNAVAILABLE = "model_metadata_unavailable"


class ModelCapabilityCheck(BaseModel):
    """一条能力证据：元数据声明或真实探测。"""

    capability: str
    label: str
    source: str = Field(description="metadata（百炼声明）或 probe（真实调用）。")
    ok: bool
    message: str | None = None


class ModelValidationReport(BaseModel):
    """一次主模型验证的完整结论（成功与失败都保留）。"""

    model_id: str
    passed: bool
    capabilities: ModelCapabilities
    context_window: int | None = None
    max_input_tokens: int | None = None
    checks: list[ModelCapabilityCheck] = Field(default_factory=list)
    message: str | None = None


class ModelSettingsResponse(BaseModel):
    """当前生效的运行配置与最近一次验证结论。"""

    model_id: str
    source: str
    capabilities: ModelCapabilities
    context_window: int | None = None
    max_input_tokens: int | None = None
    metadata_version: str
    revision: int
    validated_at: datetime | None = None
    credential_configured: bool
    last_validation: ModelValidationReport | None = None
    error: str | None = None


class ModelCandidate(BaseModel):
    """候选主模型配置。

    ``api_key`` 是**可选**的候选 Qwen 密钥（工单 01）：旧密钥已失效、而新密钥
    只覆盖另一批模型时，「Qwen 凭据」卡与「Qwen 主模型」卡会互相指向对方而
    无法脱困。在主模型卡同时提交候选模型 ID 与候选密钥，就能在**同一次操作**
    里完成"换密钥 + 换主模型"：两者都用候选值验证，通过后一起保存生效。
    """

    model_id: str
    api_key: SecretStr | None = None


def _candidate_value(secret: SecretStr | None) -> str:
    return secret.get_secret_value().strip() if secret is not None else ""


def _invalid(code: str, message: str, **extra: Any) -> HTTPException:
    detail: dict[str, Any] = {"error": code, "message": message}
    detail.update(extra)
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail
    )


def _unavailable(code: str, message: str, **extra: Any) -> HTTPException:
    detail: dict[str, Any] = {"error": code, "message": message}
    detail.update(extra)
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


def _metadata_checks(metadata: ModelMetadata) -> list[ModelCapabilityCheck]:
    """把元数据声明的四项能力转成证据行（失败原因附在对应能力上）。"""
    declared = metadata.capabilities.model_dump()
    checks: list[ModelCapabilityCheck] = []
    for capability in PROBED_CAPABILITIES:
        ok = bool(declared.get(capability))
        checks.append(
            ModelCapabilityCheck(
                capability=capability,
                label=capability_label(capability),
                source="metadata",
                ok=ok,
                message=None if ok else f"百炼元数据未声明{capability_label(capability)}能力。",
            )
        )
    return checks


def capability_label(capability: str) -> str:
    """能力中文标签（未知能力回落原名，避免抛错）。"""
    return CAPABILITY_LABELS.get(capability, capability)


def _probe_checks(outcomes: list[ProbeOutcome]) -> list[ModelCapabilityCheck]:
    return [
        ModelCapabilityCheck(
            capability=outcome.capability,
            label=outcome.label,
            source="probe",
            ok=outcome.ok,
            message=outcome.message,
        )
        for outcome in outcomes
    ]


def _missing_capability_message(missing: list[str]) -> str:
    labels = "、".join(capability_label(name) for name in missing)
    return (
        f"该模型不满足 BridGes 主模型要求：百炼元数据未声明 {labels} 能力。"
        "请换用支持文本、图片、工具调用、结构化输出与充足上下文的模型 ID。"
    )


def _probe_failure_message(failures: list[ProbeOutcome]) -> str:
    details = "；".join(
        outcome.message or f"{outcome.label}探测失败。" for outcome in failures
    )
    return f"真实能力探测未通过：{details}"


def _settings_response(request: Request) -> ModelSettingsResponse:
    config = active_run_model_config(request)
    checked_at, report = last_validation(request)
    provider = run_model_config_provider(request)
    return ModelSettingsResponse(
        model_id=config.model_id,
        source=config.source.value,
        capabilities=config.capabilities,
        context_window=config.context_window,
        max_input_tokens=config.max_input_tokens,
        metadata_version=config.metadata_version,
        revision=config.revision,
        validated_at=config.validated_at or checked_at,
        credential_configured=active_qwen_key(request) is not None,
        last_validation=report,
        error=provider.load_error,
    )


@router.get("", response_model=ModelSettingsResponse)
def get_model_settings(
    request: Request, response: Response, subject: SubjectDep
) -> ModelSettingsResponse:
    """报告实际模型 ID、能力、上下文长度与最近验证结论（不含任何密钥）。"""
    del subject
    response.headers["Cache-Control"] = "no-store"
    return _settings_response(request)


@router.put("", response_model=ModelSettingsResponse)
def replace_model_configuration(
    candidate: ModelCandidate, request: Request, subject: SubjectDep
) -> ModelSettingsResponse:
    """验证并原子激活手填的主模型 ID；失败保留原配置。

    可选地同时提交候选密钥（工单 01）：给了候选密钥就用它验证与激活，并在
    成功后一并替换全局凭据——这让"换密钥 + 换主模型"成为一次操作，用户不会
    被两张卡互相指向。
    """
    del subject
    model_id = candidate.model_id.strip()
    if not model_id:
        message = (
            f"模型 ID 不能为空；请填写百炼模型 ID，例如 {CHAT_MODEL_ID}。"
        )
        raise _invalid(MODEL_ERR_EMPTY_CANDIDATE, message, model_id=model_id)

    candidate_key = _candidate_value(candidate.api_key)
    replacing_key = bool(candidate_key)
    api_key = (
        SecretStr(candidate_key) if replacing_key else active_qwen_key(request)
    )
    if api_key is None:
        # 操作顺序：先配置密钥，再验证模型 ID（字段附近提示用同一文案）。
        raise _invalid(
            MODEL_ERR_CREDENTIAL_NOT_CONFIGURED,
            QWEN_MODEL_MIGRATION_GUIDANCE,
            model_id=model_id,
        )

    source = build_metadata_source(request, api_key)
    try:
        metadata = source.query(model_id)
    except ModelMetadataError as exc:
        report = ModelValidationReport(
            model_id=model_id,
            passed=False,
            capabilities=ModelCapabilities(),
            message=exc.message,
        )
        record_validation(request, report)
        if exc.code == MODEL_METADATA_ERR_UNAVAILABLE:
            raise _unavailable(exc.code, exc.message, model_id=model_id) from exc
        # 凭据失效、模型不存在与元数据缺失都需要用户改输入，按 422 返回
        # 具体中文原因（元数据缺失无法核对能力，因此同样不能保存）。
        raise _invalid(exc.code, exc.message, model_id=model_id) from exc

    metadata_checks = _metadata_checks(metadata)
    missing = [check.capability for check in metadata_checks if not check.ok]
    if missing:
        message = _missing_capability_message(missing)
        record_validation(
            request,
            ModelValidationReport(
                model_id=model_id,
                passed=False,
                capabilities=metadata.capabilities,
                context_window=metadata.context_window,
                max_input_tokens=metadata.max_input_tokens,
                checks=metadata_checks,
                message=message,
            ),
        )
        raise _invalid(
            MODEL_ERR_CAPABILITY_MISSING,
            message,
            model_id=model_id,
            checks=[check.model_dump() for check in metadata_checks],
        )

    probe_client = build_probe_client(request, api_key)
    outcomes = ModelCapabilityProbe(probe_client).run(
        model_id=model_id, capabilities=metadata.capabilities
    )
    checks = metadata_checks + _probe_checks(outcomes)
    failures = [outcome for outcome in outcomes if not outcome.ok]
    if failures:
        message = _probe_failure_message(failures)
        record_validation(
            request,
            ModelValidationReport(
                model_id=model_id,
                passed=False,
                capabilities=metadata.capabilities,
                context_window=metadata.context_window,
                max_input_tokens=metadata.max_input_tokens,
                checks=checks,
                message=message,
            ),
        )
        raise _invalid(
            MODEL_ERR_PROBE_FAILED,
            message,
            model_id=model_id,
            checks=[check.model_dump() for check in checks],
        )

    checked_at = datetime.now(UTC)
    key_shadowed = False
    if replacing_key:
        # 先落盘再激活：候选密钥已经过完整验证（元数据 + 真实能力探测），
        # 保存失败（凭据库不可用）时不激活模型，保持两张卡的真实状态一致。
        store = credential_store(request)
        if store is None:
            raise _unavailable(
                "credential_store_unavailable",
                "凭据存储暂不可用；主模型未做改动。",
                model_id=model_id,
            )
        try:
            store.save(GLOBAL_QWEN_CREDENTIAL_ID, api_key)
        except (CredentialStoreError, OSError) as exc:
            raise _unavailable(
                "credential_store_unavailable",
                "凭据无法安全保存，请检查凭据存储；主模型未做改动。",
                model_id=model_id,
            ) from exc
        # 环境变量优先：此时新密钥在本次运行与重启后都不会生效，如实报告而不是
        # 显示"已更换密钥"，否则用户会继续拿着失效的旧密钥排错。
        key_shadowed = qwen_key_shadowed(request)
        if not key_shadowed:
            apply_qwen_key(request, api_key)
        record_validation(request, QWEN_ITEM)
    provider = run_model_config_provider(request)
    provider.activate(
        model_id=model_id,
        capabilities=metadata.capabilities,
        context_window=metadata.context_window,
        max_input_tokens=metadata.max_input_tokens,
        validated_at=checked_at,
        metadata_version=metadata.metadata_version,
    )
    if not replacing_key:
        message = "验证通过，已从下一条消息起使用新配置。"
    elif key_shadowed:
        message = (
            f"验证通过：主模型已切换，从下一条消息起使用新配置。"
            f"{qwen_key_shadow_notice()}"
        )
    else:
        message = "验证通过：已同时更换 Qwen 密钥与主模型，从下一条消息起使用新配置。"
    report = ModelValidationReport(
        model_id=model_id,
        passed=True,
        capabilities=metadata.capabilities,
        context_window=metadata.context_window,
        max_input_tokens=metadata.max_input_tokens,
        checks=checks,
        message=message,
    )
    record_validation(request, report)
    return _settings_response(request)
