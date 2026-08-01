"""Chinese human-flavor diagnostic engine for T028.

The engine applies deterministic, auditable rules to locate template patterns,
translation patterns, rhythm issues and tone-boundary problems in Chinese
scientific expression drafts. It produces concrete text fragments, reasons,
issue types and non-binding suggested patches. It never emits an "AI probability"
and never changes fact locks.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime

from bridges.contracts.expression import (
    DraftSpan,
    ExpressionDraft,
    Genre,
    RevisionPatch,
    StyleDiagnosticFinding,
    StyleDiagnosticReport,
    StyleDiagnosticSeverity,
    StyleIssueType,
    StylePolicy,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _token(prefix: str = "style") -> str:
    return f"{prefix}-{secrets.token_urlsafe(12)}"


# Rule-based detectors. Each entry maps to one StyleIssueType and provides a
# human-readable reason plus a simple patch function.
_TEMPLATE_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(r"^(本文将|本研究将|这篇文章将|本文旨在|本文主要)"),
        "模板腔：以元话语开头，延迟给出实质判断。",
        "直接陈述核心判断，删除‘本文将’类元话语。",
    ),
    (
        re.compile(r"(综上所述|总而言之|由此可见|不难看出)"),
        "模板腔：使用程式化总结短语。",
        "用具体结论或下一步替换程式化总结。",
    ),
    (
        re.compile(r"随着[^，。；]+的发展"),
        "模板腔：‘随着……的发展’是常见背景套路。",
        "直接说明具体背景或问题。",
    ),
    (
        re.compile(r"具有重要意义|具有重要价值|具有深远影响"),
        "模板腔：空泛价值拔高。",
        "说明对谁的什么决策或理解有何具体意义。",
    ),
]

_TRANSLATION_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(r"被[^，。；\s]{2,20}(?:所|而)?(?:发现|证明|确认|使用)"),
        "翻译腔：过度使用被动句式。",
        "改用中文主动主谓结构，保留主语。",
    ),
    (
        re.compile(r"对于[^来说]+来说"),
        "翻译腔：‘对于……来说’是逐字翻译结构。",
        "改为‘对……而言’或直接调整语序。",
    ),
    (
        re.compile(r"进行一个[^，。；\s]{1,10}"),
        "翻译腔：‘进行一个’是英文 do a 的直译。",
        "改为具体动词，如‘做一次’‘开展’。",
    ),
    (
        re.compile(r"(?:导致|引起|造成)了[^，。；]{0,10}(?:结果|后果|现象)"),
        "翻译腔：用‘了’衔接名词化结果。",
        "直接说明因果关系，避免冗余名词化。",
    ),
]

_RHYTHM_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(r"[^，。；：！？]{45,}"),
        "节奏问题：连续超过 45 字无停顿，增加阅读负担。",
        "在适当位置拆分长句，保留限定条件。",
    ),
]

_TONE_BOUNDARY_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(r"我完全理解你|我懂你的感受|我能体会"),
        "分寸问题：系统不应伪装真实共情或替用户发声。",
        "删除拟人共情，改为客观信息支持。",
    ),
    (
        re.compile(r"显然|毫无疑问|绝对|必然"),
        "分寸问题：绝对化措辞可能越过证据强度。",
        "改用与证据强度匹配的限定表达。",
    ),
]

_VAGUE_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(r"有关方面|相关领域|一定程度上"),
        "空泛内容：模糊指代削弱可信度。",
        "具体说明是哪一主体、条件或范围。",
    ),
]

_MECHANICAL_ARGUMENT_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(r"(?:首先[^。]*。其次[^。]*。最后[^。]*。){1}"),
        "论证机械：固定三段式结构。",
        "按论证逻辑自然组织，不必强行一二三。",
    ),
]

_STIFF_LANGUAGE_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(r"(?:进行|开展|实施)[^，。；]{0,10}(?:分析|研究|讨论|比较)"),
        "语言生硬：动词名词化堆砌。",
        "改用更直接的动词，如‘分析’‘比较’。",
    ),
]

_FORMAT_IMBALANCE_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(r"^(#{1,6}\s+.*\n){3,}"),
        "格式失衡：连续标题可能把正文变成提纲。",
        "减少标题密度，用段落展开关键判断。",
    ),
]

_CONVERSATION_RESIDUE_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(r"^(好的|没问题|当然可以|很高兴)"),
        "交流残留：助手对话痕迹。",
        "删除礼貌寒暄，直接呈现内容。",
    ),
]

_SCIENTIFIC_OVERREACH_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(r"证明[^，。；]*(?:导致|引起|造成)"),
        "科学越界：将相关或支持证据表述为证明因果。",
        "改为‘支持’‘提示’等符合证据强度的动词。",
    ),
]

_ISSUE_RULES: list[tuple[StyleIssueType, list[tuple[re.Pattern[str], str, str]]]] = [
    (StyleIssueType.TEMPLATE_PATTERN, _TEMPLATE_PATTERNS),
    (StyleIssueType.TRANSLATION_PATTERN, _TRANSLATION_PATTERNS),
    (StyleIssueType.RHYTHM_ISSUE, _RHYTHM_PATTERNS),
    (StyleIssueType.TONE_BOUNDARY, _TONE_BOUNDARY_PATTERNS),
    (StyleIssueType.VAGUE_CONTENT, _VAGUE_PATTERNS),
    (StyleIssueType.MECHANICAL_ARGUMENT, _MECHANICAL_ARGUMENT_PATTERNS),
    (StyleIssueType.STIFF_LANGUAGE, _STIFF_LANGUAGE_PATTERNS),
    (StyleIssueType.FORMAT_IMBALANCE, _FORMAT_IMBALANCE_PATTERNS),
    (StyleIssueType.CONVERSATION_RESIDUE, _CONVERSATION_RESIDUE_PATTERNS),
    (StyleIssueType.SCIENTIFIC_OVERREACH, _SCIENTIFIC_OVERREACH_PATTERNS),
]


def _simple_patch(original: str, issue_type: StyleIssueType) -> str | None:
    """Return a trivial deterministic patch for common patterns.

    The patch is only a suggestion; the service layer verifies that it preserves
    fact locks before applying it.
    """
    if issue_type == StyleIssueType.TEMPLATE_PATTERN:
        if re.match(r"^(本文将|本研究将|这篇文章将|本文旨在|本文主要)", original):
            return re.sub(r"^(本文将|本研究将|这篇文章将|本文旨在|本文主要)", "", original)
        if "综上所述" in original or "总而言之" in original:
            return "（请用具体结论替换此处总结）"
    if issue_type == StyleIssueType.TRANSLATION_PATTERN:
        if "对于" in original and "来说" in original:
            return original.replace("来说", "而言")
        if "进行一个" in original:
            return original.replace("进行一个", "做一次")
    if issue_type == StyleIssueType.TONE_BOUNDARY:
        if "显然" in original:
            return original.replace("显然", "在所述条件下")
        if "毫无疑问" in original:
            return original.replace("毫无疑问", "现有证据支持")
    if issue_type == StyleIssueType.CONVERSATION_RESIDUE:
        return "（删除寒暄，直接给出内容）"
    if issue_type == StyleIssueType.SCIENTIFIC_OVERREACH:
        return original.replace("证明", "支持")
    return None


def _severity_for_issue(
    issue_type: StyleIssueType, genre: Genre
) -> StyleDiagnosticSeverity:
    """Map issue type and genre to a default severity.

    Scientific overreach is blocking in every genre because it risks changing the
    evidence-derived strength ceiling. Other issues are suggestions by default.
    """
    if issue_type == StyleIssueType.SCIENTIFIC_OVERREACH:
        return StyleDiagnosticSeverity.BLOCKING
    if issue_type in {
        StyleIssueType.TEMPLATE_PATTERN,
        StyleIssueType.TRANSLATION_PATTERN,
    }:
        return StyleDiagnosticSeverity.SUGGESTION
    return StyleDiagnosticSeverity.INFO


def build_style_policy(draft: ExpressionDraft) -> StylePolicy:
    """Synthesize a style policy from the draft's genre and brief."""
    genre_contract = None
    # Local import to avoid circular dependency at module load time.
    from bridges.expression.service import _genre_contract

    genre_contract = _genre_contract(draft.genre)
    forbidden: list[str] = []
    if genre_contract:
        forbidden.extend(genre_contract.prohibited_behaviors)

    max_sentence_length = 40
    if draft.genre == Genre.POPULAR_SCIENCE:
        max_sentence_length = 35
    elif draft.genre == Genre.RESEARCH_REPORT:
        max_sentence_length = 50

    return StylePolicy(
        policy_id=_token("policy"),
        draft_id=draft.draft_id,
        genre=draft.genre,
        locale=draft.brief.language_locale,
        max_sentence_length=max_sentence_length,
        preferred_term_style="audience_matched",
        forbidden_phrases=forbidden,
        required_qualifier_style="evidence_aligned",
        personalization_note=draft.personalization_note,
    )


