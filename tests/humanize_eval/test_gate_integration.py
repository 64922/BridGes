"""发布门集成（Issue 11 Test plan 4/5/6/7 + AC-12 CLI 退出码）。

- run() 产物包含统计计划哈希、配对统计与门报告（含七维度统计）；
- 注入关键事实失败（虚构亲历）→ 即使分数全高仍阻止发布；
- 报告哈希稳定（固定种子）；变更预注册配置不覆盖旧报告（append-only）；
- CLI 在候选未通过（failed/inconclusive/无门报告）时返回非零与中文原因。
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import FakeGenerationPort, make_fake_judges

from bridges.humanize_eval.cases import HUMANIZE_CASES
from bridges.humanize_eval.generation import (
    GenerationResult,
    GenerationStatus,
)
from bridges.humanize_eval.judges import JudgePreference
from bridges.humanize_eval.paired_stats import (
    ItemPreference,
    compute_paired_statistics,
)
from bridges.humanize_eval.release_gate import (
    FidelityGateInput,
    PanelHealth,
    SURFACE_CHAT,
    StyleGateInput,
    SurfaceInput,
    evaluate_humanize_gate,
)
from bridges.humanize_eval.runner import HumanizeRunner
from bridges.humanize_eval.statistics_plan import StatisticsPlan


def _make_runner(outdir: Path, workspace: Path, **kwargs) -> HumanizeRunner:
    defaults = {
        "port": FakeGenerationPort(),
        "judges": make_fake_judges(),
        "anon_seed": 2026,
    }
    defaults.update(kwargs)
    return HumanizeRunner(outdir=outdir, workspace=workspace, **defaults)


def _chat_gate(
    *,
    scores: list[float],
    fidelity: FidelityGateInput | None = None,
    style: StyleGateInput | None = None,
) -> object:
    """构造一个 chat 面门报告（CLI 退出码测试共用）。"""
    stats = compute_paired_statistics(
        peer_sut_id="current-production",
        preferences=[
            ItemPreference(
                "j1",
                f"i{index}",
                f"c{index}",
                (
                    JudgePreference.A
                    if score == 1.0
                    else JudgePreference.B
                    if score == 0.0
                    else JudgePreference.TIE
                ),
            )
            for index, score in enumerate(scores)
        ],
        case_ids=[f"c{index}" for index in range(len(scores))],
    )
    return evaluate_humanize_gate(
        plan=StatisticsPlan(),
        surface_inputs={
            SURFACE_CHAT: SurfaceInput(
                paired_statistics={"current-production": stats},
                fidelity=fidelity or FidelityGateInput(),
                style=style or StyleGateInput(),
            )
        },
        panel=PanelHealth(
            judge_count=3,
            panel_gate_ok=True,
            canary_all_passed=True,
            drift_all_ok=True,
        ),
        judge_versions={},
        disagreement_items=[],
        missing_lock_items=[],
        failed_case_ids=[],
        cost_latency={},
    )


# ---------------------------------------------------------------------------
# run() 产物包含统计与门（Test plan 7 回归 + AC-1 预注册哈希）
# ---------------------------------------------------------------------------

def test_summary_contains_statistics_and_gate(tmp_path: Path, workspace: Path):
    runner = _make_runner(tmp_path, workspace)
    summary = runner.run()
    assert summary.statistics_plan_digest
    assert summary.gate_report is not None
    gate = summary.gate_report
    assert gate.automated_system_judges_only is True
    assert gate.human_validated is False
    # 聊天与文章分别报告。
    assert set(gate.surface_verdicts) == {"chat_naturalness", "article_humanization"}
    # 配对统计：chat/article × current-production（参考可用时还有 Humanizer-zh）。
    assert "chat_naturalness" in summary.paired_statistics
    assert "article_humanization" in summary.paired_statistics
    chat_stats = summary.paired_statistics["chat_naturalness"]
    assert "current-production" in chat_stats
    for stats in chat_stats.values():
        # 统计单位是 case：n 不超过该面案例数，且每个 case 只记一次。
        chat_case_count = len(
            [c for c in HUMANIZE_CASES if c.kind.value == "chat"]
        )
        assert stats.n_valid_cases <= chat_case_count
        assert stats.ci_low <= stats.preference_mean <= stats.ci_high
    # AC-6：七维度成对统计进入报告（naturalness 必在）。
    chat_dimensions = gate.dimensions["chat_naturalness"]["current-production"]
    assert "naturalness" in chat_dimensions
    dimension = chat_dimensions["naturalness"]
    assert dimension.n_cases > 0
    assert dimension.candidate_mean >= 1 and dimension.candidate_mean <= 5
    assert dimension.mean_diff == dimension.candidate_mean - dimension.peer_mean


def test_summary_json_persists_gate_report(tmp_path: Path, workspace: Path):
    runner = _make_runner(tmp_path, workspace)
    summary = runner.run()
    summary_file = tmp_path / "runs" / summary.run_id / "summary.json"
    payload = json.loads(summary_file.read_text(encoding="utf-8"))
    assert payload["statistics_plan_digest"]
    assert payload["gate_report"]["plan_digest"]
    assert payload["paired_statistics"]
    # 报告不复制私人正文。
    assert "output_text" not in json.dumps(payload, ensure_ascii=False)


def test_repeated_run_stable_plan_digest_and_append_only(
    tmp_path: Path, workspace: Path
):
    """固定 seed 重跑：计划哈希与统计一致；旧报告不覆盖（Test plan 6）。"""
    runner = _make_runner(tmp_path, workspace)
    first = runner.run()
    second = runner.run()
    assert first.statistics_plan_digest == second.statistics_plan_digest
    runs_dir = tmp_path / "runs"
    assert len(list(runs_dir.iterdir())) == 2
    # 同一配置产生同一锁身份（digest），实例不同（run_id）。
    lock_first = json.loads(
        (runs_dir / first.run_id / "lock.json").read_text(encoding="utf-8")
    )
    lock_second = json.loads(
        (runs_dir / second.run_id / "lock.json").read_text(encoding="utf-8")
    )
    assert lock_first["lock_id"] == lock_second["lock_id"]
    # 改变预注册配置 = 新计划身份（必须创建新评测运行）。
    assert StatisticsPlan().digest() != StatisticsPlan(bootstrap_seed=7).digest()


# ---------------------------------------------------------------------------
# Test plan 4：注入关键事实失败 → 即使分数全高仍阻止发布
# ---------------------------------------------------------------------------

def test_fabricated_first_person_blocks_release_even_with_high_scores(
    tmp_path: Path, workspace: Path
):
    """虚构亲历注入：candidate 保真硬门失败 → 面 failed（多数票不能放行）。"""

    class FabricatingPort(FakeGenerationPort):
        """输出追加编造亲历（first_person 保真检查必失败）。"""

        def generate(
            self, *, system_prompt, user_prompt, params
        ) -> GenerationResult:
            result = super().generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                params=params,
            )
            return result.model_copy(
                update={"text": result.text + "我上周去过咖啡店。"
                        if result.text else ""}
            )

    runner = _make_runner(
        tmp_path, workspace, port=FabricatingPort()
    )
    summary = runner.run()
    gate = summary.gate_report
    assert gate is not None
    # 保真硬门失败 → 对应面 failed（虚构亲历为 critical）。
    assert gate.failed_case_ids, "应记录保真硬门失败 case id"
    for surface, verdict in gate.surface_verdicts.items():
        assert verdict == "failed", f"{surface} 应在虚构亲历注入下阻止发布"
    assert summary.verdict == "failed"


def test_fidelity_missing_cases_fail_closed(tmp_path: Path, workspace: Path):
    """保真检查缺失 → fail closed（不凭空通过）。"""

    class BrokenFidelityPort(FakeGenerationPort):
        """返回成功状态但正文为空：保真检查失败关闭。"""

        def generate(
            self, *, system_prompt, user_prompt, params
        ) -> GenerationResult:
            return GenerationResult(
                text="",
                model_id="fake-model",
                parameters=params.model_dump(),
                status=GenerationStatus.SUCCESS,
            )

    runner = _make_runner(tmp_path, workspace, port=BrokenFidelityPort())
    summary = runner.run()
    assert summary.verdict in ("failed", "inconclusive")
    assert summary.verdict != "passed"


# ---------------------------------------------------------------------------
# AC-12：CLI 非零退出（普通非人味评测不受影响由 cli/evaluate.py 独立保证）
# ---------------------------------------------------------------------------

def _fake_summary(gate_report, *, verdict: str = "failed"):
    from types import SimpleNamespace

    return SimpleNamespace(
        run_id="run-test",
        verdict=verdict,
        holdout_excluded=0,
        surface_verdicts=(
            gate_report.surface_verdicts if gate_report is not None else {}
        ),
        surface_cases={},
        surface_case_total={},
        canary_passed={},
        panel_issues=[],
        reasons=[],
        packet_path=None,
        mapping_path=None,
        judge_count=3,
        gate_report=gate_report,
    )


def _run_cli(monkeypatch, summary):
    from bridges.humanize_eval import cli

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            pass

        def run(self):
            return summary

    monkeypatch.setattr(
        "bridges.humanize_eval.runner.HumanizeRunner", FakeRunner
    )
    return cli.main(["run", "--outdir", "out-dir", "--judges", "3"])


def test_cli_returns_nonzero_when_candidate_fails(monkeypatch, capsys):
    """候选未通过（failed）→ CLI 返回非零并打印稳定中文原因。"""
    gate = _chat_gate(
        scores=[1.0] * 40,
        fidelity=FidelityGateInput(critical_failed_cases=["c3"]),
    )
    exit_code = _run_cli(monkeypatch, _fake_summary(gate))
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "发布门未通过" in out
    assert "candidate 不得发布" in out
    assert "硬门" in out  # 稳定中文阻断原因


def test_cli_returns_zero_when_gate_passes(monkeypatch):
    """全部面 passed → CLI 返回 0。"""
    gate = _chat_gate(scores=[1.0] * 40)
    assert _run_cli(monkeypatch, _fake_summary(gate, verdict="passed")) == 0


def test_cli_returns_nonzero_when_inconclusive(monkeypatch):
    """inconclusive 不得视为通过：CLI 同样返回非零。"""
    gate = _chat_gate(scores=[1.0] * 5)
    exit_code = _run_cli(monkeypatch, _fake_summary(gate))
    assert exit_code == 1


def test_cli_returns_nonzero_when_gate_missing_and_verdict_not_passed(
    monkeypatch,
):
    """无门报告（生成失败/裁判全部不可用）且结论非 passed → 非零。"""
    exit_code = _run_cli(
        monkeypatch, _fake_summary(None, verdict="inconclusive")
    )
    assert exit_code == 1
