"""The read-only route returns the pure resolver's decisions."""
from quam_state_manager.core.gate_action import resolve
from quam_state_manager.web.app import create_app


def test_route_matches_resolver_and_preserves_json_numbers(tmp_path):
    app = create_app(instance_path=str(tmp_path))
    app.config["TESTING"] = True
    spec = {"qubits": ["q1", "q2"], "qubit_pairs": [["q1", "q2"]]}
    action = {"gate": {"name": "cz_bm", "macro_class": "pkg.Gate"},
              "items": [{"role": "moving", "on_choice": "target", "pulse_class": "pkg.Pulse",
                         "fields": {"length": 40, "padding": 40.0, "amplitude": 1e-9}}]}
    response = app.test_client().post("/generate/additions/gate-action", json={
        "spec": spec, "pair": "q1-q2", "action": action})
    assert response.status_code == 200
    assert response.json == resolve(spec, "q1-q2", action)
    fields = response.json["additions"][0]["fields"]
    assert type(fields["length"]) is int
    assert type(fields["padding"]) is float
    assert fields["amplitude"] == 1e-9


def test_route_rejects_malformed_input(tmp_path):
    app = create_app(instance_path=str(tmp_path))
    app.config["TESTING"] = True
    response = app.test_client().post("/generate/additions/gate-action", json={"spec": []})
    assert response.status_code == 400
