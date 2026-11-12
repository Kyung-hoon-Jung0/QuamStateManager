"""The lab layout of the coupler-only CZ (`cz_coupler_only`): pure checks (decisions T-2).

Fixed-frequency qubits with no flux line, a tunable coupler per pair, the lab's
own CouplerCZGate under `cz_coupler_only`. Everything here runs without the QM
stack; the real build is the live test (T-5).
"""
from quam_state_manager.core import capabilities as cap
from quam_state_manager.core import config_generator, coupler_cz_layout as ccl
from quam_state_manager.generator.probe_capabilities import CATALOG

LAB_ROOT = {"path": "quam_config.my_quam.Quam", "importable": True, "holds_qdac": False, "fixed_root": False}
STOCK_FLUX = {"path": "quam_builder.architecture.superconducting.qpu.FluxTunableQuam",
              "importable": True, "holds_qdac": False, "fixed_root": False}
STOCK_FIXED = {"path": "quam_builder.architecture.superconducting.qpu.FixedFrequencyQuam",
               "importable": True, "holds_qdac": False, "fixed_root": True}


def _spec(shape=None, extra_lines=()):
    lines = []
    for q in ("q1", "q2"):
        lines += [{"element": q, "line": "drive"}, {"element": q, "line": "resonator"}]
    lines.append({"element": "q1-2", "line": "coupler"})
    lines += list(extra_lines)
    pairs = {"q1-2": {"coupler_pulse_shape": shape}} if shape else {}
    return {"qubits": ["q1", "q2"], "qubit_pairs": [["q1", "q2"]], "lines": lines,
            "pair_gate": "cz_coupler_only", "network": {}, "instruments": {},
            "populate": {"pairs": pairs}}


def _manifest(available=None, roots=(LAB_ROOT, STOCK_FLUX, STOCK_FIXED)):
    caps = {cid: {"available": True} for cid in cap.REGISTRY}
    for cid, ok in (available or {}).items():
        caps[cid] = {"available": ok}
    return {"capabilities": caps, "qpu_roots": list(roots)}


# --- names ---------------------------------------------------------------------

def test_names_match_the_lab_helper():
    assert ccl.MACRO_KEY == "cz_coupler_only" and ccl.ALIAS_KEY == "cz"
    assert ccl.pulse_key("q1", "q2") == "cz_coupler_only_pulse_q1_q2"
    assert ccl.flattop_length(60) == 76          # ceil((60 + 2*8)/4)*4
    assert ccl.flattop_length(61) == 80


def test_the_probe_and_the_registry_name_the_lab_classes():
    assert CATALOG["pair.coupler_cz_lab"] == {"module": ccl.LAB_GATE_MODULE, "attr": ccl.LAB_GATE_CLASS}
    assert CATALOG["pulse.snz_two_flux"] == {"module": ccl.LAB_SNZ_MODULE, "attr": ccl.LAB_SNZ_CLASS}
    assert cap.REGISTRY["pair.coupler_cz_lab"]["severity"] == cap.BLOCKER


# --- validation (D-12) -----------------------------------------------------------

def test_the_mode_validates_without_qubit_flux_lines():
    errs = config_generator.validate_spec(_spec())
    assert not [e for e in errs if "pair_gate" in e or "coupler lines need qubit flux" in e], errs


def test_a_qubit_flux_line_is_refused():
    errs = config_generator.validate_spec(_spec(extra_lines=[{"element": "q1", "line": "flux"}]))
    assert any("cz_coupler_only requires no qubit z line" in e for e in errs), errs


def test_an_unknown_pulse_shape_is_refused():
    errs = config_generator.validate_spec(_spec(shape="gauss"))
    assert any("coupler_pulse_shape" in e for e in errs), errs


# --- capabilities (D-6) ------------------------------------------------------------

