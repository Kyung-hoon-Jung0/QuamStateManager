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
    # exists → same_folder branch is taken. exist_ok: on a real case-insensitive
    # FS (native Windows) this IS src and already exists — the point of the test.
    out_dir.mkdir(exist_ok=True)
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


def test_sidecar_found_via_fallback_dir(tmp_path):
    # r13: the reconstruct route reads the WORKING COPY, but the exact-spec
    # sidecar only ever lands in the chip's real folder — ``sidecar_dirs`` lets
    # the caller offer the live folder, hash-gated on the CONTENT actually read
    # (so a diverged working copy never picks up a stale live sidecar).
    from quam_state_manager.core import regen_spec
    state = {"qubits": {"q1": {"f_01": 5.0e9}}}
    wiring = {"wiring": {}, "network": {}}
    live = tmp_path / "live"
    wc = tmp_path / "wc"
    for d in (live, wc):
        d.mkdir()
        (d / "state.json").write_text(json.dumps(state))
        (d / "wiring.json").write_text(json.dumps(wiring))
    regen_spec.write_spec_sidecar(
        live, {"qubits": ["q1"], "_marker": "live-sc"}, state, wiring)
    assert regenerate.reconstruct_from_folder(wc).exact is False   # wc alone: no sidecar
    rec = regenerate.reconstruct_from_folder(wc, sidecar_dirs=(live,))
    assert rec.exact is True
    assert rec.spec["_marker"] == "live-sc"
    # Working copy diverges → the hash gate refuses the live sidecar.
    (wc / "state.json").write_text(json.dumps({"qubits": {"q1": {"f_01": 1.0}}}))
    assert regenerate.reconstruct_from_folder(wc, sidecar_dirs=(live,)).exact is False


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


def test_class_schemas_flow_into_merge(tmp_path, monkeypatch):
    # The build result's class_schemas must reach merge_states: an old-stack
    # field on a __class__-tagged dict is dropped and reported, not grafted.
    (tmp_path / "old").mkdir()
    old_state = {"qubit_pairs": {"p1": {"macros": {"cz": {
        "__class__": "qb.CZGate", "phase": 0.2, "duration_control": None}}}}}
    (tmp_path / "old" / "state.json").write_text(json.dumps(old_state))
    (tmp_path / "old" / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}))
    fresh = {"qubit_pairs": {"p1": {"macros": {"cz": {
        "__class__": "qb.CZGate", "phase": 0.0, "duration_qubit": None}}}}}

    def fake_build(python_path, mode, spec, out_dir, timeout=300):
        out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "state.json").write_text(json.dumps(fresh))
        (out_dir / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}))
        return {"ok": True, "status": "ok", "error": None,
                "result": {"class_schemas": {
                    "qb.CZGate": ["phase", "duration_qubit"]}}}

    monkeypatch.setattr(regenerate.config_generator, "run_generator", fake_build)
    out = regenerate.run_regenerate("py", tmp_path / "old", {"x": 1}, tmp_path / "new")

    assert out["ok"] is True
    assert out["merge"]["schema_dropped"] == 1
    assert out["merge"]["schema_dropped_paths"] == [
        "qubit_pairs.p1.macros.cz.duration_control"]
    merged = json.loads((tmp_path / "new" / "state.json").read_text())
    cz = merged["qubit_pairs"]["p1"]["macros"]["cz"]
    assert "duration_control" not in cz              # gate fired
    assert cz["phase"] == 0.2                        # tier1 carry intact


