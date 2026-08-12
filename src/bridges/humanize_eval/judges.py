"""隔离的系统裁判（Issue 01 tracer bullet + Issue 10）。

至少三个相互隔离、版本固定的系统裁判适配器。裁判调用绝不继承候选
生成会话、隐藏状态、工具记录或候选来源：每次判断都是全新上下文，
输入只有匿名裁判包中的一个 item（拿不到 sealed mapping）。

每个裁判对同一配对执行 A/B 与 B/A 双向判断，返回结构化偏好、七维
评分、证据 span 与无法判断原因；位置翻转导致偏好矛盾时记录无效裁决。
每个非平局裁决必须引用候选中的有效 span 并关联稳定 reason code；
span 不存在、引用来自任务说明而非候选、理由与选择矛盾时判为无效。

裁判的模型快照、系统提示哈希、schema 版本与采样参数（temperature/
seed 等）进入 registry 与运行锁（见 registry.py）。
"""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from typing import Protocol, runtime_checkable

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

#: 预注册稳定 reason code（非平局裁决必须使用其中之一；变化必须升版本）。
JUDGE_REASON_CODES = (
    "naturalness",       # 更自然、更像有判断的人在说话
    "fidelity_risk",     # 保真/事实风险（另一版更可信）
    "concreteness",      # 更具体落地
    "restraint",         # 更有分寸、不说教
    "task_fit",          # 更贴合任务与声音
    "clarity",           # 更清晰可读
    "brevity",           # 更简洁
    "neutral",           # 平局（仅 TIE 允许）
)

#: 裁决 schema 版本（进入运行锁与 registry）。
JUDGE_SCHEMA_VERSION = "judge-schema-v2"

#: 裁判采样参数（固定预注册：低温度高确定性，进入运行锁）。
JUDGE_PARAMETERS = GenerationParameters(temperature=0.2, top_p=0.6, max_tokens=1200)


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
    reason_code: str = Field(
        default="", description="稳定 reason code（非平局必须且合法）。"
    )
    evidence_span: str = Field(
        default="", description="引用候选正文的证据 span（非平局必须有效）。"
    )
    scores: list[JudgeScoreItem] = Field(default_factory=list)
    cannot_judge_reason: str = Field(default="", description="无法判断原因。")
    invalid_reason: str = Field(
        default="", description="无效裁决原因（双向矛盾/span 无效时填写）。"
    )

    @property
    def is_valid(self) -> bool:
        return not self.invalid_reason


@runtime_checkable
class SystemJudge(Protocol):
    """系统裁判协议：隔离调用（无历史、无候选上下文）。"""

    judge_id: str
    judge_version: str

    def judge(self, item: JudgePacketItem, order: JudgeOrder) -> JudgeVerdict: ...


# ---------------------------------------------------------------------------
# 证据 span 与理由有效性校验（非平局硬性要求）
# ---------------------------------------------------------------------------

def validate_evidence(
    verdict: JudgeVerdict, item: JudgePacketItem
) -> str | None:
    """校验非平局裁决的证据引用；返回无效原因（None = 有效）。

    规则（预注册）：
    - 非平局（A/B）必须给出非空 evidence_span 与合法 reason_code；
    - span 必须存在于某个候选正文（output_a 或 output_b）；
    - span 不得只存在于任务说明（用户请求/原文/前序对话/任务上下文/
      保护项/允许材料）——引用任务说明不算引用候选；
    - span 出现在与偏好相反的候选正文中时视为理由与选择矛盾；
    - TIE/CANNOT_JUDGE 不要求证据（canary 的左右顺序 TIE 除外）。
    """
    if verdict.preference in (JudgePreference.TIE, JudgePreference.CANNOT_JUDGE):
        return None
    if not verdict.reason_code:
        return "非平局裁决缺少 reason code。"
    if verdict.reason_code not in JUDGE_REASON_CODES:
        return f"reason code 不在预注册集合内：{verdict.reason_code}"
    if verdict.reason_code == "neutral":
        return "非平局裁决不得使用 neutral 理由。"
    span = verdict.evidence_span.strip()
    if not span:
        return "非平局裁决缺少证据 span。"
    in_a = span in item.output_a
    in_b = span in item.output_b
    if not in_a and not in_b:
        # span 在候选中不存在：可能引用自任务说明（用户请求/原文/保护项
        # 等）或凭空编造——两者都算无效证据。
        return "证据 span 不存在于任何候选正文（引用来自任务说明或编造）。"
    if verdict.preference is JudgePreference.A and in_b and not in_a:
        return "理由与选择矛盾：证据 span 属于候选 B 却偏好 A。"
    if verdict.preference is JudgePreference.B and in_a and not in_b:
        return "理由与选择矛盾：证据 span 属于候选 A 却偏好 B。"
    return None


