"""RAM P1a/P2 -- Chip Status Trends served from RAM (core/chip_trends_ram).

The contract these pin:

* a cached answer never differs from a cold recompute (a random event
  sequence against an independent cold oracle, under SM_RAM_VERIFY shadow
  mode, including writes from ANOTHER connection);
* the in-RAM family table is ``leaf_index.path_families`` row for row, order
  included, over a hostile query corpus;
* a warm request resolves no path and stats almost nothing; no request ever
  runs ``leaf_index.rebuild`` itself;
* a chart's signature is stable across toggles and moves with its data (what
  lets the client keep a drawn chart), and the client patch keeps it.
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import pytest

from quam_state_manager.core import chip_trends_ram as ctr
from quam_state_manager.core import leaf_index as li
from quam_state_manager.core import search_query as sq
from quam_state_manager.web.app import create_app
from tests.test_trends_all_entities import _charts, _chip, _versions

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolate():
    ctr.close_all()
    yield
    ctr.close_all()


@pytest.fixture
def client(tmp_path):
    _chip(tmp_path / "quam_state")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path / "quam_state")})
    return c


def _join_repairs(hm):
    with hm._leaf_rebuild_lock:
        workers = list(hm._leaf_rebuild_threads.values())
    for w in workers:
        w.join(20)


# ── the family table is the SQL, row for row ────────────────────────────────

_PATHS = [
    "qubits.q1.T1", "qubits.q2.T1", "qubits.q10.T1", "qubits.q1.xy.operations.x180.amplitude",
    "qubits.q2.xy.operations.x180.amplitude", "Qubits.Q3.T1", "qubit_pairs.q1-q2.coupler.interaction_offset",
    "qubit_pairs.q2-q10.coupler.interaction_offset", "qubit_pairs.q1-q2.macros.cz.fidelity.InterleavedRB",
    "qubit_pairs.q2-q10.macros.cz_snz.fidelity.InterleavedRB", "qubitXpairs.a.b", "qubits.q1.",
    "qubits..odd", "twpas.t1.pump_amp", "wiring.ports.con1.1.offset", "qubits.q1.100%_rate",
    "qubits.q2.under_score", "qubits.q1.back\\slash", "qubits.q1.Ämp", "qubits.q2.ämp",
    "qubits.q1.mutual_flux_bias.0", "qubits.q1.mutual_flux_bias.10", "qubits.q1.mutual_flux_bias.2",
    "top_level_scalar",
]
_QUERIES = ["", "t1", "T1", "q1", "q1 t1", "offset | fidelity", "interleaved", "qubit_pairs",
            "%", "_", "100%", "under_score", "back\\slash", "\\", "a\\", "ämp", "Ämp", "xy x180",
            "| t1", "t1 |", "zzz", "q1 | q2 amplitude", ".", "mutual_flux_bias"]


def _index(tmp_path, rng):
    conn = sqlite3.connect(str(tmp_path / "fam.sqlite"), isolation_level=None)
    li.ensure_schema(conn)
    for i in range(6):
        conn.execute("INSERT INTO leaf_snaps (id, ts) VALUES (?, ?)", (i, f"2026010{i}_000000"))
    for pid, p in enumerate(_PATHS):
        conn.execute("INSERT INTO leaf_paths (id, path) VALUES (?, ?)", (pid, p))
        for sid in range(rng.randint(0, 5)):
            conn.execute("INSERT INTO leaf_cp (path_id, snap_id, value, kind) VALUES (?,?,?,0)",
                         (pid, sid, float(sid)))
    return conn


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_the_family_table_is_the_sql_row_for_row(tmp_path, seed):
    conn = _index(tmp_path, random.Random(seed))
    rows = ctr._family_rows(conn)
    for roots in (("qubits", "qubit_pairs"), ("qubit_pairs",), ("qubits",)):
        ft = ctr.FamilyTable(rows, roots)
        for q in _QUERIES:
            for limit in (None, 3):
                want = li.path_families(conn, q, roots=roots, limit=limit)
                got = ft.query(q, limit=limit)
                assert got == want, (roots, q, limit)
                if q:                     # the pre-filtered table answers the same
                    ft2 = ctr.FamilyTable([r for r in rows if ctr._alower(sq.groups(q)[0][0]) in r[2]], roots) \
                        if ("|" not in q and len(q.split()) == 1 and "\\" not in q) else ft
                    assert ft2.query(q, limit=limit) == want, ("prefiltered", roots, q)
    conn.close()


def test_matching_paths_is_the_sql(tmp_path):
    conn = _index(tmp_path, random.Random(5))

    class _IC:
        def run(self, fn):
            return fn(conn)

    t = ctr.ChipTrendsTable(None, tmp_path, tmp_path, [], _IC())
    for pat in ("qubit_pairs.*.macros.*.fidelity.InterleavedRB", "qubits.*.T1", "qubits.*.*",
                "*.q1.T1", "qubits.q1.mutual_flux_bias.*", "nothing.*"):
        assert t.leaf_matching_paths(pat) == li.matching_paths(conn, pat), pat
    conn.close()


# ── route level ──────────────────────────────────────────────────────────────

def test_curated_from_the_table_is_the_direct_call(client, tmp_path):
    from quam_state_manager.web import routes
    _versions(client, tmp_path / "quam_state")
    hm = client.application.config["history_manager"]
    path = tmp_path / "quam_state"
    tbl = ctr.table(hm, path)
    for sel in (["T1"], ["f_01", "T1"], ["x180_amplitude"], ["T1", "f_01", "x180_amplitude"], ["nope"]):
        assert routes._trend_series_curated(hm, path, sel, tbl) == \
            routes._trend_series_curated(hm, path, sel), sel
    have = routes._trend_metrics_with_data(hm, path, list(routes.DEFAULT_TRACKED_PROPERTIES), tbl)
    assert {"T1", "f_01"} <= have


def test_a_downsampled_change_read_derived_from_the_full_one_is_the_cold_read(client, tmp_path):
    """RAM P1a: extract_property_history derives a downsampled change-point
    read from the cached un-downsampled one (same version, change-point fast
    path). The derived answer must be the cold read, LTTB included -- the
    downsample here is small enough that LTTB actually drops points."""
    _versions(client, tmp_path / "quam_state", n=6)
    hm = client.application.config["history_manager"]
    path = tmp_path / "quam_state"
    props = ["f_01", "T1"]
    full = hm.extract_property_history(path, props, downsample=None, compress="changes")
    assert full and max(len(b["values"]) for b in full) > 3, "the fixture must make LTTB bite"
    before = hm.extract_derived_count
    got = hm.extract_property_history(path, props, downsample=3, compress="changes")
    assert hm.extract_derived_count == before + 1, "the derive path was not taken"
    assert any(len(b["values"]) == 3 for b in got), "LTTB did not reduce anything"
    hm._extract_history_cache.clear()
    cold = hm.extract_property_history(path, props, downsample=3, compress="changes")
    assert hm.extract_derived_count == before + 1, "the cold read must not derive"
    assert got == cold
    # the derive never mutates the cached base it was derived from
    assert hm.extract_property_history(path, props, downsample=None, compress="changes") == full
    # a capture moves the version: the stale base is never derived from
    hm.extract_property_history(path, props, downsample=None, compress="changes")
    _versions(client, path, n=1)
    doc = json.loads((path / "state.json").read_text(encoding="utf-8"))
    for i, q in enumerate(doc["qubits"]):
        doc["qubits"][q]["f_01"] = 5.0e9 + i * 1e7
    (path / "state.json").write_text(json.dumps(doc), encoding="utf-8")
    assert client.post("/state/archive", data={"tag": "moved"}).status_code == 200
    _join_background(hm)
    after = hm.extract_property_history(path, props, downsample=3, compress="changes")
    hm._extract_history_cache.clear()
    assert after == hm.extract_property_history(path, props, downsample=3, compress="changes")
    n_derived = hm.extract_derived_count
    # a filtered (windowed-SQL) read is never derived
    hm.extract_property_history(path, props, downsample=None, compress="changes",
                                triggers=["manual"])
    hm.extract_property_history(path, props, downsample=3, compress="changes",
                                triggers=["manual"])
    assert hm.extract_derived_count == n_derived


def test_a_warm_request_resolves_nothing_and_barely_stats(client, tmp_path, monkeypatch):
    _versions(client, tmp_path / "quam_state", n=2)
    url = "/topology/trends?metrics=T1,f_01&paths=qubit_pairs.*.coupler.interaction_offset"
    assert client.get(url).status_code == 200
    assert client.get(url).status_code == 200          # a table and a fragment exist now
    n = {"resolve": 0, "stat": 0}
    real_resolve, real_stat = Path.resolve, os.stat

    def resolve(self, *a, **k):
        n["resolve"] += 1
        return real_resolve(self, *a, **k)

    def stat(*a, **k):
        n["stat"] += 1
        return real_stat(*a, **k)

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(os, "stat", stat)
    t0 = time.perf_counter()
    body = client.get(url).get_data(as_text=True)
    ms = (time.perf_counter() - t0) * 1000
    monkeypatch.undo()
    assert "coupler.interaction_offset" in body
    assert n["resolve"] == 0, n
    # S10 C1: the open now creates this folderless chip's ledger (no runs in it), so the
    # warm request reads it to learn that before drawing the table: 3 stats -> 6 (the
    # zone config, the ledger file's identity, the SM-write journal), each O(1)
    assert n["stat"] <= 6, n
    assert ms < 200, ms


def test_no_request_ever_rebuilds_the_leaf_index(client, tmp_path, monkeypatch):
    _versions(client, tmp_path / "quam_state", n=2)
    hm = client.application.config["history_manager"]
    path = tmp_path / "quam_state"
    conn = hm._open_index(path)
    li.mark_dirty(conn)
    conn.close()
    threads = []
    real = li.rebuild

    def spy(*a, **k):
        threads.append(threading.current_thread().name)
        return real(*a, **k)

    monkeypatch.setattr(li, "rebuild", spy)
    for url in ("/topology/trends", "/topology/trends?metrics=&paths=qubit_pairs.*.coupler.interaction_offset",
                "/topology/trends/paths?q=offset"):
        assert client.get(url).status_code == 200
    _join_repairs(hm)
    # S10 C3: snapshot repair -> ledger reads, the retired leaf index is never needed.
    assert threads == [], threads
    assert _charts(client.get("/topology/trends").get_data(as_text=True))


def test_a_chart_signature_is_stable_across_toggles_and_moves_with_data(client, tmp_path):
    _versions(client, tmp_path / "quam_state", n=2)

    def sig(url, metric):
        return next(c["sig"] for c in _charts(client.get(url).get_data(as_text=True))
                    if c["metric"] == metric)

    a = sig("/topology/trends?metrics=T1", "T1")
    assert a == sig("/topology/trends?metrics=f_01,T1", "T1")
    assert a == sig("/topology/trends?metrics=T1&paths=qubit_pairs.*.coupler.interaction_offset", "T1")
    sp = tmp_path / "quam_state" / "state.json"
    doc = json.loads(sp.read_text(encoding="utf-8"))
    doc["qubits"]["q1"]["T1"] = 9.9e-5
    sp.write_text(json.dumps(doc), encoding="utf-8")
    client.post("/state/archive", data={"tag": "moved"})
    assert sig("/topology/trends?metrics=T1", "T1") != a


def test_the_signature_covers_every_value_and_the_provenance_it_draws():
    """The client KEEPS a drawn box whose signature is unchanged, so a value
    that moved at the SAME snapshot ids (a re-ingest, another process's
    write) must move the signature too -- ids alone are not enough."""
    from quam_state_manager.web.routes import _trend_chart_sig
    chart = {"metric": "T1", "kind": "qubit", "label": "",
             "series": [{"entity": "q1", "points": [["20260101_000000", 1e-5], ["20260102_000000", 2e-5]]}]}
    snaps = {"20260101_000000": {"run": 1, "uid": None}, "20260102_000000": {"run": 2, "uid": None}}
    a = _trend_chart_sig(chart, snaps)
    assert a == _trend_chart_sig(dict(chart, sig="whatever"), snaps)
    moved = json.loads(json.dumps(chart))
    moved["series"][0]["points"][1][1] = 3e-5
    assert _trend_chart_sig(moved, snaps) != a, "a moved value must move the signature"
    other = json.loads(json.dumps(snaps))
    other["20260102_000000"]["uid"] = "k:2"
    assert _trend_chart_sig(chart, other) != a, "a new click target must move the signature"
    assert _trend_chart_sig(chart, dict(snaps, extra={"run": 9})) == a, "provenance of a snapshot the chart does not draw is not its business"


def test_another_connections_write_is_seen_on_the_next_request(client, tmp_path):
    """data_version: a commit by ANY other connection (a second SM process)
    moves the token, and HistoryManager's own version-keyed caches are bumped
    with it, so the curated tier cannot answer from its stale LRU."""
    _versions(client, tmp_path / "quam_state", n=2)
    hm = client.application.config["history_manager"]
    url = "/topology/trends?metrics=T1"
    before = _charts(client.get(url).get_data(as_text=True))
    last = before[0]["series"][0]["points"][-1]
    # S10 C3: foreign snapshot-index write -> foreign ledger write, preserving immediate freshness.
    idx = hm._history_dir(tmp_path / "quam_state") / "ledger.sqlite"
    other = sqlite3.connect(str(idx), isolation_level=None, timeout=10)
    path = "qubits." + before[0]["series"][0]["entity"] + ".T1"
    other.execute("UPDATE changes SET num = 4.2e-4 WHERE pid = "
                  "(SELECT pid FROM paths WHERE path=?) AND eid = "
                  "(SELECT MAX(eid) FROM changes WHERE pid=(SELECT pid FROM paths WHERE path=?))",
                  (path, path))
    other.close()
    after = _charts(client.get(url).get_data(as_text=True))
    assert after[0]["series"][0]["points"][-1][1] == pytest.approx(4.2e-4), after[0]["series"][0]["points"]


def _join_background(hm, timeout=30.0):
    """Every capture index thread and every index-listener run, to quiet."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        hm._join_deferred_index()
        live = [t for t in threading.enumerate()
                if t.name in ("param-history-index", "index-listeners") and t.is_alive()]
        if not live:
            with hm._indexed_lock:
                if not hm._indexed_pending:
                    return
        for t in live:
            t.join(max(0.0, end - time.monotonic()))
        time.sleep(0.01)
    raise AssertionError("background index work did not finish")


