"""The lab layout of the coupler-only CZ, built for real (decisions T-5, D-17).

Needs a QM env with the lab's quam_config (its coupler gate, SNZ pulse and root)
and the lab checkout itself:

* ``SM_LAB_ENV_PYTHON`` -- the env's python;
* ``SM_LAB_QUAM_CONFIG_DIR`` -- the lab checkout (put FIRST on the subprocess path;
  ``quam_config.__file__`` must be under it).

Without both it skips with that reason. Every output goes to a temp folder; no
state is saved anywhere else. Checked here (the spec's acceptance 1-4 plus the
plan's extras): the built chip has the same shape as the lab's own fixture chip,
no extension class, loads with "Could not load class" as an error, generates a
config with the coupler elements and no z element, runs initialize_qpu and the
CZ macro inside a program, refuses a qubit amplitude scale, re-generates with
its values carried, and the exported recipe writes the same state.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import regen_spec, script_emitter
from quam_state_manager.core.config_generator import run_generator

PY = os.environ.get("SM_LAB_ENV_PYTHON")
LAB = os.environ.get("SM_LAB_QUAM_CONFIG_DIR")
pytestmark = pytest.mark.skipif(not (PY and LAB), reason="SM_LAB_ENV_PYTHON / SM_LAB_QUAM_CONFIG_DIR not set")

LAB_ROOT = "quam_config.my_quam.Quam"


@pytest.fixture(autouse=True)
def _lab_first_on_path(monkeypatch):
    rest = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH", LAB + (os.pathsep + rest if rest else ""))


def _spec(quam_class=LAB_ROOT):
    lines = []
    for q in ("q1", "q2", "q3"):
        lines += [{"element": q, "line": "resonator", "group": "feedline1",
                   "channel": {"kind": "mw_fem", "out_port": 1, "in_port": 1}},
                  {"element": q, "line": "drive", "channel": None}]
    lines += [{"element": "q1-q2", "line": "coupler", "channel": None},
              {"element": "q2-q3", "line": "coupler", "channel": None}]
    spec = {
        "network": {"host": "127.0.0.1", "cluster_name": "test_cluster", "port": None},
        "instruments": {"controllers": [{"con": 1, "fems": [{"slot": 1, "fem": "mw"}, {"slot": 2, "fem": "lf"}]}],
                        "opx_plus": [], "octaves": []},
        "qubits": ["q1", "q2", "q3"], "qubit_pairs": [["q1", "q2"], ["q2", "q3"]], "twpas": [],
        "pair_gate": "cz_coupler_only",
        "lines": lines,
        "populate": {"pairs": {
            "q1-q2": {"coupler_pulse_shape": "flattop", "coupler_amplitude": 0.12, "coupler_flat_length": 56,
                      "phase_shift_control": 0.11, "phase_shift_target": 0.22,
                      "spectator_qubits_phase_shift": {"q3": 0.05}, "cz_duration_cycles": 21,
                      "coupler_decouple_offset": 0.03, "coupler_settle_time": 40},
            "q2-q3": {"coupler_pulse_shape": "snz", "coupler_amplitude": 0.13, "coupler_flat_length": 44,
                      "snz_padding": 3, "t_phi": 1, "b_over_a": 0.4, "coupler_decouple_offset": 0.04}}},
    }
    if quam_class:
        spec["quam_class"] = quam_class
    return spec


def _env(script: str, *args, timeout=900):
    r = subprocess.run([PY, "-c", script, *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", timeout=timeout)
    return r.returncode, r.stdout, r.stderr


_CHECK = r'''
import json, os, sys, warnings
lab, built, fixture = sys.argv[1:4]
import quam_config
assert os.path.abspath(quam_config.__file__).startswith(os.path.abspath(lab)), quam_config.__file__
out = {}

# the lab's own target chip: fixture + populate + the lab macros (temp folder, explicit saves)
sys.path.insert(0, lab)
from tests.unit.fixtures.fixed_freq_coupler_state import build_fixed_freq_coupler_state
from quam_config import Quam
from quam_config.populate_fixed_freq_coupler import populate_fixed_freq_coupler
build_fixed_freq_coupler_state(fixture)
ref = Quam.load(fixture)
populate_fixed_freq_coupler(ref, cz_shape="flattop")
ref.save(fixture)

def walk(node, path, acc):
    if isinstance(node, dict):
        if "__class__" in node:
            acc[path] = node["__class__"]
        for k, v in node.items():
            walk(v, path + "/" + str(k), acc)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk(v, path + "/" + str(i), acc)
    return acc

def state(folder):
    return json.load(open(os.path.join(folder, "state.json"), encoding="utf-8"))

def shape(st):
    """classes + key sets of the objects the layout is about, keyed without pulse names"""
    sh = {"root": st.get("__class__"),
          "qubits": sorted({q.get("__class__") for q in st["qubits"].values()}),
          "qubit_z": sorted({repr(q.get("z")) for q in st["qubits"].values()}),
          "pairs": sorted({p.get("__class__") for p in st["qubit_pairs"].values()}),
          "pair_keys": sorted(set().union(*[set(p) for p in st["qubit_pairs"].values()])),
          "couplers": sorted({p["coupler"].get("__class__") for p in st["qubit_pairs"].values()}),
          "coupler_ids": sorted(p["coupler"].get("id") for p in st["qubit_pairs"].values()),
          "coupler_keys": sorted(set().union(*[set(p["coupler"]) for p in st["qubit_pairs"].values()])),
          "macro_classes": sorted({(k, m.get("__class__") if isinstance(m, dict) else "alias:" + m)
                                   for p in st["qubit_pairs"].values() for k, m in p["macros"].items()}),
          "macro_keys": sorted(set().union(*[set(m) for p in st["qubit_pairs"].values()
                                             for m in p["macros"].values() if isinstance(m, dict)]))}
    return sh

b_state, r_state = state(built), state(fixture)
out["shape_built"] = shape(b_state)
out["shape_ref"] = shape(r_state)
out["ext_strings"] = "qsm_quam_ext" in json.dumps(b_state)

# acceptance 1: load with "Could not load class" as an error
with warnings.catch_warnings():
    warnings.filterwarnings("error", message="Could not load class")
    m = Quam.load(built)
out["root_class"] = type(m).__module__ + "." + type(m).__name__
# acceptance 2
cfg = m.generate_config()
els = list(cfg["elements"])
out["coupler_elements"] = sorted(e for e in els if e.startswith("coupler_"))
out["z_elements"] = sorted(e for e in els if e.endswith(".z"))
ups = set()
for pair in m.qubit_pairs.values():
    ups.add(pair.coupler.opx_output.upsampling_mode)
    ups.add(("sr", pair.coupler.opx_output.sampling_rate))
out["coupler_port"] = sorted(map(str, ups))
# acceptance 3 + 4
from qm.qua import program
with program() as prog:
    m.initialize_qpu(target=m.qubits["q1"])
out["initialize_qpu"] = "ok"
for name, pair in m.qubit_pairs.items():
    with program() as prog:
        pair.macros["cz"].apply()
    try:
        with program() as prog:
            pair.macros["cz"].apply(amplitude_scale_qubit=0.5)
        out.setdefault("scale_refused", []).append((name, False))
    except TypeError:
        out.setdefault("scale_refused", []).append((name, True))
pulse_classes = {}
for name, pair in m.qubit_pairs.items():
    for k, op in pair.coupler.operations.items():
        if k.startswith("cz_coupler_only_pulse_"):
            pulse_classes[name] = (k, type(op).__name__)
out["pulses"] = pulse_classes
print("RESULT " + json.dumps(out))
'''


def _check(tmp_path, built):
    fixture = tmp_path / "fixture"
    rc, so, se = _env(_CHECK, LAB, built, fixture)
    assert rc == 0, so[-3000:] + se[-6000:]
    line = next(ln for ln in so.splitlines() if ln.startswith("RESULT "))
    return json.loads(line[len("RESULT "):])


def _build(spec, out):
    outcome = run_generator(PY, "build", spec, out, timeout=900)
    assert outcome.get("ok"), json.dumps(outcome, default=str)[-4000:]
    return outcome


def test_the_lab_layout_builds_loads_and_plays(tmp_path):
    built = tmp_path / "built"
    outcome = _build(_spec(), built)
    res = _check(tmp_path, built)
    sb, sr = res["shape_built"], res["shape_ref"]
    for key in ("root", "qubits", "qubit_z", "pairs", "couplers", "coupler_ids", "coupler_keys", "macro_classes", "macro_keys"):
        assert sb[key] == sr[key], (key, sb[key], sr[key])
    assert sb["qubit_z"] == ["None"]
    assert not res["ext_strings"]
    assert res["root_class"] == LAB_ROOT
    assert res["coupler_elements"] == ["coupler_q1_q2", "coupler_q2_q3"] and res["z_elements"] == []
    assert "pulse" in res["coupler_port"] and "('sr', 1000000000.0)" in res["coupler_port"]
    assert res["initialize_qpu"] == "ok"
    assert all(ok for _n, ok in res["scale_refused"]), res["scale_refused"]
    assert res["pulses"]["q1-2"] == ["cz_coupler_only_pulse_q1_q2", "FlatTopGaussianPulse"]
    assert res["pulses"]["q2-3"] == ["cz_coupler_only_pulse_q2_q3", "SNZTwoFluxPulse"]
    (tmp_path / "t5_result.json").write_text(json.dumps({"outcome_warnings": (outcome.get("result") or {}).get("warnings"),
                                                         "check": res}, indent=1), encoding="utf-8")


def test_regenerate_carries_the_lab_values(tmp_path):
    built = tmp_path / "built"
    _build(_spec(), built)
    state = json.loads((built / "state.json").read_text(encoding="utf-8"))
    wiring = json.loads((built / "wiring.json").read_text(encoding="utf-8"))
    rec = regen_spec.reconstruct_spec(state, wiring)
    spec2 = rec.spec
    assert spec2["pair_gate"] == "cz_coupler_only" and spec2["quam_class"] == LAB_ROOT
    rebuilt = tmp_path / "rebuilt"
    _build(spec2, rebuilt)
    s2 = json.loads((rebuilt / "state.json").read_text(encoding="utf-8"))
    for pid in ("q1-2", "q2-3"):
        a, b = state["qubit_pairs"][pid], s2["qubit_pairs"][pid]
        assert a["macros"]["cz_coupler_only"] == b["macros"]["cz_coupler_only"], pid
        assert a["coupler"]["operations"] == b["coupler"]["operations"], pid
        for k in ("flux_point", "decouple_offset", "settle_time"):
            assert a["coupler"].get(k) == b["coupler"].get(k), (pid, k)
    m12 = s2["qubit_pairs"]["q1-2"]["macros"]["cz_coupler_only"]
    assert m12["spectator_qubits_phase_shift"] == {"q3": 0.05} and m12["duration"] == 21
    assert m12["phase_shift_control"] == 0.11


def test_the_exported_recipe_writes_the_same_state(tmp_path):
    spec = _spec()
    out_a = tmp_path / "a"
    outcome = _build(spec, out_a)
    result = outcome.get("result") or {}
    bundle = script_emitter.emit_bundle(spec, result.get("allocation") or {}, result.get("versions") or {},
                                        "lab", stamp="2026-01-01")
    sdir = tmp_path / "scripts"
    script_emitter.write_bundle(sdir, bundle)
    out_b = tmp_path / "b"
    out_b.mkdir()
    for script in ("01_make_wiring.py", "02_build_machine.py"):
        r = subprocess.run([PY, str(sdir / script), str(out_b)], capture_output=True, text=True,
                           encoding="utf-8", cwd=str(sdir), timeout=900)
        assert r.returncode == 0, f"{script}: {r.stdout[-2000:]}\n{r.stderr[-4000:]}"
    for name in ("state.json", "wiring.json"):
        a = json.loads((out_a / name).read_text(encoding="utf-8"))
        b = json.loads((out_b / name).read_text(encoding="utf-8"))
        assert a == b, f"{name} differs between the wizard build and the recipe"


@pytest.mark.parametrize("fault", ["qubit_flux_line", "stock_root", "no_lab_module", "bad_declared_root"])
def test_planted_faults_are_named(tmp_path, monkeypatch, fault):
    spec = _spec()
    if fault == "qubit_flux_line":
        from quam_state_manager.core import config_generator
        spec["lines"].append({"element": "q1", "line": "flux", "channel": None})
        assert any("cz_coupler_only requires no qubit z line" in e for e in config_generator.validate_spec(spec))
        return
    if fault == "stock_root":
        spec["quam_class"] = "quam_builder.architecture.superconducting.qpu.FluxTunableQuam"
        outcome = _build(spec, tmp_path / "o")
        warns = " ".join((outcome.get("result") or {}).get("warnings") or [])
        assert "initialize_qpu" in warns, warns
        return
    if fault == "no_lab_module":
        # the lab checkout off the path; the env's installed quam_config has no coupler gate
        monkeypatch.setenv("PYTHONPATH", os.environ["PYTHONPATH"].split(os.pathsep, 1)[1]
                           if os.pathsep in os.environ["PYTHONPATH"] else "")
        spec.pop("quam_class")
        out = tmp_path / "o"
        outcome = run_generator(PY, "build", spec, out, timeout=900)
        assert not outcome.get("ok") and "cz_coupler_only needs quam_config.coupler_cz_gate" in str(outcome)
        assert not (out / "state.json").exists(), "nothing is written"
        return
    spec["quam_class"] = "quam_config.no_such_module.Quam"
    outcome = run_generator(PY, "build", spec, tmp_path / "o", timeout=900)
    assert not outcome.get("ok") and "does not import here" in str(outcome)