def test_required_capabilities_follow_the_shapes():
    req = cap.required_capabilities(_spec())
    assert {"pair.coupler_cz_lab", "pulse.cr_flattop", "qpu.flux_tunable"} <= req
    assert "pair.coupler_cz" not in req and "pair.cz_gate" not in req and "pulse.snz_two_flux" not in req
    req = cap.required_capabilities(_spec(shape="snz"))
    assert "pulse.snz_two_flux" in req and "pulse.cr_flattop" not in req


def test_the_lab_gate_missing_blocks_the_build():
    out = cap.assess(_spec(), _manifest({"pair.coupler_cz_lab": False}))
    assert not out["buildable"]
    assert [b["id"] for b in out["blockers"]] == ["pair.coupler_cz_lab"]


def test_an_snz_pair_needs_the_snz_class():
    out = cap.assess(_spec(shape="snz"), _manifest({"pulse.snz_two_flux": False}))
    assert [b["id"] for b in out["blockers"]] == ["pulse.snz_two_flux"]


def test_the_extension_gate_is_not_needed():
    out = cap.assess(_spec(), _manifest({"pair.coupler_cz": False}))
    assert out["buildable"], out["blockers"]


# --- root (D-3, D-7, Q-6) ------------------------------------------------------------

def test_the_lab_root_is_chosen_first():
    out = cap.qpu_root_check(_spec(), _manifest(roots=(STOCK_FLUX, STOCK_FIXED, LAB_ROOT)))
    assert out["chosen"] == LAB_ROOT["path"] and out["blocker"] is None and out["warning"] is None


def test_a_stock_flux_root_builds_with_a_warning():
    out = cap.qpu_root_check(_spec(), _manifest(roots=(STOCK_FLUX, STOCK_FIXED)))
    assert out["chosen"] == STOCK_FLUX["path"] and out["blocker"] is None
    assert "initialize_qpu" in out["warning"]


def test_a_declared_fixed_root_is_refused():
    spec = _spec()
    spec["quam_class"] = STOCK_FIXED["path"]
    out = cap.qpu_root_check(spec, _manifest())
    assert out["blocker"] and "fixed-frequency" in out["blocker"]


def test_a_declared_root_that_does_not_import_is_refused():
    spec = _spec()
    spec["quam_class"] = "quam_config.my_quam.Quam"
    broken = dict(LAB_ROOT, importable=False)
    out = cap.qpu_root_check(spec, _manifest(roots=(broken,)))
    assert out["blocker"] and "does not import" in out["blocker"]


def test_a_declared_stock_root_is_allowed_with_the_warning():
    spec = _spec()
    spec["quam_class"] = STOCK_FLUX["path"]
    out = cap.qpu_root_check(spec, _manifest())
    assert out["blocker"] is None and out["chosen"] == STOCK_FLUX["path"]
    assert out["warning"] and "initialize_qpu" in out["warning"]
    spec["quam_class"] = LAB_ROOT["path"]
    assert cap.qpu_root_check(spec, _manifest())["warning"] is None


def test_a_declared_root_the_probe_never_saw_is_allowed():
    spec = _spec()
    spec["quam_class"] = "somewhere.else.Quam"
    out = cap.qpu_root_check(spec, _manifest())
    assert out["blocker"] is None and out["chosen"] == "somewhere.else.Quam"


def test_no_probe_is_not_a_negative():
    out = cap.qpu_root_check(_spec(), {})
    assert out["blocker"] is None and out["chosen"] is None


# --- the build subprocess copy (D-15) --------------------------------------------------

