"""docs/296 review: the adversarial scenarios an independent review built,
each one a defect it reproduced and the fix closed -- kept as pins.

L: ledger reads (rename onto a removed id, two renames, ids with `_`, the old
chip open with or without the registry, an old run after the rebuild, a swap
whose values cross, a pair rebuilt reversed, a rebuild label never listed).
C/V: caches and version diffs of two folders of one chip in different eras.
D: Datasets writes (Apply selected values, the delta poll, another chip's runs).
"""
from __future__ import annotations

import json

import pytest

from quam_state_manager.core import hub, rename_lineage
from quam_state_manager.core.regen_merge import rebuilt_pairs, rename_plan, source_renames
from quam_state_manager.web import routes as routes_mod
from tests.test_rename_history import WIRING, history, make, state, values
from tests.test_rename_datasets import (NEW4, OLD1, OLD2, REC, chip_state, ctx_of,
                                        make_env, make_record, text)



@pytest.fixture(autouse=True)
def _inline():
    old = hub._PROJECTOR.inline
    hub.set_inline(True)
    yield
    hub.set_inline(old)


def full_record(old: dict, new: dict, sources: dict):
    """The record exactly as regenerate.run_regenerate builds it."""
    ren = source_renames(sources, old, new["qubits"])
    tok, pmap, ids = rename_plan(old, None, ren, new, None)
    return rename_lineage.new_record(
        renames=ren, tokens=tok, pairs=pmap, source_qubits=ids,
        source_pairs=list(old.get("qubit_pairs") or {}),
        qubits_after=list(new["qubits"]), pairs_after=rebuilt_pairs(new, None))


def ids(*qs):
    return {"qubits": {q: {"id": q} for q in qs}, "qubit_pairs": {}}


# ---------------------------------------------------------------------------
# L1: rename onto a removed id (q0 removed, q1 -> q0, q2 -> q1)
# ---------------------------------------------------------------------------
def test_L1_rename_onto_removed_id(tmp_path):
    rec = full_record(ids("q0", "q1", "q2"), ids("q0", "q1"), {"q0": "q1", "q1": "q2"})
    runs = [state({"q0": 4.0e9, "q1": 5.0e9, "q2": 6.0e9}),
            state({"q0": 5.0e9, "q1": 6.0e9}, [rec]),
            state({"q0": 5.1e9, "q1": 6.1e9}, [rec])]
    env = make(tmp_path, runs, state({"q0": 5.1e9, "q1": 6.1e9}, [rec]))
    got0 = values(history(env, "qubits.q0.f_01"))
    got1 = values(history(env, "qubits.q1.f_01"))
    print("L1 q0", got0, "q1", got1)
    assert 4.0e9 not in got0, "old q0 (removed qubit C) joined onto today's q0"
    assert got0 == [5.0e9, 5.1e9]
    assert got1 == [6.0e9, 6.1e9]


# ---------------------------------------------------------------------------
# L2: two renames in a row (shift, then swap)
# ---------------------------------------------------------------------------
def test_L2_two_renames(tmp_path):
    r1 = full_record(ids("q1", "q2"), ids("q0", "q1"), {"q0": "q1", "q1": "q2"})
    r2 = full_record(ids("q0", "q1"), ids("q0", "q1"), {"q0": "q1", "q1": "q0"})
    # A = 5.x, B = 6.x
    runs = [state({"q1": 5.0e9, "q2": 6.0e9}),
            state({"q0": 5.1e9, "q1": 6.1e9}, [r1]),
            state({"q0": 6.2e9, "q1": 5.2e9}, [r1, r2])]
    env = make(tmp_path, runs, state({"q0": 6.2e9, "q1": 5.2e9}, [r1, r2]))
    got0 = values(history(env, "qubits.q0.f_01"))   # today's q0 is B
    got1 = values(history(env, "qubits.q1.f_01"))   # today's q1 is A
    print("L2 q0", got0, "q1", got1)
    assert got0 == [6.0e9, 6.1e9, 6.2e9]
    assert got1 == [5.0e9, 5.1e9, 5.2e9]


