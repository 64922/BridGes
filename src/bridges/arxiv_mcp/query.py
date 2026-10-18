"""arXiv search_query 构造：安全解析规划表达式并保持硬约束。"""

from __future__ import annotations

import re

from bridges.arxiv_mcp.errors import ArxivMcpError

_QUERY_ARXIV_ID = re.compile(
    r"^(?:\d{4}\.\d{4,5}(?:v\d+)?|[A-Za-z][A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+)?/\d{7})$"
)
_STRUCTURED_FIELD = re.compile(r"(author|title|id|year)\s*:", re.I)
_EXPRESSION_FIELD = re.compile(r"(all|ti|abs|au|co|jr|cat|rn)\s*:", re.I)
_BOOLEAN_OPERATOR = re.compile(r"(?<!\S)(AND|OR|ANDNOT)(?!\S)")
_YEAR_VALUE = re.compile(r"^(\d{4})(?:\s*[-–—]\s*(\d{4}))?$")
_SUBMITTED_DATE_RANGE = re.compile(
    r"(?i)(?:(?<!\S)AND\s+)?(?<!\S)submittedDate\s*:\s*\[([^\]]+)\]"
)
_UNSUPPORTED_DATE_OPERATOR = re.compile(
    r"(?i)(?<!\S)(OR|ANDNOT)\s+submittedDate\s*:"
    r"|submittedDate\s*:\s*\[[^\]]+\]\s+(OR|ANDNOT)(?!\S)"
)
_SUBMITTED_DATE_VALUE = re.compile(r"^\d{12}\s+TO\s+\d{12}$", re.I)
_CONTROL_CHARACTER = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_EXPRESSION_FIELDS = frozenset(
    {"all", "ti", "abs", "au", "co", "jr", "cat", "rn", "author", "title", "year", "id"}
)
_EXPRESSION_FIELD_ALIASES = {"author": "au", "title": "ti"}
_MAX_EXPRESSION_LENGTH = 512
_QUOTE_CHARS = "\"'“”‘’"
_QUOTE_PAIRS = {'"': '"', "'": "'", "“": "”", "‘": "’"}
_QUOTE_TRANSLATION = dict.fromkeys(
    (ord(character) for character in _QUOTE_CHARS), ord(" ")
)


def build_search_params(query: str, max_results: int) -> dict[str, str]:
    """把路由器的约束快照转换为 arXiv API 的结构化查询。"""
    normalized = " ".join(query.split())
    _reject_control_characters(normalized)
    date_clauses = _extract_submitted_date_ranges(normalized)
    if date_clauses or _has_expression_syntax(normalized):
        if date_clauses:
            base = _strip_submitted_date_ranges(normalized)
            expression, id_list = (
                _compile_expression(base) if base else (None, None)
            )
            date_expression = " AND ".join(date_clauses)
            expression = (
                f"{expression} AND {date_expression}"
                if expression
                else date_expression
            )
        else:
            expression, id_list = _compile_expression(normalized)
        return _expression_params(expression, id_list, max_results)
    matches = _structured_field_matches(normalized)
    fields: dict[str, str] = {}
    topic_parts: list[str] = []
    cursor = 0
    for index, match in enumerate(matches):
        topic_parts.append(_strip_quotes(normalized[cursor : match[1]]))
        limit = matches[index + 1][1] if index + 1 < len(matches) else len(normalized)
        value_end = _field_value_end(match[0], normalized, match[2], limit)
        value = _clean_field_value(normalized[match[2] : value_end])
        if value:
            fields[match[0]] = value
        cursor = value_end
    if matches:
        topic_parts.append(_strip_quotes(normalized[cursor:]))
    else:
        # 普通关键词没有结构化字段时仍然是有效的 arXiv all: 查询。
        topic_parts.append(_strip_quotes(normalized))
    topic = " ".join(part for part in topic_parts if part).strip()

    params = _base_params(max_results)
    if fields.get("id"):
        id_list = _normalize_id_list(fields["id"])
        params["id_list"] = id_list

    clauses = [f"all:{token}" for token in topic.split() if token]
    if fields.get("author"):
        clauses.append(f'au:"{_quote_value(fields["author"])}"')
    if fields.get("title"):
        clauses.append(f'ti:"{_quote_value(fields["title"])}"')
    if fields.get("year"):
        year_from, year_to = _parse_year_value(fields["year"])
        clauses.append(_submitted_date_clause(year_from, year_to))
    if clauses:
        params["search_query"] = " AND ".join(clauses)
    if "search_query" not in params and "id_list" not in params:
        raise _empty_query_error()
    return params


