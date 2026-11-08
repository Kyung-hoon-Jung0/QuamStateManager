from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from quam_state_manager.core import project_env
from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app


def _state(f01: float = 5.0e9) -> dict:
    return {
        "qubits": {"qA1": {"id": "qA1", "f_01": f01}},
        "qubit_pairs": {},
        "active_qubit_names": ["qA1"],
    }


def _write_chip(folder: Path, state: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps({"network": {"host": "127.0.0.1"}}),
                                        encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chip"
    _write_chip(live, _state())
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    ctx = next(iter(app.config["contexts"].values()))
    ctx["qualibrate_project"] = "alpha"
    project_env.remember(app.instance_path, "alpha", sys.executable, "confirmed")
    return {"app": app, "client": c, "live": live, "ctx": ctx}


def _live_sig(folder: Path) -> tuple[bytes, bytes, int, int]:
    sb = (folder / "state.json").read_bytes()
    wb = (folder / "wiring.json").read_bytes()
    sm = os.stat(folder / "state.json").st_mtime_ns
    wm = os.stat(folder / "wiring.json").st_mtime_ns
    return sb, wb, sm, wm


@pytest.fixture
def probe(env, tmp_path, monkeypatch):
    """Run the remembered interpreter, with a stdlib-only fake probe script."""
    from quam_state_manager.core import config_generator
    script = tmp_path / "probe.py"
    seen = tmp_path / "seen.json"
    payload = {"schema": "quamload/v1", "ok": True, "stage": None,
               "exc_type": None, "detail": None, "lib_versions": {"quam": "0.6.0"}}

    def configure(result=payload):
        script.write_text(
            "import json, sys\nfrom pathlib import Path\n"
            "p = Path(sys.argv[sys.argv.index('--state') + 1])\n"
            f"Path({str(seen)!r}).write_text(json.dumps({{'path': str(p), "
            "'state': json.loads((p/'state.json').read_text()), "
            "'wiring': json.loads((p/'wiring.json').read_text()), "
            "'files': [x.name for x in p.iterdir()]}))\n"
            f"print({json.dumps(result)!r})\n", encoding="utf-8")
    configure()
    monkeypatch.setattr(config_generator, "_script_path", lambda name: script)
    return configure, seen


def _check(env):
    return env["client"].post("/state/load-check")


def test_load_check_ok(env, probe):
    response = _check(env)
    assert response.status_code == 200
    assert response.json["ok"] is True
    assert response.json["lib_versions"]["quam"] == "0.6.0"


def test_live_untouched(env, probe):
    before = _live_sig(env["live"])
    assert _check(env).status_code == 200
    assert _live_sig(env["live"]) == before


def test_temp_removed(env, probe):
    assert _check(env).status_code == 200
    state_dir = Path(json.loads(probe[1].read_text())["path"])
    assert state_dir.is_absolute()
    assert not state_dir.parent.exists()


def test_working_files_copied(env, probe):
    (env["ctx"]["working_copy"].working_folder / "support.json").write_text("{}")
    assert _check(env).status_code == 200
    assert "support.json" in json.loads(probe[1].read_text())["files"]


def test_unsaved_edits_included_without_clearing_pending(env, probe):
    assert env["client"].post("/field/edit", data={
        "dot_path": "qubits.qA1.f_01", "value": "5.1e9"}).status_code == 200
    store = env["ctx"]["store"]
    pending = list(store.change_log)
    before = _live_sig(env["live"])
    working = store.folder_path / "state.json"
    working_before = working.read_bytes(), working.stat().st_mtime_ns
    assert _check(env).status_code == 200
    assert json.loads(probe[1].read_text())["state"]["qubits"]["qA1"]["f_01"] == 5.1e9
    assert store.change_log == pending and pending
    assert (working.read_bytes(), working.stat().st_mtime_ns) == working_before
    assert _live_sig(env["live"]) == before


def test_load_failure_verbatim_and_links(env, probe):
    detail = "Traceback (most recent call last):\nAttributeError: object has no attribute 'f_01'"
    probe[0]({"schema": "quamload/v1", "ok": False, "stage": "load",
              "exc_type": "AttributeError", "detail": detail, "lib_versions": {}})
    response = _check(env)
    assert response.status_code == 200 and response.json["ok"] is False
    assert response.json["exc_type"] == "AttributeError"
    assert response.json["detail"] == detail
    assert {"path": "qubits.qA1.f_01", "label": "f_01"} in response.json["links"]
    assert not Path(json.loads(probe[1].read_text())["path"]).parent.exists()


def test_timeout_and_cleanup(env, monkeypatch):
    seen = []
    def timeout(args, **kwargs):
        seen.append(Path(args[args.index("--state") + 1]))
        assert kwargs["timeout"] == 120
        raise subprocess.TimeoutExpired(args, 120)
    monkeypatch.setattr(routes_mod.subprocess, "run", timeout)
    response = _check(env)
    assert response.status_code == 408 and response.json["error"] == "timeout"
    assert not seen[0].parent.exists()


def _no_project(env):
    """A chip opened by State Load: no QUAlibrate project, nothing remembered."""
    env["ctx"]["qualibrate_project"] = None
    project_env._save(env["app"].instance_path, {"projects": {}, "last_used": None})


def test_no_project_runs_with_the_selected_env(env, probe):
    from quam_state_manager.core import config_generator
    _no_project(env)
    config_generator.set_selected_env(env["app"].instance_path, sys.executable)
    response = _check(env)
    assert response.status_code == 200 and response.json["ok"] is True
    assert response.json["env"]["source"] == "selected"


def test_no_env_offers_a_chooser_and_does_not_spawn(env, monkeypatch):
    from quam_state_manager.core import config_generator
    _no_project(env)
    monkeypatch.setattr(config_generator, "get_selected_env", lambda inst: None)
    monkeypatch.setattr(config_generator, "discover_envs", lambda refresh=False: [
        {"name": "envA", "python": sys.executable}])
    def forbidden(*args, **kwargs):
        pytest.fail("must not spawn an unchosen interpreter")
    monkeypatch.setattr(routes_mod.subprocess, "run", forbidden)
    response = _check(env)
    assert response.status_code == 409
    assert response.json["error"] == "env_required"
    assert response.json["envs"] == [{"name": "envA", "python": sys.executable}]


def test_no_env_a_pick_runs(env, probe, monkeypatch):
    from quam_state_manager.core import config_generator
    _no_project(env)
    monkeypatch.setattr(config_generator, "get_selected_env", lambda inst: None)
    monkeypatch.setattr(config_generator, "discover_envs", lambda refresh=False: [
        {"name": "envA", "python": sys.executable}])
    response = env["client"].post("/state/load-check", json={"python": sys.executable})
    assert response.status_code == 200 and response.json["ok"] is True
    assert response.json["env"]["source"] == "chosen"


def test_no_env_an_unknown_pick_is_refused(env, monkeypatch, tmp_path):
    from quam_state_manager.core import config_generator
    _no_project(env)
    monkeypatch.setattr(config_generator, "get_selected_env", lambda inst: None)
    monkeypatch.setattr(config_generator, "discover_envs", lambda refresh=False: [])
    rogue = tmp_path / "python.exe"
    rogue.write_text("")
    def forbidden(*args, **kwargs):
        pytest.fail("must not spawn an undiscovered interpreter")
    monkeypatch.setattr(routes_mod.subprocess, "run", forbidden)
    response = env["client"].post("/state/load-check", json={"python": str(rogue)})
    assert response.status_code == 403 and response.json["error"] == "unknown_interpreter"


def test_project_env_wins_and_is_named(env, probe):
    response = _check(env)
    assert response.json["env"]["source"] == "project"


def test_only_remembered_env_is_run(env, probe):
    response = env["client"].post("/state/load-check", json={"python": "Z:/untrusted/python.exe"})
    assert response.status_code == 200 and response.json["ok"]


def test_invalid_probe_schema(env, probe):
    probe[0]({"schema": "other/v1", "ok": True})
    response = _check(env)
    assert response.status_code == 502 and response.json["error"] == "no_output"


def test_named_paths_exist():
    merged = {"q": {"x": 1}, "items": [{"gain": 2}]}
    assert routes_mod._loadcheck_links("KeyError: 'q.x'", merged)[0]["path"] == "q.x"
    assert routes_mod._loadcheck_links("AttributeError: 'gain'", merged)[0]["path"] == "items.0.gain"
    assert routes_mod._loadcheck_links("KeyError: 'absent'", merged) == []


def test_unknown_interpreter_is_refused_without_spawn(env, monkeypatch):
    monkeypatch.setattr(routes_mod, "_interpreter_known", lambda p: False)
    def forbidden(*args, **kwargs):
        pytest.fail("must not spawn an interpreter SM does not know")
    monkeypatch.setattr(routes_mod.subprocess, "run", forbidden)
    response = _check(env)
    assert response.status_code == 403
    assert response.json["error"] == "unknown_interpreter"


def test_message_is_the_exception_line_not_the_traceback():
    tb = ('Traceback (most recent call last):\n  File "x.py", line 1, in f\n'
          '    raise AttributeError(msg)\n    ^^^^^^^^^\n'
          'AttributeError: Failed to load QUAM state.\nUnexpected attribute \'zz\'')
    msg = routes_mod._loadcheck_message(tb, "AttributeError")
    assert msg.startswith("Failed to load QUAM state.") and "File " not in msg


def test_link_follows_the_object_quam_names_not_the_first_same_key():
    merged = {"wiring": {"qubits": {"q1": {"xy": 1}}},
              "qubits": {"q1": {"zz": 1, "xy": 2}}}
    detail = ('Unexpected attribute \'zz\' in Quam.qubits["q1"] (some.Class). '
              "Optional attributes: ['xy']")
    links = routes_mod._loadcheck_links(detail, merged)
    assert links == [{"path": "qubits.q1.zz", "label": "zz"}]
