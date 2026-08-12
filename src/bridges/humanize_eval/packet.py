"""匿名裁判包与 evaluator-private sealed mapping（Issue 01 + Issue 10）。

两个合同严格分离：

- ``JudgePacket``（judge-facing）：只出现不透明 packet/item ID、固定显示
  标签 A/B、判断所需的最小上下文（用户请求、原文或前序对话、模式、受众、
  渠道、长度、改写强度、现实承诺、允许材料、来源边界、保护项）与匿名候选
  正文；绝不出现 SUT 名、peer ID、策略名、模型身份、git/build 标识或内部
  映射。
- ``SealedMapping``（evaluator-private）：记录 packet ID、case/run、候选
  来源、生成模型、左右映射、匿名种子、内容哈希与创建版本；只有聚合器可
  读取，裁判适配器拿不到该对象（``SystemJudge.judge`` 只接收
  ``JudgePacketItem``）。

泄漏检查失败时不调用裁判（见 runner）。使用相同匿名种子可重现映射，
改变种子改变左右顺序，而裁判始终只看到 A/B。
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from bridges.humanize_eval.cases import HumanizeCase
from bridges.humanize_eval.suts import SUTOutput


class JudgePacketItem(BaseModel):
    """一个匿名比较项：判断所需最小上下文，无任何 SUT/策略/模型身份字段。"""

    item_id: str = Field(description="不透明项目标识（mapping 之外不可反推）。")
    label_a: str = Field(default="a", description="固定显示标签。")
    label_b: str = Field(default="b", description="固定显示标签。")
    user_request: str = Field(description="用户请求原文。")
    source_text: str | None = Field(default=None, description="原文（改写案例）。")
    prior_dialogue: str | None = Field(
        default=None, description="前序对话（聊天案例的历史上下文）。"
    )
    mode: str = Field(default="", description="模式：聊天 casual/learning 或改写档位。")
    audience: str = Field(default="", description="目标受众。")
    channel: str = Field(default="", description="发布渠道。")
    target_length: str = Field(default="", description="目标篇幅。")
    rewrite_intensity: str | None = Field(
        default=None, description="改写强度（light/standard/deep，生成案例可为空）。"
    )
    realism_commitment: str = Field(
        default="", description="现实承诺：不得虚构亲历/朋友对话/无来源数据。"
    )
    allowed_materials: list[str] = Field(
        default_factory=list, description="允许使用的外部材料（无来源不可新增 claim）。"
    )
    source_boundary: str = Field(
        default="", description="来源边界：哪些事实有依据、哪些必须保留原文。"
    )
    protected_items: list[str] = Field(default_factory=list, description="保护项。")
    output_a: str = Field(description="A 标签下的候选正文。")
    output_b: str = Field(description="B 标签下的候选正文。")


class JudgePacket(BaseModel):
    """发给系统裁判的完整匿名包（judge-facing 合同）。"""

    packet_id: str = Field(description="随机不透明裁判包标识（不含任何内部身份）。")
    schema_version: str = Field(description="judge packet 合同版本。")
    items: list[JudgePacketItem] = Field(default_factory=list)
    created_at: str = Field(default="", description="创建时间（ISO 8601）。")


class SealedEntry(BaseModel):
    """sealed mapping 中的一条 item 记录（evaluator-private）。

    ``flipped`` 记录匿名化是否翻转了左右（label_a 槽位装的是哪个 SUT 的
    输出由 ``candidate_source_a`` 给出；AB 展示顺序下 label_a 恒在左）。
    """

    case_id: str = Field(description="案例标识。")
    run_id: str = Field(description="所属运行标识。")
    candidate_source_a: str = Field(description="label_a 的候选来源（SUT 标识）。")
    candidate_source_b: str = Field(description="label_b 的候选来源（SUT 标识）。")
    model_a: str = Field(description="label_a 候选的生成模型快照。")
    model_b: str = Field(description="label_b 候选的生成模型快照。")
    flipped: bool = Field(description="是否翻转了候选的左右展示顺序。")
    output_sha_a: str = Field(description="label_a 候选正文内容哈希。")
    output_sha_b: str = Field(description="label_b 候选正文内容哈希。")
    anon_seed: int = Field(description="匿名化种子（改变种子改变左右顺序）。")


class SealedMapping(BaseModel):
    """evaluator-private sealed mapping：只有聚合器可读。

    记录 packet ID、case/run、候选来源、生成模型、左右映射、匿名种子、
    内容哈希与创建版本。裁判适配器拿不到本对象：``SystemJudge.judge``
    只接收 ``JudgePacketItem``。
    """

    packet_id: str
    schema_version: str = Field(description="创建版本（packet 合同版本）。")
    created_at: str = Field(default="", description="创建时间（ISO 8601）。")
    entries: dict[str, SealedEntry] = Field(
        default_factory=dict, description="item_id -> 密封记录。"
    )


class PacketAnonymityError(Exception):
    """裁判包匿名性校验失败（发现泄漏字段）。"""


#: judge packet 合同版本（进入运行锁；合同变化必须升版本）。
PACKET_SCHEMA_VERSION = "2"


def _opaque_id(seed: int, *parts: str) -> str:
    """不透明 ID：与 case/SUT/run 名无直接可读关系（sha256 前缀）。"""
    digest = hashlib.sha256(
        f"{'|'.join(parts)}|{seed}".encode()
    ).hexdigest()
    return digest[:16]


def _item_id(case_id: str, seed: int, salt: str = "") -> str:
    """不透明 item ID：与 case 名无直接可读关系。

    ``salt`` 区分同一 case 在不同配对（current 对照 / Humanizer-zh
    参考对照）中的 item，避免两个 packet 的 item_id 冲突。
    """
    return f"item-{_opaque_id(seed, case_id, salt)}"


def _task_context(case: HumanizeCase) -> dict[str, Any]:
    """从案例派生判断所需的最小上下文（不含策略/模型身份）。"""
    return {
        "mode": case.mode,
        "audience": case.audience,
        "channel": case.channel,
        "target_length": case.target_length,
        "rewrite_intensity": (
            case.rewrite_intensity.value if case.rewrite_intensity else None
        ),
        "realism_commitment": (
            "不得虚构个人经历、朋友对话、具体时间地点、未经来源支持的"
            "数据或功能。"
        ),
        "allowed_materials": list(case.allowed_materials),
        "source_boundary": (
            "来源边界：除原文与允许材料外，不得新增任何事实性内容；"
            "数字、单位、日期、专名、精确引语、URL 与否定边界必须保留。"
        ),
        "protected_items": list(case.protected_items),
    }


def build_packets(
    *,
    packet_id: str,
    cases: list[HumanizeCase],
    outputs_by_sut: dict[str, list[SUTOutput]],
    anon_seed: int,
    peer_pair: tuple[str, str] = ("current-production", "candidate"),
    run_id: str = "",
    item_salt: str = "",
) -> tuple[JudgePacket, SealedMapping]:
    """按固定种子把成对 SUT 输出匿名化为 A/B，并生成 evaluator-private 映射。

    同一种子产生相同映射；改变种子改变左右顺序。当前把 current 与
    candidate 配成一对（reference/plain 的输出另存为原始证据，不进入本包）。
    ``item_salt`` 区分同一 case 在不同配对中的 item（Issue 11 参考对照）。
    """
    rng = random.Random(anon_seed)
    sut_a, sut_b = peer_pair
    by_sut = {
        sut_id: {output.case_id: output for output in outputs}
        for sut_id, outputs in outputs_by_sut.items()
    }
    items: list[JudgePacketItem] = []
    entries: dict[str, SealedEntry] = {}
    for case in cases:
        output_a = by_sut[sut_a].get(case.case_id)
        output_b = by_sut[sut_b].get(case.case_id)
        if output_a is None or output_b is None:
            continue
        item_id = _item_id(case.case_id, anon_seed, item_salt)
        flip = rng.random() < 0.5
        if flip:
            shown_a, shown_b = output_b.text, output_a.text
            label_map = {"a": sut_b, "b": sut_a}
            model_map = {"a": output_b.generation.model_id,
                         "b": output_a.generation.model_id}
            sha_map = {"a": _sha256(output_b.text), "b": _sha256(output_a.text)}
        else:
            shown_a, shown_b = output_a.text, output_b.text
            label_map = {"a": sut_a, "b": sut_b}
            model_map = {"a": output_a.generation.model_id,
                         "b": output_b.generation.model_id}
            sha_map = {"a": _sha256(output_a.text), "b": _sha256(output_b.text)}
        context = _task_context(case)
        items.append(
            JudgePacketItem(
                item_id=item_id,
                user_request=case.user_request,
                source_text=case.source_text or case.context,
                prior_dialogue=case.context,
                mode=str(context["mode"]),
                audience=str(context["audience"]),
                channel=str(context["channel"]),
                target_length=str(context["target_length"]),
                rewrite_intensity=context["rewrite_intensity"],
                realism_commitment=str(context["realism_commitment"]),
                allowed_materials=list(context["allowed_materials"]),
                source_boundary=str(context["source_boundary"]),
                protected_items=list(context["protected_items"]),
                output_a=shown_a,
                output_b=shown_b,
            )
        )
        entries[item_id] = SealedEntry(
            case_id=case.case_id,
            run_id=run_id,
            candidate_source_a=label_map["a"],
            candidate_source_b=label_map["b"],
            model_a=model_map["a"],
            model_b=model_map["b"],
            flipped=flip,
            output_sha_a=sha_map["a"],
            output_sha_b=sha_map["b"],
            anon_seed=anon_seed,
        )
    packet = JudgePacket(
        packet_id=packet_id,
        schema_version=PACKET_SCHEMA_VERSION,
        items=items,
        created_at=datetime.now(UTC).isoformat(),
    )
    sealed = SealedMapping(
        packet_id=packet_id,
        schema_version=PACKET_SCHEMA_VERSION,
        created_at=datetime.now(UTC).isoformat(),
        entries=entries,
    )
    return packet, sealed


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 递归泄漏扫描：字段名、字段值、字段路径
# ---------------------------------------------------------------------------

def _normalize(term: str) -> str:
    """标识归一化：小写并剥掉分隔符/特殊字符（-、_、空格、. 等）。"""
    return re.sub(r"[\s\-_\.:/\\]", "", term.lower())


def scan_packet_leaks(
    packet: JudgePacket,
    *,
    identities: dict[str, list[str]],
) -> list[str]:
    """递归扫描裁判包字段名、字段值与字段路径，返回泄漏项列表。

    ``identities`` 为类别 -> 已登记标识清单（SUT、model、strategy、skill、
    peer、git/build、mapping 等）。任一标识以归一化形式出现在字段名、
    字段值或路径中即记泄漏。空列表 = 匿名性通过。泄漏检查失败时调用方
    不得调用裁判（见 runner）。
    """
    return scan_serialized_leaks(
        json.loads(packet.model_dump_json()), identities=identities
    )


def scan_serialized_leaks(
    payload: Any,
    *,
    identities: dict[str, list[str]],
) -> list[str]:
    """对序列化后的裁判包字典递归扫描字段名/值/路径（测试与导出器共用）。"""
    leaks: list[str] = []
    # 归一化后禁止出现在任何键/值中的标识（含 SUT 名及其常见变体）；
    # 保留原始标识用于泄漏消息（可读性）。
    forbidden = [
        term
        for terms in identities.values()
        for term in terms
        if term.strip()
    ]
    forbidden_normalized = {_normalize(term) for term in forbidden}
    # 固定显示标签是合同的一部分；内部映射键名不允许出现在 packet 中。
    forbidden_internal_keys = {
        "candidate_source", "sut_id", "peer_id", "label_mapping", "left_right",
        "output_sha", "anon_seed", "run_id", "model_a", "model_b",
    }

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")
        elif isinstance(node, str):
            normalized = _normalize(node)
            for term in forbidden:
                if term and _normalize(term) in normalized:
                    leaks.append(f"字段值泄漏 {term}（路径 {path}）")

    def walk_keys(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                key_norm = _normalize(key)
                if key_norm in forbidden_normalized:
                    leaks.append(f"字段名泄漏 {key}（路径 {path}）")
                if key_norm in forbidden_internal_keys:
                    leaks.append(f"字段名含内部映射键：{key}（路径 {path}）")
                walk_keys(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk_keys(value, f"{path}[{index}]")

    walk_keys(payload, "packet")
    walk(payload, "packet")
    return leaks


def restore_mapping(
    mapping: SealedMapping, packet: JudgePacket
) -> dict[str, dict[str, str]]:
    """用 sealed mapping 把匿名 item 恢复为 SUT 归属（organizer/聚合器专用）。"""
    result: dict[str, dict[str, str]] = {}
    for item in packet.items:
        entry = mapping.entries.get(item.item_id)
        if entry is None:
            raise PacketAnonymityError(f"mapping 缺少 item：{item.item_id}")
        result[item.item_id] = {
            "label_a": entry.candidate_source_a,
            "label_b": entry.candidate_source_b,
        }
    return result