def _capture(client, folder, t1):
    sp = folder / "state.json"
    doc = json.loads(sp.read_text(encoding="utf-8"))
    doc["qubits"][next(iter(doc["qubits"]))]["T1"] = t1
    sp.write_text(json.dumps(doc), encoding="utf-8")
    assert client.post("/state/archive", data={"tag": f"t{t1}"}).status_code == 200


def _spy_builds(monkeypatch):
    from quam_state_manager.web import routes
    n = {"prov": 0, "chips": 0, "rows": 0}
    for name, key in (("_snapshot_provenance_build", "prov"), ("_trend_pair_chips_uncached", "chips")):
        real = getattr(routes, name)
        monkeypatch.setattr(routes, name, lambda *a, _r=real, _k=key, **k: (n.__setitem__(_k, n[_k] + 1), _r(*a, **k))[1])
    real_rows = ctr._family_rows
    monkeypatch.setattr(ctr, "_family_rows", lambda c: (n.__setitem__("rows", n["rows"] + 1), real_rows(c))[1])
    return n


def test_a_capture_prewarms_an_open_chip_off_the_request_path(client, tmp_path, monkeypatch):
    """The first request after a new snapshot used to rebuild every part
    (0.4-2.2 s at 1,600 snapshots). The capture's index commit now re-derives
    them on a background thread, so that request computes none of them -- and
    what it serves is still exactly the cold answer."""
    folder = tmp_path / "quam_state"
    _versions(client, folder, n=2)
    hm = client.application.config["history_manager"]
    assert client.get("/topology/trends").status_code == 200        # Trends opened
    time.sleep(1.05)
    # S10 C3: snapshot-table prewarm -> the ledger's: the capture reaches the ledger on the
    # projector thread (capture refresh) and the read index is rebuilt there after the burst,
    # so the first chart request builds no index and never reads the retired snapshot table.
    from quam_state_manager.core import hub, hub_index
    from tests.test_hub_incremental import from_scratch
    old_inline, old_pre = hub._PROJECTOR.inline, hub.PREWARM
    hub.set_inline(False)
    hub.set_prewarm(True)
    try:
        _capture(client, folder, 7.7e-5)
        _join_background(hm)
        assert hub._PROJECTOR.flush(30), "the capture's ledger import never finished"
        deadline = time.monotonic() + 30
        while hub._PREWARM_THREAD is not None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert hub._PREWARM_THREAD is None, "the index prewarm never finished"
    finally:
        hub.set_inline(old_inline)
        hub.set_prewarm(old_pre)
    n = {"index": 0, "snapshot": 0}
    real_extend, real_build = hub_index.extend_index, hub_index.build_index

    def index(*a, **k):
        n["index"] += 1
        return real_extend(*a, **k)

    def build(*a, **k):
        n["index"] += 1
        return real_build(*a, **k)

    def snapshot(*a, **k):
        n["snapshot"] += 1
        raise AssertionError("retired snapshot table was read")

    monkeypatch.setattr(hub_index, "extend_index", index)
    monkeypatch.setattr(hub_index, "build_index", build)
    monkeypatch.setattr(hm, "extract_property_history", snapshot)
    warm = client.get("/topology/trends?metrics=T1,f_01").get_data(as_text=True)
    assert n == {"index": 0, "snapshot": 0}, n
    monkeypatch.undo()
    assert "7.7e-05" in warm or "7.7e-5" in warm, "the new snapshot's value is charted"
    with from_scratch():
        assert client.get("/topology/trends?metrics=T1,f_01").get_data(as_text=True) == warm


