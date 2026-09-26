"""RAM P8 -- Param History and the value-history drawer served from RAM.

Every cache here is validated on read (ram_design.md §1.1): the pins below
are that a cached answer never differs from a cold recompute, over a
randomized sequence of the events that move the underlying store --
captures, a foreign SQLite commit, a snapshot rewritten on disk -- plus the
equivalence of each rewritten computation with the one it replaced.
"""
from __future__ import annotations

import json
import os
import random
import sqlite3
import time
from pathlib import Path

import pytest

from quam_state_manager.core import history as H
from quam_state_manager.core import leaf_index as LI
from quam_state_manager.core import param_history_ram as PHR
from quam_state_manager.core.differ import Differ
from quam_state_manager.core.loader import QuamStore, flatten, merge_state_wiring
from quam_state_manager.web import routes as R
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"},
           "ports": {"mw_outputs": {"con1": {"1": {"2": {"band": 1}}}}}}
_QS = ("q1", "q2", "q10")


def _state(rng: random.Random | None = None, base: dict | None = None) -> dict:
    if base is None:
        base = {"qubits": {q: {"id": q, "T1": 1.0e-5, "f_01": 5.0e9,
                               "xy": {"operations": {
                                   "x180_Drag": {"amplitude": 0.1, "length": 40},
                                   "x180": "#./x180_Drag"}}}
                           for q in _QS},
                "qubit_pairs": {"q1-2": {"cz": {"phase": 0.1}}},
                "active_qubit_names": list(_QS)}
    s = json.loads(json.dumps(base))
    if rng is not None:
        for _ in range(rng.randint(1, 3)):
            q = rng.choice(_QS)
            which = rng.randrange(4)
            if which == 0:
                s["qubits"][q]["T1"] = rng.uniform(1e-6, 1e-4)
            elif which == 1:
                s["qubits"][q]["xy"]["operations"]["x180_Drag"]["amplitude"] = rng.uniform(0, 1)
            elif which == 2:
                s["qubit_pairs"]["q1-2"]["cz"]["phase"] = rng.uniform(-3, 3)
            else:
                s["qubits"][q]["f_01"] = rng.uniform(4e9, 6e9)
    return s


def _write(folder: Path, state: dict, wiring: dict | None = None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring or _WIRING), encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chip"
    _write(live, _state())
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    c.post("/load", data={"folder": str(live)})
    yield {"app": app, "client": c, "live": live, "hm": app.config["history_manager"]}
    PHR.close_all()


def _snap(env, state, **kw):
    _write(env["live"], state)
    time.sleep(0.003)                         # distinct ms timestamps
    m = env["hm"].check_and_snapshot(str(env["live"]), kw.pop("trigger", "manual"),
                                     force=True, **kw)
    assert m is not None
    return m


# ── changes_by_snapshot: same rows as the GROUP BY it replaced ──────────────

def _old_snaps(conn, *, limit_snaps, prefix=None, before_ts=None, at_ts=None):
    """The pre-P8 query, verbatim in shape: join + GROUP BY + LIMIT."""
    where = [f"l.kind IN ({LI.KIND_NUM}, {LI.KIND_PTR_NUM})"]
    params: list = []
    if prefix:
        where.append("p.path LIKE ? ESCAPE '\\'")
        params.append(prefix.replace("%", r"\%").replace("_", r"\_") + "%")
    if at_ts:
        where.append("s.ts = ?")
        params.append(at_ts)
    elif before_ts:
        where.append("s.ts < ?")
        params.append(before_ts)
    return [(r[1], r[6]) for r in conn.execute(
        "SELECT s.id, s.ts, s.trigger, s.run_id, s.experiment, s.folder, COUNT(*) AS n "
        "  FROM leaf_cp l JOIN leaf_paths p ON p.id = l.path_id "
        "  JOIN leaf_snaps s ON s.id = l.snap_id "
        f" WHERE {' AND '.join(where)} GROUP BY s.id ORDER BY s.id DESC LIMIT ?",
        params + [int(limit_snaps)]).fetchall()]


