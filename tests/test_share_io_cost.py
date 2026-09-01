r"""Share I/O cost of the surfaces users named as slow (docs/155 F1-F4, F6).

The customer's workspace is an SMB share, so a filesystem operation is a
network round-trip (~1.8 ms measured) rather than the ~1 us it is on the
development NVMe. Wall clock measured locally cannot see that difference at
all; the OPERATION COUNT is identical on both machines, which is why every pin
here counts syscalls and none of them looks at a clock.

Five fixes, measured at 390 date dirs (roughly 13 months of daily runs):

    /topology (Chip Status / Overview)   7,831 -> 403 ops   (~14.1 s -> ~0.7 s)
    /datasets  ·  /trends/data           1,564 -> 392
    /datasets/changes-since (every 5 s)  1,568 -> 392
    /param-history/alignment             1,180 -> 401
    /datasets/wait  (the long poll)        782 ->   0
    /datasets/poll  (every 60 s)           782 -> 392
    /field/history  (the 🕘 popover)       789 -> 399

F1 — the per-gate RB derivation (docs/138) called `_rb_run_folder` once per 2Q
edge, and each call re-entered `_active_dataset_stores`, which rescans every
dataset store, which stats every date dir. Ten edges meant ten full sweeps of
the archive to look up ten run folders.

F2 — `DatasetStore._current_mtime` spent TWO syscalls per date dir
(`Path.is_dir()` is a stat, `.stat()` is another). `os.scandir` carries the
file-type bits in the listing, so the is_dir half is free.

F2' — `HistoryManager._workspace_token` had the same shape, and on the
alignment path a third call per entry: docs/139's own-root exclusion resolved
EVERY entry. The root is resolved once now, and a non-symlink child's resolved
path is `parent_resolved / name` — string work, no syscall.

F4 — the two background polls stopped sweeping the archive to answer questions
they do not ask: the delta poll rescanned every store and then rescanned it
again (the first one WITHOUT the docs/105 #4 deadline), and `/datasets/wait`
built a run table to read the folder paths off it.

F3' — `_workspace_token` and `_current_mtime` stat the SAME directories to
answer two different questions, so a /datasets render walked the archive twice.
`core/dir_sample.py` is the one listing; the scope of its cache is ONE REQUEST,
which is what keeps docs/105 #8's write-then-poll contract intact.

F6 — `_dataset_candidate_folders(fast=True)` validates its cache on
`ws.version`, and on a miss it computed the `_workspace_token` walk anyway —
one stat per date dir, for a value that path can never read back. The scanner
bumps `ws.version` whenever a run lands, so this was re-paid all day: chip
activation went 421 -> 29 ops, and the long poll's documented 0 became true
after a change as well as before one.

All five are cost-only: the fingerprints these functions return, and the
staleness contracts built on them, must not move. That is pinned first, below.
"""
from __future__ import annotations

import contextlib
import json
import os
import threading
from pathlib import Path

import pytest

from quam_state_manager.core import dir_sample as _dir_sample
from quam_state_manager.core.dataset import DatasetStore
from quam_state_manager.core.history import HistoryManager
from quam_state_manager.core.scanner import Workspace
from quam_state_manager.web import routes


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _Spy:
    def __init__(self) -> None:
        self.stat = 0
        self.lstat = 0
        self.scandir = 0
        self.listdir = 0

    @property
    def total(self) -> int:
        return self.stat + self.lstat + self.scandir + self.listdir


@contextlib.contextmanager
def _fs_spy(*, own_thread: bool = False):
    """Count SYSCALLS.

    ``own_thread=True`` counts only the calling thread's calls. The wrappers
    are process-wide, so anything SM does on a daemon thread — the docs/142
    listing hydration, a deferred index rebuild, `run_watch` — lands in the
    same counter, and a pin that drives a ROUTE and compares two archive sizes
    then measures whichever background thread happened to be awake. Measured:
    that made one such pin fail about one run in three, and a flaky pin makes
    every mutation verdict a coin toss (docs/141 §4ae). Off by default so the
    pins written before it count exactly what they counted.

    Deliberately hooks the ``os`` level only: ``Path.stat`` calls ``os.stat``,
    so hooking both double-counts every pathlib-routed stat and inflates
    exactly the number these pins exist to hold down.

    ``lstat`` is counted because it is where a syscall hides in plain sight:
    ``os.path.islink`` routes through it, and a mutation swapping the free
    ``DirEntry.is_symlink()`` for it went completely unseen by an earlier
    version of this spy.
    """
    s = _Spy()
    o_stat, o_lstat = os.stat, os.lstat
    o_scan, o_list = os.scandir, os.listdir
    owner = threading.get_ident()

    def mine():
        return not own_thread or threading.get_ident() == owner

    def stat(*a, **k):
        s.stat += mine()
        return o_stat(*a, **k)

    def lstat(*a, **k):
        s.lstat += mine()
        return o_lstat(*a, **k)

    def scandir(*a, **k):
        s.scandir += mine()
        return o_scan(*a, **k)

    def listdir(*a, **k):
        s.listdir += mine()
        return o_list(*a, **k)

    os.stat, os.lstat = stat, lstat
    os.scandir, os.listdir = scandir, listdir
    try:
        yield s
    finally:
        os.stat, os.lstat = o_stat, o_lstat
        os.scandir, os.listdir = o_scan, o_list


