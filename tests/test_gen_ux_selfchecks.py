"""Drives the r15 Config-Gen UX behavioral checks (docs/70) under node+jsdom:

- tests/generate_slotmenu_selfcheck.cjs — the chassis FEM-chooser popup is
  fixed-positioned at the slot's viewport rect, zoom-corrected (quam_ui_scale
  sets html CSS zoom, which re-multiplies fixed-element px), closes on pane
  scroll and Escape. The old absolute+page-coords code landed the popup
  offset by the sidebar width + topbar ("popup appears in a wrong place").

- tests/generate_dragghost_selfcheck.cjs — the wiring-step drag now shows a
  body-appended cursor-following ghost (element · role label, zoom-corrected,
  removed on drop/Escape) and the docked monitor is bigger + sticky.

- tests/type_alert_popup_selfcheck.cjs — the self-raising type-anomaly alert
  (docs/78): it never interrupts (typing / drag / another modal / background
  tab), opens at most one dialog per content-entry event, closing is not
  dismissing, and auto-correct runs the docs/77 apply path.

- tests/dataset_poll_selfcheck.cjs — the dataset delta-poll's liveness
  (docs/80): a request that never settles must not wedge polling forever
  (pollInFlight stuck true = the table silently stops updating until reload),
  requests carry an abort signal, errors back off instead of hammering, a
  malformed body never advances the cursor past an unscanned window, and a
  `partial` response schedules a prompt catch-up.

- tests/wiring_portlabel_selfcheck.cjs — the shared port-circle renderer's
  label sizing (2026-08-04): single-member circles (control/z/coupler,
  single readout, input single) get chord-fit adaptive type (9-14px, short
  names big), feedline sub-circles keep 7px, every label fits its circle,
  and the iw-port drag-drop DOM contract survives.

Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def _run(name: str) -> None:
    r = subprocess.run(
        ["node", str(_ROOT / "tests" / name)],
        capture_output=True, text=True, cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "ALL OK" in r.stdout, (r.stdout + r.stderr)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_slotmenu_selfcheck_passes():
    _run("generate_slotmenu_selfcheck.cjs")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_dragghost_selfcheck_passes():
    _run("generate_dragghost_selfcheck.cjs")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_wiring_portlabel_selfcheck_passes():
    _run("wiring_portlabel_selfcheck.cjs")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_type_alert_popup_selfcheck_passes():
    _run("type_alert_popup_selfcheck.cjs")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_dataset_poll_selfcheck_passes():
    _run("dataset_poll_selfcheck.cjs")
