"""Evaluation domain module.

T012 工作流重放评测中心（EvaluationService）+ Issue 40 可复现 A/B 科学
评测套件（运行器、套件注册表、SUT、指标、盲评、报告与发布阈值）。
"""

from bridges.evaluation.service import EvaluationError, EvaluationService

__all__ = ["EvaluationError", "EvaluationService"]
