"""Simulate a node, end to end (P1 piece I): route -> server core -> a real child process -> the page bundle.

Three kinds of child, none of which reaches a cloud simulator:

* a STUB interpreter script that writes ``_status.json`` / ``_result.json`` in the child's format (the
  result's base keys are read from generator/nodesim_click.py, not retyped); the answers the routes give
  are rendered by the page bundle (tests/nodesim_selfcheck.cjs, NODESIM_ANSWERS mode);
* the REAL child (generator/nodesim_click.py) against the fake QM stack of tests/test_nodesim_child.py,
  whose cloud endpoints are loopback listeners owned by the test;
* opt-in only (``SM_NODESIM_SAAS=1``): the real child on the cloud simulator. Never run by default.

Also pinned here: the status read retry (1b), the empty-targets refusal (1c, D-38) and the Simulate entry
points (sidebar tool, Scheduler library row, a node in a subfolder preselected).
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from quam_state_manager.core import nodesim_click as click
from tests.test_nodesim_child import FAKE, NODE, listener, write_tree
from tests.test_nodesim_click import CREDS, Rig, child_writes, ok_result, rig  # noqa: F401 - rig is a fixture
from tests.test_nodesim_routes import App, H, START, STATUS

ROOT = Path(__file__).resolve().parents[1]
GENERATOR_CHILD = ROOT / "quam_state_manager" / "generator" / "nodesim_click.py"
SELFCHECK = ROOT / "tests" / "nodesim_selfcheck.cjs"
NODE_MODULES = os.environ.get("NODE_PATH") or "D:/work_laptop/statemanager-cfb/node_modules"
WAIT_CAP = 300                 # polls of 0.05 s: a stub run ends in about a second
GUARD_LINE = "GUARD FAIL socket.connect 192.0.2.1:80"


def child_base() -> dict:
    """The child's result base dict, read from its source (the ``base = {...}`` literal in main)."""
    tree = ast.parse(GENERATOR_CHILD.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "base" for t in node.targets) \
                and isinstance(node.value, ast.Dict):
            return ast.literal_eval(node.value)
    raise AssertionError("no base dict in the child")


STUB = r'''
import json, os, sys, time
from pathlib import Path

work = Path(sys.argv[1])
conn = json.loads(sys.stdin.readline())
req = json.loads(sys.stdin.readline())
ctl = json.loads(Path(__file__).with_name("stub_control.json").read_text(encoding="utf-8"))
(work / "stub_request.json").write_text(json.dumps({"request": req, "connection_keys": sorted(conn)}),
                                        encoding="utf-8")


sys.path.insert(0, ctl["generator"])
import nodesim_click as child              # the child's own status writer, not a copy of it
writer = child.Status(work)


def status(phase, phases):
    writer.phase, writer.phases = phase, phases
    writer._write()


ran = []
for name in ctl["phases"]:
    status(name, list(ran))
    time.sleep(0.02)
    ran.append({"name": name, "s": 0.02})
if ctl.get("status_after", True):
    status(ran[-1]["name"] if ran else "build", list(ran))
result = dict(ctl["base"])
result.update(ctl["result"])
result.update(phases=ran, instances_opened=ctl["opened"], instances_closed=ctl["opened"])
for line in result.get("guard_lines") or []:
    print(line, flush=True)
(work / "_result.json").write_text(json.dumps(result), encoding="utf-8")
'''

REPORT = {"analog_waveforms": [
    {"waveform_name": "q1.xy.x180.wf", "pulse_name": "x180", "length": 40, "timestamp": 600,
     "iq_info": {"isPartOfIq": False, "iqGroupId": 0, "isI": False, "isQ": False}, "element": "q1.xy",
     "output_ports": [2], "controller": "con1", "fem": 3}],
    "digital_waveforms": [],
    "adc_acquisitions": [{"start_time": 900, "end_time": 1900, "process": "DualDemodIntegration",
                          "quantum_element": "q1.resonator", "adc_ports": [1, 2], "controller": "con1",
                          "fem": 3, "element": "q1.resonator"}],
    "events": []}