def test_changes_by_snapshot_matches_the_group_by_it_replaced(env):
    rng = random.Random(8)
    base = _state()
    metas = [_snap(env, base)]
    for _ in range(14):
        base = _state(rng, base)
        metas.append(_snap(env, base))
    hm, live = env["hm"], env["live"]
    hm._ensure_leaf_index_fresh(live)
    conn = hm._open_index(live)
    try:
        tss = [m.timestamp for m in metas]
        cases = 0
        for prefix in (None, "qubits.q1.", "qubits.q10", "qubit_pairs", "qubits.q2.T1", "nope"):
            for limit in (1, 3, 21):
                for before in (None, rng.choice(tss), tss[-1]):
                    new = [(g["timestamp"], g["total"]) for g in LI.changes_by_snapshot(
                        conn, limit_snaps=limit, prefix=prefix, before_ts=before)]
                    assert new == _old_snaps(conn, limit_snaps=limit, prefix=prefix,
                                             before_ts=before), (prefix, limit, before)
                    cases += 1
            at = rng.choice(tss)
            new = [(g["timestamp"], g["total"]) for g in LI.changes_by_snapshot(
                conn, limit_snaps=1, prefix=prefix, at_ts=at)]
            assert new == _old_snaps(conn, limit_snaps=1, prefix=prefix, at_ts=at)
        # the fixture must reach a snapshot with NO matching row for a prefix,
        # or the "skip it" branch is never exercised
        assert any(n == 0 for n in (
            len(_old_snaps(conn, limit_snaps=99, prefix="qubits.q2.T1", at_ts=t)) for t in tss))
        assert cases == 54
    finally:
        conn.close()


# ── /param-history/changes: memo never differs from a cold compute ──────────

def _feed(env, qs=""):
    r = env["client"].get("/param-history/changes" + qs, headers={"HX-Request": "true"})
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _cold_feed(env, qs=""):
    R._PH_CHANGES_MEMO.clear()
    return _feed(env, qs)


def test_changes_memo_equals_cold_over_random_events(env):
    rng = random.Random(20260926)
    base = _state()
    _snap(env, base)
    hm, live = env["hm"], env["live"]
    hits_seen = 0
    for step in range(40):
        ev = rng.randrange(4)
        if ev == 0:                                     # a capture
            base = _state(rng, base)
            _snap(env, base)
        elif ev == 1:                                   # a foreign commit (2nd process)
            hm._ensure_leaf_index_fresh(live)
            idx = hm._history_dir(live) / "index.sqlite"
            with sqlite3.connect(str(idx)) as c2:
                c2.execute("UPDATE leaf_cp SET value = value + 1 WHERE snap_id = "
                           "(SELECT MAX(snap_id) FROM leaf_cp)")
        qs = rng.choice(["", "?prefix=qubits.q1", "?prefix=qubit_pairs"])
        n0 = R._PH_CHANGES_MEMO.computes
        warm = _feed(env, qs)
        if R._PH_CHANGES_MEMO.computes == n0:
            hits_seen += 1
        assert warm == _cold_feed(env, qs), (step, ev, qs)
    assert hits_seen >= 5, "the memo never served a hit -- the pin proves nothing"


def test_changes_repeat_is_served_from_the_memo(env):
    _snap(env, _state())
    _snap(env, _state(random.Random(1), _state()))
    _feed(env)
    n = R._PH_CHANGES_MEMO.computes
    _feed(env)
    assert R._PH_CHANGES_MEMO.computes == n


