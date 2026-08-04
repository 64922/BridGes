"""内置 arXiv MCP 的固定版本与最小权限清单。"""

from __future__ import annotations

from urllib.parse import urlparse

from pydantic import BaseModel, Field


class ArxivPermissionManifest(BaseModel):
    """MCP 启动前可审计的权限声明；空集合即默认拒绝。"""

    name: str = Field(description="内置能力名称。")
    version: str = Field(description="固定能力版本。")
    network_domains: list[str] = Field(default_factory=list)
    filesystem_read: list[str] = Field(default_factory=list)
    filesystem_write: list[str] = Field(default_factory=list)
    external_commands: list[str] = Field(default_factory=list)
    secret_names: list[str] = Field(default_factory=list)
    data_categories: list[str] = Field(default_factory=list)
    read_only: bool = True
    isolated_process: bool = True


def builtin_arxiv_manifest() -> ArxivPermissionManifest:
    """返回固定的 arXiv 只读权限，不从环境或用户配置扩展。"""

    return ArxivPermissionManifest(
        name="builtin-arxiv-paper-search",
        version="2026.08.04",
        network_domains=["export.arxiv.org"],
        data_categories=["public_query_terms"],
    )


def assert_registered_arxiv_url(url: str) -> None:
    """只允许访问登记的 HTTPS arXiv API 主机。"""

    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"export.arxiv.org"}:
        raise PermissionError("arXiv MCP 只允许访问登记的 export.arxiv.org HTTPS 端点。")
