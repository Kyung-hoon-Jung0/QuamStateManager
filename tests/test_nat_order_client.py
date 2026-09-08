"""Natural numeric ordering on the CLIENT surfaces — customer rule 2026-09-09.

The customer read a live-diff list as ``…weights_imag.1009``, ``.101``,
``.1011`` and asked for one rule across the whole product: a digit run counts
as a NUMBER (101 < 1009 < 1010 < 1011, q2 < q10).  The server side of that is
``core.loader.natural_key``; the client side is
``localeCompare(b, undefined, {numeric: true, sensitivity: 'base'})``, the
idiom app.js / bulk-edit.js / pair-edit.js already used.

Everything here is client-side (the Datasets table + its pickers, the chip
status tile settings, the component-map feedline slots, the wizard's step-5
wiring error), so the pin is a jsdom harness driving the REAL shipped files:
``tests/nat_order_client_selfcheck.cjs``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "nat_order_client_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_nat_order_client_selfcheck():
    proc = subprocess.run(
        ["node", str(_SELFCHECK)], capture_output=True, text=True,
        cwd=str(_ROOT), timeout=180)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