@contextlib.contextmanager
def _sample_scope():
    """One sampling scope, exactly as a request opens and closes it."""
    _dir_sample.begin()
    try:
        yield
    finally:
        _dir_sample.end()


def _seed_archive(root: Path, *, dates: int, junk: int = 0) -> Path:
    """``<root>/<date>/<run>`` with one run per date dir, plus *junk*
    non-date entries beside them (a README, an export folder — the things a
    real results folder accumulates)."""
    root.mkdir(parents=True, exist_ok=True)
    for i in range(dates):
        d = root / f"2026-{i // 28 + 1:02d}-{i % 28 + 1:02d}"
        run = d / f"#{i + 1}_04_power_rabi_120000"
        run.mkdir(parents=True, exist_ok=True)
        (run / "node.json").write_text(
            json.dumps({"id": i + 1, "name": "power_rabi"}), encoding="utf-8")
    for j in range(junk):
        (root / f"notes_{j}.md").write_text("x", encoding="utf-8")
        (root / f"export_{j}").mkdir(exist_ok=True)
    return root


# ---------------------------------------------------------------------------
# F2 — the fingerprint must not move, and must cost half as much
# ---------------------------------------------------------------------------

class TestCurrentMtimeSemanticsUnchanged:
    """Cost work that changes an answer is not an optimisation. These come
    first for that reason."""

    def test_a_run_landing_in_an_EXISTING_date_dir_still_moves_it(self, tmp_path):
        """The whole point of stat'ing the date dirs at all: a new run inside
        one already present must change the fingerprint, or a poll serves a
        stale run list.

        This pin bumps the dir's own mtime explicitly, so it is deterministic
        — and for that same reason it does NOT catch a mtime read from the
        parent's directory listing. See the note below for why nothing here
        can catch that semantically, and what does catch it instead.
        """
        root = _seed_archive(tmp_path / "ws", dates=3)
        store = DatasetStore(root)

        before = store._current_mtime()

        date_dir = sorted(p for p in root.iterdir() if p.is_dir())[0]
        new_run = date_dir / "#999_04_power_rabi_235959"
        new_run.mkdir()
        (new_run / "node.json").write_text('{"id": 999}', encoding="utf-8")
        bumped = date_dir.stat().st_mtime + 10.0
        os.utime(date_dir, (bumped, bumped))

        assert store._current_mtime() != before, (
            "a run written into an existing date dir did not move the "
            "fingerprint — every poll would report the archive unchanged"
        )

    # The `DirEntry.stat()` trap has NO semantic pin here, deliberately, and
    # the reason is worth more than a pin would be.
    #
    # The free half of `os.scandir` is the file-TYPE bits; the mtime is not
    # free, and reading it from the listing anyway is the one mistake this
    # rewrite could make (docs/141 §4ac). The obvious pin — write a run inside
    # a date dir with nobody touching the dir, assert the fingerprint moved —
    # was written, and it passed under exactly that mutation. Measured cause,
    # 20 trials per gap on this NTFS volume:
    #
    #     gap after mkdir   DirEntry.stat() saw it   os.stat() saw it
    #             0.00 s          1/20                     6/20
    #             0.02 s          0/20                     5/20
    #             0.50 s          5/20                     8/20
    #             1.20 s          5/20                    16/20
    #
    # NTFS updates the parent's recorded timestamps for a child lazily, so
    # within about a second of a `mkdir` NEITHER call reliably reports the
    # change — `os.stat` is markedly better but not deterministic either. A
    # pin built on that is a coin toss, which is worse than no pin.
    #
    # The guard that IS deterministic is the cost pin below: `de.stat()` costs
    # zero syscalls, so `test_cost_is_linear_in_dates_with_a_slope_of_one`
    # goes red the moment the mtime stops coming from `os.stat`. Verified by
    # mutation. The semantic contract keeps the explicit-`utime` pin above,
    # which is deterministic because touching the directory's own metadata is
    # what the parent's listing does record promptly.

    def test_a_new_date_dir_changes_the_count_rider(self, tmp_path):
        """The count rider (r13 audit D6) exists because one future-dated dir
        pins the max() forever. A new date dir must still change the count."""
        root = _seed_archive(tmp_path / "ws", dates=3)
        store = DatasetStore(root)
        _, n_before = store._current_mtime()

        (root / "2026-12-31" / "#77_04_power_rabi_010101").mkdir(parents=True)

        _, n_after = store._current_mtime()
        assert n_after == n_before + 1

    def test_non_date_entries_are_not_counted_as_dates(self, tmp_path):
        """The name test moved in FRONT of the is_dir() test; it must still be
        an AND, not a substitution."""
        root = _seed_archive(tmp_path / "ws", dates=4, junk=3)
        store = DatasetStore(root)
        _, n_dates = store._current_mtime()
        assert n_dates == 4

    def test_a_date_NAMED_file_is_not_a_date_dir(self, tmp_path):
        """The is_dir() half is free now, not gone."""
        root = _seed_archive(tmp_path / "ws", dates=2)
        (root / "2026-07-04").write_text("a file, not a folder", encoding="utf-8")
        store = DatasetStore(root)
        _, n_dates = store._current_mtime()
        assert n_dates == 2, (
            "a FILE named like a date was counted as a date dir — the free "
            "is_dir() from the listing was dropped rather than made free"
        )

    def test_an_unreadable_root_still_raises_rather_than_fingerprinting(
            self, tmp_path, monkeypatch):
        """``Path.iterdir()`` raised out of this method and the callers are
        written for that. Swallowing it into the (0.0, -1) sentinel would
        reclassify a permissions failure as a fingerprint — a different bug
        wearing a fixed bug's clothes."""
        root = _seed_archive(tmp_path / "ws", dates=2)
        store = DatasetStore(root)
        real = os.scandir

        def boom(path, *a, **k):
            if str(path) == str(root):
                raise PermissionError(13, "denied")
            return real(path, *a, **k)

        monkeypatch.setattr(os, "scandir", boom)
        with pytest.raises(OSError):
            store._current_mtime()


