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
        # S8 review P0-1 (supersedes "as new as ANY cell of its matrix"): a derived
        # readout fidelity is as new as the cells its number is made of
        cm = "qubits.q1.resonator.confusion_matrix"
        assert p["ro_fidelity_g"]["q1"] == [f"{cm}.0.0"]
        assert p["ro_fidelity_e"]["q1"] == [f"{cm}.1.1"]
        assert sorted(p["assignment_fidelity"]["q1"]) == [f"{cm}.0.0", f"{cm}.1.1"]
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

    def test_a_near_miss_value_is_not_history(self):
        """Verifier D4a (2026-09-27): a 1 % tolerance passed every pin. A
        relative difference of 1e-4 (a 5 GHz frequency moved by 500 kHz, a
        fidelity's 4th digit) is a different value, never "matches history";
        1e-12 is float noise."""
        s = {"f": [(self.O, 5.0e9, "m", None, "", "")],
             "r": [(self.O, 0.9912, "m", None, "", "")]}
        for dp, v in (("f", 5.0e9), ("r", 0.9912)):
            for rel in (1e-4, -1e-4, 1e-6, 5e-3):
                e = mm.newest_change(s, [dp], oldest=self.O, current={dp: v * (1 + rel)})
                assert e["matches_current"] is False, (dp, rel)
            e = mm.newest_change(s, [dp], oldest=self.O, current={dp: v * (1 + 1e-12)})
            assert e["matches_current"] is True, dp

    def test_appeared_only_when_the_lone_row_is_the_newest_change(self):
        """Verifier D4b: a subtree whose leaf A appeared late (one row) but
        whose leaf B changed AFTER that: the newest change is B's second row,
        a change of a value that already existed -- not an appearance."""
        s = {"a": [("20260103_000000", 0.1, "m", None, "", "")],
             "b": [(self.O, 0.9, "m", None, "", ""),
                   ("20260105_000000", 0.8, "experiment", 9, "x", "f")]}
        e = mm.newest_change(s, ["a", "b"], oldest=self.O)
        assert e["ts"] == "20260105_000000" and e["first"] is False
        assert "appeared" not in e, e
        # ...and the same subtree with A's lone row the newest does appear
        s["a"] = [("20260106_000000", 0.1, "m", None, "", "")]
        assert mm.newest_change(s, ["a", "b"], oldest=self.O).get("appeared") is True


