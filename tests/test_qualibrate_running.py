"""Synthetic pins for read-only local server discovery and config mismatch."""
import threading
import time
from pathlib import Path

import pytest

from quam_state_manager.core import qualibrate_running as qr

_real_running_servers = qr.running_servers


def config_file(base, project="alpha", state=None, storage=None):
    base.mkdir(parents=True, exist_ok=True)
    file = base / "server config.toml"
    file.write_text(
        f'[qualibrate]\nproject = "{project}"\nversion = 5\n'
        f'[qualibrate.storage]\nlocation = "{(storage or base / "data").as_posix()}"\n'
        f'[quam]\nstate_path = "{(state or base / "state").as_posix()}"\nversion = 3\n',
        encoding="utf-8")
    return file


@pytest.mark.parametrize("command,expected", [
    (["qualibrate.exe", "start", "--config-path", '"C:/config folder/root.toml"'],
     "C:/config folder/root.toml"),
    ('"C:/env folder/qualibrate.exe" start --config-path="C:/config folder/root.toml"',
     "C:/config folder/root.toml"),
    (["python", "-m", "qualibrate", "start", "--config-path=/tmp/root.toml"], "/tmp/root.toml"),
    (["qualibrate", "start"], None),
    (["qualibrate", "stop", "--config-path", "root.toml"], None),
    (["python", "other.py", "qualibrate", "start", "--config-path", "root.toml"], None),
    (["qualibrate.exe", "start", "--config-path"], None),
])
def test_command_parsing(command, expected):
    assert qr.config_argument(command) == expected


@pytest.mark.parametrize("prefix", [
    ["python", "other.py", "-m"], ["python", "-c", "pass", "-m"],
    ["pytest", "-m"], ["unrelated.exe", "-m"], ["python", "--label", "-m"]])
def test_module_tokens_after_a_script_or_foreign_executable_are_not_a_server(prefix):
    assert qr.config_argument(prefix + ["qualibrate", "start", "--config-path", "config.toml"]) is None


def test_module_server_from_another_environment_with_interpreter_flags():
    assert qr.config_argument(["C:/other-env/python.exe", "-B", "-u", "-m", "qualibrate", "start",
                               "--config-path=config.toml"]) == "config.toml"


def test_access_denied_process_does_not_hide_a_later_server(tmp_path, monkeypatch):
    import psutil
    from types import SimpleNamespace
    file = config_file(tmp_path)
    class Denied:
        @property
        def info(self):
            raise psutil.AccessDenied(1)
    server = SimpleNamespace(info={"pid": 2, "cmdline": ["qualibrate.exe", "start", "--config-path", str(file)]},
                             cwd=lambda: str(tmp_path))
    monkeypatch.setattr(psutil, "process_iter", lambda *a: iter([Denied(), server]))
    result = qr.detect_servers()
    assert result["known"] and [s["pid"] for s in result["servers"]] == [2]


def test_multiple_servers_and_overlay(tmp_path):
    first = config_file(tmp_path / "one")
    second = config_file(tmp_path / "two", "beta")
    overlay = first.parent / "projects" / "alpha" / "config.toml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text('[qualibrate.storage]\nlocation = "/tmp/${#/qualibrate/project}"\n',
                       encoding="utf-8")
    rows = [
        {"pid": 1, "cmdline": ["qualibrate.exe", "start", "--config-path", str(first)]},
        {"pid": 2, "cmdline": f'qualibrate start --config-path="{second.as_posix()}"'},
        {"pid": 3, "cmdline": ["qualibrate", "start", "--config-path", str(first)]},
        {"pid": 4, "cmdline": ["other.exe", "start"]},
    ]
    result = qr.detect_servers(rows)
    assert result["known"] is True
    assert [s["project"] for s in result["servers"]] == ["alpha", "beta"]
    assert result["servers"][0]["storage"].replace("\\", "/").endswith("/tmp/alpha")
    assert result["servers"][0]["state_path"] == str(first.parent / "state")


def test_relative_config_uses_server_cwd(tmp_path):
    file = config_file(tmp_path)
    result = qr.detect_servers([{"cmdline": ["qualibrate", "start", "--config-path", file.name],
                                 "cwd": str(tmp_path)}])
    assert result["servers"][0]["config_path"] == str(file)
    assert qr.detect_servers([{"cmdline": ["qualibrate", "start", "--config-path", file.name]}])["servers"] == []