class TestCurrentMtimeCostsOneSyscallPerDateDir:

    def test_one_stat_per_date_dir_not_two(self, tmp_path):
        root = _seed_archive(tmp_path / "ws", dates=25)
        store = DatasetStore(root)

        with _fs_spy() as s:
            store._current_mtime()

        # 25 date dirs + the root's own stat. The is_dir() half rides the
        # scandir listing, so it costs nothing extra.
        assert s.stat <= 25 + 1, (
            f"{s.stat} stats for 25 date dirs — the free is_dir() from the "
            f"directory listing is being paid for again"
        )
        assert s.scandir == 1, f"{s.scandir} directory listings, expected 1"

    def test_a_non_date_entry_costs_nothing_at_all(self, tmp_path):
        """The name test in front of is_dir() is what makes this true: a
        results folder full of exports and notes pays for its date dirs only."""
        plain = DatasetStore(_seed_archive(tmp_path / "plain", dates=10))
        junky = DatasetStore(_seed_archive(tmp_path / "junky", dates=10, junk=20))

        # Built OUTSIDE the spy: constructing a store scans the archive, and
        # counting that here would measure the constructor, not this function.
        with _fs_spy() as a:
            plain._current_mtime()
        with _fs_spy() as b:
            junky._current_mtime()

        assert a.total == b.total, (
            f"40 non-date entries cost {b.total - a.total} extra syscalls; "
            f"their names alone are enough to skip them"
        )

    def test_cost_is_linear_in_dates_with_a_slope_of_one(self, tmp_path):
        """The invariant stated as a measurement — the axis that grows every
        day of measurement, with the constant this fix halved."""
        small = DatasetStore(_seed_archive(tmp_path / "small", dates=10))
        big = DatasetStore(_seed_archive(tmp_path / "big", dates=60))

        with _fs_spy() as a:
            small._current_mtime()
        with _fs_spy() as b:
            big._current_mtime()

        assert b.total - a.total == 50, (
            f"50 more date dirs cost {b.total - a.total} more syscalls, not 50 "
            f"— back to two per date dir"
        )


# ---------------------------------------------------------------------------
# F1 — one staleness sweep per render, not one per 2Q edge
# ---------------------------------------------------------------------------

class _FakeEngine:
    """Just enough topology for `_topology_with_derived_rb`: *n_edges* pairs,
    each carrying one Standard-RB (clifford-level) row with its own load_id."""

    def __init__(self, n_edges: int, *, load_ids: bool = True) -> None:
        self._topo = {
            "edges": [
                {"pair_id": f"q{i}-q{i+1}",
                 "gate_fidelities": [
                     {"level": "clifford", "value": 0.97,
                      "load_id": (2000 + i) if load_ids else None},
                 ]}
                for i in range(n_edges)
            ]
        }

    def get_topology(self):
        return self._topo


class TestRbDerivationSweepsOnce:

    def _count_sweeps(self, monkeypatch, engine):
        sweeps = []

        def fake_stores(*a, **k):
            sweeps.append(1)
            return []          # no store has the run -> every lookup is a miss

        monkeypatch.setattr(routes, "_active_dataset_stores", fake_stores)
        routes._topology_with_derived_rb(engine)
        return len(sweeps)

    def test_one_sweep_for_many_edges(self, monkeypatch):
        """The fix, stated as the thing that failed: `_active_dataset_stores`
        rescans every store, and a rescan stats every date dir. Called per
        edge on a 390-date-dir archive that was 7,831 operations per render."""
        assert self._count_sweeps(monkeypatch, _FakeEngine(12)) == 1

    def test_the_sweep_count_does_not_grow_with_the_chip(self, monkeypatch):
        """A 2-pair chip and a 20-pair chip pay the same. Before, a bigger
        chip paid proportionally more — for a fixed archive."""
        small = self._count_sweeps(monkeypatch, _FakeEngine(2))
        large = self._count_sweeps(monkeypatch, _FakeEngine(20))
        assert small == large == 1, (
            f"{small} sweep(s) for 2 edges, {large} for 20 — the archive is "
            f"being re-swept per edge"
        )

    def test_nothing_to_derive_sweeps_nothing(self, monkeypatch):
        """Laziness is the other half: a chip with no Standard-RB load_id must
        keep paying zero. Resolving the stores eagerly would hand every such
        chip a full archive sweep it never used to pay."""
        assert self._count_sweeps(
            monkeypatch, _FakeEngine(8, load_ids=False)) == 0

    def test_the_enrichment_still_happens(self, monkeypatch):
        """Cost pins alone would pass with the derivation deleted."""
        seen = []

        class _OneStore:
            def get_run(self, rid):
                seen.append(rid)
                return None

        monkeypatch.setattr(routes, "_active_dataset_stores",
                            lambda *a, **k: [{"store": _OneStore()}])
        routes._topology_with_derived_rb(_FakeEngine(5))
        assert sorted(seen) == [2000, 2001, 2002, 2003, 2004], (
            "the per-gate RB derivation stopped resolving its runs"
        )

    def test_a_direct_caller_still_gets_the_old_behaviour(self, monkeypatch):
        """`stores=None` must remain byte-identical to before — the parameter
        is an opt-in for a caller resolving many ids in one render."""
        sweeps = []
        monkeypatch.setattr(routes, "_active_dataset_stores",
                            lambda *a, **k: (sweeps.append(1), [])[1])
        assert routes._rb_run_folder(1234) is None
        assert len(sweeps) == 1


