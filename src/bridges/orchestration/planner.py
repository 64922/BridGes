"""复合计划的构造与校验（改进工单 37）。

计划来自一次已确定的主理解：只把**已登记组合**里的模块编译成带依赖、
参数来源、必要性与条件键的有限计划；未登记组合返回 ``None``（理解层
保持澄清，校验层拒绝）。所有拒绝都以结构化 ``PlanViolationCode`` 返回，
执行器不会以「尽力而为」的方式尝试非法计划。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from bridges.contracts.understanding import HardCondition, HardConditionKind
from bridges.orchestration.contracts import (
    CompositePlan,
    CompositeStep,
    DataClassification,
    ParameterBinding,
    ParameterSource,
    PlanValidation,
    PlanViolation,
    PlanViolationCode,
)
from bridges.orchestration.registry import DEFAULT_PURPOSES, ModuleRegistry

#: 运行创建时允许的最大并行外部调用（与 09 账本 ``external_parallel_max`` 初值一致）。
DEFAULT_EXTERNAL_PARALLEL_MAX = 2


class CompositePlanner:
    """确定性的计划构造与校验器。"""

    def __init__(self, registry: ModuleRegistry | None = None) -> None:
        self._registry = registry or ModuleRegistry.production()

    @property
    def registry(self) -> ModuleRegistry:
        return self._registry

    # ------------------------------------------------------------------
    # 构造
    # ------------------------------------------------------------------

    def combination_for(self, module_ids: Sequence[str]) -> dict[str, tuple[str, ...]] | None:
        """识别已登记的模块组合；未登记返回 ``None``。"""
        unique = list(dict.fromkeys(module_ids))
        if len(unique) < 2:
            return None
        if any(not self._registry.known(module_id) for module_id in unique):
            return None
        rule = self._registry.combination(frozenset(unique))
        if rule is None:
            return None
        return rule

    def plan(
        self,
        *,
        goal: str,
        user_message_id: str,
        module_ids: Sequence[str],
        hard_conditions: Sequence[HardCondition] = (),
        task_id: str | None = None,
        task_version: int | None = None,
        mode: str = "companion",
        now: datetime | None = None,
        plan_id: str | None = None,
    ) -> CompositePlan | None:
        """按模块组合构造计划；未登记组合或单模块返回 ``None``。"""
        rule = self.combination_for(module_ids)
        if rule is None:
            return None
        unique = [module_id for module_id in dict.fromkeys(module_ids) if module_id in rule]
        conditions = list(hard_conditions)
        moment = now or datetime.now(UTC)
        steps: list[CompositeStep] = []
        for module_id in unique:
            descriptor = self._registry.get(module_id)
            steps.append(
                CompositeStep(
                    step_id=module_id,
                    module_id=module_id,
                    recipe_id=descriptor.recipe_id,
                    recipe_version=descriptor.recipe_version,
                    capability=descriptor.entry_capability,
                    capability_version=descriptor.capabilities[descriptor.entry_capability],
                    depends_on=list(rule.get(module_id, ())),
                    required=True,
                    purpose=DEFAULT_PURPOSES.get(module_id, descriptor.purpose),
                    public_network=descriptor.public_network,
                    parameter_bindings=self._bindings_for(
                        module_id,
                        unique=unique,
                        conditions=conditions,
                        user_message_id=user_message_id,
                    ),
                    identity_required=(
                        descriptor.identity_consumer and "paper" in unique
                    ),
                    identity_source_step=(
                        "paper"
                        if descriptor.identity_consumer and "paper" in unique
                        else None
                    ),
                    condition_keys=list(descriptor.condition_keys),
                )
            )
        return CompositePlan(
            plan_id=plan_id or f"plan_{user_message_id}",
            goal=goal,
            user_message_id=user_message_id,
            task_id=task_id,
            task_version=task_version,
            mode=mode,
            steps=steps,
            hard_conditions=[item.model_dump(mode="json") for item in conditions],
            created_at=moment,
        )

    def _bindings_for(
        self,
        module_id: str,
        *,
        unique: Sequence[str],
        conditions: Sequence[HardCondition],
        user_message_id: str,
    ) -> list[ParameterBinding]:
        """每个步骤参数都绑定权威来源；私人材料标为本地参数。"""
        bindings: list[ParameterBinding] = [
            ParameterBinding(
                name="topic",
                source=(
                    ParameterSource.UPSTREAM_ARTIFACT
                    if module_id == "resources" and "career" in unique
                    else ParameterSource.USER_MESSAGE
                ),
                source_ref=(
                    "career"
                    if module_id == "resources" and "career" in unique
                    else user_message_id
                ),
                classification=(
                    DataClassification.DERIVED_PUBLIC
                    if module_id == "resources" and "career" in unique
                    else DataClassification.PUBLIC
                ),
                leaves_device=True,
                note="检索与展示用主题；私人上下文不进入外部查询。",
            )
        ]
        if module_id == "github":
            bindings.append(
                ParameterBinding(
                    name="requirements",
                    source=ParameterSource.UPSTREAM_ARTIFACT,
                    source_ref="paper" if "paper" in unique else "career",
                    classification=DataClassification.DERIVED_PUBLIC,
                    leaves_device=True,
                    note="只使用已确认论文标识或岗位要求原词，不含私人材料。",
                )
            )
        if module_id == "career":
            bindings.append(
                ParameterBinding(
                    name="background",
                    source=ParameterSource.TASK_CONDITION,
                    source_ref="profile",
                    classification=DataClassification.PRIVATE,
                    leaves_device=False,
                    note="最小画像/背景切片只在本地参与差距对照，不发送公网。",
                )
            )
            bindings.append(
                ParameterBinding(
                    name="job_terms",
                    source=ParameterSource.USER_MESSAGE,
                    source_ref=user_message_id,
                    classification=DataClassification.PUBLIC,
                    leaves_device=True,
                    note="岗位检索词来自用户原话。",
                )
            )
        city = next(
            (item for item in conditions if item.kind is HardConditionKind.CITY), None
        )
        if city is not None and module_id == "career":
            bindings.append(
                ParameterBinding(
                    name="city",
                    source=ParameterSource.TASK_CONDITION,
                    source_ref="hard_condition:city",
                    classification=DataClassification.PUBLIC,
                    leaves_device=True,
                    note="城市条件逐字进入现有岗位样本过滤，不被放宽。",
                )
            )
        return bindings

    # ------------------------------------------------------------------
    # 校验
    # ------------------------------------------------------------------

    def validate(
        self,
        plan: CompositePlan,
        *,
        hard_conditions: Sequence[HardCondition] | None = None,
        mode: str | None = None,
        external_parallel_max: int | None = None,
        adjustment_rounds_max: int | None = None,
    ) -> PlanValidation:
        """校验计划：能力/配方、依赖、循环、模式、硬条件、参数与预算。"""
        violations: list[PlanViolation] = []
        names: dict[str, int] = {}
        for index, step in enumerate(plan.steps):
            if step.step_id in names:
                violations.append(
                    PlanViolation(
                        code=PlanViolationCode.DUPLICATE_STEP,
                        message=f"步骤 {step.step_id} 重复登记。",
                        step_id=step.step_id,
                    )
                )
                continue
            names[step.step_id] = index
            self._validate_step_registration(step, violations)
        self._validate_dependencies(plan, names, violations)
        self._validate_composite_readiness(plan, violations)
        self._validate_mode(plan, mode, violations)
        self._validate_hard_conditions(plan, hard_conditions, violations)
        self._validate_parameters(plan, violations)
        self._validate_identity(plan, violations)
        self._validate_budget(plan, external_parallel_max, adjustment_rounds_max, violations)
        return PlanValidation(ok=not violations, violations=violations)

    def _validate_step_registration(
        self, step: CompositeStep, violations: list[PlanViolation]
    ) -> None:
        if not self._registry.known(step.module_id):
            violations.append(
                PlanViolation(
                    code=PlanViolationCode.UNKNOWN_MODULE,
                    message=f"模块 {step.module_id} 未登记，不能进入计划。",
                    step_id=step.step_id,
                )
            )
            return
        descriptor = self._registry.get(step.module_id)
        if (
            step.recipe_id != descriptor.recipe_id
            or step.recipe_version != descriptor.recipe_version
        ):
            violations.append(
                PlanViolation(
                    code=PlanViolationCode.RECIPE_VERSION_MISMATCH,
                    message=(
                        f"步骤 {step.step_id} 的配方 {step.recipe_id}@{step.recipe_version} "
                        f"与登记版本 {descriptor.recipe_id}@{descriptor.recipe_version} 不一致。"
                    ),
                    step_id=step.step_id,
                )
            )
        registered = descriptor.capabilities.get(step.capability)
        if registered is None:
            violations.append(
                PlanViolation(
                    code=PlanViolationCode.UNKNOWN_CAPABILITY,
                    message=f"步骤 {step.step_id} 引用了未登记能力 {step.capability}。",
                    step_id=step.step_id,
                )
            )
        elif registered != step.capability_version:
            violations.append(
                PlanViolation(
                    code=PlanViolationCode.RECIPE_VERSION_MISMATCH,
                    message=(
                        f"步骤 {step.step_id} 的能力版本 {step.capability_version} "
                        f"与登记版本 {registered} 不一致。"
                    ),
                    step_id=step.step_id,
                )
            )

    def _validate_composite_readiness(
        self, plan: CompositePlan, violations: list[PlanViolation]
    ) -> None:
        for step in plan.steps:
            if self._registry.known(step.module_id) and not self._registry.get(
                step.module_id
            ).composite_ready:
                violations.append(
                    PlanViolation(
                        code=PlanViolationCode.UNKNOWN_MODULE,
                        message=(
                            f"模块 {step.module_id} 尚未接入复合调度，保持单模块派发。"
                        ),
                        step_id=step.step_id,
                    )
                )

    def _validate_dependencies(
        self,
        plan: CompositePlan,
        names: Mapping[str, int],
        violations: list[PlanViolation],
    ) -> None:
        for index, step in enumerate(plan.steps):
            for dependency in step.depends_on:
                if dependency not in names:
                    violations.append(
                        PlanViolation(
                            code=PlanViolationCode.MISSING_PREREQUISITE,
                            message=f"步骤 {step.step_id} 依赖未登记的 {dependency}。",
                            step_id=step.step_id,
                        )
                    )
                elif names[dependency] >= index:
                    violations.append(
                        PlanViolation(
                            code=PlanViolationCode.SKIPPED_PREREQUISITE,
                            message=(
                                f"步骤 {step.step_id} 的前置 {dependency} 不在必经顺序之前"
                                "（可能形成循环）。"
                            ),
                            step_id=step.step_id,
                        )
                    )
        self._detect_cycles(plan, violations)

    def _detect_cycles(
        self, plan: CompositePlan, violations: list[PlanViolation]
    ) -> None:
        index = {step.step_id: step for step in plan.steps}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> bool:
            if step_id in visited:
                return False
            if step_id in visiting:
                return True
            visiting.add(step_id)
            cyclic = any(visit(dependency) for dependency in index[step_id].depends_on)
            visiting.discard(step_id)
            visited.add(step_id)
            return cyclic

        if any(visit(step.step_id) for step in plan.steps):
            violations.append(
                PlanViolation(
                    code=PlanViolationCode.CYCLIC_PLAN,
                    message="计划存在循环依赖，不能执行。",
                )
            )

    def _validate_mode(
        self, plan: CompositePlan, mode: str | None, violations: list[PlanViolation]
    ) -> None:
        effective = mode if mode is not None else plan.mode
        if effective != "companion":
            violations.append(
                PlanViolation(
                    code=PlanViolationCode.MODE_CONFLICT,
                    message="复合模块计划只适用于日常陪伴模式；学习模式保持权威阶段。",
                )
            )

    def _validate_hard_conditions(
        self,
        plan: CompositePlan,
        hard_conditions: Sequence[HardCondition] | None,
        violations: list[PlanViolation],
    ) -> None:
        conditions = (
            list(hard_conditions)
            if hard_conditions is not None
            else [HardCondition.model_validate(item) for item in plan.hard_conditions]
        )
        blocks_network = any(
            item.kind in {HardConditionKind.NO_NETWORK, HardConditionKind.LOCAL_ONLY}
            for item in conditions
        )
        source_restrictions = [
            item for item in conditions if item.kind is HardConditionKind.SOURCE_RESTRICTION
        ]
        for step in plan.steps:
            if blocks_network and step.public_network:
                violations.append(
                    PlanViolation(
                        code=PlanViolationCode.HARD_CONDITION_BYPASS,
                        message=(
                            f"步骤 {step.step_id} 需要公网访问，但用户明确不要联网/只用本地材料。"
                        ),
                        step_id=step.step_id,
                    )
                )
            for restriction in source_restrictions:
                text = restriction.text.lower()
                allowed = (
                    "paper" if any(word in text for word in ("论文", "文献", "研究文章"))
                    else "tieba" if any(word in text for word in ("贴吧", "吧里", "吧内"))
                    else "github" if "github" in text
                    else None
                )
                if allowed is not None and step.module_id != allowed:
                    violations.append(
                        PlanViolation(
                            code=PlanViolationCode.HARD_CONDITION_BYPASS,
                            message=(
                                f"来源限制「{restriction.text}」不允许派发 {step.module_id}。"
                            ),
                            step_id=step.step_id,
                        )
                    )

    def _validate_parameters(
        self, plan: CompositePlan, violations: list[PlanViolation]
    ) -> None:
        for step in plan.steps:
            if not step.parameter_bindings:
                violations.append(
                    PlanViolation(
                        code=PlanViolationCode.STEP_MISSING_BINDING,
                        message=f"步骤 {step.step_id} 没有任何参数来源绑定。",
                        step_id=step.step_id,
                    )
                )
                continue
            for binding in step.parameter_bindings:
                if binding.source in {
                    ParameterSource.UPSTREAM_ARTIFACT,
                    ParameterSource.TASK_CONDITION,
                } and not binding.source_ref:
                    violations.append(
                        PlanViolation(
                            code=PlanViolationCode.PARAMETER_SOURCE_MISSING,
                            message=(
                                f"步骤 {step.step_id} 的参数 {binding.name} 缺少来源引用。"
                            ),
                            step_id=step.step_id,
                        )
                    )
                if (
                    step.public_network
                    and binding.classification is DataClassification.PRIVATE
                    and binding.leaves_device
                ):
                    violations.append(
                        PlanViolation(
                            code=PlanViolationCode.HARD_CONDITION_BYPASS,
                            message=(
                                f"步骤 {step.step_id} 是公网步骤，私人参数 {binding.name} "
                                "不得离开设备（简历/画像原文不发公网）。"
                            ),
                            step_id=step.step_id,
                        )
                    )

    def _validate_identity(
        self, plan: CompositePlan, violations: list[PlanViolation]
    ) -> None:
        for step in plan.steps:
            if not step.identity_required:
                continue
            source = step.identity_source_step
            if source is None or source not in {item.step_id for item in plan.steps}:
                violations.append(
                    PlanViolation(
                        code=PlanViolationCode.IDENTITY_REQUIRED,
                        message=(
                            f"步骤 {step.step_id} 依赖上游确认身份，但计划没有提供身份来源步骤；"
                            "身份未确认时不得宣称对应。"
                        ),
                        step_id=step.step_id,
                    )
                )
                continue
            if source not in step.depends_on:
                violations.append(
                    PlanViolation(
                        code=PlanViolationCode.SKIPPED_PREREQUISITE,
                        message=f"步骤 {step.step_id} 的身份来源 {source} 不在依赖里。",
                        step_id=step.step_id,
                    )
                )

    def _validate_budget(
        self,
        plan: CompositePlan,
        external_parallel_max: int | None,
        adjustment_rounds_max: int | None,
        violations: list[PlanViolation],
    ) -> None:
        limit = external_parallel_max or DEFAULT_EXTERNAL_PARALLEL_MAX
        width = self._max_parallel_width(plan)
        if width > limit:
            violations.append(
                PlanViolation(
                    code=PlanViolationCode.BUDGET_EXCEEDED,
                    message=(
                        f"计划同一波次有 {width} 个公网步骤，超过并行上限 {limit}，"
                        "所有分支必须共享总预算。"
                    ),
                )
            )
        rounds = adjustment_rounds_max if adjustment_rounds_max is not None else 1
        if plan.revision > rounds:
            violations.append(
                PlanViolation(
                    code=PlanViolationCode.ADJUSTMENT_ROUNDS_EXHAUSTED,
                    message=(
                        f"计划已进行第 {plan.revision} 轮调整，超过一轮受控调整上限。"
                    ),
                )
            )

    @staticmethod
    def _max_parallel_width(plan: CompositePlan) -> int:
        """按依赖层计算公网步骤的最大并行宽度（预算守卫的确定性口径）。"""
        remaining = {step.step_id: set(step.depends_on) for step in plan.steps}
        public = {step.step_id: step.public_network for step in plan.steps}
        width = 0
        while remaining:
            ready = [name for name, deps in remaining.items() if not deps]
            if not ready:
                # 循环依赖已由 _detect_cycles 拒绝，这里不再进入死循环。
                break
            width = max(width, sum(1 for name in ready if public[name]))
            for name in ready:
                del remaining[name]
            for deps in remaining.values():
                deps.difference_update(ready)
        return width

    # ------------------------------------------------------------------
    # 受控调整
    # ------------------------------------------------------------------

    def adjusted(
        self,
        plan: CompositePlan,
        *,
        reason: str,
        now: datetime | None = None,
        plan_id: str | None = None,
    ) -> tuple[CompositePlan | None, PlanViolation | None]:
        """形成一轮受控调整计划；第二轮由代码拒绝。"""
        if plan.revision >= 1:
            return None, PlanViolation(
                code=PlanViolationCode.ADJUSTMENT_ROUNDS_EXHAUSTED,
                message="整次运行最多一轮自动调整；第二轮补证/修复被拒绝。",
            )
        updated = plan.model_copy(
            update={
                "revision": plan.revision + 1,
                "plan_id": plan_id or plan.plan_id,
                "created_at": now or datetime.now(UTC),
            }
        )
        return updated, None


__all__ = ["DEFAULT_EXTERNAL_PARALLEL_MAX", "CompositePlanner"]