def _expression_params(
    expression: str | None, id_list: str | None, max_results: int
) -> dict[str, str]:
    params = _base_params(max_results)
    if id_list:
        params["id_list"] = id_list
    if expression:
        params["search_query"] = expression
    if "search_query" not in params and "id_list" not in params:
        raise _empty_query_error()
    return params


def _submitted_date_clause(year_from: str, year_to: str) -> str:
    return f"submittedDate:[{year_from}01010000 TO {year_to}12312359]"


def _extract_submitted_date_ranges(value: str) -> list[str]:
    if _UNSUPPORTED_DATE_OPERATOR.search(value) is not None:
        raise ArxivMcpError(
            "arxiv_request",
            "arXiv 日期范围暂只支持与 AND 组合，请调整检索表达后重试。",
            upstream_status="local_invariant",
            retryable=False,
        )
    clauses: list[str] = []
    for match in _SUBMITTED_DATE_RANGE.finditer(value):
        raw = " ".join(match.group(1).split())
        if _SUBMITTED_DATE_VALUE.fullmatch(raw) is None:
            raise ArxivMcpError(
                "arxiv_request",
                "arXiv 日期范围无效，请使用 YYYYMMDDHHMM TO YYYYMMDDHHMM。",
                upstream_status="local_invariant",
                retryable=False,
            )
        clauses.append(f"submittedDate:[{raw.upper()}]")
    return clauses


def _strip_submitted_date_ranges(value: str) -> str:
    stripped = _SUBMITTED_DATE_RANGE.sub(" ", value)
    stripped = re.sub(r"(?i)(?<!\S)(AND|OR|ANDNOT)\s*$", " ", stripped)
    stripped = re.sub(r"(?i)^\s*(AND|OR|ANDNOT)(?!\S)", " ", stripped)
    return " ".join(stripped.split())


def _base_params(max_results: int) -> dict[str, str]:
    return {
        "start": "0",
        "max_results": str(max_results),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }


def _empty_query_error() -> ArxivMcpError:
    return ArxivMcpError(
        "arxiv_request",
        "论文搜索主题不能为空，请补充领域或约束。",
        upstream_status="local_invariant",
        retryable=False,
    )


def _reject_control_characters(value: str) -> None:
    if _CONTROL_CHARACTER.search(value):
        raise ArxivMcpError(
            "arxiv_request",
            "论文搜索查询包含无效控制字符，请重新输入。",
            upstream_status="local_invariant",
            retryable=False,
        )


def _has_expression_syntax(value: str) -> bool:
    """检测引号外的 arXiv 字段表达式或布尔运算符。"""
    quote: str | None = None
    index = 0
    while index < len(value):
        character = value[index]
        if quote is not None:
            if character == quote:
                quote = None
            index += 1
            continue
        if character in _QUOTE_PAIRS and _can_start_quote(value, index):
            quote = _QUOTE_PAIRS[character]
            index += 1
            continue
        if index == 0 or value[index - 1].isspace() or value[index - 1] == "(":
            if _EXPRESSION_FIELD.match(value, index) is not None:
                return True
            if _BOOLEAN_OPERATOR.match(value, index) is not None:
                return True
        index += 1
    return False


class _ExpressionError(ValueError):
    """显式 arXiv 表达式无法安全解码。"""


