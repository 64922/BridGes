"""评测裁判层（Issue 40）。

三层裁判：

- 确定性裁判：metrics 模块的规则指标（0-5 分），版本随评分逻辑登记进
  运行锁；评分逻辑变化必须产生新版本（新锁）。
- 固定模型裁判：可选的自动裁判适配器，经模型网关调用固定裁判能力；
  离线/未注册时不可用——自动裁判永远不能成为唯一结论来源（AC-9），
  报告必须携带盲评摘要并显式标注是否只有自动裁判。
- 人工盲评：见 blind_review 模块。

裁判版本进入运行锁的 ``judge_versions``，任何裁判逻辑变化都会生成新的
运行锁与报告版本。
"""

from __future__ import annotations

from bridges.contracts.evaluation_suite import (
    JudgeKind,
    JudgeScore,
    MetricValue,
)

#: 确定性裁判的固定版本（评分逻辑变化必须递增并登记）。
DETERMINISTIC_JUDGE_ID = "deterministic-metrics"
DETERMINISTIC_JUDGE_VERSION = "1"


class DeterministicMetricsJudge:
    """确定性指标裁判：把维度指标映射为量表评分（同一 0-5 分）。"""

    judge_id = DETERMINISTIC_JUDGE_ID
    judge_version = DETERMINISTIC_JUDGE_VERSION
    kind = JudgeKind.DETERMINISTIC

    def score(self, metrics: list[MetricValue]) -> list[JudgeScore]:
        return [
            JudgeScore(
                judge_id=self.judge_id,
                judge_version=self.judge_version,
                kind=self.kind,
                item_id=metric.metric_id,
                score=metric.value,
                scale_id=metric.scale_id,
                rationale="确定性规则指标。",
            )
            for metric in metrics
        ]


__all__ = [
    "DeterministicMetricsJudge",
    "DETERMINISTIC_JUDGE_ID",
    "DETERMINISTIC_JUDGE_VERSION",
]