def test_a_chip_nobody_opened_trends_for_is_not_prewarmed(client, tmp_path, monkeypatch):
    folder = tmp_path / "quam_state"
    _versions(client, folder, n=2)
    hm = client.application.config["history_manager"]
    _join_background(hm)
    n = _spy_builds(monkeypatch)
    time.sleep(1.05)
    _capture(client, folder, 6.6e-5)
    _join_background(hm)
    assert n == {"prov": 0, "chips": 0, "rows": 0}, n


# ── the global pin: a random event sequence against a cold oracle ───────────

_URLS = ["/topology/trends", "/topology/trends?metrics=T1", "/topology/trends?metrics=T1,f_01",
         "/topology/trends?metrics=&paths=qubit_pairs.*.coupler.interaction_offset",
         "/topology/trends?metrics=x180_amplitude&paths=qubit_pairs.*.macros.cz.phase_shift_target",
         "/topology/trends/paths?q=offset", "/topology/trends/paths?q=q1",
         "/topology/trends/paths?q=phase%20|%20offset"]


def _norm(resp):
    return resp.get_json() if resp.is_json else resp.get_data(as_text=True)


def test_a_random_event_sequence_never_serves_a_stale_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("SM_RAM_VERIFY", "1")
    # S10 C3: forced snapshot fallback -> actual ledger events, preserving the random cold oracle.
    from quam_state_manager.web import hub_status
    from tests.test_hub_incremental import from_scratch
    folder = _chip(tmp_path / "quam_state")
    warm = create_app(testing=True, instance_path=str(tmp_path / "_i")).test_client()
    warm.post("/load", data={"folder": str(folder)})
    _versions(warm, folder, n=2)
    cold_app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    cold = cold_app.test_client()
    cold.post("/load", data={"folder": str(folder)})
    hm_w = warm.application.config["history_manager"]
    hm_c = cold_app.config["history_manager"]
    idx = hm_w._history_dir(folder) / "ledger.sqlite"
    rng = random.Random(20260926)
    counts = {"capture": 0, "leaf_write": 0, "cp_write": 0, "new_path": 0, "none": 0}
    hits0 = hub_status._CACHE.hits
    for step in range(60):
        ev = rng.choice(["none", "none", "none", "leaf_write", "cp_write", "new_path"]
                        + (["capture"] if counts["capture"] < 4 else []))
        counts[ev] += 1
        if ev == "capture":
            sp = folder / "state.json"
            doc = json.loads(sp.read_text(encoding="utf-8"))
            q = rng.choice(list(doc["qubits"]))
            doc["qubits"][q]["T1"] = rng.uniform(1e-5, 9e-5)
            doc["qubit_pairs"]["q1-q2"]["coupler"]["interaction_offset"] = rng.uniform(0, 0.1)
            sp.write_text(json.dumps(doc), encoding="utf-8")
            warm.post("/state/archive", data={"tag": f"s{step}"})
            time.sleep(1.05)
        elif ev in ("leaf_write", "cp_write", "new_path"):
            c = sqlite3.connect(str(idx), isolation_level=None, timeout=10)
            if ev == "leaf_write":
                row = c.execute("SELECT pid, eid FROM changes WHERE num IS NOT NULL "
                                "ORDER BY eid DESC, pid DESC LIMIT 1").fetchone()
                c.execute("UPDATE changes SET num = ? WHERE pid = ? AND eid = ?",
                          (rng.uniform(0, 1), row[0], row[1]))
            elif ev == "cp_write":
                c.execute("UPDATE changes SET num = ? WHERE pid = "
                          "(SELECT pid FROM paths WHERE path='qubits.q1.T1')",
                          (rng.uniform(1e-5, 9e-5),))
            else:
                pid = c.execute("INSERT INTO paths(path,entity,entity_kind,family) VALUES (?,?,?,?)",
                                (f"qubit_pairs.q1-q2.coupler.offset_{step}", "q1-q2", "pair",
                                 f"coupler.offset_{step}")).lastrowid
                eid = c.execute("SELECT MAX(eid) FROM events").fetchone()[0]
                c.execute("INSERT INTO changes(pid,eid,num,op) VALUES (?,?,?,1)", (pid, eid, 0.5))
                # a writer that adds a row to an event re-diffs it: its row count moves too
                c.execute("UPDATE events SET n_changes = n_changes + 1 WHERE eid = ?", (eid,))
            c.close()
        for url in rng.sample(_URLS, 3):
            got = _norm(warm.get(url))
            with from_scratch():
                want = _norm(cold.get(url))
            assert got == want, (step, ev, url)
    assert hub_status._CACHE.hits > hits0, "the sequence must exercise cache HITS, or it pins nothing"
    assert all(v for v in counts.values()), counts


