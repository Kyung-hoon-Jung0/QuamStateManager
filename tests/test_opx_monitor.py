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


def _measured(m, clock, skew, unc=1.0):
    """A fresh admin Date-header measurement of the OPX clock offset."""
    s = m.settings(); s['admin_url'] = 'http://sample.invalid'; om.save_settings(m.instance, s)
    m.clock_measure = {'skew_s': skew, 'uncertainty_s': unc, 'at': clock.t, 'at_mono': m.mono()}


def _watch(m, clock, seconds, step=None):
    """SM watches continuously: one successful poll every interval for ``seconds``."""
    step = step or m.poll_interval()
    for _ in range(int(seconds // step) + 1):
        clock.t += step
        m.poll_once()


def _slow(m):
    """Long intervals so a two-hour continuous watch is a few dozen polls."""
    s = m.settings(); s['interval_s'] = 300; om.save_settings(m.instance, s)


def _w(sig_q, watched_s=99_999, continuous=True):
    return {'watched_s': watched_s, 'continuous': continuous, 'sig': om.job_signature(sig_q)}


def page(app, tab):
    return app.test_client().get(f"/opx-monitor/body?tab={tab}").get_data(as_text=True)


def test_live_page_shows_fems_and_occupancy(env):
    app, m, w, clock, _ = env
    m.poll_once()
    html = page(app, "live")
    assert "Updated 0 s ago" in html
    assert "ui-opxmon-fem--busy" in html and "1 LF" in html and "running" in html
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
    assert "FEM 1 temperature" in toast and "61 °C" in toast  # mismatched population: raw key
    assert "HX-Trigger" not in c.get("/opx-monitor/alarm-badge").headers  # one toast per alarm


def test_refused_list_open_qms_says_so_and_disables_close(env):
    app, m, w, clock, _ = env
    a = answer(refused={"list_open_qms": "PermissionError: not allowed"})
    a["open_qms"] = None
    w.script[:] = [a]
    m.poll_once()
    assert "The server refused list_open_qms: PermissionError: not allowed" in page(app, "jobs")
    html = page(app, "cleanup")
    assert "Closing is not available." in html and "ui-opxmon-close\"" not in html
    r = app.test_client().post("/opx-monitor/close", data={"all": "1", "qm_ids": "QM-a", "confirm_count": "1"}, headers=ORIGIN)
    assert r.status_code == 409 and not any(c[0].startswith("close") for c in w.calls)


def test_worker_death_is_a_sentence_never_green(env):
    app, m, w, clock, _ = env
    m.poll_once()
    clock.t += 7
    w.script[:] = [om.WorkerError("The monitor worker exited while running 'poll' (exit code 1).")]
    m.poll_once()
    html = page(app, "live")
    assert "ui-opxmon-status--error" in html
    assert "exited while running" in html and "last good read was 7 s ago" in html
    assert "Updated" not in html


def test_unreachable_server_first_poll(env):
    app, m, w, clock, _ = env
    w.script[:] = [om.WorkerError("START The QM SDK could not connect: ConnectionError: refused")]
    m.poll_once()
    html = page(app, "live")
    assert "could not connect" in html and "ui-opxmon-status--error" in html
    assert "ui-opxmon-fem" not in html


def test_stale_answer_is_labelled_with_its_age(env):
    app, m, w, clock, _ = env
    m.poll_once()
    clock.t += 40  # > 3 intervals with no new answer and no error
    m.wanted_until = clock.t + 15
    html = page(app, "live")
    assert "No fresh answer for 40 s" in html and "ui-opxmon-status--stale" in html


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
    r = c.post("/opx-monitor/close", data={"all": "1", "qm_ids": "QM-a", "confirm_count": "1"}, headers=ORIGIN)
    assert r.status_code == 200 and r.get_json()["closed"] == ["QM-a"]
    sent = [kw for op, kw in w.calls if op.startswith("close")]
    assert sent == [{"qm_ids": ["QM-a"]}] and not any(op == "close_all" for op, _ in w.calls)


def test_close_all_reports_a_qm_already_gone(env):
    app, m, w, clock, _ = env
    m.poll_once()
    w.script[:] = [answer(qms=())]
    r = app.test_client().post("/opx-monitor/close", data={"all": "1", "qm_ids": "QM-a", "confirm_count": "1"}, headers=ORIGIN)
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
        return [qid for qid in self.open_ids if qid not in self.closed]

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
    clock.t += 181  # alarm-only polling uses a 60 s interval
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
    assert 'id="ui-opxmon-subnav"' in html and ">Monitor settings</a>" in html
    assert 'data-bundle-file="opx-monitor.js"' in html


def test_real_worker_rejects_unsupported_target_without_sdk(tmp_path):
    """The real script rejects unsupported targets before importing the SDK."""
    import sys
    try:
        w = om.EnvWorker(sys.executable, {"kind": "unsupported", "host": "127.0.0.1", "port": 9}, start_timeout=60)
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
    assert "5 LF" in html and "title=\"sensor 'FEM 4'\">55.2 °C" in html
    assert 'empty slot' in html and '0.0</td>' not in html
    offered = [x["key"] for x in om.offered_metrics(a)]
    assert "temp:FEM 3" not in offered and "temp:CRPS 1" not in offered and "temp:FEM 4" in offered



def test_d13_idle_recheck_skips_new_activity(env):
    from types import SimpleNamespace
    app, m, w, clock, _ = env
    w.script[:] = [answer(qms=(("QM-a", [], []),))]
    _slow(m)
    m.poll_once()
    clock.t += 7300
    m.poll_once()
    assert 'Close idle QMs (0)' in page(app, 'cleanup')  # one 2 h gap is not a watch
    _watch(m, clock, 7300)
    assert 'Close idle QMs (1)' in page(app, 'cleanup')
    assert 'while SM watched continuously' in page(app, 'jobs')
    qmm = _FakeQmm(['QM-a'])
    qm = _FakeQM(qmm.closed, 'QM-a')
    qm.get_jobs = lambda: [SimpleNamespace(id='job-new', status='Running')]
    qmm.get_qm = lambda qid: qm
    watched = {'QM-a': _w({'jobs': []})}
    result = _worker_target(qmm).close_idle(['QM-a'], watched, 30)
    assert result['closed'] == [] and result['skipped'] == ['QM-a']
    assert 'Running' in result['skip_reasons']['QM-a'] and not qmm.closed
    qm.get_jobs = lambda: []
    assert _worker_target(qmm).close_idle(['QM-a'], watched, 30)['closed'] == ['QM-a']
    assert not om.idle_state({'jobs': None}, watched['QM-a'])['idle']


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
    assert om.health(a, om.read_admin(None), {'temp:Chassis': 50}, 'ok')['color'] == 'green'
    assert om.health(a, admin, {}, 'ok')['color'] == 'green'
    assert om.health(a, admin, {'temp:Chassis': 50, 'temp:missing': 70}, 'ok')['color'] == 'grey'
    a['controllers'][0]['fems'] = {'1': 'LF'}
    assert om.health(a, admin, {'temp:Chassis': 50}, 'ok')['color'] == 'green'
    a['controllers'][0]['fems'] = {}
    admin['cluster']['hasUnreachableDevices'] = True
    assert om.health(a, admin, {'temp:Chassis': 50}, 'ok')['color'] == 'red'
    app, m, w, clock, _ = env
    m.poll_once()
    assert 'Admin n/a' in page(app, 'live')
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
    from datetime import datetime, timezone
    created = datetime.fromtimestamp(clock.t, timezone.utc).isoformat()
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
    assert 'offset from this PC is unknown' in html and 'probably #' not in html  # no measurement yet
    _measured(m, clock, 0.0)
    html = page(app, 'jobs')
    assert 'probably #7' in html and 'estimated from times' in html and set(used) == {root}
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
    assert 'mapping unverified' in html and 'Sensor FEM 4' in html
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
    clock.t += 7300
    pages['stale'] = page(app, 'live')
    m.poll_once()
    s = m.settings(); s['idle_minutes'] = 1; om.save_settings(m.instance, s)
    _watch(m, clock, 70)
    pages['idle'] = page(app, 'cleanup')
    a = answer()
    from datetime import datetime, timezone
    # created as SM first sees it: the measured OPX clock offset is 0 s
    a['jobs'][0]['created_at'] = datetime.fromtimestamp(clock.t, timezone.utc).isoformat()
    w.script[:] = [a]
    m.poll_once()
    candidates = [{'id': 7, 'url': '/dataset/fake:7', 'time': om.timestamp(a['jobs'][0]['created_at'])}]
    monkeypatch.setattr(routes, '_run_candidates', lambda: (candidates, ''))
    _measured(m, clock, 0.0)
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
    clock.t += 7300
    a = answer(qms=(("QM-a", [('j1', 'Done')], []),))
    recent = datetime.fromtimestamp(clock.t, timezone.utc).isoformat()
    a['open_qms'][0]['jobs'][0].update(created_at=recent, updated_at=recent)
    w.script[:] = [a]
    m.poll_once()
    m._histories.clear()
    w.script[:] = [answer(qms=(("QM-a", [], []),))]
    m.poll_once()
    assert 'Close idle QMs (0)' in page(app, 'cleanup')
    assert 'no job change for 0 min' in page(app, 'cleanup')


def test_unknown_job_list_is_not_none_or_a_gone_event(env):
    app, m, w, clock, _ = env
    m.poll_once()
    a = answer()
    a['open_qms'][0]['jobs'] = None
    w.script[:] = [a]
    m.poll_once()
    assert next(r for r in m.history().table() if r['job_id'] == 'j1')['open']
    assert 'data-jobs="unknown"' in page(app, 'cleanup')
    assert 'Job status: unknown' in page(app, 'cleanup')
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
    assert 'last good read at' in html and 'Updated 0' not in html


def test_d21_transport_failure_labels_retained_sections(env):
    app, m, w, clock, _ = env
    m.poll_once()
    previous = dict(m.section_times)
    clock.t += 5
    w.script[:] = [om.WorkerError('Connection refused')]
    m.poll_once()
    assert m.section_times == previous
    assert set(m.section_errors) == {'controllers', 'open_qms', 'jobs'}
    html = page(app, 'live')
    assert 'Temperatures unavailable (last good read at' in html
    assert 'Open QMs unavailable; last good read at' in html
    assert m.status()['state'] == 'error' and not m.capabilities()['close']


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
    assert html.index('ui-opxmon-verdict') < html.index('ui-opxmon-status')
    assert '<summary>Details</summary>' in html
    assert om.verdict({'open_qms': []}, {'color': 'green'}, {})['label'] == 'FREE'
    assert om.verdict(answer(), {'color': 'amber'}, {})['label'] == 'BUSY'


def test_d24_bad_outranks_unknown_and_admin_auto(monkeypatch):
    import io
    from pathlib import Path
    root = Path(__file__).parent / 'fixtures' / 'opx_monitor'
    class Opener:
        def open(self, req, timeout):
            name = 'v2_clusters.json' if req.full_url.endswith('/clusters') else 'v2_devices_detailed.json'
            return io.BytesIO((root / name).read_bytes())
    monkeypatch.setattr(om.urllib.request, 'build_opener', lambda *a: Opener())
    admin = om.read_admin('http://sample.invalid', 'any')
    assert admin['known'] and admin['selection'] == 'auto-selected from admin'
    a = answer(qms=(), temps={'FEM 0': 67, 'Chassis': 46})
    assert om.health(a, {'known': False}, {'temp:FEM 0': 50}, 'ok')['color'] == 'red'
    assert om.health(a, admin, {}, 'ok')['color'] == 'green'
    assert om.temperature_limit('FEM 0', {'temp:all_fems': 80, 'temp:FEM 0': 50}) == 50


def test_d25_inline_safety(env):
    app, m, w, clock, _ = env
    m.poll_once()
    html = page(app, 'cleanup')
    assert 'ui-opxmon-danger' in html and 'Opening a new QM normally closes existing QMs' in html
    assert om.load_settings(m.instance)['idle_minutes'] == 120
    response = app.test_client().post('/opx-monitor/close', data={'all': '1', 'qm_ids': 'QM-a'}, headers=ORIGIN)
    assert response.status_code == 400 and not any(op.startswith('close') for op, _ in w.calls)


def test_d26_format_count_rounding_and_frozen(env):
    app, m, w, clock, _ = env
    a = answer(qms=())
    a['jobs'] = [{'id': 'job-fable-' + str(i), 'status': 'Done', 'created_at': '2026-01-01T00:00:00.123456Z'} for i in range(313)]
    w.script[:] = [a]
    m.poll_once()
    html = page(app, 'jobs')
    assert '100 of 313 jobs shown' in html and 'Show more' in html
    assert html.count('No chip is open') == 1 and '(OPX clock)' in html and 'UTC+' not in html.split('Server jobs')[1]
    assert '.123456Z</span>' not in html
    state = om.idle_state({'jobs': []}, _w({'jobs': []}, 50), 1)
    assert not state['idle'] and state['basis'].startswith('no job change for 0 min')
    assert om.words('OPERATIONAL_CLUSTER') == 'operational'
    trends = m.trends()
    for t in range(0, 181, 5):
        trends.append(answer(temps={'Chassis': 40}), clock.t + t, 5)
    assert trends.series(clock.t + 180)['con1/Chassis']['unchanged_min'] == 3


def test_d27_tiles_empty_slot_and_shared_baseline(env):
    app, m, w, clock, _ = env
    a = answer(temps={'FEM 0': 67, 'FEM 1': 0, 'Chassis': 40})
    a['controllers'][0]['fems'] = {'1': 'LF'}
    w.script[:] = [a]
    m.poll_once()
    html = page(app, 'live')
    assert 'empty slot' in html and 'ui-opxmon-tile' in html and 'Chart scale' in html
    assert om.temperature_rows(a['controllers'][0])[1]['empty']
    s = m.trends().series(clock.t)
    assert ',18.13' in s['con1/FEM 0']['paths'][0]


def test_d28_cache_jobs_and_backoff(env):
    from types import SimpleNamespace
    from quam_state_manager.generator.opx_monitor_worker import QmmTarget
    calls = []
    q = SimpleNamespace(get_jobs=lambda: [], get_config=lambda: calls.append('config') or {})
    t = QmmTarget.__new__(QmmTarget)
    t.qmm = SimpleNamespace(version_dict=lambda: {}, cluster_name='sample', get_devices=lambda: SimpleNamespace(controllers={}), list_open_qms=lambda: ['QM-fable'], get_qm=lambda qid: q, get_jobs=lambda: calls.append('jobs') or [])
    t.poll(); t.poll()
    assert calls == ['config']
    t.poll(include_jobs=True)
    assert calls == ['config', 'jobs']
    app, m, w, clock, _ = env
    settings = m.settings()
    settings['alarms_armed'] = True
    om.save_settings(m.instance, settings)
    assert m.poll_interval() == 60
    m.want('jobs', 200)
    m.poll_once()
    assert w.calls[-1] == ('poll', {'include_jobs': True, 'job_limit': 200})
    m.want('live')
    m.poll_once()
    assert w.calls[-1][1]['include_jobs'] is True  # another open tab must not cancel Jobs
    assert m.poll_interval() == 5
    clock.t += 16
    m.poll_once()
    assert w.calls[-1][1]['include_jobs'] is False  # Jobs demand expires without renewal


def test_round3_dom_selfcheck(env, tmp_path):
    import shutil, subprocess
    from pathlib import Path
    app, m, w, clock, _ = env
    pages = {}
    m.poll_once()
    original = w.request
    w.request = lambda op, **kw: {'ok': True, 'result': False} if op == 'close_qm' else original(op, **kw)
    m.close('QM-a')
    pages['d22'] = page(app, 'history')
    w.request = original
    pages['d25'] = page(app, 'cleanup')
    w.script[:] = [{'controllers': None, 'open_qms': None, 'jobs': None, 'refused': {'get_devices': 'refused', 'get_jobs': 'refused', 'list_open_qms': 'refused'}}]
    m.poll_once()
    pages['d21'] = page(app, 'live')
    a = answer(qms=(), temps={'FEM 0': 40, 'FEM 1': 0})
    a['controllers'][0]['fems'] = {'1': 'LF'}
    w.script[:] = [a]
    s = m.settings(); s['thresholds'] = {'temp:FEM 0': 50}; om.save_settings(m.instance, s)
    m.poll_once()
    pages['d23'] = page(app, 'live')
    for _ in range(180):
        clock.t += 5
        m.poll_once()
    pages['d27'] = page(app, 'live')
    a['controllers'][0]['temperatures']['FEM 0'] = 67
    m.poll_once()
    pages['d24'] = page(app, 'live')
    pages['d28'] = app.test_client().get('/opx-monitor/alarm-badge').get_data(as_text=True)
    a['jobs'] = [{'id': 'job-fable-' + str(i), 'status': 'Done', 'created_at': '2026-01-01T00:00:00Z'} for i in range(313)]
    m.poll_once()
    pages['d26'] = page(app, 'jobs')
    fixture = tmp_path / 'round3-pages.json'; fixture.write_text(json.dumps(pages), encoding='utf-8')
    node = shutil.which('node')
    assert node, 'jsdom checks require node'
    result = subprocess.run([node, str(Path(__file__).with_name('opx_monitor_round3_selfcheck.cjs')), str(fixture)], capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

@pytest.mark.parametrize('mode,label', [('free', 'FREE'), ('busy', 'BUSY'), ('open', 'OPEN 1'), ('idle', 'OPEN-IDLE 1')])
def test_round4_unconfigured_health_preserves_occupancy(env, mode, label):
    app, m, w, clock, _ = env
    a = answer(qms=() if mode == 'free' else (("QM-placeholder", [('job-placeholder', 'Running')] if mode == 'busy' else [], []),))
    w.script[:] = [a]
    m.poll_once()
    if mode == 'idle':
        _slow(m)
        _watch(m, clock, 7300)
    html = page(app, 'live')
    assert '<strong>' + label + '</strong>' in html
    assert 'health not assessed: no admin URL / no limits set' in html
    assert 'ui-opxmon-health-note' in html
    assert '<strong>PROBLEM</strong>' not in html


def test_round4_recent_open_is_normal(env):
    app, m, w, clock, _ = env
    s = m.settings(); s['thresholds'] = {'temp:Chassis': 80}; om.save_settings(m.instance, s)
    w.script[:] = [answer(qms=(("QM-placeholder", [], []),), temps={'Chassis': 40})]
    m.poll_once()
    html = page(app, 'live')
    assert '<strong>OPEN 1</strong> open, no job seen yet; not idle yet' in html
    assert '<strong>PROBLEM</strong>' not in html


def test_round4_alarm_and_failed_calls(env):
    app, m, w, clock, _ = env
    s = m.settings(); s.update(thresholds={'temp:FEM 0': 50}, alarms_armed=True); om.save_settings(m.instance, s)
    w.script[:] = [answer(qms=(), temps={'FEM 0': 60, 'Chassis': 70})]
    m.poll_once()
    html = page(app, 'live')
    assert '<strong>PROBLEM</strong>' in html
    assert 'Above limit: FEM 0 60 °C' in html
    assert 'limit 50 °C' in html
    assert 'Hottest: Chassis' not in html
    w.script[:] = [om.WorkerError('read failed')]
    m.poll_once()
    html = page(app, 'live')
    assert '<strong>PROBLEM</strong>' in html
    assert 'Temperatures unavailable (last good read at' in html
    assert 'No temperature readings.' not in html
    assert 'SSH tunnel' not in html
    assert 'SSH tunnel' not in om.connection_hint('localhost')
    assert 'SSH tunnel' not in om.connection_hint('::1')
    assert 'SSH tunnel' not in om.connection_hint('service.invalid')


def test_round4_auto_cluster_placeholder(env):
    app, m, w, clock, _ = env
    m.poll_once()
    m.admin = {'known': False, 'cluster': {'name': 'cluster-placeholder'}}
    assert 'placeholder="auto: cluster-placeholder"' in page(app, 'settings')

def test_round4_partial_failure_and_admin_failure(env, monkeypatch):
    app, m, w, clock, _ = env
    w.script[:] = [answer(qms=(), refused={'con1.temperatures': 'read failed'})]
    m.poll_once()
    html = page(app, 'live')
    assert '<strong>PROBLEM</strong>' in html
    assert 'Temperatures unavailable (last good read at' in html
    assert 'No temperature readings.' not in html
    def unavailable(*args):
        raise OSError('unreachable')
    monkeypatch.setattr(om.urllib.request, 'build_opener', unavailable)
    admin = om.read_admin('https://service.invalid')
    assert admin['problem'] and 'SSH tunnel' not in admin['reason']
    assert om.verdict({'open_qms': []}, om.health({}, admin, {}, 'ok'), {})['label'] == 'PROBLEM'
    m.admin = admin
    w.script[:] = [answer(qms=())]
    m.poll_once()
    m.admin = admin
    html = page(app, 'live')
    assert '<strong>PROBLEM</strong>' in html
    assert 'The SDK itself answers.' in html


def test_round5_starting_before_first_poll(env):
    app, m, w, clock, _ = env
    html = page(app, 'live')
    assert m.status()['state'] == 'starting'
    assert not w.calls and m.last_answer is None
    assert '<strong>Checking...</strong>' in html
    assert '<strong>PROBLEM</strong>' not in html
    assert 'data-health="grey"' in html


def test_round5_mixed_idle_headline(env):
    app, m, w, clock, _ = env
    old = (('QM-old-a', [], []), ('QM-old-b', [], []))
    w.script[:] = [answer(qms=old)]
    _slow(m)
    _watch(m, clock, 3 * 3600)
    w.script[:] = [answer(qms=old + (('QM-new', [], []),))]
    m.poll_once()
    html = page(app, 'live')
    assert '<strong>OPEN 3 · 2 idle</strong> Close idle QMs under Clean up' in html
    assert 'in use / recent activity' not in html
    assert 'Close idle QMs (2)' in page(app, 'cleanup')


def test_round5_no_job_seen_headline(env):
    app, m, w, clock, _ = env
    w.script[:] = [answer(qms=(('QM-new', [], []),))]
    m.poll_once()
    html = page(app, 'live')
    assert '<strong>OPEN 1</strong> open, no job seen yet; not idle yet' in html
    assert 'in use' not in html


def test_round5_stale_headline(env):
    app, m, w, clock, _ = env
    m.poll_once()
    clock.t += 60
    m.wanted_until = clock.t + 15
    assert '<strong>BUSY — as of 60 s ago</strong>' in page(app, 'live')


def test_round5_frozen_cue_only_after_fifteen_minutes(env):
    import re
    app, m, w, clock, _ = env
    w.script[:] = [answer(temps={'FEM 0': 50})]
    m.poll_once()
    for _ in range(179):
        clock.t += 5
        m.poll_once()
    html = page(app, 'live')
    assert 'unchanged for' not in html
    clock.t += 5
    m.poll_once()
    html = page(app, 'live')
    assert 'unchanged for 15 min' in html
    # Hover and expanded details carry the cue; the collapsed summary cannot grow.
    summaries = re.findall(r'<summary>(.*?)</summary>', html, re.S)
    assert all('unchanged for' not in summary for summary in summaries)


def test_round5_display_formatting(env):
    import re
    app, m, w, clock, _ = env
    s = m.settings()
    s.update(thresholds={'temp:FEM 0': 50}, alarms_armed=True)
    om.save_settings(m.instance, s)
    w.script[:] = [answer(temps={'FEM 0': 60})]
    m.poll_once()
    html = page(app, 'live')
    assert 'limit 50 °C' in html and '50.0 °C' not in html
    assert '60 °C' in html and '60.0 °C' not in html
    assert re.search(r'Idle minutes <input\b[^>]*name="idle_minutes"[^>]*value="120"', page(app, 'settings'))
    assert re.search(r'\(UTC[+-]\d{1,2}(?::\d{2})?\)$', om.local_time('2026-11-02T20:25:40Z'))
    badge = app.test_client().get('/opx-monitor/alarm-badge').get_data(as_text=True)
    assert 'limit 50 °C' in badge and '50.0 °C' not in badge

# Real-Chrome stress regression pins. Every check uses a fake worker or SDK.
def test_stress_d1_unconfigured(env):
    app, m, *_ = env
    om.save_settings(m.instance, {})
    assert m.status()['health']['reason'] == 'health not assessed'
    html = page(app, 'live')
    assert 'Health not assessed' in html and '<strong>PROBLEM' not in html


def test_stress_d2_cleanup_poll_and_close(env):
    app, m, w, *_ = env
    m.poll_once()
    assert 'hx-get="/opx-monitor/body?tab=cleanup' in page(app, 'cleanup')
    original = w.request
    def request(op, **kw):
        result = original(op, **kw)
        if op.startswith('close'):
            w.script[:] = [answer(qms=())]
        return result
    w.request = request
    assert m.close('QM-a')['closed'] == ['QM-a']
    assert 'data-qm-id="QM-a"' not in page(app, 'cleanup')


def test_stress_d3_viewer_wakes_and_uses_viewer_freshness(env):
    app, m, w, clock, _ = env
    s = m.settings(); s['alarms_armed'] = True; om.save_settings(m.instance, s)
    m.poll_once()
    assert m.last_poll_interval == 60
    clock.t += 52
    m.want('cleanup')
    assert m._wake.is_set()
    assert m.status_state_unfresh() and not m.capabilities()['close']
    m.viewer_check_pending = False
    assert m.status()['state'] == 'stale'
    assert m.status()['valid_for_ms'] == 0


def test_stress_d5_error_cause(env):
    app, m, w, *_ = env
    a = answer(refused={'list_open_qms': 'ConnectionError: service unavailable'})
    a['open_qms'] = None; a['jobs'] = None
    w.script[:] = [a]; m.poll_once()
    html = page(app, 'jobs')
    assert 'Server unreachable while reading list_open_qms' in html
    assert 'Updated 0 s ago' not in html
    a = answer(refused={'con1.temperatures.FEM 0': "not a number: 'hot'"})
    w.script[:] = [a]; m.poll_once()
    # the headline is short; the full cause is said once, in the status line
    assert "not a number" not in m.status()['health']['reason']
    assert "not a number: 'hot'" in m.status()['sentence']
    assert m.status()['health']['reason'].startswith('Unreadable reply')
    assert 'connection' not in m.status()['health']['details']
    a = answer(refused={'get_devices': 'PermissionError: not permitted'})
    w.script[:] = [a]; m.poll_once()
    assert m.status()['health']['reason'].startswith('The server refused')
    assert 'permissions' in m.status()['health']['details']


def test_stress_d5b_malformed_device_section():
    from types import SimpleNamespace
    qmm = _FakeQmm([])
    qmm.version_dict = lambda: {}
    qmm.cluster_name = ''
    qmm.get_devices = lambda: SimpleNamespace(controllers='not-a-dict')
    qmm.get_jobs = lambda: [SimpleNamespace(id='job-still-visible', status='Done')]
    a = _worker_target(qmm).poll(include_jobs=True)
    assert a['controllers'] is None and 'get_devices' in a['refused']
    assert a['open_qms'] == [] and a['jobs'][0]['id'] == 'job-still-visible'


def test_stress_d6_full_job_ids(env):
    app, m, w, *_ = env
    ids = ['shared-prefix-job-%04d' % i for i in range(40)]
    w.script[:] = [answer(qms=(('QM-many', [(j, 'Running') for j in ids], []),))]
    m.poll_once()
    html = page(app, 'jobs')
    assert all('<code>' + j + '</code>' in html.split('Server jobs')[0] for j in ids)


def test_stress_d7_unverified_type():
    c = {'fems': {'2': 'MW'}, 'temperatures': {'FEM 1': 45, 'FEM 4': 50}}
    assert all(not r['mapping_verified'] and not r['type'] for r in om.temperature_rows(c))


def test_stress_d8_busy_empty_version(env):
    app, m, w, *_ = env
    a = answer(qms=(('QM-a', [('j1','Running')], ['con1/1']), ('QM-b', [('j2','Running')], ['con1/2'])), temps={'FEM 0': 40, 'FEM 1': 0})
    a['version'] = {'QOP': ''}; a['controllers'][0]['fems'] = {'1':'LF'}
    s = m.settings(); s['thresholds'] = {'temp:all_fems':70}; om.save_settings(m.instance,s)
    w.script[:] = [a]; m.poll_once()
    html = page(app, 'live')
    assert '2 QMs with active jobs' in html and 'QOP  (server)' not in html
    import re
    empty = next(x for x in re.findall(r'<details class="ui-opxmon-tile".*?</details>',html,re.S) if 'empty slot' in x)
    assert 'limit 70' not in empty


def test_stress_r2_plain_language(env):
    app, m, w, *_ = env
    w.script[:] = [answer(temps={'FEM 1':45})]; m.poll_once()
    html = page(app, 'cleanup') + page(app, 'live')
    assert 'close_other_machines' not in html
    assert 'active or unknown job status' not in html
    assert 'Job status: Running' in html
    assert 'Raw key' not in html and 'Shared y baseline' not in html
    assert 'SSH tunnel' not in om.connection_hint('localhost')


def test_stress_r3_section_labels(env):
    app, m, w, *_ = env
    m.section_errors = {'jobs':'missing'}
    assert '>Jobs unavailable;' in page(app, 'history')


def test_stress_r4_outage_red(env):
    app, m, w, *_ = env
    w.script[:] = [om.WorkerError('Server unreachable')]; m.poll_once()
    html = page(app,'live')
    assert 'ui-opxmon-verdict--red' in html and '<strong>PROBLEM</strong>' in html


def test_stress_r5_settings(env):
    app, m, *_ = env
    html = page(app,'settings')
    assert '<input name="python" list=' in html
    assert '<input name="port"' in html


def test_stress_r6_returning_viewer_checks(env):
    app, m, w, clock, _ = env
    m.poll_once(); clock.t += 25
    html = page(app,'live')
    # earlier answers exist: their age is shown, never "Waiting for the first server answer"
    assert 'Waiting for the first server answer' not in html
    assert '<strong>BUSY — as of 25 s ago</strong>' in html and 'last good read was 25 s ago' in html


def test_stress_r7_gap_markers(env):
    app, m, w, clock, _ = env
    w.script[:] = [answer(temps={'FEM 0':45})]
    m.poll_once(); clock.t += 60; m.poll_once()
    html = page(app,'live')
    assert html.count('<circle ') >= 4 and 'spaces indicate missing readings' in html


def test_stress_browser_pins():
    import os, subprocess
    from pathlib import Path
    r = subprocess.run(['node', str(Path(__file__).with_name('opx_monitor_stress_selfcheck.cjs'))], capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr


def test_stress_kill_tree(tmp_path):
    import os, subprocess, sys, time
    from pathlib import Path
    # Use the actual EnvWorker launcher with a tiny fake JSON-lines worker.
    script = tmp_path / 'tree_worker.py'
    marker = tmp_path / 'child.pid'
    script.write_text("import subprocess,sys,json,time\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'])\nPath(" + repr(str(marker)) + ").write_text(str(p.pid))\nprint(json.dumps({'ready':True}),flush=True)\nfor line in sys.stdin: pass\n", encoding='utf-8')
    original = om.WORKER_SCRIPT
    om.WORKER_SCRIPT = script
    worker = None
    try:
        worker = om.EnvWorker(sys.executable, {})
        child = int(marker.read_text())
        worker.kill()
        if os.name == 'nt':
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000,False,child)
            if handle:
                code = ctypes.c_ulong()
                ctypes.windll.kernel32.GetExitCodeProcess(handle,ctypes.byref(code))
                ctypes.windll.kernel32.CloseHandle(handle)
                assert code.value != 259, 'descendant survived'
        else:
            # A reparented zombie is stopped; Linux /proc retains it until reaped.
            stat = Path(f'/proc/{child}/stat')
            assert not stat.exists() or stat.read_text().split()[2] == 'Z'
    finally:
        om.WORKER_SCRIPT = original
        if worker and worker.alive(): worker.kill()
        if marker.exists() and os.name == 'nt':
            subprocess.run(['taskkill','/PID',marker.read_text(),'/T','/F'],capture_output=True)


def test_stress_r1_responsive(env):
    from pathlib import Path
    app, m, *_ = env
    m.poll_once()
    html = page(app,'cleanup')
    css = (Path(__file__).resolve().parents[1]/'quam_state_manager/web/static/ui-kit.css').read_text(encoding='utf-8')
    assert '.ui-opxmon-tiles {display:flex; flex-wrap:wrap;' in css
    assert '.ui-opxmon-table--stacked tr, .ui-opxmon-table--stacked td { display:block;' in css
    assert 'data-label="FEMs claimed"' in html



def test_stress_pending_until_reply(env):
    app, m, w, clock, _ = env
    m.poll_once(); clock.t += 9; m.want('live')
    original = w.request
    def request(op, **kw):
        assert m.status()['state'] == 'stale'
        assert m.viewer_check_pending and not m.capabilities()['close']
        return original(op, **kw)
    w.request = request
    m.poll_once()
    assert m.status()['state'] == 'ok' and not m.viewer_check_pending


def test_stress_malformed_jobs_keep_other_sections():
    from types import SimpleNamespace
    class Broken:
        @property
        def metadata(self):
            raise ValueError('bad job payload')
    qmm = _FakeQmm([])
    qmm.version_dict = lambda: {}
    qmm.cluster_name = ''
    qmm.get_devices = lambda: SimpleNamespace(controllers={})
    qmm.get_jobs = lambda: [Broken()]
    a = _worker_target(qmm).poll(include_jobs=True)
    assert a['controllers'] == [] and a['open_qms'] == []
    assert a['jobs'] is None and 'get_jobs' in a['refused']


def test_stress_d3_sleeping_poller_wakes(env):
    import threading
    import time
    import types
    app, m, w, clock, _ = env
    s = m.settings(); s['alarms_armed'] = True; om.save_settings(m.instance,s)
    polled = threading.Event()
    original = w.request
    def request(op, **kw):
        result = original(op, **kw)
        if op == 'poll': polled.set()
        return result
    w.request = request
    m._start_thread = types.MethodType(om.Monitor._start_thread,m)
    try:
        m.ensure_running_if_armed()
        assert polled.wait(2)
        # Let the first fake poll leave its lock and enter the 60 s alarm wait.
        with m._lock: pass
        time.sleep(0.05)
        polled.clear(); clock.t += 52
        m.want('cleanup')
        assert polled.wait(2), 'viewer did not wake the sleeping poller'
    finally:
        m.stop()
        if m._thread: m._thread.join(timeout=2)
    assert not m._thread.is_alive() and w.dead


def test_one_unreadable_sensor_does_not_hide_the_others(env):
    """A single sensor that is not a number is shown as unreadable with its
    reason; every readable sensor keeps its value."""
    app, m, w, clock, _ = env
    w.script[:] = [answer(qms=(), temps={'FEM 1': 51.5, 'Back plane': 41.25},
                          refused={'con1.temperatures.FEM 0': "not a number: 'NaN?'"})]
    m.poll_once()
    html = page(app, 'live')
    assert 'Temperatures unavailable' not in html
    assert '51.5 °C' in html and '41.2' in html
    assert 'ui-opxmon-tile--unreadable' in html
    assert '<strong>unreadable</strong>' in html
    assert "not a number: &#39;NaN?&#39;" in html
    assert om.unreadable_temperatures(m.status()['answer']) == {
        'con1': [{'key': 'FEM 0', 'slot': 1, 'reason': "not a number: 'NaN?'", 'label': 'FEM 0'}]}


def test_a_whole_temperature_failure_still_says_unavailable(env):
    app, m, w, clock, _ = env
    w.script[:] = [answer(qms=(), refused={'con1.temperatures': 'read failed'})]
    m.poll_once()
    assert 'Temperatures unavailable' in page(app, 'live')


def test_a_connection_failure_is_said_once_short_then_in_full(env):
    """Headline: the short cause. Status line: the full error. Advice: connection.
    Close: disabled with a reason naming the failure."""
    app, m, w, clock, _ = env
    m.poll_once()
    assert m.capabilities()['close']
    full = ('The QM SDK could not connect: ConnectionError: Failed to connect to QOP at '
            '127.0.0.1:9510')
    w.script[:] = [om.WorkerError(full)]
    clock.t += 5
    m.poll_once()
    st = m.status()
    assert st['health']['reason'] == 'The QM SDK could not connect'
    assert full in st['sentence']
    assert 'connection' in st['health']['details']
    cap = m.capabilities()
    assert not cap['close'] and 'The QM SDK could not connect' in cap['close_reason']
    html = page(app, 'live')
    import re as _re
    visible = _re.sub(r'<[^>]*>', ' ', html)
    assert visible.count('127.0.0.1:9510') == 1  # said once, in the status line
    assert '127.0.0.1:9510. The last good read' in visible


def test_every_sensor_unreadable_is_not_called_no_readings(env):
    app, m, w, clock, _ = env
    w.script[:] = [answer(qms=(), temps=None, refused={'con1.temperatures.FEM 0': "not a number: 'hot'"})]
    m.poll_once()
    html = page(app, 'live')
    assert 'No temperature readings.' not in html and '<strong>unreadable</strong>' in html


def test_a_timeout_gets_its_own_advice_not_a_permission_hint():
    errors = {'controllers': 'No answer within 30 s while reading controllers; the worker was stopped.'}
    advice = om.failure_advice(errors, 'host')
    assert 'permission' not in advice
    assert 'slow or overloaded' in advice and 'network' in advice


def test_tiles_kept_after_a_failed_read_are_marked_stale(env):
    app, m, w, clock, _ = env
    w.script[:] = [answer(temps={"FEM 1": 61.0})]
    m.poll_once()
    assert 'ui-opxmon-tile--stale' not in page(app, 'live')
    clock.t += 5
    w.script[:] = [om.WorkerError('No answer within 30 s while reading; the worker was stopped.')]
    m.poll_once()
    html = page(app, 'live')
    assert 'Temperatures unavailable (last good read at' in html
    assert 'the values below are stale' in html
    assert 'ui-opxmon-tile--stale' in html and '>stale, as of ' in html
    assert 'permission' not in html


# ---- review round 1: the monitor never shows a state it has not proven ----

_NO_ADMIN = {'known': False, 'not_applicable': True, 'reason': 'n/a'}


def _one_ctrl(temps, fems=None, metrics=None):
    return {"controllers": [{"name": "con1", "fems": fems or {"1": "LF", "2": "MW"},
                             "temperatures": temps, "metrics": metrics}], "open_qms": [], "refused": {}}


def test_all_fem_limit_is_not_green_while_a_populated_slot_reads_nothing():
    h = om.health(_one_ctrl({"FEM 0": 45.0, "FEM 1": 0.0}), _NO_ADMIN, {"temp:all_fems": 80.0}, 'ok')
    assert h['color'] == 'grey' and 'no temperature reading' in h['reason']
    h = om.health(_one_ctrl({"FEM 0": 45.0}), _NO_ADMIN, {"temp:all_fems": 80.0}, 'ok')
    assert h['color'] == 'grey'
    h = om.health(_one_ctrl({"FEM 0": 45.0, "FEM 1": 50.0}), _NO_ADMIN, {"temp:all_fems": 80.0}, 'ok')
    assert h['color'] == 'green'


@pytest.mark.parametrize("status", ["In queue", "Processing", "Running", "Something new"])
def test_sdk_active_statuses_are_busy_never_idle(status):
    q = {"id": "qm-1", "ports": ["con1/1"], "jobs": [{"id": "j1", "status": status,
         "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:01Z"}]}
    idle = {"qm-1": om.idle_state(q, _w(q), 120)}
    assert idle["qm-1"]["idle"] is False
    assert om.verdict({"open_qms": [q]}, {"color": "amber"}, idle, "ok", 1.0)['label'] == 'BUSY'


def test_sdk_error_status_is_not_finished_and_never_idle():
    # the SDK maps its UNKNOWN job state to "Error"
    q = {"id": "qm-2", "jobs": [{"id": "j2", "status": "Error", "created_at": "2026-01-01T00:00:00Z",
         "updated_at": "2026-01-01T00:00:00Z"}]}
    st = om.idle_state(q, _w(q), 120)
    assert st['idle'] is False and 'Error' in st['basis']
    q2 = dict(q, jobs=[dict(q['jobs'][0], status="Done")])
    assert om.idle_state(q2, _w(q2), 120)['idle'] is True


def test_worker_close_idle_skips_a_qm_whose_job_reads_error():
    from types import SimpleNamespace
    qmm = _FakeQmm(["QM-a"])
    job = SimpleNamespace(id="j", status="Error", time_created=None, time_last_status_update=None)
    qmm.get_qm = lambda qid: SimpleNamespace(get_jobs=lambda: [job], close=lambda: True)
    res = _worker_target(qmm).close_idle(["QM-a"], {"QM-a": {'watched_s': 9e9, 'continuous': True, 'sig': [['j', 'Error']]}}, 1)
    assert res["closed"] == [] and res["skipped"] == ["QM-a"]


def test_fem_tile_marks_a_queued_job_busy(env):
    app, m, w, clock, started = env
    w.script = [answer(qms=(("QM-a", [("j1", "In queue")], ["con1/1"]),))]
    m.poll_once()
    html = page(app, "live")
    assert "ui-opxmon-fem--busy" in html


def test_a_zero_metric_cannot_be_told_from_unset_and_never_judges_a_limit():
    from quam_state_manager.generator import opx_monitor_worker as wk
    refused = {}
    out = wk._readings({"memory_available_gb": 0.0, "cpu_utilization_percent": 3.0, "disk_available_gb": None},
                       "con1", refused, "metrics")
    assert out == {"memory_available_gb": 0.0, "cpu_utilization_percent": 3.0}
    ans = _one_ctrl({"FEM 0": 45.0, "FEM 1": 50.0}, metrics=out)
    # proto3 doubles have no presence: 0.0 is also an unfilled field
    assert om.evaluate_alarms(ans, {"metric:memory_available_gb": 1.0}) == []
    assert om.evaluate_alarms(ans, {"metric:memory_available_gb": -1.0}) == []
    h = om.health(ans, _NO_ADMIN, {"temp:all_fems": 80.0, "metric:memory_available_gb": 1.0}, 'ok')
    assert h['color'] == 'grey' and 'cannot tell from unset' in h['details']
    ans2 = _one_ctrl({"FEM 0": 45.0, "FEM 1": 50.0}, metrics={"memory_available_gb": 0.5})
    assert [a['key'] for a in om.evaluate_alarms(ans2, {"metric:memory_available_gb": 1.0})] == ["metric:memory_available_gb"]
    # a metric the server did not send stays unknown (grey), never a value
    h = om.health(ans, _NO_ADMIN, {"temp:all_fems": 80.0, "metric:disk_available_gb": 1.0}, 'ok')
    assert h['color'] == 'grey'

def test_worker_rereads_a_qm_config_when_its_jobs_change():
    from types import SimpleNamespace
    calls = []
    jobs = [[], [SimpleNamespace(id="j1", status="Running", time_created=None, time_last_status_update=None)]]
    qmm = _FakeQmm(["QM-a"])
    qmm.version_dict = lambda: {}
    qmm.cluster_name = "c"
    qmm.get_devices = lambda: SimpleNamespace(controllers={})
    qmm.get_jobs = lambda: []
    state = {"n": 0}

    def get_config():
        calls.append(1)
        return {"elements": {}}
    qmm.get_qm = lambda qid: SimpleNamespace(get_jobs=lambda: jobs[min(state["n"], 1)], get_config=get_config)
    t = _worker_target(qmm)
    t._config_cache = {}
    t.poll(); t.poll()
    assert len(calls) == 1
    state["n"] = 1
    t.poll()
    assert len(calls) == 2


# ---- live audit round 1 ----

def _iso(t):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def test_idle_needs_a_continuous_watch_with_no_job_change():
    q = {"id": "qm", "jobs": [{"id": "j", "status": "Done", "created_at": None, "updated_at": None}]}
    assert om.idle_state(q, None, 1)['idle'] is False  # never watched
    assert 'not watched' in om.idle_state(q, None, 1)['basis']
    assert om.idle_state(q, _w(q, 59), 1)['idle'] is False  # watched too briefly
    assert om.idle_state(q, _w(q, 61), 1)['idle'] is True
    assert om.idle_state(q, _w(q, 61, continuous=False), 1)['idle'] is False  # watch interrupted
    changed = dict(q, jobs=q['jobs'] + [{"id": "j2", "status": "Done"}])
    assert om.idle_state(changed, _w(q, 61), 1)['idle'] is False  # job list moved


def test_watched_then_idle_and_a_job_change_restarts_the_watch(env):
    app, m, w, clock, _ = env
    s = m.settings(); s['idle_minutes'] = 1; om.save_settings(m.instance, s)
    w.script[:] = [answer(qms=(("QM-a", [("j1", "Done")], []),))]
    m.poll_once()
    _watch(m, clock, 40)
    assert 'Close idle QMs (0)' in page(app, 'cleanup')
    _watch(m, clock, 30)
    assert 'Close idle QMs (1)' in page(app, 'cleanup')
    m.viewer_check_pending = True  # answer not fresh: no idle offer even with a long watch
    assert m.status()['state'] != 'ok'
    from quam_state_manager.web import opx_monitor_routes as routes
    with app.test_request_context('/opx-monitor/body?tab=cleanup'):
        app.config['opx_monitor'] = m
        assert routes._view('cleanup')['idle_ids'] == []
    m.viewer_check_pending = False
    w.script[:] = [answer(qms=(("QM-a", [("j1", "Done"), ("j2", "Done")], []),))]
    m.poll_once()
    assert 'Close idle QMs (0)' in page(app, 'cleanup')  # a new job: the watch starts again
    # a gap longer than a few intervals breaks the watch, even with no job change
    _watch(m, clock, 70)
    assert 'Close idle QMs (1)' in page(app, 'cleanup')
    clock.t += 5 * m.poll_interval()
    m.poll_once()
    assert 'Close idle QMs (0)' in page(app, 'cleanup')
    # no read for a few intervals (poller stopped): the watch is no longer continuous
    _watch(m, clock, 70)
    assert m.watch_info()['QM-a']['continuous']
    clock.t += 5 * m.poll_interval()
    assert not m.watch_info()['QM-a']['continuous']
    assert not om.idle_state(answer(qms=(("QM-a", [("j1", "Done"), ("j2", "Done")], []),))['open_qms'][0],
                             m.watch_info()['QM-a'], 1)['idle']


def test_r3_skew_repro_admin_clock_ahead_never_makes_a_qm_idle(env):
    """r3 D1: the admin host's Date header 3 h ahead of the QM server; a QM busy
    10 min ago must not read idle (idle never compares clocks)."""
    import threading, time as _t, http.server
    from email.utils import formatdate

    class H(http.server.BaseHTTPRequestHandler):
        def date_time_string(self, timestamp=None):
            return formatdate(_t.time() + 3 * 3600, usegmt=True)

        def do_GET(self):
            body = b'[]'
            self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(('127.0.0.1', 0), H)
    th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
    try:
        app, m, w, clock, _ = env
        clock.t = _t.time()
        s = m.settings(); s['admin_url'] = f'http://127.0.0.1:{srv.server_port}'; s['idle_minutes'] = 120
        om.save_settings(m.instance, s)
        a = answer(qms=(("QM-a", [("j1", "Done")], ["con1/1"]),))
        a['open_qms'][0]['jobs'][0].update(created_at=_iso(clock.t - 700), updated_at=_iso(clock.t - 600))
        w.script[:] = [a]
        m.poll_once()
        sk = m.clock_skew()
        assert sk is not None and sk['skew_s'] < -10000  # the admin clock really is 3 h ahead
        html = page(app, 'cleanup')
        assert 'Close idle QMs (0)' in html and 'OPEN-IDLE' not in html
        assert 'admin server clock, measured' in page(app, 'jobs') and 'OPX clock, measured' not in html
    finally:
        srv.shutdown(); srv.server_close()


def test_skew_that_puts_a_job_in_the_pc_future_is_discarded(env):
    app, m, w, clock, _ = env
    a = answer()
    a['open_qms'][0]['jobs'][0].update(created_at=_iso(clock.t - 10), updated_at=_iso(clock.t - 10))
    w.script[:] = [a]
    m.poll_once()
    _measured(m, clock, 5.0, 1.0)
    assert m.clock_skew() is not None  # job maps to clock.t - 5: past
    _measured(m, clock, 30.0, 1.0)
    assert m.clock_skew() is None  # job would map 20 s into the PC future


def test_skew_wording_zero_age_and_failed_admin_reads():
    now = 1_800_000_000.0
    assert om.skew_text({'skew_s': 0.3, 'uncertainty_s': 1.4, 'at': now, 'latest': True}, now).startswith(
        'admin server clock matches this PC within \u00b12 s')
    t = om.skew_text({'skew_s': 80.2, 'uncertainty_s': 1.0, 'at': now, 'latest': True}, now)
    assert t.startswith('admin server clock, measured 80 \u00b1 1 s behind this PC at ')
    assert 'not reported' not in t
    t = om.skew_text({'skew_s': -80.0, 'uncertainty_s': 1.0, 'at': now - 300, 'latest': False}, now)
    assert 'last measured 80' in t and 'ahead of' in t and '5 min ago; newer admin reads gave no clock' in t


def test_skew_stops_being_measured_when_admin_reads_fail(env, monkeypatch):
    app, m, w, clock, _ = env
    s = m.settings(); s['admin_url'] = 'http://sample.invalid'; om.save_settings(m.instance, s)
    monkeypatch.setattr(om, 'read_admin', lambda *a, **k: {'known': True, 'clock': {'skew_s': 10.0, 'uncertainty_s': 1.0}, 'cluster': {}, 'devices': []})
    m.poll_once()
    assert m.clock_skew()['latest'] is True
    monkeypatch.setattr(om, 'read_admin', lambda *a, **k: {'known': False, 'problem': True, 'reason': 'Admin unavailable'})
    clock.t += 60
    m.poll_once()
    assert m.clock_skew()['latest'] is False
    assert 'newer admin reads gave no clock' in page(app, 'jobs')


def test_skew_is_measured_from_the_admin_date_header():
    from email.utils import formatdate
    hdr = formatdate(1_800_000_000, usegmt=True)
    m = om.measure_skew(1_800_000_080.0, 1_800_000_080.4, hdr)
    assert abs(m['skew_s'] - 79.7) < 1e-6 and abs(m['uncertainty_s'] - 1.4) < 1e-6
    assert om.measure_skew(1.0, 2.0, None) is None
    assert om.measure_skew(1.0, 2.0, 'not a date') is None
    assert om.measure_skew(2.0, 1.0, hdr) is None


def test_read_admin_returns_the_tightest_date_header_measurement(monkeypatch):
    from pathlib import Path
    from email.utils import formatdate
    root = Path(__file__).parent / 'fixtures' / 'opx_monitor'
    ticks = iter([100.0, 100.5, 200.0, 200.1])

    class Resp:
        def __init__(self, url, date):
            self.headers = {'Date': date}
            name = 'v2_clusters.json' if url.endswith('/clusters') else 'v2_devices_detailed.json'
            self._raw = (root / name).read_bytes()

        def read(self, n):
            return self._raw

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Opener:
        def open(self, req, timeout):
            return Resp(req.full_url, formatdate(110 if req.full_url.endswith('/clusters') else 190, usegmt=True))
    monkeypatch.setattr(om.urllib.request, 'build_opener', lambda *a: Opener())
    admin = om.read_admin('http://sample.invalid', clock=lambda: next(ticks))
    assert admin['clock']['uncertainty_s'] == pytest.approx(1.1)
    assert admin['clock']['skew_s'] == pytest.approx(200.05 - 190.5)


def test_monitor_skew_is_unknown_without_admin_stale_or_after_a_pc_clock_step(env):
    app, m, w, clock, _ = env
    mono = {'t': 0.0}
    m.mono = lambda: mono['t']
    m.clock_measure = {'skew_s': 80.0, 'uncertainty_s': 1.2, 'at': clock.t, 'at_mono': 0.0}
    assert m.clock_skew() is None  # no admin URL configured
    _measured(m, clock, 80.0, 1.2)
    assert m.clock_skew()['skew_s'] == 80.0
    clock.t += 30
    mono['t'] += 30
    assert m.clock_skew() is not None
    clock.t -= 3 * 3600  # the PC clock stepped (r2 R3)
    assert m.clock_skew() is None
    clock.t += 3 * 3600
    mono['t'] += om.SKEW_MAX_AGE_S
    clock.t += om.SKEW_MAX_AGE_S
    assert m.clock_skew() is None  # too old
    m.reset()
    assert m.clock_measure is None


def test_r1_late_first_sight_never_links_a_wrong_run(env, monkeypatch):
    """r2 R1: clocks agree, SM first saw the job 1 h after it was created. The
    job-first-seen bound said 3600 s and linked run #8; a measurement links #7."""
    from quam_state_manager.web import opx_monitor_routes as routes
    app, m, w, clock, _ = env
    created = clock.t - 3600
    a = answer()
    a['open_qms'][0]['jobs'][0]['created_at'] = _iso(created)
    w.script[:] = [a]
    m.poll_once()
    cands = [{'id': 7, 'url': '/d:7', 'time': created + 5}, {'id': 8, 'url': '/d:8', 'time': clock.t}]
    monkeypatch.setattr(routes, '_run_candidates', lambda: (cands, ''))
    html = page(app, 'jobs')
    assert 'probably #' not in html and 'offset unknown' in html and 'about 3600' not in html
    _measured(m, clock, 0.0, 1.0)
    html = page(app, 'jobs')
    assert 'probably #7' in html and 'probably #8' not in html
    assert 'admin server clock matches this PC within \u00b11 s' in html


def test_r3_pc_clock_step_never_makes_a_busy_qm_idle(env):
    """r2 R3: a measurement taken before a PC clock step is dropped, so a QM
    whose last job was 60 min ago (OPX) is not aged by a 3 h step."""
    app, m, w, clock, _ = env
    s = m.settings()
    s['idle_minutes'] = 120
    om.save_settings(m.instance, s)
    mono = {'t': 0.0}
    m.mono = lambda: mono['t']
    last = clock.t - 60 * 60
    a = answer(qms=(("QM-a", [("j1", "Done")], ["con1/1"]),))
    a['open_qms'][0]['jobs'][0].update(created_at=_iso(last), updated_at=_iso(last))
    a['jobs'] = []
    w.script[:] = [a]
    m.poll_once()
    _measured(m, clock, -3 * 3600.0, 1.0)  # measured while the PC was 3 h slow
    clock.t += 3 * 3600  # NTP corrects the PC clock; no monotonic time passed
    assert m.clock_skew() is None
    html = page(app, 'cleanup')
    assert 'OPEN-IDLE' not in html


def test_run_match_uncertainty_widens_never_narrows():
    from quam_state_manager.web import opx_monitor_routes as routes
    created = 1_800_000_000.0
    job = {"created_at": _iso(created)}
    run = [{"id": 7, "url": "/x", "time": created + 81 + 100}]
    assert 'url' not in routes.run_match(job, run, skew={'skew_s': 81.0, 'uncertainty_s': 1.0})
    assert routes.run_match(job, run, skew={'skew_s': 81.0, 'uncertainty_s': 15.0})['id'] == 7
    assert 'url' not in routes.run_match(job, run, skew=None)
    assert 'url' not in routes.run_match(job, run, skew={'skew_s': None})


def test_close_idle_worker_rechecks_the_watched_job_list():
    from types import SimpleNamespace
    job = SimpleNamespace(id="j", status="Done", time_created=None, time_last_status_update=None)
    qmm = _FakeQmm(["QM-a"])
    qmm.get_qm = lambda qid: SimpleNamespace(get_jobs=lambda: [job], close=lambda: True)
    from quam_state_manager.generator import opx_monitor_worker as wk
    orig = wk._job_row
    wk._job_row = lambda j: {"id": "j", "status": "Done", "created_at": None, "updated_at": None}
    try:
        tgt = _worker_target(qmm)
        tgt._verified_close = lambda qid, qm: {"closed": [qid], "failed": {}}
        assert tgt.close_idle(["QM-a"], {}, 1)["closed"] == []  # never watched
        stale = {"QM-a": {'watched_s': 9e9, 'continuous': True, 'sig': []}}
        assert tgt.close_idle(["QM-a"], stale, 1)["closed"] == []  # job list differs now
        ok = {"QM-a": {'watched_s': 9e9, 'continuous': True, 'sig': [['j', 'Done']]}}
        assert tgt.close_idle(["QM-a"], ok, 1)["closed"] == ["QM-a"]
    finally:
        wk._job_row = orig


def test_stale_open_qm_table_is_never_shown_as_current(env):
    app, m, w, clock, _ = env
    m.poll_once()
    assert 'Not current.' not in page(app, 'jobs')
    w.script[:] = [om.WorkerError('connect failed')]
    clock.t += 99
    m.poll_once()
    html = page(app, 'jobs')
    assert 'Not current.' in html and 'last good read, 99 s ago' in html and 'data-stale="1"' in html
    # an empty list from an old answer never says "No QM is open."
    w.script[:] = [answer(qms=())]
    clock.t += 1
    m._retry_at = 0
    m.poll_once()
    assert 'No QM is open.' in page(app, 'jobs')
    w.script[:] = [om.WorkerError('connect failed')]
    clock.t += 50
    m._retry_at = 0
    m.poll_once()
    html = page(app, 'jobs')
    assert 'No QM is open.' not in html and 'No QM was open at the last good read' in html


def test_returning_viewer_keeps_the_age_on_slot_cards(env):
    app, m, w, clock, _ = env
    w.script[:] = [answer(temps={'FEM 0': 45.0})]
    m.poll_once(); clock.t += 77
    html = page(app, 'live')
    assert 'Waiting for the first server answer' not in html
    assert 'ui-opxmon-tile--stale' in html and 'stale, as of' in html


def test_metric_zero_is_shown_as_cannot_tell(env):
    app, m, w, clock, _ = env
    a = answer()
    a['controllers'][0]['metrics'] = {'disk_available_gb': 0.0, 'cpu_utilization_percent': 12.5}
    w.script[:] = [a]
    m.poll_once()
    html = page(app, 'live')
    assert '0 (SDK cannot tell 0 from unset)' in html and '12.5' in html and '>0.0<' not in html


def test_a_full_job_list_is_a_cap_not_a_total(env):
    app, m, w, clock, _ = env
    a = answer()
    a['jobs'] = [dict(a['jobs'][0], id=f"j{i}") for i in range(1000)]
    a['jobs_total'] = 1000
    w.script[:] = [a]
    m.poll_once()
    html = page(app, 'jobs')
    assert 'of at least 1000 jobs shown' in html and 'at most 1000' in html
    assert 'of 1000 jobs shown' not in html


def test_the_worker_flags_a_capped_job_list():
    from types import SimpleNamespace
    qmm = _FakeQmm([])
    qmm.version_dict = lambda: {}
    qmm.cluster_name = "c"
    qmm.get_devices = lambda: SimpleNamespace(controllers={})
    qmm.get_jobs = lambda: [SimpleNamespace(id=str(i), status="Done") for i in range(1000)]
    t = _worker_target(qmm)
    t._config_cache = {}
    assert t.poll(include_jobs=True)["jobs_capped"] is True
    qmm.get_jobs = lambda: [SimpleNamespace(id="1", status="Done")]
    assert t.poll(include_jobs=True)["jobs_capped"] is False


def test_a_zero_temperature_reads_as_such(env):
    app, m, w, clock, _ = env
    w.script[:] = [answer(temps={'FEM 0': 40, 'FEM 1': 41, 'CRPS 1': 0.0})]
    m.poll_once()
    assert 'reads 0.0 (no sensor value)' in page(app, 'live')


def test_body_carries_the_top_bar_pill_so_they_agree(env):
    app, m, w, clock, _ = env
    m.poll_once()
    html = app.test_client().get("/opx-monitor/body?tab=live", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert 'id="ui-opxmon-alarm-slot" hx-swap-oob="innerHTML"' in html and 'ui-opxmon-health-dot' in html
    assert 'hx-swap-oob' not in page(app, 'live')


def test_alarm_value_shows_the_breach():
    assert om.value_vs_limit(70.0387, 70) == '70.04'
    assert om.value_vs_limit(72.0, 70) == '72'
    assert om.value_vs_limit(70.0, 70) == '70'


def test_history_status_is_labelled_last_seen(env):
    app, m, w, clock, _ = env
    m.poll_once()
    w.script[:] = [answer(qms=())]
    m.poll_once()
    html = page(app, 'history')
    assert 'Status (last seen)' in html and 'Running (when last seen)' in html


def test_unreachable_server_is_not_called_unavailable_for_this_target(env):
    app, m, w, clock, _ = env
    w.script[:] = [ConnectionError("refused")]
    m.poll_once()
    for tab in ('live', 'jobs'):
        html = page(app, tab)
        assert 'Not available for this target.' not in html


def test_r2_live_wording_count_grammar_refusal_and_reasons(env):
    app, m, w, clock, _ = env
    # the count is the server's, before truncation to the shown 100
    a = answer()
    a['jobs'] = [dict(a['jobs'][0], id=f"j{i}") for i in range(100)]
    a['jobs_total'] = 1000
    a['jobs_capped'] = True
    w.script[:] = [a]
    m.poll_once()
    html = page(app, 'jobs')
    assert '100 of at least 1000 jobs shown' in html and 'at least 100 jobs' not in html
    # an OPX-clock time carries no PC zone label
    assert 'Created (OPX clock)' in html
    # one QM, singular
    assert om.health(answer(temps={'Chassis': 40}), {'known': False, 'not_applicable': True}, {'temp:Chassis': 50}, 'ok')['reason'] == '1 open QM claims resources'
    # answered sections have no reason
    cap = m.capabilities()
    assert cap['live'] and cap['jobs'] and cap['live_reason'] == '' and cap['jobs_reason'] == ''
    # a refused TCP connection is unreachable, never "the server refused listing QMs"
    w.script[:] = [answer(refused={'list_open_qms': 'StatusCode.UNAVAILABLE: failed to connect to all addresses; connection refused'})]
    m.poll_once()
    cap = m.capabilities()
    assert cap['close_reason'].startswith('Server unreachable') and 'refused listing' not in cap['close_reason']
