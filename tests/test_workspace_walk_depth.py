r"""The two O(runs) directory walks on the /datasets render path (docs/154).

Both were pure waste, and both were invisible until a real workspace put 5,574
run folders on a NAS share behind them. A py-spy profile of one ``/datasets``
render (30.5 s wall, 1,524 samples at 50 Hz, `Z:\DR1\...\Wallraff_9Q_20dB`)
split as:

    history.py  `_workspace_token`            20.2 s   (66%)
    routes.py   `_dataset_candidate_folders`   9.7 s   (32%)
    everything else (store aggregation + rendering 4.6 MB)
                                               0.6 s    (2%)

``_workspace_token`` descends a FIXED two levels, which is the date level only
under ``<root>/<chip>/<date>/<run>``. A workspace root that is itself a chip's
results folder is one level shallower — ``<root>/<date>/<run>`` — so level 2
lands on RUN folders and the "cheap token" becomes O(runs), the exact cost its
own docstring promises never to pay. ``_dataset_candidate_folders`` called
``is_dir()`` once per ENTRY on a set of grandparents that had exactly ONE
distinct member.

Re-measured after the fix on a LOCAL tree built to that workspace's shape
(5,574 runs over 90 date dirs), counting syscalls rather than pathlib calls
(hooking ``Path.stat`` as well as ``os.stat`` double-counts every stat):

                                  before            after
    _workspace_token          11,420 / 493.6 ms   182 /  11.6 ms
    _dataset_candidate_folders
      (it calls the token)    16,994 / 708.2 ms   182 /  31.8 ms

The candidate loop's own share is the difference — 5,574 syscalls, exactly one
per entry, now zero. The wall-clock figures above are local NVMe; the share is
where they mattered, and the NAS was unreachable when this was written, so the
end-to-end re-measurement on it is still owed.

These pins are shaped to fail on the mutation, not on the wall clock: they
count filesystem operations, so they mean the same thing on a fast local disk
as on the share where the cost was found.
"""
from __future__ import annotations

import contextlib
import json
import os
import pathlib
from pathlib import Path

from quam_state_manager.core.history import HistoryManager
from quam_state_manager.core.scanner import Workspace
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _np(p) -> str:
    """Normalized path string for comparison.

    Windows hands ``tmp_path`` back as ``pytest-of-measurement`` while the
    scanner's ``resolve()`` returns the real on-disk casing
    (``pytest-of-Measurement``); comparing the raw strings makes these pins
    fail for a reason that has nothing to do with what they measure (the
    tmp-path case-identity class in docs/87).
    """
    return os.path.normcase(str(p))


def _seed_run(root: Path, run_id: int, *, date: str = "2026-08-19",
              chip: str | None = None) -> Path:
    """One run folder at ``<root>[/<chip>]/<date>/#<id>_exp_HHMMSS``.

    ``chip=None`` is the SHALLOW layout this project actually meets in the
    field (a workspace root pointed straight at a chip's results folder);
    passing ``chip`` gives the canonical deep one.

    A ``quam_state/`` with state.json + wiring.json is written because that —
    not node.json — is what makes the folder a Workspace ENTRY, which is the
    list ``_dataset_candidate_folders`` iterates.
    """
    parent = root if chip is None else root / chip
    date_dir = parent / date
    date_dir.mkdir(parents=True, exist_ok=True)
    run = date_dir / f"#{run_id}_test_experiment_0100{run_id:02d}"
    run.mkdir()
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": "test_experiment", "status": "successful",
                     "run_start": f"{date}T01:00:00",
                     "run_end": f"{date}T01:00:01"},
        "data": {"parameters": {"model": {"qubits": ["q0"]}}, "outcomes": {}},
        "id": run_id, "parents": [], "created_at": f"{date}T01:00:00",
    }), encoding="utf-8")
    (run / "data.json").write_text(json.dumps(
        {"fit_results": {"q0": {"T1": 8.0e-6}}}), encoding="utf-8")
    qs = run / "quam_state"
    qs.mkdir()
    qs.joinpath("state.json").write_text(json.dumps(
        {"qubits": {"q0": {}}, "qubit_pairs": {}}), encoding="utf-8")
    qs.joinpath("wiring.json").write_text(json.dumps(
        {"network": {"host": "1.2.3.4", "cluster_name": "C"}}), encoding="utf-8")
    return run


