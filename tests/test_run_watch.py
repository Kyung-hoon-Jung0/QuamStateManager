"""docs/141 §4p — near-instant reaction to a new qualibrate run folder.

A stat-based watcher thread (core/run_watch.py) bumps a tick when a watched
root's signature changes; GET /datasets/wait long-polls on it; live-wake.js
wakes the existing polls (the new-run popup poll, the Datasets delta poll)
the moment the tick moves. Pinned here:

- the signature moves on a new date dir, a new run dir, and a file landing
  in the newest run dir; it does not move on nothing; an unreadable root is
  None, never an exception
- the watcher: first sight is a baseline (no tick), a change is one tick, a
  removed root is forgotten, wait() returns early on a change and after the
  timeout otherwise, a raising signature function is logged not fatal
- the route: clamps its inputs, refreshes the roots from the active dataset
  folders, answers changed=true within one interval of a run landing, and
  changed=false after the timeout when nothing happened; one watcher per app
- the wiring: live-wake.js is a core script, the popup poll listens to
  sm:runs-changed with an in-flight guard, DatasetVirtual.pollNow exists
- the jsdom harness (live_wake_selfcheck.cjs) pins the client loop
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from quam_state_manager.core import run_watch
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent


def _bump_mtime(p: Path) -> None:
    """Directory mtimes have coarse resolution on some filesystems: make sure
    a follow-up change is visible even inside the same tick of the clock."""
    now = time.time() + 2.0
    os.utime(p, (now, now))


class TestSignature:
    def test_moves_on_each_kind_of_change_and_not_otherwise(self, tmp_path):
        root = tmp_path / "data"
        root.mkdir()
        s0 = run_watch.signature(str(root))
        assert s0 is not None and s0[1] is None
        assert run_watch.signature(str(root)) == s0, "nothing changed, nothing moves"
        d = root / "2026-08-29"
        d.mkdir()
        _bump_mtime(root)
        s1 = run_watch.signature(str(root))
        assert s1 != s0 and s1[1] == "2026-08-29" and s1[3] is None
        run = d / "#12_rabi_101010"
        run.mkdir()
        _bump_mtime(d)
        s2 = run_watch.signature(str(root))
        assert s2 != s1 and s2[3] == "#12_rabi_101010" and s2[5] == 1
        (run / "node.json").write_text("{}", encoding="utf-8")
        _bump_mtime(run)
        s3 = run_watch.signature(str(root))
        assert s3 != s2 and s3[3] == "#12_rabi_101010", "a file landing in the newest run moves it"
        assert run_watch.signature(str(root)) == s3
        # a non-date directory is not a date dir; a newer date wins by name
        (root / "notes").mkdir()
        (root / "2026-08-30").mkdir()
        s4 = run_watch.signature(str(root))
        assert s4[1] == "2026-08-30" and s4[3] is None

    def test_an_unreadable_root_is_none(self, tmp_path):
        assert run_watch.signature(str(tmp_path / "missing")) is None
        f = tmp_path / "file"
        f.write_text("x", encoding="utf-8")
        assert run_watch.signature(str(f)) is None


class TestWatcher:
    def test_first_sight_is_a_baseline_then_one_tick_per_change(self, tmp_path):
        root = tmp_path / "data"
        (root / "2026-08-29").mkdir(parents=True)
        w = run_watch.RunWatcher(interval_s=0.05)
        w.set_roots([str(root)])
        assert w.poll_once() is False and w.tick == 0, "the first look records, never announces"
        assert w.poll_once() is False and w.tick == 0
        (root / "2026-08-29" / "#1_x_000001").mkdir()
        _bump_mtime(root / "2026-08-29")
        assert w.poll_once() is True and w.tick == 1
        assert w.poll_once() is False and w.tick == 1

    def test_roots_can_change_and_a_removed_root_is_forgotten(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir(); b.mkdir()
        w = run_watch.RunWatcher()
        w.set_roots([str(a), str(a), ""])
        assert w.roots == (str(a),)
        w.poll_once()
        w.set_roots([str(b)])
        assert w.roots == (str(b),)
        w.poll_once()
        (a / "2026-01-01").mkdir()
        _bump_mtime(a)
        assert w.poll_once() is False, "a root no longer watched cannot tick"
        (b / "2026-01-01").mkdir()
        _bump_mtime(b)
        assert w.poll_once() is True

    def test_wait_returns_early_on_a_change_and_after_the_timeout_otherwise(self, tmp_path):
        root = tmp_path / "data"
        root.mkdir()
        w = run_watch.RunWatcher(interval_s=0.05)
        w.set_roots([str(root)])
        w.poll_once()
        t0 = time.perf_counter()
        assert w.wait(0, 0.3) == 0
        assert 0.25 <= time.perf_counter() - t0 < 1.5
        w.start()
        try:
            assert w.running
            got = {}

            def waiter():
                got["tick"] = w.wait(0, 5.0)
            th = threading.Thread(target=waiter)
            th.start()
            time.sleep(0.15)
            (root / "2026-08-29").mkdir()
            _bump_mtime(root)
            th.join(timeout=3.0)
            assert not th.is_alive() and got["tick"] == 1
            # a stale cursor (from a previous server) never blocks
            assert w.wait(99, 2.0) == 1
        finally:
            w.stop()
        assert not w.running

    def test_a_raising_signature_is_logged_not_fatal(self, tmp_path):
        calls = []

        def bad(root):
            calls.append(root)
            raise RuntimeError("boom")
        w = run_watch.RunWatcher(signature_fn=bad)
        w.set_roots([str(tmp_path)])
        assert w.poll_once() is False and calls
        assert w.stats()["polls"] == 1

    def test_wait_clamps_to_the_maximum(self):
        w = run_watch.RunWatcher()
        t0 = time.perf_counter()
        assert w.wait(0, -5) == 0
        assert time.perf_counter() - t0 < 0.5
        assert run_watch.MAX_WAIT_S == 25.0


# ---------------------------------------------------------------------------
# the route
# ---------------------------------------------------------------------------
def _node_payload(run_id: int, name: str, date: str) -> dict:
    return {"id": run_id, "created_at": f"{date}T01:00:00+00:00", "metadata": {"name": name, "description": "", "data_path": ""},
            "data": {"quam": "./quam_state"}, "parameters": {"model": {"qubits": ["q1"]}}}


@pytest.fixture
def wake_client(tmp_path: Path):
    root = tmp_path / "data"
    (root / "2026-08-29").mkdir(parents=True)
    run = root / "2026-08-29" / "#1_seed_010000"
    run.mkdir()
    (run / "node.json").write_text(json.dumps(_node_payload(1, "seed", "2026-08-29")), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    app.config["run_watcher"] = run_watch.RunWatcher(interval_s=0.05)
    c = app.test_client()
    r = c.post("/workspace/add", data={"folder": str(root)})
    assert r.status_code in (200, 204, 302), r.status_code
    yield c, root, app
    w = app.config.get("run_watcher")
    if w:
        w.stop()


class TestWaitRoute:
    def test_nothing_happens_answers_unchanged_after_the_timeout(self, wake_client):
        c, root, app = wake_client
        t0 = time.perf_counter()
        d = c.get("/datasets/wait?since=0&timeout=0.3").get_json()
        assert d["changed"] is False and d["tick"] == 0 and d["roots"] >= 1
        assert 0.25 <= time.perf_counter() - t0 < 2.0
        assert app.config["run_watcher"].running, "the first call starts the watcher"

    def test_a_run_landing_answers_within_one_interval(self, wake_client):
        c, root, app = wake_client
        c.get("/datasets/wait?since=0&timeout=0.2")            # registers the root, baselines it
        run = root / "2026-08-29" / "#2_rabi_010500"
        run.mkdir()
        _bump_mtime(root / "2026-08-29")
        t0 = time.perf_counter()
        d = c.get("/datasets/wait?since=0&timeout=5").get_json()
        dt = time.perf_counter() - t0
        assert d["changed"] is True and d["tick"] >= 1
        assert dt < 1.0, f"answered after {dt:.2f}s"
        # the file that finishes the run moves the tick again
        tick = d["tick"]
        (run / "node.json").write_text(json.dumps(_node_payload(2, "rabi", "2026-08-29")), encoding="utf-8")
        _bump_mtime(run)
        d2 = c.get(f"/datasets/wait?since={tick}&timeout=5").get_json()
        assert d2["changed"] is True and d2["tick"] > tick
        # and the poll the client runs next sees the new run
        p = c.get("/datasets/poll").get_json()
        assert p["run_id"] == 2

    def test_garbage_inputs_are_clamped(self, wake_client):
        c, root, app = wake_client
        d = c.get("/datasets/wait?since=abc&timeout=0.1").get_json()
        assert d["changed"] is False and d["tick"] == 0, "a garbage cursor reads as 0"
        # a garbage timeout falls back to the default (25 s), which the wait
        # clamps to MAX_WAIT_S -- lowered here so the fallback is observable
        # without waiting it out
        import pytest as _pt
        mp = _pt.MonkeyPatch()
        mp.setattr(run_watch, "MAX_WAIT_S", 0.2)
        try:
            t0 = time.perf_counter()
            d = c.get("/datasets/wait?since=0&timeout=zzz").get_json()
            assert isinstance(d["tick"], int)
            assert 0.15 <= time.perf_counter() - t0 < 1.5
        finally:
            mp.undo()

    def test_the_handshake_answers_at_once_and_never_as_a_change(self, wake_client):
        """since=-1 is the client's first contact: the current tick, now.
        The first REAL change on a fresh server (tick 0 -> 1) is then reported
        as a change -- the bug real Chrome showed on the first cut, where the
        client's "first answer never wakes" rule swallowed exactly that."""
        c, root, app = wake_client
        t0 = time.perf_counter()
        d = c.get("/datasets/wait?since=-1&timeout=25").get_json()
        assert d == {"tick": 0, "changed": False, "roots": d["roots"]} and time.perf_counter() - t0 < 1.0
        (root / "2026-08-29" / "#3_x_010600").mkdir()
        _bump_mtime(root / "2026-08-29")
        d = c.get("/datasets/wait?since=0&timeout=5").get_json()
        assert d["changed"] is True and d["tick"] == 1
        d = c.get("/datasets/wait?since=-1&timeout=25").get_json()
        assert d == {"tick": 1, "changed": False, "roots": d["roots"]}

    def test_one_watcher_per_app(self, wake_client):
        c, root, app = wake_client
        c.get("/datasets/wait?since=0&timeout=0.1")
        w1 = app.config["run_watcher"]
        c.get("/datasets/wait?since=0&timeout=0.1")
        assert app.config["run_watcher"] is w1


