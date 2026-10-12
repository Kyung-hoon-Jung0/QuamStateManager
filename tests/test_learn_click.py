"""Synthetic evidence pins for learned, per-run click contracts."""
import json
from types import SimpleNamespace

import pytest

from quam_state_manager.core.interactive_plots import learn_click as lc


def run_with(tmp_path, entries, *, proposals=False):
    (tmp_path / "node.json").write_text(json.dumps({"patches": [] if proposals else entries}))
    if proposals:
        (tmp_path / "state_updates.json").write_text(json.dumps({"operations": entries}))
    return SimpleNamespace(folder_path=str(tmp_path), uid="run-1")


def write(q="q0", value=0.22, old=0.2, field="amplitude"):
    return {"op": "replace", "path": f"/quam/qubits/{q}/xy/{field}", "value": value, "old": old}


def figure(*points):
    return {"markers": [{"entity": q, "axis": "x", "coordinate": c} for q, c in points]}


def test_prefactor_is_learned_from_old_not_saved_state(tmp_path):
    run = run_with(tmp_path, [write()])
    contract = lc.learn(run, figure(("q0", 1.1)))
    assert contract and contract["learned"] and contract["n_verified"] == 1
    target = contract["targets"][0]
    assert target["transform"] == {"type": "prefactor", "old": 0.2,
                                   "coordinate_scale": 1.0, "coordinate_offset": 0.0}
    assert target["path_template"] == "qubits.{q}.xy.amplitude"
    assert target["verified_on"] == "run-1" and target["marker_id"] == "0"
    assert target["evidence_tier"] == "applied"


def test_proposals_are_evidence_but_patches_win(tmp_path):
    run = run_with(tmp_path, [write()], proposals=True)
    proposed = lc.learn(run, figure(("q0", 1.1)))
    assert proposed and proposed["verified_against"] == "proposals"
    assert "proposed, not applied" in proposed["label"]
    assert "proposed, not applied" in proposed["targets"][0]["provenance"]["formula"]
    (tmp_path / "node.json").write_text(json.dumps({"patches": [write(value=0.24)]}))
    payload = figure(("q0", 1.1))
    assert lc.learn(run, payload) is None
    assert "no common" in payload["display_only_reason"]


@pytest.mark.parametrize("old,value,coordinate,kind", [
    (0.5, 0.3, 0.3, "identity"), (0.5, 0.3, 300, "scale"),
    (0.5, 3e6, 3, "scale"), (0.5, 3e-12, 3, "scale")])
def test_closed_identity_and_power_of_ten_scales(tmp_path, old, value, coordinate, kind):
    contract = lc.learn(run_with(tmp_path, [write(old=old, value=value)]), figure(("q0", coordinate)))
    assert contract and contract["targets"][0]["transform_kind"] == kind


@pytest.mark.parametrize("old,value,coordinate", [(None, 0.22, 0.22), (0.2, 0.22, 1.11),
                                                       (0.2, float("inf"), 1.1), (0.21, 0.22, 1.1)])
def test_unproved_or_wrong_old_is_display_only(tmp_path, old, value, coordinate):
    payload = figure(("q0", coordinate))
    assert lc.learn(run_with(tmp_path, [write(old=old, value=value)]), payload) is None
    assert payload["display_only_reason"]


def test_exact_relative_tolerance(tmp_path):
    run = run_with(tmp_path, [write()])
    assert lc.learn(run, figure(("q0", 1.1 + 5e-7)))
    assert lc.learn(run, figure(("q0", 1.1 + 2e-6))) is None


def test_every_entity_must_verify_the_same_tuple(tmp_path):
    run = run_with(tmp_path, [write(), write("q1", old=0.3, value=0.36)])
    assert lc.learn(run, figure(("q0", 1.1), ("q1", 1.2)))["n_verified"] == 2
    assert lc.learn(run, figure(("q0", 1.1), ("q1", 1.3))) is None
    assert lc.learn(run, figure(("q0", 1.1))) is None
    assert lc.learn(run, figure(("q0", 1.1), ("q1", 0.36))) is None


