"""Read-only copy of the QM owner marker grammar (pure standard library).

A QUA config built by the calibration stack may carry one extra element whose NAME says
which client opened the QM::

    qmowner__v1__c<tag>__s<state>__x<session>__n<node>

SM keeps its own copy of the grammar instead of importing the calibration code: the SM
process never imports lab code, and the monitor worker runs in an env that may not have it.
The copy is pinned by test vectors generated from the reference implementation
(``tests/fixtures/qm_owner_marker_vectors.json``); a new marker version regenerates them and
the test fails until this module follows.

Two notions:
- marker-shaped: the name starts with ``qmowner__v<digits>__`` (any version or layout);
- parsed v1: the exact version-1 layout (``v1``, no leading zero); only these are read as
  owner fields. A shaped name that does not parse is reported with its version number.
"""
from __future__ import annotations

import re
from typing import Optional

PREFIX = "qmowner"
TAG_MAX = 16
SESSION_MAX = 12

SHAPE_RE = re.compile(rf"^{PREFIX}__v[0-9]+__")
_VERSION_RE = re.compile(rf"^{PREFIX}__v([0-9]+)__")
_MARKER_RE = re.compile(
    rf"^{PREFIX}__v(?P<version>1)"
    rf"__c(?P<tag>[a-z0-9]{{1,{TAG_MAX}}})"
    r"__s(?P<state>[0-9a-f]{8}|none)"
    rf"__x(?P<session>[a-z0-9]{{1,{SESSION_MAX}}})"
    r"__n(?P<node>(?:[a-z0-9]+(?:_[a-z0-9]+)*)?)$"
)


def is_marker(name) -> bool:
    """True when ``name`` is marker-shaped, readable or not."""
    return isinstance(name, str) and SHAPE_RE.match(name) is not None


VERSION_DIGITS_MAX = 6


def marker_version(name) -> Optional[int]:
    """The version number of a marker-shaped name, else None.

    A version longer than ``VERSION_DIGITS_MAX`` digits is not converted (an unbounded digit
    run must never reach ``int()``); the name stays marker-shaped and the version is None."""
    hit = _VERSION_RE.match(name) if isinstance(name, str) else None
    if not hit or len(hit.group(1)) > VERSION_DIGITS_MAX:
        return None
    return int(hit.group(1))


def version_text(name) -> str:
    """The version of a marker-shaped name as display text: the number, or
    "version with N digits" when it is too long to convert, or "unknown"."""
    hit = _VERSION_RE.match(name) if isinstance(name, str) else None
    if not hit:
        return "unknown"
    digits = hit.group(1)
    if len(digits) > VERSION_DIGITS_MAX:
        return f"version with {len(digits)} digits"
    return f"version {int(digits)}"


def display_text(text, limit: int = 120) -> str:
    """Safe display form of a string read from a server.

    Every non-printable code point (control characters, bidirectional overrides, format
    characters, separators other than the plain space) becomes a ``\\uXXXX`` escape. At
    most ``limit`` characters of the escaped text are kept; a longer text ends with
    "... (N characters)", N being the length of the original."""
    if not isinstance(text, str):
        text = str(text)
    out, used = [], 0
    for ch in text:
        piece = ch if (ch == " " or ch.isprintable()) else (
            f"\\u{ord(ch):04x}" if ord(ch) <= 0xFFFF else f"\\U{ord(ch):08x}")
        if used + len(piece) > limit:
            return "".join(out) + f"... ({len(text)} characters)"
        out.append(piece)
        used += len(piece)
    return "".join(out)


def parse_marker(name) -> Optional[dict]:
    """``{tag, state, session, node}`` for an exact version-1 name, else None."""
    hit = _MARKER_RE.match(name) if isinstance(name, str) else None
    if hit is None:
        return None
    g = hit.groupdict()
    return {"tag": g["tag"], "state": g["state"], "session": g["session"], "node": g["node"]}