PH = ("build", "login", "spawn", "connect", "submit", "wait", "report", "close")
#: kind -> (phases the stub runs, result fields, instances opened, status written after the last phase,
#:          the page state, the verdict tone)
KINDS = {
    "ok": (PH, {"ok": True, "kind": "ok", "report": REPORT, "script": "# script\n", "script_ok": True,
                "program_chars": 9, "qop_version": "v3_7_3", "window_ns": 20000, "last_adc_end_ns": 1900},
           1, True, "done", "ok"),
    "node_error": (("build",), {"kind": "node_error", "message": "KeyError: 'amplitude'"}, 0, False,
                   "node_error", "danger"),
    "serializer": (("build",), {"kind": "serializer", "message": "Error in serializer"}, 0, True,
                   "serializer", "danger"),
    "guard": (("build",), {"kind": "guard", "message": GUARD_LINE, "guard_lines": [GUARD_LINE]}, 0, True,
              "guard", "danger"),
    "too_long": (PH[:5], {"kind": "too_long",
                          "message": "QMConnectionError: Internal Server Error, status: StatusCode.UNKNOWN"},
                 1, True, "too_long", "danger"),
    "timeout": (PH[:6], {"kind": "timeout", "message": "No answer within 0.12 s."}, 1, True,
                "timeout", "danger"),
}


class Procs:
    """Real child processes started by a test; all killed (by PID) at teardown."""

    def __init__(self):
        self.started = []

    def popen(self, argv, **kw):
        proc = click._owned_popen(argv, **kw)
        self.started.append(proc)
        return proc

    def kill_all(self):
        for proc in self.started:
            if proc.poll() is None:
                try:
                    proc.kill()
                    proc.wait(10)
                except Exception:  # noqa: BLE001 - teardown keeps going
                    pass


@pytest.fixture
def procs():
    p = Procs()
    yield p
    p.kill_all()


@pytest.fixture
def stub_app(tmp_path, monkeypatch, procs):
    a = App(tmp_path, monkeypatch)
    stub = tmp_path / "stub" / "stub_child.py"
    stub.parent.mkdir()
    stub.write_text(STUB, encoding="utf-8")
    monkeypatch.setattr(click, "CHILD", stub)
    a.m.popen = procs.popen
    a.stub = stub
    yield a
    procs.kill_all()
    from quam_state_manager.core.hub_index import close_readers
    close_readers()


def run_stub(a, kind, window="w1", window_us=20):
    """One click through the routes; each kind gets its own window length so no answer is a cache hit."""
    phases, fields, opened, after, _state, _tone = KINDS[kind]
    (a.stub.parent / "stub_control.json").write_text(json.dumps(
        {"phases": list(phases), "result": fields, "opened": opened, "status_after": after,
         "base": child_base(), "generator": str(GENERATOR_CHILD.parent)}), encoding="utf-8")
    first = a.c.post(START, json={"node": "11_rabi_demo.py", "targets": ["q1"], "window_us": window_us,
                                  "window": window}, headers={"X-SM-Window-ID": window}).get_json()
    assert first["state"] in ("running", "starting"), first
    n, out = 0, first
    while n < WAIT_CAP and out["state"] in ("running", "starting"):
        n += 1
        time.sleep(0.05)
        out = a.c.get(STATUS + f"?id={first['id']}", headers={"X-SM-Window-ID": window}).get_json()
    return first, out


def render(answers, tmp_path):
    """The page bundle's rendering of each answer (selfcheck NODESIM_ANSWERS mode)."""
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(answers), encoding="utf-8")
    env = {**os.environ, "NODESIM_ANSWERS": str(path), "NODE_PATH": NODE_MODULES}
    got = subprocess.run(["node", str(SELFCHECK)], capture_output=True, text=True, encoding="utf-8",
                         env=env, timeout=120, check=False)
    if got.returncode == 2:
        pytest.skip("jsdom is not installed")
    assert got.returncode == 0, got.stdout + got.stderr
    return [json.loads(x[len("render "):]) for x in got.stdout.splitlines() if x.startswith("render ")]


# ---------------------------------------------------------------- item 4: stub child -> route -> page

def test_stub_child_every_kind_renders_on_the_page(stub_app, tmp_path):
    answers = {}
    for i, kind in enumerate(KINDS):
        first, out = run_stub(stub_app, kind, window=f"w{i}", window_us=20 + i)
        assert out["state"] == KINDS[kind][4], (kind, out)
        req = json.loads((Path(stub_app.app.instance_path) / "nodesim" / f"w{i}" / first["id"]
                          / "stub_request.json").read_text(encoding="utf-8"))
        assert req["connection_keys"] == ["saas"] and req["request"]["saas_host"] == CREDS["host"]
        answers[kind] = out
    ok = answers["ok"]
    assert [p["name"] for p in ok["phases"]] == ["copy", *PH]
    # a node error ends without a last status write; the result's own phase list still names build
    assert [p["name"] for p in answers["node_error"]["phases"]] == ["copy", "build"]
    shown = render(list(answers.values()), tmp_path)
    assert len(shown) == len(KINDS)
    for kind, page in zip(KINDS, shown):
        ans = answers[kind]
        assert page["state"] == ans["state"] and page["tone"] == KINDS[kind][5], (kind, page)
        assert page["shown"] == ans["line"], (kind, page["shown"], ans["line"])
        assert page["phases"] == [p["name"] for p in ans["phases"]], (kind, page["phases"])
    assert shown[0]["lanes"] == 2 and shown[0]["cannot_draw"] is None   # one xy lane and its readout lane
    assert answers["guard"]["line"] == "Stopped by the safety guard: " + GUARD_LINE
    assert answers["too_long"]["line"].startswith("QMConnectionError: Internal Server Error")


