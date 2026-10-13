"""总结素材保留没有上游 ID 的独立实读发言。"""

from datetime import UTC, datetime

from bridges.tieba.contracts import ReadStatus, TiebaPostProjection, TiebaReply
from bridges.tieba.summary_material import build_verification_payload, sources


def test_repeated_text_without_upstream_reply_id_remains_separate_evidence() -> None:
    post = TiebaPostProjection(
        thread_id="100",
        url="https://tieba.baidu.com/p/100",
        title="宿舍讨论",
        affiliation_evidence="页面吧名匹配",
        read_status=ReadStatus.READ,
        replies_obtained=True,
        replies=[
            TiebaReply(content="寝室有空调", floor=3),
            TiebaReply(content="寝室有空调", floor=3),
        ],
        retrieved_at=datetime.now(UTC),
    )

    evidence = sources([post])

    assert [item["content"] for item in evidence] == ["寝室有空调", "寝室有空调"]
    assert [item["id"] for item in evidence] == ["E1", "E2"]
    assert {item["identity_state"] for item in evidence} == {"unknown"}
    assert {item["duplicate_text_group"] for item in evidence} == {"D1"}
    assert {item["duplicate_text_count"] for item in evidence} == {2}

    by_id = {item["id"]: item for item in evidence}
    payload = build_verification_payload(
        [], by_id, summary_text="多位同学都提到空调。", summary_citation_ids=["E1", "E2"]
    )
    assert [
        item["duplicate_text_group"]
        for item in payload["claims"][-1]["evidence"]
    ] == ["D1", "D1"]
    assert [
        item["identity_state"] for item in payload["claims"][-1]["evidence"]
    ] == ["unknown", "unknown"]


def test_duplicate_upstream_reply_id_is_deduplicated() -> None:
    post = TiebaPostProjection(
        thread_id="100",
        url="https://tieba.baidu.com/p/100",
        title="宿舍讨论",
        affiliation_evidence="页面吧名匹配",
        read_status=ReadStatus.READ,
        replies_obtained=True,
        replies=[
            TiebaReply(content="寝室有空调", reply_id="reply-3"),
            TiebaReply(content="寝室有空调", reply_id="reply-3"),
        ],
        retrieved_at=datetime.now(UTC),
    )

    evidence = sources([post])

    assert len(evidence) == 1
    assert evidence[0]["reply_id"] == "reply-3"


def test_same_text_with_distinct_upstream_ids_remains_distinct_and_identified() -> None:
    post = TiebaPostProjection(
        thread_id="100",
        url="https://tieba.baidu.com/p/100",
        title="宿舍讨论",
        affiliation_evidence="页面吧名匹配",
        read_status=ReadStatus.READ,
        replies_obtained=True,
        replies=[
            TiebaReply(content="寝室有空调", reply_id="reply-3"),
            TiebaReply(content="寝室有空调", reply_id="reply-4"),
        ],
        retrieved_at=datetime.now(UTC),
    )

    evidence = sources([post])

    assert [item["reply_id"] for item in evidence] == ["reply-3", "reply-4"]
    assert [item["identity_state"] for item in evidence] == [
        "upstream_id",
        "upstream_id",
    ]
    assert all("duplicate_text_group" not in item for item in evidence)
