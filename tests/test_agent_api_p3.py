"""docs/273: small agent read/feed regressions, using synthetic chips only."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from quam_state_manager.core import agent_session, journal
from quam_state_manager.core.dataset import DatasetStore
from quam_state_manager.web import agent_api
from quam_state_manager.web.app import create_app


@pytest.fixture
def client(tmp_path):
    return create_app(testing=True, instance_path=str(tmp_path / "instance")).test_client()


@pytest.fixture
def loaded_client(client, tmp_path):
    from tests.test_web import _make_state, _make_wiring

    folder = tmp_path / "chip"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(_make_state()), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
    assert client.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    return client


@pytest.fixture
def dataset(client, tmp_path, monkeypatch):
    root = tmp_path / "runs"
    specs = [(1, "05_power_rabi", "2026-10-03", "q1"),
             (2, "08_qubit_spectroscopy", "2026-10-04", "q2"),
             (3, "05_power_rabi", "2026-10-04", "q1"),
             (4, "06_time_rabi", "2026-10-04", "q2")]
    for rid, name, day, qubit in specs:
        folder = root / day / f"#{rid}_{name}_010000"
        folder.mkdir(parents=True)
        (folder / "node.json").write_text(json.dumps({
            "id": rid, "created_at": f"{day}T01:00:00+09:00", "parents": [],
            "metadata": {"name": name, "status": "successful"},
            "data": {"parameters": {"model": {"qubits": [qubit]}}},
        }), encoding="utf-8")
    store = DatasetStore(root)
    monkeypatch.setattr(agent_api, "_ds", lambda: store)
    return store


def test_multiline_answer_survives_in_the_journal(client):
    answer = "The fit is clean.\n\n- Leave amplitude alone.\n- Check T1 next."
    response = client.post("/api/agent/event", json={
        "hook_event_name": "Stop", "session_id": "p3", "backend": "codex",
        "summary": answer, "event_id": "p3-answer",
    })
    assert response.status_code == 200
    text = client.get("/api/agent/journal?chip=unassigned").get_json()["text"]
    assert "The fit is clean.\n\n  · - Leave amplitude alone.\n  · - Check T1 next." in text


@pytest.mark.parametrize("answer,expected", [
    ("a" * 600, "a" * 600),
    ("first line\n" + "b" * 600, "first line\n[truncated]"),
    ("a" * 601, "a" * 588 + "\n[truncated]"),
    ("a" * 589 + "\n" + "b" * 20, "a" * 588 + "\n[truncated]"),
], ids=["exact-cap", "line-boundary", "long-single-line", "boundary-outside-budget"])
def test_answer_cap_includes_the_cut_marker(client, answer, expected):
    with client.application.app_context():
        line, _, _ = agent_api._journal_line({"hook_event_name": "Stop", "summary": answer})
    assert line == expected
    assert len(line) <= 600


def test_disabled_agent_answers_stay_out_of_journal(client):
    journal._write_settings(client.application.instance_path, {"agent_says": False})
    with client.application.app_context():
        assert agent_api._journal_line({"hook_event_name": "Stop", "summary": "one\ntwo"})[0] is None


@pytest.mark.parametrize("query,ids", [
    ("POWER", [3, 1]), ("rabi power", [3, 1]),
    ("power | spectroscopy", [3, 2, 1]),
    ("rabi power | time", [4, 3, 1]), ("05_power_rabi", [3, 1]),
])
def test_experiment_search_uses_the_shared_grammar(client, dataset, query, ids):
    response = client.get("/api/agent/runs", query_string={"experiment": query})
    assert response.status_code == 200
    assert [r["run_id"] for r in response.get_json()["runs"]] == ids


def test_experiment_search_filters_before_limiting(client, dataset):
    data = client.get("/api/agent/runs?experiment=POWER&date=2026-10-03&qubit=q1&n=1").get_json()
    assert [r["run_id"] for r in data["runs"]] == [1]


def test_unknown_experiment_returns_closest_names(client, dataset):
    data = client.get("/api/agent/runs?experiment=power_rabbi").get_json()
    assert data["ok"] and data["count"] == 0 and data["runs"] == []
    assert data["closest"][0] == "05_power_rabi"
    assert len(data["closest"]) <= 5 and len(data["closest"]) == len(set(data["closest"]))
    assert "05_power_rabi" in data["hint"]


def test_empty_date_slice_does_not_claim_the_experiment_is_unknown(client, dataset):
    data = client.get("/api/agent/runs?experiment=POWER&date=2025-01-01").get_json()
    assert data["ok"] and data["count"] == 0
    assert "closest" not in data


SHELL_TOOLS = ["Bash", "PowerShell", "exec_command", "shell_command", "shell", "functions.exec_command"]


@pytest.mark.parametrize("tool", SHELL_TOOLS)
def test_shell_commands_have_feed_cards(tool):
    card = agent_api._chat_card({"hook_event_name": "PreToolUse", "tool_name": tool,
                                 "summary": "python 05_power_rabi.py", "backend": "codex"})
    assert card is not None and card["kind"] == "tool" and card["tool"] == tool


@pytest.mark.parametrize("tool", SHELL_TOOLS)
def test_shell_commands_are_relevant_and_journaled(client, monkeypatch, tool):
    event = {"hook_event_name": "PostToolUse", "tool_name": tool,
             "summary": "python 05_power_rabi.py", "backend": "codex"}
    assert agent_api._relevant([event])
    monkeypatch.setattr(agent_api, "_newest_run_since", lambda ts: 17)
    with client.application.app_context():
        assert agent_api._journal_line(event) == ("ran `05_power_rabi`", 17, "by_codex")


def test_powershell_run_is_not_misattributed_to_a_person(client, monkeypatch):
    from quam_state_manager.core import story

    ds = SimpleNamespace(list_runs=lambda: [{"run_id": 17, "experiment_name": "05_power_rabi"}])
    monkeypatch.setattr(agent_api, "_ds", lambda: ds)
    monkeypatch.setattr(story, "run_epoch", lambda row, store: 1000.0)
    monkeypatch.setattr(story, "unattributed_agent_runs", lambda *a, **k: [])
    with client.application.app_context():
        event = {"hook_event_name": "PostToolUse", "tool_name": "PowerShell",
                 "summary": "python 05_power_rabi.py", "ts": 1000}
        assert agent_api._human_ran_recently(1001, {}, [event]) is None


@pytest.mark.parametrize("n", ["0", "-1", "nope"])
def test_versions_refuses_nonpositive_or_invalid_counts(loaded_client, n):
    response = loaded_client.get("/api/agent/versions", query_string={"n": n})
    assert response.status_code == 400
    assert "n must be a positive integer" in response.get_json()["error"]


def test_missing_field_history_is_a_404(loaded_client):
    response = loaded_client.get("/api/agent/field-history?path=qubits.no_such_qubit.f_01")
    assert response.status_code == 404
    assert "no such path" in response.get_json()["error"]


def test_existing_field_with_no_history_is_an_empty_success(loaded_client, monkeypatch):
    with loaded_client.application.app_context():
        hm = agent_api._r()._history()
    monkeypatch.setattr(hm, "field_history", lambda *a, **k: [])
    response = loaded_client.get("/api/agent/field-history?path=qubits.qA1.id")
    assert response.status_code == 200 and response.get_json()["history"] == []


@pytest.mark.parametrize("endpoint", ["runs", "journal"])
@pytest.mark.parametrize("day", ["2026-02-30", "2026-13-01", "2026-1-01", "garbage"])
def test_date_filters_refuse_invalid_days(client, endpoint, day):
    response = client.get(f"/api/agent/{endpoint}", query_string={"date": day})
    assert response.status_code == 400
    assert "date must be" in response.get_json()["error"]


@pytest.mark.parametrize("endpoint", ["runs", "journal"])
def test_date_filters_accept_leap_days(client, dataset, endpoint):
    assert client.get(f"/api/agent/{endpoint}?date=2024-02-29").status_code == 200


def test_unknown_note_subject_is_a_404(loaded_client):
    response = loaded_client.get("/api/agent/notes?path=qubits.no_such_qubit")
    assert response.status_code == 404 and "no such path" in response.get_json()["error"]


def test_bare_note_subject_is_normalized(loaded_client):
    data = loaded_client.get("/api/agent/notes?path=qA1").get_json()
    assert data["ok"] and data["path"] == "qubits.qA1"


def test_unknown_run_subject_is_a_404(client, dataset):
    response = client.get("/api/agent/runs?qubit=no_such_qubit")
    assert response.status_code == 404 and "no qubit called" in response.get_json()["error"]
    assert response.get_json()["available"] == ["q1", "q2"]


def test_session_reads_the_chip_key_already_fixed_by_docs253(loaded_client):
    with loaded_client.application.app_context():
        key, name = agent_api._chip_key(), agent_api._chip_name()
        assert key != name
        agent_session.save(loaded_client.application.instance_path, key, owner="p3-owner", mode="ask-writes")
        agent_session.save(loaded_client.application.instance_path, name, owner="wrong-name", mode="auto")
    data = loaded_client.get("/api/agent/session").get_json()
    assert data["session"] is not None and data["session"]["owner"] == "p3-owner"
    assert data["session"]["mode"] == "ask-writes"


@pytest.mark.parametrize("simulated", [True, False])
def test_run_views_use_the_recorded_result_simulation(client, monkeypatch, simulated):
    record = {"key": "p3-run", "status": "done", "since": 1,
              "simulated": not simulated, "result": {"simulated": simulated}}
    registry = SimpleNamespace(runs={"p3-run": record}, wait=lambda *a: record)
    monkeypatch.setattr(agent_api, "_registry", lambda: registry)
    for url in ("/api/agent/run/p3-run", "/api/agent/runs/agent"):
        data = client.get(url).get_json()
        run = data["runs"][0] if "runs" in data else data
        assert run["simulated"] is simulated and run["result"]["simulated"] is simulated


def test_run_view_preserves_legacy_simulation_metadata():
    assert agent_api._run_view({"simulated": True})["simulated"] is True
