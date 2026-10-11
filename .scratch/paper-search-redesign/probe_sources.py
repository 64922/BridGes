"""通过用户代理重跑真实公开来源；不以夹具替代联网结果。"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from bridges.arxiv_mcp.client import ArxivMcpClient
from bridges.arxiv_mcp.service import ArxivSearchService
from bridges.paper.discovery import CrossrefPaperSource, OpenAlexPaperSource
from bridges.paper.sources import ArxivPaperSource

QUERIES = (
    "convolutional neural network",
    "deep learning bearing fault diagnosis",
    "transformer traffic flow prediction",
    "convolutional neural network few-shot image classification",
)


def probe(query: str, source_name: str) -> dict[str, object]:
    started = time.monotonic()
    with httpx.Client(proxy="http://127.0.0.1:7890", timeout=15) as client:
        if source_name == "arxiv":
            source = ArxivPaperSource(ArxivSearchService(client=ArxivMcpClient(http_client=client)))
        elif source_name == "openalex":
            source = OpenAlexPaperSource(client=client, timeout=15)
        else:
            source = CrossrefPaperSource(client=client, timeout=15)
        outcome = source.search("acceptance-public-probe", query, max_results=2)
        return {
            "source": source_name, "query": query,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "record": outcome.record.model_dump(mode="json"),
            "candidates": [
                {"id": item.candidate_id, "title": item.title, "year": item.published_year}
                for item in outcome.candidates
            ],
        }


if __name__ == "__main__":
    results = []
    # arXiv 串行，保持请求间隔；互补来源同一例并行。
    for query in QUERIES:
        with ThreadPoolExecutor(max_workers=3) as executor:
            results.extend(executor.map(lambda name: probe(query, name),
                                        ("arxiv", "openalex", "crossref")))
        time.sleep(3)
    target = Path(__file__).parent / "acceptance-logs" / "sources-live.json"
    target.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    for result in results:
        print(result["source"], result["record"]["status"],
              len(result["candidates"]), result["elapsed_seconds"], result["query"])
