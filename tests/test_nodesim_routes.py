"""Simulate a node routes (web/nodesim_routes.py): page, scan, start / status /
stop, script, save-script. The click child is faked (tests/test_nodesim_click.py);
the chip is a tmp folder loaded through /load; nothing reaches a cloud simulator."""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import pytest

from quam_state_manager.core import nodesim_click as click
from tests.test_nodesim_click import CREDS, PW, FakeProc, child_writes, ok_result
from quam_state_manager.web import nodesim_routes as _routes_module

REAL_SAAS = _routes_module._saas  # the real D-51 ladder, kept before App patches it

START, STATUS, STOP = "/api/nodesim/start", "/api/nodesim/status", "/api/nodesim/stop"
H = {"X-SM-Window-ID": "w1"}


def _md5_tree(folder: Path) -> dict:
    return {str(p.relative_to(folder)): hashlib.md5(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*.json")) if p.is_file()}


class App:
    def __init__(self, tmp: Path, monkeypatch):
        from quam_state_manager.core import config_generator
        from quam_state_manager.web import nodesim_routes, routes
        from quam_state_manager.web.app import create_app
        monkeypatch.setattr(config_generator, "discover_envs", lambda refresh=False: [])
        self.tmp = tmp
        self.chip = tmp / "chips" / "live"
        self.chip.mkdir(parents=True)
        state = {"qubits": {"q1": {}, "q2": {}}, "qubit_pairs": {"q1-2": {}},
                 "active_qubit_names": ["q2"], "active_qubit_pair_names": ["q1-2"]}
        (self.chip / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (self.chip / "wiring.json").write_text(json.dumps({"network": {"host": "127.0.0.1", "port": 1}}),
                                               encoding="utf-8")
        self.lib = tmp / "lib"
        (self.lib / "quam_config").mkdir(parents=True)
        self.calib = self.lib / "calibrations"
        self.calib.mkdir()
        node = ('"""Rabi demo."""\nfrom qualibrate import QualibrationNode, NodeParameters\n'
                'class Parameters(NodeParameters):\n    qubits: list = []\n'
                'node = QualibrationNode(name="11_rabi_demo", parameters=Parameters())\n')
        (self.calib / "11_rabi_demo.py").write_text(node, encoding="utf-8")
        (self.calib / "15p_outside.py").write_text(node.replace("11_rabi_demo", "15p_outside"),
                                                   encoding="utf-8")
        self.app = create_app(testing=True, instance_path=str(tmp / "_inst"))
        self.c = self.app.test_client()
        monkeypatch.setattr(routes, "_calibrations_folder", lambda ctx=None: str(self.calib))
        self.python = sys.executable
        monkeypatch.setattr(nodesim_routes, "_env", lambda: (self.python, "envx"))
        self.saas = dict(CREDS)
        monkeypatch.setattr(nodesim_routes, "_saas", lambda: self.saas and dict(self.saas))
        self.procs = []
        self.behave = child_writes(ok_result())
        self.m = click.Manager(self.app.instance_path, popen=self.popen, probe=lambda p: "1.2.0")
        self.app.config["nodesim_manager"] = self.m
        r = self.c.post("/load", data={"folder": str(self.chip)})
        assert r.status_code in (200, 302)

    def popen(self, argv, **kw):
        self.procs.append(FakeProc(argv, kw, self.behave))
        return self.procs[-1]

    def wait(self, rid, cap=100):
        n, out = 0, None
        while n < cap:
            n += 1
            out = self.c.get(STATUS + f"?id={rid}", headers=H).get_json()
            if out["state"] not in ("starting", "running"):
                return out
            time.sleep(0.05)
        return out


@pytest.fixture
def app(tmp_path, monkeypatch):
    a = App(tmp_path, monkeypatch)
    yield a
    from quam_state_manager.core.hub_index import close_readers
    close_readers()


def test_page_renders_mount_and_bundle(app):
    r = app.c.get("/nodesim?node=11_rabi_demo.py")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Simulate a node" in html and "data-nodesim" in html
    assert 'data-node="11_rabi_demo.py"' in html and 'data-qubit="q2"' in html
    assert "nodesim.js" in html and "gate-timeline.js" in html
    assert 'data-qubit="q1"' in app.c.get("/nodesim?qubit=q1").get_data(as_text=True)


def test_app_js_routes_nodesim_bundle():
    text = (Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "static" / "app.js"
            ).read_text(encoding="utf-8")
    assert '[/^\/nodesim(\/|$|\?)/, ["nodesim"]]' in text


def test_nodes_answer(app):
    j = app.c.get("/api/nodesim/nodes").get_json()
    rows = {x["file_name"]: x for x in j["items"]}
    assert rows["11_rabi_demo.py"]["refused"] is False
    assert rows["15p_outside.py"]["refused"] is True
    assert rows["15p_outside.py"]["refused_line"] == click.line("refused", reason=rows["15p_outside.py"]["reason"])
    assert rows["11_rabi_demo.py"]["estimates"]["20"]["family"] == "rabi"
    assert j["qubits"] == ["q1", "q2"] and j["pairs"] == ["q1-2"]
    assert j["default_qubit"] == "q2" and j["default_pair"] == "q1-2"
    assert j["windows_us"] == [10, 20, 50, 100] and j["default_window_us"] == 20
    # the fields the page script reads
    assert rows["11_rabi_demo.py"]["file"] == "11_rabi_demo.py"
    assert rows["11_rabi_demo.py"]["estimate_lines"]["20"] == rows["11_rabi_demo.py"]["estimates"]["20"]["line"]
    assert j["active_qubits"] == ["q2"] and j["active_pairs"] == ["q1-2"] and j["selected_qubit"] is None
    assert j["context"]["env"] == "envx" and j["context"]["library"] == str(app.lib.resolve())
    for x in j["items"]:
        assert {"file", "name", "kind", "targets_name", "refused", "estimate_lines"} <= set(x)


def test_start_status_done_on_a_copy_writes_nothing_live(app):
    before = _md5_tree(app.chip)
    r = app.c.post(START, json={"node": "11_rabi_demo.py", "targets": ["q1"], "window_us": 20}, headers=H)
    j = r.get_json()
    assert r.status_code == 200 and j["ok"] is True and j["state"] == "running", j
    d = app.wait(j["id"])
    assert d["state"] == "done" and d["ok"] is True
    prov = d["result"]["provenance"]
    assert prov["line"] == prov["text"] and d["chip_seq"] == prov["seq"]
    assert "report" in d["result"]
    req = json.loads(app.procs[0].lines[1])
    assert Path(req["chip_copy"]).resolve() != app.chip.resolve()
    assert list(json.loads(app.procs[0].lines[0])) == ["saas"]
    assert _md5_tree(app.chip) == before
    s = app.c.get(f"/api/nodesim/script?run={j['id']}").get_json()
    assert s["ok"] and "program()" in s["script"]
    out = app.tmp / "out"
    out.mkdir()
    sv = app.c.post("/api/nodesim/save-script", json={"run": j["id"], "folder": str(out)}).get_json()
    assert sv["ok"] and Path(sv["path"]).parent == out.resolve()
    bad = app.c.post("/api/nodesim/save-script", json={"run": j["id"], "folder": str(app.calib)}).get_json()
    assert bad["ok"] is False
    assert PW not in json.dumps(d)


def test_preconditions_are_200_with_ok_false(app):
    app.saas = None
    j = app.c.post(START, json={"node": "11_rabi_demo.py", "targets": ["q1"], "window_us": 20}, headers=H)
    assert j.status_code == 200 and j.get_json()["ok"] is False and j.get_json()["state"] == "not_configured"
    app.saas = dict(CREDS)
    j = app.c.post(START, json={"node": "15p_outside.py", "targets": ["q1"], "window_us": 20}, headers=H)
    assert j.get_json()["state"] == "refused"
    j = app.c.post(START, json={"node": "11_rabi_demo.py", "targets": [], "window_us": 20}, headers=H)
    assert j.get_json()["state"] == "needs_targets"
    j = app.c.post(START, json={"node": "11_rabi_demo.py", "targets": ["q1"], "window_us": 150}, headers=H)
    assert j.get_json()["state"] == "too_long"
    j = app.c.post(START, json={"node": "../x.py", "targets": ["q1"], "window_us": 20}, headers=H)
    assert j.get_json()["state"] == "refused"
    app.python = None
    j = app.c.post(START, json={"node": "11_rabi_demo.py", "targets": ["q1"], "window_us": 20}, headers=H)
    assert j.get_json()["state"] == "no_env"
    assert app.procs == []


def test_bad_body_is_400(app):
    r = app.c.post(START, data="[1, 2]", content_type="application/json", headers=H)
    assert r.status_code == 400


def test_stop_route(app):
    app.behave = lambda proc, work: proc.stop_seen.wait(5.0)
    j = app.c.post(START, json={"node": "11_rabi_demo.py", "targets": ["q1"], "window_us": 20},
                   headers=H).get_json()
    s = app.c.post(STOP, json={"id": j["id"]}, headers=H).get_json()
    assert s["ok"] is True
    assert app.wait(j["id"])["state"] == "stopped"


def test_status_without_run_is_none(app):
    assert app.c.get(STATUS, headers={"X-SM-Window-ID": "other"}).get_json()["state"] == "none"


def test_no_agent_door():
    text = (Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "nodesim_routes.py"
            ).read_text(encoding="utf-8")
    assert "/api/agent" not in text


@pytest.fixture
def warm_app(app):
    """Drive real HTTP routes with the subprocess fake worker (P2 spec 5, Piece S)."""
    from quam_state_manager.core import nodesim_pool
    from tests.test_nodesim_warm import Rig
    rig = Rig(app.tmp / "warm", nodesim_pool)
    app.m.pool = rig.p
    try:
        yield app, rig
    finally:
        for job in app.m.warm_jobs.values():
            job["stop"].set()
            job["thread"].join(timeout=5)
        rig.close()


def test_p2_settings_and_instances_are_secret_free(warm_app):
    """The settings file and HTTP response carry no credentials (D-11, P2 spec 1)."""
    app, rig = warm_app
    r = app.c.get("/api/nodesim/settings").get_json()
    assert r["keep_warm"] is False and r["label"] == "Keep the cloud simulator warm"
    r = app.c.post("/api/nodesim/settings", json={"keep_warm": True, "password": PW}).get_json()
    assert r["keep_warm"] is True
    assert PW not in rig.p.settings.read_text()
    assert app.c.post("/api/nodesim/settings", json={"keep_warm": "yes"}).status_code == 400
    r = app.c.get("/api/nodesim/instances").get_json()
    assert r["limit"] == 3 and "simulation-auth" not in json.dumps(r)


def test_p2_panel_intent_warms_without_acquire(warm_app):
    """Panel-open intent prepares the worker but does not acquire (D-12, S-8)."""
    app, rig = warm_app
    ans = app.c.post("/api/nodesim/intent", json={}, headers=H).get_json()
    assert ans["state"] in ("warming", "ready")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        ans = app.c.get(STATUS, headers=H).get_json()
        if ans["state"] == "ready":
            break
        time.sleep(.01)
    assert ans["line"] == "Node simulator ready."
    assert not [c for c in rig.broker.calls if c["op"] == "acquire"]


def test_p2_warm_click_cache_and_secret_sweep(warm_app):
    """Warm HTTP answers, run folders and cache are scrubbed (D-11, D-22, S-11)."""
    app, rig = warm_app
    rig.plan["error"] = PW + " token-sentinel-93"
    request = {"node": "11_rabi_demo.py", "targets": ["q1"], "window_us": 20}
    first = app.c.post(START, json=request, headers=H).get_json()
    assert first["id"]
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        ans = app.c.get(STATUS + "?id=" + first["id"], headers=H).get_json()
        if app.m.warm_jobs[first["id"]]["done"]:
            break
        time.sleep(.01)
    assert ans["state"] == "done", ans
    assert PW not in json.dumps(ans) and "token-sentinel-93" not in json.dumps(ans)
    cached = app.c.post(START, json=request, headers=H).get_json()
    assert cached["cached"] and [p["name"] for p in cached["phases"]] == ["copy", "cache"]
    for path in Path(app.m.instance, "nodesim").rglob("*"):
        if path.is_file():
            content = path.read_text(encoding="utf-8")
            assert PW not in content and "token-sentinel-93" not in content


@pytest.mark.parametrize("route", ["intent", "renew", "new-instance", "settings"])
def test_p2_bad_json_is_rejected(warm_app, route):
    """Each new POST route checks its body before side effects (P2 spec 5)."""
    app, rig = warm_app
    assert app.c.post("/api/nodesim/" + route, data="[]", content_type="application/json").status_code == 400


def _warm_click(app):
    """One warm click through the HTTP door, waited to done; returns its id."""
    request = {"node": "11_rabi_demo.py", "targets": ["q1"], "window_us": 20}
    first = app.c.post(START, json=request, headers=H).get_json()
    assert first["id"], first
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and not app.m.warm_jobs[first["id"]]["done"]:
        time.sleep(.01)
    assert app.m.warm_jobs[first["id"]]["done"]
    return first["id"]


def _status_until(app, rid, state, cap=8):
    """Poll the page's status call (it keeps its last click id) until one state shows, capped."""
    deadline, ans = time.monotonic() + cap, None
    while time.monotonic() < deadline:
        ans = app.c.get(STATUS + "?id=" + rid, headers=H).get_json()
        if ans["state"] == state:
            return ans
        time.sleep(.01)
    raise AssertionError("status never showed " + state + ": " + repr(ans))


def test_int1_renew_after_click_shows_with_the_click_id(warm_app, monkeypatch):
    """Review int r1 P2-2: click, Renew; the page's status call with the old click id shows renewing, then ready."""
    import threading
    app, rig = warm_app
    rid = _warm_click(app)
    rig.broker.lease = dict(rig.broker.lease, instance_id="instance-b")
    hold, real = threading.Event(), rig.p.renew

    def held(**kw):
        """Show renewing and hold there until the verdict saw it (no wall-clock settle)."""
        kw["notify"]("renewing")
        assert hold.wait(8)
        return real(**kw)
    monkeypatch.setattr(rig.p, "renew", held)
    assert app.c.post("/api/nodesim/renew", json={}, headers=H).get_json()["ok"]
    assert _status_until(app, rid, "renewing")["state"] == "renewing"
    hold.set()
    _status_until(app, rid, "ready")
    assert next(iter(rig.p.leases.values()))["instance_id"] == "instance-b"


def test_int2_click_started_during_an_intent_keeps_its_own_answer(warm_app, monkeypatch):
    """Review int r2 N-1: an intent that began before a click but ends after it never hides that click's answer."""
    import threading
    app, rig = warm_app
    _warm_click(app)
    renewing, hold, real = threading.Event(), threading.Event(), rig.p.renew

    def held(**kw):
        """Show renewing, then hold until the second click is done (no wall-clock settle)."""
        kw["notify"]("renewing")
        renewing.set()
        assert hold.wait(8)
        return real(**kw)
    monkeypatch.setattr(rig.p, "renew", held)
    assert app.c.post("/api/nodesim/renew", json={}, headers=H).get_json()["ok"]
    assert renewing.wait(8)
    request = {"node": "11_rabi_demo.py", "targets": ["q1"], "window_us": 50}
    second = app.c.post(START, json=request, headers=H).get_json()
    assert second["id"], second
    rid2 = second["id"]
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and not app.m.warm_jobs[rid2]["done"]:
        time.sleep(.01)
    assert app.m.warm_jobs[rid2]["done"]
    hold.set()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and app.m.intent_answers.get(window_key_h())["state"] != "ready":
        time.sleep(.01)
    assert app.m.intent_answers[window_key_h()]["state"] == "ready"
    assert app.m.warm_jobs[rid2]["answer"]["state"] == "done"
    ans = app.c.get(STATUS + "?id=" + rid2, headers=H).get_json()
    assert ans["state"] == "done", ans


def window_key_h():
    """The window key the routes derive from the X-SM-Window-ID header used here."""
    return click.window_key(H["X-SM-Window-ID"])


def _env_login(app, monkeypatch, version=None, saved=None):
    """D-51: fake SAAS_* variables through the real _saas() ladder; optional saved port-test version."""
    for name, value in (("SAAS_EMAIL", CREDS["email"]), ("SAAS_PASSWORD", PW), ("SAAS_HOST", CREDS["host"])):
        monkeypatch.setenv(name, value)
    if version is None:
        monkeypatch.delenv("SAAS_VERSION", raising=False)
    else:
        monkeypatch.setenv("SAAS_VERSION", version)
    if saved is not None:
        folder = Path(app.m.instance, "port_test")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "settings.json").write_text(json.dumps({"saas": {"version": saved}}), encoding="utf-8")
    monkeypatch.setattr(_routes_module, "_saas", REAL_SAAS)


def test_int1_env_login_without_version_refused_before_spawn(warm_app, monkeypatch):
    """D-51: env login, no SAAS_VERSION, nothing saved: click and Renew refuse at the entry, naming SAAS_VERSION."""
    app, rig = warm_app
    _env_login(app, monkeypatch)
    request = {"node": "11_rabi_demo.py", "targets": ["q1"], "window_us": 20}
    ans = app.c.post(START, json=request, headers=H).get_json()
    assert ans["state"] == "spawn_failed", ans
    assert "Set SAAS_VERSION" in ans["line"] and "Settings" not in ans["line"]
    ans = app.c.post("/api/nodesim/renew", json={}, headers=H).get_json()
    assert ans["state"] == "spawn_failed" and "Set SAAS_VERSION" in ans["line"], ans
    ans = app.c.post("/api/nodesim/intent", json={"explicit": True}, headers=H).get_json()
    assert ans["state"] == "spawn_failed", ans
    assert rig.workers == [] and rig.procs == [] and not app.m.warm_jobs
    assert not [c for c in rig.broker.calls if c["op"] == "acquire"]
    assert PW not in json.dumps(ans)


def test_int2_panel_open_without_version_spawns_nothing(warm_app, monkeypatch):
    """Review int r2 N-2 / D-51: version-less env login, panel open, keep-warm off: refused, no worker spawned."""
    app, rig = warm_app
    _env_login(app, monkeypatch)
    assert rig.p.keep_warm is False
    ans = app.c.post("/api/nodesim/intent", json={}, headers=H).get_json()
    assert ans["state"] == "spawn_failed" and "Set SAAS_VERSION" in ans["line"], ans
    assert app.c.get(STATUS, headers=H).get_json()["state"] == "spawn_failed"
    assert rig.workers == [] and rig.procs == [] and not rig.p.workers
    assert PW not in json.dumps(ans)


def test_int1_env_login_version_sources(warm_app, monkeypatch):
    """D-51: SAAS_VERSION wins for an env login, else the saved version; the saved login keeps its own."""
    app, rig = warm_app
    _env_login(app, monkeypatch, version="v3_6_0", saved="v2_4_0")
    with app.app.app_context():
        creds = _routes_module._saas()
    assert (creds["version"], creds["version_source"]) == ("v3_6_0", "env")
    monkeypatch.delenv("SAAS_VERSION")
    with app.app.app_context():
        creds = _routes_module._saas()
    assert (creds["version"], creds["version_source"]) == ("v2_4_0", "env_saved")
    saved = click.login_version(dict(CREDS, version=None), {"SAAS_VERSION": "v3_6_0"}, None)
    assert saved["version"] is None and saved["version_source"] == "saved"


def test_int1_new_instance_drops_the_workers_lease(warm_app):
    """Review S P2-6: New instance goes through pool.drop_instance: the worker drops its lease under its job."""
    app, rig = warm_app
    _warm_click(app)
    worker = next(iter(rig.p.workers.values()))
    assert worker.lease is not None
    assert app.c.post("/api/nodesim/new-instance", json={}, headers=H).get_json()["ok"]
    assert "drop_lease" in rig.ops()
    assert [c["why"] for c in rig.broker.calls if c["op"] == "release"][:1] == ["hash_changed"]


@pytest.mark.parametrize("state", ["running", "busy_chip"])
def test_p3_display_chip_and_shared_busy(app, monkeypatch, state):
    from quam_state_manager.web import routes
    monkeypatch.setattr(routes, "_active_chip_label", lambda: "Demo display")
    facts = app.c.get("/api/nodesim/nodes").get_json()
    assert facts["chip"] == facts["context"]["chip"] == "Demo display"
    app.m.warm_jobs["a"] = dict(store_id="different-window-store", live=app.chip.resolve(), window="w1", done=False,
                               answer=click.answer(state, phase="build", name=app.chip.resolve().name))
    app.m.intent_answers["w2"] = click.answer("ready")
    app.m.intent_started["w2"] = time.monotonic()
    out = app.c.get(STATUS, headers={"X-SM-Window-ID": "w2"}).get_json()
    assert out["state"] == "busy"
    expected = (click.line("busy_chip", name="Demo display") if state == "busy_chip"
                else app.m.warm_jobs["a"]["answer"]["line"])
    assert out["line"] == expected
    assert out["id"] is None
    app.m.warm_jobs.clear()
    app.m._set_intent("w2", click.answer("busy_chip", name="internal"))
    assert app.m.intent_answers["w2"]["state"] == "ready"
    # gl-gxm3: this chip's own folder name shows as its display label; another chip's holder stays named.
    own = app.chip.resolve().name
    monkeypatch.setattr(app.m, "status", lambda *a, **k: click.answer("busy_chip", name=own))
    out = app.c.get(STATUS, headers=H).get_json()
    assert out["line"] == click.line("busy_chip", name="Demo display")
    monkeypatch.setattr(app.m, "status", lambda *a, **k: click.answer("busy_chip", name="other-chip"))
    out = app.c.get(STATUS, headers=H).get_json()
    assert out["line"] == click.line("busy_chip", name="other-chip")