def test_the_report_names_the_class_substitution_behind_the_drop(tmp_path, monkeypatch):
    """docs/202 — the customer chip's readout pulse is the LAB's own class and a
    rebuild produces the stock one, so every field only their class declares is
    dropped. That drop was reported; the substitution causing it was not, and it
    is the only part a reader can act on."""
    lab, stock = "quam_config.cw.ComplexWeightsReadoutPulse", "quam.pulses.SquareReadoutPulse"
    (tmp_path / "old").mkdir()
    old_state = {"qubits": {"q1": {"resonator": {"operations": {"readout": {
        "__class__": lab, "amplitude": 0.1, "weights_real": [1.0]}}}}}}
    (tmp_path / "old" / "state.json").write_text(json.dumps(old_state))
    (tmp_path / "old" / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}))
    fresh = {"qubits": {"q1": {"resonator": {"operations": {"readout": {
        "__class__": stock, "amplitude": 0.0}}}}}}

    def fake_build(python_path, mode, spec, out_dir, timeout=300):
        out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "state.json").write_text(json.dumps(fresh))
        (out_dir / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}))
        return {"ok": True, "status": "ok", "error": None,
                "result": {"class_schemas": {stock: ["amplitude"]}}}

    monkeypatch.setattr(regenerate.config_generator, "run_generator", fake_build)
    out = regenerate.run_regenerate("py", tmp_path / "old", {"x": 1}, tmp_path / "new")

    m = out["merge"]
    assert m["schema_dropped"] == 1                   # the consequence, as before
    assert m["class_changed"] == 1                    # and now the cause
    assert m["class_changed_total"] == 1
    assert m["class_changed_paths"] == [{
        "path": "qubits.q1.resonator.operations.readout",
        "old": lab, "new": stock}]


def test_a_rebuild_that_changes_no_class_reports_none(tmp_path, monkeypatch):
    # The ordinary case must stay quiet, or the panel cries wolf on every build.
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {"__class__": "qb.Transmon", "f_01": 5e9}}}))
    (tmp_path / "old" / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}))

    def fake_build(python_path, mode, spec, out_dir, timeout=300):
        out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "state.json").write_text(json.dumps(
            {"qubits": {"q1": {"__class__": "qb.Transmon", "f_01": 0.0}}}))
        (out_dir / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}))
        return {"ok": True, "status": "ok", "error": None, "result": {}}

    monkeypatch.setattr(regenerate.config_generator, "run_generator", fake_build)
    out = regenerate.run_regenerate("py", tmp_path / "old", {"x": 1}, tmp_path / "new")
    assert out["merge"]["class_changed"] == 0
    assert out["merge"]["class_changed_paths"] == []


