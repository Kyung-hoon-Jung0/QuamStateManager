"""The Save to quam_config door through Flask: a gate that uses an SM class
waits for the class file, every class-file write leaves one history line,
and Save to file cannot bypass the checked door."""
import base64
import json
import tempfile
from pathlib import Path

import pytest

from quam_state_manager.core import config_generator
from quam_state_manager.core import pulse_class_drafts as D
from quam_state_manager.core import pulse_class_watch as W
from quam_state_manager.core import quam_config_save as Q
from tests.test_cz_gate_routes import PLAN, App, _body

CLASS = "FlatPulse"
QCLASS = f"quam_config.{CLASS}.{CLASS}"


@pytest.fixture
def env(monkeypatch):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        folder = Path(tmp) / "source" / "quam_config"
        folder.mkdir(parents=True)
        (folder / "__init__.py").write_text("")
        monkeypatch.setattr(config_generator, "known_interpreter", lambda *a, **k: True)
        monkeypatch.setattr(config_generator, "get_lab_code_dir", lambda *a: str(folder.parent))
        checks = []

        def runner(python, req, **kwargs):
            if req["action"] == "discover":
                return {"ok": True, "folder": str(folder.resolve()), "site_packages": []}
            checks.append(req)
            data = base64.b64decode(req["source"])
            return {"ok": True, "sha256": D.sha256_bytes(data), "chip_loaded": bool(req.get("chip")),
                    "proportional": {"proportional": True}}
        monkeypatch.setattr(Q, "_run", runner)
        W._WATCHES.clear()
        a = App(tmp)
        r = a.c.post("/pulse-class/new", json={"cls": CLASS})
        assert r.status_code == 200, r.get_json()
        a.folder = folder.resolve()
        a.checks = checks
        yield a
        from quam_state_manager.core.hub_index import close_readers
        close_readers()


def _history(a):
    from quam_state_manager.core import gate_check_log
    folder = gate_check_log.chip_dir(a.app.config["history_manager"], a.ctx["path"])
    p = Path(folder) / Q.HISTORY_FILE
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def _save(a, headers=None):
    first = a.c.post("/pulse-class/save-config", json={"cls": CLASS, "python": "py"},
                     headers=headers or {}).get_json()
    assert first["confirm"]["folder"] == str(a.folder)
    return a.c.post("/pulse-class/save-config", json={"cls": CLASS, "python": "py",
                                                      "confirmed_folder": first["confirm"]["folder"]},
                    headers=headers or {})


def _sm_class_body():
    b = _body()
    b["pulses"]["flux_pulse_qubit"]["class"] = QCLASS
    return b


def test_a_gate_with_an_unsaved_sm_class_is_refused_until_the_class_is_saved(env):
    before = env.snap()
    j = env.c.post(PLAN, json=_sm_class_body()).get_json()
    refusals = " ".join(j["pairs"][0].get("refusals") or [])
    assert f"Save {CLASS} to quam_config" in refusals
    r = _save(env)
    assert r.status_code == 200, r.get_json()
    out = r.get_json()
    assert out["restart"] == Q.RESTART_NEW and "QUAlibrate" in out["restart"]
    assert f"quam_config/{CLASS}.py" in out["dependency"]
    j = env.c.post(PLAN, json=_sm_class_body()).get_json()
    assert f"Save {CLASS}" not in " ".join(j["pairs"][0].get("refusals") or [])
    assert env.snap() == before                     # the save writes no state and no extras


def test_save_to_quam_config_records_who_path_and_hash(env):
    r = _save(env, headers={"X-SM-Agent": "by_claude"})
    assert r.status_code == 200, r.get_json()
    rows = _history(env)
    target = env.folder / f"{CLASS}.py"
    assert len(rows) == 1 and rows[0]["actor"] == "agent"
    assert rows[0]["path"] == str(target) and rows[0]["sha256"] == D.sha256_bytes(target.read_bytes())
    assert D.sha256_bytes(base64.b64decode(env.checks[-1]["source"])) == rows[0]["sha256"]


def test_save_to_file_records_a_history_line_and_cannot_bypass_the_checked_door(env, tmp_path,
                                                                                monkeypatch):
    monkeypatch.setattr(D, "import_check", lambda *a, **k: None)
    outside = tmp_path / "copy.py"
    r = env.c.post("/pulse-class/save-file", json={"cls": CLASS, "target": str(outside)}).get_json()
    assert r["ok"] and r["record_error"] is None
    rows = _history(env)
    assert rows[-1]["actor"] == "person" and rows[-1]["path"] == str(outside)
    assert rows[-1]["sha256"] == D.sha256_bytes(outside.read_bytes())
    package = env.folder / "Other.py"
    r = env.c.post("/pulse-class/save-file", json={"cls": CLASS, "target": str(package)})
    assert r.status_code == 400 and "Save to quam_config" in r.get_json()["error"]
    assert not package.exists()
    assert _save(env).status_code == 200
    published = env.folder / f"{CLASS}.py"
    before = published.read_bytes()
    r = env.c.post("/pulse-class/save-file", json={"cls": CLASS, "target": str(published)})
    assert r.status_code == 400 and "Save to quam_config" in r.get_json()["error"]
    assert published.read_bytes() == before
