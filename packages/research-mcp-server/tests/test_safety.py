"""The URL admission control is the part of this server that must not be wrong.

Tau sits on a VLAN with Proxmox, Home Assistant and cameras. A fetch tool that can be talked into
resolving to a private address turns this server into a proxy into that VLAN, reachable by
whoever wrote the page the model just read.
"""

import ipaddress
from unittest.mock import patch

import pytest

from research_mcp_server.safety import UnsafeURLError, validate_url


def _resolving_to(*addresses: str):
    """Fakes DNS so tests exercise the real IP-classification logic offline."""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(2, 1, 6, "", (address, port)) for address in addresses]

    return patch("research_mcp_server.safety.socket.getaddrinfo", side_effect=fake_getaddrinfo)


def test_public_url_is_allowed():
    with _resolving_to("93.184.216.34"):
        assert validate_url("https://example.com/page") == "https://example.com/page"


@pytest.mark.parametrize(
    "address, what",
    [
        ("127.0.0.1", "loopback"),
        ("10.0.0.5", "RFC1918 private"),
        ("192.168.1.10", "home LAN - where Proxmox/HA/cameras live"),
        ("172.16.4.2", "RFC1918 private"),
        ("169.254.169.254", "cloud metadata endpoint"),
        ("0.0.0.0", "unspecified"),
        ("100.64.0.1", "CGNAT"),
        ("224.0.0.1", "multicast"),
    ],
)
def test_private_and_special_addresses_are_refused(address, what):
    with _resolving_to(address):
        with pytest.raises(UnsafeURLError, match="private or non-public"):
            validate_url(f"http://anything.example/{what}")


def test_dns_rebinding_is_caught_because_the_check_is_on_the_resolved_ip():
    """The whole point: a hostname that LOOKS public resolving to a private address. Checking the
    hostname string would catch nothing here - the check has to happen after resolution."""
    with _resolving_to("192.168.1.1"):
        with pytest.raises(UnsafeURLError):
            validate_url("https://totally-legit-public-site.com/")


def test_a_host_with_both_public_and_private_addresses_is_refused():
    """Every resolved address must be public, not just the first - otherwise which one gets used
    is a coin flip, and the attacker picks the coin."""
    with _resolving_to("93.184.216.34", "10.1.2.3"):
        with pytest.raises(UnsafeURLError):
            validate_url("https://mixed.example/")


def test_ipv4_mapped_ipv6_loopback_is_refused():
    """::ffff:127.0.0.1 is loopback wearing a hat; a naive is_global check on the v6 form misses
    it."""
    assert not ipaddress.ip_address("::ffff:127.0.0.1").ipv4_mapped.is_global
    with _resolving_to("::ffff:127.0.0.1"):
        with pytest.raises(UnsafeURLError):
            validate_url("https://sneaky.example/")


def test_ipv6_loopback_and_unique_local_are_refused():
    for address in ("::1", "fd00::1", "fe80::1"):
        with _resolving_to(address):
            with pytest.raises(UnsafeURLError):
                validate_url("https://v6.example/")


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://x.example/f", "gopher://x.example/"])
def test_non_http_schemes_are_refused(url):
    with pytest.raises(UnsafeURLError, match="only http and https"):
        validate_url(url)


def test_refusal_does_not_leak_the_resolved_internal_address():
    """The message goes back to the model and possibly into a transcript. 'evil.com resolves to
    10.0.4.7' is free internal-network reconnaissance for whoever controls evil.com's DNS."""
    with _resolving_to("10.0.4.7"):
        with pytest.raises(UnsafeURLError) as exc:
            validate_url("https://evil.example/")
    assert "10.0.4.7" not in str(exc.value)


def test_unresolvable_host_is_refused_not_crashed():
    import socket

    with patch("research_mcp_server.safety.socket.getaddrinfo", side_effect=socket.gaierror("nope")):
        with pytest.raises(UnsafeURLError, match="Could not resolve"):
            validate_url("https://does-not-exist.example/")


def test_empty_and_hostless_urls_are_refused():
    with pytest.raises(UnsafeURLError):
        validate_url("")
    with pytest.raises(UnsafeURLError):
        validate_url("http:///nohost")
