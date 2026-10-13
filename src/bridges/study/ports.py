"""学习执行使用的类型化聊天能力端口。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from bridges.ai import ModelGateway
from bridges.ai.model_quota import RunModelQuota
from bridges.chat.attachments import ChatAttachmentService
from bridges.chat.context_compiler import ContextEvidence
from bridges.chat.repository import ConversationRepository, GenerationRunRecord
from bridges.profiles.atomic import AtomicProfileService
from bridges.profiles.automatic import AutomaticProfileService
from bridges.retrieval.service import LayeredRetrievalService
from bridges.web_search.service import WebSearchService


class ContextCompiler(Protocol):
    def __call__(
        self,
        run: GenerationRunRecord,
        *,
        system_prompt: str | None = None,
        evidence: list[ContextEvidence] | None = None,
    ) -> tuple[list[dict[str, str]] | None, Mapping[str, Any] | None]: ...


@dataclass(frozen=True, slots=True)
class StudyExecutionPorts:
    """学习图需要的模型、上下文、材料、进度与提交能力。"""

    repository: ConversationRepository
    attachments: ChatAttachmentService
    gateway: ModelGateway
    context_compiler: ContextCompiler
    quota_provider: Callable[[GenerationRunRecord], RunModelQuota | None]
    atomic_profiles: AtomicProfileService | None = None
    automatic_profiles: AutomaticProfileService | None = None
    retrieval: LayeredRetrievalService | None = None
    web_search: WebSearchService | None = None

    def compile_turn_context(
        self,
        run: GenerationRunRecord,
        *,
        system_prompt: str | None = None,
        evidence: list[ContextEvidence] | None = None,
    ) -> tuple[list[dict[str, str]] | None, Mapping[str, Any] | None]:
        return self.context_compiler(
            run, system_prompt=system_prompt, evidence=evidence
        )

    def run_model_quota(self, run: GenerationRunRecord) -> RunModelQuota | None:
        return self.quota_provider(run)
