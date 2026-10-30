"""Copy and Gaussian CZ ask the same lab checker before any write."""
import json
import pytest
from tests.test_add_paths_golden import _shapes, _client, _post, recorder
from quam_state_manager.web import routes


def setup_client(tmp_path):
    state, wiring, pair = _shapes()["flux_coupler"]
    client = _client(tmp_path, "lab_check", state, wiring)
    response = client.post("/api/pulse/create", data={
        "target_kind": "qubit", "qubit": "q1", "channel": "xy", "op_name": "probe",
        "pulse_type": "SquarePulse", "amplitude": "0.1", "length": "40"})
    assert response.status_code == 200
    return client, pair


def request_data(kind, pair):
    if kind == "copy":
        return "/api/pulse/copy", {"path": "qubits.q1.xy.operations.probe", "target_kind": "qubit",
                                   "qubit": "q2", "channel": "xy", "op_name": "copy_probe"}
    return "/api/pulse/gaussian-cz", {"pair_id": pair}


@pytest.mark.parametrize("kind,count", [("copy", 1), ("gaussian", 6)])
def test_lab_refusal_precedes_all_writes_and_releases_hold(tmp_path, monkeypatch, recorder, kind, count):
    client, pair = setup_client(tmp_path)
    events, batches = [], []
    def hold(store, paths):
        events.append("hold")
        return lambda: events.append("release")
    def check(store, writes, **kw):
        events.append("check")
        batches.append(writes)
        return "the gate code refuses this pulse", {0}
    monkeypatch.setattr(routes, "_lab_hold", hold)
    monkeypatch.setattr(routes, "_lab_write_refusal", check)
    url, data = request_data(kind, pair)
    got = _post(client, recorder, url, data)
    assert got["status"] == 400 and got["ops"] == []
    assert "gate code refuses" in got["message"]
    assert events == ["hold", "check", "release"]
    assert len(batches) == 1 and len(batches[0]) == count


@pytest.mark.parametrize("kind", ["copy", "gaussian"])
def test_unavailable_lab_check_is_reported_without_blocking(tmp_path, monkeypatch, kind):
    client, pair = setup_client(tmp_path)
    def check(store, writes, *, info):
        info["notes"].append("the environment could not check this pulse")
    monkeypatch.setattr(routes, "_lab_write_refusal", check)
    url, data = request_data(kind, pair)
    response = client.post(url, data=data)
    assert response.status_code == 200
    assert "could not check" in json.loads(response.headers["HX-Trigger"])["sm:toast"]["message"]
