"""Field-kind classifier, role probe and role manifest (lab additions, phase 1)."""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from quam_state_manager.core import field_kinds as fk
from quam_state_manager.core import role_manifest as rm

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "quam_state_manager" / "generator" / "probe_roles.py"

# (annotation text, default, has_default, expected kind, expected optional)
TABLE = [
    # pulses: Union with str, Optional, "= None", bare
    ("Union[Pulse, str]", None, False, "pointer_pulse", False),
    ("Union[Pulse, str, None]", None, False, "pointer_pulse", True),
    ("typing.Union[quam.components.pulses.Pulse, str, NoneType]", None, False, "pointer_pulse", True),
    ("Pulse", None, True, "pointer_pulse", True),
    ("Optional[SquareReadoutPulse]", None, False, "pointer_pulse", True),
    ("Pulse | str | None", None, False, "pointer_pulse", True),
    ("<class 'quam.components.pulses.Pulse'>", None, False, "pointer_pulse", False),
    # forward-ref strings (from __future__ import annotations)
    ("'Union[Pulse, str]'", None, False, "pointer_pulse", False),
    ("Optional['DragPulse']", None, False, "pointer_pulse", True),
    ("typing.Dict[int, ForwardRef('OctaveUpConverter')]", None, False, "unknown", False),
    ("typing.Optional[ForwardRef('SquarePulse')]", None, False, "pointer_pulse", True),
    # channels: with a str arm is a reference, without is an inline object
    ("Union[XYDriveMW, str]", None, False, "pointer_channel", False),
    ("Optional[XYDriveMW]", None, False, "inline_object", True),
    ("Union[XYDriveIQ, XYDriveMW]", None, False, "inline_object", False),
    # dict values
    ("dict[str, Pulse]", None, False, "dict_of_pointers", False),
    ("Dict[str, Union[Pulse, str]]", None, False, "dict_of_pointers", False),
    ("Dict[str, XYDriveMW]", None, False, "dict_of_objects", False),
    ("dict[str, Any]", None, False, "unknown", False),
    ("dict", None, False, "unknown", False),
    ("Dict[str, float]", None, False, "plain", False),
    # scalars that may be references
    ("ScalarInt", None, False, "scalar_or_pointer", False),
    ("ScalarFloat", None, True, "scalar_or_pointer", True),
    ("Union[int, str]", None, False, "scalar_or_pointer", False),
    ("Optional[Union[float, str]]", None, False, "scalar_or_pointer", True),
    ("typing.Union[qm.qua._expressions.QuaVariable[int], qm.qua._expressions.QuaLiteral[int], int]",
     None, False, "scalar_or_pointer", False),
    # unknown
    ("Any", None, False, "unknown", False),
    ("typing.Optional[typing.Any]", None, False, "unknown", True),
    ("", None, False, "unknown", False),
    ("Union[Pulse, str", None, False, "unknown", False),
    ("Union[Pulse, str]]", None, False, "unknown", False),
    ("SomeUnknownThing", None, False, "unknown", False),
    # plain
    ("int", 0, True, "plain", False),
    ("float", None, True, "plain", True),
    ("str", "x", True, "plain", False),
    ("Optional[str]", None, False, "plain", True),
    ("List[float]", None, False, "plain", False),
    ("Tuple[str, int]", None, False, "plain", False),
    ("bool", False, True, "plain", False),
]


@pytest.mark.parametrize("ann,default,has_default,kind,optional", TABLE)
def test_classifier_table(ann, default, has_default, kind, optional):
    got = fk.classify(ann, default, has_default=has_default)
    assert got["kind"] == kind, (ann, got)
    assert got["optional"] is optional, (ann, got)
    assert got["kind"] in fk.KINDS


def test_env_names_override_the_suffix_rule():
    # a lab pulse class not ending in "Pulse", known only from the env's MRO
    assert fk.classify("Union[MyWaveform, str]", pulse_names=["MyWaveform"])["kind"] == "pointer_pulse"
    # a component known from the env, with and without a str arm
    assert fk.classify("Union[TWPA, str]", component_names=["TWPA"])["kind"] == "pointer_or_value"
    assert fk.classify("Optional[TWPA]", component_names=["TWPA"])["kind"] == "inline_object"
    assert fk.classify("Optional[TWPA]")["kind"] == "unknown"
    assert fk.classify("dict[str, TWPA]", component_names=["mod.TWPA"])["kind"] == "dict_of_objects"


def test_targets_name_the_class():
    got = fk.classify("Union[quam.components.pulses.SquarePulse, str]")
    assert got["targets"] == ["SquarePulse"]


def test_typing_object_is_accepted():
    import typing
    assert fk.classify(typing.Optional[typing.Any])["kind"] == "unknown"
    assert fk.classify(None)["kind"] == "unknown"


# ---- probe script, base interpreter (no quam needed) ----

def _run_probe(tmp_path, spec, python=sys.executable, env_extra=None):
    import os
    (tmp_path / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    env = dict(os.environ)
    env.update(env_extra or {})
    p = subprocess.run([python, str(PROBE), "--spec", str(tmp_path / "spec.json"),
                        "--out", str(tmp_path / "out.json")],
                       capture_output=True, text=True, timeout=300, env=env)
    return p.returncode, json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))


