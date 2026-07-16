"""core/script_emitter.py — the editable Python bundle the wizard can export
alongside state.json/wiring.json (customer requirement: "generate/populate
python scripts in a user-defined folder to modify along with a code IDE").

No QM stack needed here: every emitted .py must ast-parse + compile across
chip archetypes; the data blocks must carry the actual values; the verbatim
machinery block must stay in lock-step with generator/run_build.py (in-sync
by construction — these tests pin the mechanism); and /generate/build must
export best-effort (an emission failure never fails a successful build).
The end-to-end rebuild-equality check lives in test_script_emitter_live.py
(auto-skips without a QM env).
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest import mock

import pytest

from quam_state_manager.core import script_emitter
from quam_state_manager.web.app import create_app

STAMP = "2026-01-01"


# --- fixture specs -----------------------------------------------------------

def _base(**over):
    spec = {
        "network": {"host": "10.1.1.1", "cluster_name": "CL", "port": None},
        "instruments": {"controllers": [{"con": 1, "fems": [
            {"slot": 1, "fem": "mw"}, {"slot": 2, "fem": "lf"}]}],
            "opx_plus": [], "octaves": []},
        "qubits": ["q1", "q2"],
        "qubit_pairs": [["q1", "q2"]],
        "twpas": [],
        "pair_gate": "cz_tunable",
        "lines": [
            {"element": "q1", "line": "resonator", "group": "feedline1",
             "channel": {"kind": "mw_fem", "out_port": 8, "in_port": 1}},
            {"element": "q2", "line": "resonator", "group": "feedline1",
             "channel": {"kind": "mw_fem", "out_port": 8, "in_port": 1}},
            {"element": "q1", "line": "drive", "channel": None},
            {"element": "q2", "line": "drive", "channel": None},
            {"element": "q1", "line": "flux", "channel": None},
            {"element": "q2", "line": "flux", "channel": None},
            {"element": "q1-q2", "line": "coupler", "channel": None},
        ],
        "populate": {
            "qubit": {"q1": {"RF_freq": 5.2e9}, "q2": {"RF_freq": 4.9e9}},
            "pulses": {"q1": {"x180_amplitude": 0.12, "x180_length": 48}},
            "pairs": {"q1-q2": {"cz_amplitude": 0.1, "cz_variant": "unipolar"}},
        },
    }
    spec.update(over)
    return spec


def _cr_spec():
    return _base(
        pair_gate="cr",
        instruments={"controllers": [{"con": 1, "fems": [{"slot": 1, "fem": "mw"}]}],
                     "opx_plus": [], "octaves": []},
        lines=[
            {"element": "q1", "line": "resonator", "group": "f",
             "channel": {"kind": "mw_fem"}},
            {"element": "q1", "line": "drive", "channel": None},
            {"element": "q2", "line": "drive", "channel": None},
            {"element": "q1-q2", "line": "cross_resonance", "channel": None},
        ],
        populate={"pairs": {"q1-q2": {"cr_drive_amplitude": 1.0}}},
    )


_ALLOC = {
    "q1": {"xy": [{"con": 1, "slot": 1, "port": 2, "io_type": "output"}],
           "rr": [{"con": 1, "slot": 1, "port": 8, "io_type": "output"},
                  {"con": 1, "slot": 1, "port": 1, "io_type": "input"}]},
    "q1-2": {"c": [{"con": 1, "slot": 2, "port": 5, "io_type": "output"}]},
}


def _bundle(spec=None, alloc=_ALLOC):
    return script_emitter.emit_bundle(
        spec or _base(), alloc, {"python": "3.11", "quam": "0.5.0"},
        "demo", stamp=STAMP)


# --- compile + shape ---------------------------------------------------------

@pytest.mark.parametrize("spec_fn", [
    _base,                                        # CZ tunable
    lambda: _base(pair_gate="cz_fixed",
                  lines=[ln for ln in _base()["lines"] if ln["line"] != "coupler"]),
    _cr_spec,                                     # CR / fixed-frequency
    lambda: _base(populate={}),                   # zero-populate
])
def test_every_emitted_py_compiles(spec_fn):
    bundle = _bundle(spec_fn())
    assert set(bundle) == {"01_make_wiring.py", "02_build_machine.py",
                           "03_generate_config.py", "README.md"}
    for name, src in bundle.items():
        if name.endswith(".py"):
            ast.parse(src)
            compile(src, name, "exec")


def test_wiring_data_inlined():
    src = _bundle()["01_make_wiring.py"]
    assert "HOST = '10.1.1.1'" in src
    assert "instruments.add_mw_fem(controller=1, slots=[1])" in src
    assert "instruments.add_lf_fem(controller=1, slots=[2])" in src
    # The wizard's pinned feedline constraint, with run_build's INT indices.
    assert ("connectivity.add_resonator_line(qubits=[1, 2], "
            "constraints=mw_fem_spec(in_port=1, out_port=8))") in src
    assert "add_qubit_pair_flux_lines(qubit_pairs=[(1, 2)], constraints=None)" in src
    # Allocated ports inlined as reference comments.
    assert "# allocated: con1 s1 p8 (output), con1 s1 p1 (input)" in src
    assert "# allocated: con1 s2 p5 (output)" in src
    # One allocation pass, wizard order (fidelity pillar #1).
    assert src.count("allocate_wiring(connectivity, instruments)") == 1
    assert "FluxTunableQuam" in src


def test_alpha_names_survive():
    spec = _base(qubits=["qA1", "qB2"], qubit_pairs=[["qA1", "qB2"]])
    spec["lines"] = [
        {"element": "qA1", "line": "drive", "channel": None},
        {"element": "qA1-qB2", "line": "coupler", "channel": None},
    ]
    src = _bundle(spec, alloc={})["01_make_wiring.py"]
    assert "add_qubit_drive_lines(qubits='A1', constraints=None)" in src
    assert "qubit_pairs=[('A1', 'B2')]" in src


def test_fixed_frequency_class_choice():
    src = _bundle(_cr_spec(), alloc={})["01_make_wiring.py"]
    assert "FixedFrequencyQuam" in src
    assert "FluxTunableQuam" not in src
    assert "add_qubit_pair_cross_resonance_lines" in src


def test_build_data_inlined():
    src = _bundle()["02_build_machine.py"]
    assert "'RF_freq': 5200000000.0" in src
    assert "'x180_amplitude': 0.12" in src
    assert "'cz_amplitude': 0.1" in src
    assert "QUBIT_PAIRS = [['q1', 'q2']]" in src
    assert "PAIR_GATE = 'cz_tunable'" in src
    # The run block wires populate + gate finalization + the LO fix-up.
    assert "apply_populate(machine, POPULATE" in src
    assert "_finalize_pair_gates(machine, _spec, PAIR_GATE)" in src
    assert "_link_input_downconverters_to_outputs(" in src
    assert "machine.save()" in src


def test_machinery_is_verbatim_run_build():
    """Pillar #2: the emitted machinery IS run_build's source — extracted via
    inspect.getsource, so it can never drift. Spot-check three functions and
    the constants."""
    import importlib.util
    import inspect as _inspect

    rb_path = (Path(__file__).resolve().parent.parent
               / "quam_state_manager" / "generator" / "run_build.py")
    s = importlib.util.spec_from_file_location("rb_verbatim_check", rb_path)
    rb = importlib.util.module_from_spec(s)
    s.loader.exec_module(rb)  # type: ignore[union-attr]

    src = _bundle()["02_build_machine.py"]
    for fn in ("_seed_cz_variant", "_seed_cr_gate", "apply_populate",
               "_link_input_downconverters_to_outputs"):
        assert _inspect.getsource(getattr(rb, fn)) in src, f"{fn} not verbatim"
    assert f"_BAND_TO_DELAY_NS = {rb._BAND_TO_DELAY_NS!r}" in src
    assert f"_CZ_VARIANTS = {rb._CZ_VARIANTS!r}" in src


def test_readme_versions_and_order():
    md = _bundle()["README.md"]
    assert "`quam` 0.5.0" in md
    assert "01_make_wiring.py" in md and "03_generate_config.py" in md
    assert "EMPTY" in md          # the stray-JSON warning


def test_golden_bundle_stable(tmp_path):
    """Deterministic emission — same spec + stamp → byte-identical bundle.
    Regenerate with tests/golden/regen_scripts_bundle.py after intentional
    emitter changes."""
    golden_dir = Path(__file__).resolve().parent / "golden" / "scripts_bundle_cz"
    bundle = _bundle()
    if not golden_dir.is_dir():
        pytest.skip("golden bundle not generated yet")
    for name, src in bundle.items():
        want = (golden_dir / name).read_text(encoding="utf-8")
        assert src == want, f"{name} drifted from the golden bundle"


def test_write_bundle(tmp_path):
    files = script_emitter.write_bundle(tmp_path / "scripts", _bundle())
    assert sorted(files) == ["01_make_wiring.py", "02_build_machine.py",
                             "03_generate_config.py", "README.md"]
    assert (tmp_path / "scripts" / "02_build_machine.py").stat().st_size > 10_000


# --- /generate/build integration --------------------------------------------

@pytest.fixture
def client(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    return app.test_client()


def _canned_outcome(ok=True):
    return {"ok": ok, "status": "ok" if ok else "error",
            "result": {"allocation": _ALLOC, "versions": {"quam": "0.5.0"},
                       "qubits": ["q1", "q2"], "qubit_pairs": ["q1-2"],
                       "warnings": [], "files": {}},
            "returncode": 0}


def _post_build(client, tmp_path, scripts_dir):
    payload = {
        "spec": _base(), "output_path": str(tmp_path / "out"),
        "scripts_dir": scripts_dir, "force": True, "ack_degrades": True,
    }
    return client.post("/generate/build", json=payload)


def test_build_route_exports_scripts(client, tmp_path):
    sdir = tmp_path / "scripts"
    with mock.patch("quam_state_manager.core.config_generator.run_generator",
                    return_value=_canned_outcome()), \
         mock.patch("quam_state_manager.core.config_generator.get_selected_env",
                    return_value="/usr/bin/python3"), \
         mock.patch("quam_state_manager.core.config_generator.probe_capabilities",
                    return_value={"capabilities": None, "versions": {}}):
        r = _post_build(client, tmp_path, str(sdir))
    body = r.get_json()
    assert body["ok"] is True
    assert sorted(body["scripts"]["files"]) == [
        "01_make_wiring.py", "02_build_machine.py",
        "03_generate_config.py", "README.md"]
    assert (sdir / "01_make_wiring.py").is_file()


def test_build_route_scripts_error_never_fails_build(client, tmp_path):
    with mock.patch("quam_state_manager.core.config_generator.run_generator",
                    return_value=_canned_outcome()), \
         mock.patch("quam_state_manager.core.config_generator.get_selected_env",
                    return_value="/usr/bin/python3"), \
         mock.patch("quam_state_manager.core.config_generator.probe_capabilities",
                    return_value={"capabilities": None, "versions": {}}), \
         mock.patch("quam_state_manager.core.script_emitter.write_bundle",
                    side_effect=OSError("disk full")):
        r = _post_build(client, tmp_path, str(tmp_path / "scripts"))
    body = r.get_json()
    assert body["ok"] is True                      # the build itself still ok
    assert "disk full" in body["scripts_error"]


def test_build_route_no_scripts_dir_no_export(client, tmp_path):
    with mock.patch("quam_state_manager.core.config_generator.run_generator",
                    return_value=_canned_outcome()), \
         mock.patch("quam_state_manager.core.config_generator.get_selected_env",
                    return_value="/usr/bin/python3"), \
         mock.patch("quam_state_manager.core.config_generator.probe_capabilities",
                    return_value={"capabilities": None, "versions": {}}):
        r = _post_build(client, tmp_path, "")
    body = r.get_json()
    assert body["ok"] is True
    assert "scripts" not in body and "scripts_error" not in body
