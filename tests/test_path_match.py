"""Tests for core/path_match — does the quam_state Qualibrate writes match the
one SM has open? (gates the workbench nudge; kills the silent no-op)."""

from __future__ import annotations

import json
from pathlib import Path

from quam_state_manager.core import path_match as pm


def _chip(folder: Path, qubits, *, network="cluster1", pairs=()) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    state = {"qubits": {q: {"id": q} for q in qubits},
             "qubit_pairs": {p: {} for p in pairs}}
    wiring = {"network": {"cluster_name": network}} if network else {}
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    return folder


def test_qb_unresolved():
    v = pm.verdict(None, "/some/sm")
    assert v["state"] == pm.QB_UNRESOLVED and "reason" in v


def test_qb_unresolved_passes_reason():
    v = pm.verdict(None, "/some/sm", qb_reason="config missing")
    assert v["state"] == pm.QB_UNRESOLVED and v["reason"] == "config missing"


def test_sm_empty(tmp_path):
    qb = _chip(tmp_path / "qb", ["q1"])
    assert pm.verdict(qb, None)["state"] == pm.SM_EMPTY


def test_same_folder_is_linked(tmp_path):
    qb = _chip(tmp_path / "chip", ["q1", "q2"])
    assert pm.verdict(str(qb), str(qb))["state"] == pm.LINKED
    # trailing slash / "." segments still resolve to the same folder
    assert pm.verdict(str(qb) + "/", str(qb / "."))["state"] == pm.LINKED


def test_same_chip_different_folder(tmp_path):
    a = _chip(tmp_path / "live", ["q1", "q2"], network="clusterX")
    b = _chip(tmp_path / "exp_copy", ["q1", "q2"], network="clusterX")
    assert pm.verdict(a, b)["state"] == pm.LINKED_DIFFERENT_FOLDER


def test_different_chip_is_mismatch(tmp_path):
    a = _chip(tmp_path / "a", ["q1", "q2"], network="clusterA")
    b = _chip(tmp_path / "b", ["q7", "q8"], network="clusterB")
    assert pm.verdict(a, b)["state"] == pm.MISMATCH


def test_same_network_different_qubits_is_mismatch(tmp_path):
    # RENAMED (same network, different labels) → treated as mismatch
    a = _chip(tmp_path / "a", ["q1", "q2"], network="same")
    b = _chip(tmp_path / "b", ["q1", "q9"], network="same")
    assert pm.verdict(a, b)["state"] == pm.MISMATCH


def test_indeterminate_when_a_fingerprint_is_unreadable(tmp_path):
    a = _chip(tmp_path / "a", ["q1"])
    b = tmp_path / "empty"
    b.mkdir()  # exists but has no state.json → fingerprint None
    assert pm.verdict(a, b)["state"] == pm.INDETERMINATE


def test_is_linked_helper():
    assert pm.is_linked(pm.LINKED) and pm.is_linked(pm.LINKED_DIFFERENT_FOLDER)
    assert not pm.is_linked(pm.MISMATCH) and not pm.is_linked(pm.QB_UNRESOLVED)


def test_chip_label_uses_folder_name_for_per_chip_layout(tmp_path):
    # <workspace>/LabA/state.json  ->  "LabA"
    chip = _chip(tmp_path / "LabA", ["q1"])
    assert pm.chip_label(chip) == "LabA"


def test_chip_label_falls_back_for_generic_state_dir(tmp_path):
    # <chip>/quam_state/state.json  ->  the parent chip name
    chip = _chip(tmp_path / "Soprano" / "quam_state", ["q1"])
    assert pm.chip_label(chip) == "Soprano"
