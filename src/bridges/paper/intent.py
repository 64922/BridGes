"""模型驱动的论文研究意图理解；硬条件仍由代码解析与校验。"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from bridges.ai.payload_budget import (
    CallMaterialManifest,
    MaterialCategory,
    MaterialManifestEntry,
    estimate_tokens,
    evaluate_call_manifest,
)
from bridges.paper.contracts import (
    PaperClarification,
    PaperConditionSource,
    PaperConstraints,
    PaperResearchConcept,
    PaperTermAnalysis,
)
from bridges.paper.query_privacy import is_public_search_term
from bridges.paper.request_constraints import PAPER_COUNT, parse_constraints
from bridges.paper.topics import normalize_phrase

INTENT_CAPABILITY = "qwen_structured_output"
INTENT_CAPABILITY_VERSION = "1"
INTENT_MAX_OUTPUT_TOKENS = 1800
SUPPORTED_SOURCES = frozenset({"arxiv", "openalex", "crossref"})
_ALLOWED_CONCEPT_KINDS = frozenset(
    {"object", "question", "method", "application", "population", "condition"}
)
_SOURCE_ALIASES = {
    "arxiv": "arxiv",
    "arxiv.org": "arxiv",
    "openalex": "openalex",
    "crossref": "crossref",
}

INTENT_SYSTEM_PROMPT = (
    "你是论文搜索的研究意图理解角色。只输出符合 JSON Schema 的结构化结果，不输出思维链。\n"
    "理解完整研究问题，保留对象、问题、方法、应用场景、用户明确条件和阅读目的；"
    "不要把组合问题缩成一个宽泛主题。\n"
    "将每个检索概念关联到请求或给定任务条件中的原文片段，并给出准确、简短的英文检索表达及常见同义表达。\n"
    "不推断年份、篇数、来源限制、排除条件或用户身份。若关键术语仍有会改变检索方向的歧义，"
    "只提出一个简短问题。\n"
    "按领域推荐 2 到 3 个互补来源：arxiv 侧重预印本，openalex 覆盖广泛学术作品并有引用关系，"
    "crossref 提供 DOI 注册元数据。\n"
    "来源建议不是用户硬限制；硬条件稍后由代码按原话核对。不要返回私人背景、完整历史或不必要内容。"
)

INTENT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "research_object": {"type": "string", "maxLength": 180},
        "research_question": {"type": "string", "maxLength": 240},
        "methods": {"type": "array", "items": {"type": "string", "maxLength": 100}, "maxItems": 8},
        "applications": {
            "type": "array",
            "items": {"type": "string", "maxLength": 100},
            "maxItems": 8,
        },
        "reading_purpose": {"type": "string", "maxLength": 80},
        "reading_purpose_quote": {"type": "string", "maxLength": 120},
        "concepts": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "concept": {"type": "string", "maxLength": 120},
                    "source_phrase": {"type": "string", "maxLength": 120},
                    "kind": {"type": "string", "enum": sorted(_ALLOWED_CONCEPT_KINDS)},
                    "english_search_terms": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 120},
                        "maxItems": 5,
                    },
                },
                "required": ["concept", "source_phrase", "kind", "english_search_terms"],
            },
        },
        "recommended_sources": {
            "type": "array",
            "items": {"type": "string", "enum": sorted(SUPPORTED_SOURCES)},
            "minItems": 2,
            "maxItems": 3,
        },
        "clarification_question": {"type": "string", "maxLength": 240},
        "clarification_missing": {
            "type": "string",
            "enum": ["", "topic", "domain"],
        },
    },
    "required": [
        "research_object",
        "research_question",
        "methods",
        "applications",
        "reading_purpose",
        "reading_purpose_quote",
        "concepts",
        "recommended_sources",
        "clarification_question",
        "clarification_missing",
    ],
}


class PaperResearchIntentAnalyzer:
    """通过登记模型网关理解本轮请求，并确定性保留有效硬条件。"""

    def __init__(
        self,
        gateway: Any,
        *,
        run_context: Any,
        model_id: str | None,
        quota: Any | None,
        budget: Any | None,
        manifest_sink: Callable[[CallMaterialManifest], None] | None,
    ) -> None:
        self._gateway = gateway
        self._run_context = run_context
        self._model_id = model_id
        self._quota = quota
        self._budget = budget
        self._manifest_sink = manifest_sink

    def analyze(
        self,
        request: str,
        *,
        prior_context: Sequence[str] = (),
        topic_hint: str | None = None,
        task_conditions: Sequence[Any] = (),
        pending: Any | None = None,
        now: datetime | None = None,
        citation_seed: Any | None = None,
    ) -> PaperTermAnalysis | None:
        """模型失败或结构无效时返回 None；不会回退到词表解析。"""
        task_values = [
            {
                "kind": str(getattr(item, "kind", "")),
                "text": str(getattr(item, "text", "")).strip()[:240],
            }
            for item in task_conditions
            if str(getattr(item, "text", "")).strip()
        ]
        allowed_phrases = [request, topic_hint or "", *(item["text"] for item in task_values)]
        if citation_seed is not None:
            allowed_phrases.append(str(citation_seed.title))
        if pending is not None:
            context = getattr(pending, "context", None)
            if isinstance(context, Mapping):
                original = str(context.get("original_phrase") or "")
                if original:
                    allowed_phrases.append(original)
        request_payload = {
            "request": request,
            "task_topic": (topic_hint or "")[:240],
            "task_conditions": task_values,
            "recent_context": [
                str(item).strip()[:240] for item in prior_context[-2:] if str(item).strip()
            ],
            "pending_question": str(getattr(pending, "question", ""))[:240] if pending else "",
            "referenced_paper": (
                {"title": citation_seed.title, "abstract": citation_seed.abstract[:1500]}
                if citation_seed is not None else None
            ),
        }
        try:
            output = self._call(request_payload)
        except Exception:  # noqa: BLE001 - 失败由论文节点返回明确重试终态
            return None
        if not isinstance(output, Mapping):
            return None

        concepts = _concepts(output.get("concepts"), allowed_phrases)
        clarification_question = _text(output.get("clarification_question"), limit=240)
        missing = _text(output.get("clarification_missing"), limit=16)
        if clarification_question:
            if clarification_question.count("?") + clarification_question.count("？") > 1:
                return None
            source_phrase = next(
                (item.source_phrase for item in concepts),
                normalize_phrase(topic_hint or request)[:120],
            )
            constraints = _constraints(request, task_conditions, now=now)
            return PaperTermAnalysis(
                original_phrase=source_phrase,
                original_request=request,
                normalized_term=normalize_phrase(source_phrase),
                expansions=[],
                confidence=0.85,
                constraints=constraints,
                final_query="",
                clarification=PaperClarification(
                    question=clarification_question,
                    missing=missing if missing in {"topic", "domain"} else "domain",
                    original_phrase=source_phrase,
                ),
                clarification_missing=missing or "domain",
            )
        if not concepts or not any(item.english_search_terms for item in concepts):
            return None

        all_terms = list(
            dict.fromkeys(term for concept in concepts for term in concept.english_search_terms)
        )
        original_phrases = list(dict.fromkeys(item.source_phrase for item in concepts))
        original_phrase = "、".join(original_phrases)[:240]
        if not original_phrase or not all_terms:
            return None
        conditions = _constraints(request, task_conditions, now=now)
        selected_sources = [
            source
            for source in _string_list(output.get("recommended_sources"), limit=3)
            if source in SUPPORTED_SOURCES
        ]
        if not conditions.allowed_sources and len(selected_sources) < 2:
            return None
        purpose = _text(output.get("reading_purpose"), limit=80)
        purpose_quote = _text(output.get("reading_purpose_quote"), limit=120)
        purpose_inferred = not any(
            purpose_quote and purpose_quote in phrase for phrase in allowed_phrases
        )
        return PaperTermAnalysis(
            original_phrase=original_phrase,
            original_request=request,
            normalized_term=normalize_phrase(original_phrase),
            expansions=all_terms,
            confidence=0.9,
            research_object=_text(output.get("research_object"), limit=180),
            research_question=_text(output.get("research_question"), limit=240),
            methods=_string_list(output.get("methods"), limit=8),
            applications=_string_list(output.get("applications"), limit=8),
            reading_purpose=purpose,
            reading_purpose_inferred=purpose_inferred,
            concepts=concepts,
            recommended_sources=selected_sources,
            condition_sources=_condition_sources(
                request,
                conditions,
                [item["text"] for item in task_values],
                now=now,
            ),
            constraints=conditions,
            final_query=" ".join(all_terms),
        )

    def plan(
        self,
        analysis: PaperTermAnalysis,
        *,
        available_sources: tuple[str, ...],
        feedback: Mapping[str, Any] | None = None,
    ) -> tuple[Any, ...]:
        """使用同一登记网关与共享预算提出来源专用查询。"""
        from bridges.paper.query_planning import model_plan_queries

        return model_plan_queries(self._call, analysis, available_sources, feedback=feedback)

    def _call(
        self,
        request_payload: Mapping[str, Any],
        *,
        system_prompt: str = INTENT_SYSTEM_PROMPT,
        json_schema: dict[str, Any] = INTENT_JSON_SCHEMA,
    ) -> Any:
        user_content = json.dumps(request_payload, ensure_ascii=False, separators=(",", ":"))
        payload = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "json_schema": json_schema,
            "temperature": 0,
            "max_tokens": INTENT_MAX_OUTPUT_TOKENS,
        }
        manifest = evaluate_call_manifest(
            payload,
            quota=self._quota,
            output_tokens=INTENT_MAX_OUTPUT_TOKENS,
            entries=[
                MaterialManifestEntry(
                    material_id="paper.intent.rules",
                    category=MaterialCategory.SYSTEM_RULE.value,
                    necessity="required",
                    adopted=True,
                    reason="结构化研究意图输出规则",
                    estimated_tokens=estimate_tokens(system_prompt),
                    read_range="完整系统规则",
                ),
                MaterialManifestEntry(
                    material_id="paper.intent.request",
                    category=MaterialCategory.USER_REQUEST.value,
                    necessity="required",
                    adopted=True,
                    reason="理解用户当前论文研究请求与有效任务条件",
                    estimated_tokens=estimate_tokens(user_content),
                    read_range="本轮请求与限定的任务条件",
                ),
            ],
        )
        if self._manifest_sink is not None:
            self._manifest_sink(manifest)
        if not manifest.gate.within_budget:
            return None
        result = self._gateway.invoke(
            INTENT_CAPABILITY,
            INTENT_CAPABILITY_VERSION,
            self._run_context,
            payload=payload,
            model_override=self._model_id,
            model_quota=self._quota,
            budget=self._budget,
        )
        if getattr(result.status, "value", "") not in {"success", "degraded"}:
            return None
        return result.output


def _concepts(value: Any, allowed_phrases: Sequence[str]) -> list[PaperResearchConcept]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    output: list[PaperResearchConcept] = []
    seen: set[tuple[str, str]] = set()
    for entry in value[:12]:
        if not isinstance(entry, Mapping):
            return []
        concept = _text(entry.get("concept"), limit=120)
        source_phrase = _text(entry.get("source_phrase"), limit=120)
        kind = _text(entry.get("kind"), limit=24)
        terms = _string_list(entry.get("english_search_terms"), limit=5)
        if (
            not concept
            or not source_phrase
            or kind not in _ALLOWED_CONCEPT_KINDS
            or not any(source_phrase in phrase for phrase in allowed_phrases if phrase)
        ):
            continue
        if any(not is_public_search_term(item) for item in terms):
            return []
        terms = [item for item in terms if re.search(r"[A-Za-z]", item)]
        if not terms:
            continue
        key = (source_phrase.casefold(), kind)
        if key in seen:
            continue
        seen.add(key)
        output.append(
            PaperResearchConcept(
                concept_id=f"c{len(output) + 1}",
                concept=concept,
                source_phrase=source_phrase,
                kind=kind,
                english_search_terms=terms,
            )
        )
    return output


def _constraints(
    request: str, task_conditions: Sequence[Any], *, now: datetime | None
) -> PaperConstraints:
    current = now or datetime.now(UTC)
    constraints = parse_constraints(request, now=current)
    for item in task_conditions:
        condition_text = str(getattr(item, "text", "")).strip()
        if not condition_text:
            continue
        saved = parse_constraints(condition_text, now=current)
        if constraints.year_from is None and constraints.year_to is None:
            constraints.year_from, constraints.year_to = saved.year_from, saved.year_to
        if constraints.requested_count is None:
            constraints.requested_count = saved.requested_count
        if not constraints.allowed_sources:
            constraints.allowed_sources = list(saved.allowed_sources)
        constraints.excluded_sources = list(
            dict.fromkeys([*constraints.excluded_sources, *saved.excluded_sources])
        )
        constraints.arxiv_id = constraints.arxiv_id or saved.arxiv_id
        constraints.paper_title = constraints.paper_title or saved.paper_title
        constraints.exclude_survey = constraints.exclude_survey or saved.exclude_survey
        constraints.unsupported_sources = list(
            dict.fromkeys([*constraints.unsupported_sources, *saved.unsupported_sources])
        )
    return constraints


def _condition_sources(
    request: str,
    constraints: PaperConstraints,
    task_condition_texts: Sequence[str] = (),
    *,
    now: datetime | None = None,
) -> list[PaperConditionSource]:
    found: list[PaperConditionSource] = []
    phrases = tuple(dict.fromkeys(text for text in (request, *task_condition_texts) if text))
    current = now or datetime.now(UTC)
    year_pattern = (
        r"(?:近\s*[一二两三四五六七八九十\d]+\s*年|"
        r"\d{4}(?:\s*[-–—~～至到]\s*\d{4}|\s*年(?:以后|之后|以来|起|后)?)?)"
    )
    for phrase in phrases:
        phrase_constraints = parse_constraints(phrase, now=current)
        year_match = re.search(year_pattern, phrase)
        if (
            year_match is not None
            and (constraints.year_from is not None or constraints.year_to is not None)
            and phrase_constraints.year_from == constraints.year_from
            and phrase_constraints.year_to == constraints.year_to
        ):
            found.append(
                PaperConditionSource(
                    kind="year",
                    value=f"{constraints.year_from or ''}:{constraints.year_to or ''}",
                    source_phrase=year_match.group(0),
                )
            )
        count = PAPER_COUNT.search(phrase)
        if (
            count is not None
            and phrase_constraints.requested_count == constraints.requested_count
            and constraints.requested_count is not None
        ):
            found.append(
                PaperConditionSource(
                    kind="count",
                    value=str(constraints.requested_count),
                    source_phrase=count.group(0),
                )
            )
        phrase_sources = list(
            dict.fromkeys(
                phrase_constraints.allowed_sources
                + phrase_constraints.excluded_sources
                + phrase_constraints.unsupported_sources
            )
        )
        effective_sources = set(
            constraints.allowed_sources
            + constraints.excluded_sources
            + constraints.unsupported_sources
        )
        for source in phrase_sources:
            if source not in effective_sources:
                continue
            match = re.search(re.escape(source), phrase, re.IGNORECASE)
            if match is not None:
                found.append(
                    PaperConditionSource(kind="source", value=source, source_phrase=match.group(0))
                )
        phrase_papers = (phrase_constraints.arxiv_id, phrase_constraints.paper_title)
        for paper in phrase_papers:
            if (
                paper
                and paper in phrase
                and paper
                in (
                    constraints.arxiv_id,
                    constraints.paper_title,
                )
            ):
                found.append(PaperConditionSource(kind="paper", value=paper, source_phrase=paper))
        if constraints.exclude_survey and phrase_constraints.exclude_survey:
            match = re.search(r"综述|review", phrase, re.IGNORECASE)
            if match is not None:
                found.append(
                    PaperConditionSource(
                        kind="type", value="exclude_survey", source_phrase=match.group(0)
                    )
                )
    unique = {(item.kind, item.value, item.source_phrase): item for item in found}
    return list(unique.values())


def _text(value: Any, *, limit: int) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _string_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return list(dict.fromkeys(text for item in value[:limit] if (text := _text(item, limit=120))))
