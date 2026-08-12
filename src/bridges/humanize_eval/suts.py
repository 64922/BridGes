"""四个被测系统（SUT）与统一执行协议（Issue 01 tracer bullet + Issue 09）。

四个 SUT 身份真实且不混淆：

- ``current-production``：冻结的当前生产策略。策略来源为 git HEAD
  已提交的 humanizer SKILL 与聊天全局写作策略；``policy_ref`` 记录
  提交哈希，身份绑定提交而不是工作区。
- ``candidate``：当前代码候选。策略来源为工作区磁盘文件，记录
  内容哈希；本 Issue 尚未改进候选，输出允许与 current 相同，但
  质量结论必须为 inconclusive。
- ``plain-model``：同一基础模型与采样参数下的"无 humanizer"对照
  （Issue 09 新增）：系统提示只含任务边界，不含任何策略文本，
  用于衡量人味化策略本身的增量。
- ``humanizer-zh-reference``：挂载指定 ``Humanizer-zh`` SKILL 快照
  的参考配置。快照记录绝对来源路径、内容哈希、许可证状态（MIT）、
  基础模型与采样参数；缺快照时该 SUT 不可运行并列入缺项。

四个 SUT 共用同一 ``GenerationPort`` 执行协议：不读取任何案例内
脚本答案，也不查预写输出；脚本化 executor 在本链路中不可被选中。
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from bridges.ai.fixed_models import CHAT_MODEL_ID
from bridges.humanize_eval.cases import HumanizeCase, HumanizeCaseKind
from bridges.humanize_eval.generation import (
    GenerationParameters,
    GenerationPort,
    GenerationResult,
    GenerationStatus,
)

#: Humanizer-zh 快照默认位置：优先环境变量覆盖（跨机器可移植），
#: 缺省为用户机器上的本地参考；CLI 可用 --snapshot 进一步覆盖。
_HUMANIZER_ZH_ENV = "BRIDGES_HUMANIZER_ZH_SNAPSHOT"
HUMANIZER_ZH_DEFAULT_SNAPSHOT = os.environ.get(
    _HUMANIZER_ZH_ENV, r"C:\Users\33755\Desktop\参考资料\Humanizer-zh-main"
)

#: 快照目录下的冻结输出子目录：存在即表示外部参考不可重放，
#: 以冻结证据标记（frozen_external_reference），不调用模型端口。
FROZEN_OUTPUTS_DIR = "frozen_outputs"


class SUTKind(StrEnum):
    """SUT 身份类别（judge packet 中绝不出现这些名字）。"""

    CURRENT_PRODUCTION = "current-production"
    CANDIDATE = "candidate"
    PLAIN_MODEL = "plain-model"
    HUMANIZER_ZH_REFERENCE = "humanizer-zh-reference"


class HumanizerZhSnapshot(BaseModel):
    """Humanizer-zh 参考快照的记录（不复制内容进仓库，只引用与哈希）。"""

    absolute_source: str = Field(description="快照绝对路径。")
    content_sha256: str = Field(description="SKILL.md 内容哈希。")
    license_status: str = Field(description="许可证状态（MIT）。")
    license_file: str = Field(description="许可证文件路径。")
    license_sha256: str = Field(description="许可证文件哈希。")
    base_model: str = Field(description="挂载时使用的基础模型。")
    parameters: GenerationParameters = Field(description="采样参数。")
    available: bool = Field(description="快照文件是否可读（缺快照时不可运行）。")
    missing_items: list[str] = Field(
        default_factory=list, description="缺项清单（available=False 时列出）。"
    )
    frozen_external_reference: bool = Field(
        default=False, description="外部参考不可重放（冻结证据标记）。"
    )
    frozen_reference_note: str = Field(
        default="", description="冻结参考说明。"
    )


class SUTSpec(BaseModel):
    """一个 SUT 的身份与策略记录（进入运行锁）。"""

    sut_id: str = Field(description="SUT 标识（judge packet 外使用）。")
    kind: SUTKind = Field(description="SUT 身份类别。")
    description: str = Field(description="中文说明。")
    policy_origin: str = Field(
        description="策略来源：git-head / working-tree / external-snapshot。"
    )
    policy_sha256: str = Field(description="策略内容哈希。")
    policy_ref: str = Field(description="策略引用（提交哈希 / 文件路径 / 快照路径）。")
    model_id: str = Field(description="模型快照。")
    parameters: GenerationParameters = Field(description="采样参数。")
    available: bool = Field(default=True, description="是否满足运行前置条件。")
    missing_items: list[str] = Field(default_factory=list, description="缺项清单。")
    frozen_external_reference: bool = Field(
        default=False,
        description="外部参考不可重放：输出为冻结证据，不伪称可复现。",
    )
    frozen_reference_note: str = Field(
        default="", description="冻结参考说明（来源/时间/模型/参数）。"
    )


class SUTOutput(BaseModel):
    """一次 SUT×case 执行的输出与观察记录。"""

    sut_id: str
    case_id: str
    text: str
    generation: GenerationResult


def _sha256(content: str | bytes) -> str:
    data = content if isinstance(content, bytes) else content.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _run_git(workspace: Path, *args: str) -> str:
    """在 workspace 内执行 git，返回 stdout；失败时返回空串（调用方处理）。

    Windows 下 git 输出为 UTF-8，管道解码显式指定编码避免 GBK 误读。
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(workspace), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
        return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _head_commit(workspace: Path) -> str:
    commit = _run_git(workspace, "rev-parse", "HEAD")
    return commit or "unknown"


