"""RAM P10 -- cold open and chip switch.

* a chip that leaves the context LRU clean and pristine PARKS its models
  (core/chip_park.py) and a re-open takes them back only when the working
  files' bytes still digest to what that store was parsed from;
* the search index is built on first search, not on open (LazySearchIndex);
* the "working copy differs from the last sync" hash is memoized per file
  digest.

The global pin is the randomized event sequence at the bottom: every
context the LRU builds or re-takes must equal a cold build of the same
working files -- content, working_dirty and search answers.
"""
from __future__ import annotations

import json
import os
import random
from pathlib import Path

import pytest

from quam_state_manager.core import chip_park, path_match, ramcache, working_copy
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.search_index import LazySearchIndex, SearchIndex
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app


def _make_chip(folder: Path, f01: float = 6.1e9, n: int = 3) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    qubits = {f"q{i}": {"id": f"q{i}", "f_01": f01 + i, "T1": 1.0e-5,
                        "xy": {"operations": {"x180": {"amplitude": 0.1 * (i + 1),
                                                       "length": 40}}}}
              for i in range(1, n + 1)}
    state = {"qubits": qubits, "qubit_pairs": {},
             "active_qubit_names": list(qubits)}
    wiring = {"wiring": {"qubits": {q: {"xy": {"opx_output": "#/ports/mw/1"}}
                                    for q in qubits}},
              "network": {"host": "10.0.0.1"}}
    (folder / "state.json").write_text(json.dumps(state, indent=4))
    (folder / "wiring.json").write_text(json.dumps(wiring, indent=4))
    return folder


@pytest.fixture
def app(tmp_path):
    return create_app(testing=True, instance_path=str(tmp_path / "_inst"))


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    routes._quam_cache.clear()
    chip_park.PARKED.clear()
    chip_park.CONTENT_HASH.clear()
    monkeypatch.setattr(routes, "_QUAM_CACHE_MAX", 2)
    yield
    routes._quam_cache.clear()
    chip_park.PARKED.clear()
    chip_park.CONTENT_HASH.clear()


def _ctx(folder) -> dict:
    return routes._quam_cache[path_match.fs_key(Path(folder).resolve())]


def _load(c, folder):
    r = c.post("/load", data={"folder": str(folder)})
    assert r.status_code in (200, 302), r.status_code
    return _ctx(folder)


def _rewrite_same_size(folder: Path, old: str, new: str) -> None:
    """Change bytes without changing size, then restore the mtime: the racy
    rewrite every stat-only gate misses."""
    p = folder / "state.json"
    st = p.stat()
    raw = p.read_text()
    assert old in raw and len(old) == len(new)
    p.write_text(raw.replace(old, new, 1))
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert p.stat().st_size == st.st_size


