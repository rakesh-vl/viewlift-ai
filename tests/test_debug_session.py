"""Remote debug sessions: command allowlist, confirmation, bounds, encryption."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from nacl.secret import SecretBox

from viewlift_ai.crypto import SecretBoxCipher
from viewlift_ai.models.flow import RetentionPolicy
from viewlift_ai.services.debug_session import (
    CLIENT_TTL_SECONDS,
    DebugCommandInterrupted,
    DebugCommandTimeout,
    DebugSessionService,
)
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


def test_register_keeps_reported_app_identity(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    service.register(
        {
            "client_id": "phone-2",
            "app_name": "Demo",
            "bundle_id": "com.example.demo",
            "app_version": "2.4",
            "build": "77",
            "executable": "Demo",
            "url_schemes": ["demo", "demo", "bad scheme"],
            "frameworks": [
                {"id": "org.alamofire.Alamofire", "version": "5.9.1", "name": "Alamofire"},
                {"id": "org.alamofire.Alamofire", "version": "dup"},
                "com.example.Plain",
            ],
        }
    )

    client = service.get_client("phone-2")

    assert client["app_version"] == "2.4"
    assert client["executable"] == "Demo"
    assert client["url_schemes"] == ["demo"]
    assert client["framework_count"] == 2
    assert client["frameworks"][1] == {
        "id": "org.alamofire.Alamofire",
        "version": "5.9.1",
        "name": "Alamofire",
    }
    assert service.latest_client_id() == "phone-2"


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


def test_playback_digest_is_not_stored(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    client = _register(service)
    service.ingest_event(
        client,
        {
            "module": "playback",
            "kind": "digest",
            "payload": {"status": "playing", "live_edge_lag_sec": 4.2},
        },
    )
    assert store.list_debug_events(client_id=client, module="playback") == []


def test_activity_live_events_are_ephemeral_and_allowed(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    client = _register(service)
    service.ingest_event(
        client,
        {"module": "activity", "kind": "live", "payload": {"screen_class": "Player"}},
    )
    service.ingest_event(
        client,
        {"module": "activity", "kind": "frame", "payload": {"mime": "image/jpeg", "base64": "aa"}},
    )
    assert store.list_debug_events(client_id=client, module="activity") == []
    for name in ("activity.snapshot", "activity.live_start", "activity.live_stop"):
        assert service.enqueue(client_id=client, name=name, payload={})["name"] == name


def test_live_memory_events_publish_without_persisting(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    client = _register(service)
    service.ingest_event(
        client, {"module": "memory", "kind": "live", "payload": {"tracked_count": 3}}
    )
    service.ingest_event(
        client, {"module": "memory", "kind": "leak", "payload": {"class": "DetailVC"}}
    )
    rows = store.list_debug_events(client_id=client, module="memory")
    assert [row["kind"] for row in rows] == ["leak"]
    assert (
        service.enqueue(client_id=client, name="memory.live_start", payload={})["name"]
        == "memory.live_start"
    )


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


@pytest.mark.parametrize(
    "name",
    [
        "explorer.set",
        "explorer.call",
        "storage.defaults_delete",
        "storage.file_rename",
        "storage.file_delete",
        "storage.cookie_delete",
        "settings.set",
        "ott.action",
    ],
)
def test_explorer_and_settings_writes_need_confirmation(tmp_path: Path, name: str) -> None:
    service, _ = _service(tmp_path)
    client = _register(service)
    with pytest.raises(PermissionError, match="confirmation"):
        service.enqueue(client_id=client, name=name, payload={})
    token = service.mint_confirmation(client, name)["token"]
    service.enqueue(client_id=client, name=name, payload={}, confirmation=str(token))


@pytest.mark.parametrize(
    "name",
    [
        "explorer.roots",
        "explorer.object",
        "explorer.instances",
        "runtime.libraries",
        "runtime.image_classes",
        "storage.cookies",
        "settings.get",
        "ott.sections",
        "ott.section",
        "ott.diagnostics",
    ],
)
def test_explorer_and_settings_reads_need_no_confirmation(tmp_path: Path, name: str) -> None:
    service, _ = _service(tmp_path)
    client = _register(service)
    service.enqueue(client_id=client, name=name, payload={"handle": "h1"})


def test_ott_results_stay_visible_for_the_dashboard(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    client = _register(service)
    service.ingest_event(
        client,
        {
            "module": "ott",
            "kind": "command-result",
            "payload": {
                "name": "ott.section",
                "result": {
                    "data": {
                        "authorizationToken": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abcdefgh",
                        "parentalPin": "1234",
                        "token_present": True,
                        "site_id": "site-1",
                    }
                },
            },
        },
    )
    data = store.list_debug_events(client_id=client, module="ott")[0]["payload"]["result"]["data"]
    assert data["authorizationToken"].startswith("eyJ")
    assert data["parentalPin"] == "1234"
    assert data["token_present"] is True
    assert data["site_id"] == "site-1"


def test_storage_defaults_result_stays_visible_for_the_dashboard(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    client = _register(service)
    service.ingest_event(
        client,
        {
            "module": "storage",
            "kind": "command-result",
            "payload": {
                "name": "storage.defaults",
                "result": {"values": {"refreshToken": "abc-123-secret", "autoPlay": True}},
            },
        },
    )
    values = store.list_debug_events(client_id=client, module="storage")[0]["payload"]["result"]
    assert values["values"]["refreshToken"] == "abc-123-secret"
    assert values["values"]["autoPlay"] is True


def test_other_module_results_are_not_scrubbed(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    client = _register(service)
    service.ingest_event(
        client,
        {
            "module": "storage",
            "kind": "command-result",
            "payload": {"name": "storage.files", "token": "x1y2z3"},
        },
    )
    payload = store.list_debug_events(client_id=client, module="storage")[0]["payload"]
    assert payload["token"] == "x1y2z3"


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


def test_two_apps_on_one_device_stay_distinct(tmp_path: Path) -> None:
    service, _store = _service(tmp_path)
    shared = {
        "device_key": "SIM-1",
        "device_name": "iPhone 16",
        "device_model": "iPhone17,3",
        "is_simulator": True,
        "platform": "iOS",
        "os_version": "26",
    }
    service.register({**shared, "client_id": "app-a", "app_name": "Alpha", "bundle_id": "com.a"})
    service.register({**shared, "client_id": "app-b", "app_name": "Beta", "bundle_id": "com.b"})
    service.register(
        {
            "client_id": "app-c",
            "app_name": "Gamma",
            "bundle_id": "com.c",
            "device_key": "SIM-2",
            "device_name": "iPhone 15",
            "is_simulator": True,
        }
    )

    saved = service.get_client("app-a")
    assert saved["device_key"] == "SIM-1"
    assert saved["device_name"] == "iPhone 16"
    assert saved["device_model"] == "iPhone17,3"
    assert saved["is_simulator"] is True

    grouped = {str(item["device_key"]): item["apps"] for item in service.list_devices()}
    assert {app["id"] for app in grouped["SIM-1"]} == {"app-a", "app-b"}
    assert {app["id"] for app in grouped["SIM-2"]} == {"app-c"}


def _answer(service: DebugSessionService, client_id: str, result: dict[str, object]) -> None:
    for _ in range(100):
        polled = service.poll(client_id)
        if polled:
            service.ingest_event(
                client_id,
                {
                    "module": "performance",
                    "kind": "command-result",
                    "payload": {
                        "command_id": polled[0]["id"],
                        "name": polled[0]["name"],
                        "ok": True,
                        "result": result,
                    },
                },
            )
            return
        time.sleep(0.01)


def test_apps_from_different_vendors_on_one_phone_share_a_device(tmp_path: Path) -> None:
    service, _store = _service(tmp_path)
    phone = {"device_name": "iPhone", "device_model": "iPhone17,1", "platform": "iOS"}
    service.register(
        {
            **phone,
            "client_id": "a",
            "bundle_id": "com.a",
            "device_key": "vendor:A",
            "device_hint": "h1",
        }
    )
    service.register(
        {
            **phone,
            "client_id": "b",
            "bundle_id": "com.b",
            "device_key": "vendor:B",
            "device_hint": "h1",
        }
    )
    service.register(
        {
            **phone,
            "client_id": "c",
            "bundle_id": "com.c",
            "device_key": "vendor:C",
            "device_hint": "h2",
        }
    )
    groups = [sorted(app["id"] for app in item["apps"]) for item in service.list_devices()]
    assert sorted(groups) == [["a", "b"], ["c"]]
    assert service.get_client("a")["device_hint"] == "h1"


def test_invoke_returns_matching_event(tmp_path: Path) -> None:
    service, _store = _service(tmp_path)
    client = _register(service)
    threading.Thread(
        target=_answer,
        args=(service, client, {"cpu": 1.5}),
        daemon=True,
    ).start()
    result = service.invoke(client_id=client, name="performance.snapshot", payload={}, timeout=2)
    event = result["event"]
    assert isinstance(event, dict)
    assert event["payload"]["command_id"] == result["command"]["id"]
    assert event["payload"]["result"] == {"cpu": 1.5}


def test_invoke_rejects_mutate_without_flag(tmp_path: Path) -> None:
    service, _store = _service(tmp_path)
    client = _register(service)
    with pytest.raises(PermissionError, match="mutate"):
        service.invoke(
            client_id=client,
            name="feature_flags.set",
            payload={"key": "a", "enabled": True},
        )
    assert service.poll(client) == []


def test_invoke_with_mutate_flag_waits(tmp_path: Path) -> None:
    service, _store = _service(tmp_path)
    client = _register(service)
    threading.Thread(
        target=_answer,
        args=(service, client, {"updated": True}),
        daemon=True,
    ).start()
    result = service.invoke(
        client_id=client,
        name="feature_flags.set",
        payload={"key": "a", "enabled": True},
        mutate=True,
        timeout=2,
    )
    event = result["event"]
    assert isinstance(event, dict)
    assert event["payload"]["result"] == {"updated": True}


def test_invoke_rejects_missing_and_offline_clients(tmp_path: Path) -> None:
    service, _store = _service(tmp_path)
    with pytest.raises(LookupError, match="required"):
        service.invoke(client_id="", name="performance.snapshot", payload={})
    client = _register(service)
    base = service.time_fn()
    service.time_fn = lambda: base + CLIENT_TTL_SECONDS + 5
    with pytest.raises(LookupError, match="offline"):
        service.invoke(client_id=client, name="performance.snapshot", payload={})
    assert service.poll(client) == []


def _poll_one(service: DebugSessionService, client_id: str) -> dict[str, object]:
    for _ in range(200):
        polled = service.poll(client_id)
        if polled:
            return polled[0]
        time.sleep(0.01)
    raise AssertionError("no command was queued")


def _register_again(service: DebugSessionService) -> None:
    service.register(
        {
            "client_id": "phone-1",
            "app_name": "App",
            "bundle_id": "com.example.app",
            "platform": "iOS",
            "os_version": "26",
            "capabilities": {"performance": "available"},
        }
    )


def test_timeout_before_delivery_names_queued_phase_and_drops_command(tmp_path: Path) -> None:
    service, _store = _service(tmp_path)
    client = _register(service)
    with pytest.raises(DebugCommandTimeout) as caught:
        service.invoke(client_id=client, name="performance.snapshot", payload={}, timeout=0.3)
    assert caught.value.phase == "queued"
    assert caught.value.retryable is True
    assert "timed out" in str(caught.value)
    assert service.poll(client) == []


def test_timeout_after_delivery_names_the_delivered_phase(tmp_path: Path) -> None:
    service, _store = _service(tmp_path)
    client = _register(service)
    threading.Thread(target=_poll_one, args=(service, client), daemon=True).start()
    with pytest.raises(DebugCommandTimeout) as caught:
        service.invoke(client_id=client, name="performance.snapshot", payload={}, timeout=0.5)
    assert caught.value.phase == "delivered"


def test_read_is_sent_again_when_the_app_registers_mid_command(tmp_path: Path) -> None:
    service, _store = _service(tmp_path)
    client = _register(service)

    def flaky_device() -> None:
        _poll_one(service, client)
        _register_again(service)
        again = _poll_one(service, client)
        service.ingest_event(
            client,
            {
                "module": "performance",
                "kind": "command-result",
                "payload": {
                    "command_id": again["id"],
                    "name": again["name"],
                    "ok": True,
                    "result": {"cpu": 2},
                },
            },
        )

    threading.Thread(target=flaky_device, daemon=True).start()
    result = service.invoke(client_id=client, name="performance.snapshot", payload={}, timeout=3)
    event = result["event"]
    assert isinstance(event, dict)
    assert event["payload"]["result"] == {"cpu": 2}


def test_mutating_command_is_not_repeated_on_mid_command_register(tmp_path: Path) -> None:
    service, _store = _service(tmp_path)
    client = _register(service)

    def flaky_device() -> None:
        _poll_one(service, client)
        _register_again(service)

    threading.Thread(target=flaky_device, daemon=True).start()
    with pytest.raises(DebugCommandInterrupted) as caught:
        service.invoke(
            client_id=client,
            name="feature_flags.set",
            payload={"key": "a", "enabled": True},
            mutate=True,
            timeout=3,
        )
    assert caught.value.retryable is True
    assert service.poll(client) == []
