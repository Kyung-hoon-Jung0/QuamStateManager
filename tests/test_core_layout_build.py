"""Shared OPX cores written by the generator.

A qubit's xy and resonator share ``<qubit>_core`` when every output and input
port of both is on one controller and MW-FEM; a TWPA gets one core for its pump
pair and one for its isolation pair; a CR / ZZ channel follows its control
qubit's xy core on the same FEM. A spec without ``populate.options.pin_cores``
counts as on, in the wizard build and in the emitted scripts; ``False`` writes
no core name. Unit tests use stand-in objects; the live tests skip without a
usable QM env.
"""
from __future__ import annotations

import copy
import json
import subprocess
from types import SimpleNamespace as NS

import pytest

from quam_state_manager.core import script_emitter
from quam_state_manager.generator import run_build as rb


# ---------------------------------------------------------------- stand-ins

def _port(con="con1", fem=1):
    return NS(controller_id=con, fem_id=fem)


def _mw(out=None, inp=None):
    return NS(opx_output=out, opx_input=inp, core=None)


def _machine(qubits=None, pairs=None, twpas=None):
    return NS(qubits=qubits or {}, qubit_pairs=pairs or {}, twpas=twpas or {})


def test_same_fem_test():
    assert rb._pin_same_fem(_mw(_port()), _mw(_port(), _port())) == ("con1", 1)
    assert rb._pin_same_fem(_mw(_port()), _mw(_port(fem=2))) is None
    assert rb._pin_same_fem(_mw(_port()), _mw(_port(), _port(con="con2"))) is None
    assert rb._pin_same_fem(_mw(_port()), _mw()) is None          # no port at all
    assert rb._pin_same_fem(_mw(_port()), None) is None


def test_qubit_on_one_fem_shares_one_named_core():
    xy, res = _mw(_port()), _mw(_port(), _port())
    rb._pin_cores(_machine({"q1": NS(xy=xy, resonator=res)}))
    assert xy.core == res.core == "q1_core"


def test_resonator_input_on_another_fem_keeps_own_cores():
    xy, res = _mw(_port()), _mw(_port(), _port(fem=2))
    rb._pin_cores(_machine({"q1": NS(xy=xy, resonator=res)}))
    assert xy.core is None and res.core is None


def test_qubit_with_only_xy_or_only_resonator_keeps_own_core():
    xy = _mw(_port())
    res = _mw(_port(), _port())
    rb._pin_cores(_machine({"q1": NS(xy=xy, resonator=None),
                            "q2": NS(xy=None, resonator=res)}))
    assert xy.core is None and res.core is None


def test_lf_iq_channels_are_not_assigned():
    xy = NS(opx_output_I=_port(), opx_output_Q=_port(), core=None)
    res = NS(opx_output_I=_port(), opx_output_Q=_port(), opx_input_I=_port(),
             opx_input_Q=_port(), core=None)
    rb._pin_cores(_machine({"q1": NS(xy=xy, resonator=res)}))
    assert xy.core is None and res.core is None


def test_cr_follows_the_control_core_only_on_its_fem():
    xy1, res1 = _mw(_port()), _mw(_port(), _port())
    cr_same, zz_other = _mw(_port()), _mw(_port(fem=2))
    q1 = NS(xy=xy1, resonator=res1)
    pair = NS(qubit_control=q1, cross_resonance=cr_same, zz_drive=zz_other)
    rb._pin_cores(_machine({"q1": q1}, {"q1-2": pair}))
    assert cr_same.core == "q1_core"
    assert zz_other.core is None


def test_twpa_two_cores_and_partial_pairs():
    pump, pump_ = _mw(_port()), _mw(_port())
    iso, iso_ = _mw(_port()), _mw(_port(fem=2))          # isolation split over FEMs
    rb._pin_cores(_machine(twpas={"twpa1": NS(pump=pump, pump_=pump_,
                                              isolation=iso, isolation_=iso_)}))
    assert pump.core == pump_.core == "twpa1_pump_core"
    assert iso.core is None and iso_.core is None
    lone = _mw(_port())
    rb._pin_cores(_machine(twpas={"twpaA": NS(pump=lone)}))
    assert lone.core == "twpaA_pump_core"
    not_opx = NS(opx_output=None, core=None)
    rb._pin_cores(_machine(twpas={"twpaB": NS(pump=not_opx)}))
    assert not_opx.core is None


# ---------------------------------------------------------------- live builds

def _env():
    from tests.test_script_emitter_live import _first_usable_qm_env
    python = _first_usable_qm_env()
    if not python:
        pytest.skip("no usable QM env found")
    return python


