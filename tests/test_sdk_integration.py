from viewlift_ai.services.debug_advertise import SERVICE_TYPE
from viewlift_ai.services.sdk_integration import START_CALL, sdk_integration


def test_disabled_receiver_has_start_call_and_no_overrides() -> None:
    result = sdk_integration(enabled=False, host=None, port=None, fingerprint="ab")
    assert result["bonjour_service"] == SERVICE_TYPE
    assert result["env_overrides"] == {}
    assert START_CALL in str(result["snippet"])
    assert "VIEWLIFT_CAPTURE_HOST=" not in str(result["snippet"])


def test_enabled_receiver_uses_live_host_port_and_fingerprint() -> None:
    result = sdk_integration(enabled=True, host="10.0.0.8", port=8742, fingerprint="abcd")
    snippet = str(result["snippet"])
    assert "VIEWLIFT_CAPTURE_HOST=10.0.0.8" in snippet
    assert "VIEWLIFT_CAPTURE_PORT=8742" in snippet
    assert "VIEWLIFT_CAPTURE_FINGERPRINT=abcd" in snippet
    assert "DEBUG_KEY=" not in snippet
