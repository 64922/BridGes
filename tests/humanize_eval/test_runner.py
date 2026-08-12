"""运行锁与 append-only 语义（Test plan 2：双跑不覆盖、哈希、重放）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import FakeGenerationPort, make_fake_judges

from bridges.humanize_eval.generation import (
    GenerationResult,
    GenerationStatus,
)
from bridges.humanize_eval.runner import (
    HumanizeRunError,
    HumanizeRunner,
    _append_only_write,
)


def _make_runner(outdir: Path, workspace: Path, **kwargs) -> HumanizeRunner:
    defaults = {
        "port": FakeGenerationPort(),
        "judges": make_fake_judges(),
        "anon_seed": 2026,
    }
    defaults.update(kwargs)
    return HumanizeRunner(outdir=outdir, workspace=workspace, **defaults)


def test_run_produces_complete_evidence_tree(tmp_path: Path, workspace: Path):
    runner = _make_runner(tmp_path, workspace)
    summary = runner.run()
    runs_dir = tmp_path / "runs"
    assert runs_dir.is_dir()
    run_dir = runs_dir / summary.run_id
    lock = json.loads((run_dir / "lock.json").read_text(encoding="utf-8"))
    assert lock["lock_id"].startswith("lock-")
    raw_dir = run_dir / "raw"
    # 每个可用 SUT × 2 case 一份原始输出（不依赖外部快照存在与否）。
    from bridges.humanize_eval.suts import build_suts

    available_suts = sum(1 for s in build_suts(workspace) if s.available)
    assert len(list(raw_dir.glob("*.json"))) == available_suts * 2
    assert (run_dir / "packets").is_dir()
    assert (run_dir / "mappings").is_dir()
    assert summary.packet_path and summary.mapping_path
    # 锁记录代码/build、SUT、策略哈希、模型快照、采样参数、case 哈希与时间。
    for key in (
        "code_commit",
        "code_digest",
        "sut_specs",
        "model_snapshot",
        "parameters",
        "case_hashes",
        "started_at",
        "ended_at",
    ):
        assert lock[key], f"运行锁缺少 {key}"


def test_lock_records_strategy_hashes(tmp_path: Path, workspace: Path):
    runner = _make_runner(tmp_path, workspace)
    summary = runner.run()
    lock = json.loads(
        (tmp_path / "runs" / summary.run_id / "lock.json").read_text(
            encoding="utf-8"
        )
    )
    for spec in lock["sut_specs"]:
        assert spec["policy_sha256"], f"{spec['sut_id']} 缺少策略哈希"
    assert lock["case_hashes"], "缺少 case 哈希"


def test_second_run_appends_not_overwrites(tmp_path: Path, workspace: Path):
    """连续运行两次：新 run 目录，旧记录不覆盖。"""
    runner = _make_runner(tmp_path, workspace)
    first = runner.run()
    second = runner.run()
    assert first.run_id != second.run_id
    runs_dir = tmp_path / "runs"
    assert len(list(runs_dir.iterdir())) == 2
    # 两个 run 的锁身份相同（同配置），实例标识不同（时间戳）。
    lock_first = json.loads(
        (runs_dir / first.run_id / "lock.json").read_text(encoding="utf-8")
    )
    lock_second = json.loads(
        (runs_dir / second.run_id / "lock.json").read_text(encoding="utf-8")
    )
    assert lock_first["lock_id"] == lock_second["lock_id"]


def test_raw_outputs_are_immutable_and_content_hashed(tmp_path: Path, workspace: Path):
    runner = _make_runner(tmp_path, workspace)
    summary = runner.run()
    raw_dir = tmp_path / "runs" / summary.run_id / "raw"
    for raw_file in raw_dir.glob("*.json"):
        payload = json.loads(raw_file.read_text(encoding="utf-8"))
        assert payload["run_id"] == summary.run_id
        assert payload["case_id"] and payload["sut_id"]
        assert payload["saved_at"]
        # 篡改内容后重写应被拒绝（append-only）。
        with pytest.raises(HumanizeRunError):
            _append_only_write(raw_file, "tampered")


def test_append_only_conflict_raises(tmp_path: Path):
    target = tmp_path / "evidence.json"
    _append_only_write(target, '{"a": 1}')
    _append_only_write(target, '{"a": 1}')  # 相同内容幂等
    with pytest.raises(HumanizeRunError):
        _append_only_write(target, '{"a": 2}')


def test_lock_incomplete_means_inconclusive(tmp_path: Path, workspace: Path):
    """任一运行锁字段缺失时结论为 inconclusive。"""
    runner = _make_runner(tmp_path, workspace)
    summary = runner.run()
    # 人为破坏锁：缺 case_hashes。
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / summary.run_id
    lock_file = run_dir / "lock.json"
    lock = json.loads(lock_file.read_text(encoding="utf-8"))
    lock["case_hashes"] = {}
    with pytest.raises(HumanizeRunError):
        _append_only_write(lock_file, json.dumps(lock, ensure_ascii=False))


def test_missing_credentials_report_not_configured(tmp_path: Path, workspace: Path):
    """无凭据端口：结论 inconclusive，绝不明目张胆成功。"""

    class NoKeyPort:
        def generate(self, *, system_prompt, user_prompt, params):
            return GenerationResult(
                text="",
                model_id="fake",
                parameters=params.model_dump(),
                status=GenerationStatus.NOT_CONFIGURED,
                error_code="not_configured",
                error_message="缺少全局百炼运行凭据。",
            )

    runner = _make_runner(tmp_path, workspace, port=NoKeyPort(), judges=[])
    summary = runner.run()
    assert summary.verdict == "inconclusive"
    assert all(v == "not_configured" for v in summary.sut_status.values())
    assert summary.generation_counts == {
        "current-production": 0,
        "candidate": 0,
        "humanizer-zh-reference": 0,
    }
    assert any("凭据" in r for r in summary.reasons)


def test_empty_success_output_is_failed_not_crash(tmp_path: Path, workspace: Path):
    """成功状态空输出 = 畸形响应：记为失败并继续，不中断整个 run。"""

    class EmptyPort:
        def generate(self, *, system_prompt, user_prompt, params):
            return GenerationResult(
                text="",
                model_id="fake",
                parameters=params.model_dump(),
                status=GenerationStatus.SUCCESS,
            )

    runner = _make_runner(tmp_path, workspace, port=EmptyPort(), judges=[])
    summary = runner.run()
    assert summary.verdict == "inconclusive"
    assert all(v == "failed" for v in summary.sut_status.values())
    assert summary.generation_counts == {
        "current-production": 0,
        "candidate": 0,
        "humanizer-zh-reference": 0,
    }
    assert any("失败" in r for r in summary.reasons)


def test_lock_records_anon_seed(tmp_path: Path, workspace: Path):
    """随机种子进入运行锁（CONTEXT.md 契约）。"""
    runner = _make_runner(tmp_path, workspace, anon_seed=4242)
    summary = runner.run()
    lock = json.loads(
        (tmp_path / "runs" / summary.run_id / "lock.json").read_text(
            encoding="utf-8"
        )
    )
    assert lock["parameters"]["anon_seed"] == 4242


def test_lock_update_rejects_value_tampering(tmp_path: Path):
    """lock.json 允许补全键，但同键值篡改必须被拒绝。"""
    target = tmp_path / "lock.json"
    _append_only_write(target, json.dumps({"a": 1, "ended_at": ""}))
    # 补全（空值填充）允许。
    _append_only_write(
        target, json.dumps({"a": 1, "ended_at": "2026-08-12T00:00:00"}), allow_update=True
    )
    # 篡改同键值拒绝。
    with pytest.raises(HumanizeRunError):
        _append_only_write(
            target, json.dumps({"a": 2, "ended_at": "2026-08-12T00:00:00"}),
            allow_update=True,
        )


def test_major_fidelity_failure_blocks_passed(tmp_path: Path, workspace: Path):
    """MAJOR 保真失败（如数字+单位丢失）也必须阻止通过。"""
    from conftest import ARTICLE_FAITHFUL_OUTPUT

    class MajorFailurePort:
        def generate(self, *, system_prompt, user_prompt, params):
            if "时间块" in user_prompt:
                # 去掉 5 分钟缓冲（MAJOR 单位失败），保留其余全部。
                text = ARTICLE_FAITHFUL_OUTPUT.replace("中间留 5 分钟缓冲，", "")
            else:
                text = "番茄工作法：专注 25 分钟，休息 5 分钟，交替进行。"
            return GenerationResult(
                text=text,
                model_id="fake",
                parameters=params.model_dump(),
                status=GenerationStatus.SUCCESS,
            )

    runner = _make_runner(tmp_path, workspace, port=MajorFailurePort(), judges=[])
    summary = runner.run()
    assert summary.verdict == "inconclusive"
    assert any("保真" in r for r in summary.reasons)
    assert summary.fidelity_failures, "MAJOR 保真失败应被记录"
