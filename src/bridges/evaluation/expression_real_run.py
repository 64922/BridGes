"""工单 39：真实模型配对发送器（每场景每臂独立会话，多轮保留前文）。

真实调用只发生在 `RealArmSender`，由入口脚本以 `--real-probes` 显式
开启；发送器采集运行锁、首字延迟与用量，不做模型身份判断。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from bridges.contracts.ai import ModelRunLock
from bridges.evaluation.expression_corpus import ExpressionScenario
from bridges.evaluation.expression_gates import GateResult, evaluate_hard_gates
from bridges.evaluation.expression_policy_arms import ArmPolicyCompiler, StrategyArm
from bridges.evaluation.expression_review import ScenarioTranscript, TranscriptTurn


@dataclass(frozen=True)
class TurnMeasurement:
    arm: str
    scenario_id: str
    turn_index: int
    status: str
    answer_chars: int
    latency_ms: int
    first_token_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    retry_count: int
    capability_names: tuple[str, ...]
    model_id: str | None
    error_code: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "scenario_id": self.scenario_id,
            "turn_index": self.turn_index,
            "status": self.status,
            "answer_chars": self.answer_chars,
            "latency_ms": self.latency_ms,
            "first_token_ms": self.first_token_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "retry_count": self.retry_count,
            "capability_names": list(self.capability_names),
            "model_id": self.model_id,
            "error_code": self.error_code,
        }

    @property
    def chat_calls(self) -> int:
        return sum(1 for name in self.capability_names if name == "qwen_text_chat")

    @property
    def total_calls(self) -> int:
        return len(self.capability_names)


@dataclass
class ArmRunResult:
    scenario: ExpressionScenario
    arm: StrategyArm
    transcript: ScenarioTranscript
    measurements: list[TurnMeasurement]
    locks: list[ModelRunLock]
    gates: list[GateResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario.scenario_id,
            "arm": self.arm.value,
            "turns": [
                {
                    "user": turn.user,
                    "assistant": turn.assistant,
                    "status": turn.status,
                    "error_code": turn.error_code,
                }
                for turn in self.transcript.turns
            ],
            "measurements": [item.to_dict() for item in self.measurements],
            "gates": [gate.to_dict() for gate in self.gates],
        }


class RealArmSender:
    """真实模型配对发送器：每场景每臂独立会话，多轮保留前文。"""

    def __init__(self, gateway: Any, *, account_id: str = "eval39-account") -> None:
        self._gateway = gateway
        self._account_id = account_id

    def run(self, scenario: ExpressionScenario, arm: StrategyArm) -> ArmRunResult:
        from datetime import UTC as _UTC
        from datetime import datetime as _datetime

        from bridges.chat.repository import ConversationRepository
        from bridges.chat.service import ChatService
        from bridges.contracts.chat import ChatMode
        from bridges.contracts.projects import ObjectDomain
        from bridges.contracts.workflows import RunContextEnvelope
        from bridges.profiles.adapters import InMemoryProfileRepository
        from bridges.profiles.atomic import (
            AtomicProfileService,
            InMemoryAtomicProfileRepository,
        )
        from bridges.profiles.automatic import (
            AutomaticProfileService,
            InMemoryAutomaticProfileRepository,
        )
        from bridges.profiles.four_dimensions import (
            FourDimensionProfileService,
            InMemoryFourDimensionProfileRepository,
        )
        from bridges.storage.database import BridgesDatabase

        database = BridgesDatabase(":memory:")
        database.initialize()
        conversations = ConversationRepository(database)
        four = FourDimensionProfileService(
            InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
        )
        atomic = AtomicProfileService(four, InMemoryAtomicProfileRepository())
        automatic = AutomaticProfileService(
            four_dimension_service=four,
            repository=InMemoryAutomaticProfileRepository(),
            atomic_profile_service=atomic,
        )
        now = _datetime.now(_UTC)
        for index, fact in enumerate(scenario.profile_facts):
            atomic.remember(
                self._account_id,
                fact,
                source_message_id=f"{scenario.scenario_id}-profile-{index}",
                source_at=now,
            )
        service = ChatService(
            repository=conversations,
            gateway=self._gateway,
            four_dimension_profile_service=four,
            atomic_profile_service=atomic,
            automatic_profile_service=automatic,
            writing_policy_compiler=ArmPolicyCompiler(arm),  # type: ignore[arg-type]
        )
        mode = ChatMode.STUDY if scenario.mode == "study" else ChatMode.COMPANION
        conversation = service.create_conversation(self._account_id, mode=mode)

        turns: list[TranscriptTurn] = []
        measurements: list[TurnMeasurement] = []
        observed_locks: list[ModelRunLock] = []
        gates: list[GateResult] = []
        for turn_index, user_text in enumerate(scenario.turns, start=1):
            started = time.monotonic()
            user, assistant, _ = service.start_generation(
                self._account_id, conversation.conversation_id, user_text
            )
            context = RunContextEnvelope(
                run_id=f"eval39-{scenario.scenario_id}-{arm.value}-{turn_index}",
                account_id=self._account_id,
                project_id=conversation.conversation_id,
                workflow_name="chat",
                workflow_version="1",
                object_domain=ObjectDomain.PERSONAL_VAULT,
                submitted_at=_datetime.now(_UTC),
            )
            first_token_ms: int | None = None
            error_code: str | None = None
            turn_locks: list[ModelRunLock] = []
            for event in service.stream_generation(
                self._account_id,
                conversation.conversation_id,
                assistant.message_id,
                context,
                until_user_message_id=user.message_id,
            ):
                kind = getattr(event, "kind", None)
                if kind == "error":
                    error_code = getattr(event, "error_code", None)
                if kind == "delta" and first_token_ms is None:
                    first_token_ms = int((time.monotonic() - started) * 1000)
                stage_first = getattr(event, "first_token_ms", None)
                if first_token_ms is None and isinstance(stage_first, int):
                    first_token_ms = stage_first
                lock = getattr(event, "lock", None)
                if lock is not None:
                    turn_locks.append(lock)
            latency_ms = int((time.monotonic() - started) * 1000)
            final = service.message_projection(self._account_id, assistant.message_id)
            answer = final.content if final is not None else ""
            status = final.status.value if final is not None else "error"
            if final is not None and final.error_code:
                error_code = final.error_code
            observed_locks.extend(turn_locks)
            usage_input = sum(
                int((lock.usage or {}).get("prompt_tokens") or 0) for lock in turn_locks
            )
            usage_output = sum(
                int((lock.usage or {}).get("completion_tokens") or 0) for lock in turn_locks
            )
            measurements.append(
                TurnMeasurement(
                    arm=arm.value,
                    scenario_id=scenario.scenario_id,
                    turn_index=turn_index,
                    status=status,
                    answer_chars=len(answer),
                    latency_ms=latency_ms,
                    first_token_ms=first_token_ms,
                    input_tokens=usage_input if turn_locks else None,
                    output_tokens=usage_output if turn_locks else None,
                    retry_count=sum(int(lock.retry_count or 0) for lock in turn_locks),
                    capability_names=tuple(lock.capability_name for lock in turn_locks),
                    model_id=(
                        turn_locks[0].actual_model_id if turn_locks else None
                    ),
                    error_code=error_code,
                )
            )
            turns.append(
                TranscriptTurn(
                    user=user_text,
                    assistant=answer,
                    status=status,
                    error_code=error_code,
                    tool_outcome=scenario.tool_outcome.value,
                )
            )
            gates.extend(
                evaluate_hard_gates(
                    scenario,
                    turn_index=turn_index,
                    answer=answer,
                    status=status,
                )
            )
        transcript = ScenarioTranscript(
            scenario_id=scenario.scenario_id,
            title=scenario.title,
            category=scenario.category.value,
            formal_path=scenario.formal_path,
            arm_id=arm.value,
            turns=tuple(turns),
        )
        return ArmRunResult(
            scenario=scenario,
            arm=arm,
            transcript=transcript,
            measurements=measurements,
            locks=observed_locks,
            gates=gates,
        )



__all__ = [
    "ArmRunResult",
    "RealArmSender",
    "TurnMeasurement",
]
