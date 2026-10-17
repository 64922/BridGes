"""工单 18：真实模型画像闭环评测的类型与固定常量。

本包是 ``scripts/run_issue18_profile_loop_evaluation.py`` 的实现层：

- 场景规格与固定样例（``scenes.py``）在运行前冻结，判定标准不因实测调整；
- 真实模型装配与驱动见 ``environment.py`` / ``session.py`` / ``drivers.py``；
- 判定与统计见 ``metrics.py``；运行锁与报告见 ``evidence.py``。

评测资产只使用合成语料；不记录凭据正文、不导出内部提示词、不写真实私人
数据。替身运行（脚本模式与故障注入）与真实模型运行分开标记、分开报告。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

#: 评测资产版本：任何样例、预期或判定口径改动都必须提升版本。
PROFILE_LOOP_EVAL_VERSION = "profile-loop-eval-v1"

#: 场景类别标签（汇总与报告按类别分列）。
KIND_SCENARIO = "scenario"
KIND_HOLDOUT = "holdout"
KIND_NEGATION = "negation"
KIND_MIXED_SUBJECT = "mixed_subject"
KIND_SYNONYM = "synonym"
KIND_PAIRED = "paired"
KIND_FEEDBACK = "feedback"
KIND_BOUNDARY = "boundary"
KIND_RECOVERY = "recovery"
KIND_FAULT = "fault"

#: 对话样例与样例变体的来源划分。
SPLIT_DEV = "dev"
SPLIT_HOLDOUT = "holdout"

MODE_REAL = "real"
MODE_SCRIPTED = "scripted"


@dataclass(frozen=True, slots=True)
class FactGroup:
    """一项预期事实：任一活动条目包含全部/任一关键词即视为捕获。"""

    label: str
    keywords: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExtraSample:
    """场景的补充样例（留出、否定、混合主体或同义表达）。

    - ``expected_groups`` 为空表示该样例不期望写入长期事实；
    - ``forbidden_item_keywords`` 命中活动条目即判失败（误归属/越权）；
    - ``negation_keywords``：条目包含该词时，条目正文必须同时含否定标记
      （用于「我对心理学不感兴趣」这类否定表达）。
    """

    sample_id: str
    kind: str
    turns: tuple[str, ...]
    expected_groups: tuple[FactGroup, ...] = ()
    forbidden_item_keywords: tuple[str, ...] = ()
    negation_keywords: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True, slots=True)
class ScenarioSpec:
    """一条 T 场景的固定样例与判定关键词。"""

    scenario_id: str
    title: str
    contracts: tuple[str, ...]
    responsible_tickets: tuple[str, ...]
    dev_turns: tuple[str, ...]
    expected_groups: tuple[FactGroup, ...]
    forbidden_item_keywords: tuple[str, ...] = ()
    follow_up_question: str | None = None
    adoption_must_include: tuple[str, ...] = ()
    adoption_must_exclude: tuple[str, ...] = ()
    driver: str = "conversation"
    extra_samples: tuple[ExtraSample, ...] = ()
    note: str = ""


@dataclass(frozen=True, slots=True)
class PairedTaskSpec:
    """画像对照实验任务：无/正确/错误画像与反馈修正后画像的配对比较。"""

    task_id: str
    title: str
    correct_seed: str
    wrong_seed: str
    question: str
    correct_answer_keywords: tuple[str, ...]
    wrong_answer_keywords: tuple[str, ...]
    correction_text: str
    recurrence_question: str
    recurrence_must_include: tuple[str, ...]
    recurrence_must_exclude: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Check:
    """一条确定性判定。"""

    check_id: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SceneRecord:
    """一条场景的实测记录（驱动层填充，判定层消费）。"""

    scene_id: str
    kind: str
    title: str
    split: str = SPLIT_DEV
    account_id: str = ""
    turns: list[dict[str, Any]] = field(default_factory=list)
    conversations: list[str] = field(default_factory=list)
    items_after: list[dict[str, Any]] = field(default_factory=list)
    slices: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    elapsed_ms: int = 0
    error: str | None = None
    run_mode: str = MODE_REAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "kind": self.kind,
            "title": self.title,
            "split": self.split,
            "account_id": self.account_id,
            "turns": self.turns,
            "conversations": self.conversations,
            "items_after": self.items_after,
            "slices": self.slices,
            "extra": self.extra,
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
            "run_mode": self.run_mode,
        }


@dataclass(slots=True)
class SceneResult:
    """驱动输出：记录 + 运行期确定性判定。"""

    record: SceneRecord
    checks: list[Check] = field(default_factory=list)
    answer_review: list[dict[str, Any]] = field(default_factory=list)

    def add(self, check_id: str, ok: bool, detail: str = "") -> None:
        self.checks.append(Check(check_id=check_id, passed=bool(ok), detail=detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "record": self.record.to_dict(),
            "checks": [check.to_dict() for check in self.checks],
            "answer_review": self.answer_review,
        }


__all__ = [
    "PROFILE_LOOP_EVAL_VERSION",
    "KIND_SCENARIO",
    "KIND_HOLDOUT",
    "KIND_NEGATION",
    "KIND_MIXED_SUBJECT",
    "KIND_SYNONYM",
    "KIND_PAIRED",
    "KIND_FEEDBACK",
    "KIND_BOUNDARY",
    "KIND_RECOVERY",
    "KIND_FAULT",
    "SPLIT_DEV",
    "SPLIT_HOLDOUT",
    "MODE_REAL",
    "MODE_SCRIPTED",
    "FactGroup",
    "ExtraSample",
    "ScenarioSpec",
    "PairedTaskSpec",
    "Check",
    "SceneRecord",
    "SceneResult",
]
