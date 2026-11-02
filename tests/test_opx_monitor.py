"""OPX monitor: server side with a fake worker (no QM SDK, no network).

Covers fake answers, refused calls, a worker that dies or times out, stale
data, history dedup, the close confirm path and what the PAGE says in each
case (fault injection reads the rendered sentence, not internal state)."""
from __future__ import annotations

import json

import pytest

from quam_state_manager.core import opx_monitor as om
from quam_state_manager.web.app import create_app

ORIGIN = {"Origin": "http://localhost"}


def answer(qms=(("QM-a", [("j1", "Running")], ["con1/1"]),), temps=None, refused=None,
           controllers=True, jobs=True):
    open_qms = [{"id": q, "jobs": [{"id": j, "status": s, "created_at": None} for j, s in js],
                 "ports": ports} for q, js, ports in qms]
    return {"kind": "qmm", "version": {"server": "3.8.1"}, "cluster_name": "c",
            "controllers": ([{"name": "con1", "type": "OPX1000", "fems": {"1": "LF", "2": "MW"},
                              "temperatures": temps, "metrics": None}] if controllers else None),
            "open_qms": open_qms, "jobs": ([j for q in open_qms for j in q["jobs"]] if jobs else None),
            "refused": refused or {}}


class FakeWorker:
    def __init__(self, script):
        self.script = script  # list of dicts or exceptions, consumed per request
        self.calls = []
        self.dead = False

    def alive(self):
        return not self.dead

    def request(self, op, timeout=None, **kw):
        self.calls.append((op, kw))
        if op == "poll":
            item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
            if isinstance(item, Exception):
                raise item
            return {"ok": True, "result": item}
        if op in ("close_qm", "close_all"):
            return {"ok": True, "result": True}
        return {"ok": False, "error": "unknown"}

    def kill(self):
        self.dead = True


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    clock = Clock()
    worker = FakeWorker([answer()])
    started = []

    def factory(py, target):
        started.append(target)
        if isinstance(worker.script[0], om.WorkerError) and worker.script[0].args[0].startswith("START"):
            raise worker.script.pop(0)
        return worker

    m = om.Monitor(app.instance_path, worker_factory=factory, clock=clock)
    m._start_thread = lambda: None  # tests poll by hand
    app.config["opx_monitor"] = m
    om.save_settings(app.instance_path, {"target": {"kind": "qmm", "host": "h", "port": 80},
                                         "python": "py", "interval_s": 5, "thresholds": {},
                                         "alarms_armed": False})
    return app, m, worker, clock, started


def page(app, tab):
    return app.test_client().get(f"/opx-monitor/body?tab={tab}").get_data(as_text=True)


def test_live_page_shows_fems_and_occupancy(env):
    app, m, w, clock, _ = env
    m.poll_once()
    html = page(app, "live")
    assert "Updated 0 s ago" in html
    assert "opxmon-fem--busy" in html and "1 LF" in html and "running" in html
    assert "No temperature readings." in html  # none returned -> said, never a field


def test_temperature_and_alarm_only_for_returned_metrics(env):
    app, m, w, clock, _ = env
    w.script[:] = [answer(temps={"FEM 1": 61.0, "Chassis CPU": 40.0})]
    m.poll_once()
    offered = [x["key"] for x in om.offered_metrics(m.last_answer)]
    assert offered == ["temp:Chassis CPU", "temp:FEM 1"]
    s = om.load_settings(app.instance_path)
    s.update(thresholds={"temp:FEM 1": 55, "temp:made-up": 1}, alarms_armed=True)
    om.save_settings(app.instance_path, s)
    m.poll_once()
    assert [a["key"] for a in m.alarms] == ["temp:FEM 1"]
    c = app.test_client()
    r = c.get("/opx-monitor/alarm-badge")
    assert "OPX alarm (1)" in r.get_data(as_text=True)
    toast = json.loads(r.headers["HX-Trigger"])["showToast"]["message"]
    assert "FEM 1" in toast and "61.0" in toast
    assert "HX-Trigger" not in c.get("/opx-monitor/alarm-badge").headers  # one toast per alarm


