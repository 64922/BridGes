"""工单 39：模型承载的正式路径收据（论文概述、GitHub 借鉴角度）。

直接调用生产生成器与真实模型；生成器降级（无概述/无借鉴角度）时收据
标记 failed，运行锁与输出照常保留供审计。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from bridges.evaluation.expression_path_receipts import build_receipt
from bridges.evaluation.expression_real_gateway import receipt_run_context


def _expression_policy() -> Any:
    from bridges.chat.global_writing_policy import GlobalWritingPolicyCompiler

    return GlobalWritingPolicyCompiler(candidate_enabled=True).compile(
        "companion", user_text="按证据给中文概述，不编造。"
    )


def run_paper_receipt(gateway: Any, quota: Any) -> dict[str, Any]:
    from bridges.paper.contracts import PaperRecommendation
    from bridges.paper.presenting import PaperSummaryGenerator

    paper = PaperRecommendation(
        order=1,
        arxiv_id="2401.00001",
        title="A Survey of Linear Algebra Foundations",
        source="arxiv",
        abs_url="https://arxiv.org/abs/2401.00001",
        role="survey",
        reason_zh="入门综述，覆盖向量与矩阵基础。",
        match_basis="标题命中线性代数主题词。",
    )
    abstract = (
        "This survey introduces vector spaces and matrix decompositions "
        "with worked examples for beginners."
    )
    outcome = PaperSummaryGenerator(gateway).generate(
        receipt_run_context(),
        [paper],
        abstracts={"2401.00001": abstract},
        model_id=None,
        model_quota=quota,
        expression=_expression_policy(),
    )
    output = json.dumps(
        {
            "summaries": outcome.summaries,
            "note": outcome.note,
            "dropped": outcome.dropped,
        },
        ensure_ascii=False,
    )
    lock = outcome.lock.model_dump(mode="json") if outcome.lock is not None else None
    receipt = build_receipt(
        "paper.summary",
        execution_kind="real_generator_model_call",
        output=output,
        model_locks=[lock] if lock else [],
        notes="论文概述生成器真实调用；概述受证据逐字门控。",
    )
    if not outcome.summaries:
        receipt["status"] = "failed"
        receipt["notes"] += " 未产生概述（模型失败或证据门控）。"
    return receipt


def run_github_receipt(gateway: Any, quota: Any) -> dict[str, Any]:
    from bridges.github.contracts import (
        GithubCoverage,
        GithubLicenseCheck,
        GithubMaintenanceEvidence,
        GithubReadmeStatus,
        GithubRecommendation,
    )
    from bridges.github.presenting import GithubInsightGenerator

    recommendation = GithubRecommendation(
        rank=1,
        full_name="demo/bookswap",
        html_url="https://github.com/demo/bookswap",
        coverage=GithubCoverage.WHOLE,
        coverage_note="示例项目覆盖书籍发布与交换功能。",
        description="学生书籍交换示例项目。",
        topics=["books", "exchange"],
        readme_status=GithubReadmeStatus.READ,
        readme_excerpt="发布书籍：学生发布想卖的书，再与其他同学交换。",
        maintenance=GithubMaintenanceEvidence(note="示例证据，用于真实生成收据。"),
        license=GithubLicenseCheck(detected=True, spdx_id="MIT", note="元数据标注 MIT。"),
        reason_zh="功能矩阵匹配示例。",
        borrow_note="可借鉴其发布表单与书籍状态流转的划分方式。",
        retrieved_at=datetime.now(UTC),
    )
    outcome = GithubInsightGenerator(gateway).generate(
        receipt_run_context(),
        [recommendation],
        model_id=None,
        model_quota=quota,
        writing_policy=_expression_policy(),
    )
    output = json.dumps(
        {"insights": outcome.insights, "note": outcome.note, "dropped": outcome.dropped},
        ensure_ascii=False,
    )
    lock = outcome.lock.model_dump(mode="json") if outcome.lock is not None else None
    receipt = build_receipt(
        "github.insights",
        execution_kind="real_generator_model_call",
        output=output,
        model_locks=[lock] if lock else [],
        notes="GitHub 借鉴角度生成器真实调用；只接受逐字安全句。",
    )
    if not outcome.insights:
        receipt["status"] = "failed"
        receipt["notes"] += " 未产生借鉴角度（模型失败或逐字门控）。"
    return receipt


__all__ = ["run_github_receipt", "run_paper_receipt"]
