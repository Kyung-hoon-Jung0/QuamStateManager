"""C0: snapshot fallbacks remain available, observable, and testable."""

import logging

import pytest

from quam_state_manager.web import routes
from tests.test_hub_drawer import _inline, chip_dir, chip_state, make_app, sm, write_chip  # noqa: F401


@pytest.fixture
def no_runs(tmp_path, monkeypatch):
    monkeypatch.delenv("HUB_FALLBACK_TRIPWIRE", raising=False)
    monkeypatch.setattr(routes, "_HUB_FALLBACK_REACHED", {})
    monkeypatch.setattr(routes, "_HUB_FALLBACK_WARNED", set())
    live = tmp_path / "chip"
    write_chip(live, chip_state(), None)
    app = make_app(tmp_path)
    client = app.test_client()
    assert client.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    env = {"app": app, "client": client}
    # An SM-only ledger, with no data folder or observed run states.
    from quam_state_manager.core.hub_store import HubStore
    with HubStore(chip_dir(env)):
        pass
    return env


SURFACES = [
    ("drawer", "/field/history?path=qubits.qA1.T1"),
    ("trends", "/topology/trends?metrics=T1"),
    ("versions", "/state/versions"),
    ("state_history", "/state-history?body=1"),
    ("agent_field_history", "/api/agent/field-history?path=qubits.qA1.T1"),
    ("trends_paths", "/topology/trends/paths?q=T1"),
    ("metric_meta", "/topology/metric-meta"),
    ("param_history_grid", "/param-history?since=all&props=T1"),
    ("param_history_expand", "/param-history/expand?qubit=qA1&prop=T1"),
    ("changes", "/param-history/changes"),
    ("changes_paths", "/param-history/param-search?q=T1"),
    ("sparklines", "/api/topology/sparklines/qA1"),
    ("history_drawer", "/api/history"),
    ("version_count", "/state/version"),
]


@pytest.mark.parametrize("surface,url", SURFACES)
def test_no_runs_fallback_counts_and_tripwire_surfaces(no_runs, monkeypatch, surface, url):
    client = no_runs["client"]
    response = client.get(url, headers={"HX-Request": "true"})
    assert response.status_code == 200
    # S10 C3: old -> new, readable no-run ledgers never enter a snapshot fallback.
    assert client.get("/hub/status").get_json()["fallback_reached"] == {}
    monkeypatch.setenv("HUB_FALLBACK_TRIPWIRE", "1")
    assert client.get(url, headers={"HX-Request": "true"}).status_code == 200
    assert routes._hub_fallback_counts() == {}


def test_warning_once_per_chip_surface_reason_and_testing_gate(no_runs, monkeypatch, caplog):
    app = no_runs["app"]
    with app.test_request_context(), caplog.at_level(logging.WARNING):
        routes._hub_fallback_reached("drawer", "no_runs")
        routes._hub_fallback_reached("drawer", "no_runs")
        routes._hub_fallback_reached("drawer", "unreadable")
        monkeypatch.setenv("HUB_FALLBACK_TRIPWIRE", "1")
        app.testing = False
        raised = None
        try:
            routes._hub_fallback_reached("drawer", "no_runs")
        except RuntimeError as exc:
            raised = str(exc)
        assert raised is None
        app.testing = True
        with pytest.raises(RuntimeError, match="hub fallback reached"):
            routes._hub_fallback_reached("drawer", "no_runs")
    warnings = [r for r in caplog.records if r.message.startswith("hub fallback reached:")]
    assert len(warnings) == 2 and all(r.levelno == logging.WARNING for r in warnings)
    assert routes._hub_fallback_counts()["drawer"] == {"no_runs": 4, "unreadable": 1}


def test_idle_status_exposes_detached_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "_HUB_FALLBACK_REACHED", {("drawer", "no_runs"): 3})
    payload = make_app(tmp_path).test_client().get("/hub/status").get_json()
    assert payload == {"state": "idle", "note": "no chip is open",
                       "fallback_reached": {"drawer": {"no_runs": 3}}}


@pytest.mark.parametrize("url", [url for surface, url in SURFACES[:4]])
@pytest.mark.parametrize("mode", ["building", "preparing"])
def test_transient_waits_do_not_trip(no_runs, monkeypatch, url, mode):
    from quam_state_manager.core import hub_sync, ramcache
    monkeypatch.setenv("HUB_FALLBACK_TRIPWIRE", "1")
    if mode == "building":
        monkeypatch.setattr(hub_sync, "status", lambda directory: {"state": "building", "done": 0, "total": 1})
    else:
        from quam_state_manager.core import hub_versions, value_history
        def warming(*args, **kwargs):
            raise ramcache.Warming("test", "key", 0)
        monkeypatch.setattr(value_history, "read", warming)
        monkeypatch.setattr(hub_versions, "_version_token", warming)
    response = no_runs["client"].get(url, headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert routes._hub_fallback_counts() == {}


@pytest.mark.parametrize("surface,url", [
    ("trends", "/topology/trends?metrics=T1"),
    ("sparklines", "/api/topology/sparklines/qA1"),
])
def test_a_ledger_becoming_unreadable_after_the_mode_check_trips(sm, monkeypatch, surface, url):
    from quam_state_manager.core import value_history
    real = value_history.read
    def unreadable(directory, targets, **kwargs):
        if targets:
            raise ValueError("unreadable value")
        return real(directory, targets, **kwargs)
    monkeypatch.setattr(value_history, "read", unreadable)
    monkeypatch.setenv("HUB_FALLBACK_TRIPWIRE", "1")
    # S10 C3: old -> new, a table-time error ends unavailable without old rows.
    response = sm["client"].get(url)
    assert response.status_code == 200
    assert 'data-vh-mode="unavailable"' in response.get_data(as_text=True)
    assert "Nothing older is shown in its place." in response.get_data(as_text=True)


def test_snapshot_count_under_app_context_has_the_correct_tripwire(no_runs, monkeypatch):
    with no_runs["app"].app_context():
        assert isinstance(routes._state_version_now(routes._active_ctx())["count"], int)
        monkeypatch.setenv("HUB_FALLBACK_TRIPWIRE", "1")
        # S10 C3: old -> new, the no-run version count uses the ledger without fallback.
        assert isinstance(routes._state_version_now(routes._active_ctx())["count"], int)
        assert routes._hub_fallback_counts() == {}
