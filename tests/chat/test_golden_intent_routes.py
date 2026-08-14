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
from bridges.skills.registry import SkillRegistryError
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


class _FakeHumanizerOrchestrator:
    """人味化编排替身：只做注册校验（resolve_skill），不执行生成。"""

    def resolve_skill(self, skill_input: Any) -> Any:
        if skill_input.skill_id != "bridges-humanizer":
            raise SkillRegistryError(
                "skill_not_found", f"未注册的 SKILL 标识：{skill_input.skill_id}"
            )
        return skill_input


def _chat_service(tmp_path: Path):
    from bridges.ai import ModelGateway
    from bridges.ai.capability_registry import CapabilityRegistry
    from bridges.arxiv_mcp.service import ArxivSearchService
    from bridges.chat.repository import ConversationRepository
    from bridges.chat.service import ChatService
    from bridges.storage.database import BridgesDatabase
    from tests.chat.test_arxiv_search_chat import _capability, _FakeArxivClient

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
        arxiv_search_service=ArxivSearchService(client=_FakeArxivClient()),
        humanizer_service=_FakeHumanizerOrchestrator(),
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


@pytest.mark.parametrize("content", GOLDEN_HUMANIZER)
def test_golden_humanizer_persists_skill_snapshot(tmp_path: Path, content: str) -> None:
    service = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")

    user, assistant = service.start_generation(
        "alice", conversation.conversation_id, content
    )

    assert user.skill is not None
    assert user.skill["skill_id"] == "bridges-humanizer"
    assert user.skill["route"]["source"] == "natural_language"
    assert assistant.route is not None
    assert assistant.route.main_capability == MainCapability.HUMANIZER
    # 发送即持久化：重载后一致（快照来自落库记录，而非内存返回值）。
    persisted_user = service._repo.get_message("alice", user.message_id)  # noqa: SLF001
    assert persisted_user is not None and persisted_user.skill == user.skill
    _assert_persisted_route_snapshot(service, assistant)


@pytest.mark.parametrize("content", GOLDEN_CAREER)
def test_golden_career_persists_route_snapshot(tmp_path: Path, content: str) -> None:
    service = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")

    user, assistant = service.start_generation(
        "alice", conversation.conversation_id, content
    )

    assert assistant.route is not None
    assert assistant.route.is_career
    assert assistant.route.career_contract is not None
    assert user.route == assistant.route
    _assert_persisted_route_snapshot(service, assistant)


@pytest.mark.parametrize("content", GOLDEN_PAPER)
def test_golden_paper_persists_route_snapshot(tmp_path: Path, content: str) -> None:
    service = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")

    user, assistant = service.start_generation(
        "alice", conversation.conversation_id, content
    )

    assert assistant.route is not None
    assert assistant.route.is_paper_search
    assert assistant.arxiv_search is not None
    assert user.route == assistant.route
    _assert_persisted_route_snapshot(service, assistant)


@pytest.mark.parametrize("content", GOLDEN_ORDINARY)
def test_golden_ordinary_persists_ordinary_snapshot(
    tmp_path: Path, content: str
) -> None:
    service = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")

    user, assistant = service.start_generation(
        "alice", conversation.conversation_id, content
    )

    assert user.skill is None
    assert assistant.route is not None
    assert assistant.route.main_capability == MainCapability.ORDINARY_CHAT
    assert assistant.route.status == RouteStatus.ORDINARY
    _assert_persisted_route_snapshot(service, assistant)


@pytest.mark.parametrize("content", GOLDEN_CAREER)
def test_golden_career_streaming_rejudges_intent(tmp_path: Path, content: str) -> None:
    """生涯流式分支现场重判：未挂载规划器时以 career_unavailable 收敛。

    金标语句在流式阶段被 ``turn.py`` 的 ``is_career_intent`` 二次判定为
    生涯意图（而非退化为普通聊天回答）；若词表回归，这条消息会走普通
    生成并正常 done，本测试即红。
    """
    service = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")

    user, assistant = service.start_generation(
        "alice", conversation.conversation_id, content
    )
    events = list(
        service.stream_generation(
            "alice",
            conversation.conversation_id,
            assistant.message_id,
            _context(),
            until_user_message_id=user.message_id,
        )
    )

    errors = [event for event in events if event.kind == "error"]
    assert errors, "生涯金标语句必须进入生涯编排分支（现场重判）"
    assert errors[-1].error_code == "career_unavailable"


# ---------------------------------------------------------------------------
# 防护回归：冲突 / 优先级 / 降级语义
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


def test_golden_video_priority_survives_new_vocabulary(tmp_path: Path) -> None:
    """视频生成优先级不因新词项回归。"""
    service = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")

    _, assistant = service.start_generation(
        "alice", conversation.conversation_id, "生成一个关于春天的短视频"
    )

    assert assistant.route is not None
    assert assistant.route.main_capability == MainCapability.VIDEO
    assert assistant.route.status == RouteStatus.MATCHED


def test_golden_paper_rewrite_downgrade_stays_ordinary(tmp_path: Path) -> None:
    """论文改写降级：论文搜索侧退让（不触发 arXiv 副作用），人味化载荷生效。"""
    service = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")

    user, assistant = service.start_generation(
        "alice", conversation.conversation_id, "帮我改写这篇论文"
    )

    assert assistant.route is not None
    assert not assistant.route.is_paper_search
    assert assistant.route.main_capability == MainCapability.HUMANIZER
    assert assistant.arxiv_search is None
    assert user.skill is not None
    assert user.skill["skill_id"] == "bridges-humanizer"


def test_golden_frontend_suggestion_copy_stays_reachable() -> None:
    """前端建议卡文案对应语句纳入金标：产品自述能力真实可达。"""
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