def test_hist_token_moves_on_a_foreign_commit(env):
    _snap(env, _state())
    hm, live = env["hm"], env["live"]
    hm._ensure_leaf_index_fresh(live)
    t0 = PHR.hist_token(hm, live)
    assert PHR.hist_token(hm, live) == t0
    with sqlite3.connect(str(hm._history_dir(live) / "index.sqlite")) as c2:
        c2.execute("UPDATE leaf_cp SET value = value + 1 WHERE snap_id = "
                   "(SELECT MIN(snap_id) FROM leaf_cp)")
    assert PHR.hist_token(hm, live) != t0


# ── the drawer's snapshot-scan tier ─────────────────────────────────────────

def test_scan_series_memo_revalidates_a_rewritten_snapshot(env):
    rng = random.Random(3)
    base = _state()
    metas = [_snap(env, base)]
    for _ in range(4):
        base = _state(rng, base)
        metas.append(_snap(env, base))
    hm, live = env["hm"], env["live"]
    snaps = hm.list_snapshots(live)

    def series(dp):
        return hm._scan_field_series(live, snaps, dp, 1000)[0]

    first = series("qubits.q1.T1")
    assert series("qubits.q1.T1") == first
    # rewrite one snapshot on disk: its entry must be re-read
    victim = hm._history_dir(live) / metas[2].timestamp / "state.json"
    st = json.loads(victim.read_text(encoding="utf-8"))
    st["qubits"]["q1"]["T1"] = 123.0
    victim.write_text(json.dumps(st) + " ", encoding="utf-8")   # size moves too
    again = series("qubits.q1.T1")
    assert [v for ts, v, *_ in again if ts == metas[2].timestamp] == [123.0]
    # a container value handed out is a copy: mutating it cannot poison the memo
    sub = series("qubits.q2.xy.operations.x180_Drag")
    sub[-1][1]["amplitude"] = "POISON"
    assert series("qubits.q2.xy.operations.x180_Drag")[-1][1]["amplitude"] != "POISON"


def test_alias_path_reads_the_leaf_it_names(env):
    base = _state()
    _snap(env, base)
    for amp in (0.123457, 0.654321, 0.271828):
        base["qubits"]["q1"]["xy"]["operations"]["x180_Drag"]["amplitude"] = amp
        _snap(env, base)
    html = env["client"].get(
        "/field/history?path=qubits.q1.xy.operations.x180.amplitude").get_data(as_text=True)
    # the alias names x180_Drag: its history is the drawer's history
    assert "0.654321" in html and "0.123457" in html


# ── capture: diff_summary counted, never changed ────────────────────────────

def _reference_summary(prior_dir: Path, snap_dir: Path) -> dict:
    return Differ.summary(Differ().diff(prior_dir, snap_dir))


def test_capture_summary_equals_the_full_diff_over_random_captures(env):
    rng = random.Random(11)
    base = _state()
    _snap(env, base)
    hm, live = env["hm"], env["live"]
    hd = hm._history_dir(live)
    checked = 0
    for step in range(12):
        base = _state(rng, base)
        if step % 4 == 1:                      # added + removed leaves
            base["qubits"]["q2"].pop("f_01", None)
            base["qubits"]["q1"][f"extra_{step}"] = step
        if step == 6:                          # rewrite the prior snapshot on disk
            prior = hm.list_snapshots(live)[0]
            p = hd / prior.timestamp / "state.json"
            st = json.loads(p.read_text(encoding="utf-8"))
            st["qubits"]["q10"]["T1"] = 9.0
            p.write_text(json.dumps(st) + "  ", encoding="utf-8")
        m = _snap(env, base)
        snaps = hm.list_snapshots(live)
        assert snaps[0].timestamp == m.timestamp
        ref = _reference_summary(hd / snaps[1].timestamp, hd / m.timestamp)
        assert m.diff_summary == ref, step
        checked += 1
    assert checked == 12


