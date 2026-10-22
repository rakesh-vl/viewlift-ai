"""Remote debug sessions: command allowlist, confirmation, bounds, encryption."""

from __future__ import annotations

from pathlib import Path

import pytest
from nacl.secret import SecretBox

from viewlift_ai.crypto import SecretBoxCipher
from viewlift_ai.models.flow import RetentionPolicy
from viewlift_ai.services.debug_session import DebugSessionService
from viewlift_ai.services.encrypted_store import EncryptedSqliteStore
from viewlift_ai.services.event_bus import EventBus


def _service(tmp_path: Path) -> tuple[DebugSessionService, EncryptedSqliteStore]:
    store = EncryptedSqliteStore(
        tmp_path / "debug.db",
        SecretBoxCipher(b"k" * SecretBox.KEY_SIZE),
        retention=RetentionPolicy.SESSION,
    )
    return DebugSessionService(store=store, events=EventBus()), store


def _register(service: DebugSessionService) -> str:
    result = service.register(
        {
            "client_id": "phone-1",
            "app_name": "App",
            "bundle_id": "com.example.app",
            "platform": "iOS",
            "os_version": "26",
            "capabilities": {"performance": "available", "memory": "partial"},
        }
    )
    return str(result["client_id"])


def test_register_enqueue_poll_and_event(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    client = _register(service)
    command = service.enqueue(
        client_id=client,
        name="performance.snapshot",
        payload={},
    )
    polled = service.poll(client)
    assert polled[0]["id"] == command["id"]
    assert service.poll(client) == []

    service.ingest_event(
        client,
        {
            "module": "performance",
            "kind": "sample",
            "payload": {"cpu": 7.5},
        },
    )
    rows = store.list_debug_events(client_id=client, module="performance")
    assert rows[0]["payload"] == {"cpu": 7.5}


def test_mutation_requires_one_use_matching_confirmation(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    client = _register(service)
    with pytest.raises(PermissionError, match="confirmation"):
        service.enqueue(
            client_id=client,
            name="storage.defaults_set",
            payload={"key": "a", "value": True},
        )
    token = service.mint_confirmation(client, "storage.defaults_set")["token"]
    service.enqueue(
        client_id=client,
        name="storage.defaults_set",
        payload={"key": "a", "value": True},
        confirmation=str(token),
    )
    with pytest.raises(PermissionError, match="invalid"):
        service.enqueue(
            client_id=client,
            name="storage.defaults_set",
            payload={"key": "b"},
            confirmation=str(token),
        )


def test_unknown_command_and_large_payload_rejected(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    client = _register(service)
    with pytest.raises(ValueError, match="allowlisted"):
        service.enqueue(client_id=client, name="shell.exec", payload={})
    with pytest.raises(OverflowError):
        service.enqueue(
            client_id=client,
            name="runtime.search",
            payload={"query": "x" * 70_000},
        )


def test_keychain_values_are_stripped_before_persistence(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    client = _register(service)
    service.ingest_event(
        client,
        {
            "module": "keychain",
            "kind": "metadata",
            "payload": {
                "items": [
                    {
                        "account": "user",
                        "service": "auth",
                        "value": "never-store-this",
                        "token": "never-store-this-either",
                    }
                ]
            },
        },
    )
    payload = store.list_debug_events(client_id=client)[0]["payload"]
    item = payload["items"][0]
    assert item == {"account": "user", "service": "auth"}
