"""core/trend_index -- Datasets > Trends from RAM (design ram_design.md §2b, P3).

The contract: a served payload ALWAYS equals a cold recompute (every index
rebuilt from the store's runs right now) -- across a new run, a vanished run,
a replaced run, an incomplete run completing, a tag write, a folder change.
Pinned three ways:

* event by event, with the path taken asserted (a new newest run must take
  the APPEND path, or the pin could not catch an append bug);
* a 200-step seeded random sequence of those events, under shadow mode
  (SM_RAM_VERIFY=1: every cache hit is re-verified cold as well);
* the key sweep: ``_key`` is monkeypatched to drop ONE named component at a
  time, and a scenario built for that component must then serve a stale
  payload -- proving no key component is vacuous (and, with the real keys,
  every scenario is clean).
"""
from __future__ import annotations

import calendar
import copy
import json
import math
import random
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from quam_state_manager.core import trend_index as ti
from quam_state_manager.core.dataset import DatasetStore, build_trend_data

A, B = "05_rabi", "07_ramsey"


def _run(root: Path, rid: int, exp: str = A, *, date: str = "2026-09-01",
         hhmmss: str | None = None, fit: dict | None = None, params: dict | None = None,
         figs=("figure",), data: str | None = None) -> Path:
    hhmmss = hhmmss or f"{rid % 24:02d}{rid % 60:02d}00"
    run = root / date / f"#{rid}_{exp}_{hhmmss}"
    run.mkdir(parents=True, exist_ok=True)
    fit = fit if fit is not None else {"q1": {"amp": 0.1 * rid, "ok": True, "success": True},
                                      "q2": {"amp": 0.2 + rid, "freq": 5e9 + rid}}
    qubits = sorted(fit)
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": exp, "status": "successful"},
        "data": {"parameters": {"model": {"qubits": qubits, **(params or {"shots": 100})}},
                 "outcomes": {}},
        "id": rid, "parents": []}), encoding="utf-8")
    body = {"fit_results": fit}
    for f in figs:
        body[f] = f"./{f}.png"
    (run / "data.json").write_text(data if data is not None else json.dumps(body), encoding="utf-8")
    return run


def _rescan(store: DatasetStore) -> None:
    store._last_mtime = (0.0, -2)          # the staleness gate, as a moved mtime opens it
    store.rescan_if_stale()


@pytest.fixture(autouse=True)
def _fresh_memos():
    for m in (ti.INDEX_MEMO, ti.SERIES_MEMO, ti.PARAMS_MEMO):
        m.clear()
    yield
    for m in (ti.INDEX_MEMO, ti.SERIES_MEMO, ti.PARAMS_MEMO):
        m.clear()


def served(sel, exp, qubit=None) -> dict:
    return json.loads(ti.series_blob(sel, exp, qubit).json_bytes())


def cold(sel, exp, qubit=None) -> dict:
    return json.loads(json.dumps(ti.cold_series_payload(sel, exp, qubit)))


def _render(d: dict) -> str:
    return json.dumps(d, sort_keys=True, default=repr)


def served_params(sel, exp, qubit=None, window="20") -> dict:
    return json.loads(ti.params_blob(sel, exp, qubit, window, _render).html)


def cold_params(sel, exp, qubit=None, window="20") -> dict:
    return json.loads(_render(ti.cold_param_diff_data(sel, exp, qubit, window)))


def mismatches(sel, exps=(A, B), qubits=(None, "q1", "q2"), windows=("20", "all")) -> list:
    bad = []
    for exp in exps:
        for q in qubits:
            if served(sel, exp, q) != cold(sel, exp, q):
                bad.append(("series", exp, q))
            for w in windows:
                if served_params(sel, exp, q, w) != cold_params(sel, exp, q, w):
                    bad.append(("params", exp, q, w))
    return bad


def _index(store, exp=A):
    return ti.INDEX_MEMO.peek(ti._key(store=store.instance_seq, index_exp=exp))[1]


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "data"
    for rid in range(1, 26):
        _run(r, rid, A if rid % 3 else B, date=f"2026-09-{1 + rid // 10:02d}",
             params={"shots": 100 if rid < 20 else 200, "span": 3})
    return r


