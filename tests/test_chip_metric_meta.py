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
    O = "20260101_000000"

    def test_newest_change_first_and_value(self):
        s = {"a": [("20260101_000000", 1.0, "manual", None, "", "")],
             "b": [("20260101_000000", 1.0, "manual", None, "", ""),
                   ("20260102_000000", 2.0, "experiment", 31, "06_ramsey", "f")]}
        e = mm.newest_change(s, ["a"], oldest=self.O)
        assert e["first"] is True and e["value"] == 1.0 and e["run"] is None
        assert "appeared" not in e
        e = mm.newest_change(s, ["b"], oldest=self.O)
        assert e == {"ts": "20260102_000000", "run": 31, "trigger": "experiment",
                     "first": False, "leaves": 1, "value": 2.0}
        e = mm.newest_change(s, ["a", "b"], oldest=self.O)
        assert e["ts"] == "20260102_000000" and e["first"] is False and "value" not in e
        assert mm.newest_change(s, ["zzz"], oldest=self.O) is None
        gone = {"c": [("20260101_000000", 1.0, "m", None, "", ""),
                      ("20260103_000000", None, "m", None, "", "")]}
        assert mm.newest_change(gone, ["c"], oldest=self.O).get("gone") is True
        # the oldest snapshot unknown: nothing is called "since history began"
        assert mm.newest_change(s, ["a"])["first"] is False

    def test_a_value_that_appears_later_is_its_first_write_not_history_began(self):
        """Verifier P1 (2026-09-26): the index records no row for a null or
        absent leaf, so a leaf null in snapshot 1 and a number from snapshot 2
        on has ONE row -- at its write. That is not "unchanged since history
        began"; it appeared then."""
        s = {"t1": [("20260102_000000", 2e-5, "experiment", 142, "11_rabi", "f")],
             "cm0": [("20260101_000000", 0.9, "m", None, "", "")],
             "cm1": [("20260105_000000", 0.8, "auto", None, "", "")]}
        e = mm.newest_change(s, ["t1"], oldest=self.O)
        assert e["first"] is False and e["appeared"] is True and e["ts"] == "20260102_000000"
        assert e["run"] == 142
        # a subtree with one cell there from the start and one appearing later
        e = mm.newest_change(s, ["cm0", "cm1"], oldest=self.O)
        assert e["first"] is False and e["appeared"] is True and e["ts"] == "20260105_000000"

    def test_matches_current_covers_every_leaf_of_a_subtree(self):
        """Verifier P1: a readout fidelity from a confusion matrix has no one
        value, so only a per-leaf comparison says the matrix on screen is not
        one history ever held."""
        s = {f"m.{i}": [(self.O, v, "m", None, "", "")] for i, v in enumerate((0.754, 0.246))}
        same = {"m.0": 0.754, "m.1": 0.246}
        assert mm.newest_change(s, ["m.0", "m.1"], oldest=self.O, current=same)["matches_current"] is True
        assert mm.newest_change(s, ["m.0", "m.1"], oldest=self.O,
                                current={"m.0": 0.968, "m.1": 0.246})["matches_current"] is False
        # float noise is not an edit; a leaf history never held is
        assert mm.newest_change(s, ["m.0"], oldest=self.O,
                                current={"m.0": 0.754 * (1 + 1e-13)})["matches_current"] is True
        assert mm.newest_change(s, ["m.0", "m.9"], oldest=self.O,
                                current={"m.0": 0.754, "m.9": 0.5})["matches_current"] is False
        assert "matches_current" not in mm.newest_change(s, ["m.0"], oldest=self.O)


# ── the route, against real snapshots ───────────────────────────────────────

