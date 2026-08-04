"""Issue 22：受限 worker 的固定命令、干净环境与 JSONL 边界测试。"""

from __future__ import annotations

import io
import json
import os
import sys

from bridges.arxiv_mcp.process import ArxivMcpProcessClient


class _FakeProcess:
    def __init__(self) -> None:
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(
            json.dumps(
                {
                    "ok": True,
                    "papers": [
                        {
                            "arxiv_id": "2401.12345v2",
                            "title": "Quantum Error Correction",
                            "authors": ["Ada Lovelace"],
                            "published_at": "2024-01-18T12:00:00+00:00",
                            "abs_url": "https://arxiv.org/abs/2401.12345v2",
                            "pdf_url": "https://arxiv.org/pdf/2401.12345v2",
                            "abstract": "An official abstract.",
                        }
                    ],
                }
            )
            + "\n"
        )

    def poll(self) -> None:
        return None

    def terminate(self) -> None:
        return None

    def wait(self, timeout: float) -> None:
        return None

    def kill(self) -> None:
        return None


def test_process_uses_only_fixed_worker_and_clean_environment(monkeypatch) -> None:
    calls: dict[str, object] = {}
    process = _FakeProcess()

    def fake_popen(command, **kwargs):
        calls["command"] = command
        calls.update(kwargs)
        return process

    monkeypatch.setattr("bridges.arxiv_mcp.process.subprocess.Popen", fake_popen)
    client = ArxivMcpProcessClient(python_executable="fixed-python")

    papers = client.search("量子 纠错", max_results=1)

    assert calls["command"] == ["fixed-python", "-m", "bridges.arxiv_mcp.worker"]
    assert calls["env"] == {"PYTHONPATH": os.pathsep.join(sys.path)}
    assert calls["stderr"] is not None
    assert papers[0].abs_url == "https://arxiv.org/abs/2401.12345v2"
    assert json.loads(process.stdin.getvalue()) == {"query": "量子 纠错", "max_results": 1}