# ---------------------------------------------------------------------------
# shape
# ---------------------------------------------------------------------------

class TestPayload:
    def test_runs_carry_the_real_instant_and_a_uid(self, root):
        store = DatasetStore(root)
        p = served([("kk", store)], A)
        rid, t, uid = p["runs"][0]
        r = store.runs[rid]
        want = calendar.timegm(datetime.strptime(f"{r.date} {r.time}", "%Y-%m-%d %H:%M:%S")
                               .timetuple()) * 1000
        assert t == want and uid == f"kk:{rid}"
        assert [x[0] for x in p["runs"]] == sorted(x[0] for x in p["runs"])
        assert p["n_runs"] == len(p["runs"]) == sum(1 for r in store.runs.values()
                                                     if r.experiment_name == A)

    def test_an_undated_run_is_counted_never_placed(self, root):
        _run(root, 40, A, date="2026-02-30")          # matches the date regex, is no date
        store = DatasetStore(root)
        p = served([("k", store)], A)
        row = next(x for x in p["runs"] if x[0] == 40)
        assert row[1] is None and p["undated"] == 1

    def test_figure_runs_list_only_runs_that_have_the_figure(self, root):
        _run(root, 41, A, figs=("figure", "extra"))
        store = DatasetStore(root)
        p = served([("k", store)], A)
        k = p["fig_keys"].index("extra")
        assert [p["runs"][i][0] for i in p["fig_runs"][k]] == [41]
        assert len(p["fig_runs"][p["fig_keys"].index("figure")]) == p["n_runs"]

    def test_parity_with_the_legacy_builder(self, root):
        """The same series, in the same order, with the same values as the
        builder the page used before (bools included, NaN as null)."""
        _run(root, 42, A, fit={"q1": {"amp": float("nan"), "flag": False},
                              "q3": {"amp": float("inf")}})
        store = DatasetStore(root)
        for q in (None, "q1", "q3"):
            runs = sorted((r for r in store.runs.values() if r.experiment_name == A
                           and (q is None or q in r.qubits)), key=lambda r: r.run_id)
            old = build_trend_data(runs, qubit=q, folder_key_of=lambda r: "k")
            new = served([("k", store)], A, q)
            assert [r["run_id"] for r in old["runs"]] == [r[0] for r in new["runs"]]
            assert [(s["qubit"], s["metric"]) for s in old["series"]] == \
                   [(s["q"], s["m"]) for s in new["series"]]
            assert [s["values"] for s in old["series"]] == [s["v"] for s in new["series"]]
            assert old["figure_keys"] == new["fig_keys"]
        p = served([("k", store)], A)
        assert {"q", "m", "v"} <= set(p["series"][0])
        amp3 = next(s for s in p["series"] if s["q"] == "q3")
        assert all(v is None for v in amp3["v"]), "an all-non-finite metric keeps its (empty) series"
        flag = next(s for s in p["series"] if s["m"] == "flag")
        assert any(v is False for v in flag["v"]), "a bool flag is still charted"
        assert all(s["m"] != "success" for s in p["series"])

    def test_the_payload_is_valid_json_without_nan(self, root):
        _run(root, 43, A, fit={"q1": {"amp": float("nan")}})
        store = DatasetStore(root)
        raw = ti.series_blob([("k", store)], A, None).json_bytes().decode()
        assert "NaN" not in raw and "Infinity" not in raw
        json.loads(raw)


# ---------------------------------------------------------------------------
# staleness, event by event
# ---------------------------------------------------------------------------

