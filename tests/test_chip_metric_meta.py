"""Chip Status queue items 4 + 10 (user, 2026-09-25).

Item 4: every per-metric panel says when each value was last measured, which
run wrote it and which snapshot -- on hover, and in the panel with a per-panel
"Show Meta Info" toggle. The data is ``GET /topology/metric-meta``: the newest
change point of the leaves each panel reads, from the docs/83 change-point
index. Item 10: Overview tiles jump to their panel (pinned in the selfcheck).

The staleness pin is the one that matters: after ANY sequence of snapshots the
route's answer equals an independent recompute from the raw snapshot contents
(the oracle below walks the states themselves, never the index).
"""
from __future__ import annotations

import json
import random
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from quam_state_manager.core import metric_meta as mm
from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent

_WIRING = {"network": {"host": "3.3.3.3", "cluster_name": "C9"},
           "ports": {"mw_outputs": {"con1": {"1": {"2": {"band": 1}}}}}}


# ── the pure module ─────────────────────────────────────────────────────────

class TestPaths:
    def test_qubit_paths_follow_the_page_fields_and_matrices(self):
        doc = {"qubits": {"q1": {
            "T1": 2e-5, "f_01": 5e9,
            "resonator": {"f_01": 7e9, "confusion_matrix": [[0.9, 0.1], [0.2, 0.8]]},
            "gate_fidelity": {"averaged": 0.999},
        }}}
        p = mm.qubit_paths(doc, ["q1"])
        assert p["T1"]["q1"] == ["qubits.q1.T1"]
        assert p["readout_frequency"]["q1"] == ["qubits.q1.resonator.f_01"]
        assert p["gate_fidelity_avg"]["q1"] == ["qubits.q1.gate_fidelity.averaged"]
        # a derived readout fidelity is as new as ANY cell of its matrix
        assert sorted(p["ro_fidelity_g"]["q1"]) == sorted(
            f"qubits.q1.resonator.confusion_matrix.{i}.{j}" for i in (0, 1) for j in (0, 1))
        assert "T2echo" not in p          # absent leaf -> no entry, never invented

    def test_a_pointer_is_followed_to_where_its_history_lives(self):
        doc = {"qubits": {"q1": {"xy": {"operations": {
            "x180_DragCosine": {"amplitude": "#/shared/amp"}}}}},
               "shared": {"amp": 0.3}}
        p = mm.qubit_paths(doc, ["q1"])
        assert p["x180_amplitude"]["q1"] == ["shared.amp"]

    def test_pair_rb_groups_like_the_page_and_keeps_ids_out(self):
        doc = {"qubit_pairs": {"q1-2": {"macros": {"cz_SNZ": {"fidelity": {
            "StandardRB": {"average_gate_fidelity": 0.97, "alpha": 0.99, "run_id": 530},
            "IRB": 0.99,
            "StandardRB_load_id": 529,
            "Bell_State": {"Fidelity": 0.9},
        }}}}}}
        paths, loads = mm.pair_rb_paths(doc, ["q1-2"])
        assert set(paths) == {"2q:StandardRB:cz_SNZ", "2q:InterleavedRB:cz_SNZ"}
        srb = paths["2q:StandardRB:cz_SNZ"]["q1-2"]
        assert "qubit_pairs.q1-2.macros.cz_SNZ.fidelity.StandardRB.run_id" not in srb
        assert "qubit_pairs.q1-2.macros.cz_SNZ.fidelity.StandardRB.average_gate_fidelity" in srb
        assert paths["2q:InterleavedRB:cz_SNZ"]["q1-2"] == [
            "qubit_pairs.q1-2.macros.cz_SNZ.fidelity.IRB"]
        # the lab's own run id: the metric's *_load_id first, else a nested id
        assert loads["2q:StandardRB:cz_SNZ"]["q1-2"] == 529
        assert loads["2q:InterleavedRB:cz_SNZ"]["q1-2"] == 529   # gate-level fallback


