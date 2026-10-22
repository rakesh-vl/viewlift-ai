"""Authenticated debug clients, bounded events, and default-deny commands."""

from __future__ import annotations

import json
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from viewlift_ai.services.encrypted_store import EncryptedSqliteStore
from viewlift_ai.services.event_bus import EventBus

MAX_EVENT_BYTES = 512_000
MAX_COMMAND_BYTES = 64_000
CLIENT_TTL_SECONDS = 45.0
COMMAND_TTL_SECONDS = 30.0
CONFIRM_TTL_SECONDS = 30.0

READ_COMMANDS = {
    "performance.snapshot",
    "memory.snapshot",
    "memory.graph",
    "ui.hierarchy",
    "ui.screenshot",
    "swiftui.renders",
    "assets.audit",
    "accessibility.audit",
    "storage.defaults",
    "storage.keychain_metadata",
    "storage.files",
    "storage.file_read",
    "storage.sqlite_schema",
    "storage.sqlite_query",
    "feature_flags.list",
    "runtime.search",
    "runtime.describe",
    "macro.list",
    "bug.capture",
    "ott.snapshot",
    "hang.list",
    "crash.list",
}

MUTATING_COMMANDS = {
    "ui.highlight",
    "ui.edit",
    "storage.defaults_set",
    "storage.file_write",
    "storage.snapshot_create",
    "storage.snapshot_restore",
    "feature_flags.set",
    "feature_flags.reset",
    "macro.record_start",
    "macro.record_stop",
    "macro.replay",
    "push.local",
}

ALLOWED_COMMANDS = READ_COMMANDS | MUTATING_COMMANDS


@dataclass(slots=True)
class DebugClient:
    id: str
    app_name: str
    bundle_id: str
    platform: str
    os_version: str
    capabilities: dict[str, str]
    last_seen: float

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "app_name": self.app_name,
            "bundle_id": self.bundle_id,
            "platform": self.platform,
            "os_version": self.os_version,
            "capabilities": dict(self.capabilities),
            "last_seen_at": datetime.fromtimestamp(self.last_seen, UTC).isoformat(),
        }


