"""Activation queues feature work and excludes non-live contexts."""
import json

import pytest

from quam_state_manager.core import env_seen, state_env_schema, waveform_refs
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


def test_reference_auto_record_uses_existing_single_flight_and_attempt_memo(loaded, monkeypatch):
    from quam_state_manager.core import activity, waveform_refs

    app, _, ctx = loaded
    ctx["env_upgrade_body"] = {"official_digest": "a", "versions": {"quam": "0.6.0"}}
    calls = []

    class Immediate:
        def __init__(self, *, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(routes.threading, "Thread", Immediate)
    monkeypatch.setattr(activity, "wait_quiet", lambda **kwargs: None)
    monkeypatch.setattr(routes.config_generator, "get_selected_env", lambda _: "python")

    def preview(*args, **kwargs):
        calls.append(kwargs)
        return {"ok": False, "error": "timeout"}

    monkeypatch.setattr(waveform_refs, "preview", preview)
    routes._cfg_warm_inflight.clear()
    routes._cfg_warm_failed.clear()
    try:
        routes._cfg_warm_inflight.add("another-state")
        assert routes._warm_generated_config_async(ctx, app.instance_path, reference_only=True) == "busy"
        assert not calls
        routes._cfg_warm_inflight.clear()
        assert routes._warm_generated_config_async(ctx, app.instance_path, reference_only=True) == "started"
        assert calls == [{"timeout": waveform_refs.AUTO_RECORD_SECONDS}]
        assert routes._warm_generated_config_async(ctx, app.instance_path, reference_only=True) == "attempted-before"
        assert len(calls) == 1
        ctx["env_upgrade_body"]["official_digest"] = "b"
        assert routes._warm_generated_config_async(ctx, app.instance_path, reference_only=True) == "started"
        assert len(calls) == 2
    finally:
        routes._cfg_warm_failed.clear()
        routes._cfg_warm_inflight.clear()


def test_reference_auto_record_rejects_unsaved_edits(loaded, monkeypatch):
    app, _, ctx = loaded
    ctx["env_upgrade_body"] = {"official_digest": "a"}
    ctx["store"].change_log.append(object())
    try:
        assert routes._warm_generated_config_async(ctx, app.instance_path, reference_only=True) == "not-recordable"
    finally:
        ctx["store"].change_log.clear()


def _review_context(loaded):
    import sys
    from quam_state_manager.core import env_upgrade, state_env_baseline
    from quam_state_manager.core.loader import QuamStore
    from quam_state_manager.core.modifier import Modifier

    app, client, ctx = loaded
    state = {"objects": {"first": {"__class__": "extension.Child", "old": 2}},
             "ref": "#/objects/first/old"}
    ctx["store"] = QuamStore.from_dicts(state, {"wiring": {"ref": "#/objects/first/old"}})
    ctx["modifier"] = Modifier(ctx["store"])
    before = {"schema_format": 4, "versions": {"quam": "0.6.0"}, "classes": {}}
    for cls, origin, bases in [("official.Parent", "official", []),
                               ("extension.Child", "lab", ["official.Parent"])]:
        before["classes"][cls] = {"importable": True, "origin": origin, "bases": bases,
            "signatures": {}, "fields": {"old": {"type": {"base": "int"}, "has_default": True,
                "defining_class": "official.Parent", "default": 1}}}
    import copy
    current = copy.deepcopy(before)
    for entry in current["classes"].values():
        entry["fields"]["new"] = entry["fields"].pop("old")
    old_key = state_env_baseline.record_baseline(app.instance_path, before, python_path=sys.executable)
    old_body = state_env_baseline.load_baseline(app.instance_path, old_key)
    new_key = state_env_baseline.record_baseline(app.instance_path, current, python_path=sys.executable)
    body = state_env_baseline.load_baseline(app.instance_path, new_key)
    key = env_seen.chip_key(ctx["path"])
    env_seen.observe(app.instance_path, key, sys.executable, old_body, context=ctx)
    event = env_seen.observe(app.instance_path, key, sys.executable, body, context=ctx)
    ctx["env_upgrade_env"] = sys.executable
    ctx["env_upgrade_body"] = body
    ctx["env_upgrade_report"] = env_upgrade.report(state, current, old_body, event=event)
    return app, client, ctx


def test_card_has_scoped_groups_evidence_and_footer(loaded):
    _, client, _ = _review_context(loaded)
    html = client.get("/env-upgrade/card").get_data(as_text=True)
    assert "Checked in:" in html
    assert "Stops loading" in html
    assert "signature differs" in html and "call site is not checked" in html
    assert "samples written to the QM config" in html
    assert "meaning and unit unchanged" in html
    assert "Not checked: run-time behaviour of macros, hardware timing, classes not probed" in html
    assert "Confirm this candidate" in html
    assert "accept-all" not in html.lower()


def test_copy_text_and_acknowledgement_delta_gate(loaded):
    _, client, ctx = _review_context(loaded)
    assert "VERDICT case 3: found" in client.get("/env-upgrade/text").get_data(as_text=True)
    assert "env-upgrade-banner" in client.get("/diagnostics/banner").get_data(as_text=True)
    assert client.post("/env-upgrade/acknowledge", json={}).get_json()["ok"]
    from quam_state_manager.web.env_upgrade_view import model
    from flask import current_app
    app = loaded[0]
    with app.app_context():
        assert not model(ctx, app.instance_path)["report"]["banner"]
        ctx["env_upgrade_report"]["cases"]["3"]["rows"][0]["paths"].append("objects.other.old")
        assert model(ctx, app.instance_path)["report"]["banner"]


def test_rename_needs_test_token_and_changes_working_copy_only(loaded, monkeypatch):
    from pathlib import Path
    app, client, ctx = _review_context(loaded)
    live_before = (Path(ctx["path"]) / "state.json").read_bytes()
    selections = [{"class": "official.Parent", "old": "old", "new": "new",
                   "confirmed": True, "meaning_unchanged": True}]
    payload = {"selections": selections}
    assert client.post("/env-upgrade/rename-apply", json=payload).status_code == 400
    calls = []

    def test_load(python, folder, **kwargs):
        calls.append(kwargs)
        state = json.loads((folder / "state.json").read_text())
        assert state["objects"]["first"]["new"] == 2
        assert state["ref"] == "#/objects/first/new"
        return {"ok": True}

    monkeypatch.setattr(routes.config_generator, "run_config_preview", test_load)
    tested = client.post("/env-upgrade/rename-test", json=payload)
    assert tested.status_code == 200, tested.get_json()
    payload["token"] = tested.get_json()["token"]
    assert calls == [{"strict": True, "load_only": True}]
    applied = client.post("/env-upgrade/rename-apply", json=payload)
    assert applied.status_code == 200, applied.get_json()
    assert ctx["store"].state["objects"]["first"]["new"] == 2
    assert (Path(ctx["path"]) / "state.json").read_bytes() == live_before
    ctx["modifier"].undo_group()
    assert ctx["store"].state["objects"]["first"]["old"] == 2


def test_stale_load_test_cannot_stage_a_different_state(loaded, monkeypatch):
    _, client, ctx = _review_context(loaded)
    payload = {"selections": [{"class": "official.Parent", "old": "old", "new": "new",
                               "confirmed": True, "meaning_unchanged": True}]}
    monkeypatch.setattr(routes.config_generator, "run_config_preview", lambda *a, **k: {"ok": True})
    payload["token"] = client.post("/env-upgrade/rename-test", json=payload).get_json()["token"]
    ctx["store"].state["objects"]["first"]["old"] = 3
    assert client.post("/env-upgrade/rename-apply", json=payload).status_code == 400


def test_archive_card_has_no_rename_controls_or_banner(loaded):
    app, client, ctx = _review_context(loaded)
    ctx["origin"] = "dataset_archive"
    html = client.get("/env-upgrade/card").get_data(as_text=True)
    assert "Confirm this candidate" not in html
    assert "Acknowledge these findings" not in html
    assert client.post("/env-upgrade/rename-test", json={"selections": []}).status_code == 400


def test_feature_wording_and_no_package_walk_pin():
    import ast
    from pathlib import Path
    import re

    root = Path(__file__).resolve().parents[1] / "quam_state_manager"
    sources = [*root.joinpath("web/templates").glob("_env_upgrade*.html"),
               root / "web/static/env-upgrade.js"]
    for path in sources:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"safe|compatible", text, re.I), path
        assert not re.search(r"\b\d+ of \d+ differ", text)
    for name in ("env_seen", "env_upgrade", "env_rename", "waveform_refs"):
        path = root / "core" / f"{name}.py"
        assert "walk_packages" not in path.read_text(encoding="utf-8")


