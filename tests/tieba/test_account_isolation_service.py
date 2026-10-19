"""工单 14：账户删除时贴吧服务的启动栅栏回归。"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bridges.chat.repository import ConversationRepository
from bridges.contracts.modules import ModuleQueryStatus
from bridges.storage.database import BridgesDatabase
from bridges.tieba.contracts import (
    ReadStatus,
    TiebaReadResult,
    TiebaReply,
    TiebaSearchHit,
)
from bridges.tieba.searching import SearchOutcome, query_record
from bridges.tieba.service_contract import TiebaModuleError
from tests.kernel.support import (
    ACCOUNT,
    ASSISTANT,
    CONVERSATION,
    NOW,
    RUN,
    seed_generation_run,
)
from tests.tieba.stop_resume_support import _hit, _service, _SummaryGateway
from tests.tieba.support import _FakeReader, _FakeSearchPort, _read_result


def test_service_blocks_resume_before_any_external_work_when_deleting(
    tmp_path: Path,
) -> None:
    database = BridgesDatabase(tmp_path / "account-deleting.db")
    database.initialize()
    try:
        seed_generation_run(database)
        with database.transaction():
            database.connection.execute(
                "INSERT INTO messages (message_id, conversation_id, account_id, role,"
                " status, content, created_at, updated_at)"
                " VALUES ('msg-user', ?, ?, 'user', 'done', ?, ?, ?)",
                (
                    CONVERSATION,
                    ACCOUNT,
                    "华东交通大学吧 宿舍条件怎么样",
                    NOW.isoformat(),
                    NOW.isoformat(),
                ),
            )
        repo = ConversationRepository(database)
        search = _FakeSearchPort(hits=[_hit()])
        reader = _FakeReader(
            {
                "https://tieba.baidu.com/p/10745250786": _read_result(
                    url="https://tieba.baidu.com/p/10745250786",
                    status=ReadStatus.READ,
                )
            }
        )

        class Gateway:
            calls = 0

            def invoke(self, *_args: Any, **_kwargs: Any) -> Any:
                self.calls += 1
                raise AssertionError("账户删除后不得调用模型")

        gateway = Gateway()
        service = _service(
            search=search,
            reader=reader,
            gateway=gateway,
            account_deleting=lambda _account_id: True,
        )
        with pytest.raises(TiebaModuleError) as failure:
            service.run(
                repo=repo,
                account_id=ACCOUNT,
                conversation_id=CONVERSATION,
                user_message_id="msg-user",
                assistant_message_id=ASSISTANT,
                run_context=SimpleNamespace(run_id=RUN),
                run_model_id="verified-model",
                model_quota=None,
                emit_node=lambda *_args: None,
                stop_event=None,
            )

        assert failure.value.code == "account_deleting"
        assert search.queries == []
        assert reader.read_urls == []
        assert gateway.calls == 0
    finally:
        database.close()


def test_same_account_two_conversations_keep_task_evidence_isolated(
    tmp_path: Path,
) -> None:
    database = BridgesDatabase(tmp_path / "two-conversations.db")
    database.initialize()
    try:
        seed_generation_run(database)
        stamp = NOW.isoformat()
        with database.transaction():
            database.connection.execute(
                "INSERT INTO messages (message_id, conversation_id, account_id, role,"
                " status, content, created_at, updated_at)"
                " VALUES ('msg-user', ?, ?, 'user', 'done', ?, ?, ?)",
                (
                    CONVERSATION,
                    ACCOUNT,
                    "华东交通大学吧 宿舍条件怎么样",
                    stamp,
                    stamp,
                ),
            )
            database.connection.execute(
                "INSERT INTO conversations"
                "(conversation_id, account_id, title, mode, created_at, updated_at)"
                " VALUES ('conv-2', ?, '', 'companion', ?, ?)",
                (ACCOUNT, stamp, stamp),
            )
            database.connection.execute(
                "INSERT INTO messages (message_id, conversation_id, account_id, role,"
                " status, content, created_at, updated_at)"
                " VALUES ('msg-user-2', 'conv-2', ?, 'user', 'done', ?, ?, ?)",
                (
                    ACCOUNT,
                    "华东交通大学吧 食堂怎么样",
                    stamp,
                    stamp,
                ),
            )
            database.connection.execute(
                "INSERT INTO messages (message_id, conversation_id, account_id, role,"
                " status, content, created_at, updated_at)"
                " VALUES ('msg-assistant-2', 'conv-2', ?, 'assistant', 'streaming',"
                " '', ?, ?)",
                (ACCOUNT, stamp, stamp),
            )
            database.connection.execute(
                "INSERT INTO generation_runs"
                "(run_id, account_id, conversation_id, user_message_id,"
                " assistant_message_id, status, lease_owner, lease_expires_at,"
                " stop_requested, created_at, updated_at)"
                " VALUES ('run-2', ?, 'conv-2', 'msg-user-2', 'msg-assistant-2',"
                " 'running', 'worker-1', ?, 0, ?, ?)",
                (
                    ACCOUNT,
                    (NOW + timedelta(minutes=5)).isoformat(),
                    stamp,
                    stamp,
                ),
            )
        repo = ConversationRepository(database)
        hits = {
            "宿舍": TiebaSearchHit(
                url="https://tieba.baidu.com/p/1",
                title="宿舍条件讨论",
                snippet="华东交通大学吧",
            ),
            "食堂": TiebaSearchHit(
                url="https://tieba.baidu.com/p/2",
                title="食堂讨论",
                snippet="华东交通大学吧",
            ),
        }

        class Search:
            def search_public(
                self, account_id: str, *, query: str, **kwargs: Any
            ) -> SearchOutcome:
                del account_id, kwargs
                dorm = "宿舍" in query or "寝室" in query
                hit = hits["宿舍" if dorm else "食堂"]
                return SearchOutcome(
                    record=query_record(
                        query=query,
                        status=ModuleQueryStatus.SUCCESS,
                        evidence_count=1,
                    ),
                    hits=(hit,),
                    candidates=(hit,),
                    rejected=(),
                )

        class Reader:
            def read(self, url: str, **kwargs: Any) -> TiebaReadResult:
                del kwargs
                topic = "宿舍" if url.endswith("/1") else "食堂"
                return TiebaReadResult(
                    url=url,
                    thread_id=url.rsplit("/", 1)[-1],
                    status=ReadStatus.READ,
                    title=f"{topic}讨论",
                    forum_name="华东交通大学吧",
                    replies=[
                        TiebaReply(
                            content=f"{topic}的真实讨论",
                            reply_id=f"r-{topic}",
                            posted_at="2026-01-01",
                        )
                    ],
                    retrieved_at=NOW,
                )

        gateway = _SummaryGateway()
        gateway.fail = False
        service = _service(search=Search(), reader=Reader(), gateway=gateway)
        for run_id, conversation_id, user_message_id, assistant_id in (
            (RUN, CONVERSATION, "msg-user", ASSISTANT),
            ("run-2", "conv-2", "msg-user-2", "msg-assistant-2"),
        ):
            service.run(
                repo=repo,
                account_id=ACCOUNT,
                conversation_id=conversation_id,
                user_message_id=user_message_id,
                assistant_message_id=assistant_id,
                run_context=SimpleNamespace(run_id=run_id),
                run_model_id="verified-model",
                model_quota=None,
                emit_node=lambda *_args: None,
                stop_event=None,
            )

        first = repo.get_message(ACCOUNT, ASSISTANT)
        second = repo.get_message(ACCOUNT, "msg-assistant-2")
        assert first is not None and second is not None
        first_payload = json.dumps(first.tieba_research, ensure_ascii=False)
        second_payload = json.dumps(second.tieba_research, ensure_ascii=False)
        assert "https://tieba.baidu.com/p/1" in first_payload
        assert "https://tieba.baidu.com/p/2" not in first_payload
        assert "https://tieba.baidu.com/p/2" in second_payload
        assert "https://tieba.baidu.com/p/1" not in second_payload
    finally:
        database.close()