def _compile_expression(value: str) -> tuple[str | None, str | None]:
    """把显式字段表达式编译为安全的 arXiv search_query。

    保留 ``all:"phrase" AND all:"phrase"`` 的短语与布尔结构；旧实现把
    ``all:`` 当普通词再次加前缀，构造出 ``all:all:`` 等非法查询。
    """
    output: list[str] = []
    pending_field: str | None = None
    previous_operand = False
    id_list: str | None = None
    try:
        tokens = _expression_tokens(value)
        for kind, text, quoted in tokens:
            if kind == "field":
                if pending_field is not None:
                    raise _ExpressionError("字段缺少取值")
                pending_field = text
                continue
            if kind == "value":
                field = pending_field or "all"
                pending_field = None
                if field == "id":
                    id_list = _normalize_id_list(text)
                    continue
                if field == "year":
                    year_from, year_to = _parse_year_value(text)
                    rendered = _submitted_date_clause(year_from, year_to)
                else:
                    rendered = _render_expression_value(
                        _EXPRESSION_FIELD_ALIASES.get(field, field), text, quoted
                    )
                if previous_operand:
                    output.append("AND")
                output.append(rendered)
                previous_operand = True
                continue
            if pending_field is not None:
                raise _ExpressionError("字段缺少取值")
            if kind == "boolean":
                if not previous_operand:
                    raise _ExpressionError("布尔运算符位置无效")
                output.append(text)
                previous_operand = False
                continue
            if kind == "paren":
                if text == "(":
                    if previous_operand:
                        output.append("AND")
                    output.append("(")
                    previous_operand = False
                else:
                    if not previous_operand:
                        raise _ExpressionError("括号不匹配")
                    output.append(")")
                    previous_operand = True
                continue
            if previous_operand:
                output.append("AND")
            output.append(f"all:{text.replace(chr(34), '')}")
            previous_operand = True
        if pending_field is not None:
            raise _ExpressionError("字段缺少取值")
        if output and not previous_operand:
            raise _ExpressionError("表达式不完整")
        expression = " ".join(output)
    except _ExpressionError as exc:
        raise ArxivMcpError(
            "arxiv_request",
            "论文搜索查询表达式无效，请调整检索表达后重试。",
            upstream_status="local_invariant",
            retryable=False,
        ) from exc
    if len(expression) > _MAX_EXPRESSION_LENGTH:
        raise ArxivMcpError(
            "arxiv_request",
            "论文搜索查询过长，请缩小检索范围后重试。",
            upstream_status="local_invariant",
            retryable=False,
        )
    if not expression and id_list is None:
        raise _empty_query_error()
    return expression or None, id_list


def _expression_tokens(value: str) -> list[tuple[str, str, bool]]:
    tokens: list[tuple[str, str, bool]] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character.isspace():
            index += 1
            continue
        if character in "()":
            tokens.append(("paren", character, False))
            index += 1
            continue
        if character in _QUOTE_PAIRS and _can_start_quote(value, index):
            closing = _QUOTE_PAIRS[character]
            end = value.find(closing, index + 1)
            if end == -1:
                raise _ExpressionError("引号未闭合")
            phrase = " ".join(value[index + 1 : end].split())
            if not phrase:
                raise _ExpressionError("空引号短语")
            tokens.append(("value", phrase, True))
            index = end + 1
            continue
        end = index
        while (
            end < len(value)
            and not value[end].isspace()
            and value[end] not in "()"
            and (
                value[end] not in _QUOTE_CHARS
                or not _can_start_quote(value, end)
            )
        ):
            end += 1
        chunk = value[index:end]
        index = end
        field_match = re.match(r"^([A-Za-z]+):(.*)$", chunk)
        if (
            field_match is not None
            and field_match.group(1).lower() in _EXPRESSION_FIELDS
        ):
            tokens.append(("field", field_match.group(1).lower(), False))
            if field_match.group(2):
                tokens.append(("value", field_match.group(2), False))
            continue
        if _BOOLEAN_OPERATOR.fullmatch(chunk) is not None:
            tokens.append(("boolean", chunk.upper(), False))
            continue
        tokens.append(("term", chunk, False))
    return _merge_bare_author_title(tokens)


def _merge_bare_author_title(
    tokens: list[tuple[str, str, bool]],
) -> list[tuple[str, str, bool]]:
    """``author:Ada Lovelace`` 这类裸写法合并到下一个字段/布尔/括号前。"""
    merged: list[tuple[str, str, bool]] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        merged.append(token)
        if token[0] == "field" and token[1] in {"author", "title"}:
            next_index = index + 1
            if (
                next_index < len(tokens)
                and tokens[next_index][0] in {"value", "term"}
                and not tokens[next_index][2]
            ):
                parts = [tokens[next_index][1]]
                next_index += 1
                while next_index < len(tokens) and tokens[next_index][0] == "term":
                    parts.append(tokens[next_index][1])
                    next_index += 1
                merged.append(("value", " ".join(parts), False))
                index = next_index
                continue
        index += 1
    return merged


