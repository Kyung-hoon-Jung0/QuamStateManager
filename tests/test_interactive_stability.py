"""The dataset Interactive panel's stability rules (docs/118), executed under
jsdom against the REAL app.js (tests/interactive_stability_selfcheck.cjs).

Customer report: "interactive를 클릭하고 다시 다른 곳으로 돌아가거나 하면
이상해진다". Four independent mechanisms produced it; the selfcheck pins each:

  - a run opened as a FULL PAGE scoped every tab query to `#inspector-pane`,
    which does not contain its tabs, so NO tab ever switched (the big one);
  - the hard render cap blanked a VISIBLE tile that could then never come back,
    because an emptied tile never crosses an intersection threshold on its own;
  - purged tiles stayed in the render ledger, so the budget shrank silently;
  - pane markup round-tripped through a STRING (pin / unpin / close-keep) kept
    `data-loaded`/`data-rendered`, so figures with no Plotly behind them were
    never rebuilt;
  - plus: nothing re-sized a drawn figure when its container's geometry changed.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "interactive_stability_selfcheck.cjs"


def _node() -> str | None:
    return shutil.which("node")


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_interactive_panel_stability():
    try:
        subprocess.run([_node(), "-e", "require('jsdom')"],
                       check=True, capture_output=True, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([_node(), str(_SELFCHECK)], capture_output=True,
                         text=True, encoding="utf-8", timeout=180)
    assert res.returncode == 0, f"interactive selfcheck failed:\n{res.stdout}\n{res.stderr}"
    assert res.stdout.count("  ok  ") >= 13, res.stdout


def test_resize_helper_is_wired_not_just_defined():
    """A helper nobody calls is not a fix. Both mount points must attach the
    size watcher, and the tab re-show must resize."""
    app_js = (_ROOT / "quam_state_manager" / "web" / "static" / "app.js").read_text(
        encoding="utf-8")
    assert app_js.count("_observeInteractiveResize(container)") >= 2
    assert "resizeInteractiveTiles(c)" in app_js       # on tab re-show
    # and the click handler can no longer stack (ndview.js has done this since docs/67)
    assert app_js.count("removeAllListeners('plotly_click')") >= 1
