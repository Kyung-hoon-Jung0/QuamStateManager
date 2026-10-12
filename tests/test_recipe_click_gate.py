"""Recipe formulas need their own marker proof and a stable fingerprint."""
import json
from types import SimpleNamespace

import pytest

from quam_state_manager.core.interactive_plots import learn_click as lc, recipe_gate, registry
from quam_state_manager.core.interactive_plots.recipes.base import Bundle, FigureSpec


@pytest.fixture(autouse=True)
def clear_proof_cache():
    recipe_gate._VERIFIED.clear()
    yield
    recipe_gate._VERIFIED.clear()


def setup(tmp_path, *, applied=True, evidence=True):
    entries = [{"op": "replace", "path": "/quam/qubits/q0/xy/amplitude", "old": 0.2, "value": 0.3}] if evidence else []
    node = {"patches": entries if applied else []}
    (tmp_path / "node.json").write_text(json.dumps(node))
    if not applied:
        (tmp_path / "state_updates.json").write_text(json.dumps({"operations": entries}))
    run = SimpleNamespace(folder_path=str(tmp_path), fit_results={}, uid="run-1")
    bundle = Bundle(run=run, node_meta=node)
    spec = FigureSpec("amplitude::q0", "Amplitude", figure={"data": [], "layout": {"shapes": [
        {"type": "line", "x0": 300, "x1": 300, "y0": 0, "y1": 1, "xref": "x", "yref": "paper"}]}},
        clickable={"axis": "x", "qubit": "q0", "targets": [{"path": "qubits.q0.xy.amplitude", "scale": 0.001}]})
    return bundle, spec


def test_gate_verifies_recipe_and_learned_contract_wins(tmp_path):
    bundle, spec = setup(tmp_path)
    gated = lc.gate(bundle, spec, "revision-1")
    assert gated.clickable and gated.clickable["n_verified"] == 1
    assert gated.clickable.get("learned")
    assert gated.clickable["analysis_fingerprint"] == "revision-1"
    assert "evidence_tier" not in spec.clickable["targets"][0]


def test_recipe_formula_mutation_disables_click_even_if_learning_succeeds(tmp_path):
    bundle, spec = setup(tmp_path)
    spec.clickable["targets"][0]["scale"] = 0.002
    gated = lc.gate(bundle, spec, "revision-1")
    assert gated.clickable is None
    assert "differs from the recipe's formula" in gated.reason


def test_recipe_fingerprint_mutation_sweep_retires_verified_formula(tmp_path):
    bundle, spec = setup(tmp_path)
    source = tmp_path / "synthetic_recipe.py"
    source.write_text("scale = 0.001\n")
    recipe = SimpleNamespace(__file__=str(source))
    before = recipe_gate.analysis_fingerprint(recipe)
    assert lc.gate(bundle, spec, before).clickable
    for changed in ("scale = 0.002\n", "scale = 0.003\n", "scale = 0.001\n# Formula revision.\n"):
        source.write_text(changed)
        after = recipe_gate.analysis_fingerprint(recipe)
        assert before != after
        gated = lc.gate(bundle, spec, after)
        assert gated.clickable is None and "fingerprint changed" in gated.reason


def test_absent_evidence_keeps_recipe_with_honest_popup_note(tmp_path):
    bundle, spec = setup(tmp_path, evidence=False)
    gated = lc.gate(bundle, spec, "revision-1")
    assert gated.clickable
    assert "not verified on this run" in gated.clickable["verification_note"]
    assert "not verified on this run" in gated.clickable["targets"][0]["provenance"]["formula"]
    assert not recipe_gate._VERIFIED


def test_proposed_recipe_proof_is_labelled(tmp_path):
    bundle, spec = setup(tmp_path, applied=False)
    gated = lc.gate(bundle, spec, "revision-1")
    assert gated.clickable and gated.clickable["verified_against"] == "proposals"
    assert "proposed, not applied" in gated.clickable["targets"][0]["provenance"]["formula"]


def test_explicit_verification_points_and_recipe_only_transform(tmp_path):
    bundle, spec = setup(tmp_path)
    node = {"patches": [{"op": "replace", "path": "/quam/qubits/q0/xy/amplitude", "old": 0.2, "value": 12}]}
    bundle.node_meta = node
    (tmp_path / "node.json").write_text(json.dumps(node))
    spec.figure["layout"]["shapes"] = []
    spec.verify_points = [{"x": 9, "path": "qubits.q0.xy.amplitude"}]
    spec.clickable["targets"][0]["transform"] = {"type": "ceil4"}
    gated = lc.gate(bundle, spec, "revision-1")
    assert gated.clickable and gated.clickable["targets"][0]["transform"]["type"] == "ceil4"
    spec.verify_points = []
    assert lc.gate(bundle, spec, "revision-1").clickable is None


def test_recipe_defaults_and_missing_old_void_contract(tmp_path):
    bundle, spec = setup(tmp_path)
    spec.figure["marker_defaults"] = ["pick"]
    assert lc.gate(bundle, spec, "revision-1").clickable is None
    spec.figure.pop("marker_defaults")
    bundle.node_meta["patches"][0]["old"] = None
    assert lc.gate(bundle, spec, "revision-1").clickable is None


def test_registry_passes_each_recipe_analysis_fingerprint(tmp_path, monkeypatch):
    bundle, spec = setup(tmp_path)
    recipe = SimpleNamespace(build=lambda *_: spec, __file__=__file__)
    inputs = {"name": "synthetic", "node_meta": bundle.node_meta, "raw": None, "fit": None,
              "iqb": None, "quam_state": {}}
    monkeypatch.setattr(registry, "_bundle_inputs", lambda _: inputs)
    monkeypatch.setattr(registry, "_resolve", lambda _: recipe)
    payload = registry.build_interactive_figure(bundle.run, spec.key)
    assert payload and payload.get("analysis_fingerprint") == recipe_gate.analysis_fingerprint(recipe)
    assert payload["clickable"].get("n_verified") == 1
    spec.clickable["targets"][0]["scale"] = 0.002
    payload = registry.build_interactive_figure(bundle.run, spec.key)
    assert payload["clickable"] is None
    assert payload["display_only_reason"] and payload["notices"]
