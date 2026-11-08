"""Apple Developer Documentation: framework list, symbol search, and page reader.

Uses the public JSON that developer.apple.com serves to its own doc viewer. Responses are cached
on disk for a week so repeated searches stay fast and offline-friendly.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib import error as urlerror
from urllib import request as urlrequest

from viewlift_ai.services.host.runner import HostError

if TYPE_CHECKING:
    from viewlift_ai.services.host.toolkit import HostToolkit

BASE = "https://developer.apple.com"
CACHE_TTL = 7 * 24 * 3600
DEFAULT_FRAMEWORKS = ("swiftui", "uikit", "foundation")
MAX_FETCH_BYTES = 12_000_000
_SLUG_RE = re.compile(r"^[a-z0-9_.-]{1,80}$")
_PATH_RE = re.compile(r"^/documentation/[A-Za-z0-9_/().:,~%=+-]{1,400}$")
_SYMBOL_TYPES = frozenset(
    {
        "struct",
        "class",
        "protocol",
        "enum",
        "func",
        "method",
        "property",
        "init",
        "case",
        "typealias",
        "macro",
        "var",
        "let",
        "op",
        "subscript",
        "associatedtype",
    }
)

Fetcher = Any


def _default_fetch(url: str) -> bytes:
    req = urlrequest.Request(
        url, headers={"User-Agent": "ViewliftAI-docs/1.0", "Accept": "application/json"}
    )
    try:
        with urlrequest.urlopen(req, timeout=25) as response:
            return response.read(MAX_FETCH_BYTES)
    except urlerror.HTTPError as exc:
        if exc.code == 404:
            raise HostError("Apple documentation page not found.") from exc
        raise HostError(f"developer.apple.com returned {exc.code}.") from exc
    except urlerror.URLError as exc:
        raise HostError("developer.apple.com is not reachable.") from exc


class AppleDocs:
    def __init__(self, cache_dir: Path, fetch: Fetcher = _default_fetch) -> None:
        self._cache_dir = cache_dir
        self._fetch = fetch
        self._memory: dict[str, Any] = {}
        self._lock = threading.Lock()

    def _get_json(self, path: str) -> Any:
        url = f"{BASE}{path}"
        with self._lock:
            if url in self._memory:
                return self._memory[url]
        key = hashlib.sha256(url.encode()).hexdigest()[:32]
        cache = self._cache_dir / f"{key}.json"
        data: Any = None
        if cache.is_file() and time.time() - cache.stat().st_mtime < CACHE_TTL:
            try:
                data = json.loads(cache.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = None
        if data is None:
            raw = self._fetch(url)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise HostError("Apple documentation response was not JSON.") from exc
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data), encoding="utf-8")
        with self._lock:
            if len(self._memory) > 12:
                self._memory.clear()
            self._memory[url] = data
        return data

    def frameworks(self, args: dict[str, Any]) -> dict[str, Any]:
        data = self._get_json("/tutorials/data/documentation/technologies.json")
        wanted = str(args.get("filter") or "").lower()
        rows = []
        for section in data.get("sections", []):
            for group in section.get("groups", []):
                for tech in group.get("technologies", []):
                    ident = str((tech.get("destination") or {}).get("identifier", ""))
                    slug = ident.rsplit("/", 1)[-1].lower()
                    title = str(tech.get("title", ""))
                    tags = tech.get("tags", [])
                    if wanted and wanted not in title.lower() and wanted not in slug:
                        continue
                    rows.append({"title": title, "framework": slug, "tags": tags})
        unique = {row["framework"]: row for row in rows}
        return {
            "frameworks": sorted(unique.values(), key=lambda row: row["title"]),
            "count": len(unique),
        }

    def _index(self, framework: str) -> list[dict[str, str]]:
        if not _SLUG_RE.match(framework):
            raise HostError("framework is a slug such as swiftui or avfoundation.")
        data = self._get_json(f"/tutorials/data/index/{framework}")
        nodes = (
            (data.get("interfaceLanguages") or {}).get("swift")
            or (data.get("interfaceLanguages") or {}).get("occ")
            or []
        )
        out: list[dict[str, str]] = []

        def walk(items: list[Any], parent: str) -> None:
            for item in items:
                if not isinstance(item, dict):
                    continue
                kind = str(item.get("type", ""))
                title = str(item.get("title", ""))
                path = str(item.get("path", ""))
                if path and kind != "groupMarker" and not item.get("external"):
                    out.append({"title": title, "path": path, "type": kind, "parent": parent})
                walk(item.get("children") or [], title if path else parent)

        walk(nodes, "")
        return out

    def search(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query or len(query) > 120:
            raise HostError("query is required (try GridItem, Lazy*, or 'tab view').")
        raw = args.get("frameworks") or args.get("framework") or list(DEFAULT_FRAMEWORKS)
        frameworks = [raw] if isinstance(raw, str) else [str(item) for item in raw][:6]
        limit = max(1, min(int(args.get("limit", 20)), 60))
        lowered = query.lower()
        wildcard = any(ch in query for ch in "*?")
        words = [part for part in lowered.split() if part]
        symbols: list[tuple[int, dict[str, str]]] = []
        articles: list[tuple[int, dict[str, str]]] = []
        errors = []
        for framework in frameworks:
            try:
                entries = self._index(framework.lower())
            except HostError as exc:
                errors.append({"framework": framework, "detail": str(exc)})
                continue
            for entry in entries:
                title = entry["title"].lower()
                bare = title.split("(", 1)[0]
                if wildcard:
                    if not fnmatch.fnmatchcase(bare, lowered):
                        continue
                    score = 2
                elif bare == lowered:
                    score = 0
                elif bare.startswith(lowered):
                    score = 1
                elif all(word in f"{title} {entry['parent'].lower()}" for word in words):
                    score = 3
                else:
                    continue
                row = {**entry, "framework": framework}
                bucket = symbols if entry["type"] in _SYMBOL_TYPES else articles
                bucket.append((score + len(title) / 1000, row))
        symbols.sort(key=lambda item: item[0])
        articles.sort(key=lambda item: item[0])
        return {
            "query": query,
            "frameworks": frameworks,
            "symbols": [row for _score, row in symbols[:limit]],
            "articles": [row for _score, row in articles[: max(5, limit // 3)]],
            "errors": errors,
            "next": "Call apple_docs_get with a path to read one page.",
        }

    def get(self, args: dict[str, Any]) -> dict[str, Any]:
        path = str(args.get("path") or "").strip()
        if path.startswith(BASE):
            path = path[len(BASE) :]
        path = path.split("#", 1)[0].rstrip("/")
        if not _PATH_RE.match(path):
            raise HostError("path looks like /documentation/swiftui/griditem.")
        data = self._get_json(f"/tutorials/data{path.lower()}.json")
        refs = data.get("references") or {}
        meta = data.get("metadata") or {}
        out: dict[str, Any] = {
            "title": meta.get("title"),
            "kind": meta.get("roleHeading") or meta.get("role"),
            "framework": ", ".join(item.get("name", "") for item in meta.get("modules", [])),
            "platforms": [
                f"{item.get('name')} {item.get('introducedAt', '')}".strip()
                + (" (deprecated)" if item.get("deprecated") else "")
                for item in meta.get("platforms", [])
            ],
            "abstract": _inline(data.get("abstract"), refs),
            "url": f"{BASE}{path}",
        }
        body: list[str] = []
        for section in data.get("primaryContentSections", []):
            if section.get("kind") == "declarations":
                for decl in section.get("declarations", []):
                    out["declaration"] = "".join(
                        token.get("text", "") for token in decl.get("tokens", [])
                    )
            elif section.get("kind") == "parameters":
                out["parameters"] = [
                    {"name": item.get("name"), "about": _blocks(item.get("content"), refs)}
                    for item in section.get("parameters", [])
                ]
            elif section.get("kind") == "content":
                body.append(_blocks(section.get("content"), refs))
        text = "\n\n".join(part for part in body if part)
        max_chars = max(1000, min(int(args.get("maxChars", 8000)), 30_000))
        out["overview"] = text[:max_chars] + ("…" if len(text) > max_chars else "")
        topics = []
        for section in data.get("topicSections", [])[:20]:
            items = []
            for ident in section.get("identifiers", [])[:25]:
                ref = refs.get(ident) or {}
                items.append(
                    {"title": ref.get("title"), "path": ref.get("url"), "kind": ref.get("kind")}
                )
            topics.append({"title": section.get("title"), "items": items})
        out["topics"] = topics
        see_also = []
        for section in data.get("seeAlsoSections", [])[:3]:
            for ident in section.get("identifiers", [])[:8]:
                ref = refs.get(ident) or {}
                see_also.append({"title": ref.get("title"), "path": ref.get("url")})
        out["see_also"] = see_also
        return out


def _inline(items: Any, refs: dict[str, Any]) -> str:
    parts = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text":
            parts.append(str(item.get("text", "")))
        elif kind == "codeVoice":
            parts.append(f"`{item.get('code', '')}`")
        elif kind == "reference":
            ref = refs.get(item.get("identifier"), {})
            parts.append(f"`{ref.get('title', '')}`")
        elif kind in {"emphasis", "strong", "newTerm", "inlineHead"}:
            parts.append(_inline(item.get("inlineContent"), refs))
        elif kind == "link":
            parts.append(str(item.get("title") or item.get("destination") or ""))
    return "".join(parts)


def _blocks(items: Any, refs: dict[str, Any]) -> str:
    out = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "heading":
            out.append(f"## {item.get('text', '')}")
        elif kind == "paragraph":
            out.append(_inline(item.get("inlineContent"), refs))
        elif kind == "codeListing":
            out.append("```swift\n" + "\n".join(item.get("code", [])) + "\n```")
        elif kind in {"unorderedList", "orderedList"}:
            for entry in item.get("items", []):
                out.append(f"- {_blocks(entry.get('content'), refs)}")
        elif kind == "aside":
            title = item.get("name") or item.get("style", "Note")
            out.append(f"> {title}: {_blocks(item.get('content'), refs)}")
    return "\n".join(part for part in out if part)


def register(toolkit: HostToolkit) -> None:
    docs = AppleDocs(toolkit.ctx.work_dir / "apple_docs")
    toolkit.apple_docs = docs  # type: ignore[attr-defined]
    toolkit.add("apple_docs_list_frameworks", docs.frameworks)
    toolkit.add("apple_docs_search", docs.search)
    toolkit.add("apple_docs_get", docs.get)