# ---------------------------------------------------------------------------
# L4: ids containing "_" (q1_b stays, q1 -> q0)
# ---------------------------------------------------------------------------
def test_L4_underscore_ids(tmp_path):
    rec = full_record(ids("q1", "q1_b"), ids("q0", "q1_b"), {"q0": "q1", "q1_b": "q1_b"})
    runs = [state({"q1": 5.0e9, "q1_b": 6.0e9}),
            state({"q0": 5.1e9, "q1_b": 6.1e9}, [rec])]
    env = make(tmp_path, runs, state({"q0": 5.1e9, "q1_b": 6.1e9}, [rec]))
    a, b = values(history(env, "qubits.q0.f_01")), values(history(env, "qubits.q1_b.f_01"))
    print("L4 q0", a, "q1_b", b)
    assert a == [5.0e9, 5.1e9] and b == [6.0e9, 6.1e9]


# ---------------------------------------------------------------------------
# L5: the chip is open in the OLD era (the source folder, still open after the
# rebuild, or another SM instance) and no registry has seen the record yet;
# runs of the renamed chip already landed in the shared ledger
# ---------------------------------------------------------------------------
def test_L5_old_era_open_without_registry(tmp_path):
    rec = full_record(ids("q1", "q2"), ids("q0", "q1"), {"q0": "q1", "q1": "q2"})
    runs = [state({"q1": 5.0e9, "q2": 6.0e9}),
            state({"q0": 5.1e9, "q1": 6.1e9}, [rec]),       # renamed chip's run
            state({"q0": 5.2e9, "q1": 6.2e9}, [rec])]
    env = make(tmp_path, runs, state({"q1": 5.0e9, "q2": 6.0e9}))   # the OLD chip is open
    got = values(history(env, "qubits.q1.f_01"))    # the old q1 is qubit A (5.x)
    print("L5 q1", got)
    assert not any(6.0e9 <= v < 7.0e9 for v in got), \
        f"qubit B's values (renamed chip's q1) joined onto the old chip's q1: {got}"


# ---------------------------------------------------------------------------
# L6: the same, but the registry DOES know the record (renamed chip opened
# once) -- the old chip open in era () must read era-R positions backwards
# ---------------------------------------------------------------------------
def test_L6_old_era_open_with_registry(tmp_path):
    rec = full_record(ids("q1", "q2"), ids("q0", "q1"), {"q0": "q1", "q1": "q2"})
    runs = [state({"q1": 5.0e9, "q2": 6.0e9}),
            state({"q0": 5.1e9, "q1": 6.1e9}, [rec]),
            state({"q0": 5.2e9, "q1": 6.2e9}, [rec])]
    env = make(tmp_path, runs, state({"q1": 5.0e9, "q2": 6.0e9}))
    from quam_state_manager.web import routes as routes_mod
    with env["app"].app_context():
        ctx = routes_mod._active_ctx()
        chip_dir = routes_mod._hub_chip_dir(ctx["path"])
    rename_lineage.remember(chip_dir, [rec])
    got = values(history(env, "qubits.q1.f_01"))
    print("L6 q1", got)
    assert got == [5.0e9, 5.1e9, 5.2e9], got


