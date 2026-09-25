"""搜索、地图与主模型凭据的已认证设置路由。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, SecretStr

from bridges.ai.model_metadata import (
    MODEL_METADATA_ERR_MODEL_NOT_FOUND,
    MODEL_METADATA_ERR_UNAVAILABLE,
    ModelMetadataError,
)
from bridges.ai.model_probe import ModelCapabilityProbe
from bridges.api.auth import SubjectDep
from bridges.api.qwen_settings import (
    active_qwen_key,
    active_run_model_config,
    apply_qwen_key,
    build_metadata_source,
    build_probe_client,
)
from bridges.contracts.ai import ModelCapabilities
from bridges.credentials.ids import (
    AMAP_BROWSER_MAP_CREDENTIAL_ID,
    AMAP_WEB_SERVICE_CREDENTIAL_ID,
    GLOBAL_QWEN_CREDENTIAL_ID,
    RUNTIME_TAVILY_CREDENTIAL_ID,
    SETTINGS_TAVILY_CREDENTIAL_ID,
)
from bridges.credentials.store import CredentialStoreError, CredentialStorePort
from bridges.web_search.contracts import WebSearchHealthStatus
from bridges.web_search.tavily import TavilySearchClient

router = APIRouter(prefix="/settings/credentials", tags=["凭据设置"])

_AMAP_GEOCODE_ENDPOINT = "https://restapi.amap.com/v3/geocode/geo"
_AMAP_JS_API_ENDPOINT = "https://webapi.amap.com/maps"
_AMAP_JS_AUTH_ERRORS = (
    "INVALID_USER_KEY",
    "INVALID_USER_SCODE",
    "INVALID_USER_DOMAIN",
    "USERKEY_PLAT_NOMATCH",
)
#: Qwen 密钥候选被拒绝的原因分类（前端据此在字段附近给出操作顺序提示）。
_REASON_KEY_REJECTED = "key_rejected"
_REASON_MODEL_NOT_MATCHING = "model_not_matching_key"
_REASON_PROBE_FAILED = "probe_failed"


class CredentialStatus(BaseModel):
    configured: bool
    last_validated_at: datetime | None = None
    error: str | None = None


class AMapCredentialStatus(BaseModel):
    web_service: CredentialStatus
    browser_map: CredentialStatus


class CredentialSettingsResponse(BaseModel):
    qwen: CredentialStatus
    tavily: CredentialStatus
    amap: AMapCredentialStatus


class SecretCandidate(BaseModel):
    api_key: SecretStr


class AMapBrowserCandidate(BaseModel):
    api_key: SecretStr
    security_js_code: SecretStr


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


def _configured(request: Request, field_name: str, *credential_ids: str) -> bool:
    settings = getattr(request.app.state, "settings", None)
    configured = getattr(settings, field_name, None) is not None
    if configured:
        return True
    try:
        store = _store(request)
        return any(store.get(identifier) is not None for identifier in credential_ids)
    except (CredentialStoreError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "credential_store_unavailable", "message": "凭据存储暂不可用。"},
        ) from exc


def _validation_state(request: Request) -> dict[str, dict[str, Any]]:
    state: dict[str, dict[str, Any]] | None = getattr(
        request.app.state, "credential_validation_state", None
    )
    if state is None:
        state = {}
        request.app.state.credential_validation_state = state
    return state


def _status(
    request: Request, name: str, *, configured: bool
) -> CredentialStatus:
    validation = _validation_state(request).get(name, {})
    return CredentialStatus(
        configured=configured,
        last_validated_at=validation.get("last_validated_at"),
        error=validation.get("error"),
    )


def _amap_browser_map_configured(request: Request) -> bool:
    settings = getattr(request.app.state, "settings", None)
    has_environment_pair = (
        getattr(settings, "amap_js_api_key", None) is not None
        and getattr(settings, "amap_security_js_code", None) is not None
    )
    try:
        return has_environment_pair or _store(request).get(
            AMAP_BROWSER_MAP_CREDENTIAL_ID
        ) is not None
    except (CredentialStoreError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "credential_store_unavailable", "message": "凭据存储暂不可用。"},
        ) from exc


def _record_validation(
    request: Request, name: str, *, error: str | None = None
) -> datetime:
    checked_at = datetime.now(UTC)
    _validation_state(request)[name] = {
        "last_validated_at": checked_at,
        "error": error,
    }
    return checked_at


def _invalid_candidate(
    name: str, message: str, *, reason: str | None = None
) -> HTTPException:
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


def _probe_unavailable(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"error": "credential_probe_unavailable", "message": message},
    )


def _candidate_value(secret: SecretStr) -> str:
    return secret.get_secret_value().strip()


def _probe_amap_web_service(
    client: httpx.Client, key: str
) -> bool:
    try:
        response = client.get(
            _AMAP_GEOCODE_ENDPOINT,
            params={
                "key": key,
                "address": "华东交通大学南昌校区",
                "city": "南昌",
                "output": "JSON",
            },
            follow_redirects=False,
        )
        if response.status_code != 200:
            return False
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("status") == "1"
        and payload.get("infocode") == "10000"
    )


def _probe_amap_browser_map(client: httpx.Client, key: str) -> bool:
    """通过 JS API 加载器检查 Key；安全码不会发送给浏览器加载器。"""
    try:
        with client.stream(
            "GET",
            _AMAP_JS_API_ENDPOINT,
            params={"v": "2.0", "key": key},
            follow_redirects=False,
        ) as response:
            if response.status_code != 200:
                return False
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > 262_144:
                    return False
    except httpx.HTTPError:
        return False
    script = body.decode("utf-8", errors="replace")
    return bool(script) and not any(error in script for error in _AMAP_JS_AUTH_ERRORS)


@router.get("", response_model=CredentialSettingsResponse)
def get_credential_settings(
    request: Request, response: Response, subject: SubjectDep
) -> CredentialSettingsResponse:
    del subject
    response.headers["Cache-Control"] = "no-store"
    return CredentialSettingsResponse(
        qwen=_status(
            request, "qwen", configured=active_qwen_key(request) is not None
        ),
        tavily=_status(
            request,
            "tavily",
            configured=_configured(
                request,
                "tavily_api_key",
                RUNTIME_TAVILY_CREDENTIAL_ID,
                SETTINGS_TAVILY_CREDENTIAL_ID,
            ),
        ),
        amap=AMapCredentialStatus(
            web_service=_status(
                request,
                "amap_web_service",
                configured=_configured(
                    request, "amap_web_service_key", AMAP_WEB_SERVICE_CREDENTIAL_ID
                ),
            ),
            browser_map=_status(
                request,
                "amap_browser_map",
                configured=_amap_browser_map_configured(request),
            ),
        ),
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

    凭据正文绝不进入响应、日志或错误信息；输入框在成功后被前端清空。
    """
    del subject
    key = _candidate_value(candidate.api_key)
    if not key:
        message = "Qwen API Key 不能为空。"
        _record_validation(request, "qwen", error=message)
        raise _invalid_candidate("qwen", message, reason=_REASON_KEY_REJECTED)

    secret = SecretStr(key)
    config = active_run_model_config(request)
    try:
        build_metadata_source(request, secret).query(config.model_id)
    except ModelMetadataError as exc:
        _record_validation(request, "qwen", error=exc.message)
        if exc.code == MODEL_METADATA_ERR_UNAVAILABLE:
            raise _probe_unavailable(exc.message) from exc
        if exc.code == MODEL_METADATA_ERR_MODEL_NOT_FOUND:
            message = (
                f"该密钥看不到当前主模型 ID（{config.model_id}）。"
                "请先更换为可访问该模型的密钥，或在下方「Qwen 主模型 ID」中改填"
                "该密钥可用的模型，再回来验证密钥。"
            )
            raise _invalid_candidate(
                "qwen", message, reason=_REASON_MODEL_NOT_MATCHING
            ) from exc
        raise _invalid_candidate(
            "qwen", exc.message, reason=_REASON_KEY_REJECTED
        ) from exc

    outcomes = ModelCapabilityProbe(build_probe_client(request, secret)).run(
        model_id=config.model_id, capabilities=ModelCapabilities(text=True)
    )
    failed = [outcome for outcome in outcomes if not outcome.ok]
    if failed:
        message = failed[0].message or "Qwen 密钥验证失败，请检查密钥后重试。"
        _record_validation(request, "qwen", error=message)
        raise _invalid_candidate("qwen", message, reason=_REASON_PROBE_FAILED)

    try:
        _store(request).save(GLOBAL_QWEN_CREDENTIAL_ID, secret)
    except (CredentialStoreError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "credential_store_unavailable",
                "message": "凭据无法安全保存，请检查凭据存储。",
            },
        ) from exc

    apply_qwen_key(request, secret)
    checked_at = _record_validation(request, "qwen")
    return CredentialStatus(configured=True, last_validated_at=checked_at)


