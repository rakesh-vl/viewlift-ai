"""Detect GraphQL calls on a captured flow. No I/O."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl

from viewlift_ai.models.flow import FlowRecord

_OPERATION = re.compile(
    r"\b(query|mutation|subscription)\b(?:\s+([_A-Za-z][_0-9A-Za-z]*))?"
)


@dataclass(frozen=True, slots=True)
class GraphQLMeta:
    """graphql is false for ordinary HTTP. operation is null in that case."""

    graphql: bool
    operation: str | None
    operation_type: str | None


def graphql_meta(flow: FlowRecord) -> GraphQLMeta:
    """Classify one flow. Batched calls use the first operation name plus +N."""
    parsed = _json_body(flow.request_body)
    objects = _graphql_objects(parsed)
    document, query_name = _query_document(flow)
    raw_document = "application/graphql" in {_mime(flow.content_type), _request_mime(flow)}
    path_hit = "/graphql" in (flow.path or "").lower()
    body_text = _body_text(flow.request_body)
    body_document = bool(body_text) and _is_document(body_text) and (raw_document or path_hit)

    if not objects and document is None and not body_document and not path_hit:
        return GraphQLMeta(False, None, None)

    names: list[str] = []
    operation_type: str | None = None
    if objects:
        for obj in objects:
            explicit = obj.get("operationName")
            explicit_name = explicit.strip() if isinstance(explicit, str) else ""
            doc = obj.get("query") if isinstance(obj.get("query"), str) else ""
            doc_name, doc_type = _operation_from_document(doc)
            if operation_type is None:
                operation_type = doc_type
            names.append(explicit_name or doc_name or "anonymous")
    elif body_document and body_text is not None:
        doc_name, operation_type = _operation_from_document(body_text)
        names.append(doc_name or "anonymous")
    elif document is not None:
        doc_name, operation_type = _operation_from_document(document)
        names.append(query_name or doc_name or "anonymous")
    else:
        names.append("anonymous")
    return GraphQLMeta(True, _label(names), operation_type)


def _graphql_objects(parsed: object) -> list[dict[str, object]]:
    if isinstance(parsed, dict):
        found = _graphql_object(parsed)
        return [found] if found is not None else []
    if isinstance(parsed, list):
        found = [_graphql_object(item) for item in parsed]
        return [item for item in found if item is not None]
    return []


def _graphql_object(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    extensions = value.get("extensions")
    if isinstance(extensions, dict) and isinstance(extensions.get("persistedQuery"), dict):
        return value
    query = value.get("query")
    if isinstance(query, str) and ("operationName" in value or "variables" in value):
        return value
    return None


def _label(names: list[str]) -> str:
    if len(names) <= 1:
        return names[0] if names else "anonymous"
    return f"{names[0]}+{len(names) - 1}"


def _json_body(body: bytes) -> object | None:
    text = _body_text(body)
    if not text:
        return None
    stripped = text.lstrip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _body_text(body: bytes) -> str | None:
    if not body:
        return None
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _query_document(flow: FlowRecord) -> tuple[str | None, str | None]:
    if not flow.query:
        return None, None
    document: str | None = None
    name: str | None = None
    for key, value in parse_qsl(flow.query, keep_blank_values=True):
        if key == "query" and _is_document(value):
            document = value
        elif key == "operationName" and value.strip():
            name = value.strip()
    if document is None:
        return None, None
    return document, name


def _request_mime(flow: FlowRecord) -> str:
    for key, value in flow.request_headers.items():
        if key.lower() == "content-type":
            return _mime(value)
    return ""


def _mime(value: str | None) -> str:
    if not value:
        return ""
    return value.split(";", 1)[0].strip().lower()


def _is_document(text: str) -> bool:
    head = _leading(text)
    if not head:
        return False
    if head[0] == "{":
        return True
    return _OPERATION.match(head) is not None


def _operation_from_document(text: str) -> tuple[str | None, str | None]:
    if not text:
        return None, None
    cleaned = _without_strings_and_comments(text)
    match = _OPERATION.search(cleaned)
    if match:
        return match.group(2), match.group(1)
    if _leading(cleaned).startswith("{"):
        return None, "query"
    return None, None


def _leading(text: str) -> str:
    index = 0
    size = len(text)
    while index < size:
        while index < size and text[index].isspace():
            index += 1
        if index < size and text[index] == "#":
            newline = text.find("\n", index)
            index = size if newline < 0 else newline + 1
            continue
        break
    return text[index:]


def _without_strings_and_comments(text: str) -> str:
    out: list[str] = []
    index = 0
    size = len(text)
    while index < size:
        if text.startswith('"""', index):
            end = text.find('"""', index + 3)
            index = size if end < 0 else end + 3
            out.append(" ")
            continue
        char = text[index]
        if char == '"':
            index += 1
            while index < size and text[index] != '"':
                index += 2 if text[index] == "\\" else 1
            index = min(index + 1, size)
            out.append(" ")
            continue
        if char == "#":
            newline = text.find("\n", index)
            index = size if newline < 0 else newline + 1
            out.append(" ")
            continue
        out.append(char)
        index += 1
    return "".join(out)
