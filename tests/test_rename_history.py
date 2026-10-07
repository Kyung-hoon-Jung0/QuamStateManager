"""docs/296: after a Re-generate rename, every ledger history follows each
PHYSICAL qubit -- never two qubits joined under one name.

The archive: runs #1-#2 on the chip as it was (q1, q2 on lines 1, 2); the
chip is rebuilt with q1 -> q0, q2 -> q1 (the record goes into extras); runs
#3-#4 on the renamed chip. The live chip is the renamed one.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quam_state_manager.core import hub, rename_lineage, value_history as vh
from quam_state_manager.core.regen_merge import rebuilt_pairs, rename_plan, source_renames
from quam_state_manager.web import routes as routes_mod

T0 = int(datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc).timestamp() * 1_000_000)
WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}


@pytest.fixture(autouse=True)
def _inline():
    old = hub._PROJECTOR.inline
    hub.set_inline(True)
    yield
    hub.set_inline(old)


def state(f01: dict, chain=(), extra_t1=None):
    qs = {}
    for q, f in f01.items():
        qs[q] = {"id": q, "f_01": f, "T1": (extra_t1 or {}).get(q, 1e-5),
                 "xy": {"operations": {"x180": "#./x180_DragCosine",
                                       "x180_DragCosine": {"amplitude": f / 5e10, "length": 40}}}}
    s = {"qubits": qs, "qubit_pairs": {}, "extras": {"chip_name": "device"}}
    if chain:
        s["extras"]["qubit_renames"] = list(chain)
    return s


def record(old_ids, new_ids, sources):
    old = {"qubits": {q: {"id": q} for q in old_ids}, "qubit_pairs": {}}
    new = {"qubits": {q: {"id": q} for q in new_ids}, "qubit_pairs": {}}
    ren = source_renames(sources, old, new["qubits"])
    tok, pmap, ids = rename_plan(old, None, ren, new, None)
    return rename_lineage.new_record(renames=ren, tokens=tok, pairs=pmap, source_qubits=ids,
                                     source_pairs=[], qubits_after=list(new_ids),
                                     pairs_after=rebuilt_pairs(new, None))


REC = record(["q1", "q2"], ["q0", "q1"], {"q0": "q1", "q1": "q2"})


def run(root: Path, rid: int, st: dict, name="scan") -> Path:
    t_us = T0 + rid * 10_000_000
    hhmmss = datetime.fromtimestamp(t_us / 1e6, tz=timezone.utc).strftime("%H%M%S")
    folder = root / "2026-01-01" / f"#{rid}_{name}_{hhmmss}"
    (folder / "quam_state").mkdir(parents=True, exist_ok=True)
    qubits = sorted(st["qubits"])
    body = {"created_at": datetime.fromtimestamp(t_us / 1e6, tz=timezone.utc).isoformat(),
            "metadata": {"status": "finished", "name": name}, "id": rid,
            "data": {"parameters": {"model": {"qubits": qubits}}}}
    (folder / "node.json").write_text(json.dumps(body), encoding="utf-8")
    (folder / "quam_state" / "state.json").write_text(json.dumps(st), encoding="utf-8")
    (folder / "quam_state" / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
    return folder


def make(tmp_path, runs: list[dict], live: dict):
    data, live_dir = tmp_path / "data", tmp_path / "chips" / "live"
    for i, st in enumerate(runs, 1):
        run(data, i, st)
    live_dir.mkdir(parents=True)
    live = json.loads(json.dumps(live))
    live["extras"]["data_folder"] = str(data)
    (live_dir / "state.json").write_text(json.dumps(live), encoding="utf-8")
    (live_dir / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
    from quam_state_manager.web.app import create_app
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    app.config["HUB_SYNC_ON_OPEN"] = True
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live_dir)}).status_code in (200, 302)
    return {"app": app, "client": c, "data": data, "live": live_dir}


def history(env, path):
    with env["app"].test_request_context():
        from flask import session  # noqa: F401
    with env["app"].app_context():
        ctx = routes_mod._active_ctx()
        return routes_mod._value_history(ctx, {"k": path})


def values(ans):
    return [p["value"] for p in ans["rows"]["k"]["points"]]


A = {"q1": 5.0e9, "q2": 6.0e9}           # before: q1 is qubit A, q2 is qubit B


@pytest.fixture
def shifted(tmp_path):
    runs = [state({"q1": 5.0e9, "q2": 6.0e9}),
            state({"q1": 5.1e9, "q2": 6.1e9}),
            state({"q0": 5.1e9, "q1": 6.1e9}, [REC]),      # the rebuild carried both
            state({"q0": 5.2e9, "q1": 6.2e9}, [REC])]
    return make(tmp_path, runs, state({"q0": 5.2e9, "q1": 6.2e9}, [REC]))


class TestOneQubitOneHistory:
    def test_each_renamed_qubit_reads_its_own_values_across_the_rename(self, shifted):
        ans = history(shifted, "qubits.q1.f_01")
        assert ans["mode"] == "ledger", ans.get("reason")
        assert values(ans) == [6.0e9, 6.1e9, 6.2e9], "q1 now is the qubit that was q2"
        ans = history(shifted, "qubits.q0.f_01")
        assert values(ans) == [5.0e9, 5.1e9, 5.2e9], "q0 now is the qubit that was q1"

    def test_the_rename_point_is_marked_with_its_names(self, shifted):
        marks = history(shifted, "qubits.q1.f_01")["rows"]["k"]["renames"]
        assert len(marks) == 1
        m = marks[0]
        assert (m["was"], m["now"]) == ("qubits.q2.f_01", "qubits.q1.f_01")
        assert m["renames"][0]["qubits"] == {"q1": "q0", "q2": "q1"}

    def test_the_effective_series_follows_the_qubit_through_an_alias(self, shifted):
        ans = history(shifted, "qubits.q1.xy.operations.x180.amplitude")
        eff = [p["value"] for p in ans["rows"]["k"]["effective"]]
        assert eff == [6.0e9 / 5e10, 6.1e9 / 5e10, 6.2e9 / 5e10]

    def test_column_history_by_run_reads_each_run_in_its_own_names(self, shifted):
        with shifted["app"].app_context():
            ans = routes_mod._value_history(routes_mod._active_ctx(),
                                            {"q0": "qubits.q0.f_01", "q1": "qubits.q1.f_01"}, runs=4)
        by_run = {r["run_id"]: r["values"] for r in ans["runs"]}
        assert by_run[1] == {"q0": 5.0e9, "q1": 6.0e9}
        assert by_run[4] == {"q0": 5.2e9, "q1": 6.2e9}
        assert not any("chip_uncertain" in r["flags"] for r in ans["runs"]), \
            "a renamed chip is the same chip"


def test_a_value_the_rebuild_changed_is_a_change_of_that_qubit(tmp_path):
    # the rebuild set the old q2's (now q1's) value to exactly the old q1's:
    # the raw ledger has no row on qubits.q1.f_01 there at all
    runs = [state({"q1": 5.0e9, "q2": 6.0e9}),
            state({"q0": 5.0e9, "q1": 5.0e9}, [REC])]
    env = make(tmp_path, runs, state({"q0": 5.0e9, "q1": 5.0e9}, [REC]))
    pts = history(env, "qubits.q1.f_01")["rows"]["k"]["points"]
    assert [(p["value"], p["op"]) for p in pts] == [(6.0e9, "add"), (5.0e9, "set")]
    assert pts[1]["old"] == 6.0e9


def test_a_qubit_new_in_the_rebuild_has_no_history_before_it(tmp_path):
    rec = record(["q1", "q2"], ["q0", "q1", "q2"], {"q0": "q1"})   # q1 is brand new
    runs = [state({"q1": 5.0e9, "q2": 6.0e9}),
            state({"q0": 5.0e9, "q1": 7.0e9, "q2": 6.0e9}, [rec])]
    env = make(tmp_path, runs, state({"q0": 5.0e9, "q1": 7.0e9, "q2": 6.0e9}, [rec]))
    assert values(history(env, "qubits.q1.f_01")) == [7.0e9]
    assert values(history(env, "qubits.q2.f_01")) == [6.0e9]
    assert values(history(env, "qubits.q0.f_01")) == [5.0e9]


def test_an_old_run_after_the_rebuild_is_read_in_its_own_names(tmp_path):
    # the old chip was used once more after the rebuild was adopted
    runs = [state({"q1": 5.0e9, "q2": 6.0e9}),
            state({"q0": 5.1e9, "q1": 6.1e9}, [REC]),
            state({"q1": 5.3e9, "q2": 6.3e9}),
            state({"q0": 5.4e9, "q1": 6.4e9}, [REC])]
    env = make(tmp_path, runs, state({"q0": 5.4e9, "q1": 6.4e9}, [REC]))
    assert values(history(env, "qubits.q1.f_01")) == [6.0e9, 6.1e9, 6.3e9, 6.4e9]


def test_a_full_rename_with_no_name_in_common_stays_the_same_chip(tmp_path):
    rec = record(["q1", "q2"], ["qa", "qb"], {"qa": "q1", "qb": "q2"})
    runs = [state({"q1": 5.0e9, "q2": 6.0e9}),
            state({"qa": 5.1e9, "qb": 6.1e9}, [rec])]
    env = make(tmp_path, runs, state({"qa": 5.1e9, "qb": 6.1e9}, [rec]))
    ans = history(env, "qubits.qb.f_01")
    assert values(ans) == [6.0e9, 6.1e9]
    assert not any("chip_uncertain" in p["flags"] for p in ans["rows"]["k"]["points"])


def test_a_chip_without_renames_reads_exactly_as_before(tmp_path):
    runs = [state({"q1": 5.0e9, "q2": 6.0e9}), state({"q1": 5.1e9, "q2": 6.0e9})]
    env = make(tmp_path, runs, state({"q1": 5.1e9, "q2": 6.0e9}))
    ans = history(env, "qubits.q1.f_01")
    assert values(ans) == [5.0e9, 5.1e9]
    assert ans["rows"]["k"]["renames"] == []


def test_the_drawer_says_where_the_qubit_was_renamed(shifted):
    html = shifted["client"].get("/field/history", query_string={"path": "qubits.q1.f_01"}).data.decode()
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    assert "q2" in text and "renamed" in text.lower(), text[:2000]


# ======================================================================
# the event lists: Param History Changes and the Calibration log
# ======================================================================

def _timeline(env, **kw):
    from quam_state_manager.core import hub_query
    with env["app"].app_context():
        ctx = routes_mod._active_ctx()
        chip_dir = routes_mod._hub_chip_dir(ctx["path"])
        return hub_query.timeline(routes_mod._vh_binding(ctx, chip_dir), limit=50,
                                  **routes_mod._rename_scope(ctx, chip_dir), **kw)


def _rows_by_run(page):
    return {ev["run_id"]: {c["path"]: c for c in ev["changes"]} for ev in page["events"]}


class TestEventLists:
    def test_an_older_runs_rows_are_spelled_today_and_say_how_they_were_saved(self, shifted):
        rows = _rows_by_run(_timeline(shifted))
        r2 = rows[2]
        assert r2["qubits.q1.f_01"]["new"] == 6.1e9, "the old q2's change is today's q1's"
        assert r2["qubits.q1.f_01"]["recorded_as"] == "qubits.q2.f_01"
        assert r2["qubits.q0.f_01"]["new"] == 5.1e9
        assert "qubits.q2.f_01" not in r2

    def test_the_run_where_the_rename_came_into_force_lists_only_real_changes(self, shifted):
        page = _timeline(shifted)
        ev3 = next(e for e in page["events"] if e["run_id"] == 3)
        assert ev3["renamed_here"][0]["qubits"] == {"q1": "q0", "q2": "q1"}
        qubit_rows = [p for p in (c["path"] for c in ev3["changes"]) if p.startswith("qubits.")]
        assert qubit_rows == [], "the rebuild carried every value: nothing of a qubit changed"

    def test_a_path_filter_reads_each_event_in_its_own_names(self, shifted):
        page = _timeline(shifted, path="qubits.q1.f_01", path_prefix=True, changed_only=True)
        got = [(e["run_id"], [c["new"] for c in e["changes"] if c["path"] == "qubits.q1.f_01"])
               for e in page["events"]]
        assert got == [(4, [6.2e9]), (2, [6.1e9]), (1, [6.0e9])], got

    def test_the_calibration_log_names_old_targets_in_todays_names(self, shifted):
        from quam_state_manager.web import journal_routes
        with shifted["app"].test_request_context("/journal?day=2026-01-01"):
            data = journal_routes._build("2026-01-01", gate_wait=False, lazy_ok=False)
        cards = {c["run_id"]: c for c in data["cards"] if c["kind"] == "run"}
        assert cards[1]["targets"] == ["q0", "q1"]
        assert cards[1]["targets_as_recorded"] == ["q1", "q2"]
        assert cards[4]["targets"] == ["q0", "q1"] and cards[4]["targets_as_recorded"] is None
        assert "q1 \u2192 q0" in cards[3]["renamed_here"]
        rows1 = {w["path"]: w for w in cards[2]["writes"]}
        assert rows1["qubits.q1.f_01"]["recorded_as"] == "qubits.q2.f_01"

    def test_the_changes_page_marks_rows_saved_under_another_name(self, shifted):
        html = shifted["client"].get("/param-history/changes?prefix=qubits.q1.f_01").data.decode()
        assert "as q2" in html, "an older row says the name it was saved under"
        assert "renamed here" not in html,             "the rebuild carried q1's value: the event that renamed it changed nothing of q1.f_01"


# ======================================================================
# versions: Diff / Compare / Stage / Restore of a state saved before
# ======================================================================

def _ref_of_run(env, rid):
    from quam_state_manager.core import hub_versions
    ev = next(e for e in _timeline(env)["events"] if e["run_id"] == rid)
    return hub_versions.ref_of(ev)


class TestVersions:
    def test_a_version_from_before_the_rename_is_staged_in_todays_names(self, shifted):
        ref = _ref_of_run(shifted, 2)
        with shifted["app"].app_context():
            ctx = routes_mod._active_ctx()
            state, wiring = routes_mod._snapshot_state_wiring(routes_mod._history(), ctx["path"], ref)
        assert sorted(state["qubits"]) == ["q0", "q1"]
        assert state["qubits"]["q0"]["f_01"] == 5.1e9, "the old q1's value goes back onto q0"
        assert state["qubits"]["q1"]["f_01"] == 6.1e9
        assert rename_lineage.era(state) == (REC["id"],), "staging it does not undo the rename"

    def test_a_diff_against_now_shows_only_what_changed_on_each_qubit(self, shifted):
        ref = _ref_of_run(shifted, 2)
        with shifted["app"].app_context():
            entries = routes_mod._version_diff_now(routes_mod._active_ctx(), ref)
        paths = {e.dot_path: (e.old_value, e.new_value) for e in entries}
        assert paths["qubits.q1.f_01"] == (6.1e9, 6.2e9)
        assert paths["qubits.q0.f_01"] == (5.1e9, 5.2e9)
        assert not any(p.startswith("qubits.q2") for p in paths), sorted(paths)
        assert not any(p.endswith(".id") for p in paths), "an id that only follows the rename is no change"


def test_chip_status_lists_each_recorded_path_under_todays_name(tmp_path):
    rec = record(["q1", "q2", "q3"], ["q0", "q1", "q2"], {"q0": "q1", "q1": "q2", "q2": "q3"})
    runs = [state({"q1": 5.0e9, "q2": 6.0e9, "q3": 7.0e9}),
            state({"q0": 5.1e9, "q1": 6.1e9, "q2": 7.1e9}, [rec])]
    env = make(tmp_path, runs, state({"q0": 5.1e9, "q1": 6.1e9, "q2": 7.1e9}, [rec]))
    with env["app"].app_context():
        ans, table = routes_mod._hub_status_table(routes_mod._active_ctx())
    assert table is not None, ans.get("mode")
    got = table.leaf_matching_paths("qubits.*.f_01")
    assert got == ["qubits.q0.f_01", "qubits.q1.f_01", "qubits.q2.f_01"], \
        "q3 named a qubit only before the rename: it is today's q2"
    assert table.counts["qubits.q2.f_01"] == 2, "both events recorded today's q2"


# ======================================================================
# a chip with no change ledger: the snapshot history CUTS at the rename
# ======================================================================

def test_the_snapshot_history_cuts_at_a_rename_and_says_so(tmp_path):
    import time
    from quam_state_manager.core.history import HistoryManager
    live = tmp_path / "chip"
    live.mkdir()
    hm = HistoryManager(tmp_path / "instance", max_snapshots=50, cache_size=3)

    def save(st):
        (live / "state.json").write_text(json.dumps(st), encoding="utf-8")
        (live / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
        hm.check_and_snapshot(live, trigger="manual", force=True)
        time.sleep(0.02)
    save(state({"q1": 5.0e9, "q2": 6.0e9}))
    save(state({"q0": 5.1e9, "q1": 6.1e9}, [REC]))
    save(state({"q0": 5.2e9, "q1": 6.2e9}, [REC]))
    h = hm.field_history(live, "qubits.q1.f_01")
    assert sorted(p["value"] for p in h["points"]) == [6.1e9, 6.2e9], \
        "the old q1 (today's q0) never appears under today's q1"
    assert h["renamed_hidden"] >= 1 and h["renamed"]["since"] == REC["at"]
    rows = hm.extract_property_history(live, ["f_01"], downsample=None)
    by_qubit = {r["qubit"]: [v["value"] for v in r["values"]] for r in rows}
    assert by_qubit.get("q1") == [6.1e9, 6.2e9] and "q2" not in by_qubit, by_qubit


def test_chip_status_trends_mark_where_the_qubits_were_renamed(shifted):
    html = shifted["client"].get("/topology/trends?metric=f_01").data.decode()
    m = re.search(r'<script type="application/json" id="topo-trends-renames">(.*?)</script>', html, re.S)
    assert m, html[:500]
    marks = json.loads(m.group(1))
    assert len(marks) == 1 and "q1 \u2192 q0" in marks[0]["words"], marks


def test_the_rename_record_itself_is_said_once_not_listed(shifted):
    ev3 = next(e for e in _timeline(shifted)["events"] if e["run_id"] == 3)
    assert not [c for c in ev3["changes"] if c["path"].startswith("extras.qubit_renames")]
    assert ev3["record_rows"] > 5 and ev3["renamed_here"]


def test_a_name_valued_leaf_does_not_change_by_the_rename_alone(shifted):
    # qubits.q1.id was "q2" before the rename and "q1" after: one qubit, one id
    pts = history(shifted, "qubits.q1.id")["rows"]["k"]["points"]
    assert [(p["value"], p["op"]) for p in pts] == [("q1", "add")], pts


def test_each_older_point_says_the_name_it_was_saved_under(shifted):
    pts = history(shifted, "qubits.q1.f_01")["rows"]["k"]["points"]
    assert [p["recorded_as"] for p in pts] == ["qubits.q2.f_01", "qubits.q2.f_01", None]


def test_the_snapshot_column_history_cuts_at_a_rename_too(tmp_path):
    import time
    from quam_state_manager.core.history import HistoryManager
    live = tmp_path / "chip"
    live.mkdir()
    hm = HistoryManager(tmp_path / "instance", max_snapshots=50, cache_size=3)
    for st in (state({"q1": 5.0e9, "q2": 6.0e9}, extra_t1={"q2": 2e-5}),
               state({"q0": 5.1e9, "q1": 6.1e9}, [REC], extra_t1={"q1": 3e-5})):
        (live / "state.json").write_text(json.dumps(st), encoding="utf-8")
        (live / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
        hm.check_and_snapshot(live, trigger="manual", force=True)
        time.sleep(0.02)
    col = hm.column_history(live, {"q1": "qubits.q1.T1"})
    assert [r[1] for r in col["q1"]] == [3e-5], col
    col = hm.column_history(live, {"q1": "qubits.q1.xy.operations.x180_DragCosine.amplitude"})
    assert [r[1] for r in col["q1"]] == [6.1e9 / 5e10], col


def test_the_in_force_series_of_a_plain_leaf_follows_the_qubit(shifted):
    eff = [p["value"] for p in history(shifted, "qubits.q1.f_01")["rows"]["k"]["effective"]]
    assert eff == [6.0e9, 6.1e9, 6.2e9], eff


def test_a_path_filter_finds_a_qubits_rows_saved_under_its_old_name(shifted):
    page = _timeline(shifted, path="qubits.q0.f_01", path_prefix=True, changed_only=True)
    got = [(e["run_id"], [c["new"] for c in e["changes"] if c["path"] == "qubits.q0.f_01"])
           for e in page["events"]]
    assert got == [(4, [5.2e9]), (2, [5.1e9]), (1, [5.0e9])], got


def test_a_swap_with_equal_values_still_follows_each_qubit(tmp_path):
    # q1 <-> q2 hold the same value across the swap, so neither raw name has a
    # row where the rename came into force; the later change is still q1's own
    rec = record(["q1", "q2"], ["q1", "q2"], {"q1": "q2", "q2": "q1"})
    runs = [state({"q1": 5.0e9, "q2": 5.0e9}),
            state({"q1": 5.0e9, "q2": 5.0e9}, [rec]),
            state({"q1": 5.1e9, "q2": 5.0e9}, [rec])]
    env = make(tmp_path, runs, state({"q1": 5.1e9, "q2": 5.0e9}, [rec]))
    ans = history(env, "qubits.q1.f_01")
    assert [p["value"] for p in ans["rows"]["k"]["effective"]] == [5.0e9, 5.1e9]
    assert [p["value"] for p in ans["rows"]["k"]["points"]] == [5.0e9, 5.1e9]


def test_the_diff_workbench_compares_each_qubit_with_itself():
    before = state({"q1": 5.0e9, "q2": 6.0e9})
    after = state({"q0": 5.0e9, "q1": 6.1e9}, [REC])
    (s0, _w0), (s1, _w1) = routes_mod._pairs_in_one_era([(before, WIRING), (after, WIRING)])
    assert sorted(s0["qubits"]) == ["q0", "q1"], "the older side is moved into the newer era"
    assert s0["qubits"]["q1"]["f_01"] == 6.0e9 and s0["qubits"]["q0"]["f_01"] == 5.0e9
    assert s1 is after
    # two different renames of one source are compared as recorded
    other = record(["q1", "q2"], ["qa", "qb"], {"qa": "q1", "qb": "q2"})
    side = state({"qa": 5.0e9, "qb": 6.0e9}, [other])
    got = routes_mod._pairs_in_one_era([(after, WIRING), (side, WIRING)])
    assert got[0][0] is after and got[1][0] is side


def test_a_qubit_the_rebuild_added_never_joins_an_older_qubit_of_that_id(tmp_path):
    # q5 existed once and was removed before the rebuild's source; the rebuild
    # then ADDS a brand-new q5: two qubits, one id, never one history
    rec = record(["q1", "q2"], ["q0", "q2", "q5"], {"q0": "q1", "q2": "q2"})
    runs = [state({"q1": 5.0e9, "q2": 6.0e9, "q5": 9.0e9}),
            state({"q1": 5.0e9, "q2": 6.0e9}),
            state({"q0": 5.0e9, "q2": 6.0e9, "q5": 7.0e9}, [rec])]
    env = make(tmp_path, runs, state({"q0": 5.0e9, "q2": 6.0e9, "q5": 7.0e9}, [rec]))
    assert values(history(env, "qubits.q5.f_01")) == [7.0e9]
    assert values(history(env, "qubits.q2.f_01")) == [6.0e9], "an unrenamed qubit keeps its history"
    from quam_state_manager.core.rename_lineage import Step
    s = Step(rec)
    assert s.back_id("q5") is None and s.fwd_id("q5") == "q5_removed"
    old = state({"q1": 5.0e9, "q2": 6.0e9, "q5": 9.0e9})
    moved, _w = s.fwd_state(old, WIRING)
    assert "q5" not in moved["qubits"] and moved["qubits"]["q5_removed"]["f_01"] == 9.0e9


def test_the_datasets_table_finds_an_old_run_under_todays_name(shifted):
    html = shifted["client"].get("/datasets").data.decode()
    m = re.search(r'"rows"\s*:\s*(\[.*?\])\s*,\s*"', html, re.S) or re.search(r'(\[\{"id".*?\}\])', html, re.S)
    assert m, html[:300]
    rows = {r["id"]: r for r in json.loads(m.group(1))}
    assert rows[1]["q"] == ["q1", "q2"] and rows[1]["qn"] == ["q0", "q1"], rows[1]
    assert "qn" not in rows[4], "a run of today's era needs no second spelling"


def test_a_runs_prev_state_diff_across_the_rename_shows_only_real_changes(shifted):
    from quam_state_manager.core.history import diff_state_folders
    runs = sorted((shifted["data"] / "2026-01-01").iterdir())
    before, after = runs[1] / "quam_state", runs[2] / "quam_state"      # #2 (old names), #3 (renamed)
    paths = [e.dot_path for e in diff_state_folders(before, after)]
    assert not [p for p in paths if p.startswith("qubits.")], paths[:10]


def test_the_snapshot_history_keeps_a_qubit_the_rename_did_not_touch(tmp_path):
    import time
    from quam_state_manager.core.history import HistoryManager
    rec = record(["q1", "q2", "q5"], ["q0", "q1", "q5"], {"q0": "q1", "q1": "q2"})
    live = tmp_path / "chip"
    live.mkdir()
    hm = HistoryManager(tmp_path / "instance", max_snapshots=50, cache_size=3)
    for st in (state({"q1": 5.0e9, "q2": 6.0e9, "q5": 7.0e9}),
               state({"q0": 5.0e9, "q1": 6.0e9, "q5": 7.1e9}, [rec])):
        (live / "state.json").write_text(json.dumps(st), encoding="utf-8")
        (live / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
        hm.check_and_snapshot(live, trigger="manual", force=True)
        time.sleep(0.02)
    h = hm.field_history(live, "qubits.q5.f_01")
    assert sorted(p["value"] for p in h["points"]) == [7.0e9, 7.1e9], "q5 is the same qubit"
    assert h["renamed_hidden"] == 0
    h1 = hm.field_history(live, "qubits.q1.f_01")
    assert [p["value"] for p in h1["points"]] == [6.0e9] and h1["renamed_hidden"] == 1