def test_probe_reports_failing_and_hanging_modules(tmp_path):
    pkg = tmp_path / "mods"
    pkg.mkdir()
    (pkg / "lab_hang.py").write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    (pkg / "lab_boom.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    (pkg / "lab_exit.py").write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"qubits": {"q1": {"__class__": "lab_boom.Thing"}}}), encoding="utf-8")
    rc, out = _run_probe(tmp_path, {"modules": ["lab_hang", "lab_exit"], "state_path": str(state),
                                    "timeout_s": 2}, env_extra={"PYTHONPATH": str(pkg)})
    assert rc == 0 and out["status"] == "ok"
    assert out["modules"]["lab_hang"] == "timeout"
    assert out["modules"]["lab_boom"].startswith("error: RuntimeError")
    assert out["modules"]["lab_exit"].startswith("error: SystemExit")
    assert "lab_boom.Thing" in out["missing_classes"]


def test_classes_in_state_collects_every_class():
    from quam_state_manager.generator.probe_roles import classes_in_state
    st = {"__class__": "a.Root", "qubits": {"q1": {"__class__": "a.Q", "xy": {"__class__": "b.C"}}},
          "l": [{"__class__": "a.Q"}, {"__class__": "noDot"}]}
    assert classes_in_state(st) == ["a.Root", "a.Q", "b.C"]


def test_probe_never_walks_packages():
    src = PROBE.read_text(encoding="utf-8")
    assert "walk_packages" not in src.split('"""', 2)[2]
    assert "iter_modules" not in src


# ---- manifest readers ----

def _man(qb, measure=True, root_fields=()):
    classes = {"x.Root": {"role": "root", "origin": "lab", "fields": {f: {} for f in root_fields}}}
    if measure:
        classes[rm._MEASURE_MACRO] = {"role": "macro_qubit", "origin": "official", "fields": {}}
    return {"versions": {"quam_builder": qb}, "classes": classes}


def test_readout_capability_rule():
    assert rm.readout_capability(_man("0.6.0"))["mode"] == "pulse_and_macro"
    assert rm.readout_capability(_man("0.4.0"))["mode"] == "pulse_only"
    assert rm.readout_capability(_man("0.6.0", measure=False))["mode"] == "pulse_only"
    assert rm.readout_capability(_man(None))["mode"] == "pulse_only"
    assert rm.readout_capability(_man("0.10.1"))["mode"] == "pulse_and_macro"


def test_root_capabilities():
    got = rm.root_capabilities(_man("0.4.0", root_fields=("active_twpa_names", "extras")))
    assert got == {"x.Root": {"origin": "lab", "active_twpa_names": True, "extras": True}}
    assert rm.root_capabilities(_man("0.4.0"))["x.Root"]["active_twpa_names"] is False


def test_cache_round_trip(tmp_path, monkeypatch):
    py = sys.executable
    monkeypatch.setattr(rm, "_env_signature", lambda p: "sig1")
    man = _man("0.6.0")
    (tmp_path / rm._CACHE_FILENAME).write_text(json.dumps({py: {
        "format": rm.ROLE_FORMAT, "signature": "sig1",
        "requested": {"classes": ["a.B"], "modules": ["m"]}, "manifest": man}}), encoding="utf-8")
    assert rm.roles_for_env(py, tmp_path) == man
    assert rm.roles_for_env(py, tmp_path, state_classes=["a.B"], modules=["m"]) == man
    assert rm.roles_for_env(py, tmp_path, state_classes=["a.C"]) is None
    assert rm.roles_for_env(py, tmp_path, modules=["other"]) is None
    # a class the cached manifest already describes (read through a module) is served
    in_man = next(iter(man["classes"]))
    assert rm.roles_for_env(py, tmp_path, state_classes=[in_man]) == man
    monkeypatch.setattr(rm, "_env_signature", lambda p: "sig2")
    assert rm.roles_for_env(py, tmp_path) is None


# ---- a real official env (skipped when absent) ----

def _env_python(name):
    import shutil
    for base in (Path(r"C:\ProgramData\miniconda3\envs"), Path.home() / "miniconda3" / "envs",
                 Path.home() / "anaconda3" / "envs"):
        p = base / name / ("python.exe" if sys.platform == "win32" else "bin/python")
        if p.is_file():
            return str(p)
    return shutil.which(f"{name}-python")


@pytest.mark.parametrize("env", ["qsm_official", "qsm_official_ext"])
def test_probe_in_official_env(tmp_path, env):
    py = _env_python(env)
    if not py:
        pytest.skip(f"env {env} not present")
    res = rm.probe_roles(py, tmp_path, timeout=300)
    assert res["ok"], res["error"]
    m = res["manifest"]
    c = m["classes"]
    sc = "quam_builder.architecture.superconducting.custom_gates."
    assert c[sc + "single_qubit_gates.MeasureMacro"]["role"] == "macro_qubit"
    cz = c[sc + "flux_tunable_transmon_pair.two_qubit_gates.CZGate"]
    assert cz["role"] == "macro_pair" and cz["origin"] == "official"
    assert cz["fields"]["flux_pulse_qubit"]["kind"] == "pointer_pulse"
    assert cz["fields"]["spectator_qubits_control"]["kind"] == "dict_of_pointers"
    assert cz["fields"]["duration_qubit"]["kind"] == "scalar_or_pointer"
    assert m["versions"]["quam_builder"] and m["versions"]["quam"]
    assert rm.readout_capability(m)["mode"] == (
        "pulse_and_macro" if rm._version_tuple(m["versions"]["quam_builder"]) >= (0, 6) else "pulse_only")
    # cached now: a request-path read answers without spawning
    assert rm.roles_for_env(py, tmp_path) is not None