def test_capture_summary_uses_quamstore_merge_on_a_colliding_key(tmp_path):
    """A wiring.json that carries ``qubits`` at top level deep-merges into
    state (QuamStore._merge) -- the in-memory side must match the disk side."""
    wiring = dict(_WIRING, qubits={"q1": {"xy": {"opx_output": "#/ports/x"}}})
    live = tmp_path / "chip"
    _write(live, _state(), wiring)
    hm = H.HistoryManager(tmp_path / "_inst")
    a = hm.check_and_snapshot(str(live), "manual", force=True)
    s2 = _state()
    s2["qubits"]["q1"]["T1"] = 2e-5
    _write(live, s2, dict(_WIRING, qubits={"q1": {"xy": {"opx_output": "#/ports/y"}}}))
    time.sleep(0.003)
    b = hm.check_and_snapshot(str(live), "manual", force=True)
    hd = hm._history_dir(live)
    assert b.diff_summary == _reference_summary(hd / a.timestamp, hd / b.timestamp)
    assert b.diff_summary["modified"] == 2
    st = json.loads((live / "state.json").read_text())
    wi = json.loads((live / "wiring.json").read_text())
    assert flatten(merge_state_wiring(st, wi)) == flatten(QuamStore(live).merged)


def test_differ_diff_order_and_content_unchanged(monkeypatch):
    """diff() lost its three per-set pre-sorts; its output must be the same
    list the old code produced (reference re-implemented here)."""
    from quam_state_manager.core.differ import _values_equal, _DEFAULT_IGNORE, _leaf_key
    from quam_state_manager.core.loader import natural_key
    rng = random.Random(2)
    for _ in range(30):
        a = {f"q{rng.randrange(12)}.p{rng.randrange(12)}": rng.choice([1, 1.0, 2.5, "x", None, True])
             for _ in range(60)}
        for i in range(4):
            a[f"q{i}.__class__"] = "C"                 # ignored leaf, on the a side
        b = dict(a)
        for k in rng.sample(sorted(b), 15) + ["q0.__class__", "q1.__class__"]:
            b.pop(k, None)                             # removed ignored leaves too
        for _ in range(15):
            b[f"q{rng.randrange(12)}.p{rng.randrange(12)}.{rng.choice(['__class__', 'v'])}"] = rng.random()
        for k in rng.sample(sorted(b), 10):
            b[k] = rng.random()
        # equal by == but not by type: _values_equal says DIFFERENT
        a["t.int_float"], b["t.int_float"] = 1, 1.0
        a["t.bool_int"], b["t.bool_int"] = True, 1
        a["t.nan"], b["t.nan"] = float("nan"), float("nan")
        a["t.tol"], b["t.tol"] = 1.0, 1.0 + 1e-15          # within tolerance: equal
        a["q3.__class__"], b["q3.__class__"] = "A", "B"   # ignored even though it differs
        old = []
        for key in sorted(b.keys() - a.keys(), key=natural_key):
            if _leaf_key(key) not in _DEFAULT_IGNORE:
                old.append((key, None, b[key], "added"))
        for key in sorted(a.keys() - b.keys(), key=natural_key):
            if _leaf_key(key) not in _DEFAULT_IGNORE:
                old.append((key, a[key], None, "removed"))
        for key in sorted(a.keys() & b.keys(), key=natural_key):
            if _leaf_key(key) in _DEFAULT_IGNORE or _values_equal(a[key], b[key], 1e-12):
                continue
            old.append((key, a[key], b[key], "modified"))
        old.sort(key=lambda e: natural_key(e[0]))
        # compare through the flat-side seam directly
        got = Differ().diff(({}, {}), ({}, {}))
        assert got == []
        with monkeypatch.context() as mp:
            mp.setattr(Differ, "_flatten_side", staticmethod(lambda side: side))
            got = [(e.dot_path, e.old_value, e.new_value, e.change_type)
                   for e in Differ().diff(a, b)]
        assert got == old
        assert Differ.summary_between(a, b) == {
            "added": sum(1 for e in old if e[3] == "added"),
            "removed": sum(1 for e in old if e[3] == "removed"),
            "modified": sum(1 for e in old if e[3] == "modified"),
            "total": len(old)}