class TestTheSourceClassesTheEnvHolds:
    """docs/202 §15 -- the merge keeps a lab subclass only when the BUILD env
    imports it; that answer comes from the per-env schema probe."""

    LAB = "quam_config.cw.ComplexWeightsReadoutPulse"
    STOCK = "quam.pulses.SquareReadoutPulse"

    def _probe(self, classes):
        def probe(python_path, class_paths, instance_path=None):
            return {"ok": True, "classes": classes}
        return probe

    def test_only_importable_dataclasses_are_offered(self):
        probe = self._probe({
            self.LAB: {"importable": True, "is_dataclass": True,
                       "bases": [self.STOCK], "fields": {"weights_real": {}}},
            # Deliberately hostile: the real probe returns fields=None for any
            # class it cannot import, so a realistic entry is filtered by the
            # fields check alone and the `importable` guard went untested
            # (the sweep's GREEN). This one is complete in every other way.
            "lab.NotImportable": {"importable": False, "is_dataclass": True,
                                  "bases": [self.STOCK], "fields": {"weights_real": {}}},
            "lab.NoFields": {"importable": True, "is_dataclass": True,
                             "bases": [self.STOCK], "fields": None},
        })
        keep = regenerate._source_classes_the_env_holds(
            "py", {"x": {"__class__": self.LAB}}, probe=probe)
        assert keep == {self.LAB: {"bases": [self.STOCK], "fields": ["weights_real"]}}

    def test_a_failed_probe_keeps_nothing(self):
        def boom(*a, **k):
            raise RuntimeError("env vanished")
        assert regenerate._source_classes_the_env_holds(
            "py", {"x": {"__class__": self.LAB}}, probe=boom) is None
        assert regenerate._source_classes_the_env_holds(
            "py", {"x": {"__class__": self.LAB}},
            probe=lambda *a, **k: {"ok": False, "classes": {}}) is None

    def test_the_report_says_what_was_kept(self, tmp_path, monkeypatch):
        (tmp_path / "old").mkdir()
        (tmp_path / "old" / "state.json").write_text(json.dumps(
            {"qubits": {"q1": {"resonator": {"operations": {"readout": {
                "__class__": self.LAB, "amplitude": 0.1, "weights_real": [1.0, 2.0]}}}}}}))
        (tmp_path / "old" / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}))

        def fake_build(python_path, mode, spec, out_dir, timeout=300):
            out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "state.json").write_text(json.dumps(
                {"qubits": {"q1": {"resonator": {"operations": {"readout": {
                    "__class__": self.STOCK, "amplitude": 0.0}}}}}}))
            (out_dir / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}))
            return {"ok": True, "status": "ok", "error": None,
                    "result": {"class_schemas": {self.STOCK: ["amplitude"]}}}

        monkeypatch.setattr(regenerate.config_generator, "run_generator", fake_build)
        probe = self._probe({self.LAB: {"importable": True, "is_dataclass": True,
                                        "bases": [self.STOCK],
                                        "fields": {"amplitude": {}, "weights_real": {}}}})
        out = regenerate.run_regenerate("py", tmp_path / "old", {"x": 1},
                                        tmp_path / "new", source_probe=probe)
        m = out["merge"]
        assert m["class_kept"] == 1 and m["class_changed"] == 0
        assert m["class_kept_paths"] == [{"path": "qubits.q1.resonator.operations.readout",
                                          "cls": self.LAB}]
        merged = json.loads((tmp_path / "new" / "state.json").read_text())
        ro = merged["qubits"]["q1"]["resonator"]["operations"]["readout"]
        assert ro["__class__"] == self.LAB and ro["weights_real"] == [1.0, 2.0]

    def test_the_root_the_spec_names_is_the_root_written(self, tmp_path, monkeypatch):
        """QA regenerate-r2-16, end to end: spec.quam_class = the stock root,
        the build writes it, the source's lab root is importable and
        subclasses it -- state.json must still declare the stock root."""
        lab_root, stock_root = "quam_config.my_quam.Quam", "qb.FluxTunableQuam"
        (tmp_path / "old").mkdir()
        (tmp_path / "old" / "state.json").write_text(json.dumps(
            {"__class__": lab_root, "qubits": {}}))
        (tmp_path / "old" / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}))

        def fake_build(python_path, mode, spec, out_dir, timeout=300):
            out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "state.json").write_text(json.dumps(
                {"__class__": spec["quam_class"], "qubits": {}}))
            (out_dir / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}))
            return {"ok": True, "status": "ok", "error": None,
                    "result": {"class_schemas": {stock_root: ["qubits"]}}}

        monkeypatch.setattr(regenerate.config_generator, "run_generator", fake_build)
        probe = self._probe({lab_root: {"importable": True, "is_dataclass": True,
                                        "bases": [stock_root],
                                        "fields": {"qubits": {}}}})
        out = regenerate.run_regenerate("py", tmp_path / "old",
                                        {"quam_class": stock_root},
                                        tmp_path / "new", source_probe=probe)
        assert out["merge"]["class_kept"] == 0
        merged = json.loads((tmp_path / "new" / "state.json").read_text())
        assert merged["__class__"] == stock_root


class TestCollectClassSchemas:
    """run_build._collect_class_schemas — the in-env harvest feeding the gate.

    run_build is import-light (heavy QM imports live inside functions), so the
    harvest is unit-testable here with a fake module injected into sys.modules.
    """

    def test_harvest_fields_and_omit_unimportable(self, tmp_path, monkeypatch):
        import dataclasses as dc
        import sys
        import types

        import quam_state_manager.generator.run_build as run_build

        mod = types.ModuleType("fake_quam_mod")

        @dc.dataclass
        class Gate:
            duration_qubit: int = 0
            phase: float = 0.0

        mod.Gate = Gate
        monkeypatch.setitem(sys.modules, "fake_quam_mod", mod)

        state = {"pairs": {"p": {
            "macros": {"cz": {"__class__": "fake_quam_mod.Gate"}},
            "ghost": {"__class__": "no_such_mod.Nope"},   # omitted, not fatal
        }}}
        sp = tmp_path / "state.json"
        sp.write_text(json.dumps(state))
        wp = tmp_path / "wiring.json"
        wp.write_text(json.dumps({"wiring": {}}))

        schemas = run_build._collect_class_schemas(sp, wp)
        assert schemas == {"fake_quam_mod.Gate": ["duration_qubit", "phase"]}

    def test_unreadable_artefacts_yield_empty(self, tmp_path):
        import quam_state_manager.generator.run_build as run_build
        assert run_build._collect_class_schemas(
            tmp_path / "none.json", tmp_path / "none2.json") == {}