@contextlib.contextmanager
def _fs_spy():
    """Record every directory STAT and every directory LISTING, by path.

    Hooked at the ``os`` level, not on ``pathlib``. That is not a style
    choice: docs/155 F2' moved this walk from ``Path.iterdir()`` +
    ``Path.is_dir()`` + ``Path.stat()`` onto ``os.scandir`` + ``os.stat``,
    and a pathlib-only spy went BLIND — every pin below started asserting
    against an empty set instead of against the walk. A spy that only sees
    one spelling of a syscall pins the spelling, not the cost.

    ``os`` is also the layer that cannot be bypassed: ``Path.stat`` calls
    ``os.stat`` and ``Path.iterdir`` calls ``os.listdir``, so both spellings
    land here, and ``Path.is_dir()`` (a stat in CPython) is counted with the
    stats — which is the whole point, since both docs/154 defects were stat
    storms wearing an ``is_dir()`` hat.
    """
    seen: dict[str, list[str]] = {"stat": [], "iterdir": []}
    real_stat, real_scandir, real_listdir = os.stat, os.scandir, os.listdir

    def stat(path, *a, **k):
        seen["stat"].append(_np(path))
        return real_stat(path, *a, **k)

    def scandir(path=".", *a, **k):
        seen["iterdir"].append(_np(path))
        return real_scandir(path, *a, **k)

    def listdir(path=".", *a, **k):
        seen["iterdir"].append(_np(path))
        return real_listdir(path, *a, **k)

    os.stat, os.scandir, os.listdir = stat, scandir, listdir
    try:
        yield seen
    finally:
        os.stat, os.scandir, os.listdir = real_stat, real_scandir, real_listdir


def _touched(seen: dict[str, list[str]]) -> set[str]:
    return set(seen["stat"]) | set(seen["iterdir"])


# ---------------------------------------------------------------------------
# _workspace_token: the date level is found by NAME, not by depth
# ---------------------------------------------------------------------------

class TestWorkspaceTokenStopsAtTheDateLevel:

    def test_shallow_layout_never_touches_a_run_folder(self, tmp_path):
        """``<root>/<date>/<run>`` — level 2 is runs, and must stay unvisited."""
        root = tmp_path / "Wallraff_9Q_Temp"
        run1 = _seed_run(root, 1)
        run2 = _seed_run(root, 2)
        date_dir = root / "2026-08-19"
        ws = Workspace()
        ws.add_root(root)

        with _fs_spy() as seen:
            HistoryManager._workspace_token(ws)

        touched = _touched(seen)
        assert _np(run1) not in touched, (
            "the workspace token stat'ed a RUN folder — on the real 5,574-run "
            "workspace this is 11,148 SMB round-trips (20.2 s) per call"
        )
        assert _np(run2) not in touched
        # The date level itself is still measured (that is C33's requirement)…
        assert _np(date_dir) in set(seen["stat"])
        # …but never descended into, which is what made it O(runs).
        assert _np(date_dir) not in set(seen["iterdir"])

    def test_shallow_layout_still_flips_on_a_new_run_c33(self, tmp_path):
        """Stopping early must not reintroduce the staleness the token exists
        to prevent: a run added inside an EXISTING date dir bumps that dir's
        mtime, and the token folds that mtime in."""
        root = tmp_path / "results"
        _seed_run(root, 1)
        date_dir = root / "2026-08-19"
        ws = Workspace()
        ws.add_root(root)

        before = HistoryManager._workspace_token(ws)
        assert HistoryManager._workspace_token(ws) == before  # stable when idle

        _seed_run(root, 2)
        bumped = date_dir.stat().st_mtime + 10.0
        os.utime(date_dir, (bumped, bumped))

        assert HistoryManager._workspace_token(ws) != before, (
            "token did not move when a run landed in an existing date dir "
            "(finding C33) — the alignment scan would serve a stale result"
        )

    def test_canonical_deep_layout_is_unchanged(self, tmp_path):
        """``<root>/<chip>/<date>/<run>`` — the chip dir is NOT date-named, so
        the descent proceeds exactly as before and stops on the date dirs."""
        root = tmp_path / "workspace"
        run = _seed_run(root, 1, chip="chipA")
        chip_dir = root / "chipA"
        date_dir = chip_dir / "2026-08-19"
        ws = Workspace()
        ws.add_root(root)

        with _fs_spy() as seen:
            HistoryManager._workspace_token(ws)

        assert _np(chip_dir) in set(seen["iterdir"])   # still descended
        assert _np(date_dir) in set(seen["stat"])      # date level measured
        assert _np(run) not in _touched(seen)          # runs still untouched

    def test_a_dir_that_merely_CONTAINS_a_date_is_still_descended(self, tmp_path):
        """``fullmatch``, not ``search``: the descent stops only on a dir that
        IS a date.

        Labs name chip folders after the day they were made
        (``chipA_2026-08-19_backup``). Matching a date ANYWHERE in the name
        would end the walk one level early on such a root, so the date dirs
        below it are never stat'ed and a run landing in an existing date dir
        stops moving the token — reintroducing exactly the staleness (C33)
        this token exists to prevent, silently, and only for labs with that
        naming habit. A false NEGATIVE only costs speed; a false POSITIVE
        costs correctness, which is why the asymmetry is pinned.
        """
        root = tmp_path / "workspace"
        chip = "chipA_2026-08-19_backup"
        _seed_run(root, 1, chip=chip)
        chip_dir = root / chip
        date_dir = chip_dir / "2026-08-19"
        ws = Workspace()
        ws.add_root(root)

        with _fs_spy() as seen:
            HistoryManager._workspace_token(ws)

        assert _np(chip_dir) in set(seen["iterdir"]), (
            "the descent stopped on a dir that only CONTAINS a date — a chip "
            "folder is not a date folder, and its date children went unmeasured"
        )
        assert _np(date_dir) in set(seen["stat"])

        # …and the staleness guarantee therefore still holds on such a root.
        before = HistoryManager._workspace_token(ws)
        _seed_run(root, 2, chip=chip)
        bumped = date_dir.stat().st_mtime + 10.0
        os.utime(date_dir, (bumped, bumped))
        assert HistoryManager._workspace_token(ws) != before, (
            "a new run under a date-named-looking chip dir no longer moves "
            "the token (finding C33)"
        )

    def test_cost_is_flat_in_the_number_of_runs(self, tmp_path):
        """The invariant, stated as a measurement: adding runs must not add
        filesystem operations. This is the pin that fails loudest if anyone
        reinstates a fixed-depth descent."""
        small = tmp_path / "small"
        for i in range(2):
            _seed_run(small, i)
        big = tmp_path / "big"
        for i in range(40):
            _seed_run(big, i)

        ws_small = Workspace()
        ws_small.add_root(small)
        ws_big = Workspace()
        ws_big.add_root(big)

        with _fs_spy() as seen_small:
            HistoryManager._workspace_token(ws_small)
        with _fs_spy() as seen_big:
            HistoryManager._workspace_token(ws_big)

        n_small = len(seen_small["stat"]) + len(seen_small["iterdir"])
        n_big = len(seen_big["stat"]) + len(seen_big["iterdir"])
        assert n_small == n_big, (
            f"walk cost grew with run count ({n_small} ops for 2 runs vs "
            f"{n_big} for 40) — the token is O(runs) again"
        )


