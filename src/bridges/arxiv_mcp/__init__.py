"""BridGes 内置、只读、受限 arXiv MCP。"""

from bridges.arxiv_mcp.client import ArxivMcpClient
from bridges.arxiv_mcp.contracts import (
    ArxivPaper,
    ArxivPaperProjection,
    ArxivSearchProjection,
    ArxivSearchStatus,
)
from bridges.arxiv_mcp.errors import ArxivMcpError

__all__ = [
    "ArxivMcpClient",
    "ArxivMcpError",
    "ArxivPaper",
    "ArxivPaperProjection",
    "ArxivSearchProjection",
    "ArxivSearchStatus",
]
