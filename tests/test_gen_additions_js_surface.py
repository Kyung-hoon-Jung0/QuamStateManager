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
