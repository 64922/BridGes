"""运行额度与调用合同沿用正式备份、恢复及账户删除路径。"""

from __future__ import annotations

from typing import Any

from harness import Harness

from bridges.chat.repository import ConversationRepository
from bridges.chat.service import ChatService
from tests.chat.test_chat_api import _gateway_with
from tests.chat.test_improvement03_model_quota import _context, _ProgrammableAdapter


def test_quota_and_call_contract_survive_backup_and_account_deletion(tmp_path: Any) -> None:
    harness = Harness(tmp_path)
    repo = ConversationRepository(harness.database)
    service = ChatService(repository=repo, gateway=_gateway_with(_ProgrammableAdapter()))
    conversation = service.create_conversation(harness.acc1)
    _, assistant, _ = service.start_generation(harness.acc1, conversation.conversation_id, "你好")
    run = repo.get_run_by_message(harness.acc1, assistant.message_id)
    assert run is not None and run.config is not None
    quota = run.config["model_quota"]
    list(
        service.stream_generation(
            harness.acc1,
            conversation.conversation_id,
            assistant.message_id,
            _context(run.run_id, account_id=harness.acc1),
        )
    )
    _, backup = harness.backup.create_backup("运行额度备份测试口令")
    harness.database.connection.execute("UPDATE generation_runs SET config_json = '{}'")
    preview = harness.backup.restore_backup("运行额度备份测试口令", backup, confirmation="恢复")
    assert preview.ok, preview.reasons
    restored_repo = ConversationRepository(harness.database)
    restored = restored_repo.get_generation_run(harness.acc1, run.run_id)
    assert restored is not None and restored.config is not None
    assert restored.config["model_quota"] == quota
    locks = restored_repo._run_lock_recorder.list_locks_by_run(harness.acc1, run.run_id)
    assert len(locks) == 1 and locks[0].call_contract is not None
    harness.deletion.delete_account(harness.acc1)
    assert restored_repo.get_generation_run(harness.acc1, run.run_id) is None
    assert restored_repo._run_lock_recorder.list_locks_by_run(harness.acc1, run.run_id) == []
    assert harness.identity.get_account(harness.acc2) is not None
