"""R05：上下文检查点已提交后失联，撤回照片再恢复同一旧运行。"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.chat.checkpoints import RepositoryCheckpointSaver
from tests.chat.test_improvement14_material_reads import _send_photo_turn
from tests.chat.test_v2_05_photo_attachments import (
    _app,
    _CapturingAdapter,
    _gateway_with,
    _register,
    _start_conversation,
    _upload_draft,
)


class ProcessLost(BaseException):
    """模拟进程死亡，避免普通异常被收敛为新重试运行。"""


class CrashBeforeAnswer(_CapturingAdapter):
    def stream_call(self, capability: Any, run_context: Any, payload: Any) -> Any:
        self.payloads.append(payload)
        raise ProcessLost()
        yield  # 保持生成器协议；崩溃发生在首次迭代。


def test_r05_revoked_photo_is_not_reused_after_checkpoint_resume(
    tmp_path: Path, monkeypatch: Any, generation_helpers: dict[str, Any]
) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        account = _register(client, "r05-resume")
        adapter = _CapturingAdapter()
        service = app.state.chat_service
        service._gateway = _gateway_with(adapter)
        conversation_id = _start_conversation(client, app)
        draft = _upload_draft(client, upload_id="r05-photo").json()
        initial = _send_photo_turn(
            client,
            app,
            generation_helpers,
            conversation_id,
            object_id=draft["object_id"],
            content="这张图里是什么？",
        )
        crash = CrashBeforeAnswer()
        service._gateway = _gateway_with(crash)
        sent = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "刚才那张照片左下角的小字是什么？"},
        ).json()
        run_id = sent["run_id"]
        with pytest.raises(ProcessLost):
            app.state.generation_executor.run_tick()
        assert crash.payloads and "image_url" in str(crash.payloads[-1])
        run = service._repo.get_generation_run(account["id"], run_id)
        assert run is not None and run.status == "running"
        saver = RepositoryCheckpointSaver(
            app.state.bridges_database,
            account_id=account["id"],
            conversation_id=conversation_id,
            run_id=run_id,
        )
        checkpoint = saver.get_tuple(saver.run_config())
        assert checkpoint is not None
        assert checkpoint.checkpoint["channel_values"]["compiled_messages"]
        app.state.chat_attachment_service.delete(
            account["id"],
            conversation_id,
            draft["object_id"],
            message_id=initial["user_message"]["message_id"],
        )
        past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        database = app.state.bridges_database
        with database.transaction():
            database.connection.execute(
                "UPDATE generation_runs SET lease_expires_at = ? WHERE run_id = ?", (past, run_id)
            )
            database.connection.execute(
                "UPDATE task_claims SET lease_expires_at = ? WHERE task_key = ?",
                (past, f"generation:{run_id}"),
            )
        resumed = _CapturingAdapter()
        service._gateway = _gateway_with(resumed)
        generation_helpers["drive"](app)
        final_run = service._repo.get_generation_run(account["id"], run_id)
        assert final_run is not None and final_run.run_id == run_id
        assert final_run.attempt_count == 2
        # 可安全终止，或重新编译为明确缺口；任何路线均不得重发撤回照片。
        assert all("image_url" not in str(payload) for payload in resumed.payloads)
        if resumed.payloads:
            assert "当前无法读取" in str(resumed.payloads[-1])
            assert "不得用旧描述代替" in str(resumed.payloads[-1])
        else:
            assert final_run.status in {"failed", "stopped"}
