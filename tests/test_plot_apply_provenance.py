"""Plot origins are optional metadata on the existing staging doors."""
import json

import pytest

from tests.state_model.repros._rig import Chip


ORIGIN = {"src": "plot_click", "run_uid": "run-7", "request_hash": "abc123",
          "text": "derived from run #7: learned from proposal; clicked * 2 + 1"}
PATH = "qubits.q1.T1"


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("bad", [None, [], "text", 3, True, {"extra": "x"},
                                  {"text": []}, {"text": None}, {"src": 7},
                                  {"text": "x" * 301}, {"run_uid": "x" * 301}])
def test_bad_origin_refuses_without_changes(tmp_path, batch, bad):
    chip = Chip(tmp_path)
    before = (chip.log(), chip.journal(), chip.live_state(), chip.ctx["store"].mutation_seq)
    if batch:
        r = chip.c.post("/field/edit-batch", json={
            "updates": [{"dot_path": PATH, "value": "0.00004"}], "provenance": bad})
    else:
        r = chip.c.post("/field/edit", data={
            "dot_path": PATH, "value": "0.00004", "provenance": json.dumps(bad)})
    assert r.status_code == 400, r.data
    assert (chip.log(), chip.journal(), chip.live_state(), chip.ctx["store"].mutation_seq) == before


def test_malformed_form_origin_and_bad_late_batch_row(tmp_path):
    chip = Chip(tmp_path)
    for raw in ("", "{", "[" * 2000):
        r = chip.c.post("/field/edit", data={"dot_path": PATH, "value": "4", "provenance": raw})
        assert r.status_code == 400
    r = chip.c.post("/field/edit-batch", json={"updates": [
        {"dot_path": PATH, "value": "4"},
        {"dot_path": "qubits.q2.T1", "value": "5", "provenance": {"text": 3}}]})
    assert r.status_code == 400
    assert chip.log() == []


def test_optional_bounded_origin(tmp_path):
    from quam_state_manager.web.request_body import provenance
    chip = Chip(tmp_path)
    with chip.app.app_context():
        assert provenance({}) is None
        assert provenance({"provenance": {}}) == {}
        full = {key: "x" * 300 for key in ORIGIN}
        assert provenance({"provenance": full}) == full
        assert provenance({"provenance": json.dumps(ORIGIN)}, form=True) == ORIGIN
    assert chip.edit(PATH, "0.00004", provenance=json.dumps(ORIGIN)).get_json()["ok"]