# ------------------------------------------------------------------ parking
class TestParkAndReopen:
    def test_reopen_after_eviction_takes_the_same_models_back(self, app, tmp_path):
        c = app.test_client()
        a, b, d = (_make_chip(tmp_path / n) for n in ("A", "B", "D"))
        ctx_a = _load(c, a)
        store_a, engine_a, index_a = ctx_a["store"], ctx_a["engine"], ctx_a["index"]
        _load(c, b)
        _load(c, d)                               # cap 2: A leaves the LRU
        assert path_match.fs_key(a.resolve()) not in routes._quam_cache
        assert len(chip_park.PARKED.slots()) == 1
        again = _load(c, a)
        assert again is not ctx_a                 # a new context ...
        assert again["store"] is store_a          # ... over the parked models
        assert again["engine"] is engine_a
        assert again["index"] is index_a
        # taken, not copied (B, evicted by this very open, may be parked now)
        assert path_match.fs_key(again["working_copy"].working_folder)             not in chip_park.PARKED.slots()

    def test_debug_ram_totals_equal_entry_sizes(self, app, tmp_path):
        c = app.test_client()
        chips = [_make_chip(tmp_path / f"C{i}") for i in range(5)]
        for ch in chips + chips[:2]:
            _load(c, ch)
        snap = c.get("/debug/ram").get_json()
        assert snap["total_bytes"] == snap["entry_bytes_sum"]
        parked = next(m for m in snap["memos"] if m["name"] == "quam_parked")
        assert parked["entries"] >= 1 and parked["bytes"] > 0
        assert parked["bytes"] == parked["entry_bytes_sum"]
        assert snap["process_rss_bytes"] is None or snap["process_rss_bytes"] > 0
        assert snap["quam_contexts"]["max"] == 2

    def test_same_size_rewrite_with_restored_mtime_is_not_reused(self, app, tmp_path):
        c = app.test_client()
        a, b, d = (_make_chip(tmp_path / n) for n in ("A", "B", "D"))
        ctx_a = _load(c, a)
        wf = Path(ctx_a["working_copy"].working_folder)
        _load(c, b)
        _load(c, d)
        _rewrite_same_size(wf, "6100000001.0", "6100000009.0")
        again = _load(c, a)
        assert again["store"] is not ctx_a["store"]
        assert again["store"].state["qubits"]["q1"]["f_01"] == 6100000009.0

    def test_a_wiring_only_rewrite_is_not_reused(self, app, tmp_path):
        c = app.test_client()
        a, b, d = (_make_chip(tmp_path / n) for n in ("A", "B", "D"))
        ctx_a = _load(c, a)
        wf = Path(ctx_a["working_copy"].working_folder)
        _load(c, b)
        _load(c, d)
        p = wf / "wiring.json"
        st = p.stat()
        p.write_text(p.read_text().replace("10.0.0.1", "10.0.0.2"))
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
        again = _load(c, a)
        assert again["store"] is not ctx_a["store"]
        assert again["store"].wiring["network"]["host"] == "10.0.0.2"

    def test_a_late_edit_on_a_parked_store_is_never_served(self, app, tmp_path):
        """A request still holding the evicted context edits it after it was
        parked. The files did not change, so only the store's own counter can
        tell: re-validation must reject it."""
        c = app.test_client()
        a, b, d = (_make_chip(tmp_path / n) for n in ("A", "B", "D"))
        ctx_a = _load(c, a)
        _load(c, b)
        _load(c, d)
        ctx_a["modifier"].set_value("qubits.q1.T1", 7.0e-5)
        again = _load(c, a)
        assert again["store"] is not ctx_a["store"]
        assert again["store"].state["qubits"]["q1"]["T1"] == 1.0e-5

    def test_an_edited_and_saved_chip_reopens_as_its_files(self, app, tmp_path):
        """Whether the saved store is parked (a save that reloads makes it
        pristine again, over the NEW bytes) or rebuilt, the re-open equals
        the files."""
        c = app.test_client()
        a, b, d = (_make_chip(tmp_path / n) for n in ("A", "B", "D"))
        _load(c, a)
        c.post("/field/edit", data={"dot_path": "qubits.q2.T1", "value": "3e-05"})
        assert c.post("/save").status_code == 200
        _load(c, b)
        _load(c, d)
        again = _load(c, a)
        cold = QuamStore(Path(again["working_copy"].working_folder))
        assert again["store"].state == cold.state
        assert again["store"].state["qubits"]["q2"]["T1"] == 3e-05


class TestParkOwnGate:
    """park() refuses a non-pristine store ON ITS OWN -- not only because
    unpark re-checks later (verifier P3-1: deleting park's gate kept every
    other test green)."""

    def test_an_edited_store_is_not_parked(self, app, tmp_path):
        c = app.test_client()
        ctx = _load(c, _make_chip(tmp_path / "A"))
        ctx["modifier"].set_value("qubits.q1.T1", 7.0e-5)
        assert chip_park.park(ctx) is False
        assert chip_park.PARKED.slots() == []

    def test_an_undone_edit_is_still_not_pristine(self, app, tmp_path):
        """Content equal again, counter moved: pristine means 'no mutation
        since the load', not 'equal content' -- park must refuse."""
        c = app.test_client()
        ctx = _load(c, _make_chip(tmp_path / "A"))
        ctx["modifier"].set_value("qubits.q1.T1", 7.0e-5)
        ctx["modifier"].undo()
        assert not ctx["store"].change_log
        assert chip_park.park(ctx) is False
        assert chip_park.PARKED.slots() == []

    def test_a_pristine_store_is_parked(self, app, tmp_path):
        c = app.test_client()
        ctx = _load(c, _make_chip(tmp_path / "A"))
        assert chip_park.park(ctx) is True
        assert len(chip_park.PARKED.slots()) == 1


