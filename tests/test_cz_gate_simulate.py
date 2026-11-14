"""Simulate for the New CZ gate dialog: worker, run bookkeeping, routes.

The QM stack is faked: a fake manager whose ``execute`` / ``open_qm`` raise,
and a fake runner process that writes the answer the real worker writes."""
import hashlib
import json
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from quam_state_manager.core import cz_gate_simulate as sim
from quam_state_manager.core import port_test_runs as ptr

GEN = Path(sim.__file__).resolve().parent.parent / "generator"
sys.path.insert(0, str(GEN))
import run_gate_sim as rgs  # noqa: E402
import run_port_test as rpt  # noqa: E402

START, STATUS, STOP = ("/api/pulse/cz-gate/simulate/start", "/api/pulse/cz-gate/simulate/status",
                       "/api/pulse/cz-gate/simulate/stop")
CREDS = {"email": "a@b.c", "host": "sim.example", "password": "s3cret-pw"}


# --------------------------------------------------------------------------- worker

def _code(path: Path) -> str:
    """Source without comments and docstrings."""
    import ast
    import io
    import tokenize
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    doc_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and ast.get_docstring(node):
            d = node.body[0]
            doc_lines.update(range(d.lineno, d.end_lineno + 1))
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT or tok.start[0] in doc_lines:
            continue
        out.append(tok.string)
    return " ".join(out)


def test_worker_source_only_simulates_and_never_saves():
    code = _code(GEN / "run_gate_sim.py")
    assert "simulate" in code
    for bad in ("execute", "open_qm", "save", "run_on_hardware"):
        assert not re.search(r"\b" + bad + r"\b", code), bad


class FakeJob:
    def wait_until(self, _s):
        pass

    def get_simulated_samples(self):
        class Con:
            analog = {"1-1": [0.0, 0.1, 0.1, 0.0], "1-7": [0.0] * 4}
            analog_sampling_rate = {"1-1": 2e9, "1-7": 2e9}
            digital = {}
        class S:
            con1 = Con()
        return S()


class FakeQmm:
    def __init__(self):
        self.calls = []

    def simulate(self, config, program, simcfg):
        self.calls.append(("simulate", simcfg))
        return FakeJob()

    def execute(self, *a, **k):
        raise AssertionError("execute called")

    def open_qm(self, *a, **k):
        raise AssertionError("open_qm called")


CONFIG = {"controllers": {"con1": {"fems": {"1": {"type": "LF"}}}},
          "elements": {"q1.z": {"singleInput": {"port": ["con1", 1, 1]}},
                       "q9.z": {"singleInput": {"port": ["con1", 1, 7]}}}}


def _fake_qm(monkeypatch, qmm):
    class SimCfg:
        def __init__(self, duration):
            self.duration = duration
    fake_qm = type(sys)("qm")
    fake_qm.SimulationConfig = SimCfg
    monkeypatch.setitem(sys.modules, "qm", fake_qm)
    monkeypatch.setattr(rpt, "connection", lambda mod, t: qmm)


def test_worker_runs_only_simulate_on_the_manager(monkeypatch):
    qmm = FakeQmm()
    _fake_qm(monkeypatch, qmm)
    mod = rgs.GateModule(CONFIG, object(), [("q1.z", "cz")])
    res = rpt.run_sim(mod, {"local": {"host": "h"}}, {"sim_ns": 504}, lambda **k: None, lambda: False)
    assert [c[0] for c in qmm.calls] == ["simulate"]
    assert qmm.calls[0][1].duration == 504 // 4
    ports = rgs._ports(res["traces"], rgs._port_elements(CONFIG, mod.PLAYS))
    # only the port the program drives, with the simulator's own sample step
    assert [(p["port"], p["elements"], p["dt_ns"]) for p in ports] == [(1, ["q1.z"], 0.5)]
    assert ports[0]["volts"] == [0.0, 0.1, 0.1, 0.0] and ports[0]["cut_at_end"] is False


@pytest.mark.parametrize("ends,pad,want", [([300], 200, 500), ([301], 200, 504), ([10, 999], 0, 1000)])
def test_sim_length_rounds_up_to_4_ns(ends, pad, want):
    rows = [type("R", (), {"end_ns": e})() for e in ends]
    assert rgs.sim_length_ns(rows, [], pad) == (want, "timeline")


def test_sim_length_without_a_timeline_is_the_sum_of_plays():
    ops = [{"length": 100, "delay": 0}, {"length": 60, "delay": 20}]
    assert rgs.sim_length_ns([], ops, 200) == (380, "sum")