# ---------------------------------------------------------------------------
# F2' — `_workspace_token` had F2's shape, and the own-root exclusion resolved
#       every entry on top of it
# ---------------------------------------------------------------------------

def _seed_shallow(root: Path, dates: int) -> Path:
    """``<root>/<date>/<run>`` — the layout a qualibrate storage.location has,
    and the one docs/154 taught this token to stop descending."""
    return _seed_archive(root, dates=dates)


class TestWorkspaceTokenCostsOnePerDir:
    """The token's promise is O(roots + chips + dates). That was true of the
    number of DIRECTORIES it visits and false of the number of SYSCALLS it
    spent on each."""

    def _ws(self, root: Path) -> Workspace:
        ws = Workspace()
        ws.add_root(root)
        return ws

    def test_one_stat_per_dir_not_two(self, tmp_path):
        root = _seed_shallow(tmp_path / "ws", 25)
        ws = self._ws(root)

        with _fs_spy() as s:
            HistoryManager._workspace_token(ws)

        # The root itself + one stat per date dir. `is_dir()` rides the
        # listing; before F2' it was a second stat each.
        assert s.stat <= 1 + 25, (
            f"{s.stat} stats for a root with 25 date dirs — the free is_dir() "
            f"from the directory listing is being paid for again"
        )
        assert s.scandir + s.listdir == 1, (
            f"{s.scandir + s.listdir} listings, expected exactly 1 (the root); "
            f"a date dir must never be descended into (docs/154)"
        )

    def test_the_own_root_exclusion_costs_no_resolve_per_entry(self, tmp_path):
        """docs/139's own-root exclusion called ``Path.resolve()`` on EVERY
        entry at both levels — one share round-trip each, and on the alignment
        path (the only caller that passes ``own_root``) that was a third of
        the whole sweep. Every entry is a direct child of a directory the walk
        already holds, so the root is resolved once and the rest is string
        work."""
        root = _seed_shallow(tmp_path / "ws", 30)
        ws = self._ws(root)
        own = tmp_path / "instance" / "history"
        own.mkdir(parents=True)

        with _fs_spy() as without:
            HistoryManager._workspace_token(ws)
        with _fs_spy() as with_own:
            HistoryManager._workspace_token(ws, own_root=own)

        extra = with_own.total - without.total
        assert extra <= 4, (
            f"asking for the own-root exclusion cost {extra} extra syscalls "
            f"over 30 entries — it is resolving per entry again"
        )

    def test_the_exclusion_still_excludes(self, tmp_path):
        """A cost pin alone would pass with the exclusion deleted. SM's own
        history dir nested under a workspace root must not contribute: its
        mtime moving is the scan's own bookkeeping, and folding it in makes
        the scan invalidate its own cache (docs/139 fix 2)."""
        root = tmp_path / "ws"
        _seed_shallow(root, 3)
        own = root / "_sm_instance"
        own.mkdir()

        ws = self._ws(root)
        before = HistoryManager._workspace_token(ws, own_root=own)

        bumped = os.stat(own).st_mtime + 10_000.0
        os.utime(own, (bumped, bumped))

        assert HistoryManager._workspace_token(ws, own_root=own) == before, (
            "SM's own instance dir moved the workspace token — the scan's "
            "bookkeeping is invalidating the scan's own cache"
        )
        # …and with no own_root declared it is ordinary content again.
        assert (HistoryManager._workspace_token(ws)
                != HistoryManager._workspace_token(ws, own_root=own)), (
            "the exclusion made no difference at all — it is not being applied"
        )


# ---------------------------------------------------------------------------
# F4 — the background polls stopped sweeping the archive to answer questions
#      they do not ask
# ---------------------------------------------------------------------------

