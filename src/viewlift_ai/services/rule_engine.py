"""SOLID rule engine: match, map local/remote, rewrite, throttle (no FastAPI/mitm)."""

from __future__ import annotations

import fnmatch
import mimetypes
import re
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar
from urllib.parse import urljoin, urlparse, urlunparse

from viewlift_ai.models.rules import (
    BreakpointRule,
    BreakpointStage,
    MapLocalResult,
    MapLocalRule,
    MapRemoteResult,
    MapRemoteRule,
    RequestMatchContext,
    RewriteAction,
    RewriteActionKind,
    RewriteResult,
    RewriteRule,
    RuleMatch,
    RulesConfig,
    ThrottleEffect,
    ThrottleRule,
    match_to_dict,
)
from viewlift_ai.services.rule_store import (
    RuleConfigStore,
    config_from_dict,
    config_to_dict,
)

# Safe numeric bounds (mutating traffic control).
MAX_LATENCY_MS = 30_000
MAX_KBPS = 100_000
MIN_KBPS = 1
MAX_REPLACEMENT_CHARS = 256 * 1024
MAX_RESULT_BODY_BYTES = 1 * 1024 * 1024
MAX_STATUS = 599
MIN_STATUS = 100

T = TypeVar("T")


class PathJailError(ValueError):
    """Relative path escapes the configured map-local root."""


class RuleValidationError(ValueError):
    """Invalid rule payload from API or store."""


def matches_globs(match: RuleMatch, ctx: RequestMatchContext) -> bool:
    method_pat = (match.method or "*").strip() or "*"
    host_pat = (match.host or "*").strip() or "*"
    path_pat = (match.path or "*").strip() or "*"

    method = (ctx.method or "").strip().upper()
    host = (ctx.host or "").strip().lower().rstrip(".")
    path = ctx.path or "/"
    if not path.startswith("/"):
        path = "/" + path

    if method_pat != "*" and method_pat.upper() != method:
        if not fnmatch.fnmatchcase(method, method_pat.upper()):
            return False

    host_pat_norm = host_pat.lower().rstrip(".")
    if host_pat_norm != "*" and not _fnmatch_ci(host, host_pat_norm):
        return False

    if path_pat != "*" and not fnmatch.fnmatchcase(path, path_pat):
        return False
    return True


def _fnmatch_ci(value: str, pattern: str) -> bool:
    if pattern == value:
        return True
    return fnmatch.fnmatchcase(value, pattern)


def resolve_map_local_path(root: Path, relative_path: str) -> Path:
    """Resolve a relative path under root; reject absolute/traversal/symlink escape."""
    if not relative_path or not str(relative_path).strip():
        raise PathJailError("empty relative path")
    rel = str(relative_path).strip().replace("\\", "/")
    if rel.startswith("/") or rel.startswith("~"):
        raise PathJailError("absolute paths are not allowed")
    if ":" in rel.split("/", 1)[0] and len(rel.split("/", 1)[0]) == 2:
        # Windows drive-style absolute
        raise PathJailError("absolute paths are not allowed")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise PathJailError("path traversal is not allowed")
    if not root or not str(root).strip():
        raise PathJailError("map local root is not configured")

    root_resolved = root.resolve(strict=False)
    candidate = (root_resolved.joinpath(*parts)).resolve(strict=False)
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise PathJailError("path escapes map local root") from exc
    return candidate


def clamp_latency_ms(value: int) -> int:
    return max(0, min(int(value), MAX_LATENCY_MS))


def clamp_kbps(value: int | None) -> int | None:
    if value is None:
        return None
    return max(MIN_KBPS, min(int(value), MAX_KBPS))


def validate_http_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise RuleValidationError("map remote target must use http or https")
    if not parsed.netloc:
        raise RuleValidationError("map remote target must include a host")
    if parsed.username or parsed.password:
        raise RuleValidationError("map remote target must not include credentials")
    return url.strip()


