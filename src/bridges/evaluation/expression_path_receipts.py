"""工单 39：正式用户可见路径的真实执行收据（确定性路径与固定文案）。

每条路径在收据里记录执行方式、真实输出摘要与（模型路径的）运行锁；
确定性渲染与固定文案直接调用生产函数，不做证据文件存在性检查。
"""

from __future__ import annotations

from datetime import UTC, datetime
from string import Formatter
from typing import Any

from bridges.evaluation.expression_provenance import sha256_text
from bridges.evaluation.expression_spec import FORMAL_PATH_IDS, FORMAL_PATHS


def _preview(text: str, limit: int = 240) -> str:
    collapsed = " ".join((text or "").split())
    return collapsed[:limit]


def path_meta(path_id: str) -> dict[str, str]:
    for path in FORMAL_PATHS:
        if path.path_id == path_id:
            return {
                "title": path.title,
                "render_kind": path.render_kind,
                "seam": path.seam,
            }
    raise KeyError(path_id)


def serialize_locks(locks: list[Any]) -> list[dict[str, Any]]:
    """把模型运行锁序列化为收据可写形态。"""

    return [lock.model_dump(mode="json") for lock in locks or []]


def build_receipt(
    path_id: str,
    *,
    execution_kind: str,
    output: str,
    model_locks: list[dict[str, Any]] | None = None,
    notes: str = "",
) -> dict[str, Any]:
    meta = path_meta(path_id)
    return {
        "path_id": path_id,
        **meta,
        "execution_kind": execution_kind,
        "status": "executed",
        "executed_at": datetime.now(UTC).isoformat(),
        "output_digest": sha256_text(output or ""),
        "output_chars": len(output),
        "output_preview": _preview(output),
        "model_locks": model_locks or [],
        "notes": notes,
    }


def _commute_projection() -> Any:
    from bridges.commute.contracts import (
        CommutePlace,
        CommuteRouteProjection,
        CommuteRouteStatus,
        CommuteRouteStep,
    )

    return CommuteRouteProjection(
        status=CommuteRouteStatus.SUCCESS,
        mode="walking",
        mode_label="步行",
        origin=CommutePlace(
            role="origin",
            original_phrase="宿舍",
            query="宿舍",
            name="学生宿舍",
            location="校园东区",
            match_basis="校园地点库",
        ),
        destination=CommutePlace(
            role="destination",
            original_phrase="实验室",
            query="实验室",
            name="实验楼",
            location="校园西区",
            match_basis="校园地点库",
        ),
        distance_m=1200,
        base_duration_seconds=900,
        suggested_total_seconds=960,
        steps=[
            CommuteRouteStep(index=1, instruction="沿主干道向西步行 800 米。"),
            CommuteRouteStep(index=2, instruction="在实验楼路口左转步行 400 米。"),
        ],
        path_verified=True,
    )


def _resources_inputs() -> tuple[Any, Any, Any]:
    from bridges.resources.contracts import (
        LearningResourcesProjection,
        ResourceItem,
        ResourcesQueryPlan,
        ResourcesStatus,
        ResourcesTermAnalysis,
    )

    analysis = ResourcesTermAnalysis(
        original_phrase="线性代数",
        normalized_term="线性代数",
        confidence=0.9,
    )
    plan = ResourcesQueryPlan(
        term="线性代数",
        book_query="线性代数 教材",
        video_query="线性代数 视频",
        book_candidate_limit=3,
        video_candidate_limit=3,
        rationale="按原词优先，同时给教材与视频。",
    )
    projection = LearningResourcesProjection(
        status=ResourcesStatus.SUCCESS,
        normalized_term="线性代数",
        path_verified=True,
        items=[
            ResourceItem(
                order=1,
                kind="book",
                title="线性代数（示例教材）",
                source="example-publisher",
                url="https://example.org/linear-algebra",
                stage="入门",
                reason_zh="覆盖向量、矩阵与特征值的入门教材。",
                match_basis="标题与原词一致",
            )
        ],
    )
    return analysis, plan, projection


def _tieba_projection() -> Any:
    from bridges.tieba.contracts import (
        ReadStatus,
        TiebaPostProjection,
        TiebaReply,
        TiebaResearchProjection,
        TiebaResearchStatus,
        TiebaTimeFilter,
    )

    post = TiebaPostProjection(
        url="https://tieba.example.org/p/1001",
        title="转专业经验交流",
        thread_id="1001",
        affiliation_evidence="吧内帖子，明确标注学院与年级。",
        read_status=ReadStatus.READ,
        pages_read=1,
        pages_limit=1,
        replies_obtained=True,
        replies=[
            TiebaReply(
                floor=1,
                posted_at="2026-09-01",
                is_original_poster=True,
                content="转专业要先修完大一核心课，再参加学院考核。",
            )
        ],
        retrieved_at="2026-10-06T08:00:00Z",
    )
    return TiebaResearchProjection(
        status=TiebaResearchStatus.SUCCESS,
        topic="转专业",
        original_question="转专业需要准备什么",
        topic_terms=["转专业"],
        time_filter=TiebaTimeFilter(
            requirement=None, year=None, applied=False, note="本轮问题没有提出时间条件。"
        ),
        confirmed_posts=[post],
    )