class TestBackgroundPollsSweepOnce:
    """These two run forever on every open tab. The delta poll fires every 5 s
    and did TWO full staleness sweeps per call; `/datasets/wait` did one to
    compute a run table it never reads. On the customer's share that was
    ~19 s of network round-trips per minute per tab, which is what makes a
    page nobody is looking at slow down the page somebody is."""

    def _app(self, tmp_path, folder):
        from quam_state_manager.web.app import create_app
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        r = c.post("/workspace/add", data={"folder": str(folder)})
        assert r.status_code in (200, 204, 302), r.status_code
        return app, c

    def _count_sweeps(self, monkeypatch, client, url):
        calls = []
        real = DatasetStore.rescan_if_stale

        def counting(self, *a, **k):
            calls.append(k.get("deadline", "no-deadline"))
            return real(self, *a, **k)

        monkeypatch.setattr(DatasetStore, "rescan_if_stale", counting)
        r = client.get(url)
        assert r.status_code == 200, r.status_code
        return calls

    def test_the_delta_poll_sweeps_once_per_store_not_twice(
            self, tmp_path, monkeypatch):
        folder = _seed_archive(tmp_path / "data", dates=6)
        _, c = self._app(tmp_path, folder)
        c.get("/datasets")                       # warm the store, as a page does

        calls = self._count_sweeps(monkeypatch, c, "/datasets/changes-since?ts=0")

        assert len(calls) == 1, (
            f"{len(calls)} staleness sweeps for one folder in one delta poll "
            f"({calls}) — every one of them stats every date dir in the archive"
        )

    def test_the_delta_poll_keeps_the_deadline_on_the_sweep_it_does(
            self, tmp_path, monkeypatch):
        """docs/105 #4 bounded this walk. The sweep that got removed was the
        UNBOUNDED one, which ran first and spent the budget before the bounded
        one was consulted — so this is not merely cheaper, it is the shape the
        budget was written for."""
        folder = _seed_archive(tmp_path / "data", dates=6)
        _, c = self._app(tmp_path, folder)
        c.get("/datasets")

        calls = self._count_sweeps(monkeypatch, c, "/datasets/changes-since?ts=0")

        assert calls and all(d not in (None, "no-deadline") for d in calls), (
            f"the surviving sweep carries no deadline ({calls}) — the poll is "
            f"unbounded again on a folder someone is actively writing"
        )

    def test_the_long_poll_sweeps_nothing(self, tmp_path, monkeypatch):
        """`/datasets/wait` hands the watcher a list of PATHS. The watcher
        takes its own signature on its own thread; the run table it used to
        build here was read by nobody."""
        folder = _seed_archive(tmp_path / "data", dates=6)
        _, c = self._app(tmp_path, folder)
        c.get("/datasets")

        calls = self._count_sweeps(monkeypatch, c, "/datasets/wait?since=-1")

        assert calls == [], (
            f"{len(calls)} staleness sweeps to answer a question about paths"
        )

    def test_the_long_poll_still_watches_the_folder(self, tmp_path):
        """A cost pin alone would pass with `set_roots` deleted."""
        folder = _seed_archive(tmp_path / "data", dates=3)
        app, c = self._app(tmp_path, folder)
        c.get("/datasets")

        r = c.get("/datasets/wait?since=-1")
        assert r.status_code == 200
        assert r.get_json().get("roots", 0) >= 1, (
            "the watcher was handed no roots — nothing wakes the polls now"
        )

    def test_a_folder_with_no_runs_yet_is_not_dropped(self, tmp_path):
        """`rescan=False` means we did not refresh `run_count`, so filtering on
        it would drop a folder that is EMPTY at this instant — which is exactly
        the folder about to receive its first run, and exactly the moment a
        watcher is for."""
        empty = tmp_path / "brand_new"
        empty.mkdir()
        app, c = self._app(tmp_path, empty)

        with app.test_request_context():
            from quam_state_manager.web.routes import _active_dataset_stores
            # normcase: Windows hands tmp_path back as `pytest-of-measurement`
            # while the scanner's resolve() returns the on-disk casing — the
            # docs/87 class, nothing to do with what this pin measures.
            paths = {os.path.normcase(e["path"])
                     for e in _active_dataset_stores(fast=True, rescan=False)}
        assert os.path.normcase(str(empty)) in paths, (
            "an empty data folder fell out of the watched set; its first run "
            "would not wake anything"
        )

    def test_the_delta_poll_still_discovers_a_new_run(self, tmp_path):
        """The end-to-end contract the cost pins must not have broken: the
        delta poll's OWN rescan — the one `rescan=False` leaves it to do —
        still finds a run that landed after the last poll.

        Asserted against a `ts=0` poll rather than against the running cursor
        on purpose. The cursor is a wall-clock float compared with `<=`, and
        this test creates a run microseconds after taking the cursor, so
        whether the row clears that comparison is a race at test timescales
        (measured 3 failures in 40 when asserted that way, and the same
        raciness is 13x WORSE without this commit: 2 in 6). Nothing here can
        fix that comparison, and in production the polls are five seconds
        apart. What this pin owns is discovery — that the rescan happened at
        all — and `ts=0` asks exactly that, deterministically.
        """
        folder = _seed_archive(tmp_path / "data", dates=2)
        _, c = self._app(tmp_path, folder)
        c.get("/datasets")
        c.get("/datasets/changes-since?ts=0")

        run = folder / "2026-06-15" / "#4242_04_power_rabi_235959"
        run.mkdir(parents=True)
        (run / "node.json").write_text(
            json.dumps({"id": 4242, "name": "power_rabi"}), encoding="utf-8")

        body = c.get("/datasets/changes-since?ts=0").get_json()
        ids = {row["id"] for row in body.get("updated", [])}
        assert 4242 in ids, (
            "the delta poll no longer discovers a run that landed after the "
            "last poll — its own rescan is not running, and nothing else on "
            "this path was going to do it"
        )