def _state(t1: float, f01: float, cm: list, marker: int, rb: float = 0.97,
           t2e=None) -> dict:
    return {"qubits": {"q1": {"id": "q1", "T1": t1, "f_01": f01, "T2echo": t2e,
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


def _take_live(env):
    """The page's "Take live": the working copy becomes the live content."""
    r = env["client"].post("/state/sync", data={"mode": "discard"},
                           headers={"Origin": "http://localhost"})
    assert r.status_code == 200, r.get_data(as_text=True)


def _history_states(env) -> list[tuple[str, dict]]:
    """EVERY snapshot the chip's history holds, oldest first, read from the
    snapshot files themselves -- never from the change-point index."""
    hm, live = env["hm"], env["live"]
    metas = sorted(hm.list_snapshots(str(live)), key=lambda m: m.timestamp)
    return [(m.timestamp, hm.load_snapshot(str(live), m.timestamp).merged) for m in metas]


def _get(doc, path):
    cur = doc
    for seg in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(seg)
        elif isinstance(cur, list) and seg.isdigit() and int(seg) < len(cur):
            cur = cur[int(seg)]
        else:
            return None
    return cur


def _oracle(states: list[tuple[str, dict]], dot: str):
    """(ts, first) of the newest change of *dot*, from the raw states.

    A null/absent value is a state like any other (an index row is only made
    where a number appears, changes or disappears), so a leaf that was null
    in the early snapshots changes when it is first written. ``first`` is True
    only when the newest change is at the OLDEST snapshot -- the value was
    there when history began and never moved."""
    prev, last_i = None, None
    for i, (_ts, st) in enumerate(states):
        v = _get(st, dot)
        if v != prev:
            last_i = i
        prev = v
    if last_i is None:
        return None
    return states[last_i][0], last_i == 0


def test_a_value_null_in_the_first_snapshot_is_never_since_history_began(env):
    """Verifier P1 repro, pinned: q1.T2echo is None in snapshot 1 and a number
    from snapshot 2 on. Its one index row is the write -- the route must say
    so (first False, appeared True, the write's own run), never "unchanged
    since history began"."""
    _snap(env, _state(2e-5, 5e9, [[0.9, 0.1], [0.2, 0.8]], 1))
    t2 = _snap(env, _state(2e-5, 5e9, [[0.9, 0.1], [0.2, 0.8]], 2, t2e=3.3e-5),
               trigger="experiment", experiment_name="12_echo", run_id=142)
    _snap(env, _state(2e-5, 5e9, [[0.9, 0.1], [0.2, 0.8]], 3, t2e=3.3e-5))
    _take_live(env)
    d = _meta(env)
    e = d["q"]["T2echo"]["q1"]
    assert e["ts"] == t2 and e["first"] is False and e.get("appeared") is True, e
    assert e["run"] == 142 and e["matches_current"] is True
    # f_01 WAS there from the oldest snapshot and never moved
    assert d["q"]["f_01"]["q1"]["first"] is True


def test_an_edited_matrix_cell_is_not_in_history(env):
    """Verifier P1 repro, pinned: the working copy's confusion matrix differs
    from every snapshot (a cell edited, no new snapshot). The readout
    fidelity's entry must say the value on screen is not history's."""
    _snap(env, _state(2e-5, 5e9, [[0.9, 0.1], [0.2, 0.8]], 1))
    _take_live(env)
    d = _meta(env)
    assert d["q"]["assignment_fidelity"]["q1"]["matches_current"] is True
    tok = env["client"].get("/api/agent/chip").get_json()["chip_token"]
    r = env["client"].post("/field/edit", data={
        "dot_path": "qubits.q1.resonator.confusion_matrix.0.0", "value": "0.968",
        "expect_chip": tok}, headers={"Origin": "http://localhost"})
    assert r.status_code == 200, r.get_data(as_text=True)
    d = _meta(env)
    for key in ("assignment_fidelity", "ro_fidelity_g", "ro_fidelity_e"):
        assert d["q"][key]["q1"]["matches_current"] is False, (key, d["q"][key]["q1"])
    assert d["q"]["T1"]["q1"]["matches_current"] is True       # untouched leaf


def test_a_random_event_sequence_never_serves_a_stale_answer(env):
    """Staleness is the global risk: after every step of a random sequence of
    snapshots (value changes, matrix-cell changes, a leaf appearing from null
    and going back to null, unchanged re-saves, runs and manual captures, each
    followed by "Take live"), the route equals the oracle computed from EVERY
    raw snapshot state the history holds -- never an answer about an earlier
    step, and never "first" for a value that appeared after history began."""
    rng = random.Random(20260926)
    t1, f01, cm, rb, t2e = 2e-5, 5e9, [[0.9, 0.1], [0.2, 0.8]], 0.97, None
    firsts = appeared = 0
    for step in range(16):
        kind = rng.choice(["t1", "f01", "cm", "rb", "t2e", "t2e_null", "none"])
        if kind == "t1":
            t1 = round(t1 * rng.uniform(0.8, 1.2), 12)
        elif kind == "f01":
            f01 = f01 + rng.choice([-1, 1]) * 1e6
        elif kind == "cm":
            a = round(rng.uniform(0.85, 0.99), 4)
            cm = [[a, round(1 - a, 4)], cm[1]]
        elif kind == "rb":
            rb = round(rng.uniform(0.9, 0.99), 4)
        elif kind == "t2e":
            t2e = round(rng.uniform(1e-5, 5e-5), 12)
        elif kind == "t2e_null":
            t2e = None
        st = _state(t1, f01, cm, step + 10, rb, t2e)
        run = rng.choice([None, 100 + step])
        _snap(env, st, trigger="experiment" if run else "manual",
              **({"experiment_name": "x", "run_id": run} if run else {}))
        _take_live(env)
        states = _history_states(env)
        d = _meta(env)
        checks = [(d["q"].get("T1", {}).get("q1"), ["qubits.q1.T1"]),
                  (d["q"].get("f_01", {}).get("q1"), ["qubits.q1.f_01"]),
                  (d["q"].get("T2echo", {}).get("q1"), ["qubits.q1.T2echo"]),
                  (d["q"].get("assignment_fidelity", {}).get("q1"),
                   [f"qubits.q1.resonator.confusion_matrix.{i}.{j}" for i in (0, 1) for j in (0, 1)]),
                  (d["p"].get("2q:StandardRB:cz_SNZ", {}).get("q1-2"),
                   ["qubit_pairs.q1-2.macros.cz_SNZ.fidelity.StandardRB.average_gate_fidelity"])]
        for got, dots in checks:
            if t2e is None and dots == ["qubits.q1.T2echo"]:
                # not a number on screen now -> the page reads no leaf, no entry
                assert got is None, (step, got)
                continue
            exps = [_oracle(states, dt) for dt in dots]
            newest_ts = max(x[0] for x in exps)
            assert got["ts"] == newest_ts, (step, dots, got, exps)
            assert got["first"] is all(x[1] for x in exps), (step, dots, got, exps)
            # the working copy was just taken from live == the newest snapshot
            assert got["matches_current"] is True, (step, dots, got)
            if got["first"]:
                assert got["ts"] == d["oldest"] == states[0][0], (step, dots, got, d["oldest"])
            firsts += got["first"]
            appeared += bool(got.get("appeared"))
        e = d["q"]["T1"]["q1"]
        assert d["snaps"].get(e["ts"], {}).get("run") == e["run"] or e["run"] is None
    # the sequence exercised both labels (a vacuous pass would see neither)
    assert firsts and appeared, (firsts, appeared)


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