def test_the_build_copy_matches_the_names():
    from quam_state_manager.generator import run_build as rb
    c = rb._COUPLER_ONLY
    assert (c["mode"], c["macro_key"], c["alias_key"]) == (ccl.MODE, ccl.MACRO_KEY, ccl.ALIAS_KEY)
    assert (c["gate_module"], c["gate_class"]) == (ccl.LAB_GATE_MODULE, ccl.LAB_GATE_CLASS)
    assert (c["snz_module"], c["snz_class"]) == (ccl.LAB_SNZ_MODULE, ccl.LAB_SNZ_CLASS)
    assert (c["amplitude_v"], c["flat_ns"], c["rise_ns"], c["snz_padding_ns"]) == (
        ccl.DEFAULT_AMPLITUDE_V, ccl.DEFAULT_FLAT_NS, ccl.DEFAULT_RISE_NS, ccl.DEFAULT_SNZ_PADDING_NS)
    assert (c["sampling_rate"], c["upsampling_mode"], c["output_mode"], c["flux_point"]) == (
        ccl.COUPLER_SAMPLING_RATE, ccl.COUPLER_UPSAMPLING_MODE, ccl.COUPLER_OUTPUT_MODE, ccl.COUPLER_FLUX_POINT)
    assert rb._coupler_only_pulse_key("q1", "q2") == ccl.pulse_key("q1", "q2")
    for spec in (_spec(), _spec(shape="snz"), _spec(shape="flattop"),
                 dict(_spec(shape="snz"), qubit_pairs=[["q1", "q2"], ["q2", "q3"]])):
        assert rb._coupler_only_shapes(spec) == ccl.pair_shapes(spec)


def test_the_build_refuses_before_writing_without_the_lab_gate(monkeypatch):
    import importlib

    import pytest

    from quam_state_manager.generator import run_build as rb
    real = importlib.import_module

    def fake(name, *a, **k):
        if name.startswith("quam_config"):
            raise ImportError("no " + name)
        return real(name, *a, **k)
    monkeypatch.setattr(rb.importlib, "import_module", fake)
    with pytest.raises(RuntimeError, match="cz_coupler_only needs quam_config.coupler_cz_gate"):
        rb._require_lab_coupler_gate(_spec())
    rb._require_lab_coupler_gate(dict(_spec(), pair_gate="cz_coupler"))   # the ext mode is not asked


# --- recipe and additions (D-9, D-10) ------------------------------------------------------

def _bundle(spec):
    from quam_state_manager.core import script_emitter
    return script_emitter.emit_bundle(spec, {}, {}, chip_name="chip", stamp="2026-01-01")


def test_the_recipe_builds_the_lab_layout():
    spec = _spec()
    spec["quam_class"] = "quam_config.my_quam.Quam"
    b = _bundle(spec)
    for name in ("01_make_wiring.py", "02_build_machine.py"):
        assert "from quam_config.my_quam import Quam as QuamCls" in b[name], name
    assert "qubit_pair_type" not in b["01_make_wiring.py"]          # the stock pair class
    two = b["02_build_machine.py"]
    assert "PAIR_GATE = 'cz_coupler_only'" in two
    assert "_COUPLER_ONLY = {" in two and "def _seed_coupler_only(" in two
    assert "    for _w in _finalize_coupler_only(machine, _spec):" in two
    compile(two, "02_build_machine.py", "exec")


def test_without_a_declared_root_the_recipe_roots_flux_tunable():
    two = _bundle(_spec())["02_build_machine.py"]
    assert "FluxTunableQuam as QuamCls" in two and "FixedFrequencyQuam as QuamCls" not in two


def test_the_extension_recipe_carries_none_of_it():
    spec = dict(_spec(), pair_gate="cz_coupler")
    two = _bundle(spec)["02_build_machine.py"]
    assert "_seed_coupler_only" not in two and "_COUPLER_ONLY" not in two


def test_additions_name_the_lab_macro():
    from quam_state_manager.core import additions
    assert additions.builder_gate_names(_spec(), ["q1", "q2"]) == ["cz_coupler_only", "cz"]


# --- re-generate (D-11) --------------------------------------------------------------------

