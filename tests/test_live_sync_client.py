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
- liveedit-r2-09: a passive window's grid follows a foreign edit; its own
  edit re-GETs nothing.
- F8: the pending box follows the server's per-path ``pending`` flag.
- F11: Escape / an outside click close the Live-Edit pickers and Auto-Sync.
- F12: the Review drawer footer stays in view (CSS rule pin below).

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
    assert r.stdout.count("ok - ") >= 65, r.stdout


def test_the_review_drawer_footer_stays_in_view():
    """QA F12: the drawer is a 280px scroll box and its footer (Save to working
    state -- the only path to /save -- and Discard all) was its LAST child, so
    with ~4+ rows it sat below the clip line and a click there hit the sidebar
    behind. Stuck to the drawer's bottom edge, opaque, above the rows. The
    geometry itself is verified in real Chrome (elementFromPoint returns the
    buttons with 5+ rows); this pins the rule that does it."""
    import re
    css = (_ROOT / "quam_state_manager" / "web" / "static" / "style.css").read_text(encoding="utf-8")
    m = re.search(r"\n\.tray-drawer-foot\s*\{(.*?)\n\}", css, re.S)
    assert m, "the .tray-drawer-foot rule is gone"
    body = re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)
    decl = {k.strip(): v.strip() for k, v in
            (d.split(":", 1) for d in body.split(";") if ":" in d)}
    assert decl.get("position") == "sticky", decl
    # flush with the scrollport edge: minus the expanded drawer's bottom padding
    pad = re.search(r"\.tray-drawer\.tray-expanded\s*\{[^}]*padding:\s*([^;]+);", css)
    assert pad and decl.get("bottom") == "-" + pad.group(1).split()[0].lstrip("0"), (decl, pad and pad.group(1))
    assert decl.get("background") == "var(--pico-card-background-color)", decl
    assert decl.get("z-index") == "1", decl
