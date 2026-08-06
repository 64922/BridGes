"""跨站请求伪造（CSRF）来源校验中间件（Issue 39 AC2）。

会话 Cookie 为 SameSite=Lax，浏览器对跨站表单与脚本请求默认不携带
Cookie；本中间件在此基础上做纵深防御：对一切改变状态的请求（POST/
PUT/PATCH/DELETE），当请求携带 ``Origin`` 或 ``Referer`` 时，来源必须
落在允许来源集合内，否则 403 拒绝——跨站页面发起的表单/脚本请求
（浏览器必定携带与页面同源的 ``Origin``）不能成功。

允许来源集合的确定顺序：

1. 显式配置 ``BRIDGES_ALLOWED_ORIGINS``（逗号分隔）时只匹配该集合
   （反向代理/容器部署时浏览器来源与 API 的 Host 不同，必须显式配置）；
2. 未配置时优先取 ``X-Forwarded-Proto`` + ``X-Forwarded-Host`` 推导的
   来源（受信反代转发原始 Host 的场景）；
3. 否则取请求自身 scheme + Host；
4. 环回兜底：请求 Host 与来源 Host 均为环回地址（localhost/127.0.0.1/
   ::1）时视为同站点放行——本地开发经 Next.js 代理（浏览器来源
   localhost:3000，API Host 127.0.0.1:8000）无需配置即可工作，而
   恶意远程页面（Origin 为外部域名或外部 IP）仍被拒绝；会话 Cookie
   是主机域 Cookie，跨端口环回页面无法携带它，故不放宽威胁面。

两个头部均缺失时按非浏览器客户端放行（curl/TestClient 等不会自动
携带 Cookie 执行跨站攻击；浏览器跨站改变状态请求必然携带 Origin）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})

#: 改变状态的 HTTP 方法；GET/HEAD/OPTIONS 视为只读，不校验来源。
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_REJECT_MESSAGE = "请求来源不受信任，已拒绝（CSRF 防护）。"


def _origin_of(scheme: str, host: str) -> str:
    """按 RFC 6454 语义构造来源（默认端口省略，与浏览器 Origin 一致）。

    Host 头显式携带默认端口（如 ``example.com:80``）时必须剥离，否则
    与浏览器 Origin（默认端口省略）比较失败，合法同源请求被误拒。
    """
    default_port = ":80" if scheme == "http" else ":443" if scheme == "https" else ""
    if default_port and host.endswith(default_port):
        host = host[: -len(default_port)]
    return f"{scheme}://{host}"


def _host_is_loopback(host: str) -> bool:
    hostname = host.split(":", 1)[0].strip("[]").lower()
    return hostname in _LOOPBACK_HOSTS


def _normalize_origin(value: str) -> str | None:
    """把请求头中的来源值归一化为 scheme://host（去掉默认端口）。"""
    value = value.strip()
    if not value or value == "null":
        return None
    if "://" not in value:
        return None
    scheme, _, rest = value.partition("://")
    if scheme not in ("http", "https"):
        return None
    host = rest.split("/", 1)[0]
    if not host:
        return None
    return _origin_of(scheme, host)


class CsrfOriginMiddleware(BaseHTTPMiddleware):
    """校验改变状态请求的来源；不匹配时 403 拒绝并给出可恢复说明。"""

    def __init__(self, app: Any, *, allowed_origins: list[str] | None = None) -> None:
        super().__init__(app)
        self._allowed_origins = [origin for origin in (allowed_origins or []) if origin]

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method in _UNSAFE_METHODS:
            rejection = self._validate(request)
            if rejection is not None:
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": {
                            "error": "csrf_origin_rejected",
                            "message": rejection,
                        }
                    },
                    headers={"Cache-Control": "no-store"},
                )
        return await call_next(request)

    def _validate(self, request: Request) -> str | None:
        origin = _normalize_origin(request.headers.get("origin", ""))
        if origin is None:
            referer = request.headers.get("referer", "")
            if referer:
                origin = _referer_origin(referer)
        if origin is None:
            # 非浏览器客户端（两个来源头均缺失）：无 Cookie 自动携带场景，
            # 不属于跨站表单/脚本攻击面，放行。
            return None
        if self._origin_allowed(request, origin):
            return None
        return _REJECT_MESSAGE

    def _origin_allowed(self, request: Request, origin: str) -> bool:
        if self._allowed_origins:
            return origin in self._allowed_origins
        forwarded = self._forwarded_origin(request)
        if forwarded is not None and origin == forwarded:
            return True
        host_origin = _origin_of(request.url.scheme or "http", request.headers.get("host", ""))
        if origin == host_origin:
            return True
        # 环回兜底：请求 Host 与来源 Host 均为环回地址时放行（本地开发
        # 经 Next.js 代理，浏览器来源端口与 API 端口不同）。
        request_host = request.headers.get("host", "")
        origin_host = origin.partition("://")[2]
        return bool(
            origin_host and _host_is_loopback(request_host) and _host_is_loopback(origin_host)
        )

    @staticmethod
    def _forwarded_origin(request: Request) -> str | None:
        forwarded_host = request.headers.get("x-forwarded-host", "")
        forwarded_scheme = request.headers.get("x-forwarded-proto", "")
        if not forwarded_host or not forwarded_scheme:
            return None
        scheme = forwarded_scheme.split(",")[0].strip()
        host = forwarded_host.split(",")[0].strip()
        if scheme not in ("http", "https") or not host:
            return None
        return _origin_of(scheme, host)


def _referer_origin(referer: str) -> str | None:
    """从完整 Referer URL 提取来源（scheme://host）。"""
    try:
        from urllib.parse import urlsplit

        parsed = urlsplit(referer)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    return _origin_of(parsed.scheme, parsed.hostname)


__all__ = ["CsrfOriginMiddleware"]
