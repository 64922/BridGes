"""搜索和地图凭据的已认证设置路由。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, SecretStr

from bridges.api.auth import SubjectDep
from bridges.credentials.ids import (
    AMAP_BROWSER_MAP_CREDENTIAL_ID,
    AMAP_WEB_SERVICE_CREDENTIAL_ID,
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


class CredentialStatus(BaseModel):
    configured: bool
    last_validated_at: datetime | None = None
    error: str | None = None


class AMapCredentialStatus(BaseModel):
    web_service: CredentialStatus
    browser_map: CredentialStatus


class CredentialSettingsResponse(BaseModel):
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


def _invalid_candidate(name: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"error": "credential_invalid", "credential": name, "message": message},
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