def test_legacy_seen_transition_imports_once_and_new_findings_reappear(loaded):
    from quam_state_manager.core import state_env_baseline
    from quam_state_manager.web.env_upgrade_view import model

    app, _, ctx = _review_context(loaded)
    with app.app_context():
        transition = model(ctx, app.instance_path)["schema_transition"]
        assert state_env_baseline.dismiss_transition(app.instance_path, transition["from_key"],
                                                      transition["to_key"], transition["sig"])
        assert not model(ctx, app.instance_path)["report"]["banner"]
        ctx["env_upgrade_report"]["cases"]["3"]["rows"][0]["paths"].append("objects.additional.old")
        assert model(ctx, app.instance_path)["report"]["banner"]
        record = env_seen.read(app.instance_path, env_seen.chip_key(ctx["path"]))
        assert len(record["acknowledgements"]) == 1
        assert len(record["legacy_ack_imports"]) == 1


def test_schema_detail_lists_exact_omitted_count(loaded):
    from pathlib import Path
    from quam_state_manager.core import state_env_baseline
    from quam_state_manager.web.env_upgrade_view import model

    app, client, ctx = _review_context(loaded)
    key = ctx["env_upgrade_report"]["event"]["previous"]["baseline_key"]
    old = state_env_baseline.load_baseline(app.instance_path, key)
    for entry in old["classes"].values():
        for index in range(30):
            entry["fields"][f"removed{index}"] = {"t": {"base": "int"}, "d": True, "o": False}
    path = Path(app.instance_path) / state_env_baseline.BASELINE_DIRNAME / f"{key}.json"
    path.write_text(json.dumps(old), encoding="utf-8")
    with app.app_context():
        transition = model(ctx, app.instance_path)["schema_transition"]
    assert transition["diff"]["omitted"] > 0
    html = client.get("/env-upgrade/card").get_data(as_text=True)
    assert f"{transition['diff']['omitted']} more not listed" in html


