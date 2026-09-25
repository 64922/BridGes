"""Issue 03（feature 7）：意图路由金标契约测试集。

生产截图三句话全部漏路由后，用金标契约把三类语句钉死在回归里，防止
词表扩展后再静默退化。金标断言发送时**持久化的路由快照**（skill 载荷 /
CapabilityRoute），而非仅断言函数返回值；生涯语句额外走一次流式分支的
现场重判（``turn.py`` 的 ``is_career_intent`` 二次判定）。

- 正例：人味化 / 生涯规划 / 论文搜索三类用户点名语句原句 + 近似变体
  （含前端建议卡文案「帮我排一下研究生三年的学习优先级」）；
- 负例：论文内容问答、文章评价、学习计划之外的事、非发展语境「规划」
  等必须保持普通聊天，不被新词项劫持；
- 防护回归：复合任务 CLARIFY、图片/视频优先级、``route.is_paper_search``
  降级语义不回归。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

# bridges.routing 与 bridges.contracts.chat 存在既有导入环：首个直接
# import bridges.routing 的模块会踩中部分初始化（routing → contracts →
# video → ai → adapters → contracts.chat → routing）。先预载 bridges.ai
# 走完整链路，后续导入全部命中已初始化模块。
import bridges.ai  # noqa: F401 - 预载以打破既有导入环
from bridges.career.intent import is_career_intent
from bridges.routing import MainCapability, NaturalLanguageRouter, RouteStatus
from bridges.skills.humanizer.intent import route_humanizer_message
from tests.chat.test_arxiv_search_chat import _CapturingAdapter, _context

#: 前端建议卡「生涯规划助手」文案原句（chat-template.tsx），金标要求
#: 产品自述能力真实可达，文案与本测试共用同一常量防止漂移。
FRONTEND_CAREER_SUGGESTION = "帮我排一下研究生三年的学习优先级"

# ---------------------------------------------------------------------------
# 金标集
# ---------------------------------------------------------------------------

#: 人味化：原句 + 变体（含 Issue 03 根因句「把这篇文章改得有人味一点」）。
GOLDEN_HUMANIZER: tuple[str, ...] = (
    "帮我人味化这篇文章",
    "给我人味化润色这篇文章：Transformer 是一种深度学习架构。",
    "把这篇文章改得有人味一点",
    "帮我把这篇稿子改得自然一点，别像 AI 写的",
    "把这段话改得不像机器写的",
)

#: 生涯规划：原句（学习任务变体）+ 变体（含前端建议卡文案原句）。
GOLDEN_CAREER: tuple[str, ...] = (
    "给我规划一下我的学习任务",
    FRONTEND_CAREER_SUGGESTION,
    "帮我安排一下复习任务",
    "给我规划一下考研的复习安排",
    "帮我规划一下这学期的课程安排",
)

#: 论文搜索：原句 + 变体。
GOLDEN_PAPER: tuple[str, ...] = (
    "给我找几篇关于 Transformer 的论文",
    "帮我找找近三年大语言模型安全方向的研究文章",
    "推荐几篇 Transformer 的综述论文",
)

#: 负例：必须保持普通聊天（不触发任何能力载荷）。
GOLDEN_ORDINARY: tuple[str, ...] = (
    "这篇论文讲了什么",
    "你觉得这篇文章哪里写得不好",
    "今天帮我安排一下学习计划之外的事",
    "帮我规划一下周末去爬山的路线",
    "考研英语怎么复习",
    "帮我制定一个机器学习学习计划",
)


# ---------------------------------------------------------------------------
# 单元层：确定性检测器与统一分类器
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("content", GOLDEN_HUMANIZER)
def test_golden_humanizer_detector(content: str) -> None:
    routed = route_humanizer_message(content)

    assert routed is not None
    assert routed.skill_input.skill_id == "bridges-humanizer"
    assert routed.skill_input.route is not None
    assert routed.skill_input.route.source == "natural_language"


@pytest.mark.parametrize("content", GOLDEN_CAREER)
def test_golden_career_detector(content: str) -> None:
    assert is_career_intent(content)
    decision = NaturalLanguageRouter().classify(content)

    assert decision.main_capability == MainCapability.CAREER
    assert decision.status == RouteStatus.MATCHED
    assert decision.career_contract is not None
    assert decision.career_contract.target


@pytest.mark.parametrize("content", GOLDEN_PAPER)
def test_golden_paper_router(content: str) -> None:
    decision = NaturalLanguageRouter().classify(content)

    assert decision.status == RouteStatus.MATCHED
    assert decision.is_paper_search
    assert decision.paper_search is not None


@pytest.mark.parametrize("content", GOLDEN_ORDINARY)
def test_golden_ordinary_stays_ordinary(content: str) -> None:
    assert route_humanizer_message(content) is None
    assert not is_career_intent(content)
    decision = NaturalLanguageRouter().classify(content)

    assert decision.main_capability == MainCapability.ORDINARY_CHAT
    assert decision.status == RouteStatus.ORDINARY


def test_golden_study_planning_contract_target_is_study_scoped() -> None:
    """学习任务规划的路由合同目标不套用职业方向文案。"""
    decision = NaturalLanguageRouter().classify("给我规划一下我的学习任务")

    assert decision.career_contract is not None
    assert "学习" in decision.career_contract.target
    # 含职业方向词的考研规划仍走职业方向目标（分支顺序不回归）。
    career_decision = NaturalLanguageRouter().classify("帮我规划考研后的职业方向")
    assert career_decision.career_contract is not None
    assert "职业方向" in career_decision.career_contract.target


# ---------------------------------------------------------------------------
# 集成层：发送时持久化的路由快照
# ---------------------------------------------------------------------------


def _chat_service(tmp_path: Path):
    from bridges.ai import ModelGateway
    from bridges.ai.capability_registry import CapabilityRegistry
    from bridges.chat.repository import ConversationRepository
    from bridges.chat.service import ChatService
    from bridges.storage.database import BridgesDatabase
    from tests.chat.test_arxiv_search_chat import _capability

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    repository = ConversationRepository(database)
    registry = CapabilityRegistry()
    registry.register(_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", _CapturingAdapter())
    return ChatService(
        repository=repository,
        gateway=gateway,
    )


def _assert_persisted_route_snapshot(service: Any, assistant: Any) -> None:
    """发送即持久化：落库记录与重载投影都必须等于发送时路由快照。"""
    persisted = service._repo.get_message("alice", assistant.message_id)  # noqa: SLF001
    assert persisted is not None
    assert persisted.route == assistant.route.model_dump(mode="json")
    assert (
        service.message_projection("alice", assistant.message_id).route
        == assistant.route
    )


SPECIALTY_REQUESTS_WITHOUT_SELECTION: tuple[str, ...] = (
    *GOLDEN_HUMANIZER,
    *GOLDEN_CAREER,
    *GOLDEN_PAPER,
    "帮我规划职业方向，顺便润色一下简历",
    "帮我规划职业方向，顺便把这篇稿子改得自然一点",
    "生成一个关于春天的短视频",
    "帮我改写这篇论文",
    "生成一张小猫图片",
)


@pytest.mark.parametrize("content", SPECIALTY_REQUESTS_WITHOUT_SELECTION)
def test_unselected_specialty_requests_stay_ordinary(
    tmp_path: Path, content: str
) -> None:
    """正文表达专用能力意图时，未显式选择模块仍走日常对话。"""
    service = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")

    user, assistant, _ = service.start_generation(
        "alice", conversation.conversation_id, content
    )

    assert user.skill is None
    assert user.route == assistant.route
    assert assistant.route is not None
    assert assistant.route.main_capability == MainCapability.ORDINARY_CHAT
    assert assistant.route.status == RouteStatus.ORDINARY
    assert assistant.arxiv_search is None
    _assert_persisted_route_snapshot(service, assistant)
    events = list(
        service.stream_generation(
            "alice",
            conversation.conversation_id,
            assistant.message_id,
            _context(),
            until_user_message_id=user.message_id,
        )
    )
    assert events[-1].kind == "done"


@pytest.mark.parametrize("content", GOLDEN_ORDINARY)
def test_golden_ordinary_persists_ordinary_snapshot(
    tmp_path: Path, content: str
) -> None:
    service = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")

    user, assistant, _ = service.start_generation(
        "alice", conversation.conversation_id, content
    )

    assert user.skill is None
    assert assistant.route is not None
    assert assistant.route.main_capability == MainCapability.ORDINARY_CHAT
    assert assistant.route.status == RouteStatus.ORDINARY
    _assert_persisted_route_snapshot(service, assistant)


# ---------------------------------------------------------------------------
# 分类器仍可供显式模块入口使用；普通聊天发送不调用该分类器。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        "帮我规划职业方向，顺便润色一下简历",
        "帮我规划职业方向，顺便把这篇稿子改得自然一点",
    ],
)
def test_golden_conflict_clarifies_instead_of_side_effect(content: str) -> None:
    """复合任务（生涯 + 人味化）只澄清一次，不先偷跑任何一侧。"""
    decision = NaturalLanguageRouter().classify(content)

    assert decision.status == RouteStatus.CLARIFY
    assert decision.main_capability == MainCapability.CLARIFICATION
    assert decision.clarification_question


def test_golden_frontend_suggestion_copy_stays_reachable() -> None:
    """旧建议文案仍被保留，供后续显式模块入口替换。"""
    template = (
        Path(__file__).resolve().parents[2]
        / "apps"
        / "web"
        / "src"
        / "app"
        / "templates"
        / "chat"
        / "chat-template.tsx"
    )
    source = template.read_text(encoding="utf-8")

    assert FRONTEND_CAREER_SUGGESTION in source
