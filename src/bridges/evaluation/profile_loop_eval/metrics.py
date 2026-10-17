"""工单 18：确定性判定与统计汇总。

判定标准在运行前固定（``scenes.py`` 的关键词与本节规则）；统计口径：

- 事实召回：预期事实组中「任一活动条目命中任一关键词」的占比（分母为
  全部预期事实组）；
- 主体误归属/禁止内容：活动条目命中禁止关键词即违规，违规数如实计数；
- 采用率：追问轮采用切片同时满足 must_include / must_exclude 的占比；
- 修订、去重、复发、边界由对应驱动显式添加判定；
- 未匹配任何预期事实组的活动条目列为「待复核条目」，不计入精确率分母，
  报告样本量与复核口径，不做推断。
"""

from __future__ import annotations

from typing import Any

from bridges.evaluation.profile_loop_eval.types import (
    Check,
    FactGroup,
    SceneResult,
)

#: 否定标记：条目包含否定词时，否定样例的关键词命中不判违规。
_NEGATION_MARKERS = ("不", "没", "别", "无", "非", "未")


def _texts(record: dict[str, Any]) -> list[str]:
    return [
        str(item.get("text") or "")
        for item in record.get("items_after") or []
        if item.get("status") == "active"
    ]


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def check_fact_groups(
    result: SceneResult, groups: tuple[FactGroup, ...], *, prefix: str = "fact"
) -> None:
    texts = _texts(result.record.to_dict())
    for group in groups:
        hits = [text for text in texts if _contains_any(text, group.keywords)]
        result.add(
            f"{prefix}:{group.label}",
            bool(hits),
            f"命中条目={hits!r} 关键词={list(group.keywords)!r}",
        )


def check_forbidden(
    result: SceneResult, keywords: tuple[str, ...], *, prefix: str = "forbidden"
) -> None:
    texts = _texts(result.record.to_dict())
    for keyword in keywords:
        hits = [text for text in texts if keyword in text]
        result.add(
            f"{prefix}:{keyword}",
            not hits,
            f"违规条目={hits!r}",
        )


def check_negation(
    result: SceneResult, keywords: tuple[str, ...], *, prefix: str = "negation"
) -> None:
    texts = _texts(result.record.to_dict())
    for keyword in keywords:
        hits = [text for text in texts if keyword in text]
        bad = [text for text in hits if not any(marker in text for marker in _NEGATION_MARKERS)]
        result.add(
            f"{prefix}:{keyword}",
            not bad,
            f"未带否定标记的条目={bad!r}",
        )


def check_follow_up(
    result: SceneResult,
    *,
    must_include: tuple[str, ...],
    must_exclude: tuple[str, ...],
    prefix: str = "follow-up",
) -> None:
    follow_up = result.record.extra.get("follow_up") or {}
    adopted = [str(item) for item in follow_up.get("adopted_items") or []]
    joined = " ".join(adopted)
    if must_include:
        ok = _contains_any(joined, must_include)
        result.add(
            f"{prefix}:include",
            ok,
            f"采用条目={adopted!r} 需包含={list(must_include)!r}",
        )
    if must_exclude:
        bad = [item for item in adopted if _contains_any(item, must_exclude)]
        result.add(
            f"{prefix}:exclude",
            not bad,
            f"采用条目={adopted!r} 不得包含={list(must_exclude)!r}",
        )


def scene_status(checks: list[Check]) -> str:
    if not checks:
        return "no_checks"
    passed = sum(1 for check in checks if check.passed)
    if passed == len(checks):
        return "pass"
    if passed == 0:
        return "fail"
    return "partial"


def unmatched_items(record: dict[str, Any], expected_groups: tuple[FactGroup, ...]) -> list[str]:
    """活动条目中不命中任何预期事实组关键词的条目（待人工复核）。"""

    keywords = tuple(keyword for group in expected_groups for keyword in group.keywords)
    return [text for text in _texts(record) if not _contains_any(text, keywords)]