# ---------------------------------------------------------------------------
# M1: an old run executed after the rebuild -- how many "rename" marks, and
# what they claim (the drawer and the Chip Status Trends marks)
# ---------------------------------------------------------------------------
def test_M1_marks_for_an_old_run_after_the_rebuild(tmp_path):
    rec = full_record(ids("q1", "q2"), ids("q0", "q1"), {"q0": "q1", "q1": "q2"})
    runs = [state({"q1": 5.0e9, "q2": 6.0e9}),
            state({"q0": 5.1e9, "q1": 6.1e9}, [rec]),
            state({"q1": 5.3e9, "q2": 6.3e9}),            # the OLD chip used once more
            state({"q0": 5.4e9, "q1": 6.4e9}, [rec])]
    env = make(tmp_path, runs, state({"q0": 5.4e9, "q1": 6.4e9}, [rec]))
    marks = history(env, "qubits.q1.f_01")["rows"]["k"]["renames"]
    print("M1 drawer marks:", [(m["was"], m["now"], m["renames"]) for m in marks])
    html = env["client"].get("/field/history", query_string={"path": "qubits.q1.f_01"}).data.decode()
    import re
    txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    print("M1 drawer text count 'Renamed by Re-generate':", txt.count("Renamed by Re-generate"))
    t = env["client"].get("/topology/trends?metric=f_01").data.decode()
    m = re.search(r'id="topo-trends-renames">(.*?)</script>', t, re.S)
    print("M1 chip status marks:", m.group(1) if m else None)
    assert len(marks) == 1, "one Re-generate rename, but the drawer marks %d" % len(marks)


# ---------------------------------------------------------------------------
# E1: the event where a swap came into force, with the values crossing --
# each physical qubit's value changed, neither raw name has a row
# ---------------------------------------------------------------------------
def test_E1_swap_with_crossing_values_event_rows(tmp_path):
    from tests.test_rename_history import _timeline
    rec = full_record(ids("q1", "q2"), ids("q1", "q2"), {"q1": "q2", "q2": "q1"})
    runs = [state({"q1": 5.0e9, "q2": 6.0e9}),
            state({"q1": 5.0e9, "q2": 6.0e9}, [rec])]   # A (now q2) 5.0 -> 6.0, B (now q1) 6.0 -> 5.0
    env = make(tmp_path, runs, state({"q1": 5.0e9, "q2": 6.0e9}, [rec]))
    page = _timeline(env)
    ev = next(e for e in page["events"] if e["run_id"] == 2)
    rows = [(c["path"], c["old"], c["new"]) for c in ev["changes"] if c["path"].endswith("f_01")]
    print("E1 rename event f_01 rows:", rows)
    drawer = values(history(env, "qubits.q1.f_01"))
    print("E1 drawer q1 (B):", drawer)
    assert rows, "the event list says nothing of q1/q2 f_01 changed, while the drawer shows a change"


# ---------------------------------------------------------------------------
# P1: a pair rebuilt REVERSED under the same id (swap q1 <-> q2; the rebuilt
# pair "q1-q2" is control q1 = old q2, target q2 = old q1)
# ---------------------------------------------------------------------------
def _with_pair(st, pid, c, t, amp):
    st = json.loads(json.dumps(st))
    st["qubit_pairs"][pid] = {"qubit_control": f"#/qubits/{c}", "qubit_target": f"#/qubits/{t}",
                              "coupler_amp": amp}
    return st


def test_P1_pair_rebuilt_reversed_keeps_no_history(tmp_path):
    old = _with_pair(ids("q1", "q2"), "q1-q2", "q1", "q2", 0.1)
    new = _with_pair(ids("q1", "q2"), "q1-q2", "q1", "q2", 0.2)
    rec = full_record(old, new, {"q1": "q2", "q2": "q1"})
    print("P1 pair_map:", rec["pair_map"], "pairs:", rec["pairs"])
    runs = [_with_pair(state({"q1": 5.0e9, "q2": 6.0e9}), "q1-q2", "q1", "q2", 0.1),
            _with_pair(state({"q1": 6.0e9, "q2": 5.0e9}, [rec]), "q1-q2", "q1", "q2", 0.2)]
    env = make(tmp_path, runs, runs[-1])
    got = values(history(env, "qubit_pairs.q1-q2.coupler_amp"))
    print("P1 history of today's q1-q2.coupler_amp:", got)
    assert 0.1 not in got, "the old (reversed) pair's value joined onto the rebuilt pair"


