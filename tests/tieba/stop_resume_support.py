"""Shared fixtures for issue 10 stop/resume tests."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from bridges.ai.model_quota import QuotaVerificationBasis, RunModelQuota
from bridges.contracts.ai import ModelCallResult, ModelCallStatus
from bridges.kernel.contracts import (
    ArtifactTrust,
    InputDependency,
    NodeArtifact,
    RecipeInputs,
)
from bridges.tieba.contracts import (
    ReadStatus,
    TiebaPostProjection,
    TiebaReadResult,
    TiebaReply,
    TiebaSearchHit,
)
from bridges.tieba.recipe import (
    TIEBA_RECIPE_ID,
    TIEBA_RECIPE_VERSION,
    build_tieba_recipe,
)
from bridges.tieba.service import TiebaResearchService
from tests.kernel.support import ACCOUNT, CONVERSATION, NOW
from tests.tieba.support import CONFIRMED_URL
from tests.tieba.support import _read_result as _fake_read_result

SECOND_URL = "https://tieba.baidu.com/p/10745250787"


def _quota() -> RunModelQuota:
    return RunModelQuota(
        model_id="verified-model",
        context_window=16000,
        max_input_tokens=12000,
        verification_basis=QuotaVerificationBasis.FACTORY_MATRIX,
    )


def _summary_post() -> TiebaPostProjection:
    return TiebaPostProjection(
        thread_id="10745250786",
        url=CONFIRMED_URL,
        title="宿舍条件",
        affiliation_evidence="页面声明所属贴吧为「华东交通大学吧」",
        read_status=ReadStatus.READ,
        pages_read=2,
        pages_limit=2,
        pages_complete=True,
        total_pages=2,
        replies_obtained=True,
        replies=[
            TiebaReply(
                floor=1,
                reply_id="r1",
                posted_at="2026-09-01",
                content="北区宿舍有空调，热水要刷卡。",
            )
        ],
        retrieved_at=datetime(2026, 9, 2, tzinfo=UTC),
    )


def _hit(url: str = CONFIRMED_URL) -> TiebaSearchHit:
    return TiebaSearchHit(url=url, title="宿舍条件", snippet="")


def _read_result(url: str) -> TiebaReadResult:
    return _fake_read_result(
        url=url,
        status=ReadStatus.READ,
        title="宿舍条件",
        replies=(
            TiebaReply(
                floor=1,
                reply_id="r1",
                posted_at="2026-09-01",
                content="北区宿舍有空调，热水要刷卡。",
            ),
        ),
        pages_read=2,
        total_pages=2,
    )



def _artifact(
    *,
    node: str,
    inputs: RecipeInputs,
    dependencies: dict[str, NodeArtifact],
    payload: dict[str, Any],
    run_id: str,
    recipe_version: str = TIEBA_RECIPE_VERSION,
    trust: ArtifactTrust = ArtifactTrust.EVIDENCE_BOUND,
) -> NodeArtifact:
    spec = {item.name: item for item in build_tieba_recipe().nodes}[node]
    return NodeArtifact.build(
        account_id=inputs.account_id,
        conversation_id=inputs.conversation_id,
        run_id=run_id,
        task_id=inputs.task_id,
        task_version=inputs.task_version,
        recipe_id=TIEBA_RECIPE_ID,
        recipe_version=recipe_version,
        node=node,
        artifact_type=spec.artifact_type,
        capability_version=spec.capability_version,
        trust_state=trust,
        input_key=spec.input_key(inputs),
        input_deps=tuple(
            InputDependency(
                node=name,
                artifact_id=artifact.artifact_id,
                content_hash=artifact.content_hash,
            )
            for name, artifact in dependencies.items()
        ),
        source_refs=(),
        read_scope="test",
        requirement_coverage=(),
        unconfirmed=(),
        error=None,
        payload=payload,
        now=NOW,
    )


def _inputs(
    *,
    run_id: str,
    task_id: str | None = None,
    task_version: int | None = None,
    artifacts: dict[str, NodeArtifact] | None = None,
) -> RecipeInputs:
    return RecipeInputs(
        account_id=ACCOUNT,
        conversation_id=CONVERSATION,
        run_id=run_id,
        user_message_id="msg-user",
        user_content="华东交通大学吧 宿舍条件",
        task_id=task_id,
        task_version=task_version,
        wait_identity=None,
        artifacts=artifacts or {},
    )




class _SummaryGateway:
    def __init__(self) -> None:
        self.fail = True
        self.calls = 0

    def invoke(self, *args: Any, **kwargs: Any) -> ModelCallResult:
        self.calls += 1
        if self.fail:
            raise RuntimeError("上游模型超时")
        claim = {
            "kind": "finding",
            "text": "一条宿舍经历提到有空调。",
            "citation_ids": ["E1"],
        }
        outputs = [
            {"claims": [claim]},
            {"summary": "一条宿舍经历提到有空调。", "claims": [claim]},
            {"supported": [0, 1]},
        ]
        return ModelCallResult(
            status=ModelCallStatus.SUCCESS, output=outputs[self.calls - 1]
        )


def _service(*, search: Any, reader: Any, gateway: Any) -> TiebaResearchService:
    return TiebaResearchService(
        search=search,
        reader=reader,
        model_gateway=gateway,
        clock=lambda: NOW,
    )


def _run(
    service: TiebaResearchService,
    repo: Any,
    *,
    run_id: str,
    assistant_id: str,
) -> None:
    service.run(
        repo=repo,
        account_id=ACCOUNT,
        conversation_id=CONVERSATION,
        user_message_id="msg-user",
        assistant_message_id=assistant_id,
        run_context=SimpleNamespace(run_id=run_id),
        run_model_id="verified-model",
        model_quota=_quota(),
        emit_node=lambda *_args: None,
        stop_event=None,
    )

