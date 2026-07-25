"""Tests for core/path_match — does the quam_state Qualibrate writes match the
one SM has open? (gates the workbench nudge; kills the silent no-op)."""

from __future__ import annotations

import json
import os
import sys
import unicodedata
from pathlib import Path

import pytest

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


# ---------------------------------------------------------------------------
# fs_key — the canonical string identity for hashing/keying (per-OS fold)
# ---------------------------------------------------------------------------

def test_fs_key_resolves_spelling_variants(tmp_path):
    d = tmp_path / "chip"
    d.mkdir()
    # trailing slash / "." segments / relative-through resolve to one key
    assert pm.fs_key(str(d) + "/") == pm.fs_key(d)
    assert pm.fs_key(d / ".") == pm.fs_key(d)
    assert pm.fs_key(tmp_path / "other" / ".." / "chip") == pm.fs_key(d)


def test_fs_key_resolves_symlink_spelling(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(real)
    except OSError:                     # unprivileged Windows
        pytest.skip("symlinks unavailable")
    assert pm.fs_key(alias) == pm.fs_key(real)


def test_fs_key_nfc_folds_unicode(tmp_path):
    # macOS hands back NFD-decomposed names; both spellings must key together.
    d = tmp_path / "café"          # NFC
    nfd = unicodedata.normalize("NFD", str(d))
    assert nfd != str(d)                # the spellings really differ byte-wise
    assert pm.fs_key(nfd) == pm.fs_key(str(d))


# The platform-conditional fold is tested by patching path_match's OWN os/sys
# references: patching the real os.name to "nt" would break pathlib itself on
# POSIX (Path.__new__ dispatches WindowsPath on os.name).

def test_fs_key_no_case_fold_on_case_sensitive_hosts(monkeypatch):
    # Linux: case-variant paths are DISTINCT dirs — folding them aliased two
    # chips onto one working copy (the proven data-loss class).
    from types import SimpleNamespace
    monkeypatch.setattr(pm, "os", SimpleNamespace(name="posix", path=os.path))
    monkeypatch.setattr(sys, "platform", "linux")
    assert pm.fs_key("/A/B") != pm.fs_key("/a/b")


def test_fs_key_case_folds_on_windows(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(pm, "os", SimpleNamespace(name="nt", path=os.path))
    assert pm.fs_key("/A/B") == pm.fs_key("/a/b")


def test_fs_key_case_folds_on_mac(monkeypatch):
    # normcase is the IDENTITY on mac — fs_key must fold explicitly there.
    from types import SimpleNamespace
    monkeypatch.setattr(pm, "os", SimpleNamespace(name="posix", path=os.path))
    monkeypatch.setattr(sys, "platform", "darwin")
    assert pm.fs_key("/A/B") == pm.fs_key("/a/b")