# ---------------------------------------------------------------- 1a: the saved host reaches the child

def real_rig(tmp_path, procs, monkeypatch, saas_port):
    rig = Rig(tmp_path)
    lib = tmp_path / "library"
    write_tree(lib, FAKE)
    node = lib / "calibrations" / "11_fake_rabi.py"
    node.parent.mkdir(parents=True, exist_ok=True)
    node.write_text(NODE.format(targets_field="qubits: List[str] = [\"q1\"]", targets_name="qubits",
                                body="    pass"), encoding="utf-8")
    rig.real_lib, rig.real_node = lib, node
    rig.m.popen = procs.popen
    rig.saas = {"email": "user@example.com", "host": "127.0.0.1", "password": "fake-pw-91x",
                "port": saas_port, "version": "v3_7_3"}
    return rig


def real_start(rig, instance_port):
    (rig.real_lib / "fake_control.json").write_text(json.dumps({"instance_port": instance_port}),
                                                    encoding="utf-8")
    info = SimpleNamespace(file=str(rig.real_node), name=rig.real_node.stem, kind="node",
                           targets_name="qubits", error=None)
    a = rig.start(library=rig.real_lib, info=info, targets=["q2"], saas=dict(rig.saas))
    assert a["state"] == "running", a
    return a, rig.wait(a["id"], cap=1200)


def test_real_child_accepts_the_saved_host(tmp_path, procs, monkeypatch):
    with listener() as (login_port, login), listener() as (instance_port, instance):
        rig = real_rig(tmp_path, procs, monkeypatch, login_port)
        a, out = real_start(rig, instance_port)
    assert out["state"] == "done", out
    assert out["line"] != click.first_line("Only the cloud simulator is allowed.")
    assert [p["name"] for p in out["phases"]] == ["copy", *PH]
    assert login["connections"] == 1 and instance["connections"] == 1


def test_request_without_saas_host_is_refused_verbatim_and_opens_nothing(tmp_path, procs, monkeypatch):
    real_launch = click.ClickRuns.launch

    def launch(self, python, connection, request, meta, **kw):   # a server that forgets the host
        return real_launch(self, python, connection, {k: v for k, v in request.items() if k != "saas_host"},
                           meta, **kw)
    monkeypatch.setattr(click.ClickRuns, "launch", launch)
    with listener() as (login_port, login), listener() as (instance_port, instance):
        rig = real_rig(tmp_path, procs, monkeypatch, login_port)
        a, out = real_start(rig, instance_port)
    assert out["state"] == "failed" and out["line"] == "Only the cloud simulator is allowed.", out
    work = tmp_path / "inst" / "nodesim" / "w1" / a["id"]
    result = json.loads((work / "_result.json").read_text(encoding="utf-8"))
    assert result["instances_opened"] == 0 and login["connections"] == 0 and instance["connections"] == 0
    shown = render([out], tmp_path)
    assert shown[0]["shown"] == "Only the cloud simulator is allowed." and shown[0]["tone"] == "danger"


# ---------------------------------------------------------------- 1b: a bad status read never ends the run

