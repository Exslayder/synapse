"""Small networking helpers shared by config, resolver and proxy."""

from __future__ import annotations

import ipaddress
import re

HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$",
    re.IGNORECASE,
)

BROWSER_EXES = {
    "chrome.exe",
    "msedge.exe",
    "brave.exe",
    "chromium.exe",
    "vivaldi.exe",
}


def normalize_host(host: str) -> str:
    return host.strip().strip("[]").rstrip(".").lower()


def is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text.strip("[]"))
    except ValueError:
        return False
    return True


def is_global_ip(text: str) -> bool:
    try:
        addr = ipaddress.ip_address(text.strip("[]"))
    except ValueError:
        return False
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    ):
        return False
    return bool(addr.is_global)


def is_valid_host(host: str) -> bool:
    host = normalize_host(host)
    if not host:
        return False
    if is_ip(host):
        return is_global_ip(host)
    return bool(HOST_RE.match(host))


def is_valid_domain(host: str) -> bool:
    """A DNS name we are willing to ask about (IP literals are not domains)."""
    host = normalize_host(host)
    return bool(host) and not is_ip(host) and bool(HOST_RE.match(host))


def split_host_port(target: str, default: int) -> tuple[str, int]:
    if target.startswith("["):
        host, _, rest = target[1:].partition("]")
        port_s = rest[1:] if rest.startswith(":") else ""
    elif target.count(":") == 1:
        host, port_s = target.split(":", 1)
    else:
        host, port_s = target, ""
    try:
        port = int(port_s) if port_s else default
    except ValueError:
        port = 0
    return normalize_host(host), port