# ---------------------------------------------------------------------------
# F3' — the two walkers stopped walking the same tree twice
# ---------------------------------------------------------------------------

class TestOneSweepSharedByBothWalkers:
    """`_workspace_token` and `_current_mtime` ask different questions of the
    SAME directories, and a /datasets render walked the archive once for each.
    They share one listing now; they must not share an answer."""

    def _app(self, tmp_path, folder):
        from quam_state_manager.web.app import create_app
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        r = c.post("/workspace/add", data={"folder": str(folder)})
        assert r.status_code in (200, 204, 302), r.status_code
        return app, c

    def _date_dir_stats(self, client, url, dates):
        hits = []
        real = os.stat

        def counting(path, *a, **k):
            try:
                if os.path.basename(os.fspath(path)) in dates:
                    hits.append(os.fspath(path))
            except (TypeError, ValueError):
                pass
            return real(path, *a, **k)

        os.stat = counting
        try:
            r = client.get(url)
        finally:
            os.stat = real
        assert r.status_code == 200, r.status_code
        return len(hits)

    def test_a_datasets_render_stats_each_date_dir_once(self, tmp_path):
        folder = _seed_archive(tmp_path / "data", dates=20)
        _, c = self._app(tmp_path, folder)
        dates = {q.name for q in folder.iterdir() if q.is_dir()}
        c.get("/datasets")                      # warm every other cache first

        n = self._date_dir_stats(c, "/datasets", dates)

        assert n <= 20, (
            "%d stats over 20 date dirs in one render - the workspace token "
            "and the store's staleness check are each sweeping the archive"
            % n
        )

    def test_the_two_walkers_still_answer_independently(self, tmp_path):
        """Sharing the I/O must not merge the ANSWERS: the token folds in
        every root, a store's fingerprint is about its own folder alone."""
        a = _seed_archive(tmp_path / "a", dates=3)
        b = _seed_archive(tmp_path / "b", dates=5)
        ws = Workspace()
        ws.add_root(a)
        ws.add_root(b)

        with _sample_scope():
            token = HistoryManager._workspace_token(ws)
            best_a, n_a = DatasetStore(a)._current_mtime()
            best_b, n_b = DatasetStore(b)._current_mtime()

        assert (n_a, n_b) == (3, 5), (
            "the stores' date counts came back as %r - a shared listing is "
            "being read as a shared answer" % ((n_a, n_b),)
        )
        assert token[0] == 2
        assert best_a != 0.0 and best_b != 0.0


class TestTheSampleScopeIsOneRequest:
    """docs/105 #8's contract, restated for the shared listing: a TTL memo was
    rejected because a run written milliseconds before a poll must be seen by
    THAT poll. A request-scoped one keeps it - every poll is its own request."""

    def test_a_later_request_takes_a_fresh_sample(self, tmp_path):
        from quam_state_manager.web.app import create_app
        folder = _seed_archive(tmp_path / "data", dates=3)
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        c.post("/workspace/add", data={"folder": str(folder)})
        c.get("/datasets")

        store = DatasetStore(folder)
        with app.test_request_context():
            first = store._current_mtime()

        date_dir = sorted(q for q in folder.iterdir() if q.is_dir())[0]
        bumped = os.stat(date_dir).st_mtime + 10.0
        os.utime(date_dir, (bumped, bumped))

        with app.test_request_context():
            second = store._current_mtime()

        assert second != first, (
            "a second request reused the first one's listing - the cache "
            "outlived the request, and the write-then-poll contract with it"
        )

    def test_outside_a_request_nothing_is_cached(self, tmp_path):
        """The scheduler worker, autofit and the CLI never open a scope. They
        must behave exactly as they did before this module existed."""
        folder = _seed_archive(tmp_path / "data", dates=3)
        _dir_sample.end()                        # no scope, as a worker has

        store = DatasetStore(folder)
        first = store._current_mtime()
        date_dir = sorted(q for q in folder.iterdir() if q.is_dir())[0]
        bumped = os.stat(date_dir).st_mtime + 10.0
        os.utime(date_dir, (bumped, bumped))

        assert store._current_mtime() != first, (
            "a caller with no sampling scope was served a cached listing"
        )

    def test_a_leaked_scope_self_heals_on_the_next_begin(self, tmp_path):
        """Worker threads are reused. If a teardown is ever missed, the next
        request must not inherit the stale listing."""
        folder = _seed_archive(tmp_path / "data", dates=3)
        store = DatasetStore(folder)

        _dir_sample.begin()
        try:
            first = store._current_mtime()
            date_dir = sorted(q for q in folder.iterdir() if q.is_dir())[0]
            bumped = os.stat(date_dir).st_mtime + 10.0
            os.utime(date_dir, (bumped, bumped))
            assert store._current_mtime() == first   # same scope, by design

            _dir_sample.begin()                      # a new one, no end() first
            assert store._current_mtime() != first, (
                "a new scope inherited the previous one's listing"
            )
        finally:
            _dir_sample.end()


