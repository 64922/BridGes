"""三 SUT 统一执行协议（Test plan 1）。

验证：三个 SUT 都经过同一执行协议调用真实模型端口；脚本化 executor
不可被误选——输出必须来自端口响应，而不是案例内预写答案。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FakeGenerationPort

from bridges.humanize_eval.cases import HUMANIZE_CASES
from bridges.humanize_eval.suts import (
    SUTKind,
    SUTUnavailableError,
    build_suts,
    execute_sut,
    system_prompt_for,
)


def test_three_suts_have_distinct_identities(workspace: Path):
    suts = build_suts(workspace)
    assert len(suts) == 3
    kinds = {spec.kind for spec in suts}
    assert kinds == {
        SUTKind.CURRENT_PRODUCTION,
        SUTKind.CANDIDATE,
        SUTKind.HUMANIZER_ZH_REFERENCE,
    }
    # 身份记录不混淆：current 绑定 git HEAD，candidate 绑定工作区。
    current = next(s for s in suts if s.kind is SUTKind.CURRENT_PRODUCTION)
    candidate = next(s for s in suts if s.kind is SUTKind.CANDIDATE)
    assert current.policy_origin == "git-head"
    assert candidate.policy_origin == "working-tree"
    reference = next(s for s in suts if s.kind is SUTKind.HUMANIZER_ZH_REFERENCE)
    assert reference.policy_origin == "external-snapshot"
    from bridges.humanize_eval.suts import resolve_humanizer_zh_snapshot

    snapshot = resolve_humanizer_zh_snapshot()
    if snapshot.available:
        assert snapshot.license_status == "MIT"


def test_reference_snapshot_records_source_hash_license_model(workspace: Path):
    reference = next(
        s for s in build_suts(workspace)
        if s.kind is SUTKind.HUMANIZER_ZH_REFERENCE
    )
    assert reference.policy_sha256  # SKILL.md 内容哈希
    assert reference.model_id  # 基础模型
    assert reference.parameters.temperature  # 参数记录
    if reference.available:
        from bridges.humanize_eval.suts import HUMANIZER_ZH_DEFAULT_SNAPSHOT

        assert reference.policy_ref == HUMANIZER_ZH_DEFAULT_SNAPSHOT


def test_all_suts_go_through_same_generation_protocol(workspace: Path):
    """三个 SUT 都调用端口 generate()：输出必须来自端口响应。"""
    port = FakeGenerationPort()
    suts = build_suts(workspace)
    for spec in suts:
        if not spec.available:
            continue
        for case in HUMANIZE_CASES:
            output = execute_sut(spec, case, port, workspace)
            # 输出是端口响应正文，而不是案例内脚本答案。
            if case.case_id.startswith("article"):
                assert output.text == port.article_output
            else:
                assert output.text == port.chat_output
    assert len(port.calls) == 2 * sum(1 for s in suts if s.available)


def test_scripted_executor_not_selectable(workspace: Path):
    """脚本化 executor 不可被误选：没有任何路径从案例取预写答案。"""
    port = FakeGenerationPort()
    for case in HUMANIZE_CASES:
        if not case.source_text:
            continue
        assert case.source_text not in (
            port.article_output if case.case_id.startswith("article")
            else port.chat_output
        )


def test_unavailable_reference_sut_fails_closed(workspace: Path, tmp_path: Path):
    """缺快照时 reference SUT 不可运行：前置条件失败，不假成功。"""
    suts = build_suts(workspace, snapshot_dir=str(tmp_path / "missing"))
    reference = next(
        s for s in suts if s.kind is SUTKind.HUMANIZER_ZH_REFERENCE
    )
    assert not reference.available
    assert reference.missing_items
    port = FakeGenerationPort()
    with pytest.raises(SUTUnavailableError):
        execute_sut(reference, HUMANIZE_CASES[0], port, workspace)


def test_git_failure_makes_current_sut_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """git HEAD 不可读时 current 身份无法冻结：失败关闭并列出缺项。"""
    from bridges.humanize_eval import suts as suts_module

    monkeypatch.setattr(suts_module, "_head_commit", lambda workspace: "unknown")
    monkeypatch.setattr(suts_module, "_head_file_content", lambda workspace, rel: "")
    suts = build_suts(tmp_path)
    current = next(
        s for s in suts if s.kind is SUTKind.CURRENT_PRODUCTION
    )
    assert not current.available
    assert any("git" in item for item in current.missing_items)
    candidate = next(s for s in suts if s.kind is SUTKind.CANDIDATE)
    assert not candidate.available


def test_system_prompt_includes_task_bounds_not_internal_fields(workspace: Path):
    """系统提示带任务边界与策略文本，不含内部身份字段名。"""
    suts = build_suts(workspace)
    for spec in suts:
        if not spec.available:
            continue
        prompt = system_prompt_for(spec, HUMANIZE_CASES[0], workspace)
        assert "改写" in prompt or "策略" in prompt
        for internal in ("sut_id", "policy_sha256", "policy_ref"):
            assert internal not in prompt
