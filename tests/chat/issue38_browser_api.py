"""工单 38 浏览器验收服务：正式 HTTP/执行器/图，仅模型和公开来源固定。"""

from __future__ import annotations

import json
import os
import socket
from typing import Any

import uvicorn

from bridges.api.main import create_app
from bridges.contracts.ai import ModelCallResult
from tests.chat.test_chat_api import _gateway_with
from tests.chat.test_improvement36_evidence_bound_summary import Ticket36Gateway
from tests.github.test_github_module_flow import (
    WHOLE_QUERY,
    _candidate,
    _evidence,
    _FakeReader,
    _FakeSearchPort,
    _install_github_service,
)
from tests.paper.test_paper_module_flow import (
    ATTENTION_CANDIDATES,
    _FakePaperSource,
    _install_paper_source,
    _SilentAdapter,
)


class BrowserGateway(Ticket36Gateway):
    """学习固定单题，每个教材范围首次总结超时，用户重试后恢复。"""

    def __init__(self) -> None:
        super().__init__()
        self.question_count = 1
        self.page_numbers = None
        self.failed_scopes: set[str] = set()
        self.chat_gateway = _gateway_with(_SilentAdapter())

    def invoke(
        self, capability: str, version: str, context: Any,
        payload: dict[str, Any], **kwargs: Any,
    ) -> ModelCallResult:
        if capability in {"qwen_ocr", "qwen_vision"} or str(
            payload.get("task", "")
        ).startswith("study."):
            return super().invoke(capability, version, context, payload, **kwargs)
        return self.chat_gateway.invoke(capability, version, context, payload, **kwargs)

    def summarize(self, payload: dict[str, Any]) -> ModelCallResult:
        data = next(
            json.loads(item["content"]) for item in payload["messages"]
            if item["content"].startswith('{"')
        )
        scope = data["sources"][0]["fragment_id"]
        self.fail_summary = None if scope in self.failed_scopes else "timeout"
        self.failed_scopes.add(scope)
        return super().summarize(payload)


def main() -> None:
    """不新增测试路由；网络硬守卫禁止来源替身之外的意外外呼。"""
    original_connect = socket.socket.connect

    def local_connect(connection: socket.socket, address: object) -> None:
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1"}:
            raise OSError("工单 38 浏览器验收禁止外部网络请求。")
        original_connect(connection, address)

    socket.socket.connect = local_connect
    app = create_app()
    app.state.chat_service._gateway = BrowserGateway()
    _install_paper_source(app, _FakePaperSource(candidates=ATTENTION_CANDIDATES))
    _install_github_service(
        app,
        port=_FakeSearchPort(per_query={
            WHOLE_QUERY: [_candidate("demo/books", description="校园二手书交换")],
        }),
        reader=_FakeReader({
            "demo/books": _evidence("demo/books", description="校园二手书交换平台"),
        }),
    )
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("API_PORT", "8038")))


if __name__ == "__main__":
    main()