class TestFold:
    def test_newest_change_first_and_value(self):
        s = {"a": [("20260101_000000", 1.0, "manual", None, "", "")],
             "b": [("20260101_000000", 1.0, "manual", None, "", ""),
                   ("20260102_000000", 2.0, "experiment", 31, "06_ramsey", "f")]}
        e = mm.newest_change(s, ["a"])
        assert e["first"] is True and e["value"] == 1.0 and e["run"] is None
        e = mm.newest_change(s, ["b"])
        assert e == {"ts": "20260102_000000", "run": 31, "trigger": "experiment",
                     "first": False, "leaves": 1, "value": 2.0}
        e = mm.newest_change(s, ["a", "b"])
        assert e["ts"] == "20260102_000000" and e["first"] is False and "value" not in e
        assert mm.newest_change(s, ["zzz"]) is None
        gone = {"c": [("20260101_000000", 1.0, "m", None, "", ""),
                      ("20260103_000000", None, "m", None, "", "")]}
        assert mm.newest_change(gone, ["c"]).get("gone") is True


# ── the route, against real snapshots ───────────────────────────────────────

def _state(t1: float, f01: float, cm: list, marker: int, rb: float = 0.97) -> dict:
    return {"qubits": {"q1": {"id": "q1", "T1": t1, "f_01": f01,
                              "resonator": {"confusion_matrix": cm}},
                       "q2": {"id": "q2", "T1": 3e-5, "f_01": 5.2e9}},
            "qubit_pairs": {"q1-2": {"qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
                                     "macros": {"cz_SNZ": {"fidelity": {
                                         "StandardRB": {"average_gate_fidelity": rb},
                                         "StandardRB_load_id": 529}}}}},
            "active_qubit_names": ["q1", "q2"], "extras": {"marker": marker}}


def _write_chip(folder: Path, state: dict):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    _write_chip(live, _state(2e-5, 5e9, [[0.9, 0.1], [0.2, 0.8]], 0))
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "client": c, "live": live, "hm": app.config["history_manager"]}


def _snap(env, state, trigger="manual", **kw):
    _write_chip(env["live"], state)
    meta = env["hm"].check_and_snapshot(str(env["live"]), trigger, force=True, **kw)
    assert meta is not None
    return meta.timestamp


def _meta(env) -> dict:
    """The route's answer once the index is not being repaired (a read never
    waits for a rebuild; the page asks again while ``updating``)."""
    for _ in range(100):
        d = env["client"].get("/topology/metric-meta").get_json()
        if not d.get("updating"):
            return d
        time.sleep(0.1)
    raise AssertionError("index never settled")


