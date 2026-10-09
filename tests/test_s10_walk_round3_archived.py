"""S10 walk, round 3 (the v1.2.0 candidate's real-browser walk).

* N4 -- an archived chip has no live state: every cell of its Param History
  grid showed "—" and its cell drawer had no value line. Each cell now shows
  the NEWEST value its history recorded (labelled as such, never "current"),
  the drawer names its newest point as the latest recorded; and the open
  chip's live widgets (its "Live changes since baseline" box, its chip pill
  and count, its "Import from workspace") are not drawn over another chip's
  history.
* N5 -- a folder whose data folder is not linked named the data folder its
  runs had LEFT (a moved folder: the runs are recorded under both roots), and
  the value surfaces counted one run more than Versions (a run with no
  readable saved state). The note names the folder the runs are under now,
  every surface gives one count, and the run it cannot list is said apart.
  SM's own "Backup before Take live" label no longer rides an SM write's row
  of the same content: a label belongs to the row it was written for.
* N8 -- each build phase counted its own work, so the count went DOWN between
  phases ("1864 of 3521" folders, then "925 of 3521" runs; "563 of 576" runs,
  then "271 of 647" snapshots). The message now says which step it is on.
"""

from __future__ import annotations

import html
import os
import re
import shutil
import time
from pathlib import Path

from quam_state_manager.core import hub_sync
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app
from tests.ledger_fixture import declare_root, no_runs  # noqa: F401
from tests.test_archived_chip_build import (_archived, _drawer, _grid, _inline, _one_slice,  # noqa: F401
                                            _queued_only, _t, _with_runs)
from tests.test_hub_other_chip import _seed_run, _state, _write_chip
from tests.test_s10_walk_left_out_notes import _copy_open, _text

HX = {"HX-Request": "true"}


def _cell_value(grid: str, qubit: str, prop: str) -> tuple[str, str]:
    m = re.search(r'<td class="history-cell"\s+data-qubit="%s" data-prop="%s".*?'
                  r'<span class="history-cell-value([^"]*)"([^>]*)>(.*?)</span>' % (qubit, prop), grid, re.S)
    assert m, (qubit, prop)
    return m.group(1).strip() + "|" + html.unescape(m.group(2)), m.group(3).strip()


# ======================================================================
# N4: an archived chip's grid shows its newest recorded values
# ======================================================================

def test_an_archived_grid_cell_carries_its_newest_recorded_value(tmp_path):
    env = _archived(tmp_path)
    grid = _grid(env)
    attrs, shown = _cell_value(grid, "qA1", "T1")
    assert shown == "3e-05", shown               # 1e-5, 2e-5, 9e-5 (#7), then 3e-5: the newest
    assert "history-cell-latest" in attrs and "Newest recorded value" in attrs, attrs
    assert _cell_value(grid, "qA2", "T1")[1] == "2e-05"
    body, _row = _drawer(env)
    text = _t(body)
    assert "latest recorded: 3e-05" in text and "current:" not in text, text[:300]


def test_an_archived_view_draws_none_of_the_open_chips_widgets(tmp_path):
    env = _archived(tmp_path)
    archived = _grid(env)
    for widget in ('class="ph-drift-card"', 'id="live-drift-panel"', "chip-selector-row",
                   "Import from workspace"):
        assert widget not in archived, widget
    assert "Viewing history for chip" in archived
    # the open chip's own page keeps them
    loaded = env["client"].get("/param-history", headers=HX).get_data(as_text=True)
    for widget in ('id="live-drift-panel"', "chip-selector-row", "Import from workspace"):
        assert widget in loaded, widget
    assert "history-cell-latest" not in loaded, "the open chip's cells keep their current value"


# ======================================================================
# N5: a moved data folder is named where its runs are now; one count
# ======================================================================

