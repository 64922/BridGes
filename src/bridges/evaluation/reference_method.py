"""合法开源参考方法（Issue 40 AC-8 的 open_source_reference SUT）。

本方法是本仓库为评测套件独立编写的规则式基线：不调用任何模型，只对
案例的固定输入（知识库材料、预期 Claim）做确定性处理。它的行为刻意
朴素——绝不幻觉（只引用提供的材料）、绝不越界（不写任何画像）、
绝不个性化——因此可作为「事实安全但无个性化/无人味」的合法对照，
用于证明评测指标能区分系统行为差异。

许可证：随本仓库 MIT 发布（见套件 license-reference-method 记录）；
实现仅受公开评测方法学启发，不复制任何外部项目代码。
"""

from __future__ import annotations

from bridges.contracts.evaluation_suite import EvalCase, ToolCallRecord
from bridges.contracts.expression import Genre
from bridges.contracts.humanizer import (
    FactLockCheckResult,
    FactLockEntry,
    FactLockSeverity,
    FactLockStatus,
    HumanizerEdit,
    HumanizerEditKind,
    HumanizerFactCheckItem,
    HumanizerOutputContract,
    HumanizerPath,
    HumanizerResultProjection,
    HumanizerResultStatus,
    HumanizerTaskContract,
)
from bridges.skills.humanizer.factlock import extract_locks

#: 参考方法的固定版本（进入运行锁的 tool_adapter_versions）。
REFERENCE_METHOD_VERSION = "reference-method-v1"


class ReferenceMethodError(Exception):
    """参考方法执行错误。"""


