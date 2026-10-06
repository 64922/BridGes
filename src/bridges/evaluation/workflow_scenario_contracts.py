"""工作流场景与外部门的类型合同。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ExternalGate(StrEnum):
    """需要真实外部服务探测的上线门（工单 42 任务 6）。"""

    ARXIV_FULL_TEXT = "arxiv.full_text"
    AMAP_CAMPUS_ROUTES = "amap.campus_routes"
    TIEBA_REPLIES = "tieba.replies"
    PUBLIC_JOBS = "jobs.public_detail"
    VIDEO_INTRO = "video.intro"
    GITHUB_FILES = "github.files"
    MODEL_CAPABILITIES = "model.configured_capabilities"
    WEB_SEARCH = "web_search.tavily"


class EvidenceLayer(StrEnum):
    """场景证据的类型；真实模型与外部探针分开报告。"""

    DETERMINISTIC = "deterministic"
    FAULT_INJECTION = "fault_injection"
    REAL_MODEL = "real_model"
    EXTERNAL_PROBE = "external_probe"


class ZeroTolerance(StrEnum):
    """验收标准中零容忍的缺陷类型。"""

    CROSS_ACCOUNT = "cross_account"
    HARD_CONDITION_BYPASS = "hard_condition_bypass"
    SYSTEM_FAILURE_AS_ERROR = "system_failure_as_student_error"
    DUPLICATE_JUDGEMENT = "duplicate_judgement"
    WRITE_AFTER_STOP = "write_after_stop"


@dataclass(frozen=True)
class Scenario:
    """一个固定场景及其证据绑定。"""

    scenario_id: str
    title: str
    expectation: str
    deterministic_tests: tuple[str, ...] = ()
    fault_injection_tests: tuple[str, ...] = ()
    real_model: bool = False
    external_gates: tuple[ExternalGate, ...] = ()
    zero_tolerance: tuple[ZeroTolerance, ...] = ()
    availability_note: str = ""

    @property
    def tests(self) -> tuple[str, ...]:
        return self.deterministic_tests + self.fault_injection_tests

    @property
    def kind(self) -> str:
        return self.scenario_id[0]

    @property
    def layers(self) -> tuple[EvidenceLayer, ...]:
        found: list[EvidenceLayer] = []
        if self.deterministic_tests:
            found.append(EvidenceLayer.DETERMINISTIC)
        if self.fault_injection_tests:
            found.append(EvidenceLayer.FAULT_INJECTION)
        if self.real_model:
            found.append(EvidenceLayer.REAL_MODEL)
        if self.external_gates:
            found.append(EvidenceLayer.EXTERNAL_PROBE)
        return tuple(found)


@dataclass(frozen=True)
class ZeroToleranceGuard:
    """零容忍项的独立守卫：必须由真实执行过的测试证明。"""

    kind: ZeroTolerance
    description: str
    tests: tuple[str, ...]
