from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit


class UnsafeTargetError(ValueError):
    pass


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def sha256_payload(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def sign_payload(secret: str, timestamp: int, body: bytes) -> str:
    signed = str(timestamp).encode() + b"." + body
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"v1={digest}"


def verify_signature(secret: str, timestamp: int, body: bytes, signature: str) -> bool:
    return hmac.compare_digest(sign_payload(secret, timestamp, body), signature)


Resolver = Callable[[str, int], list[str]]


def _system_resolver(hostname: str, port: int) -> list[str]:
    return sorted(
        {str(item[4][0]) for item in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)}
    )


def _is_forbidden(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return any(
        (
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    )


class TargetValidator:
    """Reject webhook targets that could expose internal infrastructure."""

    def __init__(
        self,
        *,
        allow_private: bool = False,
        allow_insecure_http: bool = False,
        resolver: Resolver = _system_resolver,
    ) -> None:
        self.allow_private = allow_private
        self.allow_insecure_http = allow_insecure_http
        self.resolver = resolver

    def validate(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            raise UnsafeTargetError("target must use http or https")
        if parsed.scheme == "http" and not self.allow_insecure_http:
            raise UnsafeTargetError("plain HTTP targets are disabled")
        if not parsed.hostname:
            raise UnsafeTargetError("target must include a hostname")
        if parsed.username or parsed.password:
            raise UnsafeTargetError("credentials must not be embedded in target URLs")
        if parsed.fragment:
            raise UnsafeTargetError("target URLs must not contain fragments")

        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            addresses = self.resolver(parsed.hostname, port)
        except (OSError, socket.gaierror) as exc:
            raise UnsafeTargetError("target hostname could not be resolved") from exc
        if not addresses:
            raise UnsafeTargetError("target hostname did not resolve to an address")
        if not self.allow_private and any(_is_forbidden(address) for address in addresses):
            raise UnsafeTargetError(
                "private, loopback, link-local and reserved targets are disabled"
            )
