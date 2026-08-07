"""账户数据导出测试（Issue 37，AC1-2）。

覆盖：预览范围与预计大小（确认前可见）、导出内容完整（逐类别条数与
内容）、稳定标识/时间/关系/来源、秘密金丝雀不出现、跨账户隔离（B 的
数据不在 A 的导出中）、对象只出元数据清单、审计不含正文。
"""

from __future__ import annotations

import json

import pytest
from harness import SMTP_CANARY, Harness, logical_summary

from bridges.contracts.lifecycle import DataLifecycleError


def _export_document(harness: Harness, account_id: str) -> dict:
    filename, payload = harness.export.export_data(account_id)
    assert filename.endswith(".json")
    return json.loads(payload)


def test_preview_lists_categories_and_estimates(tmp_path) -> None:
    harness = Harness(tmp_path)
    harness.seed_everything()
    preview = harness.export.preview(harness.acc1)
    assert preview.secrets_omitted is True
    categories = {item.category: item for item in preview.categories}
    # 全部既定类别出现，且条数与数据一致。
    for key in (
        "conversations",
        "messages",
        "profile",
        "learning_projects",
        "reminders",
        "plugins",
        "mcp",
        "documents",
        "objects",
        "retrieval",
    ):
        assert key in categories, key
    assert categories["conversations"].item_count == 1
    assert categories["messages"].item_count == 3
    assert categories["objects"].item_count == 1
    assert preview.total_items == 3 + 1 + 1 + 1 + 1 + 1 + 1 + 1
    # 预计大小为正且随数据增长而增长。
    small = harness.export.preview(harness.acc2).total_estimated_bytes
    assert small > 0
    assert preview.total_estimated_bytes > small


def test_export_contains_full_account_data_with_stable_identity(tmp_path) -> None:
    harness = Harness(tmp_path)
    harness.seed_everything()
    document = _export_document(harness, harness.acc1)
    assert document["format_version"] == 1
    assert "exported_at" in document
    assert document["account"]["account_id"] == harness.acc1
    assert document["account"]["username"] == "alice"
    assert document["account"]["qq_email"] == "10001@qq.com"
    assert document["secrets_omitted"] is True
    messages = document["categories"]["messages"]["items"]
    assert len(messages) == 3
    assert any("我的对话" in str(item["content"]) for item in messages)
    # 时间与关系字段随行保留（画像闭环可审阅）。
    profile = document["categories"]["profile"]["items"]
    assert any(item["canonical_dimension"] == "BASIC_INFORMATION" for item in profile)
    reminders = document["categories"]["reminders"]["items"]
    assert any(item["subject"] == "每周交作业" for item in reminders)
    plugins = document["categories"]["plugins"]["items"]
    assert any(item.get("plugin_id") == "demo-pack" for item in plugins)
    mcp = document["categories"]["mcp"]["items"]
    assert any(item.get("mcp_id", "").startswith("mcp-") for item in mcp)


def test_export_object_assets_are_metadata_only(tmp_path) -> None:
    harness = Harness(tmp_path)
    harness.seed_everything()
    document = _export_document(harness, harness.acc1)
    assets = document["categories"]["objects"]["items"]
    assert len(assets) == 1
    asset = assets[0]
    assert asset["original_filename"] == "素材.txt"
    assert asset["content_length"] == len(b"alice material bytes")
    # 资产清单只含元数据，不含对象字节。
    payload = json.dumps(document, ensure_ascii=False)
    assert b"alice material bytes" not in payload.encode("utf-8")


def test_export_excludes_all_secret_canaries(tmp_path) -> None:
    harness = Harness(tmp_path)
    harness.seed_everything()
    harness.inject_canaries()
    filename, payload = harness.export.export_data(harness.acc1)
    raw = payload.decode("utf-8", errors="replace")
    assert SMTP_CANARY not in raw
    # 密码哈希（身份存储）与 B 的金丝雀同样不出现。
    assert "canary-password" not in raw


def test_export_isolated_between_accounts(tmp_path) -> None:
    harness = Harness(tmp_path)
    harness.seed_everything()
    document = _export_document(harness, harness.acc1)
    raw = json.dumps(document, ensure_ascii=False)
    assert "B 的对话" not in raw
    assert "bob material bytes" not in raw
    document_b = _export_document(harness, harness.acc2)
    raw_b = json.dumps(document_b, ensure_ascii=False)
    assert "我的对话" not in raw_b
    assert "alice material bytes" not in raw_b


def test_export_unknown_account_rejected(tmp_path) -> None:
    harness = Harness(tmp_path)
    with pytest.raises(DataLifecycleError) as excinfo:
        harness.export.export_data("no-such-account")
    assert excinfo.value.status_code == 404


def test_export_audit_contains_counts_not_content(tmp_path) -> None:
    harness = Harness(tmp_path)
    harness.seed_everything()
    harness.export.export_data(harness.acc1)
    assert "export_create" in harness.audit_actions()
    details = harness.audit_details()[-1]
    assert details["total_items"] >= 1
    assert "我的对话" not in str(details)


def test_export_matches_logical_summary(tmp_path) -> None:
    """导出条数与逻辑摘要一致（Verification 1 的摘要比对基础）。"""
    harness = Harness(tmp_path)
    harness.seed_everything()
    document = _export_document(harness, harness.acc1)
    summary = logical_summary(harness, harness.acc1)
    assert len(document["categories"]["conversations"]["items"]) == summary["conversations"]
    assert len(document["categories"]["messages"]["items"]) == summary["messages"]
    assert len(document["categories"]["objects"]["items"]) == summary["objects"]