def test_take_never_returns_another_tokens_value():
    m = ramcache.KeyedMemo("t.take")
    try:
        m.put("s", "tok1", b"x" * 10)
        assert m.take("s", "tok2") is None          # wrong token: a miss ...
        assert m.peek("s") is None                  # ... and the stale entry is gone
        m.put("s", "tok1", b"y" * 10)
        assert m.take("s", "tok1") == b"y" * 10     # right token: handed over once
        assert m.take("s", "tok1") is None
        assert m.stats()["bytes"] == 0 == m.stats()["entry_bytes_sum"]
    finally:
        m.clear()


# -------------------------------------------------------------- lazy index
class TestLazySearchIndex:
    def test_open_builds_no_index_and_first_search_does(self, app, tmp_path, monkeypatch):
        # The OPEN builds nothing on the request thread. The background
        # prewarm (tested below) is switched off here so the assertion is
        # about the open alone, not a race with the worker.
        from quam_state_manager.core import search_index as si
        monkeypatch.setattr(si, "_prewarm_submit", lambda lazy, pre=None: None)
        c = app.test_client()
        ctx = _load(c, _make_chip(tmp_path / "A"))
        idx = ctx["index"]
        assert isinstance(idx, LazySearchIndex) and not idx.built
        r = c.get("/api/search?q=f_01")
        assert r.status_code == 200 and len(r.get_json()) == 3
        assert idx.built and idx.builds == 1

    def test_edits_before_the_build_are_in_it(self, tmp_path):
        store = QuamStore(_make_chip(tmp_path / "A"))
        lazy = store.search_index = LazySearchIndex(store)
        from quam_state_manager.core.modifier import Modifier
        Modifier(store).set_value("qubits.q1.T1", 4.25e-05)
        hits = lazy.search("4.25e-05")
        assert [h.dot_path for h in hits] == ["qubits.q1.T1"]

    def test_an_edit_racing_the_build_forces_a_rebuild(self, tmp_path, monkeypatch):
        """The build runs from a snapshot with the store lock released; an
        edit landing in that window must not leave its old value indexed."""
        store = QuamStore(_make_chip(tmp_path / "A"))
        lazy = store.search_index = LazySearchIndex(store)
        from quam_state_manager.core.modifier import Modifier
        mod = Modifier(store)
        real = SearchIndex.from_leaves.__func__
        calls = {"n": 0}

        def racing(cls, leaves, keys):
            calls["n"] += 1
            if calls["n"] == 1:
                mod.set_value("qubits.q1.T1", 9.75e-05)   # lands mid-build
            return real(cls, leaves, keys)

        monkeypatch.setattr(SearchIndex, "from_leaves", classmethod(racing))
        hits = lazy.search("9.75e-05")
        assert [h.dot_path for h in hits] == ["qubits.q1.T1"]
        assert calls["n"] == 2


