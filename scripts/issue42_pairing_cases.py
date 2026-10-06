"""工单 42：锁定的配对任务数据与行为检查点。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

TERMINAL_STATUSES = {"done", "error", "stopped"}


@dataclass(frozen=True)
class TurnSpec:
    """一次用户发言；R07 可在发送后立即请求停止。"""

    content: str
    module_id: str | None = None
    stop_after_send: bool = False


@dataclass(frozen=True)
class CaseSpec:
    """一个配对场景：轮次与质量检查点。"""

    case_id: str
    scenario_id: str
    turns: tuple[TurnSpec, ...]
    checks: tuple[str, ...]
    budget_class: str


SCENARIOS: tuple[CaseSpec, ...] = (
    CaseSpec(
        case_id="A01.lightweight",
        scenario_id="A01",
        turns=(TurnSpec("你好，今天想随便聊两句，你最近怎么样？"),),
        checks=(
            "answer_nonempty",
            "single_chat_call",
            "no_external_model_capability",
            "no_task_created",
            "no_unnecessary_search",
        ),
        budget_class="lightweight",
    ),
    CaseSpec(
        case_id="A02.hint_override",
        scenario_id="A02",
        turns=(TurnSpec("从南区宿舍走到图书馆大概要多久？走路。", module_id="paper"),),
        checks=("answer_nonempty", "no_paper_results", "commute_route"),
        budget_class="normal",
    ),
    CaseSpec(
        case_id="A03.ambiguous",
        scenario_id="A03",
        turns=(TurnSpec("帮我找一下 transformer 相关的资料。"),),
        checks=("answer_nonempty", "asks_one_clarification", "no_paper_results"),
        budget_class="normal",
    ),
    CaseSpec(
        case_id="A11.hard_condition",
        scenario_id="A11",
        turns=(TurnSpec("帮我找几篇 Transformer 的论文，只要 2020 年以后的，不要联网搜索。"),),
        checks=("no_paper_results", "hard_condition_blocked"),
        budget_class="normal",
    ),
    CaseSpec(
        case_id="R07.stop_continue",
        scenario_id="R07",
        turns=(
            TurnSpec("推荐几本 Python 入门教材。"),
            TurnSpec("继续推荐更多 Python 教程。", stop_after_send=True),
            TurnSpec("继续刚才的任务，接着推荐。"),
        ),
        checks=(
            "answer_nonempty",
            "stop_no_auto_continue",
            "continue_creates_new_run",
        ),
        budget_class="normal",
    ),
    # 工单 42 预算校准：deep 类真实样本（论文搜索按创建路由进入 120 秒/30 秒
    # 预留的深入信封）。质量仅要求交付非空正文；本案例不冒充旧新质量对比。
    CaseSpec(
        case_id="C01.deep_paper",
        scenario_id="C01",
        turns=(
            TurnSpec("帮我找几篇关于自注意力序列建模的论文，逐篇说明主要方法和结论。"),
        ),
        checks=("answer_nonempty",),
        budget_class="deep",
    ),
    # 工单 42 预算校准：normal 类真实样本（已登记跨模块组合按 60 秒/15 秒预留，
    # 全部分支共享 09 账本与并发位）。姊妹分支可能短暂占满并发位，本案例只要求
    # 交付正文非空，并发竞争下的如实降级由报告与验收记录描述。
    CaseSpec(
        case_id="C02.normal_composite",
        scenario_id="C02",
        turns=(
            TurnSpec(
                "帮我找几篇关于自注意力序列建模的入门论文，再配一些配套的学习资料。"
            ),
        ),
        checks=("answer_nonempty",),
        budget_class="normal",
    ),
)


def corpus_sha256() -> str:
    """锁定配对任务数据内容。"""
    payload = json.dumps(
        [
            {
                "case_id": case.case_id,
                "scenario_id": case.scenario_id,
                "turns": [asdict(turn) for turn in case.turns],
                "checks": list(case.checks),
            }
            for case in SCENARIOS
        ],
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def projection_empty(value: Any) -> bool:
    """会话投影中某模块是否没有产出（None/空列表/空字典都算空）。"""
    if value is None:
        return True
    if isinstance(value, list | dict):
        return len(value) == 0
    return False


def evaluate_case_checks(case: dict[str, Any]) -> dict[str, bool | None]:
    """由一个用例的采集结果计算质量检查点（纯函数）。"""
    spec = next(item for item in SCENARIOS if item.case_id == case["case_id"])
    turns = case.get("turns", [])
    computed: dict[str, bool | None] = {}
    outstanding = [turn for turn in turns if str(turn.get("status") or "") != "stopped"]
    computed["answer_nonempty"] = (
        all(
            turn.get("status") == "done" and str(turn.get("answer") or "").strip()
            for turn in outstanding
        )
        if outstanding
        else None
    )
    chat_calls = sum(
        int(turn.get("capability_calls", {}).get("qwen_text_chat", 0)) for turn in turns
    )
    computed["single_chat_call"] = chat_calls == 1
    computed["no_external_model_capability"] = all(
        set(turn.get("capability_calls", {})) <= {"qwen_text_chat"} for turn in turns
    )
    tasks = case.get("tasks")
    computed["no_task_created"] = None if tasks is None else len(tasks) == 0
    computed["no_unnecessary_search"] = not any(bool(turn.get("search_planned")) for turn in turns)
    computed["no_paper_results"] = all(bool(turn.get("paper_search_empty", True)) for turn in turns)
    last = turns[-1] if turns else {}
    route = last.get("route") or {}
    computed["commute_route"] = route.get("module_id") == "commute"
    # 问号不是澄清证据；要求生产路由的结构化等待，并确认没有先查猜测领域。
    computed["asks_one_clarification"] = (
        last.get("status") == "done"
        and bool(route.get("clarification_question"))
        and not last.get("search_planned")
        and all(turn.get("paper_search_empty") is True for turn in turns)
    )
    computed["hard_condition_blocked"] = any(
        turn.get("error_code") == "network_not_allowed"
        and not turn.get("search_planned")
        and turn.get("paper_search_empty") is True
        for turn in turns
    )
    stopped_run_id = case.get("stopped_run_id")
    completed_runs = (
        {str(turn.get("run_id")) for turn in turns[:2]}
        if case["case_id"] == "R07.stop_continue"
        else set()
    )
    computed["stop_no_auto_continue"] = (
        stopped_run_id is not None
        and int(case.get("idle_new_runs", -1)) == 0
        and case.get("stopped_run_status") == "stopped"
    )
    computed["continue_creates_new_run"] = (
        case.get("continued_run_id") is not None
        and str(case.get("continued_run_id")) not in completed_runs
    )
    return {check: computed.get(check) for check in spec.checks}
