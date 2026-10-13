"""Synthetic pins for read-only local server discovery and config mismatch."""
import threading
import time
from pathlib import Path

import pytest

from quam_state_manager.core import qualibrate_running as qr


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