class TestWiring:
    def test_live_wake_is_a_core_script_and_the_polls_listen(self):
        base = (ROOT / "quam_state_manager/web/templates/base.html").read_text(encoding="utf-8")
        assert "asset_url('live-wake.js')" in base
        assert base.index("asset_url('live-wake.js')") > base.index("asset_url('app.js')")
        app_js = (ROOT / "quam_state_manager/web/static/app.js").read_text(encoding="utf-8")
        assert 'document.addEventListener("sm:runs-changed", function() {' in app_js
        assert "if (_inFlight) { _wakeAgain = true; return; }" in app_js
        # the baseline poll runs shortly after load -- otherwise the first WAKE
        # becomes the baseline and the run that caused it is never shown
        tail = app_js[app_js.index("window.NewRunPoll = {"):]
        # docs/141 4ac: gated on the popup existing. Unconditional, it put a
        # background request into every jsdom harness that evaluates app.js,
        # which is what made version_diff_selfcheck.cjs flake ~50% of runs.
        assert "if (document.getElementById('new-run-popup')) _schedule(1500);" in tail[:1600]
        ds = (ROOT / "quam_state_manager/web/static/dataset-virtual.js").read_text(encoding="utf-8")
        assert "pollNow: function () {" in ds and "state.pollWakeAgain" in ds
        lw = (ROOT / "quam_state_manager/web/static/live-wake.js").read_text(encoding="utf-8")
        assert "/datasets/wait?since=" in lw and "sm:runs-changed" in lw and "DatasetVirtual.pollNow()" in lw


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_live_wake_selfcheck():
    node = shutil.which("node")
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True, capture_output=True, timeout=30)
    except Exception:
        pytest.skip("jsdom not installed")
    r = subprocess.run([node, str(ROOT / "tests" / "live_wake_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok - ") >= 12, r.stdout


class TestPoolSafety:
    """docs/141 4ac (CRITICAL) -- a long poll must never take the WSGI pool.

    `live-wake.js` is a CORE script, so every open SM tab permanently holds one
    `/datasets/wait` for up to 25 s. `qsm serve` / `qsm browser` run waitress at
    its default `threads=4`: four tabs took the whole pool and `GET /` measured
    22.8 s. The desktop launcher (`app.run(threaded=True)`) was never affected,
    which is why the doc's "the servers all run threaded=True" read as true.
    """

    def test_serve_gives_waitress_more_threads_than_the_wait_bound(self, monkeypatch):
        from quam_state_manager import cli
        from quam_state_manager.web import routes

        captured = {}

        def fake_serve(app, **kw):
            captured.update(kw)

        monkeypatch.setattr(cli, "waitress_serve", fake_serve, raising=False)
        assert cli._SERVE_THREADS >= 8, "waitress's default 4 is what froze the UI"
        assert cli._SERVE_THREADS > routes._WAIT_SLOTS_N + 2, \
            "the pool must keep workers for ordinary requests while every slot waits"
        src = (Path(cli.__file__)).read_text(encoding="utf-8")
        assert "threads=_SERVE_THREADS" in src, "the constant must actually reach waitress"

    def test_a_refused_wait_answers_saturated_instead_of_blocking(self, tmp_path):
        """When every slot is taken the route answers at once rather than
        holding another worker -- and says so, so the client can back off."""
        import threading as _t
        from quam_state_manager.web import routes

        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        with app.app_context():
            sem = routes._wait_slots()
            taken = [sem.acquire(blocking=False) for _ in range(routes._WAIT_SLOTS_N)]
        assert all(taken), "fixture: every slot is held"
        try:
            t0 = time.monotonic()
            r = c.get("/datasets/wait?since=0&timeout=25")
            dt = time.monotonic() - t0
            assert r.status_code == 200
            body = r.get_json()
            assert body.get("saturated") is True, body
            assert body.get("changed") is False
            # short enough to be invisible, long enough that a client which
            # ignores `saturated` still cannot spin (C2-2 measured ~73 req/s)
            assert routes._WAIT_SATURATED_FLOOR_S >= 1.0
            assert dt < 10.0, f"a refused wait must not block the worker ({dt:.1f}s)"
            assert dt >= routes._WAIT_SATURATED_FLOOR_S - 0.2, \
                f"a refused wait must still cost a floor ({dt:.2f}s)"
        finally:
            with app.app_context():
                for _ in range(routes._WAIT_SLOTS_N):
                    routes._wait_slots().release()

    def test_the_semaphore_is_per_app_not_a_module_global(self, tmp_path):
        """A test session, an embedded host and the desktop launcher all create
        several apps; a module-level semaphore would make one app's waiters
        throttle another's (and the desktop pool is unbounded anyway)."""
        from quam_state_manager.web import routes

        a = create_app(testing=True, instance_path=str(tmp_path / "a"))
        b = create_app(testing=True, instance_path=str(tmp_path / "b"))
        with a.app_context():
            sa = routes._wait_slots()
        with b.app_context():
            sb = routes._wait_slots()
        assert sa is not sb
        with a.app_context():
            assert routes._wait_slots() is sa, "and it is stable within one app"

    def test_a_normal_wait_still_answers_the_tick(self, tmp_path):
        """The guard must not change the ordinary path."""
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        r = c.get("/datasets/wait?since=-1")
        assert r.status_code == 200
        body = r.get_json()
        assert body["changed"] is False and "saturated" not in body
        r2 = c.get("/datasets/wait?since=0&timeout=0.2")
        assert r2.status_code == 200 and "tick" in r2.get_json()


class TestSecondTick:
    """docs/141 4ac (R2-2/R2-3). The module's docstring promises a SECOND tick
    when qualibrate writes node.json into the run it just created -- that is
    what turns docs/80's `incomplete` run into a complete one on screen.

    It never fired on Windows: `DirEntry.stat()` is served from the
    FindFirstFile listing (the PARENT directory's cached copy of the child's
    timestamps) and NTFS does not refresh it for a write INSIDE the child.
    The pins could not see it because every one of them called `_bump_mtime`
    (an explicit `os.utime` on the run directory) first -- a direct metadata
    write, which DOES propagate, unlike the real event. So these tests use no
    `os.utime` anywhere.
    """

    @staticmethod
    def _run_dir(root, date="2026-08-30", name="#1_foo_120000"):
        d = root / date / name
        d.mkdir(parents=True)
        return d

    def test_a_file_landing_in_the_newest_run_moves_the_signature(self, tmp_path):
        from quam_state_manager.core import run_watch

        run = self._run_dir(tmp_path)
        before = run_watch.signature(str(tmp_path))
        assert before is not None
        time.sleep(0.05)
        (run / "node.json").write_text('{"id": 1}', encoding="utf-8")
        after = run_watch.signature(str(tmp_path))
        assert after != before, \
            "a write INSIDE the newest run must move the signature (no os.utime here)"

    def test_a_write_into_an_older_run_of_the_same_day_also_ticks(self, tmp_path):
        """The trigger is 'any write inside the newest date directory', not
        'the run being created'. docs/141 4ac widened it deliberately (the
        alternative -- statting only the newest run -- needs a correct 'newest',
        and run folders are `#<id>_...`, so `#10_` sorts below `#9_`)."""
        from quam_state_manager.core import run_watch

        old = self._run_dir(tmp_path, name="#1_old_100000")
        self._run_dir(tmp_path, name="#2_new_120000")
        before = run_watch.signature(str(tmp_path))
        time.sleep(0.05)
        (old / "figure.png").write_bytes(b"x")
        assert run_watch.signature(str(tmp_path)) != before

    def test_the_first_tick_still_fires_for_a_new_run_directory(self, tmp_path):
        from quam_state_manager.core import run_watch

        (tmp_path / "2026-08-30").mkdir(parents=True)
        before = run_watch.signature(str(tmp_path))
        time.sleep(0.05)
        (tmp_path / "2026-08-30" / "#7_bar_130000").mkdir()
        assert run_watch.signature(str(tmp_path)) != before

    def test_the_source_uses_os_stat_not_direntry_stat(self):
        """The mechanism, pinned where a filesystem cannot be relied on to
        reproduce it: DirEntry.stat() is the cache read."""
        src = (Path(__file__).resolve().parent.parent
               / "quam_state_manager/core/run_watch.py").read_text(encoding="utf-8")
        i = src.index("runs.append(")
        line = src[i:src.index(chr(10), i)]
        assert "os.stat(os.path.join(dpath, e.name))" in line, line
        assert "e.stat(" not in line
