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
    SOURCE_GONE, UNDONE, HubStore)
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


def age(path: Path, seconds: float = 3600.0) -> None:
    """Make a folder look settled: every file and directory under it was last
    written *seconds* ago (a run still being written waits, docs/275 review)."""
    old = time.time() - seconds
    for p in [path, *path.rglob("*")]:
        os.utime(p, (old, old))


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
        age(root)                              # a settled archive: nothing is being written
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
        f2 = run(root, 2, None)
        sync(chip, root)
        assert len(events(chip)) == 1
        age(f2)                                # the crashed run's files are long settled
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


# ======================================================================
# 7. the review round (docs/275 "Review round"): every refutation, pinned
# ======================================================================

import sqlite3  # noqa: E402
import threading  # noqa: E402

from quam_state_manager.core import qualibrate_config as _qc  # noqa: E402


class _Clock:
    """hub_sync's view of time.monotonic, shifted forward on demand."""

    def __init__(self):
        self.offset = 0.0

    def monotonic(self):
        return time.monotonic() + self.offset

    def __getattr__(self, name):
        return getattr(time, name)


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(hub_sync, "time", c)
    return c


def _rename(a: Path, b: Path) -> None:
    """A folder rename that waits out a scanner's brief hold on fresh files."""
    for _ in range(50):
        try:
            a.rename(b)
            return
        except PermissionError:
            time.sleep(0.1)
    a.rename(b)


def _restart():
    """A new SM process: RAM bookkeeping is rebuilt from the ledger."""
    for h in list(hub.Hub._cache.values()):
        h._close_ledger()
    hub_sync._SYNCS.clear()