def test_S1_chip_status_lists_a_removed_qubits_label(tmp_path):
    """Rename onto a removed id: the old q0 (qubit C, removed) has no name
    today. Does Chip Status list a path for it (a rebuild label)?"""
    from quam_state_manager.web import routes as routes_mod
    rec = full_record(ids("q0", "q1", "q2"), ids("q0", "q1"), {"q0": "q1", "q1": "q2"})
    runs = [state({"q0": 4.0e9, "q1": 5.0e9, "q2": 6.0e9}),
            state({"q0": 5.0e9, "q1": 6.0e9}, [rec])]
    env = make(tmp_path, runs, state({"q0": 5.0e9, "q1": 6.0e9}, [rec]))
    with env["app"].app_context():
        ans, table = routes_mod._hub_status_table(routes_mod._active_ctx())
        got = table.leaf_matching_paths("qubits.*.f_01")
    print("S1 chip status f_01 paths:", got)
    assert not any("_removed" in p or "_stale" in p for p in got), got





def _series(env, path):
    with env["app"].app_context():
        ctx = routes_mod._active_ctx()
        ans, table = routes_mod._hub_status_table(ctx)
        assert table is not None, ans.get("mode")
        got = table.series_many([path])
        return ctx["path"], ctx["store"].mutation_seq, [p["value"] for p in got["rows"][path]["points"]]


