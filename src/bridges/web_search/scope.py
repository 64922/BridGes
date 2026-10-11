"""把查询里的站点边界落实为提供方域名限制与读取前的 URL 校验。"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_SITE = re.compile(r"(?:^|\s)site:([A-Za-z0-9.-]+)(/[^\s]*)?", re.IGNORECASE)


def query_scopes(query: str) -> tuple[tuple[str, str], ...]:
    """保留站点操作符的域名与路径；路径约束由本地再次核对。"""
    return tuple((match[1].lower(), match[2] or "") for match in _SITE.finditer(query))


def url_in_scopes(url: str, scopes: tuple[tuple[str, str], ...]) -> bool:
    """先核对真实主机，再核对路径；查询字符串里的域名不构成归属。"""
    if not scopes:
        return True
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and any(
        (host == domain or host.endswith(f".{domain}")) and parsed.path.startswith(path)
        for domain, path in scopes
    )
