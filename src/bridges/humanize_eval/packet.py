"""匿名裁判包与 organizer mapping（Issue 01 tracer bullet）。

judge-facing packet 只出现：不透明 item ID、固定显示标签 A/B、用户请求、
原文、任务边界、保护项和候选正文；绝不出现 SUT 名、peer ID、策略名、
模型身份或内部映射。

organizer-only mapping 与 judge packet 分开保存：使用相同匿名种子可
重现映射，改变种子会改变左右顺序。裁判只接触 packet，永不接触 mapping。
"""

from __future__ import annotations

import hashlib
import random
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from bridges.humanize_eval.cases import HumanizeCase
from bridges.humanize_eval.suts import SUTOutput


class JudgePacketItem(BaseModel):
    """一个匿名比较项：无任何 SUT/策略/模型身份字段。"""

    item_id: str = Field(description="不透明项目标识（mapping 之外不可反推）。")
    label_a: str = Field(default="a", description="固定显示标签。")
    label_b: str = Field(default="b", description="固定显示标签。")
    user_request: str = Field(description="用户请求原文。")
    source_text: str | None = Field(default=None, description="原文或上下文。")
    task_bounds: str = Field(description="任务边界（不含策略名）。")
    protected_items: list[str] = Field(default_factory=list, description="保护项。")
    output_a: str = Field(description="A 标签下的候选正文。")
    output_b: str = Field(description="B 标签下的候选正文。")


class JudgePacket(BaseModel):
    """发给系统裁判的完整匿名包。"""

    packet_id: str = Field(description="裁判包标识。")
    items: list[JudgePacketItem] = Field(default_factory=list)
    created_at: str = Field(
        default="", description="创建时间（ISO 8601）。"
    )


class OrganizerMapping(BaseModel):
    """organizer-only 映射：item -> 标签 -> SUT 标识。"""

    packet_id: str
    anon_seed: int
    item_map: dict[str, dict[str, str]] = Field(
        description="item_id -> {label_a: sut_id, label_b: sut_id}。"
    )


class PacketAnonymityError(Exception):
    """裁判包匿名性校验失败（发现泄漏字段）。"""


#: 禁止出现在裁判包中的身份词（SUT 名 / 内部标签 / 策略与模型身份）。
FORBIDDEN_IDENTITY_TERMS = (
    "current-production",
    "current_production",
    "candidate",
    "humanizer-zh-reference",
    "humanizer_zh_reference",
    "humanizer-zh",
    "Humanizer-zh",
    "HUMANIZER_ZH",
    "SUT",
    "sut_id",
    "policy_ref",
    "policy_sha",
    "BRIDGES_QWEN",
    "qwen3.7-plus",
    "qwen3.7",
    "generation",
    "temperature",
    "top_p",
    "seed",
    "model_id",
    "modelId",
)


def _opaque_item_id(case_id: str, seed: int) -> str:
    """不透明 item ID：与 case 名无直接可读关系。"""
    digest = hashlib.sha256(f"{case_id}|{seed}".encode()).hexdigest()
    return f"item-{digest[:10]}"


def _task_bounds(case: HumanizeCase) -> str:
    """任务边界描述（表面类型/模式/受众/渠道/长度，不含策略身份）。"""
    return (
        f"表面类型：{case.surface_type}；模式：{case.mode}；"
        f"受众：{case.audience}；渠道：{case.channel}；"
        f"目标长度：{case.target_length}；禁止新增：{('；'.join(case.forbidden_claims))}。"
    )


def build_packets(
    *,
    packet_id: str,
    cases: list[HumanizeCase],
    outputs_by_sut: dict[str, list[SUTOutput]],
    anon_seed: int,
    peer_pair: tuple[str, str] = ("current-production", "candidate"),
) -> tuple[JudgePacket, OrganizerMapping]:
    """按固定种子把成对 SUT 输出匿名化为 A/B，并生成分离的 organizer mapping。

    同一种子产生相同映射；改变种子改变左右顺序。当前把 current 与
    candidate 配成一对（tracer bullet 阶段 reference 的输出另存为
    原始证据，不进入本包）。
    """
    rng = random.Random(anon_seed)
    sut_a, sut_b = peer_pair
    by_sut = {
        sut_id: {output.case_id: output for output in outputs}
        for sut_id, outputs in outputs_by_sut.items()
    }
    items: list[JudgePacketItem] = []
    item_map: dict[str, dict[str, str]] = {}
    for case in cases:
        output_a = by_sut[sut_a].get(case.case_id)
        output_b = by_sut[sut_b].get(case.case_id)
        if output_a is None or output_b is None:
            continue
        item_id = _opaque_item_id(case.case_id, anon_seed)
        flip = rng.random() < 0.5
        if flip:
            shown_a, shown_b = output_b.text, output_a.text
            item_mapping = {"label_a": sut_b, "label_b": sut_a}
        else:
            shown_a, shown_b = output_a.text, output_b.text
            item_mapping = {"label_a": sut_a, "label_b": sut_b}
        items.append(
            JudgePacketItem(
                item_id=item_id,
                user_request=case.user_request,
                source_text=case.source_text or case.context,
                task_bounds=_task_bounds(case),
                protected_items=case.protected_items,
                output_a=shown_a,
                output_b=shown_b,
            )
        )
        item_map[item_id] = item_mapping
    packet = JudgePacket(
        packet_id=packet_id,
        items=items,
        created_at=datetime.now(UTC).isoformat(),
    )
    organizer_mapping = OrganizerMapping(
        packet_id=packet_id,
        anon_seed=anon_seed,
        item_map=item_map,
    )
    return packet, organizer_mapping


def scan_packet_leaks(packet: JudgePacket) -> list[str]:
    """扫描裁判包全部字段与值，返回发现的泄漏项（空列表 = 匿名性通过）。"""
    leaks: list[str] = []
    serialized = packet.model_dump_json()
    for term in FORBIDDEN_IDENTITY_TERMS:
        if term in serialized:
            leaks.append(f"裁判包包含身份词：{term}")
    for item in packet.items:
        for key in ("sut_id", "peer_id", "label_mapping", "model_id", "policy"):
            if key in item.model_dump():
                leaks.append(f"裁判包条目包含内部字段：{key}")
    return leaks


def restore_mapping(
    mapping: OrganizerMapping, packet: JudgePacket
) -> dict[str, dict[str, str]]:
    """用映射把匿名 item 恢复为 SUT 归属（organizer 专用）。"""
    result: dict[str, dict[str, str]] = {}
    for item in packet.items:
        item_mapping = mapping.item_map.get(item.item_id)
        if item_mapping is None:
            raise PacketAnonymityError(f"mapping 缺少 item：{item.item_id}")
        result[item.item_id] = {
            "label_a": item_mapping["label_a"],
            "label_b": item_mapping["label_b"],
        }
    return result
