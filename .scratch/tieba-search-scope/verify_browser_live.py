"""原始问题的真实浏览器检索与读取；不保存登录数据。"""
import json
import sys
from pathlib import Path

from bridges.tieba.browser import BrowserTiebaSearch, BrowserTiebaThreadReader
from bridges.tieba.browser_runtime import TiebaBrowserRuntime
from bridges.tieba.evidence import read_candidates, search_candidates
from bridges.tieba.parsing import parse_tieba_request


class UnusedPublic:
    def search_public(self, *args, **kwargs):
        raise RuntimeError("本次验收只验证原生检索")


runtime = TiebaBrowserRuntime(Path(sys.argv[1]))
try:
    analysis = parse_tieba_request("我想了解国庆节假期的讨论")
    attempts = search_candidates(BrowserTiebaSearch(runtime, UnusedPublic()),
                                sys.argv[2], analysis, stop_event=None,
                                deadline_seconds=25)
    result = read_candidates(BrowserTiebaThreadReader(runtime, sys.argv[2]), attempts,
                             analysis=analysis, stop_event=None, deadline_seconds=35)
    value = {"question": analysis.original_question,
             "queries": [q.model_dump(mode="json") for q in attempts.records],
             "confirmed": [p.model_dump(mode="json") for p in result.confirmed],
             "unconfirmed": [p.model_dump(mode="json") for p in result.unconfirmed],
             "rejected": [p.model_dump(mode="json") for p in result.rejected]}
    Path(__file__).with_name("browser-live.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(value, ensure_ascii=False))
finally:
    runtime.close()