class TestTruncated:
    """Verifier D2 (2026-09-27): on a chip larger than the change-point index
    covers (``leaf_meta truncated=1``), a leaf's lone row later than the
    oldest snapshot may be a cap artifact and a leaf with no row may simply
    never have been walked. Neither is a fact to date a value from."""
    O, T2, T3 = "20260101_000000", "20260102_000000", "20260103_000000"

    def test_missing_or_unverified_leaves_are_incomplete_not_dated(self):
        s = {"a": [(self.T2, 1.0, "experiment", 5, "x", "f")],
             "b": [(self.O, 2.0, "m", None, "", ""), (self.T3, 3.0, "m", None, "", "")]}
        assert mm.newest_change(s, ["zzz"], oldest=self.O, truncated=True) ==             {"incomplete": True, "leaves": 0}
        assert mm.newest_change(s, ["a"], oldest=self.O, truncated=True)["incomplete"] is True
        assert mm.newest_change(s, ["a", "b"], oldest=self.O, truncated=True)["incomplete"] is True
        # a multi-row leaf is dated as before; a confirmed lone row too
        assert mm.newest_change(s, ["b"], oldest=self.O, truncated=True)["ts"] == self.T3
        e = mm.newest_change(s, ["a"], oldest=self.O, truncated=True, confirmed={"a"})
        assert e["ts"] == self.T2 and e.get("appeared") is True
        # the same inputs on a complete index are dated (no regression)
        assert mm.newest_change(s, ["a"], oldest=self.O)["ts"] == self.T2

    def test_the_snapshot_before_a_lone_row_decides_what_it_was(self):
        s = {"same_at_oldest": [(self.T2, 1.0, "m", None, "", "")],
             "absent_before": [(self.T2, 2.0, "experiment", 7, "x", "f")],
             "changed": [(self.T2, 3.0, "experiment", 7, "x", "f")],
             "same_later": [(self.T3, 4.0, "m", None, "", "")],
             "multi": [(self.O, 5.0, "experiment", 1000, "boot", "f0"), (self.T3, 6.0, "m", None, "", "")]}
        files = {self.O: {"same_at_oldest": 1.0, "changed": 2.5},
                 self.T2: {"same_later": 4.0}}
        reads = []

        def load(ts, paths):
            reads.append((ts, sorted(paths)))
            return {p: files[ts].get(p) for p in paths}

        out, conf = mm.verify_truncated(s, oldest=self.O, ts_list=[self.O, self.T2, self.T3],
                                        load_values=load)
        assert conf == {"same_at_oldest", "absent_before", "changed"}
        e = mm.newest_change(out, ["same_at_oldest"], oldest=self.O, truncated=True, confirmed=conf)
        assert e["first"] is True and e["ts"] == self.O
        # the oldest snapshot's own provenance rides along (read off another
        # leaf's row at it), never an invented "no run"
        assert e["run"] == 1000 and e["trigger"] == "experiment"
        e = mm.newest_change(out, ["absent_before"], oldest=self.O, truncated=True, confirmed=conf)
        assert e["ts"] == self.T2 and e["appeared"] is True and e["run"] == 7
        e = mm.newest_change(out, ["changed"], oldest=self.O, truncated=True, confirmed=conf)
        assert e["ts"] == self.T2 and "appeared" not in e and e["first"] is False
        assert mm.newest_change(out, ["same_later"], oldest=self.O, truncated=True,
                                confirmed=conf)["incomplete"] is True
        assert out["multi"] is s["multi"]           # never re-read
        # bounded: one read per predecessor snapshot, the budget honoured
        assert len(reads) == 2
        reads.clear()
        out1, conf1 = mm.verify_truncated(s, oldest=self.O, ts_list=[self.O, self.T2, self.T3],
                                          load_values=load, max_snaps=1)
        assert len(reads) == 1 and reads[0][0] == self.O       # the busiest first
        assert "same_later" not in conf1
        # an unreadable snapshot leaves its leaves unconfirmed
        def boom(ts, paths):
            raise OSError("gone")
        _o, c2 = mm.verify_truncated(s, oldest=self.O, ts_list=[self.O, self.T2, self.T3],
                                     load_values=boom)
        assert c2 == set()


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
    # S10 C3: fabricated snapshot run hints -> real ledger runs, retaining the raw-state oracle.
    from datetime import datetime, timezone
    from quam_state_manager.core import hub_sync, value_history
    from quam_state_manager.core.hub_store import HubStore
    from quam_state_manager.core.loader import flatten
    from tests.ledger_fixture import declare_root
    before = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
    _write_chip(env["live"], state)
    meta = env["hm"].check_and_snapshot(str(env["live"]), trigger, force=True, **kw)
    assert meta is not None
    if trigger == "experiment":
        root = env["live"].parent.parent / "data"
        folder = root / "2026-10-09" / f"#{kw['run_id']}_scan_010000"
        _write_chip(folder / "quam_state", state)
        flat, old = flatten(state), flatten(before)
        patches = [{"op": "replace", "path": "/quam/" + p.replace(".", "/"),
                    "value": v, "old": old.get(p)} for p, v in flat.items()
                   if p not in old or old[p] != v]
        instant = hub_sync.snapshot_instant_us(meta.timestamp)
        clock = datetime.fromtimestamp(instant / 1e6, timezone.utc).isoformat()
        (folder / "node.json").write_text(json.dumps({"id": kw["run_id"], "created_at": clock,
            "metadata": {"name": kw.get("experiment_name", "scan"), "status": "finished"},
            "patches": patches}), encoding="utf-8")
        env["client"].post("/workspace/add", data={"folder": str(root)})
        directory = declare_root(env["client"], root)
    else:
        with env["app"].app_context():
            from quam_state_manager.web import routes
            directory = routes._active_ctx()["hub_chip_dir"]
    with HubStore(directory) as store:
        if trigger == "experiment":
            eid = store.conn.execute("SELECT eid FROM events WHERE run_id=?", (kw["run_id"],)).fetchone()[0]
        else:
            eid = store.conn.execute("SELECT eid FROM events WHERE t_src=?", (meta.timestamp,)).fetchone()[0]
    env.setdefault("event_ids", {})[meta.timestamp] = eid
    return meta.timestamp


def _instant(ts):
    from quam_state_manager.core import hub_sync, value_history
    return value_history.iso_z(hub_sync.snapshot_instant_us(ts))


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
    # S10 C3: snapshot stamp/capturer -> ledger instant and own-patch writer, no capturer map.
    assert t1e["ts"] == _instant(t1) and t1e["eid"] == env["event_ids"][t1]
    assert t1e["run"] == 77 and t1e["first"] is False and t1e["value"] == 2.5e-5
    assert t1e["provenance"] == "run_proven" and d["snaps"] == {}
    # f_01 never changed: its newest row is its first -> "unchanged since"
    f = d["q"]["f_01"]["q1"]
    assert f["first"] is True and f["ts"] == _instant(t0) and f["provenance"] == "observed"
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
    # S10 C3: snapshot appearance flag -> proven ledger change, retaining the no-false-baseline pin.
    assert e["ts"] == _instant(t2) and e["eid"] == env["event_ids"][t2] and e["first"] is False, e
    assert e["provenance"] == "run_proven" and e["value"] == 3.3e-5
    assert e["run"] == 142 and e["matches_current"] is True
    # f_01 WAS there from the oldest snapshot and never moved
    assert d["q"]["f_01"]["q1"]["provenance"] == "observed"
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
    for key in ("assignment_fidelity", "ro_fidelity_g"):
        assert d["q"][key]["q1"]["matches_current"] is False, (key, d["q"][key]["q1"])
    # S8 review P0-1: |e> reads cell 1.1 only -- the 0.0 edit did not change the number
    # it shows (this pin used to expect False: every cell of the matrix counted)
    assert d["q"]["ro_fidelity_e"]["q1"]["matches_current"] is True
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
            # S10 C3: snapshot stamp/first flag -> ledger instant/event, observations never claim a writer.
            assert got["ts"] == _instant(newest_ts), (step, dots, got, exps)
            assert got["eid"] == env["event_ids"][newest_ts], (step, dots, got, exps)
            assert got["first"] is all(x[1] for x in exps), (step, dots, got, exps)
            # the working copy was just taken from live == the newest snapshot
            assert got["matches_current"] is True, (step, dots, got)
            if all(x[1] for x in exps):
                assert got["ts"] == d["oldest"] == _instant(states[0][0]), (step, dots, got, d["oldest"])
                assert got["provenance"] == "observed"
                firsts += 1
            appeared += any(not x[1] for x in exps)
        e = d["q"]["T1"]["q1"]
        assert e["run"] is None or e["writer"]["run"] == e["run"]
    # the sequence exercised both labels (a vacuous pass would see neither)
    assert firsts and appeared, (firsts, appeared)


