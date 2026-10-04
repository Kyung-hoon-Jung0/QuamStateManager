"""Read-only figure delivery and the chip's run failures and effective mode (docs/274)."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from quam_state_manager import mcp
from quam_state_manager.core import agent_plans
from quam_state_manager.core.dataset import DatasetStore
from quam_state_manager.web import agent_api
from quam_state_manager.web.app import create_app
from tests.test_story import _run_folder

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
)
NOW = datetime(2026, 10, 4, 12).timestamp()


@pytest.fixture
def figures(tmp_path, monkeypatch):
    folder = _run_folder(tmp_path / "data", 101, "node", "09:00:00", qubits=["qA1"])
    (folder / "fig.png").write_bytes(PNG)
    ds = DatasetStore(tmp_path / "data")
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    monkeypatch.setattr(agent_api, "_ds", lambda: ds)
    return app.test_client(), ds, folder


@pytest.mark.parametrize("selector", [{"name": "figures.fig"}, {"index": 0}])
def test_figure_returns_png_by_name_or_index(figures, selector):
    client, _, _ = figures
    response = client.get("/api/agent/run/101/figure", query_string=selector)
    assert response.status_code == 200
    image = response.get_json()["image"]
    assert set(image) == {"type", "data", "mimeType"}
    assert image["type"] == "image" and image["mimeType"] == "image/png"
    assert base64.b64decode(image["data"]) == PNG


def test_listing_explains_how_to_fetch_figures(figures):
    client, _, _ = figures
    run = client.get("/api/agent/run/101").get_json()["run"]
    assert run["figures"][0]["name"] == "figures.fig" and run["figures"][0]["index"] == 0
    assert "run_figure" in run["figure_help"] and "zero-based index" in run["figure_help"]
    assert "run_figure" in mcp.TOOLS["run"][0]["description"]
    assert "yourself" not in mcp.TOOLS["run"][0]["description"]


@pytest.mark.parametrize("name", ["../fig.png", "/fig.png", "C:\\fig.png", "..\\fig.png"])
def test_path_selectors_are_refused_even_if_declared(figures, monkeypatch, name):
    client, ds, _ = figures
    run = ds.get_run(101)
    run["figure_names"] = [name]
    monkeypatch.setattr(ds, "get_run", lambda _: run)
    monkeypatch.setattr(ds, "get_figure_path", lambda *_: pytest.fail("path selector reached resolution"))
    response = client.get("/api/agent/run/101/figure", query_string={"name": name})
    assert response.status_code == 400
    assert "path" in response.get_json()["error"]


def test_resolved_file_must_stay_in_run_folder(figures, tmp_path, monkeypatch):
    client, ds, folder = figures
    outside = folder.with_name(folder.name + "-other")
    outside.mkdir()
    image = outside / "fig.png"
    image.write_bytes(PNG)
    monkeypatch.setattr(ds, "get_figure_path", lambda *_: image)
    assert client.get("/api/agent/run/101/figure?index=0").status_code == 403


def test_unknown_run_is_refused(figures):
    client, _, _ = figures
    response = client.get("/api/agent/run/999/figure?index=0")
    assert response.status_code == 404 and "unknown run" in response.get_json()["error"]


@pytest.mark.parametrize("selector, status", [({"name": "other"}, 404), ({"index": -1}, 404),
                                            ({"index": 1}, 404), ({"index": "bad"}, 400),
                                            ({}, 400), ({"name": "figures.fig", "index": 0}, 400)])
def test_unknown_or_ambiguous_figure_is_refused(figures, selector, status):
    client, _, _ = figures
    response = client.get("/api/agent/run/101/figure", query_string=selector)
    assert response.status_code == status
    if selector == {"name": "other"}:
        assert response.get_json()["error"] == "unknown figure name"


def test_size_cap_refuses_before_read(figures, monkeypatch):
    client, _, folder = figures
    (folder / "fig.png").write_bytes(PNG + b"0" * agent_api._FIGURE_MAX_BYTES)
    original = Path.open

    def guarded(path, *args, **kwargs):
        if path.name == "fig.png":
            pytest.fail("oversized image was opened")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    response = client.get("/api/agent/run/101/figure?index=0")
    assert response.status_code == 413 and "2 MB" in response.get_json()["error"]


def test_size_cap_also_bounds_a_growing_file(figures, monkeypatch):
    client, _, _ = figures
    original = Path.open

    class Growing:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self, limit):
            assert limit == agent_api._FIGURE_MAX_BYTES + 1
            return PNG + b"0" * (limit - len(PNG))

    monkeypatch.setattr(Path, "open", lambda path, *args, **kwargs:
                        Growing() if path.name == "fig.png" else original(path, *args, **kwargs))
    assert client.get("/api/agent/run/101/figure?index=0").status_code == 413


def test_non_image_content_is_refused(figures):
    client, _, folder = figures
    (folder / "fig.png").write_text("plain text", encoding="ascii")
    assert client.get("/api/agent/run/101/figure?index=0").status_code == 415


def test_non_png_file_is_refused(figures, monkeypatch):
    client, ds, folder = figures
    other = folder / "image.txt"
    other.write_bytes(PNG)
    monkeypatch.setattr(ds, "get_figure_path", lambda *_: other)
    assert client.get("/api/agent/run/101/figure?index=0").status_code == 415


def test_missing_image_is_refused(figures):
    client, _, folder = figures
    (folder / "fig.png").unlink()
    assert client.get("/api/agent/run/101/figure?index=0").status_code == 404


def test_mcp_delivers_actual_image_content_in_readonly_mode(figures, monkeypatch):
    client, _, _ = figures

    class Link:
        def alive(self):
            return True

        def get(self, path, params=None):
            response = client.get(path, query_string=params)
            return response.status_code, response.get_json()

    monkeypatch.setattr(mcp, "_link", Link())
    monkeypatch.setattr(mcp, "_CHIP_PIN", None)
    monkeypatch.setattr(mcp, "READ_ONLY", True)
    replies = []
    monkeypatch.setattr(mcp, "_respond", lambda mid, result: replies.append(result))
    mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "run_figure", "arguments": {"run_id": 101, "index": 0}}})
    assert replies == [{"content": [{"type": "image", "mimeType": "image/png",
                                     "data": base64.b64encode(PNG).decode("ascii")}], "isError": False}]


def test_readonly_environment_lists_figure_tool():
    proc = subprocess.run([sys.executable, "-m", "quam_state_manager.mcp"],
                          input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n",
                          env={**os.environ, "SM_MCP_MODE": "readonly", "PYTHONUTF8": "1"},
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    names = {t["name"] for t in json.loads(proc.stdout)["result"]["tools"]}
    assert "run_figure" in names and "run_node" not in names


@pytest.fixture
def pill(tmp_path, monkeypatch):
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    monkeypatch.setattr(agent_api, "_chip_key", lambda: "chipX")
    monkeypatch.setattr(agent_api, "_clock_now", lambda: NOW)
    monkeypatch.setattr(agent_api, "_clock_today", lambda: datetime.fromtimestamp(NOW))
    monkeypatch.setattr(agent_api, "_reconcile_grant", lambda: None)
    monkeypatch.setattr(agent_api, "_mode_and_limits", lambda: {"mode": "ask-writes"})
    monkeypatch.setattr(agent_api, "_waiting_count", lambda: 0)
    monkeypatch.setattr(agent_api, "_human_ran_recently", lambda *_: None)
    return app, app.test_client()


def _record(app, key="run", *, chip="chipX", classification="node_error", ended=NOW - 30, status="failed"):
    record = {"key": key, "chip": chip, "node": "node", "since": ended - 60, "ended": ended,
              "result": {"status": status, "classification": classification}}
    path = Path(app.instance_path) / "agent_runs" / key / "meta.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="ascii")
    return record


def _now(client):
    return client.get("/api/agent/now").get_json()


@pytest.mark.parametrize("classification", ["host_unreachable", "hardware_contention", "timeout",
                                             "node_error", "interrupted"])
def test_each_failed_node_run_class_counts_without_hooks(pill, classification):
    app, client = pill
    _record(app, classification=classification)
    state = _now(client)
    assert state["failures_today"] == 1 and state["state"] == "failed"


def test_tool_errors_do_not_count(pill, monkeypatch):
    _, client = pill
    monkeypatch.setattr(agent_api, "_events", lambda: [
        {"session_id": "session", "ts": NOW - 1, "hook_event_name": "PostToolUseFailure",
         "tool_name": "mcp__sm__state_get", "tool_use_id": "tool", "failed": True, "error": "HTTP 404"}])
    state = _now(client)
    assert state["failures_today"] == 0 and state["state"] != "failed"


def test_failure_count_follows_open_chip(pill, monkeypatch):
    app, client = pill
    _record(app)
    _record(app, "other", chip="chipY")
    _record(app, "unknown", chip=None)
    assert _now(client)["failures_today"] == 1
    monkeypatch.setattr(agent_api, "_chip_key", lambda: "chipZ")
    state = _now(client)
    assert state["failures_today"] == 0 and state["state"] == "idle"


def test_failure_day_uses_completion_not_start_and_excludes_other_days(pill):
    app, client = pill
    midnight = datetime.fromtimestamp(NOW).replace(hour=0).timestamp()
    record = _record(app, "overnight", ended=midnight)
    record["since"] = midnight - 3600
    app.config["agent_run_registry"] = SimpleNamespace(runs={"overnight": record})
    assert _now(client)["failures_today"] == 1
    _record(app, "yesterday", ended=midnight - 1)
    assert _now(client)["failures_today"] == 1
    _record(app, "tomorrow", ended=(datetime.fromtimestamp(midnight) + timedelta(days=1)).timestamp())
    assert _now(client)["failures_today"] == 1


@pytest.mark.parametrize("classification, status", [("ok", "done"), ("unattributed", "done"),
                                                    ("node_error", "refused"), (None, "running")])
def test_successful_or_refused_calls_are_not_failed_runs(pill, classification, status):
    app, client = pill
    _record(app, classification=classification, status=status)
    assert _now(client)["failures_today"] == 0


def test_persisted_runs_are_not_limited_to_recent_cards_or_double_counted(pill):
    app, client = pill
    for index in range(55):
        record = _record(app, f"run{index}")
    app.config["agent_run_registry"] = SimpleNamespace(runs={record["key"]: record})
    assert _now(client)["failures_today"] == 55


def test_tool_activity_does_not_make_an_old_run_failure_recent(pill, monkeypatch):
    app, client = pill
    _record(app, ended=NOW - 7200)
    monkeypatch.setattr(agent_api, "_events", lambda: [
        {"session_id": "session", "ts": NOW - 1, "hook_event_name": "PostToolUse",
         "tool_name": "mcp__sm__state_get", "tool_use_id": "tool"}])
    state = _now(client)
    assert state["failures_today"] == 1 and state["state"] != "failed"


@pytest.mark.parametrize("armed", [True, False])
def test_mode_is_armed_plan_mode_else_chip_limit(pill, monkeypatch, armed):
    _, client = pill
    monkeypatch.setattr(agent_api, "_session", lambda: {
        "plan_id": "plan", "start_token": "token" if armed else None, "mode": "auto"})
    monkeypatch.setattr(agent_plans, "get", lambda *_: {"mode": "ask-all", "status": "running"})
    assert _now(client)["mode"] == ("ask-all" if armed else "ask-writes")


def test_a_plan_that_is_not_running_does_not_set_the_mode(pill, monkeypatch):
    """The run's own rule (agent_runs.run_mode): only a running/stopping plan's mode
    governs; otherwise the armed session's mode."""
    _, client = pill
    monkeypatch.setattr(agent_api, "_session", lambda: {
        "plan_id": "plan", "start_token": "token", "mode": "auto"})
    monkeypatch.setattr(agent_plans, "get", lambda *_: {"mode": "ask-all", "status": "done"})
    assert _now(client)["mode"] == "auto"


