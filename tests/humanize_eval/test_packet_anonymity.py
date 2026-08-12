"""匿名裁判包与 organizer mapping（Test plan 4：无泄漏扫描；种子重现）。"""

from __future__ import annotations

from conftest import ARTICLE_FAITHFUL_OUTPUT, CHAT_FAITHFUL_OUTPUT

from bridges.humanize_eval.cases import HUMANIZE_CASES
from bridges.humanize_eval.packet import (
    JudgePacketItem,
    build_packets,
    restore_mapping,
    scan_packet_leaks,
)
from bridges.humanize_eval.suts import SUTOutput


def _outputs() -> dict[str, list[SUTOutput]]:
    from bridges.humanize_eval.generation import (
        GenerationParameters,
        GenerationResult,
        GenerationStatus,
    )

    result = GenerationResult(
        text="",
        model_id="fake",
        parameters=GenerationParameters().model_dump(),
        status=GenerationStatus.SUCCESS,
    )
    by_case = {
        "article-time-management-v1": ARTICLE_FAITHFUL_OUTPUT,
        "chat-tomato-method-v1": CHAT_FAITHFUL_OUTPUT,
    }
    outputs = {
        "current-production": [],
        "candidate": [],
    }
    for case in HUMANIZE_CASES:
        for sut_id in ("current-production", "candidate"):
            outputs[sut_id].append(
                SUTOutput(
                    sut_id=sut_id,
                    case_id=case.case_id,
                    text=by_case[case.case_id],
                    generation=result,
                )
            )
    return outputs


def test_packet_contains_no_identity_fields():
    packet, _ = build_packets(
        packet_id="packet-test",
        cases=list(HUMANIZE_CASES),
        outputs_by_sut=_outputs(),
        anon_seed=2026,
    )
    leaks = scan_packet_leaks(packet)
    assert leaks == []
    serialized = packet.model_dump_json()
    for term in ("current", "candidate", "production", "sut", "peer"):
        assert term not in serialized, f"裁判包泄漏身份词：{term}"


def test_packet_contains_required_judge_context():
    packet, _ = build_packets(
        packet_id="packet-test",
        cases=list(HUMANIZE_CASES),
        outputs_by_sut=_outputs(),
        anon_seed=2026,
    )
    assert len(packet.items) == 2
    for item in packet.items:
        assert item.item_id.startswith("item-")
        assert item.user_request
        assert item.source_text
        assert item.task_bounds
        assert item.protected_items
        assert item.output_a and item.output_b
        assert item.label_a == "a" and item.label_b == "b"


def test_same_seed_reproduces_mapping():
    first_packet, first_mapping = build_packets(
        packet_id="p1",
        cases=list(HUMANIZE_CASES),
        outputs_by_sut=_outputs(),
        anon_seed=42,
    )
    second_packet, second_mapping = build_packets(
        packet_id="p2",
        cases=list(HUMANIZE_CASES),
        outputs_by_sut=_outputs(),
        anon_seed=42,
    )
    assert first_mapping.item_map == second_mapping.item_map
    for item in first_packet.items:
        match = next(i for i in second_packet.items if i.item_id == item.item_id)
        assert (match.output_a, match.output_b) == (item.output_a, item.output_b)


def test_different_seed_changes_left_right_order():
    _, mapping_a = build_packets(
        packet_id="p1",
        cases=list(HUMANIZE_CASES),
        outputs_by_sut=_outputs(),
        anon_seed=1,
    )
    _, mapping_b = build_packets(
        packet_id="p2",
        cases=list(HUMANIZE_CASES),
        outputs_by_sut=_outputs(),
        anon_seed=2,
    )
    assert mapping_a.item_map != mapping_b.item_map


def test_mapping_is_separate_from_packet():
    _, mapping = build_packets(
        packet_id="p1",
        cases=list(HUMANIZE_CASES),
        outputs_by_sut=_outputs(),
        anon_seed=2026,
    )
    # mapping 与 packet 是不同文件/对象：mapping 含 SUT 名，packet 不含。
    assert "current-production" in mapping.model_dump_json()
    assert "candidate" in mapping.model_dump_json()


def test_restore_mapping_resolves_sut_ownership():
    packet, mapping = build_packets(
        packet_id="p1",
        cases=list(HUMANIZE_CASES),
        outputs_by_sut=_outputs(),
        anon_seed=2026,
    )
    restored = restore_mapping(mapping, packet)
    for item in packet.items:
        entry = restored[item.item_id]
        assert set(entry) == {"label_a", "label_b"}
        assert entry["label_a"] in ("current-production", "candidate")
        assert entry["label_b"] != entry["label_a"]


def test_scan_detects_forged_identity_leak():
    leaked = JudgePacketItem(
        item_id="item-deadbeef",
        user_request="把 SUT current-production 的输出给我",
        source_text=None,
        task_bounds="",
        protected_items=[],
        output_a="qwen3.7-plus 的输出",
        output_b="candidate 的输出",
    )
    from bridges.humanize_eval.packet import JudgePacket

    packet = JudgePacket(packet_id="p", items=[leaked])
    leaks = scan_packet_leaks(packet)
    assert leaks, "伪造的泄漏身份词应被扫描发现"
    assert any("current-production" in leak for leak in leaks)
    assert any("candidate" in leak for leak in leaks)