def test_refused_list_open_qms_says_so_and_disables_close(env):
    app, m, w, clock, _ = env
    a = answer(refused={"list_open_qms": "PermissionError: not allowed"})
    a["open_qms"] = None
    w.script[:] = [a]
    m.poll_once()
    assert "The server refused list_open_qms: PermissionError: not allowed" in page(app, "jobs")
    html = page(app, "cleanup")
    assert "Closing is not available." in html and "opxmon-close\"" not in html
    r = app.test_client().post("/opx-monitor/close", data={"all": "1"}, headers=ORIGIN)
    assert r.status_code == 409 and not any(c[0] == "close_all" for c in w.calls)


def test_worker_death_is_a_sentence_never_green(env):
    app, m, w, clock, _ = env
    m.poll_once()
    clock.t += 7
    w.script[:] = [om.WorkerError("The monitor worker exited while running 'poll' (exit code 1).")]
    m.poll_once()
    html = page(app, "live")
    assert "opxmon-status--error" in html
    assert "exited while running" in html and "from 7 s ago" in html
    assert "Updated" not in html


def test_unreachable_server_first_poll(env):
    app, m, w, clock, _ = env
    w.script[:] = [om.WorkerError("START The QM SDK could not connect: ConnectionError: refused")]
    m.poll_once()
    html = page(app, "live")
    assert "could not connect" in html and "opxmon-status--error" in html
    assert "opxmon-fem" not in html


def test_stale_answer_is_labelled_with_its_age(env):
    app, m, w, clock, _ = env
    m.poll_once()
    clock.t += 40  # > 3 intervals with no new answer and no error
    html = page(app, "live")
    assert "No fresh answer for 40 s" in html and "opxmon-status--stale" in html


def test_history_dedup_and_gone(env):
    app, m, w, clock, _ = env
    for _ in range(3):
        m.poll_once()
        clock.t += 5
    h = m.history()
    lines = h.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2  # qm_seen + job_seen once, not per poll
    w.script[:] = [answer(qms=(("QM-a", [("j1", "Done")], ["con1/1"]),))]
    m.poll_once()
    clock.t += 5
    w.script[:] = [answer(qms=())]
    m.poll_once()
    evs = [json.loads(x)["ev"] for x in h.path.read_text(encoding="utf-8").splitlines()]
    assert evs == ["qm_seen", "job_seen", "job_status", "qm_gone", "job_gone"]
    rows = {r["job_id"]: r for r in h.table()}
    assert rows["j1"]["status"] == "Done" and rows["j1"]["open"] is False
    # a restart rebuilds the same rows from the file
    h2 = om.History(h.path)
    assert {r["job_id"]: (r["status"], r["open"]) for r in h2.table()} == {None: (None, False), "j1": ("Done", False)}
    assert "gone" in page(app, "history")


def test_history_survives_a_torn_line(env, tmp_path):
    p = tmp_path / "h.jsonl"
    p.write_text('{"ev":"qm_seen","qm_id":"Q","t":"x"}\n{"ev":"job_se', encoding="utf-8")
    assert [r["qm_id"] for r in om.History(p).table()] == ["Q"]


def test_close_one_logs_and_the_confirm_names_it(env):
    app, m, w, clock, _ = env
    m.poll_once()
    html = page(app, "cleanup")
    assert 'data-qm-id="QM-a"' in html and 'data-ports="con1/1"' in html and 'data-jobs="Running"' in html
    c = app.test_client()
    assert c.post("/opx-monitor/close", data={}, headers=ORIGIN).status_code == 400
    r = c.post("/opx-monitor/close", data={"qm_id": "QM-a", "by": "tester"}, headers=ORIGIN)
    assert r.status_code == 200 and r.get_json()["closed"] == ["QM-a"]
    assert ("close_qm", {"qm_id": "QM-a"}) in w.calls
    acts = m.history().recent_actions()
    assert acts[0]["action"] == "close_qm" and acts[0]["by"] == "tester" and acts[0]["ok"]


