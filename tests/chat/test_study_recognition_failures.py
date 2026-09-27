"""学习书页识别失败的原因、证据与重试行为（issue 04）。

现场证据：用户上传原始三张教材照片后，学习轮次（``aDwgMOgx…``，节点
``study.recognize``）收到 ``client_error_400``，助手消息统一显示"学习处理模型
暂时不可用，请稍后重试。"，且该失败尝试的运行锁没有落库。参数形态本身（OCR／
视觉适配器发明 ``min_pixels``）已在 ``tests/ai/test_image_request_contract.py``
修复；本文件覆盖失败面：

- 参数错误按真实原因呈现，不再提示"等待即可恢复"；失败尝试的运行锁带上
  能力、实际模型与运行关联，供内部复核；
- 可重试错误保留重试能力，重试成功后页数、页序正确且不重复添加已识别页；
- 部分页已识别后失败再重试，不产生重复书页、不虚假推进阶段。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from bridges.ai import ModelGateway
from bridges.ai.adapters import AdapterError
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    ModelCallResult,
    ModelCallStatus,
)
from tests.chat.test_v2_05_photo_attachments import PNG_BYTES, _app, _register, _upload_draft
from tests.chat.test_v2_17_study_pages import StudyGateway, _first

MODEL_ID = "qwen3.7-plus-2026-05-26"


class _RejectingAdapter:
    """确定性替身：按实测的上游参数拒绝失败关闭（不发明内容）。"""

    def __init__(self, code: str = "client_error_400") -> None:
        self._code = code

    def call(self, capability: Any, run_context: Any, payload: dict[str, Any]) -> Any:
        del capability, run_context, payload
        raise AdapterError(
            code=self._code,
            message=(
                "Qwen client error (400). <400> InternalError.Algo.InvalidParameter: "
                "Parameter min_pixels must be greater than or equal to 65536"
            ),
            retryable=False,
        )


class _TransientOnceGateway(StudyGateway):
    """首轮识别返回可重试故障，重试起恢复正常识别。"""

    def __init__(self, *, fail_capability: str, fail_at: int | None = None) -> None:
        super().__init__()
        self._fail_capability = fail_capability
        self._fail_at = fail_at
        self.failures = 0

    def invoke(
        self,
        capability: str,
        version: str,
        context: Any,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> ModelCallResult:
        failing = capability == self._fail_capability and self.failures == 0
        if failing and self._fail_at is not None:
            failing = self.calls.count(self._fail_capability) == self._fail_at - 1
        if failing:
            self.failures += 1
            self.calls.append(capability)
            return ModelCallResult(
                status=ModelCallStatus.RETRYABLE_FAIL,
                error_code="transient",
                error_message="Qwen request timeout.",
            )
        return super().invoke(capability, version, context, payload, **kwargs)


class _FencedVisionGateway(StudyGateway):
    """视觉输出带 ```json 围栏（真实模型实测形态），结构解析仍须成立。"""

    def invoke(
        self,
        capability: str,
        version: str,
        context: Any,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> ModelCallResult:
        result = super().invoke(capability, version, context, payload, **kwargs)
        if capability == "qwen_vision" and result.output is not None:
            return result.model_copy(
                update={
                    "output": {
                        "content": f"```json\n{result.output['content']}\n```\n以上为识别结果。"
                    }
                }
            )
        return result


def test_vision_output_wrapped_in_json_fence_still_parses(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """围栏包装的识别输出不再被当成"结构识别不完整"（issue 04 真实复现）。"""
    app = _app(tmp_path, monkeypatch)
    app.state.chat_service._gateway = _FencedVisionGateway()
    with TestClient(app) as client:
        _register(client, "studyissue04fence")
        draft = _upload_draft(client, upload_id="issue04-fence-page").json()
        first = _first(client, [draft["object_id"]], "issue04-fence-first")
        assert first.status_code == 201, first.text
        conversation_id = first.json()["conversation"]["conversation_id"]
        app.state.generation_executor.run_tick()

        projection = client.get(f"/chat/conversations/{conversation_id}").json()
        assert projection["study"]["stage"] == "tutoring"
        assert [page["ordinal"] for page in projection["study"]["pages"]] == [1]
        assert projection["study"]["pages"][0]["fragments"][0]["text"] == "y=ax+b"


def test_json_object_text_strips_model_wrapping() -> None:
    """包装剥离只做确定性抽取，不改动 JSON 正文本身。"""
    from bridges.study.service import _json_object_text

    body = '{"fragments": [{"text": "y=ax+b"}]}'
    assert _json_object_text(body) == body
    assert _json_object_text(f"```json\n{body}\n```") == body
    assert _json_object_text(f"以下是结果：\n{body}\n请核对。") == body


class _PayloadRecordingGateway(StudyGateway):
    """记录每次调用的载荷，用于核对整页图片调用的超时窗口。"""

    def __init__(self) -> None:
        super().__init__()
        self.payloads: list[tuple[str, dict[str, Any]]] = []

    def invoke(
        self,
        capability: str,
        version: str,
        context: Any,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> ModelCallResult:
        self.payloads.append((capability, dict(payload)))
        return super().invoke(capability, version, context, payload, **kwargs)


def test_study_image_calls_use_full_page_timeout_window(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """整页图片调用带上实测所需的超时窗口（默认 60 秒会让视觉调用超时）。"""
    from bridges.study.service import STUDY_IMAGE_CALL_TIMEOUT_SECONDS

    app = _app(tmp_path, monkeypatch)
    gateway = _PayloadRecordingGateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        _register(client, "studyissue04timeout")
        draft = _upload_draft(client, upload_id="issue04-timeout-page").json()
        first = _first(client, [draft["object_id"]], "issue04-timeout-first")
        assert first.status_code == 201, first.text
        app.state.generation_executor.run_tick()

    image_calls = [
        payload
        for capability, payload in gateway.payloads
        if capability in {"qwen_ocr", "qwen_vision"}
    ]
    assert image_calls, gateway.payloads
    for payload in image_calls:
        assert payload["request_timeout_seconds"] == STUDY_IMAGE_CALL_TIMEOUT_SECONDS


class _FirstPageUnclearGateway(StudyGateway):
    """只有第 1 页有看不清项，其后各页清晰。"""

    def __init__(self) -> None:
        super().__init__(unclear=True)

    def invoke(
        self,
        capability: str,
        version: str,
        context: Any,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> ModelCallResult:
        if capability == "qwen_vision" and self.vision_count >= 1:
            self.unclear = False
        return super().invoke(capability, version, context, payload, **kwargs)


def _upload_pages(client: TestClient, count: int, tag: str) -> list[str]:
    return [
        _upload_draft(
            client, upload_id=f"{tag}-{index}", content=PNG_BYTES + f"-{tag}{index}".encode()
        ).json()["object_id"]
        for index in range(1, count + 1)
    ]


def test_first_upload_keeps_every_page_when_first_page_is_unclear(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """三页首传且第 1 页有看不清项：第 2、3 页是新页，不替换第 1 页。

    旧实现把空消息下的"唯一待确认页"当成补拍目标，后一页会替换掉前一页
    （issue 04 用原始三张教材页实测：最终只剩两页，页数与页序都不对）。
    """
    app = _app(tmp_path, monkeypatch)
    app.state.chat_service._gateway = _FirstPageUnclearGateway()
    with TestClient(app) as client:
        _register(client, "studyissue04keepall")
        pages = _upload_pages(client, 3, "keep")
        first = _first(client, pages, "issue04-keep-first")
        assert first.status_code == 201, first.text
        conversation_id = first.json()["conversation"]["conversation_id"]
        app.state.generation_executor.run_tick()

        study = client.get(f"/chat/conversations/{conversation_id}").json()["study"]
        assert study["stage"] == "awaiting_pages"
        assert study["wait_reason"] == "unclear_page"
        assert [page["ordinal"] for page in study["pages"]] == [1, 2, 3]
        assert [page["object_id"] for page in study["pages"]] == pages
        assert study["pages"][0]["unclear"], "第 1 页的看不清项应保留待补拍"
        assert study["pages"][0]["replaced_object_ids"] == []


def test_single_photo_after_wait_still_replaces_unclear_page(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """等补拍期间补发单张照片：仍按原合同替换待确认页（隐式补齐不回归）。"""
    app = _app(tmp_path, monkeypatch)
    gateway = StudyGateway(unclear=True)
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        _register(client, "studyissue04implicit")
        draft = _upload_draft(client, upload_id="issue04-implicit-old").json()
        first = _first(client, [draft["object_id"]], "issue04-implicit-first")
        conversation_id = first.json()["conversation"]["conversation_id"]
        app.state.generation_executor.run_tick()
        assert client.get(f"/chat/conversations/{conversation_id}").json()["study"][
            "wait_reason"
        ] == "unclear_page"

        gateway.unclear = False
        reshoot = _upload_draft(
            client, upload_id="issue04-implicit-new", content=PNG_BYTES + b"-reshoot"
        ).json()
        sent = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={
                "content": "",
                "attachment_ids": [reshoot["object_id"]],
                "idempotency_key": "issue04-implicit-reshoot",
            },
        )
        assert sent.status_code == 200, sent.text
        app.state.generation_executor.run_tick()

        study = client.get(f"/chat/conversations/{conversation_id}").json()["study"]
        assert study["stage"] == "tutoring"
        assert len(study["pages"]) == 1
        assert study["pages"][0]["object_id"] == reshoot["object_id"]
        assert study["pages"][0]["replaced_object_ids"] == [draft["object_id"]]


def _real_gateway(adapter: Any, name: str) -> ModelGateway:
    """真实网关 + 确定性适配器：失败锁由网关按既有合同构造。"""
    registry = CapabilityRegistry()
    registry.register(
        CapabilityRecord(
            name=name,
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id=MODEL_ID,
            input_schema_version="image-ocr-v1",
            output_schema_version="ocr-text-v1",
            supported_modalities=["text", "image"],
        )
    )
    gateway = ModelGateway(registry)
    gateway.register_adapter(name, "1", adapter)
    return gateway


def _three_pages(client: TestClient) -> list[str]:
    return [
        _upload_draft(
            client, upload_id=f"issue04-page-{index}", content=PNG_BYTES + f"-p{index}".encode()
        ).json()["object_id"]
        for index in (1, 2, 3)
    ]


def _blocks(app: Any, account_id: str) -> list[dict[str, Any]]:
    cursor = app.state.bridges_database.scoped(account_id).execute(
        "SELECT capability_name, status, error_code, actual_model_id"
        " FROM model_run_locks WHERE account_id = ?",
        (account_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def test_parameter_rejection_reports_real_reason_with_linked_lock(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """参数错误给出真实原因与下一步，失败锁带上能力、模型与运行关联。"""
    app = _app(tmp_path, monkeypatch)
    app.state.chat_service._gateway = _real_gateway(_RejectingAdapter(), "qwen_ocr")
    with TestClient(app) as client:
        account = _register(client, "studyissue04param")
        draft = _upload_draft(client, upload_id="issue04-param-page").json()
        first = _first(client, [draft["object_id"]], "issue04-param-first")
        assert first.status_code == 201, first.text
        conversation_id = first.json()["conversation"]["conversation_id"]
        app.state.generation_executor.run_tick()

        projection = client.get(f"/chat/conversations/{conversation_id}").json()
        study = projection["study"]
        assert study["stage"] == "recognizing"
        assert study["pages"] == [] and study["units"] == [] and study["questions"] == []

        message = projection["messages"][-1]
        assert message["status"] == "error"
        assert message["error_code"] == "client_error_400"
        # 参数错误不可重试：说明真实原因与下一步，不再提示"稍后重试"。
        assert "重试不会恢复" in message["error_message"]
        assert "稍后重试" not in message["error_message"]
        # 失败尝试同样留下可复核证据：实际模型与运行锁标识。
        assert message["model_id"] == MODEL_ID
        assert message["run_lock_id"]

        locks = _blocks(app, account["id"])
        assert [(row["capability_name"], row["error_code"]) for row in locks] == [
            ("qwen_ocr", "client_error_400")
        ]
        assert locks[0]["actual_model_id"] == MODEL_ID
        assert locks[0]["status"] == "blocked"


def test_transient_failure_keeps_retry_and_recognizes_three_pages(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """超时类故障保留重试能力；重试成功后三页全部识别并进入预习。"""
    app = _app(tmp_path, monkeypatch)
    gateway = _TransientOnceGateway(fail_capability="qwen_ocr")
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        _register(client, "studyissue04retry")
        pages = _three_pages(client)
        first = _first(client, pages, "issue04-retry-first")
        assert first.status_code == 201, first.text
        conversation_id = first.json()["conversation"]["conversation_id"]
        app.state.generation_executor.run_tick()

        failed = client.get(f"/chat/conversations/{conversation_id}").json()
        assert failed["study"]["stage"] == "recognizing"
        assert failed["study"]["pages"] == []
        message = failed["messages"][-1]
        assert message["status"] == "error" and message["error_code"] == "transient"
        # 可重试故障仍给出重试指引（超时不是参数错误）。
        assert "重试" in message["error_message"]

        retry = client.post(
            f"/chat/conversations/{conversation_id}/messages/"
            f"{message['message_id']}/retry",
            json={"idempotency_key": "issue04-retry-attempt"},
        )
        assert retry.status_code == 200, retry.text
        app.state.generation_executor.run_tick()

        reloaded = client.get(f"/chat/conversations/{conversation_id}").json()
        assert reloaded["study"]["stage"] == "tutoring"
        assert [page["ordinal"] for page in reloaded["study"]["pages"]] == [1, 2, 3]
        assert [page["object_id"] for page in reloaded["study"]["pages"]] == pages
        assert "已识别本节范围" in reloaded["messages"][-1]["content"]


def test_retry_after_partial_failure_does_not_duplicate_pages(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """第 2 页识别失败后重试：已识别的第 1 页不重复添加，页序保持。"""
    app = _app(tmp_path, monkeypatch)
    gateway = _TransientOnceGateway(fail_capability="qwen_vision", fail_at=2)
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        _register(client, "studyissue04partial")
        pages = _three_pages(client)
        first = _first(client, pages, "issue04-partial-first")
        assert first.status_code == 201, first.text
        conversation_id = first.json()["conversation"]["conversation_id"]
        app.state.generation_executor.run_tick()

        failed = client.get(f"/chat/conversations/{conversation_id}").json()
        # 失败不虚报：只有真识别成功的第 1 页在案，阶段停在识别中。
        assert failed["study"]["stage"] == "recognizing"
        assert [page["ordinal"] for page in failed["study"]["pages"]] == [1]

        message = failed["messages"][-1]
        assert message["status"] == "error"
        retry = client.post(
            f"/chat/conversations/{conversation_id}/messages/"
            f"{message['message_id']}/retry",
            json={"idempotency_key": "issue04-partial-attempt"},
        )
        assert retry.status_code == 200, retry.text
        app.state.generation_executor.run_tick()

        reloaded = client.get(f"/chat/conversations/{conversation_id}").json()
        assert reloaded["study"]["stage"] == "tutoring"
        assert [page["ordinal"] for page in reloaded["study"]["pages"]] == [1, 2, 3]
        assert [page["object_id"] for page in reloaded["study"]["pages"]] == pages
        # 已识别的第 1 页被识别为重复并跳过，没有追加成第 4 页。
        assert "检测到1张重复书页，已跳过。" in reloaded["messages"][-1]["content"]


def test_restart_after_partial_failure_recovers_without_duplicating_pages(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """进程重启后恢复：已识别页与阶段来自持久化，重试不重复、不虚假推进。

    第 2 页识别失败时中途重启（同一数据目录重建 app，内存态清空），
    重启后的重试仍按原状态合同收敛：阶段、页序与上下文不因重启改变。
    """
    app = _app(tmp_path, monkeypatch)
    app.state.chat_service._gateway = _TransientOnceGateway(
        fail_capability="qwen_vision", fail_at=2
    )
    with TestClient(app) as client:
        _register(client, "studyissue04restart")
        pages = _three_pages(client)
        first = _first(client, pages, "issue04-restart-first")
        assert first.status_code == 201, first.text
        conversation_id = first.json()["conversation"]["conversation_id"]
        app.state.generation_executor.run_tick()

        failed = client.get(f"/chat/conversations/{conversation_id}").json()
        assert failed["study"]["stage"] == "recognizing"
        assert [page["ordinal"] for page in failed["study"]["pages"]] == [1]
        pages_before_restart = failed["study"]["pages"]
        message_id = failed["messages"][-1]["message_id"]
        assert failed["messages"][-1]["status"] == "error"

    # 模拟进程重启：同一数据目录重建应用，内存注册表与网关都清空。
    restarted = _app(tmp_path, monkeypatch)
    assert restarted is not app
    restarted.state.chat_service._gateway = StudyGateway()
    with TestClient(restarted) as client:
        logged_in = client.post(
            "/auth/login",
            json={"identifier": "studyissue04restart_user", "password": "Passw0rd123!"},
        )
        assert logged_in.status_code == 200, logged_in.text

        restored = client.get(f"/chat/conversations/{conversation_id}").json()
        assert restored["study"]["stage"] == "recognizing"
        # 页与页级证据完全来自持久化状态，重启前后一致（内存态不参与）。
        assert restored["study"]["pages"] == pages_before_restart

        retry = client.post(
            f"/chat/conversations/{conversation_id}/messages/{message_id}/retry",
            json={"idempotency_key": "issue04-restart-attempt"},
        )
        assert retry.status_code == 200, retry.text
        restarted.state.generation_executor.run_tick()

        reloaded = client.get(f"/chat/conversations/{conversation_id}").json()
        assert reloaded["study"]["stage"] == "tutoring"
        assert [page["object_id"] for page in reloaded["study"]["pages"]] == pages
        assert "检测到1张重复书页，已跳过。" in reloaded["messages"][-1]["content"]
        # 附件仍绑定原会话：账户范围内可见，且没有跨会话挂载。
        assert all(page["object_id"] in pages for page in reloaded["study"]["pages"])
