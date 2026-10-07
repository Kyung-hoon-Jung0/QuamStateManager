"""docs/301 F17: leaving a large page logged htmx:historyCacheError x4.

htmx snapshots the page it leaves -- the whole body -- into localStorage. The
Calibration log (hundreds of cards) and the Datasets list (every run rides the
page as one JSON script) are larger than the quota: each retry failed, and
emptying the cache to make room took every OTHER page's snapshot with it.

Those two pages carry ``hx-history="false"``, htmx's own switch: no snapshot
of them, and Back onto them re-requests the page from the server (a
history-restore request, which ``routes._is_htmx`` answers with the full page).
The mechanism under the bundled htmx, with a control, is
``tests/history_marker_selfcheck.cjs``.

htmx looks for the marker ANYWHERE in the document, so it must live inside
those pages' own markup and nowhere else: in the shell it would switch the
cache off for every page.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_calibration_log_hub import DAY, world  # noqa: F401  (fixture)
from tests.test_datasets_refresh import HX, _app, _seed_run

MARK = 'hx-history="false"'
ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "quam_state_manager" / "web" / "templates"


def test_the_calibration_log_is_never_snapshotted(world):
    for headers in ({}, {"HX-Request": "true"}):          # full page, and the pane swap
        html = world["client"].get(f"/journal?day={DAY}", headers=headers).get_data(as_text=True)
        assert re.search(r'<div id="jr-body" ' + re.escape(MARK) + r'>', html), headers
        assert html.count(MARK) == 1, headers


def test_the_datasets_list_is_never_snapshotted(tmp_path):
    root = tmp_path / "data"
    _seed_run(root, 1, date="2026-05-01", name="power_rabi")
    _seed_run(root, 2, date="2026-05-02", name="ramsey")
    _, c = _app(tmp_path, root)
    for url in ("/datasets", "/collections"):             # one template, both lists
        for headers in ({}, HX):
            html = c.get(url, headers=headers).get_data(as_text=True)
            assert re.search(r'<div class="datasets-page" ' + re.escape(MARK) + r'>', html), (url, headers)
            assert html.count(MARK) == 1, (url, headers)


def test_no_other_markup_switches_the_cache_off():
    hits = sorted(p.name for p in TPL.glob("*.html") if MARK in p.read_text(encoding="utf-8"))
    assert hits == ["_datasets.html", "_journal.html"], hits


def test_the_marker_does_what_the_fix_relies_on():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    r = subprocess.run([node, str(ROOT / "tests" / "history_marker_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=str(ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all history-marker checks passed" in r.stdout