@router.put("/tavily", response_model=CredentialStatus)
def replace_tavily_credential(
    candidate: SecretCandidate, request: Request, subject: SubjectDep
) -> CredentialStatus:
    del subject
    key = _candidate_value(candidate.api_key)
    if not key:
        message = "Tavily API Key 不能为空。"
        _record_validation(request, "tavily", error=message)
        raise _invalid_candidate("tavily", message)

    probe_client = TavilySearchClient(
        api_key=SecretStr(key),
        http_client=request.app.state.credential_probe_http_client,
        max_results=1,
        fetch_sources=False,
    )
    result = probe_client.health_check()
    if result.status != WebSearchHealthStatus.READY:
        message = "Tavily 验证失败，请检查密钥和账户权限。"
        _record_validation(request, "tavily", error=message)
        raise _invalid_candidate("tavily", message)

    try:
        _store(request).save(SETTINGS_TAVILY_CREDENTIAL_ID, SecretStr(key))
    except (CredentialStoreError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "credential_store_unavailable",
                "message": "凭据无法安全保存，请检查凭据存储。",
            },
        ) from exc

    app_settings = request.app.state.settings
    if app_settings is not None:
        request.app.state.settings = app_settings.model_copy(
            update={"tavily_api_key": SecretStr(key)}
        )
    web_search_service = getattr(request.app.state, "web_search_service", None)
    if web_search_service is not None:
        web_search_service.replace_tavily_api_key(SecretStr(key))
    checked_at = _record_validation(request, "tavily")
    return CredentialStatus(configured=True, last_validated_at=checked_at)