def _render_expression_value(field: str, value: str, quoted: bool) -> str:
    cleaned = " ".join(value.replace('"', " ").split()).strip()
    if not cleaned:
        raise _ExpressionError("字段取值为空")
    if field in {"au", "ti"} or quoted:
        return f'{field}:"{cleaned}"'
    return f"{field}:{cleaned}"


def _structured_field_matches(
    normalized: str,
) -> list[tuple[str, int, int]]:
    """查找不在引号内的结构化字段，返回字段名、起点和取值起点。"""
    matches: list[tuple[str, int, int]] = []
    quote: str | None = None
    index = 0
    while index < len(normalized):
        character = normalized[index]
        if quote is not None:
            if character == quote:
                quote = None
            index += 1
            continue
        if (
            character in _QUOTE_PAIRS
            and _can_start_quote(normalized, index)
            and not (character in "'’" and index > 0 and normalized[index - 1].isalnum())
        ):
            quote = _QUOTE_PAIRS[character]
            index += 1
            continue
        if index == 0 or normalized[index - 1].isspace():
            match = _STRUCTURED_FIELD.match(normalized, index)
            if match is not None:
                matches.append((match.group(1).lower(), match.start(), match.end()))
                index = match.end()
                continue
        index += 1
    return matches


def _can_start_quote(value: str, index: int) -> bool:
    return index == 0 or value[index - 1].isspace() or value[index - 1] in ":(["


def _strip_quotes(value: str) -> str:
    return " ".join(value.translate(_QUOTE_TRANSLATION).split())


def _clean_field_value(value: str) -> str:
    cleaned = _strip_quotes(value)
    return cleaned.strip(" ,;，。；：:")


def _field_value_end(field: str, value: str, value_start: int, limit: int) -> int:
    remainder = value[value_start:limit]
    leading = len(remainder) - len(remainder.lstrip())
    if leading == len(remainder):
        return limit
    quote_char = remainder[leading]
    closing = {'"': '"', "'": "'", "“": "”", "‘": "’"}.get(quote_char)
    if closing is None:
        if field == "id":
            return limit
        if field == "year":
            token = re.match(r"\S+", remainder[leading:])
            if token is not None:
                return value_start + leading + token.end()
        return limit
    closing_offset = remainder.find(closing, leading + 1)
    if closing_offset != -1:
        return value_start + closing_offset + 1
    if field in {"id", "year"}:
        token = re.match(r"\S+", remainder[leading:])
        if token is not None:
            return value_start + leading + token.end()
    return limit


def _quote_value(value: str) -> str:
    return " ".join(value.replace('"', " ").split()).strip()


def _normalize_id_list(value: str) -> str:
    identifiers = [item.strip() for item in value.split(",")]
    if not identifiers or any(
        not item or not _QUERY_ARXIV_ID.fullmatch(item) for item in identifiers
    ):
        raise ArxivMcpError(
            "arxiv_request",
            "arXiv 标识符无效，请检查 id 约束后重试。",
            upstream_status="local_invariant",
            retryable=False,
        )
    return ",".join(identifiers)


def _parse_year_value(value: str) -> tuple[str, str]:
    match = _YEAR_VALUE.fullmatch(value)
    if match is None:
        raise ArxivMcpError(
            "arxiv_request",
            "年份约束无效，请使用 YYYY 或 YYYY-YYYY。",
            upstream_status="local_invariant",
            retryable=False,
        )
    year_from = int(match.group(1))
    year_to = int(match.group(2) or match.group(1))
    if not 1900 <= year_from <= year_to <= 2100:
        raise ArxivMcpError(
            "arxiv_request",
            "年份约束无效，请使用 1900 年以后的有效范围。",
            upstream_status="local_invariant",
            retryable=False,
        )
    return str(year_from), str(year_to)