def test_unavailable_listing_is_unknown(monkeypatch):
    def unavailable():
        raise PermissionError("process listing unavailable")
    monkeypatch.setattr(qr, "_processes", unavailable)
    assert qr.detect_servers() == {"known": False, "servers": []}


def test_platform_fallback(monkeypatch, tmp_path):
    import sys
    file = config_file(tmp_path)
    monkeypatch.setitem(sys.modules, "psutil", None)
    monkeypatch.setattr(qr, "_platform_processes", lambda: [
        {"cmdline": ["qualibrate", "start", "--config-path", str(file)]}])
    result = qr.detect_servers()
    assert [s["project"] for s in result["servers"]] == ["alpha"]


def test_slow_discovery_never_waits_and_cache_is_reused(monkeypatch):
    monkeypatch.setattr(qr, "running_servers", _real_running_servers)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def slow():
        calls.append(1)
        entered.set()
        release.wait(3)
        return {"known": True, "servers": [{"project": "alpha"}]}

    monkeypatch.setattr(qr, "detect_servers", slow)
    monkeypatch.setattr(qr, "_cache", {"checked": float("-inf"), "busy": False,
                                     "known": False, "servers": []})
    try:
        start = time.monotonic()
        assert qr.running_servers() == {"known": False, "servers": []}
        assert time.monotonic() - start < 0.2
        assert entered.wait(1)
        assert qr.running_servers()["known"] is False
    finally:
        release.set()
    for _ in range(100):
        if not qr._cache["busy"]:
            break
        time.sleep(0.01)
    assert qr.running_servers()["servers"] == [{"project": "alpha"}]
    assert len(calls) == 1


def test_bad_config_is_ignored(tmp_path):
    file = tmp_path / "bad.toml"
    for text in ('[broken', '[qualibrate]\nproject = "alpha"\nstorage = 7\n',
                 '[qualibrate]\nproject = "../outside"\n'):
        file.write_text(text, encoding="utf-8")
        assert qr.read_server_config(file) is None


@pytest.mark.parametrize("running,open_chip,expected", [
    ("match", None, 0),
    ("differ", None, 1),
    ("differ", "same", 1),
    ("differ", "other", 0),
    ("none", None, 0),
    ("unknown", None, 0),
])
def test_mismatch_rule(tmp_path, running, open_chip, expected):
    sm = tmp_path / "sm.toml"
    chip = str(tmp_path / "state")
    server = {"config_path": str(sm if running == "match" else tmp_path / "server.toml"),
              "project": "alpha", "storage": str(tmp_path / "data"), "state_path": chip}
    discovery = {"known": running != "unknown", "servers": [] if running == "none" else [server]}
    open_state = chip if open_chip == "same" else str(tmp_path / "other") if open_chip else None
    assert len(qr.config_mismatches(discovery, sm, open_state)) == expected
    assert qr.config_mismatches(discovery, sm, open_state, [server["config_path"]]) == []


@pytest.fixture
def running_app(tmp_path, monkeypatch):
    from quam_state_manager.core import qualibrate_config as qc
    from quam_state_manager.web.app import create_app

    state = tmp_path / "state"
    state.mkdir()
    (state / "state.json").write_text('{"qubits": {}, "extras": {}}', encoding="utf-8")
    (state / "wiring.json").write_text('{}', encoding="utf-8")
    sm = config_file(tmp_path / "sm", "beta", state)
    server = config_file(tmp_path / "server", "alpha", state)
    overlay = sm.parent / "projects" / "beta" / "config.toml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text('', encoding="utf-8")
    monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(sm))
    rows = [{"pid": 1, "cmdline": ["qualibrate", "start", "--config-path", str(server)]}]
    monkeypatch.setattr(qr, "running_servers", lambda: qr.detect_servers(rows))
    # Route rendering pins use a ready synthetic snapshot; async latency has its own pin.
    monkeypatch.setattr(qr, "sm_location", qr._read_sm_location)
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    yield app, sm, server, state
    qc.set_dir_override(None)


