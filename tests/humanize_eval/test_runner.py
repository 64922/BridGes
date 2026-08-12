"""运行锁与 append-only 语义（Test plan 2/3：双跑不覆盖、哈希、重放、完整性）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import FakeGenerationPort, make_fake_judges

from bridges.humanize_eval.cases import HUMANIZE_CASES, case_hashes
from bridges.humanize_eval.generation import (
    GenerationResult,
    GenerationStatus,
)
from bridges.humanize_eval.holdout import HoldoutController
from bridges.humanize_eval.runner import (
    HumanizeRunError,
    HumanizeRunner,
    _append_only_write,
)

SUT_IDS = ("current-production", "candidate", "plain-model", "humanizer-zh-reference")
TOTAL_CASES = len(HUMANIZE_CASES)


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
    # 每个可用 SUT × 全部 case 一份原始输出（不依赖外部快照存在与否）。
    from bridges.humanize_eval.suts import build_suts

    available_suts = sum(1 for s in build_suts(workspace) if s.available)
    assert len(list(raw_dir.glob("*.json"))) == available_suts * TOTAL_CASES
    assert (run_dir / "packets").is_dir()
    assert (run_dir / "mappings").is_dir()
    assert summary.packet_path and summary.mapping_path
    # 锁记录代码/build、语料/账本/契约、SUT、策略哈希、模型快照、
    # 采样参数、环境、执行次数、case 哈希与时间。
    for key in (
        "code_commit",
        "code_digest",
        "corpus_hashes",
        "ledger_hashes",
        "contract_versions",
        "sut_specs",
        "model_snapshot",
        "parameters",
        "environment",
        "run_count",
        "case_hashes",
        "started_at",
        "ended_at",
    ):
        assert lock[key], f"运行锁缺少 {key}"
    assert set(lock["corpus_hashes"]) == {"chat", "article"}
    assert lock["run_count"] == 1


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
    assert lock_second["run_count"] == 2


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


def test_raw_files_unique_per_case_sut(tmp_path: Path, workspace: Path):
    """原始输出按 case/SUT/run 唯一定位。"""
    runner = _make_runner(tmp_path, workspace)
    summary = runner.run()
    raw_dir = tmp_path / "runs" / summary.run_id / "raw"
    keys = sorted(p.stem for p in raw_dir.glob("*.json"))
    assert len(keys) == len(set(keys)), "case/SUT 定位重复"


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
        "plain-model": 0,
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
        "plain-model": 0,
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


def test_surface_reports_are_separate(tmp_path: Path, workspace: Path):
    """聊天与文章分别报告、分别计数。"""
    runner = _make_runner(tmp_path, workspace)
    summary = runner.run()
    assert set(summary.surface_verdicts) == {
        "chat_naturalness",
        "article_humanization",
    }
    assert summary.surface_cases["chat_naturalness"] >= 40
    assert summary.surface_cases["article_humanization"] >= 40
    assert summary.surface_case_total["chat_naturalness"] == 45
    assert summary.surface_case_total["article_humanization"] == 48


def test_surface_filter_runs_one_surface(tmp_path: Path, workspace: Path):
    runner = _make_runner(tmp_path, workspace, surface="chat")
    summary = runner.run()
    assert summary.cases_total == 45
    assert summary.surface_case_total["chat_naturalness"] == 45
    assert summary.surface_case_total["article_humanization"] == 0


def _tmp_holdout(tmp_path: Path, workspace: Path) -> HoldoutController:
    """在 tmp 目录构造 holdout 控制器（不污染真实仓库的冻结资产）。"""
    holdout_dir = tmp_path / "holdout"
    return HoldoutController(
        workspace,
        manifest_path=holdout_dir / "manifest.json",
        audit_path=holdout_dir / "audit.log",
    )


def _sealed_case_hashes() -> dict[str, str]:
    return {
        c.case_id: case_hashes()[c.case_id]
        for c in HUMANIZE_CASES
        if c.partition.value == "holdout"
    }


def test_run_without_holdout_excludes_sealed(tmp_path: Path, workspace: Path):
    """默认运行：holdout 冻结案例被排除且记录排除数。"""
    holdout = _tmp_holdout(tmp_path, workspace)
    holdout.save_manifest(
        holdout.build_manifest(_sealed_case_hashes(), corpus_version="9.1")
    )
    runner = _make_runner(tmp_path, workspace, holdout=holdout)
    summary = runner.run()
    assert summary.holdout_excluded == len(_sealed_case_hashes())
    assert summary.cases_total == TOTAL_CASES - len(_sealed_case_hashes())
    raw_dir = tmp_path / "runs" / summary.run_id / "raw"
    # 任一 SUT 的 raw 文件都不包含 holdout 案例。
    raw_names = [p.stem for p in raw_dir.glob("*.json")]
    sealed = _sealed_case_hashes()
    assert all(
        not any(cid in name for cid in sealed) for name in raw_names
    ), "holdout 案例泄漏到 development 运行"


def test_holdout_early_access_rejected(tmp_path: Path, workspace: Path):
    """未解封时显式请求 holdout 一律拒绝。"""
    holdout = _tmp_holdout(tmp_path, workspace)
    holdout.save_manifest(
        holdout.build_manifest(_sealed_case_hashes(), corpus_version="9.1")
    )
    runner = _make_runner(tmp_path, workspace, holdout=holdout, allow_holdout=True)
    with pytest.raises(HumanizeRunError, match="未解封"):
        runner.run()


def test_holdout_hash_change_blocks_run(tmp_path: Path, workspace: Path):
    """holdout 冻结哈希与注册表不一致时运行被拒绝。"""
    holdout = _tmp_holdout(tmp_path, workspace)
    holdout.save_manifest(
        holdout.build_manifest(_sealed_case_hashes(), corpus_version="9.1")
    )
    # 篡改冻结清单中的哈希，模拟 holdout 内容变化。
    manifest = holdout.load_manifest()
    manifest = manifest.model_copy(
        update={
            "sealed_cases": {
                cid: ("f" * 64) for cid in manifest.sealed_cases
            }
        }
    )
    holdout.manifest_path.write_text(
        manifest.model_dump_json(indent=2), encoding="utf-8"
    )
    runner = _make_runner(tmp_path, workspace, holdout=holdout)
    with pytest.raises(HumanizeRunError, match="冻结校验失败"):
        runner.run()


def test_parameters_change_changes_lock_identity(tmp_path: Path, workspace: Path):
    """模型参数不同 => 锁身份不同（同配置才是同一锁）。"""
    from bridges.humanize_eval.generation import GenerationParameters
    from bridges.humanize_eval.suts import build_suts

    runner = _make_runner(tmp_path, workspace)
    suts_a = build_suts(workspace, params=GenerationParameters(temperature=0.5))
    suts_b = build_suts(workspace, params=GenerationParameters(temperature=0.9))
    lock_a = runner._lock(suts_a, run_count=1)
    lock_b = runner._lock(suts_b, run_count=1)
    assert lock_a.digest() != lock_b.digest()


def test_skill_hash_change_changes_lock(tmp_path: Path, workspace: Path):
    """SKILL/策略哈希变化会改变运行锁身份（比较前完整性）。"""
    from bridges.humanize_eval.suts import build_suts

    runner = _make_runner(tmp_path, workspace)
    suts = build_suts(workspace)
    lock_a = runner._lock(suts, run_count=1)
    altered = [s.model_copy(update={"policy_sha256": "f" * 64}) for s in suts]
    lock_b = runner._lock(altered, run_count=1)
    assert lock_a.digest() != lock_b.digest()