# ── disk stats: the scandir walk sums what the rglob walk summed ────────────

def test_dir_bytes_equals_the_rglob_sum(tmp_path):
    rng = random.Random(4)
    for i in range(20):
        d = tmp_path / f"s{i}" / ("sub" if i % 3 == 0 else "")
        d.mkdir(parents=True, exist_ok=True)
        (d / f"f{i}.json").write_bytes(os.urandom(rng.randrange(1, 5000)))
    (tmp_path / "top.sqlite").write_bytes(b"x" * 777)
    (tmp_path / "emptydir").mkdir()
    ref = sum(p.stat().st_size for p in tmp_path.rglob("*") if p.is_file())
    assert H._dir_bytes(tmp_path) == ref


def test_natural_first_equals_the_full_natural_sort():
    from quam_state_manager.core.loader import natural_key
    rng = random.Random(9)
    segs = ["qubits", "qubit_pairs", "Q", "q", "xy", "z", "operations", "x180", "x90",
            "amplitude", "T1", "f_01", "weights_imag", "extras", "a", "", "w"]
    fast = 0
    for trial in range(60):
        rows = []
        seen = set()
        for _ in range(rng.randrange(1, 900)):
            parts = []
            for _d in range(rng.randrange(1, 6)):
                s = rng.choice(segs)
                if rng.random() < 0.6:
                    s = s + str(rng.randrange(0, 1200 if rng.random() < 0.2 else 12))
                parts.append(s)
            p = ".".join(parts)
            if p in seen:
                continue
            seen.add(p)
            rows.append((p, rng.random(), len(rows)))
        n = rng.choice([1, 2, 5, 25, 25, 2000])
        want = sorted(rows, key=lambda r: natural_key(r[0]))[:n]
        assert LI.natural_first(list(rows), n) == want, trial
        fast += len(rows) > 4 * n
    assert fast >= 20, "the coarse-key branch was barely exercised"


def test_changes_filter_input_survives_the_swap(env):
    """The debounced keyup swaps the whole root: the box must be preserved
    by id (focus, caret and in-flight keys; checked in real Chrome by the
    w7 bench's ds group) and Clear must not rely on overwriting it."""
    _snap(env, _state())
    html = _feed(env, "?prefix=qubits")
    import re as _re
    inp = _re.search(r'<input[^>]*name="prefix"[^>]*>', html).group(0)
    assert 'id="ph-changes-prefix"' in inp and 'hx-preserve="true"' in inp
    clear = _re.search(r'<[^>]*>Clear</', html).group(0)
    assert "hx-get" not in clear and 'href="/param-history/changes"' in clear


def test_chip_histories_rows_equal_cold_over_random_events(tmp_path):
    """list_chip_histories reuses a chip's row while its index files and
    the dir's version are unchanged -- never a row a cold read would not
    give, whichever chip moves and however (capture, foreign commit)."""
    rng = random.Random(12)
    hm = H.HistoryManager(tmp_path / "_inst")
    chips = []
    for k in range(3):
        live = tmp_path / f"chip{k}"
        st = _state()
        st["extras"] = {"chip_name": f"chip{k}"}
        _write(live, st)
        hm.check_and_snapshot(str(live), "manual", force=True)
        chips.append((live, st))

    def cold():
        hm._chip_row_memo.clear()
        with hm._lock:
            hm._chip_histories_cache = None
        return hm.list_chip_histories()

    hits = 0
    for step in range(25):
        live, st = rng.choice(chips)
        ev = rng.randrange(3)
        if ev == 0:
            st = _state(rng, st)
            _write(live, st)
            time.sleep(0.003)
            hm.check_and_snapshot(str(live), "manual", force=True)
        elif ev == 1:                                   # another process commits
            idx = hm._history_dir(live) / "index.sqlite"
            with sqlite3.connect(str(idx)) as c2:
                c2.execute("PRAGMA journal_mode=WAL")
                c2.execute("DELETE FROM param_history WHERE timestamp = "
                           "(SELECT MAX(timestamp) FROM param_history)")
            with hm._lock:
                hm._chip_histories_cache = None        # the global slot is not the subject
        with hm._lock:
            hm._global_version += 1                     # what any capture does
        before = dict(hm._chip_row_memo)
        warm = hm.list_chip_histories()
        hits += sum(1 for k, v in hm._chip_row_memo.items() if before.get(k) is v)
        assert warm == cold(), (step, ev)
    assert hits >= 10, "the per-chip memo never served a row"


