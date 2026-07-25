"""Orchestrator tests for core/regenerate.py (env-independent).

The subprocess build is mocked (it's exercised for real by the P2 probe); these
pin the orchestration wiring: same-folder guard, build-failure passthrough, and
that a successful build gets the OLD chip's values merged onto it with zero loss.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from quam_state_manager.core import regenerate


def test_same_folder_guard(tmp_path):
    out = regenerate.run_regenerate("py", tmp_path, {"qubits": []}, tmp_path)
    assert out["merge"] is None
    assert "must differ" in (out["error"] or "")


def test_same_folder_guard_symlink_spelling(tmp_path):
    # An alias spelling of the source dir must trip the samefile-grounded
    # guard — the build would otherwise write INTO the source chip.
    src = tmp_path / "chip"
    src.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(src)
    except OSError:                     # unprivileged Windows
        pytest.skip("symlinks unavailable")
    out = regenerate.run_regenerate("py", src, {"qubits": []}, alias)
    assert out["merge"] is None
    assert "must differ" in (out["error"] or "")


def test_same_folder_guard_case_insensitive_host(tmp_path, monkeypatch):
    # On macOS/Windows a case-variant spelling IS the source dir, but POSIX
    # resolve() doesn't case-canonicalize so resolve()-equality misses it.
    # Simulate the case-insensitive samefile verdict; the guard must fire.
    src = tmp_path / "Chip"
    src.mkdir()
    out_dir = tmp_path / "chip"
    out_dir.mkdir()                     # exists → same_folder branch is taken
    monkeypatch.setattr(regenerate.path_match, "same_folder", lambda a, b: True)
    out = regenerate.run_regenerate("py", src, {"qubits": []}, out_dir)
    assert out["merge"] is None
    assert "must differ" in (out["error"] or "")


def test_build_failure_passthrough(tmp_path, monkeypatch):
    monkeypatch.setattr(
        regenerate.config_generator, "run_generator",
        lambda *a, **k: {"ok": False, "status": "error", "error": "boom", "result": None},
    )
    out = regenerate.run_regenerate("py", tmp_path / "old", {"qubits": []}, tmp_path / "new")
    assert out["ok"] is False
    assert out["merge"] is None


def test_merge_applied_to_build_output(tmp_path, monkeypatch):
    # OLD = calibrated chip; the "build" produces a fresh structure (defaults).
    (tmp_path / "old").mkdir()
    old_state = {"qubits": {"q1": {"f_01": 5.1e9, "z": {"operations": {
        "cz_unipolar": {"length": 100}, "cz_flattop": {"length": 120, "sigma": 5}}}}},
        "active_qubit_names": ["q1"]}
    (tmp_path / "old" / "state.json").write_text(json.dumps(old_state))
    (tmp_path / "old" / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}))

    fresh = {"qubits": {"q1": {"f_01": 0.0, "z": {"operations": {
        "cz_unipolar": {"length": 16}}}}}, "active_qubit_names": ["q1"]}

    def fake_build(python_path, mode, spec, out_dir, timeout=300):
        out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "state.json").write_text(json.dumps(fresh))
        (out_dir / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}))
        return {"ok": True, "status": "ok", "error": None, "result": {}}

    monkeypatch.setattr(regenerate.config_generator, "run_generator", fake_build)
    out = regenerate.run_regenerate("py", tmp_path / "old", {"x": 1}, tmp_path / "new")

    assert out["ok"] is True
    m = out["merge"]
    assert m["residual_lost"] == []                 # nothing lost
    assert m["carried"] >= 1                         # f_01 calibrated value carried
    merged = json.loads((tmp_path / "new" / "state.json").read_text())
    assert merged["qubits"]["q1"]["f_01"] == 5.1e9   # tier1
    assert "cz_flattop" in merged["qubits"]["q1"]["z"]["operations"]  # tier2 graft


def test_spec_sidecar_written_and_preferred(tmp_path, monkeypatch):
    # A successful rebuild writes an EXACT spec sidecar; a later reconstruct of
    # the same folder prefers it (exact=True) over best-effort reconstruction.
    fresh = {"qubits": {"q1": {"f_01": 5.0e9}}, "active_qubit_names": ["q1"]}

    def fake_build(python_path, mode, spec, out_dir, timeout=300):
        out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "state.json").write_text(json.dumps(fresh))
        (out_dir / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}))
        return {"ok": True, "status": "ok", "error": None, "result": {}}

    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {"f_01": 5.1e9}}, "active_qubit_names": ["q1"]}))
    (tmp_path / "old" / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}))

    monkeypatch.setattr(regenerate.config_generator, "run_generator", fake_build)
    marker_spec = {"qubits": ["q1"], "pair_gate": "cz_fixed", "_marker": "exact-123"}
    out = regenerate.run_regenerate("py", tmp_path / "old", marker_spec, tmp_path / "new")
    assert out["ok"] is True

    side = tmp_path / "new" / ".regen" / "generate_spec.json"
    assert side.is_file()                                # sidecar written (subfolder)

    rec = regenerate.reconstruct_from_folder(tmp_path / "new")
    assert rec.exact is True
    assert rec.spec["_marker"] == "exact-123"            # exact spec, not inferred
    assert "populate" in rec.spec                        # populate refreshed from state


def test_sidecar_ignored_when_chip_changed(tmp_path):
    from quam_state_manager.core import regen_spec
    state = {"qubits": {"q1": {"f_01": 5.0e9}}}
    wiring = {"wiring": {}, "network": {}}
    (tmp_path / "state.json").write_text(json.dumps(state))
    (tmp_path / "wiring.json").write_text(json.dumps(wiring))
    regen_spec.write_spec_sidecar(tmp_path, {"_marker": "x"}, state, wiring)
    assert regen_spec.load_spec_sidecar(tmp_path, state, wiring)["_marker"] == "x"
    # chip edited out of band -> hash mismatch -> sidecar ignored
    changed = {"qubits": {"q1": {"f_01": 9.9e9}}}
    assert regen_spec.load_spec_sidecar(tmp_path, changed, wiring) is None
    rec = regenerate.reconstruct_from_folder  # fall back path is reconstruct_spec
    (tmp_path / "state.json").write_text(json.dumps(changed))
    assert rec(tmp_path).exact is False                  # inferred, not from sidecar


# --- real rebuilt output from the P2 probe (auto-skip when absent) ----------
_OLD = Path("<quam-states>/gen_2x3_cz_tunable")
_REBUILT = Path("/mnt/d/Work/state-manager/.tmp_p2/rebuilt")


@pytest.mark.skipif(not (_OLD.exists() and _REBUILT.exists()),
                    reason="real chip + rebuilt probe output not present")
def test_real_regenerate_zero_loss(tmp_path, monkeypatch):
    def fake_build(python_path, mode, spec, out_dir, timeout=300):
        out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_REBUILT / "state.json", out_dir / "state.json")
        shutil.copy2(_REBUILT / "wiring.json", out_dir / "wiring.json")
        return {"ok": True, "status": "ok", "error": None, "result": {}}

    monkeypatch.setattr(regenerate.config_generator, "run_generator", fake_build)
    out = regenerate.run_regenerate("py", _OLD, {"x": 1}, tmp_path / "new")
    assert out["ok"] is True
    assert out["merge"]["residual_lost"] == []
    assert out["merge"]["carried"] > 500
    assert out["merge"]["grafted"] > 50