def build_map_remote_url(
    *,
    original_url: str,
    target_url: str,
    preserve_path: bool,
    preserve_query: bool,
) -> str:
    target = validate_http_url(target_url)
    target_parsed = urlparse(target)
    original = urlparse(original_url)

    if preserve_path:
        base_path = target_parsed.path or ""
        orig_path = original.path or "/"
        if base_path in ("", "/"):
            new_path = orig_path
        elif base_path.endswith("/"):
            new_path = urljoin(base_path, orig_path.lstrip("/"))
        else:
            # Target path is a prefix replacement base; append original path.
            new_path = base_path.rstrip("/") + orig_path
    else:
        new_path = target_parsed.path or "/"

    if preserve_query:
        query = original.query or target_parsed.query
    else:
        query = target_parsed.query

    return urlunparse(
        (
            target_parsed.scheme,
            target_parsed.netloc,
            new_path or "/",
            "",
            query,
            "",
        )
    )


def apply_rewrite_actions(
    *,
    status: int,
    headers: dict[str, str],
    body: bytes,
    actions: list[RewriteAction],
) -> RewriteResult:
    new_status = int(status)
    new_headers = dict(headers)
    new_body = body
    try:
        for action in actions:
            if action.kind == RewriteActionKind.SET_HEADER:
                name = (action.header_name or "").strip()
                if not name:
                    continue
                new_headers[name] = action.header_value or ""
            elif action.kind == RewriteActionKind.REMOVE_HEADER:
                name = (action.header_name or "").strip().lower()
                if not name:
                    continue
                for key in list(new_headers):
                    if key.lower() == name:
                        del new_headers[key]
            elif action.kind == RewriteActionKind.SET_STATUS:
                if action.status is None:
                    continue
                code = int(action.status)
                if MIN_STATUS <= code <= MAX_STATUS:
                    new_status = code
            elif action.kind == RewriteActionKind.REPLACE_BODY_LITERAL:
                find = action.find or ""
                replace = action.replace or ""
                if len(replace) > MAX_REPLACEMENT_CHARS:
                    return RewriteResult(
                        status=status,
                        headers=headers,
                        body=body,
                        error="replacement exceeds size cap",
                    )
                text = new_body.decode("utf-8", errors="replace")
                text = text.replace(find, replace)
                encoded = text.encode("utf-8")
                if len(encoded) > MAX_RESULT_BODY_BYTES:
                    return RewriteResult(
                        status=status,
                        headers=headers,
                        body=body,
                        error="result body exceeds size cap",
                    )
                new_body = encoded
            elif action.kind == RewriteActionKind.REPLACE_BODY_REGEX:
                find = action.find or ""
                replace = action.replace or ""
                if len(replace) > MAX_REPLACEMENT_CHARS:
                    return RewriteResult(
                        status=status,
                        headers=headers,
                        body=body,
                        error="replacement exceeds size cap",
                    )
                try:
                    pattern = re.compile(find)
                except re.error as exc:
                    return RewriteResult(
                        status=status,
                        headers=headers,
                        body=body,
                        error=f"invalid regex: {exc}",
                    )
                text = new_body.decode("utf-8", errors="replace")
                try:
                    text = pattern.sub(replace, text)
                except re.error as exc:
                    return RewriteResult(
                        status=status,
                        headers=headers,
                        body=body,
                        error=f"regex replacement failed: {exc}",
                    )
                encoded = text.encode("utf-8")
                if len(encoded) > MAX_RESULT_BODY_BYTES:
                    return RewriteResult(
                        status=status,
                        headers=headers,
                        body=body,
                        error="result body exceeds size cap",
                    )
                new_body = encoded
    except Exception as exc:  # noqa: BLE001 — safe boundary for live traffic
        return RewriteResult(
            status=status,
            headers=headers,
            body=body,
            error=f"rewrite failed: {exc}",
        )
    return RewriteResult(status=new_status, headers=new_headers, body=new_body)


def throttle_delay_seconds(*, size_bytes: int, kbps: int | None) -> float:
    """Compute asyncio-friendly delay for bandwidth shaping."""
    rate = clamp_kbps(kbps)
    if rate is None or size_bytes <= 0:
        return 0.0
    # kbps → bytes/sec
    bytes_per_sec = (rate * 1000) / 8.0
    if bytes_per_sec <= 0:
        return 0.0
    return float(size_bytes) / bytes_per_sec


