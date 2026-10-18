"""v1.5.1 (perf) -- a ledger's declared-chip verdicts outlive the process.

The folder view of a chip's ledger (``hub_lanes.build``) judges every flagged
run by whether its saved state DECLARES another chip, which rebuilds that
run's whole state (``state_at``). Measured on a copy of a customer 5-qubit
chip: 30 such runs, 2.2 s of the first ledger read after EVERY restart -- the
top-bar version chip took 2-4.5 s, and the Calibration log or Param History
paid it when they read first. The verdicts now persist beside the ledger
(``hub_lanes.DECLARED_FILE``), keyed exactly as the RAM memo is.

Pinned on generic synthetic runs:

* a restart reads them back and rebuilds no state (cost pin: state_at calls);
* an in-place rewrite the ledger's log records (docs/298) since they were
  written makes every verdict judged again -- neither the file nor this
  process's own RAM memo is believed -- and the verdict follows the ledger;
* a run appended since keeps the old verdicts (only the new run is judged);
* another ledger's file is never believed;
* (R3B M1) a flags-only change of one old event (an undo mark, a gone
  source, a mid-chain mark) judges NO run again, in RAM or from the file --
  the verdict is keyed by the run's saved-state hash, which did not change;
  a rewrite of one run's stored values judges only that run; a change of
  every event (the order) judges them all.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections import OrderedDict
from pathlib import Path

import pytest

from quam_state_manager.core import hub, hub_build, hub_index, hub_lanes
from quam_state_manager.core.hub_store import CHIP_UNCERTAIN, HubStore
from tests.test_hub_sync import run

NET = {"network": {"host": "10.0.0.1", "cluster_name": "CA"}}
QUBITS = ("qA1", "qA2")
ALPHA = {"name": "alpha", "fingerprint": None, "qubits": list(QUBITS)}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    old = hub._PROJECTOR.inline
    hub.set_inline(True)
    monkeypatch.setattr(hub_lanes, "_DECLARED", OrderedDict())
    monkeypatch.setattr(hub_lanes, "_DECLARED_AT", {})
    yield
    hub.set_inline(old)


def state(v: float, name: str) -> dict:
    return {"qubits": {q: {"f": v if i == 0 else 2.0} for i, q in enumerate(QUBITS)},
            "extras": {"chip_name": name}}


def restart(monkeypatch):
    """A new process: the RAM memo is gone, the file beside the ledger stays."""
    monkeypatch.setattr(hub_lanes, "_DECLARED", OrderedDict())
    monkeypatch.setattr(hub_lanes, "_DECLARED_AT", {})


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    data, chip = tmp_path / "data", tmp_path / "chip"
    run(data, 1, state(1.0, "alpha"), wiring=NET)
    run(data, 2, state(2.0, "beta"), wiring=NET)        # declares another chip
    run(data, 3, state(3.0, "alpha"), wiring=NET)
    hub_build.build(data, chip, identity=ALPHA)
    flag_all(chip)
    calls = {"n": 0}
    real = hub_lanes._Reader.state_at

    def counted(self, eid):
        calls["n"] += 1
        return real(self, eid)
    monkeypatch.setattr(hub_lanes._Reader, "state_at", counted)
    return {"data": data, "chip": chip, "calls": calls}


def flag_all(chip: Path) -> None:
    """Every run flagged uncertain, so each one is judged by its declaration
    (a run already flagged is left alone: setting a column logs a rewrite)."""
    with HubStore(chip) as st:
        st.conn.execute("UPDATE events SET flags = flags | ? WHERE kind='run' AND (flags & ?) = 0",
                        (CHIP_UNCERTAIN, CHIP_UNCERTAIN))
        st.conn.commit()


def judge(lab) -> dict:
    """``{run_id: in this chip's lane}`` as a folder view classifies it now."""
    conn = sqlite3.connect(Path(lab["chip"], "ledger.sqlite").resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")
        index = hub_index.build_index(conn)
        view = hub_lanes.FolderView(key="here", roots=frozenset(
            {hub_lanes.root_key(os.path.normcase(str(lab["data"].resolve())))}))
        facts = hub_lanes._facts(conn)
        out, _roots, _links = hub_lanes.classify(index, view, conn, facts)
        return {facts[eid]["run_id"]: in_lane for eid, (in_lane, _e) in out.items()
                if facts[eid]["kind"] == "run"}
    finally:
        conn.close()


def rewrite_declared_name(chip: Path, run_id: int, name: str) -> None:
    """An in-place change of one run's stored chip name (the triggers log it)."""
    with HubStore(chip) as st:
        eid = st.conn.execute("SELECT eid FROM events WHERE run_id=?", (run_id,)).fetchone()[0]
        n = st.conn.execute(
            "UPDATE changes SET txt=? WHERE eid=? AND pid=(SELECT pid FROM paths WHERE path='extras.chip_name')",
            (json.dumps(name), eid)).rowcount
        st.conn.commit()
    assert n == 1, "the run's chip-name row was not found"


def test_a_restart_reads_the_verdicts_back_and_rebuilds_no_state(ledger, monkeypatch):
    assert judge(ledger) == {1: True, 2: False, 3: True}
    judged = ledger["calls"]["n"]
    assert judged >= 2
    assert (ledger["chip"] / hub_lanes.DECLARED_FILE).is_file()
    restart(monkeypatch)
    ledger["calls"]["n"] = 0
    assert judge(ledger) == {1: True, 2: False, 3: True}
    assert ledger["calls"]["n"] == 0, "a restart rebuilt saved states the file already judged"


def test_an_in_place_rewrite_since_is_judged_again_from_disk_and_in_ram(ledger, monkeypatch):
    assert judge(ledger)[2] is False
    rewrite_declared_name(ledger["chip"], 2, "alpha")
    # this process: its own RAM verdict is not believed past the logged rewrite
    assert judge(ledger)[2] is True
    # and a restart does not believe the file written before it
    rewrite_declared_name(ledger["chip"], 2, "beta")
    restart(monkeypatch)
    ledger["calls"]["n"] = 0
    assert judge(ledger)[2] is False
    assert ledger["calls"]["n"] >= 1


def test_a_run_appended_since_keeps_the_old_verdicts(ledger, monkeypatch):
    judge(ledger)
    run(ledger["data"], 4, state(4.0, "beta"), wiring=NET)
    hub_build.build(ledger["data"], ledger["chip"], identity=ALPHA)
    flag_all(ledger["chip"])        # flags the new run only
    restart(monkeypatch)
    ledger["calls"]["n"] = 0
    assert judge(ledger) == {1: True, 2: False, 3: True, 4: False}
    assert ledger["calls"]["n"] == 1, "an appended run made the old verdicts judged again"


def test_another_ledgers_file_is_never_believed(ledger, monkeypatch):
    judge(ledger)
    side = ledger["chip"] / hub_lanes.DECLARED_FILE
    raw = json.loads(side.read_text(encoding="utf-8"))
    raw["at"][0] = "another-ledger"
    raw["entries"] = [[e[0], e[1], e[2], not e[3]] for e in raw["entries"]]   # every verdict wrong
    side.write_text(json.dumps(raw), encoding="utf-8")
    restart(monkeypatch)
    assert judge(ledger) == {1: True, 2: False, 3: True}


def set_flag_on(chip: Path, run_id: int, bit: int = 1 << 20) -> None:
    """An in-place flags change of one old event (its saved state unchanged):
    an undo mark, SOURCE_GONE and a mid-chain mark all take this shape."""
    with HubStore(chip) as st:
        n = st.conn.execute("UPDATE events SET flags = flags | ? WHERE run_id=?", (bit, run_id)).rowcount
        st.conn.commit()
    assert n == 1


def test_a_flags_only_change_of_one_old_event_judges_no_run_again(ledger, monkeypatch):
    assert judge(ledger) == {1: True, 2: False, 3: True}
    set_flag_on(ledger["chip"], 1)
    ledger["calls"]["n"] = 0
    assert judge(ledger) == {1: True, 2: False, 3: True}
    assert ledger["calls"]["n"] == 0, "a flags-only change re-judged unrelated runs (RAM)"
    # and from the file, in a new process, after another such change
    set_flag_on(ledger["chip"], 3)
    restart(monkeypatch)
    ledger["calls"]["n"] = 0
    assert judge(ledger) == {1: True, 2: False, 3: True}
    assert ledger["calls"]["n"] == 0, "a flags-only change re-judged unrelated runs (file)"


def test_a_rewrite_of_one_runs_values_judges_only_that_run(ledger, monkeypatch):
    judge(ledger)
    rewrite_declared_name(ledger["chip"], 2, "alpha")
    ledger["calls"]["n"] = 0
    assert judge(ledger) == {1: True, 2: True, 3: True}
    assert ledger["calls"]["n"] == 1, "a rewrite of one run's values re-judged the others"
    rewrite_declared_name(ledger["chip"], 2, "beta")
    restart(monkeypatch)
    ledger["calls"]["n"] = 0
    assert judge(ledger) == {1: True, 2: False, 3: True}
    assert ledger["calls"]["n"] == 1


def test_a_change_of_every_event_judges_them_all(ledger, monkeypatch):
    judge(ledger)
    with HubStore(ledger["chip"]) as st:
        st.renumber()                     # logged as a change of every event
        st.conn.commit()
    ledger["calls"]["n"] = 0
    assert judge(ledger) == {1: True, 2: False, 3: True}
    assert ledger["calls"]["n"] == 3
