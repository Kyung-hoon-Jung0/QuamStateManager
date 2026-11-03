"""fix/trends-stress2: the second real-Chrome round over Trends.

The client half is ``trends_stress2_selfcheck.cjs`` (run here); this file
pins the server half:

* item 1 -- a derived metric (readout fidelity over a confusion matrix) gets
  the same "now" value as a plain one: the formula over the matrix the chip
  holds now, when it differs from the line's last value;
* item 2 -- a left-out (hollow) value carries the run that saved it, openable
  (``uid`` on proof, else ``saved_uid``), and a "first recorded in #N" point
  opens the run whose saved state the ledger starts with;
* readability -- fidelities are drawn in %, and the y axis carries a human
  name, never the raw key.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web import hub_status, routes

ROOT = Path(__file__).resolve().parents[1]


def test_trends_stress2_client_half():
    if shutil.which("node") is None:
        pytest.skip("node not available")
    if subprocess.run(["node", "-e", "require('jsdom')"], cwd=ROOT, capture_output=True).returncode:
        pytest.skip("jsdom not installed")
    r = subprocess.run(["node", "tests/trends_stress2_selfcheck.cjs"], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0 and "trends_stress2_selfcheck ok" in r.stdout, r.stdout + r.stderr


# ---------------------------------------------------------------- item 1
def _table(matrix):
    t = hub_status.LedgerTable.__new__(hub_status.LedgerTable)
    t._recorders, t._facts_now, t._merged = [], {}, {}
    t.ctx = {"store": None}
    t._targets = {("qubits.q1.resonator.confusion_matrix", True):
                  {"has_current": matrix is not None, "current": matrix, "array": None}}
    t._eval = lambda kind, arg: None          # the facts are not under test here
    return t


_COMP = ("qubits.q1.resonator.confusion_matrix",
         ("qubits.q1.resonator.confusion_matrix.0.0", "qubits.q1.resonator.confusion_matrix.0.1",
          "qubits.q1.resonator.confusion_matrix.1.0", "qubits.q1.resonator.confusion_matrix.1.1"),
         None, "assignment_fidelity")


def test_a_derived_metric_names_its_value_now_when_it_differs():
    now = _table([[0.96, 0.04], [0.08, 0.92]]).now_unlike_derived(_COMP, 0.90)
    assert now == pytest.approx(0.94)
    assert _table([[0.9, 0.1], [0.1, 0.9]]).now_unlike_derived(_COMP, 0.90) is None
    assert _table(None).now_unlike_derived(_COMP, 0.90) is None
    assert _table("text").now_unlike_derived(_COMP, 0.90) is None


def test_curated_rows_ask_the_derived_value_now():
    import inspect
    src = inspect.getsource(hub_status.LedgerTable._curated)
    assert "now_unlike_derived" in src


# ---------------------------------------------------------------- item 2
def test_a_left_out_value_carries_the_run_that_saved_it():
    t = hub_status.LedgerTable.__new__(hub_status.LedgerTable)
    t._roots, t.uid_memo, t._now = [], {}, None
    t.present = lambda p, roots, memo, now=None: (
        {"uid": "u:1124"} if p.get("run_id") == 1124 else {"saved_uid": "u:1130"})
    row = {"not_kept": [{"t_us": 2_000_000, "eid": 99, "value": 0.97968, "removed": False, "run_id": 1124,
                         "by": {"kind": "run", "run_id": 1125, "value": 0.97622}}],
           "excursions": [{"points": [{"t_us": 3_000_000, "eid": 100, "value": 0.981, "removed": False,
                                       "run_id": 1130}],
                           "back": {"kind": "run", "run_id": 1140, "value": 0.97622}}]}
    got = t._left_out_keyed(row)
    assert [d.get("uid") for d in got] == ["u:1124", None]
    assert [d.get("saved_uid") for d in got] == [None, "u:1130"]
    assert all("_pt" not in d for d in got)
    ser: dict = {}
    routes._trend_witness_fields(ser, got, None)
    assert [r[7:] for r in ser["left_out"]] == [["u:1124", None], [None, "u:1130"]]


def test_a_first_recorded_point_opens_the_run_that_saved_it(monkeypatch):
    monkeypatch.setattr(routes, "_uid_for_run_ref", lambda folder, rid, roots: f"u:{rid}")
    pt = {"provenance": "first_record", "kind": "run", "run_id": 812, "experiment": "25_T1",
          "folder": "D:/data/2026-10-13/#812_25_T1", "flags": [], "value": 1e-5, "removed": False,
          "eid": 5}
    view = routes._vh_present(pt, [], {})
    assert view["label"] == "first recorded in #812"
    assert view["first_uid"] == "u:812" and not view["uid"] and not view["saved_uid"]
    # a Trends point carries it as the run to open (?via=saved)
    t = hub_status.LedgerTable.__new__(hub_status.LedgerTable)
    t._roots, t.uid_memo, t._now = [], {}, None
    t.present = lambda p, roots, memo, now=None: routes._vh_present(p, roots, memo, now=now)
    pt2 = dict(pt, t_us=1_000_000)
    _key, info = t.point(pt2)
    assert info.get("first_uid") == "u:812" and "uid" not in info and "saved_uid" not in info
    gone = routes._vh_present(dict(pt, flags=["source_gone"]), [], {})
    assert gone["first_uid"] is None


# ---------------------------------------------------------------- readability
def test_fidelities_are_drawn_in_percent_and_named():
    assert routes._trend_display_unit("gate_fidelity_avg") == ("%", 100.0)
    assert routes._trend_display_unit("assignment_fidelity") == ("%", 100.0)
    assert routes._trend_display_unit("macros.cz.fidelity.StandardRB") == ("%", 100.0)
    # a decay base is not a fidelity
    assert routes._trend_display_unit("macros.cz.fidelity.StandardRB.alpha")[0] != "%"
    assert routes._trend_axis_title("assignment_fidelity") == "Readout Fidelity (GE)"
    assert routes._trend_axis_title("gate_fidelity_avg") == "1Q gate fidelity"
    assert routes._trend_axis_title("resonator.operations.readout.amplitude") == "readout amplitude"
