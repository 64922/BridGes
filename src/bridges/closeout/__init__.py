"""收尾发布门的显式测试协议 fixture。"""

from bridges.closeout.fixtures import (
    CloseoutArxivClient,
    CloseoutQwenAdapter,
    CloseoutWebSearchClient,
)

__all__ = ["CloseoutArxivClient", "CloseoutQwenAdapter", "CloseoutWebSearchClient"]
