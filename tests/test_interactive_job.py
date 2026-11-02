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
