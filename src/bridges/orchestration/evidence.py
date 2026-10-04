"""复合生产边界：从真实产物重建证据，独立核对结论，不以运行完成代替可信。

生产路径的可信状态不能来自 ``DONE``：

1. 只有从内核 ``NodeArtifact`` 依赖链重新核验账户、会话、任务、配方/能力/
   Schema/内容哈希与依赖关系后，步骤才进入 ``qualified``；
2. 只提取绑定原证据的短结论与引用；综合与最终门只消费这些短结论；
3. 交付投影必须与真实产物一致（论文身份、资料条目、岗位投影、开源项目
   投影都逐字段回对）；
4. 私人证据（简历/背景正文）只留在本地 career 复核链中，不写入公网请求，
   也不重复保存进综合产物。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError

from bridges.chat.repository import ConversationRepository
from bridges.contracts.chat import ChatMessageStatus
from bridges.contracts.modules import ModuleDelivery
from bridges.kernel.contracts import (
    ARTIFACT_SCHEMA_VERSION,
    ArtifactTrust,
    KernelResult,
    KernelStatus,
    NodeArtifact,
    RecipeDefinition,
)
from bridges.kernel.repository import NodeKernelRepository
from bridges.orchestration.contracts import (
    Claim,
    CompositeStep,
    IndependentVerification,
    StepFailure,
    StepResult,
    StepState,
    VerificationSource,
    VerificationTrigger,
    VerificationVerdict,
)
from bridges.orchestration.executor import StepRunContext
from bridges.orchestration.production import step_result_from_delivery

#: 跨运行复用窗口：公网材料在此期限内且内容哈希/版本一致才可复用。
PUBLIC_EVIDENCE_TTL = timedelta(hours=24)

#: 比较投影时忽略的易变字段（时间戳，不承载事实）。
_VOLATILE_PROJECTION_KEYS = frozenset(
    {"completed_at", "searched_at", "retrieved_at", "updated_at", "created_at"}
)

#: 综合正文最多保留的短结论条数（按目标组织，不拼长回答）。
_MAX_SUMMARY_CLAIMS = 4

#: 各模块终态节点允许的可交付可信状态；paper.verify 的成功态即
#: ``EVIDENCE_BOUND``（其风险已由 verify 载荷逐项核对），其余模块必须
#: 达到 ``QUALIFIED`` 才能作为复合层合格交付。
_DELIVERABLE_TERMINAL_TRUST: dict[str, frozenset[ArtifactTrust]] = {
    "paper": frozenset({ArtifactTrust.EVIDENCE_BOUND, ArtifactTrust.QUALIFIED}),
    "resources": frozenset({ArtifactTrust.QUALIFIED}),
    "career": frozenset({ArtifactTrust.QUALIFIED}),
    "github": frozenset({ArtifactTrust.QUALIFIED}),
}


def _recipes() -> dict[str, RecipeDefinition]:
    from bridges.career_plan.kernel import build_career_recipe
    from bridges.github.kernel import build_github_recipe
    from bridges.paper.kernel import build_paper_recipe
    from bridges.resources.kernel import build_resources_recipe

    return {
        "paper": build_paper_recipe(),
        "resources": build_resources_recipe(),
        "career": build_career_recipe(),
        "github": build_github_recipe(),
    }


class DeliveryEvidence:
    """真实产物链 → 步骤可信裁决；只把短结论和原证据引用交给综合。"""

    def __init__(self, conversations: ConversationRepository) -> None:
        self._conversations = conversations
        self._repo = NodeKernelRepository(conversations.database)
        self._recipes = _recipes()

    # ------------------------------------------------------------------
    # 本步核验
    # ------------------------------------------------------------------

    def qualify(
        self, step: CompositeStep, context: StepRunContext, delivery: ModuleDelivery
    ) -> StepResult:
        result = step_result_from_delivery(step, delivery)
        if result.state is not StepState.COMPLETED:
            return result
        try:
            chain = self._chain(step, context, delivery.artifact_refs)
            projection = self._validated_projection(step.module_id, delivery, chain)
            claims, evidence, limitations = _claims(step.module_id, projection, chain)
        except (ValueError, KeyError, ValidationError) as error:
            # 运行完成不等于可信：保留调度完成与交付，但不给任何结论背书。
            return result.model_copy(
                update={
                    "trust_state": "draft",
                    "claims": [],
                    "evidence": {},
                    "evidence_refs": [],
                    "unconfirmed": [f"交付证据未通过核验：{error}"],
                    "blocked_reason": f"交付证据未通过核验：{error}",
                    "failure": StepFailure(
                        code="delivery_evidence_unverified",
                        message=f"交付证据未通过核验：{error}",
                        retryable=False,
                    ),
                }
            )
        return result.model_copy(
            update=self._qualified_updates(step.module_id, chain, claims, evidence, limitations)
        )

    def _qualified_updates(
        self,
        module: str,
        chain: Mapping[str, NodeArtifact],
        claims: Sequence[Claim],
        evidence: Mapping[str, str],
        limitations: Sequence[str],
    ) -> dict[str, Any]:
        read_scope = "；".join(
            dict.fromkeys(a.read_scope for a in chain.values() if a.read_scope)
        )
        return {
            "trust_state": "qualified",
            "claims": list(claims),
            "evidence": dict(evidence),
            "evidence_refs": list(evidence),
            "summary": (
                "\n".join(claim.text for claim in claims[:_MAX_SUMMARY_CLAIMS])
                or "本轮未取得可核验的条目。"
            ),
            "unconfirmed": list(dict.fromkeys(item for item in limitations if item)),
            "read_scope": read_scope or "交付级",
            "scope_key": _scope_key(chain),
        }

    # ------------------------------------------------------------------
    # 跨轮恢复
    # ------------------------------------------------------------------

    def restore(
        self, step: CompositeStep, context: StepRunContext, record: Mapping[str, Any]
    ) -> StepResult | None:
        """从综合产物里的脱敏步骤引用重建可复用结果；不合法返回 ``None``。"""
        if record.get("module_id") != step.module_id:
            return None
        if record.get("trust_state") != "qualified":
            return None
        refs = record.get("artifact_refs")
        if not isinstance(refs, dict) or not refs:
            return None
        try:
            chain = self._chain(step, context, {str(k): str(v) for k, v in refs.items()})
            projection = self._projection_from_chain(step.module_id, chain)
            claims, evidence, limitations = _claims(step.module_id, projection, chain)
            delivery = self._delivery_from_chain(step, chain, projection, refs)
        except (ValueError, KeyError, ValidationError):
            return None
        return StepResult(
            step_id=step.step_id,
            module_id=step.module_id,
            state=StepState.COMPLETED,
            trust_state="qualified",
            reused=False,
            delivery=delivery,
            artifact_refs={str(k): str(v) for k, v in refs.items()},
            claims=claims,
            evidence=evidence,
            evidence_refs=list(evidence),
            read_scope=(
                "；".join(dict.fromkeys(a.read_scope for a in chain.values() if a.read_scope))
                or "交付级"
            ),
            summary=(
                "\n".join(claim.text for claim in claims[:_MAX_SUMMARY_CLAIMS])
                or "本轮未取得可核验的条目。"
            ),
            unconfirmed=list(dict.fromkeys(item for item in limitations if item)),
            input_fingerprint=(
                str(record["input_fingerprint"])
                if record.get("input_fingerprint")
                else None
            ),
            resolved_param_names=[
                str(item) for item in record.get("resolved_param_names") or []
            ],
            scope_key=_scope_key(chain),
        )

    # ------------------------------------------------------------------
    # 依赖链
    # ------------------------------------------------------------------

    def _chain(
        self,
        step: CompositeStep,
        context: StepRunContext,
        artifact_refs: Mapping[str, str],
    ) -> dict[str, NodeArtifact]:
        recipe = self._recipes[step.module_id]
        specs = {spec.name: spec for spec in recipe.nodes}
        terminal = (
            "github.present" if step.module_id == "github" else f"{step.module_id}.verify"
        )
        start = artifact_refs.get(terminal)
        if not start:
            raise ValueError(f"缺少终态产物引用 {terminal}。")
        chain: dict[str, NodeArtifact] = {}

        def walk(ref: str) -> None:
            artifact = self._repo.get_artifact(context.account_id, ref)
            if artifact is None or artifact.node not in specs:
                raise ValueError("产物不存在或节点未登记。")
            spec = specs[artifact.node]
            if (
                artifact.conversation_id != context.conversation_id
                or not _task_compatible(artifact, context)
                or artifact.recipe_id != recipe.recipe_id
                or artifact.recipe_version != recipe.recipe_version
                or artifact.schema_version != ARTIFACT_SCHEMA_VERSION
                or artifact.capability_version != spec.capability_version
                or artifact.artifact_type != spec.artifact_type
                or not artifact.reusable
                or artifact.error is not None
                or not artifact.verify_hash()
            ):
                raise ValueError("权限、版本、可信状态或内容哈希不兼容。")
            # 公开证据也有时效；个人背景不跨运行复用，重新读取撤回/更正后的权威来源。
            if artifact.run_id != context.run_id and (
                step.module_id == "career"
                or datetime.now(UTC) - artifact.created_at > PUBLIC_EVIDENCE_TTL
            ):
                raise ValueError("证据需要重新读取。")
            if artifact.node in chain:
                if chain[artifact.node].artifact_id != ref:
                    raise ValueError("同一节点引用了不同证据。")
                return
            chain[artifact.node] = artifact
            if {d.node for d in artifact.input_deps} != set(spec.depends_on):
                raise ValueError("必经证据依赖缺失。")
            for dep in artifact.input_deps:
                upstream = self._repo.get_artifact(
                    context.account_id, dep.artifact_id or ""
                )
                if (
                    upstream is None
                    or upstream.node != dep.node
                    or upstream.content_hash != dep.content_hash
                ):
                    raise ValueError("依赖哈希或节点错连。")
                walk(upstream.artifact_id or "")

        walk(start)
        for node, ref in artifact_refs.items():
            if node not in chain or chain[node].artifact_id != ref:
                raise ValueError("交付引用不属于核验链。")
        if chain[terminal].trust_state not in _DELIVERABLE_TERMINAL_TRUST[step.module_id]:
            raise ValueError("终态产物未达到可交付可信状态。")
        if step.module_id == "github" and not {
            "github.verify",
            "github.present",
        }.issubset(chain):
            raise ValueError("GitHub 交付缺少核验节点。")
        return chain

    # ------------------------------------------------------------------
    # 投影核对
    # ------------------------------------------------------------------

    def _validated_projection(
        self,
        module: str,
        delivery: ModuleDelivery,
        chain: Mapping[str, NodeArtifact],
    ) -> dict[str, Any]:
        projection = dict(delivery.projection)
        if module in {"resources", "career"}:
            original = chain[f"{module}.verify"].payload.get("projection")
            if not isinstance(original, dict):
                raise ValueError("交付产物缺少投影载荷。")
            if _stable(original) != _stable(projection):
                raise ValueError("交付投影与原产物不一致。")
        elif module == "paper":
            evaluate = chain["paper.evaluate"].payload
            papers = [
                _without(item, {"summary_zh", "summary_evidence"})
                for item in projection.get("papers") or []
            ]
            expected = [
                _without(item, {"summary_zh", "summary_evidence"})
                for item in evaluate.get("recommendations") or []
            ]
            if papers != expected:
                raise ValueError("论文推荐与评估产物错连。")
            if list(projection.get("selected") or []) != list(
                evaluate.get("selection") or []
            ):
                raise ValueError("论文身份与评估产物错连。")
            verify = chain["paper.verify"].payload
            if verify.get("unsupported_claims") or verify.get("hard_condition_violations"):
                raise ValueError("论文结论缺少支持或违反硬条件。")
        else:
            from bridges.github.contracts import GithubProjectsProjection
            from bridges.github.service import (
                GithubDelivery,
                verify_github_delivery_projection,
            )

            present = chain["github.present"]
            native = GithubDelivery(
                projection=GithubProjectsProjection.model_validate(projection),
                content=delivery.content,
                message_status=ChatMessageStatus(delivery.message_status),
                lock=None,
                verification_artifact_id=chain["github.verify"].artifact_id,
                present_artifact_id=present.artifact_id,
                lease_owner=None,
                task_ref=(present.task_id, present.task_version),
            )
            if not verify_github_delivery_projection(
                repository=self._repo,
                recipe=self._recipes["github"],
                delivery=native,
                account_id=present.account_id,
                run_id=present.run_id,
                conversation_id=present.conversation_id,
            ):
                raise ValueError("开源项目投影与原证据不一致。")
        return projection

    def _projection_from_chain(
        self, module: str, chain: Mapping[str, NodeArtifact]
    ) -> dict[str, Any]:
        if module in {"resources", "career"}:
            return dict(chain[f"{module}.verify"].payload["projection"])
        if module == "paper":
            parse = chain["paper.parse"].payload.get("analysis") or {}
            plan = chain["paper.plan"].payload.get("plan") or {}
            search = chain["paper.search"].payload
            enrich = chain["paper.enrich"].payload
            evaluate = chain["paper.evaluate"].payload
            verify = chain["paper.verify"].payload
            papers = list(evaluate.get("recommendations") or [])
            return {
                "status": "success" if papers else "empty",
                "original_phrase": parse.get("original_phrase", ""),
                "normalized_term": parse.get("normalized_term", ""),
                "expansions": list(parse.get("expansions") or []),
                "confidence": parse.get("confidence", 0.0),
                "context_label": parse.get("context_label"),
                "queries": [
                    *(search.get("queries") or []),
                    *(enrich.get("queries") or []),
                ],
                "final_query": plan.get("query", ""),
                "papers": papers,
                "selected": list(evaluate.get("selection") or []),
                "requested_count": plan.get("target_count", 0),
                "artifacts": {node: art.artifact_id for node, art in chain.items()},
                "expression_policy_version": None,
                "evidence_notes": list(verify.get("notes") or []),
                "pending": None,
                "searched_at": None,
                "error_code": None,
                "error_message": None,
                "retryable": False,
            }
        from bridges.github.presenting import InsightOutcome
        from bridges.github.service import build_github_projection

        present = chain["github.present"]
        payload = present.payload
        return build_github_projection(
            result=KernelResult(
                status=KernelStatus.COMPLETED,
                nodes=(),
                artifacts=tuple(chain.values()),
                delivery=present,
            ),
            insights=InsightOutcome(
                insights=payload.get("insights") or {}, note=payload.get("note")
            ),
            clock=lambda: present.created_at,
        ).model_dump(mode="json")

    def _delivery_from_chain(
        self,
        step: CompositeStep,
        chain: Mapping[str, NodeArtifact],
        projection: dict[str, Any],
        refs: Mapping[str, Any],
    ) -> ModuleDelivery:
        fields = {
            "paper": "paper_search",
            "resources": "learning_resources",
            "career": "career_plan",
            "github": "github_projects",
        }
        status = str(projection.get("status") or "")
        return ModuleDelivery(
            module_id=step.module_id,
            status=status,
            projection_field=fields[step.module_id],
            projection=projection,
            content="",
            message_status=ChatMessageStatus.DONE.value,
            artifact_refs={str(k): str(v) for k, v in refs.items()},
        )


def _task_compatible(artifact: NodeArtifact, context: StepRunContext) -> bool:
    if artifact.task_id != context.task_id:
        return False
    return (artifact.task_version or 0) <= (context.task_version or 0)


def _scope_key(chain: Mapping[str, NodeArtifact]) -> str:
    return NodeArtifact.hash_payload(
        {"chain": {node: artifact.content_hash for node, artifact in sorted(chain.items())}}
    )


def _stable(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in _VOLATILE_PROJECTION_KEYS}


def _without(value: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if key not in keys}


# ----------------------------------------------------------------------
# 短结论与原始证据
# ----------------------------------------------------------------------


def _paper_text(original: Mapping[str, Any]) -> str:
    return (
        f"{original.get('title', '')}（已读范围：{original.get('read_scope', '摘要')}）："
        f"{original.get('url', '')}"
    )


def _resources_text(original: Mapping[str, Any]) -> str:
    return (
        f"{original.get('title', '')}（已读范围：{original.get('read_scope', '标题元数据')}）："
        f"{original.get('url', '')}"
    )


def _github_text(original: Mapping[str, Any]) -> str:
    return (
        f"{original.get('title', '')}（文档/静态证据，未验证实际运行）："
        f"{original.get('url', '')}"
    )


def _career_samples_text(original: Mapping[str, Any]) -> str:
    return f"本轮公开岗位样本：{original.get('sample_count', 0)} 个；仅描述这些样本。"


def _career_gap_text(original: Mapping[str, Any]) -> str:
    return f"{original.get('term', '')}：{original.get('note', '')}"


_RENDERERS: dict[str, Callable[[Mapping[str, Any]], str]] = {
    "paper_identity": _paper_text,
    "resources_item": _resources_text,
    "github_repository": _github_text,
    "career_samples": _career_samples_text,
    "career_gap": _career_gap_text,
}


def _claims(
    module: str, projection: dict[str, Any], chain: Mapping[str, NodeArtifact]
) -> tuple[list[Claim], dict[str, str], list[str]]:
    claims: list[Claim] = []
    evidence: dict[str, str] = {}
    limitations = [item for artifact in chain.values() for item in artifact.unconfirmed]

    def add(
        text: str,
        render: str,
        original: dict[str, Any],
        *,
        risk: str | None = None,
        scope: str = "",
    ) -> None:
        ref = f"{module}:{len(evidence) + 1}"
        evidence[ref] = json.dumps(
            {
                "ref": ref,
                "module": module,
                "render": render,
                "original": original,
                "scope": scope,
            },
            ensure_ascii=False,
        )
        claims.append(
            Claim(text=text, evidence_refs=[ref], qualification=scope or None, risk=risk)
        )

    if module == "paper":
        for paper in projection.get("papers", []):
            scope = str(paper.get("read_scope") or "摘要")
            original = {
                "title": paper.get("title", ""),
                "url": paper.get("abs_url", ""),
                "read_scope": scope,
                "abstract": paper.get("source_abstract", ""),
                "read_evidence": paper.get("read_evidence") or [],
            }
            add(_paper_text(original), "paper_identity", original, scope=scope)
            limitations.extend(paper.get("unverified") or [])
        limitations.append("论文按实际阅读范围列出，不把摘要或候选身份当作正文结论。")
    elif module == "resources":
        for item in projection.get("items", []):
            scope = str(item.get("read_scope") or "标题元数据")
            original = {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "read_scope": scope,
                "read": chain["resources.read"].payload,
                "unverified": item.get("unverified") or [],
            }
            add(_resources_text(original), "resources_item", original, scope=scope)
            limitations.extend(item.get("unverified") or [])
        if not projection.get("path_verified"):
            limitations.append("资料目前仅作候选，未核实其构成完整学习路径。")
    elif module == "career":
        from bridges.career_plan.contracts import CareerPlanProjection

        career = CareerPlanProjection.model_validate(projection)
        samples_original = {
            "projection": projection,
            "sample_count": len(career.samples),
        }
        add(
            _career_samples_text(samples_original),
            "career_samples",
            samples_original,
            risk=VerificationTrigger.KEY_FORMULA_OR_NUMBER.value,
        )
        for gap in career.gaps:
            gap_original = {
                "projection": projection,
                "term": gap.term,
                "note": gap.note,
            }
            add(
                _career_gap_text(gap_original),
                "career_gap",
                gap_original,
                risk=VerificationTrigger.PERSONAL_GAP.value,
            )
        limitations.extend(career.evidence_boundary)
        limitations.extend(career.personal_boundary)
        if career.follow_up_question:
            limitations.append(career.follow_up_question)
    else:
        for item in projection.get("recommendations", []):
            original = {
                "title": item.get("full_name", ""),
                "url": item.get("html_url", ""),
                "read": chain["github.read"].payload,
                "runtime_verified": item.get("runtime_verified"),
            }
            add(
                _github_text(original),
                "github_repository",
                original,
                risk=VerificationTrigger.IMPLEMENTATION_EVIDENCE.value,
                scope="文档/静态证据，未验证实际运行",
            )
            limitations.extend(item.get("limitations") or [])
        requirement = projection.get("requirement_source")
        if isinstance(requirement, dict) and not requirement.get("identity_confirmed"):
            limitations.append(
                "需求来源身份尚未确认：只按原词检索，不宣称仓库是它的对应实现。"
            )
    return claims, evidence, limitations


class EvidenceVerifier:
    """独立代码角色，只接收结论、原始证据和规则；私人证据留在本机。"""

    def verify(
        self,
        *,
        conclusion: str,
        evidence: Sequence[str],
        rules: Sequence[str],
        trigger: VerificationTrigger,
    ) -> IndependentVerification:
        passed = bool(evidence) and bool(conclusion)
        refs: list[str] = []
        for raw in evidence:
            try:
                entry = json.loads(raw)
            except (TypeError, ValueError):
                passed = False
                continue
            if not isinstance(entry, dict):
                passed = False
                continue
            ref = str(entry.get("ref") or "")
            if ref:
                refs.append(ref)
            original = entry.get("original")
            render = _RENDERERS.get(str(entry.get("render") or ""))
            if not isinstance(original, dict) or render is None:
                passed = False
                continue
            if entry.get("module") == "career":
                passed &= self._verify_career(
                    conclusion=conclusion,
                    render=str(entry.get("render")),
                    original=original,
                    trigger=trigger,
                )
            else:
                passed &= conclusion == render(original)
                if entry.get("module") == "github":
                    # 实现证据不足只允许文档/静态限定，不能借 README 宣称已经运行。
                    passed &= bool(original.get("read"))
        return IndependentVerification(
            required=True,
            trigger=trigger,
            verdict=(
                VerificationVerdict.PASS if passed else VerificationVerdict.BLOCK
            ),
            source=VerificationSource.DETERMINISTIC_CODE,
            independent_fact_source=False,
            evidence_refs=refs,
            rules=list(rules)
            or ["从原证据重新计算/逐条核对；未知不等于不足；文档不等于运行。"],
            note="独立代码复核，不新增事实来源。",
        )

    @staticmethod
    def _verify_career(
        *,
        conclusion: str,
        render: str,
        original: Mapping[str, Any],
        trigger: VerificationTrigger,
    ) -> bool:
        from bridges.career_plan.analyzing import analyze_samples
        from bridges.career_plan.contracts import CareerPlanProjection
        from bridges.career_plan.reviewing import review_personal_projection

        projection = CareerPlanProjection.model_validate(original.get("projection") or {})
        if render == "career_samples":
            if trigger is not VerificationTrigger.KEY_FORMULA_OR_NUMBER:
                return False
            recalculated = analyze_samples(
                projection.samples,
                experience_unverified_count=(
                    projection.analysis.experience_unverified_count
                    if projection.analysis is not None
                    else 0
                ),
            )
            return conclusion == _career_samples_text(
                {"sample_count": len(projection.samples)}
            ) and (projection.analysis is None or recalculated == projection.analysis)
        if render == "career_gap":
            if trigger is not VerificationTrigger.PERSONAL_GAP:
                return False
            gap = next(
                (item for item in projection.gaps if item.term == original.get("term")),
                None,
            )
            return (
                review_personal_projection(projection) is None
                and gap is not None
                and conclusion
                == _career_gap_text({"term": gap.term, "note": gap.note})
            )
        return False


__all__ = [
    "DeliveryEvidence",
    "EvidenceVerifier",
    "PUBLIC_EVIDENCE_TTL",
]
