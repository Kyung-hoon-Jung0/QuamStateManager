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
        if op == "close_qm":
            return {"ok": True, "result": {"closed": [kw["qm_id"]], "failed": {}}}
        if op == "close_qms":
            open_now = {q["id"] for q in (self.script[0].get("open_qms") or [])}
            ids = list(kw.get("qm_ids") or [])
            return {"ok": True, "result": {"closed": [i for i in ids if i in open_now],
                                           "skipped": [i for i in ids if i not in open_now],
                                           "failed": {}}}
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
    assert "FEM 1 temperature" in toast and "61.0" in toast  # mismatched population: raw key
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
    r = app.test_client().post("/opx-monitor/close", data={"all": "1", "qm_ids": "QM-a"}, headers=ORIGIN)
    assert r.status_code == 409 and not any(c[0].startswith("close") for c in w.calls)


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


def test_a_target_that_is_not_a_qm_server_is_one_sentence(env):
    app, m, w, clock, started = env
    s = om.load_settings(app.instance_path)
    s["target"] = {"kind": "other", "backend": "b"}
    om.save_settings(app.instance_path, s)
    m.poll_once()
    assert not started  # no worker is spawned for it
    for tab in ("jobs", "cleanup", "live"):
        assert "This target is not a QM server." in page(app, tab)


