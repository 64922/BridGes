"""搜索、地图与主模型凭据的已认证设置路由（工单 01 修订）。

「保存成功 ⇒ 系统真的用上了」是本模块的合同：

- **真验证**：每个候选值都要经过一次真实只读请求才会被保存——Qwen 用元数据
  查询加最小真实调用，Tavily 用固定探针查询，高德 Web 服务用地理编码请求，
  浏览器地图用「Key + 安全密钥」成对的真实数据服务请求（官方代理方案下由
  服务端追加 ``jscode``）。探测本身跑不通（上游不可达、非 200、无法解析）与
  「上游明确拒绝了这个值」是两种结论，前者按 503 报告，绝不冒充"值无效"。
- **失败落到具体原因**：五张卡都给出稳定分类码（``reason``）与可操作的中文
  诊断，区分值不存在、平台不符、服务未开通或权限不足、签名或白名单限制、
  额度超限与上游不可达。文案与日志都不含凭据正文（也不回显上游 ``info``）。
- **生效边界如实报告**：``effective_source`` 说明当前真正生效的值来自环境变量
  还是凭据库——环境变量优先，它存在时页面保存的值不会生效（这一点必须让用户
  看得见，而不是显示"已配置"却解释不了行为）。
- **只声称已被证明的事**：浏览器地图的底图渲染依赖浏览器的真实请求，保存时
  无法证明，因此保存成功文案只声明"Key 与安全码通过了一次真实数据服务请求"，
  底图路径的结论由地图代理在首次真实请求后回写（见 ``runtime_evidence``）。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, SecretStr

from bridges.ai.model_metadata import (
    MODEL_METADATA_ERR_MODEL_NOT_FOUND,
    MODEL_METADATA_ERR_UNAVAILABLE,
    ModelMetadataError,
)
from bridges.ai.model_probe import ModelCapabilityProbe
from bridges.api.auth import SubjectDep
from bridges.api.credential_state import record_validation, validation_snapshot
from bridges.api.qwen_settings import (
    active_run_model_config,
    apply_qwen_key,
    build_metadata_source,
    build_probe_client,
    credential_resolver,
    qwen_key_shadow_notice,
    qwen_key_shadowed,
    runtime_settings,
)
from bridges.config import secret_environment_source
from bridges.contracts.ai import ModelCapabilities
from bridges.credentials.amap_probes import (
    REASON_BAD_PAYLOAD,
    REASON_UPSTREAM_ERROR,
    REASON_UPSTREAM_UNREACHABLE,
    AmapDiagnosis,
    probe_data_service,
    probe_loader,
)
from bridges.credentials.ids import (
    AMAP_BROWSER_MAP_CREDENTIAL_ID,
    AMAP_BROWSER_MAP_ITEM,
    AMAP_WEB_SERVICE_CREDENTIAL_ID,
    AMAP_WEB_SERVICE_ITEM,
    GLOBAL_QWEN_CREDENTIAL_ID,
    QWEN_ITEM,
    SETTINGS_TAVILY_CREDENTIAL_ID,
    TAVILY_ITEM,
)
from bridges.credentials.runtime_resolver import (
    CredentialSource,
    ResolvedBrowserMapPair,
    ResolvedCredential,
)
from bridges.credentials.store import CredentialStoreError, CredentialStorePort
from bridges.web_search.contracts import WebSearchHealth, WebSearchHealthStatus
from bridges.web_search.tavily import TavilySearchClient

router = APIRouter(prefix="/settings/credentials", tags=["凭据设置"])

#: 失败原因分类（稳定机器码；中文文案不参与断言）。
REASON_CREDENTIAL_MISSING = "credential_missing"
REASON_KEY_REJECTED = "key_rejected"
REASON_MODEL_NOT_MATCHING = "model_not_matching_key"
REASON_PROBE_FAILED = "probe_failed"

#: Tavily 失败分类：错误码 → (分类码, 中文诊断)。
_TAVILY_REASONS: dict[str, tuple[str, str]] = {
    "web_search_credentials": (
        "search_credential_invalid",
        "Tavily 拒绝了该 API Key（凭据无效）：请核对控制台里的 Key 是否完整、"
        "是否已删除或过期，再重新填入。",
    ),
    "web_search_configuration": (
        "search_credential_invalid",
        "Tavily 拒绝了该 API Key（凭据无效）：请核对控制台里的 Key 是否完整、"
        "是否已删除或过期，再重新填入。",
    ),
    "web_search_rate_limit": (
        "search_rate_limited",
        "Tavily 提示请求过于频繁（429）：请稍后重试。",
    ),
    "web_search_dns": (
        "search_upstream_unreachable",
        "无法解析 Tavily 域名：请检查本机网络与 DNS 设置后重试。",
    ),
    "web_search_connect": (
        "search_upstream_unreachable",
        "无法连接 Tavily：请检查本机网络与代理设置后重试。",
    ),
    "web_search_offline": (
        "search_upstream_unreachable",
        "当前网络不可用，无法连接 Tavily：请检查网络后重试。",
    ),
    "web_search_timeout": (
        "search_upstream_unreachable",
        "连接 Tavily 超时：请检查本机网络与代理设置后重试。",
    ),
    "web_search_provider": (
        "search_upstream_error",
        "Tavily 服务端返回错误：请稍后重试。",
    ),
}


class CredentialStatus(BaseModel):
    """一张卡片的凭据状态（不含任何凭据正文）。"""

    configured: bool
    effective_source: Literal["environment", "credential_store"] | None = None
    last_validated_at: datetime | None = None
    error: str | None = None
    message: str | None = None
    runtime_evidence: str | None = None


class AMapCredentialStatus(BaseModel):
    web_service: CredentialStatus
    browser_map: CredentialStatus


class CredentialSettingsResponse(BaseModel):
    qwen: CredentialStatus
    tavily: CredentialStatus
    amap: AMapCredentialStatus
    store_error: str | None = None


class SecretCandidate(BaseModel):
    api_key: SecretStr | None = None


class AMapBrowserCandidate(BaseModel):
    api_key: SecretStr | None = None
    security_js_code: SecretStr | None = None


def _store(request: Request) -> CredentialStorePort:
    if getattr(request.app.state, "runtime_credential_store_error", False):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "credential_store_unavailable", "message": "凭据存储暂不可用。"},
        )
    store: CredentialStorePort | None = getattr(
        request.app.state, "runtime_credential_store", None
    )
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "credential_store_unavailable", "message": "凭据存储暂不可用。"},
        )
    return store


def _save_credential(request: Request, identifier: str, secret: SecretStr) -> None:
    """把已验证的候选值写入凭据库；失败给出不含秘密的中文错误。"""
    try:
        _store(request).save(identifier, secret)
    except (CredentialStoreError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "credential_store_unavailable",
                "message": "凭据无法安全保存，请检查凭据存储。",
            },
        ) from exc


def _mirror_settings(request: Request, **updates: Any) -> None:
    """把已保存的凭据同步进本进程的运行期 Settings（其他读取方立即看到新值）。"""
    settings = runtime_settings(request)
    if settings is not None:
        request.app.state.settings = settings.model_copy(update=updates)


def _reported_source(source: CredentialSource) -> Literal["environment", "credential_store"] | None:
    if source is CredentialSource.ENVIRONMENT:
        return "environment"
    if source is CredentialSource.CREDENTIAL_STORE:
        return "credential_store"
    return None


def _shadowed_by_environment(
    resolved: ResolvedCredential | ResolvedBrowserMapPair,
) -> bool:
    """该项当前是否由环境变量提供（此时页面保存的值不会生效）。"""
    return resolved.source is CredentialSource.ENVIRONMENT


def _shadow_notice(field_name: str) -> str:
    """环境变量遮蔽凭据库时的中文提示：写明真正生效的是哪一个值与下一步操作。"""
    env_var = secret_environment_source(field_name) or f"BRIDGES_{field_name.upper()}"
    return (
        f"已通过验证并保存到凭据库；但该项当前由环境变量（{env_var}）提供，"
        "环境变量优先，本次保存的值不会生效。若要改用页面保存的值，"
        "请先移除该环境变量（或其 _FILE 引用）并重启 BridGes。"
    )


def _status(
    request: Request,
    name: str,
    *,
    resolved: ResolvedCredential | ResolvedBrowserMapPair,
) -> CredentialStatus:
    snapshot = validation_snapshot(request, name)
    return CredentialStatus(
        configured=resolved.configured,
        effective_source=_reported_source(resolved.source),
        last_validated_at=snapshot.get("last_validated_at"),
        error=snapshot.get("error"),
        runtime_evidence=snapshot.get("runtime_evidence"),
    )


def _invalid_candidate(name: str, message: str, *, reason: str | None = None) -> HTTPException:
    detail: dict[str, Any] = {
        "error": "credential_invalid",
        "credential": name,
        "message": message,
    }
    if reason is not None:
        detail["reason"] = reason
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=detail,
    )


def _probe_unavailable(message: str, *, reason: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "error": "credential_probe_unavailable",
            "message": message,
            "reason": reason,
        },
    )


def _amap_probe_error(
    request: Request, item: str, diagnosis: AmapDiagnosis
) -> HTTPException:
    """把高德诊断投影成 HTTP 结论。

    「探测没跑通」（上游不可达、非 200、无法解析的响应）给 503：这时我们并没有
    拿到"这个值不行"的证据，不能替用户下结论；「上游明确拒绝了这个值」给 422。
    """
    record_validation(request, item, error=diagnosis.message)
    if diagnosis.reason in {
        REASON_UPSTREAM_UNREACHABLE,
        REASON_UPSTREAM_ERROR,
        REASON_BAD_PAYLOAD,
    }:
        return _probe_unavailable(diagnosis.message, reason=diagnosis.reason)
    return _invalid_candidate(item, diagnosis.message, reason=diagnosis.reason)


def _candidate_value(secret: SecretStr | None) -> str:
    return secret.get_secret_value().strip() if secret is not None else ""


def _tavily_diagnosis(result: WebSearchHealth) -> tuple[str, str] | None:
    """把 Tavily 健康检查结论映射为 (分类码, 中文诊断)；READY 时返回 None。"""
    if result.status == WebSearchHealthStatus.READY:
        return None
    code = result.error_code or ""
    known = _TAVILY_REASONS.get(code)
    if known is not None:
        return known
    if result.status == WebSearchHealthStatus.RATE_LIMITED:
        return ("search_rate_limited", "Tavily 提示请求过于频繁（429）：请稍后重试。")
    if result.status == WebSearchHealthStatus.DNS_ERROR:
        return (
            "search_upstream_unreachable",
            "无法解析 Tavily 域名：请检查本机网络与 DNS 设置后重试。",
        )
    if result.status == WebSearchHealthStatus.CONNECT_ERROR:
        return (
            "search_upstream_unreachable",
            "无法连接 Tavily：请检查本机网络与代理设置后重试。",
        )
    if result.status == WebSearchHealthStatus.AUTH_ERROR:
        return (
            "search_credential_invalid",
            "Tavily 拒绝了该 API Key（凭据无效）：请核对控制台里的 Key 是否完整、"
            "是否已删除或过期，再重新填入。",
        )
    return ("search_upstream_error", "Tavily 服务未通过验证：请稍后重试。")


@router.get("", response_model=CredentialSettingsResponse)
def get_credential_settings(
    request: Request, response: Response, subject: SubjectDep
) -> CredentialSettingsResponse:
    """报告四类凭据的配置状态、当前生效来源与最近一次验证结论。"""
    del subject
    response.headers["Cache-Control"] = "no-store"
    resolver = credential_resolver(request)
    return CredentialSettingsResponse(
        qwen=_status(request, QWEN_ITEM, resolved=resolver.qwen_api_key()),
        tavily=_status(request, TAVILY_ITEM, resolved=resolver.tavily_api_key()),
        amap=AMapCredentialStatus(
            web_service=_status(
                request, AMAP_WEB_SERVICE_ITEM, resolved=resolver.amap_web_service_key()
            ),
            browser_map=_status(
                request,
                AMAP_BROWSER_MAP_ITEM,
                resolved=resolver.amap_browser_map_pair(),
            ),
        ),
        store_error=resolver.load_error,
    )


@router.put("/qwen", response_model=CredentialStatus)
def replace_qwen_credential(
    candidate: SecretCandidate, request: Request, subject: SubjectDep
) -> CredentialStatus:
    """验证并替换全局 Qwen 凭据；失败保留旧凭据（V2 Issue 09）。

    验证对象是当前生效的主模型 ID：先查百炼模型元数据（密钥可用且能看到该
    模型），再用候选密钥做一次最小真实调用（密钥能实际调用推理服务）。任一
    不通过都不保存、不改运行期状态；成功后就地轮换运行期密钥，下一次模型
    调用即使用新凭据。

    该密钥看不到当前主模型时，提示指向「Qwen 主模型」卡的同一次迁移操作
    （那里可以同时填入候选模型 ID 与这把新密钥），不再形成互相指向的循环。

    凭据正文绝不进入响应、日志或错误信息；输入框在成功后被前端清空。
    """
    del subject
    key = _candidate_value(candidate.api_key)
    if not key:
        message = "Qwen API Key 不能为空。"
        record_validation(request, QWEN_ITEM, error=message)
        raise _invalid_candidate(
            QWEN_ITEM, message, reason=REASON_CREDENTIAL_MISSING
        )

    secret = SecretStr(key)
    config = active_run_model_config(request)
    try:
        build_metadata_source(request, secret).query(config.model_id)
    except ModelMetadataError as exc:
        record_validation(request, QWEN_ITEM, error=exc.message)
        if exc.code == MODEL_METADATA_ERR_UNAVAILABLE:
            raise _probe_unavailable(exc.message, reason=exc.code) from exc
        if exc.code == MODEL_METADATA_ERR_MODEL_NOT_FOUND:
            message = (
                f"该密钥看不到当前主模型 ID（{config.model_id}）：这正是旧密钥已失效、"
                "而新密钥只覆盖另一批模型时的典型症状。请在下方「Qwen 主模型」卡里"
                "同时填入该密钥可用的模型 ID 与这把新密钥，一次完成「换密钥 + "
                "换主模型」；只换密钥（保留当前主模型）请改用一把能看到该模型的密钥。"
            )
            raise _invalid_candidate(
                QWEN_ITEM, message, reason=REASON_MODEL_NOT_MATCHING
            ) from exc
        raise _invalid_candidate(
            QWEN_ITEM, exc.message, reason=REASON_KEY_REJECTED
        ) from exc

    outcomes = ModelCapabilityProbe(build_probe_client(request, secret)).run(
        model_id=config.model_id, capabilities=ModelCapabilities(text=True)
    )
    failed = [outcome for outcome in outcomes if not outcome.ok]
    if failed:
        message = failed[0].message or "Qwen 密钥验证失败，请检查密钥后重试。"
        record_validation(request, QWEN_ITEM, error=message)
        raise _invalid_candidate(
            QWEN_ITEM, message, reason=REASON_PROBE_FAILED
        )

    _save_credential(request, GLOBAL_QWEN_CREDENTIAL_ID, secret)
    shadowed = qwen_key_shadowed(request)
    if not shadowed:
        apply_qwen_key(request, secret)
    checked_at = record_validation(request, QWEN_ITEM)
    resolved = credential_resolver(request).qwen_api_key()
    return CredentialStatus(
        configured=True,
        effective_source=_reported_source(resolved.source),
        last_validated_at=checked_at,
        message=(
            qwen_key_shadow_notice()
            if shadowed
            else (
                "Qwen 凭据已验证并保存：已核对百炼元数据，并完成一次最小真实调用"
                f"（模型 {config.model_id}）。新密钥对下一次调用立即生效，无需重启。"
            )
        ),
    )


@router.put("/tavily", response_model=CredentialStatus)
def replace_tavily_credential(
    candidate: SecretCandidate, request: Request, subject: SubjectDep
) -> CredentialStatus:
    """验证并替换 Tavily 搜索凭据；失败保留旧凭据。"""
    del subject
    key = _candidate_value(candidate.api_key)
    if not key:
        message = "Tavily API Key 不能为空。"
        record_validation(request, TAVILY_ITEM, error=message)
        raise _invalid_candidate(
            TAVILY_ITEM, message, reason=REASON_CREDENTIAL_MISSING
        )

    probe_client = TavilySearchClient(
        api_key=SecretStr(key),
        http_client=request.app.state.credential_probe_http_client,
        max_results=1,
        fetch_sources=False,
    )
    before = credential_resolver(request).tavily_api_key()
    diagnosis = _tavily_diagnosis(probe_client.health_check())
    if diagnosis is not None:
        reason, message = diagnosis
        record_validation(request, TAVILY_ITEM, error=message)
        if reason == "search_upstream_unreachable":
            raise _probe_unavailable(message, reason=reason)
        raise _invalid_candidate(TAVILY_ITEM, message, reason=reason)

    _save_credential(request, SETTINGS_TAVILY_CREDENTIAL_ID, SecretStr(key))
    shadowed = _shadowed_by_environment(before)
    if not shadowed:
        _mirror_settings(request, tavily_api_key=SecretStr(key))
        web_search_service = getattr(request.app.state, "web_search_service", None)
        if web_search_service is not None:
            web_search_service.replace_tavily_api_key(SecretStr(key))
    checked_at = record_validation(request, TAVILY_ITEM)
    resolved = credential_resolver(request).tavily_api_key()
    return CredentialStatus(
        configured=True,
        effective_source=_reported_source(resolved.source),
        last_validated_at=checked_at,
        message=(
            _shadow_notice("TAVILY_API_KEY")
            if shadowed
            else (
                "Tavily 凭据已验证并保存：已完成一次固定探针搜索，"
                "联网搜索的下一次调用立即使用新凭据，无需重启。"
            )
        ),
    )


@router.put("/amap/web-service", response_model=CredentialStatus)
def replace_amap_web_service_credential(
    candidate: SecretCandidate, request: Request, subject: SubjectDep
) -> CredentialStatus:
    """验证并替换高德 Web 服务 Key；失败按具体原因分类，保留旧凭据。"""
    del subject
    key = _candidate_value(candidate.api_key)
    if not key:
        message = "高德 Web 服务 Key 不能为空。"
        record_validation(request, AMAP_WEB_SERVICE_ITEM, error=message)
        raise _invalid_candidate(
            AMAP_WEB_SERVICE_ITEM, message, reason=REASON_CREDENTIAL_MISSING
        )

    diagnosis = probe_data_service(
        request.app.state.credential_probe_http_client, key=key
    )
    if diagnosis is not None:
        raise _amap_probe_error(request, AMAP_WEB_SERVICE_ITEM, diagnosis)

    before = credential_resolver(request).amap_web_service_key()
    _save_credential(request, AMAP_WEB_SERVICE_CREDENTIAL_ID, SecretStr(key))
    shadowed = _shadowed_by_environment(before)
    if not shadowed:
        _mirror_settings(request, amap_web_service_key=SecretStr(key))
    checked_at = record_validation(request, AMAP_WEB_SERVICE_ITEM)
    resolved = credential_resolver(request).amap_web_service_key()
    return CredentialStatus(
        configured=True,
        effective_source=_reported_source(resolved.source),
        last_validated_at=checked_at,
        message=(
            _shadow_notice("AMAP_WEB_SERVICE_KEY")
            if shadowed
            else (
                "高德 Web 服务凭据已验证并保存：已完成一次真实地理编码请求"
                "（固定公开地址）。服务端路线与地点查询的下一次调用立即使用新值。"
            )
        ),
    )


@router.put("/amap/browser-map", response_model=CredentialStatus)
def replace_amap_browser_map_credential(
    candidate: AMapBrowserCandidate, request: Request, subject: SubjectDep
) -> CredentialStatus:
    """验证并保存浏览器地图凭据对（JS API Key ＋ 安全密钥）。

    验证由两部分组成，且只有第二部分是有效性证据：

    1. **加载器可达性**：只读加载器正文前缀判断可达与形状（真实正文约 968 KB，
       按体积判失败会误杀有效 Key），并说明"拿到脚本不等于 Key 有效"；
    2. **成对真实请求**：按高德官方代理方案，在服务端把 ``jscode``（安全密钥）
       与 JS API Key 一起送到数据服务，用一次真实地理编码请求证明配对可用。

    底图能否在用户浏览器里渲染依赖浏览器侧的真实请求，保存时无法证明，因此
    成功文案只声明已经证明的那件事；浏览器路径的结论由地图代理在首次真实请求
    后回写（``runtime_evidence``）。
    """
    del subject
    key = _candidate_value(candidate.api_key)
    security_code = _candidate_value(candidate.security_js_code)
    if not key or not security_code:
        message = "高德浏览器地图 Key 和安全码都不能为空。"
        record_validation(request, AMAP_BROWSER_MAP_ITEM, error=message)
        raise _invalid_candidate(
            AMAP_BROWSER_MAP_ITEM, message, reason=REASON_CREDENTIAL_MISSING
        )

    probe_http = request.app.state.credential_probe_http_client
    loader = probe_loader(probe_http, key)
    if not loader.reachable:
        assert loader.diagnosis is not None
        raise _amap_probe_error(request, AMAP_BROWSER_MAP_ITEM, loader.diagnosis)

    diagnosis = probe_data_service(
        probe_http, key=key, security_code=security_code
    )
    if diagnosis is not None:
        raise _amap_probe_error(request, AMAP_BROWSER_MAP_ITEM, diagnosis)

    pair = json.dumps(
        {"api_key": key, "security_js_code": security_code},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    before = credential_resolver(request).amap_browser_map_pair()
    _save_credential(request, AMAP_BROWSER_MAP_CREDENTIAL_ID, SecretStr(pair))
    shadowed = _shadowed_by_environment(before)
    if not shadowed:
        _mirror_settings(
            request,
            amap_js_api_key=SecretStr(key),
            amap_security_js_code=SecretStr(security_code),
        )
    checked_at = record_validation(request, AMAP_BROWSER_MAP_ITEM)
    resolved = credential_resolver(request).amap_browser_map_pair()
    return CredentialStatus(
        configured=True,
        effective_source=_reported_source(resolved.source),
        last_validated_at=checked_at,
        message=(
            _shadow_notice("AMAP_JS_API_KEY")
            if shadowed
            else (
                "JS API Key 与安全码已保存，并完成一次成对的真实数据服务请求"
                "（服务端追加 jscode 的地理编码请求）；地图加载器可达。"
                "底图能否在你的浏览器中渲染，会在首次打开地图时由真实请求确认并回写状态。"
            )
        ),
    )