def test_a_moved_data_folder_is_named_where_its_runs_are_now_and_counted_once(tmp_path):
    """The walk's chip copy: the ledger recorded the runs under the data
    folder they were first read from, then under the folder they moved to;
    a run of it has no readable saved state. A folder of the chip with no
    data folder linked names the folder the runs are under now, and its
    drawer and Versions give one count (the unreadable run said apart)."""
    old = tmp_path / "old" / "Data_Root"
    _seed_run(old, 31, _state(7.1e9, "alpha"), "010000")
    _seed_run(old, 32, _state(7.2e9, "alpha"), "020000")
    _seed_run(old, 33, _state(7.3e9, "alpha"), "003000")
    gone = next(old.glob("2026-07-29/#33_*"))
    shutil.rmtree(gone / "quam_state")                           # no readable saved state
    for p in (gone / "node.json", gone / "data.json", gone):
        os.utime(p, (time.time() - 86_400, time.time() - 86_400))
    live = tmp_path / "chips" / "a"
    _write_chip(live, _state(7.2e9, "alpha"))
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    c.post("/workspace/add", data={"folder": str(old)})
    declare_root(c, old)
    moved = tmp_path / "moved" / "Data_Root"
    shutil.copytree(old, moved)
    declare_root(c, moved)                                      # the runs read again, from where they are now
    _copy_open(tmp_path, c, moved)
    where = str(moved.resolve())
    drawer = _text(c.get("/field/history?path=qubits.qA1.f_01").get_data(as_text=True))
    assert f"2 runs of a data folder not linked to this folder ({where}) are not part" in drawer, drawer
    assert str(old.resolve()) not in drawer
    assert "1 more run of it has no readable saved state." in drawer, drawer
    versions = _text(c.get("/state/versions", headers=HX).get_data(as_text=True))
    assert f"2 runs of a data folder not linked to this folder ({where}) are listed below" in versions, versions
    assert "1 more run of it has no readable saved state." in versions


def test_a_backup_label_stays_on_its_own_row(no_runs, monkeypatch):
    """SM's protective copy ("Backup before Take live (...)") of a state an SM
    write produced: its words belong to the backup's row, never to the SM
    write's row (which then read as two actions at once)."""
    from quam_state_manager.core import hub_versions
    monkeypatch.setattr(hub_versions._Hashes, "_work", lambda self: None)
    client = no_runs["client"]
    client.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "4e-5"})
    client.post("/state/apply-to-live")
    label = "Backup before Take live (1 unapplied edit)"
    with no_runs["app"].app_context():
        ctx = routes._active_ctx()
        hm = routes._history()
        meta = hm.check_and_snapshot(ctx["path"], "manual", force=True, kind="backup")
        assert meta is not None
        hm.annotate_snapshot(ctx["path"], meta.timestamp, label=label)
        listing = routes._versions_read(ctx, hm.list_snapshots(ctx["path"]))
    writes = [r for r in listing["rows"] if r.get("badge") == "SM write"]
    assert writes and all(label not in (r.get("label") or "") for r in writes), \
        [(r.get("badge"), r.get("label")) for r in listing["rows"]]
    own = [r for r in listing["rows"] if label in (r.get("label") or "")]
    assert len(own) == 1 and own[0].get("badge") != "SM write", own


# ======================================================================
# N8: the build says which step it is on
# ======================================================================

def test_an_archived_build_reads_as_steps_never_a_count_going_down(tmp_path, monkeypatch):
    env = _with_runs(tmp_path)
    _queued_only(monkeypatch)
    seen = []
    for _ in range(300):
        page = _grid(env)
        if 'data-vh-mode="building"' not in page:
            break
        m = re.search(r"being built: step (\d) of (\d), ([A-Za-z' ]+?)(?: \((\d+) of (\d+)\))?\.", _t(page))
        if m:
            seen.append((int(m.group(1)), int(m.group(2)), m.group(3), int(m.group(4) or 0)))
        _one_slice(env)
    assert "30 recorded changes shown" in _t(_grid(env))
    assert seen, "the build was never seen building"
    assert {s[1] for s in seen} == {3}
    steps = [s[0] for s in seen]
    assert steps == sorted(steps), seen                          # never back to an earlier step
    for a, b in zip(seen, seen[1:]):
        if a[0] == b[0] and a[2] == b[2]:
            assert b[3] >= a[3], (a, b)                          # within a step, never down
    whats = {s[2] for s in seen}
    assert "reading this chip's Param History snapshots" in whats, whats
    assert any(s[0] == 3 for s in seen) and any(s[0] < 3 for s in seen), seen


def test_progress_words_number_the_steps_only_when_there_are_several():
    words = hub_sync.progress_words
    st = {"phase": "observing", "observed_done": 2, "observed_total": 4, "observes": True, "archive": True}
    assert words(st) == ": reading this chip's Param History snapshots (2 of 4)"
    st = dict(st, roots=[{"path": "x"}], total=9, done=9)
    assert words(st) == ": step 3 of 3, reading this chip's Param History snapshots (2 of 4)"
    assert words(dict(st, phase="ingesting", done=4)) == ": step 2 of 3, adding runs (4 of 9)"
    assert words({"phase": "ingesting", "done": 4, "total": 9}) == " (4 of 9 runs)"   # no steps named