def test_persistent_connections_are_bounded_and_never_reuse_a_token(tmp_path):
    paths = []
    for k in range(PHR._CONNS_MAX + 3):
        p = tmp_path / f"i{k}.sqlite"
        with sqlite3.connect(str(p)) as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("CREATE TABLE t (x)")
        paths.append(p)
    first = PHR.data_version(paths[0])
    for p in paths[1:]:
        PHR.data_version(p)
    assert len(PHR._CONNS) <= PHR._CONNS_MAX          # evicted, handle closed
    assert str(paths[0]) not in PHR._CONNS
    with sqlite3.connect(str(paths[0])) as c:          # changed while nobody watched
        c.execute("INSERT INTO t VALUES (1)")
    assert PHR.data_version(paths[0]) != first         # a reopen is never an old token
    PHR.close_all()


# ── the parameter typeahead from RAM (PathRank) ─────────────────────────────

_ODD_PATHS = ["qubits.Ärger.T1", "qubits.ärger.T1", "straße.x", "STRASSE.x",
              "qubits.İd.f", "qubits.id.F", "q_1%x.amp", "q1x.amp", "Q10.T2",
              "q10.t2", "qubits.q2.xy.operations.x180_Drag.amplitude"]


def _rank_db(rng: random.Random) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    LI.ensure_schema(conn)
    paths = set(_ODD_PATHS)
    toks = ["qubits", "q1", "q2", "q10", "Q3", "xy", "operations", "x180",
            "amplitude", "T1", "t2", "f_01", "cz", "phase", "qubit_pairs",
            "q1-2", "weights", "0", "1", "12", "ß", "Ä", "%", "_"]
    while len(paths) < 400:
        paths.add(".".join(rng.choice(toks) for _ in range(rng.randint(1, 5))))
    for pid, p in enumerate(sorted(paths), 1):
        conn.execute("INSERT INTO leaf_paths (id, path) VALUES (?, ?)", (pid, p))
        for sid in rng.sample(range(1, 30), rng.choice([0, 1, 1, 1, 2, 3, 7])):
            conn.execute("INSERT INTO leaf_cp (path_id, snap_id, value) VALUES (?, ?, 0)",
                         (pid, sid))
    return conn


def test_path_rank_equals_search_paths_over_random_queries():
    """PathRank.search is the SQL typeahead's answer, character for character:
    the LIKE grammar (space AND, | OR), ASCII-only case folding (the Ä/ä,
    ß/SS, İ/i paths), literal % and _, the (-changes, natural) order and the
    limit."""
    rng = random.Random(20260926)
    conn = _rank_db(rng)
    rank = LI.PathRank.from_conn(conn)
    paths = [r[0] for r in conn.execute("SELECT path FROM leaf_paths")]
    fixed = ["q1", "Q1", "ä", "Ä", "ß", "SS", "i", "İ", "%", "_", "q_1%",
             "x180 amp", "q1 | q2", "T1|t2 qubits", "zzz", "", "  ", "|", "q |"]
    qs = list(fixed)
    for _ in range(300):
        p = rng.choice(paths)
        a = rng.randrange(len(p))
        t = p[a:a + rng.randint(1, 6)]
        if rng.random() < 0.3:
            t = t.upper()
        if rng.random() < 0.3:
            t = t + rng.choice([" ", " | "]) + rng.choice(paths)[:rng.randint(1, 4)]
        qs.append(t)
    for q in qs:
        for lim in (1, 5, 30, 1000):
            assert rank.search(q, lim) == LI.search_paths(conn, q, limit=lim), (q, lim)


