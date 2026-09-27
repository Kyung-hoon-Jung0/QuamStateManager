"""w7 fq-sync P3: four pre-existing sync-surface defects (all reproduced on
e74b8ce as well), each pinned by the state that exposed it.

(a) the top-bar version chip stayed "unrecorded" after a capture: its memo
    was keyed on the live files alone, so a GET between a live write and the
    post-write capture pinned "no snapshot holds this" until the files moved;
(b) the Versions quick-diff compared an Apply's snapshot with a weeks-old run
    ("29049 values differ" after one edit): the changes-only filter hid the
    pre-apply row, whose zeros meant "no prior", once run ingests landed
    older rows beneath it;
(c) a freshly opened window toasted "Another State Manager window undid a
    change" for an undo made before it existed: its first tray (the full-page
    one) lacked the undo seq it seeds from;
(d) ``SM_RAM_VERIFY=1`` raised on every correct ``diff_cache.lookup`` hit (the
    lookup's "missing" compute was compared with the held diff).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app


def _chip(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {"id": "q1", "f_01": 6.1e9}},
         "qubit_pairs": {}, "active_qubit_names": ["q1"]}), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(
        {"network": {"host": "1.2.3.4"}, "wiring": {"qubits": {}}}), encoding="utf-8")
    return folder


@pytest.fixture
def app_client(tmp_path):
    _chip(tmp_path / "quam_state")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path / "quam_state")})
    return app, c


def _ctx(app):
    for ctx in app.config["contexts"].values():
        if isinstance(ctx, dict) and ctx.get("store") is not None:
            return ctx


# --------------------------------------------------------------------- (a)

def test_a_capture_that_lands_after_the_chip_looked_is_named(app_client, tmp_path):
    app, c = app_client
    c.post("/api/history/snapshot")                       # history exists
    live = tmp_path / "quam_state" / "state.json"
    doc = json.loads(live.read_text(encoding="utf-8"))
    doc["qubits"]["q1"]["f_01"] = 6.4e9
    live.write_text(json.dumps(doc), encoding="utf-8")    # a live write...
    body = c.get("/state/version").get_data(as_text=True)
    assert "unrecorded" in body                           # ...not captured yet
    c.post("/api/history/snapshot")                       # the capture lands; live files untouched
    body = c.get("/state/version").get_data(as_text=True)
    assert "unrecorded" not in body
    assert re.search(r"The live chip is on recorded version (\d{8}_\d{6}\S*)", body), body


# --------------------------------------------------------------------- (b)

def _meta(ts, *, kind, trigger, total, h):
    from quam_state_manager.core.history import SnapshotMeta
    return SnapshotMeta(timestamp=ts, trigger=trigger,
                        diff_summary={"added": 0, "removed": 0, "modified": total, "total": total},
                        new_experiments=[], source_path="", state_hash=h, kind=kind)


def test_a_no_prior_row_stays_visible_once_older_runs_land_beneath_it(app_client, monkeypatch):
    """The first Apply on a chip with a data folder: the pre-apply capture had
    no prior (zeros), the apply's own capture diffs against it (1 change), and
    the run ingest then lands two OLDER EXP rows. Newest-first:"""
    app, c = app_client
    snaps = [
        _meta("20260928_120010", kind="manual", trigger="save", total=1, h="H2"),
        _meta("20260928_120000", kind="backup", trigger="auto", total=0, h="H1"),
        _meta("20260907_100000", kind="exp", trigger="experiment", total=0, h="H41"),
        _meta("20260906_100000", kind="exp", trigger="experiment", total=0, h="H39"),
    ]
    asked = []
    hm = app.config["history_manager"]
    monkeypatch.setattr(hm, "list_snapshots", lambda p: snaps)
    monkeypatch.setattr(hm, "snapshot_ts_for_current_content", lambda p: "20260928_120010")
    monkeypatch.setattr(hm, "diff_snapshots", lambda p, a, b: (asked.append((a, b)), [])[1])
    body = c.get("/state/versions").get_data(as_text=True)
    shown = re.findall(r'sv-check" value="(\d{8}_\d{6})"', body)
    assert shown == [m.timestamp for m in snaps]          # nothing called an unchanged copy
    assert asked == [("20260928_120000", "20260928_120010")], \
        "the quick-diff compared the Apply with something that is not its previous version"


def test_an_identical_copy_is_still_hidden(app_client, monkeypatch):
    """The docs/132 filter keeps working where it can vouch: zeros AND the
    same content as the row below."""
    app, c = app_client
    snaps = [
        _meta("20260928_120020", kind="manual", trigger="save", total=1, h="H3"),
        _meta("20260928_120010", kind="manual", trigger="manual", total=0, h="H2"),
        _meta("20260928_120000", kind="manual", trigger="save", total=1, h="H2"),
        _meta("20260927_120000", kind="manual", trigger="manual", total=0, h="H1"),
    ]
    hm = app.config["history_manager"]
    monkeypatch.setattr(hm, "list_snapshots", lambda p: snaps)
    monkeypatch.setattr(hm, "snapshot_ts_for_current_content", lambda p: "20260928_120020")
    monkeypatch.setattr(hm, "diff_snapshots", lambda p, a, b: [])
    body = c.get("/state/versions").get_data(as_text=True)
    shown = re.findall(r'sv-check" value="(\d{8}_\d{6})"', body)
    assert "20260928_120010" not in shown and len(shown) == 3
    assert "1 unchanged copy hidden" in body


# --------------------------------------------------------------------- (c)

def test_the_full_page_tray_carries_the_newest_live_undo(app_client):
    app, c = app_client
    _ctx(app)["last_live_undo"] = {"seq": 3, "message": "Undone → live: qubits.q1.f_01"}
    page = c.get("/").get_data(as_text=True)
    m = re.search(r'<div id="pending-tray"[^>]*>', page, flags=re.S)
    assert m, "no tray on the page"
    assert 'data-live-undo-seq="3"' in m.group(0)
    # ...the same value the OOB tray renders, so the first swap is not "news"
    tray = c.get("/state/tray").get_data(as_text=True)
    assert 'data-live-undo-seq="3"' in tray


def test_a_window_seeds_what_it_has_seen_from_its_first_tray():
    import quam_state_manager
    src = (Path(quam_state_manager.__file__).parent / "web" / "static" / "app.js").read_text(encoding="utf-8")
    i = src.index("function _noticeForeignLiveUndo")
    body = src[i:i + 900]
    assert 'getAttribute("data-live-undo-seq")' in body
    assert "window._liveUndoSeen === undefined" in body


# --------------------------------------------------------------------- (d)

def test_a_lookup_hit_is_not_a_staleness_error_in_shadow_mode(monkeypatch):
    from quam_state_manager.core import diff_cache
    from quam_state_manager.core.differ import DiffEntry
    monkeypatch.setenv("SM_RAM_VERIFY", "1")
    diff_cache.PAIRS.clear()
    e = DiffEntry("qubits.q1.f_01", 6.1e9, 6.2e9, "modified")
    diff_cache.remember("A", "B", [e])
    assert diff_cache.lookup("A", "B") == [e]             # no StaleCacheError
    assert diff_cache.lookup("A", "C") is None
    diff_cache.remember("A", "B", [e])                    # re-remembered: still checked
    diff_cache.PAIRS.clear()