# ---------------------------------------------------------------------------
# _dataset_candidate_folders: dedupe BEFORE the stat
# ---------------------------------------------------------------------------

class TestCandidateFoldersStatOnce:

    def _app(self, tmp_path, folders):
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        for f in folders:
            c.post("/workspace/add", data={"folder": str(f)})
        return app

    def test_one_stat_per_distinct_grandparent_not_per_entry(
            self, tmp_path, monkeypatch):
        fa = tmp_path / "chipA"
        for i in range(12):
            _seed_run(fa, i)
        app = self._app(tmp_path, [fa])
        routes._dataset_candidates_cache.clear()

        # Isolate the candidate loop: the token has its own walk (pinned above)
        # and would otherwise be counted here too.
        monkeypatch.setattr(routes.HistoryManager, "_workspace_token",
                            staticmethod(lambda ws, own_root=None: "TOK"))

        with app.test_request_context():
            ws = routes.current_app.config["workspace"]
            entries = [e for e in ws.all_entries if not e.is_standalone]
            grandparents = {e.folder_path.parent.parent for e in entries}
            with _fs_spy() as seen:
                result = routes._dataset_candidate_folders()

        assert len(entries) >= 12, "fixture must have many entries per folder"
        assert len(grandparents) == 1, (
            "fixture must reproduce the real shape: many entries, ONE distinct "
            "grandparent — otherwise this pin proves nothing"
        )
        # The result is exactly what the per-entry loop built.
        assert fa in result
        assert result == sorted(set(result))

        gp_stats = [p for p in seen["stat"] if p in {_np(g) for g in grandparents}]
        assert len(gp_stats) <= len(grandparents), (
            f"stat'ed a candidate {len(gp_stats)} times for "
            f"{len(grandparents)} distinct path(s) — the per-entry is_dir() "
            f"storm is back ({len(entries)} entries would mean "
            f"{len(entries)} SMB round-trips)"
        )

    def test_result_is_unchanged_for_a_real_two_folder_workspace(self, tmp_path):
        """Safety property: deduping before the stat must not change WHICH
        folders come out — including a genuine second data folder that is not
        a workspace root of its own."""
        fa = tmp_path / "chipA"
        fb = tmp_path / "chipB"
        for i in range(3):
            _seed_run(fa, i)
            _seed_run(fb, 100 + i)
        app = self._app(tmp_path, [fa, fb])
        routes._dataset_candidates_cache.clear()

        with app.test_request_context():
            result = routes._dataset_candidate_folders()

        assert fa in result and fb in result
