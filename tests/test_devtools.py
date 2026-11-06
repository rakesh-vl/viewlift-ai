"""Server-side toolkit actions."""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from nacl.secret import SecretBox
from PIL import Image

from viewlift_ai.app import create_app
from viewlift_ai.config import AppConfig, SecretKeyState
from viewlift_ai.models.flow import RetentionPolicy
from viewlift_ai.services.devtools import DevToolError, DevToolsService


def _client(tmp_path: Path) -> TestClient:
    cfg = AppConfig(
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
    return TestClient(create_app(cfg))


def test_yaml_roundtrip() -> None:
    service = DevToolsService()
    parsed = service.yaml_parse({"text": "name: api\nreplicas: 2\n"})
    assert parsed["documents"] == [{"name": "api", "replicas": 2}]
    dumped = service.yaml_dump({"data": parsed["documents"][0]})
    again = service.yaml_parse({"text": dumped["text"]})
    assert again["documents"] == [{"name": "api", "replicas": 2}]
    multi = service.yaml_dump({"data": [{"a": 1}, {"b": 2}], "multi": True})
    assert service.yaml_parse({"text": multi["text"]})["documents"] == [{"a": 1}, {"b": 2}]


def test_hash_and_bcrypt() -> None:
    service = DevToolsService()
    hashed = service.hash_data({"text": "viewlift", "algorithms": ["sha256", "crc32"]})
    assert hashed["digests"]["sha256"] == (
        "6443bfa25280ff1fb225586233b271b45cef1146ec1697e61ad02fcf4db1d615"
    )
    assert hashed["digests"]["crc32"] == "50c12d6e"
    made = service.bcrypt_hash({"password": "correct horse", "cost": 4})
    assert made["hash"].startswith("$2")
    verified = service.bcrypt_verify({"password": "correct horse", "hash": made["hash"]})
    assert verified["match"] is True
    assert service.bcrypt_verify({"password": "nope", "hash": made["hash"]})["match"] is False


def test_keys_csr_and_ssh() -> None:
    service = DevToolsService()
    ed = service.ed25519_keypair({})
    assert "BEGIN PRIVATE KEY" in ed["private_pem"]
    assert len(base64.b64decode(ed["public_raw_base64"])) == 32
    ssh = service.ssh_keypair({"type": "ed25519", "comment": "dev@mac"})
    assert ssh["public_openssh"].startswith("ssh-ed25519 ")
    assert ssh["public_openssh"].rstrip().endswith("dev@mac")
    assert ssh["fingerprint_sha256"].startswith("SHA256:")
    csr = service.csr(
        {"common_name": "api.example.com", "key_type": "ec", "sans": ["api.example.com"]}
    )
    assert "BEGIN CERTIFICATE REQUEST" in csr["csr_pem"]
    assert "BEGIN PRIVATE KEY" in csr["private_key_pem"]


def test_cuid2_shape() -> None:
    service = DevToolsService()
    made = service.cuid2({"count": 3, "length": 12})
    assert len(made["ids"]) == 3
    assert all(len(item) == 12 and item[0].islower() for item in made["ids"])


def test_http_rejects_non_http_and_dns_validates_name() -> None:
    calls: list[list[str]] = []

    def runner(args, **_kwargs):  # noqa: ANN001
        calls.append(args)
        raise AssertionError("lookup should not run")

    service = DevToolsService(runner=runner)
    with pytest.raises(DevToolError, match="http"):
        service.http_request({"method": "GET", "url": "ftp://example.com/file"})
    with pytest.raises(DevToolError, match="host name"):
        service.dns_lookup({"name": "not a host", "type": "A"})
    assert calls == []


def test_image_convert_png() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 3), (10, 20, 30)).save(buffer, format="PNG")
    service = DevToolsService()
    result = service.image_convert(
        {"data": base64.b64encode(buffer.getvalue()).decode("ascii"), "format": "PNG"}
    )
    assert result["width"] == 2
    assert result["height"] == 3
    assert result["mime"] == "image/png"
    assert base64.b64decode(result["data"])[:8] == b"\x89PNG\r\n\x1a\n"


def test_action_route_status(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        missing_type = client.post("/api/tools/action/hash", content=b"{}")
        assert missing_type.status_code == 415
        unknown = client.post("/api/tools/action/nope", json={})
        assert unknown.status_code == 404
        hashed = client.post(
            "/api/tools/action/hash",
            json={"text": "viewlift", "algorithms": ["crc32"]},
        )
        assert hashed.status_code == 200
        assert hashed.json()["digests"]["crc32"] == "50c12d6e"
