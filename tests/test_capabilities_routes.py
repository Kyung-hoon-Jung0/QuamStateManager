"""Route tests for the capability endpoint + the build-time capability guard.

Monkeypatch `config_generator.probe_capabilities` (and env selection) with canned
values so no conda env is needed — asserts the report shape and that blockers
refuse / degrades need `ack_degrades`, independently of the stray-JSON `force`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from quam_state_manager.core import config_generator
from quam_state_manager.web.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    return app.test_client()


# a spec that requests a TWPA (via a twpa_pump line) + basic qubit lines
_SPEC = {
    "network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
    "instruments": {"controllers": [{"con": 1, "fems": [{"slot": 1, "fem": "mw"}]}]},
    "qubits": ["q1"], "qubit_pairs": [], "twpas": ["twpaA"], "pair_gate": "",
    "lines": [{"element": "q1", "line": "resonator"},
              {"element": "q1", "line": "drive"},
              {"element": "twpaA", "line": "twpa_pump"}],
}


def _manifest(has_twpa: bool):
    from quam_state_manager.generator.probe_capabilities import CATALOG_IDS
    caps = {cid: {"available": True, "detail": ""} for cid in CATALOG_IDS}
    caps["wire.twpa_lines"]["available"] = has_twpa
    return {"ok": True, "cached": False, "error": None,
            "capabilities": caps, "versions": {"quam_builder": "0.2.0"}}


def _patch(monkeypatch, has_twpa: bool):
    monkeypatch.setattr(config_generator, "get_selected_env", lambda _p: "py")
    monkeypatch.setattr(config_generator, "probe_capabilities",
                        lambda *a, **k: _manifest(has_twpa))


def test_capabilities_endpoint_reports_degrade(client, monkeypatch):
    _patch(monkeypatch, has_twpa=False)
    r = client.post("/generate/capabilities", json={"spec": _SPEC})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] and body["report"]["buildable"] is True
    warn_ids = [w["id"] for w in body["report"]["warnings"]]
    assert warn_ids == ["wire.twpa_lines"]
    assert body["report"]["warnings"][0]["fix"]          # actionable


def test_capabilities_endpoint_all_present(client, monkeypatch):
    _patch(monkeypatch, has_twpa=True)
    r = client.post("/generate/capabilities", json={"spec": _SPEC})
    body = r.get_json()
    assert body["report"]["warnings"] == [] and body["report"]["blockers"] == []


def test_build_degrade_needs_ack(client, monkeypatch, tmp_path):
    _patch(monkeypatch, has_twpa=False)
    # A REAL absolute path. A bare POSIX literal has no drive, so on Windows
    # it fails _ingest_abs_path and the route answers 400 BEFORE the
    # capability gate -- the gate under test then never ran (docs/155 F8').
    r = client.post("/generate/build",
                    json={"spec": _SPEC, "output_path": str(tmp_path / "x")})
    body = r.get_json()
    assert body["ok"] is False and body.get("needs_confirm") is True
    assert body.get("confirm_kind") == "capability"
    assert [w["id"] for w in body["capability_warnings"]] == ["wire.twpa_lines"]


def test_build_proceeds_after_ack(client, monkeypatch, tmp_path):
    _patch(monkeypatch, has_twpa=False)
    called = {}

    def fake_run(python, mode, spec, out_dir, timeout=300):
        called["yes"] = True
        return {"ok": True, "status": "ok", "result": {"warnings": []}, "error": None}

    monkeypatch.setattr(config_generator, "run_generator", fake_run)
    out = tmp_path / "out"
    r = client.post("/generate/build",
                    json={"spec": _SPEC, "output_path": str(out), "ack_degrades": True})
    body = r.get_json()
    assert called.get("yes") is True                     # got past the capability gate
    assert body["ok"] is True


def test_build_blocker_refuses(client, monkeypatch, tmp_path):
    # env missing a CORE cap (build.quam) → hard blocker, no override
    from quam_state_manager.generator.probe_capabilities import CATALOG_IDS
    caps = {cid: {"available": True, "detail": ""} for cid in CATALOG_IDS}
    caps["build.quam"]["available"] = False
    monkeypatch.setattr(config_generator, "get_selected_env", lambda _p: "py")
    monkeypatch.setattr(config_generator, "probe_capabilities",
                        lambda *a, **k: {"ok": True, "capabilities": caps,
                                         "versions": {}, "error": None})
    r = client.post("/generate/build",
                    json={"spec": _SPEC, "output_path": str(tmp_path / "x"),
                          "ack_degrades": True})
    assert r.status_code == 400
    body = r.get_json()
    assert body["ok"] is False
    assert "build.quam" in [b["id"] for b in body["capability_blockers"]]


def test_capabilities_reads_the_wizards_env_from_the_body(client, monkeypatch):
    """R4-TL-5: like /generate/allocate (docs/134), the auto-picked env rides
    in the body -- with no persisted selection the review used to answer 400."""
    seen = []
    monkeypatch.setattr(config_generator, "get_selected_env", lambda _p: None)
    monkeypatch.setattr(config_generator, "known_interpreter", lambda p, *a, **k: p == "picked-py")
    monkeypatch.setattr(config_generator, "probe_capabilities",
                        lambda py, *a, **k: seen.append(py) or _manifest(True))
    r = client.post("/generate/capabilities", json={"spec": _SPEC, "python": "picked-py"})
    assert r.status_code == 200 and seen == ["picked-py"]
    r = client.post("/generate/capabilities", json={"spec": _SPEC, "python": "elsewhere-py"})
    assert r.status_code == 403 and seen == ["picked-py"], "an undiscovered interpreter is never probed"
    r = client.post("/generate/capabilities", json={"spec": _SPEC})
    assert r.status_code == 400, "no body env and none selected: still a 400"


def test_every_wizard_capabilities_call_carries_the_env():
    from pathlib import Path
    import re
    js = (Path(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "static" / "generate.js").read_text(encoding="utf-8")
    calls = [m.start() for m in re.finditer(r'fetch\("/generate/capabilities"', js)]
    assert calls
    for i in calls:
        assert re.search(r"python: (?:state\.env|selectedEnv)", js[i:i + 400]), js[i:i + 200]
