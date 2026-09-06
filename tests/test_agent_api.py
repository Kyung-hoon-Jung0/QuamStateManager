"""docs/172: the JSON door a terminal agent uses -- /api/agent/*.

Reads come from the same store the GUI renders; the journal refuses an
agent entry without a reason; the live strip derives "running" from an
unmatched PreToolUse and survives a restart from the hook's own jsonl.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from quam_state_manager.web.app import create_app

_H = {"Origin": "http://localhost"}          # the CSRF guard wants one; browsers send it


@pytest.fixture
def client(tmp_path):
    return create_app(testing=True, instance_path=str(tmp_path / "_app_instance")).test_client()


@pytest.fixture
def synth_folder(tmp_path):
    """The same synthetic chip test_web.py builds (its fixture is module-local)."""
    from tests.test_web import _make_state, _make_wiring
    d = tmp_path / "chip"
    d.mkdir()
    (d / "state.json").write_text(json.dumps(_make_state(), indent=2), encoding="utf-8")
    (d / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2), encoding="utf-8")
    return d


@pytest.fixture
def loaded_client(client, synth_folder):
    client.post("/load", data={"folder": str(synth_folder)})
    return client


class TestWithoutAChip:
    def test_chip_says_not_loaded_and_state_refuses(self, client):
        d = client.get("/api/agent/chip").get_json()
        assert d["ok"] is True and d["loaded"] is False and d["now"]["state"] == "idle"
        assert client.get("/api/agent/state?path=qubits").status_code == 409
        assert client.get("/api/agent/tray").status_code == 409
        assert client.get("/api/agent/versions").status_code == 409

    def test_families_and_a_manual_answer_without_a_chip(self, client):
        fams = client.get("/api/agent/families").get_json()["families"]
        assert any(f["family"] == "resonator_spectroscopy" and f["manual"] for f in fams)
        m = client.get("/api/agent/manual/resonator_spectroscopy").get_json()
        assert m["ok"] and m["cases"] and all("prescription" in c for c in m["cases"])
        assert client.get("/api/agent/manual/nope").status_code == 404

    def test_mutations_sit_behind_the_app_wide_csrf_guard(self):
        """The guard is bypassed under TESTING; a production app refuses a
        POST with no Origin -- the out-of-process callers send one."""
        import os
        os.environ["SM_DISABLE_ENV_WARMUP"] = "1"
        import tempfile
        app = create_app(testing=False, instance_path=tempfile.mkdtemp(prefix="quam_test_instance_"))
        c = app.test_client()
        assert c.post("/api/agent/journal", json={"text": "x", "reason": "y"}).status_code == 403
        assert c.post("/api/agent/journal", json={"text": "x", "reason": "y"},
                      headers={"Origin": "http://localhost"}).status_code == 200


class TestWithAChip:
    def test_state_leaf_container_and_missing(self, loaded_client):
        c = loaded_client
        top = c.get("/api/agent/state").get_json()
        assert top["kind"] == "container" and "qubits" in top["keys"]
        cont = c.get("/api/agent/state?path=qubits").get_json()
        assert cont["kind"] == "container" and "qA1" in cont["keys"]
        leaf_path = None
        for k, v in cont["value"]["qA1"].items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                leaf_path = f"qubits.qA1.{k}"
                break
        assert leaf_path, "the synth chip carries a numeric leaf on qA1"
        leaf = c.get(f"/api/agent/state?path={leaf_path}").get_json()
        assert leaf["kind"] == "leaf" and leaf["source_file"] == "state" and leaf["is_pointer"] is False
        assert c.get("/api/agent/state?path=qubits.nope.x").status_code == 404

    def test_a_pointer_leaf_is_resolved_beside_its_raw_text(self, loaded_client):
        c = loaded_client
        pairs = c.get("/api/agent/state?path=qubit_pairs").get_json()["value"]
        found = None
        for pid, pair in pairs.items():
            for k, v in pair.items():
                if isinstance(v, str) and v.startswith("#/"):
                    found = (f"qubit_pairs.{pid}.{k}", v)
                    break
            if found:
                break
        assert found, "the synth chip carries a pointer on a pair"
        leaf = c.get(f"/api/agent/state?path={found[0]}").get_json()
        assert leaf["is_pointer"] is True and leaf["value"] == found[1]
        assert leaf["resolved"] != found[1] and isinstance(leaf["resolved"], dict),             "the agent gets what the pointer POINTS AT, not just the text"

    def test_chip_tray_and_the_edit_door(self, loaded_client):
        c = loaded_client
        d = c.get("/api/agent/chip").get_json()
        assert d["loaded"] and "qA1" in d["qubits"] and d["pending"] == 0 and d["chip_token"]
        cont = c.get("/api/agent/state?path=qubits.qA1").get_json()["value"]
        key = next(k for k, v in cont.items() if isinstance(v, (int, float)) and not isinstance(v, bool))
        r = c.post("/field/edit", data={"dot_path": f"qubits.qA1.{key}", "value": "1234.5",
                                        "expect_chip": d["chip_token"]}, headers=_H)
        assert r.status_code == 200, r.get_data(as_text=True)
        tray = c.get("/api/agent/tray").get_json()
        assert tray["count"] == 1 and tray["seen_changes"] == 1
        assert tray["entries"][0]["path"] == f"qubits.qA1.{key}" and tray["entries"][0]["new"] == 1234.5
        assert c.get("/api/agent/chip").get_json()["pending"] == 1

    def test_versions_and_field_history_answer(self, loaded_client):
        c = loaded_client
        v = c.get("/api/agent/versions?n=5").get_json()
        assert v["ok"] and isinstance(v["versions"], list)
        cont = c.get("/api/agent/state?path=qubits.qA1").get_json()["value"]
        key = next(k for k, v in cont.items() if isinstance(v, (int, float)) and not isinstance(v, bool))
        h = c.get(f"/api/agent/field-history?path=qubits.qA1.{key}").get_json()
        assert h["ok"] and h["path"] == f"qubits.qA1.{key}"
        assert c.get("/api/agent/field-history").status_code == 400

    def test_diagnostics_is_json(self, loaded_client):
        d = loaded_client.get("/api/agent/diagnostics").get_json()
        assert d["ok"] and set(d["summary"]) >= {"error", "warning", "total"} and isinstance(d["findings"], list)

    def test_runs_without_a_dataset_folder_is_an_honest_empty(self, loaded_client):
        d = loaded_client.get("/api/agent/runs").get_json()
        assert d["ok"] and d["count"] == 0 and "note" in d
        assert loaded_client.get("/api/agent/run/1").status_code == 409

    def test_a_note_lands_in_the_entity_notes_store(self, loaded_client):
        r = loaded_client.post("/api/agent/note", json={"subject": "qA1", "text": "left alone: T1 outlier"}, headers=_H)
        assert r.status_code == 200 and r.get_json()["note"]["author"] == "claude-code"


class TestJournalDoor:
    def test_an_agent_entry_without_a_reason_is_refused(self, client):
        r = client.post("/api/agent/journal", json={"text": "ran x"}, headers=_H)
        assert r.status_code == 400 and "reason" in r.get_json()["error"]

    def test_a_hook_entry_needs_no_reason_and_the_file_is_readable_back(self, client):
        r = client.post("/api/agent/journal", json={"text": "ran `x`", "kind": "hook"}, headers=_H)
        assert r.status_code == 200
        r = client.post("/api/agent/journal", json={"text": "set amp", "reason": "left-biased", "run_id": 7,
                                                    "paths": "a.b, c.d"}, headers=_H)
        assert r.status_code == 200 and r.get_json()["entry"]["paths"] == ["a.b", "c.d"]
        j = client.get("/api/agent/journal").get_json()
        assert "`hook` ran `x`" in j["text"] and "run #7" in j["text"] and "because: left-biased" in j["text"]
        assert j["days"] == [datetime.now().strftime("%Y-%m-%d")]

    def test_bad_run_id(self, client):
        r = client.post("/api/agent/journal", json={"text": "x", "reason": "y", "run_id": "seven"}, headers=_H)
        assert r.status_code == 400

    def test_root_can_be_pointed_at_a_vault_and_back(self, client, tmp_path):
        vault = tmp_path / "vault"
        r = client.post("/api/agent/journal/root", json={"root": str(vault)}, headers=_H)
        assert r.status_code == 200 and r.get_json()["root"] == str(vault)
        assert client.get("/api/agent/journal/root").get_json()["root"] == str(vault)
        client.post("/api/agent/journal/root", json={"root": ""}, headers=_H)
        assert client.get("/api/agent/journal/root").get_json()["root"].endswith("journal")


class TestTheLiveStrip:
    def test_running_is_an_unmatched_pre_event(self, client):
        client.post("/api/agent/event", json={"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                              "tool_use_id": "t1", "summary": "python 05_power_rabi.py",
                                              "session_id": "s"}, headers=_H)
        now = client.get("/api/agent/now").get_json()
        assert now["state"] == "running" and now["running"]["summary"] == "python 05_power_rabi.py"
        client.post("/api/agent/event", json={"hook_event_name": "PostToolUse", "tool_name": "Bash",
                                              "tool_use_id": "t1", "summary": "python 05_power_rabi.py"}, headers=_H)
        now = client.get("/api/agent/now").get_json()
        assert now["state"] == "between" and now["running"] is None
        client.post("/api/agent/event", json={"hook_event_name": "Stop", "summary": "Rabi looks clean."}, headers=_H)
        now = client.get("/api/agent/now").get_json()
        assert now["last_message"] == "Rabi looks clean." and now["events_today"] == 3
        ev = client.get("/api/agent/events?n=2").get_json()
        assert ev["count"] == 2 and ev["events"][-1]["hook_event_name"] == "Stop"

    def test_a_restart_replays_the_hooks_jsonl(self, tmp_path):
        from quam_state_manager.web.app import create_app
        inst = tmp_path / "_inst"
        (inst / "agent_events").mkdir(parents=True)
        f = inst / "agent_events" / (datetime.now().strftime("%Y-%m-%d") + ".jsonl")
        f.write_text(json.dumps({"ts": 1.0, "hook_event_name": "PreToolUse", "tool_name": "Bash",
                                 "tool_use_id": "old", "summary": "python x.py"}) + "\n", encoding="utf-8")
        c = create_app(testing=True, instance_path=str(inst)).test_client()
        now = c.get("/api/agent/now").get_json()
        assert now["events_today"] == 1 and now["last"]["summary"] == "python x.py"
        assert now["running"] is None, "an hour-old unmatched Pre is not 'running now'"

    def test_a_non_object_event_is_refused(self, client):
        assert client.post("/api/agent/event", json=[1, 2], headers=_H).status_code == 400
