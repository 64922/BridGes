"""四 SUT 统一执行协议（Test plan 1）。

验证：四个 SUT 都经过同一执行协议调用真实模型端口；脚本化 executor
不可被误选——输出必须来自端口响应，而不是案例内预写答案。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FakeGenerationPort, make_fake_judges

from bridges.humanize_eval.cases import HUMANIZE_CASES
from bridges.humanize_eval.suts import (
    SUTKind,
    SUTUnavailableError,
    build_suts,
    execute_sut,
    system_prompt_for,
)


def test_four_suts_have_distinct_identities(workspace: Path):
    suts = build_suts(workspace)
    assert len(suts) == 4
    kinds = {spec.kind for spec in suts}
    assert kinds == {
        SUTKind.CURRENT_PRODUCTION,
        SUTKind.CANDIDATE,
        SUTKind.PLAIN_MODEL,
        SUTKind.HUMANIZER_ZH_REFERENCE,
    }
    # 身份记录不混淆：current 绑定 git HEAD，candidate 绑定工作区。
    current = next(s for s in suts if s.kind is SUTKind.CURRENT_PRODUCTION)
    candidate = next(s for s in suts if s.kind is SUTKind.CANDIDATE)
    assert current.policy_origin == "git-head"
    assert candidate.policy_origin == "working-tree"
    reference = next(s for s in suts if s.kind is SUTKind.HUMANIZER_ZH_REFERENCE)
    assert reference.policy_origin == "external-snapshot"
    # plain-model 无策略（空策略哈希），与 current/candidate 不同。
    plain = next(s for s in suts if s.kind is SUTKind.PLAIN_MODEL)
    assert plain.policy_origin == "none"
    assert plain.policy_sha256 != current.policy_sha256
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
    """四个 SUT 都调用端口 generate()：输出必须来自端口响应。"""
    port = FakeGenerationPort()
    suts = build_suts(workspace)
    for spec in suts:
        if not spec.available:
            continue
        for case in HUMANIZE_CASES:
            output = execute_sut(spec, case, port, workspace)
            # 输出是端口响应正文（原文+保护项），而不是案例内脚本答案。
            expected = (case.source_text or "") + "".join(case.protected_items)
            assert output.text == expected
    assert len(port.calls) == len(HUMANIZE_CASES) * sum(
        1 for s in suts if s.available
    )


def test_plain_model_has_no_policy_prompt(workspace: Path):
    """plain-model 系统提示不含任何策略文本（仅基础角色）。"""
    suts = build_suts(workspace)
    plain = next(s for s in suts if s.kind is SUTKind.PLAIN_MODEL)
    prompt = system_prompt_for(plain, HUMANIZE_CASES[0], workspace)
    assert "策略" not in prompt
    assert "改写时不得" not in prompt
    assert "简单问题直接简短回答" not in prompt


def test_scripted_executor_not_selectable(workspace: Path):
    """脚本化 executor 不可被误选：没有任何路径从案例取预写答案。"""
    port = FakeGenerationPort()
    suts = build_suts(workspace)
    outputs = set()
    for spec in suts:
        if not spec.available:
            continue
        for case in HUMANIZE_CASES:
            output = execute_sut(spec, case, port, workspace)
            outputs.add(output.text)
    # 没有预写答案文件被读取：输出全部来自端口响应且无案例外文本。
    for case in HUMANIZE_CASES:
        assert case.user_request not in outputs
    # 端口被真实调用（脚本路径不会调用端口）。
    assert port.calls


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


def _frozen_snapshot(tmp_path: Path, case: object | None = None) -> Path:
    """构造带 frozen_outputs/ 的 Humanizer-zh 快照（外部不可重放参考）。

    默认写入全部案例的冻结输出（runner 遍历全部 case 时缺一即失败关闭）。
    """
    from bridges.humanize_eval.cases import HUMANIZE_CASES

    snapshot = tmp_path / "humanizer-zh-frozen"
    snapshot.mkdir(parents=True)
    (snapshot / "SKILL.md").write_text("# Humanizer-zh 快照\n", encoding="utf-8")
    (snapshot / "LICENSE").write_text("MIT License\n", encoding="utf-8")
    frozen = snapshot / "frozen_outputs"
    frozen.mkdir()
    for c in HUMANIZE_CASES:
        (frozen / f"{c.case_id}.txt").write_text(
            "这是冻结的外部参考输出。", encoding="utf-8"
        )
    return snapshot


def test_frozen_external_reference_marked_and_not_replayed(
    workspace: Path, tmp_path: Path
):
    """外部不可重放参考：标记 frozen_external_reference，输出读冻结文件。"""
    from bridges.humanize_eval.cases import HUMANIZE_CASES

    case = HUMANIZE_CASES[0]
    snapshot = _frozen_snapshot(tmp_path, case)
    suts = build_suts(workspace, snapshot_dir=str(snapshot))
    reference = next(
        s for s in suts if s.kind is SUTKind.HUMANIZER_ZH_REFERENCE
    )
    assert reference.available
    assert reference.frozen_external_reference
    assert reference.frozen_reference_note
    port = FakeGenerationPort()
    output = execute_sut(reference, case, port, workspace)
    assert output.text == "这是冻结的外部参考输出。"
    # 冻结参考不调用模型端口（不可重放，不伪称可复现）。
    assert port.calls == []


def test_frozen_reference_missing_output_fails_closed(
    workspace: Path, tmp_path: Path
):
    """冻结参考缺少某案例的冻结输出：失败关闭，不伪造可复现。"""
    from bridges.humanize_eval.cases import HUMANIZE_CASES

    case = HUMANIZE_CASES[0]
    snapshot = tmp_path / "humanizer-zh-frozen-missing"
    snapshot.mkdir(parents=True)
    (snapshot / "SKILL.md").write_text("# Humanizer-zh\n", encoding="utf-8")
    (snapshot / "LICENSE").write_text("MIT License\n", encoding="utf-8")
    (snapshot / "frozen_outputs").mkdir()
    suts = build_suts(workspace, snapshot_dir=str(snapshot))
    reference = next(
        s for s in suts if s.kind is SUTKind.HUMANIZER_ZH_REFERENCE
    )
    assert reference.frozen_external_reference
    port = FakeGenerationPort()
    with pytest.raises(SUTUnavailableError, match="冻结输出"):
        execute_sut(reference, case, port, workspace)


def test_frozen_reference_reported_in_summary(tmp_path: Path, workspace: Path):
    """运行摘要明确披露冻结外部参考（不得静默假装可复现）。"""
    from bridges.humanize_eval.cases import HUMANIZE_CASES
    from bridges.humanize_eval.holdout import HoldoutController
    from bridges.humanize_eval.runner import HumanizeRunner

    case = HUMANIZE_CASES[0]
    snapshot = _frozen_snapshot(tmp_path, case)
    runner = HumanizeRunner(
        outdir=tmp_path / "out",
        workspace=workspace,
        snapshot_dir=str(snapshot),
        port=FakeGenerationPort(),
        judges=make_fake_judges(),
        holdout=HoldoutController(
            workspace,
            manifest_path=tmp_path / "holdout" / "manifest.json",
            audit_path=tmp_path / "holdout" / "audit.log",
        ),
    )
    summary = runner.run()
    assert any("冻结外部参考" in reason for reason in summary.reasons)


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
        assert "改写" in prompt or "策略" in prompt or "助手" in prompt
        for internal in ("sut_id", "policy_sha256", "policy_ref"):
            assert internal not in prompt