def test_archive_keeps_the_ordinary_config_warm_but_never_records_a_reference(monkeypatch, tmp_path):
    """The docs/189 warm still serves archive runs; only reference recording is live-only."""
    from types import SimpleNamespace
    monkeypatch.setattr(routes.config_generator, "get_selected_env", lambda inst: None)
    store = SimpleNamespace(generated_config=None, change_log=[])
    ctx = {"store": store, "path": str(tmp_path), "origin": "dataset_archive"}
    assert routes._warm_generated_config_async(ctx, str(tmp_path)) == "no-env"
    assert routes._warm_generated_config_async(ctx, str(tmp_path), reference_only=True) == "not-live"


def test_a_failed_observation_names_its_reason_on_the_card_and_logs_a_traceback(loaded, monkeypatch, caplog):
    import logging
    from quam_state_manager.core import activity, config_generator, state_env_baseline

    app, client, ctx = loaded
    queued = []
    app.config.update(SM_ENV_UPGRADE_IN_TESTS=True, SM_ENV_UPGRADE_STARTER=queued.append)
    monkeypatch.setattr(config_generator, "get_selected_env", lambda _: "env-a")
    monkeypatch.setattr(config_generator, "_env_signature", lambda _: "sig-a")
    monkeypatch.setattr(activity, "wait_quiet", lambda **kwargs: None)
    monkeypatch.setattr(routes, "_warm_state_schema_async", lambda *a, **k: None)
    monkeypatch.setattr(state_env_schema, "manifest_for_store",
                        lambda *a, **k: {"classes": {}, "versions": {"quam": "1.0"}})
    monkeypatch.setattr(state_env_baseline, "record_baseline", lambda *a, **k: "key-a")
    monkeypatch.setattr(state_env_baseline, "load_baseline", lambda *a, **k: {"classes": {}})

    def broken(*args, **kwargs):
        raise KeyError("baseline_key")

    monkeypatch.setattr(env_seen, "observe", broken)
    assert client.post("/load", data={"folder": ctx["path"]}).status_code == 302
    with caplog.at_level(logging.WARNING, logger=routes.logger.name):
        queued.pop(0)()
    error = ctx["env_upgrade_error"]
    assert "KeyError" in error and "baseline_key" in error
    records = [r for r in caplog.records if "environment observation failed" in r.getMessage()]
    assert records and records[0].exc_info and records[0].levelno >= logging.ERROR
    html = client.get("/diagnostics").get_data(as_text=True)
    assert "KeyError" in html and "baseline_key" in html