# ---------------------------------------------------------- warm path memos
class TestWarmPathMemos:
    def test_needs_generated_config_walks_the_pulses_once_per_content(self, tmp_path, monkeypatch):
        store = QuamStore(_make_chip(tmp_path / "A"))
        n = {"rows": 0}
        real_rows = routes.PulseIndex.rows

        def counting(self):
            n["rows"] += 1
            return real_rows(self)

        monkeypatch.setattr(routes.PulseIndex, "rows", counting)
        routes._chip_needs_generated_config(store)
        routes._chip_needs_generated_config(store)
        assert n["rows"] == 1
        from quam_state_manager.core.modifier import Modifier
        Modifier(store).set_value("qubits.q1.T1", 2e-05)
        routes._chip_needs_generated_config(store)
        assert n["rows"] == 2

    def test_class_harvest_is_memoized_on_the_store(self, tmp_path, monkeypatch):
        from quam_state_manager.core import state_env_schema as ses
        store = QuamStore(_make_chip(tmp_path / "A"))
        n = {"walks": 0}
        real = ses.harvest_classes

        def counting(state, *a, **k):
            n["walks"] += 1
            return real(state, *a, **k)

        monkeypatch.setattr(ses, "harvest_classes", counting)
        assert ses._harvest_for_store(store) == ses._harvest_for_store(store)
        assert n["walks"] == 1
        from quam_state_manager.core.modifier import Modifier
        Modifier(store).set_value("qubits.q1.T1", 2e-05)
        ses._harvest_for_store(store)
        assert n["walks"] == 2

    def test_reopening_the_first_recent_writes_no_session_file(self, app, tmp_path, monkeypatch):
        c = app.test_client()
        a = _make_chip(tmp_path / "A")
        _load(c, a)
        writes = []
        real = routes._save_session
        monkeypatch.setattr(routes, "_save_session", lambda d: (writes.append(d), real(d)))
        _load(c, a)
        assert writes == []
        _load(c, _make_chip(tmp_path / "B"))
        assert len(writes) == 1

    def test_one_config_warm_at_a_time(self, tmp_path, monkeypatch):
        store = QuamStore(_make_chip(tmp_path / "A"))
        monkeypatch.setattr(routes.config_generator, "get_selected_env", lambda inst: "py")
        monkeypatch.setattr(routes, "_cfg_warm_inflight", {"some-other-chip"})
        ctx = {"store": store, "path": str(tmp_path / "A")}
        assert routes._warm_generated_config_async(ctx, str(tmp_path)) == "busy"


# ------------------------------------------------------------- global pin
def test_randomized_event_sequence_matches_a_cold_build(app, tmp_path):
    """500 steps of loads, edits, saves, undos, outside rewrites of working
    files (half of them same-size with the mtime restored) and late edits on
    evicted contexts. Every context the LRU builds or takes back from the
    park must be indistinguishable from a cold build of its working files."""
    rng = random.Random(20260926)
    c = app.test_client()
    chips = [_make_chip(tmp_path / f"R{i}", f01=6.1e9 + i * 1e8) for i in range(8)]
    seen: dict[str, dict] = {}          # fs key -> last context object served
    old_ctxs: list[dict] = []
    checked = 0
    parked_hits0 = chip_park.PARKED.hits
    for step in range(500):
        op = rng.choice(["load"] * 8 + ["edit", "edit", "save", "apply", "apply",
                                         "apply", "undo", "outside", "late"])
        active = routes._quam_cache or None
        if op == "load" or active is None:
            ch = rng.choice(chips)
            ctx = _load(c, ch)
            key = path_match.fs_key(ch.resolve())
            if seen.get(key) is not ctx:        # freshly built or taken back
                wf = Path(ctx["working_copy"].working_folder)
                cold = QuamStore(wf)
                with ctx["store"]._lock:
                    assert ctx["store"].state == cold.state, step
                    assert ctx["store"].wiring == cold.wiring, step
                wc = ctx["working_copy"]
                cold_dirty = (wc.synced_live_hash is not None and
                              working_copy.content_hash(cold.state, cold.wiring)
                              != wc.synced_live_hash)
                assert ctx["working_dirty"] == cold_dirty, step
                cold_idx = SearchIndex.build(cold.merged, wiring_keys=set(cold.wiring))
                for q in ("f_01", "t1", "q2", "amplitude", "6100000", "e-05"):
                    got = [(h.dot_path, h.value_str) for h in ctx["index"].search(q, limit=500)]
                    want = [(h.dot_path, h.value_str) for h in cold_idx.search(q, limit=500)]
                    assert got == want, (step, q)
                checked += 1
                if key in seen:
                    old_ctxs.append(seen[key])
            seen[key] = ctx
        elif op == "edit":
            q = rng.choice(["q1", "q2", "q3"])
            v = rng.choice(["1e-05", "2e-05", "3.5e-05"])
            c.post("/field/edit", data={"dot_path": f"qubits.{q}.T1", "value": v})
        elif op == "save":
            c.post("/save")
        elif op == "apply":                 # push to live: the context is clean again
            c.post("/state/apply-to-live")
        elif op == "undo":
            c.post("/undo")
        elif op == "outside":
            # SM's own sync/save is what rewrites a working folder; this is
            # the adversarial version, aimed at chips that may be parked.
            ch = rng.choice(chips)
            wc = working_copy.load(app.instance_path, ch.resolve())
            if wc is not None:
                wf = Path(wc.working_folder)
                raw = (wf / "state.json").read_text()
                import re
                m = re.search(r'"f_01": (\d)\.(\d)', raw)
                if m and rng.random() < 0.5:
                    d = str((int(m.group(2)) + 1) % 10)
                    _rewrite_same_size(wf, m.group(0), m.group(0)[:-1] + d)
                else:
                    st = json.loads(raw)
                    st["qubits"]["q3"]["T1"] = rng.choice([4e-05, 5e-05, 6e-05])
                    (wf / "state.json").write_text(json.dumps(st, indent=4))
        elif op == "late" and old_ctxs:
            late = rng.choice(old_ctxs)
            try:
                late["modifier"].set_value("qubits.q1.T1", rng.choice([8e-05, 9e-05]))
            except Exception:  # noqa: BLE001
                pass
    hits = chip_park.PARKED.hits - parked_hits0
    print(f"checked={checked} parked_hits={hits} cache={len(routes._quam_cache)} "
          f"dirty={sum(routes._quam_ctx_dirty(x) for x in routes._quam_cache.values())}")
    assert checked >= 25, checked
    assert hits >= 3, "the sequence barely took a parked chip back"


