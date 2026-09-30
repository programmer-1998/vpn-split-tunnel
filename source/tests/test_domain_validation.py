"""Validation of domain and IP/CIDR input.

The domain editor accepts two kinds of entry -- a hostname or an IP/CIDR --
and rejects garbage in both manual entry and file import. This module pins
``normalize_domain`` (which both validates and tidies friendly pastes such as
full URLs) and ``is_ip_or_cidr`` (the bucket decision).
"""

from __future__ import annotations

import pytest

from vpn_split_tunnel.utils.validation import is_ip_or_cidr, normalize_domain


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Plain hostnames pass through, lowercased.
        ("sina.ir", "sina.ir"),
        ("a.sina.ir", "a.sina.ir"),
        ("Sub.Example.COM", "sub.example.com"),
        # A full URL is reduced to its hostname.
        ("https://sina.ir/", "sina.ir"),
        ("http://www.example.com/path?q=1", "www.example.com"),
        ("HTTPS://Sina.IR", "sina.ir"),
        # Trailing dot (FQDN form) is stripped.
        ("sina.ir.", "sina.ir"),
        # Surrounding whitespace is ignored.
        ("  example.com  ", "example.com"),
    ],
)
def test_normalize_domain_accepts(raw: str, expected: str) -> None:
    assert normalize_domain(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        # IPs and CIDRs belong in the IP field, not the domain field.
        "10.0.0.1",
        "192.168.1.0/24",
        "10.0.0.1/8",
        "::1",
        # Whitespace inside the name.
        "exa mple.com",
        "foo bar.ir",
        # Scheme without a host, or a bare single label.
        "https://",
        "sina",
        "localhost",
        # Delimiters and punctuation that cannot appear in a hostname.
        "foo@bar",
        "foo:bar",
        "foo,bar",
        "foo_bar.ir",
        # Invalid label shapes.
        "-sina.ir",
        "sina-.ir",
        "sina..ir",
        "sina..ir.",
        # Non-ASCII (IDN should be entered as punycode).
        "سینا.ir",
        "пример.рф",
    ],
)
def test_normalize_domain_rejects(raw: str) -> None:
    assert normalize_domain(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.0.0.1", True),
        ("192.168.1.0/24", True),
        ("2001:db8::1", True),
        ("2001:db8::/32", True),
        ("example.com", False),
        ("10.0.0.1/33", False),  # out-of-range prefix
        ("", False),
    ],
)
def test_is_ip_or_cidr(raw: str, expected: bool) -> None:
    assert is_ip_or_cidr(raw) is expected