def test_status_read_failing_twice_then_good_keeps_the_run(rig, monkeypatch):
    import threading
    gate = threading.Event()

    def behave(proc, work):
        (work / "_status.json").write_text(json.dumps({"phase": "spawn", "phases": [
            {"name": "build", "s": 7.5}, {"name": "login", "s": 1.0}]}), encoding="utf-8")
        gate.wait(10.0)
        (work / "_result.json").write_text(json.dumps(ok_result()), encoding="utf-8")
    rig.behave = behave
    a = rig.start()
    n = 0
    while n < 100 and rig.m.status("w1", a["id"])["phase"] != "spawn":
        n += 1
        time.sleep(0.02)
    real = click._status_bytes
    left = {"fail": 2}

    def flaky(path):
        if left["fail"] > 0:
            left["fail"] -= 1
            raise PermissionError(13, "sharing violation")
        return real(path)
    monkeypatch.setattr(click, "_status_bytes", flaky)
    st = rig.m.status("w1", a["id"])
    assert left["fail"] == 0
    assert st["state"] == "starting" and st["phase"] == "spawn", st
    assert [p["name"] for p in st["phases"]] == ["copy", "build", "login"]
    left["fail"] = 10 ** 6                          # every try of this poll fails: the last good status stays
    st = rig.m.status("w1", a["id"])
    assert st["state"] == "starting" and [p["name"] for p in st["phases"]] == ["copy", "build", "login"]
    monkeypatch.setattr(click, "_status_bytes", real)
    gate.set()
    assert rig.wait(a["id"])["state"] == "done"


def test_bad_json_status_is_retried(tmp_path, monkeypatch):
    path = tmp_path / "_status.json"
    path.write_text("{half", encoding="utf-8")
    seen = []
    monkeypatch.setattr(click.time, "sleep", lambda s: (seen.append(s), path.write_text(
        json.dumps({"phase": "wait", "phases": []}), encoding="utf-8")))
    assert click._read_status(path) == {"phase": "wait", "phases": []} and len(seen) == 1


# ---------------------------------------------------------------- 1c / 2a: server pins

def test_empty_targets_refused_before_any_copy_or_spawn(rig):
    for targets in ([], None, "q1"):
        a = rig.start(targets=targets)
        assert a["state"] == "needs_targets" and a["line"] == click.LINES["needs_targets"]
    assert rig.procs == [] and rig.copies == []


def test_cache_hit_phases_are_exactly_copy_and_cache(rig):
    rig.behave = child_writes(ok_result())
    rig.wait(rig.start()["id"])
    hit = rig.start()
    assert hit["cached"] is True and [p["name"] for p in hit["phases"]] == ["copy", "cache"]
    assert all(isinstance(p["s"], float) for p in hit["phases"])


# ---------------------------------------------------------------- 3: entry points

def test_sidebar_has_the_simulate_tool(app_page):
    html = app_page.c.get("/", follow_redirects=True).get_data(as_text=True)
    assert 'href="/nodesim" hx-get="/nodesim"' in html and ">Simulate a node</span>" in html


@pytest.fixture
def app_page(tmp_path, monkeypatch):
    a = App(tmp_path, monkeypatch)
    sub = a.calib / "sub"
    sub.mkdir()
    text = (a.calib / "11_rabi_demo.py").read_text(encoding="utf-8")
    (sub / "12_ramsey_demo.py").write_text(text.replace("11_rabi_demo", "12_ramsey_demo"), encoding="utf-8")
    a.sub_node = sub / "12_ramsey_demo.py"
    yield a
    from quam_state_manager.core.hub_index import close_readers
    close_readers()


SCHED_JS = r'''
const fs = require('fs');
const { JSDOM } = require('jsdom');
const [js, itemsFile] = process.argv.slice(2);
const items = JSON.parse(fs.readFileSync(itemsFile, 'utf8'));
const dom = new JSDOM('<!doctype html><body><div id="sched-root"></div>'
    + '<input id="sched-cal-folder" value="lib"><button id="sched-scan-btn"></button>'
    + '<span id="sched-scan-info"></span><div id="sched-library"></div></body>',
    { url: 'http://localhost/scheduler', runScripts: 'outside-only' });
const w = dom.window;
w.__items = items;
w.eval("window.fetch = function (url) { var body = String(url).indexOf('/scheduler/scan') === 0 "
    + "? { ok: true, items: window.__items, qubits: [], pairs: [] } : {}; "
    + "return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(body); } }); };");
w.eval(fs.readFileSync(js, 'utf8'));
w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
setTimeout(() => {
    const rows = [...w.document.querySelectorAll('.sched-lib-row')].map((r) => {
        const a = r.querySelector('[data-sched-sim]');
        return { name: r.querySelector('.sched-lib-name').textContent, href: a ? a.getAttribute('href') : null,
                 cls: a ? a.className : null, text: a ? a.textContent : null };
    });
    console.log('rows ' + JSON.stringify(rows));
    process.exit(0);
}, 200);
'''


