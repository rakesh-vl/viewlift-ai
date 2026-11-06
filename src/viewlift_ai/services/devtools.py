"""Server-side helpers for the Tools page: work the browser cannot do on its own."""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import io
import ipaddress
import json
import math
import re
import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import zlib
from collections.abc import Callable
from typing import Any

MAX_TEXT_CHARS = 2_000_000
MAX_BINARY_BYTES = 25 * 1024 * 1024
MAX_HTTP_BODY_BYTES = 2 * 1024 * 1024
HASH_ALGORITHMS = ("md5", "sha1", "sha224", "sha256", "sha384", "sha512", "sha3_256", "sha3_512")
DNS_TYPES = ("A", "AAAA", "CNAME", "MX", "NS", "TXT", "SOA", "SRV", "CAA", "PTR", "ANY")
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
IMAGE_FORMATS = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
    "BMP": "image/bmp",
    "GIF": "image/gif",
    "TIFF": "image/tiff",
}
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9_-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9_-]{1,63}(?<!-))*\.?$"
)
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")

CommandRunner = Callable[..., Any]


class DevToolError(Exception):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class DevToolsService:
    """Named actions with validated JSON payloads. Each returns a JSON-safe dict."""

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner: CommandRunner = runner or subprocess.run
        self._actions: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "yaml-parse": self.yaml_parse,
            "yaml-dump": self.yaml_dump,
            "hash": self.hash_data,
            "bcrypt-hash": self.bcrypt_hash,
            "bcrypt-verify": self.bcrypt_verify,
            "rsa-keypair": self.rsa_keypair,
            "ed25519-keypair": self.ed25519_keypair,
            "ssh-keypair": self.ssh_keypair,
            "csr": self.csr,
            "cuid2": self.cuid2,
            "dns": self.dns_lookup,
            "whois": self.whois_lookup,
            "ip-info": self.ip_info,
            "http-request": self.http_request,
            "image-metadata": self.image_metadata,
            "image-strip": self.image_strip,
            "image-convert": self.image_convert,
        }

    @property
    def action_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._actions))

    def run(self, name: str, payload: Any) -> dict[str, Any]:
        action = self._actions.get(name)
        if action is None:
            raise DevToolError("Unknown tool action.", status=404)
        if not isinstance(payload, dict):
            raise DevToolError("JSON object required.")
        return action(payload)

    # YAML

    def yaml_parse(self, payload: dict[str, Any]) -> dict[str, Any]:
        from ruamel.yaml import YAML
        from ruamel.yaml.error import MarkedYAMLError, YAMLError

        text = _text(payload, "text", allow_empty=True)
        loader = YAML(typ="safe", pure=True)
        try:
            documents = [_json_safe(doc) for doc in loader.load_all(text)]
        except MarkedYAMLError as exc:
            mark = exc.problem_mark or exc.context_mark
            where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
            problem = exc.problem or exc.context or "invalid YAML"
            raise DevToolError(f"YAML error{where}: {problem}.") from exc
        except YAMLError as exc:
            raise DevToolError(f"YAML error: {exc}") from exc
        return {"documents": documents}

    def yaml_dump(self, payload: dict[str, Any]) -> dict[str, Any]:
        from ruamel.yaml import YAML

        indent = _int(payload, "indent", default=2, low=2, high=8)
        multi = bool(payload.get("multi", False))
        if "data" not in payload:
            raise DevToolError("data is required.")
        data = payload["data"]
        dumper = YAML(typ="rt")
        dumper.default_flow_style = False
        dumper.width = 4096
        dumper.indent(mapping=indent, sequence=indent + 2, offset=indent)
        buffer = io.StringIO()
        if multi:
            if not isinstance(data, list):
                raise DevToolError("data must be a list when multi is true.")
            dumper.explicit_start = True
            dumper.dump_all(data, buffer)
        else:
            dumper.dump(data, buffer)
        return {"text": buffer.getvalue()}

    # Hashing and passwords

    def hash_data(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = _bytes_or_text(payload)
        requested = payload.get("algorithms") or list(HASH_ALGORITHMS)
        if not isinstance(requested, list) or not all(isinstance(a, str) for a in requested):
            raise DevToolError("algorithms must be a list of names.")
        digests: dict[str, str] = {}
        for name in requested:
            key = name.lower().replace("-", "")
            if key == "crc32":
                digests["crc32"] = f"{zlib.crc32(data) & 0xFFFFFFFF:08x}"
                continue
            if key == "adler32":
                digests["adler32"] = f"{zlib.adler32(data) & 0xFFFFFFFF:08x}"
                continue
            normalized = key.replace("sha3", "sha3_").replace("sha3__", "sha3_")
            if normalized not in HASH_ALGORITHMS:
                raise DevToolError(f"Unsupported algorithm: {name}.")
            digests[normalized] = hashlib.new(normalized, data).hexdigest()
        return {"size": len(data), "digests": digests}

    def bcrypt_hash(self, payload: dict[str, Any]) -> dict[str, Any]:
        import bcrypt

        password = _text(payload, "password", allow_empty=True, limit=1024).encode("utf-8")
        if len(password) > 72:
            raise DevToolError("bcrypt uses at most 72 bytes of the password.")
        cost = _int(payload, "cost", default=12, low=4, high=16)
        started = time.perf_counter()
        hashed = bcrypt.hashpw(password, bcrypt.gensalt(rounds=cost)).decode("ascii")
        return {"hash": hashed, "cost": cost, "ms": round((time.perf_counter() - started) * 1000)}

    def bcrypt_verify(self, payload: dict[str, Any]) -> dict[str, Any]:
        import bcrypt

        password = _text(payload, "password", allow_empty=True, limit=1024).encode("utf-8")
        hashed = _text(payload, "hash", limit=200).strip().encode("ascii", "ignore")
        if len(password) > 72:
            raise DevToolError("bcrypt uses at most 72 bytes of the password.")
        try:
            match = bcrypt.checkpw(password, hashed)
        except ValueError as exc:
            raise DevToolError("Not a valid bcrypt hash.") from exc
        return {"match": bool(match)}

    # Keys and certificates

    def rsa_keypair(self, payload: dict[str, Any]) -> dict[str, Any]:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        bits = _choice_int(payload, "bits", (2048, 3072, 4096), default=2048)
        private_format = _choice(payload, "private_format", ("pkcs8", "pkcs1"), default="pkcs8")
        key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
        fmt = (
            serialization.PrivateFormat.PKCS8
            if private_format == "pkcs8"
            else serialization.PrivateFormat.TraditionalOpenSSL
        )
        private_pem = key.private_bytes(
            serialization.Encoding.PEM, fmt, serialization.NoEncryption()
        ).decode("ascii")
        public = key.public_key()
        return {
            "bits": bits,
            "private_pem": private_pem,
            "public_pem": public.public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            ).decode("ascii"),
            "public_pkcs1_pem": public.public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.PKCS1
            ).decode("ascii"),
        }

    def ed25519_keypair(self, payload: dict[str, Any]) -> dict[str, Any]:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519

        key = ed25519.Ed25519PrivateKey.generate()
        public = key.public_key()
        raw_private = key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        raw_public = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return {
            "private_pem": key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode("ascii"),
            "public_pem": public.public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            ).decode("ascii"),
            "private_raw_base64": base64.b64encode(raw_private).decode("ascii"),
            "public_raw_base64": base64.b64encode(raw_public).decode("ascii"),
            "public_hex": raw_public.hex(),
        }

    def ssh_keypair(self, payload: dict[str, Any]) -> dict[str, Any]:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

        kind = _choice(payload, "type", ("ed25519", "rsa", "ecdsa"), default="ed25519")
        comment = _text(payload, "comment", allow_empty=True, limit=120).strip()
        if any(char in comment for char in "\r\n"):
            raise DevToolError("Comment must be one line.")
        passphrase = _text(payload, "passphrase", allow_empty=True, limit=256)
        if kind == "rsa":
            bits = _choice_int(payload, "bits", (2048, 3072, 4096), default=4096)
            key: Any = rsa.generate_private_key(public_exponent=65537, key_size=bits)
        elif kind == "ecdsa":
            key = ec.generate_private_key(ec.SECP256R1())
        else:
            key = ed25519.Ed25519PrivateKey.generate()
        encryption = (
            serialization.BestAvailableEncryption(passphrase.encode("utf-8"))
            if passphrase
            else serialization.NoEncryption()
        )
        private_text = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, encryption
        ).decode("ascii")
        public_raw = key.public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
        )
        blob = base64.b64decode(public_raw.split(b" ")[1])
        fingerprint = base64.b64encode(hashlib.sha256(blob).digest()).decode("ascii").rstrip("=")
        public_text = public_raw.decode("ascii") + (f" {comment}" if comment else "")
        return {
            "type": kind,
            "private_openssh": private_text,
            "public_openssh": public_text + "\n",
            "fingerprint_sha256": f"SHA256:{fingerprint}",
            "encrypted": bool(passphrase),
        }

    def csr(self, payload: dict[str, Any]) -> dict[str, Any]:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec, rsa
        from cryptography.x509.oid import NameOID

        common_name = _text(payload, "common_name", limit=64).strip()
        if not common_name:
            raise DevToolError("Common name is required.")
        attributes = [x509.NameAttribute(NameOID.COMMON_NAME, common_name)]
        optional = (
            ("organization", NameOID.ORGANIZATION_NAME, 64),
            ("org_unit", NameOID.ORGANIZATIONAL_UNIT_NAME, 64),
            ("locality", NameOID.LOCALITY_NAME, 128),
            ("state", NameOID.STATE_OR_PROVINCE_NAME, 128),
            ("email", NameOID.EMAIL_ADDRESS, 128),
        )
        for field, oid, limit in optional:
            value = _text(payload, field, allow_empty=True, limit=limit).strip()
            if value:
                attributes.append(x509.NameAttribute(oid, value))
        country = _text(payload, "country", allow_empty=True, limit=2).strip().upper()
        if country:
            if not re.fullmatch(r"[A-Z]{2}", country):
                raise DevToolError("Country must be a two-letter code.")
            attributes.append(x509.NameAttribute(NameOID.COUNTRY_NAME, country))

        key_type = _choice(payload, "key_type", ("rsa", "ec"), default="rsa")
        if key_type == "ec":
            key: Any = ec.generate_private_key(ec.SECP256R1())
        else:
            bits = _choice_int(payload, "bits", (2048, 3072, 4096), default=2048)
            key = rsa.generate_private_key(public_exponent=65537, key_size=bits)

        builder = x509.CertificateSigningRequestBuilder().subject_name(x509.Name(attributes))
        sans_raw = payload.get("sans") or []
        if not isinstance(sans_raw, list):
            raise DevToolError("sans must be a list.")
        names: list[x509.GeneralName] = []
        for raw in sans_raw[:100]:
            value = str(raw).strip()
            if not value:
                continue
            try:
                names.append(x509.IPAddress(ipaddress.ip_address(value)))
            except ValueError:
                if not _HOSTNAME.match(value.lstrip("*.")):
                    raise DevToolError(f"Not a host name or IP address: {value}") from None
                names.append(x509.DNSName(value))
        if names:
            builder = builder.add_extension(x509.SubjectAlternativeName(names), critical=False)
        request = builder.sign(key, hashes.SHA256())
        return {
            "csr_pem": request.public_bytes(serialization.Encoding.PEM).decode("ascii"),
            "private_key_pem": key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode("ascii"),
            "subject": request.subject.rfc4514_string(),
        }

    def cuid2(self, payload: dict[str, Any]) -> dict[str, Any]:
        count = _int(payload, "count", default=5, low=1, high=1000)
        length = _int(payload, "length", default=24, low=4, high=32)
        fingerprint = hashlib.sha3_512(secrets.token_bytes(32)).hexdigest()
        counter = secrets.randbelow(476_782_367)
        ids = []
        for _ in range(count):
            counter += 1
            first = chr(ord("a") + secrets.randbelow(26))
            material = f"{time.time_ns():x}{secrets.token_hex(16)}{counter:x}{fingerprint}"
            digest = int(hashlib.sha3_512(material.encode("ascii")).hexdigest(), 16)
            ids.append((first + _base36(digest)[1:])[:length])
        return {"ids": ids}

    # Network lookups

    def dns_lookup(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = _text(payload, "name", limit=253).strip().rstrip(".")
        record_type = _choice(payload, "type", DNS_TYPES, default="A", upper=True)
        server = _text(payload, "server", allow_empty=True, limit=64).strip()
        if record_type == "PTR":
            try:
                ipaddress.ip_address(name)
            except ValueError as exc:
                raise DevToolError("PTR lookups need an IP address.") from exc
        elif not _HOSTNAME.match(name):
            raise DevToolError("Not a valid host name.")
        args = [_tool_path("dig"), "+time=4", "+tries=1", "+noall", "+answer", "+authority"]
        if server:
            try:
                ipaddress.ip_address(server)
            except ValueError as exc:
                raise DevToolError("DNS server must be an IP address.") from exc
            args.append(f"@{server}")
        args += ["-x", name] if record_type == "PTR" else [name, record_type]
        started = time.perf_counter()
        output = self._exec(args, timeout=10)
        records = []
        for line in output.splitlines():
            if not line.strip() or line.startswith(";"):
                continue
            parts = line.split(None, 4)
            if len(parts) == 5:
                records.append(
                    {
                        "name": parts[0],
                        "ttl": parts[1],
                        "class": parts[2],
                        "type": parts[3],
                        "data": parts[4],
                    }
                )
        return {
            "records": records,
            "raw": output,
            "ms": round((time.perf_counter() - started) * 1000),
        }

    def whois_lookup(self, payload: dict[str, Any]) -> dict[str, Any]:
        query = _text(payload, "query", limit=253).strip().rstrip(".")
        try:
            ipaddress.ip_address(query)
        except ValueError:
            if not _HOSTNAME.match(query) or "." not in query:
                raise DevToolError("Enter a domain name or an IP address.") from None
        output = self._exec([_tool_path("whois"), query], timeout=20)
        wanted = {
            "domain name": "Domain",
            "registrar": "Registrar",
            "creation date": "Created",
            "registry expiry date": "Expires",
            "registrar registration expiration date": "Expires",
            "updated date": "Updated",
            "name server": "Name servers",
            "domain status": "Status",
            "registrant organization": "Registrant",
            "netname": "Network",
            "orgname": "Organization",
            "org-name": "Organization",
            "cidr": "CIDR",
            "inetnum": "Range",
            "netrange": "Range",
            "country": "Country",
        }
        fields: dict[str, list[str]] = {}
        for line in output.splitlines():
            key, sep, value = line.partition(":")
            label = wanted.get(key.strip().lower())
            if not sep or not label or not value.strip():
                continue
            bucket = fields.setdefault(label, [])
            item = value.strip()
            if item not in bucket and len(bucket) < 12:
                bucket.append(item)
        return {"fields": fields, "raw": output}

    def ip_info(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw = _text(payload, "ip", limit=64).strip()
        try:
            address = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise DevToolError("Not a valid IPv4 or IPv6 address.") from exc
        info: dict[str, Any] = {
            "address": str(address),
            "version": address.version,
            "integer": str(int(address)),
            "hex": f"0x{int(address):0{8 if address.version == 4 else 32}x}",
            "private": address.is_private,
            "global": address.is_global,
            "loopback": address.is_loopback,
            "link_local": address.is_link_local,
            "multicast": address.is_multicast,
            "reserved": address.is_reserved,
            "unspecified": address.is_unspecified,
            "reverse_pointer": address.reverse_pointer,
        }
        if address.version == 6:
            info["exploded"] = address.exploded
            mapped = address.ipv4_mapped
            if mapped is not None:
                info["ipv4_mapped"] = str(mapped)
        else:
            info["binary"] = ".".join(f"{octet:08b}" for octet in address.packed)
        lookup = bool(payload.get("reverse", True))
        if lookup:
            try:
                output = self._exec(
                    [_tool_path("dig"), "+time=3", "+tries=1", "+short", "-x", str(address)],
                    timeout=8,
                )
                info["ptr"] = [line.strip() for line in output.splitlines() if line.strip()]
            except DevToolError as exc:
                info["ptr_error"] = str(exc)
        return info

    def http_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        method = _choice(payload, "method", HTTP_METHODS, default="GET", upper=True)
        url = _text(payload, "url", limit=8192).strip()
        if not re.match(r"^https?://", url, re.IGNORECASE):
            raise DevToolError("URL must start with http:// or https://.")
        headers_raw = payload.get("headers") or {}
        if not isinstance(headers_raw, dict):
            raise DevToolError("headers must be an object.")
        headers: dict[str, str] = {}
        for key, value in headers_raw.items():
            name = str(key).strip()
            text = str(value)
            if not _HEADER_NAME.match(name) or any(c in text for c in "\r\n"):
                raise DevToolError(f"Invalid header: {name}")
            headers[name] = text
        body = payload.get("body")
        data: bytes | None = None
        if body not in (None, ""):
            if not isinstance(body, str):
                raise DevToolError("body must be a string.")
            data = body.encode("utf-8")
            if len(data) > MAX_HTTP_BODY_BYTES:
                raise DevToolError("Request body is larger than 2 MB.")
        timeout = _int(payload, "timeout", default=20, low=1, high=60)
        follow = bool(payload.get("follow_redirects", True))
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        opener = (
            urllib.request.build_opener()
            if follow
            else urllib.request.build_opener(_NoRedirect())
        )
        started = time.perf_counter()
        try:
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            raise DevToolError(f"Request failed: {reason}") from exc
        with response:
            raw = response.read(MAX_HTTP_BODY_BYTES + 1)
            status = response.status if hasattr(response, "status") else response.getcode()
            reason = getattr(response, "reason", "") or ""
            header_items = list(response.headers.items())
            final_url = response.geturl() if hasattr(response, "geturl") else url
        elapsed = round((time.perf_counter() - started) * 1000)
        truncated = len(raw) > MAX_HTTP_BODY_BYTES
        raw = raw[:MAX_HTTP_BODY_BYTES]
        try:
            text = raw.decode("utf-8")
            body_out: dict[str, Any] = {"text": text}
        except UnicodeDecodeError:
            body_out = {"base64": base64.b64encode(raw).decode("ascii")}
        return {
            "status": status,
            "reason": str(reason),
            "url": final_url,
            "headers": [[str(k), str(v)] for k, v in header_items],
            "size": len(raw),
            "truncated": truncated,
            "ms": elapsed,
            **body_out,
        }

    # Images

    def image_metadata(self, payload: dict[str, Any]) -> dict[str, Any]:
        from PIL import ExifTags, IptcImagePlugin

        data = _binary(payload, "data")
        image = _open_image(data)
        with image:
            result: dict[str, Any] = {
                "format": image.format,
                "mode": image.mode,
                "width": image.width,
                "height": image.height,
                "frames": getattr(image, "n_frames", 1),
                "size_bytes": len(data),
                "icc_profile": bool(image.info.get("icc_profile")),
                "dpi": _json_safe(image.info.get("dpi")),
            }
            exif = image.getexif()
            tags: dict[str, Any] = {}
            for tag_id, value in exif.items():
                tags[ExifTags.TAGS.get(tag_id, f"0x{tag_id:04x}")] = _exif_value(value)
            for ifd_id, label in ((ExifTags.IFD.Exif, "Exif"), (ExifTags.IFD.GPSInfo, "GPS")):
                try:
                    ifd = exif.get_ifd(ifd_id)
                except (KeyError, ValueError):
                    continue
                names = ExifTags.GPSTAGS if label == "GPS" else ExifTags.TAGS
                for tag_id, value in ifd.items():
                    tags[f"{label}.{names.get(tag_id, f'0x{tag_id:04x}')}"] = _exif_value(value)
            result["exif"] = tags
            try:
                iptc = IptcImagePlugin.getiptcinfo(image) or {}
            except (SyntaxError, ValueError, OSError):
                iptc = {}
            result["iptc"] = {f"{k[0]}:{k[1]}": _exif_value(v) for k, v in iptc.items()}
        start = data.find(b"<x:xmpmeta")
        end = data.find(b"</x:xmpmeta>", start)
        if start >= 0 and end > start:
            result["xmp"] = data[start : end + len(b"</x:xmpmeta>")].decode("utf-8", "replace")
        return result

    def image_strip(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = _binary(payload, "data")
        keep_icc = bool(payload.get("keep_icc", True))
        image = _open_image(data)
        with image:
            fmt = (image.format or "PNG").upper()
            if fmt not in IMAGE_FORMATS:
                fmt = "PNG"
            clean = image.copy()
            icc = image.info.get("icc_profile") if keep_icc else None
        clean.info = {}
        buffer = io.BytesIO()
        options: dict[str, Any] = {}
        if icc:
            options["icc_profile"] = icc
        if fmt == "JPEG":
            options["quality"] = 95
            if clean.mode not in ("RGB", "L", "CMYK"):
                clean = clean.convert("RGB")
        clean.save(buffer, format=fmt, **options)
        out = buffer.getvalue()
        return {
            "format": fmt,
            "mime": IMAGE_FORMATS[fmt],
            "data": base64.b64encode(out).decode("ascii"),
            "before_bytes": len(data),
            "after_bytes": len(out),
        }

    def image_convert(self, payload: dict[str, Any]) -> dict[str, Any]:
        from PIL import Image

        data = _binary(payload, "data")
        fmt = _choice(payload, "format", tuple(IMAGE_FORMATS), default="PNG", upper=True)
        quality = _int(payload, "quality", default=90, low=1, high=100)
        image = _open_image(data)
        with image:
            image.load()
            converted = image.copy()
        if fmt in ("JPEG", "BMP") and converted.mode not in ("RGB", "L"):
            rgba = converted.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.getchannel("A"))
            converted = background
        elif fmt == "GIF" and converted.mode not in ("P", "L"):
            converted = converted.convert("RGBA").convert("P", palette=Image.Palette.ADAPTIVE)
        buffer = io.BytesIO()
        options: dict[str, Any] = {}
        if fmt in ("JPEG", "WEBP"):
            options["quality"] = quality
        converted.save(buffer, format=fmt, **options)
        out = buffer.getvalue()
        return {
            "format": fmt,
            "mime": IMAGE_FORMATS[fmt],
            "data": base64.b64encode(out).decode("ascii"),
            "width": converted.width,
            "height": converted.height,
            "before_bytes": len(data),
            "after_bytes": len(out),
        }

    def _exec(self, args: list[str], *, timeout: int) -> str:
        try:
            completed = self._runner(
                args, capture_output=True, text=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise DevToolError("Lookup timed out.", status=504) from exc
        except OSError as exc:
            raise DevToolError(f"Could not run {args[0]}.", status=503) from exc
        output = (completed.stdout or "").strip()
        if completed.returncode != 0 and not output:
            message = (completed.stderr or "").strip() or "lookup failed"
            raise DevToolError(f"Lookup failed: {message[:300]}", status=502)
        return output[:200_000]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _tool_path(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise DevToolError(f"{name} is not installed on this Mac.", status=503)
    return path


def _text(
    payload: dict[str, Any], key: str, *, allow_empty: bool = False, limit: int = MAX_TEXT_CHARS
) -> str:
    value = payload.get(key, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise DevToolError(f"{key} must be a string.")
    if not allow_empty and not value.strip():
        raise DevToolError(f"{key} is required.")
    if len(value) > limit:
        raise DevToolError(f"{key} is too long.")
    return value


def _int(payload: dict[str, Any], key: str, *, default: int, low: int, high: int) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool):
        raise DevToolError(f"{key} must be a number.")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise DevToolError(f"{key} must be a number.") from exc
    if number < low or number > high:
        raise DevToolError(f"{key} must be between {low} and {high}.")
    return number


def _choice(
    payload: dict[str, Any],
    key: str,
    allowed: tuple[str, ...],
    *,
    default: str,
    upper: bool = False,
) -> str:
    value = payload.get(key, default)
    if not isinstance(value, str):
        raise DevToolError(f"{key} must be a string.")
    value = value.strip().upper() if upper else value.strip().lower()
    if value not in allowed:
        raise DevToolError(f"{key} must be one of: {', '.join(allowed)}.")
    return value


def _choice_int(
    payload: dict[str, Any], key: str, allowed: tuple[int, ...], *, default: int
) -> int:
    number = _int(payload, key, default=default, low=min(allowed), high=max(allowed))
    if number not in allowed:
        raise DevToolError(f"{key} must be one of: {', '.join(str(a) for a in allowed)}.")
    return number


def _binary(payload: dict[str, Any], key: str) -> bytes:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise DevToolError(f"{key} is required.")
    if value.startswith("data:"):
        value = value.split(",", 1)[-1]
    if len(value) > MAX_BINARY_BYTES * 4 // 3 + 8:
        raise DevToolError("File is larger than 25 MB.")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise DevToolError(f"{key} must be base64.") from exc


def _bytes_or_text(payload: dict[str, Any]) -> bytes:
    if payload.get("data"):
        return _binary(payload, "data")
    return _text(payload, "text", allow_empty=True).encode("utf-8")


def _open_image(data: bytes):
    from PIL import Image, UnidentifiedImageError

    Image.MAX_IMAGE_PIXELS = 80_000_000
    try:
        return Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise DevToolError("Not a supported image.") from exc


def _exif_value(value: Any) -> Any:
    if isinstance(value, bytes):
        if len(value) > 64:
            return f"<{len(value)} bytes>"
        text = value.decode("utf-8", "replace").strip("\x00")
        return text if text.isprintable() else value.hex()
    if isinstance(value, tuple):
        return [_exif_value(item) for item in value]
    if hasattr(value, "numerator") and hasattr(value, "denominator"):
        try:
            return round(float(value), 6)
        except (ZeroDivisionError, ValueError):
            return str(value)
    return _json_safe(value)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(v) for v in value]
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return str(value)
    return value


def _base36(number: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if number == 0:
        return "0"
    out = []
    while number:
        number, rem = divmod(number, 36)
        out.append(alphabet[rem])
    return "".join(reversed(out))
