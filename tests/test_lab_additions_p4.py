"""Lab additions, phase 4: presets (readout per quam_builder version, pulsed
TWPA as pulse + fields), the ``field`` addition kind, the both-movers CZ group,
and QDAC qubits (a z-line pulse refused where the bias replaced ``z``).

Real builds are skipped unless their interpreter is given:

* ``QSM_OFFICIAL_PYTHON`` (else a conda env named ``qsm_official``),
* ``QSM_LAB_TREE_PYTHON`` + ``QSM_LAB_TREE_PYTHONPATH`` -- an env that imports a
  COPY of a lab source tree placed first on ``PYTHONPATH`` (the both-movers
  gate class lives only in such a tree),
* ``QSM_QDAC_PYTHON`` -- an env with the QDAC bias classes.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from quam_state_manager.core import additions, config_generator, role_manifest

QB = "quam_builder.architecture.superconducting."
BOTH = "lab.gates.CZGateBothMovers"
FLAT = "quam.components.pulses.FlatTopGaussianPulse"


def _f(raw, has_default=False, kind="plain", optional=False):
    return {"raw": raw, "has_default": has_default, "kind": kind, "optional": optional}


MAN = {"versions": {"quam_builder": "0.4.0"}, "classes": {
    BOTH: {"role": "macro_pair", "origin": "lab", "fields": {
        "flux_pulse_high": _f("Union[Pulse, str]", kind="pointer_pulse"),
        "flux_pulse_low": _f("Union[Pulse, str]", kind="pointer_pulse"),
        "coupler_flux_pulse": _f("Pulse", True, "pointer_pulse", True)}},
    FLAT: {"role": "pulse", "origin": "official", "fields": {
        "length": _f("int"), "amplitude": _f("float"), "flat_length": _f("int")}},
}}


def _state(f1=5.2e9, f2=4.9e9):
    q = lambda f: {"f_01": f, "z": {"operations": {}}, "xy": {"operations": {}}}
    return {"__class__": "x.Root", "qubits": {"q1": q(f1), "q2": q(f2)},
            "qubit_pairs": {"q1-2": {"qubit_control": "#/qubits/q1",
                                     "qubit_target": "#/qubits/q2"}}}


def _spec(adds):
    return {"qubits": ["q1", "q2"], "qubit_pairs": [["q1", "q2"]],
            "lines": [{"element": q, "line": ln} for q in ("q1", "q2")
                      for ln in ("drive", "flux", "resonator")],
            "additions": adds}


def _pulse(q, name):
    return {"kind": "pulse", "target": f"qubits.{q}.z.operations", "name": name, "class": FLAT,
            "fields": {"length": 40, "amplitude": 0.1, "flat_length": 24}}


def _both(high, low):
    return {"kind": "macro", "target": "qubit_pairs.q1-2.macros", "name": "cz_both", "class": BOTH,
            "fields": {"flux_pulse_high": f"#/qubits/{high}/z/operations/p_{high}",
                       "flux_pulse_low": f"#/qubits/{low}/z/operations/p_{low}"}}


def _run(adds, state):
    rep = additions.apply_additions(state, _spec(adds), MAN)
    return [e["name"] for e in rep["added"]], {e["name"]: e["reason"] for e in rep["refused"]}


# ---------------------------------------------------------------------------
# both movers: the high pulse sits on the higher-frequency qubit
# ---------------------------------------------------------------------------

def test_both_movers_high_pulse_on_the_higher_qubit_is_accepted():
    added, _ = _run([_pulse("q1", "p_q1"), _pulse("q2", "p_q2"), _both("q1", "q2")], _state())
    assert "cz_both" in added


def test_both_movers_high_pulse_on_the_lower_qubit_is_refused():
    added, by = _run([_pulse("q1", "p_q1"), _pulse("q2", "p_q2"), _both("q2", "q1")], _state())
    assert "cz_both" not in added
    assert "higher-frequency qubit" in by["cz_both"], by


def test_both_movers_with_unknown_frequencies_leaves_either_qubit_open():
    st = _state(None, None)
    added, _ = _run([_pulse("q1", "p_q1"), _pulse("q2", "p_q2"), _both("q2", "q1")], st)
    assert "cz_both" in added


def test_both_movers_tie_goes_to_the_control():
    added, by = _run([_pulse("q1", "p_q1"), _pulse("q2", "p_q2"), _both("q2", "q1")],
                     _state(5e9, 5e9))
    assert "cz_both" not in added and "higher-frequency" in by["cz_both"]


# ---------------------------------------------------------------------------
# real build: the both-movers gate of a lab source tree (a COPY on PYTHONPATH)
# ---------------------------------------------------------------------------

def _lab_tree_env(monkeypatch):
    py = os.environ.get("QSM_LAB_TREE_PYTHON")
    tree = os.environ.get("QSM_LAB_TREE_PYTHONPATH")
    if not (py and tree and Path(py).is_file() and Path(tree).is_dir()):
        pytest.skip("QSM_LAB_TREE_PYTHON / QSM_LAB_TREE_PYTHONPATH not given")
    monkeypatch.setenv("PYTHONPATH", tree)
    return py


def test_real_build_lab_both_movers_group(tmp_path, monkeypatch):
    from tests.test_lab_additions_p3 import _plan_rows
    from tests.test_lab_additions_p2 import _real_spec
    py = _lab_tree_env(monkeypatch)
    module = os.environ.get("QSM_LAB_TREE_BOTH_MOVERS_MODULE", "quam_config.cz_gate_both_movers")
    gate = module + ".CZGateBothMovers"
    man = role_manifest.probe_roles(py, None, state_classes=[gate], modules=[module])
    assert man["ok"], man["error"]
    entry = man["manifest"]["classes"][gate]
    assert entry["role"] == "macro_pair"
    recipe = additions.cz_recipe(entry)
    assert [r["on"] for r in recipe[:2]] == ["high", "low"], recipe

    out = tmp_path / "chip"
    spec = _real_spec()
    o = config_generator.run_generator(py, "build", spec, out, timeout=900)
    assert o.get("ok"), o
    built = json.loads((out / "state.json").read_text(encoding="utf-8"))
    pair = built["qubit_pairs"]["q1-2"]
    f = {q: built["qubits"][q].get("f_01") for q in ("q1", "q2")}
    high, low = ("q1", "q2") if f["q1"] >= f["q2"] else ("q2", "q1")
    flat = {"length": 48, "amplitude": 0.08, "flat_length": 32}
    spec["additions"] = [
        {"kind": "pulse", "target": f"qubits.{high}.z.operations", "name": "cz_hi", "class": FLAT,
         "fields": dict(flat)},
        {"kind": "pulse", "target": f"qubits.{low}.z.operations", "name": "cz_lo", "class": FLAT,
         "fields": dict(flat, amplitude=-0.05)},
        {"kind": "macro", "target": "qubit_pairs.q1-2.macros", "name": "cz_both", "class": gate,
         "fields": {"flux_pulse_high": f"#/qubits/{high}/z/operations/cz_hi",
                    "flux_pulse_low": f"#/qubits/{low}/z/operations/cz_lo"}},
        # fault: the pulses swapped (the high pulse on the lower-frequency qubit)
        {"kind": "macro", "target": "qubit_pairs.q1-2.macros", "name": "cz_both_swapped",
         "class": gate,
         "fields": {"flux_pulse_high": f"#/qubits/{low}/z/operations/cz_lo",
                    "flux_pulse_low": f"#/qubits/{high}/z/operations/cz_hi"}},
    ]
    rep = additions.apply_and_verify(py, out, spec, man["manifest"])
    (tmp_path / "additions_report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    added = [e["name"] for e in rep["added"]]
    by = {e["name"]: e["reason"] for e in rep["refused"]}
    assert added == ["cz_hi", "cz_lo", "cz_both"], by
    assert "higher-frequency qubit" in by["cz_both_swapped"]
    assert "qubit_pairs.q1-2.macros.cz_both" in rep["verified"]["own_code"]
    prev = config_generator.run_config_preview(py, out)
    assert prev.get("ok"), prev
    st = json.loads((out / "state.json").read_text(encoding="utf-8"))
    assert st["qubit_pairs"]["q1-2"]["macros"]["cz_both"]["__class__"] == gate
    html = _plan_rows(tmp_path, out)
    (tmp_path / "cz_plan_both.html").write_text(html, encoding="utf-8")
    assert "qubit_pairs.q1-2.macros.cz_both" in html
    from quam_state_manager.core import cz_gate_plan as czp
    row = next(r for r in czp.operations(st)[0] if r["path"].endswith("macros.cz_both"))
    assert {ln["pulse_field"] for ln in row["lines"]} == {"flux_pulse_high", "flux_pulse_low"}
    assert pair  # the builder wrote the pair