def test_no_cloud_client_path_in_the_feature():
    """The worker never imports a third-party cloud client (removed by decision)."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "quam_state_manager"
    for f in ("generator/opx_monitor_worker.py", "core/opx_monitor.py", "web/opx_monitor_routes.py",
              "web/templates/_opx_monitor_body.html"):
        text = (root / f).read_text(encoding="utf-8").lower()
        assert "cloud_client" not in text and "cloudtarget" not in text and 'name="backend"' not in text


# ---- review round: closing, worker lifetime, stale alarms, history bound

def test_close_all_closes_only_the_confirmed_qms(env):
    """A QM opened after the page rendered is not in the confirm list and stays open."""
    app, m, w, clock, _ = env
    m.poll_once()
    assert 'data-qm-ids="QM-a"' in page(app, "cleanup")
    w.script[:] = [answer(qms=(("QM-a", [], ["con1/1"]), ("QM-new", [], ["con1/2"])))]
    c = app.test_client()
    assert c.post("/opx-monitor/close", data={"all": "1"}, headers=ORIGIN).status_code == 400
    r = c.post("/opx-monitor/close", data={"all": "1", "qm_ids": "QM-a"}, headers=ORIGIN)
    assert r.status_code == 200 and r.get_json()["closed"] == ["QM-a"]
    sent = [kw for op, kw in w.calls if op.startswith("close")]
    assert sent == [{"qm_ids": ["QM-a"]}] and not any(op == "close_all" for op, _ in w.calls)


def test_close_all_reports_a_qm_already_gone(env):
    app, m, w, clock, _ = env
    m.poll_once()
    w.script[:] = [answer(qms=())]
    r = app.test_client().post("/opx-monitor/close", data={"all": "1", "qm_ids": "QM-a"}, headers=ORIGIN)
    j = r.get_json()
    assert j["closed"] == [] and j["skipped"] == ["QM-a"]


class _FakeQM:
    def __init__(self, log, qid):
        self.log, self.qid = log, qid

    def close(self):
        self.log.append(self.qid)
        return True


class _FakeQmm:
    def __init__(self, open_ids, temps=None):
        self.open_ids, self.closed, self.temps = list(open_ids), [], temps

    def list_open_qms(self):
        return list(self.open_ids)

    def get_qm(self, qid):
        return _FakeQM(self.closed, qid)

    def close_all_qms(self):
        self.closed.extend(self.open_ids)


def _worker_target(qmm):
    from quam_state_manager.generator import opx_monitor_worker as wk
    t = wk.QmmTarget.__new__(wk.QmmTarget)
    t.qmm = qmm
    return t


def test_worker_never_closes_an_unnamed_or_stale_qm():
    qmm = _FakeQmm(["QM-a", "QM-new"])
    t = _worker_target(qmm)
    res = t.close_qms(["QM-a", "QM-old"])
    assert qmm.closed == ["QM-a"] and res["skipped"] == ["QM-old"]
    with pytest.raises(RuntimeError, match="no longer open"):
        t.close_qm("QM-old")
    assert qmm.closed == ["QM-a"]


def test_worker_survives_a_temperature_of_the_wrong_type():
    from types import SimpleNamespace
    ctrl = SimpleNamespace(controller_type="OPX1000", fems={1: "LF"},
                           temperatures={"FEM 1": "n/a", "FEM 2": 41, "FEM 3": None, "flag": True},
                           metrics=SimpleNamespace(cpu_utilization_percent="12.5", qop_cpu_percent=[1]))
    qmm = _FakeQmm([])
    qmm.version_dict = lambda: {"server": "x"}
    qmm.cluster_name = "c"
    qmm.get_devices = lambda: SimpleNamespace(controllers={"con1": ctrl})
    qmm.get_jobs = lambda: []
    ans = _worker_target(qmm).poll()
    c = ans["controllers"][0]
    assert c["temperatures"] == {"FEM 2": 41.0}
    assert c["metrics"] == {"cpu_utilization_percent": 12.5}
    assert "con1.temperatures.FEM 1" in ans["refused"]
    assert ans["open_qms"] == []


def test_a_worker_that_fails_oddly_is_killed_and_restarts_back_off(env):
    app, m, w, clock, started = env
    w.script[:] = [ValueError("Expecting value: line 1 column 1")]
    m.poll_once()
    assert w.dead and m._worker is None and "ValueError" in m.last_error
    n = len(started)
    w.dead = False
    m.poll_once()  # same instant: backing off, no new env process
    assert len(started) == n
    clock.t += om.RESTART_BACKOFF_S + 0.1
    w.script[:] = [answer()]
    m.poll_once()
    assert len(started) == n + 1 and m.last_error is None


def test_an_alarm_never_fires_on_stale_data(env):
    app, m, w, clock, _ = env
    s = om.load_settings(app.instance_path)
    s.update(thresholds={"temp:FEM 1": 55}, alarms_armed=True)
    om.save_settings(app.instance_path, s)
    w.script[:] = [answer(temps={"FEM 1": 61.0})]
    m.poll_once()
    clock.t += 60  # the answer is now stale
    c = app.test_client()
    r = c.get("/opx-monitor/alarm-badge")
    assert "HX-Trigger" not in r.headers and "OPX alarm (" not in r.get_data(as_text=True)
    assert "role=\"alert\"" not in page(app, "live")


def test_history_file_is_bounded_and_keeps_its_rows(env, monkeypatch):
    app, m, w, clock, _ = env
    monkeypatch.setattr(om, "HISTORY_MAX_BYTES", 4000)
    for i in range(200):
        w.script[:] = [answer(qms=(("QM-a", [("j1", "Running" if i % 2 else "Paused")], ["con1/1"]),))]
        m.poll_once()
        clock.t += 5
    h = m.history()
    assert h.path.stat().st_size <= 4000 + 2000
    want = {r["job_id"]: (r["status"], r["open"]) for r in h.table()}
    assert {r["job_id"]: (r["status"], r["open"]) for r in om.History(h.path).table()} == want


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
    assert r.stdout.count("ok - ") >= 13, r.stdout


def test_post_routes_need_our_origin(env):
    app, m, w, clock, _ = env
    m.poll_once()
    app.config["TESTING"] = False  # the guard is bypassed under TESTING
    c = app.test_client()
    for url, data in (("/opx-monitor/close", {"qm_id": "QM-a"}), ("/opx-monitor/settings", {"host": "x"})):
        assert c.post(url, data=data).status_code == 403
        assert c.post(url, data=data, headers={"Origin": "http://evil.example"}).status_code == 403
    assert not any(op.startswith("close") for op, _ in w.calls)


# the shape one OPX1000 returned (QOP 3.7.3): sensors FEM 0..7 for slots 1..8,
# 0.0 for an empty slot or an absent supply
MEASURED_TEMPS = {"FEM 0": 67.06, "FEM 1": 65.08, "FEM 2": 68.55, "FEM 3": 0.0, "FEM 4": 55.17,
                  "FEM 5": 52.69, "FEM 6": 60.12, "FEM 7": 0.0, "Chassis": 45.5, "Chassis CPU": 65.85,
                  "CRPS 0": 37.0, "CRPS 1": 0.0, "CRPS 2": 0.0}
MEASURED_FEMS = {"1": "MW", "2": "MW", "3": "MW", "5": "LF", "6": "LF", "7": "LF"}


def test_fem_sensor_k_is_slot_k_plus_1_and_zero_is_no_sensor(env):
    app, m, w, clock, _ = env
    a = answer(temps=MEASURED_TEMPS)
    a["controllers"][0]["fems"] = MEASURED_FEMS
    w.script[:] = [a]
    m.poll_once()
    rows = {r["key"]: r for r in om.temperature_rows(a["controllers"][0])}
    assert rows["FEM 0"]["slot"] == 1 and rows["FEM 4"]["slot"] == 5 and rows["Chassis"]["slot"] is None
    assert rows["FEM 3"]["value"] is None and rows["CRPS 1"]["value"] is None
    html = page(app, "live")
    # slot 5 (LF) carries sensor FEM 4's reading, its raw key in the tooltip
    assert "5 LF" in html and "title=\"sensor 'FEM 4'\">55.2 degC" in html
    assert ">FEM slot 4</td>" in html and "no sensor" in html and "0.0</td>" not in html
    offered = [x["key"] for x in om.offered_metrics(a)]
    assert "temp:FEM 3" not in offered and "temp:CRPS 1" not in offered and "temp:FEM 4" in offered



def test_d13_idle_recheck_skips_new_activity(env):
    from types import SimpleNamespace
    app, m, w, clock, _ = env
    w.script[:] = [answer(qms=(("QM-a", [], []),))]
    m.poll_once()
    clock.t += 1900
    m.poll_once()
    assert 'Close idle QMs (1)' in page(app, 'cleanup')
    assert 'since SM first saw it' in page(app, 'jobs')
    qmm = _FakeQmm(['QM-a'])
    qm = _FakeQM(qmm.closed, 'QM-a')
    qm.get_jobs = lambda: [SimpleNamespace(id='job-new', status='Running')]
    qmm.get_qm = lambda qid: qm
    result = _worker_target(qmm).close_idle(['QM-a'], {'QM-a': '2020-01-01T00:00:00Z'}, 30)
    assert result['closed'] == [] and result['skipped'] == ['QM-a']
    assert 'active' in result['skip_reasons']['QM-a'] and not qmm.closed
    qm.get_jobs = lambda: []
    assert _worker_target(qmm).close_idle(['QM-a'], {'QM-a': '2020-01-01T00:00:00Z'}, 30)['closed'] == ['QM-a']
    assert not om.idle_state({'jobs': None}, '2020-01-01T00:00:00Z', clock.t)['idle']


def test_d14_admin_health_and_unknown(env, monkeypatch):
    from pathlib import Path
    import io
    root = Path(__file__).parent / 'fixtures' / 'opx_monitor'
    calls = []
    class Opener:
        def open(self, req, timeout):
            calls.append((req.full_url, req.method, timeout))
            name = 'v2_clusters.json' if req.full_url.endswith('/clusters') else 'v2_devices_detailed.json'
            return io.BytesIO((root / name).read_bytes())
    monkeypatch.setattr(om.urllib.request, 'build_opener', lambda *a: Opener())
    admin = om.read_admin('http://sample.invalid')
    assert admin['known']
    assert [u.rsplit('/api', 1)[1] for u, method, timeout in calls] == ['/v2/clusters', '/v2/devices/detailed']
    assert all(method == 'GET' and timeout == 2 for u, method, timeout in calls)
    a = answer(qms=(), temps={'Chassis': 40})
    a['controllers'][0]['fems'] = {}
    assert om.health(a, admin, {'temp:Chassis': 50}, 'ok')['color'] == 'green'
    assert om.health(a, admin, {'temp:Chassis': 50}, 'stale')['color'] == 'grey'
    assert om.health(a, om.read_admin(None), {'temp:Chassis': 50}, 'ok')['color'] == 'grey'
    assert om.health(a, admin, {}, 'ok')['color'] == 'grey'
    assert om.health(a, admin, {'temp:Chassis': 50, 'temp:missing': 70}, 'ok')['color'] == 'grey'
    a['controllers'][0]['fems'] = {'1': 'LF'}
    assert om.health(a, admin, {'temp:Chassis': 50}, 'ok')['color'] == 'grey'
    a['controllers'][0]['fems'] = {}
    admin['cluster']['hasUnreachableDevices'] = True
    assert om.health(a, admin, {'temp:Chassis': 50}, 'ok')['color'] == 'red'
    app, m, w, clock, _ = env
    m.poll_once()
    assert 'Set Admin URL' in page(app, 'live')
    p = om._dir(app.instance_path) / 'settings.json'
    p.write_text('{broken')
    assert 'corrupt' in page(app, 'settings')
    assert p.with_suffix('.json.corrupt').read_text() == '{broken'


def test_d15_trends_gaps_zero_bounds_and_span(tmp_path, env):
    store = om.TemperatureTrends(tmp_path / 'trend.jsonl')
    store.append(answer(temps={'FEM 0': 40, 'CRPS 1': 0.0}), 100, 5)
    store.append(answer(temps={'FEM 0': 45}), 105, 5)
    store.append(answer(temps={'FEM 0': 50}), 200, 5)
    trend = store.series(200)['con1/FEM 0']
    assert len(trend['paths']) == 2
    assert trend['min'] == 40 and trend['max'] == 50 and trend['span_s'] == 100
    assert 'con1/CRPS 1' not in store.series(200)
    assert om.TemperatureTrends(store.path).series(200) == store.series(200)
    store.append(answer(temps={'FEM 0': 55}), 90000, 5)
    assert store.series(90000)['con1/FEM 0']['samples'] == 1
    app, m, w, clock, _ = env
    w.script[:] = [answer(temps={'Chassis': 42})]
    m.poll_once()
    assert 'actual span 0 s' in page(app, 'live') and '<polyline' in page(app, 'live')


def test_d16_unique_run_only_and_open_chip_scope(env, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from quam_state_manager.web import opx_monitor_routes as routes
    from quam_state_manager.web import routes as main
    app, m, w, clock, _ = env
    created = '2026-01-01T00:00:00Z'
    a = answer()
    a['open_qms'][0]['jobs'][0]['created_at'] = created
    w.script[:] = [a]
    m.poll_once()
    assert 'No chip is open' in page(app, 'jobs') and 'probably #' not in page(app, 'jobs')
    root = tmp_path / 'sample-data'
    root.mkdir()
    run = SimpleNamespace(created_at=created, run_id=7, folder_path=root / 'run7')
    store = SimpleNamespace(scan_truncated=False, runs={7: run})
    monkeypatch.setattr(main, '_active_ctx', lambda: {'type': 'quam'})
    monkeypatch.setattr(main, '_hub_roots_for', lambda ctx: [(str(root), 'declared')])
    used = []
    monkeypatch.setattr(main, '_get_or_create_store', lambda p, **kw: used.append(p) or store)
    html = page(app, 'jobs')
    assert 'probably #7' in html and 'estimated from times' in html and used == [root]
    store.runs[8] = SimpleNamespace(created_at=created, run_id=8, folder_path=root / 'run8')
    html = page(app, 'jobs')
    assert '2 runs at that time' in html and 'probably #' not in html
    store.scan_truncated = True
    assert 'incomplete' in page(app, 'jobs') and 'probably #' not in page(app, 'jobs')
    assert 'url' not in routes.run_match({'created_at': created}, [])


def test_d17_alarm_raise_clear_missing_and_restart(env):
    app, m, w, clock, _ = env
    s = m.settings()
    s.update(alarms_armed=True, thresholds={'temp:Chassis': 50})
    om.save_settings(app.instance_path, s)
    w.script[:] = [answer(temps={'Chassis': 60})]
    m.poll_once()
    m.poll_once()
    w.script[:] = [answer(temps={'Chassis': 0.0})]
    m.poll_once()
    assert [a['ev'] for a in m.history().recent_actions()] == ['alarm_raise']
    m._histories.clear()
    w.script[:] = [answer(temps={'Chassis': 40})]
    m.poll_once()
    events = m.history().recent_actions()
    assert [a['ev'] for a in events] == ['alarm_clear', 'alarm_raise']
    assert events[0]['value'] == 40 and events[0]['limit'] == 50 and events[0]['t']
    assert 'alarm_clear' in page(app, 'history') and 'threshold 50' in page(app, 'history')


def test_d18_mapping_mismatch_keeps_raw_keys(env):
    app, m, w, clock, _ = env
    a = answer(temps=MEASURED_TEMPS)
    a['controllers'][0]['fems'] = {'1': 'MW'}
    w.script[:] = [a]
    m.poll_once()
    rows = {r['key']: r for r in om.temperature_rows(a['controllers'][0])}
    assert rows['FEM 4']['label'] == 'FEM 4'
    assert not om.slot_temperatures(a['controllers'][0])
    html = page(app, 'live')
    assert 'mapping unverified' in html and '>FEM 4</td>' in html
    assert 'title="sensor &#39;FEM 4&#39;"' not in html  # raw tooltip remains literal, not an inferred slot


def test_round2_dom_selfcheck(env, tmp_path, monkeypatch):
    import shutil
    import subprocess
    from pathlib import Path
    from quam_state_manager.web import opx_monitor_routes as routes
    app, m, w, clock, _ = env
    pages = {}
    w.script[:] = [answer(qms=(("QM-a", [], []),), temps={'FEM 0': 40, 'CRPS 0': 0.0})]
    m.poll_once()
    pages['unknown'] = page(app, 'live')
    clock.t += 100
    m.poll_once()
    pages['trend'] = page(app, 'live')
    pages['mapping'] = pages['trend']
    clock.t += 1900
    pages['stale'] = page(app, 'live')
    m.poll_once()
    pages['idle'] = page(app, 'cleanup')
    a = answer()
    a['jobs'][0]['created_at'] = '2026-01-01T00:00:00Z'
    w.script[:] = [a]
    m.poll_once()
    candidates = [{'id': 7, 'url': '/dataset/fake:7', 'time': om.timestamp(a['jobs'][0]['created_at'])}]
    monkeypatch.setattr(routes, '_run_candidates', lambda: (candidates, ''))
    pages['unique'] = page(app, 'jobs')
    candidates.append({**candidates[0], 'id': 8, 'url': '/dataset/fake:8'})
    pages['ambiguous'] = page(app, 'jobs')
    s = m.settings()
    s.update(alarms_armed=True, thresholds={'temp:Chassis': 50})
    om.save_settings(m.instance, s)
    w.script[:] = [answer(temps={'Chassis': 60})]
    m.poll_once()
    pages['alarm'] = page(app, 'history')
    fixture = tmp_path / 'pages.json'
    fixture.write_text(json.dumps(pages), encoding='utf-8')
    node = shutil.which('node')
    if not node:
        pytest.fail('node is required for the round 2 DOM selfcheck')
    result = subprocess.run([node, str(Path(__file__).with_name('opx_monitor_selfcheck.cjs')), str(fixture)],
                            capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_d13_remembers_activity_after_job_disappears(env):
    from datetime import datetime, timezone
    app, m, w, clock, _ = env
    w.script[:] = [answer(qms=(("QM-a", [], []),))]
    m.poll_once()
    clock.t += 1900
    a = answer(qms=(("QM-a", [('j1', 'Done')], []),))
    recent = datetime.fromtimestamp(clock.t, timezone.utc).isoformat()
    a['open_qms'][0]['jobs'][0].update(created_at=recent, updated_at=recent)
    w.script[:] = [a]
    m.poll_once()
    m._histories.clear()
    w.script[:] = [answer(qms=(("QM-a", [], []),))]
    m.poll_once()
    assert 'Close idle QMs (0)' in page(app, 'cleanup')
    assert 'job created/last status update observed by SM' in page(app, 'cleanup')


def test_unknown_job_list_is_not_none_or_a_gone_event(env):
    app, m, w, clock, _ = env
    m.poll_once()
    a = answer()
    a['open_qms'][0]['jobs'] = None
    w.script[:] = [a]
    m.poll_once()
    assert next(r for r in m.history().table() if r['job_id'] == 'j1')['open']
    assert 'data-jobs="unknown"' in page(app, 'cleanup')
    assert 'active or unknown job status' in page(app, 'cleanup')
    assert 'Occupancy is unknown' in page(app, 'live')


def test_d21_all_refused_is_error(env):
    app, m, w, clock, _ = env
    m.poll_once()
    a = {'controllers': None, 'open_qms': None, 'jobs': None,
         'refused': {'get_devices': 'refused', 'list_open_qms': 'refused', 'get_jobs': 'refused'}}
    w.script[:] = [a]
    m.poll_once()
    assert m.status()['state'] == 'error'
    assert not m.capabilities()['close'] and not m.capabilities()['live']
    html = page(app, 'live')
    assert 'as of' in html and 'Updated 0' not in html


def test_d22_close_false_or_still_listed(env):
    from types import SimpleNamespace
    from quam_state_manager.generator.opx_monitor_worker import QmmTarget
    for result, remaining in [(False, []), (True, ['QM-fable'])]:
        t = QmmTarget.__new__(QmmTarget)
        t.qmm = SimpleNamespace(list_open_qms=lambda: remaining)
        outcome = t._verified_close('QM-fable', SimpleNamespace(close=lambda: result))
        assert outcome['closed'] == [] and 'QM-fable' in outcome['failed']
    app, m, w, clock, _ = env
    m.poll_once()
    original = w.request
    w.request = lambda op, **kw: {'ok': True, 'result': False} if op == 'close_qm' else original(op, **kw)
    assert not m.close('QM-a')['ok']
    assert m.history().recent_actions()[0]['qm_ids'] == []


def test_d23_verdict_first(env):
    app, m, w, clock, _ = env
    m.poll_once()
    html = page(app, 'live')
    assert html.index('opxmon-verdict') < html.index('opxmon-status')
    assert '<summary>Details</summary>' in html
    assert om.verdict({'open_qms': []}, {'color': 'green'}, {})['label'] == 'FREE'
    assert om.verdict(answer(), {'color': 'amber'}, {})['label'] == 'BUSY'