# ------------------------------------------------------------- memory pin
def _make_sized_chip(folder: Path, n: int, pad: int) -> Path:
    """A chip whose qubits each carry *pad* calibration floats, so three
    chips differ in size the way big30x / big20 / krs5 do (30 / 20 / 5)."""
    _make_chip(folder, n=n)
    st = json.loads((folder / "state.json").read_text())
    for i, q in enumerate(st["qubits"].values()):
        q["extras"] = {"cal": [1.0e-6 * (i + 1) + k * 1e-9 for k in range(pad)]}
    (folder / "state.json").write_text(json.dumps(st, indent=4))
    return folder


def test_ten_chip_switches_hold_memory_flat_and_debug_ram_adds_up(app, tmp_path):
    """RAM P10 memory pin. Three chips of 30 / 20 / 5 qubits, a context LRU
    of 2, so every switch evicts (parks) one chip and takes another back.

    Budget (stated): after the first round has opened all three (and built
    each one's search index -- a one-time cost, ~27x the file bytes on this
    padded fixture, measured), ten more
    switches may grow Python's traced heap by at most HALF the largest
    chip's file bytes, and by at most 2 KB per switch past the second.
    A switch that leaked one store would grow it by about
    one chip's bytes per switch (a parsed store is ~1.0x its bytes, see
    ParkedChip), i.e. ~10x the budget by the end. And at every step
    /debug/ram's total must equal the sum of its entries' sizes, and the
    parked memo must hold exactly what its ParkedChip entries say."""
    import gc
    import tracemalloc

    c = app.test_client()
    chips = [_make_sized_chip(tmp_path / "big30x", 30, 600),
             _make_sized_chip(tmp_path / "big20", 20, 600),
             _make_sized_chip(tmp_path / "krs5", 5, 600)]
    biggest = max((p / "state.json").stat().st_size + (p / "wiring.json").stat().st_size
                  for p in chips)
    budget = biggest // 2
    tracemalloc.start()
    try:
        for ch in chips:                         # round 1: everything built once,
            _load(c, ch)["index"].search("f_01", limit=5)   # search index included
        gc.collect()
        base = tracemalloc.get_traced_memory()[0]
        growth = []
        hits0 = chip_park.PARKED.hits
        for k in range(10):
            ctx = _load(c, chips[k % 3])
            ctx["index"].search("f_01", limit=5)  # use it: builds the lazy index
            gc.collect()
            growth.append(tracemalloc.get_traced_memory()[0] - base)
            snap = c.get("/debug/ram").get_json()
            assert snap["total_bytes"] == snap["entry_bytes_sum"], k
            parked = next(m for m in snap["memos"] if m["name"] == "quam_parked")
            assert parked["bytes"] == parked["entry_bytes_sum"], k
            assert parked["bytes"] == sum(
                e.value.ram_bytes() for e in chip_park.PARKED._entries.values()), k
            assert parked["entries"] <= len(chips), k
            assert len(routes._quam_cache) <= 2, k
    finally:
        tracemalloc.stop()
    print(f"biggest={biggest} budget={budget} growth={growth}")
    assert chip_park.PARKED.hits - hits0 >= 8, "switches did not take parked chips back"
    assert max(growth) <= budget, (growth, budget)
    # Slope after the one-time second-switch step: a steady per-switch leak
    # shows here long before it reaches the absolute budget. Measured after
    # the fix: ~0.5 KB/switch (stdlib allocator residue); a fresh ctypes
    # prototype per /debug/ram call measured ~8 KB/switch.
    slope = (growth[-1] - growth[1]) / (len(growth) - 2)
    assert slope <= 2048, (slope, growth)


