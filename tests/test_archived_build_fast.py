"""v1.3.0 (perf, A2): an archived chip's first view builds its ledger fast,
and builds exactly the ledger it built before.

Profiled on a copy of a real archived chip (576 runs, 647 Param History
snapshots, ~1.6 MB states): 2,378 flattens were 24 s of a 53 s build -- each
long array's canonical JSON + SHA-1 recomputed on every state, though one save
repeats the last one's arrays -- and 555 of the 647 snapshots were a run's own
save seen by SM, each settled only after three flattens and two state rebuilds.
Pins:

* ``hub_rules.flatten`` memoizes a long array's hash by its values, and every
  hash it gives is the one the v1.2.0 algorithm gives (ints vs integral floats,
  -0.0, NaN, infinities, big ints, strings, None; a list holding a bool is
  never memoized: True == 1, but "true" != "1"); a repeated array is encoded
  once;
* a snapshot equal to its successor's content is settled ``same_after`` by the
  content hash, with no flat built -- and only where the flat path would have
  said the same: a successor whose own rows are empty (a type-only save) still
  takes the flat path and answers ``same_before``;
* a whole build with the shortcut and one without it write the same ledger.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from quam_state_manager.core import hub_rules, hub_sync
from quam_state_manager.core.hub_store import HubStore
from tests.test_hub_versions import T0, WIRING, chip_state, run as hub_run


# ---------------------------------------------------------------------------
# 1. the flatten memo is exact
# ---------------------------------------------------------------------------

def _reference_flatten(doc):
    """The v1.2.0 flatten, verbatim in behaviour (no memo)."""
    out = {}

    def walk(value, parts):
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, parts + (hub_rules._segment(key),))
        elif isinstance(value, list):
            if len(value) > 16 and all(not isinstance(v, (dict, list)) for v in value):
                payload = hub_rules._json([hub_rules._hash_scalar(v) for v in value]).encode("utf-8")
                out[".".join(parts)] = {"_array": len(value), "_hash": hashlib.sha1(payload).hexdigest()}
            else:
                for index, child in enumerate(value):
                    walk(child, parts + (str(index),))
        else:
            out[".".join(parts)] = value

    walk(doc, ())
    return out


ARRAYS = {
    "floats": [0.1 * i for i in range(40)],
    "integral_floats": [float(i) for i in range(20)],
    "ints": list(range(20)),
    "bools": [True] * 10 + [False] * 10,
    "ones_and_zeros": [1] * 10 + [0] * 10,
    "neg_zero": [-0.0] * 20,
    "pos_zero": [0.0] * 20,
    "int_zero": [0] * 20,
    "nan": [float("nan")] * 20,
    "inf": [float("inf"), float("-inf")] * 10,
    "strings": ["a"] * 20,
    "none": [None] * 20,
    "big_ints": [2 ** 60 + i for i in range(20)],
    "big_float": [float(2 ** 60)] * 20,
    "big_int": [2 ** 60] * 20,
    "mixed_with_bool": [1, "1", 1.0, None, True] * 4,
    "mixed": [1, "1", 1.5, None, -0.0] * 4,
    "short": [0.1 * i for i in range(16)],
    "nested": [[0.0] * 17, [1.0] * 17],
}


def test_every_memoized_hash_is_the_v120_hash(monkeypatch):
    monkeypatch.setattr(hub_rules, "_ARRAY_MEMO", hub_rules.OrderedDict())
    monkeypatch.setattr(hub_rules, "_array_memo_elements", 0)
    want = {k: _reference_flatten({k: v}) for k, v in ARRAYS.items()}
    for _round in range(2):                      # cold, then served by the memo
        for k, v in ARRAYS.items():
            got = hub_rules.flatten({k: v})
            assert json.dumps(got, sort_keys=True) == json.dumps(want[k], sort_keys=True), k
    # equal values of another type share the hash exactly when v1.2.0's does
    order = ["ints", "integral_floats", "ones_and_zeros", "bools", "int_zero", "neg_zero", "pos_zero",
             "big_int", "big_float"]
    for a in order:
        for b in order:
            ha, hb = (hub_rules.flatten({"x": ARRAYS[n]})["x"]["_hash"] for n in (a, b))
            ra, rb = (_reference_flatten({"x": ARRAYS[n]})["x"]["_hash"] for n in (a, b))
            assert (ha == hb) == (ra == rb), (a, b)


def test_a_bool_list_never_takes_an_equal_int_lists_hash(monkeypatch):
    monkeypatch.setattr(hub_rules, "_ARRAY_MEMO", hub_rules.OrderedDict())
    monkeypatch.setattr(hub_rules, "_array_memo_elements", 0)
    ints = hub_rules.flatten({"x": [1] * 17 + [0] * 3})["x"]["_hash"]
    bools = hub_rules.flatten({"x": [True] * 17 + [False] * 3})["x"]["_hash"]
    assert ints != bools
    assert bools == _reference_flatten({"x": [True] * 17 + [False] * 3})["x"]["_hash"]


def test_a_repeated_array_is_encoded_once(monkeypatch):
    monkeypatch.setattr(hub_rules, "_ARRAY_MEMO", hub_rules.OrderedDict())
    monkeypatch.setattr(hub_rules, "_array_memo_elements", 0)
    calls = []
    real = hub_rules._json
    monkeypatch.setattr(hub_rules, "_json", lambda v: calls.append(1) or real(v))
    doc = {"q": {"w": [0.25 * i for i in range(500)], "v": [1.5] * 30}}
    a = hub_rules.flatten(doc)
    n = len(calls)
    b = hub_rules.flatten(json.loads(json.dumps(doc)))       # a fresh parse of the same save
    assert a == b and n == 2 and len(calls) == n, (n, len(calls))


def test_the_memo_is_bounded(monkeypatch):
    monkeypatch.setattr(hub_rules, "_ARRAY_MEMO", hub_rules.OrderedDict())
    monkeypatch.setattr(hub_rules, "_array_memo_elements", 0)
    monkeypatch.setattr(hub_rules, "_ARRAY_MEMO_MAX", 5)
    monkeypatch.setattr(hub_rules, "_ARRAY_MEMO_MAX_ELEMENTS", 100)
    for i in range(20):
        hub_rules.flatten({"x": [float(i)] * 30})
    assert len(hub_rules._ARRAY_MEMO) <= 3 and hub_rules._array_memo_elements <= 100
    assert hub_rules._array_memo_elements == sum(len(k) for k in hub_rules._ARRAY_MEMO)


# ---------------------------------------------------------------------------
# 2. a run's save seen by SM is settled by its content hash
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _own_syncs():
    old = dict(hub_sync._SYNCS)
    hub_sync._SYNCS.clear()
    yield
    hub_sync._SYNCS.clear()
    hub_sync._SYNCS.update(old)


def _ledger(tmp_path, states, name="ledger") -> Path:
    data = tmp_path / f"data_{name}"
    for i, s in enumerate(states):
        hub_run(data, i + 1, s)
    chip_dir = tmp_path / name
    chip_dir.mkdir()
    out = hub_sync.catch_up(chip_dir, [str(data)])
    assert out["state"] == "ready", out
    return chip_dir


def _snapshot(tmp_path, ts: str, state, t_us: int) -> dict:
    d = tmp_path / "snaps" / ts
    d.mkdir(parents=True)
    # re-serialised (indented): never the run's own bytes, as SM's captures are not
    (d / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    (d / "wiring.json").write_text(json.dumps(WIRING, indent=2), encoding="utf-8")
    return {"ts": ts, "t_us": t_us, "trigger": "auto", "dir": str(d)}


def _count_flats(monkeypatch):
    n = {"flatten": 0, "flat_of": 0}
    real_flatten, real_flat_of = hub_rules.flatten, HubStore.flat_of

    def flatten(doc):
        n["flatten"] += 1
        return real_flatten(doc)

    def flat_of(self, row):
        n["flat_of"] += 1
        return real_flat_of(self, row)
    monkeypatch.setattr(hub_rules, "flatten", flatten)
    monkeypatch.setattr(HubStore, "flat_of", flat_of)
    return n


def test_a_runs_save_seen_early_is_settled_with_no_flat(tmp_path, monkeypatch):
    states = [chip_state(t1=1e-5), chip_state(t1=2e-5), chip_state(t1=3e-5)]
    chip_dir = _ledger(tmp_path, states)
    # SM captured run #2's save one second before the run's own instant
    snap = _snapshot(tmp_path, "20260101_120019_000001", states[1], T0 + 2 * 10_000_000 - 1_000_000)
    n = _count_flats(monkeypatch)
    with HubStore(chip_dir) as store:
        assert hub_sync.attach_observed(store, snap) == "same_after"
    assert n == {"flatten": 0, "flat_of": 0}, n


def test_a_save_equal_to_both_neighbours_still_answers_same_before(tmp_path, monkeypatch):
    # run #2 is a type-only save (n_avg 1 -> 1.0): its rows are empty, its
    # content hash is its own; a capture of it is ALSO run #1's state by the
    # S2 rules -- the flat path says same_before, and so must the build
    states = [chip_state(n_avg=1), chip_state(n_avg=1.0), chip_state(n_avg=1.0, t1=4e-5)]
    chip_dir = _ledger(tmp_path, states)
    with HubStore(chip_dir) as store:
        two = store.conn.execute("SELECT n_changes, chash FROM events WHERE run_id=2").fetchone()
        one = store.conn.execute("SELECT chash FROM events WHERE run_id=1").fetchone()
    assert two[0] == 0 and two[1] != one[0]
    snap = _snapshot(tmp_path, "20260101_120019_000001", states[1], T0 + 2 * 10_000_000 - 1_000_000)
    with HubStore(chip_dir) as store:
        assert hub_sync.attach_observed(store, snap) == "same_before"


# ---------------------------------------------------------------------------
# 3. the same ledger either way
# ---------------------------------------------------------------------------

def _fingerprint(chip_dir: Path) -> dict:
    c = sqlite3.connect(chip_dir / "ledger.sqlite")
    c.row_factory = sqlite3.Row
    keep = [r[1] for r in c.execute("PRAGMA table_info(events)") if r[1] not in ("eid", "ord")]
    ident = {}
    events = []
    for r in c.execute("SELECT * FROM events ORDER BY ord"):
        ident[r["eid"]] = (r["rel_path"], r["run_id"], r["kind"])
        events.append({k: r[k] for k in keep if k not in ("state_ref", "rel_path")}
                      | {"rel": (r["rel_path"] or "").rsplit("/", 1)[-1]})
    changes = sorted(json.dumps({**dict(r), "eid": ident[r["eid"]], "pid": None}, sort_keys=True,
                                default=str)
                     for r in c.execute("SELECT c.*, p.path FROM changes c JOIN paths p USING(pid)"))
    observed = sorted((r[0], r[1], ident.get(r[2])) for r in
                      c.execute("SELECT ts, outcome, eid FROM observed_snapshots"))
    c.close()
    return {"events": events, "changes": changes, "observed": observed}


def _build(tmp_path, name, states, snaps_spec, monkeypatch, *, shortcut: bool) -> dict:
    with monkeypatch.context() as m:
        if not shortcut:
            m.setattr(hub_sync, "_same_after_by_hash", lambda *a: False)
        chip_dir = _ledger(tmp_path, states, name=name)
        outcomes = []
        with HubStore(chip_dir) as store:
            for ts, state, t_us in snaps_spec:
                snap = _snapshot(tmp_path / f"s_{name}", ts, state, t_us)
                outcomes.append(hub_sync.attach_observed(store, snap))
        fp = _fingerprint(chip_dir)
    fp["outcomes"] = outcomes
    return fp


def test_a_build_with_and_without_the_shortcut_writes_the_same_ledger(tmp_path, monkeypatch):
    s = [chip_state(t1=1e-5), chip_state(t1=2e-5), chip_state(t1=2e-5, n_avg=1.0),
         chip_state(t1=3e-5, n_avg=1.0), chip_state(t1=3e-5, n_avg=1.0, f01=5.2e9)]
    at = [T0 + (i + 1) * 10_000_000 for i in range(len(s))]
    spec = [("20260101_115959_000001", s[0], at[0] - 1_000_000),     # before #1, = #1
            ("20260101_120019_000001", s[1], at[1] - 1_000_000),     # = #2 (its save, early)
            ("20260101_120029_000001", s[2], at[2] - 1_000_000),     # = #3 = #2 by the rules
            ("20260101_120031_000001", chip_state(t1=7e-5), at[2] + 1_000_000),  # an outside edit
            ("20260101_120039_000001", s[3], at[3] - 1_000_000),     # = #4, after the edit
            ("20260101_120049_000001", s[4], at[4] - 1_000_000),     # = #5
            ("20260101_120051_000001", s[4], at[4] + 1_000_000)]     # after the head, = #5
    fast = _build(tmp_path, "fast", s, spec, monkeypatch, shortcut=True)
    slow = _build(tmp_path, "slow", s, spec, monkeypatch, shortcut=False)
    assert fast["outcomes"] == slow["outcomes"]
    assert "same_after" in fast["outcomes"] and "same_before" in fast["outcomes"]
    assert fast == slow


def test_the_shortcut_decides_only_against_the_successors_own_base():
    """Unit: each condition of ``_same_after_by_hash`` refuses on its own."""
    pred = {"eid": 1, "state_hash": "p", "kind": "run"}
    succ = {"eid": 2, "chash": "c", "kind": "run", "error": None, "n_changes": 3, "base_hash": "p"}
    ok = dict(lpred=pred, lsucc=succ, pred=pred, succ=succ, chash="c")
    assert hub_sync._same_after_by_hash(**ok) is True
    for change in ({"chash": "other"}, {"lsucc": None}, {"lpred": {**pred, "eid": 9}},
                   {"lsucc": {**succ, "eid": 9}}, {"succ": {**succ, "kind": "sm_apply"}},
                   {"succ": {**succ, "n_changes": 0}}, {"succ": {**succ, "error": "x"}},
                   {"succ": {**succ, "base_hash": "q"}}, {"chash": None}):
        kw = {**ok, **change}
        if "succ" in change:
            kw["lsucc"] = kw["succ"]
        assert hub_sync._same_after_by_hash(**kw) is False, change
    # no predecessor at all: the successor's rows were measured against nothing
    first = {**succ, "base_hash": None}
    assert hub_sync._same_after_by_hash(None, first, None, first, "c") is True
    assert hub_sync._same_after_by_hash(None, succ, None, succ, "c") is False


def _reference_diff(before_flat, after_flat):
    """The v1.2.0 diff (every path sorted, ``same`` on every pair)."""
    rows = []
    for path in sorted(before_flat.keys() | after_flat.keys()):
        old_present, new_present = path in before_flat, path in after_flat
        old, new = before_flat.get(path), after_flat.get(path)
        if old_present and new_present and hub_rules.same(old, new):
            continue
        if not old_present:
            op = "add"
        elif not new_present:
            op = "gone"
        elif hub_rules._pointer(old) and hub_rules._pointer(new):
            op = "retarget"
        else:
            op = "set"
        old_num, old_txt = hub_rules._encoded(old) if old_present else (None, None)
        num, txt = hub_rules._encoded(new) if new_present else (None, None)
        rows.append(hub_rules.Change(path, op, old_num, old_txt, num, txt))
    return rows


def test_the_faster_diff_gives_the_v120_rows_in_the_v120_order():
    import random
    vals = [0, 1, 1.0, -0.0, 0.0, True, False, None, "a", "#/x", "#./y", float("nan"), float("inf"),
            2.5, {"_array": 20, "_hash": "h1"}, {"_array": 20, "_hash": "h2"}, {"a": True}, {"a": 1},
            [1, 2], [1.0, 2.0], [True, 2]]
    rng = random.Random(7)

    def rows(rs):
        return [(r.path, r.op, repr(r.old_num), r.old_txt, repr(r.num), r.txt) for r in rs]
    for _ in range(3000):
        keys = [f"p{i}" for i in range(rng.randint(0, 12))]
        a = {k: rng.choice(vals) for k in keys if rng.random() < 0.8}
        b = {k: (a[k] if k in a and rng.random() < 0.5 else rng.choice(vals))
             for k in keys if rng.random() < 0.8}
        assert rows(hub_rules.diff(a, b)) == rows(_reference_diff(a, b)), (a, b)
