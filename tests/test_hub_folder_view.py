"""S10 C1.5 -- one change ledger per chip identity, read per FOLDER.

Every live folder that resolves to one chip identity writes into the chip's
one ledger. A folder reads it through its view (``hub_lanes``): its own SM
writes and observed states, another folder's recorded before its own history
began (labelled), runs of a data root linked to it; everything else is left
out of value answers and counted, listings keep it labelled; an event whose
predecessor in the ledger is outside the lane (or an SM write over a state the
lane never held) has its rows re-diffed within the lane.

Pins (generic folders "labA" / "labB", a generic data folder, real routes
where a surface is named; each mutation-checked by
tools/mutate_hub_folder_view.py):

* (a) a shared data folder, (b) a folder with no data folder, (c) a moved
  folder -- every value surface and every listing, seen from A and from B;
* P1 (a spurious run row after another folder's write), P2 (a masked change)
  and P3 (an SM write over a state the lane never held), each in its smallest
  chain; the SM seam's rows split into ``sm`` and ``held_before_write``;
* an unknown folder before and after the cut; an unlinked root hidden, then
  shown once linked; a moved data root keeps its runs; the legacy-root rule;
* the observed dedupe compares a snapshot with its own folder's neighbours;
* REVERTS_TO_EARLIER / OVERLAPS_SM_WRITE recomputed within the lane;
* one classifier for snapshots and events;
* one folder's cached answer is never served to another; a reused answer
  is dropped when a new lane event follows another folder's row;
* the fast path: a chip whose every event is the folder's own builds no view
  and answers exactly as the ledger's own index does.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from quam_state_manager.core import (
    history as hmod, hub, hub_index, hub_lanes, hub_rules as rules, hub_sync, safe_io, value_history as vh,
    working_copy)
from quam_state_manager.core.hub_store import OVERLAPS_SM_WRITE, REVERTS_TO_EARLIER, HubStore
from quam_state_manager.web import routes as routes_mod
from tests.test_hub_drawer import WIRING, chip_state, make_app, rows, write_chip

pytestmark = pytest.mark.usefixtures("_inline")


@pytest.fixture
def _inline():
    old = hub._PROJECTOR.inline
    hub.set_inline(True)
    hub_lanes.clear_caches()
    yield
    hub.set_inline(old)


def now_us() -> int:
    return int(time.time() * 1_000_000)


# ======================================================================
# a ledger built through SM's own write doors (no app): exact chains
# ======================================================================

class Lab:
    """Folders of ONE chip identity writing into one ledger: SM writes
    through ``working_copy.apply_to_live`` (the journal records their live
    folder and base content), outside edits, runs in a data folder ingested
    by the run sync with the folder that registered the root."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.chip = tmp / "hist"
        self.inst = tmp / "inst"
        self.data = tmp / "data"
        self.wcs: dict[str, working_copy.WorkingCopy] = {}
        self.lives: dict[str, Path] = {}
        self.began: dict[str, str] = {}
        self.rid = 0

    def folder(self, name: str, state: dict) -> Path:
        live = self.tmp / name / "quam_state"
        live.mkdir(parents=True, exist_ok=True)
        (live / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (live / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
        self.wcs[name] = working_copy.create(self.inst, live)
        self.lives[name] = live
        # the folder's own history begins now (docs/250's cut)
        self.began[name] = hmod.source_stamp(now_us())
        time.sleep(0.002)
        return live

    def key(self, name: str) -> str:
        return hmod._source_key(str(self.lives[name].resolve()))

    def state(self, name: str) -> dict:
        s, _w = safe_io.read_state_wiring(self.lives[name])
        return s

    def write(self, name: str, path: str, value, *, kind="sm_apply") -> None:
        """An SM write of ONE value through the journal (a forced push: the
        working copy's content goes live, whatever the live files hold)."""
        wc = self.wcs[name]
        s, w = safe_io.read_state_wiring(wc.working_folder)
        node = s
        parts = path.split(".")
        for p in parts[:-1]:
            node = node[p]
        old = node.get(parts[-1])
        node[parts[-1]] = value
        safe_io.write_state_wiring(wc.working_folder, s, w)
        p = hub.Pending(self.chip, kind, "human:operator", "apply",
                        entries=[{"path": path, "old": old, "new": value}])
        working_copy.apply_to_live(wc, record=p, force=True)
        assert p.landed
        time.sleep(0.002)

    def outside(self, name: str, path: str, value) -> None:
        """A change made outside SM that SM then works from (the working copy
        holds it too, as after a pull) -- no snapshot records it."""
        for folder in (self.lives[name], self.wcs[name].working_folder):
            s, w = safe_io.read_state_wiring(folder)
            node = s
            parts = path.split(".")
            for p in parts[:-1]:
                node = node[p]
            node[parts[-1]] = value
            safe_io.write_state_wiring(folder, s, w)
        time.sleep(0.002)

    def run(self, state: dict, *, start_us: int | None = None, root: Path | None = None) -> Path:
        """A run saved NOW (its instant after every write so far)."""
        self.rid += 1
        t_us = now_us() + 1000
        day = datetime.fromtimestamp(t_us / 1e6, tz=timezone.utc)
        folder = (root or self.data) / day.strftime("%Y-%m-%d") / f"#{self.rid}_scan_{day.strftime('%H%M%S')}"
        (folder / "quam_state").mkdir(parents=True, exist_ok=True)
        body = {"created_at": day.isoformat(), "metadata": {"status": "finished", "name": "scan"},
                "id": self.rid, "data": {"parameters": {"model": {"qubits": ["qA1", "qA2"]}}}}
        if start_us is not None:
            body["run_start"] = datetime.fromtimestamp(start_us / 1e6, tz=timezone.utc).isoformat()
            body["metadata"]["run_start"] = body["run_start"]
        (folder / "node.json").write_text(json.dumps(body), encoding="utf-8")
        (folder / "quam_state" / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (folder / "quam_state" / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
        time.sleep(0.005)
        return folder

    def sync(self, name: str, roots=None) -> None:
        """The run sync as folder *name* registers its data roots."""
        cs = hub_sync.sync_for(self.chip)
        cs.folder = self.key(name)
        cs.links_due = True
        hub_sync.catch_up(self.chip, [str(r) for r in (roots if roots is not None else [self.data])])

    def view(self, name: str, *, roots=(), others=False, cut=None, sources_ok=True, snapshots=None):
        return hub_lanes.FolderView(
            key=self.key(name), path=str(self.lives[name]), cut=cut if cut is not None else self.began[name],
            roots=frozenset(hub_lanes.root_key(os.path.normcase(str(Path(r).resolve()))) for r in roots),
            others=others, sources_ok=sources_ok, snapshots=snapshots)

    def binding(self, view):
        return hub_index.context(SimpleNamespace(directory=self.chip), folder=view)

    def points(self, name_or_view, path: str, **kw) -> list[dict]:
        view = name_or_view if isinstance(name_or_view, hub_lanes.FolderView) else self.view(name_or_view, **kw)
        doc = rules.merged(chip_state(), WIRING)
        ans = vh.read(self.chip, {"k": vh.target(doc, path)}, binding=self.binding(view))
        return ans["rows"]["k"]["points"]

    def read(self, view, path: str) -> dict:
        doc = rules.merged(chip_state(), WIRING)
        return vh.read(self.chip, {"k": vh.target(doc, path)}, binding=self.binding(view))


@pytest.fixture
def lab(tmp_path):
    return Lab(tmp_path)


def values(points) -> list:
    return [p["value"] for p in points]


# ======================================================================
# 1. P1, P2, P3 -- each in its smallest chain
# ======================================================================

def test_p1_a_run_after_another_folders_write_has_no_spurious_rows(lab):
    """A writes f_01; B writes T1; A's next run saves A's state. Stored rows
    of that run are its diff against B's state: a T1 row 9e-5 -> 1e-5 that A
    never had. A's view re-diffs the run within its lane: no row."""
    lab.folder("labA", chip_state())
    lab.write("labA", "qubits.qA1.f_01", 5.01e9)
    lab.folder("labB", chip_state())
    lab.write("labB", "qubits.qA1.T1", 9e-5)
    lab.run(lab.state("labA"))
    lab.sync("labA")
    a_t1 = lab.points("labA", "qubits.qA1.T1")
    assert 9e-5 not in values(a_t1), "B's value is not A's"
    assert [p["kind"] for p in a_t1] == [], "the run changed no T1 of A's: no spurious row"
    a_f01 = lab.points("labA", "qubits.qA1.f_01")
    assert values(a_f01) == [5.01e9]
    assert all(p["kind"] != "run" for p in a_f01)


def test_p2_a_change_masked_by_another_folders_equal_value_is_found(lab):
    """A holds f_01 5.01e9; B writes 5.2e9; A's run saves 5.2e9. The stored
    row of that run is empty (B already held 5.2e9): A's change is masked.
    A's view finds it."""
    lab.folder("labA", chip_state())
    lab.write("labA", "qubits.qA1.f_01", 5.01e9)
    lab.folder("labB", chip_state())
    lab.write("labB", "qubits.qA1.f_01", 5.2e9)
    lab.run(chip_state(f01=5.2e9))
    lab.sync("labA")
    pts = lab.points("labA", "qubits.qA1.f_01")
    assert values(pts) == [5.01e9, 5.2e9]
    assert pts[-1]["kind"] == "run" and pts[-1]["old"] == 5.01e9


def test_p3_an_sm_write_over_a_state_the_lane_never_held_splits_sm_and_held(lab):
    """A changed f_01 outside SM (no snapshot), then SM wrote T1 from that
    state: the write's base is not its lane predecessor's content. Its rows
    become the lane diff: T1 is SM's (``sm``), f_01 was held when SM wrote
    (``held_before_write``) -- never silently A's old value forever."""
    lab.folder("labA", chip_state())
    lab.write("labA", "qubits.qA1.T1", 2e-5)
    lab.outside("labA", "qubits.qA1.f_01", 5.05e9)
    lab.write("labA", "qubits.qA1.T1", 3e-5)
    f01 = lab.points("labA", "qubits.qA1.f_01")
    assert values(f01)[-1] == 5.05e9, "the fold no longer keeps the old value"
    assert f01[-1]["provenance"] == "held_before_write"
    t1 = lab.points("labA", "qubits.qA1.T1")
    assert values(t1)[-2:] == [2e-5, 3e-5] and t1[-1]["provenance"] == "sm"


def test_a_single_folder_break_is_d5s_one_rule(lab):
    """D5: the same rule on a chip with one folder only -- the break is a
    seam, the fast path does not hide it."""
    lab.folder("labA", chip_state())
    lab.write("labA", "qubits.qA1.T1", 2e-5)
    lab.outside("labA", "qubits.qA1.f_01", 5.05e9)
    lab.write("labA", "qubits.qA1.T1", 3e-5)
    built = []
    hub_lanes.ON_BUILD.append(built.append)
    try:
        lab.points("labA", "qubits.qA1.f_01")
    finally:
        hub_lanes.ON_BUILD.remove(built.append)
    assert built, "a break is a seam: a view is built"


# ======================================================================
# 2. unknown folders, the cut, links, legacy roots, moved roots
# ======================================================================

def _blank_live(lab, eid_kind="sm_apply"):
    """Make the newest SM write's folder unrecorded (an older journal line)."""
    import sqlite3
    con = sqlite3.connect(lab.chip / "ledger.sqlite")
    try:
        eid = con.execute("SELECT MAX(eid) FROM events WHERE kind=?", (eid_kind,)).fetchone()[0]
        con.execute("UPDATE events SET live=NULL WHERE eid=?", (eid,))
        con.execute("UPDATE sm_events SET live=NULL WHERE eid=?", (eid,))
        con.commit()
    finally:
        con.close()
    hub_index.close_readers(lab.chip)


def test_an_unknown_folder_before_the_cut_is_labelled_and_after_it_is_hidden_and_counted(lab):
    lab.folder("labA", chip_state())
    lab.write("labA", "qubits.qA1.T1", 2e-5)
    _blank_live(lab)
    # before A's cut: earlier -- shown, said to be of an unrecorded folder
    early = lab.points(lab.view("labA", cut=hmod.source_stamp(now_us())), "qubits.qA1.T1")
    assert values(early) == [2e-5]
    assert early[-1]["source"]["kind"] == "unknown" and early[-1]["source"]["lineage"] == "earlier"
    # after the cut: hidden from the value timeline, and counted
    view = lab.view("labA", cut="19700101_000000_0000")
    ans = lab.read(view, "qubits.qA1.T1")
    assert ans["rows"]["k"]["points"] == []
    assert ans["ledger"]["left_out"]["unknown"] == 1
    notes = vh.folder_notes(ans["ledger"]["left_out"])
    assert any(n["code"] == "other_folders" and "a folder that is not recorded" in n["text"] for n in notes)


def test_an_unlinked_root_is_hidden_then_shown_once_linked(lab):
    lab.folder("labA", chip_state())
    lab.folder("labB", chip_state())
    lab.run(chip_state(t1=4e-5))
    lab.sync("labA")                                   # the root is linked to A only
    ans = lab.read(lab.view("labB"), "qubits.qA1.T1")
    assert ans["rows"]["k"]["points"] == [], "S10: never adopt runs by identity alone"
    assert ans["ledger"]["left_out"]["unlinked"] == 1
    note = [n for n in vh.folder_notes(ans["ledger"]["left_out"]) if n["code"] == "unlinked_roots"]
    assert note and "data" in note[0]["text"]
    # B links the data folder (its declared roots): the run is B's to read
    shown = lab.points(lab.view("labB", roots=[lab.data]), "qubits.qA1.T1")
    assert values(shown) == [4e-5]


def test_a_root_with_no_link_rows_counts_only_while_no_other_folder_is_known(lab):
    lab.folder("labA", chip_state())
    lab.run(chip_state(t1=4e-5))
    cs = hub_sync.sync_for(lab.chip)
    cs.folder = None                                   # an older SM: no link rows at all
    hub_sync.catch_up(lab.chip, [str(lab.data)])
    assert values(lab.points(lab.view("labA", others=False), "qubits.qA1.T1")) == [4e-5]
    ans = lab.read(lab.view("labA", others=True), "qubits.qA1.T1")
    assert ans["rows"]["k"]["points"] == [] and ans["ledger"]["left_out"]["unlinked"] == 1


def test_a_moved_data_root_keeps_its_runs(lab):
    import shutil
    lab.folder("labA", chip_state())
    lab.run(chip_state(t1=4e-5))
    lab.sync("labA")
    moved = lab.tmp / "data_moved"
    shutil.copytree(lab.data, moved)
    shutil.rmtree(lab.data)
    lab.sync("labA", roots=[moved])
    pts = lab.points(lab.view("labA", roots=[moved]), "qubits.qA1.T1")
    assert values(pts) == [4e-5]


# ======================================================================
# 3. the observed dedupe; lane flags
# ======================================================================

def test_the_observed_dedupe_compares_a_snapshot_with_its_own_folders_neighbours(lab):
    """B's state equal to A's write right before it is B's state too: B held
    it. Compared with A's neighbour it was dropped ("same_before"); compared
    within B's lane it is imported."""
    lab.folder("labA", chip_state())
    lab.write("labA", "qubits.qA1.T1", 7e-5)
    lab.folder("labB", chip_state(t1=7e-5))
    snap_dir = lab.tmp / "snapB"
    snap_dir.mkdir()
    (snap_dir / "state.json").write_text(json.dumps(lab.state("labA")), encoding="utf-8")
    (snap_dir / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
    with HubStore(lab.chip) as store:
        got = hub_sync.attach_observed(store, {"ts": hmod.source_stamp(now_us()), "t_us": now_us(),
                                               "trigger": "auto", "dir": str(snap_dir),
                                               "live": str(lab.lives["labB"].resolve())})
    assert got in ("added", "inserted"), got
    pts = lab.points("labB", "qubits.qA1.T1", cut=hmod.source_stamp(0))
    assert pts and pts[-1]["kind"] == "observed"


def test_lane_flags_are_recomputed_within_the_lane(lab):
    """OVERLAPS_SM_WRITE: B's write inside A's run is not A's overlap.
    REVERTS_TO_EARLIER: A's run saving A's unchanged state after B's write is
    a return only in the global order."""
    lab.folder("labA", chip_state())
    lab.write("labA", "qubits.qA1.T1", 2e-5)
    start = now_us()
    lab.folder("labB", chip_state())
    lab.write("labB", "qubits.qA1.T1", 9e-5)
    lab.run(lab.state("labA"), start_us=start)
    lab.sync("labA")
    import sqlite3
    con = sqlite3.connect(f"file:{lab.chip / 'ledger.sqlite'}?mode=ro", uri=True)
    try:
        flags = con.execute("SELECT flags FROM events WHERE kind='run'").fetchone()[0]
    finally:
        con.close()
    assert flags & OVERLAPS_SM_WRITE, "the premise: globally the run overlaps B's write"
    with hub_index.snapshot(lab.binding(lab.view("labA"))) as (_conn, index):
        pos = [i for i, e in enumerate(index.eids) if index.kind[i] == index.names["kind"]["run"]]
        assert pos and not index.flags[pos[0]] & OVERLAPS_SM_WRITE
        assert not index.flags[pos[0]] & REVERTS_TO_EARLIER


# ======================================================================
# 4. one classifier; caches per folder
# ======================================================================

@pytest.mark.parametrize("where", ["snapshot", "event"])
@pytest.mark.parametrize("owner,at,expect", [
    ("here", "20260101_120000_0000", ("this", "own")),
    ("other", "20250101_120000_0000", ("other", "earlier")),
    ("other", "20270101_120000_0000", ("other", "parallel")),
    (None, "20250101_120000_0000", ("unknown", "earlier")),
    (None, "20270101_120000_0000", ("unknown", "parallel")),
])
def test_one_classifier_for_snapshots_and_events(lab, where, owner, at, expect):
    """THE rule (history.classify_source) classifies a Param History snapshot
    and a ledger event alike."""
    lab.folder("labA", chip_state())
    here = lab.key("labA")
    cut = "20260101_000000_0000"
    raw = {"here": str(lab.lives["labA"].resolve()), "other": str(lab.tmp / "labB" / "quam_state"),
           None: None}[owner]
    if where == "snapshot":
        hm = hmod.HistoryManager(str(lab.tmp / "ph"))
        meta = SimpleNamespace(trigger="save", source_path=raw or "", experiment_folder_path=None,
                               timestamp=at)
        kind, _folder = hm._classify_source(meta, here)
        got = (kind, hmod.source_lineage(kind, at, cut))
    else:
        view = hub_lanes.FolderView(key=here, path=str(lab.lives["labA"]), cut=cut)
        index = SimpleNamespace(eids=[1], live=[1 if raw else 0], names={"live": {raw: 1} if raw else {}})
        f = {1: {"kind": "sm_apply", "t": int(datetime.strptime(at, "%Y%m%d_%H%M%S_%f").replace(
            tzinfo=timezone.utc).timestamp() * 1e6), "t_src": None, "root_id": None}}
        cls = hub_lanes.classify(index, view, _NoLinks(), f)[0][1]
        got = ("this", "own") if cls[1] is None else (cls[1]["kind"], cls[1]["lineage"])
    assert got == expect


class _Empty(list):
    def fetchall(self):
        return []


class _NoLinks:
    """A read connection with no roots, links or locations."""

    def execute(self, sql, *_a):
        return _Empty()


def test_one_folders_cached_answer_is_never_served_to_another(lab):
    """A and a copy C made after A's write, before B's: their lanes hold the
    same events -- C labels A's write as A's, A does not. The answer kept for
    one folder is never handed to the other."""
    lab.folder("labA", chip_state())
    lab.write("labA", "qubits.qA1.T1", 2e-5)
    lab.folder("labC", chip_state(t1=2e-5))
    lab.folder("labB", chip_state())
    lab.write("labB", "qubits.qA1.T1", 9e-5)
    a = lab.points("labA", "qubits.qA1.T1")
    c = lab.points("labC", "qubits.qA1.T1")
    assert values(a) == values(c) == [2e-5]
    assert a[-1].get("source") is None
    assert (c[-1].get("source") or {}).get("kind") == "other", "C's answer names A's folder"
    assert (c[-1].get("source") or {}).get("lineage") == "earlier"
    a2 = lab.points("labA", "qubits.qA1.T1")
    assert a2[-1].get("source") is None


def test_a_kept_answer_is_dropped_when_a_new_lane_event_follows_another_folders_row(lab):
    """docs/298 reuse: A's run lands after B's write and, within A's lane,
    sets T1 to the value B held (P2) -- its stored rows touch nothing A's
    kept answer watched, so only the lane can say the answer moved."""
    lab.folder("labA", chip_state())
    lab.write("labA", "qubits.qA1.T1", 2e-5)
    lab.folder("labB", chip_state())
    lab.write("labB", "qubits.qA1.T1", 9e-5)
    lab.run(chip_state(t1=1e-5))          # the genesis run: shared, first
    lab.sync("labA")
    before = lab.points("labA", "qubits.qA1.T1")
    lab.run(chip_state(t1=9e-5))
    lab.sync("labA")
    after = lab.points("labA", "qubits.qA1.T1")
    assert values(after) == values(before) + [9e-5]


# ======================================================================
# 5. the fast path
# ======================================================================

def test_the_fast_path_builds_no_view_and_answers_as_the_ledger_does(lab):
    lab.folder("labA", chip_state())
    lab.write("labA", "qubits.qA1.T1", 2e-5)
    lab.run(lab.state("labA"))
    lab.sync("labA")
    lab.write("labA", "qubits.qA1.f_01", 5.3e9)
    built = []
    hub_lanes.ON_BUILD.append(built.append)
    try:
        for path in ("qubits.qA1.T1", "qubits.qA1.f_01"):
            doc = rules.merged(chip_state(), WIRING)
            tgt = {"k": vh.target(doc, path)}
            with_view = vh.read(lab.chip, tgt, binding=lab.binding(lab.view("labA")))
            plain = vh.read(lab.chip, tgt, binding=SimpleNamespace(directory=lab.chip))
            assert json.dumps(with_view["rows"], sort_keys=True, default=str) == \
                json.dumps(plain["rows"], sort_keys=True, default=str)
            assert "left_out" not in with_view["ledger"]
        with hub_index.snapshot(lab.binding(lab.view("labA"))) as (_c, index):
            assert index.lane is None
    finally:
        hub_lanes.ON_BUILD.remove(built.append)
    assert built == [], "a single-folder chip with no break never builds a view"


def test_the_c1_other_folders_fallback_is_gone():
    import inspect
    src = inspect.getsource(routes_mod)
    assert "_hub_other_folders" not in src
    # S10 C3: old -> new, the terminal-note map replaces the fallback-note map.
    assert '"other_folders"' not in src.split("_VH_UNAVAILABLE_NOTES = {", 1)[1].split("}", 1)[0]


# ======================================================================
# 6. the surfaces: (a) shared data folder, (b) no data folder, (c) moved
# ======================================================================

def _edit(c, path, value):
    r = c.post("/field/edit", data={"dot_path": path, "value": value}, headers={"HX-Request": "true"})
    assert r.status_code == 200, r.data[:300]


def _apply(c):
    r = c.post("/state/apply-to-live", headers={"HX-Request": "true"})
    assert r.status_code == 200, r.data[:300]


def _load(c, folder):
    assert c.post("/load", data={"folder": str(folder)}).status_code in (200, 302)


def _app_run(data: Path, rid: int, state: dict) -> Path:
    t_us = now_us() + 1000
    day = datetime.fromtimestamp(t_us / 1e6, tz=timezone.utc)
    folder = data / day.strftime("%Y-%m-%d") / f"#{rid}_scan_{day.strftime('%H%M%S')}"
    (folder / "quam_state").mkdir(parents=True, exist_ok=True)
    (folder / "node.json").write_text(json.dumps({
        "created_at": day.isoformat(), "metadata": {"status": "finished", "name": "scan"}, "id": rid,
        "data": {"parameters": {"model": {"qubits": ["qA1", "qA2"]}}}}), encoding="utf-8")
    (folder / "quam_state" / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "quam_state" / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
    time.sleep(0.005)
    return folder


def _two(tmp_path, *, b_data: bool, moved: bool = False) -> dict:
    data = tmp_path / "data"
    _app_run(data, 1, chip_state())
    a = tmp_path / "labA" / "quam_state"
    b = tmp_path / "labB" / "quam_state"
    write_chip(a, chip_state(), data)
    app = make_app(tmp_path)
    c = app.test_client()
    _load(c, a)
    _edit(c, "qubits.qA1.f_01", "5010000000.0")
    _apply(c)
    if moved:
        import shutil
        shutil.copytree(a, b)
        _load(c, b)
        _edit(c, "qubits.qA1.T1", "9e-05")
        _apply(c)
        return {"app": app, "client": c, "a": a, "b": b, "data": data, "tmp": tmp_path}
    write_chip(b, chip_state(t1=4.0e-5), data if b_data else None)
    _load(c, b)
    _edit(c, "qubits.qA1.T1", "9e-05")
    _apply(c)
    s, _w = safe_io.read_state_wiring(a)
    _app_run(data, 2, s)                        # A's chip measured again: A's own state
    _load(c, b)                                 # B's window looks again (its own states SM saw)
    _load(c, a)
    # A writes again after B's write: A's newest own snapshot is after it (the
    # cut is A's FIRST own snapshot, never its newest)
    _edit(c, "qubits.qA2.f_01", "6010000000.0")
    _apply(c)
    return {"app": app, "client": c, "a": a, "b": b, "data": data, "tmp": tmp_path}


def _drawer(env, path):
    r = env["client"].get("/field/history", query_string={"path": path})
    assert r.status_code == 200
    return r.data.decode()


def _surfaces(env, path="qubits.qA1.T1") -> dict:
    c = env["client"]
    out = {"drawer": _drawer(env, path)}
    r = c.post("/bulk/column-history", data={"grid": "qubit", "label": "T1", "unit": "", "col_key": "c",
                                              "paths": json.dumps({"qA1": path})})
    out["column"] = r.data.decode()
    out["agent"] = json.dumps(c.get("/api/agent/field-history", query_string={"path": path}).get_json())
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for d in {day, datetime.now().strftime("%Y-%m-%d")}:
        out.setdefault("log", "")
        out["log"] += c.get(f"/journal/day?day={d}", headers={"HX-Request": "true"}).data.decode()
    out["versions"] = c.get("/state/versions?changes=all").data.decode()
    out["state_history"] = c.get("/state-history?body=1", headers={"HX-Request": "true"}).data.decode()
    out["history_drawer"] = c.get("/api/history").data.decode()
    return out


@pytest.fixture
def shared(tmp_path):
    return _two(tmp_path, b_data=True)


@pytest.fixture
def no_folder(tmp_path):
    return _two(tmp_path, b_data=False)


@pytest.fixture
def moved(tmp_path):
    return _two(tmp_path, b_data=True, moved=True)


_VALUE = ("drawer", "column", "agent", "log")
_LISTING = ("versions", "state_history", "history_drawer")


class TestSurfaces:
    def test_a_shared_data_folder_seen_from_a(self, shared):
        s = _surfaces(shared)
        for name in _VALUE:
            assert "9e-05" not in s[name] and "9e-5" not in s[name] and "0.00009" not in s[name], \
                f"{name}: B's write is not A's history"
        assert "fh-other-note" in s["drawer"] and "labB/quam_state" in s["drawer"]
        assert "not part of this folder" in s["log"], "the Calibration log has a folder gate"
        body = json.loads(s["agent"])["history"]
        assert body["parallel_hidden"] >= 1 and not any(p.get("value") == 9e-5 for p in body["points"])
        # P1 through the surfaces: A's run after B's write wrote no T1 of A's
        assert [r["value"] for r in rows(s["drawer"])] == ["0.00001"] or \
            all("run" not in r["label"] or r["prov"] == "first_record" for r in rows(s["drawer"]))
        for name in _LISTING:
            assert ">from labB/quam_state<" in s[name], f"{name}: B's row stays listed, labelled"

    def test_a_shared_data_folder_seen_from_b(self, shared):
        _load(shared["client"], shared["b"])
        s = _surfaces(shared)
        assert "9e-05" in s["drawer"] or "0.00009" in s["drawer"]
        # the runs of the shared data folder are B's too (D1); A's write before B
        # began is labelled as A's
        f01 = _drawer(shared, "qubits.qA1.f_01")
        assert ">from labA/quam_state<" in f01
        # A's write after B began is A's alone: left out of B's history, counted
        assert 'data-note="other_folders"' in s["drawer"] and "(labA/quam_state)" in s["drawer"]
        assert "6,010,000,000" not in _drawer(shared, "qubits.qA2.f_01")

    def test_no_data_folder_b_reads_no_run_and_says_so(self, no_folder):
        _load(no_folder["client"], no_folder["b"])
        s = _surfaces(no_folder)
        html = s["drawer"]
        assert not any(r["prov"].startswith("run") or r["prov"] == "first_record" for r in rows(html)), \
            "S10: B never adopts A's runs by identity"
        assert 'data-note="unlinked_roots"' in html and "data" in html
        body = json.loads(s["agent"])["history"]
        assert body.get("unlinked_hidden", 0) >= 1
        for name in _LISTING:
            assert "data folder not linked" in s[name], f"{name}: the unlinked runs stay listed, labelled"

    def test_no_data_folder_seen_from_a(self, no_folder):
        s = _surfaces(no_folder)
        assert "fh-other-note" in s["drawer"] and "labB/quam_state" in s["drawer"]
        for name in _VALUE:
            assert "9e-05" not in s[name] and "0.00009" not in s[name], name

    def test_a_moved_folder_keeps_its_history_labelled(self, moved):
        s = _surfaces(moved, "qubits.qA1.f_01")
        html = s["drawer"]
        assert ">from labA/quam_state<" in html, "the old path's write: before the move, labelled"
        assert any(r["value"].startswith("5,010,000,000") for r in rows(html))
        for name in _LISTING:
            assert ">from labA/quam_state<" in s[name], name
        # the run of the data folder both paths declared stays in the history
        assert any(r["prov"] == "first_record" or r["prov"].startswith("run") for r in rows(html))

    def test_versions_never_call_another_folders_observed_state_its_own(self, shared):
        """The observed rows of B are B's: seen from A, B's observed state is
        labelled with B's folder, never "this folder's own"."""
        b_observed = _ledger_rows(shared, "SELECT COUNT(*) FROM events WHERE kind='observed' AND live LIKE ?",
                                  ("%labB%",))[0][0]
        assert b_observed >= 1, "the premise: the ledger holds a state B's window observed"
        html = shared["client"].get("/state/versions?changes=all&limit=50").data.decode()
        rows_ = html.split('<li class="state-version-row')[1:]
        seen = [r for r in rows_ if "seen by SM" in r]
        assert any("from labB/quam_state" in r for r in seen), "B's observed row names B's folder"
        assert any("from labB/quam_state" not in r for r in seen), "A's own observed row stays unlabelled"


def _ledger_rows(env, sql, args=()):
    import sqlite3
    with env["app"].app_context():
        chip = routes_mod._hub_chip_dir(routes_mod._active_ctx()["path"])
    con = sqlite3.connect(f"file:{chip / 'ledger.sqlite'}?mode=ro", uri=True)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()