def test_two_distinct_transforms_are_ambiguous(tmp_path):
    run = run_with(tmp_path, [write()])
    payload = figure(("q0", 1.1), ("q0", 0.22))
    assert lc.learn(run, payload) is None
    assert "ambiguous" in payload["display_only_reason"]


def test_marker_defaults_void_but_unrelated_defaults_do_not(tmp_path):
    run = run_with(tmp_path, [write()])
    payload = figure(("q0", 1.1))
    payload["bind_report"] = {"arg": {"status": "defaulted"}}
    assert lc.learn(run, payload)
    payload["bind_report"]["arg"]["feeds_marker"] = True
    assert lc.learn(run, payload) is None
    assert "defaulted" in payload["display_only_reason"]
    payload = {**figure(("q0", 1.1)), "marker_defaults": ["pick"]}
    assert lc.learn(run, payload) is None


def test_increment_requires_real_axis_anchor(tmp_path):
    run = run_with(tmp_path, [write(old=10, value=12)])
    payload = figure(("q0", 7))
    assert lc.learn(run, payload) is None
    payload["markers"][0]["anchor"] = 5
    contract = lc.learn(run, payload)
    assert contract and contract["targets"][0]["offset"] == 5


def test_half_double_chain_uses_a_verified_other_write(tmp_path):
    run = run_with(tmp_path, [write(old=0.23, value=0.253), write(field="half", value=0.1265, old=0.3)])
    contract = lc.learn(run, figure(("q0", 1.1)))
    assert contract and contract["n_verified"] == 2
    assert contract["targets"][1]["transform_kind"] == "chain"
    assert contract["targets"][1]["scale"] == 0.115


def test_marker_extraction_ignores_curves_colorbars_and_other_entities(tmp_path):
    run = run_with(tmp_path, [write(value=0.3, old=0.2)])
    payload = {"axes": [{"title": "q0", "traces": [
        {"type": "line", "role": "curve", "x": [300, 300], "y": [0, 1]}]},
        {"title": "q0", "is_colorbar": True, "traces": [
            {"type": "line", "role": "vline", "x": [300, 300]}]}]}
    assert lc.learn(run, payload) is None
    payload["axes"][0]["traces"][0]["role"] = "vline"
    contract = lc.learn(run, payload)
    assert contract and contract["targets"][0]["plotly_axis"] == "x"
    payload["axes"][0]["title"] = "q1"
    assert lc.learn(run, payload) is None


def test_pair_single_point_marker_and_secondary_axis_are_owned(tmp_path):
    entry = {"op": "replace", "path": "/quam/qubit_pairs/q0-q1/gate/amplitude", "old": 0.2, "value": 0.22}
    run = run_with(tmp_path, [entry])
    payload = {"axes": [{"title": "q0-q1", "traces": [
        {"type": "scatter", "x": [275], "y": [0.9]}],
        "secondary_axes": [{"axis": "x", "map": {"parent": [250, 300], "forward": [1, 1.2]}}]}]}
    contract = lc.learn(run, payload)
    assert contract and contract["targets"][0]["transform_kind"] == "prefactor"
    assert contract["targets"][0]["path_template"] == "qubit_pairs.{pair}.gate.amplitude"
    assert contract["targets"][0]["scale"] == pytest.approx(0.0008)


def test_repeated_and_twin_panels_bind_to_the_converter_axes(tmp_path):
    run = run_with(tmp_path, [write(value=0.3)])
    panel = {"title": "q0", "traces": [{"type": "line", "role": "vline", "x": [300, 300], "y": [0, 1]}]}
    payload = {"axes": [panel, {"traces": []}, dict(panel),
                         {**panel, "shares_x_with": 2, "y_side": "right"}]}
    contract = lc.learn(run, payload)
    assert contract and contract["n_verified"] == 1
    assert {t["plotly_axis"] for t in contract["targets"]} == {"x", "x2"}