def test_path_rank_reusing_an_earlier_natural_order_equals_cold():
    """A rebuild reuses the previous rank's natural order (a capture moves
    counts, not paths). Counts moved, a path added (the order must be
    recomputed, not guessed) and paths removed (a superset order stays
    exact): every answer still equals the SQL typeahead on the index NOW."""
    rng = random.Random(9)
    conn = _rank_db(rng)
    order0 = LI.PathRank.from_conn(conn).nat_order
    paths = [r[0] for r in conn.execute("SELECT path FROM leaf_paths")]
    reused = 0
    for step in range(12):
        ev = step % 3
        if ev == 0:        # counts move
            pid = rng.randrange(1, len(paths) + 1)
            conn.execute("INSERT OR IGNORE INTO leaf_cp (path_id, snap_id, value) "
                         "VALUES (?, ?, 1)", (pid, 100 + step))
        elif ev == 1:      # a new path, ranked BETWEEN existing ones
            conn.execute("INSERT INTO leaf_paths (path) VALUES (?)",
                         (f"q1.x{step}0.amplitude",))
        else:              # a path removed
            conn.execute("DELETE FROM leaf_paths WHERE id = ?",
                         (rng.randrange(1, len(paths) + 1),))
        rank = LI.PathRank.from_conn(conn, order0)
        if rank.nat_order is order0:
            reused += 1
        order0 = rank.nat_order            # carried forward, as production does
        for q in ("q1", "x", "amplitude", "q1 | x1", "T1", "a"):
            for lim in (1, 7, 1000):
                assert rank.search(q, lim) == LI.search_paths(conn, q, limit=lim), (step, q, lim)
    assert reused >= 4, "the earlier order was never reused -- the pin proves nothing"


def _param_search(env, q):
    r = env["client"].get("/param-history/param-search?q=" + q)
    assert r.status_code == 200
    return r.get_json()["results"]


def test_param_search_memo_equals_cold_over_random_events(env):
    """Captures and foreign commits (a second process adding a path and a
    change point) interleaved with typeahead reads: the memo's answer always
    equals the manager's own SQL on the index as it is NOW."""
    rng = random.Random(7)
    base = _state()
    _snap(env, base)
    hm, live = env["hm"], env["live"]
    memo = PHR._path_rank_memo()
    hits_seen = 0
    for step in range(40):
        ev = rng.randrange(4)
        if ev == 0:
            base = _state(rng, base)
            _snap(env, base)
        elif ev == 1:
            hm._ensure_leaf_index_fresh(live)
            idx = hm._history_dir(live) / "index.sqlite"
            with sqlite3.connect(str(idx)) as c2:
                c2.execute("INSERT OR IGNORE INTO leaf_paths (path) VALUES (?)",
                           (f"qubits.q1.foreign_{step}",))
                c2.execute("INSERT OR IGNORE INTO leaf_cp (path_id, snap_id, value) "
                           "SELECT id, (SELECT MAX(snap_id) FROM leaf_cp), 1 "
                           "FROM leaf_paths WHERE path = ?", (f"qubits.q1.foreign_{step}",))
        q = rng.choice(["q1", "T1", "amplitude", "foreign", "q1 | q2", "zzz", "Q1"])
        n0 = memo.computes
        warm = _param_search(env, q)
        if memo.computes == n0:
            hits_seen += 1
        assert warm == hm.leaf_search(live, q, limit=30), (step, ev, q)
    assert hits_seen >= 5, "the memo never served a hit -- the pin proves nothing"


def test_changes_page_warms_the_typeahead_rank(env):
    _snap(env, _state())
    memo = PHR._path_rank_memo()
    memo.clear()
    _feed(env)
    for _ in range(200):                      # the warm runs on a daemon thread
        if memo.slots():
            break
        time.sleep(0.01)
    n = memo.computes
    _param_search(env, "q1")
    assert memo.computes == n, "the first keystroke rebuilt the rank"


