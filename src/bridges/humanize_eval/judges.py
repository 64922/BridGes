"""隔离的系统裁判（Issue 01 tracer bullet）。

至少三个相互隔离、版本固定的系统裁判适配器。裁判调用绝不继承候选
生成会话、隐藏状态、工具记录或候选来源：每次判断都是全新上下文，
输入只有匿名裁判包中的一个 item。

每个裁判对同一配对执行 A/B 与 B/A 双向判断，返回结构化偏好、七维
评分、证据 span 和无法判断原因；位置翻转导致偏好矛盾时记录无效裁决，
不计入有效票。只有一个裁判可用、裁判分歧或顺序一致性失败时，结论
固定为 inconclusive。
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field

from bridges.humanize_eval.generation import (
    GenerationParameters,
    GenerationPort,
    GenerationResult,
)
from bridges.humanize_eval.packet import JudgePacketItem

#: 七维评分（README「自动多裁判匿名盲评」预注册维度）。
JUDGE_DIMENSIONS = (
    "naturalness",       # 自然度
    "credibility",       # 可信与有根据
    "concreteness",      # 具体落地
    "restraint",         # 分寸与非说教
    "task_fit",          # 任务与声音适配
    "readability",       # 可读性
    "fidelity",          # 事实忠实
)

JUDGE_DIMENSION_LABELS = {
    "naturalness": "自然度",
    "credibility": "可信与有根据",
    "concreteness": "具体落地",
    "restraint": "分寸与非说教",
    "task_fit": "任务与声音适配",
    "readability": "可读性",
    "fidelity": "事实忠实",
}


class JudgePreference(StrEnum):
    """主问题偏好：A / B / 平局 / 无法判断。"""

    A = "A"
    B = "B"
    TIE = "TIE"
    CANNOT_JUDGE = "CANNOT_JUDGE"


class JudgeOrder(StrEnum):
    """展示顺序：AB（A 左 B 右）或 BA（A 右 B 左）。"""

    AB = "AB"
    BA = "BA"


class JudgeScoreItem(BaseModel):
    """一维评分的结构化结果。"""

    dimension: str = Field(description="评分维度（JUDGE_DIMENSIONS 之一）。")
    score: int = Field(ge=1, le=5, description="1-5 分。")
    evidence_span: str = Field(default="", description="输出正文中的证据片段。")
    note: str = Field(default="", description="中文说明（可空）。")


class JudgeVerdict(BaseModel):
    """一次单向判断的结构化结果。"""

    judge_id: str
    judge_version: str
    item_id: str
    order: JudgeOrder = Field(description="本次展示顺序。")
    preference: JudgePreference = Field(description="主问题偏好。")
    scores: list[JudgeScoreItem] = Field(default_factory=list)
    cannot_judge_reason: str = Field(default="", description="无法判断原因。")
    invalid_reason: str = Field(default="", description="无效裁决原因（双向矛盾时填写）。")

    @property
    def is_valid(self) -> bool:
        return not self.invalid_reason


class SystemJudge(Protocol):
    """系统裁判协议：隔离调用（无历史、无候选上下文）。"""

    judge_id: str
    judge_version: str

    def judge(self, item: JudgePacketItem, order: JudgeOrder) -> JudgeVerdict: ...


class QwenSystemJudge:
    """真实系统裁判：Qwen 文本模型 + 结构化判断提示。

    同一模型家族适配器可以实例化多个（不同 model_id），但本项目固定
    模型矩阵只有 CHAT_MODEL_ID 一个文本模型；无法满足预注册的裁判
    多样性时，聚合层必须把结论固定为 inconclusive。
    """

    def __init__(
        self,
        port: GenerationPort,
        *,
        judge_id: str = "qwen-system-judge",
        judge_version: str = "v1",
    ) -> None:
        self.port = port
        self.judge_id = judge_id
        self.judge_version = judge_version

    def _system_prompt(self) -> str:
        dims = "；".join(
            f"{key}（{JUDGE_DIMENSION_LABELS[key]}，1-5 分）"
            for key in JUDGE_DIMENSIONS
        )
        return (
            "你是 BridGes 表达质量的系统裁判。你只根据给定用户请求、原文、"
            "任务边界与两段匿名候选正文做判断。你没有其他上下文，不猜测"
            "候选来自哪个系统，也不使用任何历史会话。\n"
            "候选一显示为 A，候选二显示为 B（A/B 标签与展示顺序绑定）。\n"
            "主问题：哪一版更适合直接发送或发布？答案 A 表示候选一更好，"
            "B 表示候选二更好，TIE 表示相当，CANNOT_JUDGE 表示无法判断。\n"
            f"七维评分：{dims}。\n"
            "输出格式（严格 JSON）：\n"
            '{"preference": "A|B|TIE|CANNOT_JUDGE", '
            '"scores": {"维度": {"score": 1-5, "evidence_span": "输出中证据原文片段", '
            '"note": "中文说明"}}, "cannot_judge_reason": "无法判断时写中文原因，否则为空"}'
        )

    def _user_prompt(self, item: JudgePacketItem, order: JudgeOrder) -> str:
        if order is JudgeOrder.AB:
            first, second = item.output_a, item.output_b
        else:
            first, second = item.output_b, item.output_a
        return (
            f"用户请求：{item.user_request}\n"
            f"原文：{item.source_text or '（无）'}\n"
            f"任务边界：{item.task_bounds}\n"
            f"保护项：{'；'.join(item.protected_items)}\n"
            f"候选一：\n{first}\n\n候选二：\n{second}\n"
            "请以 JSON 输出你的判断。"
        )

    def _parse_verdict(
        self,
        text: str,
        item: JudgePacketItem,
        order: JudgeOrder,
    ) -> JudgeVerdict:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return JudgeVerdict(
                judge_id=self.judge_id,
                judge_version=self.judge_version,
                item_id=item.item_id,
                order=order,
                preference=JudgePreference.CANNOT_JUDGE,
                cannot_judge_reason="裁判输出不是合法 JSON，无法解析。",
            )
        import json

        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return JudgeVerdict(
                judge_id=self.judge_id,
                judge_version=self.judge_version,
                item_id=item.item_id,
                order=order,
                preference=JudgePreference.CANNOT_JUDGE,
                cannot_judge_reason="裁判输出 JSON 解析失败。",
            )
        preference_raw = str(payload.get("preference", "")).upper()
        try:
            preference = JudgePreference(preference_raw)
        except ValueError:
            preference = JudgePreference.CANNOT_JUDGE
        scores: list[JudgeScoreItem] = []
        for dimension in JUDGE_DIMENSIONS:
            entry = payload.get("scores", {}).get(dimension)
            if not isinstance(entry, dict):
                continue
            try:
                score = max(1, min(5, int(entry.get("score", 3))))
            except (TypeError, ValueError):
                score = 3
            scores.append(
                JudgeScoreItem(
                    dimension=dimension,
                    score=score,
                    evidence_span=str(entry.get("evidence_span", ""))[:200],
                    note=str(entry.get("note", ""))[:200],
                )
            )
        return JudgeVerdict(
            judge_id=self.judge_id,
            judge_version=self.judge_version,
            item_id=item.item_id,
            order=order,
            preference=preference,
            scores=scores,
            cannot_judge_reason=str(payload.get("cannot_judge_reason", "")),
        )

    def judge(self, item: JudgePacketItem, order: JudgeOrder) -> JudgeVerdict:
        result: GenerationResult = self.port.generate(
            system_prompt=self._system_prompt(),
            user_prompt=self._user_prompt(item, order),
            params=GenerationParameters(temperature=0.2, top_p=0.6, max_tokens=1200),
        )
        if not result.ok:
            return JudgeVerdict(
                judge_id=self.judge_id,
                judge_version=self.judge_version,
                item_id=item.item_id,
                order=order,
                preference=JudgePreference.CANNOT_JUDGE,
                cannot_judge_reason=result.error_message or "裁判模型调用失败。",
            )
        return self._parse_verdict(result.text, item, order)


def judge_pair(
    judge: SystemJudge, item: JudgePacketItem
) -> tuple[JudgeVerdict, JudgeVerdict]:
    """对同一 item 执行 AB 与 BA 双向判断；返回 (AB 裁决, BA 裁决)。

    双向一致性：同一候选在左或在右，裁判对"哪版更好"的答案应当一致
    （A 在 AB 偏好 A ⟺ A 在 BA 偏好 B）。矛盾时两个裁决都标记 invalid。
    """
    verdict_ab = judge.judge(item, JudgeOrder.AB)
    verdict_ba = judge.judge(item, JudgeOrder.BA)
    if not _orders_consistent(verdict_ab, verdict_ba):
        reason = "位置翻转后偏好矛盾，裁决无效（位置偏差）。"
        verdict_ab.invalid_reason = reason
        verdict_ba.invalid_reason = reason
    return verdict_ab, verdict_ba


def _orders_consistent(ab: JudgeVerdict, ba: JudgeVerdict) -> bool:
    """AB/BA 偏好是否一致。

    一致：两个顺序都指向同一 output（AB 偏好 A ⟺ BA 偏好 B），或两个
    顺序都中性（TIE/CANNOT_JUDGE）。一侧中性、另一侧有明确偏好（如
    TIE→A）是「位置翻转后无合理理由地改变偏好」，记为位置偏差无效。
    """

    def normalized(verdict: JudgeVerdict) -> str:
        if verdict.preference is JudgePreference.A:
            return "A"
        if verdict.preference is JudgePreference.B:
            return "B"
        return "N"

    left = normalized(ab)
    right = normalized(ba)
    if left == "N" and right == "N":
        return True
    if left == "N" or right == "N":
        return False
    # AB 中偏好 A 表示「候选一（output_a）更好」；
    # BA 中偏好 A 表示「候选一（output_b）更好」，即偏好 output_b。
    # 一致当且仅当两者指向同一 output。
    return (left == "A" and right == "B") or (left == "B" and right == "A")


class FakeSystemJudge:
    """确定性假裁判（测试用）：按脚本返回偏好与分数。"""

    def __init__(
        self,
        *,
        judge_id: str,
        judge_version: str = "test-v1",
        scripted_preferences: dict[str, JudgePreference] | None = None,
        scripted_scores: dict[str, dict[str, int]] | None = None,
        cannot_judge_reason: str = "",
    ) -> None:
        self.judge_id = judge_id
        self.judge_version = judge_version
        self._preferences = scripted_preferences or {}
        self._scores = scripted_scores or {}
        self.calls: list[tuple[str, JudgeOrder, str, str]] = []

    def judge(self, item: JudgePacketItem, order: JudgeOrder) -> JudgeVerdict:
        self.calls.append(
            (item.item_id, order, item.output_a[:40], item.output_b[:40])
        )
        preference = self._preferences.get(
            item.item_id, JudgePreference.TIE
        )
        if preference is JudgePreference.CANNOT_JUDGE:
            return JudgeVerdict(
                judge_id=self.judge_id,
                judge_version=self.judge_version,
                item_id=item.item_id,
                order=order,
                preference=JudgePreference.CANNOT_JUDGE,
                cannot_judge_reason="假裁判无法判断。",
            )
        scores = [
            JudgeScoreItem(
                dimension=dimension,
                score=self._scores.get(item.item_id, {}).get(dimension, 3),
                evidence_span=item.output_a[:20],
            )
            for dimension in JUDGE_DIMENSIONS
        ]
        return JudgeVerdict(
            judge_id=self.judge_id,
            judge_version=self.judge_version,
            item_id=item.item_id,
            order=order,
            preference=preference,
            scores=scores,
        )


def build_judges(port: GenerationPort) -> list[SystemJudge]:
    """构造三个隔离的真实裁判适配器（Qwen 文本模型实例化）。

    三个实例共享同一模型家族，不满足预注册的裁判多样性；聚合层检测到
    多样性不足时结论必须为 inconclusive（见 runner.aggregate）。
    """
    return [
        QwenSystemJudge(port, judge_id="qwen-system-judge-1", judge_version="v1"),
        QwenSystemJudge(port, judge_id="qwen-system-judge-2", judge_version="v1"),
        QwenSystemJudge(port, judge_id="qwen-system-judge-3", judge_version="v1"),
    ]