def _five_qubit_spec(isolation=True, twpa=True):
    qs = [f"q{i}" for i in range(1, 6)]
    lines = []
    for q in qs:
        lines.append({"element": q, "line": "resonator", "group": "f",
                      "channel": {"kind": "mw_fem"}})
        lines.append({"element": q, "line": "drive", "channel": {"kind": "mw_fem"}})
    twpas = []
    if twpa:
        twpas = [{"id": "twpa1", "qubits": qs}]
        lines.append({"element": "twpa1", "line": "twpa_pump", "channel": None})
        if isolation:
            lines.append({"element": "twpa1", "line": "twpa_isolation",
                          "channel": {"kind": "mw_fem"}})
    return {
        "network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
        "instruments": {"controllers": [{"con": 1, "fems": [{"slot": 1, "fem": "mw"}]}],
                        "opx_plus": [], "octaves": []},
        "qubits": qs, "qubit_pairs": [], "twpas": twpas, "pair_gate": "none",
        "lines": lines,
        "populate": {
            "qubit": {q: {"RF_freq": 5.0e9 + 0.05e9 * i, "LO_frequency": 5.1e9}
                      for i, q in enumerate(qs)},
            "resonator": {q: {"RF_freq": 7.2e9 + 0.02e9 * i, "LO_frequency": 7.35e9}
                          for i, q in enumerate(qs)},
        },
    }


def _build(python, spec, out):
    from quam_state_manager.core.config_generator import run_generator
    outcome = run_generator(python, "build", spec, out, timeout=600)
    assert outcome.get("ok"), outcome
    return outcome, json.loads((out / "state.json").read_text(encoding="utf-8"))


def _cores(state):
    """{dotted element path: core} for every channel dict that carries a core key."""
    found = {}

    def walk(node, path):
        if isinstance(node, dict):
            if "core" in node:
                found[path] = node["core"]
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k)
    walk(state, "")
    return found


def _expect_layout_a(state, isolation=True):
    for q in state["qubits"]:
        assert state["qubits"][q]["xy"]["core"] == f"{q}_core"
        assert state["qubits"][q]["resonator"]["core"] == f"{q}_core"
    tw = (state.get("twpas") or {}).get("twpa1")
    if tw is None:
        return
    assert tw["pump"]["core"] == tw["pump_"]["core"] == "twpa1_pump_core"
    if isolation and tw.get("isolation"):
        assert tw["isolation"]["core"] == tw["isolation_"]["core"] == "twpa1_isolation_core"
    names = set(_cores(state).values()) - {None}
    assert names <= ({f"{q}_core" for q in state["qubits"]}
                     | {"twpa1_pump_core", "twpa1_isolation_core"}), names


def test_default_build_writes_layout_a_and_two_twpa_cores(tmp_path):
    python = _env()
    spec = _five_qubit_spec()
    assert "options" not in spec["populate"]          # no key counts as on
    _, state = _build(python, spec, tmp_path / "a")
    if not (state.get("twpas") or {}).get("twpa1", {}).get("isolation"):
        pytest.skip("this env's quam_builder builds no TWPA isolation line")
    _expect_layout_a(state)


def test_pump_only_twpa_writes_only_the_pump_name(tmp_path):
    python = _env()
    _, state = _build(python, _five_qubit_spec(isolation=False), tmp_path / "a")
    if not state.get("twpas"):
        pytest.skip("this env's quam_builder builds no TWPAs")
    names = set(_cores(state).values())
    assert "twpa1_pump_core" in names and "twpa1_isolation_core" not in names
    _expect_layout_a(state, isolation=False)


def test_no_twpa_and_checkbox_off(tmp_path):
    python = _env()
    _, state = _build(python, _five_qubit_spec(twpa=False), tmp_path / "a")
    names = set(_cores(state).values()) - {None}
    assert names == {f"q{i}_core" for i in range(1, 6)}
    spec = _five_qubit_spec()
    spec["populate"]["options"] = {"pin_cores": False}
    _, off = _build(python, spec, tmp_path / "b")
    assert _cores(off) and set(_cores(off).values()) == {None}


def _two_twpa_spec():
    # three qubits keep the eight MW outputs of the one MW-FEM enough
    spec = _five_qubit_spec(isolation=False)
    spec["qubits"] = ["q1", "q2", "q3"]
    spec["lines"] = [ln for ln in spec["lines"] if ln["element"] not in ("q4", "q5")]
    for part in spec["populate"].values():
        for q in ("q4", "q5"):
            part.pop(q, None)
    spec["twpas"] = [{"id": "twpa1", "qubits": ["q1", "q2"]},
                     {"id": "twpa2", "qubits": ["q3"]}]
    spec["lines"].append({"element": "twpa2", "line": "twpa_pump", "channel": None})
    spec["lines"].append({"element": "twpa2", "line": "twpa_isolation",
                          "channel": {"kind": "mw_fem"}})
    return spec


