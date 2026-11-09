"""Agent safety layer: secret scrubbing, policy switches, and a call audit. No MCP SDK import.

Every console response that reaches an agent passes through `scrub`. Credentials are masked
even when the console reveal session is unlocked, so an agent never sees auth tokens, API keys,
cookies, passwords, signed CDN parameters, or private keys.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

MASK = "[REDACTED]"
MAX_STRING = 16_000
_MAX_JSON_PARSE = 2_000_000

READ_ONLY_ENV = "VIEWLIFT_AI_MCP_READ_ONLY"
ALLOW_REVEAL_ENV = "VIEWLIFT_AI_MCP_ALLOW_REVEAL"

# Normalized key fragments: lowercase with "-", "_", ".", and spaces removed.
_KEY_FRAGMENTS = (
    "authorization",
    "token",
    "secret",
    "password",
    "passwd",
    "passphrase",
    "apikey",
    "accesskey",
    "privatekey",
    "credential",
    "cookie",
    "sessionid",
    "csrf",
    "xsrf",
    "bearer",
    "jwt",
    "fairplaycert",
    "spc",
    "ckc",
)
_KEY_EXACT = frozenset({
    "auth", "otp", "pin", "pwd", "sig", "sid", "ssn", "cvv", "cvc",
    "parentalpin", "pincode", "userpin",
})
# Keys that are never secrets even though they contain a fragment above.
_KEY_SAFE = frozenset({"tokencount", "hastoken", "tokenexpired", "cookiecount"})
# Inside a masked container these label fields stay readable so the agent can see names.
_LABEL_KEYS = frozenset({"name", "key", "domain", "path", "label", "type", "class", "expires"})
_PAIR_VALUE_KEYS = ("value", "preview", "description", "raw", "string")
_PASSTHROUGH_KEYS = frozenset({"base64"})
_DROP_KEYS = frozenset({"unlock_token", "data_url", "confirmation", "confirmation_token"})

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        MASK,
    ),
    (re.compile(r"(?i)\b(Bearer|Basic|Digest|Token)\s+[A-Za-z0-9._~+/=-]{6,}"), rf"\1 {MASK}"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{4,}"), MASK),
    (re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"), MASK),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), MASK),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), MASK),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), MASK),
    (re.compile(r"\b(sk|rk|pk)_(live|test)_[A-Za-z0-9]{10,}\b"), MASK),
    (
        re.compile(
            r"(?i)(^|[?&;#])("
            r"(?:access_|refresh_|id_|auth_)?token|api[_-]?key|apikey|key|sig|signature|secret"
            r"|client_secret|password|passwd|auth|session(?:_?id)?|hdnts|hdnea|policy"
            r"|key-pair-id|x-amz-signature|x-amz-credential|x-amz-security-token|jwt"
            r")=([^&#\s\"']+)"
        ),
        rf"\1\2={MASK}",
    ),
    (
        re.compile(
            r"(?i)\b("
            r"api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|auth[_-]?token"
            r"|client[_-]?secret|token|secret|password|passwd|authorization|x-api-key"
            r")(\"?'?\s*[:=]\s*\"?'?)(?!\[REDACTED\])(?!Bearer\b|Basic\b)([^\s\"'&,;}\]]{3,})"
        ),
        rf"\1\2{MASK}",
    ),
)


def _normalize(key: str) -> str:
    return re.sub(r"[-_.\s]", "", key.lower())


def is_sensitive_key(key: object) -> bool:
    if not isinstance(key, str) or not key:
        return False
    norm = _normalize(key)
    if norm in _KEY_SAFE:
        return False
    if norm in _KEY_EXACT:
        return True
    return any(fragment in norm for fragment in _KEY_FRAGMENTS)


@dataclass(slots=True)
class ScrubReport:
    masked: int = 0
    truncated: int = 0


def scrub_text(text: str, report: ScrubReport | None = None) -> str:
    """Mask credential patterns in free text and truncate very long strings."""
    report = report if report is not None else ScrubReport()
    if not text:
        return text
    stripped = text.lstrip()
    if stripped[:1] in {"{", "["} and len(text) <= _MAX_JSON_PARSE:
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, RecursionError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            cleaned = _scrub(parsed, report, force=False)
            indent = 2 if "\n" in text else None
            separators = None if indent else (",", ":")
            text = json.dumps(cleaned, indent=indent, separators=separators, ensure_ascii=False)
    for pattern, replacement in _PATTERNS:
        text, count = pattern.subn(replacement, text)
        report.masked += count
    if len(text) > MAX_STRING:
        report.truncated += 1
        text = f"{text[:MAX_STRING]}…[truncated {len(text) - MAX_STRING} chars]"
    return text


def _mask_value(value: Any, report: ScrubReport) -> Any:
    if isinstance(value, str):
        if not value or value == MASK:
            return value
        report.masked += 1
        return MASK
    if isinstance(value, (dict, list)):
        return _scrub(value, report, force=True)
    return value


def _scrub_pair(obj: Mapping[str, Any], report: ScrubReport, force: bool) -> dict[str, Any]:
    label = obj.get("name") if isinstance(obj.get("name"), str) else obj.get("key")
    pair_secret = is_sensitive_key(label)
    out: dict[str, Any] = {}
    for key, value in obj.items():
        if key in _DROP_KEYS:
            report.masked += 1
            continue
        if key in _PASSTHROUGH_KEYS:
            out[key] = value
        elif pair_secret and key in _PAIR_VALUE_KEYS:
            out[key] = _mask_value(value, report)
        elif is_sensitive_key(key):
            out[key] = _mask_value(value, report)
        elif key in _LABEL_KEYS:
            out[key] = _scrub(value, report, force=False)
        elif force:
            out[key] = _mask_value(value, report)
        else:
            out[key] = _scrub(value, report, force=False)
    return out


def _scrub(value: Any, report: ScrubReport, *, force: bool) -> Any:
    if isinstance(value, dict):
        return _scrub_pair(value, report, force)
    if isinstance(value, list):
        return [_scrub(item, report, force=force) for item in value]
    if isinstance(value, tuple):
        return [_scrub(item, report, force=force) for item in value]
    if isinstance(value, str):
        return _mask_value(value, report) if force else scrub_text(value, report)
    return value


def scrub(value: Any, report: ScrubReport | None = None) -> Any:
    """Return a copy with every credential masked. Never mutates the input."""
    return _scrub(value, report if report is not None else ScrubReport(), force=False)


# Extra keys masked only on the MCP path. The web dashboard still receives the original values.
_AGENT_KEY_FRAGMENTS = (
    "email",
    "phone",
    "mobile",
    "userid",
    "anonymousid",
    "deviceid",
    "household",
    "ipaddress",
    "zipcode",
    "postal",
    "firstname",
    "lastname",
    "nickname",
    "fullname",
    "birth",
    "latitude",
    "longitude",
    "profileid",
    # DRM and partner secrets from the framework sample hosts.
    "license",
    "skd",
    "softwarestatement",
    "cloudfront",
    "keypair",
)
_AGENT_KEY_EXACT = frozenset({
    "email", "ip", "city", "dob", "zip", "address", "username",
    "nickname", "fullname", "firstname", "lastname",
})
_AGENT_NAME_SIBLINGS = frozenset({"email", "userid", "loggedin", "nickname"})
_EMAIL_TEXT = re.compile(r"(?i)\b[a-z0-9*._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b")
_IPV4_TEXT = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
)


def is_agent_pii_key(key: object) -> bool:
    """True for account, contact, and device identifiers an agent must not receive."""
    if not isinstance(key, str) or not key:
        return False
    norm = _normalize(key)
    if norm in _AGENT_KEY_EXACT:
        return True
    return any(fragment in norm for fragment in _AGENT_KEY_FRAGMENTS)


def _mask_pii_text(text: str, report: ScrubReport) -> str:
    if not text or text == MASK:
        return text
    cleaned, count = _EMAIL_TEXT.subn(MASK, text)
    report.masked += count
    cleaned, count = _IPV4_TEXT.subn(MASK, cleaned)
    report.masked += count
    return cleaned


def _mask_agent_value(value: Any, report: ScrubReport) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        report.masked += 1
        return MASK
    return _mask_value(value, report)


def _scrub_agent(value: Any, report: ScrubReport) -> Any:
    if isinstance(value, dict):
        norms = {_normalize(str(key)) for key in value}
        person_record = bool(norms & _AGENT_NAME_SIBLINGS)
        out: dict[str, Any] = {}
        for key, item in value.items():
            hide_name = person_record and key == "name"
            if hide_name or is_agent_pii_key(key):
                out[key] = _mask_agent_value(item, report)
            else:
                out[key] = _scrub_agent(item, report)
        return out
    if isinstance(value, list):
        return [_scrub_agent(item, report) for item in value]
    if isinstance(value, str):
        return _mask_pii_text(value, report)
    return value


def scrub_agent(value: Any, report: ScrubReport | None = None) -> Any:
    """Credential scrub plus account PII. Used only for MCP tool results."""
    report = report if report is not None else ScrubReport()
    return _scrub_agent(scrub(value, report), report)


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def read_only() -> bool:
    return _flag(READ_ONLY_ENV)


def reveal_allowed() -> bool:
    return _flag(ALLOW_REVEAL_ENV)


def read_only_refusal() -> dict[str, object] | None:
    if not read_only():
        return None
    return {
        "ok": False,
        "detail": f"MCP server is read-only ({READ_ONLY_ENV}=1). State-changing tools are off.",
    }


def policy() -> dict[str, object]:
    return {
        "read_only": read_only(),
        "reveal_allowed": reveal_allowed(),
        "always_masked": [
            "Authorization, Proxy-Authorization, Cookie, Set-Cookie, and X-Api-Key headers",
            "JSON keys and name/value pairs containing token, secret, password, apikey, "
            "credential, cookie, session id, csrf, or jwt",
            "Bearer, Basic, and JWT values inside any text",
            "Signed URL parameters (token, sig, signature, key, hdnts, Policy, X-Amz-*)",
            "AWS, Google, GitHub, Slack, and Stripe key formats, and PEM private keys",
            "FairPlay SPC, CKC, and certificate material",
        ],
        "agent_only_masked": [
            "Emails, phone numbers, and IP addresses in any MCP text",
            "User id, anonymous id, profile id, device id, and household id",
            "Name on a user record, nickname, postal code, and date of birth",
            "The web dashboard is not masked this way",
        ],
        "never_returned": ["reveal unlock tokens", "mutation confirmation tokens"],
        "limits": {"max_string_chars": MAX_STRING},
        "switches": {
            READ_ONLY_ENV: "1 turns off every state-changing tool",
            ALLOW_REVEAL_ENV: "1 lets get_flow reveal=true read raw bodies. Secrets stay masked",
        },
    }


@dataclass(slots=True)
class AuditLog:
    """In-memory record of tool calls for this MCP process. Arguments are scrubbed."""

    capacity: int = 200
    _rows: deque[dict[str, Any]] = field(default_factory=deque)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, tool: str, args: Mapping[str, Any], result: Any, duration_ms: float) -> None:
        ok = not (isinstance(result, dict) and result.get("ok") is False)
        row = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "tool": tool,
            "args": scrub_agent({k: v for k, v in args.items() if v not in (None, "", {}, [])}),
            "ok": ok,
            "duration_ms": round(duration_ms, 1),
        }
        with self._lock:
            self._rows.append(row)
            while len(self._rows) > self.capacity:
                self._rows.popleft()

    def rows(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._rows)
        return items[-max(1, min(limit, self.capacity)) :][::-1]


AUDIT = AuditLog()
