"""Same app on two devices, and traffic filtered by who was signed in."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from nacl.secret import SecretBox

from viewlift_ai.app import create_app
from viewlift_ai.config import AppConfig, SecretKeyState
from viewlift_ai.crypto import SecretBoxCipher
from viewlift_ai.models.flow import FlowRecord, FlowSource, RetentionPolicy
from viewlift_ai.models.log import LogLevel, LogRecord
from viewlift_ai.services.debug_session import DebugSessionService
from viewlift_ai.services.encrypted_store import EncryptedSqliteStore
from viewlift_ai.services.event_bus import EventBus

KEY = b"k" * SecretBox.KEY_SIZE


class _Clock:
    def __init__(self) -> None:
        self.now = 1_700_000_000.0

    def __call__(self) -> float:
        return self.now


def _store(tmp_path: Path) -> EncryptedSqliteStore:
    return EncryptedSqliteStore(
        tmp_path / "spans.db", SecretBoxCipher(KEY), retention=RetentionPolicy.SESSION
    )


def _service(tmp_path: Path, clock: _Clock) -> tuple[DebugSessionService, EncryptedSqliteStore]:
    store = _store(tmp_path)
    return DebugSessionService(store=store, events=EventBus(), time_fn=clock), store


def _phone(client_id: str, device_key: str, *, model: str = "iPhone15,2") -> dict[str, object]:
    return {
        "client_id": client_id,
        "app_name": "CHSN",
        "bundle_id": "com.example.chsn",
        "platform": "iOS",
        "os_version": "18.0",
        "device_key": device_key,
        "device_name": "iPhone",
        "device_model": model,
        "is_simulator": False,
    }


def _flow(client_id: str, when: datetime, path: str) -> FlowRecord:
    return FlowRecord(
        id=str(uuid4()),
        method="GET",
        host="api.example.com",
        path=path,
        status=200,
        duration_ms=1.0,
        size_bytes=0,
        protocol="HTTPS",
        source=FlowSource.SDK,
        created_at=when,
        client_id=client_id,
    )


def _log(client_id: str, when: datetime, message: str) -> LogRecord:
    return LogRecord(
        id=str(uuid4()),
        level=LogLevel.INFO,
        subsystem="app",
        message=message,
        created_at=when,
        source="sdk",
        client_id=client_id,
    )


def _at(stamp: float) -> datetime:
    return datetime.fromtimestamp(stamp, UTC)


def test_raw_email_is_dropped_and_mask_is_kept(tmp_path: Path) -> None:
    service, store = _service(tmp_path, _Clock())
    service.register(
        {
            **_phone("phone", "vendor:aaaa"),
            "session": {
                "auth_state": "logged_in",
                "user_id": "user-1",
                "email": "ada@viewlift.com",
                "email_mask": "ada@viewlift.com",
                "profile_id": "profile-1",
                "login_provider": "apple",
            },
        }
    )
    row = service.get_client("phone")
    assert "email" not in row
    assert row["email_mask"] == ""
    assert row["user_id"] == "user-1"
    assert store.session_span_rows()[0]["email_mask"] == ""

    service.ingest_event(
        "phone",
        {
            "module": "session",
            "kind": "update",
            "payload": {
                "auth_state": "logged_in",
                "user_id": "user-1",
                "email_mask": "a***@viewlift.com",
                "profile_id": "profile-1",
                "login_provider": "apple",
            },
        },
    )
    assert service.get_client("phone")["email_mask"] == "a***@viewlift.com"
    assert service.list_events(client_id="phone") == []
    service.register(_phone("phone", "vendor:aaaa"))
    assert service.get_client("phone")["auth_state"] == "logged_in"
    assert len(store.session_span_rows()) == 1


def test_guest_then_login_splits_flows_and_logs(tmp_path: Path) -> None:
    clock = _Clock()
    service, store = _service(tmp_path, clock)
    service.register(_phone("phone", "vendor:aaaa"))
    service.ingest_event(
        "phone",
        {
            "module": "session",
            "kind": "update",
            "payload": {
                "auth_state": "guest",
                "user_id": "should-drop",
                "anonymous_id": "anon-1",
                "email": "ada@viewlift.com",
                "email_mask": "ada@viewlift.com",
            },
        },
    )
    assert service.get_client("phone")["user_id"] == ""
    assert service.get_client("phone")["email_mask"] == ""
    guest_at = _at(clock.now + 30)
    clock.now += 100
    login_at = _at(clock.now)
    service.ingest_event(
        "phone",
        {
            "module": "session",
            "kind": "update",
            "payload": {
                "auth_state": "logged_in",
                "user_id": "user-1",
                "anonymous_id": "anon-1",
                "email_mask": "a***@viewlift.com",
                "profile_id": "profile-a",
                "login_provider": "apple",
            },
        },
    )
    during_login = _at(clock.now + 30)
    clock.now += 100
    service.ingest_event(
        "phone",
        {
            "module": "session",
            "kind": "update",
            "payload": {
                "auth_state": "logged_in",
                "user_id": "user-1",
                "email_mask": "a***@viewlift.com",
                "profile_id": "profile-b",
                "login_provider": "apple",
            },
        },
    )
    during_profile = _at(clock.now + 30)
    for when, path in (
        (guest_at, "/guest"),
        (login_at, "/boundary"),
        (during_login, "/login"),
        (during_profile, "/profile"),
    ):
        store.save_flow(_flow("phone", when, path))
        store.save_log(_log("phone", when, path))

    def paths(spans: list[tuple[str, str, str | None]]) -> set[str]:
        return {item.path for item in store.list_flows(spans=spans)}

    guest = service.matching_spans(auth_state="guest", anonymous_id="anon-1")
    user = service.matching_spans(user_id="user-1")
    profile_a = service.matching_spans(profile_id="profile-a")
    assert paths(guest) == {"/guest"}
    assert paths(user) == {"/boundary", "/login", "/profile"}
    assert paths(profile_a) == {"/boundary", "/login"}
    assert {item.message for item in store.list_logs(spans=guest)} == {"/guest"}
    assert "/guest" not in paths(user)

    accounts = service.session_accounts()
    assert accounts["auth_states"] == ["guest", "logged_in"]
    (person,) = accounts["users"]
    assert person["email_mask"] == "a***@viewlift.com"
    assert person["login_provider"] == "apple"
    assert person["devices"] == 1
    assert person["profiles"] == ["profile-a", "profile-b"]
    assert accounts["guests"] == [{"anonymous_id": "anon-1", "devices": 1}]


def test_same_app_on_two_phones_stays_distinct(tmp_path: Path) -> None:
    config = AppConfig(
        data_dir=tmp_path,
        database_path=tmp_path / "ui.db",
        secret_key_path=tmp_path / "secret.key",
        retention=RetentionPolicy.SESSION,
        secret=SecretKeyState(
            key=b"t" * SecretBox.KEY_SIZE,
            used_keychain_fallback=True,
            fallback_path=tmp_path / "secret.key",
        ),
        proxy_enabled=False,
    )
    with TestClient(create_app(config)) as client:
        sessions = client.app.state.debug_sessions
        store = client.app.state.store
        sessions.register(_phone("phone-a", "vendor:aaaa", model="iPhone15,2"))
        sessions.register(_phone("phone-b", "vendor:bbbb", model="iPhone16,1"))
        sessions.record_session(
            "phone-a",
            {
                "auth_state": "logged_in",
                "user_id": "user-9",
                "anonymous_id": "",
                "email_mask": "a***@viewlift.com",
                "profile_id": "",
                "login_provider": "apple",
            },
        )
        client.app.state.ingest.record_request("192.168.1.42", "/v1/flows", client_id="phone-a")
        client.app.state.ingest.record_request("192.168.1.43", "/v1/flows", client_id="phone-b")
        later = datetime.now(UTC) + timedelta(seconds=5)
        earlier = datetime.now(UTC) - timedelta(hours=1)
        store.save_flow(_flow("phone-a", later, "/now"))
        store.save_flow(_flow("phone-a", earlier, "/before"))
        store.save_flow(_flow("phone-b", later, "/other"))

        tree = client.get("/api/apps").json()
        apps = [app for device in tree["devices"] for app in device["apps"]]
        by_id = {app["id"]: app for app in apps}
        assert set(by_id) == {"phone-a", "phone-b"}
        assert by_id["phone-a"]["device_name"] == by_id["phone-b"]["device_name"] == "iPhone"
        assert by_id["phone-a"]["device_model"] != by_id["phone-b"]["device_model"]
        assert by_id["phone-a"]["peer"] == "192.168.1.42"
        assert by_id["phone-b"]["peer"] == "192.168.1.43"
        assert by_id["phone-a"]["device_key"] != by_id["phone-b"]["device_key"]

        both = client.get("/api/flows", params={"bundle_id": "com.example.chsn"}).json()["flows"]
        assert {row["client_id"] for row in both} == {"phone-a", "phone-b"}
        one = client.get("/api/flows", params={"device_key": "vendor:aaaa"}).json()["flows"]
        assert {row["client_id"] for row in one} == {"phone-a"}
        user = client.get("/api/flows", params={"user_id": "user-9"}).json()["flows"]
        assert [row["path"] for row in user] == ["/now"]
        assert client.get("/api/flows", params={"auth_state": "guest"}).json()["flows"] == []
        missing = client.get("/api/logs", params={"bundle_id": "com.example.missing"}).json()
        assert missing["logs"] == []