def test_client_patch_selfcheck():
    if shutil.which("node") is None:
        pytest.skip("node not available")
    try:
        import importlib.util as _u  # noqa: F401
        r = subprocess.run(["node", "-e", "require('jsdom')"], cwd=ROOT, capture_output=True)
        if r.returncode:
            pytest.skip("jsdom not installed")
    except OSError:
        pytest.skip("node not runnable")
    r = subprocess.run(["node", "tests/trends_patch_selfcheck.cjs"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr
    assert re.search(r"ok \(\d+ assertions\)", r.stdout), r.stdout


# ── a capture DERIVES the family table; anything else reads everything ──────

_DERIVE_QUERIES = ["", "t1", "q1", "amplitude", "offset | fidelity", "qubit_pairs", "q1 t1",
                   "interleaved", "zzz"]
_DERIVE_PATTERNS = ("qubits.*.T1", "qubit_pairs.*.macros.*.fidelity.InterleavedRB",
                    "qubits.*.*", "qubits.q1.*")


class _OneConn:
    def __init__(self, conn):
        self.conn = conn

    def run(self, fn):
        return fn(self.conn)


def _derive_step(prev, conn, ic):
    t = ctr.ChipTrendsTable(None, "x", Path("x"), [], ic)
    if prev is not None:
        t._base = prev.export_base()
        t._depth = (t._base.depth + 1) if t._base is not None else 0
    for roots, term in ((("qubits", "qubit_pairs"), None), (("qubit_pairs",), "qubit_pairs")):
        ft = t.families(roots, term)
        for q in _DERIVE_QUERIES:
            if term is not None and term not in q:
                continue
            assert ft.query(q) == li.path_families(conn, q, roots=roots), (roots, term, q)
        # the table itself: family for family what a full build makes
        rows = ctr._family_rows(conn)
        if term is not None:
            rows = [r for r in rows if term in r[2]]
        assert ft == ctr.FamilyTable(rows, roots, term)
    for pat in _DERIVE_PATTERNS:
        assert t.leaf_matching_paths(pat) == li.matching_paths(conn, pat), pat
    return t


@pytest.mark.parametrize("seed", [11, 12, 13, 14])
def test_a_derived_family_table_is_a_full_build_under_any_event_sequence(tmp_path, seed):
    """The RAM P1a after-capture path: an APPEND (new snapshot, new change
    points, new paths) derives the table from the previous token's; every
    other writer -- a rebuild that renumbers, a re-stamp that deletes a
    snapshot's rows, a change point written into the past, a moved row --
    must be detected and answered by a full read. After EVERY event the
    served answer is compared with the SQL and the table with a full build."""
    rng = random.Random(seed)
    conn = sqlite3.connect(str(tmp_path / "d.sqlite"), isolation_level=None)
    conn.execute("PRAGMA synchronous=OFF")
    li.ensure_schema(conn)
    paths = list(_PATHS)
    for pid, p in enumerate(paths):
        conn.execute("INSERT INTO leaf_paths (id, path) VALUES (?, ?)", (pid, p))
    conn.execute("INSERT INTO leaf_snaps (id, ts) VALUES (0, '20260101_000000')")
    for pid in range(len(paths)):
        conn.execute("INSERT INTO leaf_cp (path_id, snap_id, value, kind) VALUES (?,0,0,0)", (pid,))
    ic = _OneConn(conn)
    before = dict(ctr.COUNTS)
    prev = _derive_step(None, conn, ic)
    seen = set()
    for step in range(70):
        ev = rng.choice(["append", "append", "append", "append_new_path", "none", "value_rewrite",
                         "past_insert", "delete_snapshot", "move_row", "renumber",
                         "path_delete"])
        seen.add(ev)
        sid = conn.execute("SELECT MAX(id) FROM leaf_snaps").fetchone()[0]
        if ev in ("append", "append_new_path"):
            new = sid + 1
            conn.execute("INSERT INTO leaf_snaps (id, ts) VALUES (?, ?)", (new, f"2026{new:010d}"))
            for pid in rng.sample(range(len(paths)), rng.randint(0, 4)):
                conn.execute("INSERT INTO leaf_cp (path_id, snap_id, value, kind) VALUES (?,?,?,0)",
                             (pid, new, rng.random()))
            if ev == "append_new_path":
                p = rng.choice(["qubits.q%d.T1", "qubit_pairs.q%d-q9.macros.cz.fidelity.InterleavedRB",
                                "qubits.q1.extra_%d", "loose_%d", "qubits.q%d.coupler.interaction_offset",
                                "qubit_pairs.q%d-q7.coupler.interaction_offset"]) % step
                if p not in paths:
                    conn.execute("INSERT INTO leaf_paths (id, path) VALUES (?, ?)", (len(paths), p))
                    conn.execute("INSERT INTO leaf_cp (path_id, snap_id, value, kind) VALUES (?,?,1,0)",
                                 (len(paths), new))
                    paths.append(p)
        elif ev == "value_rewrite":
            conn.execute("UPDATE leaf_cp SET value = ? WHERE snap_id = ?", (rng.random(), sid))
        elif ev == "past_insert" and sid > 0:
            pid = rng.randrange(len(paths))
            conn.execute("INSERT OR IGNORE INTO leaf_cp (path_id, snap_id, value, kind) "
                         "VALUES (?, 0, 5, 0)", (pid,))
        elif ev == "delete_snapshot" and sid > 0:
            victim = rng.randint(1, sid)
            conn.execute("DELETE FROM leaf_cp WHERE snap_id = ?", (victim,))
        elif ev == "move_row":
            row = conn.execute("SELECT path_id, snap_id FROM leaf_cp ORDER BY snap_id DESC LIMIT 1").fetchone()
            other = (row[0] + 1) % len(paths)
            conn.execute("DELETE FROM leaf_cp WHERE path_id = ? AND snap_id = ?", row)
            conn.execute("INSERT OR IGNORE INTO leaf_cp (path_id, snap_id, value, kind) VALUES (?,?,0,0)",
                         (other, row[1]))
        elif ev == "path_delete":
            # not a writer SM has (leaf_paths is insert-only); the paths
            # fingerprint must still see it
            pid = rng.randrange(len(paths) - 1)
            conn.execute("DELETE FROM leaf_paths WHERE id = ?", (pid,))
        elif ev == "renumber":
            # a rebuild's shape: every change point re-inserted, snapshots renumbered
            rows = conn.execute("SELECT path_id, snap_id, value, kind FROM leaf_cp").fetchall()
            conn.execute("DELETE FROM leaf_cp")
            conn.executemany("INSERT INTO leaf_cp (path_id, snap_id, value, kind) VALUES (?,?,?,?)",
                             [(p, max(0, s - 1), v, k) for p, s, v, k in rows
                              if not (s == 1)])
        prev = _derive_step(prev, conn, ic)
    after = {k: ctr.COUNTS[k] - before[k] for k in before}
    assert after["derive"] > 10 and after["full"] > 3 and after["reuse"] > 0, (after, seen)
    conn.close()


def test_the_derive_reads_no_path_it_already_holds(tmp_path):
    """The point of the derive: after one appended snapshot the second table
    fetches only the changed paths, not every path (the 16-22 s case)."""
    conn = sqlite3.connect(str(tmp_path / "n.sqlite"), isolation_level=None)
    conn.execute("PRAGMA synchronous=OFF")
    li.ensure_schema(conn)
    conn.execute("BEGIN")
    conn.execute("INSERT INTO leaf_snaps (id, ts) VALUES (0, 'a')")
    conn.executemany("INSERT INTO leaf_paths (id, path) VALUES (?, ?)",
                     [(i, f"qubits.q{i}.T1") for i in range(3000)])
    conn.executemany("INSERT INTO leaf_cp (path_id, snap_id, value, kind) VALUES (?,0,0,0)",
                     [(i,) for i in range(3000)])
    conn.execute("COMMIT")
    sqls = []

    class _Recording:
        def run(self, fn):
            class C:
                def execute(_s, sql, *a):
                    sqls.append(sql)
                    return conn.execute(sql, *a)
            return fn(C())

    ic = _Recording()
    t1 = ctr.ChipTrendsTable(None, "x", Path("x"), [], ic)
    t1.families(("qubits",), None)
    assert any("group_concat" in q for q in sqls), "the first table reads everything"
    conn.execute("INSERT INTO leaf_snaps (id, ts) VALUES (1, 'b')")
    conn.execute("INSERT INTO leaf_cp (path_id, snap_id, value, kind) VALUES (7, 1, 1, 0)")
    sqls.clear()
    t2 = ctr.ChipTrendsTable(None, "x", Path("x"), [], ic)
    t2._base = t1.export_base()
    ft = t2.families(("qubits",), None)
    assert ft.query("q7.") == li.path_families(conn, "q7.", roots=("qubits",))
    assert sqls and not any("group_concat" in q or "GROUP BY path_id)" in q for q in sqls), sqls
    conn.close()


def test_a_tie_is_ordered_like_the_sql_whatever_order_families_arrive_in(tmp_path):
    """Equal change counts AND equal tails under two scopes: the SQL keeps its
    GROUP BY (scope, tail) order through a stable sort; a derived table
    appends new families at the END, so the RAM query must not depend on
    arrival order."""
    conn = sqlite3.connect(str(tmp_path / "t.sqlite"), isolation_level=None)
    li.ensure_schema(conn)
    tie = ["qubits.q1.coupler.interaction_offset", "qubit_pairs.q1-q2.coupler.interaction_offset",
           "qubits.q2.T1", "qubit_pairs.q1-q2.T1"]
    conn.execute("INSERT INTO leaf_snaps (id, ts) VALUES (0, 'a')")
    for pid, p in enumerate(tie):
        conn.execute("INSERT INTO leaf_paths (id, path) VALUES (?, ?)", (pid, p))
        conn.execute("INSERT INTO leaf_cp (path_id, snap_id, value, kind) VALUES (?,0,0,0)", (pid,))
    rows = ctr._family_rows(conn)
    want = li.path_families(conn, "")
    assert [r["scope"] for r in want][:2] == ["qubit_pairs", "qubits"]   # the tie exists
    for order in (rows, list(reversed(rows))):
        assert ctr.FamilyTable(order, ("qubits", "qubit_pairs")).query("") == want
    conn.close()


def test_the_rows_and_their_marks_are_read_in_one_transaction(tmp_path):
    """A capture committing BETWEEN the marks read and the rows read must not
    be counted twice by the next derive (WAL: one read transaction sees one
    commit)."""
    db = tmp_path / "w.sqlite"
    conn = sqlite3.connect(str(db), isolation_level=None)
    assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    li.ensure_schema(conn)
    conn.execute("INSERT INTO leaf_snaps (id, ts) VALUES (0, 'a')")
    for pid in range(5):
        conn.execute("INSERT INTO leaf_paths (id, path) VALUES (?, ?)", (pid, f"qubits.q{pid}.T1"))
        conn.execute("INSERT INTO leaf_cp (path_id, snap_id, value, kind) VALUES (?,0,0,0)", (pid,))
    writer = sqlite3.connect(str(db), isolation_level=None, timeout=30)
    state = {"armed": True}

    class _Racy:
        def run(self, fn):
            class C:
                def execute(_s, sql, *a):
                    cur = conn.execute(sql, *a)
                    if state["armed"] and "MAX(snap_id)" in sql:
                        rows = cur.fetchall()
                        state["armed"] = False          # a capture lands right now
                        writer.execute("INSERT INTO leaf_snaps (id, ts) VALUES (1, 'b')")
                        writer.execute("INSERT INTO leaf_cp (path_id, snap_id, value, kind) "
                                       "VALUES (3, 1, 1, 0)")

                        class R:
                            def fetchone(self):
                                return rows[0]
                        return R()
                    return cur
            return fn(C())

    ic = _Racy()
    t1 = ctr.ChipTrendsTable(None, "x", Path("x"), [], ic)
    t1.families(("qubits",), None)
    t2 = ctr.ChipTrendsTable(None, "x", Path("x"), [], ic)
    t2._base = t1.export_base()
    assert t2.families(("qubits",), None).query("") == li.path_families(conn, "", roots=("qubits",))
    writer.close()
    conn.close()


def test_parts_compute_concurrently_and_once(tmp_path):
    """The pre-warm thread computing one part (the provenance map) must not
    hold a request needing ANOTHER part; two askers of ONE part compute it
    once; a failed owner leaves the part to the next asker."""
    t = ctr.ChipTrendsTable(None, "x", Path("x"), [], None)
    gate, started, calls = threading.Event(), threading.Event(), []

    def slow():
        calls.append("a")
        started.set()
        gate.wait(10)
        return "A"

    th = threading.Thread(target=lambda: t.part(("a",), slow))
    th.start()
    assert started.wait(5)
    t0 = time.monotonic()
    assert t.part(("b",), lambda: "B") == "B"
    assert time.monotonic() - t0 < 1.0, "another key waited for the slow part"
    got = []
    th2 = threading.Thread(target=lambda: got.append(t.part(("a",), slow)))
    th2.start()
    time.sleep(0.2)
    gate.set()
    th.join(5)
    th2.join(5)
    assert got == ["A"] and calls == ["a"]

    def boom():
        raise RuntimeError("x")
    with pytest.raises(RuntimeError):
        t.part(("c",), boom)
    assert t.part(("c",), lambda: "C") == "C"


# ── D1: the shadow check compares at the token, never across a commit ───────

def _wal_index(tmp_path):
    db = tmp_path / "index.sqlite"
    conn = sqlite3.connect(str(db), isolation_level=None)
    assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    li.ensure_schema(conn)
    conn.execute("INSERT INTO leaf_snaps (id, ts) VALUES (0, 'a')")
    for pid in range(4):
        conn.execute("INSERT INTO leaf_paths (id, path) VALUES (?, ?)",
                     (pid, f"qubit_pairs.q{pid}-q9.macros.cz.fidelity.InterleavedRB"))
        conn.execute("INSERT INTO leaf_cp (path_id, snap_id, value, kind) VALUES (?,0,0,0)", (pid,))
    return db, conn


def _append(conn, sid, pid):
    conn.execute("INSERT INTO leaf_snaps (id, ts) VALUES (?, ?)", (sid, f"s{sid}"))
    conn.execute("INSERT INTO leaf_cp (path_id, snap_id, value, kind) VALUES (?,?,1,0)", (pid, sid))


class _ColdHm:
    """The cold path the shadow check compares with: a fresh connection."""

    def __init__(self, db):
        self.db = db

    def leaf_matching_paths(self, _path, pattern):
        c = sqlite3.connect(str(self.db))
        try:
            return li.matching_paths(c, pattern)
        finally:
            c.close()


def _tabled(db, prev):
    """A table stored under the index's CURRENT data_version, deriving from
    *prev* -- what ``ctr.table`` builds for a new token."""
    ic = ctr._conn_for(db.parent)
    t = ctr.ChipTrendsTable(_ColdHm(db), "x", db.parent, [], ic)
    t.token = (str(db.parent), 1, 0, None, None, ic.data_version())
    if prev is not None:
        t._base = prev.export_base()
        t._depth = (t._base.depth + 1) if t._base is not None else 0
    return t


_PAT = "qubit_pairs.*.macros.*.fidelity.InterleavedRB"


def test_a_commit_between_a_delta_and_its_shadow_check_is_not_a_stale_serve(tmp_path, monkeypatch):
    """D1 (verifier, lab-F-5h rig, SM_RAM_VERIFY=1): ``part ('delta', marks)
    differs from a cold recompute`` on leaf_matching_paths right after a
    capture. Reproduced deterministically: the background indexer commits
    between the delta read and its shadow recompute. The SERVED delta is the
    exact append as of the table's token (never older); only the check was
    comparing it with a LATER index. After the fix the check skips a
    comparison the index moved under, and still catches a wrong value when
    the index sits at the token (the poison half)."""
    monkeypatch.setenv("SM_RAM_VERIFY", "1")
    db, w = _wal_index(tmp_path)
    t0 = _tabled(db, None)
    assert t0.leaf_matching_paths(_PAT) == li.matching_paths(w, _PAT)
    t0.families(("qubit_pairs",), None)
    _append(w, 1, 2)                                  # the capture
    at_token = li.matching_paths(w, _PAT)
    fam_at_token = li.path_families(w, "", roots=("qubit_pairs",))
    t1 = _tabled(db, t0)
    real = ctr._delta_read
    fired = []

    def racing(conn, base):
        got = real(conn, base)
        if not fired:                                 # the indexer's next commit lands NOW
            fired.append(1)
            _append(w, 2, 3)
            w.execute("INSERT INTO leaf_paths (id, path) VALUES (9, ?)",
                      ("qubit_pairs.q7-q9.macros.cz.fidelity.InterleavedRB",))
        return got
    monkeypatch.setattr(ctr, "_delta_read", racing)
    # the badge row's family table computes the delta, the indexer commits,
    # then the IRB default's matching paths HIT that memoized delta: the
    # verifier's exact call path (leaf_matching_paths -> _delta)
    assert t1.families(("qubit_pairs",), None).query("") == fam_at_token
    assert fired, "the race was not staged"
    served = t1.leaf_matching_paths(_PAT)
    assert served == at_token, "the served answer is not the one AT the table's token"
    d = t1._parts[("delta", t0._parts[("matching_m", _PAT)][1])]
    assert d.marks.snap_max == 1, "the memoized delta is not the token's append"
    # the next token sees the racing commit, derived from this table
    monkeypatch.setattr(ctr, "_delta_read", real)
    t2 = _tabled(db, t1)
    assert t2.leaf_matching_paths(_PAT) == li.matching_paths(w, _PAT)
    assert len(t2.leaf_matching_paths(_PAT)) == 5
    # poison: the index AT the token, a memoized part that is wrong -> caught
    t3 = _tabled(db, t2)
    key = ("delta", t2._parts[("matching_m", _PAT)][1])
    t3._parts[key] = ctr._Delta({}, [("qubit_pairs.bogus", 1, "qubit_pairs.bogus", 99)],
                                t2._parts[("matching_m", _PAT)][1])
    with pytest.raises(ctr.ramcache.StaleCacheError):
        t3._delta(t2._parts[("matching_m", _PAT)][1])
    w.close()


# ── D2: a one-term family table never reads the whole index ─────────────────

_TERMS = ["qubit_pairs", "t1", "q1", "%", "_", "100%", "under_score", "ämp", "Ämp".lower(),
          "interleavedrb", "zzz", ".", "qubits.q1."]


@pytest.mark.parametrize("seed", [1, 2])
def test_a_term_read_is_the_full_read_filtered(tmp_path, seed):
    """The filtered read is row for row (order, counts, ids) the full read
    filtered by the same term, and carries the full read's marks -- so a
    table built from it derives after an append exactly as before."""
    conn = _index(tmp_path, random.Random(seed))
    full, fmarks = ctr._family_rows_marked(conn)
    for t in _TERMS:
        t = ctr._alower(t)
        rows, marks = ctr._family_rows_term_marked(conn, t)
        assert rows == [r for r in full if t in r[2]], t
        assert marks == fmarks, t


def test_the_badge_rows_first_table_reads_only_its_paths(tmp_path, monkeypatch):
    """The first Trends request after a restart asks only for the
    ``qubit_pairs`` table; reading all 180k paths for it made that request
    slower than the base branch (verifier D2, big30x). The typeahead's
    full table is still a full read."""
    conn = _index(tmp_path, random.Random(3))
    whole = []
    real = ctr._family_rows_marked
    monkeypatch.setattr(ctr, "_family_rows_marked", lambda c: whole.append(1) or real(c))
    t = ctr.ChipTrendsTable(None, "x", Path("x"), [], _OneConn(conn))
    ft = t.families(("qubit_pairs",), "qubit_pairs")
    assert whole == [], "a one-term table read the whole index"
    assert ft.query("qubit_pairs") == li.path_families(conn, "qubit_pairs", roots=("qubit_pairs",))
    t.families(("qubits", "qubit_pairs"), None)
    assert whole == [1]
    conn.close()