def test_C1_chip_status_series_cache_crosses_eras(tmp_path):
    rec = full_record(ids("q1", "q2"), ids("q0", "q1"), {"q0": "q1", "q1": "q2"})
    runs = [state({"q1": 5.0e9, "q2": 6.0e9}),
            state({"q0": 5.1e9, "q1": 6.1e9}, [rec]),
            state({"q0": 5.2e9, "q1": 6.2e9}, [rec])]
    env = make(tmp_path, runs, state({"q0": 5.2e9, "q1": 6.2e9}, [rec]))
    # the source folder of the rebuild: same chip, era (), same data folder
    old_dir = tmp_path / "chips" / "source"
    old_dir.mkdir(parents=True)
    old = state({"q1": 5.0e9, "q2": 6.0e9})
    old["extras"]["data_folder"] = str(env["data"])
    (old_dir / "state.json").write_text(json.dumps(old), encoding="utf-8")
    (old_dir / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")

    a = _series(env, "qubits.q1.f_01")           # renamed chip: q1 is qubit B
    print("renamed chip open:", a)
    assert env["client"].post("/load", data={"folder": str(old_dir)}).status_code in (200, 302)
    with env["app"].app_context():
        print("chip dirs:", routes_mod._hub_chip_dir(env["live"]), routes_mod._hub_chip_dir(old_dir))
    b = _series(env, "qubits.q1.f_01")           # old chip: q1 is qubit A
    print("source chip open:", b)
    # ground truth for the source chip's q1 (qubit A) read fresh, no cache
    with env["app"].app_context():
        fresh = routes_mod._value_history(routes_mod._active_ctx(), {"k": "qubits.q1.f_01"})
    truth = [p["value"] for p in fresh["rows"]["k"]["points"]]
    print("fresh read for source chip:", truth)
    assert b[2] == truth, f"Chip Status served the renamed chip's q1 (qubit B) for the source chip's q1: {b[2]} vs {truth}"


def _ref_of_run(env, rid):
    from quam_state_manager.core import hub_query, hub_versions
    with env["app"].app_context():
        ctx = routes_mod._active_ctx()
        chip_dir = routes_mod._hub_chip_dir(ctx["path"])
        page = hub_query.timeline(routes_mod._vh_binding(ctx, chip_dir), limit=50)
    ev = next(e for e in page["events"] if e["run_id"] == rid)
    return hub_versions.ref_of(ev)


def test_V1_version_diff_of_a_newer_era_version_with_the_source_folder_open(tmp_path):
    """The source folder (era ()) is open; a version saved after the rename is
    diffed against now. q1 in that version is qubit B, q1 now is qubit A."""
    rec = full_record(ids("q1", "q2"), ids("q0", "q1"), {"q0": "q1", "q1": "q2"})
    runs = [state({"q1": 5.0e9, "q2": 6.0e9}),
            state({"q0": 5.1e9, "q1": 6.1e9}, [rec]),
            state({"q0": 5.2e9, "q1": 6.2e9}, [rec])]
    env = make(tmp_path, runs, state({"q0": 5.2e9, "q1": 6.2e9}, [rec]))
    old_dir = tmp_path / "chips" / "source"
    old_dir.mkdir(parents=True)
    old = state({"q1": 5.0e9, "q2": 6.0e9})
    old["extras"]["data_folder"] = str(env["data"])
    (old_dir / "state.json").write_text(json.dumps(old), encoding="utf-8")
    (old_dir / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
    assert env["client"].post("/load", data={"folder": str(old_dir)}).status_code in (200, 302)
    ref = _ref_of_run(env, 3)
    with env["app"].app_context():
        entries = routes_mod._version_diff_now(routes_mod._active_ctx(), ref)
    got = {e.dot_path: (e.old_value, e.new_value) for e in entries if e.dot_path.endswith("f_01")}
    print("V1 version(R) vs now(source):", got)
    # qubit A: 5.2 in the version (as q0), 5.0 now (as q1); qubit B: 6.2 (as q1) / 6.0 (as q2)
    assert got.get("qubits.q1.f_01") != (6.2e9, 5.0e9), \
        "the diff pairs the version's q1 (qubit B) with today's q1 (qubit A)"


def test_C2_versions_quick_diff_cache_crosses_eras(tmp_path):
    """_VERSION_QUICK is keyed (chip dir, ref a, ref b); _version_side depends on
    the OPEN chip's era. Two folders of one chip share the chip dir."""
    rec = full_record(ids("q1", "q2"), ids("q0", "q1"), {"q0": "q1", "q1": "q2"})
    runs = [state({"q1": 5.0e9, "q2": 6.0e9}),
            state({"q1": 5.1e9, "q2": 6.1e9}),
            state({"q0": 5.2e9, "q1": 6.2e9}, [rec])]
    env = make(tmp_path, runs, state({"q0": 5.2e9, "q1": 6.2e9}, [rec]))
    old_dir = tmp_path / "chips" / "source"
    old_dir.mkdir(parents=True)
    old = state({"q1": 5.1e9, "q2": 6.1e9})
    old["extras"]["data_folder"] = str(env["data"])
    (old_dir / "state.json").write_text(json.dumps(old), encoding="utf-8")
    (old_dir / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
    r1, r2 = _ref_of_run(env, 1), _ref_of_run(env, 2)
    assert env["client"].post("/load", data={"folder": str(old_dir)}).status_code in (200, 302)
    with env["app"].app_context():
        a = routes_mod._version_quick_entries(routes_mod._active_ctx()["path"], r1, r2)
    print("C2 source open:", sorted((e.dot_path, e.old_value, e.new_value) for e in a))
    assert env["client"].post("/load", data={"folder": str(env["live"])}).status_code in (200, 302)
    with env["app"].app_context():
        b = routes_mod._version_quick_entries(routes_mod._active_ctx()["path"], r1, r2)
        routes_mod._VERSION_QUICK.clear()
        fresh = routes_mod._version_quick_entries(routes_mod._active_ctx()["path"], r1, r2)
    print("C2 renamed open (cached):", sorted((e.dot_path, e.old_value, e.new_value) for e in b))
    print("C2 renamed open (fresh):", sorted((e.dot_path, e.old_value, e.new_value) for e in fresh))
    assert sorted(e.dot_path for e in b) == sorted(e.dot_path for e in fresh)





def _pick(env, rid, paths, which="state"):
    r = env["client"].post(f"/dataset/{env['uid'](rid)}/apply-selected/preview",
                           json={"file": which, "paths": paths})
    assert r.status_code == 200, r.data[:400]
    d = r.get_json()
    return d, {row["path"]: row for row in d["rows"]}


@pytest.fixture
def shifted(tmp_path):
    runs = [(OLD1, {"q1": {"frequency": 5.01e9}, "q2": {"frequency": 6.01e9}}),
            (OLD2, {"q1": {"frequency": 5.11e9}, "q2": {"frequency": 6.11e9}}),
            (NEW4, {"q0": {"frequency": 5.21e9}, "q1": {"frequency": 6.21e9}})]
    return make_env(tmp_path, runs, NEW4)


def test_D1_apply_selected_stages_an_old_id_string_onto_todays_qubit(shifted):
    """Pick run #1's q2 subtree leaf `id` (the string "q2"): today's q1 is that
    qubit, and its id is "q1". The preview translates the PATH, not the value."""
    d, rows = _pick(shifted, 1, ["qubits.q2.id"])
    print("D1 rows:", rows)
    row = rows.get("qubits.q1.id")
    assert row is not None
    assert not (row["status"] in ("change", "new") and row["new"] == "q2"), \
        "Apply selected would write today's q1.id = 'q2' (the name of no qubit / another qubit)"


def test_D2_apply_selected_old_name_inside_a_value(tmp_path):
    """A name-valued, editable leaf (a channel's thread names its qubit): the
    run's q2 is today's q1; the run's value "q2" must not land on today's q1."""
    def with_thread(st):
        st = json.loads(json.dumps(st))
        for q in st["qubits"]:
            st["qubits"][q]["xy"]["thread"] = q
        return st
    old, new = with_thread(OLD1), with_thread(NEW4)
    env = make_env(tmp_path, [(old, {"q1": {"frequency": 5.01e9}})], new)
    d, rows = _pick(env, 1, ["qubits.q2.xy.thread"])
    print("D2 rows:", rows)
    row = rows.get("qubits.q1.xy.thread")
    assert not (row and row["status"] in ("change", "new") and row["new"] == "q2"),         "Apply selected would set today's q1 thread to 'q2' (the run's old name of this same qubit)"


def test_D3_delta_poll_ships_an_old_run_without_todays_names(shifted):
    """The table payload gives run #1 qn (today's names); the 15 s delta poll
    re-ships the same run (its data.json was rewritten) through _compact_row
    alone -- the client replaces the whole row, so qn is gone."""
    import re, time
    html = shifted["client"].get("/datasets").data.decode()
    m = re.search(r'"rows"\s*:\s*(\[.*?\])\s*,\s*"', html, re.S) or re.search(r'(\[\{"id".*?\}\])', html, re.S)
    rows = {r["id"]: r for r in json.loads(m.group(1))}
    print("D3 table row #1:", {k: rows[1].get(k) for k in ("q", "qn")})
    ts = time.time()
    time.sleep(0.05)
    dj = shifted["folders"][0] / "data.json"
    dj.write_text(json.dumps({"fit_results": {"q1": {"frequency": 5.02e9}, "q2": {"frequency": 6.02e9}}}),
                  encoding="utf-8")
    shifted["client"].post("/datasets/rescan")
    r = shifted["client"].get(f"/datasets/changes-since?ts={ts}")
    upd = [u for u in r.get_json()["updated"] if u["id"] == 1]
    print("D3 delta row #1:", [{k: u.get(k) for k in ("q", "qn")} for u in upd])
    assert upd, "the poll did not re-ship run #1 (nothing to judge)"
    assert upd[0].get("qn") == ["q0", "q1"], "the delta row lost today's names"


def test_D4_another_chips_run_is_translated_by_this_chips_rename(tmp_path):
    """Chip Y (another chip: other chip_name and network) never renamed; its
    run sits in the open chip X's data folder. X was renamed q1->q0, q2->q1.
    The run detail must not say Y's q1 is 'now q0' nor target X's q0."""
    import re
    other = chip_state({"q1": 5.5e9, "q2": 6.5e9})
    other["extras"]["chip_name"] = "other-device"
    env = make_env(tmp_path, [(OLD1, {"q1": {"frequency": 5.01e9}}),
                              (other, {"q1": {"frequency": 5.51e9}, "q2": {"frequency": 6.51e9}})], NEW4)
    wj = env["folders"][1] / "quam_state" / "wiring.json"
    wj.write_text(json.dumps({"network": {"host": "10.9.9.9", "cluster_name": "OTHER"}}), encoding="utf-8")
    r = env["client"].get(f"/dataset/{env['uid'](2)}", headers={"HX-Request": "true"})
    html = r.data.decode()
    paths = re.findall(r'data-fit-path="([^"]+)"', html)
    notes = re.findall(r'class="muted fit-renamed"[^>]*>\(([^)]*)\)', html)
    refused = re.findall(r'fit-refused muted">([^<]*)<', html)
    print("D4 other chip's run #2: fit paths", paths, "notes", notes, "refused", refused[:1])
    _d, rows = _pick(env, 2, ["qubits.q1.f_01"])
    print("D4 apply-selected rows:", {k: (v.get("from"), v.get("status"), v.get("reason")) for k, v in rows.items()})
    assert not notes and "qubits.q0.f_01" not in paths, \
        "another chip's run is translated by the open chip's rename"


def test_D5_another_chips_run_staged_through_this_chips_rename(tmp_path):
    other = chip_state({"q1": 5.5e9, "q2": 6.5e9})
    other["extras"]["chip_name"] = "other-device"
    env = make_env(tmp_path, [(OLD1, {"q1": {"frequency": 5.01e9}}),
                              (other, {"q1": {"frequency": 5.51e9}})], NEW4)
    wj = env["folders"][1] / "quam_state" / "wiring.json"
    wj.write_text(json.dumps({"network": {"host": "10.9.9.9", "cluster_name": "OTHER"}}), encoding="utf-8")
    r = env["client"].post(f"/dataset/{env['uid'](2)}/load-state?force_chip=1")
    merged = ctx_of(env)["store"].merged
    print("D5 status", r.status_code, "msg:", text(r.data.decode())[:300])
    print("D5 staged qubits:", sorted(merged["qubits"]), "era:", rename_lineage.era(merged),
          "chip_name:", merged.get("extras", {}).get("chip_name"))
    assert sorted(merged["qubits"]) == ["q1", "q2"], \
        "another chip's state was re-keyed through the open chip's rename"


def test_D2b_swap_apply_selected_points_a_field_at_the_other_qubit(tmp_path):
    """Swap q1 <-> q2. The run's q1 (qubit A) is today's q2; its thread "q1"
    staged as-is onto today's q2 names today's q1 -- qubit B."""
    rec = make_record(["q1", "q2"], ["q1", "q2"], {"q1": "q2", "q2": "q1"})

    def with_thread(st):
        st = json.loads(json.dumps(st))
        for q in st["qubits"]:
            st["qubits"][q]["xy"]["thread"] = q
        return st
    old = with_thread(chip_state({"q1": 5.0e9, "q2": 6.0e9}))
    new = with_thread(chip_state({"q1": 6.0e9, "q2": 5.0e9}, [rec]))
    env = make_env(tmp_path, [(old, {"q1": {"frequency": 5.01e9}})], new)
    _d, rows = _pick(env, 1, ["qubits.q1.xy.thread"])
    print("D2b rows:", rows)
    row = rows.get("qubits.q2.xy.thread")
    assert not (row and row["status"] == "change" and row["new"] == "q1"), \
        "today's q2 (qubit A) would get thread 'q1' -- the name of qubit B today"