class RuleEngine:
    """Orchestrates ordered rule evaluation. Defaults: all mutating rules off."""

    def __init__(self, store: RuleConfigStore) -> None:
        self._store = store
        self._lock = threading.RLock()
        self._config = store.load()
        self._listeners: list[Callable[[RulesConfig], None]] = []

    @property
    def config(self) -> RulesConfig:
        with self._lock:
            return self._clone(self._config)

    def subscribe(self, listener: Callable[[RulesConfig], None]) -> None:
        self._listeners.append(listener)

    def any_mutating_enabled(self) -> bool:
        with self._lock:
            return self._config.any_mutating_enabled()

    def reload(self) -> RulesConfig:
        with self._lock:
            self._config = self._store.load()
            cfg = self._clone(self._config)
        self._notify(cfg)
        return cfg

    def replace(self, config: RulesConfig) -> RulesConfig:
        normalized = self._normalize_config(config)
        with self._lock:
            self._store.save(normalized)
            self._config = normalized
            cfg = self._clone(self._config)
        self._notify(cfg)
        return cfg

    def set_allowlist(self, patterns: list[str]) -> RulesConfig:
        with self._lock:
            cleaned = [p.strip() for p in patterns if p and str(p).strip()]
            self._config.allowlist = cleaned
            cfg = self._commit()
        self._notify(cfg)
        return cfg

    def set_map_local_root(self, root: str) -> RulesConfig:
        with self._lock:
            self._config.map_local_root = str(root or "").strip()
            cfg = self._commit()
        self._notify(cfg)
        return cfg

    def list_rules(self) -> dict[str, object]:
        with self._lock:
            data = config_to_dict(self._config)
            data["mutating_enabled"] = self._config.any_mutating_enabled()
            return data

    def add_map_local(self, payload: dict) -> MapLocalRule:
        rule = self._parse_map_local(payload, rule_id=str(uuid.uuid4()))
        with self._lock:
            rule.order = self._next_order(self._config.map_local)
            self._config.map_local.append(rule)
            cfg = self._commit()
            out = self._clone(self._config).map_local[-1]
        self._notify(cfg)
        return out

    def update_map_local(self, rule_id: str, payload: dict) -> MapLocalRule:
        with self._lock:
            idx = self._index_by_id(self._config.map_local, rule_id)
            merged = self._parse_map_local(
                {**_map_local_as_dict(self._config.map_local[idx]), **payload},
                rule_id=rule_id,
            )
            self._config.map_local[idx] = merged
            self._config.map_local.sort(key=lambda r: r.order)
            cfg = self._commit()
            out = next(r for r in self._clone(self._config).map_local if r.id == rule_id)
        self._notify(cfg)
        return out

    def delete_map_local(self, rule_id: str) -> None:
        with self._lock:
            idx = self._index_by_id(self._config.map_local, rule_id)
            del self._config.map_local[idx]
            cfg = self._commit()
        self._notify(cfg)

    def toggle_map_local(self, rule_id: str, enabled: bool | None = None) -> MapLocalRule:
        with self._lock:
            idx = self._index_by_id(self._config.map_local, rule_id)
            rule = self._config.map_local[idx]
            rule.enabled = (not rule.enabled) if enabled is None else bool(enabled)
            cfg = self._commit()
            out = next(r for r in self._clone(self._config).map_local if r.id == rule_id)
        self._notify(cfg)
        return out

    def add_map_remote(self, payload: dict) -> MapRemoteRule:
        rule = self._parse_map_remote(payload, rule_id=str(uuid.uuid4()))
        with self._lock:
            rule.order = self._next_order(self._config.map_remote)
            self._config.map_remote.append(rule)
            cfg = self._commit()
            out = self._clone(self._config).map_remote[-1]
        self._notify(cfg)
        return out

    def update_map_remote(self, rule_id: str, payload: dict) -> MapRemoteRule:
        with self._lock:
            idx = self._index_by_id(self._config.map_remote, rule_id)
            merged = self._parse_map_remote(
                {**_map_remote_as_dict(self._config.map_remote[idx]), **payload},
                rule_id=rule_id,
            )
            self._config.map_remote[idx] = merged
            self._config.map_remote.sort(key=lambda r: r.order)
            cfg = self._commit()
            out = next(r for r in self._clone(self._config).map_remote if r.id == rule_id)
        self._notify(cfg)
        return out

    def delete_map_remote(self, rule_id: str) -> None:
        with self._lock:
            idx = self._index_by_id(self._config.map_remote, rule_id)
            del self._config.map_remote[idx]
            cfg = self._commit()
        self._notify(cfg)

    def toggle_map_remote(self, rule_id: str, enabled: bool | None = None) -> MapRemoteRule:
        with self._lock:
            idx = self._index_by_id(self._config.map_remote, rule_id)
            rule = self._config.map_remote[idx]
            rule.enabled = (not rule.enabled) if enabled is None else bool(enabled)
            cfg = self._commit()
            out = next(r for r in self._clone(self._config).map_remote if r.id == rule_id)
        self._notify(cfg)
        return out

    def add_rewrite(self, payload: dict) -> RewriteRule:
        rule = self._parse_rewrite(payload, rule_id=str(uuid.uuid4()))
        with self._lock:
            rule.order = self._next_order(self._config.rewrite)
            self._config.rewrite.append(rule)
            cfg = self._commit()
            out = self._clone(self._config).rewrite[-1]
        self._notify(cfg)
        return out

    def update_rewrite(self, rule_id: str, payload: dict) -> RewriteRule:
        with self._lock:
            idx = self._index_by_id(self._config.rewrite, rule_id)
            merged = self._parse_rewrite(
                {**_rewrite_as_dict(self._config.rewrite[idx]), **payload},
                rule_id=rule_id,
            )
            self._config.rewrite[idx] = merged
            self._config.rewrite.sort(key=lambda r: r.order)
            cfg = self._commit()
            out = next(r for r in self._clone(self._config).rewrite if r.id == rule_id)
        self._notify(cfg)
        return out

    def delete_rewrite(self, rule_id: str) -> None:
        with self._lock:
            idx = self._index_by_id(self._config.rewrite, rule_id)
            del self._config.rewrite[idx]
            cfg = self._commit()
        self._notify(cfg)

    def toggle_rewrite(self, rule_id: str, enabled: bool | None = None) -> RewriteRule:
        with self._lock:
            idx = self._index_by_id(self._config.rewrite, rule_id)
            rule = self._config.rewrite[idx]
            rule.enabled = (not rule.enabled) if enabled is None else bool(enabled)
            cfg = self._commit()
            out = next(r for r in self._clone(self._config).rewrite if r.id == rule_id)
        self._notify(cfg)
        return out

    def upsert_throttle(self, rule_id: str, payload: dict) -> ThrottleRule:
        """Insert or replace one throttle rule by id. Used by the debug network profiles."""
        rule = self._parse_throttle(payload, rule_id=rule_id)
        with self._lock:
            try:
                idx = self._index_by_id(self._config.throttle, rule_id)
            except RuleValidationError:
                rule.order = self._next_order(self._config.throttle)
                self._config.throttle.append(rule)
            else:
                rule.order = self._config.throttle[idx].order
                self._config.throttle[idx] = rule
            cfg = self._commit()
            out = next(r for r in self._clone(self._config).throttle if r.id == rule_id)
        self._notify(cfg)
        return out

    def delete_throttle_if_present(self, rule_id: str) -> bool:
        with self._lock:
            try:
                idx = self._index_by_id(self._config.throttle, rule_id)
            except RuleValidationError:
                return False
            del self._config.throttle[idx]
            cfg = self._commit()
        self._notify(cfg)
        return True

    def add_throttle(self, payload: dict) -> ThrottleRule:
        rule = self._parse_throttle(payload, rule_id=str(uuid.uuid4()))
        with self._lock:
            rule.order = self._next_order(self._config.throttle)
            self._config.throttle.append(rule)
            cfg = self._commit()
            out = self._clone(self._config).throttle[-1]
        self._notify(cfg)
        return out

    def update_throttle(self, rule_id: str, payload: dict) -> ThrottleRule:
        with self._lock:
            idx = self._index_by_id(self._config.throttle, rule_id)
            merged = self._parse_throttle(
                {**_throttle_as_dict(self._config.throttle[idx]), **payload},
                rule_id=rule_id,
            )
            self._config.throttle[idx] = merged
            self._config.throttle.sort(key=lambda r: r.order)
            cfg = self._commit()
            out = next(r for r in self._clone(self._config).throttle if r.id == rule_id)
        self._notify(cfg)
        return out

    def delete_throttle(self, rule_id: str) -> None:
        with self._lock:
            idx = self._index_by_id(self._config.throttle, rule_id)
            del self._config.throttle[idx]
            cfg = self._commit()
        self._notify(cfg)

    def toggle_throttle(self, rule_id: str, enabled: bool | None = None) -> ThrottleRule:
        with self._lock:
            idx = self._index_by_id(self._config.throttle, rule_id)
            rule = self._config.throttle[idx]
            rule.enabled = (not rule.enabled) if enabled is None else bool(enabled)
            cfg = self._commit()
            out = next(r for r in self._clone(self._config).throttle if r.id == rule_id)
        self._notify(cfg)
        return out

    def add_breakpoint(self, payload: dict) -> BreakpointRule:
        rule = self._parse_breakpoint(payload, rule_id=str(uuid.uuid4()))
        with self._lock:
            rule.order = self._next_order(self._config.breakpoints)
            self._config.breakpoints.append(rule)
            cfg = self._commit()
            out = self._clone(self._config).breakpoints[-1]
        self._notify(cfg)
        return out

    def update_breakpoint(self, rule_id: str, payload: dict) -> BreakpointRule:
        with self._lock:
            idx = self._index_by_id(self._config.breakpoints, rule_id)
            merged = self._parse_breakpoint(
                {**_breakpoint_as_dict(self._config.breakpoints[idx]), **payload},
                rule_id=rule_id,
            )
            self._config.breakpoints[idx] = merged
            self._config.breakpoints.sort(key=lambda r: r.order)
            cfg = self._commit()
            out = next(r for r in self._clone(self._config).breakpoints if r.id == rule_id)
        self._notify(cfg)
        return out

    def delete_breakpoint(self, rule_id: str) -> None:
        with self._lock:
            idx = self._index_by_id(self._config.breakpoints, rule_id)
            del self._config.breakpoints[idx]
            cfg = self._commit()
        self._notify(cfg)

    def toggle_breakpoint(self, rule_id: str, enabled: bool | None = None) -> BreakpointRule:
        with self._lock:
            idx = self._index_by_id(self._config.breakpoints, rule_id)
            rule = self._config.breakpoints[idx]
            rule.enabled = (not rule.enabled) if enabled is None else bool(enabled)
            cfg = self._commit()
            out = next(r for r in self._clone(self._config).breakpoints if r.id == rule_id)
        self._notify(cfg)
        return out

    # --- Evaluation (ordered; caller applies before forward / on response) ---

    def evaluate_throttle(self, host: str) -> ThrottleEffect:
        with self._lock:
            rules = sorted(
                (r for r in self._config.throttle if r.enabled),
                key=lambda r: r.order,
            )
        offline = False
        latency = 0
        upload: int | None = None
        download: int | None = None
        host_norm = (host or "").strip().lower().rstrip(".")
        for rule in rules:
            pat = (rule.host or "*").strip().lower().rstrip(".") or "*"
            if pat != "*" and not _fnmatch_ci(host_norm, pat):
                continue
            if rule.offline:
                offline = True
            latency = max(latency, clamp_latency_ms(rule.latency_ms))
            if rule.upload_kbps is not None:
                capped = clamp_kbps(rule.upload_kbps)
                upload = capped if upload is None else min(upload, capped)
            if rule.download_kbps is not None:
                capped = clamp_kbps(rule.download_kbps)
                download = capped if download is None else min(download, capped)
        return ThrottleEffect(
            offline=offline,
            latency_ms=latency,
            upload_kbps=upload,
            download_kbps=download,
        )

    def evaluate_map_local(self, ctx: RequestMatchContext) -> MapLocalResult | None:
        with self._lock:
            root_str = self._config.map_local_root
            rules = sorted(
                (r for r in self._config.map_local if r.enabled),
                key=lambda r: r.order,
            )
        for rule in rules:
            if not matches_globs(rule.match, ctx):
                continue
            return self._serve_map_local(root_str, rule.relative_path)
        return None

    def evaluate_map_remote(
        self,
        ctx: RequestMatchContext,
        *,
        original_url: str,
    ) -> MapRemoteResult | None:
        with self._lock:
            rules = sorted(
                (r for r in self._config.map_remote if r.enabled),
                key=lambda r: r.order,
            )
        for rule in rules:
            if not matches_globs(rule.match, ctx):
                continue
            try:
                url = build_map_remote_url(
                    original_url=original_url,
                    target_url=rule.target_url,
                    preserve_path=rule.preserve_path,
                    preserve_query=rule.preserve_query,
                )
            except RuleValidationError:
                continue
            return MapRemoteResult(url=url)
        return None

    def evaluate_breakpoint(
        self,
        ctx: RequestMatchContext,
        *,
        stage: BreakpointStage,
    ) -> BreakpointRule | None:
        with self._lock:
            rules = sorted(
                (r for r in self._config.breakpoints if r.enabled),
                key=lambda r: r.order,
            )
        for rule in rules:
            if stage == BreakpointStage.REQUEST and not rule.request_enabled:
                continue
            if stage == BreakpointStage.RESPONSE and not rule.response_enabled:
                continue
            if not matches_globs(rule.match, ctx):
                continue
            return BreakpointRule(
                id=rule.id,
                enabled=rule.enabled,
                order=rule.order,
                match=RuleMatch(
                    method=rule.match.method,
                    host=rule.match.host,
                    path=rule.match.path,
                ),
                request_enabled=rule.request_enabled,
                response_enabled=rule.response_enabled,
            )
        return None

    def evaluate_response_rewrite(
        self,
        ctx: RequestMatchContext,
        *,
        status: int,
        headers: dict[str, str],
        body: bytes,
    ) -> RewriteResult:
        with self._lock:
            rules = sorted(
                (r for r in self._config.rewrite if r.enabled),
                key=lambda r: r.order,
            )
        current_status = status
        current_headers = dict(headers)
        current_body = body
        last_error: str | None = None
        for rule in rules:
            if not matches_globs(rule.match, ctx):
                continue
            result = apply_rewrite_actions(
                status=current_status,
                headers=current_headers,
                body=current_body,
                actions=rule.actions,
            )
            if result.error:
                last_error = result.error
                continue
            current_status = result.status
            current_headers = result.headers
            current_body = result.body
        return RewriteResult(
            status=current_status,
            headers=current_headers,
            body=current_body,
            error=last_error,
        )

    def _serve_map_local(self, root_str: str, relative_path: str) -> MapLocalResult:
        try:
            root = Path(root_str)
            resolved = resolve_map_local_path(root, relative_path)
        except PathJailError as exc:
            return MapLocalResult(
                status=404,
                body=b"Not Found",
                content_type="text/plain; charset=utf-8",
                relative_path=relative_path,
                rejected=True,
                reason=str(exc),
            )
        if not resolved.is_file():
            return MapLocalResult(
                status=404,
                body=b"Not Found",
                content_type="text/plain; charset=utf-8",
                relative_path=relative_path,
                missing=True,
            )
        try:
            data = resolved.read_bytes()
        except OSError:
            return MapLocalResult(
                status=404,
                body=b"Not Found",
                content_type="text/plain; charset=utf-8",
                relative_path=relative_path,
                missing=True,
            )
        if len(data) > MAX_RESULT_BODY_BYTES:
            data = data[:MAX_RESULT_BODY_BYTES]
        content_type, _ = mimetypes.guess_type(str(resolved))
        return MapLocalResult(
            status=200,
            body=data,
            content_type=content_type or "application/octet-stream",
            relative_path=relative_path,
        )

    def _persist_locked(self) -> RulesConfig:
        self._store.save(self._config)
        cfg = self._clone(self._config)
        return cfg

    def _commit(self) -> RulesConfig:
        """Persist current config; caller must hold ``_lock`` until return, then notify."""
        cfg = self._persist_locked()
        return cfg

    def _notify(self, cfg: RulesConfig) -> None:
        for listener in list(self._listeners):
            try:
                listener(cfg)
            except Exception:
                pass

    def _normalize_config(self, config: RulesConfig) -> RulesConfig:
        return RulesConfig(
            allowlist=[p.strip() for p in config.allowlist if p and str(p).strip()],
            map_local_root=str(config.map_local_root or "").strip(),
            map_local=sorted(config.map_local, key=lambda r: r.order),
            map_remote=sorted(config.map_remote, key=lambda r: r.order),
            rewrite=sorted(config.rewrite, key=lambda r: r.order),
            throttle=sorted(
                [
                    ThrottleRule(
                        id=r.id,
                        enabled=r.enabled,
                        order=r.order,
                        host=r.host,
                        latency_ms=clamp_latency_ms(r.latency_ms),
                        upload_kbps=clamp_kbps(r.upload_kbps),
                        download_kbps=clamp_kbps(r.download_kbps),
                        offline=r.offline,
                    )
                    for r in config.throttle
                ],
                key=lambda r: r.order,
            ),
            breakpoints=sorted(config.breakpoints, key=lambda r: r.order),
        )

    @staticmethod
    def _clone(config: RulesConfig) -> RulesConfig:
        return config_from_dict(config_to_dict(config))

    @staticmethod
    def _next_order(rules: list) -> int:
        if not rules:
            return 0
        return max(int(r.order) for r in rules) + 1

    @staticmethod
    def _index_by_id(rules: list[T], rule_id: str) -> int:
        for i, rule in enumerate(rules):
            if getattr(rule, "id", None) == rule_id:
                return i
        raise RuleValidationError(f"rule not found: {rule_id}")

    def _parse_map_local(self, payload: dict, *, rule_id: str) -> MapLocalRule:
        match = _match_from_payload(payload)
        rel = str(payload.get("relative_path") or "").strip()
        if not rel:
            raise RuleValidationError("relative_path is required")
        # Validate jail shape when root is set; still allow saving relative path.
        if ".." in rel.replace("\\", "/").split("/") or rel.startswith("/") or rel.startswith("~"):
            raise RuleValidationError("relative_path must be relative without traversal")
        return MapLocalRule(
            id=rule_id,
            enabled=bool(payload.get("enabled", False)),
            order=int(payload.get("order") or 0),
            match=match,
            relative_path=rel,
        )

    def _parse_map_remote(self, payload: dict, *, rule_id: str) -> MapRemoteRule:
        match = _match_from_payload(payload)
        target = str(payload.get("target_url") or "").strip()
        validate_http_url(target)
        return MapRemoteRule(
            id=rule_id,
            enabled=bool(payload.get("enabled", False)),
            order=int(payload.get("order") or 0),
            match=match,
            target_url=target,
            preserve_path=bool(payload.get("preserve_path", True)),
            preserve_query=bool(payload.get("preserve_query", True)),
        )

    def _parse_rewrite(self, payload: dict, *, rule_id: str) -> RewriteRule:
        match = _match_from_payload(payload)
        actions_raw = payload.get("actions") or []
        if not isinstance(actions_raw, list):
            raise RuleValidationError("actions must be a list")
        actions: list[RewriteAction] = []
        for item in actions_raw:
            if not isinstance(item, dict):
                raise RuleValidationError("each action must be an object")
            actions.append(_parse_action(item))
        return RewriteRule(
            id=rule_id,
            enabled=bool(payload.get("enabled", False)),
            order=int(payload.get("order") or 0),
            match=match,
            actions=actions,
        )

    def _parse_throttle(self, payload: dict, *, rule_id: str) -> ThrottleRule:
        return ThrottleRule(
            id=rule_id,
            enabled=bool(payload.get("enabled", False)),
            order=int(payload.get("order") or 0),
            host=str(payload.get("host") or "*"),
            latency_ms=clamp_latency_ms(int(payload.get("latency_ms") or 0)),
            upload_kbps=clamp_kbps(
                None if payload.get("upload_kbps") is None else int(payload["upload_kbps"])
            ),
            download_kbps=clamp_kbps(
                None if payload.get("download_kbps") is None else int(payload["download_kbps"])
            ),
            offline=bool(payload.get("offline", False)),
        )

    def _parse_breakpoint(self, payload: dict, *, rule_id: str) -> BreakpointRule:
        match = _match_from_payload(payload)
        request_enabled = bool(payload.get("request_enabled", False))
        response_enabled = bool(payload.get("response_enabled", False))
        if not request_enabled and not response_enabled:
            raise RuleValidationError(
                "breakpoint requires request_enabled and/or response_enabled"
            )
        return BreakpointRule(
            id=rule_id,
            enabled=bool(payload.get("enabled", False)),
            order=int(payload.get("order") or 0),
            match=match,
            request_enabled=request_enabled,
            response_enabled=response_enabled,
        )