def test_a_truncated_index_never_dates_what_it_cannot_vouch_for(env, monkeypatch):
    """Verifier D2 repro, pinned small: the big30x index hit both caps (walk
    and rows per snapshot) and the route published the artifacts as facts
    (125/330 wrong: "First measured ... run #1001" for a value unchanged since
    the oldest snapshot, "No change on record" for leaves never walked). Here
    the same two caps are forced on a tiny chip. Every entry the route DATES
    must equal the oracle computed from the raw snapshot files; everything
    else must say ``incomplete``; and the check against the snapshot before a
    lone row must turn at least one cap artifact back into the truth."""
    from quam_state_manager.core import leaf_index as li
    real = li.numeric_leaves

    def walk_capped(state, wiring=None, **kw):
        kw.setdefault("cap", 14)
        return real(state, wiring, **kw)

    monkeypatch.setattr(li, "numeric_leaves", walk_capped)
    monkeypatch.setattr(li, "SNAP_ROW_CAP", 4)
    cm = [[0.9, 0.1], [0.2, 0.8]]
    _snap(env, _state(2e-5, 5e9, cm, 1))
    _snap(env, _state(2e-5, 5e9, cm, 2))
    _snap(env, _state(2.2e-5, 5e9, cm, 3, t2e=3e-5), trigger="experiment",
          experiment_name="25_T1", run_id=77)
    _snap(env, _state(2.2e-5, 5.001e9, [[0.91, 0.09], [0.2, 0.8]], 4, t2e=3e-5))
    _take_live(env)
    d = _meta(env)
    # S10 C3: capped snapshot index -> complete ledger, the legacy caps cannot truncate its answer.
    assert d["mode"] == "ledger" and not d.get("incomplete_index")
    states = _history_states(env)
    assert d["oldest"] == _instant(states[0][0])
    dots = {
        ("q", "T1", "q1"): ["qubits.q1.T1"], ("q", "T1", "q2"): ["qubits.q2.T1"],
        ("q", "f_01", "q1"): ["qubits.q1.f_01"], ("q", "f_01", "q2"): ["qubits.q2.f_01"],
        ("q", "T2echo", "q1"): ["qubits.q1.T2echo"],
        ("q", "assignment_fidelity", "q1"):
            [f"qubits.q1.resonator.confusion_matrix.{i}.{j}" for i in (0, 1) for j in (0, 1)],
        ("p", "2q:StandardRB:cz_SNZ", "q1-2"):
            ["qubit_pairs.q1-2.macros.cz_SNZ.fidelity.StandardRB.average_gate_fidelity"],
    }
    raw = env["hm"].leaf_field_series_many(str(env["live"]), [x for v in dots.values() for x in v])
    dated = incomplete = firsts = rescued = 0
    for (g, key, ent), dl in dots.items():
        got = d[g][key][ent]
        assert not got.get("incomplete"), got
        dated += 1
        exps = [_oracle(states, dt) for dt in dl]
        stamp = max(x[0] for x in exps)
        assert got["ts"] == _instant(stamp) and got["eid"] == env["event_ids"][stamp], (key, ent, got, exps)
        assert got["first"] is all(x[1] for x in exps), (key, ent, got, exps)
        firsts += got["first"]
        # dated although the index alone could not vouch for it: a leaf with
        # a lone row later than the oldest snapshot, settled by the file
        rescued += any(len(raw.get(dt) or []) == 1 and raw[dt][0][0] != d["oldest"] for dt in dl)
    # both outcomes happened (a vacuous pass would see only one), and the
    # snapshot check dated something the index alone could not
    assert dated == len(dots) and firsts and rescued, (dated, firsts, rescued)


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
