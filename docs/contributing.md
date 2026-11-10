# Contributing

## Checks

Python (from the repository root):

```bash
uv sync --extra dev --extra mcp
uv run pytest
uv run ruff check src tests
```

Swift package:

```bash
cd ios/ViewliftNetworkCapture
swift test
swiftlint lint --config .swiftlint.yml
xcodebuild test -scheme ViewliftNetworkCapture -destination 'platform=iOS Simulator,name=iPhone 16'
xcodebuild test -scheme ViewliftNetworkCapture -destination 'platform=tvOS Simulator,name=Apple TV'
```

`swift test` runs on macOS, where UIKit paths are compiled out. Run the `xcodebuild` lines for iOS and tvOS before merging Swift changes; both platforms must build with no errors and no SwiftLint violations. If a change touches the AppCMS host files, also build **AppCMS** and **AppCMS_tvOS** in Debug.

Delete any one-off scripts or log files you create while debugging before you commit.

## Python conventions

- Python 3.12, `from __future__ import annotations`, and a docstring at the top of each module.
- Ruff: target py312, line length 100, rules `E`, `F`, `I`, `UP`, `B`.
- Models are dataclasses with `slots=True`; enums use `StrEnum`.
- Services in `services/` and helpers in `proxy/` must not import FastAPI. `tests/test_services_isolation.py` enforces this.
- Inject collaborators (clock functions, subprocess runners, interface listers, stream factories) so services can be tested without real devices or processes.
- Subprocesses always take an argument list, never `shell=True`.
- Redact before anything leaves the store: display, export, MCP output.

## Python tests

pytest runs with `asyncio_mode = "auto"`, `testpaths = ["tests"]` and `pythonpath = ["src"]`. There is no shared `conftest.py`; each test file builds its own fixtures.

For API tests, build an app with an explicit config and use `TestClient`:

```python
from fastapi.testclient import TestClient
from nacl.secret import SecretBox

from viewlift_ai.app import create_app
from viewlift_ai.config import AppConfig, SecretKeyState
from viewlift_ai.models.flow import RetentionPolicy


def _config(tmp_path):
    return AppConfig(
        data_dir=tmp_path,
        database_path=tmp_path / "test.db",
        secret_key_path=tmp_path / "secret.key",
        retention=RetentionPolicy.SESSION,
        secret=SecretKeyState(
            key=b"a" * SecretBox.KEY_SIZE,
            used_keychain_fallback=True,
            fallback_path=tmp_path / "secret.key",
        ),
        proxy_enabled=False,
    )


def test_health(tmp_path):
    with TestClient(create_app(_config(tmp_path))) as client:
        assert client.get("/health").status_code == 200
```

`create_app` resets every singleton, so tests do not share state. Loopback guards accept the `testclient` host.

## Swift conventions

- MVVM with service classes. Features are services behind protocols (`Protocols.swift`); `CaptureService` composes them. Host apps should call the SDK from a debug-only service, not from view models.
- Respect `.swiftlint.yml`. Do not disable a rule unless there is no alternative; if you do, disable it for the smallest block and say which rule and why in the pull request.
- Wrap debug-only code in `#if DEBUG || VIEWLIFT_CAPTURE_ENABLED`.
- Parse untrusted payloads defensively (the package uses `JSONValue`). In AppCMS host code, use SwiftyJSON from `ViewliftOne/Core/Utils`.
- No third-party dependencies in the package.

## Recipes

### Add an API route

1. Put logic in a service under `services/` (no FastAPI imports) and expose it through a getter in `app.py` if it is a new singleton.
2. Add the route to the matching router in `api/`, or create a router and register it with `app.include_router(...)` in `app.py`.
3. Call the loopback guard for anything that reveals data or acts on a device.
4. Accept `client_id` and `device_key` and pass them through `resolve_client_ids` if the data is per app.
5. Add a test with `TestClient`, and document the route in [API reference](api-reference.md).

### Add a service

1. Create `services/<name>.py` with a class whose dependencies are constructor arguments.
2. Add a protocol in `services/protocols.py` if other services depend on it.
3. Unit-test it directly, without FastAPI.

### Add a debug command

Python side:

1. Add the name to `READ_COMMANDS` or `MUTATING_COMMANDS` in [services/debug_session.py](../src/viewlift_ai/services/debug_session.py).
2. Add a note to `COMMAND_NOTES` in [mcp_server/catalog.py](../src/viewlift_ai/mcp_server/catalog.py). `tests/test_agent_page.py` fails if any command lacks a note.
3. Add UI in [debug.js](../src/viewlift_ai/web/static/js/debug.js) and [debug_suite.html](../src/viewlift_ai/web/templates/debug_suite.html) if the Debug Suite should show it.

Swift side:

1. Add a `case` in the module's handler in [DebugCommandExecutor.swift](../ios/ViewliftNetworkCapture/Sources/ViewliftNetworkCapture/DebugCommandExecutor.swift). The prefix before the dot must be an existing `DebugModule`, or add a new case to `DebugModule`.
2. Implement the work in the relevant service, with size limits on anything returned.
3. Return `[String: JSONValue]`. Throw `DebugBridgeError.invalidPayload` for bad input.
4. Add an XCTest where the logic does not need UIKit.

Then update the command tables in [iOS SDK internals](ios-sdk-internals.md#commands) and [MCP server](mcp-server.md#on-device-commands).

### Allowlist

The proxy decrypts only hosts in `RulesConfig.allowlist`. Persist them through `RuleEngine.set_allowlist`. The Setup page calls `PUT /api/rules/allowlist`. Do not add map, rewrite, throttle, or breakpoint rules.

### Add an MCP tool

See [MCP server, adding a tool](mcp-server.md#adding-a-tool).

## Documentation

Update the relevant page in `docs/` when you change behavior, routes, environment variables or commands.