def summarize_results(results: list[SceneResult]) -> dict[str, Any]:
    """按判定前缀汇总计数与比例；只报实测。"""

    counters: dict[str, dict[str, int]] = {}
    matrix: list[dict[str, Any]] = []
    for result in results:
        record = result.record
        status = scene_status(result.checks)
        for check in result.checks:
            scope = check.check_id.split(":", 1)[0]
            bucket = counters.setdefault(scope, {"passed": 0, "failed": 0})
            bucket["passed" if check.passed else "failed"] += 1
        matrix.append(
            {
                "scene_id": record.scene_id,
                "kind": record.kind,
                "title": record.title,
                "split": record.split,
                "status": status,
                "checks": len(result.checks),
                "passed": sum(1 for check in result.checks if check.passed),
                "failed_checks": [check.check_id for check in result.checks if not check.passed],
                "elapsed_ms": record.elapsed_ms,
                "error": record.error,
            }
        )
    totals = {
        scope: {
            **bucket,
            "total": bucket["passed"] + bucket["failed"],
            "rate": round(bucket["passed"] / max(1, bucket["passed"] + bucket["failed"]), 4),
        }
        for scope, bucket in sorted(counters.items())
    }
    return {
        "matrix": matrix,
        "check_totals": totals,
        "sample_size": {
            "scenes": len(results),
            "scenarios": len([r for r in results if r.record.kind == "scenario"]),
            "extras": len([r for r in results if r.record.kind != "scenario"]),
        },
    }


def summarize_resources(locks: list[dict[str, Any]]) -> dict[str, Any]:
    """模型调用量、token 与延迟样本汇总；无价格依据时只报用量。"""

    by_capability: dict[str, dict[str, Any]] = {}
    prompt_tokens = 0
    completion_tokens = 0
    for lock in locks:
        bucket = by_capability.setdefault(
            str(lock.get("capability_name") or "unknown"),
            {"calls": 0, "success": 0, "failed": 0, "prompt_tokens": 0, "completion_tokens": 0},
        )
        bucket["calls"] += 1
        if str(lock.get("status")) == "success":
            bucket["success"] += 1
        else:
            bucket["failed"] += 1
        usage = lock.get("usage") or {}
        bucket["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        bucket["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        prompt_tokens += int(usage.get("prompt_tokens") or 0)
        completion_tokens += int(usage.get("completion_tokens") or 0)
    return {
        "model_calls": len(locks),
        "input_tokens": prompt_tokens,
        "output_tokens": completion_tokens,
        "by_capability": by_capability,
        "note": "只报告实测调用量与 token；缺少价格依据时不折算费用。",
    }


def latency_summary(results: list[SceneResult]) -> dict[str, Any]:
    """回答与提取延迟样本（毫秒），从中位数/p90 聚合。"""

    completion: list[int] = []
    extraction: list[int] = []
    for result in results:
        for turn in result.record.turns:
            run = turn.get("generation_run") or {}
            if isinstance(run.get("duration_ms"), int):
                completion.append(int(run["duration_ms"]))
            for extraction_run in turn.get("extraction_runs") or []:
                delta = _delta_ms(
                    extraction_run.get("created_at"), extraction_run.get("updated_at")
                )
                if delta is not None:
                    extraction.append(delta)
    return {
        "generation_duration_ms": _sample_stats(completion),
        "extraction_duration_ms": _sample_stats(extraction),
    }


def _delta_ms(start: Any, end: Any) -> int | None:
    from datetime import UTC, datetime

    if not isinstance(start, str) or not isinstance(end, str) or not start or not end:
        return None
    try:
        first = datetime.fromisoformat(start.replace("Z", "+00:00"))
        second = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    if first.tzinfo is None:
        first = first.replace(tzinfo=UTC)
    if second.tzinfo is None:
        second = second.replace(tzinfo=UTC)
    return max(0, int((second - first).total_seconds() * 1000))


def _sample_stats(samples: list[int]) -> dict[str, Any]:
    if not samples:
        return {"count": 0}
    ordered = sorted(samples)
    return {
        "count": len(ordered),
        "min": ordered[0],
        "median": ordered[len(ordered) // 2],
        "p90": ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))],
        "max": ordered[-1],
    }


__all__ = [
    "check_fact_groups",
    "check_forbidden",
    "check_negation",
    "check_follow_up",
    "scene_status",
    "unmatched_items",
    "summarize_results",
    "summarize_resources",
    "latency_summary",
]