# ── distinct counts by index skip-scan ──────────────────────────────────────

def _ph_table(rng: random.Random, indexed: bool) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    pk = ", PRIMARY KEY (timestamp, qubit, property)" if indexed else ""
    conn.execute("CREATE TABLE param_history (timestamp TEXT NOT NULL, qubit TEXT NOT NULL,"
                 " property TEXT NOT NULL, value REAL, raw_pointer TEXT,"
                 " trigger TEXT NOT NULL, run_id INTEGER, experiment TEXT" + pk + ")")
    if indexed:
        conn.execute("CREATE INDEX idx_qubit_property_ts ON param_history (qubit, property, timestamp)")
        conn.execute("CREATE INDEX idx_trigger_ts ON param_history (trigger, timestamp)")
    qs = ["q1", "q10", "q2", "Q3", "qA-1", "é", "E"]
    trig = ["manual", "auto", "experiment", "save", "Manual"]
    seen = set()
    for _ in range(rng.randint(0, 400)):
        ts = f"2026{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}_{rng.randint(0, 999999):06d}"
        q, prop = rng.choice(qs), rng.choice(["T1", "f_01", "amp"])
        if (ts, q, prop) in seen:
            continue
        seen.add((ts, q, prop))
        conn.execute("INSERT INTO param_history VALUES (?,?,?,?,?,?,?,?)",
                     (ts, q, prop, 1.0, None, rng.choice(trig), None, None))
    return conn


def test_skip_scan_distincts_equal_the_sql_they_replace():
    rng = random.Random(4242)
    used_skip = 0
    for i in range(60):
        indexed = i % 3 != 0
        conn = _ph_table(rng, indexed)
        heads = H._param_history_index_heads(conn)
        used_skip += any(h[0] == "timestamp" for h in heads)
        assert H._ph_snap_count(conn) == conn.execute(
            "SELECT COUNT(DISTINCT timestamp) FROM param_history").fetchone()[0]
        assert H._ph_distinct(conn, "qubit") == [r[0] for r in conn.execute(
            "SELECT DISTINCT qubit FROM param_history ORDER BY qubit")]
        assert H._ph_trigger_counts(conn) == dict(conn.execute(
            "SELECT trigger, COUNT(DISTINCT timestamp) FROM param_history GROUP BY trigger"))
    assert used_skip >= 30, "the skip-scan branch never ran"


def test_skip_scan_never_runs_without_a_leading_index():
    conn = _ph_table(random.Random(1), indexed=False)
    assert not any(h[0] in ("timestamp", "qubit", "trigger")
                   for h in H._param_history_index_heads(conn))
    plans = []
    conn.set_trace_callback(plans.append)
    H._ph_snap_count(conn); H._ph_distinct(conn, "qubit"); H._ph_trigger_counts(conn)
    assert plans and not any("RECURSIVE" in s for s in plans)


def test_changes_typeahead_survives_the_filter_swap(env, tmp_path):
    """The typeahead's datalist across the filter's htmx swaps, under the real
    bundled htmx in jsdom, on the route's real output
    (tests/ph_typeahead_swap_fragcheck.cjs)."""
    import shutil
    import subprocess
    if shutil.which("node") is None:
        pytest.skip("node not available")
    _snap(env, _state())
    a, b = tmp_path / "frag_a.html", tmp_path / "frag_b.html"
    a.write_text(_feed(env), encoding="utf-8")
    b.write_text(_feed(env, "?prefix=qu"), encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(["node", str(root / "tests" / "ph_typeahead_swap_fragcheck.cjs"),
                           str(a), str(b)], capture_output=True, text=True,
                          cwd=str(root), timeout=120)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stdout or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert "ALL OK" in proc.stdout
