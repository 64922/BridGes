"""humanize_eval 测试共享夹具（Issue 01 tracer bullet + Issue 09 分层语料）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from bridges.humanize_eval.cases import HUMANIZE_CASES
from bridges.humanize_eval.generation import (
    GenerationParameters,
    GenerationResult,
    GenerationStatus,
)

#: 文章案例的完整保留输出（含全部保护项与引语，保真检查应通过）。
ARTICLE_FAITHFUL_OUTPUT = (
    "时间块管理法（Time Blocking）把日程按 90 分钟工作块切分，中间留 5 分钟缓冲，"
    "配合 25 分钟番茄钟单元。它最早见于 1992 年出版的《Getting Things Done》"
    "（简称 GTD）相关讨论，2024 年综述见 https://research.example.org/time-blocking-2024。"
    "方法不鼓励把任务切得过碎，也不鼓励跨块切换。管理学者说过："
    "“计划赶不上变化”，所以每个工作块预留 10% 弹性。"
    "每周超过 40 小时的人适用，但并非适合所有人：约 30% 的人更依赖任务清单"
    "而非时间块。每周日晚规划下一周的块状日程。"
)

CHAT_FAITHFUL_OUTPUT = "番茄工作法：专注 25 分钟，休息 5 分钟，交替进行。"

#: 用户请求前缀 -> 案例（fake port 按提示定位案例，输出保真通过的确定性文本）。
_PROMPT_TO_CASE = {
    f"用户请求：{case.user_request}": case for case in HUMANIZE_CASES
}


class FakeGenerationPort:
    """确定性假模型适配器：按案例返回"原文+保护项"的保真输出。"""

    def __init__(
        self,
        *,
        article_output: str = ARTICLE_FAITHFUL_OUTPUT,
        chat_output: str = CHAT_FAITHFUL_OUTPUT,
    ) -> None:
        self.article_output = article_output
        self.chat_output = chat_output
        self.calls: list[dict[str, object]] = []

    def _output_for(self, user_prompt: str) -> str:
        # user_prompt 以"用户请求：…"开头，映射 key 是同一前缀。
        for prefix, case in _PROMPT_TO_CASE.items():
            if user_prompt.startswith(prefix):
                break
        else:
            # 兼容旧测试的按关键词回退（无案例匹配时返回通用输出）。
            if "时间块" in user_prompt:
                return self.article_output
            if "番茄工作法" in user_prompt:
                return self.chat_output
            raise KeyError(f"fake port 无法识别提示：{user_prompt[:40]}…")
        # 输出 = 原文（若有）+ 全部保护项：任何保留检查都可执行且通过。
        return (case.source_text or "") + "".join(case.protected_items)

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        params: GenerationParameters,
    ) -> GenerationResult:
        self.calls.append(
            {"system_prompt": system_prompt, "user_prompt": user_prompt}
        )
        return GenerationResult(
            text=self._output_for(user_prompt),
            model_id="fake-model",
            parameters=params.model_dump(),
            status=GenerationStatus.SUCCESS,
        )


def make_fake_judges():
    """三个不同家族标识的假裁判（默认 TIE，双向一致性通过）。"""
    from bridges.humanize_eval.judges import FakeSystemJudge

    return [
        FakeSystemJudge(judge_id="family-a-judge-1"),
        FakeSystemJudge(judge_id="family-b-judge-1"),
        FakeSystemJudge(judge_id="family-c-judge-1"),
    ]


@pytest.fixture
def workspace() -> Path:
    return Path.cwd()


@pytest.fixture
def fake_port() -> FakeGenerationPort:
    return FakeGenerationPort()


@pytest.fixture
def tmp_outdir(tmp_path: Path) -> Path:
    return tmp_path / "humanize-eval-out"