class TestDirSampleItself:

    def test_a_known_mtime_is_not_stat_ed_again(self, tmp_path):
        """The deep layout reads a chip dir's mtime while listing the root and
        then descends into it. Paying a second stat for the same directory
        would hand back what F2' removed."""
        d = _seed_archive(tmp_path / "chip", dates=4)

        with _fs_spy() as s:
            _dir_sample.sample(d, own_mtime=123.0)

        assert s.stat == 0, "%d stats for a directory whose mtime we had" % s.stat
        assert s.scandir == 1

    def test_a_listing_costs_the_same_whatever_it_holds(self, tmp_path):
        """One stat for the directory, one scandir, and nothing per entry.

        Everything the listing yields — the name, that it is a directory, that
        it is not a symlink — rides that one scandir. The own-root exclusion
        in `_workspace_token` needs the symlink bit PER ENTRY, so taking it
        any other way (``os.path.islink``, which is an ``lstat``) puts the
        per-entry syscall back that docs/155 F2' removed.

        Written as an invariant across sizes rather than a magic number: the
        first version of this pin read ``smp.children`` inside the spy, which
        measures a tuple unpack rather than the listing, and a mutation doing
        exactly that swap passed it.
        """
        few = _seed_archive(tmp_path / "few", dates=3)
        many = _seed_archive(tmp_path / "many", dates=30)

        with _fs_spy() as a:
            smp_few = _dir_sample.sample(few)
        with _fs_spy() as b:
            smp_many = _dir_sample.sample(many)

        assert len(smp_few.children) == 3 and len(smp_many.children) == 30
        assert a.total == b.total, (
            "listing 30 entries cost %d syscalls where 3 cost %d - something "
            "is asking the filesystem per entry" % (b.total, a.total)
        )
        assert b.total == 2, (
            "a listing cost %d syscalls; it should be one stat and one "
            "scandir" % b.total
        )
        assert not any(is_link for _n, _p, is_link in smp_many.children)

    def test_a_listing_failure_is_reported_not_swallowed(self, tmp_path,
                                                         monkeypatch):
        d = _seed_archive(tmp_path / "ws", dates=2)
        real = os.scandir

        def boom(path, *a, **k):
            if os.fspath(path) == str(d):
                raise PermissionError(13, "denied")
            return real(path, *a, **k)

        monkeypatch.setattr(os, "scandir", boom)
        smp = _dir_sample.sample(d)

        assert isinstance(smp.error, OSError), (
            "a failed listing came back as an empty directory - the caller "
            "that propagates one would report the archive as unchanged"
        )


# ---------------------------------------------------------------------------
# F6 — a fast caller stopped paying the walk that `fast=True` exists to skip
# ---------------------------------------------------------------------------

