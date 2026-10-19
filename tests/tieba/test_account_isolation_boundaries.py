"""工单 14：账户删除与数据边界的回归。"""

from __future__ import annotations

import json
from pathlib import Path

from bridges.tieba.browser_worker import _error_payload
from bridges.tieba.contracts import TiebaSearchHit


def test_browser_boundary_and_errors_do_not_expose_session_secrets() -> None:
    secret = "BDUSS=account-session-canary"
    payload = _error_payload(RuntimeError(f"Cookie: {secret}"))
    assert secret not in json.dumps(payload, ensure_ascii=False)

    from bridges.tieba.browser_read_projection import read_projection

    projected = read_projection(
        "https://tieba.baidu.com/p/123",
        {
            "forum": "华东交通大学吧",
            "title": "宿舍条件",
            "original": "北区宿舍有空调。",
            "cookie": secret,
            "token": secret,
            "headers": {"Cookie": secret},
            "replies": [],
        },
    )
    assert secret not in json.dumps(projected.model_dump(mode="json"), ensure_ascii=False)
    hit = TiebaSearchHit.model_validate(
        {
            "url": "https://tieba.baidu.com/p/123",
            "title": "宿舍条件",
            "snippet": "讨论",
            "cookie": secret,
        }
    )
    assert secret not in hit.model_dump_json()


def test_tieba_evidence_export_is_account_scoped(tmp_path: Path) -> None:
    from tests.lifecycle.harness import Harness

    harness = Harness(tmp_path)
    try:
        own = harness.create_conversation(harness.acc1, "A 的贴吧", 1)
        foreign = harness.create_conversation(harness.acc2, "B 的贴吧", 1)
        own_text = "A 账户的宿舍讨论原文"
        foreign_text = "B 账户的宿舍讨论原文"
        with harness.database.transaction():
            harness.database.connection.execute(
                "UPDATE messages SET tieba_research = ? WHERE conversation_id = ?",
                (json.dumps({"original_question": own_text}, ensure_ascii=False), own),
            )
            harness.database.connection.execute(
                "UPDATE messages SET tieba_research = ? WHERE conversation_id = ?",
                (json.dumps({"original_question": foreign_text}, ensure_ascii=False), foreign),
            )

        _filename, payload = harness.export.export_data(harness.acc1)
        raw = payload.decode("utf-8")

        assert own_text in raw
        assert foreign_text not in raw
    finally:
        harness.database.close()
        harness.state.close()
