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
    assert rows["15p_outside.py"]["refused_line"] == click.REFUSED_LIST_LINE
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