class TestStaleness:
    def test_a_new_newest_run_appends_and_equals_cold(self, root):
        store = DatasetStore(root)
        sel = [("k", store)]
        assert mismatches(sel) == []
        before = _index(store)
        frozen = {f: copy.deepcopy(getattr(before, f)) for f in ti.ExperimentTrend._FIELDS}
        _run(root, 50, A, date="2026-09-05",
             fit={"q1": {"amp": 5.0, "new_metric": 1.0}, "q2": {"amp": 6.0}})
        _rescan(store)
        assert mismatches(sel) == []
        idx = _index(store)
        assert idx is not before and idx.how == "append"
        assert before.max_run_id < 50 == idx.max_run_id
        # copy-on-write: a request still holding the old object reads it whole
        for f, v in frozen.items():
            assert getattr(before, f) == v, f"the old index's {f} was modified in place"

    def test_an_older_run_id_rebuilds(self, root):
        store = DatasetStore(root)
        sel = [("k", store)]
        served(sel, A)
        _run(root, 0, A, date="2026-09-01", hhmmss="000100")
        _rescan(store)
        assert mismatches(sel) == []
        assert _index(store).how == "build"

    def test_a_vanished_run_rebuilds(self, root):
        store = DatasetStore(root)
        sel = [("k", store)]
        served(sel, A)
        shutil.rmtree(store.runs[25].folder_path if store.runs[25].experiment_name == A
                      else store.runs[23].folder_path)
        _rescan(store)
        assert mismatches(sel) == []
        assert _index(store).how == "build"

    def test_a_replaced_run_rebuilds(self, root):
        store = DatasetStore(root)
        sel = [("k", store)]
        old = served(sel, A)
        r = store.runs[4]
        (r.folder_path / "data.json").write_text(json.dumps(
            {"fit_results": {"q1": {"amp": 123.0}}}), encoding="utf-8")
        store.force_rescan()
        assert served(sel, A) != old
        assert mismatches(sel) == []

    def test_an_incomplete_run_is_excluded_then_included(self, root):
        store = DatasetStore(root)
        sel = [("k", store)]
        served(sel, A)
        run = _run(root, 60, A, date="2026-09-05", data='{"fit_results": {"q1": {"amp"')
        _rescan(store)
        p = served(sel, A)
        assert store.runs[60].incomplete
        assert 60 not in [x[0] for x in p["runs"]] and p["incomplete"] == 1
        assert mismatches(sel) == []
        (run / "data.json").write_text(json.dumps({"fit_results": {"q1": {"amp": 6.0}}}),
                                       encoding="utf-8")
        _rescan(store)
        p = served(sel, A)
        assert 60 in [x[0] for x in p["runs"]] and p["incomplete"] == 0
        assert mismatches(sel) == []
        assert _index(store).how == "append", "completing the newest run is an append"

    def test_a_tag_write_changes_nothing_and_stays_cached(self, root):
        store = DatasetStore(root)
        sel = [("k", store)]
        first = served(sel, A)
        hits = ti.SERIES_MEMO.hits
        store.add_tag(4, "good")
        store.set_note(5, "a note")
        store.toggle_bookmark(7)
        assert served(sel, A) == first == cold(sel, A)
        assert ti.SERIES_MEMO.hits == hits + 1, "a tag write evicted the trend payload"

    def test_other_experiments_stay_cached_when_a_run_lands(self, root):
        store = DatasetStore(root)
        sel = [("k", store)]
        served(sel, B)
        _run(root, 70, A, date="2026-09-05")
        _rescan(store)
        hits = ti.SERIES_MEMO.hits
        served(sel, B)
        assert ti.SERIES_MEMO.hits == hits + 1
        assert mismatches(sel) == []

    def test_a_folder_change_and_a_same_chip_merge(self, root, tmp_path):
        other = tmp_path / "other"
        for rid in (4, 30, 31):                       # id 4 collides with the first folder
            _run(other, rid, A, date="2026-09-02", hhmmss=f"{rid:02d}3000")
        s1, s2 = DatasetStore(root), DatasetStore(other)
        for sel in ([("k1", s1)], [("k2", s2)], [("k1", s1), ("k2", s2)], [("k2", s2), ("k1", s1)]):
            assert mismatches(sel, exps=(A,)) == [], sel
        merged = served([("k1", s1), ("k2", s2)], A)
        keys = [(s1 if uid.startswith("k1") else s2).runs[rid] for rid, _t, uid in merged["runs"]]
        assert [(r.date, r.time, r.run_id) for r in keys] == \
               sorted((r.date, r.time, r.run_id) for r in keys)
        assert {uid for *_x, uid in merged["runs"]} >= {"k1:4", "k2:4"}