class TestMatchPopulatePairs:
    """run_build._match_populate_pairs — the three-tier per-pair seed lookup."""

    def test_three_tiers_resolve_and_unmatched_warns(self):
        import quam_state_manager.generator.run_build as rb
        pop = {"q1-0": {"a": 1},           # exact
               "q0-q3": {"b": 2},          # spelled form -> q0-3
               "q2-5": {"c": 3},           # membership: built id is q5-2 (flipped)
               "q9-7": {"d": 4}}           # matches nothing
        resolved, unmatched = rb._match_populate_pairs(
            pop, ["q1-0", "q0-3", "q5-2"])
        assert resolved == {"q1-0": {"a": 1}, "q0-3": {"b": 2}, "q5-2": {"c": 3}}
        assert unmatched == ["q9-7"]

    def test_exact_key_wins_over_flipped_duplicate(self):
        import quam_state_manager.generator.run_build as rb
        pop = {"q1-0": {"exact": True}, "q0-1": {"flipped": True}}
        resolved, unmatched = rb._match_populate_pairs(pop, ["q1-0"])
        assert resolved == {"q1-0": {"exact": True}}
        assert unmatched == []


def test_populate_baseline_protects_wizard_edit(tmp_path, monkeypatch):
    # r16 (docs/72): a value the user CHANGED in the wizard's Populate step
    # must survive the merge; untouched values still tier-1 carry.
    (tmp_path / "old").mkdir()
    old_state = {"qubits": {"q1": {"f_01": 5.0e9, "anharmonicity": -2.0e8}},
                 "active_qubit_names": ["q1"]}
    (tmp_path / "old" / "state.json").write_text(json.dumps(old_state))
    (tmp_path / "old" / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}))

    # build applies the edited populate: f_01 = 5.2e9 (user edit), anharm default
    fresh = {"qubits": {"q1": {"f_01": 5.2e9, "anharmonicity": -1.9e8}},
             "active_qubit_names": ["q1"]}

    def fake_build(python_path, mode, spec, out_dir, timeout=300):
        out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "state.json").write_text(json.dumps(fresh))
        (out_dir / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}))
        return {"ok": True, "status": "ok", "error": None, "result": {}}

    monkeypatch.setattr(regenerate.config_generator, "run_generator", fake_build)
    spec = {"qubits": ["q1"],
            "populate": {"qubit": {"q1": {"RF_freq": 5.2e9}}}}
    baseline = {"qubit": {"q1": {"RF_freq": 5.0e9}}}   # what the wizard showed
    out = regenerate.run_regenerate("py", tmp_path / "old", spec,
                                    tmp_path / "new",
                                    populate_baseline=baseline)
    assert out["ok"] is True
    merged = json.loads((tmp_path / "new" / "state.json").read_text())
    assert merged["qubits"]["q1"]["f_01"] == 5.2e9        # user edit KEPT
    assert merged["qubits"]["q1"]["anharmonicity"] == -2.0e8  # tier-1 carry
    assert out["merge"]["populate_protected"] == 1
    assert out["merge"]["populate_protected_paths"] == ["qubits.q1.f_01"]


def test_no_baseline_is_legacy_byte_identical(tmp_path, monkeypatch):
    (tmp_path / "old").mkdir()
    old_state = {"qubits": {"q1": {"f_01": 5.0e9}}, "active_qubit_names": ["q1"]}
    (tmp_path / "old" / "state.json").write_text(json.dumps(old_state))
    (tmp_path / "old" / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}))
    fresh = {"qubits": {"q1": {"f_01": 5.2e9}}, "active_qubit_names": ["q1"]}

    def fake_build(python_path, mode, spec, out_dir, timeout=300):
        out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "state.json").write_text(json.dumps(fresh))
        (out_dir / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}))
        return {"ok": True, "status": "ok", "error": None, "result": {}}

    monkeypatch.setattr(regenerate.config_generator, "run_generator", fake_build)
    out = regenerate.run_regenerate(
        "py", tmp_path / "old",
        {"qubits": ["q1"], "populate": {"qubit": {"q1": {"RF_freq": 5.2e9}}}},
        tmp_path / "new")
    merged = json.loads((tmp_path / "new" / "state.json").read_text())
    assert merged["qubits"]["q1"]["f_01"] == 5.0e9        # legacy tier-1 revert
    assert out["merge"]["populate_protected"] == 0


