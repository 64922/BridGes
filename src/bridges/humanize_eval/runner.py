"""运行锁、append-only 原始输出与分别报告（Issue 01 + Issue 09）。

每次运行记录：代码/build、语料版本与面级哈希、契约版本、来源账本哈希、
SUT/策略/SKILL 哈希、模型快照、temperature/top_p/seed、重试、匿名种子、
执行次数、环境、裁判版本与 case 哈希。原始输出 append-only：相同运行锁
的重放生成新的证据记录，绝不覆盖旧记录。

Issue 09 起聊天与文章分别建集、分别报告：``surface_verdicts`` 按
chat_naturalness / article_humanization 分别给出结论，任一面的有效案例
不足 40 时该面结论固定为 inconclusive。holdout 默认冻结：未解封时只运行
development 分区，冻结哈希不一致或提前读取一律拒绝。
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from bridges import __version__ as package_version
from bridges.humanize_eval.cases import (
    CORPUS_VERSION,
    HUMANIZE_CASES,
    HumanizeCase,
    HumanizeCaseKind,
    MIN_CASES_PER_SURFACE,
    case_hashes,
    corpus_hashes,
    ledger_hashes,
    validate_cases,
)
from bridges.humanize_eval.fidelity import FidelityReport, run_fidelity_check
from bridges.humanize_eval.generation import (
    GenerationPort,
    GenerationResult,
    GenerationStatus,
)
from bridges.humanize_eval.holdout import HoldoutController, HoldoutError
from bridges.humanize_eval.judges import (
    JudgeVerdict,
    SystemJudge,
    judge_pair,
)
from bridges.humanize_eval.packet import (
    JudgePacket,
    OrganizerMapping,
    build_packets,
    scan_packet_leaks,
)
from bridges.humanize_eval.suts import (
    SUTOutput,
    SUTSpec,
    SUTUnavailableError,
    build_suts,
    execute_sut,
)

#: 运行锁不完整时按重要度排序的缺失键（用于 inconclusive 报告）。
LOCK_REQUIRED_KEYS = (
    "lock_id",
    "code_commit",
    "code_digest",
    "corpus_hashes",
    "ledger_hashes",
    "contract_versions",
    "case_hashes",
    "sut_specs",
    "model_snapshot",
    "parameters",
    "environment",
    "run_count",
    "judge_versions",
    "started_at",
    "ended_at",
)

#: 语料面的报告名（分别报告）。
SURFACE_REPORT_NAMES = {
    HumanizeCaseKind.CHAT: "chat_naturalness",
    HumanizeCaseKind.ARTICLE: "article_humanization",
}


class HumanizeRunLock(BaseModel):
    """一次评测运行实际使用内容的不可变快照（digest 作为锁身份）。"""

    lock_id: str
    code_commit: str = Field(description="git HEAD 提交哈希。")
    code_digest: str = Field(description="代码/构建摘要（提交 + 包版本）。")
    corpus_hashes: dict[str, str] = Field(description="语料面 -> 注册表聚合哈希。")
    ledger_hashes: dict[str, str] = Field(description="语料面 -> 来源账本聚合哈希。")
    contract_versions: dict[str, str] = Field(
        description="语料/契约 schema 版本（如 case_schema）。"
    )
    case_hashes: dict[str, str] = Field(description="case_id -> 内容哈希。")
    sut_specs: list[SUTSpec] = Field(description="四个 SUT 规格（含策略哈希）。")
    model_snapshot: dict[str, str] = Field(description="能力 -> 模型快照。")
    parameters: dict[str, float | int] = Field(
        description="temperature/top_p/seed/retries/anon_seed。"
    )
    environment: dict[str, str] = Field(
        description="平台/python/包版本等环境快照。"
    )
    run_count: int = Field(description="该输出目录下的执行次数。")
    judge_versions: dict[str, str] = Field(description="裁判 id -> 版本。")
    started_at: str
    ended_at: str

    def digest(self) -> str:
        """锁身份：除 lock_id/时间戳/执行计数外全部内容参与哈希。

        run_count 是观测记录（第几次执行）而非配置，不参与锁身份：
        同一配置的重放必须产生同一锁身份（不同 run 实例）。
        """
        payload = self.model_dump(
            exclude={"lock_id", "started_at", "ended_at", "run_count"}, mode="json"
        )
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RunOutcome(BaseModel):
    """一次 SUT×case 执行的完整产物（原始证据，append-only）。"""

    run_id: str
    case_id: str
    sut_id: str
    output_text: str = Field(description="候选正文（原始输出，仅存 raw 目录）。")
    generation: GenerationResult
    fidelity: FidelityReport | None = Field(default=None)
    saved_at: str


class JudgeOutcome(BaseModel):
    """一个裁判对一个 item 的双向判断产物。"""

    judge_id: str
    judge_version: str
    item_id: str
    verdict_ab: JudgeVerdict
    verdict_ba: JudgeVerdict
    consistent: bool


class RunSummary(BaseModel):
    """一次运行的脱敏执行摘要（不含私人正文；聊天/文章分别报告）。"""

    run_id: str
    verdict: str = Field(description="inconclusive / passed / failed。")
    reasons: list[str] = Field(default_factory=list)
    lock_complete: bool
    missing_lock_items: list[str] = Field(default_factory=list)
    sut_status: dict[str, str] = Field(description="sut_id -> success/not_configured/failed。")
    generation_counts: dict[str, int] = Field(description="sut_id -> 成功调用数。")
    fidelity_failures: list[str] = Field(description="保真失败类型（去重）。")
    fidelity_missing: list[str] = Field(description="缺失的检查项（去重）。")
    judge_count: int = Field(description="可用的裁判数。")
    judge_diverse: bool = Field(description="是否满足裁判多样性。")
    inconsistent_items: list[str] = Field(description="顺序一致性失败的 item。")
    invalid_verdicts: int = Field(description="无效裁决总数。")
    packet_id: str | None = Field(default=None)
    packet_leaks: list[str] = Field(default_factory=list)
    packet_path: str | None = Field(default=None)
    mapping_path: str | None = Field(default=None)
    cases_ran: int
    cases_total: int
    surface_verdicts: dict[str, str] = Field(
        description="chat_naturalness/article_humanization 分别结论。"
    )
    surface_cases: dict[str, int] = Field(
        description="每面成功完成的 case 数。"
    )
    surface_case_total: dict[str, int] = Field(
        description="每面总 case 数（含 holdout 排除）。"
    )
    holdout_excluded: int = Field(
        default=0, description="被 holdout 冻结排除的 case 数。"
    )
    holdout_problems: list[str] = Field(
        default_factory=list, description="holdout 冻结哈希校验问题。"
    )
    started_at: str
    ended_at: str


class HumanizeRunError(Exception):
    """运行错误：append-only 冲突或前置条件失败。"""


class HumanizeRunner:
    """评测运行器：校验 → 生成 → 保真 → 裁判 → 导出，全程可重放。"""

    def __init__(
        self,
        outdir: Path,
        workspace: Path,
        *,
        snapshot_dir: str | None = None,
        port: GenerationPort | None = None,
        judges: list[SystemJudge] | None = None,
        anon_seed: int = 2026,
        allow_real: bool = False,
        holdout: HoldoutController | None = None,
        surface: str | None = None,
        allow_holdout: bool = False,
    ) -> None:
        self.outdir = Path(outdir)
        self.workspace = Path(workspace)
        self.snapshot_dir = snapshot_dir
        self.port = port if port is not None else self._default_port()
        self.judges = judges
        self.anon_seed = anon_seed
        self.allow_real = allow_real
        self.holdout = holdout
        self.surface = surface
        self.allow_holdout = allow_holdout

    def _default_port(self) -> GenerationPort:
        from bridges.humanize_eval.generation import QwenGenerationPort

        return QwenGenerationPort()

    def _guard_real_port(self) -> None:
        """防御：已配置真实凭据但未显式允许时拒绝运行（防 CI 误触真实调用）。

        无凭据时 QwenGenerationPort 只报告 not_configured，不发起网络调用，
        允许继续以 inconclusive 列出缺项；假端口不受影响。
        """
        from bridges.humanize_eval.generation import QwenGenerationPort

        if (
            isinstance(self.port, QwenGenerationPort)
            and self.port.configured
            and not self.allow_real
        ):
            raise HumanizeRunError(
                "已配置全局百炼凭据但未显式允许真实调用；"
                "请显式设置 allow_real=True（CLI：--allow-real）。"
            )

    def _select_cases(self) -> tuple[list[HumanizeCase], list[str]]:
        """选择本次运行的案例：surface 过滤 + holdout 访问门。

        返回 (可运行案例, holdout 问题清单)。holdout 访问由
        ``HoldoutController.runnable_case_ids`` 统一裁决（未解封只跑
        development；解封后含 holdout 并记录审计）；显式请求 holdout
        而未解封则抛错拒绝（提前读取）。
        """
        problems: list[str] = []
        if self.holdout is not None:
            problems = self.holdout.validate_against(case_hashes())
            self.holdout.check_access(requested_holdout=self.allow_holdout)
        cases = list(HUMANIZE_CASES)
        if self.surface:
            cases = [
                case for case in cases
                if case.kind.value == self.surface
            ]
        if self.holdout is not None:
            runnable_ids = self.holdout.runnable_case_ids(
                [case.case_id for case in cases]
            )
            cases = [case for case in cases if case.case_id in runnable_ids]
            if self.allow_holdout and self.holdout.load_manifest() is not None:
                manifest = self.holdout.load_manifest()
                if manifest is not None and manifest.unsealed:
                    sealed = self.holdout.sealed_ids()
                    accessed = [cid for cid in runnable_ids if cid in sealed]
                    if accessed:
                        self.holdout.audit_access(
                            case_ids=accessed,
                            commit=self._current_commit(),
                        )
        return cases, problems

    def _current_commit(self) -> str:
        """git HEAD 提交（运行锁记录；读不到时为 unknown，不依赖 SUT 顺序）。"""
        import subprocess

        try:
            proc = subprocess.run(
                ["git", "-C", str(self.workspace), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
            )
            return proc.stdout.strip() or "unknown"
        except (OSError, subprocess.SubprocessError):
            return "unknown"

    def _lock(
        self, suts: list[SUTSpec], run_count: int
    ) -> HumanizeRunLock:
        started = datetime.now(UTC).isoformat()
        code_commit = self._current_commit()
        return HumanizeRunLock(
            lock_id="",
            code_commit=code_commit,
            code_digest=f"{package_version}-{code_commit[:12]}",
            corpus_hashes=corpus_hashes(),
            ledger_hashes=ledger_hashes(),
            contract_versions={
                "case_schema": CORPUS_VERSION,
                "corpus_version": CORPUS_VERSION,
                "task_contract": "3",
            },
            case_hashes=case_hashes(),
            sut_specs=suts,
            model_snapshot={"qwen_text_chat": suts[0].model_id if suts else ""},
            parameters={
                "temperature": suts[0].parameters.temperature if suts else 0.7,
                "top_p": suts[0].parameters.top_p if suts else 0.9,
                "seed": suts[0].parameters.seed if suts else 2026,
                "retries": suts[0].parameters.retries if suts else 2,
                "anon_seed": self.anon_seed,
            },
            environment={
                "platform": platform.system(),
                "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                "package": package_version,
            },
            run_count=run_count,
            judge_versions=(
                {j.judge_id: j.judge_version for j in self.judges}
                if self.judges
                else {}
            ),
            started_at=started,
            ended_at="",
        )

    def _persist_lock(self, lock: HumanizeRunLock) -> tuple[str, Path]:
        digest = lock.digest()
        # 锁身份稳定（digest），实例 run 唯一（时间戳）：重放产生新 run，
        # 旧记录 append-only 保留，绝不覆盖。
        lock.lock_id = f"lock-{digest[:16]}"
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        run_id = f"run-{digest[:16]}-{stamp}"
        lock.ended_at = ""
        runs_dir = self.outdir / "runs" / run_id
        lock_file = runs_dir / "lock.json"
        _append_only_write(lock_file, lock.model_dump_json(indent=2))
        return run_id, runs_dir

    def run(self) -> RunSummary:
        """执行一次完整运行：校验 → 生成 → 保真 → 裁判 → 导出。"""
        self._guard_real_port()
        started = datetime.now(UTC).isoformat()
        problems = validate_cases()
        if problems:
            raise HumanizeRunError("案例校验失败：\n" + "\n".join(problems))

        try:
            runnable_cases, holdout_problems = self._select_cases()
        except HoldoutError as exc:
            raise HumanizeRunError(str(exc)) from exc
        if holdout_problems:
            raise HumanizeRunError(
                "holdout 冻结校验失败：\n" + "\n".join(holdout_problems)
            )
        sealed_ids = (
            self.holdout.sealed_ids() if self.holdout is not None else set()
        )
        excluded = len(sealed_ids & {case.case_id for case in HUMANIZE_CASES})

        suts = build_suts(self.workspace, snapshot_dir=self.snapshot_dir)
        run_count = self._next_run_count()
        lock = self._lock(suts, run_count)
        run_id, runs_dir = self._persist_lock(lock)

        outputs_by_sut: dict[str, list[SUTOutput]] = {}
        sut_status: dict[str, str] = {}
        generation_counts: dict[str, int] = {}
        fidelity_failures: list[str] = []
        fidelity_missing: list[str] = []
        for spec in suts:
            outputs: list[SUTOutput] = []
            if not spec.available:
                sut_status[spec.sut_id] = "unavailable"
                generation_counts[spec.sut_id] = 0
                outputs_by_sut[spec.sut_id] = []
                continue
            for case in runnable_cases:
                try:
                    output = execute_sut(
                        spec, case, self.port, self.workspace
                    )
                except SUTUnavailableError as exc:
                    sut_status[spec.sut_id] = "unavailable"
                    raise HumanizeRunError(str(exc)) from exc
                except Exception as exc:
                    sut_status[spec.sut_id] = "failed"
                    raise HumanizeRunError(f"SUT {spec.sut_id} 执行失败：{exc}") from exc
                # 成功状态空输出 = 畸形响应：降级为失败，绝不当作成功证据。
                if output.generation.ok and not output.text.strip():
                    output.generation.status = GenerationStatus.FAILED
                    output.generation.error_code = "empty_output"
                    output.generation.error_message = "模型返回成功状态但正文为空。"
                fidelity = None
                if output.generation.ok:
                    fidelity = run_fidelity_check(case, output.text)
                    # 任一保真失败（含 MAJOR）都阻止通过：保真硬门不允许被均分抵消。
                    # 失败记录带 case_id 前缀，面级归属按结构化前缀判定。
                    failed_checks = [
                        item for item in fidelity.checks if item.effective_failure
                    ]
                    if failed_checks:
                        fidelity_failures.extend(
                            f"{case.case_id}:{item.reason}"
                            for item in failed_checks
                        )
                    fidelity_missing.extend(
                        f"{case.case_id}:{item.label}"
                        for item in fidelity.missing_checks
                    )
                outputs.append(output)
                outcome = RunOutcome(
                    run_id=run_id,
                    case_id=case.case_id,
                    sut_id=spec.sut_id,
                    output_text=output.text,
                    generation=output.generation,
                    fidelity=fidelity,
                    saved_at=datetime.now(UTC).isoformat(),
                )
                raw_file = runs_dir / "raw" / f"{spec.sut_id}__{case.case_id}.json"
                _append_only_write(raw_file, outcome.model_dump_json(indent=2))
            sut_status[spec.sut_id] = (
                "success"
                if outputs and all(o.generation.ok for o in outputs)
                else (
                    "not_configured"
                    if all(o.generation.status.value == "not_configured"
                           for o in outputs)
                    else "failed"
                )
            )
            generation_counts[spec.sut_id] = sum(
                1 for o in outputs if o.generation.ok
            )
            outputs_by_sut[spec.sut_id] = outputs

        # 裁判与匿名包：current vs candidate 配对（reference/plain 输出留作原始证据）。
        packet: JudgePacket | None = None
        mapping: OrganizerMapping | None = None
        judge_outcomes: list[JudgeOutcome] = []
        inconsistent_items: list[str] = []
        invalid_verdicts = 0
        if self.judges and all(
            o.generation.ok for o in outputs_by_sut.get("current-production", [])
        ) and all(
            o.generation.ok for o in outputs_by_sut.get("candidate", [])
        ):
            packet, mapping = build_packets(
                packet_id=f"packet-{run_id}",
                cases=runnable_cases,
                outputs_by_sut=outputs_by_sut,
                anon_seed=self.anon_seed,
            )
            packet_leaks = scan_packet_leaks(packet)
            for judge in self.judges:
                for item in packet.items:
                    verdict_ab, verdict_ba = judge_pair(judge, item)
                    consistent = (
                        verdict_ab.is_valid and verdict_ba.is_valid
                    )
                    if not consistent:
                        inconsistent_items.append(item.item_id)
                        invalid_verdicts += 2
                    judge_outcomes.append(
                        JudgeOutcome(
                            judge_id=judge.judge_id,
                            judge_version=judge.judge_version,
                            item_id=item.item_id,
                            verdict_ab=verdict_ab,
                            verdict_ba=verdict_ba,
                            consistent=consistent,
                        )
                    )
            packet_file = runs_dir / "packets" / f"{packet.packet_id}.json"
            mapping_file = runs_dir / "mappings" / f"{packet.packet_id}.json"
            _append_only_write(packet_file, packet.model_dump_json(indent=2))
            _append_only_write(mapping_file, mapping.model_dump_json(indent=2))
        else:
            packet_leaks = []

        ended = datetime.now(UTC).isoformat()
        lock.ended_at = ended
        _append_only_write(
            runs_dir / "lock.json", lock.model_dump_json(indent=2), allow_update=True
        )

        summary = self._aggregate(
            run_id=run_id,
            started_at=started,
            ended_at=ended,
            lock=lock,
            suts=suts,
            runnable_cases=runnable_cases,
            outputs_by_sut=outputs_by_sut,
            sut_status=sut_status,
            generation_counts=generation_counts,
            fidelity_failures=fidelity_failures,
            fidelity_missing=fidelity_missing,
            packet=packet,
            packet_leaks=packet_leaks,
            inconsistent_items=inconsistent_items,
            invalid_verdicts=invalid_verdicts,
            judge_outcomes=judge_outcomes,
            holdout_problems=holdout_problems,
            holdout_excluded=excluded,
        )
        if packet is not None:
            summary = summary.model_copy(
                update={
                    "packet_path": str(runs_dir / "packets" / f"{packet.packet_id}.json"),
                    "mapping_path": str(runs_dir / "mappings" / f"{packet.packet_id}.json"),
                }
            )
        summary_file = runs_dir / "summary.json"
        _append_only_write(summary_file, summary.model_dump_json(indent=2))
        return summary

    def _next_run_count(self) -> int:
        runs_dir = self.outdir / "runs"
        if not runs_dir.is_dir():
            return 1
        return sum(1 for entry in runs_dir.iterdir() if entry.is_dir()) + 1

    def _surface_aggregate(
        self,
        cases: list[HumanizeCase],
        outputs_by_sut: dict[str, list[SUTOutput]],
        *,
        success_ok: bool,
        fidelity_failures: list[str],
        fidelity_missing: list[str],
        reasons: list[str],
        surface_reasons: dict[str, list[str]],
        surface_cases_ok: dict[str, int],
    ) -> None:
        """按语料面聚合结论：案例数不足或该面存在失败即 inconclusive。"""
        per_kind: dict[HumanizeCaseKind, list[HumanizeCase]] = {}
        for case in cases:
            per_kind.setdefault(case.kind, []).append(case)
        # 面级完成数取 current-production 的成功输出（该 SUT 不可用时取 candidate）。
        current_ok = {
            o.case_id for o in outputs_by_sut.get("current-production", [])
            if o.generation.ok
        } or {
            o.case_id for o in outputs_by_sut.get("candidate", [])
            if o.generation.ok
        }
        for kind, name in SURFACE_REPORT_NAMES.items():
            surface_cases = per_kind.get(kind, [])
            surface_reasons[name] = []
            if not surface_cases:
                surface_reasons[name].append("该语料面无案例，结论固定为 inconclusive。")
                continue
            if len(surface_cases) < MIN_CASES_PER_SURFACE:
                surface_reasons[name].append(
                    f"该面只有 {len(surface_cases)} 个案例（少于 "
                    f"{MIN_CASES_PER_SURFACE}），结论固定为 inconclusive。"
                )
            # 保真失败与缺失检查按案例归属面记录（结构化 case_id 前缀）。
            case_ids = {c.case_id for c in surface_cases}
            surface_failures = [
                f for f in fidelity_failures
                if f.split(":", 1)[0] in case_ids
            ]
            if surface_failures:
                surface_reasons[name].append(
                    "该面保真检查存在失败：" + "；".join(dict.fromkeys(surface_failures))
                )
            surface_missing = [
                m for m in fidelity_missing
                if m.split(":", 1)[0] in case_ids
            ]
            if surface_missing:
                surface_reasons[name].append(
                    "该面保真检查存在缺失项：" + "；".join(dict.fromkeys(surface_missing))
                )
            if not success_ok:
                surface_reasons[name].append("该面存在失败的模型调用，不得标记通过。")
            if surface_reasons[name]:
                reasons.append(f"[{name}] " + "；".join(surface_reasons[name]))
            surface_cases_ok[name] = len(
                [c for c in surface_cases if c.case_id in current_ok]
            )

    def _aggregate(
        self,
        *,
        run_id: str,
        started_at: str,
        ended_at: str,
        lock: HumanizeRunLock,
        suts: list[SUTSpec],
        runnable_cases: list[HumanizeCase],
        outputs_by_sut: dict[str, list[SUTOutput]],
        sut_status: dict[str, str],
        generation_counts: dict[str, int],
        fidelity_failures: list[str],
        fidelity_missing: list[str],
        packet: JudgePacket | None,
        packet_leaks: list[str],
        inconsistent_items: list[str],
        invalid_verdicts: int,
        judge_outcomes: list[JudgeOutcome],
        holdout_problems: list[str],
        holdout_excluded: int,
    ) -> RunSummary:
        reasons: list[str] = []
        missing_lock_items = [
            key for key in LOCK_REQUIRED_KEYS
            if getattr(lock, key, None) in ("", None, [], {}, 0)
        ]
        # 未配置裁判时 judge_versions 为空是合法状态（"未提供系统裁判"已单独报告）。
        if not self.judges:
            missing_lock_items = [
                key for key in missing_lock_items if key != "judge_versions"
            ]

        cases_ran = sum(generation_counts.values())
        cases_total = len(runnable_cases)

        if not all(spec.available for spec in suts):
            missing = [item for spec in suts for item in spec.missing_items]
            reasons.append("存在不可用 SUT，缺项：" + "；".join(missing))
        for spec in suts:
            if spec.frozen_external_reference:
                reasons.append(
                    f"{spec.sut_id} 为冻结外部参考（{spec.frozen_reference_note}），"
                    "不伪称完全可复现。"
                )
        if any(v == "not_configured" for v in sut_status.values()):
            reasons.append("缺少全局百炼运行凭据，未发生真实模型调用。")
        if any(v == "failed" for v in sut_status.values()):
            reasons.append("存在失败的模型调用，不得标记通过。")
        if any(v not in ("success", "unavailable") for v in sut_status.values()):
            reasons.append("不是全部可用 SUT 成功完成。")
        if fidelity_failures:
            failures = "；".join(dict.fromkeys(fidelity_failures))
            reasons.append(f"保真检查存在关键失败（{failures}）。")
        if fidelity_missing:
            missing_detail = "；".join(dict.fromkeys(fidelity_missing))
            reasons.append(f"保真检查存在缺失项（{missing_detail}）。")
        if not self.judges:
            reasons.append("未提供系统裁判。")
        elif len(self.judges) < 3:
            reasons.append(
                f"系统裁判只有 {len(self.judges)} 个（少于 3），结论固定为 inconclusive。"
            )
        elif judge_outcomes:
            # 多样性：全部裁判来自同一适配器族时不足（本项目当前只有 Qwen）。
            judge_ids = {outcome.judge_id for outcome in judge_outcomes}
            diverse = len({j.split("-")[0] for j in judge_ids}) > 1
            if not diverse:
                reasons.append(
                    "系统裁判多样性不足（全部来自同一模型家族），结论固定为 inconclusive。"
                )
            if inconsistent_items:
                reasons.append(
                    f"顺序一致性失败 item：{'、'.join(dict.fromkeys(inconsistent_items))}"
                )
            if invalid_verdicts:
                reasons.append(f"无效裁决数：{invalid_verdicts}。")
        if packet_leaks:
            reasons.append("裁判包匿名性泄漏：" + "；".join(packet_leaks))
        if missing_lock_items:
            reasons.append("运行锁不完整，缺少：" + "、".join(missing_lock_items))
        if holdout_problems:
            reasons.append("holdout 冻结校验问题：" + "；".join(holdout_problems))

        # 分别报告：聊天/文章各自检查样本量与失败。
        surface_reasons: dict[str, list[str]] = {}
        surface_cases_ok: dict[str, int] = {}
        success_ok = all(
            v in ("success", "unavailable") for v in sut_status.values()
        )
        self._surface_aggregate(
            runnable_cases,
            outputs_by_sut,
            success_ok=success_ok,
            fidelity_failures=fidelity_failures,
            fidelity_missing=fidelity_missing,
            reasons=reasons,
            surface_reasons=surface_reasons,
            surface_cases_ok=surface_cases_ok,
        )
        surface_verdicts = {
            name: "inconclusive" if surf_reasons else "passed"
            for name, surf_reasons in surface_reasons.items()
        }
        # 全量结论：任一原因存在即 inconclusive（passed 只有全部满足）。
        verdict = "inconclusive" if reasons else "passed"

        return RunSummary(
            run_id=run_id,
            verdict=verdict,
            reasons=reasons,
            lock_complete=not missing_lock_items,
            missing_lock_items=missing_lock_items,
            sut_status=sut_status,
            generation_counts=generation_counts,
            fidelity_failures=list(dict.fromkeys(fidelity_failures)),
            fidelity_missing=list(dict.fromkeys(fidelity_missing)),
            judge_count=len(self.judges or []),
            judge_diverse=len(
                {outcome.judge_id.split("-")[0] for outcome in judge_outcomes}
            ) > 1 if judge_outcomes else False,
            inconsistent_items=list(dict.fromkeys(inconsistent_items)),
            invalid_verdicts=invalid_verdicts,
            packet_id=packet.packet_id if packet else None,
            packet_leaks=packet_leaks,
            cases_ran=cases_ran,
            cases_total=cases_total,
            surface_verdicts=surface_verdicts,
            surface_cases=surface_cases_ok,
            surface_case_total={
                name: len(
                    [
                        c for c in runnable_cases
                        if c.kind is kind
                    ]
                )
                for kind, name in SURFACE_REPORT_NAMES.items()
            },
            holdout_excluded=holdout_excluded,
            holdout_problems=holdout_problems,
            started_at=started_at,
            ended_at=ended_at,
        )


def _append_only_write(
    path: Path, content: str, *, allow_update: bool = False
) -> None:
    """append-only 写入：文件已存在且内容不同时抛错（绝不覆盖旧证据）。

    allow_update=True 只允许把同一 run 的 lock.json 从"进行中"更新为
    "已完成"：旧键的值必须保持不变，只允许新增键（如 ended_at 补全）；
    同键值被篡改（如 case_hashes 置空）一律拒绝。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing == content:
            return
        if allow_update and existing.strip() and content.strip():
            try:
                old = json.loads(existing)
                new = json.loads(content)
                if (
                    isinstance(old, dict)
                    and isinstance(new, dict)
                    and set(old) <= set(new)
                    and all(
                        old[key] == new[key] or not old[key]
                        for key in old
                        if key in new
                    )
                ):
                    path.write_text(content, encoding="utf-8")
                    return
            except json.JSONDecodeError:
                pass
        raise HumanizeRunError(
            f"append-only 冲突：{path} 已存在且内容不同，拒绝覆盖（重放应产生新 run）。"
        )
    path.write_text(content, encoding="utf-8")
