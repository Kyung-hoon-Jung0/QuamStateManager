"""P1-1 (U2-stale, 6 of 31 task rows): the previous page's inspector no longer
covers the next page.

Qubits -> q2 -> sidebar Datasets left Datasets 121 px under a 748 px q2
inspector; the same happened Pairs -> CZ gate plan, Qubits -> Pulses,
Pulses -> Instrument Wiring, Search -> Help and Resonators -> Help. Navigation
to a page that cannot show the open entity now closes its inspector; a page
that can (Qubits -> Resonators) keeps it; a dataset run keeps its own
collapse rule; and the close never writes the user's remembered split (the
"last action decides" rule). Behaviour: ``tests/stale_inspector_selfcheck.cjs``
against the REAL base.html split script.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def test_navigation_closes_a_stale_inspector():
    node = shutil.which("node")
    if node is None or subprocess.run([node, "-e", "require('jsdom')"], capture_output=True,
                                      cwd=str(_ROOT)).returncode != 0:
        pytest.skip("node + jsdom not available")
    res = subprocess.run([node, str(_ROOT / "tests" / "stale_inspector_selfcheck.cjs")],
                         capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "stale_inspector_selfcheck:" in res.stdout and "checks ok" in res.stdout
