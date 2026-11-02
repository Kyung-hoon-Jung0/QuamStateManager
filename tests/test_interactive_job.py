"""D-1 (docs/305): the Interactive menu answers at once; the replay runs as a job.

A fake ``_invoke`` blocks on an Event, so every state of the job is observable.
"""
import threading
import time
from types import SimpleNamespace

import pytest

from quam_state_manager.core.interactive_plots import replot as R
from quam_state_manager.generator.iplot_sources import fingerprint


def _fig():
    return {"key": "fit", "title": "fit", "axes": [], "size_inches": [6, 4]}


@pytest.fixture
def world(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    (run_dir / "quam_state").mkdir(parents=True)
    (run_dir / "node.json").write_text('{"metadata": {"name": "05_fit_thing"}}')
    (run_dir / "data.json").write_text('{"figures": {"fit": "figure_fit.png"}}')
    (run_dir / "figure_fit.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    source = tmp_path / "plotting.py"
    source.write_text("x = 1")
    run = SimpleNamespace(folder_path=str(run_dir), experiment_name="05_fit_thing",
                          qubits=["q1"], qubit_pairs=[])
    gate = threading.Event()
    calls = []
    payload = {"figures": [_fig()], "errors": []}

    def fake_invoke(env, folder, root, timeout, progress=None):
        calls.append(root)
        if progress:
            progress("drawing figure", 1, 1, "fit")
        assert gate.wait(10)
        return dict(payload, module_files=[{"path": str(source)}], module_trees=[],
                    code_fingerprint=fingerprint([source]))

    monkeypatch.setattr("quam_state_manager.core.config_generator.get_selected_env", lambda _: "/fake/py")
    monkeypatch.setattr(R, "_invoke", fake_invoke)
    monkeypatch.setattr(R, "replot_capability", lambda run, ip: {
        "available": True, "reason": "", "util": "fit_thing", "env": "/fake/py"})
    with R._LOCK:
        R._CACHE.clear(); R._INFLIGHT.clear(); R._JOBS.clear(); R._HELD.clear()
    from quam_state_manager.web.app import create_app
    from quam_state_manager.web import routes as r
    ds = SimpleNamespace(runs={1: run})
    monkeypatch.setattr(r, "_resolve_run", lambda uid: (ds, 1, ""))
    app = create_app()
    app.instance_path = str(tmp_path / "instance")
    for name in ("_replot_progress.html", "_dataset_interactive.html", "_dataset_replot.html"):
        app.jinja_env.get_template(name)      # compile outside the timed request
    yield SimpleNamespace(client=app.test_client(), gate=gate, calls=calls, run=run,
                          payload=payload, instance=app.instance_path)
    gate.set()

    # Finish jobs while their synthetic worker/environment are still patched.
    # Otherwise a job can write its result into the next test's cleared cache.
    end = time.monotonic() + 10
    while time.monotonic() < end:
        with R._LOCK:
            active = any(j["state"] in ("starting", "replaying") for j in R._JOBS.values())
        if not active:
            break
        time.sleep(.01)
    assert not active, "synthetic replay job did not finish during teardown"


def _wait(client, job, states=("done", "failed"), limit=10.0):
    end = time.monotonic() + limit
    seen = []
    while time.monotonic() < end:
        s = client.get(f"/dataset/u/interactive/status?job={job}").get_json()
        if not seen or seen[-1] != s["state"]:
            seen.append(s["state"])
        if s["state"] in states:
            return seen, s
        time.sleep(.02)
    raise AssertionError(f"job never finished: {seen}")


def _job(body):
    import re
    m = re.search(r'data-replot-job="([0-9a-f]+)"', body)
    return m and m.group(1)


def test_the_first_answer_is_immediate_and_carries_the_static_figures(world):
    world.client.get("/dataset/u/interactive/status?job=warm")   # first-request app setup
    t = time.monotonic()
    body = world.client.get("/dataset/u/interactive").data.decode()
    assert time.monotonic() - t < 0.5
    assert _job(body), "a progress strip names its job"
    assert "(static)" in body and "/dataset/u/fig/" in body
    assert "%" not in body.split("data-replot-job")[1].split("</div>")[0]


def test_the_states_run_in_order_and_done_renders_like_the_synchronous_path(world):
    body = world.client.get("/dataset/u/interactive").data.decode()
    job = _job(body)
    s = world.client.get(f"/dataset/u/interactive/status?job={job}").get_json()
    assert s["state"] in ("starting", "replaying")
    end = time.monotonic() + 5
    while s.get("key") != "fit" and time.monotonic() < end:
        s = world.client.get(f"/dataset/u/interactive/status?job={job}").get_json()
    assert s["state"] == "replaying" and s["label"] == "drawing figure 1 of 1 (fit)"
    world.gate.set()
    seen, final = _wait(world.client, job)
    assert final["state"] == "done" and seen[-1] == "done"
    done = world.client.get("/dataset/u/interactive").data
    assert b"data-replot-job" not in done
    # the synchronous render of the same cached result, byte for byte
    from quam_state_manager.web import routes  # noqa: F401
    import quam_state_manager.core.interactive_plots.replot as RR
    saved = RR.start_job
    RR.start_job = lambda *a, **k: None
    try:
        with RR._LOCK:
            RR._CACHE.clear()
        again = world.client.get("/dataset/u/interactive").data   # disk hit, same path
    finally:
        RR.start_job = saved
    assert again == done
    assert len(world.calls) == 1


def test_a_second_click_joins_the_same_job(world):
    a = _job(world.client.get("/dataset/u/interactive").data.decode())
    first = R.job_status(a)["t0"]
    time.sleep(.01)
    b = _job(world.client.get("/dataset/u/interactive").data.decode())
    assert a == b and R.job_status(b)["t0"] == first, "the running job was joined, not restarted"
    world.gate.set()
    _wait(world.client, a)
    assert len(world.calls) == 1


def test_replot_door_duplicates_the_default_figures(world):
    """Measure both menus and their tile payloads against the same fake replay."""
    import re
    world.payload["util"] = "fit_thing"
    world.gate.set()
    job = _job(world.client.get("/dataset/u/interactive").data.decode())
    _wait(world.client, job)
    default = world.client.get("/dataset/u/interactive").data.decode()
    extra = world.client.get("/dataset/u/replot").data.decode()
    tiles = lambda html: re.findall(r'class="ds-interactive-plot"[^>]*data-fig="([^"]+)"', html)
    assert tiles(default) == tiles(extra) == ["fit"]
    # Both menus point to the very same tile endpoint, so mounting the extra
    # menu produces a second visible copy without adding a figure or a replay.
    assert 'data-endpoint="replot/plot"' in default and 'data-endpoint="replot/plot"' in extra
    a = world.client.get("/dataset/u/replot/plot?fig=fit")
    b = world.client.get("/dataset/u/replot/plot?fig=fit")
    assert a.status_code == b.status_code == 200 and a.json == b.json
    assert len(world.calls) == 1
    print("duplicate door: default=1 tile, extra=1 identical tile, replay calls=1")


def test_interactive_has_one_container_and_retry_forces_a_background_job(world):
    from pathlib import Path
    detail = (Path(__file__).resolve().parents[1] / "quam_state_manager" /
              "web/templates/_dataset_detail.html").read_text(encoding="utf-8")
    assert 'id="ds-replot-container"' not in detail
    assert 'ds-replot-bar' not in detail
    assert detail.count('id="ds-interactive-container"') == 1
    world.gate.set()
    job = _job(world.client.get("/dataset/u/interactive").data.decode())
    _wait(world.client, job)
    done = world.client.get("/dataset/u/interactive").data.decode()
    assert "loadDatasetInteractive('u', this.closest('.dataset-tab-content'), true)" in done
    before = len(world.calls)
    world.gate.clear()
    try:
        retry = world.client.get("/dataset/u/interactive?force=1").data.decode()
        assert _job(retry), "Retry must bypass the cached menu and return a job"
    finally:
        world.gate.set()
    _wait(world.client, _job(retry))
    assert len(world.calls) == before + 1
    assert not _job(world.client.get("/dataset/u/interactive").data.decode())


def test_the_job_still_caches_when_nobody_is_polling(world):
    job = _job(world.client.get("/dataset/u/interactive").data.decode())
    # the client navigated away (gen bump): nobody asks for status
    world.gate.set()
    end = time.monotonic() + 5
    while R.job_status(job)["state"] not in ("done", "failed") and time.monotonic() < end:
        time.sleep(.02)
    assert R.peek(world.run, world.instance) is not None


def test_an_uncacheable_result_is_held_for_the_refetch_not_restarted(world, tmp_path):
    (tmp_path / "run" / "figure_fit.png").unlink()      # nothing saved to fall back to
    world.payload.update(figures=[], errors=[{"stage": "timeout", "trace": "exceeded 180s"}])
    job = _job(world.client.get("/dataset/u/interactive").data.decode())
    world.gate.set()
    _, final = _wait(world.client, job)
    assert final["state"] == "failed"
    body = world.client.get("/dataset/u/interactive").data.decode()
    assert "data-replot-job" not in body and "timeout" in body
    assert len(world.calls) == 1
    # held, never cached: past the hold the next open replays again
    with R._LOCK:
        for k, (until, res) in list(R._HELD.items()):
            R._HELD[k] = (0.0, res)
    assert _job(world.client.get("/dataset/u/interactive").data.decode())


def test_a_hold_never_answers_a_direct_replay(world, tmp_path):
    (tmp_path / "run" / "figure_fit.png").unlink()
    world.payload.update(figures=[], errors=[{"stage": "timeout", "trace": "x"}])
    world.gate.set()
    R.replot_run(world.run, world.instance)
    assert not R._HELD, "only a job holds its result"


def test_an_unknown_job_reads_unknown(world):
    s = world.client.get("/dataset/u/interactive/status?job=nope").get_json()
    assert s["state"] == "unknown"


def test_interactive_lab_check_wait_measurement(world, monkeypatch):
    """Use the real driver and draw locks, with fake slow worker responses."""
    from concurrent.futures import ThreadPoolExecutor
    from quam_state_manager.core import lab_waveform
    entered = threading.Event()

    def slow(*args, **kwargs):
        entered.set()
        time.sleep(.7)
        return dict(world.payload, module_files=[], module_trees=[])

    monkeypatch.setattr(R, "_invoke", slow)
    monkeypatch.setattr(lab_waveform, "cached", lambda *a: None)
    monkeypatch.setattr(lab_waveform, "_env_sig", lambda *a: ())
    monkeypatch.setattr(lab_waveform, "_run", lambda *a: {
        "ok": True, "items": [{"ok": True}], "sources": {}})
    with ThreadPoolExecutor(2) as pool:
        replay = pool.submit(R.replot_run, world.run, world.instance, force=True)
        assert entered.wait(5)
        t0 = time.monotonic()
        result = lab_waveform.draw("/fake/py", [("fake.Pulse", {})])
        waited = time.monotonic() - t0
        replay.result(timeout=5)
    assert result[0]["ok"]
    print(f"lab check during 0.7 s replay: {waited:.3f} s")
    assert waited >= .5


def test_a_second_process_result_is_picked_up_after_the_lab_lock(world, monkeypatch):
    """The disk is re-read after machine_lock: another SM made the replay meanwhile."""
    req = R._request(world.run, world.instance)
    folder, _env, context, _key = req
    from quam_state_manager.core.interactive_plots import replot_cache as cache
    from quam_state_manager.core import lab_worker
    disk = cache.DiskCache(world.instance)
    real = lab_worker.machine_lock.acquire

    def acquire(blocking=True):
        # what the other process wrote while this one waited for the lock
        if not disk.load(context):
            disk.store(context, dict(world.payload, module_files=[], module_trees=[]))
        return real(blocking)
    monkeypatch.setattr(lab_worker.machine_lock, "acquire", acquire)
    out = R.replot_run(world.run, world.instance)
    assert out["figures"] and world.calls == []


# ---------------------------------------------------------------------------
# D-2: the worker streams phases on marked lines, apart from its answers
# ---------------------------------------------------------------------------

def test_serve_writes_progress_lines_apart_from_the_answer(monkeypatch):
    import io, json, sys
    from quam_state_manager.generator import run_interactive_replot as runner

    def replay(args):
        runner._progress(args, "importing lab code")
        runner._progress(args, "drawing figure", 1, 2, "a")
        runner._progress(args, "drawing figure", 2, 2, "b")
        return {"figures": ["x"], "errors": []}
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"run":"r"}\n'))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(runner, "replay", replay)
    runner.serve()
    lines = out.getvalue().splitlines()
    prog = [json.loads(l[len(runner.PROGRESS_MARK):]) for l in lines if l.startswith(runner.PROGRESS_MARK)]
    answers = [l for l in lines if l.startswith(runner.SERVE_MARK)]
    assert [p["phase"] for p in prog] == ["importing lab code", "drawing figure", "drawing figure", "converting"]
    assert prog[2] == {"phase": "drawing figure", "i": 2, "n": 2, "key": "b"}
    assert len(answers) == 1 and json.loads(answers[0][len(runner.SERVE_MARK):])["figures"] == ["x"]
    assert not runner.SERVE_MARK.startswith(runner.PROGRESS_MARK)
    assert not runner.PROGRESS_MARK.startswith(runner.SERVE_MARK)


def test_the_plan_count_is_the_figure_denominator():
    import inspect
    from quam_state_manager.generator import run_interactive_replot as runner
    src = inspect.getsource(runner._execute)
    assert '_progress(args, "drawing figure", number, len(invocations), key)' in src
    assert src.index('"importing lab code"') < src.index('"loading run state"') < src.index('"opening datasets"')


def test_the_worker_routes_progress_to_its_listener_never_the_answer(tmp_path):
    import sys
    from quam_state_manager.core import lab_worker
    script = tmp_path / "w.py"
    script.write_text(
        "import sys,json\n"
        "for line in sys.stdin:\n"
        " print('@P'+json.dumps({'phase':'drawing figure','i':1,'n':3,'key':'k'}),flush=True)\n"
        " print('@P'+'not json',flush=True)\n"
        " print('@A'+json.dumps({'status':'ok','sources':{}}),flush=True)\n")
    heard = []
    with lab_worker.machine_lock:
        w = lab_worker.Worker(sys.executable, str(script), "@A", progress_marker="@P")
        try:
            w.on_progress = heard.append
            assert w.ask({}, 10) == {"status": "ok", "sources": {}}
            w.on_progress = None
            assert w.ask({}, 10)["status"] == "ok"      # no listener: progress is dropped
        finally:
            w.kill()
    assert heard == [{"phase": "drawing figure", "i": 1, "n": 3, "key": "k"}]


def test_a_warm_replay_reports_worker_phases_to_the_job(tmp_path, monkeypatch):
    from quam_state_manager.core import lab_worker
    heard = []

    class FakeWorker:
        context, python_path = "", None
        def __init__(self, env, script, marker, **kw):
            assert kw.get("progress_marker") == "@@SM-IPLOT-PROG@@"
            self.python_path = env; self.proc = SimpleNamespace(pid=0)
        def fresh(self): return True
        def kill(self): pass
        def ask(self, spec, timeout):
            self.on_progress({"phase": "drawing figure", "i": 1, "n": 2, "key": "a"})
            return {"figures": [], "module_files": [], "module_trees": []}
    env = tmp_path / "python.exe"; env.write_text("")
    monkeypatch.setattr(lab_worker, "Worker", FakeWorker)
    monkeypatch.setattr(lab_worker, "retire_except", lambda *a: None)
    monkeypatch.setattr(R, "_PLOT_WORKER", None)
    R._warm_request(str(env), tmp_path, None, 10, lambda *a: heard.append(a))
    assert ("drawing figure", 1, 2, "a") in heard


# ---------------------------------------------------------------------------
# D-4: fixed costs
# ---------------------------------------------------------------------------

def test_the_sha_memo_never_changes_the_fingerprint(tmp_path, monkeypatch):
    import os
    from quam_state_manager.generator import iplot_sources as S
    tree = tmp_path / "pkg"; tree.mkdir()
    files = []
    for i in range(5):
        f = tree / f"m{i}.py"; f.write_text(f"v = {i}"); files.append(f)
    S._SHA_MEMO.clear()
    cold = S.fingerprint([files[0]], [tree])
    reads = []
    real = S.Path.read_bytes
    monkeypatch.setattr(S.Path, "read_bytes", lambda self: reads.append(self) or real(self))
    warm = S.fingerprint([files[0]], [tree])
    assert warm == cold and reads == [], "unchanged stats are not read again"
    S._SHA_MEMO.clear()
    assert S.fingerprint([files[0]], [tree]) == cold, "with and without the memo"
    st = files[2].stat()
    files[2].write_text("v = 9")                       # same size, new bytes
    os.utime(files[2], ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    reads.clear()
    edited = S.fingerprint([files[0]], [tree])
    assert edited != cold and [p.name for p in reads] == ["m2.py"]
    S._SHA_MEMO.clear()
    assert S.fingerprint([files[0]], [tree]) == edited


def test_an_import_failure_is_cached_until_retry(tmp_path, monkeypatch):
    source = tmp_path / "imported.py"; source.write_text("x = 1")
    run = SimpleNamespace(folder_path=str(tmp_path / "run"), experiment_name="05_x")
    calls = []

    def failing(*a):
        calls.append(1)
        return {"figures": [], "errors": [{"stage": "import", "trace": "ModuleNotFoundError: No module named 'lab'"}],
                "module_files": [{"path": str(source)}], "module_trees": [],
                "code_fingerprint": fingerprint([source])}
    monkeypatch.setattr("quam_state_manager.core.config_generator.get_selected_env", lambda _: "env")
    monkeypatch.setattr(R, "_invoke", failing)
    with R._LOCK:
        R._CACHE.clear(); R._INFLIGHT.clear(); R._HELD.clear()
    inst = tmp_path / "instance"
    first = R.replot_run(run, inst)
    assert R.replot_outcome(first)["state"] == "failed"
    n = len(calls)
    with R._LOCK:
        R._CACHE.clear()
    assert R.peek(run, inst) is not None and R.replot_run(run, inst)["errors"][0]["stage"] == "import"
    assert len(calls) == n, "the second open answers from the cache"
    R.replot_run(run, inst, force=True)
    assert len(calls) > n, "Retry runs it again"
    source.write_text("x = 22")                        # the watched code changed
    with R._LOCK:
        R._CACHE.clear()
    assert R.peek(run, inst) is None
