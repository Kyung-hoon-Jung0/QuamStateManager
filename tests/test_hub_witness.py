"""P0-1 -- a run's saved value that never reached the chip (``hub_witness``).

Pins, on generic synthetic ledgers and run folders in ``tmp_path``:

* the rule (``hub_witness.verdicts``): confirmed by a run that did not measure
  the qubit; contradicted by one; a targeted re-measurement ends the walk; an
  observed state and an SM write are witnesses; a pair target covers both of
  its qubits; a masked re-measurement (it carries the old value back) is not
  contradicted; the two refinements (a walk passes a re-measurement the chip
  never kept; a patch-proven change is confirmed by its patch) and the spec's
  unrefined rule;
* the series: a contradicted change and its witness's restoring row both leave
  the value series and the fold stays exact across them; the live tail decides
  the newest change; nothing is silent (``not_kept``);
* the surfaces: value drawer, Column History (Changes + By run), Calibration
  log, Param History Changes, metric meta, Trends, the calibration age.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quam_state_manager.core import hub_index, hub_query, hub_rules as rules, hub_witness, story
from quam_state_manager.core import value_history as vh
from quam_state_manager.core.hub_store import HubStore
from quam_state_manager.web import journal_routes, routes
from tests.test_hub_drawer import _inline, chip_dir, make_app, write_chip  # noqa: F401 -- autouse

T0 = int(datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc).timestamp() * 1_000_000)
A, B, C = 5.0e9, 5.2e9, 5.3e9


# ======================================================================
# 1. the rule, on a synthetic ledger
# ======================================================================

@pytest.fixture
def led(tmp_path):
    with HubStore(tmp_path / "ledger") as store:
        root = store.register_root(tmp_path / "archive", "+00:00")
        previous = {}

        def add(doc, *, targets=None, kind="run", proven=None):
            nonlocal previous
            rank = store.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] + 1
            flat = rules.flatten(doc)
            event = {"kind": kind, "ord": rank, "root_id": root, "rel_path": f"run-{rank}",
                     "run_id": rank if kind == "run" else None, "experiment": "scan",
                     "actor": "human:user-a", "targets": json.dumps(targets), "flags": 0,
                     "t_utc_us": T0 + rank * 1_000_000, "state_hash": str(rank),
                     "base_hash": str(rank - 1) if rank > 1 else None,
                     "status": "landed" if kind not in ("run", "observed") else "finished"}
            eid = store.append(event, rules.diff(previous, flat), doc, flat, proven=proven)
            previous = flat
            return eid

        store.add = add
        store.dir = tmp_path / "ledger"
        yield store
    hub_index.close_readers()


def doc(f=A, f2=1.0, ph=0.5):
    return {"qubits": {"qA1": {"f": f}, "qA2": {"f": f2}}, "qubit_pairs": {"qA1-qA2": {"ph": ph}}}


def q(*ids):
    return {"qubits": list(ids)}


def judged(store, **kw):
    conn = sqlite3.connect(f"file:{(store.dir / 'ledger.sqlite').as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        index = hub_index.build_index(conn)
        v = hub_witness.verdicts(index, conn, **kw)
        pid = index.paths
        return v, pid
    finally:
        conn.close()


def series(store, path):
    return [(row[0]["eid"], row[2]) for row in hub_query.series(store, path)]


F = "qubits.qA1.f"


class TestTheRule:
    def test_confirmed_by_a_run_that_did_not_measure_the_qubit(self, led):
        led.add(doc(), targets=q("qA1"))
        r = led.add(doc(f=B), targets=q("qA1"))
        w = led.add(doc(f=B, f2=2.0), targets=q("qA2"))
        v, pid = judged(led)
        assert v.code(r, pid[F]) == hub_witness.CONFIRMED and v.witness_of(r, pid[F]) == w
        assert v.drop(pid[F]) == frozenset()

    def test_contradicted_by_a_run_that_did_not_measure_the_qubit(self, led):
        led.add(doc(), targets=q("qA1"))
        r = led.add(doc(f=B), targets=q("qA1"))
        w = led.add(doc(f=A, f2=2.0), targets=q("qA2"))
        v, pid = judged(led)
        assert v.code(r, pid[F]) == hub_witness.CONTRADICTED and v.witness_of(r, pid[F]) == w
        assert v.pairs_of(pid[F]) == {r: w} and v.restores(w, pid[F]) == r
        assert v.drop(pid[F]) == {r, w}, "the change AND the witness's restoring row leave the series"
        assert v.code(w, pid["qubits.qA2.f"]) is not None and w not in v.drop(pid["qubits.qA2.f"]), \
            "the witness's own change of its own qubit stays"

    def test_a_targeted_remeasurement_ends_the_walk(self, led):
        led.add(doc(), targets=q("qA1"))
        r = led.add(doc(f=B), targets=q("qA1"))
        r2 = led.add(doc(f=C), targets=q("qA1"))
        led.add(doc(f=C, f2=2.0), targets=q("qA2"))
        v, pid = judged(led)
        assert v.code(r, pid[F]) == hub_witness.REMEASURED, "no witness: the next claim replaced it"
        assert v.code(r2, pid[F]) == hub_witness.CONFIRMED

    def test_a_targeted_run_that_left_the_value_alone_is_passed_over(self, led):
        led.add(doc(), targets=q("qA1"))
        r = led.add(doc(f=B), targets=q("qA1"))
        led.add(doc(f=B, ph=0.6), targets=q("qA1"))            # targets qA1, f untouched
        w = led.add(doc(f=A, f2=2.0), targets=q("qA2"))
        v, pid = judged(led)
        assert v.code(r, pid[F]) == hub_witness.CONTRADICTED and v.witness_of(r, pid[F]) == w

    @pytest.mark.parametrize("kind", ["observed", "sm_apply"])
    def test_an_observed_state_and_an_sm_write_are_witnesses(self, led, kind):
        led.add(doc(), targets=q("qA1"))
        r = led.add(doc(f=B), targets=q("qA1"))
        w = led.add(doc(f=A), kind=kind)
        v, pid = judged(led)
        assert v.code(r, pid[F]) == hub_witness.CONTRADICTED and v.pairs_of(pid[F]) == {r: w}

    @pytest.mark.parametrize("kind", ["observed", "sm_apply"])
    def test_an_observed_state_and_an_sm_write_confirm(self, led, kind):
        led.add(doc(), targets=q("qA1"))
        r = led.add(doc(f=B), targets=q("qA1"))
        w = led.add(doc(f=B, f2=2.0), kind=kind)
        v, pid = judged(led)
        assert v.code(r, pid[F]) == hub_witness.CONFIRMED and v.witness_of(r, pid[F]) == w

    def test_a_pair_target_covers_both_qubits_and_the_pair(self, led):
        led.add(doc(), targets=q("qA1"))
        r = led.add(doc(f=B, ph=0.7), targets={"qubit_pairs": ["qA1-qA2"]})
        r2 = led.add(doc(f=A, ph=0.5), targets={"qubit_pairs": ["qA1-qA2"]})
        v, pid = judged(led)
        assert v.code(r, pid[F]) == hub_witness.REMEASURED, "a pair target re-measures its qubit"
        assert v.code(r, pid["qubit_pairs.qA1-qA2.ph"]) == hub_witness.REMEASURED, "...and its pair"
        led.add(doc(f=A, ph=0.5, f2=3.0), targets=q("qA3"))
        v, pid = judged(led)
        assert v.code(r2, pid[F]) == hub_witness.CONFIRMED
        assert v.code(r2, pid["qubit_pairs.qA1-qA2.ph"]) == hub_witness.CONFIRMED

    def test_a_masked_remeasurement_is_open_not_contradicted(self, led):
        led.add(doc(), targets=q("qA1"))
        r = led.add(doc(f=B), targets=q("qA1"))
        rr = led.add(doc(f=A), targets=q("qA1"))               # re-measures AND carries old back
        led.add(doc(f=A, f2=2.0), targets=q("qA2"))
        v, pid = judged(led)
        assert v.code(r, pid[F]) == hub_witness.REMEASURED
        assert r not in v.drop(pid[F]) and rr not in v.drop(pid[F])

    def test_the_newest_change_with_no_witness_is_open(self, led):
        led.add(doc(), targets=q("qA1"))
        r = led.add(doc(f=B), targets=q("qA1"))
        v, pid = judged(led)
        assert v.code(r, pid[F]) == hub_witness.OPEN and v.tail(pid[F]) == (r, hub_witness.OPEN)

    def test_a_walk_passes_a_remeasurement_the_chip_never_kept(self, led):
        led.add(doc(), targets=q("qA1"))
        r = led.add(doc(f=B), targets=q("qA1"))
        r2 = led.add(doc(f=C), targets=q("qA1"))
        w = led.add(doc(f=B, f2=2.0), targets=q("qA2"))          # still had r's value
        v, pid = judged(led)
        assert v.code(r2, pid[F]) == hub_witness.CONTRADICTED and v.pairs_of(pid[F]) == {r2: w}
        assert v.code(r, pid[F]) == hub_witness.CONFIRMED and v.witness_of(r, pid[F]) == w
        v0, _ = judged(led, refined=False)
        assert v0.code(r, pid[F]) == hub_witness.REMEASURED, "the spec's section 2 rule stops at r2"

    def test_a_patch_proven_change_is_confirmed_by_its_patch(self, led):
        led.add(doc(), targets=q("qA1"))
        r = led.add(doc(f=B), targets=q("qA1"), proven={F})
        led.add(doc(f=A, f2=2.0), targets=q("qA2"))
        v, pid = judged(led)
        assert v.code(r, pid[F]) == hub_witness.CONFIRMED and v.drop(pid[F]) == frozenset()
        v0, _ = judged(led, refined=False)
        assert v0.code(r, pid[F]) == hub_witness.CONTRADICTED

    @pytest.mark.parametrize("unknown", [{"qubits": None}, None, {}, {"qubits": []}, {"cz_macro_name": "cz"}])
    def test_a_run_that_names_no_target_is_never_a_witness(self, led, unknown):
        led.add(doc(), targets=q("qA1"))
        r = led.add(doc(f=B), targets=q("qA1"))
        led.add(doc(f=B, f2=2.0), targets=unknown)            # may have measured qA1 too
        v, pid = judged(led)
        assert v.code(r, pid[F]) == hub_witness.OPEN, "it carried the value but may have measured it"
        w = led.add(doc(f=A, f2=2.0), targets=unknown)         # changes it back: a re-measurement
        v, pid = judged(led)
        assert v.code(r, pid[F]) == hub_witness.REMEASURED and v.drop(pid[F]) == frozenset()
        v0, _ = judged(led, unknown_targets="none")
        assert v0.code(r, pid[F]) == hub_witness.CONFIRMED, "read as naming nothing it would vouch for qA1"
        assert w not in v.drop(pid[F])

    def test_runs_alone_as_witnesses(self, led):
        led.add(doc(), targets=q("qA1"))
        r = led.add(doc(f=B), targets=q("qA1"))
        led.add(doc(f=A), kind="observed")
        v, pid = judged(led, witnesses=frozenset({hub_witness.RUN}))
        assert v.code(r, pid[F]) == hub_witness.OPEN, "an observed state is no witness when runs alone are"

    def test_judged_per_holder_or_all_at_once_the_same(self, led):
        led.add(doc(), targets=q("qA1"))
        led.add(doc(f=B, ph=0.7), targets=q("qA1"))
        led.add(doc(f=A, ph=0.7, f2=2.0), targets=q("qA2"))
        led.add(doc(f=C, ph=0.5, f2=2.0), targets={"qubit_pairs": ["qA1-qA2"]})
        led.add(doc(f=C, ph=0.5, f2=3.0), kind="observed")
        full, pid = judged(led)
        conn = sqlite3.connect(f"file:{(led.dir / 'ledger.sqlite').as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            lazy = hub_witness.verdicts(hub_index.build_index(conn), conn, lazy=True)
            for p in pid.values():
                assert lazy.pairs_of(p) == full.pairs_of(p) and lazy.tail(p) == full.tail(p)
            assert dict(lazy) == dict(full) and len(full) > 0
        finally:
            conn.close()

    def test_every_zone_view_shares_one_set_of_verdicts(self, led):
        led.add(doc(), targets=q("qA1"))
        r = led.add(doc(f=B), targets=q("qA1"))
        conn = sqlite3.connect(f"file:{(led.dir / 'ledger.sqlite').as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            base = hub_index.build_index(conn)
            utc, other = base.for_zone("UTC"), base.for_zone("Asia/Seoul")
            assert utc is not other
            v = hub_witness.of(conn, utc)
            v.prime()
            assert hub_witness.of(conn, other) is v, "the Calibration log's zone reuses the drawer's verdicts"
            led.add(doc(f=B, f2=2.0), targets=q("qA2"))
            grown = hub_index.extend_index(conn, base)
            fresh = hub_witness.of(conn, grown.for_zone("UTC"))
            assert fresh is not v and fresh.code(r, grown.paths[F]) == hub_witness.CONFIRMED
        finally:
            conn.close()

    def test_built_once_per_index(self, led):
        led.add(doc(), targets=q("qA1"))
        led.add(doc(f=B), targets=q("qA1"))
        with hub_index.snapshot(type("S", (), {"directory": led.dir})()) as (conn, index):
            assert hub_witness.of(conn, index) is hub_witness.of(conn, index)


class TestTheSeries:
    def test_the_pair_leaves_the_series_and_the_fold_stays_exact(self, led):
        a = led.add(doc(), targets=q("qA1"))
        r = led.add(doc(f=B), targets=q("qA1"))
        w = led.add(doc(f=A, f2=2.0), targets=q("qA2"))
        x = led.add(doc(f=C, f2=2.0), targets=q("qA1"))
        assert series(led, F) == [(a, A), (x, C)], "old -> new -> old reads as old throughout"
        tgt = {"f": vh.target(doc(f=C, f2=2.0), F)}
        ans = vh.read(led.dir, tgt, runs=10)
        row = ans["rows"]["f"]
        assert [(p["eid"], p["value"], p["old"]) for p in row["points"]] == [(a, A, None), (x, C, A)]
        assert [p["value"] for p in row["effective"]] == [A, C]
        nk = row["not_kept"]
        assert [(n["eid"], n["value"], n["old"], n["by"]["eid"], n["by"]["value"]) for n in nk] == \
            [(r, B, A, w, A)], "the left-out change is listed with its witness, never silent"
        by_run = {e["eid"]: e for e in ans["runs"]}
        assert by_run[r]["values"]["f"] == B and by_run[r]["kept"]["f"] == A, \
            "By run is what the run saved; the chip held the old value"
        assert by_run[w]["values"]["f"] == A and "f" not in by_run[w]["kept"]
        assert by_run[x]["values"]["f"] == C and "f" not in by_run[x]["kept"]

    def test_the_timeline_lists_it_apart_and_the_witness_changed_nothing(self, led):
        led.add(doc(), targets=q("qA1"))
        r = led.add(doc(f=B), targets=q("qA1"))
        w = led.add(doc(f=A, f2=2.0), targets=q("qA2"))
        events = {e["eid"]: e for e in hub_query.timeline(led)["events"]}
        assert [c["path"] for c in events[r]["changes"]] == []
        assert [(c["path"], c["old"], c["new"], c["witness"]["run_id"]) for c in events[r]["not_kept"]] == \
            [(F, A, B, w)]
        assert [c["path"] for c in events[w]["changes"]] == ["qubits.qA2.f"], \
            "the run that still found the old value did not change it"
        listing = {e["eid"]: e for e in hub_query.timeline(led, foreign="label")["events"]}
        assert [c["path"] for c in listing[r]["changes"]] == [F], "a listing of saved states is unchanged"

    @pytest.mark.parametrize("live,code", [(B, None), (A, "contradicted"), (C, "contradicted")])
    def test_the_live_tail_decides_the_newest_change(self, led, live, code):
        a = led.add(doc(), targets=q("qA1"))
        r = led.add(doc(f=B), targets=q("qA1"))
        tgt = {"f": vh.target(doc(f=B), F)}
        row = vh.read(led.dir, tgt, live={"f": live})["rows"]["f"]
        if code is None:
            assert [p["eid"] for p in row["points"]] == [a, r] and "witness" not in row["points"][-1]
            assert row["not_kept"] == []
        else:
            assert [p["eid"] for p in row["points"]] == [a], "a value the chip does not hold left"
            assert row["total"] == 1 and [p["eid"] for p in row["effective"]] == [a]
            assert [(n["eid"], n["by"]["kind"], n["by"]["value"]) for n in row["not_kept"]] == \
                [(r, "live", live)]
        unknown = vh.read(led.dir, tgt)["rows"]["f"]
        assert unknown["points"][-1]["eid"] == r and unknown["points"][-1]["witness"] == "open", \
            "with the chip's value unknown the newest change stays open, labelled"


# ======================================================================
# 2. the surfaces, on a chip with run folders
# ======================================================================

def iso(t_us: int) -> str:
    return datetime.fromtimestamp(t_us / 1e6, tz=timezone.utc).isoformat()


def state(*, f1=A, t1=1e-5, t2=2e-5):
    return {"qubits": {"qA1": {"id": "qA1", "f_01": f1, "T1": t1},
                       "qA2": {"id": "qA2", "f_01": 6.0e9, "T1": t2}},
            "qubit_pairs": {}, "extras": {"chip_name": "device"}}


def run_folder(root: Path, rid: int, st: dict, targets: list[str], *, name="scan") -> Path:
    t_us = T0 + rid * 10_000_000
    hhmmss = datetime.fromtimestamp(t_us / 1e6, tz=timezone.utc).strftime("%H%M%S")
    folder = root / "2026-01-01" / f"#{rid}_{name}_{hhmmss}"
    (folder / "quam_state").mkdir(parents=True, exist_ok=True)
    body = {"created_at": iso(t_us), "metadata": {"status": "finished", "name": name},
            "id": rid, "data": {"parameters": {"model": {"qubits": targets}}}}
    (folder / "node.json").write_text(json.dumps(body), encoding="utf-8")
    (folder / "quam_state" / "state.json").write_text(json.dumps(st), encoding="utf-8")
    (folder / "quam_state" / "wiring.json").write_text(
        json.dumps({"network": {"host": "127.0.0.1", "cluster_name": "C1"}}), encoding="utf-8")
    return folder


#: #1 genesis; #2 qA1 T1 (kept: #3 carries it); #3 qA2 T1; #4 qA1 f_01 -> 5.2e9
#: (NOT kept: #5, which did not measure qA1, still had 5.0e9); #5 qA2 T1
RUNS = [
    (state(), ["qA1", "qA2"]),
    (state(t1=3e-5), ["qA1"]),
    (state(t1=3e-5, t2=2.2e-5), ["qA2"]),
    (state(f1=B, t1=3e-5, t2=2.2e-5), ["qA1"]),
    (state(t1=3e-5, t2=2.4e-5), ["qA2"]),
]


def chip(tmp_path, live_state):
    data, live = tmp_path / "data", tmp_path / "chips" / "live"
    for rid, (st, targets) in enumerate(RUNS, start=1):
        run_folder(data, rid, st, targets)
    write_chip(live, live_state, data)
    app = make_app(tmp_path)
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "client": c, "live": live, "data": data, "tmp": tmp_path}


@pytest.fixture
def sm(tmp_path):
    return chip(tmp_path, RUNS[-1][0])


def text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).replace("&mdash;", "\u2014")


def drawer(env, path, poll=False) -> str:
    if poll:
        with env["app"].test_request_context():
            routes._sync_live_refresh(routes._active_ctx())
    r = env["client"].get("/field/history", query_string={"path": path})
    assert r.status_code == 200, r.data[:400]
    return r.data.decode()


def rows_of(html):
    return [text(r) for r in re.findall(r'<tr class="vh-row[^"]*".*?</tr>', html, re.S)]


class TestTheSurfaces:
    def test_the_value_drawer_leaves_it_out_and_lists_it(self, sm):
        html = drawer(sm, "qubits.qA1.f_01")
        rows = rows_of(html)
        assert len(rows) == 1 and "5,000,000,000" in rows[0] and "first recorded in #1" in rows[0], rows
        assert "5,200,000,000" not in " ".join(rows), "no Revert / Use offers a value the chip never held"
        assert '<details class="vh-not-kept">' in html, "what was left out is listed, never silent"
        listed = text(html.split('<details class="vh-not-kept">', 1)[1].split("</details>", 1)[0])
        assert "1 saved value not kept on the chip" in listed
        assert ("Saved by run #4 scan: 5,200,000,000.0 \u2014 not kept on the chip "
                "(#5, which did not measure qA1, still had 5,000,000,000.0)") in listed
        foot = text(html.split('class="fh-foot vh-foot"', 1)[1])
        assert "1 saved value not kept on the chip" in foot

    def test_a_kept_value_reads_as_before(self, sm):
        rows = rows_of(drawer(sm, "qubits.qA1.T1"))
        assert [r.split()[0] for r in rows] == ["3e-05", "1e-05"] and "saved in #2" in rows[0]
        assert "read the chip" not in rows[0], "a confirmed change carries no doubt"

    def test_the_live_tail_on_the_drawer(self, tmp_path):
        env = chip(tmp_path, state(t1=3e-5, t2=2.2e-5))      # the lab did not keep #5's qA2 T1
        unproven = rows_of(drawer(env, "qubits.qA2.T1"))    # no live read has judged the chip yet
        assert unproven[0].split()[0] == "2.4e-05" and "no later run has read the chip yet" in unproven[0]
        html = drawer(env, "qubits.qA2.T1", poll=True)       # the live files read equal to the copy
        rows = rows_of(html)
        assert rows[0].split()[0] == "2.2e-05" and "saved in #3" in rows[0] and "current" in rows[0], rows
        assert "not kept on the chip (the chip holds 2.2e-05 now)" in text(html)

    def test_unproven_live_leaves_the_newest_open(self, sm):
        rows = rows_of(drawer(sm, "qubits.qA2.T1"))
        assert rows[0].split()[0] == "2.4e-05" and "no later run has read the chip yet" in rows[0]

    def test_column_history_changes_and_by_run(self, sm):
        r = sm["client"].post("/bulk/column-history", data={
            "grid": "qubit", "label": "f01", "unit": "", "col_key": "c",
            "paths": json.dumps({"qA1": "qubits.qA1.f_01"})})
        html = r.data.decode()
        chips = html.split('class="ch-view ch-view-changes"', 1)[1].split('class="ch-view ch-view-byrun"', 1)[0]
        assert "5,200,000,000" not in chips and "1 not kept on the chip" in text(chips)
        byrun = html.split('class="ch-view ch-view-byrun"', 1)[1]
        cells = re.findall(r'<td class="ch-val[^"]*".*?</td>', byrun, re.S)
        marked = [text(c) for c in cells if "ch-not-kept" in c]
        assert len(marked) == 1 and "5,200,000,000" in marked[0] and "not kept on the chip" in marked[0], \
            "By run is what each run saved; the value the chip never held is marked"

    def test_the_calibration_log_lists_it_apart(self, sm):
        with sm["app"].test_request_context():
            ctx = routes._active_ctx()
            binding = routes._vh_binding(ctx, chip_dir(sm))
        page = hub_query.timeline(binding)
        by_run = {e["run_id"]: e for e in page["events"]}
        assert [c["path"] for c in by_run[4]["changes"]] == []
        assert [c["path"] for c in by_run[4]["not_kept"]] == ["qubits.qA1.f_01"]
        assert [c["path"] for c in by_run[5]["changes"]] == ["qubits.qA2.T1"], \
            "#5 did not measure qA1: it is never listed as having changed its f_01"
        from quam_state_manager.web.app import create_app  # noqa: F401 -- the template renders
        with sm["app"].test_request_context():
            html = sm["app"].jinja_env.from_string(
                "{% from '_journal_changes.html' import run_changes %}{{ run_changes(c) }}").render(
                c={"writes": [], "not_kept": by_run[4]["not_kept"], "first_state_n": None})
        assert "Saved in its own folder, not kept on the chip" in html
        assert "#5 still had the earlier value" in text(html)

    def test_the_story_card_carries_it(self, sm, monkeypatch):
        with sm["app"].test_request_context():
            ctx = routes._active_ctx()
            binding = routes._vh_binding(ctx, chip_dir(sm))
        bound = hub_index.context(binding.store if hasattr(binding, "store") else binding,
                                  zone="UTC", folder=getattr(binding, "folder", None))
        day = story.build_day(sm["tmp"] / "_inst", "device", "2026-01-01", ds=None, ledger=bound,
                              with_gates=False)
        cards = {c["run_id"]: c for c in day["cards"] if c["kind"] == "run"}
        assert [w["path"] for w in cards[4]["not_kept"]] == ["qubits.qA1.f_01"] and cards[4]["writes"] == []
        assert "qubits.qA1.f_01" not in [w["path"] for w in cards[5]["writes"]]

    def test_param_history_changes(self, sm):
        html = sm["client"].get("/param-history/changes").data.decode()
        groups = {re.search(r"run #(\d+)", g).group(1): g
                  for g in html.split('<div class="ph-change-group">')[1:] if re.search(r"run #(\d+)", g)}
        assert "qubits.qA1.f_01" not in groups["5"], "#5 never changed qA1's f_01"
        assert "4" in groups, "run #4's group lists what it saved that the chip never kept"
        assert "ph-not-kept" in groups["4"] and "not kept on the chip" in text(groups["4"])

    def test_the_metric_meta_and_trends(self, sm):
        meta = sm["client"].get("/topology/metric-meta").get_json()["q"]["f_01"]["qA1"]
        assert meta["first"] and meta["saved_run"] == 1, "unchanged since the ledger began, not #5"
        r = sm["client"].get("/topology/trends", query_string={"metrics": "", "path": "qubits.qA1.f_01"})
        data = json.loads(re.search(r'id="topo-trends-data"[^>]*>(.*?)</script>', r.data.decode(), re.S).group(1))
        values = [p[1] for c in data for s in c["series"] if s["entity"] == "qA1" for p in s["points"]]
        assert B not in values and values and set(values) == {A}

    def test_the_calibration_age(self, sm):
        with sm["app"].test_request_context():
            _ans, table = routes._hub_status_table(routes._active_ctx())
            times = table.run_change_times()
        t = {rid: T0 + rid * 10_000_000 for rid in range(1, 6)}
        assert times["q"]["qA1"] == t[2], "the last run that changed a value qA1 kept is #2"
        assert times["q"]["qA2"] == t[5]

    def test_the_agent_answer(self, sm):
        with sm["app"].test_request_context():
            ans = routes._value_history(routes._active_ctx(), {"v": "qubits.qA1.f_01"})
            view = routes._vh_agent_view(ans, "v")
        assert [p["value"] for p in view["points"]] == [A]
        assert [(n["value"], n["run_id"], n["witness"]["run_id"]) for n in view["not_kept"]] == [(B, 4, 5)]


# ======================================================================
# 3. an archived chip (S10 F3): its history from runs, run captures and states SM saw
# ======================================================================

def test_an_archived_chips_history_still_reads_every_value(tmp_path):
    """A run captured by Param History whose folder is gone is a run event
    with no data root and no targets (``snapshot:`` rel_path): it may have
    measured anything, so it never witnesses a value -- and nothing the
    pre-S10 grid drew for the chip is left out."""
    from tests.test_archived_chip_build import GONE, _drawer, _grid, _ledger, _pre_s10, _with_runs
    env = _with_runs(tmp_path)
    _grid(env)
    _body, row = _drawer(env, "qA1")
    assert [v["value"] for v in row["values"]] == _pre_s10(env, "qA1"), "every value is still read"
    captured = {e for (e,) in _ledger(env, "SELECT eid FROM events WHERE kind='run' AND rel_path LIKE 'snapshot:%'")}
    assert len(captured) == len(GONE)
    conn = sqlite3.connect((env["dir"] / "ledger.sqlite").resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        index = hub_index.build_index(conn)
        v = hub_witness.verdicts(index, conn)
        pid = index.paths["qubits.qA1.T1"]
        assert not v.pairs_of(pid), "nothing the chip saw is left out"
        assert not captured & {v.witness_of(e, pid) for e in index.path_postings[pid]}, \
            "a capture with no targets never vouches for a value"
    finally:
        conn.close()


# ======================================================================
# 4. "since": an excursion of unconfirmed saves that came back is not a change of the chip
# ======================================================================

C_, R_, D_ = hub_witness.CONFIRMED, hub_witness.REMEASURED, hub_witness.DIFFERS


def _since(seq):
    return hub_witness.since(seq, lambda a, b: a == b)


class TestSince:
    def test_the_chain_shape_anchors_at_the_value_it_came_back_to(self):
        # first state X; #58 A (confirmed); #1534.. B, C (re-measured, unconfirmed); back to A
        assert _since([("X", None), ("A", C_), ("B", R_), ("C", R_), ("A", C_)]) == (1, [2, 3])

    def test_a_confirmed_change_inside_breaks_the_excursion(self):
        assert _since([("X", None), ("A", C_), ("B", C_), ("C", R_), ("A", C_)]) == (4, [])

    def test_an_excursion_back_to_a_different_value_is_a_change(self):
        assert _since([("X", None), ("A", C_), ("B", R_), ("C", C_)]) == (3, [])

    def test_every_excursion_back_to_the_value_is_skipped(self):
        assert _since([("A", C_), ("B", R_), ("A", R_), ("C", D_), ("A", C_)]) == (0, [1, 3])

    def test_a_point_that_is_not_a_judged_change_breaks_it(self):
        assert _since([("A", C_), ("B", None), ("A", C_)]) == (2, [])
        assert _since([]) == (-1, []) and _since([("A", C_)]) == (0, [])

    def _chain(self, led, *, confirm_inside=False, back=B):
        led.add(doc(f=A), targets=q("qA1"))
        r58 = led.add(doc(f=B), targets=q("qA1"))
        led.add(doc(f=B, f2=2.0), targets=q("qA2"))            # confirms #58
        r1 = led.add(doc(f=C, f2=2.0), targets=q("qA1"))
        if confirm_inside:
            led.add(doc(f=C, f2=2.5), targets=q("qA2"))         # the chip held C: confirmed
        r2 = led.add(doc(f=4.0e9, f2=2.5), targets=q("qA1"))
        r3 = led.add(doc(f=back, f2=2.5), targets=q("qA1"))
        led.add(doc(f=back, f2=3.0), targets=q("qA2"))         # confirms the return
        return r58, r1, r2, r3

    def test_the_series_keeps_every_point_and_says_since(self, led):
        r58, r1, r2, r3 = self._chain(led)
        row = vh.read(led.dir, {"f": vh.target(doc(f=B, f2=3.0), F)})["rows"]["f"]
        assert [p["eid"] for p in row["points"]][-4:] == [r58, r1, r2, r3], "the excursion stays listed"
        assert [p.get("witness") for p in row["points"]][-3:] == [R_, R_, None]
        assert row.get("since"), "the value has been on the chip since #58, not since its return"
        assert row["since"]["point"]["eid"] == r58
        assert [s["eid"] for s in row["since"]["skipped"]] == [r1, r2]

    def test_no_since_when_a_save_inside_was_confirmed(self, led):
        self._chain(led, confirm_inside=True)
        row = vh.read(led.dir, {"f": vh.target(doc(f=B, f2=3.0), F)})["rows"]["f"]
        assert not row.get("since")

    def test_no_since_when_it_came_back_to_another_value(self, led):
        self._chain(led, back=5.5e9)
        row = vh.read(led.dir, {"f": vh.target(doc(f=5.5e9, f2=3.0), F)})["rows"]["f"]
        assert not row.get("since")


#: #1 genesis; #2 qA1 T1 3e-5 (confirmed by #3, which did not measure qA1);
#: #4, #5 qA1 T1 3.3e-5, 3.4e-5 (re-measured, never confirmed); #6 back to
#: 3e-5 exactly (confirmed by #7)
CHAIN = [
    (state(), ["qA1", "qA2"]),
    (state(t1=3e-5), ["qA1"]),
    (state(t1=3e-5, t2=2.2e-5), ["qA2"]),
    (state(t1=3.3e-5, t2=2.2e-5), ["qA1"]),
    (state(t1=3.4e-5, t2=2.2e-5), ["qA1"]),
    (state(t1=3e-5, t2=2.2e-5), ["qA1"]),
    (state(t1=3e-5, t2=2.4e-5), ["qA2"]),
]


@pytest.fixture
def chain(tmp_path):
    data, live = tmp_path / "data", tmp_path / "chips" / "live"
    for rid, (st, targets) in enumerate(CHAIN, start=1):
        run_folder(data, rid, st, targets)
    write_chip(live, CHAIN[-1][0], data)
    app = make_app(tmp_path)
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "client": c, "live": live, "data": data, "tmp": tmp_path}


NOTE = "runs #4-#5 saved other values that no later read of the chip confirmed"


class TestSinceOnTheSurfaces:
    def test_the_drawer_says_since_the_value_the_chip_kept(self, chain):
        html = drawer(chain, "qubits.qA1.T1")
        rows = rows_of(html)
        assert [r.split()[0] for r in rows] == ["3e-05", "3.4e-05", "3.3e-05", "3e-05", "1e-05"], \
            "every save stays listed"
        assert "changed again before the chip was read" in rows[1] and "changed again" in rows[2]
        since = text(html.split('class="vh-since"', 1)[1].split("</p>", 1)[0]) if 'class="vh-since"' in html else ""
        assert "On the chip since" in since and f"(saved in #2 scan): {NOTE}." in since, since

    def test_the_metric_meta_and_the_calibration_age_anchor_there(self, chain):
        meta = chain["client"].get("/topology/metric-meta").get_json()["q"]["T1"]["qA1"]
        assert meta["saved_run"] == 2 and meta["since_note"] == NOTE + ".", meta
        assert meta["ts"] == vh.iso_z(T0 + 2 * 10_000_000)
        with chain["app"].test_request_context():
            _ans, table = routes._hub_status_table(routes._active_ctx())
            times = table.run_change_times()
        assert times["q"]["qA1"] == T0 + 2 * 10_000_000, "the returning excursion did not calibrate it"
        assert times["q"]["qA2"] == T0 + 7 * 10_000_000

    def test_column_history_and_the_agent_say_it_too(self, chain):
        r = chain["client"].post("/bulk/column-history", data={
            "grid": "qubit", "label": "T1", "unit": "", "col_key": "c",
            "paths": json.dumps({"qA1": "qubits.qA1.T1"})})
        m = re.search(r'<span class="vh-since-chip"[^>]*title="([^"]*)"[^>]*>(.*?)</td>', r.data.decode(), re.S)
        assert m and "since" in text(m.group(2)) and "#2" in text(m.group(2)), r.data.decode()[-800:]
        assert "<" not in m.group(1) and NOTE in m.group(1), "the title is plain text"
        with chain["app"].test_request_context():
            ans = routes._value_history(routes._active_ctx(), {"v": "qubits.qA1.T1"})
            view = routes._vh_agent_view(ans, "v")
        assert view["since"]["run_id"] == 2 and view["since"]["note"] == NOTE