def test_scheduler_row_simulate_on_a_subfolder_node_preselects_it(app_page, tmp_path):
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    # the Scheduler scans the folder the person typed: here the subfolder
    scan = app_page.c.get("/scheduler/scan?folder=" + str(app_page.sub_node.parent)).get_json()
    items = [i for i in scan["items"] if i["kind"] != "other"]
    assert [Path(i["file"]).name for i in items] == ["12_ramsey_demo.py"]
    items.append({**items[0], "kind": "graph", "name": "a_graph", "file": str(app_page.calib / "g.py")})
    script, data = tmp_path / "sched_check.cjs", tmp_path / "items.json"
    script.write_text(SCHED_JS, encoding="utf-8")
    data.write_text(json.dumps(items), encoding="utf-8")
    got = subprocess.run(["node", str(script), str(ROOT / "quam_state_manager" / "web" / "static" / "scheduler.js"),
                          str(data)], capture_output=True, text=True, encoding="utf-8", timeout=120, check=False,
                         env={**os.environ, "NODE_PATH": NODE_MODULES})
    line = [x for x in got.stdout.splitlines() if x.startswith("rows ")]
    if not line and "Cannot find module 'jsdom'" in got.stderr:
        pytest.skip("jsdom is not installed")
    assert line, got.stdout + got.stderr
    rows = json.loads(line[0][len("rows "):])
    node_row = [r for r in rows if r["name"] == "12_ramsey_demo"][0]
    assert node_row["text"] == "Simulate" and node_row["cls"] == "btn-sm outline"
    assert [r for r in rows if r["name"] == "a_graph"][0]["href"] is None       # a graph is not simulated
    href = node_row["href"]
    assert href.startswith("/nodesim?node=")
    page = app_page.c.get(href).get_data(as_text=True)
    assert 'data-node="sub/12_ramsey_demo.py"' in page
    nodes = app_page.c.get("/api/nodesim/nodes?node=sub/12_ramsey_demo.py").get_json()
    assert "sub/12_ramsey_demo.py" in [i["file"] for i in nodes["items"]]
    assert "sub/12_ramsey_demo.py" not in [i["file"] for i in app_page.c.get("/api/nodesim/nodes").get_json()["items"]]
    started = app_page.c.post(START, json={"node": "sub/12_ramsey_demo.py", "targets": ["q1"], "window_us": 20},
                              headers=H).get_json()
    assert started["state"] in ("running", "starting"), started
    assert app_page.procs and Path(json.loads(app_page.procs[0].lines[1])["node"]) == app_page.sub_node.resolve()
    app_page.wait(started["id"])


@pytest.mark.parametrize("value", ["../outside.py", "sub/../../x.py", "C:/elsewhere/x.py", "x.txt", ""])
def test_node_outside_the_library_is_not_started(app_page, value):
    out = app_page.c.post(START, json={"node": value, "targets": ["q1"], "window_us": 20}, headers=H).get_json()
    assert out["state"] not in ("running", "starting", "done") and app_page.procs == []


# ---------------------------------------------------------------- opt-in: the real cloud simulator

@pytest.mark.skipif(os.environ.get("SM_NODESIM_SAAS") != "1",
                    reason="opt-in: SM_NODESIM_SAAS=1 runs the real child once on the cloud simulator")
def test_real_child_on_the_cloud_simulator(tmp_path, procs):  # pragma: no cover - never in the default run
    """One real click on the cloud simulator: a copy of a test chip, the library named by
    SM_NODESIM_LIBRARY, the node by SM_NODESIM_NODE, the login from the SAAS_* env vars."""
    lib, node = Path(os.environ["SM_NODESIM_LIBRARY"]), Path(os.environ["SM_NODESIM_NODE"])
    saas = {"email": os.environ["SAAS_EMAIL"], "host": os.environ["SAAS_HOST"],
            "password": os.environ["SAAS_PASSWORD"], "version": os.environ.get("SAAS_VERSION", "")}
    python = os.environ.get("SM_NODESIM_PYTHON") or sys.executable
    rig = Rig(tmp_path)
    rig.m = click.Manager(tmp_path / "inst", popen=procs.popen, make_copy=rig.make_copy,
                          remove_copy=rig.remove_copy)
    info = SimpleNamespace(file=str(node), name=node.stem, kind="node", targets_name="qubits", error=None)
    a = rig.start(python=python, library=lib, info=info, targets=["q1"], saas=saas, window_us=10)
    assert a["state"] == "running", a
    out = rig.wait(a["id"], cap=int(click.LIMIT_S / 0.05) + 600)
    assert out["state"] == "done", out
    assert saas["password"] not in json.dumps(out)