def _career_projection() -> Any:
    from bridges.career_plan.contracts import (
        CareerAnalysis,
        CareerPlanProjection,
        CareerPlanStatus,
        JobSample,
    )

    return CareerPlanProjection(
        status=CareerPlanStatus.SUCCESS,
        topic="后端开发实习",
        original_request="帮我规划后端开发实习路径",
        samples=[
            JobSample(
                url="https://example.org/job/1",
                source="example-jobs",
                source_label="示例岗位源",
                title="后端开发实习生",
                title_evidence="岗位标题逐字来自榜单。",
                city_evidence="岗位卡片标注城市。",
                city="杭州",
                retrieved_at="2026-10-06T08:00:00Z",
                read_status="read",
            )
        ],
        analysis=CareerAnalysis(
            sample_count=1,
            sample_scope_note="公开可读样本 1 个。",
            small_sample=True,
            overall_inference_stopped=False,
        ),
    )


def run_deterministic_path_receipts() -> list[dict[str, Any]]:
    """真实调用四条确定性渲染路径。"""

    from bridges.career_plan.presenting import render_result_content as render_career
    from bridges.commute.presenting import render_result_content as render_commute
    from bridges.resources.presenting import render_result_content as render_resources
    from bridges.tieba.presenting import render_result_content as render_tieba

    analysis, plan, projection = _resources_inputs()
    outputs = {
        "commute.result": render_commute(_commute_projection()),
        "resources.result": render_resources(analysis, plan, projection),
        "tieba.research": render_tieba(_tieba_projection()),
        "career_plan.result": render_career(_career_projection()),
    }
    return [
        build_receipt(path_id, execution_kind="real_renderer_call", output=output)
        for path_id, output in outputs.items()
    ]


_PLACEHOLDER_VALUES: dict[str, str] = {
    "names": "论文、通勤",
    "label": "联网检索",
    "message": "提供方未就绪",
    "recovery": "稍后重试",
    "goal": "找论文并对比 GitHub 项目",
    "items": "论文检索、GitHub 检索",
}


def _placeholder_names(template: str) -> list[str]:
    names: list[str] = []
    for _, field_name, _, _ in Formatter().parse(template):
        if field_name:
            names.append(field_name.split(".")[0].split("[")[0])
    return names


def run_fixed_copy_receipts() -> dict[str, Any]:
    """真实渲染全部固定文案模板（含占位符取值），聚合为一条路径收据。"""

    from bridges.state_copy.registry import STATE_COPY_REGISTRY, render_state_copy
    from bridges.state_copy.types import CopyStrategy

    fixed = {
        path: entry
        for path, entry in STATE_COPY_REGISTRY.items()
        if entry.strategy is CopyStrategy.FIXED_TEMPLATE
    }
    rendered: list[dict[str, Any]] = []
    failures: list[str] = []
    for path, entry in sorted(fixed.items()):
        values = {
            name: _PLACEHOLDER_VALUES.get(name, "示例")
            for name in _placeholder_names(entry.text or "")
        }
        try:
            output = render_state_copy(path, **values)
        except Exception as exc:  # noqa: BLE001 - 渲染失败必须进入收据
            failures.append(f"{path}: {exc}")
            continue
        rendered.append(
            {"path": path, "chars": len(output), "digest": sha256_text(output)}
        )
    payload_text = "\n".join(
        f"{item['path']}\t{item['digest']}" for item in rendered
    )
    receipt = build_receipt(
        "fixed_copy",
        execution_kind="real_template_render",
        output=payload_text,
        notes=(
            f"fixed_template 共 {len(fixed)} 条；成功渲染 {len(rendered)} 条；"
            f"失败 {len(failures)} 条。"
        ),
    )
    receipt["rendered_count"] = len(rendered)
    receipt["expected_count"] = len(fixed)
    receipt["rendered_entries"] = rendered
    receipt["failures"] = failures
    if failures or len(rendered) != len(fixed):
        receipt["status"] = "failed"
    return receipt


#: 复合路径的模型步骤由 paper/github 独立收据覆盖，合成与渲染是确定性的。
_MODEL_LOCK_EXEMPT = frozenset({"composite"})


def verify_path_receipts(payload: dict[str, Any]) -> list[str]:
    """校验 14 条正式路径与长任务真实材料的收据完整性。"""

    receipts = payload.get("paths") or []
    by_id = {entry.get("path_id"): entry for entry in receipts}
    problems: list[str] = []
    missing = sorted(FORMAL_PATH_IDS - set(by_id))
    if missing:
        problems.append(f"缺少正式路径收据：{', '.join(missing)}。")
    for path_id in sorted(FORMAL_PATH_IDS & set(by_id)):
        entry = by_id[path_id]
        if entry.get("status") != "executed":
            problems.append(f"{path_id} 未成功执行。")
        if not entry.get("output_digest"):
            problems.append(f"{path_id} 缺少输出摘要。")
        if (
            path_id != "fixed_copy"
            and entry.get("execution_kind") == "failed"
        ):
            problems.append(f"{path_id} 执行失败。")
        if (
            path_meta(path_id)["render_kind"] == "model_with_policy"
            and path_id not in _MODEL_LOCK_EXEMPT
            and not entry.get("model_locks")
        ):
            problems.append(f"{path_id} 缺少模型运行锁。")
    long_runs = payload.get("long_task_runs") or []
    if len(long_runs) < 2:
        problems.append("长任务真实执行材料不足（需含修复后的推导与多方法对照）。")
    for run in long_runs:
        scenario_id = run.get("scenario_id") or "unknown"
        if run.get("status") != "executed":
            problems.append(f"长任务 {scenario_id} 未真实完成。")
        if not run.get("transcript"):
            problems.append(f"长任务 {scenario_id} 缺少真实回答。")
        if not run.get("model_locks"):
            problems.append(f"长任务 {scenario_id} 缺少模型运行锁。")
    return problems


__all__ = [
    "build_receipt",
    "run_deterministic_path_receipts",
    "run_fixed_copy_receipts",
    "serialize_locks",
    "verify_path_receipts",
]
