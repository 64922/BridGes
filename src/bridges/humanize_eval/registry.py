"""版本化 judge registry（Issue 10）。

正式 panel 预注册：至少三个裁判、优先来自不同模型家族或提供方，并且
不得全部与候选生成模型相同。每个登记项记录 judge_id、模型家族/提供方、
模型快照、版本、系统提示哈希、schema 版本与采样参数（temperature/seed
等进入运行锁）。不满足预注册多样性时结论为 ``inconclusive``。

canary 状态进入登记项：裁判上线前必须通过冻结 canary 硬门（100%），
裁判模型、提示、schema 或参数变化后自动重跑；相对冻结基线漂移超过
预注册阈值时禁止进入正式 panel（见 canary.py）。
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, Field

from bridges.humanize_eval.generation import GenerationParameters

#: registry 版本（进入运行锁；登记结构/规则变化必须升版本）。
REGISTRY_VERSION = "1"

#: 正式 panel 最低裁判数（少于 3 结论固定为 inconclusive）。
MIN_PANEL_JUDGES = 3

#: 预注册漂移阈值：相对冻结基线的偏好/评分漂移超过此值禁止进入正式 panel。
DEFAULT_DRIFT_THRESHOLD = 0.2


class JudgeRegistration(BaseModel):
    """一个系统裁判的登记项（版本化，进入运行锁）。"""

    judge_id: str
    model_family: str = Field(description="模型家族（如 qwen）。")
    provider: str = Field(description="提供方（如 alibaba）。")
    model_id: str = Field(description="模型快照。")
    judge_version: str = Field(description="裁判提示/解析逻辑版本。")
    system_prompt_sha256: str = Field(description="系统提示内容哈希。")
    schema_version: str = Field(description="裁决 schema 版本。")
    parameters: GenerationParameters = Field(
        description="采样参数（temperature/top_p/seed 进入运行锁）。"
    )
    canary_status: str = Field(
        default="pending",
        description="pending / passed / failed / drifted。",
    )
    canary_run_id: str | None = Field(default=None, description="最近一次 canary 运行。")
    enabled: bool = Field(default=False, description="是否允许进入正式 panel。")


class JudgeRegistry(BaseModel):
    """预注册 judge registry：panel 名单 + 登记项 + 多样性/漂移规则。"""

    registry_version: str = REGISTRY_VERSION
    panel_name: str = Field(default="formal-panel", description="正式 panel 名称。")
    judge_ids: list[str] = Field(
        default_factory=list, description="预注册 panel 裁判顺序名单。"
    )
    registrations: dict[str, JudgeRegistration] = Field(
        default_factory=dict, description="judge_id -> 登记项。"
    )
    generation_family: str = Field(
        default="", description="候选生成模型家族（多样性门参照）。"
    )
    canary_sha256: str = Field(
        default="", description="冻结 canary 集内容哈希（变化必须重跑）。"
    )
    drift_threshold: float = Field(
        default=DEFAULT_DRIFT_THRESHOLD, description="预注册漂移阈值（0-1）。"
    )

    def digest(self) -> str:
        """registry 内容哈希（进入运行锁；任何变化改变锁身份）。"""
        payload = {
            "registry_version": self.registry_version,
            "panel_name": self.panel_name,
            "judge_ids": list(self.judge_ids),
            "generation_family": self.generation_family,
            "canary_sha256": self.canary_sha256,
            "drift_threshold": self.drift_threshold,
            "registrations": {
                judge_id: reg.model_dump(mode="json")
                for judge_id, reg in sorted(self.registrations.items())
            },
        }
        import json

        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def register_judge(
    registry: JudgeRegistry,
    *,
    judge_id: str,
    model_family: str,
    provider: str,
    model_id: str,
    judge_version: str,
    system_prompt_sha256: str,
    schema_version: str,
    parameters: GenerationParameters,
) -> JudgeRegistry:
    """登记一个裁判并加入 panel 名单（预注册：只追加，不改旧项）。"""
    if judge_id in registry.registrations:
        raise JudgeRegistryError(f"裁判重复登记：{judge_id}")
    registration = JudgeRegistration(
        judge_id=judge_id,
        model_family=model_family,
        provider=provider,
        model_id=model_id,
        judge_version=judge_version,
        system_prompt_sha256=system_prompt_sha256,
        schema_version=schema_version,
        parameters=parameters,
    )
    return registry.model_copy(
        update={
            "registrations": {
                **registry.registrations,
                judge_id: registration,
            },
            "judge_ids": [*registry.judge_ids, judge_id],
        }
    )


def active_judges(registry: JudgeRegistry) -> list[JudgeRegistration]:
    """正式 panel 中已启用（通过 canary 门）的裁判。"""
    return [
        registry.registrations[judge_id]
        for judge_id in registry.judge_ids
        if registry.registrations[judge_id].enabled
    ]


def panel_gate_issues(registry: JudgeRegistry) -> list[str]:
    """预注册多样性/可用性门：返回问题清单（空 = 门通过）。

    规则：正式 panel 至少三个有效（已通过 canary 启用）裁判；登记 panel
    至少两个不同模型家族或提供方（预注册静态检查，不依赖启用状态）；
    不得全部与候选生成模型家族相同。任一不满足，结论固定为 inconclusive。
    """
    issues: list[str] = []
    registered = [
        registry.registrations[judge_id]
        for judge_id in registry.judge_ids
        if judge_id in registry.registrations
    ]
    active = [reg for reg in registered if reg.enabled]
    if len(active) < MIN_PANEL_JUDGES:
        issues.append(
            f"正式 panel 只有 {len(active)} 个有效裁判（少于 "
            f"{MIN_PANEL_JUDGES}），结论固定为 inconclusive。"
        )
    families = {reg.model_family for reg in registered}
    providers = {reg.provider for reg in registered}
    if len(families) < 2 and len(providers) < 2:
        issues.append(
            "系统裁判多样性不足（全部来自同一模型家族/提供方），"
            "结论固定为 inconclusive。"
        )
    if registry.generation_family and families == {registry.generation_family}:
        issues.append(
            "全部裁判与候选生成模型同家族，不得作为正式 panel 结论来源。"
        )
    return issues


def build_default_registry(
    *,
    generation_model_id: str,
    parameters: GenerationParameters | None = None,
    canary_sha256: str = "",
) -> JudgeRegistry:
    """构造默认 registry：当前唯一文本模型家族的三个 Qwen 裁判。

    三个实例共享同一模型家族，不满足预注册多样性；聚合层检测到
    ``panel_gate_issues`` 非空时结论必须为 inconclusive（本项目当前
    只有 Qwen 家族，正式 panel 在引入第二个家族前始终不能通过门）。
    """
    params = parameters or GenerationParameters()
    from bridges.humanize_eval.judges import QwenSystemJudge

    family = model_family_of(generation_model_id)
    registry = JudgeRegistry(
        generation_family=family,
        canary_sha256=canary_sha256,
    )
    for index in (1, 2, 3):
        judge_id = f"qwen-system-judge-{index}"
        registry = register_judge(
            registry,
            judge_id=judge_id,
            model_family=family,
            provider="alibaba",
            model_id=generation_model_id,
            judge_version="v1",
            system_prompt_sha256=_sha256(QwenSystemJudge.system_prompt_text()),
            schema_version="judge-schema-v2",
            parameters=params,
        )
    return registry


def model_family_of(model_id: str) -> str:
    """从模型快照提取家族（如 qwen3.7-plus -> qwen；glm-4 -> glm）。

    家族 = 模型 ID 的字母品牌前缀（不含版本号），用于多样性门：同一
    品牌的不同版本（qwen3.7/qwen3.8）属于同一家族，不构成多样性。
    """
    import re

    if not model_id:
        return "unknown"
    match = re.match(r"[a-zA-Z]+", model_id)
    return match.group(0).lower() if match else model_id


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class JudgeRegistryError(Exception):
    """judge registry 领域错误。"""
