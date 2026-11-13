"""A fitted value the run itself wrote is not labelled "copy-only".

The Results tab said "copy-only ... isn't mapped to a chip state field" for
values the run's own node.json records as written: an RB fidelity written
as-is, and a CZ phase correction the node folds into ``phase_shift_*`` as
``(old - fit) mod 1``. ``fit_targets.written_by_run`` reads those records
(``patches`` and ``state_changes``; never the ``state_updates.json``
proposals) and the row says "written by this run" with the path.

Synthetic values, generic names.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import fit_targets as ft

PAIR = "qA1-qA2"


def _node(folder: Path, **extra):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "node.json").write_text(json.dumps(dict({
        "metadata": {"name": "x"}, "data": {}}, **extra)), encoding="utf-8")


def _changes(*rows):
    return {"state_changes": {"changes": [
        {"key": k, "attr": k.rsplit("/", 1)[-1], "old": o, "new": n} for k, o, n in rows]}}


def test_same_value_and_folded_rule_are_found(tmp_path):
    corr = 0.6123
    _node(tmp_path, **_changes(
        (f"#/qubit_pairs/{PAIR}/macros/cz_X/fidelity/InterleavedRB", None, 0.9871),
        (f"#/qubit_pairs/{PAIR}/macros/cz_X/phase_shift_control", 0.25, (0.25 - corr) % 1.0)))
    run = {"folder_path": str(tmp_path), "fit_results": {PAIR: {
        "fidelity": 0.9871, "control_phase_correction": corr,
        "target_phase_correction": 0.3, "success": True, "control_freq": 1.0}}}
    got = ft.written_by_run(run)[PAIR]
    assert got["fidelity"] == {"path": f"qubit_pairs.{PAIR}.macros.cz_X.fidelity.InterleavedRB",
                               "new": 0.9871, "basis": "same value"}
    assert got["control_phase_correction"]["path"].endswith(".phase_shift_control")
    assert got["control_phase_correction"]["basis"] == "(old - fit) mod 1"
    # no record for it, a bool, and a value no write carries under its key
    assert set(got) == {"fidelity", "control_phase_correction"}


def test_a_value_match_under_another_key_or_entity_is_not_claimed(tmp_path):
    _node(tmp_path, **_changes(
        (f"#/qubit_pairs/{PAIR}/macros/cz_X/amplitude", 0.1, 0.5),
        ("#/qubit_pairs/qA3-qA4/macros/cz_X/fidelity/InterleavedRB", None, 0.9)))
    run = {"folder_path": str(tmp_path),
           "fit_results": {PAIR: {"fidelity": 0.9, "visibility": 0.5}}}
    assert ft.written_by_run(run) == {}


def test_patches_spelling_and_proposals_ignored(tmp_path):
    _node(tmp_path, patches=[{"op": "replace", "path": f"/quam/qubit_pairs/{PAIR}/fidelity",
                              "value": 0.95, "old": 0.9}])
    (tmp_path / "state_updates.json").write_text(json.dumps({"operations": [
        {"op": "replace", "path": f"/quam/qubit_pairs/{PAIR}/epg", "value": 0.01}]}))
    run = {"folder_path": str(tmp_path), "fit_results": {PAIR: {"fidelity": 0.95, "epg": 0.01}}}
    got = ft.written_by_run(run)
    assert list(got[PAIR]) == ["fidelity"]


# ----- the Results row, rendered ----------------------------------------------

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}


def test_results_row_says_written_by_this_run(tmp_path):
    from quam_state_manager.web import routes as routes_mod
    from quam_state_manager.web.app import create_app
    live = tmp_path / "chips" / "live"
    live.mkdir(parents=True)
    st = {"qubits": {"qA1": {"id": "qA1"}, "qA2": {"id": "qA2"}},
          "qubit_pairs": {PAIR: {"macros": {"cz_X": {"fidelity": {}}}}}}
    (live / "state.json").write_text(json.dumps(st), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    root = tmp_path / "data"
    run = root / "2026-12-30" / "#7_37c_two_qubit_paired_interleaved_rb_010000"
    _node(run, metadata={"name": "37c_two_qubit_paired_interleaved_rb", "status": "successful",
                         "run_start": "2026-12-30T01:00:00", "run_end": "2026-12-30T01:00:01"},
          data={"parameters": {"model": {"qubit_pairs": [PAIR]}}, "outcomes": {}},
          id=7, parents=[], created_at="2026-12-30T01:00:00",
          **_changes((f"#/qubit_pairs/{PAIR}/macros/cz_X/fidelity/InterleavedRB", None, 0.9871)))
    (run / "data.json").write_text(json.dumps(
        {"fit_results": {PAIR: {"fidelity": 0.9871, "rms": 0.002}}}), encoding="utf-8")
    c.post("/workspace/add", data={"folder": str(root)})
    html = c.get(f"/dataset/{routes_mod._folder_key(root)}:7").data.decode()
    path = f"qubit_pairs.{PAIR}.macros.cz_X.fidelity.InterleavedRB"
    assert ">written by this run</span>" in html
    assert f"This run recorded writing 0.9871 to {path}" in html
    assert f'data-fit-path="{path}"' in html
    # the diagnostic beside it keeps its honest label
    assert html.count(">copy-only</span>") == 1
