"""校园通勤模块的浏览器地图运行时配置与高德代理（V2 Issue 12）。

`docs/v2/architecture.md` 第 7 节要求：高德浏览器地图的**安全密钥由后端保护**，
按高德官方代理方案使用，**不能直接内嵌到静态 JS**。因此：

- ``GET /commute/map-config`` 只下发前端加载地图所需的 **JS API Key**（它本身
  必然出现在加载器 URL 里，属于公开字段）与**代理路径**；安全密钥永不下发；
- ``GET /commute/amap-proxy/{path}`` 把高德数据服务请求（仅 ``v3``／``v5``）
  转发到 ``restapi.amap.com``，在服务端追加 ``jscode``。浏览器只看到自己的
  后端地址，安全密钥不出服务器。

代理只放行高德数据服务的两个版本前缀，不做通用转发；上游失败只回可操作的中文
提示，绝不回显安全密钥或上游原始正文。
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, SecretStr

from bridges.api.auth import SubjectDep
from bridges.commute.sources import AMAP_REST_BASE
from bridges.credentials.store import CredentialStoreError

router = APIRouter(prefix="/commute", tags=["校园通勤"])

#: 允许代理的路径前缀（只放行高德数据服务；地址与模块共用同一常量）。
AMAP_PROXY_PATH_PREFIXES = ("v3/", "v5/")

#: 前端据此拼出绝对地址作为 JS API 的 ``serviceHost``；**相对 API 基地址**
#: （前端默认基地址为 ``/api``，Next 重写会剥掉该前缀），因此这里不带 ``/api``。
AMAP_PROXY_PATH = "/commute/amap-proxy"

PROXY_TIMEOUT_SECONDS = 10.0
MAX_PROXY_RESPONSE_BYTES = 262_144

#: 浏览器地图缺少凭据时的中文指引（含操作顺序）。
MAP_NOT_CONFIGURED_NOTICE = (
    "未配置高德浏览器地图凭据：路线、距离与耗时仍来自高德路线服务并照常显示，"
    "但地图底图不可用。请在设置页「密钥与模型管理 → 高德凭据」中填写浏览器地图 "
    "Key 与安全密钥后重试。"
)

#: 只有 JS Key、没有安全密钥时的提示（新 Key 必须配安全密钥）。
MAP_SECURITY_MISSING_NOTICE = (
    "已配置浏览器地图 Key，但缺少安全密钥：2021-12-02 之后新建的高德 JS API Key "
    "必须配安全密钥，否则地图加载会失败。请在同一设置项中补填安全密钥。"
)


class MapConfigResponse(BaseModel):
    """浏览器地图的运行时配置（不含任何安全密钥正文）。"""

    configured: bool
    js_api_key: str | None = None
    service_host_path: str | None = Field(
        default=None, description="代理路径（相对 API 基地址；前端据此拼出绝对地址）。"
    )
    security_code_configured: bool = False
    notice: str | None = None


@router.get("/map-config", response_model=MapConfigResponse)
def get_map_config(
    request: Request, response: Response, subject: SubjectDep
) -> MapConfigResponse:
    """返回浏览器地图的加载配置；每次都实时读取当前生效的凭据。"""
    del subject
    response.headers["Cache-Control"] = "no-store"
    js_key = _secret(request, "amap_js_api_key")
    security_code = _secret(request, "amap_security_js_code")
    if not js_key:
        return MapConfigResponse(configured=False, notice=MAP_NOT_CONFIGURED_NOTICE)
    if not security_code:
        return MapConfigResponse(
            configured=True,
            js_api_key=js_key,
            service_host_path=None,
            security_code_configured=False,
            notice=MAP_SECURITY_MISSING_NOTICE,
        )
    return MapConfigResponse(
        configured=True,
        js_api_key=js_key,
        service_host_path=AMAP_PROXY_PATH,
        security_code_configured=True,
    )


@router.get("/amap-proxy/{path:path}")
def amap_proxy(path: str, request: Request, subject: SubjectDep) -> Response:
    """代理高德数据服务请求并在服务端追加安全密钥（官方代理方案）。"""
    del subject
    if not path.startswith(AMAP_PROXY_PATH_PREFIXES):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "amap_proxy_path_not_allowed", "message": "该路径不在代理范围内。"},
        )
    security_code = _secret(request, "amap_security_js_code")
    if not security_code:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "amap_security_code_missing", "message": MAP_SECURITY_MISSING_NOTICE},
        )
    params: dict[str, Any] = dict(request.query_params)
    # 客户端传入的 jscode 一律忽略：安全密钥只由服务端追加，浏览器侧拿不到。
    params["jscode"] = security_code
    client: httpx.Client | None = getattr(
        request.app.state, "credential_probe_http_client", None
    )
    try:
        if client is not None:
            upstream = client.get(
                f"{AMAP_REST_BASE}/{path}",
                params=params,
                timeout=PROXY_TIMEOUT_SECONDS,
                follow_redirects=False,
            )
        else:  # pragma: no cover - 组合根总会挂载探测客户端
            upstream = httpx.get(
                f"{AMAP_REST_BASE}/{path}",
                params=params,
                timeout=PROXY_TIMEOUT_SECONDS,
                follow_redirects=False,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "amap_proxy_unavailable",
                "message": "无法连接高德服务，请检查网络后重试。",
            },
        ) from exc
    if len(upstream.content) > MAX_PROXY_RESPONSE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "amap_proxy_response_too_large",
                "message": "高德返回内容超出可处理大小。",
            },
        )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )


def secret_setting(settings: object, field: str) -> str | None:
    """读取当前生效的凭据字段；凭据库不可读时按未配置处理（不升级为请求失败）。

    组合根（加载高德路线 Key 的客户端）与本路由共用同一实现，避免两处各写
    一份读取规则而在异常处理上走样。
    """
    if settings is None:
        return None
    try:
        value = getattr(settings, field, None)
    except (CredentialStoreError, OSError):
        return None
    if not isinstance(value, SecretStr):
        return None
    secret = value.get_secret_value().strip()
    return secret or None


def _secret(request: Request, field: str) -> str | None:
    return secret_setting(getattr(request.app.state, "settings", None), field)