def test_banner_route_and_existing_adoption(running_app):
    import json
    from quam_state_manager.core import qualibrate_config as qc

    app, sm, server, _ = running_app
    client = app.test_client()
    response = client.get("/qualibrate/running-banner")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "The running QUAlibrate uses" in html
    assert "Use this config" in html and "Dismiss" in html
    assert "alpha" in html and "SM reads" in html
    assert str(sm) in html and str(server) in html
    import re
    visible = re.sub(r"<[^>]+>", "", html)
    assert visible.isascii() and "--" not in visible
    assert 'hx-post="/qualibrate/use-location"' in html
    assert "The running QUAlibrate uses" in client.get("/").get_data(as_text=True)
    assert "The running QUAlibrate uses" in client.get("/qualibrate", headers={"HX-Request": "true"}).get_data(as_text=True)
    response = client.post("/qualibrate/use-location", data={"path": str(server)},
                           headers={"HX-Request": "true"})
    assert response.status_code == 200 and response.headers["HX-Refresh"] == "true"
    assert qc.root_config_path() == server
    assert "The running QUAlibrate uses" not in client.get("/qualibrate/running-banner").get_data(as_text=True)
    memo = json.loads((Path(app.instance_path) / "qualibrate_location.json").read_text())
    assert memo["config_file"] == str(server)


def test_dismissal_survives_restart_per_config(running_app):
    from quam_state_manager.web.app import create_app

    app, _, server, _ = running_app
    client = app.test_client()
    response = client.post("/qualibrate/dismiss-running", data={"path": str(server)},
                           headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "The running QUAlibrate uses" not in client.get("/qualibrate/running-banner").get_data(as_text=True)
    restarted = create_app(testing=True, instance_path=app.instance_path)
    assert "The running QUAlibrate uses" not in restarted.test_client().get("/qualibrate/running-banner").get_data(as_text=True)
    other = server.with_name("another.toml")
    other.write_bytes(server.read_bytes())
    from unittest.mock import patch
    with patch.object(qr, "running_servers", lambda: {"known": True, "servers": [qr.read_server_config(other)]}):
        assert "The running QUAlibrate uses" in client.get("/qualibrate/running-banner").get_data(as_text=True)


def test_bad_running_config_never_500(running_app):
    app, _, server, _ = running_app
    client = app.test_client()
    for text in ('[broken', '[qualibrate]\nproject = 7\n',
                 '[qualibrate]\nproject = "alpha"\nstorage = 7\n'):
        server.write_text(text, encoding="utf-8")
        response = client.get("/qualibrate/running-banner")
        assert response.status_code == 200
        assert "The running QUAlibrate uses" not in response.get_data(as_text=True)


def test_config_files_remain_byte_and_mtime_identical(running_app):
    app, sm, server, _ = running_app
    files = []
    for root in (sm, server):
        overlay = root.parent / "projects" / "alpha" / "config.toml"
        overlay.parent.mkdir(parents=True)
        overlay.write_text('# A read-only overlay\n', encoding="utf-8")
        files.extend([root, overlay])
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}
    client = app.test_client()
    for url in ("/", "/qualibrate", "/qualibrate/running-banner"):
        assert client.get(url).status_code == 200
    assert client.post("/qualibrate/dismiss-running", data={"path": str(server)}).status_code == 302
    assert client.post("/qualibrate/use-location", data={"path": str(server)}).status_code == 302
    assert client.get("/qualibrate/running-banner").status_code == 200
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files} == before


@pytest.mark.parametrize("known,chip,folder,added,expected", [
    (True, "same", "different", False, 1),
    (True, "same", "same", False, 0),
    (True, "other", "different", False, 0),
    (True, None, "different", False, 0),
    (False, "same", "different", False, 0),
    (True, "same", None, False, 0),
    (True, "same", "different", True, 0),
])
def test_dataset_hint_rule(tmp_path, known, chip, folder, added, expected):
    state, data = str(tmp_path / "state"), str(tmp_path / "data")
    server = {"state_path": state, "storage": data}
    open_state = state if chip == "same" else str(tmp_path / "other") if chip else None
    folders = [data if folder == "same" else str(tmp_path / "elsewhere")] if folder else []
    assert len(qr.dataset_hints({"known": known, "servers": [server, server]},
                                open_state, folders, [data] if added else [])) == expected
    assert qr.dataset_hints({"known": True, "servers": []}, open_state, folders) == []