class TestReviewSync:
    # -- P1-2: a run whose ingestion raised is retried --------------------
    def test_a_run_whose_ingestion_raised_once_is_retried(self, tmp_path, monkeypatch, clock):
        root, chip = tmp_path / "data", tmp_path / "chip"
        run(root, 1, doc(1.0))
        run(root, 2, doc(2.0))
        sync(chip, root)
        run(root, 3, doc(3.0))
        real = hub_sync.attach_run
        raised = []

        def flaky(store, cand, *a, **k):
            if cand.run.run_id == 3 and not raised:
                raised.append(1)
                raise sqlite3.OperationalError("database is locked")    # another window's long write
            return real(store, cand, *a, **k)
        monkeypatch.setattr(hub_sync, "attach_run", flaky)
        tick(chip, root)
        assert raised and 3 not in by_run(chip)
        cs = hub_sync.sync_for(chip)
        st = cs.status()
        assert st["state"] == "building" and st["failed"] == 1, "a failed run awaiting its retry is not 'ready'"
        clock.offset += 10                         # a periodic listing before the retry is due
        cs.request(listing=True)
        hub_sync.kick(cs)
        assert 3 not in by_run(chip)
        for _ in range(3):
            clock.offset += 40
            hub_sync.periodic()
            tick(chip, root)
        assert 3 in by_run(chip), "run 3 raised once and was never retried"
        assert cs.status()["state"] == "ready" and cs.status()["failed"] == 0

    def test_a_day_whose_run_failed_is_listed_again(self, tmp_path, monkeypatch, clock):
        root, chip = tmp_path / "data", tmp_path / "chip"
        for i in (1, 2):
            run(root, i, doc(float(i)))
        cs = sync(chip, root)
        run(root, 3, doc(3.0))
        real = hub_sync.attach_run
        monkeypatch.setattr(hub_sync, "attach_run", lambda store, cand, *a, **k: (
            (_ for _ in ()).throw(sqlite3.OperationalError("locked")) if cand.run.run_id == 3
            else real(store, cand, *a, **k)))
        tick(chip, root)
        rs = cs.roots[hub_sync._norm(root)]
        cs.request(listing=True)                    # a periodic look before the retry is due
        hub_sync.kick(cs)
        assert rs.failed and "2026-01-01" not in rs.dates, "a day with a failed run is not marked listed"

    def test_a_run_that_keeps_failing_is_reported_not_ready(self, tmp_path, monkeypatch, clock):
        root, chip = tmp_path / "data", tmp_path / "chip"
        run(root, 1, doc(1.0))
        cs = sync(chip, root)
        run(root, 2, doc(2.0))
        real = hub_sync.attach_run
        monkeypatch.setattr(hub_sync, "attach_run", lambda store, cand, *a, **k: (
            (_ for _ in ()).throw(RuntimeError("always")) if cand.run.run_id == 2 else real(store, cand, *a, **k)))
        tick(chip, root)
        for _ in range(hub_sync.MAX_RETRIES + 1):
            clock.offset += hub_sync.RETRY_AFTER_S + 1
            hub_sync.periodic()
        st = cs.status()
        assert st["state"] == "degraded" and st["failed"] == 1, st

    # -- P1-3: status is never "ready" while a found run is in hand -----------
    def test_status_never_says_ready_while_a_moved_root_is_being_listed(self, tmp_path, monkeypatch):
        root, chip = tmp_path / "data", tmp_path / "chip"
        run(root, 1, doc(1.0))
        cs = sync(chip, root)
        assert cs.status()["state"] == "ready"
        real = hub_sync.ChipSync._list_root
        in_listing = threading.Event()

        def slow(self, rs, mode):
            in_listing.set()
            time.sleep(0.6)
            return real(self, rs, mode)
        monkeypatch.setattr(hub_sync.ChipSync, "_list_root", slow)
        run(root, 2, doc(2.0))
        hub.set_inline(False)
        try:
            hub_sync.on_roots_moved([str(root)])
            assert in_listing.wait(5)
            time.sleep(0.1)
            mid = cs.status()["state"]
            landed = 2 in by_run(chip)
            assert hub.flush(30)
            deadline = time.monotonic() + 10
            while cs.has_work() and time.monotonic() < deadline:
                time.sleep(0.05)
        finally:
            hub.set_inline(True)
        assert not (mid == "ready" and not landed), "ready while the moved root's new run was not in the ledger"

    def test_status_never_says_ready_while_the_last_run_is_being_ingested(self, tmp_path, monkeypatch):
        root, chip = tmp_path / "data", tmp_path / "chip"
        run(root, 1, doc(1.0))
        cs = sync(chip, root)
        real = hub_sync.attach_run
        inside = threading.Event()

        def slow(*a, **k):
            inside.set()
            time.sleep(0.6)
            return real(*a, **k)
        monkeypatch.setattr(hub_sync, "attach_run", slow)
        run(root, 2, doc(2.0))
        hub.set_inline(False)
        try:
            hub_sync.on_roots_moved([str(root)])
            assert inside.wait(5)
            time.sleep(0.1)
            mid = cs.status()
            landed = 2 in by_run(chip)
            assert hub.flush(30)
        finally:
            hub.set_inline(True)
        assert not (mid["state"] == "ready" and not landed)

    def test_status_is_building_while_a_periodic_listing_ingests_what_it_found(self, tmp_path, monkeypatch):
        root, chip = tmp_path / "data", tmp_path / "chip"
        run(root, 1, doc(1.0))
        cs = sync(chip, root)
        real = hub_sync.attach_run
        inside = threading.Event()

        def slow(*a, **k):
            inside.set()
            time.sleep(0.6)
            return real(*a, **k)
        monkeypatch.setattr(hub_sync, "attach_run", slow)
        run(root, 2, doc(2.0))
        hub.set_inline(False)
        try:
            cs.request(listing=True)                 # no watcher tick: the 30 s look found it
            hub_sync.kick(cs)
            assert inside.wait(5)
            time.sleep(0.1)
            mid = cs.status()
            landed = 2 in by_run(chip)
            assert hub.flush(30)
        finally:
            hub.set_inline(True)
        assert not (mid["state"] == "ready" and not landed), mid

    # -- P1-6: one failed slice does not stop the catch-up ----------------------
    def test_a_slice_that_raised_is_retried(self, tmp_path, monkeypatch):
        root, chip = tmp_path / "data", tmp_path / "chip"
        for i in range(1, 6):
            run(root, i, doc(float(i)))
        from quam_state_manager.core import hub_store
        real = hub_store.HubStore.__init__
        failed = []

        def flaky(self, *a, **k):
            if threading.current_thread().name == "sm-hub-projector" and not failed:
                failed.append(1)
                raise sqlite3.OperationalError("database is locked")
            real(self, *a, **k)
        monkeypatch.setattr(hub_store.HubStore, "__init__", flaky)
        monkeypatch.setattr(hub, "_PERIODIC_S", 3600.0)       # the backoff alone, no periodic rescue
        hub._PROJECTOR.__dict__["_last_periodic"] = time.monotonic()
        hub.set_inline(False)
        try:
            cs = hub_sync.open_chip(chip, [(str(root), "declared")])
            deadline = time.monotonic() + 20
            while (cs.has_work() or len(events(chip)) < 5) and time.monotonic() < deadline:
                time.sleep(0.1)
            assert hub.flush(30)
        finally:
            hub.set_inline(True)
        assert failed and len(events(chip)) == 5, "one failed slice stopped the catch-up"

    def test_periodic_rescues_a_chip_with_work_and_no_slice_queued(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        run(root, 1, doc(1.0))
        cs = hub_sync.open_chip(chip, [(str(root), "declared")], kick=False)
        assert cs.has_work() and not events(chip) if (chip / "ledger.sqlite").exists() else cs.has_work()
        hub_sync.periodic()
        assert [e["run_id"] for e in events(chip)] == [1]

    # -- the light tick, numerically --------------------------------------------
    def test_a_light_tick_stat_checks_the_newest_runs(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        folders = {i: run(root, i, doc(float(i))) for i in range(1, 121)}
        sync(chip, root)
        time.sleep(0.02)
        (folders[120] / "quam_state" / "state.json").write_text(json.dumps(doc(999.0, T1=5e-6)), encoding="utf-8")
        tick(chip, root)
        assert by_run(chip)[120]["flags"] & REWRITTEN, "the newest run's rewrite was not seen on the light tick"

    # -- OVERLAPS_SM_WRITE for a stateless run, either landing order -----------
    def test_overlap_flag_of_a_stateless_run_does_not_depend_on_landing_order(self, tmp_path, monkeypatch):
        out = {}
        for order in ("run_first", "sm_first"):
            root, chip = tmp_path / order / "data", tmp_path / order / "chip"
            run(root, 1, doc(1.0), t_us=T0 + 1_000_000)
            run(root, 3, doc(1.0), t_us=T0 + 90_000_000)
            if order == "run_first":
                age(run(root, 2, None, t_us=T0 + 50_000_000, start_us=T0 + 5_000_000))
                sync(chip, root)
                sm_write(chip, monkeypatch, T0 + 20_000_000, doc(1.0), doc(2.0))
            else:
                sync(chip, root)
                sm_write(chip, monkeypatch, T0 + 20_000_000, doc(1.0), doc(2.0))
                age(run(root, 2, None, t_us=T0 + 50_000_000, start_us=T0 + 5_000_000))
                sweep(chip)
            out[order] = bool(by_run(chip)[2]["flags"] & OVERLAPS_SM_WRITE)
        assert out == {"run_first": True, "sm_first": True}

    # -- offset votes: one per ingested run -----------------------------------------
    def test_a_deferred_run_is_voted_once(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        run(root, 1, doc(1.0))
        run(root, 2, None)
        cs = sync(chip, root)
        rs = cs.roots[hub_sync._norm(root)]
        before = sum(rs.votes.values())
        for _ in range(20):
            tick(chip, root)
        assert sum(rs.votes.values()) == before == 1

    # -- copies ------------------------------------------------------------------------
    @staticmethod
    def _locs_by_root(chip) -> dict:
        with HubStore(chip) as st:
            return {Path(r[0]).name: (r[1], r[2]) for r in st.conn.execute(
                "SELECT r.path, l.eid, e.t_utc_us FROM locations l JOIN roots r USING(root_id) "
                "JOIN events e USING(eid) WHERE l.rel_path LIKE '%#2_%'")}

    def test_a_move_keeps_the_other_locations(self, tmp_path):
        """A copy whose node.json now names another instant leaves the shared
        event; the other copy keeps its event, its location and its instant
        (one event per instant, run and bytes -- what a build from the files
        gives)."""
        a, b, chip = tmp_path / "a", tmp_path / "b", tmp_path / "chip"
        run(a, 1, doc(1.0))
        f2 = run(a, 2, doc(2.0))
        shutil.copytree(f2, b / f2.parent.name / f2.name)
        sync(chip, a, b)
        before = self._locs_by_root(chip)
        assert before["a"] == before["b"]
        node = json.loads((f2 / "node.json").read_text())
        node["created_at"] = "2026-01-01T11:00:00+00:00"
        (f2 / "node.json").write_text(json.dumps(node), encoding="utf-8")
        sweep(chip)
        after = self._locs_by_root(chip)
        assert after["b"] == before["b"], "the copy that did not move lost its event or its instant"
        assert after["a"][0] != before["a"][0] and after["a"][1] == T0 - 3600 * 10**6, after
        with HubStore(chip) as st:
            assert st.conn.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 3
        check(chip)

    def test_a_move_of_one_copy_is_the_same_ledger_after_a_restart(self, tmp_path):
        a, b, chip = tmp_path / "a", tmp_path / "b", tmp_path / "chip"
        run(a, 1, doc(1.0))
        f2 = run(a, 2, doc(2.0))
        shutil.copytree(f2, b / f2.parent.name / f2.name)
        sync(chip, a, b)
        node = json.loads((f2 / "node.json").read_text())
        node["created_at"] = "2026-01-01T11:00:00+00:00"
        (f2 / "node.json").write_text(json.dumps(node), encoding="utf-8")
        sweep(chip)
        mid = [(e["eid"], e["run_id"], e["t_utc_us"]) for e in events(chip)]
        _restart()
        sync(chip, a, b)
        assert [(e["eid"], e["run_id"], e["t_utc_us"]) for e in events(chip)] == mid, "a restart changed the ledger"
        check(chip)

    def test_copies_that_move_to_one_instant_are_one_event_again(self, tmp_path):
        """The second copy to move joins the event the first one made; the
        event no folder names any more is removed (one event per instant, run
        and bytes, as a build from the files gives)."""
        a, b, chip = tmp_path / "a", tmp_path / "b", tmp_path / "chip"
        run(a, 1, doc(1.0))
        f2 = run(a, 2, doc(2.0))
        g2 = b / f2.parent.name / f2.name
        shutil.copytree(f2, g2)
        sync(chip, a, b)
        for folder in (g2, f2):
            node = json.loads((folder / "node.json").read_text())
            node["created_at"] = "2026-01-01T11:00:00+00:00"
            time.sleep(0.01)
            (folder / "node.json").write_text(json.dumps(node), encoding="utf-8")
            sweep(chip)
        twos = [e for e in events(chip) if e["run_id"] == 2]
        assert [e["t_utc_us"] for e in twos] == [T0 - 3600 * 10**6], twos
        locs = self._locs_by_root(chip)
        assert locs["a"] == locs["b"], locs
        check(chip)

    def test_every_location_of_a_rewritten_copied_run_replays_its_own_bytes(self, tmp_path):
        a, b, chip = tmp_path / "a", tmp_path / "b", tmp_path / "chip"
        run(a, 1, doc(1.0))
        f2 = run(a, 2, doc(2.0))
        g2 = b / f2.parent.name / f2.name
        shutil.copytree(f2, g2)
        sync(chip, b, a)
        time.sleep(0.05)
        (f2 / "quam_state" / "state.json").write_text(json.dumps(doc(7.0, T1=9e-6)), encoding="utf-8")
        sweep(chip)
        with HubStore(chip) as st:
            roots = dict(st.conn.execute("SELECT root_id, path FROM roots").fetchall())
            locs = {Path(roots[r[1]]).name: r[0] for r in st.conn.execute("SELECT eid, root_id, rel_path FROM locations "
                                                                         "WHERE rel_path LIKE '%#2_%'")}
            assert not rules.diff(rules.flatten(st.state_at(locs["a"])), own(f2))
            assert not rules.diff(rules.flatten(st.state_at(locs["b"])), own(g2)), \
                "the unchanged copy replays the rewritten copy's bytes"
            assert hub_sync.verify(st)["problems"] == []

    def test_verify_checks_every_location(self, tmp_path):
        a, b, chip = tmp_path / "a", tmp_path / "b", tmp_path / "chip"
        f1 = run(a, 1, doc(1.0))
        shutil.copytree(f1, b / f1.parent.name / f1.name)
        sync(chip, a, b)
        with HubStore(chip) as st:
            st.conn.execute("DELETE FROM run_files")          # no watermark: read every location
            st.conn.commit()
        (b / f1.parent.name / f1.name / "quam_state" / "state.json").write_text(json.dumps(doc(5.0)),
                                                                                 encoding="utf-8")
        with HubStore(chip) as st:
            assert hub_sync.verify(st)["problems"], "a location whose bytes differ is not reported"

    @staticmethod
    def _two_copies(tmp_path):
        a, b, chip = tmp_path / "a", tmp_path / "b", tmp_path / "chip"
        run(a, 1, doc(1.0))
        f2 = run(a, 2, doc(2.0))
        run(a, 3, doc(3.0))
        c2 = b / f2.parent.name / f2.name
        shutil.copytree(f2, c2)
        age(a)
        age(b)
        sync(chip, a, b)
        with HubStore(chip) as st:
            shared = st.conn.execute("SELECT eid FROM locations GROUP BY eid HAVING COUNT(*) > 1").fetchall()
        assert len(shared) == 1
        return chip, f2, c2, shared[0][0]

    @staticmethod
    def _gone(chip, eid) -> bool:
        with HubStore(chip) as st:
            return bool(st.conn.execute("SELECT flags FROM events WHERE eid=?", (eid,)).fetchone()[0] & SOURCE_GONE)

    def test_a_run_whose_every_copy_is_deleted_is_gone(self, tmp_path):
        """A copy deleted earlier keeps its location row: the event is gone
        when no copy still holds it, not when it has one location left."""
        chip, f2, c2, eid = self._two_copies(tmp_path)
        shutil.rmtree(f2)
        sweep(chip)
        assert not self._gone(chip, eid), "one copy remains"
        shutil.rmtree(c2)
        sweep(chip)
        assert self._gone(chip, eid), "every copy is deleted, yet the event is not SOURCE_GONE"

    def test_every_copy_deleted_before_one_sweep_is_gone(self, tmp_path):
        chip, f2, c2, eid = self._two_copies(tmp_path)
        shutil.rmtree(f2)
        shutil.rmtree(c2)
        sweep(chip)
        assert self._gone(chip, eid), "every copy is deleted, yet the event is not SOURCE_GONE"

    def test_a_copy_that_lost_its_state_while_another_holds_it_is_not_gone(self, tmp_path):
        chip, f2, c2, eid = self._two_copies(tmp_path)
        (f2 / "quam_state" / "state.json").unlink()
        age(f2)
        sweep(chip)
        assert not self._gone(chip, eid), "the other copy still holds the saved state"
        (c2 / "quam_state" / "state.json").unlink()
        age(c2)
        sweep(chip)
        assert self._gone(chip, eid), "no copy holds the saved state any more"

    def test_a_copy_reread_without_its_state_is_not_gone_while_another_holds_it(self, tmp_path, monkeypatch):
        """The re-read path (the state went between the stat and the read)
        follows the same rule as the sweep's."""
        chip, f2, c2, eid = self._two_copies(tmp_path)
        (f2 / "quam_state" / "state.json").unlink()
        age(f2)
        real = hub_sync.file_sig
        monkeypatch.setattr(hub_sync, "file_sig",
                            lambda folder: "1:1|2:2|3:3" if Path(folder) == f2 else real(folder))
        sweep(chip)
        assert hub_sync.sync_for(chip).counts["gone"] >= 1, "the re-read path was not taken"
        assert not self._gone(chip, eid), "the other copy still holds the saved state"

    def test_a_live_copy_is_one_whose_watermark_still_has_its_state(self, tmp_path):
        """``_live_copies`` reads the watermark both ways a copy can lose its
        state: marked gone by the sweep, or stored with no state file (``-``)."""
        chip, f2, c2, eid = self._two_copies(tmp_path)
        with HubStore(chip) as st:
            (ra, rel_a), (rb, rel_b) = st.conn.execute(
                "SELECT root_id, rel_path FROM locations WHERE eid=? ORDER BY root_id", (eid,)).fetchall()
            assert hub_sync._live_copies(st, eid, ra, rel_a) == 1
            for sig in ("gone", "-|10:1|20:2"):
                st.conn.execute("UPDATE run_files SET sig=? WHERE root_id=? AND rel_path=?", (sig, rb, rel_b))
                assert hub_sync._live_copies(st, eid, ra, rel_a) == 0, sig
            st.conn.rollback()

    def test_rewriting_the_last_copy_on_disk_is_a_rewrite_not_a_split(self, tmp_path):
        """With the other copy deleted, a rewrite of the one left is the
        event's own rewrite; splitting it off would leave an event that only a
        deleted folder names, unflagged."""
        chip, f2, c2, eid = self._two_copies(tmp_path)
        shutil.rmtree(f2)
        sweep(chip)
        (c2 / "quam_state" / "state.json").write_text(json.dumps(doc(7.0)), encoding="utf-8")
        age(c2)
        sweep(chip)
        with HubStore(chip) as st:
            orphans = st.conn.execute(
                "SELECT e.eid FROM events e WHERE e.kind='run' AND e.flags & ? = 0 AND NOT EXISTS ("
                "SELECT 1 FROM locations l JOIN run_files f ON f.root_id=l.root_id AND f.rel_path=l.rel_path "
                "WHERE l.eid=e.eid AND f.sig<>'gone')", (SOURCE_GONE,)).fetchall()
        assert orphans == [], "an event that only a deleted folder names is not flagged SOURCE_GONE"
        check(chip)

    # -- in flight ---------------------------------------------------------------------
    def test_a_late_copy_caught_mid_copy_is_not_a_final_error(self, tmp_path, clock):
        root, chip = tmp_path / "data", tmp_path / "chip"
        run(root, 1, doc(1.0), t_us=T0 + 1_000_000)
        run(root, 3, doc(3.0), t_us=T0 + 90_000_000)
        sync(chip, root)
        late = run(root, 2, doc(2.0, T1=4e-6), t_us=T0 + 50_000_000)
        wiring = late / "quam_state" / "wiring.json"
        held = wiring.read_bytes()
        wiring.unlink()
        clock.offset += 31
        hub_sync.periodic()
        mid = by_run(chip).get(2)
        wiring.write_bytes(held)
        clock.offset += 400
        hub_sync.periodic()
        r2 = by_run(chip)[2]
        assert mid is None or mid["error"] is None, "a copy in progress was committed as a final error event"
        assert r2["error"] is None and not r2["flags"] & REWRITTEN

    def test_an_error_event_that_gains_its_state_is_completed_not_rewritten(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        run(root, 1, doc(1.0))
        f2 = run(root, 2, None)
        age(f2)
        run(root, 3, doc(3.0))
        sync(chip, root)
        assert by_run(chip)[2]["error"]
        (f2 / "quam_state" / "state.json").write_text(json.dumps(doc(2.0)), encoding="utf-8")
        (f2 / "quam_state" / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
        sweep(chip)
        r2 = by_run(chip)[2]
        assert r2["error"] is None and not r2["flags"] & REWRITTEN
        assert hub_sync.sync_for(chip).counts["completed"] == 1
        check(chip)

    def test_a_run_in_flight_in_the_same_second_as_the_previous_save_waits(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        run(root, 1, doc(1.0), t_us=T0 + 25_300_000)               # created_at 12:00:25.3
        sync(chip, root)
        (root / "2026-01-01" / "#2_scan_120025").mkdir(parents=True)   # folder clock 12:00:25, nothing in it
        tick(chip, root)
        assert 2 not in by_run(chip), "a run still being written was committed as a final error event"

    def test_repairing_a_corrupt_node_keeps_the_event_and_is_not_a_rewrite(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        run(root, 1, doc(1.0))
        f2 = run(root, 2, doc(2.0))
        (f2 / "node.json").write_text("{not json", encoding="utf-8")
        run(root, 3, doc(3.0))
        sync(chip, root)
        r2 = by_run(chip)[2]
        assert r2["flags"] & NODE_UNREADABLE
        (f2 / "node.json").write_text(json.dumps({"created_at": iso(T0 + 20_250_000), "metadata": {
            "status": "finished", "name": "scan"}}), encoding="utf-8")
        sweep(chip)
        r2b = by_run(chip)[2]
        assert r2b["eid"] == r2["eid"], "the repaired run became a new event"
        assert not r2b["flags"] & (REWRITTEN | NODE_UNREADABLE)
        check(chip)

    def test_a_move_that_renumbers_the_ledger_lands(self, tmp_path, monkeypatch):
        """A run being re-placed holds a negative rank (-eid) while its new
        place is found; a renumber in that window must not collide with it
        (SQLite checks UNIQUE row by row: rank 5 negated is -6, the rank of
        the moved eid 6). Found by the fuzz with a small ORD_EPS."""
        monkeypatch.setattr("quam_state_manager.core.hub_store.ORD_EPS", 0.6)
        root, chip = tmp_path / "data", tmp_path / "chip"
        folders = [run(root, i, doc(float(i)), t_us=T0 + i * 10_000_000) for i in range(1, 7)]
        sync(chip, root)
        age(run(root, 7, doc(7.0), t_us=T0 + 15_000_000))        # rank 1.5, between #1 and #2
        sweep(chip)
        f6 = folders[5]
        node = json.loads((f6 / "node.json").read_text())
        node["created_at"] = iso(T0 + 12_000_000)                 # between #1 (1.0) and #7 (1.5): renumbers
        (f6 / "node.json").write_text(json.dumps(node), encoding="utf-8")
        sweep(chip)
        assert not hub_sync.sync_for(chip).roots[next(iter(hub_sync.sync_for(chip).roots))].failed
        r6 = by_run(chip)[6]
        assert r6["t_utc_us"] == T0 + 12_000_000, "the move failed and the run stayed at its old instant"
        assert [e["run_id"] for e in events(chip)] == [1, 6, 7, 2, 3, 4, 5]
        check(chip)

    def test_an_error_events_base_hash_follows_a_late_insertion_like_the_offline_build(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        run(root, 1, doc(1.0), t_us=T0 + 10_000_000)
        age(run(root, 3, None, t_us=T0 + 30_000_000))
        run(root, 4, doc(4.0), t_us=T0 + 40_000_000)
        sync(chip, root)
        age(run(root, 2, doc(2.0), t_us=T0 + 20_000_000))
        tick(chip, root)
        sweep(chip)
        hub_build.build(root, tmp_path / "offline")
        a = {e["run_id"]: e["base_hash"] for e in events(tmp_path / "offline")}
        b = {e["run_id"]: e["base_hash"] for e in events(chip)}
        assert a == b

    # -- a data folder that cannot be listed ----------------------------------------
    def test_an_unreachable_data_folder_is_not_every_run_deleted(self, tmp_path):
        root, chip = tmp_path / "share" / "data", tmp_path / "chip"
        for i in range(1, 6):
            run(root, i, doc(float(i)))
        sync(chip, root)
        _rename(tmp_path / "share", tmp_path / "share_offline")
        sigs = []
        real_sig = hub_sync.file_sig
        hub_sync.file_sig = lambda f: (sigs.append(f), real_sig(f))[1]
        try:
            sweep(chip)
        finally:
            hub_sync.file_sig = real_sig
        assert sigs == [], "the runs of a root that cannot be listed were stat'ed one by one"
        st = hub_sync.status(chip)
        assert not [rid for rid, e in by_run(chip).items() if e["flags"] & SOURCE_GONE]
        assert st["state"] == "degraded" and st["unreadable"], st
        assert hub_sync.require_ready(chip)["state"] == "degraded"
        _rename(tmp_path / "share_offline", tmp_path / "share")
        sweep(chip)
        assert hub_sync.status(chip)["state"] == "ready"

    # -- one vanished folder never wedges the sync ---------------------------------
    def test_a_folder_that_vanishes_before_it_is_resolved_does_not_wedge_the_sync(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        for i in range(1, 4):
            run(root, i, doc(float(i)))
        cs = sync(chip, root)
        odd = root / "2026-01-01" / "#7_scan_996199"        # no clock: its instant needs the folder's mtime
        odd.mkdir()
        run(root, 8, doc(8.0), t_us=T0 + 80_000_000)
        cs.request(roots=[str(root)])
        with HubStore(chip) as st:
            hub_sync.run(chip, st, 0.0)                     # one item read
            shutil.rmtree(odd)
            for _ in range(5):
                hub_sync.run(chip, st, 0.0)
        assert 8 in by_run(chip) and not cs.read

    # -- every chip ever opened stays watched and swept -------------------------
    def test_only_the_open_chip_is_swept(self, tmp_path, monkeypatch):
        old_root, old_chip = tmp_path / "old_data", tmp_path / "old_chip"
        run(old_root, 1, doc(1.0))
        sync(old_chip, old_root)
        root, chip = tmp_path / "data", tmp_path / "chip"
        for i in range(1, 4):
            run(root, i, doc(float(i)))
        sync(chip, root)                                # the open chip changes
        from quam_state_manager.core import hub_store
        real_init = hub_store.HubStore.__init__
        opened = []

        def counting(self, directory, *a, **k):
            opened.append(str(directory))
            real_init(self, directory, *a, **k)
        monkeypatch.setattr(hub_store.HubStore, "__init__", counting)
        hub_sync.periodic(now=time.monotonic() + 10_000)
        hub_sync.on_roots_moved([str(old_root)])
        assert str(old_chip) not in opened, "a closed chip's ledger is opened and swept"
        assert not hub_sync.sync_for(old_chip).active

    # -- deep archives: bounded listing, slices that yield ------------------------
    def test_a_periodic_listing_stats_a_bounded_chunk_of_days(self, tmp_path, monkeypatch, clock):
        root, chip = tmp_path / "data", tmp_path / "chip"
        for d in range(1, 13):
            run(root, d, doc(float(d)), day=f"2026-01-{d:02d}")
        cs = sync(chip, root)
        monkeypatch.setattr(hub_sync, "LISTING_CHUNK", 3)
        stats = []
        real_stat = os.stat

        class _Os:
            def __getattr__(self, name):
                return getattr(os, name)

            @staticmethod
            def stat(p, *a, **k):
                if hub_sync._DAY.fullmatch(Path(p).name):
                    stats.append(Path(p).name)
                return real_stat(p, *a, **k)
        monkeypatch.setattr(hub_sync, "os", _Os())
        late = run(root, 99, doc(99.0), day="2026-01-07", t_us=T0 + 6 * 86_400_000_000 + 500_000)
        looks = 0
        while 99 not in by_run(chip) and looks < 6:
            stats.clear()
            clock.offset += hub_sync.LISTING_EVERY_S + 1
            hub_sync.periodic()
            assert len(stats) <= 3 + hub_sync.NEWEST_DATES, stats
            looks += 1
        assert 99 in by_run(chip) and looks > 1, (looks, "the late copy into an old day lands within a few looks")
        assert not rules.diff(state_flat(chip, by_run(chip)[99]["eid"]), own(late))

    def test_a_slice_ends_after_one_item_when_a_request_waits(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        for i in range(1, 7):
            run(root, i, doc(float(i)))
        cs = hub_sync.open_chip(chip, [(str(root), "declared")], kick=False)
        with HubStore(chip) as st:
            assert cs.run_slice(st, None, should_yield=lambda: True)
            assert len(cs.unread) == 5, "the slice went on past one item while a request waited"
            while cs.run_slice(st, None):
                pass
        assert len(events(chip)) == 6


class TestReviewRoots:
    @pytest.fixture
    def two_projects(self, tmp_path, monkeypatch, any_project_env_chosen):
        """One storage location in the ROOT config (no project template): qualibrate
        writes each project's runs to <location>/<project>/<date>/... Two projects,
        two chips."""
        cfg = tmp_path / ".qualibrate"
        chips = {}
        for tag, name, v in (("a", "chip-a", 1.0), ("b", "chip-b", 9.0)):
            f = tmp_path / "chips" / f"chip_{tag}"
            f.mkdir(parents=True)
            (f / "state.json").write_text(json.dumps(doc(v, name=name)), encoding="utf-8")
            (f / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
            chips[tag] = f
        storage = tmp_path / "datasets"
        (cfg / "projects" / "pa").mkdir(parents=True)
        (cfg / "projects" / "pb").mkdir(parents=True)
        (cfg / "config.toml").write_text(
            f'[qualibrate]\nproject = "pa"\nversion = 5\n\n[qualibrate.storage]\nlocation = "{storage.as_posix()}"\n\n'
            f'[quam]\nstate_path = "{chips["a"].as_posix()}"\nversion = 3\n', encoding="utf-8")
        (cfg / "projects" / "pa" / "config.toml").write_text(
            f'[quam]\nstate_path = "{chips["a"].as_posix()}"\n', encoding="utf-8")
        (cfg / "projects" / "pb" / "config.toml").write_text(
            f'[quam]\nstate_path = "{chips["b"].as_posix()}"\n', encoding="utf-8")
        monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(cfg))
        monkeypatch.delenv("QUALIBRATE_CONFIG_DIR", raising=False)
        _qc._state_index_cache.clear()
        run(storage / "pa", 1, doc(1.0, name="chip-a"))
        run(storage / "pa", 2, doc(2.0, name="chip-a"))
        run(storage / "pb", 1, doc(9.0, name="chip-b"))
        run(storage / "pb", 7, doc(8.0, name="chip-b"))
        from quam_state_manager.web.app import create_app
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        app.config["HUB_SYNC_ON_OPEN"] = True
        return {"app": app, "client": app.test_client(), "chip_a": chips["a"], "storage": storage}

    def test_opening_a_project_syncs_its_own_runs_and_no_other_chips(self, two_projects):
        c = two_projects["client"]
        assert c.post("/qualibrate/open", data={"project": "pa"}).status_code in (200, 302)
        st1 = c.get("/hub/status").get_json()
        chip_dir = Path(st1["chip_dir"])
        first = [(e["run_id"], bool(e["flags"] & CHIP_UNCERTAIN)) for e in events(chip_dir)]
        c.post("/load", data={"folder": str(two_projects["chip_a"])})
        second = [(e["run_id"], bool(e["flags"] & CHIP_UNCERTAIN), e["state_ref"]) for e in events(chip_dir)]
        assert first == [(1, False), (2, False)], "the first open did not sync the project's own runs"
        assert [r[:2] for r in second] == [(1, False), (2, False)], "another project's runs were synced"
        assert all(Path(r[2]).parent.parent.name == "pa" for r in second)

    def test_a_project_known_only_after_the_pin_is_synced(self, tmp_path, monkeypatch, any_project_env_chosen):
        """Two projects share one state_path: activation cannot tell which one
        it is (no scope) -- only /qualibrate/open's pin names it."""
        cfg = tmp_path / ".qualibrate"
        chip = tmp_path / "chips" / "shared"
        chip.mkdir(parents=True)
        (chip / "state.json").write_text(json.dumps(doc(1.0, name="chip-a")), encoding="utf-8")
        (chip / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
        storage = tmp_path / "datasets"
        other = tmp_path / "chips" / "other"
        other.mkdir(parents=True)
        for p, sp in (("pa", chip), ("pc", chip), ("pz", other)):
            (cfg / "projects" / p).mkdir(parents=True)
            (cfg / "projects" / p / "config.toml").write_text(f'[quam]\nstate_path = "{sp.as_posix()}"\n',
                                                               encoding="utf-8")
        # qualibrate's ACTIVE project is a third one: the derive at activation
        # sees two matches and refuses to guess, so no scope is known yet
        (cfg / "config.toml").write_text(
            f'[qualibrate]\nproject = "pz"\nversion = 5\n\n[qualibrate.storage]\nlocation = "{storage.as_posix()}"\n\n'
            f'[quam]\nstate_path = "{other.as_posix()}"\nversion = 3\n', encoding="utf-8")
        monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(cfg))
        monkeypatch.delenv("QUALIBRATE_CONFIG_DIR", raising=False)
        _qc._state_index_cache.clear()
        run(storage / "pa", 1, doc(1.0, name="chip-a"))
        from quam_state_manager.web.app import create_app
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        app.config["HUB_SYNC_ON_OPEN"] = True
        c = app.test_client()
        assert c.post("/qualibrate/open", data={"project": "pa"}).status_code in (200, 302)
        st = c.get("/hub/status").get_json()
        assert [r["sources"] for r in st["roots"]] == [["project_storage"]], st["roots"]
        assert [e["run_id"] for e in events(Path(st["chip_dir"]))] == [1]

    def test_project_run_root_and_sibling_folders(self, tmp_path):
        loc = tmp_path / "datasets"
        for p in ("pa", "pb"):
            (loc / p).mkdir(parents=True)
        assert hub_sync.project_run_root(str(loc), "pa") == str(loc / "pa")
        assert hub_sync.project_run_root(str(loc / "pa"), "pa") == str(loc / "pa")
        out = hub_sync.roots_for_chip(project_storage=str(loc / "pa"), shared_location=str(loc),
                                      project_roots=[str(loc / "pa"), str(loc / "pb")])
        assert out == [(str(loc / "pa"), "project_storage")]

    def test_a_decided_same_root_must_be_named(self, tmp_path):
        from quam_state_manager.core.history import _chip_decisions_file
        from quam_state_manager.core import path_match
        from quam_state_manager.web import routes
        from quam_state_manager.web.app import create_app
        live = tmp_path / "chips" / "live"
        live.mkdir(parents=True)
        (live / "state.json").write_text(json.dumps(doc(1.0, name="chip-a")), encoding="utf-8")
        (live / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
        mine = tmp_path / "lab1" / "data" / "QPU" / "chip_a_runs"
        theirs = tmp_path / "lab1" / "data" / "QPU" / "chip_b_runs"
        run(mine, 1, doc(1.0, name="chip-a"))
        run(theirs, 1, doc(9.0, name="chip-b"))
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        c.post("/load", data={"folder": str(live)})
        with app.test_request_context():
            ws = routes._ws()
            ws.add_root(str(mine), defer_parse=True)
            ws.add_root(str(theirs), defer_parse=True)
            ctx = routes._active_ctx()
            chip_dir = routes._hub_chip_dir(ctx["path"])
            f = _chip_decisions_file(app.instance_path)
            # a label shared by two roots is evidence for neither
            f.write_text(json.dumps({f"{chip_dir.name}::QPU": "same"}), encoding="utf-8")
            assert routes._hub_roots_for(ctx) == []
            # a decision that names the root
            f.write_text(json.dumps({f"{chip_dir.name}::root:{path_match.fs_key(str(mine))}": "same"}),
                         encoding="utf-8")
            assert [Path(p).name for p, _ in routes._hub_roots_for(ctx)] == ["chip_a_runs"]


class TestReviewIdentity:
    @staticmethod
    def _nameless(v, extra_qubit=False):
        s = {"qubits": {"qA1": {"f": v}, "qA2": {"f": 2.0}}}
        if extra_qubit:
            s["qubits"]["qA3"] = {"f": 3.0}
        return s

    @pytest.mark.parametrize("change", ["qubit_added_since", "network_changed_since"])
    def test_the_chips_own_history_is_not_flagged_foreign(self, tmp_path, change):
        from quam_state_manager.web import routes
        from quam_state_manager.web.app import create_app
        data = tmp_path / "data"
        for i in (1, 2, 3):
            run(data, i, self._nameless(float(i)))
        live = tmp_path / "chips" / "live"
        live.mkdir(parents=True)
        state = self._nameless(4.0, extra_qubit=(change == "qubit_added_since"))
        state["extras"] = {"data_folder": str(data)}
        wiring = WIRING if change != "network_changed_since" else {"network": {"host": "10.0.0.2",
                                                                              "cluster_name": "C1"}}
        (live / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (live / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        app.config["HUB_SYNC_ON_OPEN"] = True
        app.test_client().post("/load", data={"folder": str(live)})
        with app.test_request_context():
            chip = routes._hub_chip_dir(routes._active_ctx()["path"])
        hub_build.build(data, tmp_path / "offline")
        flags = lambda d: [(e["run_id"], bool(e["flags"] & CHIP_UNCERTAIN)) for e in events(d)]
        assert flags(chip) == flags(tmp_path / "offline") == [(1, False), (2, False), (3, False)]

    def test_two_chips_that_share_a_chip_name_are_told_apart(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        for i, (qs, host) in enumerate(((("qA1", "qA2"), "10.0.0.1"), (("qB1", "qB2", "qB3"), "10.0.0.9")), 1):
            st = {"qubits": {q: {"f": float(i)} for q in qs}, "extras": {"chip_name": "QPU"}}
            run(root, i, st, wiring={"network": {"host": host, "cluster_name": "c"}})
        sync(chip, root, identity=hub_build._chip_identity(
            {"qubits": {"qA1": {}, "qA2": {}}, "extras": {"chip_name": "QPU"}},
            {"network": {"host": "10.0.0.1", "cluster_name": "c"}}, tmp_path))
        assert [(e["run_id"], bool(e["flags"] & CHIP_UNCERTAIN)) for e in events(chip)] == [(1, False), (2, True)]

    def test_a_named_chip_that_gained_a_qubit_is_still_itself(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        run(root, 1, doc(1.0))
        grown = doc(2.0)
        grown["qubits"]["qA3"] = {"f": 3.0}
        run(root, 2, grown, wiring={"network": {"host": "10.0.0.5", "cluster_name": "c"}})
        sync(chip, root, identity={"name": "device", "fingerprint": "x", "qubits": ["qA1", "qA2"]})
        assert not any(e["flags"] & CHIP_UNCERTAIN for e in events(chip))


class TestReviewCausal:
    """P1-5: SM writes follow causality, not wall clocks."""

    @staticmethod
    def _chash(folder):
        from quam_state_manager.core import working_copy
        return working_copy.content_hash(json.loads((folder / "quam_state" / "state.json").read_text()),
                                         json.loads((folder / "quam_state" / "wiring.json").read_text()))

    def _sm(self, chip, monkeypatch, t_us, after, base, post="p1", derived=False, undoes=None, units=None):
        monkeypatch.setattr(hub, "_now", lambda: (t_us, iso(t_us)))
        rec = hub.Hub.for_chip(chip).record(
            "undo" if undoes else "sm_apply", "human", [{"path": "qubits.qA1.f", "old": 2.0, "new": 5.0}], base,
            None if derived else (json.dumps(after).encode(), json.dumps(WIRING).encode()), "apply",
            post_hash=post, undoes=undoes, units=units)
        rec.landed()
        monkeypatch.undo()
        return rec.id

    @pytest.mark.parametrize("order", ["run_first", "sm_first"])
    def test_a_run_whose_clock_is_ahead_is_still_the_base_of_the_write(self, tmp_path, monkeypatch, order):
        root, chip = tmp_path / "data", tmp_path / "chip"
        run(root, 1, doc(1.0), t_us=T0 + 10_000_000)
        r2 = run(root, 2, doc(2.0), t_us=T0 + 80_000_000)       # saved at ~T0+20 s, its PC's clock 60 s ahead
        base = self._chash(r2)
        after = doc(5.0)
        if order == "run_first":
            sync(chip, root)
            sid = self._sm(chip, monkeypatch, T0 + 30_000_000, after, base)
        else:
            (root / "2026-01-01" / r2.name).rename(tmp_path / "held")
            sync(chip, root)
            sid = self._sm(chip, monkeypatch, T0 + 30_000_000, after, base)
            (tmp_path / "held").rename(root / "2026-01-01" / r2.name)
            tick(chip, root)
        kinds = [(e["kind"], e["run_id"]) for e in events(chip)]
        assert kinds == [("run", 1), ("run", 2), ("sm_apply", None)], kinds
        with HubStore(chip) as st:
            eid = st.conn.execute("SELECT eid FROM sm_events WHERE sm_id=?", (sid,)).fetchone()[0]
        assert rows_of(chip, eid)["qubits.qA1.f"] == (OPS["set"], 2.0, 5.0)
        assert rows_of(chip, by_run(chip)[2]["eid"]) == {"qubits.qA1.f": (OPS["set"], 1.0, 2.0)}, \
            "the run shows the user's write reverted"
        check(chip)

    def test_an_undo_with_an_earlier_clock_still_undoes(self, tmp_path, monkeypatch):
        chip = tmp_path / "chip"
        apply_id = self._sm(chip, monkeypatch, T0 + 50_000_000, doc(5.0), "b0", post="p1", units=["u1"])
        # another window (its own working copy: an unrelated base), clock behind
        self._sm(chip, monkeypatch, T0 + 40_000_000, doc(2.0), "w2", post="p2",
                 undoes=[{"event": apply_id, "units": ["u1"]}], units=["u1"])
        with HubStore(chip) as st:
            rows = st.conn.execute("SELECT s.sm_id, e.flags FROM sm_events s JOIN events e USING(eid) "
                                   "ORDER BY e.ord").fetchall()
        assert [r[0] for r in rows][0] == apply_id, "journal order: the apply comes first"
        assert dict(rows)[apply_id] & UNDONE

    def test_a_write_after_a_backward_clock_step_derives_from_the_write_before(self, tmp_path, monkeypatch):
        chip = tmp_path / "chip"
        self._sm(chip, monkeypatch, T0 + 50_000_000, doc(5.0, T1=7e-6), "b0", post="p1")
        # the clock stepped back; this window's base hash names nothing else
        sid = self._sm(chip, monkeypatch, T0 + 20_000_000, None, "w1", post="p2", derived=True)
        with HubStore(chip) as st:
            eid = st.conn.execute("SELECT eid FROM sm_events WHERE sm_id=?", (sid,)).fetchone()[0]
            got = st.state_at(eid)
        assert got["qubits"]["qA1"]["T1"] == 7e-6, "the derived write invented a state from nothing"


class TestReviewS4AndStore:
    def test_a_line_read_before_it_is_registered_is_not_unconfirmed(self, tmp_path, monkeypatch):
        chip = tmp_path / "chip"
        h = hub.Hub.for_chip(chip)
        real = hub.Hub._append

        seen = []

        def append_then_project(self, obj, *a, **k):
            out = real(self, obj, *a, **k)
            if "id" in obj:
                hub.project(self)            # a projector reads the journal in the gap
                with HubStore(chip) as st:
                    seen.append(st.conn.execute("SELECT outcome FROM sm_events WHERE sm_id=?",
                                                (obj["id"],)).fetchone())
            return out
        monkeypatch.setattr(hub.Hub, "_append", append_then_project)
        rec = h.record("sm_apply", "human", [], None, (b'{"qubits": {}}', b"{}"), "apply")
        monkeypatch.undo()
        rec.landed()
        with HubStore(chip) as st:
            row = st.conn.execute("SELECT outcome FROM sm_events WHERE sm_id=?", (rec.id,)).fetchone()
        assert seen == [None], "the line was projected (as unknown) while its writer was still writing"
        assert row is not None and row[0] == "landed"

    def test_a_derived_write_is_computed_inside_its_transaction(self, tmp_path, monkeypatch):
        """P1-4: another window ingests a run just before an SM line with no
        bytes is placed; the write's rows come from the predecessor it is
        placed after, so every later replay works."""
        root, chip = tmp_path / "data", tmp_path / "chip"
        want = [1] * 20
        want[3] = 9
        run(root, 1, doc(1.0, wave=[0] * 20), t_us=T0 + 10_000_000)
        run(root, 3, doc(1.0, wave=want), t_us=T0 + 30_000_000)
        sync(chip, root)
        window_a = hub_sync.ChipSync(chip)
        window_a.set_roots([(str(root), "declared")])
        from quam_state_manager.core.hub_store import HubStore as HS
        real = HS.append_sm
        raced = []

        def racing(self, **kw):
            if not raced:
                raced.append(1)
                run(root, 2, doc(1.0, wave=[1] * 20), t_us=T0 + 20_000_000)
                window_a.request(full=True)
                with HubStore(chip) as st_a:
                    while window_a.run_slice(st_a, None):
                        pass
            return real(self, **kw)
        monkeypatch.setattr(HS, "append_sm", racing)
        monkeypatch.setattr(hub, "_now", lambda: (T0 + 25_000_000, iso(T0 + 25_000_000)))
        rec = hub.Hub.for_chip(chip).record("sm_apply", "human", [{"path": "qubits.qA1.wave.3", "old": 1, "new": 9}],
                                            "b", None, "apply", post_hash="p")
        rec.landed()
        monkeypatch.undo()
        assert raced
        assert [e["run_id"] for e in events(chip)] == [1, 2, None, 3]
        with HubStore(chip) as st:
            st.conn.execute("DELETE FROM checkpoints")       # replay through the SM rows
            st.conn.commit()
            for e in st.conn.execute("SELECT eid FROM events WHERE error IS NULL").fetchall():
                st.state_at(e[0])
            sm = st.conn.execute("SELECT eid FROM sm_events WHERE sm_id=?", (rec.id,)).fetchone()[0]
            assert st.state_at(sm)["qubits"]["qA1"]["wave"] == want
        check(chip)

    def test_opening_a_ledger_takes_no_write_lock(self, tmp_path):
        chip = tmp_path / "chip"
        HubStore(chip).close()
        holder = sqlite3.connect(chip / "ledger.sqlite", timeout=1, check_same_thread=False)
        holder.execute("BEGIN IMMEDIATE")
        released = threading.Timer(3.0, holder.rollback)
        released.start()
        t0 = time.monotonic()
        try:
            HubStore(chip).close()
            took = time.monotonic() - t0
        finally:
            released.join()
            holder.close()
        assert took < 1.0, f"opening the ledger waited {took:.1f} s for another window's write"

    def test_run_files_are_read_through_a_share_delete_handle(self, tmp_path, monkeypatch):
        folder = run(tmp_path / "data", 1, doc(1.0))
        from quam_state_manager.core import safe_io
        real = safe_io.open_shared
        seen = []

        def spy(path):
            seen.append(Path(path).name)
            return real(path)
        monkeypatch.setattr(safe_io, "open_shared", spy)
        hub_build.read_pair(folder)
        hub_build.read_node(folder)
        assert {"state.json", "wiring.json", "node.json"} <= set(seen)

    def test_a_root_the_hub_saw_first_is_baselined_for_the_datasets_page(self, tmp_path):
        root = tmp_path / "data"
        run(root, 1, doc(1.0))
        w = run_watch.RunWatcher()
        ds = []
        w.add_listener(ds.append)
        w.watch("hub", [str(root)])
        w.poll_once()
        run(root, 2, doc(2.0))                          # lands before the Datasets page adds the root
        w.set_roots([str(root)])
        w.poll_once()
        assert ds == [] and w.tick == 0, "exactly as before: a root added to Datasets is a baseline"
        # ... taken when Datasets adds it, not at the thread's next look: a run
        # landing in between is announced, exactly as for a root the hub never saw
        root2 = tmp_path / "data2"
        run(root2, 1, doc(1.0))
        w.watch("hub", [str(root), str(root2)])
        w.poll_once()
        w.set_roots([str(root), str(root2)])
        run(root2, 2, doc(2.0))
        w.poll_once()
        assert ds == [[str(root2)]] and w.tick == 1, ds

    def test_two_windows_split_and_flag_the_same_event(self, tmp_path):
        a, b, chip = tmp_path / "a", tmp_path / "b", tmp_path / "chip"
        f1 = run(a, 1, doc(1.0))
        f2 = run(a, 2, doc(2.0))
        shutil.copytree(f2, b / f2.parent.name / f2.name)

        def window():
            cs = hub_sync.ChipSync(chip)
            cs.set_roots([(str(a), "declared"), (str(b), "declared")])
            return cs

        def full(cs):
            cs.request(full=True)
            with HubStore(chip) as st:
                while cs.run_slice(st, None):
                    pass
        wa, wb = window(), window()
        full(wa)
        full(wb)
        time.sleep(0.02)
        (f2 / "quam_state" / "state.json").write_text(json.dumps(doc(7.0)), encoding="utf-8")
        full(wa)                                     # A splits the rewritten copy off
        shutil.rmtree(f2)
        full(wb)                                     # B (its RAM still names the shared event) sees it gone
        with HubStore(chip) as st:
            roots = dict(st.conn.execute("SELECT root_id, path FROM roots").fetchall())
            by_root = {Path(roots[r[1]]).name: r[0] for r in st.conn.execute(
                "SELECT eid, root_id FROM locations WHERE rel_path LIKE '%#2_%'")}
            flags = dict(st.conn.execute("SELECT eid, flags FROM events").fetchall())
        assert flags[by_root["a"]] & SOURCE_GONE, "the deleted copy's own event is not flagged"
        assert not flags[by_root["b"]] & SOURCE_GONE, "the copy still on disk was flagged gone"

    def test_two_windows_moving_and_flagging_agree(self, tmp_path):
        root, chip = tmp_path / "data", tmp_path / "chip"
        folders = {i: run(root, i, doc(float(i))) for i in range(1, 5)}

        def window():
            cs = hub_sync.ChipSync(chip)
            cs.set_roots([(str(root), "declared")])
            return cs

        def full(cs):
            cs.request(full=True)
            with HubStore(chip) as st:
                while cs.run_slice(st, None):
                    pass
        a, b = window(), window()
        full(a)
        full(b)
        body = json.loads((folders[3] / "node.json").read_text())
        body["created_at"] = iso(T0 + 45_000_000)
        (folders[3] / "node.json").write_text(json.dumps(body), encoding="utf-8")
        full(a)                                       # A moves #3
        shutil.rmtree(folders[3])
        full(b)                                       # B sees it deleted
        r3 = by_run(chip)[3]
        assert r3["flags"] & SOURCE_GONE, "the deletion was flagged on a stale event"
        check(chip)
