"""Tests for the workbench before/after loop: ``GET /state/live-diff`` and the
inline-Explorer accept path (``/field/edit-batch`` with raw JSON values).

``/state/live-diff`` is the JSON sibling of ``/state/review`` — it diffs the SM
working copy (``old`` = before) against Qualibrate's live state files (``new`` =
after). It drives the content-aware workbench nudge (only fire when the live
state TRULY differs) and the inline Explorer diff (``?with_live=1`` ships the raw
live dicts for the tree's ``refData``). "Accept Qualibrate's value" rides on the
existing atomic ``/field/edit-batch``, so accepting a field makes it stop
differing on the next live-diff.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app


# --- synth (same shape as test_state_sync_modes.py) ------------------------


def _make_state(f_01: float = 6.25e9, tof: int = 280) -> dict:
    return {
        "qubits": {
            "qA1": {
                "id": "qA1",
                "f_01": f_01,
                "xy": {"RF_frequency": f_01},
                "resonator": {
                    "RF_frequency": 7.64e9,
                    "time_of_flight": tof,
                    "operations": {"readout": {"amplitude": 0.042, "length": 1000}},
                },
            },
        },
        "qubit_pairs": {},
        "active_qubit_names": ["qA1"],
    }


def _make_wiring() -> dict:
    return {
        "wiring": {"qubits": {"qA1": {"xy": {"opx_output": "MW-FEM/1/2"}}}},
        "network": {"host": "10.1.1.18"},
    }


def _write_live_state(folder: Path, state: dict) -> None:
    """Rewrite live state.json with a future mtime (simulates Qualibrate's save)."""
    p = folder / "state.json"
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")
    future = time.time() + 100
    os.utime(p, (future, future))


@pytest.fixture
def synth_folder(tmp_path: Path) -> Path:
    (tmp_path / "state.json").write_text(json.dumps(_make_state(), indent=2), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2), encoding="utf-8")
    return tmp_path


@pytest.fixture
def loaded_client(tmp_path, synth_folder):
    app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
    client = app.test_client()
    client.post("/load", data={"folder": str(synth_folder)})
    return client


# --- /state/live-diff -------------------------------------------------------


def test_live_diff_no_chip_is_400():
    app = create_app(testing=True)
    r = app.test_client().get("/state/live-diff")
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_live_diff_fresh_load_is_zero(loaded_client):
    # working copy == live straight after load.
    d = loaded_client.get("/state/live-diff").get_json()
    assert d["ok"] is True
    assert d["total"] == 0
    assert d["entries"] == []


def test_live_diff_reports_qualibrate_change_before_after(loaded_client, synth_folder):
    # Qualibrate writes a new time_of_flight to the live files.
    _write_live_state(synth_folder, _make_state(tof=36))
    d = loaded_client.get("/state/live-diff").get_json()
    assert d["total"] == 1
    e = d["entries"][0]
    assert e["dot_path"] == "qubits.qA1.resonator.time_of_flight"
    assert e["old"] == 280   # before = SM working copy
    assert e["new"] == 36    # after  = Qualibrate live
    assert e["change_type"] == "modified"


def test_live_diff_with_live_returns_raw_dicts(loaded_client, synth_folder):
    _write_live_state(synth_folder, _make_state(tof=36))
    d = loaded_client.get("/state/live-diff?with_live=1").get_json()
    assert d["live_state"]["qubits"]["qA1"]["resonator"]["time_of_flight"] == 36
    assert "wiring" in d["live_wiring"]
    # without the flag, the raw dicts are omitted (keeps the nudge poll light).
    d2 = loaded_client.get("/state/live-diff").get_json()
    assert "live_state" not in d2 and "live_wiring" not in d2


def test_live_diff_touch_without_change_is_zero(loaded_client, synth_folder):
    # A save that doesn't alter any value (mtime bumps, content identical) must
    # report total 0 — this is what makes the nudge content-aware.
    _write_live_state(synth_folder, _make_state())  # same values as load
    d = loaded_client.get("/state/live-diff").get_json()
    assert d["total"] == 0


# --- accept path (inline Explorer ✓ / Accept-all → /field/edit-batch) -------


def test_accept_live_value_via_batch_makes_it_stop_differing(loaded_client, synth_folder):
    _write_live_state(synth_folder, _make_state(tof=36))
    # The inline ✓ posts the raw live value through the atomic batch endpoint.
    r = loaded_client.post(
        "/field/edit-batch",
        json={"updates": [{"dot_path": "qubits.qA1.resonator.time_of_flight", "value": 36}]},
    )
    assert r.status_code == 200 and r.get_json()["ok"] is True
    # Accepting Qualibrate's value into the working copy closes that diff.
    assert loaded_client.get("/state/live-diff").get_json()["total"] == 0