def _lab_state(snz=True, z_on=()):
    key = "cz_coupler_only_pulse_q1_q2"
    pulse = ({"__class__": "quam_config.two_flux_gate.SNZTwoFluxPulse", "amplitude": 0.21, "flat_length": 44,
              "padding": 3, "t_phi": 1, "b_over_a": 0.4, "id": key} if snz else
             {"__class__": "quam_builder.common.pulses.FlatTopGaussianPulse", "amplitude": 0.21,
              "flat_length": 44, "length": 60, "id": key})
    qubits = {q: {"id": q, "z": None,
                  "__class__": "quam_builder.architecture.superconducting.qubit.flux_tunable_transmon."
                               "FluxTunableTransmon"} for q in ("q1", "q2")}
    for q in z_on:
        qubits[q]["z"] = {"id": q + ".z"}
    return {
        "__class__": "quam_config.my_quam.Quam",
        "qubits": qubits,
        "qubit_pairs": {"q1-2": {
            "__class__": "quam_builder.architecture.superconducting.qubit_pair.flux_tunable_transmon_pair."
                         "FluxTunableTransmonPair",
            "id": "q1-2", "qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
            "coupler": {"id": "coupler_q1_q2", "flux_point": "off", "decouple_offset": 0.03, "settle_time": 16,
                        "operations": {key: pulse}},
            "macros": {"cz_coupler_only": {
                "__class__": "quam_config.coupler_cz_gate.CouplerCZGate",
                "coupler_flux_pulse": "#/qubit_pairs/q1-2/coupler/operations/" + key,
                "phase_shift_control": 0.11, "phase_shift_target": 0.22,
                "spectator_qubits_phase_shift": {"q3": 0.05}, "duration": 15},
                "cz": "#./cz_coupler_only"}}},
    }


def _reconstruct(state):
    from quam_state_manager.core import regen_spec
    rec = regen_spec.reconstruct_spec(state, {"wiring": {}})
    return rec


def test_a_lab_layout_chip_reads_back_as_its_family():
    rec = _reconstruct(_lab_state())
    spec = rec.spec
    assert spec["pair_gate"] == "cz_coupler_only"
    assert spec["quam_class"] == "quam_config.my_quam.Quam"
    pv = next(iter(spec["populate"]["pairs"].values()))
    assert pv["coupler_pulse_shape"] == "snz" and pv["coupler_amplitude"] == 0.21
    assert pv["coupler_flat_length"] == 44 and pv["snz_padding"] == 3
    assert pv["t_phi"] == 1 and pv["b_over_a"] == 0.4
    assert pv["spectator_qubits_phase_shift"] == {"q3": 0.05} and pv["cz_duration_cycles"] == 15
    assert pv["phase_shift_control"] == 0.11 and pv["phase_shift_target"] == 0.22
    assert pv["coupler_decouple_offset"] == 0.03 and pv["coupler_settle_time"] == 16


def test_a_flat_top_lab_chip_reads_its_shape():
    pv = next(iter(_reconstruct(_lab_state(snz=False)).spec["populate"]["pairs"].values()))
    assert pv["coupler_pulse_shape"] == "flattop" and "snz_padding" not in pv


def test_a_mixed_chip_says_so():
    rec = _reconstruct(_lab_state(z_on=("q1",)))
    assert rec.spec["pair_gate"] == "cz_coupler_only"
    assert any("qubits with a flux line (q1)" in n for n in rec.notes), rec.notes


# --- pages and diagnostics (D-13, D-14) ----------------------------------------------------

def _store(tmp_path, state):
    import json

    from quam_state_manager.core.loader import QuamStore
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}), encoding="utf-8")
    return QuamStore(tmp_path)


def test_the_pair_page_reads_the_coupler_pulse(tmp_path):
    from quam_state_manager.core.query import QueryEngine
    eng = QueryEngine(_store(tmp_path, _lab_state(snz=False)))
    p = eng.get_pair("q1-2")
    assert p["cz_coupler_only_coupler_amplitude"] == 0.21
    assert p["cz_coupler_only_length"] == 60 and p["cz_coupler_only_flat_length"] == 44
    assert p["cz_coupler_only_phase_shift_control"] == 0.11
    edge = next(e for e in eng.get_topology()["edges"] if e["pair_id"] == "q1-2")
    det = next(d for d in edge["gate_details"] if d["name"] == "cz_coupler_only")
    assert det["phase_ctrl"] == 0.11   # raw macros: a pointer-valued pulse is read by the pair page