def test_scripts_dir_param_replaces_hardcoded_folder(tmp_path, monkeypatch):
    # r16 ⓪-4: /regenerate/build's scripts_dir must land the bundle THERE,
    # not in the hardcoded <out>/build_scripts.
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {}}, "active_qubit_names": []}))
    (tmp_path / "old" / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}))

    def fake_build(python_path, mode, spec, out_dir, timeout=300):
        out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "state.json").write_text(json.dumps({"qubits": {"q1": {}}}))
        (out_dir / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}))
        return {"ok": True, "status": "ok", "error": None, "result": {}}

    monkeypatch.setattr(regenerate.config_generator, "run_generator", fake_build)
    scripts = tmp_path / "my_scripts" / "state_gen_scripts"
    out = regenerate.run_regenerate("py", tmp_path / "old", {"qubits": ["q1"]},
                                    tmp_path / "new", scripts_dir=scripts)
    assert out["ok"] is True
    if out.get("script"):                       # emitter succeeded
        assert (scripts / "02_build_machine.py").exists()
        assert not (tmp_path / "new" / "build_scripts").exists()
        assert out["script"] == str(scripts)


class TestTheQaRegenerateFixesEndToEnd:
    """QA F1 / r2-09 / r2-10 / r2-15 through run_regenerate (build mocked):
    what the merge learned must reach the report the panel reads."""

    LF = "quam.components.ports.analog_outputs.LFFEMAnalogOutputPort"

    def _write(self, folder, state, wiring):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "state.json").write_text(json.dumps(state))
        (folder / "wiring.json").write_text(json.dumps(wiring))

    def _flux_chip(self, port, delay, ff):
        state = {"qubits": {"q1": {"z": {
            "opx_output": "#/wiring/qubits/q1/z/opx_output", "__class__": "qb.FluxLine"}}},
            "ports": {"analog_outputs": {"con1": {"5": {str(port): {
                "controller_id": "con1", "fem_id": 5, "port_id": port,
                "delay": delay, "feedforward_filter": ff, "__class__": self.LF}}}}}}
        wiring = {"wiring": {"qubits": {"q1": {"z": {
            "opx_output": f"#/ports/analog_outputs/con1/5/{port}"}}}}, "network": {}}
        return state, wiring

    def _fake(self, state, wiring, result=None, seen=None):
        def fake_build(python_path, mode, spec, out_dir, timeout=300):
            if seen is not None:
                seen.append(spec)
            self._write(Path(out_dir), state, wiring)
            return {"ok": True, "status": "ok", "error": None, "result": result or {}}
        return fake_build

    def test_a_moved_flux_line_takes_its_filters_and_the_report_names_it(
            self, tmp_path, monkeypatch):
        self._write(tmp_path / "old", *self._flux_chip(1, 37, [0.1] * 48))
        monkeypatch.setattr(regenerate.config_generator, "run_generator",
                            self._fake(*self._flux_chip(6, 141, None)))
        out = regenerate.run_regenerate("py", tmp_path / "old", {"x": 1},
                                        tmp_path / "new",
                                        source_probe=lambda *a, **k: {"ok": False})
        m = out["merge"]
        assert m["ports_moved"] == [{"from": "ports.analog_outputs.con1.5.1",
                                     "to": "ports.analog_outputs.con1.5.6",
                                     "owner": "q1.z"}]
        assert m["ports_moved_total"] == 1 and m["residual_lost"] == []
        merged = json.loads((tmp_path / "new" / "state.json").read_text())
        p6 = merged["ports"]["analog_outputs"]["con1"]["5"]["6"]
        assert p6["delay"] == 37 and p6["feedforward_filter"] == [0.1] * 48

    def test_the_spec_twpa_list_reaches_the_merge(self, tmp_path, monkeypatch):
        old_state = {"twpas": {"twpa1": {"id": "twpa1", "settling_time": 40,
                     "pump": {"opx_output": "#/wiring/twpas/twpa1/p/opx_output"}}}}
        old_wiring = {"wiring": {"twpas": {"twpa1": {"p": {
            "opx_output": "#/ports/mw_outputs/con1/3/7"}}}}, "network": {}}
        self._write(tmp_path / "old", old_state, old_wiring)
        new_state = {"twpas": {"twpaMain": {"id": "twpaMain", "settling_time": 0}}}
        new_wiring = {"wiring": {"twpas": {"twpaMain": {"p": {
            "opx_output": "#/ports/mw_outputs/con1/3/7"}}}}, "network": {}}
        monkeypatch.setattr(regenerate.config_generator, "run_generator",
                            self._fake(new_state, new_wiring))
        out = regenerate.run_regenerate(
            "py", tmp_path / "old", {"twpas": [{"id": "twpaMain", "qubits": []}]},
            tmp_path / "new", source_probe=lambda *a, **k: {"ok": False})
        m = out["merge"]
        assert m["twpa_wiring_carried"] == 0
        assert m["twpas_removed"] == [{"id": "twpa1", "lost": 2}]   # id + settling_time
        merged = json.loads((tmp_path / "new" / "state.json").read_text())
        assert set(merged["twpas"]) == {"twpaMain"}

    def test_a_spec_without_twpas_keeps_the_graft_all_fallback(self):
        assert regenerate._spec_twpa_ids({"x": 1}) is None
        assert regenerate._spec_twpa_ids({"twpas": ["twpa1", {"id": " B "}, {"id": ""}]}) \
            == ["twpa1", "B"]

    def test_the_env_view_gates_the_graft(self, tmp_path, monkeypatch):
        drag = "qb04.pulses.DragCosinePulse"
        xy = "qb.XYDriveMW"
        old_state = {"qubits": {"q1": {"xy": {"__class__": xy, "operations": {
            "EF_x180": {"__class__": drag, "amplitude": 0.2}}}}}}
        self._write(tmp_path / "old", old_state, {"wiring": {}, "network": {}})
        new_state = {"qubits": {"q1": {"xy": {"__class__": xy, "operations": {}}}}}
        monkeypatch.setattr(regenerate.config_generator, "run_generator",
                            self._fake(new_state, {"wiring": {}, "network": {}}))
        calls = []

        def probe(python_path, class_paths, instance_path=None):
            calls.append(tuple(class_paths))
            return {"ok": True, "classes": {
                xy: {"importable": True, "is_dataclass": True, "bases": [],
                     "fields": {"operations": {}}},
                drag: {"importable": False, "is_dataclass": False, "fields": None}}}
        out = regenerate.run_regenerate("py", tmp_path / "old", {"x": 1},
                                        tmp_path / "new", source_probe=probe)
        assert len(calls) == 1, "one probe answers keep_classes AND the gate"
        assert out["merge"]["schema_dropped_paths"] == ["qubits.q1.xy.operations.EF_x180"]
        merged = json.loads((tmp_path / "new" / "state.json").read_text())
        assert merged["qubits"]["q1"]["xy"]["operations"] == {}

    def test_a_reversed_pair_is_named_with_its_loss(self, tmp_path, monkeypatch):
        def pair(c, t, **kw):
            return {"qubit_control": f"#/qubits/{c}", "qubit_target": f"#/qubits/{t}", **kw}
        self._write(tmp_path / "old", {"qubits": {"q2": {}, "q3": {}}, "qubit_pairs": {
            "q2-3": pair("q2", "q3", extras={"cz_plan": 1, "bench": 2})}},
            {"wiring": {}, "network": {}})
        monkeypatch.setattr(regenerate.config_generator, "run_generator", self._fake(
            {"qubits": {"q2": {}, "q3": {}}, "qubit_pairs": {"q3-2": pair("q3", "q2")}},
            {"wiring": {}, "network": {}}))
        out = regenerate.run_regenerate("py", tmp_path / "old", {"x": 1},
                                        tmp_path / "new",
                                        source_probe=lambda *a, **k: {"ok": False})
        assert out["merge"]["pairs_reversed"] == [{"old": "q2-3", "new": "q3-2", "lost": 2}]