def _match_from_payload(payload: dict) -> RuleMatch:
    raw = payload.get("match")
    if isinstance(raw, dict):
        return RuleMatch(
            method=str(raw.get("method") or "*"),
            host=str(raw.get("host") or "*"),
            path=str(raw.get("path") or "*"),
        )
    return RuleMatch(
        method=str(payload.get("method") or "*"),
        host=str(payload.get("host") or "*"),
        path=str(payload.get("path") or "*"),
    )


def _parse_action(data: dict) -> RewriteAction:
    kind_raw = str(data.get("kind") or "")
    try:
        kind = RewriteActionKind(kind_raw)
    except ValueError as exc:
        raise RuleValidationError(f"unknown rewrite action kind: {kind_raw}") from exc
    replace = data.get("replace")
    if replace is not None and len(str(replace)) > MAX_REPLACEMENT_CHARS:
        raise RuleValidationError("replacement exceeds size cap")
    status = data.get("status")
    if status is not None:
        code = int(status)
        if code < MIN_STATUS or code > MAX_STATUS:
            raise RuleValidationError("status out of range")
    return RewriteAction(
        kind=kind,
        header_name=None if data.get("header_name") is None else str(data.get("header_name")),
        header_value=None if data.get("header_value") is None else str(data.get("header_value")),
        status=int(status) if status is not None else None,
        find=None if data.get("find") is None else str(data.get("find")),
        replace=None if replace is None else str(replace),
    )