def reason_code_label(code: str) -> str:
    """reason code 的中文标签（报告展示用）。"""
    return {
        "naturalness": "更自然",
        "fidelity_risk": "保真风险",
        "concreteness": "更具体落地",
        "restraint": "更有分寸",
        "task_fit": "任务与声音适配",
        "clarity": "更清晰",
        "brevity": "更简洁",
        "neutral": "平局",
    }.get(code, code)


class QwenSystemJudge:
    """真实系统裁判：Qwen 文本模型 + 结构化判断提示。

    同一模型家族适配器可以实例化多个（不同 model_id），但本项目固定
    模型矩阵只有 CHAT_MODEL_ID 一个文本模型；无法满足预注册的裁判
    多样性时，聚合层必须把结论固定为 inconclusive（见 registry）。
    """

    def __init__(
        self,
        port: GenerationPort,
        *,
        judge_id: str = "qwen-system-judge",
        judge_version: str = "v1",
        model_family: str = "qwen",
        provider: str = "alibaba",
        parameters: GenerationParameters = JUDGE_PARAMETERS,
    ) -> None:
        self.port = port
        self.judge_id = judge_id
        self.judge_version = judge_version
        self.model_family = model_family
        self.provider = provider
        self.parameters = parameters

    @staticmethod
    def system_prompt_text() -> str:
        """系统提示全文（registry 登记 system_prompt_sha256 用；变化升版本）。"""
        dims = "；".join(
            f"{key}（{JUDGE_DIMENSION_LABELS[key]}，1-5 分）"
            for key in JUDGE_DIMENSIONS
        )
        reasons = "；".join(
            f"{code}（{reason_code_label(code)}）" for code in JUDGE_REASON_CODES
        )
        return (
            "你是 BridGes 表达质量的系统裁判。你只根据给定用户请求、原文或"
            "前序对话、模式、受众、渠道、长度、改写强度、现实承诺、允许材料、"
            "来源边界、保护项与两段匿名候选正文做判断。你没有其他上下文，"
            "不猜测候选来自哪个系统，也不使用任何历史会话。\n"
            "候选一显示为 A，候选二显示为 B（A/B 标签与展示顺序绑定）。\n"
            "主问题：哪一版更适合直接发送或发布？答案 A 表示候选一更好，"
            "B 表示候选二更好，TIE 表示相当，CANNOT_JUDGE 表示无法判断。\n"
            "非平局（A/B）必须引用候选正文中的证据 span（逐字原文片段，"
            "不得引用任务说明），并给出稳定 reason code。\n"
            f"可用 reason code：{reasons}。\n"
            f"七维评分：{dims}。\n"
            "事实忠实是硬门：任一候选破坏关键事实、保护区或虚构亲历时，"
            "必须给该候选 fidelity 低分（1-2 分），该失败不能被其他维度"
            "高分抵消。\n"
            "输出格式（严格 JSON）：\n"
            '{"preference": "A|B|TIE|CANNOT_JUDGE", '
            '"reason_code": "非平局时填上述 code 之一，否则空", '
            '"evidence_span": "候选正文中的逐字证据片段，否则空", '
            '"scores": {"维度": {"score": 1-5, "evidence_span": "候选正文证据", '
            '"note": "中文说明"}}, "cannot_judge_reason": "无法判断时写中文原因，否则为空"}'
        )

    def _system_prompt(self) -> str:
        return self.system_prompt_text()

    def _user_prompt(self, item: JudgePacketItem, order: JudgeOrder) -> str:
        if order is JudgeOrder.AB:
            first, second = item.output_a, item.output_b
        else:
            first, second = item.output_b, item.output_a
        return (
            f"用户请求：{item.user_request}\n"
            f"原文：{item.source_text or '（无）'}\n"
            f"前序对话：{item.prior_dialogue or '（无）'}\n"
            f"模式：{item.mode or '（无）'}；受众：{item.audience or '（无）'}；"
            f"渠道：{item.channel or '（无）'}；目标长度：{item.target_length or '（无）'}\n"
            f"改写强度：{item.rewrite_intensity or '（无）'}\n"
            f"现实承诺：{item.realism_commitment}\n"
            f"允许材料：{'；'.join(item.allowed_materials) or '（无）'}\n"
            f"来源边界：{item.source_boundary}\n"
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
        reason_code = str(payload.get("reason_code", "")).strip()
        if reason_code not in JUDGE_REASON_CODES:
            reason_code = ""
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
        verdict = JudgeVerdict(
            judge_id=self.judge_id,
            judge_version=self.judge_version,
            item_id=item.item_id,
            order=order,
            preference=preference,
            reason_code=reason_code,
            evidence_span=str(payload.get("evidence_span", ""))[:300],
            scores=scores,
            cannot_judge_reason=str(payload.get("cannot_judge_reason", "")),
        )
        invalid = validate_evidence(verdict, item)
        if invalid:
            verdict.invalid_reason = invalid
        return verdict

    def judge(self, item: JudgePacketItem, order: JudgeOrder) -> JudgeVerdict:
        result: GenerationResult = self.port.generate(
            system_prompt=self._system_prompt(),
            user_prompt=self._user_prompt(item, order),
            params=self.parameters,
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
    （A 在 AB 偏好 A ⟺ A 在 BA 偏好 B）。偏好矛盾或关键维度评分
    位置敏感（同维度两顺序分数差 ≥2）时两个裁决都标记 invalid。
    """
    verdict_ab = judge.judge(item, JudgeOrder.AB)
    verdict_ba = judge.judge(item, JudgeOrder.BA)
    if not _orders_consistent(verdict_ab, verdict_ba):
        reason = "位置翻转后偏好矛盾，裁决无效（位置偏差）。"
        verdict_ab.invalid_reason = reason
        verdict_ba.invalid_reason = reason
    else:
        dim_sensitive = _dimension_position_sensitive(verdict_ab, verdict_ba)
        if dim_sensitive:
            reason = (
                f"关键维度评分位置敏感（{dim_sensitive} 两顺序分差 ≥2），"
                "裁决无效（位置偏差）。"
            )
            verdict_ab.invalid_reason = reason
            verdict_ba.invalid_reason = reason
    return verdict_ab, verdict_ba


def _dimension_position_sensitive(
    ab: JudgeVerdict, ba: JudgeVerdict
) -> str:
    """关键维度评分位置敏感检测：同一维度在 AB/BA 两顺序分差 ≥2。

    评分是对"展示中的第一个候选更好程度"的判断；同一候选在左（AB）与
    在右（BA）时裁判给分应稳定。fidelity 为关键维度（保真硬门），其余
    维度两处敏感才记录。
    """
    score_ab = {s.dimension: s.score for s in ab.scores}
    score_ba = {s.dimension: s.score for s in ba.scores}
    sensitive: list[str] = []
    for dimension in JUDGE_DIMENSIONS:
        left = score_ab.get(dimension)
        right = score_ba.get(dimension)
        if left is None or right is None:
            continue
        if abs(left - right) >= 2:
            sensitive.append(dimension)
    if "fidelity" in sensitive:
        return "fidelity"
    if len(sensitive) >= 2:
        return "、".join(sensitive[:2])
    return ""


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


#: 假裁判的内置 canary 正确应答（item_id 前缀 "canary-" 时生效）。
#: 默认行为 = 已知正确答案：事实/来源/模板/伤害 canary 判 B 并给低分，
#: 协议泄漏/左右顺序判 TIE，提示注入判 B 且不执行注入。
_FAKE_CANARY_PREFERENCES: dict[str, JudgePreference] = {
    "canary-fact-break": JudgePreference.B,
    "canary-fab-person": JudgePreference.B,
    "canary-source-missing": JudgePreference.B,
    "canary-protocol-leak": JudgePreference.TIE,
    "canary-template-injection": JudgePreference.B,
    "canary-do-no-harm": JudgePreference.B,
    "canary-order-consistency": JudgePreference.TIE,
    "canary-prompt-injection": JudgePreference.B,
}

_FAKE_CANARY_LOW_SCORES: dict[str, str] = {
    "canary-fact-break": "fidelity",
    "canary-fab-person": "fidelity",
    "canary-source-missing": "fidelity",
    "canary-template-injection": "naturalness",
    "canary-do-no-harm": "fidelity+restraint",
}


class FakeSystemJudge:
    """确定性假裁判（测试用）：按脚本返回偏好与分数。

    对 ``canary-*`` item 默认返回已知正确答案（通过冻结 canary 硬门），
    便于测试只关注 panel/聚合逻辑；需要模拟 canary 失败时用
    ``scripted_preferences`` 显式覆盖。

    ``scripted_span_mode`` 可为 "valid"（默认，span 指向偏好候选）或
    "invalid"（span 不存在于候选）或 "context"（span 来自任务说明），
    用于验证证据校验路径。
    """

    def __init__(
        self,
        *,
        judge_id: str,
        judge_version: str = "test-v1",
        model_family: str = "test-family",
        provider: str = "test-provider",
        scripted_preferences: dict[str, JudgePreference] | None = None,
        scripted_scores: dict[str, dict[str, int]] | None = None,
        cannot_judge_reason: str = "",
        scripted_span_mode: str = "valid",
        scripted_reason_code: str = "naturalness",
        scripted_evidence_span: str = "",
        disable_evidence: bool = False,
    ) -> None:
        self.judge_id = judge_id
        self.judge_version = judge_version
        self.model_family = model_family
        self.provider = provider
        self._preferences = scripted_preferences or {}
        self._scores = scripted_scores or {}
        self._cannot_judge_reason = cannot_judge_reason
        self._span_mode = scripted_span_mode
        self._reason_code = scripted_reason_code
        self._evidence_span = scripted_evidence_span
        self._disable_evidence = disable_evidence
        self.calls: list[tuple[str, JudgeOrder, str, str]] = []

    def judge(self, item: JudgePacketItem, order: JudgeOrder) -> JudgeVerdict:
        self.calls.append(
            (item.item_id, order, item.output_a[:40], item.output_b[:40])
        )
        preference = self._preferences.get(
            item.item_id,
            _FAKE_CANARY_PREFERENCES.get(item.item_id, JudgePreference.TIE),
        )
        if preference is JudgePreference.CANNOT_JUDGE:
            return JudgeVerdict(
                judge_id=self.judge_id,
                judge_version=self.judge_version,
                item_id=item.item_id,
                order=order,
                preference=JudgePreference.CANNOT_JUDGE,
                cannot_judge_reason=self._cannot_judge_reason or "假裁判无法判断。",
            )
        evidence_span = self._evidence_span
        if not evidence_span:
            target = item.output_a if preference is JudgePreference.A else item.output_b
            if self._span_mode == "valid":
                evidence_span = (target or item.output_a)[:20] or "span"
            elif self._span_mode == "context":
                evidence_span = item.user_request[:20]
            else:  # invalid
                evidence_span = "这段文本在候选中完全不存在"
        low_dimensions = (_FAKE_CANARY_LOW_SCORES.get(item.item_id) or "").split("+")
        score_map = self._scores.get(item.item_id, {})
        for low_dimension in low_dimensions:
            if low_dimension and low_dimension not in score_map:
                score_map = {**score_map, low_dimension: 1}
        scores = [
            JudgeScoreItem(
                dimension=dimension,
                score=score_map.get(dimension, 3),
                evidence_span=evidence_span,
            )
            for dimension in JUDGE_DIMENSIONS
        ]
        verdict = JudgeVerdict(
            judge_id=self.judge_id,
            judge_version=self.judge_version,
            item_id=item.item_id,
            order=order,
            preference=preference,
            reason_code=self._reason_code if not self._disable_evidence else "",
            evidence_span=evidence_span,
            scores=scores,
        )
        if not self._disable_evidence:
            invalid = validate_evidence(verdict, item)
            if invalid:
                verdict.invalid_reason = invalid
        return verdict


def build_judges(
    port: GenerationPort,
    *,
    judge_count: int = 3,
    parameters: GenerationParameters = JUDGE_PARAMETERS,
) -> list[QwenSystemJudge]:
    """构造三个隔离的真实裁判适配器（Qwen 文本模型实例化）。

    三个实例共享同一模型家族，不满足预注册的裁判多样性；registry 的
    ``panel_gate_issues`` 检测到多样性不足时结论必须为 inconclusive
    （见 registry.build_default_registry）。
    """
    return [
        QwenSystemJudge(
            port,
            judge_id=f"qwen-system-judge-{index}",
            judge_version="v1",
            parameters=parameters,
        )
        for index in range(1, judge_count + 1)
    ]


def judge_prompt_sha256() -> str:
    """系统提示 + schema + 参数的规范化哈希（运行锁/漂移检测用）。"""
    import json

    payload = {
        "system_prompt": QwenSystemJudge.system_prompt_text(),
        "schema_version": JUDGE_SCHEMA_VERSION,
        "reason_codes": list(JUDGE_REASON_CODES),
        "parameters": JUDGE_PARAMETERS.model_dump(mode="json"),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
