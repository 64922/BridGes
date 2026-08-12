"""匿名裁判包与 sealed mapping（Test plan 1/2：递归泄漏扫描；种子重现）。"""

from __future__ import annotations

from bridges.humanize_eval.cases import HUMANIZE_CASES
from bridges.humanize_eval.packet import (
    JudgePacket,
    JudgePacketItem,
    SealedMapping,
    build_packets,
    restore_mapping,
    scan_packet_leaks,
)
from bridges.humanize_eval.suts import SUTOutput

#: 已登记身份（测试用）：SUT/model/strategy/git/mapping 各类别。
IDENTITIES = {
    "sut": ["current-production", "candidate", "plain-model", "humanizer-zh-reference"],
    "model": ["qwen3.7-plus", "fake-model"],
    "strategy": ["policy_sha_deadbeef", "SKILL.md"],
    "git": ["a1b2c3d4e5f6"],
    "mapping": ["label_mapping", "candidate_source", "anon_seed"],
}


def _outputs() -> dict[str, list[SUTOutput]]:
    from bridges.humanize_eval.generation import (
        GenerationParameters,
        GenerationResult,
        GenerationStatus,
    )

    result = GenerationResult(
        text="",
        model_id="fake-model",
        parameters=GenerationParameters().model_dump(),
        status=GenerationStatus.SUCCESS,
    )
    outputs = {
        "current-production": [],
        "candidate": [],
    }
    for case in HUMANIZE_CASES:
        text = (case.source_text or "") + "".join(case.protected_items)
        for sut_id in ("current-production", "candidate"):
            outputs[sut_id].append(
                SUTOutput(
                    sut_id=sut_id,
                    case_id=case.case_id,
                    text=text,
                    generation=result,
                )
            )
    return outputs


def _build(seed: int = 2026, packet_id: str = "packet-test"):
    return build_packets(
        packet_id=packet_id,
        cases=list(HUMANIZE_CASES),
        outputs_by_sut=_outputs(),
        anon_seed=seed,
        run_id="run-test",
    )


def test_packet_contains_no_identity_fields():
    packet, _ = _build()
    leaks = scan_packet_leaks(packet, identities=IDENTITIES)
    assert leaks == []
    serialized = packet.model_dump_json()
    for term in ("current", "candidate", "production", "sut", "peer"):
        assert term not in serialized, f"裁判包泄漏身份词：{term}"
    # 递归泄漏扫描：字段名、字段值、路径都不允许出现登记标识。
    for term in ("current-production", "qwen3.7-plus", "policy_sha_deadbeef"):
        assert term not in serialized


def test_packet_contains_required_judge_context():
    packet, _ = _build()
    assert len(packet.items) == len(HUMANIZE_CASES)
    for item in packet.items:
        assert item.item_id.startswith("item-")
        assert item.user_request
        # Issue 10 最小上下文：模式/受众/渠道/长度/改写强度/现实承诺/
        # 允许材料/来源边界/保护项。
        assert item.mode
        assert item.audience
        assert item.channel
        assert item.target_length
        assert item.realism_commitment
        assert item.source_boundary
        assert item.protected_items
        assert item.output_a and item.output_b
        assert item.label_a == "a" and item.label_b == "b"
        # 文章改写案例有改写强度；前序对话可为空（聊天案例才有）。
        if item.rewrite_intensity:
            assert item.rewrite_intensity in ("light", "standard", "deep")


def test_sealed_mapping_records_full_identity():
    packet, sealed = _build(seed=7)
    assert isinstance(sealed, SealedMapping)
    assert sealed.packet_id == packet.packet_id
    assert sealed.schema_version
    for item in packet.items:
        entry = sealed.entries[item.item_id]
        assert entry.case_id
        assert entry.run_id == "run-test"
        assert entry.candidate_source_a in ("current-production", "candidate")
        assert entry.candidate_source_b != entry.candidate_source_a
        assert entry.model_a and entry.model_b
        assert entry.output_sha_a and entry.output_sha_b
        assert entry.anon_seed == 7
        # 内容哈希与 packet 输出一致（防篡改验证）。
        assert len(entry.output_sha_a) == 64


def test_same_seed_reproduces_mapping():
    first_packet, first_mapping = _build(seed=42)
    second_packet, second_mapping = _build(seed=42)
    assert first_mapping.entries == second_mapping.entries
    for item in first_packet.items:
        match = next(i for i in second_packet.items if i.item_id == item.item_id)
        assert (match.output_a, match.output_b) == (item.output_a, item.output_b)


def test_different_seed_changes_left_right_order():
    _, mapping_a = _build(seed=1)
    _, mapping_b = _build(seed=2)
    assert mapping_a.entries != mapping_b.entries


def test_mapping_is_separate_from_packet():
    packet, sealed = _build()
    # sealed mapping 与 packet 是不同对象：mapping 含 SUT 名，packet 不含。
    assert "current-production" in sealed.model_dump_json()
    assert "candidate" in sealed.model_dump_json()
    assert "current-production" not in packet.model_dump_json()


def test_restore_mapping_resolves_sut_ownership():
    packet, sealed = _build()
    restored = restore_mapping(sealed, packet)
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
        mode="rewrite",
        audience="普通读者",
        channel="博客",
        target_length="中等",
        realism_commitment="不虚构。",
        source_boundary="只使用原文。",
        protected_items=[],
        output_a="qwen3.7-plus 的输出",
        output_b="candidate 的输出",
    )
    packet = JudgePacket(
        packet_id="p", schema_version="2", items=[leaked]
    )
    leaks = scan_packet_leaks(packet, identities=IDENTITIES)
    assert leaks, "伪造的泄漏身份词应被扫描发现"
    assert any("current-production" in leak for leak in leaks)
    assert any("candidate" in leak for leak in leaks)
    assert any("qwen3.7-plus" in leak for leak in leaks)


def test_scan_detects_internal_mapping_keys_in_field_names():
    """内部映射键出现在字段名中也算泄漏（字段路径递归扫描）。"""
    from bridges.humanize_eval.packet import scan_serialized_leaks

    payload = {
        "packet_id": "p",
        "schema_version": "2",
        "items": [
            {
                "item_id": "item-deadbeef",
                "user_request": "请求",
                "candidate_source": "current-production",
                "output_a": "候选一",
                "output_b": "候选二",
            }
        ],
    }
    leaks = scan_serialized_leaks(payload, identities=IDENTITIES)
    assert any("candidate_source" in leak for leak in leaks)


def test_packet_id_is_opaque_and_random():
    """packet ID 随机不透明：不复用 case/SUT/模型/策略或映射信息。"""
    packet, sealed = _build()
    assert packet.packet_id.startswith("packet-")
    # packet ID 不与任何 case 名相关（随机不透明）。
    for case in HUMANIZE_CASES:
        assert case.case_id not in packet.packet_id
    for term in ("current", "candidate", "production", "qwen", "run"):
        assert term not in packet.packet_id
    # sealed mapping 的 packet_id 与 packet 相同（同一运行的密封记录）。
    assert sealed.packet_id == packet.packet_id
