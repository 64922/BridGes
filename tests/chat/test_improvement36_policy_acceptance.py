"""工单 36 独立验收：真实总结采用路径、撤回竞争和独立重试。"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.chat.test_improvement19_purpose_aware_slice import BREVITY
from tests.chat.test_v2_05_photo_attachments import _app
from tests.chat.test_v2_18_study_tutoring import _ask, _retry, _start
from tests.chat.test_v2_20_study_summary import SummaryGateway


class PolicyGateway(SummaryGateway):
    def __init__(self) -> None:
        super().__init__()
        self.question_count = 1
        self.summary_payloads: list[dict[str, Any]] = []

    def summarize(self, payload: dict[str, Any]) -> Any:
        self.summary_payloads.append(payload)
        return super().summarize(payload)


def test_real_summary_compiles_adopted_policy_and_keeps_retry_snapshot(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = PolicyGateway()
    service = app.state.chat_service
    service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        account = client.get("/auth/session").json()["account"]["id"]
        app.state.atomic_profile_service.remember(account, BREVITY, source_message_id="m36")
        _ask(client, app, endpoint, "学完了")
        gateway.fail_summary = "timeout"
        failed = _ask(client, app, endpoint, "a 是斜率")
        message = failed["messages"][-1]
        assert message["status"] == "error"
        run = service._repo.get_run_by_message(account, message["message_id"])
        policy = run.config["global_writing_policy"]
        adopted = run.config["adopted_profile_slice"]
        assert policy["snapshot_complete"] is True
        assert policy["profile_slice_id"] == adopted["slice_id"]
        assert policy["profile_revocation_version"] == adopted["revocation_version"]
        payload = gateway.summary_payloads[-1]
        rendered = str(payload["messages"])
        assert BREVITY in rendered and "用户长期偏好简短直接" in rendered
        assert "本轮没有可用画像信息" not in rendered
        assert payload["max_tokens"] == policy["output_tokens"]
        gateway.fail_summary = None
        done = _retry(client, app, endpoint)
        assert done["messages"][-1]["status"] == "done"
        retry = service._repo.get_run_by_message(account, done["messages"][-1]["message_id"])
        assert retry.config["global_writing_policy"] == policy
        assert [task for task, _ in gateway.review_calls].count("study.grade") == 1


def test_summary_revocation_during_compilation_clears_rules_and_data(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = PolicyGateway()
    service = app.state.chat_service
    service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        account = client.get("/auth/session").json()["account"]["id"]
        atomic = app.state.atomic_profile_service
        atomic.remember(account, BREVITY, source_message_id="m36-race")
        _ask(client, app, endpoint, "学完了")
        original = service.compile_turn_context
        revoked = False

        def compile_then_revoke(run: Any, **kwargs: Any) -> Any:
            nonlocal revoked
            result = original(run, **kwargs)
            if not revoked and any(
                item.evidence_id == "study-summary" for item in kwargs.get("evidence", [])
            ):
                item = atomic.list_items(account)[0]
                atomic.delete_item(account, item.profile_item_id, item.version)
                revoked = True
            return result

        monkeypatch.setattr(service, "compile_turn_context", compile_then_revoke)
        done = _ask(client, app, endpoint, "a 是斜率")
        assert done["messages"][-1]["status"] == "done", done["messages"][-1]
        assert revoked
        rendered = str(gateway.summary_payloads[-1]["messages"])
        assert BREVITY not in rendered
        assert "用户长期偏好简短直接" not in rendered
        run = service._repo.get_run_by_message(account, done["messages"][-1]["message_id"])
        assert "adopted_profile_slice" not in run.config
        assert run.config["global_writing_policy"]["profile_items"] == []


def test_summary_retry_after_delete_recompiles_effective_adoption(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = PolicyGateway()
    service = app.state.chat_service
    service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        account = client.get("/auth/session").json()["account"]["id"]
        atomic = app.state.atomic_profile_service
        atomic.remember(account, BREVITY, source_message_id="m36-retry")
        _ask(client, app, endpoint, "学完了")
        gateway.fail_summary = "timeout"
        failed = _ask(client, app, endpoint, "a 是斜率")
        assert failed["messages"][-1]["status"] == "error"
        assert BREVITY in str(gateway.summary_payloads[-1]["messages"])
        item = atomic.list_items(account)[0]
        atomic.delete_item(account, item.profile_item_id, item.version)
        gateway.fail_summary = None
        done = _retry(client, app, endpoint)
        assert done["messages"][-1]["status"] == "done"
        assert BREVITY not in str(gateway.summary_payloads[-1]["messages"])
        assert "用户长期偏好简短直接" not in str(gateway.summary_payloads[-1]["messages"])
        assert [task for task, _ in gateway.review_calls].count("study.grade") == 1