@pytest.mark.parametrize("case", ["pump_and_isolation", "pump_only", "two_twpas"])
def test_emitted_script_without_the_key_builds_layout_a(tmp_path, case):
    python = _env()
    spec = {"pump_and_isolation": lambda: _five_qubit_spec(),
            "pump_only": lambda: _five_qubit_spec(isolation=False),
            "two_twpas": _two_twpa_spec}[case]()
    outcome, a = _build(python, spec, tmp_path / "a")
    if not a.get("twpas"):
        pytest.skip("this env's quam_builder builds no TWPAs")
    result = outcome.get("result") or {}
    bundle = script_emitter.emit_bundle(
        copy.deepcopy(spec), result.get("allocation") or {}, result.get("versions") or {},
        "cores", stamp="2026-01-01")
    sdir = tmp_path / "scripts"
    script_emitter.write_bundle(sdir, bundle)
    out_b = tmp_path / "b"
    out_b.mkdir()
    for script in ("01_make_wiring.py", "02_build_machine.py"):
        r = subprocess.run([python, str(sdir / script), str(out_b)], capture_output=True,
                           text=True, encoding="utf-8", cwd=str(sdir), timeout=600)
        assert r.returncode == 0, f"{script}: {r.stdout}\n{r.stderr}"
        assert "NameError" not in r.stderr
    b = json.loads((out_b / "state.json").read_text(encoding="utf-8"))
    wa = json.loads((tmp_path / "a" / "wiring.json").read_text(encoding="utf-8"))
    wb = json.loads((out_b / "wiring.json").read_text(encoding="utf-8"))
    # every TWPA line the wizard wired (pump and isolation) is wired the same way
    assert wa["wiring"].get("twpas") == wb["wiring"].get("twpas")
    for tid, tw in a["twpas"].items():
        assert set(tw) == set(b["twpas"][tid]), tid
    iso = {tid for tid, tw in b["twpas"].items() if tw.get("isolation")}
    assert iso == {"pump_and_isolation": {"twpa1"}, "pump_only": set(),
                   "two_twpas": {"twpa2"}}[case]
    if case != "two_twpas":
        _expect_layout_a(b)
    assert _cores(a) == _cores(b)


def test_cr_channel_rides_the_control_core_in_a_real_build(tmp_path):
    python = _env()
    from tests.test_cr_live_env import _shared_cr_zz_spec
    _, state = _build(python, _shared_cr_zz_spec(), tmp_path / "a")
    pairs = state.get("qubit_pairs") or {}
    if not pairs:
        pytest.skip("this env builds no CR pairs")
    checked = 0
    for pair in pairs.values():
        ctrl = str(pair.get("qubit_control", "")).rsplit("/", 1)[-1]
        for chan in ("cross_resonance", "zz_drive"):
            c = pair.get(chan)
            if isinstance(c, dict) and "core" in c and ctrl in state["qubits"]:
                assert c["core"] == f"{ctrl}_core", (chan, c["core"])
                checked += 1
    assert checked


# ---------------------------------------------------------------- wizard

def test_wizard_checkbox_is_checked_and_writes_the_option():
    from pathlib import Path
    web = Path(__file__).resolve().parent.parent / "quam_state_manager" / "web"
    html = (web / "templates" / "_generate.html").read_text(encoding="utf-8")
    js = (web / "static" / "generate.js").read_text(encoding="utf-8")
    assert '<input type="checkbox" id="gen-pin-cores-chk" checked>' in html
    assert "Share one core per qubit (xy + readout)" in html
    assert ("Known risk: on QOP 3.8.1 (another device), with xy and resonator on one core, "
            "a resonator update_frequency inside a program made later x180 pulses have no "
            "effect; the simulator did not show it; it depended on the program layout. "
            "Not tested on QOP 3.7.3; a hardware test is planned.") in html
    assert "populate: { options: { pin_cores: true } }" in js
    assert "state.spec.populate.options.pin_cores = !!chk.checked;" in js
    # every build post carries the key
    body = js[js.index("function specWithCoBands()"):]
    assert body.index("ensurePinCores(state.spec);") < body.index("loBandFindings(")


# ---------------------------------------------------------------- identity

def test_checkbox_off_is_byte_identical_to_the_pre_layout_generator(tmp_path):
    """With the option off, state.json and wiring.json are byte for byte what
    the generator before shared cores wrote."""
    import io
    import shutil
    import tarfile
    from pathlib import Path
    python = _env()
    root = Path(__file__).resolve().parent.parent
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    arch = subprocess.run(["git", "archive", "c92b2fe9", "quam_state_manager/generator"],
                          capture_output=True, cwd=str(root))
    if arch.returncode != 0:
        pytest.skip("base commit not in this clone")
    base_dir = tmp_path / "base"
    with tarfile.open(fileobj=io.BytesIO(arch.stdout)) as tf:
        tf.extractall(base_dir)
    spec = _five_qubit_spec()
    spec["populate"]["options"] = {"pin_cores": False}
    spec_path = tmp_path / "_spec.json"
    spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    base_out = tmp_path / "base_out"
    r = subprocess.run([python, str(base_dir / "quam_state_manager" / "generator" / "run_build.py"),
                        "--mode", "build", "--spec", str(spec_path), "--out", str(base_out)],
                       capture_output=True, text=True, encoding="utf-8", timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    _build(python, spec, tmp_path / "built")
    for name in ("state.json", "wiring.json"):
        assert (tmp_path / "built" / name).read_bytes() == (base_out / name).read_bytes(), name
