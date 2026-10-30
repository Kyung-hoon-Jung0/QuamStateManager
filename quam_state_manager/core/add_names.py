"""The name rules of the three add paths, as named constants.

Two rules exist today and are kept apart on purpose (unifying them is a
separate decision, with its own golden re-pin):

``ADDITION_NAME_RE`` -- a name in the Generate wizard's ``spec.additions``
    (``additions.structural_errors``; gen-additions.js checks the same
    pattern before it writes). Letters, digits, ``_`` and ``-``; a leading
    digit is allowed.
``OPERATION_NAME_RE`` -- a pulse / operation name on the Pulses page (create,
    copy, duplicate, rename) and a gate name on the pair page's Add gate. A
    letter first, then letters, digits or ``_``; at most 64 characters.
"""
from __future__ import annotations

import re

ADDITION_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
# The HTML ``pattern`` of the Pulses / pair-gate name inputs (anchored by the
# browser) and the server's check are one string.
OPERATION_NAME_PATTERN = "[a-zA-Z][a-zA-Z0-9_]{0,63}"
OPERATION_NAME_RE = re.compile("^" + OPERATION_NAME_PATTERN + "$")


def unordered_key(pair) -> str:
    """Flip-stable pair identity, sorted lexically by normalized index."""
    from quam_state_manager.core.script_emitter import _run_build
    c, t = pair.split("-") if isinstance(pair, str) else pair
    norm = _run_build()._norm_index
    return "-".join(map(str, sorted((norm(c), norm(t)), key=str)))
