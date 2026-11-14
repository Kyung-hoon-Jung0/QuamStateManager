"""Real builds: every output of a generated chip leaves on one time base.

With latency(band) from ``_BAND_TO_DELAY_NS`` and each port's delay from the
config ``machine.generate_config()`` makes of the saved chip, ``latency +
delay`` is the same number for every MW-FEM output and equals the delay of
every flux LF-FEM port. A chip whose MW outputs are in one latency class
(band 2, or bands 1 and 3) is byte-identical to the base commit's build.
Skips without a usable QM env.
"""

import copy
import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from quam_state_manager.core.config_generator import run_config_preview, run_generator
from tests.test_script_emitter_live import _first_usable_qm_env, _spec

BASE = "1ec245db"
LAT = {1: 141, 2: 161, 3: 141}
_ROOT = Path(__file__).resolve().parent.parent


def _one_qubit(xy_band, ro_band):
    s = copy.deepcopy(_spec())
    s["qubits"], s["qubit_pairs"], s["pair_gate"] = ["q1"], [], "none"
    s["lines"] = [ln for ln in s["lines"] if ln["element"] == "q1"]
    for ln in s["lines"]:
        if ln["line"] == "drive":        # off out 1, which is coupled with the readout input
            ln["channel"] = {"kind": "mw_fem", "out_port": 2}
    s["populate"] = {
        "qubit": {"q1": {"RF_freq": 5.2e9, "LO_frequency": 5.0e9, "band": xy_band}},
        "resonator": {"q1": {"RF_freq": 7.2e9, "LO_frequency": 7.35e9, "band": ro_band}}}
    return s


def _pair(b1, b2, ro):
    s = copy.deepcopy(_spec())
    s["populate"]["qubit"]["q1"]["band"] = b1
    s["populate"]["qubit"]["q2"]["band"] = b2
    for r in s["populate"]["resonator"].values():
        r["band"] = ro
    return s


def _build(python, spec, out):
    outcome = run_generator(python, "build", spec, out, timeout=600)
    assert outcome.get("ok"), outcome
    return json.loads((out / "state.json").read_text(encoding="utf-8"))


def _config_ports(python, out):
    cfg = run_config_preview(python, out, timeout=300)
    assert cfg.get("ok"), cfg
    config = (cfg.get("result") or {}).get("config") or {}
    mw, lf = {}, {}
    for con, c in config["controllers"].items():
        for fem, f in (c.get("fems") or {}).items():
            kind = f.get("type")
            for pid, e in (f.get("analog_outputs") or {}).items():
                key = (con, str(fem), str(pid))
                if kind == "MW" or "band" in e:
                    mw[key] = (e["band"], e.get("delay", 0))
                else:
                    lf[key] = e.get("delay", 0)
    return mw, lf


CASES = {
    # name: (spec, MW delays by band, flux delay, time of flight per resonator)
    "pair_bands_2_and_1": (lambda: _pair(2, 1, 2), {2: 0, 1: 20}, 161, 32),
    "xy2_readout3": (lambda: _one_qubit(2, 3), {2: 0, 3: 20}, 161, 52),
    "xy3_readout2": (lambda: _one_qubit(3, 2), {3: 20, 2: 0}, 161, 32),
    "all_band_2": (lambda: _pair(2, 2, 2), {2: 0}, 161, 32),
    "bands_1_and_3": (lambda: _pair(1, 1, 3), {1: 0, 3: 0}, 141, 32),
}


@pytest.mark.parametrize("name", list(CASES))
def test_every_output_arrives_together(tmp_path, name):
    python = _first_usable_qm_env()
    if not python:
        pytest.skip("no usable QM env found")
    make, mw_by_band, flux, tof = CASES[name]
    out = tmp_path / name
    state = _build(python, make(), out)

    # state.json
    for con in state["ports"]["mw_outputs"].values():
        for fem in con.values():
            for p in fem.values():
                assert p["delay"] == mw_by_band[p["band"]], (name, p)
    lf_state = [p["delay"] for con in state["ports"]["analog_outputs"].values()
                for fem in con.values() for p in fem.values()]
    assert lf_state and set(lf_state) == {flux}, (name, lf_state)
    assert {q["resonator"]["time_of_flight"] for q in state["qubits"].values()} == {tof}

    # the config generated from the saved chip, and the arrival-time property
    mw, lf = _config_ports(python, out)
    assert mw and lf
    arrivals = {LAT[band] + delay for band, delay in mw.values()}
    assert arrivals == {flux} == set(lf.values()), (name, mw, lf)


