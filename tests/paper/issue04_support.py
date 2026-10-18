"""工单 04 验收测试共用的候选、确定性网关与装配助手。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from bridges.contracts.ai import ModelCallResult, ModelCallStatus
from bridges.kernel.repository import NodeKernelRepository
from bridges.paper.intent import PaperResearchIntentAnalyzer
from bridges.paper.service import PaperSearchService
from bridges.paper.sources import PaperCandidate
from tests.chat.test_chat_api import (
    _create_conversation,
    _gateway_with,
    _register,
)
from tests.paper.test_paper_module_flow import (
    _run_and_read,
    _send,
    _SilentAdapter,
)

REQUEST = "帮我找几篇机器学习入门论文"
NOW = datetime(2026, 10, 18, tzinfo=UTC)


def _candidate(
    title: str,
    abstract: str,
    *,
    source: str = "arxiv",
    identifier: str = "2401.10001",
) -> PaperCandidate:
    """构造标题/摘要可逐字核验的合成候选；两个来源使用不同原生身份。"""
    if source == "arxiv":
        return PaperCandidate(
            arxiv_id=identifier,
            title=title,
            authors=["Ada Lovelace"],
            published_at=datetime(2024, 1, 1, tzinfo=UTC),
            abs_url=f"https://arxiv.org/abs/{identifier}",
            pdf_url=f"https://arxiv.org/pdf/{identifier}",
            abstract=abstract,
            primary_category="cs.LG",
            source=source,
            source_id=identifier,
            sources=(source,),
            source_ids=(f"arxiv:{identifier}",),
        )
    return PaperCandidate(
        arxiv_id=None,
        title=title,
        authors=["Grace Hopper"],
        published_at=datetime(2023, 1, 1, tzinfo=UTC),
        abs_url=f"https://openalex.org/{identifier}",
        pdf_url="",
        abstract=abstract,
        source=source,
        source_id=identifier,
        openalex_id=f"https://openalex.org/{identifier}",
        sources=(source,),
        source_ids=(f"{source}:{identifier.lower()}",),
    )


GOOD_ARXIV = _candidate(
    "Machine Learning: An Introduction",
    "This introduction explains the basic goals and methods of machine learning.",
)
GOOD_OPENALEX = _candidate(
    "Machine Learning for Beginners",
    "A short introduction to machine learning for new students.",
    source="openalex",
    identifier="W100",
)
PARTIAL_OPENALEX = _candidate(
    "Machine Learning in Practice",
    "A practical introduction with examples.",
    source="openalex",
    identifier="W200",
)
UNKNOWN_OPENALEX = _candidate(
    "Unrelated Vision Systems",
    "We study image classification accuracy.",
    source="openalex",
    identifier="W300",
)

_QUOTE_TERM = {"机器学习": "Machine Learning", "入门": "Introduction"}


def _locate(candidate: dict[str, Any], term: str) -> tuple[str, str]:
    for source in ("title", "abstract"):
        text = str(candidate.get(source) or "")
        index = text.lower().find(term.lower())
        if index >= 0:
            return source, text[index : index + len(term)]
    raise AssertionError(f"合成候选缺少可定位引文：{term!r}")


class _PaperGateway:
    """结构化网关替身：真实调用意图、规划、批量证据角色，只替换模型输出。"""

    def __init__(
        self,
        *,
        judge_mode: str = "valid",
        display_name: str = "Machine Learning",
    ) -> None:
        self.judge_mode = judge_mode
        self.display_name = display_name
        self.calls: list[str] = []

    def invoke(
        self,
        _capability: str,
        _version: str,
        _context: Any,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> ModelCallResult:
        schema = payload.get("json_schema") or {}
        properties = set(schema.get("properties") or {})
        if "concepts" in properties:
            self.calls.append("intent")
            output = {
                "research_object": "机器学习入门",
                "research_question": "机器学习入门需要阅读哪些论文",
                "methods": ["机器学习"],
                "applications": [],
                "reading_purpose": "主题探索",
                "reading_purpose_quote": "入门",
                "concepts": [
                    {
                        "concept": self.display_name,
                        "source_phrase": "机器学习",
                        "kind": "method",
                        "english_search_terms": ["machine learning"],
                    },
                    {
                        "concept": "Introduction",
                        "source_phrase": "入门",
                        "kind": "question",
                        "english_search_terms": ["introduction"],
                    },
                ],
                "recommended_sources": ["arxiv", "openalex"],
                "clarification_question": "",
                "clarification_missing": "",
            }
        elif "queries" in properties:
            self.calls.append("plan")
            material = json.loads(payload["messages"][-1]["content"])
            output = {
                "queries": [
                    {
                        "source": source,
                        "query": (
                            'all:"machine learning" AND all:"introduction"'
                            if source == "arxiv"
                            else "machine learning introduction"
                        ),
                    }
                    for source in material["sources"]
                ]
            }
        elif "matches" in properties:
            self.calls.append("judge")
            output = self._judge(payload)
        elif "papers" in properties:
            self.calls.append("review")
            output = {"passed": True, "reason": "证据可逐字定位", "evidence": []}
        else:
            raise AssertionError(f"未预期的结构化请求：{properties}")
        return ModelCallResult(status=ModelCallStatus.SUCCESS, output=output)

    def _judge(self, payload: dict[str, Any]) -> dict[str, Any]:
        material = json.loads(payload["messages"][-1]["content"])
        requirements = material["request"]["requirements"]
        matches = []
        for candidate in material["candidates"]:
            title = str(candidate["title"])
            if self.judge_mode == "all_invalid":
                evidence = [
                    {
                        "requirement": requirements[0]["id"],
                        "source": "title",
                        "quote": "fabricated quote not present",
                    }
                ]
                relevant = True
            elif self.judge_mode == "unknown_requirement":
                evidence = [
                    {
                        "requirement": "c9",
                        "source": "title",
                        "quote": title,
                    }
                ]
                relevant = True
            elif self.judge_mode == "not_relevant":
                evidence = []
                relevant = False
            elif self.judge_mode == "mixed" and title.startswith("Unrelated"):
                evidence = [
                    {
                        "requirement": "c9",
                        "source": "title",
                        "quote": title,
                    }
                ]
                relevant = True
            elif self.judge_mode == "mixed" and title.startswith("Machine Learning in Practice"):
                source, quote = _locate(candidate, "Machine Learning")
                evidence = [
                    {
                        "requirement": requirements[0]["id"],
                        "source": source,
                        "quote": quote,
                    }
                ]
                relevant = True
            else:
                evidence = []
                for requirement in requirements:
                    term = _QUOTE_TERM[requirement["source_phrase"]]
                    source, quote = _locate(candidate, term)
                    evidence.append(
                        {
                            "requirement": requirement["id"],
                            "source": source,
                            "quote": quote,
                        }
                    )
                relevant = True
            matches.append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "relevant": relevant,
                    "evidence": evidence,
                }
            )
        return {"matches": matches}


def _install_paper(
    app: Any,
    *,
    arxiv: Any,
    openalex: Any,
    gateway: _PaperGateway,
) -> PaperSearchService:
    service = PaperSearchService(
        source=arxiv,
        sources={"arxiv": arxiv, "openalex": openalex},
        enricher=None,
        summarizer=None,
        gateway=gateway,
        intent_analyzer_factory=PaperResearchIntentAnalyzer,
    )
    app.state.paper_search_service = service
    app.state.chat_service._paper_search = service  # noqa: SLF001
    app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    return service


def _journey(
    app: Any,
    client: TestClient,
    drive: Any,
    *,
    gateway: _PaperGateway,
    arxiv: Any,
    openalex: Any,
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    _register(client)
    _install_paper(app, arxiv=arxiv, openalex=openalex, gateway=gateway)
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, REQUEST, module_id="paper")
    assistant = _run_and_read(
        app, client, drive, conversation_id, created
    )
    return assistant, created, conversation_id, created["assistant_message"]["message_id"]


def _artifacts(app: Any, account_id: str, conversation_id: str) -> dict[str, Any]:
    repository = NodeKernelRepository(app.state.chat_service._repo.database)  # noqa: SLF001
    return {
        artifact.node: artifact
        for artifact in repository.list_artifacts(account_id, conversation_id)
    }