class TestFastCandidatesNeverWalkTheArchive:
    """`_dataset_candidate_folders(fast=True)` validates its cache on
    ``ws.version``. On a MISS it used to fall through and compute
    ``HistoryManager._workspace_token`` — one stat per date dir — which can
    never produce a hit there: the token slot is validated on ``ws.version``
    too, and a fast miss has already failed that test. It was computed only to
    be STORED, priming a cache for some future slow caller at exactly the cost
    the flag exists to avoid.

    That made it far worse than a cold-start constant. The scanner bumps
    ``ws.version`` on every rescan that finds something new, so the walk was
    re-paid every time a run landed — by chip activation (``POST /load``,
    measured 421 -> 29 share ops at 390 date dirs) and by whichever background
    poll fired first afterwards.
    """

    def _app(self, tmp_path, folder, name="_inst"):
        from quam_state_manager.web.app import create_app
        app = create_app(testing=True, instance_path=str(tmp_path / name))
        c = app.test_client()
        r = c.post("/workspace/add", data={"folder": str(folder)})
        assert r.status_code in (200, 204, 302), r.status_code
        return app, c

    # -- cost -------------------------------------------------------------

    def test_a_fast_rebuild_costs_the_same_whatever_the_archive_holds(
            self, tmp_path):
        """The axis that grows every day of measurement, held flat.

        A slope, not an absolute: what must never come back is a per-date-dir
        cost on the fast path.
        """
        counts = {}
        for label, dates in (("small", 6), ("big", 46)):
            folder = _seed_archive(tmp_path / label, dates=dates)
            app, _ = self._app(tmp_path, folder, name="_inst_" + label)
            with app.test_request_context():
                routes._dataset_candidates_cache.clear()
                routes._dataset_candidate_folders(fast=True)      # warm
                app.config["workspace"]._version += 1             # a run landed
                with _sample_scope(), _fs_spy(own_thread=True) as s:
                    routes._dataset_candidate_folders(fast=True)  # the miss
            counts[label] = s.total

        assert counts["big"] == counts["small"], (
            f"40 more date dirs cost {counts['big'] - counts['small']} more "
            f"syscalls on the FAST path ({counts['small']} -> {counts['big']}) "
            f"— the token walk is back, and it is paid every time a run lands"
        )

    def test_the_fast_path_does_not_compute_the_token_at_all(
            self, tmp_path, monkeypatch):
        """Stated directly, because the slope pin above cannot tell a walk
        that got cheaper from a walk that is gone."""
        folder = _seed_archive(tmp_path / "ws", dates=8)
        app, _ = self._app(tmp_path, folder)
        calls = []

        def counting(_ws):
            calls.append(1)
            return "TOKEN"

        monkeypatch.setattr(HistoryManager, "_workspace_token",
                            staticmethod(counting))

        with app.test_request_context():
            routes._dataset_candidates_cache.clear()
            routes._dataset_candidate_folders(fast=True)     # cold miss
            app.config["workspace"]._version += 1
            routes._dataset_candidate_folders(fast=True)     # version miss

        assert calls == [], (
            f"{len(calls)} workspace-token walks on the fast path — that is "
            f"one stat per date dir, for a value the fast path never reads"
        )

    def test_chip_activation_after_a_run_lands_stops_scaling_with_the_archive(
            self, tmp_path):
        """The user-visible half: opening a chip runs
        ``_maybe_data_folder_suggest`` -> ``_data_folder_candidates`` ->
        ``_dataset_candidate_folders(fast=True)``, so ``/load`` inherited the
        walk — and inherited it again after every run that landed.
        """
        live = tmp_path / "chip"
        live.mkdir()
        (live / "state.json").write_text(
            json.dumps({"qubits": {"q1": {"T1": 1e-5}}}), encoding="utf-8")
        (live / "wiring.json").write_text(
            json.dumps({"network": {"host": "1.2.3.4"}}), encoding="utf-8")

        counts = {}
        for label, dates in (("small", 6), ("big", 46)):
            folder = _seed_archive(tmp_path / label, dates=dates)
            app, c = self._app(tmp_path, folder, name="_inst_load_" + label)
            assert c.post("/load", data={"folder": str(live)}).status_code < 400
            app.config["workspace"]._version += 1             # a run landed
            with _fs_spy(own_thread=True) as s:
                assert c.post(
                    "/load", data={"folder": str(live)}).status_code < 400
            counts[label] = s.total

        assert counts["big"] == counts["small"], (
            f"opening a chip cost {counts['big'] - counts['small']} more "
            f"syscalls on the bigger archive ({counts['small']} -> "
            f"{counts['big']}) — a chip load is walking the run archive again"
        )

    # -- the promises the cost must not have cost --------------------------

    def test_a_fast_caller_still_discovers_a_new_candidate_after_a_bump(
            self, tmp_path):
        """The property the old pin was reaching for. It asserted it by
        counting token computations — a PROXY for "did it rebuild" — so
        removing the token broke the pin while the property stood. Assert the
        property.
        """
        folder = _seed_archive(tmp_path / "ws", dates=4)
        other = _seed_archive(tmp_path / "later", dates=2)
        app, _ = self._app(tmp_path, folder)

        with app.test_request_context():
            routes._dataset_candidates_cache.clear()
            before = routes._dataset_candidate_folders(fast=True)
            assert other not in before
            app.config["workspace"].add_root(other)       # bumps ws.version
            after = routes._dataset_candidate_folders(fast=True)

        assert other in after, (
            "a folder the scanner has already discovered is invisible to every "
            "fast caller — polls would not see a new data folder until a full "
            "/datasets render"
        )

    def test_a_fast_caller_answers_from_the_cache_while_the_version_holds(
            self, tmp_path):
        """The other half: without a version bump the cached list stands, so
        the fast path really is a cache and not a rebuild every time."""
        folder = _seed_archive(tmp_path / "ws", dates=4)
        sneaky = _seed_archive(tmp_path / "sneaky", dates=2)
        app, _ = self._app(tmp_path, folder)

        with app.test_request_context():
            routes._dataset_candidates_cache.clear()
            routes._dataset_candidate_folders(fast=True)          # warm
            app.config["workspace"].root_folders.append(sneaky)   # no bump
            again = routes._dataset_candidate_folders(fast=True)

        assert sneaky not in again, (
            "the fast path rebuilt without a version bump — it is not "
            "consulting the cache, and the pin above proves nothing"
        )

    def test_a_slow_caller_still_validates_on_its_token(self, tmp_path):
        """``fast=False`` keeps the token contract exactly: the same token and
        version answers from the cache, a moved token rebuilds."""
        folder = _seed_archive(tmp_path / "ws", dates=4)
        sneaky = _seed_archive(tmp_path / "sneaky", dates=2)
        app, _ = self._app(tmp_path, folder)
        token = {"v": "T1"}

        with app.test_request_context():
            routes._dataset_candidates_cache.clear()
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(HistoryManager, "_workspace_token",
                           staticmethod(lambda ws: token["v"]))
                routes._dataset_candidate_folders()                   # warm
                app.config["workspace"].root_folders.append(sneaky)   # no bump
                assert sneaky not in routes._dataset_candidate_folders()
                token["v"] = "T2"                                     # layout moved
                assert sneaky in routes._dataset_candidate_folders(), (
                    "the token moved and the slow path still answered from "
                    "the cache — the staleness contract is gone"
                )

    def test_an_unvalidated_slot_never_satisfies_a_slow_caller(self, tmp_path):
        """A fast rebuild stores its result with NO token. A slow caller must
        read that as a miss: it never validated the layout, so answering a
        ``fast=False`` caller from it would hand out an unchecked list."""
        folder = _seed_archive(tmp_path / "ws", dates=4)
        sneaky = _seed_archive(tmp_path / "sneaky", dates=2)
        app, _ = self._app(tmp_path, folder)

        with app.test_request_context():
            routes._dataset_candidates_cache.clear()
            routes._dataset_candidate_folders(fast=True)          # stores no token
            app.config["workspace"].root_folders.append(sneaky)   # no bump
            slow = routes._dataset_candidate_folders()

        assert sneaky in slow, (
            "a slow caller was served the fast path's unvalidated slot — the "
            "token it computed was never compared against anything"
        )