def map_local_to_api(rule: MapLocalRule) -> dict:
    return _map_local_as_dict(rule)


def map_remote_to_api(rule: MapRemoteRule) -> dict:
    return _map_remote_as_dict(rule)


def rewrite_to_api(rule: RewriteRule) -> dict:
    return _rewrite_as_dict(rule)


def throttle_to_api(rule: ThrottleRule) -> dict:
    return _throttle_as_dict(rule)


def breakpoint_to_api(rule: BreakpointRule) -> dict:
    return _breakpoint_as_dict(rule)


def _map_local_as_dict(rule: MapLocalRule) -> dict:
    return {
        "id": rule.id,
        "enabled": bool(rule.enabled),
        "order": int(rule.order),
        "match": match_to_dict(rule.match),
        "relative_path": rule.relative_path,
    }


def _map_remote_as_dict(rule: MapRemoteRule) -> dict:
    return {
        "id": rule.id,
        "enabled": bool(rule.enabled),
        "order": int(rule.order),
        "match": match_to_dict(rule.match),
        "target_url": rule.target_url,
        "preserve_path": bool(rule.preserve_path),
        "preserve_query": bool(rule.preserve_query),
    }


def _rewrite_as_dict(rule: RewriteRule) -> dict:
    return {
        "id": rule.id,
        "enabled": bool(rule.enabled),
        "order": int(rule.order),
        "match": match_to_dict(rule.match),
        "actions": [
            {
                "kind": str(a.kind),
                "header_name": a.header_name,
                "header_value": a.header_value,
                "status": a.status,
                "find": a.find,
                "replace": a.replace,
            }
            for a in rule.actions
        ],
    }


def _throttle_as_dict(rule: ThrottleRule) -> dict:
    return {
        "id": rule.id,
        "enabled": bool(rule.enabled),
        "order": int(rule.order),
        "host": rule.host,
        "latency_ms": int(rule.latency_ms),
        "upload_kbps": rule.upload_kbps,
        "download_kbps": rule.download_kbps,
        "offline": bool(rule.offline),
    }


def _breakpoint_as_dict(rule: BreakpointRule) -> dict:
    return {
        "id": rule.id,
        "enabled": bool(rule.enabled),
        "order": int(rule.order),
        "match": match_to_dict(rule.match),
        "request_enabled": bool(rule.request_enabled),
        "response_enabled": bool(rule.response_enabled),
    }
