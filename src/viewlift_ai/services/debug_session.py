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
# iOS suspends a backgrounded app within seconds, so silence is expected, not a disconnect.
# An app that reported "background" stays listed as backgrounded for this long.
BACKGROUND_GRACE_SECONDS = 1800.0
LIFECYCLE_STATES = frozenset({"foreground", "background"})
PERSIST_INTERVAL_SECONDS = 30.0
COMMAND_TTL_SECONDS = 30.0
CONFIRM_TTL_SECONDS = 30.0
INVOKE_DEFAULT_TIMEOUT = 12.0
INVOKE_MAX_TIMEOUT = 25.0

READ_COMMANDS = {
    "performance.snapshot",
    "memory.snapshot",
    "memory.graph",
    "memory.leaks",
    "memory.heap",
    "memory.live_start",
    "memory.live_stop",
    "ui.hierarchy",
    "ui.describe",
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

# Streamed once a second; persisting them would push command results out of the event history.
EPHEMERAL_EVENTS = frozenset({("memory", "live")})


MAX_FRAMEWORKS = 400


@dataclass(slots=True)
class DebugClient:
    id: str
    app_name: str
    bundle_id: str
    platform: str
    os_version: str
    capabilities: dict[str, str]
    last_seen: float
    app_version: str = ""
    build: str = ""
    executable: str = ""
    sdk_version: str = ""
    frameworks: list[dict[str, str]] = field(default_factory=list)
    url_schemes: list[str] = field(default_factory=list)
    device_key: str = ""
    device_name: str = ""
    device_model: str = ""
    is_simulator: bool = False
    first_seen: float = 0.0
    superseded_by: str | None = None
    persisted_at: float = 0.0
    lifecycle: str = ""
    lifecycle_at: float = 0.0

    def presence(self, now: float) -> str:
        """online: talking now. background: app said it left the foreground. offline: silent."""
        age = now - self.last_seen
        if self.lifecycle == "background":
            return "background" if age <= BACKGROUND_GRACE_SECONDS else "offline"
        return "online" if age <= CLIENT_TTL_SECONDS else "offline"

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "app_name": self.app_name,
            "bundle_id": self.bundle_id,
            "platform": self.platform,
            "os_version": self.os_version,
            "app_version": self.app_version,
            "build": self.build,
            "executable": self.executable,
            "sdk_version": self.sdk_version,
            "framework_count": len(self.frameworks),
            "url_schemes": list(self.url_schemes),
            "capabilities": dict(self.capabilities),
            "device_key": self.device_key,
            "device_name": self.device_name,
            "device_model": self.device_model,
            "is_simulator": self.is_simulator,
            "superseded_by": self.superseded_by,
            "lifecycle": self.lifecycle or "foreground",
            "source": "sdk",
            "last_seen_at": datetime.fromtimestamp(self.last_seen, UTC).isoformat(),
        }

    def persisted_payload(self) -> dict[str, object]:
        return {
            "app_name": self.app_name,
            "platform": self.platform,
            "os_version": self.os_version,
            "capabilities": dict(self.capabilities),
            "app_version": self.app_version,
            "build": self.build,
            "executable": self.executable,
            "sdk_version": self.sdk_version,
            "frameworks": [dict(item) for item in self.frameworks],
            "url_schemes": list(self.url_schemes),
            "device_name": self.device_name,
            "device_model": self.device_model,
            "is_simulator": self.is_simulator,
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
    _waiters: dict[str, tuple[threading.Event, dict[str, object]]] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def __post_init__(self) -> None:
        self._load_persisted()

    def register(self, payload: dict[str, Any]) -> dict[str, object]:
        client_id = _identifier(payload.get("client_id"), fallback=str(uuid.uuid4()))
        capabilities = _capabilities(payload.get("capabilities"))
        now = self.time_fn()
        with self._lock:
            previous = self._clients.get(client_id)
        client = DebugClient(
            id=client_id,
            app_name=_short(payload.get("app_name"), 120) or "Unknown",
            bundle_id=_short(payload.get("bundle_id"), 200),
            platform=_short(payload.get("platform"), 20) or "unknown",
            os_version=_short(payload.get("os_version"), 80),
            capabilities=capabilities,
            last_seen=now,
            app_version=_short(payload.get("app_version"), 64),
            build=_short(payload.get("build"), 64),
            executable=_short(payload.get("executable"), 200),
            sdk_version=_short(payload.get("sdk_version"), 64),
            frameworks=_frameworks(payload.get("frameworks")),
            url_schemes=_url_schemes(payload.get("url_schemes")),
            device_key=_short(payload.get("device_key"), 200),
            device_name=_short(payload.get("device_name"), 120),
            device_model=_short(payload.get("device_model"), 64),
            is_simulator=bool(payload.get("is_simulator")),
            first_seen=previous.first_seen if previous else now,
        )
        with self._lock:
            self._clients[client_id] = client
            self._commands.setdefault(client_id, [])
            superseded = self._supersede_locked(client)
        self._persist(client)
        for old in superseded:
            self._persist(old)
        self._publish({"type": "debug-client", "client": client.as_dict()})
        self._publish_apps_changed()
        return {"ok": True, "client_id": client_id, "commands": sorted(ALLOWED_COMMANDS)}

    def heartbeat(self, client_id: str) -> dict[str, object]:
        client = self._require_client(client_id)
        now = self.time_fn()
        with self._lock:
            before = client.presence(now)
            client.last_seen = now
            after = client.presence(now)
            stale = now - client.persisted_at >= PERSIST_INTERVAL_SECONDS
        if stale:
            self._persist(client)
        if before != after:
            self._publish_apps_changed()
        return {"ok": True}

    def touch(self, client_id: str | None) -> None:
        """Any authenticated upload proves the app is alive. Unknown ids are ignored."""
        if not client_id:
            return
        try:
            self.heartbeat(client_id)
        except LookupError:
            return

    def record_lifecycle(self, client_id: str, kind: str) -> None:
        """Foreground, background, or network-change report from the SDK."""
        client = self._require_client(client_id)
        now = self.time_fn()
        changed = False
        with self._lock:
            before = client.presence(now)
            if kind in LIFECYCLE_STATES:
                changed = client.lifecycle != kind
                client.lifecycle = kind
                client.lifecycle_at = now
            client.last_seen = now
            after = client.presence(now)
        if changed or before != after:
            self._publish_apps_changed()

    def is_online(self, client_id: str) -> bool:
        with self._lock:
            client = self._clients.get(client_id)
        return client is not None and client.presence(self.time_fn()) == "online"

    def _row(self, client: DebugClient, now: float) -> dict[str, object]:
        row = client.as_dict()
        state = client.presence(now)
        row["state"] = state
        row["online"] = state == "online"
        return row

    def client_ids_for_device(self, device_key: str) -> list[str]:
        with self._lock:
            return [item.id for item in self._clients.values() if item.device_key == device_key]

    def find_by_udid(self, udid: str) -> list[dict[str, object]]:
        """Simulator clients whose device_key is the given simulator UDID."""
        with self._lock:
            matches = [
                item
                for item in self._clients.values()
                if item.is_simulator and item.device_key == udid and not item.superseded_by
            ]
        return [item.as_dict() for item in matches]

    def resolve_client_id(self, client_id: str | None) -> str | None:
        """
        Explicit id wins. Without one, fall back only when exactly one app is online,
        so two apps on one device never flip the dashboard between each other.
        """
        if client_id:
            return client_id
        now = self.time_fn()
        with self._lock:
            online = [
                item.id
                for item in self._clients.values()
                if item.presence(now) == "online" and not item.superseded_by
            ]
        if len(online) == 1:
            return online[0]
        return None

    def online_count(self) -> int:
        now = self.time_fn()
        with self._lock:
            return sum(1 for item in self._clients.values() if item.presence(now) == "online")

    def apps_tree(self, counts: dict[str, dict[str, int]] | None = None) -> dict[str, object]:
        """Device > App grouping for the dashboard scope picker."""
        counts = counts or {}
        now = self.time_fn()
        with self._lock:
            clients = list(self._clients.values())
        devices: dict[str, dict[str, object]] = {}
        for client in sorted(clients, key=lambda item: item.last_seen, reverse=True):
            key = client.device_key or f"client:{client.id}"
            device = devices.setdefault(
                key,
                {
                    "device_key": key,
                    "device_name": client.device_name or client.device_model or "Unknown device",
                    "device_model": client.device_model,
                    "platform": client.platform,
                    "os_version": client.os_version,
                    "is_simulator": client.is_simulator,
                    "online": False,
                    "apps": [],
                },
            )
            row = self._row(client, now)
            online = bool(row["online"])
            row["counts"] = counts.get(client.id, {"flows": 0, "logs": 0})
            apps = device["apps"]
            assert isinstance(apps, list)
            apps.append(row)
            if online:
                device["online"] = True
        rows = list(devices.values())
        rows.sort(key=lambda item: (not item["online"], str(item["device_name"]).lower()))
        return {"devices": rows, "online_apps": self.online_count()}

    def list_devices(self) -> list[dict[str, object]]:
        """Apps grouped by device_key. One phone or simulator can hold many apps."""
        devices = self.apps_tree()["devices"]
        if not isinstance(devices, list):
            return []
        return devices

    def forget_client(self, client_id: str) -> None:
        """Drop an app instance from the registry (used by per-app clear when offline)."""
        with self._lock:
            self._clients.pop(client_id, None)
            self._commands.pop(client_id, None)
        self.store.delete_clients([client_id])
        self._publish_apps_changed()

    def reset_session(self) -> None:
        """Last dashboard tab closed: clear queues and forget apps that are not connected."""
        now = self.time_fn()
        with self._lock:
            self._commands = {key: [] for key in self._clients}
            self._confirmations.clear()
            offline = [
                key
                for key, item in self._clients.items()
                if item.presence(now) == "offline" or item.superseded_by
            ]
            for key in offline:
                self._clients.pop(key, None)
                self._commands.pop(key, None)
        self.store.delete_clients(offline)
        self._publish_apps_changed()

    def get_client(self, client_id: str) -> dict[str, object]:
        client = self._require_client(client_id)
        row = self._row(client, self.time_fn())
        row["frameworks"] = [dict(item) for item in client.frameworks]
        return row

    def latest_client_id(self) -> str | None:
        with self._lock:
            clients = list(self._clients.values())
        if not clients:
            return None
        return max(clients, key=lambda item: item.last_seen).id

    def list_clients(self) -> list[dict[str, object]]:
        now = self.time_fn()
        with self._lock:
            clients = list(self._clients.values())
        rows = []
        for client in clients:
            rows.append(self._row(client, now))
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
        command_id: str | None = None,
    ) -> dict[str, object]:
        self._require_client(client_id)
        if name not in ALLOWED_COMMANDS:
            raise ValueError("command is not allowlisted")
        clean = _bounded_payload(payload, MAX_COMMAND_BYTES)
        if name in MUTATING_COMMANDS:
            self._consume_confirmation(client_id, name, confirmation)
        command = DebugCommand(
            id=_identifier(command_id, fallback=str(uuid.uuid4())),
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

    def invoke(
        self,
        *,
        client_id: str,
        name: str,
        payload: dict[str, Any] | None = None,
        mutate: bool = False,
        timeout: float | None = None,
    ) -> dict[str, object]:
        """Enqueue one command and block until that command's result event arrives."""
        if not str(client_id or "").strip():
            raise LookupError("client_id is required")
        client = self._require_client(client_id)
        state = client.presence(self.time_fn())
        if state == "background":
            raise LookupError("debug client is in the background; bring the app to the foreground")
        if state != "online":
            raise LookupError("debug client is offline")
        body = payload or {}
        _bounded_payload(body, MAX_COMMAND_BYTES)
        confirmation: str | None = None
        if name in MUTATING_COMMANDS:
            if not mutate:
                raise PermissionError("mutate must be true for mutating commands")
            confirmation = str(self.mint_confirmation(client_id, name)["token"])
        elif name not in READ_COMMANDS:
            raise ValueError("command is not allowlisted")
        wait = INVOKE_DEFAULT_TIMEOUT if timeout is None else float(timeout)
        if wait <= 0 or wait > INVOKE_MAX_TIMEOUT:
            raise ValueError("timeout must be between 0 and 25 seconds")
        command_id = str(uuid.uuid4())
        done = threading.Event()
        box: dict[str, object] = {}
        with self._lock:
            self._waiters[command_id] = (done, box)
        try:
            command = self.enqueue(
                client_id=client_id,
                name=name,
                payload=body,
                confirmation=confirmation,
                command_id=command_id,
            )
            if not done.wait(wait):
                raise TimeoutError("debug command timed out")
            event = box.get("event")
            return {"ok": True, "command": command, "event": event}
        finally:
            with self._lock:
                self._waiters.pop(command_id, None)

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
        if module == "lifecycle":
            self.record_lifecycle(client_id, kind)
            return {"ok": True}
        if module == "keychain":
            clean = _strip_keychain_values(clean)
        event_id = _identifier(payload.get("id"), fallback=str(uuid.uuid4()))
        created_at = datetime.now(UTC)
        if (module, kind) not in EPHEMERAL_EVENTS:
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
        self._signal_waiter(event)
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

    def _supersede_locked(self, client: DebugClient) -> list[DebugClient]:
        """A reinstall gets a new client_id; retire older ids for the same app on that device."""
        if not client.device_key or not client.bundle_id:
            return []
        retired: list[DebugClient] = []
        for other in self._clients.values():
            if (
                other.id != client.id
                and other.device_key == client.device_key
                and other.bundle_id == client.bundle_id
                and other.superseded_by != client.id
                and other.last_seen <= client.last_seen
            ):
                other.superseded_by = client.id
                retired.append(other)
        return retired

    def _persist(self, client: DebugClient) -> None:
        with self._lock:
            client.persisted_at = self.time_fn()
        self.store.save_client(
            client_id=client.id,
            device_key=client.device_key,
            bundle_id=client.bundle_id,
            payload=client.persisted_payload(),
            first_seen=client.first_seen or client.last_seen,
            last_seen=client.last_seen,
            superseded_by=client.superseded_by,
        )

    def _load_persisted(self) -> None:
        loader = getattr(self.store, "list_clients", None)
        if loader is None:
            return
        for row in loader():
            payload = row.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            client_id = str(row.get("client_id") or "")
            if not client_id:
                continue
            client = DebugClient(
                id=client_id,
                app_name=_short(payload.get("app_name"), 120) or "Unknown",
                bundle_id=str(row.get("bundle_id") or ""),
                platform=_short(payload.get("platform"), 20) or "unknown",
                os_version=_short(payload.get("os_version"), 80),
                capabilities=_capabilities(payload.get("capabilities")),
                last_seen=float(row.get("last_seen") or 0.0),
                app_version=_short(payload.get("app_version"), 64),
                build=_short(payload.get("build"), 64),
                executable=_short(payload.get("executable"), 200),
                sdk_version=_short(payload.get("sdk_version"), 64),
                frameworks=_frameworks(payload.get("frameworks")),
                url_schemes=_url_schemes(payload.get("url_schemes")),
                device_key=str(row.get("device_key") or ""),
                device_name=_short(payload.get("device_name"), 120),
                device_model=_short(payload.get("device_model"), 64),
                is_simulator=bool(payload.get("is_simulator")),
                first_seen=float(row.get("first_seen") or 0.0),
                superseded_by=(str(row["superseded_by"]) if row.get("superseded_by") else None),
                persisted_at=float(row.get("last_seen") or 0.0),
            )
            self._clients[client_id] = client
            self._commands.setdefault(client_id, [])

    def _signal_waiter(self, event: dict[str, object]) -> None:
        if event.get("kind") != "command-result":
            return
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return
        command_id = payload.get("command_id")
        if not isinstance(command_id, str):
            return
        with self._lock:
            waiter = self._waiters.get(command_id)
        if waiter is None:
            return
        waiter[1]["event"] = event
        waiter[0].set()

    def _publish_apps_changed(self) -> None:
        self._publish({"type": "apps-changed"})

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


def _frameworks(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, str):
            identifier, version, path = _short(item, 200), "", ""
        elif isinstance(item, dict):
            identifier = _short(item.get("id") or item.get("bundle_id"), 200)
            version = _short(item.get("version"), 64)
            path = _short(item.get("name"), 200)
        else:
            continue
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        rows.append({"id": identifier, "version": version, "name": path})
        if len(rows) >= MAX_FRAMEWORKS:
            break
    return sorted(rows, key=lambda row: row["id"].lower())


def _url_schemes(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    schemes: list[str] = []
    for item in value:
        scheme = _short(item, 64)
        valid = scheme and all(char.isalnum() or char in "+.-" for char in scheme)
        if valid and scheme not in schemes:
            schemes.append(scheme)
    return schemes[:20]


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