def _head_file_content(workspace: Path, rel_path: str) -> str:
    """读取 git HEAD 中已提交的文件内容（冻结 current production 用）。"""
    return _run_git(workspace, "show", f"HEAD:{rel_path}")


#: 现行生产策略文件（相对仓库根）。
_HUMANIZER_SKILL_REL = "src/bridges/skills/humanizer/skill/SKILL.md"
_CHAT_POLICY_REL = "src/bridges/chat/global_writing_policy.py"
_REFERENCE_SKILL_FILE = "SKILL.md"


def _article_policy_prompt(policy_text: str) -> str:
    """把策略文本组装成文章场景系统提示（策略文本来自 SKILL 快照）。"""
    return (
        "你是 BridGes 的文章表达助手。下面的策略定义了改写风格与边界：\n"
        "---\n"
        f"{policy_text}\n"
        "---\n"
        "改写时不得新增原文不存在的个人经历、朋友对话、具体时间地点、"
        "数据或功能；数字、单位、日期、专名、精确引语、URL 与否定边界必须保留。"
    )


def _chat_policy_prompt(policy_text: str) -> str:
    """把策略文本组装成聊天场景系统提示。"""
    return (
        "你是 BridGes 的聊天助手。下面的策略定义了回答风格与边界：\n"
        "---\n"
        f"{policy_text}\n"
        "---\n"
        "简单问题直接简短回答，不扩成教学长文；不编造数据或个人经历；"
        "没有真实下一步时不加客服式尾句。"
    )


def _build_user_prompt(case: HumanizeCase) -> str:
    """组装用户侧提示：请求 + 原文 + 任务边界（不含策略身份）。"""
    parts = [f"用户请求：{case.user_request}"]
    if case.source_text:
        parts.append(f"原文：\n{case.source_text}")
    if case.context:
        parts.append(f"上下文：{case.context}")
    parts.append(
        f"任务边界：表面类型 {case.surface_type}；模式 {case.mode}；"
        f"受众 {case.audience}；渠道 {case.channel}；目标长度 {case.target_length}。"
    )
    if case.allowed_materials:
        parts.append(f"允许使用的材料：{('；'.join(case.allowed_materials))}")
    parts.append(f"禁止新增：{('；'.join(case.forbidden_claims))}。")
    parts.append(f"必须保留：{('；'.join(case.protected_items))}。")
    return "\n\n".join(parts)


