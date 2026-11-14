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


def marker_version(name) -> Optional[int]:
    """The version number of a marker-shaped name, else None."""
    hit = _VERSION_RE.match(name) if isinstance(name, str) else None
    return int(hit.group(1)) if hit else None


def parse_marker(name) -> Optional[dict]:
    """``{tag, state, session, node}`` for an exact version-1 name, else None."""
    hit = _MARKER_RE.match(name) if isinstance(name, str) else None
    if hit is None:
        return None
    g = hit.groupdict()
    return {"tag": g["tag"], "state": g["state"], "session": g["session"], "node": g["node"]}
