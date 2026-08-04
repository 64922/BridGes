"""arXiv MCP 的受限 worker 进程适配器。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import suppress
from datetime import datetime
from typing import Any

from bridges.arxiv_mcp.client import ArxivMcpError
from bridges.arxiv_mcp.contracts import ArxivPaper


class ArxivMcpProcessClient:
    """通过 JSONL 与固定模块 worker 通信，避免 MCP 继承应用权限。"""

    def __init__(self, *, python_executable: str | None = None) -> None:
        self._python_executable = python_executable or sys.executable
        self._process: subprocess.Popen[str] | None = None

    def search(self, query: str, *, max_results: int = 5) -> list[ArxivPaper]:
        process = self._ensure_process()
        if process.stdin is None or process.stdout is None:
            raise ArxivMcpError("arxiv_startup", "arXiv 搜索服务启动失败，请重试。")
        request = json.dumps(
            {"query": query, "max_results": max_results}, ensure_ascii=False
        )
        try:
            process.stdin.write(request + "\n")
            process.stdin.flush()
            line = process.stdout.readline()
        except (OSError, ValueError) as exc:
            self.close()
            raise ArxivMcpError("arxiv_startup", "arXiv 搜索服务启动失败，请重试。") from exc
        if not line:
            self.close()
            raise ArxivMcpError("arxiv_startup", "arXiv 搜索服务启动失败，请重试。")
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ArxivMcpError("arxiv_parse", "arXiv worker 返回内容损坏，请重试。") from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            code = payload.get("code") if isinstance(payload, dict) else None
            message = payload.get("message") if isinstance(payload, dict) else None
            raise ArxivMcpError(
                str(code or "arxiv_startup"),
                str(message or "arXiv 搜索服务启动失败，请重试。"),
                permission=code == "arxiv_permission",
            )
        raw_papers = payload.get("papers")
        if not isinstance(raw_papers, list):
            raise ArxivMcpError("arxiv_parse", "arXiv worker 返回内容损坏，请重试。")
        try:
            return [_paper_from_payload(item) for item in raw_papers]
        except (KeyError, TypeError, ValueError) as exc:
            raise ArxivMcpError("arxiv_parse", "arXiv worker 返回内容损坏，请重试。") from exc

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.stdin is not None:
            with suppress(OSError):
                process.stdin.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()

    def _ensure_process(self) -> subprocess.Popen[str]:
        if self._process is not None and self._process.poll() is None:
            return self._process
        # 只传入导入内置包所需的 PYTHONPATH，不继承账户 Key、SMTP 码或其余环境。
        clean_env = {"PYTHONPATH": os.pathsep.join(sys.path)}
        try:
            self._process = subprocess.Popen(
                [self._python_executable, "-m", "bridges.arxiv_mcp.worker"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                env=clean_env,
            )
        except OSError as exc:
            raise ArxivMcpError("arxiv_startup", "arXiv 搜索服务启动失败，请重试。") from exc
        return self._process


def _paper_from_payload(payload: Any) -> ArxivPaper:
    if not isinstance(payload, dict):
        raise TypeError("paper payload must be an object")
    published_at = datetime.fromisoformat(str(payload["published_at"]))
    return ArxivPaper(
        arxiv_id=str(payload["arxiv_id"]),
        title=str(payload["title"]),
        authors=[str(author) for author in payload["authors"]],
        published_at=published_at,
        abs_url=str(payload["abs_url"]),
        pdf_url=str(payload["pdf_url"]),
        abstract=str(payload["abstract"]),
    )
