"""Dataset monitoring under a CONCURRENT WRITER (docs/80).

The situation these pin: something other than this process is creating run
folders in a data folder we are polling — a qualibrate node mid-run, or a
second State Manager window on another port. That writer is not atomic at the
folder level. It makes a directory, then writes ``node.json``, then
``data.json``, then figures; every poll that lands inside that sequence sees a
run that is REAL but not finished.

The promise being pinned is threefold and each part has failed in a way a user
would never notice:

  * **never stall** — a half-written file must not cost the 0.9s live-file
    retry ladder (``_READ_ATTEMPTS=4`` × ``_READ_BACKOFF_S``), per file, per
    scan. That is what turned "another process is writing" into a poll that
    takes minutes.
  * **never lose** — a run caught mid-write must not freeze with the partial
    metadata we happened to catch. It is re-parsed until it is whole.
  * **never die** — one broken folder must not 500 the poll (the client
    swallows it and the table silently stops updating), and one hung request
    must not leave the in-flight latch stuck forever.

Everything here writes REAL directories and REAL files, including genuinely
truncated JSON, and several cases run a real background writer thread while
the poll loop runs. Synthetic dicts would not exercise the code that broke.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from quam_state_manager.core import safe_io
from quam_state_manager.core.dataset import DatasetStore
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app


# ----------------------------------------------------------------------
# Real-folder writer
# ----------------------------------------------------------------------

def _node_payload(run_id: int, name: str, date: str, qubits) -> dict:
    return {
        "metadata": {"name": name, "status": "successful",
                     "run_start": f"{date}T01:00:00", "run_end": f"{date}T01:00:01",
                     "description": f"run {run_id}"},
        "data": {"parameters": {"model": {"qubits": qubits}}, "outcomes": {}},
        "id": run_id, "parents": [], "created_at": f"{date}T01:00:00",
    }


def _data_payload(qubit: str, t1: float) -> dict:
    return {"fit_results": {qubit: {"T1": t1}}}


class RunWriter:
    """Creates run folders the way a real writer does — incrementally.

    ``mode`` picks how far through the sequence the folder is left:

    ``complete``    node.json + data.json, both valid.
    ``node_only``   the folder + node.json exist; data.json has not been
                    written yet (the common case for the newest run).
    ``partial``     node.json exists but holds TRUNCATED JSON — a reader that
                    lands mid-write sees exactly this.
    ``empty``       the run folder exists with nothing in it yet.
    ``reverse``     data.json written before node.json.
    """

    def __init__(self, root: Path, date: str = "2026-08-05"):
        self.root = root
        self.date = date
        self.root.mkdir(parents=True, exist_ok=True)

    def folder(self, run_id: int, name: str = "test_experiment",
               hhmmss: str = "010000") -> Path:
        return self.root / self.date / f"#{run_id}_{name}_{hhmmss}"

    def write(self, run_id: int, *, mode: str = "complete",
              name: str = "test_experiment", hhmmss: str = "010000",
              t1: float = 8.0e-6) -> Path:
        run = self.folder(run_id, name, hhmmss)
        run.mkdir(parents=True, exist_ok=True)
        qubit = f"q{run_id}"
        node_text = json.dumps(_node_payload(run_id, name, self.date, [qubit]))
        data_text = json.dumps(_data_payload(qubit, t1))
        if mode == "empty":
            return run
        if mode == "partial":
            # A real truncation: valid JSON prefix, no closing braces. This is
            # what a reader sees between the writer's first and last chunk.
            (run / "node.json").write_text(node_text[: len(node_text) // 2],
                                           encoding="utf-8")
            return run
        if mode == "node_only":
            (run / "node.json").write_text(node_text, encoding="utf-8")
            return run
        if mode == "reverse":
            (run / "data.json").write_text(data_text, encoding="utf-8")
            (run / "node.json").write_text(node_text, encoding="utf-8")
            return run
        (run / "node.json").write_text(node_text, encoding="utf-8")
        (run / "data.json").write_text(data_text, encoding="utf-8")
        return run

    def finish(self, run_id: int, *, name: str = "test_experiment",
               hhmmss: str = "010000", t1: float = 8.0e-6) -> Path:
        """Complete a folder previously left partial/node_only/empty."""
        return self.write(run_id, mode="complete", name=name, hhmmss=hhmmss, t1=t1)


def _touch_tree(root: Path, when: float | None = None) -> None:
    """Bump root + date-dir mtimes so the staleness gate reliably opens.

    Real writers move these for free; in a test the writes can land inside one
    coarse filesystem tick, so we make the change explicit rather than sleeping.
    """
    when = time.time() + 5 if when is None else when
    for p in [root, *[d for d in root.iterdir() if d.is_dir()]]:
        try:
            os.utime(p, (when, when))
        except OSError:
            pass


# ----------------------------------------------------------------------
# App + poll driver
# ----------------------------------------------------------------------

@pytest.fixture
def app_client(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    return app, app.test_client()


class Poller:
    """Drives /datasets/changes-since exactly as the browser does."""

    def __init__(self, client):
        self.client = client
        self.ts = 0.0
        self.seen: dict[str, dict] = {}
        self.durations: list[float] = []
        self.responses: list[dict] = []
        self.duplicate_hits = 0

    def poll(self) -> dict:
        t0 = time.perf_counter()
        r = self.client.get(f"/datasets/changes-since?ts={self.ts}")
        self.durations.append(time.perf_counter() - t0)
        assert r.status_code == 200, f"poll must never fail: {r.status_code}"
        body = r.get_json()
        self.responses.append(body)
        for row in body.get("updated", []):
            uid = f"{row.get('f')}:{row.get('id')}"
            if uid in self.seen:
                self.duplicate_hits += 1
            self.seen[uid] = row
        for uid in body.get("vanished", []):
            self.seen.pop(uid, None)
        self.ts = body.get("now", self.ts)
        return body

    def run_ids(self) -> set[int]:
        return {row["id"] for row in self.seen.values()}

    @property
    def slowest(self) -> float:
        return max(self.durations) if self.durations else 0.0


def _add_folder(client, folder: Path):
    r = client.post("/workspace/add", data={"folder": str(folder)})
    assert r.status_code in (200, 204, 302), r.status_code


# ======================================================================
# The retry ladder must not run during a scan
# ======================================================================

class TestNoRetryLadderOnScan:
    """The 0.9s-per-file stall is the engine of every symptom in docs/80."""

    def test_scan_json_never_sleeps_on_a_missing_file(self, tmp_path):
        t0 = time.perf_counter()
        assert safe_io.scan_json(tmp_path / "nope.json") is None
        assert time.perf_counter() - t0 < 0.05

    def test_scan_json_never_sleeps_on_truncated_json(self, tmp_path):
        p = tmp_path / "half.json"
        p.write_text('{"metadata": {"name": "x", "st', encoding="utf-8")
        t0 = time.perf_counter()
        assert safe_io.scan_json(p) is None
        assert time.perf_counter() - t0 < 0.05

    def test_scan_json_rejects_non_objects_without_raising(self, tmp_path):
        p = tmp_path / "list.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        assert safe_io.scan_json(p) is None

    def test_read_json_keeps_its_ladder_for_live_files(self, tmp_path):
        """The live-state path MUST keep retrying — that is what makes a read
        landing inside an experiment's atomic replace survive. Only the scan
        path opted out."""
        p = tmp_path / "live.json"
        p.write_text("{trunc", encoding="utf-8")
        t0 = time.perf_counter()
        with pytest.raises(Exception):
            safe_io.read_json(p)
        assert time.perf_counter() - t0 >= 0.3, "the live ladder must still back off"

    def test_read_json_attempts_override(self, tmp_path):
        p = tmp_path / "live.json"
        p.write_text("{trunc", encoding="utf-8")
        t0 = time.perf_counter()
        with pytest.raises(Exception):
            safe_io.read_json(p, attempts=1)
        assert time.perf_counter() - t0 < 0.05

    def test_a_scan_never_sleeps_no_matter_how_many_runs_are_mid_write(
            self, tmp_path, monkeypatch):
        """Case 3+4 — the whole point, asserted DETERMINISTICALLY.

        Wall-clock alone is a weak pin here: the parse pass fans out over a
        thread pool, so on a many-core machine the old per-file backoff hid
        inside the parallelism and a timing threshold passed by luck. What
        must be true is categorical — a scan performs no retry sleeps at all,
        because during a scan there is nothing to wait for.
        """
        sleeps: list[float] = []
        real_sleep = time.sleep

        def spy(seconds):
            sleeps.append(seconds)
            real_sleep(0)

        monkeypatch.setattr(safe_io.time, "sleep", spy)

        root = tmp_path / "data"
        w = RunWriter(root)
        for i in range(1, 41):
            w.write(i, mode="partial" if i % 2 else "node_only",
                    hhmmss=f"{i:06d}")
        t0 = time.perf_counter()
        store = DatasetStore(root)
        elapsed = time.perf_counter() - t0

        assert not sleeps, (
            f"the scan slept {len(sleeps)} times ({sum(sleeps):.2f}s total) "
            "waiting on files another writer had simply not finished")
        assert elapsed < 2.0, f"scan of 40 mid-write runs took {elapsed:.2f}s"
        assert len(store.runs) == 40, "a mid-write run is still a real run"

    def test_a_poll_never_sleeps_on_mid_write_runs(self, tmp_path, app_client,
                                                   monkeypatch):
        """The same guarantee at the level the user actually feels it."""
        app, client = app_client
        root = tmp_path / "data"
        w = RunWriter(root)
        w.write(1)
        _add_folder(client, root)
        p = Poller(client)
        p.poll()

        for i in range(2, 22):
            w.write(i, mode="partial", hhmmss=f"{i:06d}")
        _touch_tree(root)

        sleeps: list[float] = []
        real_sleep = time.sleep
        monkeypatch.setattr(safe_io.time, "sleep",
                            lambda s: (sleeps.append(s), real_sleep(0))[1])
        p.poll()
        assert not sleeps, f"poll slept {sum(sleeps):.2f}s on unfinished runs"


# ======================================================================
# Nothing is lost, nothing freezes half-parsed
# ======================================================================

class TestIncompleteRunsHeal:
    def test_a_partial_run_is_reparsed_until_whole(self, tmp_path):
        """Case 4: partial node.json → the row appears (its id/name/date come
        from the folder name) but must NOT freeze without its metadata."""
        root = tmp_path / "data"
        w = RunWriter(root)
        w.write(7, mode="partial")
        store = DatasetStore(root)
        assert 7 in store.runs
        assert store.runs[7].incomplete is True
        assert store.runs[7].description == "", "nothing was readable yet"

        w.finish(7)
        _touch_tree(root)
        store.rescan_if_stale()
        assert store.runs[7].incomplete is False
        assert store.runs[7].description == "run 7"
        assert store.runs[7].fit_results, "data.json must be picked up too"

    def test_reparse_happens_even_if_only_the_run_folder_changed(self, tmp_path):
        """The date dir's mtime does NOT move when a file inside a run folder
        is written, so the B27 date-dir short-circuit would have skipped the
        walk that reaches an incomplete run. Membership in _incomplete_paths
        is what defeats that."""
        root = tmp_path / "data"
        w = RunWriter(root)
        w.write(1, mode="complete")
        w.write(2, mode="partial", hhmmss="020000")
        store = DatasetStore(root)
        assert store.runs[2].incomplete is True
        date_dir = root / w.date
        date_mtime_before = date_dir.stat().st_mtime

        w.finish(2, hhmmss="020000")
        os.utime(date_dir, (date_mtime_before, date_mtime_before))  # pin it back
        store.force_rescan()
        assert store.runs[2].incomplete is False
        assert store.runs[2].description == "run 2"

    def test_node_only_run_completes_later(self, tmp_path):
        root = tmp_path / "data"
        w = RunWriter(root)
        w.write(3, mode="node_only")
        store = DatasetStore(root)
        assert store.runs[3].description == "run 3"
        assert not store.runs[3].fit_results
        assert store.runs[3].incomplete is False, "a file that is ABSENT is not partial"

        w.finish(3)
        _touch_tree(root)
        store.rescan_if_stale()
        assert store.runs[3].fit_results

    def test_reverse_write_order_lands_correct(self, tmp_path):
        """Case 5."""
        root = tmp_path / "data"
        RunWriter(root).write(4, mode="reverse")
        store = DatasetStore(root)
        assert store.runs[4].description == "run 4"
        assert store.runs[4].fit_results

    def test_an_empty_run_folder_does_not_crash_the_scan(self, tmp_path):
        root = tmp_path / "data"
        w = RunWriter(root)
        w.write(5, mode="empty")
        w.write(6, mode="complete", hhmmss="030000")
        store = DatasetStore(root)
        assert {5, 6} <= set(store.runs)

    def test_a_run_deleted_between_scans_vanishes_cleanly(self, tmp_path):
        """Case 7 — including for an INCOMPLETE run, whose path is still
        tracked for vanish-detection despite carrying a sentinel print."""
        import shutil
        root = tmp_path / "data"
        w = RunWriter(root)
        w.write(1, mode="complete")
        w.write(2, mode="partial", hhmmss="020000")
        store = DatasetStore(root)
        assert {1, 2} <= set(store.runs)

        shutil.rmtree(w.folder(2, hhmmss="020000"))
        _touch_tree(root)
        store.rescan_if_stale()
        assert 2 not in store.runs
        assert 2 in [rid for rid, _ts in store._vanished]
        assert not any("#2_" in str(p) for p in store._incomplete_paths)


# ======================================================================
# The poll endpoint under a live writer
# ======================================================================

class TestPollEndpointUnderWriter:
    def test_a_new_run_appears_exactly_once(self, tmp_path, app_client):
        """Case 1."""
        app, client = app_client
        root = tmp_path / "data"
        w = RunWriter(root)
        w.write(1)
        _add_folder(client, root)
        p = Poller(client)
        p.poll()
        assert p.run_ids() == {1}

        w.write(2, hhmmss="020000")
        _touch_tree(root)
        p.poll()
        assert p.run_ids() == {1, 2}
        p.poll()
        assert p.duplicate_hits == 0 or p.run_ids() == {1, 2}

    def test_fifty_runs_at_once_all_arrive(self, tmp_path, app_client):
        """Case 6."""
        app, client = app_client
        root = tmp_path / "data"
        w = RunWriter(root)
        w.write(1)
        _add_folder(client, root)
        p = Poller(client)
        p.poll()

        for i in range(2, 52):
            w.write(i, hhmmss=f"{i:06d}")
        _touch_tree(root)
        for _ in range(4):
            p.poll()
            if len(p.run_ids()) == 51:
                break
        assert p.run_ids() == set(range(1, 52))

    def test_runs_landing_during_the_poll_loop_are_never_lost(self, tmp_path, app_client):
        """Case 2 + 13 (soak, in miniature): a background thread writes runs
        while we poll. Every run must eventually surface — the cursor may
        re-offer rows, never skip them."""
        app, client = app_client
        root = tmp_path / "data"
        w = RunWriter(root)
        w.write(1)
        _add_folder(client, root)
        p = Poller(client)
        p.poll()

        total = 24
        stop = threading.Event()

        def writer():
            for i in range(2, total + 2):
                if stop.is_set():
                    return
                # node first, data a beat later: the poll WILL land between them
                w.write(i, mode="node_only", hhmmss=f"{i:06d}")
                time.sleep(0.01)
                w.finish(i, hhmmss=f"{i:06d}")
                _touch_tree(root)
                time.sleep(0.01)

        th = threading.Thread(target=writer, daemon=True)
        th.start()
        deadline = time.time() + 45
        while time.time() < deadline:
            p.poll()
            if th.is_alive():
                time.sleep(0.05)
                continue
            p.poll()
            p.poll()
            if len(p.run_ids()) == total + 1:
                break
        stop.set()
        th.join(timeout=10)
        missing = set(range(1, total + 2)) - p.run_ids()
        assert not missing, f"runs never surfaced: {sorted(missing)}"
        assert p.slowest < 5.0, f"slowest poll {p.slowest:.2f}s"

    def test_a_run_that_stays_partial_still_completes_later(self, tmp_path, app_client):
        """The write stalls (writer crashed / is slow); the row is visible and
        fills in when the writer resumes, with no manual rescan."""
        app, client = app_client
        root = tmp_path / "data"
        w = RunWriter(root)
        w.write(1)
        _add_folder(client, root)
        p = Poller(client)
        p.poll()

        def row9():
            key = next(k for k in p.seen if k.endswith(":9"))
            return p.seen[key]

        w.write(9, mode="partial", hhmmss="090000")
        _touch_tree(root)
        p.poll()
        assert 9 in p.run_ids(), "a mid-write run is still a real run — show it"
        # Identity comes from the folder name, so the row is already useful...
        assert row9()["exp"] == "test_experiment"
        # ...but nothing that lives in node.json/data.json was readable yet.
        assert row9()["status"] == ""
        assert not row9()["metric"]

        # The writer finishes. No manual rescan, no page reload: the next poll
        # must re-emit the row filled in, because the incomplete parse withheld
        # a usable fingerprint.
        w.finish(9, hhmmss="090000")
        _touch_tree(root)
        for _ in range(3):
            p.poll()
            if row9()["status"]:
                break
        assert row9()["status"] == "successful"
        assert row9()["metric"], "data.json was picked up on a later poll"


# ======================================================================
# One bad folder must not take the poll down
# ======================================================================

class TestPollIsolationAndBudget:
    def test_a_failing_folder_holds_its_cursor_and_others_keep_flowing(
            self, tmp_path, app_client, monkeypatch):
        """Case 8+9: the route used to have NO try/except around the per-folder
        loop, so one raising store 500'd the poll — which the client swallows,
        leaving the table frozen with no error anyone sees."""
        app, client = app_client
        good = tmp_path / "good"
        bad = tmp_path / "bad"
        RunWriter(good).write(1)
        RunWriter(bad, date="2026-08-04").write(2)
        _add_folder(client, good)
        _add_folder(client, bad)

        real = DatasetStore.changes_since

        def flaky(self, ts, date=None, **kw):
            if self.folder_path.name == "bad":
                raise RuntimeError("simulated folder failure")
            return real(self, ts, date=date, **kw)

        monkeypatch.setattr(DatasetStore, "changes_since", flaky)

        p = Poller(client)
        body = p.poll()
        assert body["partial"] is True and body["skipped"] == 1
        assert 1 in p.run_ids(), "the healthy folder must keep updating"
        assert body["now"] == 0.0, "a failed folder holds the shared cursor"

        # ...and it heals without a reload once the folder recovers.
        monkeypatch.setattr(DatasetStore, "changes_since", real)
        _touch_tree(good)
        _touch_tree(bad)
        body = p.poll()
        assert body["partial"] is False
        assert {1, 2} <= p.run_ids()

    def test_the_budget_stops_the_walk_and_reports_partial(
            self, tmp_path, app_client, monkeypatch):
        """Case 10: past the wall-clock budget the remaining folders are left
        un-scanned with their cursors HELD, so the response stays prompt and
        nothing is skipped."""
        app, client = app_client
        a, b = tmp_path / "a", tmp_path / "b"
        RunWriter(a).write(1)
        RunWriter(b, date="2026-08-04").write(2)
        _add_folder(client, a)
        _add_folder(client, b)

        monkeypatch.setattr(routes, "_POLL_BUDGET_S", 0.05)
        real = DatasetStore.changes_since

        def slow(self, ts, date=None):
            time.sleep(0.2)
            return real(self, ts, date=date)

        monkeypatch.setattr(DatasetStore, "changes_since", slow)
        p = Poller(client)
        body = p.poll()
        assert body["partial"] is True and body["skipped"] >= 1
        assert body["now"] == 0.0, "un-scanned folders hold the cursor"

        monkeypatch.setattr(routes, "_POLL_BUDGET_S", 60.0)
        monkeypatch.setattr(DatasetStore, "changes_since", real)
        for _ in range(3):
            p.poll()
        assert {1, 2} <= p.run_ids(), "the skipped window is re-offered, not lost"

    def test_an_unresolvable_workspace_never_500s(self, tmp_path, app_client, monkeypatch):
        app, client = app_client
        RunWriter(tmp_path / "d").write(1)
        _add_folder(client, tmp_path / "d")

        def boom(*a, **k):
            raise RuntimeError("workspace exploded")

        monkeypatch.setattr(routes, "_active_dataset_stores", boom)
        r = client.get("/datasets/changes-since?ts=123.5")
        assert r.status_code == 200
        body = r.get_json()
        assert body["now"] == 123.5, "hold the cursor rather than skip a window"
        assert body["partial"] is True

    def test_a_vanishing_folder_mid_poll_does_not_500(self, tmp_path, app_client):
        import shutil
        app, client = app_client
        root = tmp_path / "data"
        w = RunWriter(root)
        w.write(1)
        _add_folder(client, root)
        p = Poller(client)
        p.poll()
        shutil.rmtree(root)
        r = client.get(f"/datasets/changes-since?ts={p.ts}")
        assert r.status_code == 200


# ======================================================================
# The shared tags file (data folders may legitimately be shared)
# ======================================================================

class TestSharedTagsFile:
    def test_a_concurrent_writers_tags_are_not_clobbered(self, tmp_path):
        """Case 11 — the reported reason data-folder sharing was unsafe.

        Two DatasetStore instances over ONE folder stand in for two SM
        windows: their _tags_lock is per-instance, exactly like the real
        cross-process situation.
        """
        root = tmp_path / "data"
        w = RunWriter(root)
        w.write(1)
        w.write(2, hhmmss="020000")
        win_a = DatasetStore(root)
        win_b = DatasetStore(root)

        win_a.add_tag(1, "cooldown-3")
        win_b.add_tag(2, "bad-fit")          # B's dict never saw A's tag

        on_disk = json.loads((root / "quashboard_tags.json").read_text(encoding="utf-8"))
        assert on_disk["tags"]["1"] == ["cooldown-3"], "A's tag was clobbered"
        assert on_disk["tags"]["2"] == ["bad-fit"]

    def test_notes_and_tags_merge_independently(self, tmp_path):
        root = tmp_path / "data"
        w = RunWriter(root)
        w.write(1)
        w.write(2, hhmmss="020000")
        a, b = DatasetStore(root), DatasetStore(root)
        a.set_note(1, "retuned by hand")
        b.add_tag(2, "keep")
        a.add_tag(1, "verified")

        on_disk = json.loads((root / "quashboard_tags.json").read_text(encoding="utf-8"))
        assert on_disk["notes"]["1"] == "retuned by hand"
        assert on_disk["tags"]["2"] == ["keep"]
        assert on_disk["tags"]["1"] == ["verified"]

    def test_removing_a_tag_still_removes_it(self, tmp_path):
        """Merging must not resurrect a deletion from the stale disk copy."""
        root = tmp_path / "data"
        RunWriter(root).write(1)
        s = DatasetStore(root)
        s.add_tag(1, "x")
        s.remove_tag(1, "x")
        on_disk = json.loads((root / "quashboard_tags.json").read_text(encoding="utf-8"))
        assert "1" not in on_disk.get("tags", {})

    def test_the_writer_adopts_the_other_windows_entries(self, tmp_path):
        root = tmp_path / "data"
        w = RunWriter(root)
        w.write(1)
        w.write(2, hhmmss="020000")
        a, b = DatasetStore(root), DatasetStore(root)
        b.add_tag(2, "from-b")
        a.add_tag(1, "from-a")
        assert a.runs[2].tags == ["from-b"], "the merge should surface B's tag in A"

    def test_a_corrupt_tags_file_does_not_lose_the_new_write(self, tmp_path):
        root = tmp_path / "data"
        RunWriter(root).write(1)
        s = DatasetStore(root)
        (root / "quashboard_tags.json").write_text("{ not json", encoding="utf-8")
        s.add_tag(1, "survives")
        on_disk = json.loads((root / "quashboard_tags.json").read_text(encoding="utf-8"))
        assert on_disk["tags"]["1"] == ["survives"]

    def test_the_favorite_migration_still_rewrites_wholesale(self, tmp_path):
        """The legacy-bookmark migration rewrites every key by design and must
        keep the whole-file path."""
        root = tmp_path / "data"
        w = RunWriter(root)
        w.write(1)
        w.write(2, hhmmss="020000")
        (root / "quashboard_tags.json").write_text(
            json.dumps({"bookmarks": [1, 2], "tags": {}, "notes": {}}), encoding="utf-8")
        s = DatasetStore(root)
        assert s.runs[1].bookmarked and s.runs[2].bookmarked
        on_disk = json.loads((root / "quashboard_tags.json").read_text(encoding="utf-8"))
        assert on_disk["bookmarks"] == []
        assert set(on_disk["tags"]) == {"1", "2"}


class TestIncompleteIsBounded:
    """"Not finished yet" is a bet. Some files never become valid — a
    hand-edited node.json holding a JSON list, a permission error, real
    corruption — and betting on those forever is expensive in a way that is
    easy to miss: the folder re-parses on every scan AND its membership in
    _incomplete_paths defeats the date-dir short-circuit, so every SIBLING run
    in that date is re-walked too, permanently undoing the optimisation that
    keeps a steady-state poll at O(date dirs) on a large workspace.
    """

    def _broken_and_good(self, root: Path):
        w = RunWriter(root)
        broken = w.write(1, mode="empty")
        (broken / "node.json").write_text("[1, 2, 3]", encoding="utf-8")  # valid JSON, wrong shape
        w.write(2, mode="complete", hhmmss="020000")
        return w

    def test_a_permanently_broken_run_stops_being_re_parsed(self, tmp_path):
        root = tmp_path / "data"
        w = self._broken_and_good(root)
        store = DatasetStore(root)
        assert store.runs[1].incomplete is True

        parses: list[str] = []
        real = DatasetStore._parse_run_folder

        def spy(self, entry, *a, **k):
            parses.append(entry.name)
            return real(self, entry, *a, **k)

        DatasetStore._parse_run_folder = spy
        try:
            for i in range(6):
                parses.clear()
                _touch_tree(root, time.time() + 10 * (i + 1))
                store.rescan_if_stale()
                last = list(parses)
        finally:
            DatasetStore._parse_run_folder = real

        assert last == [], "an unfixable run must stop taxing every poll"
        assert not store._incomplete_paths
        fp = store._folder_fp[w.folder(1)]
        assert fp[0] != ("incomplete",), "it is cached under its REAL fingerprint now"

    def test_the_healthy_siblings_stop_being_dragged_along(self, tmp_path):
        """The expensive half: one broken run used to re-walk its whole date."""
        root = tmp_path / "data"
        self._broken_and_good(root)
        store = DatasetStore(root)
        parses: list[str] = []
        real = DatasetStore._parse_run_folder

        def spy(self, entry, *a, **k):
            parses.append(entry.name)
            return real(self, entry, *a, **k)

        DatasetStore._parse_run_folder = spy
        try:
            for i in range(6):
                parses.clear()
                _touch_tree(root, time.time() + 10 * (i + 1))
                store.rescan_if_stale()
        finally:
            DatasetStore._parse_run_folder = real
        assert not any("#2_" in n for n in parses)

    def test_a_run_that_keeps_changing_still_heals(self, tmp_path):
        """The bound must not cut short a genuine writer: a real one moves the
        fingerprint on every write, which resets the count."""
        root = tmp_path / "data"
        run = RunWriter(root).folder(5)
        run.mkdir(parents=True)
        full = json.dumps(_node_payload(5, "late", "2026-08-05", ["q5"]))
        store = None
        for i in range(6):
            run.joinpath("node.json").write_text(full[: 10 + i * 8], encoding="utf-8")
            _touch_tree(root, time.time() + 10 * (i + 1))
            if store is None:
                store = DatasetStore(root)
            else:
                store.rescan_if_stale()
        run.joinpath("node.json").write_text(full, encoding="utf-8")
        _touch_tree(root, time.time() + 200)
        store.rescan_if_stale()
        assert store.runs[5].status == "successful"
        assert store.runs[5].incomplete is False

    def test_a_fixed_file_is_picked_up_even_after_we_gave_up(self, tmp_path):
        root = tmp_path / "data"
        w = self._broken_and_good(root)
        store = DatasetStore(root)
        for i in range(5):
            _touch_tree(root, time.time() + 10 * (i + 1))
            store.rescan_if_stale()
        assert not store._incomplete_paths          # given up

        w.finish(1)                                  # the user repairs it
        _touch_tree(root, time.time() + 500)
        store.rescan_if_stale()
        assert store.runs[1].status == "successful", (
            "giving up must not be permanent — a changed fingerprint re-parses")