def _main(monkeypatch, tmp_path, built, target=None, limit_s=60):
    job = {"folder": str(tmp_path / "chip"), "macro": "qubit_pairs.p.macros.cz",
           "max_ns": sim.MAX_SIM_NS, "pad_ns": sim.PAD_NS, "limit_s": limit_s}
    (tmp_path / "job.json").write_text(json.dumps(job), encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(rgs, "build", lambda j: built)
    monkeypatch.setattr(rgs.rpt, "_detach_stdin", lambda: __import__("io").StringIO(""))
    monkeypatch.setattr(rgs.rpt, "watch_stdin", lambda s, e: None)
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"target": target or {}}) + "\n"))
    monkeypatch.setattr(sys, "argv", ["x", str(tmp_path / "job.json"), str(work)])
    rgs.main()
    return json.loads((work / "_result.json").read_text(encoding="utf-8")), work


def test_worker_refuses_above_20_us_before_connecting(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(rgs.rpt, "run_sim", lambda *a, **k: called.append(1))
    res, _ = _main(monkeypatch, tmp_path, {"ok": True, "sim_ns": 20004, "config": {}, "program": None,
                                           "plays": [], "length_from": "timeline", "cannot_draw": None})
    assert res == {"ok": False, "too_long": True, "sim_ns": 20004, "max_ns": 20000}
    assert not called


def test_worker_at_20_us_is_simulated(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(rgs.rpt, "run_sim", lambda *a, **k: called.append(1) or {"traces": {}})
    res, _ = _main(monkeypatch, tmp_path, {"ok": True, "sim_ns": 20000, "config": {}, "program": None,
                                           "plays": [], "length_from": "timeline", "cannot_draw": "cannot draw: x"})
    assert called and res["ok"] and res["cannot_draw"] == "cannot draw: x"


def test_worker_never_writes_the_password(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("login failed for s3cret-pw")
    monkeypatch.setattr(rgs.rpt, "run_sim", boom)
    res, work = _main(monkeypatch, tmp_path, {"ok": True, "sim_ns": 100, "config": {}, "program": None,
                                              "plays": [], "length_from": "timeline", "cannot_draw": None},
                      target={"local": {}, "saas": dict(CREDS)})
    assert not res["ok"]
    for f in work.iterdir():
        assert "s3cret-pw" not in f.read_text(encoding="utf-8"), f.name
    assert "***" in res["error"]


def test_worker_watchdog_writes_timeout_and_closes_the_instance(monkeypatch, tmp_path):
    closed = []

    class Inst:
        def close(self):
            closed.append(1)

    class Mod:
        instance = Inst()

    class Exit(Exception):
        pass

    def fake_exit(code):
        raise Exit(code)
    monkeypatch.setattr(rgs.os, "_exit", fake_exit)
    with pytest.raises(Exit):
        rgs._watchdog(0.05, tmp_path, [Mod()], threading.Event(), "")
    assert json.loads((tmp_path / "_result.json").read_text())["timed_out"] is True
    assert closed == [1]


# --------------------------------------------------------------------------- pure SM side

def test_target_of_names_the_limits():
    none = sim.target_of({"target": "saas", "opx": {"host": "h"}}, None)
    assert none[0] is None
    t, label, limit = sim.target_of({"target": "opx", "opx": {"host": "10.0.0.2"}}, None)
    assert t["local"]["host"] == "10.0.0.2" and limit == sim.LIMIT_LOCAL_S == 60
    t, label, limit = sim.target_of({"target": "saas", "opx": {}}, CREDS)
    assert t["saas"]["password"] and limit == sim.LIMIT_CLOUD_S == 240 and "QM-SaaS" in label


@pytest.mark.parametrize("st,res,state", [
    ({"state": "none"}, None, "none"),
    ({"state": "running", "phase": "connecting"}, None, "starting"),
    ({"state": "running", "phase": "simulating"}, None, "running"),
    ({"state": "done"}, {"ok": False, "timed_out": True}, "timeout"),
    ({"state": "done"}, {"ok": False, "stopped": True}, "stopped"),
    ({"state": "done"}, {"ok": False, "too_long": True, "sim_ns": 20100}, "too_long"),
    ({"state": "done"}, {"ok": False, "error_kind": "gate", "error": "KeyError: 'x'"}, "not_run"),
    ({"state": "done"}, {"ok": False, "error_kind": "connect", "error": "Could not reach x."}, "failed"),
    ({"state": "done"}, {"ok": True, "ports": []}, "failed"),
])
def test_every_state_has_its_line_and_no_invented_waveform(st, res, state):
    if res is not None:
        st = dict(st, result=res)
    out = sim.from_status(st, {"where": "w", "limit_s": 60})
    assert out["state"] == state
    assert out["line"] and " -- " not in out["line"] and "CAS" not in out["line"]
    assert "patch" not in out["line"].lower()
    assert "ports" not in out


def test_done_shapes_ports_and_drops_malformed():
    res = {"ok": True, "sim_ns": 504, "backend_label": "QM-SaaS cloud simulator", "ports": [
        {"controller": "con1", "fem": 5, "port": 1, "elements": ["q1.z"], "t0_ns": 0, "dt_ns": 0.5,
         "volts": [0, 0.1]},
        {"controller": "con1", "fem": "x", "port": 2, "volts": [1]},
        {"controller": "con1", "fem": 5, "port": 3, "volts": []}]}
    out = sim.from_status({"state": "done", "result": res}, {})
    assert out["state"] == "done" and out["line"] == "Simulated on QM-SaaS cloud simulator: 504 ns."
    assert out["ports"] == [{"key": "con1/5/1", "port_text": "con1 / 5 / 1", "controller": "con1",
                             "fem": 5, "port": 1, "elements": ["q1.z"], "kind": None, "t0_ns": 0.0,
                             "dt_ns": 0.5, "volts": [0.0, 0.1], "decimated": False,
                             "cut_at_end": False}]
    assert "cut_note" not in out
    res["ports"][0]["cut_at_end"] = True
    cut = sim.from_status({"state": "done", "result": res}, {})
    assert cut["cut_note"] == ("The simulation window ended while these ports were still playing: "
                               "con1 / 5 / 1.")


def test_static_lines():
    assert sim.answer("not_configured")["line"] == (
        "Not simulated: no simulator is set up. Set the OPX host or the QM-SaaS account in "
        "Settings, Port test run targets.")
    assert "20 us" in sim.line("too_long", sim_ns=20100)
    assert "60 s" in sim.line("timeout", limit_s=60)


class FakeProc:
    def __init__(self, argv, result, delay=0.0):
        self.pid = None
        self.argv = argv
        self.returncode = None
        self.stdin = self
        self.lines = []
        self.work = Path(argv[3])
        self._done = threading.Event()
        self.result = result

        def finish():
            time.sleep(delay)
            if (self.work / "stop").exists():
                self.result = {"ok": False, "stopped": True}
            (self.work / "_result.json").write_text(json.dumps(self.result), encoding="utf-8")
            self.returncode = 0
            self._done.set()
        threading.Thread(target=finish, daemon=True).start()

    def write(self, s):
        self.lines.append(s)

    def flush(self):
        pass

    def close(self):
        pass

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = -9
        self._done.set()


def test_reap_asks_a_stop_past_the_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(sim, "SM_SLACK_S", 0.0)
    m = sim.Manager(tmp_path)
    procs = []

    def popen_for(folder):
        def p(argv, **kw):
            procs.append(FakeProc(argv, {"ok": True}, delay=1.0))
            return procs[-1]
        return p
    removed = []
    copy = type("C", (), {"after": tmp_path / "after", "root_class": None})()
    ans, code = m.start("w1", python=sys.executable, target={"saas": CREDS}, label="L", limit_s=0.1,
                        make_copy=lambda: copy, remove_copy=removed.append, macro="m",
                        popen_for=popen_for)
    assert code == 200
    procs[0]._done.wait(5)
    n = 0
    while not removed and n < 50:
        n += 1
        time.sleep(0.1)
    assert (procs[0].work / "stop").exists()
    assert removed == [copy]


# --------------------------------------------------------------------------- routes

from tests.test_cz_gate_routes import App, _body  # noqa: E402


def _md5_tree(folder: Path) -> dict:
    return {str(p.relative_to(folder)): hashlib.md5(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*.json")) if p.is_file()}


@pytest.fixture
def app(monkeypatch):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        a = App(tmp)
        a.tmp = Path(tmp)
        from quam_state_manager.web import cz_gate_routes as czr
        from quam_state_manager.web import port_test_routes as ptroutes
        monkeypatch.setattr(czr, "_env_python", lambda: sys.executable)
        monkeypatch.setattr(ptroutes, "_saas_credentials", lambda: dict(CREDS))
        a.procs = []
        a.result = {"ok": True, "sim_ns": 504, "backend_label": "QM-SaaS cloud simulator",
                    "ports": [{"controller": "con1", "fem": 1, "port": 1, "elements": ["q1.z"],
                               "t0_ns": 0, "dt_ns": 1.0, "volts": [0.0, 0.2, 0.0]}]}
        a.delay = 0.0

        def lab_popen(env):
            def p(argv, **kw):
                assert Path(env["QUAM_STATE_PATH"]).is_dir()
                a.env = env
                a.procs.append(FakeProc(argv, a.result, a.delay))
                return a.procs[-1]
            return p
        monkeypatch.setattr(sim, "lab_popen", lab_popen)
        yield a
        from quam_state_manager.core.hub_index import close_readers
        close_readers()


def _wait(c, rid, window="w1"):
    n = 0
    while n < 100:
        n += 1
        r = c.get(STATUS + f"?id={rid}", headers={"X-SM-Window-ID": window}).get_json()
        if r["state"] not in ("starting", "running"):
            return r
        time.sleep(0.05)
    return r


def test_start_runs_on_a_copy_and_writes_nothing(app):
    live = app.tmp / "chips" / "live"
    wc = app.tmp / "_inst" / "working_state"
    assert _md5_tree(wc) and _md5_tree(live)
    before_live, before_wc = _md5_tree(live), _md5_tree(wc)
    snap = app.snap()
    r = app.c.post(START, json=_body(), headers={"X-SM-Window-ID": "w1"})
    j = r.get_json()
    assert r.status_code == 200 and j["state"] == "starting", j
    job = json.loads(Path(app.procs[0].argv[2]).read_text(encoding="utf-8"))
    assert job["macro"] == "qubit_pairs.q1-2.macros.cz_sq"
    assert job["pad_ns"] == 200 and job["max_ns"] == 20_000
    assert job["folder"] == app.env["QUAM_STATE_PATH"]
    assert Path(job["folder"]).resolve() != live.resolve()
    # the password rides stdin only, never the job file
    assert "s3cret-pw" not in json.dumps(job) and "s3cret-pw" in app.procs[0].lines[0]
    done = _wait(app.c, j["id"])
    assert done["state"] == "done" and done["ports"][0]["elements"] == ["q1.z"]
    assert _md5_tree(live) == before_live and _md5_tree(wc) == before_wc
    assert app.snap() == snap
    n = 0
    while Path(job["folder"]).exists() and n < 50:
        n += 1
        time.sleep(0.1)
    assert not Path(job["folder"]).exists()      # the copy is removed after the run


def test_one_run_per_window_and_stop(app):
    app.delay = 1.5
    h1, h2 = {"X-SM-Window-ID": "w1"}, {"X-SM-Window-ID": "w2"}
    r1 = app.c.post(START, json=_body(), headers=h1).get_json()
    busy = app.c.post(START, json=_body(), headers=h1)
    assert busy.status_code == 409 and busy.get_json()["state"] == "busy"
    other = app.c.post(START, json=_body(), headers=h2)
    assert other.status_code == 200
    s = app.c.post(STOP, json={"id": r1["id"]}, headers=h1).get_json()
    assert s["state"] == "running" and s["phase"] == "stopping"
    assert _wait(app.c, r1["id"])["state"] == "stopped"
    assert _wait(app.c, other.get_json()["id"], "w2")["state"] == "done"


def test_stop_body_goes_through_request_body(app):
    r = app.c.post(STOP, data="[1]", content_type="application/json",
                   headers={"X-SM-Window-ID": "w1"})
    assert r.status_code == 400 and "JSON object" in r.get_json()["error"]


def test_not_configured_and_no_env(app, monkeypatch):
    from quam_state_manager.web import cz_gate_routes as czr
    from quam_state_manager.web import port_test_routes as ptroutes
    monkeypatch.setattr(ptroutes, "_saas_credentials", lambda: None)
    r = app.c.post(START, json=_body())
    assert r.status_code == 400 and r.get_json()["state"] == "not_configured"
    assert "Port test run targets" in r.get_json()["line"]
    monkeypatch.setattr(czr, "_env_python", lambda: None)
    assert app.c.post(START, json=_body()).get_json()["state"] == "no_env"
    assert not app.procs


def test_bad_body_and_unknown_pair(app):
    r = app.c.post(START, data="[1]", content_type="application/json")
    assert r.status_code == 400
    r = app.c.post(START, json=dict(_body(), pair="q9-9"))
    assert r.status_code == 400 and r.get_json()["state"] == "not_run"
    assert not app.procs


def test_failed_run_reports_the_classified_reason(app):
    app.result = {"ok": False, "error_kind": "connect", "error": "Could not reach the QM-SaaS simulator at x."}
    j = app.c.post(START, json=_body(), headers={"X-SM-Window-ID": "w1"}).get_json()
    out = _wait(app.c, j["id"])
    assert out["state"] == "failed"
    assert out["line"] == "Not simulated: Could not reach the QM-SaaS simulator at x."
    assert "ports" not in out


def test_busy_window_refuses_before_making_a_copy(tmp_path):
    m = sim.Manager(tmp_path)
    m.runs("w1").busy = lambda: True

    def no_copy():
        raise AssertionError("a busy window must not copy the chip")
    ans, code = m.start("w1", python=sys.executable, target={"saas": CREDS}, label="L",
                        limit_s=1, make_copy=no_copy, remove_copy=lambda c: None, macro="m",
                        popen_for=lambda folder: None)
    assert code == 409 and ans["state"] == "busy"
