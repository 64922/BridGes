"""收尾 smoke 2（issue 01 反馈环）：真实 arXiv worker 的 UTF-8 JSONL 往返。

启动真实 ``bridges.arxiv_mcp.worker`` 子进程（非 mock），经
``sitecustomize`` 在 worker 启动时把外部边界（arXiv 网络客户端）替换为
确定性替身：返回标题含 GBK 不可编码字符（emoji 与非断行连字符）的固定
论文。父进程 ``ArxivMcpProcessClient.search`` 必须读到合法 UTF-8 响应。

现状基线：worker 与父进程以系统代码页（Windows 为 GBK/CP936）的文本管道
通信，子进程写 emoji 触发 ``UnicodeEncodeError`` 崩溃，父进程读到 EOF 后
统一误报 ``arxiv_startup``（issue 05 负责修复编码与错误分类；本 smoke 的
失败 trace 即为 issue 05 的锁定证据）。
"""

from __future__ import annotations

from pathlib import Path

from bridges.arxiv_mcp.process import ArxivMcpProcessClient

#: 经 PYTHONPATH 注入的 sitecustomize：解释器启动时把 arXiv 网络客户端
#: 替换为确定性替身（只替换外部边界，worker 协议本身保持真实）。
#: 标题刻意包含 U+1F680（emoji）与 U+2011（非断行连字符），两者均不在
#: GBK/CP936 可编码字符集内。
_SITECUSTOMIZE_SOURCE = '''\
"""收尾 smoke 确定性替身：在 worker 进程内替换 arXiv 网络客户端。

sitecustomize 由 Python 解释器在启动早期自动导入（sys.path 命中即
生效），只影响本子进程；返回固定论文，不发起任何真实网络请求。

注入生效哨兵：写入本模块所在目录的 ``.injected`` 文件，供测试断言
替身确实被加载（worker 的 stderr 被生产代码丢弃，注入失败只能通过
哨兵文件被发现，避免测试静默退回真实 arXiv 网络请求）。
"""

from datetime import UTC, datetime
from pathlib import Path

import bridges.arxiv_mcp.client as _client_module
from bridges.arxiv_mcp.contracts import ArxivPaper

Path(__file__).parent.joinpath(".injected").write_text(
    "arxiv deterministic client injected", encoding="utf-8"
)


class _DeterministicArxivClient:
    """返回含 GBK 不可编码字符的固定论文（标题、作者、摘要全覆盖）。"""

    def search(self, query: str, *, max_results: int = 5) -> list[ArxivPaper]:
        return [
            ArxivPaper(
                arxiv_id="2401.12345v2",
                title="Transformer 注意力机制 🚀 与非断行‑连字符（U+2011）",
                authors=["Ada Lovelace ‑ 测试", "Grace Hopper 🚀"],
                published_at=datetime.fromisoformat(
                    "2024-01-18T12:00:00+00:00"
                ).astimezone(UTC),
                abs_url="https://arxiv.org/abs/2401.12345v2",
                pdf_url="https://arxiv.org/pdf/2401.12345v2",
                abstract="含 🚀 的摘要正文（GBK 不可编码字符必须在 UTF-8 管道中往返）。",
            )
        ]


_client_module.ArxivMcpClient = _DeterministicArxivClient
'''


def _install_shadow(tmp_path: Path) -> Path:
    shadow = tmp_path / "arxiv-shadow"
    shadow.mkdir(parents=True, exist_ok=True)
    (shadow / "sitecustomize.py").write_text(
        _SITECUSTOMIZE_SOURCE, encoding="utf-8"
    )
    return shadow


def test_real_arxiv_worker_returns_gbk_unencodable_paper_in_valid_utf8(
    venv_python, tmp_path
) -> None:
    shadow = _install_shadow(tmp_path)
    client = ArxivMcpProcessClient(
        python_executable=venv_python,
        extra_env={"PYTHONPATH": str(shadow)},
    )
    try:
        papers = client.search("量子 纠错", max_results=1)
    finally:
        client.close()

    # 注入生效哨兵：sitecustomize 必须真正被 worker 加载（防注入失效时
    # 静默退回真实 arXiv 网络请求，红因偏移成网络错误）
    assert (shadow / ".injected").is_file(), (
        "sitecustomize 注入未生效：worker 可能回退到真实 arXiv 客户端。"
    )

    # 父进程必须读到合法 UTF-8：emoji 与非断行连字符原样到达
    assert papers, "worker 应返回固定论文"
    assert "🚀" in papers[0].title
    assert "‑" in papers[0].title
    assert "🚀" in papers[0].abstract
