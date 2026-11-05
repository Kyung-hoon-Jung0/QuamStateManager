"""W3-4: a run SM's own runner made (Experiment Runner, Auto Calibrate) is
SM's, never "a person's" -- the Agent must not refuse run_node with
human_active because of it. Auto Calibrate removes each step's queue item,
so the attribution must outlive the queue."""
from datetime import datetime, timezone
from types import SimpleNamespace

from quam_state_manager.core import scheduler, story
import pytest

from quam_state_manager.web import agent_api
from quam_state_manager.web.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    return app.test_client()


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")


def _setup(client, monkeypatch, tmp_path, name="08_qubit_spectroscopy"):
    ds = SimpleNamespace(list_runs=lambda: [{"run_id": 35, "experiment_name": name}])
    monkeypatch.setattr(agent_api, "_ds", lambda: ds)
    monkeypatch.setattr(agent_api, "_strip_ds", lambda: ds, raising=False)
    monkeypatch.setattr(story, "run_epoch", lambda row, store: 10_000.0)
    monkeypatch.setattr(story, "unattributed_agent_runs", lambda *a, **k: [])
    from quam_state_manager.web import routes
    monkeypatch.setattr(routes, "_sched_inst", lambda: str(tmp_path))


def test_a_run_the_runner_made_is_not_a_persons(client, monkeypatch, tmp_path):
    _setup(client, monkeypatch, tmp_path)
    scheduler.record_sm_run(tmp_path, {"id": "a1", "name": "08_qubit_spectroscopy",
                                       "label": "Auto Calibrate: qspec (attempt 1)",
                                       "started_at": _iso(9_990), "ended_at": _iso(10_030)})
    with client.application.app_context():
        assert agent_api._human_ran_recently(10_100, {}, []) is None


def test_a_run_outside_the_runner_still_counts(client, monkeypatch, tmp_path):
    _setup(client, monkeypatch, tmp_path)
    scheduler.record_sm_run(tmp_path, {"id": "a1", "name": "08_qubit_spectroscopy",
                                       "started_at": _iso(5_000), "ended_at": _iso(5_050)})
    with client.application.app_context():
        h = agent_api._human_ran_recently(10_100, {}, [])
    assert h is not None and h["run_id"] == 35


def test_another_node_in_the_window_is_not_covered(client, monkeypatch, tmp_path):
    _setup(client, monkeypatch, tmp_path, name="05_power_rabi")
    scheduler.record_sm_run(tmp_path, {"id": "a1", "name": "08_qubit_spectroscopy",
                                       "started_at": _iso(9_990), "ended_at": _iso(10_030)})
    with client.application.app_context():
        assert agent_api._human_ran_recently(10_100, {}, []) is not None


def test_the_worker_records_each_finished_item(tmp_path, monkeypatch):
    """The record is written where the item ends, not by the caller."""
    import inspect
    src = inspect.getsource(scheduler)
    i = src.index('it["ended_at"] = _now()\n                    it["returncode"]')
    assert "record_sm_run(instance_path, it)" in src[i:i + 900]