def test_a_flux_gate_keeps_its_own_lengths(tmp_path):
    from quam_state_manager.core.query import QueryEngine
    state = _lab_state(snz=False)
    gate = state["qubit_pairs"]["q1-2"]["macros"]["cz_coupler_only"]
    gate["flux_pulse_qubit"] = {"amplitude": 0.3, "length": 40}
    p = QueryEngine(_store(tmp_path, state)).get_pair("q1-2")
    assert p["cz_coupler_only_length"] == 40 and p["cz_coupler_only_amplitude"] == 0.3


def test_qubits_without_a_flux_line_are_not_a_finding(tmp_path):
    from quam_state_manager.core import diagnostics
    findings = diagnostics.lint_state(_store(tmp_path, _lab_state())) or []
    about_z = [f for f in findings if ".z" in str(getattr(f, "path", "")) or " z " in str(getattr(f, "message", ""))]
    assert not about_z, about_z


def test_topology_gate_details_take_an_inline_coupler_pulse_length(tmp_path):
    from quam_state_manager.core.query import QueryEngine
    state = _lab_state(snz=False)
    gate = state["qubit_pairs"]["q1-2"]["macros"]["cz_coupler_only"]
    gate["coupler_flux_pulse"] = {"amplitude": 0.21, "length": 60, "flat_length": 44}
    edge = next(e for e in QueryEngine(_store(tmp_path, state)).get_topology()["edges"] if e["pair_id"] == "q1-2")
    det = next(d for d in edge["gate_details"] if d["name"] == "cz_coupler_only")
    assert det["length"] == 60 and det["flat_length"] == 44 and det["coupler_amp"] == 0.21


# --- the wizard (D-2, B-4) ------------------------------------------------------------------

def test_the_wizard_offers_the_lab_layout():
    import os
    import shutil
    import subprocess
    from pathlib import Path

    import pytest
    if not shutil.which("node"):
        pytest.skip("node not installed")
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run(["node", str(root / "tests/generate_coupler_lab_selfcheck.cjs")],
                       cwd=root, env=dict(os.environ), capture_output=True, text=True, timeout=120)
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "coupler lab selfcheck passed" in r.stdout


# --- every extension-mode site has a stated disposition (D-16) ----------------------------

# (file, the def/var the literal sits in) -> how many "cz_coupler" literals there. A site
# that moves to another function, or a new one, fails here until it is decided.
_EXT_SITES = {
    ("quam_state_manager/generator/run_build.py", "_require_coupler_extension"): 1,
    ("quam_state_manager/generator/run_build.py", "_seed_coupler_cz"): 1,
    ("quam_state_manager/generator/run_build.py", "_finalize_pair_gates"): 1,
    ("quam_state_manager/generator/run_build.py", "run_build"): 4,
    ("quam_state_manager/core/config_generator.py", "validate_spec"): 4,
    ("quam_state_manager/core/capabilities.py", "required_capabilities"): 2,
    ("quam_state_manager/core/capabilities.py", "assess"): 1,
    ("quam_state_manager/core/capabilities.py", "qpu_root_check"): 1,
    ("quam_state_manager/core/additions.py", "builder_gate_names"): 2,
    ("quam_state_manager/core/script_emitter.py", "_is_flux_tunable"): 1,
    ("quam_state_manager/core/script_emitter.py", "_emit_wiring"): 1,
    ("quam_state_manager/core/regen_spec.py", "_detect_pair_gate"): 1,
}


def _sites(path):
    """``{enclosing top-level def: literal count}`` for one Python file."""
    import ast
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            n = sum(1 for sub in ast.walk(node)
                    if isinstance(sub, ast.Constant) and sub.value == "cz_coupler")
            if n:
                out[node.name] = n
    return out


