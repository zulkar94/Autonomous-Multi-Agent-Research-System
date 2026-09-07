"""SSRF guard for every outbound URL the agents touch.

Defends against: non-HTTP schemes, credentials in URL, non-standard ports,
private/loopback/link-local/CGNAT ranges, cloud metadata endpoints, and
DNS-rebinding (resolution result is validated, and the connection is pinned to
the validated IP by the caller when possible).
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

from ..config import get_settings

BLOCKED_HOSTS = {"metadata.google.internal", "metadata.goog", "instance-data"}
BLOCKED_IPS = {"169.254.169.254", "100.100.100.200", "fd00:ec2::254"}


class SSRFError(ValueError):
    """Raised when a URL fails egress policy."""


@dataclass(slots=True)
class ValidatedURL:
    url: str
    host: str
    ip: str
    port: int


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10"))
    )


def _resolve(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SSRFError(f"cannot resolve host: {host}") from exc
    return sorted({info[4][0] for info in infos})


def validate_url(raw_url: str) -> ValidatedURL:
    """Validate a URL against egress policy or raise `SSRFError`."""
    settings = get_settings()
    parts = urlsplit(raw_url.strip())

    if parts.scheme.lower() not in settings.allowed_url_schemes:
        raise SSRFError(f"scheme not allowed: {parts.scheme or '<none>'}")
    if parts.username or parts.password:
        raise SSRFError("credentials in URL are not allowed")
    host = (parts.hostname or "").lower()
    if not host:
        raise SSRFError("missing host")
    if host in BLOCKED_HOSTS:
        raise SSRFError("blocked host")
    for denied in settings.domain_denylist:
        if host == denied or host.endswith(f".{denied}"):
            raise SSRFError(f"domain denylisted: {denied}")

    port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    if port not in settings.allowed_ports:
        raise SSRFError(f"port not allowed: {port}")

    addresses = _resolve(host)
    if not addresses:
        raise SSRFError("host did not resolve")
    for addr in addresses:
        if addr in BLOCKED_IPS:
            raise SSRFError("cloud metadata endpoint blocked")
        ip = ipaddress.ip_address(addr)
        if not _is_public(ip) and not settings.allow_private_network:
            raise SSRFError(f"non-public address blocked: {addr}")

    return ValidatedURL(url=raw_url.strip(), host=host, ip=addresses[0], port=port)


def is_safe_url(raw_url: str) -> bool:
    try:
        validate_url(raw_url)
    except SSRFError:
        return False
    return True
