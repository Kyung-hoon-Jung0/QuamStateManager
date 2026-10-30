"""The browser renders choices; Python owns the CZ decisions."""
from pathlib import Path


def test_no_name_clash_or_frequency_mirror():
    source = (Path(__file__).parents[1] / "quam_state_manager/web/static/gen-additions.js").read_text(encoding="utf-8")
    assert "function gateNameClash(" not in source
    assert "RF_freq" not in source
    assert 'postJSON("/generate/additions/gate-action"' in source


def test_display_route_returns_real_pair_facts(tmp_path):
    import json
    from quam_state_manager.web.app import create_app
    from quam_state_manager.core.gate_action import display_context
    spec = {"qubit_pairs": [["q1", "q2"]], "pair_gate": "cz_tunable",
            "lines": [{"line": "coupler", "element": "q1-q2"}]}
    app = create_app(testing=True, instance_path=str(tmp_path))
    response = app.test_client().get("/generate/roles", query_string={"spec": json.dumps(spec)})
    assert response.json["display"] == display_context(spec)
    pair = response.json["display"]["pairs"]["q1-q2"]
    assert pair["coupler"] and pair["pair"] == "q1-2"
    assert "cz_SNZ" in pair["builder"]


def test_the_gates_display_facts_ride_a_post_body_not_the_url():
    """A 60-qubit / 100-pair spec in a GET URL was over the dev server's
    64 KiB line limit (414) and the Gates cells never rendered."""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "quam_state_manager/web/static/gen-additions.js").read_text(encoding="utf-8")
    assert "/generate/roles?spec=" not in js
    assert "display_spec" in js
    from quam_state_manager.web.app import create_app
    app = create_app()
    app.config["TESTING"] = True
    spec = {"qubits": ["q%d" % i for i in range(60)],
            "qubit_pairs": [["q%d" % i, "q%d" % (i + 1)] for i in range(59)],
            "lines": [{"element": "q%d" % i, "line": "flux"} for i in range(60)] * 40}
    r = app.test_client().post("/generate/roles", json={"display_spec": spec})
    assert r.status_code == 200 and "display" in r.get_json()