def run_diagnostic(draft: ExpressionDraft) -> StyleDiagnosticReport:
    """Run the rule-based Chinese expression diagnostic over a draft.

    The diagnostic scans each span for concrete pattern matches and returns
    findings anchored to span ids and text fragments. AI-detector scores are not
    computed or used as gates.
    """
    findings: list[StyleDiagnosticFinding] = []
    seen: set[tuple[str | None, str]] = set()

    spans = list(draft.spans)
    for span in spans:
        for issue_type, rules in _ISSUE_RULES:
            for pattern, reason, rule in rules:
                for match in pattern.finditer(span.text):
                    fragment = match.group(0)
                    key = (span.span_id, fragment)
                    if key in seen:
                        continue
                    seen.add(key)
                    suggested = _simple_patch(fragment, issue_type)
                    findings.append(
                        StyleDiagnosticFinding(
                            finding_id=_token("find"),
                            span_id=span.span_id,
                            issue_type=issue_type,
                            severity=_severity_for_issue(issue_type, draft.genre),
                            original_text=fragment,
                            reason=reason,
                            suggested_patch=suggested,
                            genre_rule=rule,
                        )
                    )

    passed = not any(
        f.severity == StyleDiagnosticSeverity.BLOCKING for f in findings
    )

    return StyleDiagnosticReport(
        report_id=_token("report"),
        draft_id=draft.draft_id,
        findings=findings,
        ai_detector_score=None,
        ai_detector_used_as_gate=False,
        passed=passed,
    )


