"""docs/164 — "loading…" under a date with nobody running an experiment.

Customer-reported twice. The sidebar tree refetches itself (a workspace version
bump, and unconditionally every 10th poll), the swap rebuilds every ``<details>``
CLOSED with the lazy placeholder inside, and the sticky restore re-opens the
ones that were open. Their runs arrive on exactly one path —
``hx-trigger="toggle[this.open] once"`` — which is true for a person opening the
group and, measured in real Chrome, not true for the restore: after the refetch
neither a ``toggle`` nor a request fires, and the group sits open showing
"loading…" until the user toggles it by hand.

The behaviour lives in ``app.js``, so the pin that can fail lives in the jsdom
selfcheck this driver runs. Without a driver `pytest tests/` executes none of
its assertions (docs/141 §4ae C12).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "sidebar_lazy_group_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_sidebar_lazy_group_selfcheck():
    proc = subprocess.run(
        ["node", str(_SELFCHECK)], capture_output=True, text=True,
        cwd=str(_ROOT), timeout=180)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"


def test_the_restore_asks_rather_than_simulating_a_toggle():
    """A source pin on the ONE decision that took four attempts to get right.

    Three toggle-shaped fixes were measured against the real browser and none
    reached the trigger — ``htmx.trigger``'s CustomEvent, a native ``Event``
    dispatched inside ``afterSwap``, and the same deferred by a task. Each left
    ``__lazyAsked`` true on a still-stuck group. Re-introducing any of them
    would look like a simplification, so say here that it is not one.
    """
    js = (_ROOT / "quam_state_manager" / "web" / "static" / "app.js").read_text(
        encoding="utf-8")
    i = js.index("details[data-lazy-group][open]")
    block = js[i:i + 1600]
    assert "htmx.ajax('GET', url," in block, \
        "the restore must ASK with the element's own hx-get, not simulate a toggle"
    assert "getAttribute('hx-vals')" in block, \
        "the parameters are read off the element so they cannot drift from the markup"
    assert ".trigger(g, 'toggle')" not in block and "dispatchEvent(new Event('toggle'))" not in block, \
        "a simulated toggle was measured NOT to reach this trigger -- see docs/164"
