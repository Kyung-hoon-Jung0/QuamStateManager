"""The URL prefix the suite is running under (docs/226, spec §5.1).

``SM_TEST_URL_PREFIX=/sm pytest tests/`` runs the WHOLE suite with SM mounted
under ``/sm`` (tests/conftest.py copies it into ``SM_URL_PREFIX``, so every
``create_app()`` that does not pass ``url_prefix`` mounts there). Unset, the
suite runs at root exactly as before.

An assertion on a URL that SM EMITS (a ``Location`` / ``HX-Redirect`` header,
an ``href`` in rendered HTML) is written ``P("/diff")``: ``"/diff"`` at root,
``"/sm/diff"`` under the prefix. A URL the TEST sends stays app-relative --
``client.get("/qubits")`` works in both modes (the middleware's tolerant strip).
"""
from __future__ import annotations

import os
import re

PREFIX = os.environ.get("SM_TEST_URL_PREFIX", "").rstrip("/")
RE_PREFIX = re.escape(PREFIX)          # for rf'href="{RE_PREFIX}/x"' regex pins


def P(path: str) -> str:
    """App-root-absolute ``path`` -> the URL SM emits in the current mode."""
    return PREFIX + path
