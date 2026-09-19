"""Every ``*_selfcheck.cjs`` runs under pytest, not only under ``npm run selfcheck``.

Most selfchecks have a Python driver beside the pins they belong to. Found
2026-09-19: 14 of 136 had none -- among them the docs/125 red-team fixes'
own pins (settle_config, plothost, scroll_abort, livediff_buttons, fh_chart)
and docs/202's generate_classchange. The full suite, which is the gate
before every push, never executed them; only a manual ``npm run selfcheck``
did. All 14 were green when this was found, which is luck, not coverage.

So this driver runs every selfcheck that no other test file names. It is
computed, not listed, so a selfcheck added tomorrow without a driver is
still run -- and one that later gains its own driver drops out of here
instead of running twice.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
_ROOT = _TESTS.parent


def _undriven() -> list[str]:
    drivers = [p.read_text(encoding="utf-8", errors="replace")
               for p in _TESTS.glob("*.py") if p.name != Path(__file__).name]
    out = []
    for cjs in sorted(_TESTS.glob("*_selfcheck.cjs")):
        if not any(cjs.name in src for src in drivers):
            out.append(cjs.name)
    return out


def test_the_scan_finds_the_known_orphans():
    """Not vacuous: when this driver was written these were undriven."""
    found = set(_undriven())
    assert {"generate_classchange_selfcheck.cjs", "settle_config_selfcheck.cjs",
            "plothost_selfcheck.cjs"} <= found, sorted(found)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
@pytest.mark.parametrize("name", _undriven())
def test_undriven_selfcheck(name):
    proc = subprocess.run(["node", str(_TESTS / name)], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          cwd=str(_ROOT), timeout=300)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, (
        f"{name} failed\nstdout:\n{proc.stdout[-3000:]}\nstderr:\n{proc.stderr[-3000:]}")
