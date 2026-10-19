"""工单 14：账户删除时贴吧服务的启动栅栏回归。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bridges.chat.repository import ConversationRepository
from bridges.storage.database import BridgesDatabase
from bridges.tieba.contracts import ReadStatus
from bridges.tieba.service_contract import TiebaModuleError
from tests.kernel.support import (
    ACCOUNT,
    ASSISTANT,
    CONVERSATION,
    NOW,
    RUN,
    seed_generation_run,
)
from tests.tieba.stop_resume_support import _hit, _service
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