def test_accept_all_fields_atomic(loaded_client, synth_folder):
    # f_01 feeds both qubits.qA1.f_01 and qubits.qA1.xy.RF_frequency (two literal
    # fields), so this live write touches 3 paths (those two + tof).
    _write_live_state(synth_folder, _make_state(f_01=7.0e9, tof=36))
    d = loaded_client.get("/state/live-diff").get_json()
    assert d["total"] == 3
    updates = [{"dot_path": e["dot_path"], "value": e["new"]} for e in d["entries"]]
    r = loaded_client.post("/field/edit-batch", json={"updates": updates})
    assert r.get_json()["ok"] is True
    # Accept-all closes every diff.
    assert loaded_client.get("/state/live-diff").get_json()["total"] == 0


# --- /explorer renders the live-diff controls ------------------------------


def test_explorer_renders_livediff_controls(loaded_client):
    body = loaded_client.get("/explorer").get_data(as_text=True)
    assert "explorer-livediff-toggle" in body
    assert "explorer-livediff-bar" in body
    assert "explorerLiveDiff" in body


# --- QA JT-03: who moved each differing field --------------------------------

_TOF = "qubits.qA1.resonator.time_of_flight"


def test_live_diff_own_edit_is_mine_not_qualibrate(loaded_client):
    """The user's own unapplied edit, nothing written outside SM: the diff is
    SM-side, so it must never be attributed to Qualibrate (was: the bar read
    "Qualibrate changed 1 field(s)" and ✓ reverted the edit)."""
    r = loaded_client.post("/field/edit-batch",
                           json={"updates": [{"dot_path": _TOF, "value": 99}]})
    assert r.get_json()["ok"] is True
    d = loaded_client.get("/state/live-diff").get_json()
    assert d["total"] == 1
    assert d["live_moved"] is False
    assert _TOF in d["mine"]
    assert d["external"] == [] and d["conflicts"] == []


def test_live_diff_outside_write_is_external(loaded_client, synth_folder):
    _write_live_state(synth_folder, _make_state(tof=36))
    d = loaded_client.get("/state/live-diff").get_json()
    assert d["live_moved"] is True
    assert d["external"] == [_TOF]
    assert d["mine"] == [] and d["conflicts"] == []


def test_live_diff_both_sides_is_a_conflict(loaded_client, synth_folder):
    loaded_client.post("/field/edit-batch",
                       json={"updates": [{"dot_path": _TOF, "value": 99}]})
    _write_live_state(synth_folder, _make_state(tof=36))
    d = loaded_client.get("/state/live-diff").get_json()
    assert d["live_moved"] is True
    assert d["conflicts"] == [_TOF]
    assert _TOF not in d["external"]


def test_live_diff_mixed_names_each_side(loaded_client, synth_folder):
    amp = "qubits.qA1.resonator.operations.readout.amplitude"
    loaded_client.post("/field/edit-batch",
                       json={"updates": [{"dot_path": amp, "value": 0.05}]})
    _write_live_state(synth_folder, _make_state(tof=36))
    d = loaded_client.get("/state/live-diff").get_json()
    assert d["total"] == 2 and d["live_moved"] is True
    assert amp in d["mine"] and d["external"] == [_TOF] and d["conflicts"] == []


# --- QA r2-02: Accept all creates added keys and DELETES removed ones ---------


def test_edit_batch_delete_removes_the_key_never_stores_null(loaded_client, synth_folder):
    live = _make_state()
    del live["qubits"]["qA1"]["resonator"]["time_of_flight"]
    live["qubits"]["qA1"]["extras_added"] = 123.5
    _write_live_state(synth_folder, live)
    d = loaded_client.get("/state/live-diff").get_json()
    assert d["total"] == 2
    r = loaded_client.post("/field/edit-batch", json={"independent": True, "updates": [
        {"dot_path": _TOF, "delete": True},
        {"dot_path": "qubits.qA1.extras_added", "value": 123.5, "create": True},
    ]}).get_json()
    assert [x["applied"] for x in r["results"]] == [True, True], r
    assert r["results"][0].get("deleted") is True
    # the key is GONE (a null would still differ from live, which lacks it)
    assert loaded_client.get("/state/live-diff").get_json()["total"] == 0


def test_edit_batch_delete_keeps_the_crud_guards(loaded_client):
    r = loaded_client.post("/field/edit-batch", json={"updates": [
        {"dot_path": "qubits", "delete": True}]})
    assert r.status_code == 400
    assert "top-level" in r.get_json()["results"][0]["error"]
    r = loaded_client.post("/field/edit-batch", json={"updates": [
        {"dot_path": "qubits.qA1.id", "delete": True}]})
    assert r.status_code == 400 and r.get_json()["results"][0]["applied"] is False
    # an atomic batch whose later row fails restores the deleted key
    r = loaded_client.post("/field/edit-batch", json={"updates": [
        {"dot_path": _TOF, "delete": True},
        {"dot_path": "qubits.qA1.no_such_key", "value": 1},
    ]})
    assert r.status_code == 400
    assert loaded_client.get("/state/live-diff").get_json()["total"] == 0
