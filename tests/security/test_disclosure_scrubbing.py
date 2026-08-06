"""Issue 39 AC5/AC6：日志/审计脱敏与最小云披露回归测试。

覆盖：
1. 搜索查询脱敏：手机号/身份证号/密钥赋值/邮箱/URL 不出站；
2. 搜索审计含数据类别与授权快照，不含查询正文；
3. 审计 scrubber 对金丝雀秘密（sk-/LTAI/SMTP 码）一律脱敏；
4. 生产环境强制禁止 cassette 录制（完整对话正文不明文落盘）。
"""

from __future__ import annotations

from typing import Any

from bridges.api.main import create_app
from bridges.config import get_settings
from bridges.observability.scrubber import scrub_payload
from bridges.web_search.service import LocalQueryPlanner


def test_search_scrub_removes_phone_and_id_card() -> None:
    """手机号与身份证号不得进入出站查询词。"""
    planner = LocalQueryPlanner()
    plan = planner.plan("帮我查一下 13800138000 和 110101199001011234 的信息", force=True)
    assert plan.should_search
    assert "13800138000" not in plan.query
    assert "110101199001011234" not in plan.query


def test_search_scrub_removes_secret_assignment() -> None:
    """密钥赋值（如密码=xxx / api key）不出站。"""
    planner = LocalQueryPlanner()
    plan = planner.plan("搜索 api_key=sk-abcd1234efgh5678ijkl 的用途", force=True)
    assert "sk-abcd1234efgh5678ijkl" not in plan.query


def test_search_scrub_removes_email_and_url() -> None:
    """QQ 邮箱与 URL 不出站（身份与私人材料不发送给搜索服务）。"""
    planner = LocalQueryPlanner()
    plan = planner.plan("查一下 10001@qq.com 和 https://my.private.net/doc 相关的信息", force=True)
    assert "10001@qq.com" not in plan.query
    assert "my.private.net" not in plan.query


def test_search_scrub_keeps_legitimate_numeric_terms() -> None:
    """合法数字查询（年份/统计量）不被误伤。"""
    planner = LocalQueryPlanner()
    plan = planner.plan("2025 年中国人口 14.1 亿 增长率", force=True)
    assert "2025" in plan.query


def test_search_audit_has_data_categories_and_authorization_snapshot() -> None:
    """搜索披露审计：只记类别/长度/状态，含授权快照，不含查询正文。"""
    from bridges.contracts.chat import ChatMode
    from bridges.contracts.observability import AuditAction
    from bridges.web_search.service import WebSearchService

    class _FakeClient:
        def search(self, query: str) -> list[Any]:
            from bridges.web_search.contracts import WebSearchResult

            return [
                WebSearchResult(
                    result_id="r1",
                    title="t",
                    url="https://example.com",
                    site="example.com",
                    snippet="s",
                    accessed_at="2026-08-06T00:00:00+00:00",
                )
            ]

    class _RecordingObservability:
        def __init__(self) -> None:
            self.events: list[dict[str, Any]] = []

        def log_audit(self, **kwargs: Any) -> None:
            self.events.append(kwargs)

    observability = _RecordingObservability()
    service = WebSearchService(
        client=_FakeClient(),
        observability=observability,  # type: ignore[arg-type]
    )
    plan = service.plan("请搜索一下量子计算最新进展", ChatMode.COMPANION, force=True)
    result = service.search("acc-1", plan)
    assert result is not None and result.status.value == "success"

    event = observability.events[0]
    assert event["action"] == AuditAction.WEB_SEARCH
    details = event["details"]
    assert details["data_categories"] == ["public_query_terms"]
    assert details["authorization_snapshot"] == "authz-1.0"
    assert "query" not in details
    assert "量子计算" not in str(details)


def test_scrubber_redacts_secret_canaries_in_audit_details() -> None:
    """审计 details 携带金丝雀秘密时，键值一律 <redacted>，不回显正文。"""
    canaries = {
        "api_key": "sk-test-canary-qwen-1234567890abcdef1234567890abcdef",
        "smtp_code": "qqsmtp-canary-abcdef123456",
        "user_message": "我的私人正文完整内容",
        "transcript": "转写全文：这是隐私内容",
        "query": "搜索词包含身份信息",
    }
    scrubbed, manifest = scrub_payload(canaries)
    for key, value in scrubbed.items():
        assert value == "<redacted>", f"{key} 未脱敏"
    assert manifest.includes_secret
    # user_message/transcript/query 属于正文类键，纳入私有正文统计
    assert manifest.includes_private_body


def test_scrubber_redacts_aliyun_key_pattern() -> None:
    """LTAI 阿里云 AccessKey 出现在字符串值中整体脱敏。"""
    scrubbed, _ = scrub_payload({"detail": "配置了 LTAI4Gabcdefghijklmnopqrstuvwxyz12345 的账户"})
    assert scrubbed["detail"] == "<redacted>"


def test_scrubber_keeps_safe_audit_metadata() -> None:
    """常规审计元数据（类别/计数/状态）不被误伤。"""
    payload = {
        "data_categories": ["public_query_terms"],
        "query_length": 12,
        "result_count": 3,
        "status": "success",
        "authorization_snapshot": "authz-1.0",
    }
    scrubbed, _ = scrub_payload(payload)
    assert scrubbed["query_length"] == 12
    assert scrubbed["result_count"] == 3
    assert scrubbed["status"] == "success"
    assert scrubbed["authorization_snapshot"] == "authz-1.0"


def test_production_disables_cassette_recording(tmp_path: Any, monkeypatch: Any) -> None:
    """生产环境即使配置了录制开关，cassette 录制也必须被禁用。"""
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "production")
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path}/bridges.db")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "test-secret-key-32bytes-xxxxxxxx")
    monkeypatch.setenv("BRIDGES_QWEN_API_KEY", "sk-fake-prod-key-1234567890abcdefghij")
    monkeypatch.setenv("BRIDGES_QWEN_CASSETTE_DIR", str(tmp_path / "cassettes"))
    monkeypatch.setenv("BRIDGES_QWEN_RECORD_CASSETTES", "true")
    monkeypatch.setenv("BRIDGES_QWEN_FORCE_STUB", "false")
    get_settings.cache_clear()
    try:
        app = create_app()
        adapter = app.state.model_gateway._adapters[("qwen_text_chat", "1")]
        client = adapter._client
        assert client._record_mode is False, "生产环境不得启用 cassette 录制"
        assert client._cassette_store is not None
    finally:
        monkeypatch.delenv("BRIDGES_ENVIRONMENT", raising=False)
        monkeypatch.delenv("BRIDGES_DATABASE_URL", raising=False)
        monkeypatch.delenv("BRIDGES_SECRET_KEY", raising=False)
        monkeypatch.delenv("BRIDGES_QWEN_API_KEY", raising=False)
        monkeypatch.delenv("BRIDGES_QWEN_CASSETTE_DIR", raising=False)
        monkeypatch.delenv("BRIDGES_QWEN_RECORD_CASSETTES", raising=False)
        get_settings.cache_clear()
