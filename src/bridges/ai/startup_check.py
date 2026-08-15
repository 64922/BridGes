"""启动连通性自检（Issue 03）：DNS 预检 / 代理环境变量 / workspace 形态。

非阻塞：任何异常只折叠为可操作警告（warning 日志），绝不抛出、不阻断
启动；不记录 API Key 等敏感信息（主机名与配置形态不属敏感字段）。
test 环境不执行（确定性适配器驱动），由装配处门控。
"""

from __future__ import annotations

import logging
import os
import re
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from bridges.ai.qwen_client import qwen_base_url_host
from bridges.config import Settings

logger = logging.getLogger(__name__)

#: workspace id 必须是合法 DNS 标签（小写字母数字与连字符，首尾不为连字符）。
_WORKSPACE_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

#: 需要参与校验的代理环境变量（httpx ``trust_env`` 默认读取的大小写形态）。
_PROXY_ENV_VARS = (
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "ALL_PROXY",
    "all_proxy",
)

#: 受支持的代理协议（httpx 支持的形态）。
_SUPPORTED_PROXY_SCHEMES = {"http", "https", "socks4", "socks5", "socks5h"}

Resolver = Callable[[str, int], list[Any]]


def check_qwen_startup_connectivity(
    settings: Settings, *, resolver: Resolver = socket.getaddrinfo
) -> list[str]:
    """非阻塞连通性自检，返回可操作警告列表（不抛出、不含敏感信息）。

    - DNS 预检：解析 ``base_url`` 主机名（默认 ``socket.getaddrinfo``，
      测试可注入假 resolver）；
    - 代理环境变量：存在时校验协议前缀与受支持协议；
    - ``BRIDGES_QWEN_WORKSPACE_ID`` 形态：必须是合法 DNS 标签。

    正常环境返回空列表；每类失败最多一条警告，绝不记录 Key。
    """
    warnings: list[str] = []
    host = qwen_base_url_host(settings.qwen_workspace_id, settings.qwen_region)
    try:
        resolver(host, 443)
    except OSError as exc:
        warnings.append(
            f"启动自检：无法解析 Qwen 服务域名 {host}，请检查 DNS 或代理设置"
            f"（{exc.__class__.__name__}）。"
        )
    except Exception as exc:  # noqa: BLE001 - 自检绝不阻断启动，一律折叠为警告
        warnings.append(
            f"启动自检：DNS 预检异常（{exc.__class__.__name__}），"
            "请检查网络与 DNS 配置。"
        )
    if settings.qwen_workspace_id and not _WORKSPACE_ID_RE.match(settings.qwen_workspace_id):
        warnings.append(
            "启动自检：BRIDGES_QWEN_WORKSPACE_ID 形态异常（仅允许小写字母、"
            "数字与连字符），将拼出不可达主机名，请检查配置。"
        )
    for var in _PROXY_ENV_VARS:
        value = os.environ.get(var)
        if not value:
            continue
        parts = urlsplit(value)
        # "localhost:8080" 会被 urlsplit 误解析为 scheme="localhost"；
        # 缺少 "://" 且无 netloc 视为缺协议前缀。
        missing_prefix = not parts.scheme or (
            not parts.netloc and "://" not in value
        )
        if missing_prefix:
            warnings.append(
                f"启动自检：代理环境变量 {var} 缺少协议前缀（如 http://），"
                "Qwen 连接可能失败。"
            )
        elif parts.scheme not in _SUPPORTED_PROXY_SCHEMES:
            warnings.append(
                f"启动自检：代理环境变量 {var} 使用不受支持的协议 {parts.scheme!r}，"
                "Qwen 连接可能失败。"
            )
    return warnings


def log_qwen_startup_connectivity_warnings(settings: Settings) -> None:
    """以 warning 级别输出自检警告（供启动装配处调用；自身不抛出）。"""
    for warning in check_qwen_startup_connectivity(settings):
        logger.warning(warning)