def test_a_new_extension_mode_site_is_a_decision():
    """A `"cz_coupler"` literal in a function not listed here is a place that may need the
    lab mode too: decide it (and list it) rather than let it slip past."""
    files = sorted({f for f, _ in _EXT_SITES})
    got = {(f, fn): n for f in files for fn, n in _sites(f).items()}
    assert got == _EXT_SITES, sorted(set(got.items()) ^ set(_EXT_SITES.items()))


def test_an_snz_alias_length_is_never_shown_as_text(tmp_path):
    """A lab SNZ pulse stores `length` as a quam alias; the page shows the computed
    length or a blank, never the pointer string (review P1-2)."""
    from quam_state_manager.core.query import QueryEngine
    state = _lab_state(snz=True)
    op = state["qubit_pairs"]["q1-2"]["coupler"]["operations"]["cz_coupler_only_pulse_q1_q2"]
    op["length"] = "#./inferred_length"
    p = QueryEngine(_store(tmp_path, state)).get_pair("q1-2")
    assert p["cz_coupler_only_length"] is None or isinstance(p["cz_coupler_only_length"], int), p["cz_coupler_only_length"]


def test_a_flat_top_alias_length_is_computed(tmp_path):
    from quam_state_manager.core.query import QueryEngine
    state = _lab_state(snz=False)
    op = state["qubit_pairs"]["q1-2"]["coupler"]["operations"]["cz_coupler_only_pulse_q1_q2"]
    op["length"] = "#./inferred_length"
    p = QueryEngine(_store(tmp_path, state)).get_pair("q1-2")
    assert not isinstance(p["cz_coupler_only_length"], str)


def test_topology_reads_the_coupler_pulse_through_its_pointer(tmp_path):
    """A built chip refers to the coupler operation; Chip Status reads it through that
    reference, for the lab layout and the extension layout alike (review P2-3)."""
    from quam_state_manager.core.query import QueryEngine
    for macro_key in ("cz_coupler_only", "cz_coupler"):
        state = _lab_state(snz=False)
        macros = state["qubit_pairs"]["q1-2"]["macros"]
        if macro_key == "cz_coupler":
            macros["cz_coupler"] = macros.pop("cz_coupler_only")
            macros["cz"] = "#./cz_coupler"
        d = tmp_path / macro_key
        d.mkdir()
        edge = next(e for e in QueryEngine(_store(d, state)).get_topology()["edges"] if e["pair_id"] == "q1-2")
        det = next(x for x in edge["gate_details"] if x["name"] == macro_key)
        assert det["coupler_amp"] == 0.21 and det["length"] == 60 and det["flat_length"] == 44, det


def test_the_capability_cache_sees_a_changed_lab_file(tmp_path, monkeypatch):
    """An editable lab package changes files without a version bump: a moved lab file,
    or a lab file that appears, is a cache miss (review P2-7)."""
    import time

    from quam_state_manager.core import config_generator as cg
    from quam_state_manager.generator import probe_capabilities as pc
    lab = tmp_path / "quam_config"
    lab.mkdir()
    (lab / "__init__.py").write_text("", encoding="utf-8")
    sig = {str(lab): pc.file_sig(str(lab)), str(lab / "__init__.py"): pc.file_sig(str(lab / "__init__.py")),
           str(lab / "coupler_cz_gate.py"): pc.file_sig(str(lab / "coupler_cz_gate.py"))}
    assert sig[str(lab / "coupler_cz_gate.py")] is None
    assert cg._lab_files_unchanged(sig)
    time.sleep(0.02)
    (lab / "coupler_cz_gate.py").write_text("class CouplerCZGate: pass\n", encoding="utf-8")
    assert not cg._lab_files_unchanged(sig), "a lab file that appeared is a miss"
    assert not cg._lab_files_unchanged(None), "an entry that never recorded the lab files is a miss"