def test_a_200_step_random_sequence_under_shadow_mode(tmp_path, monkeypatch):
    """Every served payload equals a cold recompute after every step of a
    seeded random mix of all the events, with SM_RAM_VERIFY=1 re-checking
    every cache hit on top."""
    monkeypatch.setenv("SM_RAM_VERIFY", "1")
    rng = random.Random(20260925)
    r1, r2 = tmp_path / "d1", tmp_path / "d2"
    for rid in range(1, 8):
        _run(r1, rid, rng.choice((A, B)))
        _run(r2, rid + 100, A, date="2026-09-02")
    s1, s2 = DatasetStore(r1), DatasetStore(r2)
    next_id = [8]
    paths = {"append": 0, "build": 0}

    def new_run():
        rid = next_id[0]
        next_id[0] += 1
        _run(r1, rid, rng.choice((A, B)), date=rng.choice(("2026-09-03", "2026-09-04")),
             fit={"q1": {"amp": rng.random()}, rng.choice(("q2", "q3")): {"amp": rng.random()}})

    def low_run():
        rid = rng.randint(1, 7)
        if not any(p.name.startswith(f"#{rid}_") for p in r1.glob("*/*")):
            _run(r1, rid, rng.choice((A, B)), date="2026-09-01")

    def vanish():
        runs = sorted(r1.glob("*/*"))
        if len(runs) > 3:
            shutil.rmtree(rng.choice(runs))

    def replace():
        runs = sorted(r1.glob("*/*"))
        (rng.choice(runs) / "data.json").write_text(json.dumps(
            {"fit_results": {"q1": {"amp": rng.random()}}}), encoding="utf-8")
        s1.force_rescan()

    def incomplete():
        rid = next_id[0]
        next_id[0] += 1
        _run(r1, rid, A, date="2026-09-04", data='{"fit_resu')

    def complete():
        for p in r1.glob("*/*"):
            if (p / "data.json").read_text(encoding="utf-8").startswith('{"fit_resu'):
                (p / "data.json").write_text(json.dumps({"fit_results": {"q1": {"amp": 1.5}}}),
                                             encoding="utf-8")

    def tag():
        rid = rng.choice(sorted(s1.runs))
        rng.choice((lambda: s1.add_tag(rid, "t"), lambda: s1.set_note(rid, "n"),
                    lambda: s1.toggle_bookmark(rid)))()

    events = [new_run, new_run, new_run, low_run, vanish, replace, incomplete, complete, tag]
    for step in range(200):
        ev = rng.choice(events)
        ev()
        _rescan(s1)
        _rescan(s2)
        sel = rng.choice(([("k1", s1)], [("k1", s1), ("k2", s2)]))
        exp = rng.choice((A, B))
        q = rng.choice((None, "q1", "q3"))
        assert served(sel, exp, q) == cold(sel, exp, q), (step, ev.__name__, sel, exp, q)
        w = rng.choice(("20", "all"))
        assert served_params(sel, exp, q, w) == cold_params(sel, exp, q, w), (step, ev.__name__)
        idx = ti.INDEX_MEMO.peek(ti._key(store=s1.instance_seq, index_exp=exp))
        if idx is not None:
            paths[idx[1].how] += 1
    assert paths["append"] > 10 and paths["build"] > 10, paths


# ---------------------------------------------------------------------------
# the key sweep: no key component is vacuous
# ---------------------------------------------------------------------------

def _sc_new_run(tmp_path, monkeypatch) -> bool:
    root = tmp_path / "d"
    for rid in (1, 2, 3):
        _run(root, rid, A)
    store = DatasetStore(root)
    sel = [("k", store)]
    served(sel, A)
    _run(root, 9, A, date="2026-09-02")
    _rescan(store)
    return served(sel, A) != cold(sel, A)


def _sc_two_stores_same_generation(tmp_path, monkeypatch) -> bool:
    """Two stores (an evicted-and-recreated one, say) whose generations are
    equal while their runs differ -- only their identity tells them apart."""
    s = []
    for n, val in (("d1", 1.0), ("d2", 2.0)):
        _run(tmp_path / n, 1, A, fit={"q1": {"amp": val}})
        s.append(DatasetStore(tmp_path / n))
    assert s[0].exp_gen[A] == s[1].exp_gen[A]
    served([("k", s[0])], A)
    return served([("k", s[1])], A) != cold([("k", s[1])], A)


