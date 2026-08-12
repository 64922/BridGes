"""真实人味评测 tracer bullet（人味化改进 Issue 01 + Issue 10）。

最短但真实的端到端评测链路：版本化案例 → 真实模型三方生成 →
最小保真检查 → 匿名系统裁判包 → canary 硬门 → 隔离多模型裁判 →
预注册聚合 → 可重放证据。结论只表述为 inconclusive / passed /
failed；样本不足、裁判不足或运行锁不完整时固定为 inconclusive。
"""

from bridges.humanize_eval.cases import HUMANIZE_CASES, HumanizeCase, validate_cases
from bridges.humanize_eval.generation import (
    GenerationParameters,
    GenerationPort,
    GenerationResult,
    QwenGenerationPort,
)
from bridges.humanize_eval.packet import JudgePacket, SealedMapping
from bridges.humanize_eval.runner import HumanizeRunner, RunSummary

__all__ = [
    "HUMANIZE_CASES",
    "HumanizeCase",
    "validate_cases",
    "GenerationParameters",
    "GenerationPort",
    "GenerationResult",
    "QwenGenerationPort",
    "JudgePacket",
    "SealedMapping",
    "HumanizeRunner",
    "RunSummary",
]