def resolve_humanizer_zh_snapshot(
    snapshot_dir: str | None = None,
    *,
    model_id: str = CHAT_MODEL_ID,
    params: GenerationParameters | None = None,
) -> HumanizerZhSnapshot:
    """解析 Humanizer-zh 快照并记录来源/哈希/许可证/模型/参数。

    快照目录下存在 ``frozen_outputs/`` 时，该参考被标记为外部不可重放：
    输出为冻结证据（frozen_external_reference），不调用模型端口，
    也不伪称完全可复现。
    """
    base = Path(snapshot_dir or HUMANIZER_ZH_DEFAULT_SNAPSHOT)
    skill_file = base / _REFERENCE_SKILL_FILE
    license_file = base / "LICENSE"
    frozen_dir = base / FROZEN_OUTPUTS_DIR
    missing: list[str] = []
    available = True
    skill_text = ""
    license_text = ""
    if not base.is_dir():
        missing.append(f"Humanizer-zh 快照目录不存在：{base}")
        available = False
    else:
        if not skill_file.is_file():
            missing.append(f"快照缺少 SKILL.md：{skill_file}")
            available = False
        else:
            skill_text = skill_file.read_text(encoding="utf-8")
        if license_file.is_file():
            license_text = license_file.read_text(encoding="utf-8")
        else:
            missing.append(f"快照缺少 LICENSE 文件：{license_file}")
            available = False
    return HumanizerZhSnapshot(
        absolute_source=str(base),
        content_sha256=_sha256(skill_text) if skill_text else "",
        license_status="MIT" if "MIT" in license_text else "unknown",
        license_file=str(license_file),
        license_sha256=_sha256(license_text) if license_text else "",
        base_model=model_id,
        parameters=params or GenerationParameters(),
        available=available,
        missing_items=missing,
        frozen_external_reference=frozen_dir.is_dir(),
        frozen_reference_note=(
            f"快照含 {FROZEN_OUTPUTS_DIR}/：外部参考输出为冻结证据，"
            "不调用模型端口，不可完全重放。"
            if frozen_dir.is_dir()
            else ""
        ),
    )


def build_suts(
    workspace: Path,
    *,
    snapshot_dir: str | None = None,
    params: GenerationParameters | None = None,
) -> list[SUTSpec]:
    """构建三个 SUT 规格；缺项记录在 spec.missing_items 中，不抛错。

    current 与 candidate 使用同一现行策略文件，但身份记录不同：
    current 绑定 git HEAD 提交（冻结生产），candidate 绑定工作区
    内容哈希（当前代码）。参考快照缺文件时 reference SUT 不可运行。
    """
    params = params or GenerationParameters()
    commit = _head_commit(workspace)
    head_skill = _head_file_content(workspace, _HUMANIZER_SKILL_REL)
    head_chat_policy = _head_file_content(workspace, _CHAT_POLICY_REL)

    # git HEAD 不可读时 current 身份无法冻结：失败关闭，列入缺项。
    current_missing: list[str] = []
    if commit == "unknown":
        current_missing.append("无法读取 git HEAD 提交，冻结 current 身份不可用")
    if not head_skill:
        current_missing.append(f"git HEAD 缺少 {_HUMANIZER_SKILL_REL}")
    if not head_chat_policy:
        current_missing.append(f"git HEAD 缺少 {_CHAT_POLICY_REL}")

    current_policy = f"{head_skill}\n\n聊天策略（HEAD）：\n{head_chat_policy}"
    current_policy_sha = _sha256(current_policy)

    # 工作区策略文件不可读时 candidate 同样失败关闭（不抛裸异常）。
    candidate_missing: list[str] = []
    try:
        work_skill = (workspace / _HUMANIZER_SKILL_REL).read_text(encoding="utf-8")
        work_chat_policy = (workspace / _CHAT_POLICY_REL).read_text(encoding="utf-8")
    except OSError:
        work_skill = ""
        work_chat_policy = ""
        candidate_missing.append(
            f"工作区缺少策略文件（{_HUMANIZER_SKILL_REL}、{_CHAT_POLICY_REL}）"
        )
    candidate_policy = f"{work_skill}\n\n聊天策略（工作区）：\n{work_chat_policy}"
    candidate_policy_sha = _sha256(candidate_policy)

    snapshot = resolve_humanizer_zh_snapshot(snapshot_dir, model_id=CHAT_MODEL_ID)

    return [
        SUTSpec(
            sut_id="current-production",
            kind=SUTKind.CURRENT_PRODUCTION,
            description="冻结的当前生产策略（绑定 git HEAD 提交）。",
            policy_origin="git-head",
            policy_sha256=current_policy_sha,
            policy_ref=commit,
            model_id=CHAT_MODEL_ID,
            parameters=params,
            available=not current_missing,
            missing_items=current_missing,
        ),
        SUTSpec(
            sut_id="candidate",
            kind=SUTKind.CANDIDATE,
            description="当前代码候选（本 Issue 未改进，允许与 current 相同）。",
            policy_origin="working-tree",
            policy_sha256=candidate_policy_sha,
            policy_ref=f"工作区文件（{_HUMANIZER_SKILL_REL}、{_CHAT_POLICY_REL}）",
            model_id=CHAT_MODEL_ID,
            parameters=params,
            available=not candidate_missing,
            missing_items=candidate_missing,
        ),
        SUTSpec(
            sut_id="plain-model",
            kind=SUTKind.PLAIN_MODEL,
            description="无 humanizer 策略的普通模型对照（仅任务边界）。",
            policy_origin="none",
            policy_sha256=_sha256(""),
            policy_ref="无策略（空系统提示基线）",
            model_id=CHAT_MODEL_ID,
            parameters=params,
            available=True,
            missing_items=[],
        ),
        SUTSpec(
            sut_id="humanizer-zh-reference",
            kind=SUTKind.HUMANIZER_ZH_REFERENCE,
            description="挂载 Humanizer-zh SKILL 快照的参考配置（MIT）。",
            policy_origin="external-snapshot",
            policy_sha256=snapshot.content_sha256,
            policy_ref=snapshot.absolute_source,
            model_id=CHAT_MODEL_ID,
            parameters=params,
            available=snapshot.available,
            missing_items=list(snapshot.missing_items),
            frozen_external_reference=snapshot.frozen_external_reference,
            frozen_reference_note=snapshot.frozen_reference_note,
        ),
    ]