def suggest_patch_for_finding(
    draft: ExpressionDraft, finding: StyleDiagnosticFinding
) -> RevisionPatch | None:
    """Create a revision patch from a diagnostic finding.

    The patch records the original and patched text so the service layer can
    verify fact-lock invariance before applying it. When the finding only flags
    a small fragment, the patch applies the suggested replacement to the whole
    span text so the user sees the complete revised sentence.
    """
    span_id = finding.span_id
    if span_id is None:
        return None
    span = next((s for s in draft.spans if s.span_id == span_id), None)
    if span is None:
        return None

    original_fragment = finding.original_text
    suggested_fragment = finding.suggested_patch

    if suggested_fragment is not None and original_fragment in span.text:
        patched_text = span.text.replace(original_fragment, suggested_fragment, 1)
    elif suggested_fragment is not None:
        patched_text = suggested_fragment
    else:
        # No explicit patch: the finding is informational only.
        return None

    if patched_text == span.text:
        return None

    return RevisionPatch(
        patch_id=_token("patch"),
        finding_id=finding.finding_id,
        target_span_id=span_id,
        original_text=span.text,
        patched_text=patched_text,
        issue_type=finding.issue_type,
        reason=finding.reason,
    )


__all__ = [
    "build_style_policy",
    "run_diagnostic",
    "suggest_patch_for_finding",
]
