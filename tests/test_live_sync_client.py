"""QA live-sync package -- the client halves, executed under jsdom.

Drives ``tests/live_sync_selfcheck.cjs`` against the real app.js / bulk-edit.js
/ pair-edit.js / grid-virt.js / auto-apply.js:

- F3: the tray ✕ (cellDiscarded) repaints the Live-Edit grid cell and clears
  its pending box when the path's last log entry went.
- liveedit-r2-08: Ctrl+Z and a pull patch reach the discovered-collection
  (wiring / TWPA) grids, never a grid whose table is gone.
- liveedit-r2-06: the tray Apply waits for the click-away row commit its own
  mousedown started, then declares the tray as it stands after it.
- liveedit-r2-05: the one-click apply asks for the same-field collision check;
  a person is asked once, the automatic merge only raises the banner, and OK
  re-posts with ``ack_collision`` (never force / ack_unseen).
- liveedit-r2-07: the ``liveConflict`` signal re-renders the drift banner in
  place, chip-guarded.

Exit code is the verdict (the harness prints "all checks passed" only when
every assertion held). Skips when node/jsdom are absent.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_live_sync_client_selfcheck():
    r = subprocess.run(
        ["node", str(_ROOT / "tests" / "live_sync_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT),
        timeout=180)
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
    assert r.stdout.count("ok - ") >= 35, r.stdout
