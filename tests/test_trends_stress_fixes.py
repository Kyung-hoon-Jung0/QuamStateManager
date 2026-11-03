"""fix/trends-stress: Trends consistency and readability (T1-T7).

A real-Chrome check of Trends against raw run files found the values right
and the presentation inconsistent. The client half is
``trends_witness_selfcheck.cjs`` (Chip Status Trends), ``trends_view_selfcheck.cjs``
(Datasets Trends) and ``split_arrows_selfcheck.cjs trend_chart``; this file
runs the first and pins the server half. The witness judgement itself
(``hub_witness``) is unchanged: only how it is shown.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from quam_state_manager.core import hub_witness
from quam_state_manager.web import hub_status, routes

ROOT = Path(__file__).resolve().parents[1]


def _node():
    if shutil.which("node") is None:
        pytest.skip("node not available")
    r = subprocess.run(["node", "-e", "require('jsdom')"], cwd=ROOT, capture_output=True)
    if r.returncode:
        pytest.skip("jsdom not installed")


def test_chip_trends_client_half():
    _node()
    r = subprocess.run(["node", "tests/trends_witness_selfcheck.cjs"], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0 and "trends_witness_selfcheck ok" in r.stdout, r.stdout + r.stderr


# ---------------------------------------------------------------- T2
def test_left_out_lists_not_kept_and_excursion_values():
    row = {"not_kept": [{"t_us": 2_000_000, "eid": 99, "value": 0.97968, "removed": False, "run_id": 1124,
                         "experiment": "irb", "by": {"kind": "run", "run_id": 1125, "value": 0.97622}}],
           "excursions": [{"points": [{"t_us": 3_000_000, "eid": 100, "value": 0.981, "removed": False,
                                       "run_id": 1130}],
                           "back": {"kind": "run", "run_id": 1140, "value": 0.97622}}]}
    got = hub_status.left_out(row)
    assert [(d["eid"], d["v"], d["run"], d["why"], d["who"], d["held"]) for d in got] == [
        (99, 0.97968, 1124, "not_kept", "#1125", 0.97622),
        (100, 0.981, 1130, "excursion", "#1140", 0.97622)]
    # the words are the witness rule's own
    assert set(hub_witness.WORDS) >= {d["why"] for d in got}
    live = hub_status.left_out({"not_kept": [{"t_us": 1, "eid": 5, "value": 7e-6, "removed": False,
                                              "run_id": 1763, "by": {"kind": "live", "value": 3e-6}}]})
    assert live[0]["who"] == "the chip" and live[0]["now"] is True


def test_witness_fields_ship_only_when_there_is_something():
    ser: dict = {}
    routes._trend_witness_fields(ser, [], None)
    assert ser == {}
    routes._trend_witness_fields(ser, [{"k": "20261014_061809_e99", "v": 0.97968, "run": 1124,
                                        "why": "not_kept", "who": "#1125", "held": 0.97622}], 3.45e-6)
    assert ser["left_out"] == [["20261014_061809_e99", 0.97968, 1124, "not_kept", "#1125", 0.97622, 0]]
    assert ser["now"] == 3.45e-6


# ---------------------------------------------------------------- T1
def _table(current):
    t = hub_status.LedgerTable.__new__(hub_status.LedgerTable)
    t._recorders, t._facts_now, t._merged = [], {}, {}
    t.ctx = {"store": None}
    t._targets = {("qubits.q4.T1", False): {"has_current": current is not None, "current": current,
                                            "array": None}}
    return t


def test_now_unlike_names_the_value_now_only_when_it_differs():
    assert _table(3.45e-6).now_unlike("qubits.q4.T1", 3e-5) == 3.45e-6
    assert _table(3e-5).now_unlike("qubits.q4.T1", 3e-5) is None
    assert _table(None).now_unlike("qubits.q4.T1", 3e-5) is None
    assert _table("text").now_unlike("qubits.q4.T1", 3e-5) is None


# ---------------------------------------------------------------- T5
def test_trend_chart_unit_is_the_tables_unit():
    assert routes._trend_display_unit("T1") == ("\u00b5s", 1e6)
    assert routes._trend_display_unit("T2echo") == ("\u00b5s", 1e6)
    assert routes._trend_display_unit("f_01") == ("GHz", 1e-9)
    unit, scale = routes._trend_display_unit("gate_fidelity_avg")
    assert scale == 1.0


# ---------------------------------------------------------------- T4
def test_changes_decide_the_newest_open_change_by_the_chip_value_now():
    t = SimpleNamespace(ctx={}, target=lambda p: {"path": p},
                        _live=lambda ctx, tg: {"qubits.q4.T1": 3.45e-6, "qubits.q1.T1": 1.2e-5})
    events = [{"eid": 119, "changes": [
        {"path": "qubits.q4.T1", "op": "set", "new": 7.2e-6, "doubt": hub_witness.OPEN},
        {"path": "qubits.q1.T1", "op": "set", "new": 1.2e-5, "doubt": hub_witness.OPEN},
        {"path": "qubits.q5.T1", "op": "set", "new": 8e-6, "doubt": hub_witness.OPEN}]}]
    got = routes._changes_live_doubts(t, events, "", 50)
    assert got[(119, "qubits.q4.T1")] == hub_witness.CONTRADICTED   # same words as Trends
    assert got[(119, "qubits.q1.T1")] is None                        # confirmed: no doubt
    assert (119, "qubits.q5.T1") not in got                          # value now unknown: stays open


class _Conn:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, args):
        return SimpleNamespace(fetchone=lambda: self.rows.get(args))


def test_calibration_age_leaves_out_what_the_chip_does_not_hold_now():
    t = _table(3.45e-6)
    t._live = lambda ctx, tg: {"qubits.q4.T1": 3.45e-6, "qubits.q1.T1": 1.2e-5}
    t._targets[("qubits.q1.T1", False)] = {"has_current": True, "current": 1.2e-5, "array": None}
    index = SimpleNamespace(paths={"qubits.q4.T1": 4, "qubits.q1.T1": 1, "ports.x": 9})
    verdicts = SimpleNamespace(tail=lambda pid: {4: (119, hub_witness.OPEN), 1: (119, hub_witness.OPEN),
                                                 9: (119, hub_witness.OPEN)}[pid])
    conn = _Conn({(4, 119): ("set", 7.2e-6, None), (1, 119): ("set", 1.2e-5, None)})
    assert t._live_left(conn, index, verdicts) == frozenset({(4, 119)})


def test_calibration_age_carries_the_run_it_comes_from():
    topo = {"nodes": [{"id": "q4"}], "edges": []}
    out = routes._fold_ledger_times(topo, {"q": {"q4": 1_700_000_000_000_000}, "p": {},
                                           "runs": {"q": {"q4": {"run": 989, "experiment": "25_T1"}}, "p": {}}})
    assert out["nodes"][0]["last_run_change_run"] == {"run": 989, "experiment": "25_T1"}


# ---------------------------------------------------------------- T6
def _write(folder: Path, state: dict, wiring: dict):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")


def test_runs_under_a_date_folder_are_never_a_same_chip_question(tmp_path):
    from quam_state_manager.core.history import HistoryManager
    from quam_state_manager.core.scanner import Workspace
    wiring = {"network": {"host": "10.1.1.1", "cluster_name": "C"}}
    root = tmp_path / "ws"
    for rid, day, when in [(10, "2026-10-12", "120000"), (11, "2026-10-13", "121000")]:
        run = root / "data" / day / f"#{rid}_x_{when}"
        _write(run / "quam_state", {"qubits": {q: {"id": q, "T1": rid * 1e-6} for q in ("q0", "q1")},
                                    "qubit_pairs": {}}, wiring)
        (run / "node.json").write_text(json.dumps({
            "id": rid, "created_at": f"{day}T{when[:2]}:{when[2:4]}:{when[4:]}",
            "metadata": {"name": "x", "status": "completed"}}), encoding="utf-8")
    loaded = tmp_path / "ws" / "chip"
    _write(loaded, {"qubits": {q: {"id": q, "T1": 25e-6} for q in ("q0", "q1")}, "qubit_pairs": {}}, wiring)
    ws = Workspace()
    ws.add_root(root / "data")
    hm = HistoryManager(tmp_path / "instance", max_snapshots=50, cache_size=3)
    report = hm.backfill_from_workspace(loaded, ws, instance_path=tmp_path / "instance")
    assert report["pending_decisions"] == []
    assert report["ingested"] == 2


def test_stale_date_folder_prompts_are_not_shown(monkeypatch):
    key = "Z:/chip"
    monkeypatch.setitem(routes._backfill_state, str(Path(key).resolve()), {"pending_decisions": [
        {"data_folder": "2026-10-12", "count": 35, "chip_key": "c"},
        {"data_folder": "SisterChip", "count": 3, "chip_key": "c"}]})
    assert [d["data_folder"] for d in routes._pending_decisions_for(Path(key))] == ["SisterChip"]


def test_an_empty_default_window_shows_every_change_and_says_why(tmp_path, monkeypatch):
    from quam_state_manager.web.app import create_app
    folder = tmp_path / "quam_state"
    st = {"qubits": {"qA1": {"id": "qA1", "T1": 8834e-9}}, "active_qubit_names": ["qA1"]}
    _write(folder, st, {"wiring": {"qubits": {"qA1": {}}}, "network": {"host": "10.1.1.18"}})
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(folder)})
    hm = app.config["history_manager"]
    hm.check_and_snapshot(folder, "manual", force=True)
    st["qubits"]["qA1"]["T1"] = 9000e-9
    (folder / "state.json").write_text(json.dumps(st), encoding="utf-8")
    hm.check_and_snapshot(folder, "manual", force=True)
    real = routes._parse_since
    # every recorded change is OLDER than the default 7-day window
    monkeypatch.setattr(routes, "_parse_since",
                        lambda v: "29990101_000000_000" if v == "now-7d" else real(v))
    html = ""
    for _ in range(60):
        html = c.get("/param-history", headers={"HX-Request": "true"}).get_data(as_text=True)
        if "param-history-summary" in html:
            break
        import time
        time.sleep(0.25)
    assert "No change was recorded in the last 7 days, so every recorded change is shown" in html
    assert "<strong>0</strong> recorded change" not in html
    # an explicit window is the user's choice: never widened
    html = c.get("/param-history?since=now-7d", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert "No change was recorded in the last 7 days" not in html