def system_prompt_for(
    spec: SUTSpec, case: HumanizeCase, workspace: Path
) -> str:
    """按 SUT 身份加载策略文本并组装系统提示。

    current/candidate 使用本仓库现行 humanizer SKILL 与聊天策略
    （净室原创）；plain-model 不带任何策略（仅任务边界，见
    ``_build_user_prompt``）；reference 使用外部 Humanizer-zh 快照的
    SKILL.md（MIT，仅运行时读取，不复制进仓库）。
    """
    if spec.kind is SUTKind.PLAIN_MODEL:
        return "你是 BridGes 的助手。按照用户的请求完成任务。"
    if spec.kind is SUTKind.HUMANIZER_ZH_REFERENCE:
        policy_path = Path(spec.policy_ref) / _REFERENCE_SKILL_FILE
        try:
            policy_text = policy_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SUTUnavailableError(
                f"Humanizer-zh 快照不可读：{spec.policy_ref}（{exc}）"
            ) from exc
    else:
        try:
            policy_text = (
                (workspace / _HUMANIZER_SKILL_REL).read_text(encoding="utf-8")
                + "\n\n聊天策略：\n"
                + (workspace / _CHAT_POLICY_REL).read_text(encoding="utf-8")
            )
        except OSError as exc:
            raise SUTUnavailableError(f"策略文件不可读：{exc}") from exc
    if case.kind is HumanizeCaseKind.CHAT:
        return _chat_policy_prompt(policy_text)
    return _article_policy_prompt(policy_text)


class SUTUnavailableError(Exception):
    """SUT 前置条件不满足（缺快照/缺文件），运行结果必须为 inconclusive。"""


def execute_sut(
    spec: SUTSpec,
    case: HumanizeCase,
    port: GenerationPort,
    workspace: Path,
    *,
    system_prompt: str | None = None,
) -> SUTOutput:
    """通过统一执行协议运行一个 SUT×case：只调用端口，不查脚本答案。

    外部冻结参考（frozen_external_reference）不调用端口：从快照的
    ``frozen_outputs/<case_id>.txt`` 读取冻结证据；缺文件时失败关闭，
    不伪称可复现。
    """
    if not spec.available:
        raise SUTUnavailableError(
            f"SUT {spec.sut_id} 前置条件不满足：{'；'.join(spec.missing_items)}"
        )
    if spec.frozen_external_reference:
        frozen_file = Path(spec.policy_ref) / FROZEN_OUTPUTS_DIR / f"{case.case_id}.txt"
        try:
            text = frozen_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise SUTUnavailableError(
                f"外部冻结参考缺少 {case.case_id} 的冻结输出：{frozen_file}（{exc}）"
            ) from exc
        return SUTOutput(
            sut_id=spec.sut_id,
            case_id=case.case_id,
            text=text,
            generation=GenerationResult(
                text=text,
                model_id=spec.model_id,
                parameters=spec.parameters.model_dump(),
                status=GenerationStatus.SUCCESS,
            ),
        )
    prompt = system_prompt or system_prompt_for(spec, case, workspace)
    result = port.generate(
        system_prompt=prompt,
        user_prompt=_build_user_prompt(case),
        params=spec.parameters,
    )
    return SUTOutput(
        sut_id=spec.sut_id,
        case_id=case.case_id,
        text=result.text,
        generation=result,
    )
