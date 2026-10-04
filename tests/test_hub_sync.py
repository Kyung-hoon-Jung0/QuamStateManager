"""docs/275 (S5) -- the ledger keeps itself current.

Pins, on generic synthetic run folders in ``tmp_path``:

* placement by instant (I1) with local repair: a late run is inserted, only
  its successor is re-diffed; two folders merge into one timeline; an SM
  event projected late takes its place; an un-anchored SM event is anchored
  before anything is inserted in front of it; checkpoints after an insertion
  point stay correct; ranks renumber when a gap runs out;
* in-flight, rewritten, deleted, corrupt-node and foreign-chip run folders;
* the stat watermark reads no unchanged run;
* status (building n/N | ready, ``Building`` is a ``ramcache.Warming``);
* in SM: opening a chip catches its folders up with no other page visit, the
  run watcher lands a new run within one tick, the one roots function;
* two SM windows on one chip, a crash mid-insertion, a process killed mid
  catch-up -- the ledger is consistent afterwards.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quam_state_manager.core import hub, hub_build, hub_rules as rules, hub_sync, run_watch
from quam_state_manager.core.hub_store import (
    CHIP_UNCERTAIN, NODE_UNREADABLE, OPS, OVERLAPS_SM_WRITE, REVERTS_TO_EARLIER, REWRITTEN,
    SOURCE_GONE, HubStore)
from quam_state_manager.core.ramcache import Warming

ROOT = Path(__file__).resolve().parent.parent
T0 = int(datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc).timestamp() * 1_000_000)
WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}


@pytest.fixture(autouse=True)
def _inline():
    old = hub._PROJECTOR.inline
    hub.set_inline(True)
    yield
    hub.set_inline(old)


def iso(t_us: int) -> str:
    return datetime.fromtimestamp(t_us / 1e6, tz=timezone.utc).isoformat()


def doc(v, *, name="device", **extra):
    state = {"qubits": {"qA1": {"f": v, "T1": 1e-5}, "qA2": {"f": 2.0}}, "extras": {"chip_name": name}}
    state["qubits"]["qA1"].update(extra)
    return state


def run(root: Path, rid: int, state, *, t_us: int | None = None, day="2026-01-01", start_us=None,
        node: dict | None = None, wiring=None) -> Path:
    t_us = T0 + rid * 10_000_000 if t_us is None else t_us
    hhmmss = datetime.fromtimestamp(t_us / 1e6, tz=timezone.utc).strftime("%H%M%S")
    folder = root / day / f"#{rid}_scan_{hhmmss}"
    (folder / "quam_state").mkdir(parents=True, exist_ok=True)
    meta = {"status": "finished", "name": "scan"}
    if start_us is not None:
        meta["run_start"] = iso(start_us)
    body = {"created_at": iso(t_us), "metadata": meta}
    body.update(node or {})
    (folder / "node.json").write_text(json.dumps(body), encoding="utf-8")
    if state is not None:
        (folder / "quam_state" / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (folder / "quam_state" / "wiring.json").write_text(json.dumps(wiring or WIRING), encoding="utf-8")
    return folder


def sync(chip: Path, *roots: Path, identity=None):
    cs = hub_sync.open_chip(chip, [(str(r), "declared") for r in roots], identity=identity)
    return cs


def tick(chip: Path, *roots: Path):
    hub_sync.on_roots_moved([str(r) for r in roots])


def sweep(chip: Path):
    cs = hub_sync.sync_for(chip)
    cs.request(full=True)
    hub_sync._kick(cs)


def events(chip: Path) -> list[dict]:
    with HubStore(chip) as st:
        return [dict(r) for r in st.conn.execute("SELECT * FROM events ORDER BY ord")]


def rows_of(chip: Path, eid: int) -> dict:
    with HubStore(chip) as st:
        out = {}
        for r in st.conn.execute("SELECT p.path,c.op,c.num,c.txt,c.old_num,c.old_txt,c.proven FROM changes c "
                                 "JOIN paths p USING(pid) WHERE c.eid=?", (eid,)):
            new = json.loads(r["txt"]) if r["txt"] is not None else r["num"]
            old = json.loads(r["old_txt"]) if r["old_txt"] is not None else r["old_num"]
            out[r["path"]] = (r["op"], old, new)
        return out


def own(folder: Path) -> dict:
    return rules.flatten(hub_build.read_doc(folder))


def state_flat(chip: Path, eid: int) -> dict:
    with HubStore(chip) as st:
        return rules.flatten(st.state_at(eid))


def check(chip: Path) -> dict:
    with HubStore(chip) as st:
        out = hub_sync.verify(st)
    assert out["problems"] == [], out["problems"]
    return out


def fold_rows(chip: Path) -> dict[int, dict]:
    """Change rows alone, folded from genesis (no checkpoints, no state_at)."""
    flat, out = {}, {}
    with HubStore(chip) as st:
        for ev in st.conn.execute("SELECT eid,error FROM events ORDER BY ord").fetchall():
            for r in st.conn.execute("SELECT p.path,c.op,c.num,c.txt FROM changes c JOIN paths p USING(pid) "
                                     "WHERE eid=?", (ev["eid"],)):
                if r["op"] == OPS["gone"]:
                    flat.pop(r["path"], None)
                else:
                    flat[r["path"]] = json.loads(r["txt"]) if r["txt"] is not None else r["num"]
            if ev["error"] is None:
                out[ev["eid"]] = dict(flat)
    return out


def by_run(chip: Path) -> dict[int, dict]:
    return {e["run_id"]: e for e in events(chip) if e["kind"] == "run"}


# ======================================================================
# 1. placement by instant, local repair
# ======================================================================

class TestPlacement:
    def test_cold_catch_up_equals_the_offline_build(self, tmp_path):
        root = tmp_path / "data"
        for i, v in enumerate((1.0, 2.0, 2.0, 1.0, 3.0), 1):
            run(root, i, doc(v, extra=list(range(i, i + 20))))
        run(root, 6, None)                     # a stateless run, final (a later one exists)
        (run(root, 7, doc(4.0)) / "node.json").write_text("{torn", encoding="utf-8")
        run(root, 8, doc(5.0))
        hub_build.build(root, tmp_path / "offline")
        sync(tmp_path / "chip", root)
        a, b = events(tmp_path / "offline"), events(tmp_path / "chip")
        keys = ("run_id", "t_utc_us", "n_changes", "state_hash", "base_hash", "flags", "error", "status")
        assert [tuple(e[k] for k in keys) for e in a] == [tuple(e[k] for k in keys) for e in b]
        for ea, eb in zip(a, b):
            assert rows_of(tmp_path / "offline", ea["eid"]) == rows_of(tmp_path / "chip", eb["eid"])
        r7 = by_run(tmp_path / "offline")[7]
        assert r7["error"] is None and r7["flags"] & NODE_UNREADABLE, "both builders keep the saved state"
        assert "_ledger_store" not in hub.Hub.for_chip(tmp_path / "chip").__dict__, \
            "an inline sync leaves no ledger handle open"
        check(tmp_path / "chip")

    def test_a_late_run_is_inserted_and_only_its_successor_is_rediffed(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        folders = {i: run(root, i, doc(float(i), T1=i * 1e-6)) for i in (1, 2, 3, 4)}
        sync(chip, root)
        before = {e["run_id"]: (e["eid"], rows_of(chip, e["eid"])) for e in events(chip)}
        late = run(root, 9, doc(9.0, T1=2e-6, extra=[1, 2]), t_us=T0 + 25_000_000)   # between #2 and #3
        tick(chip, root)
        evs = events(chip)
        assert [e["run_id"] for e in evs] == [1, 2, 9, 3, 4]
        assert hub_sync.sync_for(chip).counts["inserted"] == 1
        new = by_run(chip)
        # the inserted run: diffed against its predecessor (#2)
        assert rows_of(chip, new[9]["eid"]) == {
            "qubits.qA1.extra.0": (OPS["add"], None, 1), "qubits.qA1.extra.1": (OPS["add"], None, 2),
            "qubits.qA1.f": (OPS["set"], 2.0, 9.0)}
        assert new[9]["base_hash"] == new[2]["state_hash"]
        # its successor: re-diffed against it; nobody else touched
        assert rows_of(chip, new[3]["eid"]) == {
            "qubits.qA1.T1": (OPS["set"], 2e-6, 3e-6), "qubits.qA1.extra.0": (OPS["gone"], 1, None),
            "qubits.qA1.extra.1": (OPS["gone"], 2, None), "qubits.qA1.f": (OPS["set"], 9.0, 3.0)}
        assert new[3]["base_hash"] == new[9]["state_hash"]
        for rid in (1, 2, 4):
            assert rows_of(chip, new[rid]["eid"]) == before[rid][1]
        for rid, folder in {**folders, 9: late}.items():
            assert not rules.diff(state_flat(chip, new[rid]["eid"]), own(folder))
        folded = fold_rows(chip)
        for rid, folder in {**folders, 9: late}.items():
            assert not rules.diff(folded[new[rid]["eid"]], own(folder)), rid
        check(chip)

    def test_a_second_folder_merges_into_one_timeline_with_correct_checkpoints(self, tmp_path, monkeypatch):
        monkeypatch.setattr("quam_state_manager.core.hub_store.CHECKPOINT_INTERVAL", 3)
        a, b, chip = tmp_path / "a", tmp_path / "b", tmp_path / "chip"
        folders = {}
        for i in range(1, 9):
            folders[("a", i)] = run(a, i, doc(float(i)), t_us=T0 + i * 20_000_000)
        sync(chip, a)
        assert len(events(chip)) == 8
        for i in range(1, 9):     # the second folder's runs fall between the first one's
            folders[("b", i)] = run(b, i, doc(100.0 + i), t_us=T0 + i * 20_000_000 + 7_000_000)
        sync(chip, a, b)
        evs = events(chip)
        assert len(evs) == 16
        assert [e["t_utc_us"] for e in evs] == sorted(e["t_utc_us"] for e in evs)
        with HubStore(chip) as st:
            assert st.conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] >= 2
            roots = dict(st.conn.execute("SELECT root_id, path FROM roots").fetchall())
            loc = {(Path(roots[r[1]]).name, r[2]): r[0] for r in st.conn.execute(
                "SELECT eid, root_id, rel_path FROM locations")}
        for (which, i), folder in folders.items():
            eid = loc[(which, f"{folder.parent.name}/{folder.name}")]
            assert not rules.diff(state_flat(chip, eid), own(folder)), (which, i)
        out = check(chip)
        assert out["runs_checked"] == 16

    def test_runs_of_the_same_instant_order_by_run_id(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        same = T0 + 40_000_000                      # created_at with one-second resolution
        run(root, 1, doc(1.0), t_us=same)
        run(root, 3, doc(3.0), t_us=same)
        sync(chip, root)
        run(root, 2, doc(2.0), t_us=same)
        tick(chip, root)
        assert [e["run_id"] for e in events(chip)] == [1, 2, 3]
        assert rows_of(chip, by_run(chip)[3]["eid"]) == {"qubits.qA1.f": (OPS["set"], 2.0, 3.0)}
        check(chip)

    def test_ranks_renumber_when_a_gap_runs_out(self, tmp_path, monkeypatch):
        monkeypatch.setattr("quam_state_manager.core.hub_store.ORD_EPS", 0.2)
        root, chip = tmp_path / "data", tmp_path / "chip"
        run(root, 1, doc(1.0), t_us=T0)
        run(root, 2, doc(2.0), t_us=T0 + 1_000_000_000)
        sync(chip, root)
        for k in range(8):       # every late run falls in the SAME gap
            run(root, 10 + k, doc(10.0 + k), t_us=T0 + 1_000_000 * (k + 1))
            tick(chip, root)
        evs = events(chip)
        assert [e["run_id"] for e in evs] == [1, *range(10, 18), 2]
        assert all(b["ord"] > a["ord"] for a, b in zip(evs, evs[1:]))
        check(chip)

    def test_an_error_checkpoint_after_an_insertion_holds_the_new_predecessor(self, tmp_path, monkeypatch):
        monkeypatch.setattr("quam_state_manager.core.hub_store.CHECKPOINT_INTERVAL", 2)
        root, chip = tmp_path / "data", tmp_path / "chip"
        run(root, 1, doc(1.0))
        run(root, 2, None)                                  # stateless, final once #3 exists
        run(root, 3, doc(3.0))
        hub_build.build(root, chip, checkpoint_interval=2)  # S3 puts a checkpoint on the error event
        with HubStore(chip) as st:
            cp = st.conn.execute("SELECT c.eid FROM checkpoints c JOIN events e USING(eid) "
                                 "WHERE e.error IS NOT NULL").fetchone()
            assert cp is not None
        late = run(root, 9, doc(9.0, T1=3e-6), t_us=T0 + 15_000_000)     # before the error event
        sync(chip, root)
        with HubStore(chip) as st:
            assert rules.flatten(st.blob(st.conn.execute("SELECT hash FROM checkpoints WHERE eid=?",
                                                         (cp[0],)).fetchone()[0])) == own(late)
        assert not rules.diff(state_flat(chip, by_run(chip)[3]["eid"]), own(root / "2026-01-01" / next(
            p.name for p in (root / "2026-01-01").iterdir() if p.name.startswith("#3_"))))
        check(chip)

    def test_a_run_whose_node_names_another_instant_moves(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        f = {i: run(root, i, doc(float(i))) for i in (1, 2, 3, 4)}
        sync(chip, root)
        node = json.loads((f[2] / "node.json").read_text())
        node["created_at"] = iso(T0 + 35_000_000)            # now after #3
        (f[2] / "node.json").write_text(json.dumps(node), encoding="utf-8")
        sweep(chip)
        assert [e["run_id"] for e in events(chip)] == [1, 3, 2, 4]
        ev = by_run(chip)
        assert rows_of(chip, ev[3]["eid"]) == {"qubits.qA1.f": (OPS["set"], 1.0, 3.0)}
        assert rows_of(chip, ev[2]["eid"]) == {"qubits.qA1.f": (OPS["set"], 3.0, 2.0)}
        assert rows_of(chip, ev[4]["eid"]) == {"qubits.qA1.f": (OPS["set"], 2.0, 4.0)}
        assert hub_sync.sync_for(chip).counts["moved"] == 1
        check(chip)

    def test_a_rebuilt_ledger_file_is_caught_up_from_scratch(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        for i in (1, 2, 3):
            run(root, i, doc(float(i)))
        sync(chip, root)
        for name in ("ledger.sqlite", "ledger.sqlite-wal", "ledger.sqlite-shm"):
            (chip / name).unlink(missing_ok=True)
        sweep(chip)                       # the same window: its RAM knew every run
        assert [e["run_id"] for e in events(chip)] == [1, 2, 3]
        check(chip)

    def test_a_run_another_window_ingested_is_skipped(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        for i in (1, 2, 3):
            run(root, i, doc(float(i)))
        other = hub_sync.ChipSync(chip)                # a second window, same ledger
        other.set_roots([(str(root), "declared")])
        with HubStore(chip) as st:
            other._bind(st)
            other._list(full=True, dirty=set())
            while other.unread:
                other._read(*other.unread.popleft())
            other._resolve()
            sync(chip, root)                           # this window ingests first
            while other.ready_cands:
                other._ingest(st, other.ready_cands.popleft())
        assert other.counts["present"] == 3 and not other.counts["failed"]
        assert [e["run_id"] for e in events(chip)] == [1, 2, 3]
        check(chip)

    def test_a_long_lived_connection_sees_paths_another_window_added(self, tmp_path):
        with HubStore(tmp_path / "chip") as a, HubStore(tmp_path / "chip") as b:
            pid = b._pid("qubits.qA1.new")
            b.conn.commit()
            assert a._pid("qubits.qA1.new") == pid

    def test_a_reader_adopts_the_ledgers_own_checkpoint_interval(self, tmp_path):
        with HubStore(tmp_path / "chip", checkpoint_interval=7):
            pass
        with HubStore(tmp_path / "chip") as st:
            assert st.checkpoint_interval == 7
        with pytest.raises(ValueError, match="incompatible"):
            HubStore(tmp_path / "chip", checkpoint_interval=9)

    def test_revert_fact_follows_the_current_order(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        run(root, 1, doc(1.0))
        run(root, 3, doc(1.0, T1=5e-6))
        sync(chip, root)
        assert not any(e["flags"] & REVERTS_TO_EARLIER for e in events(chip))
        # a late run between them makes #3's state... no: #3 differs from #1;
        # a late run equal to #1, AFTER #3, is a revert; one equal to #3 before
        # #3 makes #3 an adjacent repeat (never a revert)
        shutil.copytree(root / "2026-01-01" / next(p.name for p in (root / "2026-01-01").iterdir()
                                                   if p.name.startswith("#1_")),
                        root / "2026-01-01" / "#5_scan_120050")
        node = json.loads((root / "2026-01-01" / "#5_scan_120050" / "node.json").read_text())
        node["created_at"] = iso(T0 + 50_000_000)
        (root / "2026-01-01" / "#5_scan_120050" / "node.json").write_text(json.dumps(node))
        tick(chip, root)
        assert by_run(chip)[5]["flags"] & REVERTS_TO_EARLIER
        run(root, 2, doc(1.0, T1=5e-6), t_us=T0 + 25_000_000)
        tick(chip, root)
        flags = {rid: e["flags"] & REVERTS_TO_EARLIER for rid, e in by_run(chip).items()}
        assert flags == {1: 0, 2: 0, 3: 0, 5: REVERTS_TO_EARLIER}
        assert by_run(chip)[3]["n_changes"] == 0, "an adjacent repeat is a zero-change event"
        check(chip)


# ======================================================================
# 2. runs and SM events in one ledger
# ======================================================================

def sm_write(chip: Path, monkeypatch, t_us: int, before: dict, after: dict, *, base="b", post="p",
             path="qubits.qA1.f"):
    """One landed SM write at instant *t_us* (the S4 door, projected inline)."""
    monkeypatch.setattr(hub, "_now", lambda: (t_us, iso(t_us)))
    parts = path.split(".")
    old, new = before, after
    for p in parts:
        old, new = old[p], new[p]
    rec = hub.Hub.for_chip(chip).record(
        "sm_apply", "human:operator", [{"path": path, "old": old, "new": new}], base,
        (json.dumps(after).encode(), json.dumps(WIRING).encode()), "apply", post_hash=post)
    rec.landed()
    monkeypatch.undo()
    with HubStore(chip) as st:
        return st.conn.execute("SELECT eid FROM sm_events WHERE sm_id=?", (rec.id,)).fetchone()[0]


class TestRunsAndSmEvents:
    def test_a_run_inserted_before_an_unanchored_sm_event_anchors_it_first(self, tmp_path, monkeypatch):
        root, chip = tmp_path / "data", tmp_path / "chip"
        s0, s1, s2 = doc(1.0), doc(5.0), doc(6.0)
        e1 = sm_write(chip, monkeypatch, T0 + 10_000_000, s0, s1, base="h0", post="h1")
        e2 = sm_write(chip, monkeypatch, T0 + 30_000_000, s1, s2, base="h1", post="h2")
        with HubStore(chip) as st:
            assert not st.conn.execute("SELECT 1 FROM sm_anchors WHERE eid=?", (e2,)).fetchone(), \
                "the second write chains to the first: replayed, not anchored"
            exact = st.state_at(e2)
        rows2 = rows_of(chip, e2)
        run(root, 1, doc(9.0, T1=7e-6), t_us=T0 + 20_000_000)        # between the two SM writes
        sync(chip, root)
        assert [e["kind"] for e in events(chip)] == ["sm_apply", "run", "sm_apply"]
        with HubStore(chip) as st:
            assert st.conn.execute("SELECT 1 FROM sm_anchors WHERE eid=?", (e2,)).fetchone()
            assert st.state_at(e2) == exact, "the SM write is a fact: its state never moves"
        assert rows_of(chip, e2) == rows2, "an SM event's rows are what SM wrote"
        check(chip)

    def test_an_sm_event_projected_late_takes_its_place_and_the_run_after_it_is_rediffed(
            self, tmp_path, monkeypatch):
        root, chip = tmp_path / "data", tmp_path / "chip"
        run(root, 1, doc(1.0), t_us=T0 + 10_000_000)
        f2 = run(root, 2, doc(7.0), t_us=T0 + 30_000_000)
        sync(chip, root)
        assert rows_of(chip, by_run(chip)[2]["eid"]) == {"qubits.qA1.f": (OPS["set"], 1.0, 7.0)}
        eid = sm_write(chip, monkeypatch, T0 + 20_000_000, doc(1.0), doc(4.0, T1=3e-6), path="qubits.qA1.f")
        assert [e["kind"] for e in events(chip)] == ["run", "sm_apply", "run"]
        r2 = by_run(chip)[2]
        assert rows_of(chip, r2["eid"]) == {"qubits.qA1.f": (OPS["set"], 4.0, 7.0),
                                            "qubits.qA1.T1": (OPS["set"], 3e-6, 1e-5)}
        assert r2["base_hash"] == events(chip)[1]["state_hash"]
        assert not rules.diff(state_flat(chip, r2["eid"]), own(f2))
        assert rows_of(chip, eid) == {"qubits.qA1.f": (OPS["set"], 1.0, 4.0),
                                      "qubits.qA1.T1": (OPS["set"], 1e-5, 3e-6)} or \
            rows_of(chip, eid)["qubits.qA1.f"] == (OPS["set"], 1.0, 4.0)
        check(chip)

    def test_a_run_that_straddles_an_sm_write_is_flagged_whichever_lands_first(self, tmp_path, monkeypatch):
        root, chip = tmp_path / "data", tmp_path / "chip"
        run(root, 1, doc(1.0), t_us=T0 + 50_000_000, start_us=T0 + 5_000_000)
        sync(chip, root)
        assert not by_run(chip)[1]["flags"] & OVERLAPS_SM_WRITE
        sm_write(chip, monkeypatch, T0 + 20_000_000, doc(1.0), doc(2.0))
        assert by_run(chip)[1]["flags"] & OVERLAPS_SM_WRITE
        # the other order: the SM write is in the ledger before the run lands
        run(root, 2, doc(3.0), t_us=T0 + 90_000_000, start_us=T0 + 15_000_000)
        run(root, 3, doc(3.0), t_us=T0 + 95_000_000, start_us=T0 + 91_000_000)
        tick(chip, root)
        flags = {rid: bool(e["flags"] & OVERLAPS_SM_WRITE) for rid, e in by_run(chip).items()}
        assert flags == {1: True, 2: True, 3: False}

    def test_a_late_sm_line_without_bytes_replays_from_its_place(self, tmp_path, monkeypatch):
        """A DERIVED SM event (its bytes were not in RAM, e.g. after a
        restart) is predecessor + entries -- the predecessor at ITS instant."""
        root, chip = tmp_path / "data", tmp_path / "chip"
        run(root, 1, doc(1.0, wave=[0] * 20), t_us=T0 + 10_000_000)
        run(root, 2, doc(1.0, wave=[1] * 20), t_us=T0 + 30_000_000)
        sync(chip, root)
        monkeypatch.setattr(hub, "_now", lambda: (T0 + 20_000_000, iso(T0 + 20_000_000)))
        rec = hub.Hub.for_chip(chip).record("sm_apply", "human", [
            {"path": "qubits.qA1.wave.3", "old": 0, "new": 9}], "b", None, "apply", post_hash="p")
        rec.landed()
        monkeypatch.undo()
        with HubStore(chip) as st:
            eid = st.conn.execute("SELECT eid FROM sm_events WHERE sm_id=?", (rec.id,)).fetchone()[0]
            got = st.state_at(eid)
        want = [0] * 20
        want[3] = 9
        assert got["qubits"]["qA1"]["wave"] == want
        r = rows_of(chip, eid)["qubits.qA1.wave"]
        assert json.loads(json.dumps(r[2]))["_array"] == 20
        flat_want = rules.flatten({"w": want})["w"]
        assert r[2]["_hash"] == flat_want["_hash"], "the row is the list as written at that place"
        check(chip)

    def test_a_projection_runs_under_the_ledger_writer_lock(self, tmp_path, monkeypatch):
        chip = tmp_path / "chip"
        h = hub.Hub.for_chip(chip)
        held = []
        real = hub.project

        def spy(hb, proj=None):
            held.append(hb.writer._is_owned())
            return real(hb, proj)
        monkeypatch.setattr(hub, "project", spy)
        rec = h.record("sm_apply", "human", [], None, None, "apply")
        rec.landed()
        assert held == [True]

    def test_a_rewrite_after_an_sm_write_keeps_the_sm_event(self, tmp_path, monkeypatch):
        root, chip = tmp_path / "data", tmp_path / "chip"
        f1 = run(root, 1, doc(1.0), t_us=T0 + 10_000_000)
        sync(chip, root)
        eid = sm_write(chip, monkeypatch, T0 + 20_000_000, doc(1.0), doc(4.0))
        with HubStore(chip) as st:
            exact = st.state_at(eid)
        rows = rows_of(chip, eid)
        time.sleep(0.02)
        (f1 / "quam_state" / "state.json").write_text(json.dumps(doc(1.5, T1=2e-5)), encoding="utf-8")
        sweep(chip)
        r1 = by_run(chip)[1]
        assert r1["flags"] & REWRITTEN
        assert rows_of(chip, r1["eid"])["qubits.qA1.f"] == (OPS["add"], None, 1.5)
        with HubStore(chip) as st:
            assert st.state_at(eid) == exact
        assert rows_of(chip, eid) == rows
        check(chip)


# ======================================================================
# 3. in-flight, rewritten, deleted, corrupt, foreign
# ======================================================================

class TestRunFolders:
    def test_an_inflight_newest_run_waits_then_lands_when_complete(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        run(root, 1, doc(1.0))
        f2 = run(root, 2, None, node={"metadata": {"status": "running", "name": "scan"}})
        cs = sync(chip, root)
        assert [e["run_id"] for e in events(chip)] == [1]
        assert cs.status()["deferred"] == 1
        (f2 / "quam_state" / "state.json").write_text(json.dumps(doc(2.0)), encoding="utf-8")
        (f2 / "quam_state" / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
        tick(chip, root)
        r2 = by_run(chip)[2]
        assert r2["error"] is None and rows_of(chip, r2["eid"]) == {"qubits.qA1.f": (OPS["set"], 1.0, 2.0)}
        assert cs.status()["deferred"] == 0
        check(chip)

    def test_a_stateless_run_is_final_once_a_later_run_exists(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        run(root, 1, doc(1.0))
        run(root, 2, None)
        sync(chip, root)
        assert len(events(chip)) == 1
        run(root, 3, doc(3.0))
        tick(chip, root)
        evs = by_run(chip)
        assert evs[2]["error"] and evs[2]["status"] == "finished" and evs[2]["n_changes"] == 0
        assert rows_of(chip, evs[3]["eid"]) == {"qubits.qA1.f": (OPS["set"], 1.0, 3.0)}
        check(chip)

    def test_a_late_copy_into_an_old_day_lands_on_the_periodic_listing(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        for i, day in ((1, "2026-01-01"), (2, "2026-01-02"), (3, "2026-01-03")):
            run(root, i, doc(float(i)), day=day)
        cs = sync(chip, root)
        late = run(root, 9, doc(9.0), day="2026-01-01", t_us=T0 + 15_000_000)
        tick(chip, root)                               # the newest days only: not seen yet
        assert 9 not in by_run(chip)
        hub_sync.periodic(now=cs.last_listing + hub_sync.LISTING_EVERY_S + 1)
        assert [e["run_id"] for e in events(chip)] == [1, 9, 2, 3]
        assert not rules.diff(state_flat(chip, by_run(chip)[9]["eid"]), own(late))
        check(chip)

    def test_a_deferred_run_is_looked_at_again_without_a_watcher_tick(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        run(root, 1, doc(1.0))
        f2 = run(root, 2, doc(2.0))
        (f2 / "quam_state" / "state.json").write_text("{\"qubits\": {", encoding="utf-8")   # mid-write
        cs = sync(chip, root)
        assert cs.status()["deferred"] == 1
        (f2 / "quam_state" / "state.json").write_text(json.dumps(doc(2.0)), encoding="utf-8")
        hub_sync.periodic(now=time.monotonic())
        assert by_run(chip)[2]["error"] is None

    def test_a_rewritten_run_is_rediffed_in_place_with_its_successor(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        f = {i: run(root, i, doc(float(i))) for i in (1, 2, 3)}
        sync(chip, root)
        eids = {rid: e["eid"] for rid, e in by_run(chip).items()}
        time.sleep(0.02)
        (f[2] / "quam_state" / "state.json").write_text(json.dumps(doc(2.0, T1=4e-6)), encoding="utf-8")
        sweep(chip)
        ev = by_run(chip)
        assert {rid: e["eid"] for rid, e in ev.items()} == eids, "in place: the same events"
        assert ev[2]["flags"] & REWRITTEN and not ev[3]["flags"] & REWRITTEN
        assert rows_of(chip, eids[2]) == {"qubits.qA1.f": (OPS["set"], 1.0, 2.0),
                                          "qubits.qA1.T1": (OPS["set"], 1e-5, 4e-6)}
        assert rows_of(chip, eids[3]) == {"qubits.qA1.f": (OPS["set"], 2.0, 3.0),
                                          "qubits.qA1.T1": (OPS["set"], 4e-6, 1e-5)}
        assert ev[3]["base_hash"] == ev[2]["state_hash"]
        for rid in (1, 2, 3):
            assert not rules.diff(state_flat(chip, eids[rid]), own(f[rid]))
        check(chip)

    def test_identical_bytes_saved_again_are_not_a_rewrite(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        f1 = run(root, 1, doc(1.0))
        sync(chip, root)
        time.sleep(0.02)
        path = f1 / "quam_state" / "state.json"
        path.write_bytes(path.read_bytes())
        sweep(chip)
        assert not by_run(chip)[1]["flags"] & REWRITTEN
        assert hub_sync.sync_for(chip).counts["unchanged"] == 1

    def test_a_sweep_reads_no_unchanged_run(self, tmp_path, monkeypatch):
        root, chip = tmp_path / "data", tmp_path / "chip"
        for i in range(1, 7):
            run(root, i, doc(float(i)))
        sync(chip, root)
        reads = []
        real = hub_build.read_pair
        monkeypatch.setattr(hub_build, "read_pair", lambda folder: (reads.append(folder), real(folder))[1])
        sweep(chip)
        tick(chip, root)
        assert reads == [], "a watermark that did not move is never re-read"

    def test_a_deleted_run_folder_keeps_its_event_rows_and_replay(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        f = {i: run(root, i, doc(float(i))) for i in (1, 2, 3)}
        sync(chip, root)
        expected = state_flat(chip, by_run(chip)[2]["eid"])
        rows = rows_of(chip, by_run(chip)[2]["eid"])
        moved = tmp_path / "away"
        shutil.move(str(f[2]), str(moved))
        sweep(chip)
        r2 = by_run(chip)[2]
        assert r2["flags"] & SOURCE_GONE
        assert rows_of(chip, r2["eid"]) == rows
        assert state_flat(chip, r2["eid"]) == expected, "replay needs no archive file"
        assert len(events(chip)) == 3
        shutil.move(str(moved), str(f[2]))
        sweep(chip)
        assert not by_run(chip)[2]["flags"] & SOURCE_GONE
        check(chip)

    def test_a_corrupt_node_json_keeps_the_saved_state(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        run(root, 1, doc(1.0))
        f2 = run(root, 2, doc(2.0))
        (f2 / "node.json").write_text("{not json", encoding="utf-8")
        cs = sync(chip, root)
        assert cs.status()["deferred"] == 1, "the newest run with a torn node.json may be mid-write"
        run(root, 3, doc(3.0))
        tick(chip, root)
        r2 = by_run(chip)[2]
        assert r2["error"] is None and r2["flags"] & NODE_UNREADABLE
        assert rows_of(chip, r2["eid"]) == {"qubits.qA1.f": (OPS["set"], 1.0, 2.0)}
        assert r2["t_quality"] != "offset" and r2["status"] is None
        assert [e["run_id"] for e in events(chip)] == [1, 2, 3]
        # the node is repaired later: its metadata lands, the flag clears
        (f2 / "node.json").write_text(json.dumps({"created_at": iso(T0 + 20_000_000), "metadata": {
            "status": "finished", "name": "scan"}}), encoding="utf-8")
        sweep(chip)
        r2 = by_run(chip)[2]
        assert not r2["flags"] & NODE_UNREADABLE and r2["status"] == "finished" and r2["t_quality"] == "offset"
        assert [e["run_id"] for e in events(chip)] == [1, 2, 3]
        check(chip)

    def test_a_run_of_another_chip_is_kept_and_flagged(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        # the foreign run comes FIRST: the chip's identity is the open chip's,
        # never whichever run the folder happens to start with
        run(root, 1, doc(5.0, name="other"))
        run(root, 2, doc(1.0))
        run(root, 3, doc(6.0, name="other"))
        sync(chip, root, identity={"name": "device", "fingerprint": None})
        ev = by_run(chip)
        assert [rid for rid, e in ev.items() if e["flags"] & CHIP_UNCERTAIN] == [1, 3]
        assert ev[3]["n_changes"] > 0 and ev[3]["error"] is None, "kept, with its own rows, never dropped"
        check(chip)

    def test_a_folder_deleted_before_it_is_ingested_is_not_an_event(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        f = {i: run(root, i, doc(float(i))) for i in (1, 2, 3)}
        cs = hub_sync.open_chip(chip, [(str(root), "declared")], kick=False)
        with HubStore(chip) as st:
            cs._bind(st)
            cs._list(full=True, dirty=set())
            while cs.unread:
                cs._read(*cs.unread.popleft())
            cs._resolve()
            shutil.rmtree(f[2])                        # gone between the listing and the insert
            while cs.ready_cands:
                cs._ingest(st, cs.ready_cands.popleft())
        assert [e["run_id"] for e in events(chip)] == [1, 3]
        assert cs.counts["vanished"] == 1 and not cs.counts["failed"]
        check(chip)

    def test_a_copy_of_a_folder_is_a_location_not_an_event(self, tmp_path):
        root, copy, chip = tmp_path / "data", tmp_path / "copy", tmp_path / "chip"
        for i in (1, 2):
            run(root, i, doc(float(i)))
        shutil.copytree(root, copy)
        sync(chip, root, copy)
        assert len(events(chip)) == 2
        with HubStore(chip) as st:
            assert st.conn.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 4


# ======================================================================
# 4. status, budget, the watcher, the roots rule
# ======================================================================

class TestStatus:
    def test_building_until_caught_up_and_require_ready_raises(self, tmp_path, monkeypatch):
        root, chip = tmp_path / "data", tmp_path / "chip"
        for i in range(1, 9):
            run(root, i, doc(float(i)))
        cs = hub_sync.open_chip(chip, [(str(root), "declared")], kick=False)
        assert cs.status()["state"] == "building"
        with pytest.raises(hub_sync.Building) as err:
            hub_sync.require_ready(chip)
        assert isinstance(err.value, Warming)
        seen = []
        with HubStore(chip) as st:
            while True:
                more = cs.run_slice(st, 0.0)        # one item per slice at most
                s = cs.status()
                seen.append((s["state"], s["done"], s["total"]))
                if len(seen) == 3:
                    cs.request(roots=[str(root)])   # the watcher wakes it mid catch-up
                if not more:
                    break
        assert seen[-1] == ("ready", 8, 8), "every run counts once in n/N"
        assert any(state == "building" and 0 < done < total for state, done, total in seen)
        assert hub.Hub.for_chip(chip).status()["state"] == "ready"
        assert hub.Hub.for_chip(chip).require_ready()["state"] == "ready"

    def test_a_moved_root_is_building_until_it_is_looked_at(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        run(root, 1, doc(1.0))
        cs = sync(chip, root)
        assert cs.status()["state"] == "ready"
        run(root, 2, doc(2.0))
        cs.request(roots=[str(root)])
        assert cs.status()["state"] == "building"
        with HubStore(chip) as st:
            assert cs.run_slice(st, 0.0)       # looked at, read, not yet in the ledger
            assert cs.status()["state"] == "building", "a read run waiting to land is not 'ready'"
            while cs.run_slice(st, 0.0):
                pass
        assert cs.status()["state"] == "ready"

    def test_the_watcher_wakes_the_hub_without_touching_dataset_listeners(self, tmp_path):
        root, other = tmp_path / "data", tmp_path / "shown"
        run(root, 1, doc(1.0))
        other.mkdir()
        w = run_watch.RunWatcher()
        heard_all, heard_ds = [], []
        w.add_listener(heard_ds.append)
        w.add_listener(heard_all.append, all_roots=True)
        w.set_roots([str(other)])
        w.watch("hub:x", [str(root)])
        assert w.roots == (str(other),)
        w.poll_once()
        assert heard_all == [[str(root)]], "an owner's new root is announced on its first look"
        heard_all.clear()
        tick0 = w.tick
        run(root, 2, doc(2.0), day="2026-01-02")
        assert w.poll_once() is False, "a hub-only root does not move the dataset tick"
        assert w.tick == tick0 and heard_ds == [] and heard_all == [[str(root)]]

    def test_roots_for_chip(self, tmp_path):
        mk = lambda *p: (tmp_path.joinpath(*p).mkdir(parents=True) or str(tmp_path.joinpath(*p)))
        declared, storage, rec = mk("declared"), mk("storage"), mk("recorded")
        mine, foreign = mk("ws", "data", "mine"), mk("ws", "data", "theirs")
        out = hub_sync.roots_for_chip(
            declared=[declared, str(tmp_path / "missing")], project_storage=storage,
            project_roots=[rec, declared], workspace_roots=[mine, foreign],
            decided_same=lambda p: Path(p).name == "mine")
        assert out == [(declared, "declared"), (storage, "project_storage"), (rec, "project_roots"),
                       (mine, "decided_same")]


# ======================================================================
# 5. in SM: chip open, the watcher tick, /hub/status
# ======================================================================

@pytest.fixture
def sm(tmp_path):
    from quam_state_manager.web.app import create_app
    data = tmp_path / "data"
    live = tmp_path / "chips" / "live"
    live.mkdir(parents=True)
    state = doc(1.0)
    state["extras"]["data_folder"] = str(data)
    (live / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    app.config["HUB_SYNC_ON_OPEN"] = True
    return {"app": app, "client": app.test_client(), "live": live, "data": data}


def _chip_of(sm) -> Path:
    from quam_state_manager.web import routes
    with sm["app"].app_context():
        return routes._hub_chip_dir(routes._active_ctx()["path"])


class TestInSm:
    def test_opening_a_chip_catches_up_its_folder_with_no_page_visit(self, sm):
        for i in (1, 2, 3):
            run(sm["data"], i, doc(float(i)))
        assert sm["client"].post("/load", data={"folder": str(sm["live"])}).status_code in (200, 302)
        chip = _chip_of(sm)
        assert [e["run_id"] for e in events(chip)] == [1, 2, 3]
        st = sm["client"].get("/hub/status").get_json()
        assert st["state"] == "ready" and st["done"] == 3 and st["roots"][0]["sources"] == ["declared"]
        check(chip)

    def test_a_run_added_while_open_lands_on_the_watcher_tick(self, sm):
        run(sm["data"], 1, doc(1.0))
        sm["client"].post("/load", data={"folder": str(sm["live"])})
        chip = _chip_of(sm)
        w = run_watch.RunWatcher()
        w.add_listener(hub_sync.on_roots_moved, all_roots=True)
        w.watch("hub", [str(sm["data"])])
        w.poll_once()
        run(sm["data"], 2, doc(2.0), day="2026-01-02")
        w.poll_once()
        assert [e["run_id"] for e in events(chip)] == [1, 2]

    def test_a_testing_app_does_not_sync_unless_asked(self, sm):
        run(sm["data"], 1, doc(1.0))
        sm["app"].config["HUB_SYNC_ON_OPEN"] = False
        sm["client"].post("/load", data={"folder": str(sm["live"])})
        chip = _chip_of(sm)
        assert not (chip / "ledger.sqlite").exists() or events(chip) == []
        assert sm["client"].get("/hub/status").get_json()["state"] == "building"


# ======================================================================
# 6. concurrency and crashes
# ======================================================================

def _py(code: str) -> list[str]:
    return [sys.executable, "-c", f"import sys; sys.path.insert(0, {str(ROOT)!r}); " + code]


def _env():
    return {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}


class TestConcurrency:
    def test_two_windows_on_one_chip_ingest_each_run_once(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        for i in range(1, 41):
            run(root, i, doc(float(i % 7), extra=list(range(i, i + 30))))
        code = ("from quam_state_manager.core import hub_sync; "
                f"hub_sync.catch_up({str(chip)!r}, [{str(root)!r}], budget_s=0.05)")
        procs = [subprocess.Popen(_py(code), env=_env()) for _ in range(2)]
        assert [p.wait(120) for p in procs] == [0, 0]
        evs = events(chip)
        assert sorted(e["run_id"] for e in evs) == list(range(1, 41))
        check(chip)

    def test_a_crash_mid_insertion_leaves_the_ledger_as_it_was(self, tmp_path, monkeypatch):
        root, chip = tmp_path / "data", tmp_path / "chip"
        for i in (1, 2, 3):
            run(root, i, doc(float(i)))
        sync(chip, root)
        with HubStore(chip) as st:
            before = [tuple(r) for r in st.conn.execute("SELECT * FROM events ORDER BY ord")]
            rows_before = [tuple(r) for r in st.conn.execute("SELECT * FROM changes ORDER BY eid, pid")]
        run(root, 9, doc(9.0), t_us=T0 + 15_000_000)
        from quam_state_manager.core.hub_store import HubStore as HS

        def boom(*a, **k):
            raise RuntimeError("killed mid-insertion")
        monkeypatch.setattr(HS, "rediff_run", boom)
        tick(chip, root)
        with HubStore(chip) as st:
            assert [tuple(r) for r in st.conn.execute("SELECT * FROM events ORDER BY ord")] == before
            assert [tuple(r) for r in st.conn.execute("SELECT * FROM changes ORDER BY eid, pid")] == rows_before
        monkeypatch.undo()
        assert hub_sync.sync_for(chip).counts["failed"] == 1
        hub_sync._SYNCS.pop(hub_sync._norm(chip))        # SM restarts: RAM starts over
        sync(chip, root)
        assert [e["run_id"] for e in events(chip)] == [1, 9, 2, 3]
        check(chip)

    def test_killed_mid_catch_up_is_consistent_on_restart(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        big = {f"k{j}": [float(j)] * 40 for j in range(400)}
        for i in range(1, 121):
            run(root, i, doc(float(i), big=big, i=i))
        code = ("from quam_state_manager.core import hub_sync; "
                f"hub_sync.catch_up({str(chip)!r}, [{str(root)!r}])")
        p = subprocess.Popen(_py(code), env=_env())
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                time.sleep(0.05)
                try:
                    import sqlite3
                    con = sqlite3.connect(f"file:{chip / 'ledger.sqlite'}?mode=ro", uri=True, timeout=1)
                    n = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                    con.close()
                except Exception:  # noqa: BLE001 -- not created yet
                    n = 0
                if n >= 10:
                    break
        finally:
            p.kill()
            p.wait(30)
        n_killed = len(events(chip))
        assert 10 <= n_killed < 120, "killed mid catch-up"
        check(chip)
        sync(chip, root)
        evs = events(chip)
        assert sorted(e["run_id"] for e in evs) == list(range(1, 121))
        check(chip)

    def test_a_burst_shares_one_connection_and_releases_it_when_idle(self, tmp_path, monkeypatch):
        root, chip = tmp_path / "data", tmp_path / "chip"
        for i in range(1, 13):
            run(root, i, doc(float(i)))
        opened = []
        from quam_state_manager.core import hub_store
        real_init = hub_store.HubStore.__init__

        def counting(self, *a, **k):
            opened.append(1)
            real_init(self, *a, **k)
        monkeypatch.setattr(hub_store.HubStore, "__init__", counting)
        monkeypatch.setattr(hub_sync, "SLICE_S", 0.0)       # many slices
        hub.set_inline(False)
        try:
            cs = hub_sync.open_chip(chip, [(str(root), "declared")])
            assert hub.flush(60)
            deadline = time.monotonic() + 30
            while cs.has_work() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert hub.flush(60)
        finally:
            hub.set_inline(True)
        assert cs.slices > 3 and len(opened) == 1, (cs.slices, len(opened))
        assert "_ledger_store" not in hub.Hub.for_chip(chip).__dict__, "closed once the projector went idle"
        monkeypatch.undo()
        assert len(events(chip)) == 12