@pytest.mark.parametrize("declared", [False, True])
def test_datasets_hint_adds_running_folder_read_only(running_app, declared):
    import json
    app, sm, server, state = running_app
    data = server.parent / "data"
    data.mkdir()
    if declared:
        (state / "state.json").write_text(json.dumps({"qubits": {}, "extras": {
            "data_folder": str(sm.parent / "data")}}), encoding="utf-8")
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns)
              for root in (sm.parent, server.parent) for p in root.rglob("*.toml")}
    client = app.test_client()
    assert client.post("/load", data={"folder": str(state)}).status_code in (200, 302)
    response = client.get("/datasets")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert f"Add {data} (the running QUAlibrate&#39;s data folder)" in html or f"Add {data} (the running QUAlibrate's data folder)" in html
    assert 'hx-get="/qualibrate/running-data-hint"' in html
    hint = client.get("/qualibrate/running-data-hint").get_data(as_text=True)
    assert 'hx-post="/workspace/add" hx-target="#sidebar-tree"' in hint
    assert 'name="folder"' in hint
    assert client.post("/workspace/add", data={"folder": str(data)}, headers={"HX-Request": "true"}).status_code == 200
    assert qr.path_key(data) in {qr.path_key(root) for root in app.config["workspace"].root_folders}
    assert "the running QUAlibrate's data folder" not in client.get("/qualibrate/running-data-hint").get_data(as_text=True)
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before} == before


def test_dataset_hint_no_chip_and_same_folder(running_app):
    import json
    app, _, server, state = running_app
    client = app.test_client()
    assert "name=\"folder\"" not in client.get("/qualibrate/running-data-hint").get_data(as_text=True)
    data = server.parent / "data"
    (state / "state.json").write_text(json.dumps({"qubits": {}, "extras": {
        "data_folder": str(data)}}), encoding="utf-8")
    client.post("/load", data={"folder": str(state)})
    assert "name=\"folder\"" not in client.get("/qualibrate/running-data-hint").get_data(as_text=True)


@pytest.mark.parametrize("context", [{"origin": "dataset_archive"}, {"type": "experiment"}])
def test_dataset_hint_without_live_store(running_app, monkeypatch, context):
    from quam_state_manager.web import routes
    from types import SimpleNamespace
    app, sm, _, state = running_app
    app.config["PROPAGATE_EXCEPTIONS"] = False
    if context.get("origin") == "dataset_archive":
        context = dict(context, live_path=str(state), store=SimpleNamespace(
            _lock=threading.RLock(), state={"extras": {"data_folder": str(sm.parent / "data")}}))
    monkeypatch.setattr(routes, "_active_ctx", lambda: context)
    response = app.test_client().get("/qualibrate/running-data-hint")
    assert response.status_code == 200
    assert 'name="folder"' not in response.get_data(as_text=True)


def test_local_config_resolution_never_blocks_banner_requests(tmp_path, monkeypatch):
    from quam_state_manager.web.app import create_app
    from quam_state_manager.web import routes
    from types import SimpleNamespace

    entered, release = threading.Event(), threading.Event()
    calls = []
    request_thread = threading.get_ident()
    sm, server, state, data = (str(tmp_path / name) for name in ("sm.toml", "server.toml", "state", "data"))
    cache = {"checked": float("-inf"), "busy": False, "key": None, "value": None}
    monkeypatch.setattr(qr, "_location_cache", cache)
    monkeypatch.setattr(qr, "running_servers", lambda: {"known": True, "servers": [
        {"config_path": server, "project": "alpha", "state_path": state, "storage": data}]})
    monkeypatch.setattr(routes, "_active_ctx", lambda: {
        "live_path": state, "qualibrate_project": "alpha", "store": SimpleNamespace(
            _lock=threading.RLock(), state={"extras": {}})})

    def slow(project):
        calls.append(threading.get_ident())
        entered.set()
        release.wait(3)
        return {"config_path": sm, "storage": str(tmp_path / "other")}

    monkeypatch.setattr(qr, "_read_sm_location", slow)
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    client = app.test_client()
    try:
        for endpoint in ("/qualibrate/running-banner", "/qualibrate/running-data-hint"):
            start = time.monotonic()
            response = client.get(endpoint)
            elapsed = time.monotonic() - start
            assert response.status_code == 200
            assert elapsed < .2, "Config hints must not wait on filesystem reads"
        assert entered.wait(1)
        assert len(calls) == 1 and calls[0] != request_thread
    finally:
        release.set()
        for _ in range(100):
            if not cache["busy"]:
                break
            time.sleep(.01)
    assert "The running QUAlibrate uses" in client.get("/qualibrate/running-banner").get_data(as_text=True)
    assert 'name="folder"' in client.get("/qualibrate/running-data-hint").get_data(as_text=True)
    monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(tmp_path / "changed.toml"))
    assert qr.sm_location("alpha") is None, "An old config snapshot must not survive a location change"
    for _ in range(100):
        if not cache["busy"]:
            break
        time.sleep(.01)
