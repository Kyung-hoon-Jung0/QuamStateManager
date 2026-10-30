"""Activation queues feature work and excludes non-live contexts."""
import json

import pytest

from quam_state_manager.core import env_seen, state_env_schema
from quam_state_manager.web.app import create_app
from quam_state_manager.web import routes


@pytest.fixture
def loaded(tmp_path):
    folder = tmp_path / "state"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps({"qubits": {}}), encoding="utf-8")
    (folder / "wiring.json").write_text("{}", encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    client = app.test_client()
    client.post("/load", data={"folder": str(folder)})
    ctx = app.config["contexts"][app.config["active_context"]]
    return app, client, ctx


def test_open_queues_no_synchronous_probe_or_record(loaded, monkeypatch):
    app, client, ctx = loaded
    queued = []

    class Deferred:
        def __init__(self, *, target, **kwargs):
            self.target = target

        def start(self):
            queued.append(self.target)

    def forbidden(*args, **kwargs):
        pytest.fail("activation performed synchronous environment work")

    app.config["SM_ENV_UPGRADE_IN_TESTS"] = True
    monkeypatch.setattr(routes.threading, "Thread", Deferred)
    monkeypatch.setattr(state_env_schema, "probe_state_schema", forbidden)
    monkeypatch.setattr(env_seen, "observe", forbidden)
    routes._env_upgrade_inflight.clear()
    try:
        assert client.post("/load", data={"folder": ctx["path"]}).status_code == 302
        assert any(f.__qualname__.startswith("_maybe_check_env_upgrade") for f in queued)
    finally:
        routes._env_upgrade_inflight.clear()


@pytest.mark.parametrize("context", [{"origin": "dataset_archive"}, {"origin": "run_snapshot"},
    {"readonly": True}, {"live_readonly_hint": True}])
def test_archive_activation_starts_no_feature_work(loaded, monkeypatch, context):
    app, _, ctx = loaded
    app.config["SM_ENV_UPGRADE_IN_TESTS"] = True

    def forbidden(*args, **kwargs):
        pytest.fail("a non-live activation started environment work")

    monkeypatch.setattr(routes.threading, "Thread", forbidden)
    with app.app_context():
        routes._maybe_check_env_upgrade({**ctx, **context}, app.instance_path)


def test_background_observation_runs_after_quiet_gate(loaded, monkeypatch):
    from quam_state_manager.core import activity

    app, _, ctx = loaded
    app.config["SM_ENV_UPGRADE_IN_TESTS"] = True
    calls = []
    queued = []

    class Deferred:
        def __init__(self, *, target, **kwargs):
            self.target = target

        def start(self):
            queued.append(self.target)

    monkeypatch.setattr(routes.threading, "Thread", Deferred)
    monkeypatch.setattr(activity, "wait_quiet", lambda **kwargs: calls.append("quiet"))
    monkeypatch.setattr(routes.config_generator, "get_selected_env", lambda _: "env-a")

    def manifest(*args, **kwargs):
        calls.append("probe")
        return {"classes": {}, "versions": {"quam": "0.6.0"}, "sources": {}}

    monkeypatch.setattr(state_env_schema, "manifest_for_store", manifest)
    with app.app_context():
        routes._maybe_check_env_upgrade(ctx, app.instance_path)
    assert calls == []
    queued[0]()
    assert calls.count("probe") == 1
    assert calls.index("quiet") < calls.index("probe")
    assert ctx["env_upgrade_event"]["first"]
    assert not ctx["env_upgrade_report"]["banner"]
    assert ctx["env_upgrade_report"]["footer"].startswith("Not checked:")


def test_wheel_install_flips_signature_and_open_triggers_transition(loaded, tmp_path, monkeypatch):
    import os
    from pathlib import Path
    import subprocess
    import sys
    import zipfile
    from quam_state_manager.core import activity, config_generator

    venv = tmp_path / "venv"
    created = subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(venv)],
        capture_output=True, text=True, timeout=120)
    assert created.returncode == 0, created.stderr
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    wheel = tmp_path / "quam-99.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("quam-99.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: quam\nVersion: 99.0\n")
        archive.writestr("quam-99.0.dist-info/WHEEL", "Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr("quam-99.0.dist-info/RECORD", "")
    app, client, ctx = loaded
    queued = []
    app.config.update(SM_ENV_UPGRADE_IN_TESTS=True, SM_ENV_UPGRADE_STARTER=queued.append)
    monkeypatch.setattr(config_generator, "get_selected_env", lambda _: str(python))
    monkeypatch.setattr(activity, "wait_quiet", lambda **kwargs: None)
    monkeypatch.setattr(routes, "_warm_state_schema_async", lambda *a, **k: None)
    force_values = []
    probe = state_env_schema.probe_state_schema

    def recording_probe(*args, **kwargs):
        force_values.append(kwargs.get("force", False))
        return probe(*args, **kwargs)

    monkeypatch.setattr(state_env_schema, "probe_state_schema", recording_probe)
    assert client.post("/load", data={"folder": ctx["path"]}).status_code == 302
    queued.pop(0)()
    assert ctx["env_upgrade_event"]["first"]
    signature = config_generator._env_signature(str(python))
    installed = subprocess.run([str(python), "-m", "pip", "install", "--no-deps",
        "--ignore-installed", str(wheel)], capture_output=True, text=True, timeout=120)
    assert installed.returncode == 0, installed.stderr
    assert signature != config_generator._env_signature(str(python))
    assert client.post("/load", data={"folder": ctx["path"]}).status_code == 302
    queued.pop(0)()
    assert ctx["env_upgrade_event"]["changed"]
    assert "quam" in ctx["env_upgrade_event"]["direction"]
    assert "99.0 (upgrade)" in ctx["env_upgrade_event"]["direction"]
    assert force_values and not any(force_values)