@dataclass(slots=True)
class DebugCommand:
    id: str
    client_id: str
    name: str
    payload: dict[str, object]
    expires_at: float
    created_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "payload": self.payload,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class DebugSessionService:
    store: EncryptedSqliteStore
    events: EventBus[dict[str, object]]
    time_fn: Any = time.time
    _clients: dict[str, DebugClient] = field(default_factory=dict)
    _commands: dict[str, list[DebugCommand]] = field(default_factory=dict)
    _confirmations: dict[str, tuple[str, str, float]] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def register(self, payload: dict[str, Any]) -> dict[str, object]:
        client_id = _identifier(payload.get("client_id"), fallback=str(uuid.uuid4()))
        capabilities = _capabilities(payload.get("capabilities"))
        client = DebugClient(
            id=client_id,
            app_name=_short(payload.get("app_name"), 120) or "Unknown",
            bundle_id=_short(payload.get("bundle_id"), 200),
            platform=_short(payload.get("platform"), 20) or "unknown",
            os_version=_short(payload.get("os_version"), 80),
            capabilities=capabilities,
            last_seen=self.time_fn(),
        )
        with self._lock:
            self._clients[client_id] = client
            self._commands.setdefault(client_id, [])
        self._publish({"type": "debug-client", "client": client.as_dict()})
        return {"ok": True, "client_id": client_id, "commands": sorted(ALLOWED_COMMANDS)}

    def heartbeat(self, client_id: str) -> dict[str, object]:
        client = self._require_client(client_id)
        with self._lock:
            client.last_seen = self.time_fn()
        return {"ok": True}

    def list_clients(self) -> list[dict[str, object]]:
        now = self.time_fn()
        with self._lock:
            clients = list(self._clients.values())
        rows = []
        for client in clients:
            row = client.as_dict()
            row["online"] = now - client.last_seen <= CLIENT_TTL_SECONDS
            rows.append(row)
        return sorted(rows, key=lambda item: str(item["last_seen_at"]), reverse=True)

    def mint_confirmation(self, client_id: str, command: str) -> dict[str, object]:
        self._require_client(client_id)
        if command not in MUTATING_COMMANDS:
            raise ValueError("confirmation is only used for mutating commands")
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._confirmations[token] = (
                client_id,
                command,
                self.time_fn() + CONFIRM_TTL_SECONDS,
            )
        return {"token": token, "expires_in": int(CONFIRM_TTL_SECONDS)}

    def enqueue(
        self,
        *,
        client_id: str,
        name: str,
        payload: dict[str, Any],
        confirmation: str | None = None,
    ) -> dict[str, object]:
        self._require_client(client_id)
        if name not in ALLOWED_COMMANDS:
            raise ValueError("command is not allowlisted")
        clean = _bounded_payload(payload, MAX_COMMAND_BYTES)
        if name in MUTATING_COMMANDS:
            self._consume_confirmation(client_id, name, confirmation)
        command = DebugCommand(
            id=str(uuid.uuid4()),
            client_id=client_id,
            name=name,
            payload=clean,
            expires_at=self.time_fn() + COMMAND_TTL_SECONDS,
            created_at=datetime.now(UTC).isoformat(),
        )
        with self._lock:
            queue = self._commands.setdefault(client_id, [])
            queue.append(command)
            if len(queue) > 100:
                del queue[:-100]
        self._publish(
            {
                "type": "debug-command",
                "client_id": client_id,
                "command": command.as_dict(),
            }
        )
        return command.as_dict()

    def poll(self, client_id: str, *, limit: int = 10) -> list[dict[str, object]]:
        self.heartbeat(client_id)
        now = self.time_fn()
        with self._lock:
            queue = self._commands.setdefault(client_id, [])
            live = [item for item in queue if item.expires_at > now]
            taken = live[: max(1, min(limit, 20))]
            taken_ids = {item.id for item in taken}
            self._commands[client_id] = [item for item in live if item.id not in taken_ids]
        return [item.as_dict() for item in taken]

    def ingest_event(self, client_id: str, payload: dict[str, Any]) -> dict[str, object]:
        self.heartbeat(client_id)
        module = _short(payload.get("module"), 64)
        kind = _short(payload.get("kind"), 64)
        if not module or not kind:
            raise ValueError("module and kind are required")
        event_payload = payload.get("payload")
        if not isinstance(event_payload, dict):
            raise ValueError("payload must be an object")
        clean = _bounded_payload(event_payload, MAX_EVENT_BYTES)
        if module == "keychain":
            clean = _strip_keychain_values(clean)
        event_id = _identifier(payload.get("id"), fallback=str(uuid.uuid4()))
        created_at = datetime.now(UTC)
        self.store.save_debug_event(
            event_id=event_id,
            client_id=client_id,
            module=module,
            kind=kind,
            payload=clean,
            created_at=created_at,
        )
        event = {
            "id": event_id,
            "client_id": client_id,
            "module": module,
            "kind": kind,
            "payload": clean,
            "created_at": created_at.isoformat(),
        }
        self._publish({"type": "debug-event", "event": event})
        return {"ok": True, "id": event_id}

    def list_events(
        self,
        *,
        client_id: str,
        module: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, object]]:
        self._require_client(client_id)
        return self.store.list_debug_events(
            client_id=client_id,
            module=module,
            limit=limit,
        )

    def _require_client(self, client_id: str) -> DebugClient:
        with self._lock:
            client = self._clients.get(client_id)
        if client is None:
            raise LookupError("debug client not registered")
        return client

    def _consume_confirmation(
        self,
        client_id: str,
        command: str,
        token: str | None,
    ) -> None:
        if not token:
            raise PermissionError("confirmation token required")
        with self._lock:
            record = self._confirmations.pop(token, None)
        if record is None:
            raise PermissionError("invalid confirmation token")
        expected_client, expected_command, expires_at = record
        if self.time_fn() >= expires_at:
            raise PermissionError("confirmation token expired")
        if expected_client != client_id or expected_command != command:
            raise PermissionError("confirmation token does not match command")

    def _publish(self, event: dict[str, object]) -> None:
        self.events.publish_threadsafe(event)


def _bounded_payload(payload: dict[str, Any], limit: int) -> dict[str, object]:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(raw) > limit:
        raise OverflowError(f"payload exceeds {limit} bytes")
    return json.loads(raw.decode("utf-8"))


def _identifier(value: object, *, fallback: str) -> str:
    text = _short(value, 128)
    if text and all(char.isalnum() or char in "._-" for char in text):
        return text
    return fallback


def _short(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _capabilities(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    allowed = {"available", "partial", "unsupported"}
    return {
        _short(key, 64): status
        for key, raw in value.items()
        if (status := _short(raw, 20)) in allowed and _short(key, 64)
    }


def _strip_keychain_values(payload: dict[str, object]) -> dict[str, object]:
    forbidden = {"value", "data", "secret", "password", "token"}

    def scrub(value: object) -> object:
        if isinstance(value, dict):
            return {
                str(key): scrub(item)
                for key, item in value.items()
                if str(key).lower() not in forbidden
            }
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    result = scrub(payload)
    return result if isinstance(result, dict) else {}