def _base_generator(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    arch = subprocess.run(["git", "archive", BASE, "quam_state_manager/generator"],
                          capture_output=True, cwd=str(_ROOT))
    if arch.returncode != 0:
        pytest.skip("base commit not in this clone")
    base_dir = tmp_path / "base"
    with tarfile.open(fileobj=io.BytesIO(arch.stdout)) as tf:
        tf.extractall(base_dir)
    return base_dir / "quam_state_manager" / "generator" / "run_build.py"


@pytest.mark.parametrize("name", ["all_band_2", "bands_1_and_3"])
def test_one_latency_class_is_byte_identical_to_the_base_build(tmp_path, name):
    python = _first_usable_qm_env()
    if not python:
        pytest.skip("no usable QM env found")
    gen = _base_generator(tmp_path)
    spec = CASES[name][0]()
    # the base generator writes no core names; shared cores are on by default now
    spec["populate"].setdefault("options", {})["pin_cores"] = False
    spec_path = tmp_path / "_spec.json"
    spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    base_out = tmp_path / "base_out"
    r = subprocess.run([python, str(gen), "--mode", "build", "--spec", str(spec_path),
                        "--out", str(base_out)],
                       capture_output=True, text=True, encoding="utf-8", timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    out = tmp_path / "built"
    _build(python, spec, out)
    for fn in ("state.json", "wiring.json"):
        assert (out / fn).read_bytes() == (base_out / fn).read_bytes(), f"{fn} differs"


# --- the build result lists what the build wrote (core/output_timing) ------

def _shared_feedline_2_and_3():
    """Two resonators typed band 2 and band 3 on one feedline port, xy band 1:
    the port takes one band, so the result shows no per-line shift."""
    s = _pair(1, 1, 2)
    s["populate"]["resonator"]["q2"]["band"] = 3
    return s


def _dedicated_cr():
    """xy band 2 and a dedicated cross-resonance MW port (band 1)."""
    s = _pair(2, 2, 2)
    s["pair_gate"] = "cr"
    s["lines"] = [ln for ln in s["lines"] if ln["line"] != "coupler"] + [
        {"element": "q1-q2", "line": "cross_resonance",
         "channel": {"kind": "mw_fem", "out_port": 5}}]
    s["populate"].pop("pairs", None)
    return s


def _pump_band_1():
    """xy band 2 with a TWPA pump port in band 1."""
    s = _pair(2, 2, 2)
    s["twpas"] = [{"id": "twpa1"}]
    s["lines"] = s["lines"] + [{"element": "twpa1", "line": "twpa_pump",
                                "channel": {"kind": "mw_fem", "out_port": 5}}]
    s["populate"]["twpa"] = {"twpa1": {"pump_frequency": 4.2e9, "band": 1}}
    return s


def _typed_tof():
    s = _one_qubit(2, 3)
    s["populate"]["resonator"]["q1"]["time_of_flight"] = 300
    return s


def _prefilled_tof():
    """xy band 2, readout band 3, every time of flight the wizard's prefill."""
    s = _pair(2, 2, 3)
    for ln in s["lines"]:              # off out 1, which is coupled with the readout input
        if ln["line"] == "drive":
            ln["channel"] = {"kind": "mw_fem", "out_port": {"q1": 2, "q2": 3}[ln["element"]]}
    s["populate"].setdefault("options", {})["tof_defaults"] = {}
    for q, r in s["populate"]["resonator"].items():
        r["time_of_flight"] = 28
        s["populate"]["options"]["tof_defaults"][q] = 28
    return s


def _prefilled_tof_q2_set():
    """The same, the user changed q2's cell to 300."""
    s = _prefilled_tof()
    s["populate"]["resonator"]["q2"]["time_of_flight"] = 300
    return s


EXTRA = {"shared_feedline_2_and_3": _shared_feedline_2_and_3, "dedicated_cr": _dedicated_cr,
         "pump_band_1": _pump_band_1, "typed_tof": _typed_tof,
         "prefilled_tof": _prefilled_tof, "prefilled_tof_q2_set": _prefilled_tof_q2_set}


@pytest.mark.parametrize("name", list(EXTRA))
def test_result_lists_what_the_build_wrote(tmp_path, name):
    from quam_state_manager.core import output_timing
    python = _first_usable_qm_env()
    if not python:
        pytest.skip("no usable QM env found")
    spec = EXTRA[name]()
    out = tmp_path / name
    outcome = run_generator(python, "build", spec, out, timeout=600)
    assert outcome.get("ok"), outcome
    state = json.loads((out / "state.json").read_text(encoding="utf-8"))
    wiring = json.loads((out / "wiring.json").read_text(encoding="utf-8"))
    summ = output_timing.summary(state, wiring, tof_typed=output_timing.typed_tof(spec))
    # every MW output in use: latency + delay is one number, equal to every flux delay
    arrivals = {LAT[m["band"]] + m["delay"] for m in summ["mw"]}
    assert len(arrivals) == 1 and {f["delay"] for f in summ["flux"]} <= arrivals, summ
    # the summary is the state: every listed delay is the stored one
    for m in summ["mw"]:
        node = state
        for part in m["port"].split("/"):
            node = node[part]
        assert node.get("delay", 0) == m["delay"]
    if name == "shared_feedline_2_and_3":
        assert {m["delay"] for m in summ["mw"]} == {0} and summ["tof"] == [], summ
    if name == "dedicated_cr":
        cr = state["qubit_pairs"]["q1-2"]["cross_resonance"]["opx_output"]
        _, port = output_timing._resolve({"wiring": wiring["wiring"], **state}, cr)
        assert port["band"] == 1 and port["delay"] == 20, port
        assert {m["delay"] for m in summ["mw"] if m["band"] == 1} == {20}
    if name == "pump_band_1":
        assert any(m["band"] == 1 and m["delay"] == 20 for m in summ["mw"]), summ
    if name == "typed_tof":
        assert state["qubits"]["q1"]["resonator"]["time_of_flight"] == 300
        assert summ["tof"] == [{"qubit": "q1", "shift": 20, "value": 300, "moved": False}]
        assert any("left as you set it on q1;" in w for w in outcome["result"]["warnings"])
    if name == "prefilled_tof":
        assert {q["resonator"]["time_of_flight"] for q in state["qubits"].values()} == {48}
        assert summ["tof"] and all(t["moved"] for t in summ["tof"]), summ
        assert not any("left as you set it" in w for w in outcome["result"]["warnings"])
    if name == "prefilled_tof_q2_set":
        tofs = {q: v["resonator"]["time_of_flight"] for q, v in state["qubits"].items()}
        assert tofs == {"q1": 48, "q2": 300}, tofs
        assert [t["moved"] for t in summ["tof"]] == [True, False], summ
        assert any("left as you set it on q2;" in w for w in outcome["result"]["warnings"])