def test_cloud_target_disables_jobs_and_cleanup(env):
    app, m, w, clock, _ = env
    s = om.load_settings(app.instance_path)
    s["target"] = {"kind": "cloud", "backend": "b"}
    om.save_settings(app.instance_path, s)
    w.script[:] = [{"kind": "cloud", "online": True, "controllers": None, "open_qms": None,
                    "jobs": None, "refused": {}}]
    m.poll_once()
    for tab in ("jobs", "cleanup", "live"):
        assert "exposes no quantum-machine manager" in page(app, tab)
    assert "Backend status reported by the cloud service: <strong>online" in page(app, "live")


def test_settings_secret_headers_never_rendered_or_in_settings(env):
    app, m, *_ = env
    c = app.test_client()
    r = c.post("/opx-monitor/settings", data={"kind": "qmm", "host": "h2", "port": "9510", "tls": "1",
                                              "headers": '{"auth": "SECRET-123"}', "interval_s": "1"},
               headers=ORIGIN)
    assert r.status_code == 200
    s = om.load_settings(app.instance_path)
    assert s["interval_s"] == om.MIN_INTERVAL_S  # clamped
    assert "SECRET-123" not in json.dumps(s)
    assert "SECRET-123" not in c.get("/opx-monitor/settings").get_data(as_text=True)
    assert om.load_secret_headers(app.instance_path, om.target_key(s["target"])) == {"auth": "SECRET-123"}
    bad = c.post("/opx-monitor/settings", data={"kind": "qmm", "host": "h", "headers": "[1]"}, headers=ORIGIN)
    assert bad.status_code == 400 and "JSON object" in bad.get_data(as_text=True)


def test_threshold_for_unreported_metric_is_kept_and_nan_refused(env):
    app, m, *_ = env
    s = om.load_settings(app.instance_path)
    s["thresholds"] = {"temp:FEM 9": 70.0}
    om.save_settings(app.instance_path, s)
    c = app.test_client()
    c.post("/opx-monitor/settings", data={"kind": "qmm", "host": "h", "port": "80", "python": "",
                                          "interval_s": "5"}, headers=ORIGIN)
    assert om.load_settings(app.instance_path)["thresholds"] == {"temp:FEM 9": 70.0}
    r = c.post("/opx-monitor/settings", data={"kind": "qmm", "host": "h", "th__temp:FEM 9": "nan"}, headers=ORIGIN)
    assert r.status_code == 400


def test_polling_only_while_wanted_or_armed(env):
    app, m, w, clock, _ = env
    assert not m.should_poll()
    m.want()
    assert m.should_poll()
    clock.t += 16
    assert not m.should_poll()
    s = om.load_settings(app.instance_path)
    s["alarms_armed"] = True
    om.save_settings(app.instance_path, s)
    assert m.should_poll()


def test_no_settings_is_a_sentence(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "i2"))
    m = om.Monitor(app.instance_path, worker_factory=lambda *a: pytest.fail("spawned"))
    m._start_thread = lambda: None
    app.config["opx_monitor"] = m
    html = page(app, "live")
    assert "No OPX connection is set up" in html and 'hx-trigger="every' not in html


def test_sidebar_has_the_menu(env):
    app, *_ = env
    html = app.test_client().get("/opx-monitor/jobs").get_data(as_text=True)
    assert 'id="opxmon-subnav"' in html and ">Monitor settings</a>" in html
    assert 'data-bundle-file="opx-monitor.js"' in html


def test_real_worker_reports_a_bad_target(tmp_path):
    """The real worker script with this interpreter: no qm installed here or a
    dead host -- either way it must answer with a sentence, not hang."""
    import sys
    try:
        w = om.EnvWorker(sys.executable, {"kind": "qmm", "host": "127.0.0.1", "port": 9}, start_timeout=60)
    except om.WorkerError as exc:
        assert "could not connect" in str(exc) or "exited" in str(exc)
    else:  # an env with qm: the connect is lazy, a poll reports refusals
        r = w.request("poll", timeout=60)
        w.kill()
        assert r.get("ok") is False or r["result"]["refused"]


def test_page_js_selfcheck():
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([node, str(root / "tests" / "opx_monitor_selfcheck.cjs")], capture_output=True,
                       text=True, encoding="utf-8", timeout=120, cwd=str(root))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok - ") >= 12, r.stdout