def _sc_cache_loaded_experiments(tmp_path, monkeypatch) -> bool:
    """A store loaded from the persisted cache gives every experiment the
    same generation -- only the experiment name tells them apart."""
    root = tmp_path / "d"
    _run(root, 1, A)
    _run(root, 2, B)
    cache = tmp_path / "inst" / "workspace_cache"
    cache.parent.mkdir(parents=True)
    assert DatasetStore(root, cache_dir=cache).flush_store_cache()
    store = DatasetStore(root, cache_dir=cache)
    assert store.exp_gen[A] == store.exp_gen[B]
    served([("k", store)], A)
    return served([("k", store)], B) != cold([("k", store)], B)


def _sc_same_store_two_keys(tmp_path, monkeypatch) -> bool:
    _run(tmp_path / "d", 1, A)
    store = DatasetStore(tmp_path / "d")
    served([("k1", store)], A)
    return served([("k2", store)], A) != cold([("k2", store)], A)


def _sc_qubit(tmp_path, monkeypatch) -> bool:
    _run(tmp_path / "d", 1, A)
    store = DatasetStore(tmp_path / "d")
    served([("k", store)], A, None)
    return served([("k", store)], A, "q1") != cold([("k", store)], A, "q1")


def _sc_window(tmp_path, monkeypatch) -> bool:
    for rid in range(1, 30):
        _run(tmp_path / "d", rid, A, params={"shots": rid})
    store = DatasetStore(tmp_path / "d")
    served_params([("k", store)], A, None, "20")
    return served_params([("k", store)], A, None, "all") != cold_params([("k", store)], A, None, "all")


def _sc_version(tmp_path, monkeypatch) -> bool:
    _run(tmp_path / "d", 1, A)
    store = DatasetStore(tmp_path / "d")
    served([("k", store)], A)
    real = ti.build_payload
    monkeypatch.setattr(ti, "PAYLOAD_VER", ti.PAYLOAD_VER + 1)
    monkeypatch.setattr(ti, "build_payload", lambda *a, **kw: {**real(*a, **kw), "new_field": 1})
    return served([("k", store)], A) != cold([("k", store)], A)


def _sc_truncated(tmp_path, monkeypatch) -> bool:
    _run(tmp_path / "d", 1, A)
    store = DatasetStore(tmp_path / "d")
    served([("k", store)], A)
    store._last_scan_truncated = True
    return served([("k", store)], A) != cold([("k", store)], A)


SWEEP = {
    # index memo
    "store": _sc_two_stores_same_generation,
    "index_exp": _sc_cache_loaded_experiments,
    "exp_gen": _sc_new_run,
    # series / params memos
    "folders": _sc_same_store_two_keys,
    "exp": _sc_cache_loaded_experiments,
    "qubit": _sc_qubit,
    "window": _sc_window,
    "ver": _sc_version,
    "stores": _sc_new_run,
    "seq": _sc_two_stores_same_generation,
    "gen": _sc_new_run,
    "truncated": _sc_truncated,
}


@pytest.mark.parametrize("name", sorted({f.__name__ for f in SWEEP.values()}))
def test_every_scenario_is_clean_with_the_real_keys(name, tmp_path, monkeypatch):
    fn = next(f for f in SWEEP.values() if f.__name__ == name)
    assert fn(tmp_path, monkeypatch) is False


@pytest.mark.parametrize("component", sorted(SWEEP))
def test_dropping_a_key_component_serves_a_stale_payload(component, tmp_path, monkeypatch):
    real = ti._key
    monkeypatch.setattr(ti, "_key", lambda **kw: real(**{k: v for k, v in kw.items()
                                                            if k != component}))
    assert SWEEP[component](tmp_path, monkeypatch) is True, \
        f"dropping {component!r} from the key went unnoticed -- a vacuous component or pin"


def test_the_sweep_covers_every_component_the_code_uses(tmp_path, monkeypatch):
    """A new key component must come with a sweep scenario."""
    seen: set[str] = set()
    real = ti._key

    def spy(**kw):
        seen.update(kw)
        return real(**kw)

    monkeypatch.setattr(ti, "_key", spy)
    _run(tmp_path / "d", 1, A)
    store = DatasetStore(tmp_path / "d")
    served([("k", store)], A)
    served_params([("k", store)], A)
    assert seen == set(SWEEP), seen ^ set(SWEEP)