@router.put("/amap/web-service", response_model=CredentialStatus)
def replace_amap_web_service_credential(
    candidate: SecretCandidate, request: Request, subject: SubjectDep
) -> CredentialStatus:
    del subject
    key = _candidate_value(candidate.api_key)
    if not key:
        message = "高德 Web 服务 Key 不能为空。"
        _record_validation(request, "amap_web_service", error=message)
        raise _invalid_candidate("amap_web_service", message)

    if not _probe_amap_web_service(request.app.state.credential_probe_http_client, key):
        message = "高德 Web 服务 Key 验证失败，请检查密钥、服务权限和额度。"
        _record_validation(request, "amap_web_service", error=message)
        raise _invalid_candidate("amap_web_service", message)

    try:
        _store(request).save(AMAP_WEB_SERVICE_CREDENTIAL_ID, SecretStr(key))
    except (CredentialStoreError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "credential_store_unavailable",
                "message": "凭据无法安全保存，请检查凭据存储。",
            },
        ) from exc
    checked_at = _record_validation(request, "amap_web_service")
    app_settings = request.app.state.settings
    if app_settings is not None:
        request.app.state.settings = app_settings.model_copy(
            update={"amap_web_service_key": SecretStr(key)}
        )
    return CredentialStatus(configured=True, last_validated_at=checked_at)


@router.put("/amap/browser-map", response_model=CredentialStatus)
def replace_amap_browser_map_credential(
    candidate: AMapBrowserCandidate, request: Request, subject: SubjectDep
) -> CredentialStatus:
    del subject
    key = _candidate_value(candidate.api_key)
    security_code = _candidate_value(candidate.security_js_code)
    if not key or not security_code:
        message = "高德浏览器地图 Key 和安全码都不能为空。"
        _record_validation(request, "amap_browser_map", error=message)
        raise _invalid_candidate("amap_browser_map", message)

    if not _probe_amap_browser_map(
        request.app.state.credential_probe_http_client, key
    ):
        message = "高德 JS API Key 验证失败，请检查密钥和 Web 平台配置。"
        _record_validation(request, "amap_browser_map", error=message)
        raise _invalid_candidate("amap_browser_map", message)

    pair = json.dumps(
        {"api_key": key, "security_js_code": security_code},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    try:
        _store(request).save(AMAP_BROWSER_MAP_CREDENTIAL_ID, SecretStr(pair))
    except (CredentialStoreError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "credential_store_unavailable",
                "message": "凭据无法安全保存，请检查凭据存储。",
            },
        ) from exc
    checked_at = _record_validation(request, "amap_browser_map")
    app_settings = request.app.state.settings
    if app_settings is not None:
        request.app.state.settings = app_settings.model_copy(
            update={
                "amap_js_api_key": SecretStr(key),
                "amap_security_js_code": SecretStr(security_code),
            }
        )
    return CredentialStatus(configured=True, last_validated_at=checked_at)
