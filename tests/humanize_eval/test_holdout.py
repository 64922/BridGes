"""holdout 冻结、哈希校验、访问门与解封审计（Test plan 4）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bridges.humanize_eval.cases import CORPUS_VERSION, case_hashes
from bridges.humanize_eval.holdout import HoldoutController, HoldoutError


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path / "workspace"


@pytest.fixture
def controller(workspace: Path) -> HoldoutController:
    return HoldoutController(workspace)


def _sealed_hashes(sample_ids: list[str]) -> dict[str, str]:
    hashes = case_hashes()
    return {cid: hashes[cid] for cid in sample_ids}


def test_no_manifest_means_no_sealed_cases(controller: HoldoutController):
    assert controller.load_manifest() is None
    assert controller.sealed_ids() == set()
    assert controller.validate_against(case_hashes()) == []


def test_freeze_and_validate_roundtrip(controller: HoldoutController):
    sealed = _sealed_hashes(["chat-tomato-method-v1", "article-nl01-share-list-help-v1"])
    manifest = controller.build_manifest(sealed, corpus_version=CORPUS_VERSION)
    assert manifest.frozen_commit
    controller.save_manifest(manifest)
    loaded = controller.load_manifest()
    assert loaded is not None
    assert loaded.sealed_cases == sealed
    assert controller.sealed_ids() == set(sealed)
    assert controller.validate_against(case_hashes()) == []


def test_manifest_save_refuses_overwrite(controller: HoldoutController):
    sealed = _sealed_hashes(["chat-tomato-method-v1"])
    controller.save_manifest(
        controller.build_manifest(sealed, corpus_version=CORPUS_VERSION)
    )
    with pytest.raises(HoldoutError):
        controller.save_manifest(
            controller.build_manifest(sealed, corpus_version=CORPUS_VERSION)
        )


def test_hash_change_reported(controller: HoldoutController, workspace: Path):
    sealed = _sealed_hashes(["chat-tomato-method-v1"])
    controller.save_manifest(
        controller.build_manifest(sealed, corpus_version=CORPUS_VERSION)
    )
    # 模拟案例被改（哈希变化）后校验必须报告问题。
    forged = case_hashes()
    forged["chat-tomato-method-v1"] = "f" * 64
    problems = controller.validate_against(forged)
    assert problems, "冻结哈希变化必须被报告"
    assert any("chat-tomato-method-v1" in p for p in problems)


def test_removed_case_reported(controller: HoldoutController):
    sealed = _sealed_hashes(["chat-tomato-method-v1"])
    controller.save_manifest(
        controller.build_manifest(sealed, corpus_version=CORPUS_VERSION)
    )
    problems = controller.validate_against({})
    assert problems and any("已从注册表移除" in p for p in problems)


def test_unsealed_holdout_rejected_without_unseal(controller: HoldoutController):
    """未解封时请求 holdout 访问一律拒绝（提前读取）。"""
    sealed = _sealed_hashes(["chat-tomato-method-v1", "article-nl01-share-list-help-v1"])
    controller.save_manifest(
        controller.build_manifest(sealed, corpus_version=CORPUS_VERSION)
    )
    with pytest.raises(HoldoutError, match="未解封"):
        controller.check_access(requested_holdout=True)
    # 默认（不请求 holdout）不抛错。
    controller.check_access(requested_holdout=False)


def test_runnable_excludes_sealed(controller: HoldoutController):
    sealed = _sealed_hashes(["chat-tomato-method-v1"])
    controller.save_manifest(
        controller.build_manifest(sealed, corpus_version=CORPUS_VERSION)
    )
    all_ids = ["chat-tomato-method-v1", "article-nl01-share-list-help-v1"]
    assert controller.runnable_case_ids(all_ids) == ["article-nl01-share-list-help-v1"]


def test_unseal_is_one_shot_and_audited(controller: HoldoutController):
    sealed = _sealed_hashes(["chat-tomato-method-v1"])
    controller.save_manifest(
        controller.build_manifest(sealed, corpus_version=CORPUS_VERSION)
    )
    updated = controller.unseal("发布 Issue 12 验收")
    assert updated.unsealed and updated.unsealed_reason
    # 二次解封拒绝（已解封的 holdout 不能重新伪装成未见数据）。
    with pytest.raises(HoldoutError, match="已解封"):
        controller.unseal("再次解封")
    events = controller.audit_events()
    assert len(events) == 1
    assert events[0]["event"] == "holdout_unseal"
    assert events[0]["reason"] == "发布 Issue 12 验收"
    # 解封后 runnable 包含全部案例。
    assert controller.runnable_case_ids(["chat-tomato-method-v1"]) == ["chat-tomato-method-v1"]


def test_unseal_requires_reason(controller: HoldoutController):
    sealed = _sealed_hashes(["chat-tomato-method-v1"])
    controller.save_manifest(
        controller.build_manifest(sealed, corpus_version=CORPUS_VERSION)
    )
    with pytest.raises(HoldoutError, match="理由"):
        controller.unseal("  ")


def test_unseal_without_manifest_rejected(controller: HoldoutController):
    with pytest.raises(HoldoutError, match="清单"):
        controller.unseal("理由")


def test_corrupt_manifest_rejected(controller: HoldoutController, workspace: Path):
    controller.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    controller.manifest_path.write_text("{broken json", encoding="utf-8")
    with pytest.raises(HoldoutError):
        controller.load_manifest()


def test_audit_log_append_only(controller: HoldoutController):
    """审计日志事件只能追加，内容不可覆盖。"""
    controller.audit.record({"event": "a", "at": "t1"})
    controller.audit.record({"event": "b", "at": "t2"})
    lines = controller.audit.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "a"
    assert json.loads(lines[1])["event"] == "b"
