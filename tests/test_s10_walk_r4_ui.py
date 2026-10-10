"""S10 walk, round 4 (the v1.2.0 candidate's third real-browser walk) -- UI items.

* P2-11 -- the Param History cell drawer's header read "· current: 3.20405e-05":
  a separator with nothing before it (it joins the pointer line, which most
  values do not have). And the drawer opened below a 21-row grid with only its
  title on screen: the scroll ran on the one-line "Loading..." placeholder, not
  on the loaded drawer.
* P2-13 -- with the change history unreadable, the Versions panel's fallback
  rows (date, Diff, the After-#run chip, Stage, Pull to Live) left the meta
  column narrow, and the live-change tracker's baseline badge could not wrap
  (``white-space: nowrap``): a horizontal scrollbar on the panel and the
  badge's text cut at its edge.
* P2-5 -- two commits to ONE field: the top bar said "2 unapplied edits /
  Apply 2", and the apply then said "Written to live · 1 edit". Every count of
  the user's unapplied edits now counts FIELDS (what changes on the chip; the
  apply replays one value per path); the change log keeps every commit (undo)
  and the consent gate keeps comparing log entries.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from flask import render_template

from tests.test_hub_drawer import _inline, sm  # noqa: F401 -- _inline is an autouse fixture
from tests.test_sync_one_control import _drift, _edit, _live, chip  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
HX = {"HX-Request": "true"}


def _css() -> str:
    return (ROOT / "quam_state_manager" / "web" / "static" / "style.css").read_text(encoding="utf-8")


def _block(css: str, selector: str) -> str:
    i = css.index(selector)
    return css[i:css.index("}", i)]


# ======================================================================
# P2-11: the Param History cell drawer
# ======================================================================

def _header(html: str) -> str:
    m = re.search(r'<div class="phd-header">(.*?)<button', html, re.S)
    assert m, html[:600]
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1).replace("&nbsp;", " "))).strip()


def test_the_drawer_header_has_no_dangling_separator(sm):  # noqa: F811
    html = sm["client"].get("/param-history/expand?qubit=qA1&prop=T1").get_data(as_text=True)
    head = _header(html)
    assert "current: 3e-05" in head, head
    # S10 walk r4: old "qA1 · T1 · current: ..." (a middot opening the value line) -> none
    assert not re.search(r"·\s*current:", head), head


def test_the_separator_joins_the_pointer_line_when_there_is_one(sm):  # noqa: F811
    app = sm["app"]
    with app.test_request_context("/"):
        with_ptr = render_template("_param_history_drawer.html",
                                   row={"qubit": "qA1", "property": "x180", "raw_pointer": "#./x180_Gauss",
                                        "values": []},
                                   row_json="{}", qubit="qA1", prop="x180", current_value=0.25)
        latest = render_template("_param_history_drawer.html",
                                 row={"qubit": "qA1", "property": "T1", "raw_pointer": None, "values": []},
                                 row_json="{}", qubit="qA1", prop="T1", current_value=None,
                                 latest_value=3e-5, latest_t=None)
    assert re.search(r"x180_Gauss\s*·\s*current: 0\.25", _header(with_ptr)), _header(with_ptr)
    assert _header(latest).endswith("latest recorded: 3e-05") and "· latest" not in _header(latest)


def test_the_ph_drawer_scrolls_its_loaded_content_into_view():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    r = subprocess.run([node, str(Path(__file__).with_name("ph_drawer_scroll_selfcheck.cjs"))],
                       capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=str(ROOT))
    if r.returncode == 2 and "jsdom not installed" in r.stdout:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert re.search(r"all \d+ checks passed", r.stdout), r.stdout


# ======================================================================
# P2-13: the Versions panel never scrolls sideways
# ======================================================================

def test_the_baseline_badge_wraps_inside_the_row():
    block = _block(_css(), ".snap-baseline {")
    # S10 walk r4: old "white-space: nowrap" (70 characters that pushed the panel wide) -> wraps
    assert "nowrap" not in block, block
    assert "max-width: 100%" in block and "overflow-wrap: anywhere" in block, block


def test_a_version_row_wraps_rather_than_overflow_the_panel():
    css = _css()
    row = _block(css, ".state-version-row {")
    assert "flex-wrap: wrap" in row, row
    meta = _block(css, ".sv-meta {")
    # the meta column takes the room left beside the actions, or moves under them
    assert "flex: 1 1 8rem" in meta and "min-width: 0" in meta, meta
    assert "overflow-wrap: anywhere" in meta, meta
    kids = _block(css, ":where(.sv-meta > *) {")
    assert "max-width: 100%" in kids, kids
    # a source badge keeps its own cap but never exceeds the column
    assert "max-width: min(16rem, 100%)" in _block(css, ".snap-src {")


# ======================================================================
# P2-5: one count of the user's unapplied edits
# ======================================================================

def _tray(client) -> str:
    return client.get("/state/tray").get_data(as_text=True)


def test_two_commits_to_one_field_are_one_edit_everywhere(chip):  # noqa: F811
    client, folder = chip
    _edit(client, "qubits.q2.T1", "5.5e-05")
    _edit(client, "qubits.q2.T1", "6.5e-05")
    s = _drift(client)["sync"]
    assert s["state"] == "mine" and s["unapplied"] == 1, s
    tray = _tray(client)
    # S10 walk r4: old "2 unapplied edits" / "Apply 2" -> one field, one edit
    assert "1 unapplied edit<" in tray and "Apply 1<" in tray, re.findall(r"unapplied[^<]*|Apply \d", tray)
    assert "2 unapplied" not in tray and "Apply 2" not in tray
    # the consent count stays the change log's (two commits)
    assert 'data-change-count="2"' in tray
    review = client.get("/state/review").get_data(as_text=True)
    assert "Writes your 1 edit to the live chip." in review
    assert "Your unapplied edits (1 field, 2 changes)" in review
    sig = re.search(r'data-change-sig="([^"]*)"', tray).group(1)
    r = client.post("/state/sync", data={"mode": "apply", "seen_changes": "2", "seen_sig": sig},
                    headers=HX)
    d = r.get_json()
    assert d["status"] == "ok" and d["replay"]["applied"] == 1, d   # "Written to live · 1 edit"
    assert _live(folder, "q2") == 6.5e-05


def test_one_edit_per_field_is_counted_once_per_field(chip):  # noqa: F811
    client, _folder = chip
    _edit(client, "qubits.q1.T1", "5.5e-05")
    _edit(client, "qubits.q2.T1", "6.5e-05")
    _edit(client, "qubits.q1.T1", "7.5e-05")
    assert _drift(client)["sync"]["unapplied"] == 2
    assert "2 unapplied edits<" in _tray(client)
    review = client.get("/state/review").get_data(as_text=True)
    assert "Your unapplied edits (2 fields, 3 changes)" in review


def test_the_staged_apply_declares_the_change_log_it_showed(chip):  # noqa: F811
    """The staged Apply's consent count is the change log's ENTRIES -- the
    gate compares entries, and the field count the label now shows would
    under-declare two commits to one field."""
    client, _folder = chip
    _edit(client, "qubits.q2.T1", "5.5e-05")
    assert client.post("/save", headers=HX).status_code == 200
    _edit(client, "qubits.q2.T1", "6.5e-05")
    _edit(client, "qubits.q2.T1", "7.5e-05")
    tray = _tray(client)
    # a saved working state plus edits: the control's Apply pushes it whole (sync-arm)
    assert 'data-working-dirty="1"' in tray and "sync-control-act btn-apply-live sync-arm" in tray
    assert "1 unapplied edit<" in tray, re.findall(r"unapplied[^<]*", tray)
    vals = re.search(r'hx-post="/state/apply-to-live"[^>]*hx-vals=\'([^\']*)\'', tray, re.S)
    assert vals, tray[:2000]
    assert json.loads(vals.group(1))["seen_changes"] == "2", vals.group(1)
