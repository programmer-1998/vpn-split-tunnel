"""Input validation for domains, IP addresses and CIDR ranges.

The domain editor accepts two kinds of entry -- a hostname (``sina.ir``) or an
IP/CIDR (``10.0.0.1`` / ``192.168.1.0/24``) -- and the two must never be
confused. ``is_ip_or_cidr`` decides which bucket an entry belongs to and
``normalize_domain`` both validates a hostname and tidies common, friendly
pastes (a full ``https://…`` URL becomes the bare hostname) so that garbage
never reaches the routing rules.
"""

from __future__ import annotations

import ipaddress
import re

_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_MAX_LENGTH = 253
_MIN_LABELS = 2


def is_ip_or_cidr(value: str) -> bool:
    """Return True if ``value`` is a valid IP address or CIDR range."""
    try:
        if "/" in value:
            ipaddress.ip_network(value, strict=False)
        else:
            ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def normalize_domain(value: str) -> str | None:
    """Return a normalized lowercase hostname, or None if not a usable domain.

    Tolerances for real-world pastes:

    * a scheme (``https://sina.ir/``) is dropped, so a full URL works;
    * any path/query/fragment (``sina.ir/news/…``, ``sina.ir?x=1``) is cut;
    * a trailing dot (``sina.ir.``) is stripped;
    * surrounding whitespace and letter case are ignored.

    Anything else -- spaces inside the name, ``@``/``:``/``,`` characters, an
    IP address or CIDR (those belong in the IP field), a bare single-label
    name, or an invalid label -- is rejected as garbage.
    """
    v = value.strip().lower()
    if not v:
        return None

    # Drop a scheme so pasting "https://sina.ir/" works.
    if "://" in v:
        v = v.split("://", 1)[1]
    # Cut everything after the first path/query/fragment separator.
    for sep in ("/", "?", "#"):
        v = v.split(sep, 1)[0]
    # Tidy the remainder.
    v = v.strip().rstrip(".")

    if not v or len(v) > _MAX_LENGTH:
        return None
    if any(c.isspace() or c in "@:," for c in v):
        return None
    if is_ip_or_cidr(v):
        return None

    labels = v.split(".")
    # Require at least a name and a TLD: a bare "sina" is not routable.
    if len(labels) < _MIN_LABELS or any(not _LABEL.match(label) for label in labels):
        return None
    return v
