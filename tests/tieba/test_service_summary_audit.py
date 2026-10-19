"""真实 SQLite 与节点流程验证总结调用审计及账户、运行绑定。"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bridges.ai.model_quota import QuotaVerificationBasis, RunModelQuota
from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder
from bridges.chat.repository import ConversationRepository
from bridges.contracts.ai import ModelCallResult, ModelCallStatus, ModelRunLock
from bridges.contracts.modules import ModuleQueryStatus
from bridges.storage.database import BridgesDatabase
from bridges.tieba.contracts import ReadStatus, TiebaReadResult, TiebaReply, TiebaSearchHit
from bridges.tieba.recipe import TIEBA_RECIPE_VERSION
from bridges.tieba.searching import SearchOutcome, query_record
from bridges.tieba.service import TiebaResearchService
from bridges.tieba.service_contract import TiebaModuleError
from tests.kernel.support import (
    ACCOUNT,
    ASSISTANT,
    CONVERSATION,
    NOW,
    RUN,
    seed_generation_run,
)


@pytest.mark.parametrize("invalid_identity", [None, "account", "run"])
def test_service_persists_every_attempt_only_for_current_identity(
    tmp_path: Path,
    invalid_identity: str | None,
) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    try:
        seed_generation_run(database)
        with database.transaction():
            database.connection.execute(
                "INSERT INTO messages (message_id, conversation_id, account_id, role,"
                " status, content, created_at, updated_at)"
                " VALUES ('msg-user', ?, ?, 'user', 'done', '搜集宿舍条件讨论', ?, ?)",
                (CONVERSATION, ACCOUNT, NOW.isoformat(), NOW.isoformat()),
            )
        repo = ConversationRepository(database)
        hit = TiebaSearchHit(
            url="https://tieba.baidu.com/p/100",
            title="宿舍条件",
            snippet="华东交通大学吧",
        )

        class Search:
            def search_public(self, account_id: str, *, query: str, **kwargs: Any) -> SearchOutcome:
                return SearchOutcome(
                    record=query_record(
                        query=query, status=ModuleQueryStatus.SUCCESS, evidence_count=1
                    ),
                    hits=(hit,),
                    candidates=(hit,),
                    rejected=(),
                )

        class Reader:
            def read(self, url: str, **kwargs: Any) -> TiebaReadResult:
                return TiebaReadResult(
                    url=url,
                    thread_id="100",
                    status=ReadStatus.READ,
                    title="宿舍条件",
                    forum_name="华东交通大学吧",
                    retrieved_at=NOW,
                    replies=[
                        TiebaReply(content="宿舍有空调", reply_id="r1", posted_at="2026-01-01"),
                    ],
                )

        class Gateway:
            calls = 0

            def invoke(self, *args: Any, **kwargs: Any) -> ModelCallResult:
                self.calls += 1
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
                    status=ModelCallStatus.SUCCESS,
                    output=outputs[self.calls - 1],
                    lock=ModelRunLock(
                        lock_id=f"call-{self.calls}",
                        run_id="other" if invalid_identity == "run" else RUN,
                        account_id="other" if invalid_identity == "account" else ACCOUNT,
                        project_id="test",
                        capability_name="qwen_structured_output",
                        capability_version="1",
                        actual_model_id="verified-model",
                        region="test",
                        prompt_version="test",
                        input_output_contract="test",
                        call_contract=kwargs["call_contract"],
                        status=ModelCallStatus.SUCCESS,
                        created_at=NOW,
                    ),
                )

        gateway = Gateway()
        service = TiebaResearchService(
            search=Search(), reader=Reader(), model_gateway=gateway, clock=lambda: NOW
        )
        try:
            service.run(
                repo=repo,
                account_id=ACCOUNT,
                conversation_id=CONVERSATION,
                user_message_id="msg-user",
                assistant_message_id=ASSISTANT,
                run_context=SimpleNamespace(run_id=RUN),
                run_model_id="verified-model",
                model_quota=RunModelQuota(
                    model_id="verified-model",
                    context_window=16000,
                    max_input_tokens=12000,
                    verification_basis=QuotaVerificationBasis.FACTORY_MATRIX,
                ),
                emit_node=lambda *_args: None,
                stop_event=None,
            )
        except TiebaModuleError:
            # Invalid audit identity fails the run after the failure projection is saved.
            assert invalid_identity is not None
        else:
            assert invalid_identity is None
        locks = SqliteModelRunLockRecorder(database).list_locks_by_run(ACCOUNT, RUN)
        message = repo.get_message(ACCOUNT, ASSISTANT)
        assert message is not None and message.tieba_research is not None
        if invalid_identity:
            assert not locks
            assert gateway.calls == 1
            assert message.tieba_research["summary"] is None
        else:
            assert len(locks) == gateway.calls == 3
            assert message.tieba_research["summary"] == "一条宿舍经历提到有空调。"
            metadata = message.tieba_research["run_metadata"]
            assert metadata["run_id"] == RUN
            assert metadata["model_call_count"] == 3
            summary_metadata = metadata["summary"]
            assert summary_metadata["contract_version"] == "tieba-summary-run-v1"
            assert summary_metadata["prompt_version"] == "tieba-summary-v8"
            assert summary_metadata["model_evidence_count"] == 1
            assert summary_metadata["model_call_count"] == 3
            assert summary_metadata["supported_claim_count"] == 1
            assert summary_metadata["summary_supported"] is True
            assert metadata["confirmed_post_count"] == 1
            assert metadata["source_urls"] == ["https://tieba.baidu.com/p/100"]
            assert metadata["component_versions"]["browser_protocol"]
            assert all(
                lock.call_contract.recipe_version == TIEBA_RECIPE_VERSION for lock in locks
            )
            assert [lock.business_refs[0].attempt_ordinal for lock in locks] == [1, 2, 3]
            assert all(lock.business_refs[0].object_id == ASSISTANT for lock in locks)
        assert not SqliteModelRunLockRecorder(database).list_locks_by_run("other", RUN)
    finally:
        database.close()