class ReferenceMethod:
    """确定性规则式参考基线：按维度对固定输入做朴素处理。"""

    def run(self, case: EvalCase) -> dict[str, object]:
        """执行一个案例，返回结构化产物（与各维度执行器同形状）。"""
        if case.task_id == "task-humanization":
            return self._humanization(case)
        if case.task_id == "task-career":
            return self._career(case)
        if case.task_id == "task-multimodal":
            return self._multimodal(case)
        if case.task_id == "task-security":
            return self._security(case)
        if case.task_id == "task-profile-loop":
            return self._profile(case)
        if case.task_id == "task-teaching":
            return self._teaching(case)
        return self._science(case)

    # ------------------------------------------------------------------
    # 各维度朴素行为
    # ------------------------------------------------------------------

    def _docs(self, case: EvalCase) -> list[str]:
        docs = case.initial_state.get("kb_docs", [])
        return [str(doc.get("content", "")) for doc in docs]

    def _science(self, case: EvalCase) -> dict[str, object]:
        docs = self._docs(case)
        if not docs:
            return {
                "final_answer": "没有可用的本地资料，无法给出有依据的回答。",
                "citations": [],
                "profile_used": False,
                "tool_calls": [],
            }
        # 只引用第一份材料的原文片段，绝不超出材料范围。
        snippet = docs[0][:200]
        return {
            "final_answer": f"根据本地资料：{snippet}",
            "citations": [{"source": "reference-method", "detail": "本地材料原文摘录"}],
            "profile_used": False,
            "tool_calls": [],
        }

    def _teaching(self, case: EvalCase) -> dict[str, object]:
        docs = self._docs(case)
        material = docs[0] if docs else "（无本地材料）"
        return {
            "diagnosis": "参考方法不做先备诊断（朴素基线）。",
            "plan_steps": ["阅读材料", "做练习"],
            "quiz": None,
            "web_search_triggered": False,
            "answer": f"请阅读以下材料：{material[:200]}",
            "tool_calls": [],
        }

    def _profile(self, case: EvalCase) -> dict[str, object]:
        # 参考方法绝不写画像、绝不个性化——记录为空、回答通用。
        last = case.turns[-1].content[:100] if case.turns else ""
        return {
            "recorded_assertions": [],
            "final_answer": f"你好，你提到「{last}」。参考基线不维护画像，无法个性化。",
            "context_note": {"state": "empty", "profile_enabled": False, "profile_items": []},
            "tool_calls": [],
        }

    def _humanization(self, case: EvalCase) -> dict[str, object]:
        # 朴素基线：原样返回输入文本，事实锁逐一核对为保持，不做任何改写。
        source = str(
            case.initial_state.get("source_text", "")
            or (case.turns[-1].content if case.turns else "")
        )
        locks = extract_locks(source)
        entries = [
            FactLockEntry(
                entry_id=f"ref-lock-{index}",
                kind=lock.kind,
                surface_before=lock.surface,
                surface_after=lock.surface,
                canonical=lock.canonical,
                status=FactLockStatus.PRESERVED,
                severity=FactLockSeverity.INFO,
                note="参考基线不做改写，事实锁保持。",
            )
            for index, lock in enumerate(locks)
        ]
        projection = HumanizerResultProjection(
            task_id="reference-task",
            skill_id="bridges-humanizer",
            skill_version="1.0.0",
            path=HumanizerPath.REWRITE,
            genre=Genre.POPULAR_SCIENCE,
            contract=HumanizerTaskContract(
                path=HumanizerPath.REWRITE,
                genre=Genre.POPULAR_SCIENCE,
                source_text=source[:400],
                source_label="参考基线",
            ),
            status=HumanizerResultStatus.DONE,
            output=HumanizerOutputContract(
                final_text=source[:400],
                edits=[
                    HumanizerEdit(
                        edit_id="ref-1",
                        kind=HumanizerEditKind.NO_CHANGE,
                        original="（参考基线）",
                        revised="（参考基线）",
                        reason="参考基线不做改写。",
                    )
                ],
                fact_check=[
                    HumanizerFactCheckItem(
                        item="全部事实锁",
                        result="已核实（原样保持）",
                        evidence="原文事实锁",
                    )
                ],
                open_questions=["参考基线不识别任务契约。"],
            ),
            fact_lock_check=FactLockCheckResult(
                check_id="ref-factlock",
                source_text=source,
                entries=entries,
                blocking_conflicts=[],
                needs_human=[],
                passed=True,
            ),
            references=[],
            genre_check=["参考基线不执行体裁规则。"],
        )
        return projection.model_dump(mode="json")

    def _career(self, case: EvalCase) -> dict[str, object]:
        return {
            "items": [
                {
                    "title": "参考基线",
                    "content": "本基线不提供个性化规划；任何承诺性表述均不可信。",
                }
            ],
            "facts": [],
            "assumptions": ["参考基线不区分事实与假设"],
            "warnings": ["参考基线提醒：涉及就业、薪酬的承诺应保持警惕"],
            "evidence": [],
            "tool_calls": [],
        }

    def _multimodal(self, case: EvalCase) -> dict[str, object]:
        return {
            "asset_available": False,
            "alt_text": "",
            "model_id": None,
            "recovered": False,
            "tool_calls": [],
            "note": "参考基线不调用多模态能力，资产不可用。",
        }

    def _security(self, case: EvalCase) -> dict[str, object]:
        return {
            "final_answer": "参考基线：不处理该请求。",
            "refused": True,
            "tool_calls": [],
        }


def reference_tool_record() -> ToolCallRecord:
    """参考方法的固定工具记录（进入结果用于审计）。"""
    return ToolCallRecord(
        call_id="reference-method",
        capability_name="reference_method",
        capability_version=REFERENCE_METHOD_VERSION,
        actual_model_id=None,
        prompt_version="1",
        input_output_contract="reference-v1",
        status="success",
        error_code=None,
        latency_ms=0,
        result_summary={"method": "deterministic_rule_based"},
    )


__all__ = ["ReferenceMethod", "ReferenceMethodError", "REFERENCE_METHOD_VERSION",
    "reference_tool_record"]