def test_armed_session_mode_is_fallback_when_plan_mode_is_absent(pill, monkeypatch):
    _, client = pill
    monkeypatch.setattr(agent_api, "_session", lambda: {
        "plan_id": "plan", "start_token": "token", "mode": "ask-all"})
    monkeypatch.setattr(agent_plans, "get", lambda *_: {"status": "running"})
    assert _now(client)["mode"] == "ask-all"


def _shell_failure(ts, summary="python 05_power_rabi.py", **extra):
    return {"session_id": "session", "ts": ts, "hook_event_name": "PostToolUseFailure",
            "tool_name": "Bash", "tool_use_id": f"t{ts}", "failed": True, "error": "KeyError",
            "summary": summary, **extra}


def test_a_node_a_terminal_agent_ran_and_failed_counts(pill, monkeypatch):
    """A node run through the agent's shell that failed is a failed node run too
    (the journal writes it as one); a failed shell command that ran no node is not."""
    _, client = pill
    monkeypatch.setattr(agent_api, "_events", lambda: [
        _shell_failure(NOW - 10), _shell_failure(NOW - 5, summary="ls -la")])
    state = _now(client)
    assert state["failures_today"] == 1 and state["state"] == "failed"


def test_a_terminal_node_failure_on_another_chip_does_not_count(pill, monkeypatch):
    _, client = pill
    monkeypatch.setattr(agent_api, "_events", lambda: [
        _shell_failure(NOW - 10, quam_state_path="D:/elsewhere/OtherChip/quam_state")])
    assert _now(client)["failures_today"] == 0


def test_a_terminal_node_failure_yesterday_does_not_count(pill, monkeypatch):
    _, client = pill
    midnight = datetime.fromtimestamp(NOW).replace(hour=0).timestamp()
    monkeypatch.setattr(agent_api, "_events", lambda: [_shell_failure(midnight - 1)])
    assert _now(client)["failures_today"] == 0


def test_the_persisted_scan_is_reused_until_a_run_is_added(pill, monkeypatch):
    """The pill polls: the meta scan runs once per change of the runs folder."""
    app, client = pill
    _record(app, "a")
    reads = []
    real = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (reads.append(self.name), real(self, *a, **k))[1])
    assert _now(client)["failures_today"] == 1
    first = reads.count("meta.json")
    assert _now(client)["failures_today"] == 1
    assert reads.count("meta.json") == first
    _record(app, "b")
    assert _now(client)["failures_today"] == 2
