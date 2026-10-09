"""S10 walk: the user's own write is one event, whichever arrived first.

After ONE Apply to live, Versions listed the write twice: the Param History
``save`` copy taken at the apply was imported as an observed state ("seen by
SM (save snapshot) writer unknown") because the capture kicked the observed
import BEFORE the write's journal line was projected; the write then landed
in front of it and nothing took the duplicate back. The quick diff compared
the two copies ("#2 -> #1 . 0 changes").

Pinned here, on generic synthetic chips:

* both arrival orders of an Apply's save copy and its SM event end with ONE
  event (the SM write) and the snapshot recorded as ``same_before``;
* a run ingested late, placed before an observation of its own saved state,
  takes that observation back too;
* the quick diff compares the two newest DIFFERENT states.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

import pytest

from quam_state_manager.core import hub, hub_sync
from tests.ledger_fixture import no_runs  # noqa: F401
from tests.test_hub_drawer import _inline, chip_dir, drawer, rows, run, sm  # noqa: F401


def _ledger(env):
    con = sqlite3.connect(f"{(chip_dir(env) / 'ledger.sqlite').resolve().as_uri()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        events = [dict(r) for r in con.execute("SELECT eid, kind, status, chash FROM events ORDER BY ord")]
        snaps = {r["ts"]: r["outcome"] for r in con.execute("SELECT ts, outcome FROM observed_snapshots")}
    finally:
        con.close()
    return events, snaps


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _apply(env):
    c = env["client"]
    assert c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "4.5e-5"}).status_code == 200
    assert c.post("/state/apply-to-live").status_code == 200


def _assert_one_write(env):
    events, snaps = _ledger(env)
    kinds = [e["kind"] for e in events]
    assert kinds[-1] == "sm_apply", f"the SM write is the newest event, nothing after it ({kinds})"
    assert [e["status"] for e in events if e["kind"] == "observed"] == ["auto"], \
        f"only the pre-apply backup is an observed state, never the apply's save copy ({events})"
    assert sorted(snaps.values()) == ["added", "same_before"], snaps
    body = env["client"].get("/state/versions").get_data(as_text=True)
    text = _text(body)
    assert body.count('<li class="state-version-row') == 2, text
    assert "save snapshot" not in text, "the apply's own copy is not listed as a second version"
    m = re.search(r"Since the previous version #(\d+) \S #(\d+) .*? \S (\d+) changes? ", text)
    assert m and m.groups() == ("2", "1", "1"), text[:600]
    assert "qubits.qA1.T1" in text


def test_the_save_copy_imported_after_the_write_landed_is_no_event(no_runs):
    _apply(no_runs)                               # inline: the line lands before the capture is imported
    _assert_one_write(no_runs)


def test_the_save_copy_imported_before_the_write_landed_is_taken_back(no_runs, monkeypatch):
    real = hub.project
    monkeypatch.setattr(hub, "project", lambda *a, **k: 0)    # the line is projected late
    _apply(no_runs)
    events, snaps = _ledger(no_runs)
    assert [e["status"] for e in events] == ["auto", "save"], \
        f"(the walk's order: both captures imported before the write's line) {events}"
    monkeypatch.setattr(hub, "project", real)
    hub.catch_up(chip_dir(no_runs))
    _assert_one_write(no_runs)


def test_a_run_ingested_late_takes_back_an_observation_of_its_own_state(sm):
    """A run that saved state X lands in the ledger AFTER SM imported its own
    capture of X (taken after the run ended): the capture is the run's state
    seen by SM, not a second change."""
    live = sm["live"]
    st = json.loads((live / "state.json").read_text(encoding="utf-8"))
    st["qubits"]["qA1"]["T1"] = 7.0e-5
    (live / "state.json").write_text(json.dumps(st), encoding="utf-8")
    with sm["app"].app_context():
        from quam_state_manager.web import routes
        assert routes._history().check_and_snapshot(str(live), "auto", force=True)
    got = rows(drawer(sm, "qubits.qA1.T1"))
    assert got[0]["prov"] == "observed", "(the capture is imported first)"
    run(sm["data"], 5, st, t_us=int(time.time() * 1e6) - 60_000_000)
    with sm["app"].app_context():
        hub_sync.on_roots_moved([str(sm["data"])])
    got = rows(drawer(sm, "qubits.qA1.T1"))
    assert [r["prov"] for r in got].count("observed") == 0, got
    assert got[0]["value"] == "7e-05" and got[0]["prov"].startswith("run_"), got
    _events, snaps = _ledger(sm)
    assert list(snaps.values()) == ["same_before"], snaps


def test_the_quick_diff_skips_a_row_holding_the_same_state(sm):
    """A run that saved exactly the state the Apply wrote is a row of its own
    (a run is never deduplicated), but "since the previous version" compares
    the two newest DIFFERENT states: the Apply's change, not 0 changes."""
    _apply(sm)
    st = json.loads((sm["live"] / "state.json").read_text(encoding="utf-8"))
    run(sm["data"], 5, st, t_us=int(time.time() * 1e6) + 60_000_000)
    with sm["app"].app_context():
        hub_sync.on_roots_moved([str(sm["data"])])
    text = _text(sm["client"].get("/state/versions").get_data(as_text=True))
    assert re.search(r"#1 .*? run #5 ", text) and re.search(r"#2 .*? applied by ", text), text[:900]
    m = re.search(r"Since the previous version #(\d+) \S #(\d+) .*? \S (\d+) changes? ", text)
    assert m and m.groups() == ("3", "1", "1"), text[:600]
    assert "qubits.qA1.T1" in text.split("Compare")[0]
