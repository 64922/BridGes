"""BridGes 内置、只读、受限 arXiv MCP。"""

from bridges.arxiv_mcp.client import ArxivMcpClient, ArxivMcpError
from bridges.arxiv_mcp.contracts import (
    ArxivPaper,
    ArxivPaperProjection,
    ArxivSearchProjection,
    ArxivSearchStatus,
)

__all__ = [
    "ArxivMcpClient",
    "ArxivMcpError",
    "ArxivPaper",
    "ArxivPaperProjection",
    "ArxivSearchProjection",
    "ArxivSearchStatus",
]