# ------------------------------------------------------ search-index prewarm
def _wait(pred, timeout=20.0):
    import time
    t = time.monotonic()
    while time.monotonic() - t < timeout:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_opening_a_chip_prewarms_its_search_index_without_a_search(app, tmp_path):
    """Lazy alone moved a 2.9 s build (big30x, measured) onto the first
    keystroke; the open must now leave the index building in the background."""
    c = app.test_client()
    ctx = _load(c, _make_chip(tmp_path / "chipA", n=4))
    idx = ctx["index"]
    assert isinstance(idx, LazySearchIndex)
    assert _wait(lambda: idx.built), "the opened chip's index was never prewarmed"
    assert idx.builds == 1


def test_prewarm_builds_only_the_latest_of_a_burst(tmp_path):
    """One worker, one slot: A is in flight, B then C are submitted; B is
    overwritten by C and never built (a burst of switches builds one chip)."""
    from quam_state_manager.core import search_index as si
    stores = [QuamStore(_make_chip(tmp_path / n, n=2)) for n in ("A", "B", "C")]
    a, b, cc = (LazySearchIndex(s) for s in stores)
    a._build_lock.acquire()               # the worker will block inside a.get()
    try:
        a.prewarm()
        assert _wait(lambda: si._PREWARM_SLOT[0] is None), "worker never took A"
        b.prewarm()
        cc.prewarm()
    finally:
        a._build_lock.release()
    assert _wait(lambda: cc.built)
    assert a.built and not b.built
    assert b.search("q1", limit=5)        # still builds on demand
    assert b.builds == 1


def test_an_edit_racing_the_prewarm_matches_a_cold_build(tmp_path):
    """The prewarm is get() itself: an edit that lands while it builds (seq
    moves) must end with an index equal to a cold build of the edited store."""
    import threading
    store = QuamStore(_make_chip(tmp_path / "R", n=6))
    lazy = LazySearchIndex(store)
    real = SearchIndex.from_leaves
    entered, go = threading.Event(), threading.Event()

    def slow(leaves, keys):
        entered.set()
        go.wait(10)
        return real(leaves, keys)

    SearchIndex.from_leaves = staticmethod(slow)
    try:
        lazy.prewarm()
        assert entered.wait(10)
        with store._lock:                  # the edit path: mutate, bump seq, hook
            store.merged["qubits"]["q3"]["T1"] = "zz_edited_marker"
            store.mutation_seq += 1
        lazy.update_entry("qubits.q3.T1", "zz_edited_marker")
        go.set()
        assert _wait(lambda: lazy.built)
    finally:
        SearchIndex.from_leaves = real
    cold = SearchIndex.build(store.merged, wiring_keys=set(store.wiring.keys()))
    for q in ("zz_edited", "q3", "T1", "amplitude"):
        assert ([e.dot_path for e in lazy.search(q, limit=500)]
                == [e.dot_path for e in cold.search(q, limit=500)]), q
