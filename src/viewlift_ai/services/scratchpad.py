"""Scratchpad scripts: built-ins plus user scripts stored as mode-0600 files."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from viewlift_ai.services.scratchpad_builtin import BUILTIN_SCRIPTS

MAX_SOURCE_CHARS = 64_000
MAX_USER_SCRIPTS = 200
_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAIN = re.compile(r"function\s+main\s*\(")


class ScratchpadError(Exception):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class ScriptRecord:
    id: str
    name: str
    description: str
    author: str
    tags: tuple[str, ...]
    builtin: bool
    source: str
    error: str | None = None

    def public(self, *, include_source: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "author": self.author,
            "tags": list(self.tags),
            "builtin": self.builtin,
        }
        if self.error:
            payload["error"] = self.error
        if include_source:
            payload["source"] = self.source
        return payload


@dataclass(frozen=True, slots=True)
class _Compiled:
    name: str
    description: str
    author: str
    tags: tuple[str, ...]
    body: str


class ScratchpadLibrary:
    """Load built-in scripts and user scripts under the app data directory."""

    def __init__(self, user_dir: Path) -> None:
        self._user_dir = user_dir
        self._lock = threading.RLock()

    @property
    def user_dir(self) -> Path:
        return self._user_dir

    def list_scripts(self) -> list[ScriptRecord]:
        with self._lock:
            user = self._read_user_scripts()
            shadowed = {item.id for item in user}
            builtin = [item for item in self._builtins() if item.id not in shadowed]
        builtin.sort(key=lambda item: item.name.casefold())
        user.sort(key=lambda item: item.name.casefold())
        return builtin + user

    def get(self, script_id: str) -> ScriptRecord:
        self._require_id(script_id)
        with self._lock:
            user = self._read_user(script_id)
            if user is not None:
                return user
            for item in self._builtins():
                if item.id == script_id:
                    return item
        raise ScratchpadError("Script not found.", status=404)

    def create(self, source: str) -> ScriptRecord:
        compiled = compile_source(source)
        with self._lock:
            existing = self._read_user_scripts()
            if len(existing) >= MAX_USER_SCRIPTS:
                raise ScratchpadError(f"At most {MAX_USER_SCRIPTS} saved scripts.")
            taken = {item.id for item in self._builtins()} | {item.id for item in existing}
            script_id = _allocate_id(compiled.name, taken)
            text = render_source(compiled)
            self._write(self._user_path(script_id), text)
        return _record(script_id, compiled, source=text, builtin=False)

    def update(self, script_id: str, source: str) -> ScriptRecord:
        self._require_id(script_id)
        if any(item.id == script_id for item in self._builtins()):
            user_path = self._user_path(script_id)
            if not user_path.is_file():
                raise ScratchpadError(
                    "Built-in scripts stay as they are. Duplicate one, then change the copy."
                )
        compiled = compile_source(source)
        with self._lock:
            path = self._user_path(script_id)
            if not path.is_file():
                raise ScratchpadError("Script not found.", status=404)
            text = render_source(compiled)
            self._write(path, text)
        return _record(script_id, compiled, source=text, builtin=False)

    def delete(self, script_id: str) -> None:
        self._require_id(script_id)
        with self._lock:
            path = self._user_path(script_id)
            if path.is_file():
                path.unlink()
                return
        if any(item.id == script_id for item in self._builtins()):
            raise ScratchpadError(
                "Built-in scripts stay as they are. Duplicate one, then change the copy."
            )
        raise ScratchpadError("Script not found.", status=404)

    def _builtins(self) -> list[ScriptRecord]:
        records: list[ScriptRecord] = []
        for script_id, source in BUILTIN_SCRIPTS:
            self._require_id(script_id)
            try:
                compiled = compile_source(source)
            except ScratchpadError as exc:
                records.append(
                    ScriptRecord(
                        id=script_id,
                        name=script_id,
                        description="",
                        author="Viewlift AI",
                        tags=(),
                        builtin=True,
                        source=source,
                        error=str(exc),
                    )
                )
                continue
            records.append(_record(script_id, compiled, source=source, builtin=True))
        return records

    def _read_user_scripts(self) -> list[ScriptRecord]:
        if not self._user_dir.is_dir():
            return []
        records: list[ScriptRecord] = []
        for path in sorted(self._user_dir.glob("*.js")):
            if not _ID.fullmatch(path.stem):
                continue
            record = self._read_user(path.stem)
            if record is not None:
                records.append(record)
        return records

    def _read_user(self, script_id: str) -> ScriptRecord | None:
        path = self._user_path(script_id)
        if not path.is_file():
            return None
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return ScriptRecord(
                id=script_id,
                name=script_id,
                description="",
                author="",
                tags=(),
                builtin=False,
                source="",
                error="Could not read this script.",
            )
        try:
            compiled = compile_source(source)
        except ScratchpadError as exc:
            return ScriptRecord(
                id=script_id,
                name=script_id,
                description="",
                author="",
                tags=(),
                builtin=False,
                source=source,
                error=str(exc),
            )
        return _record(script_id, compiled, source=source, builtin=False)

    def _user_path(self, script_id: str) -> Path:
        self._require_id(script_id)
        path = (self._user_dir / f"{script_id}.js").resolve()
        root = self._user_dir.resolve()
        if path.parent != root:
            raise ScratchpadError("Invalid script id.")
        return path

    def _write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".scratch-", suffix=".tmp", dir=str(path.parent))
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, path)
            os.chmod(path, 0o600)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _require_id(script_id: str) -> None:
        if not isinstance(script_id, str) or not _ID.fullmatch(script_id) or len(script_id) > 64:
            raise ScratchpadError("Invalid script id.")


def compile_source(source: str) -> _Compiled:
    if not isinstance(source, str) or not source.strip():
        raise ScratchpadError("source is required.")
    if "\x00" in source:
        raise ScratchpadError("Script contains a null byte.")
    if len(source) > MAX_SOURCE_CHARS:
        raise ScratchpadError("Script is larger than 64 KB.")
    raw_header, body = _split_header(source)
    try:
        meta = json.loads(raw_header)
    except json.JSONDecodeError as exc:
        raise ScratchpadError("Script header is not valid JSON.") from exc
    if not isinstance(meta, dict):
        raise ScratchpadError("Script header must be a JSON object.")
    api = meta.get("api", 1)
    if api != 1:
        raise ScratchpadError("Only script api version 1 is supported.")
    if not body.strip() or _MAIN.search(body) is None:
        raise ScratchpadError("Script needs function main(state).")
    return _Compiled(
        name=_clean_line(meta.get("name"), field="Name", limit=80, required=True),
        description=_clean_line(meta.get("description", ""), field="Description", limit=280),
        author=_clean_line(meta.get("author", ""), field="Author", limit=60) or "you",
        tags=_tags(meta.get("tags", "")),
        body=body.strip() + "\n",
    )


def render_source(compiled: _Compiled) -> str:
    payload = {
        "api": 1,
        "name": compiled.name,
        "description": compiled.description,
        "author": compiled.author,
        "tags": ",".join(compiled.tags),
    }
    header = json.dumps(payload, indent=2, ensure_ascii=False)
    return f"/**\n{header}\n**/\n\n{compiled.body}"


def _record(script_id: str, compiled: _Compiled, *, source: str, builtin: bool) -> ScriptRecord:
    return ScriptRecord(
        id=script_id,
        name=compiled.name,
        description=compiled.description,
        author=compiled.author,
        tags=compiled.tags,
        builtin=builtin,
        source=source,
    )


def _split_header(source: str) -> tuple[str, str]:
    text = source.lstrip("\ufeff").lstrip()
    if not text.startswith("/**"):
        raise ScratchpadError(
            "Script needs a header comment. Start with /** { JSON } **/ then function main(state)."
        )
    end = text.find("**/")
    if end < 0:
        raise ScratchpadError("Header comment is not closed.")
    raw = text[3:end].strip()
    body = text[end + 3 :]
    if body.startswith("\r\n"):
        body = body[2:]
    elif body.startswith("\n"):
        body = body[1:]
    if not raw:
        raise ScratchpadError("Script header is empty.")
    return raw, body


def _clean_line(value: object, *, field: str, limit: int, required: bool = False) -> str:
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = " ".join(value.split())
    else:
        raise ScratchpadError(f"{field} must be a string.")
    if required and not text:
        raise ScratchpadError(f"{field} is required.")
    if len(text) > limit:
        raise ScratchpadError(f"{field} must be {limit} characters or fewer.")
    return text


def _tags(value: object) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
    elif isinstance(value, list):
        parts = []
        for item in value:
            if not isinstance(item, str):
                raise ScratchpadError("Tags must be strings.")
            parts.append(item.strip())
    else:
        raise ScratchpadError("Tags must be a string or a list of strings.")
    cleaned: list[str] = []
    for part in parts:
        if not part:
            continue
        if len(part) > 24:
            raise ScratchpadError("Each tag must be 24 characters or fewer.")
        if any(char.isspace() for char in part):
            raise ScratchpadError("Tags cannot contain spaces. Separate them with commas.")
        if part not in cleaned:
            cleaned.append(part)
        if len(cleaned) > 8:
            raise ScratchpadError("Use 8 tags or fewer.")
    return tuple(cleaned)


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if len(slug) > 48:
        slug = slug[:48].rstrip("-")
    return slug or "script"


def _allocate_id(name: str, taken: set[str]) -> str:
    base = _slug(name)
    if base not in taken:
        return base
    for number in range(2, 500):
        candidate = f"{base}-{number}"
        if len(candidate) > 64:
            candidate = f"{base[:40].rstrip('-')}-{number}"
        if candidate not in taken and _ID.fullmatch(candidate):
            return candidate
    raise ScratchpadError("Could not assign a script id.")
