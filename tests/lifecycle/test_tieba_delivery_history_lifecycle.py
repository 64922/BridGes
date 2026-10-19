"""贴吧历史的证据边界与读取范围沿导出保留，不被聊天正文覆盖。"""

import json

from tests.lifecycle.harness import Harness


def test_tieba_evidence_limits_survive_export(tmp_path):
    harness = Harness(tmp_path)
    conversation_id = harness.create_conversation(harness.acc1, "贴吧交付历史", 1)
    message_id = f"msg-{conversation_id}-1"
    tieba = {
        "status": "success",
        "topic": "宿舍",
        "original_question": "华东交通大学吧 宿舍",
        "forum": "华东交通大学吧",
        "summary": "已读楼层提到晚间热水供应不稳定。",
        "summary_citations": [
            {
                "citation_id": "E1",
                "url": "https://tieba.baidu.com/p/8123456789",
                "floor": 3,
                "author": "吧友甲",
                "posted_at": "2026-09-20 21:14",
                "quote": "热水要刷卡。",
            }
        ],
        "confirmed_posts": [
            {
                "url": "https://tieba.baidu.com/p/8123456789",
                "read_status": "partial",
                "pages_read": 1,
                "pages_limit": 2,
                "pages_complete": False,
                "replies_obtained": True,
                "replies": [],
            }
        ],
        "candidate_links": [],
        "rejected_candidates": [],
        "official_checks": [],
        "time_filter": {
            "requirement": None,
            "applied": False,
            "mode": "unavailable",
            "eligible_count": 0,
            "context_only_count": 0,
            "excluded_count": 0,
            "history_supplemented": False,
            "recent_count": 0,
            "historical_count": 0,
            "unknown_count": 0,
            "note": "未提出时间条件。",
        },
        "evidence_boundary": [
            "普通楼层页只读取第 1/2 页，未读部分不进入总结。",
        ],
        "retryable": False,
        "pending": None,
        "recovery": {
            "action": "retry_incomplete_stage",
            "automatic": False,
            "reuses_saved_evidence": True,
            "failed_stage": "tieba.synthesize",
            "scope_note": "重试只重做总结阶段。",
        },
    }
    harness.database.scoped(harness.acc1).execute(
        "UPDATE messages SET role = 'assistant', tieba_research = ?"
        " WHERE account_id = ? AND message_id = ?",
        (json.dumps(tieba, ensure_ascii=False), harness.acc1, message_id),
    )

    _, payload = harness.export.export_data(harness.acc1)
    items = json.loads(payload)["categories"]["messages"]["items"]
    exported = next(item for item in items if item["message_id"] == message_id)
    stored = json.loads(exported["tieba_research"])

    assert stored["evidence_boundary"] == [
        "普通楼层页只读取第 1/2 页，未读部分不进入总结。"
    ]
    assert stored["confirmed_posts"][0]["pages_read"] == 1
    assert stored["confirmed_posts"][0]["pages_limit"] == 2
    assert stored["recovery"]["action"] == "retry_incomplete_stage"
    assert stored["summary_citations"][0]["author"] == "吧友甲"
