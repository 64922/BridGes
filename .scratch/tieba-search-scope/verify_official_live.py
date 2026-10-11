"""公开通知的真实取证验证，不读取浏览器会话或应用凭据。"""

import json
from pathlib import Path

from bridges.tieba.evidence import assess_applicability
from bridges.tieba.official import HttpOfficialSiteReader
from bridges.tieba.parsing import parse_tieba_request

analysis = parse_tieba_request("我想了解国庆节假期的讨论")
reader = HttpOfficialSiteReader()
try:
    check = reader.fetch(
        "https://lib.ecjtu.edu.cn/info/1076/7501.htm", terms=tuple(analysis.topic_terms)
    )
    check.applicability = assess_applicability(check, analysis)
    output = check.model_dump(mode="json")
    Path(__file__).with_name("official-live.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(output, ensure_ascii=False, indent=2))
finally:
    reader.close()