def test_no_chip_is_an_honest_answer(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    d = app.test_client().get("/topology/metric-meta").get_json()
    assert d == {"ok": False, "reason": "no chip"}


def test_the_route_names_the_run_and_the_snapshot(env):
    t0 = _snap(env, _state(2e-5, 5e9, [[0.9, 0.1], [0.2, 0.8]], 1))
    t1 = _snap(env, _state(2.5e-5, 5e9, [[0.9, 0.1], [0.2, 0.8]], 2), trigger="experiment",
               experiment_name="25_T1", run_id=77)
    d = _meta(env)
    assert d["ok"] and d["snapshots"] >= 2
    t1e = d["q"]["T1"]["q1"]
    assert t1e["ts"] == t1 and t1e["run"] == 77 and t1e["first"] is False and t1e["value"] == 2.5e-5
    assert d["snaps"][t1]["run"] == 77
    # f_01 never changed: its newest row is its first -> "unchanged since"
    f = d["q"]["f_01"]["q1"]
    assert f["first"] is True and f["ts"] <= t0
    # the 2Q panel key is the page's own, and the lab's load_id rides along
    p = d["p"]["2q:StandardRB:cz_SNZ"]["q1-2"]
    assert p["load_id"] == 529 and p["ts"]


def _oracle(states: list[tuple[str, dict]], dot: str):
    """(ts, first) of the newest change of *dot*, from the raw states."""
    def get(doc, path):
        cur = doc
        for seg in path.split("."):
            if isinstance(cur, dict):
                cur = cur.get(seg)
            elif isinstance(cur, list) and seg.isdigit() and int(seg) < len(cur):
                cur = cur[int(seg)]
            else:
                return None
        return cur
    last_i, prev = None, object()
    seen = [(ts, get(st, dot)) for ts, st in states]
    seen = [(ts, v) for ts, v in seen if v is not None or last_i is not None]
    for i, (ts, v) in enumerate(seen):
        if i == 0 or v != prev:
            last_i = i
        prev = v
    if last_i is None:
        return None
    return seen[last_i][0], last_i == 0


def test_a_random_event_sequence_never_serves_a_stale_answer(env):
    """Staleness is the global risk: after every step of a random sequence of
    snapshots (value changes, matrix-cell changes, unchanged re-saves, runs
    and manual captures), the route equals the oracle computed from the raw
    states -- never an answer about an earlier step."""
    rng = random.Random(20260926)
    t1, f01, cm, rb = 2e-5, 5e9, [[0.9, 0.1], [0.2, 0.8]], 0.97
    states: list[tuple[str, dict]] = []
    for step in range(14):
        kind = rng.choice(["t1", "f01", "cm", "rb", "none"])
        if kind == "t1":
            t1 = round(t1 * rng.uniform(0.8, 1.2), 12)
        elif kind == "f01":
            f01 = f01 + rng.choice([-1, 1]) * 1e6
        elif kind == "cm":
            a = round(rng.uniform(0.85, 0.99), 4)
            cm = [[a, round(1 - a, 4)], cm[1]]
        elif kind == "rb":
            rb = round(rng.uniform(0.9, 0.99), 4)
        st = _state(t1, f01, cm, step + 10, rb)
        run = rng.choice([None, 100 + step])
        ts = _snap(env, st, trigger="experiment" if run else "manual",
                   **({"experiment_name": "x", "run_id": run} if run else {}))
        states.append((ts, st))
        d = _meta(env)
        for key, dot in (("T1", "qubits.q1.T1"), ("f_01", "qubits.q1.f_01")):
            exp = _oracle(states, dot)
            got = d["q"][key]["q1"]
            # history before the fixture's own states may hold earlier rows:
            # the oracle's FIRST row is only "first" when nothing preceded it
            assert got["ts"] == exp[0] or (exp[1] and got["first"]), (step, key, got, exp)
        cells = [f"qubits.q1.resonator.confusion_matrix.{i}.{j}" for i in (0, 1) for j in (0, 1)]
        exp_cm = max((_oracle(states, c) for c in cells), key=lambda x: x[0])
        got_cm = d["q"]["assignment_fidelity"]["q1"]
        assert got_cm["ts"] == exp_cm[0] or (exp_cm[1] and got_cm["first"]), (step, got_cm, exp_cm)
        exp_rb = _oracle(states, "qubit_pairs.q1-2.macros.cz_SNZ.fidelity.StandardRB.average_gate_fidelity")
        got_rb = d["p"]["2q:StandardRB:cz_SNZ"]["q1-2"]
        assert got_rb["ts"] == exp_rb[0] or (exp_rb[1] and got_rb["first"]), (step, got_rb, exp_rb)
        # the run named is the snapshot's own
        e = d["q"]["T1"]["q1"]
        assert d["snaps"].get(e["ts"], {}).get("run") == e["run"] or e["run"] is None


# ── the shipped JS ──────────────────────────────────────────────────────────

def test_the_page_selfcheck():
    """Items 4 + 10 in the real chip-status.js under jsdom
    (tests/chip_meta_info_selfcheck.cjs)."""
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    r = subprocess.run(["node", str(_ROOT / "tests" / "chip_meta_info_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(_ROOT), timeout=180)
    if r.returncode == 2 and "jsdom not installed" in (r.stderr or ""):
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, (r.stdout + r.stderr)
