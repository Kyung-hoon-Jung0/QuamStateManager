"""Render a self-contained LF-FEM DC offset calibration script.

`render_script(params)` returns the text of one Python file a person runs by
hand on real hardware (VS Code / Jupyter ``#%%`` cells or ``python file.py``).
The script imports only ``qm``, ``qualang_tools`` and ``numpy``; it never
imports quam_state_manager. Every port / slot / controller / mode value lives
in one settings block at the top; everything below it is derived from those
variables, so changing a port never needs an edit further down.

The algorithm follows the reference DC-offset calibration procedure
(slope from +-step, sign coupling, bounded correction, tolerance, max
iterations), with the offset clamp taken from the output mode
(direct 0.5 V, amplified 2.5 V) instead of a fixed 1.0 V.
"""

from __future__ import annotations

import numbers
import pprint

__all__ = ["render_script", "DEFAULTS", "SETTINGS_BEGIN", "SETTINGS_END"]

SETTINGS_BEGIN = "# ======================= USER SETTINGS -- edit here ======================="
SETTINGS_END = "# =================== END OF USER SETTINGS (nothing below needs editing) ==="

OUTPUT_MODES = ("direct", "amplified")

DEFAULTS = {
    "qop_ip": "127.0.0.1",
    "cluster_name": None,
    "qop_port": None,
    "con": "con1",
    "fem": 1,
    "ao_port": 1,
    "ai_port": 1,
    "output_mode": "direct",
    "ai_gain_db": 0,
    "ao_offset_start": 0.0,
    "ai_offset_start": 0.0,
    "ai_offset": None,  # used when the input step is skipped; None -> ai_offset_start
    "n_avg": 100,
    "tol_mV": 0.5,
    "max_iters": 6,
    "max_step_V": 0.1,
    "slope_step_V": 0.05,
    "time_of_flight": 28,
    "readout_len": 1728,
    "run_input_cal": True,
    "run_output_cal": True,
    "pause_for_cable": True,
    "other_controllers": None,
}


def _int(p, key, lo=None, hi=None, multiple=None):
    v = p[key]
    if isinstance(v, bool) or not isinstance(v, numbers.Integral):
        raise ValueError(f"{key} must be an integer, got {v!r}")
    v = int(v)
    if lo is not None and v < lo:
        raise ValueError(f"{key} must be >= {lo}, got {v}")
    if hi is not None and v > hi:
        raise ValueError(f"{key} must be <= {hi}, got {v}")
    if multiple and v % multiple:
        raise ValueError(f"{key} must be a multiple of {multiple}, got {v}")
    return v


def _float(p, key, positive=False, limit=None):
    v = p[key]
    if isinstance(v, bool) or not isinstance(v, numbers.Real):
        raise ValueError(f"{key} must be a number, got {v!r}")
    v = float(v)
    if v != v or v in (float("inf"), float("-inf")):
        raise ValueError(f"{key} must be finite, got {v}")
    if positive and v <= 0:
        raise ValueError(f"{key} must be > 0, got {v}")
    if limit is not None and abs(v) > limit:
        raise ValueError(f"|{key}| must be <= {limit} V, got {v}")
    return v


def _validate(params):
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError("params must be a dict")
    unknown = set(params) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown parameter(s): {sorted(unknown)}")
    p = dict(DEFAULTS)
    p.update({k: v for k, v in params.items()})
    out = {}
    if not isinstance(p["qop_ip"], str) or not p["qop_ip"]:
        raise ValueError("qop_ip must be a non-empty string")
    out["qop_ip"] = p["qop_ip"]
    if p["cluster_name"] is not None and not isinstance(p["cluster_name"], str):
        raise ValueError("cluster_name must be a string or None")
    out["cluster_name"] = p["cluster_name"]
    out["qop_port"] = None if p["qop_port"] is None else _int(p, "qop_port", 1, 65535)
    if not isinstance(p["con"], str) or not p["con"]:
        raise ValueError("con must be a non-empty string")
    out["con"] = p["con"]
    out["fem"] = _int(p, "fem", 1, 8)
    out["ao_port"] = _int(p, "ao_port", 1, 8)
    out["ai_port"] = _int(p, "ai_port", 1, 2)
    if p["output_mode"] not in OUTPUT_MODES:
        raise ValueError(f"output_mode must be one of {OUTPUT_MODES}, got {p['output_mode']!r}")
    out["output_mode"] = p["output_mode"]
    out["ai_gain_db"] = _int(p, "ai_gain_db")
    ao_limit = 0.5 if out["output_mode"] == "direct" else 2.5
    out["ao_offset_start"] = _float(p, "ao_offset_start", limit=ao_limit)
    out["ai_offset_start"] = _float(p, "ai_offset_start", limit=0.5)
    if p["ai_offset"] is None:
        out["ai_offset"] = out["ai_offset_start"]
    else:
        out["ai_offset"] = _float(p, "ai_offset", limit=0.5)
    out["n_avg"] = _int(p, "n_avg", 1)
    out["tol_mV"] = _float(p, "tol_mV", positive=True)
    out["max_iters"] = _int(p, "max_iters", 1)
    out["max_step_V"] = _float(p, "max_step_V", positive=True)
    out["slope_step_V"] = _float(p, "slope_step_V", positive=True)
    out["time_of_flight"] = _int(p, "time_of_flight", 24, multiple=4)
    out["readout_len"] = _int(p, "readout_len", 16, multiple=4)
    for k in ("run_input_cal", "run_output_cal", "pause_for_cable"):
        if not isinstance(p[k], bool):
            raise ValueError(f"{k} must be True or False")
        out[k] = p[k]
    oc = p["other_controllers"]
    if oc is not None and not isinstance(oc, dict):
        raise ValueError("other_controllers must be a controllers dict or None")
    out["other_controllers"] = oc or {}
    return out


def _settings_block(v):
    oc = pprint.pformat(v["other_controllers"], indent=4, width=100, sort_dicts=False)
    lines = [
        SETTINGS_BEGIN,
        "# Network",
        f"QOP_IP = {v['qop_ip']!r}",
        f"CLUSTER_NAME = {v['cluster_name']!r}",
        f"QOP_PORT = {v['qop_port']!r}",
        "",
        "# Which port: controller (chassis) name, LF-FEM slot, analog output and analog input",
        f"CON = {v['con']!r}",
        f"FEM = {v['fem']!r}",
        f"AO_PORT = {v['ao_port']!r}  # analog output 1..8",
        f"AI_PORT = {v['ai_port']!r}  # analog input 1 or 2 (input1()/input2() is chosen from this)",
        "",
        "# Port settings -- offsets differ between modes and after a gain change",
        f"OUTPUT_MODE = {v['output_mode']!r}  # 'direct' (+-0.5 V) or 'amplified' (+-2.5 V)",
        f"AI_GAIN_DB = {v['ai_gain_db']!r}",
        "",
        "# Start values",
        f"AO_OFFSET_START = {v['ao_offset_start']!r}  # V",
        f"AI_OFFSET_START = {v['ai_offset_start']!r}  # V, start of the input calibration",
        f"AI_OFFSET = {v['ai_offset']!r}  # V, used for the output step when RUN_INPUT_CAL = False",
        "",
        "# Steps to run",
        f"RUN_INPUT_CAL = {v['run_input_cal']!r}  # step 1: analog input OPEN (nothing connected)",
        f"RUN_OUTPUT_CAL = {v['run_output_cal']!r}  # step 2: loopback cable AO -> AI",
        f"PAUSE_FOR_CABLE = {v['pause_for_cable']!r}  # wait for Enter before step 2",
        "",
        "# Algorithm",
        f"N_AVG = {v['n_avg']!r}",
        f"TOL_MV = {v['tol_mV']!r}",
        f"MAX_ITERS = {v['max_iters']!r}",
        f"MAX_STEP_V = {v['max_step_V']!r}  # largest offset change per iteration",
        f"SLOPE_STEP_V = {v['slope_step_V']!r}  # +- step used to measure the slope",
        "",
        "# Timing (ns)",
        f"TIME_OF_FLIGHT = {v['time_of_flight']!r}",
        f"READOUT_LEN = {v['readout_len']!r}",
        "",
        "# Other controllers / FEMs / ports to keep in the config with their real settings.",
        "# The port under calibration above is always written over whatever is here.",
        f"OTHER_CONTROLLERS = {oc}",
        SETTINGS_END,
    ]
    return "\n".join(lines)


_HEADER = '''#%%
"""
LF-FEM DC offset calibration (OPX1000), one analog output + one analog input.

What it does
  Step 1 (input):  with NOTHING connected to the analog input, adjust the
                   analog input offset until the measured DC is ~0 mV.
  Step 2 (output): connect a loopback cable AO -> AI, then adjust the analog
                   output offset until the measured DC is ~0 mV.
  Each step measures the slope with a +-SLOPE_STEP_V probe, then applies a
  bounded correction (at most MAX_STEP_V) until |DC| <= TOL_MV or MAX_ITERS.

Notes
  * The LF-FEM analog input is inverted. The measured slope sign already
    handles this, so the printed AO offset is used as is (no sign flip).
  * Offsets differ between 'direct' and 'amplified' output mode and change
    after an analog input gain_db change: calibrate in the mode you will use.
  * Closing a quantum machine returns the outputs to 0 V; the offsets found
    here take effect only when you put them in your own configuration.
  * The only file written is lf_offsets.csv next to this script.

Edit only the USER SETTINGS block below.
"""
'''

_BODY = r'''

# =========================
# Imports
# =========================
import csv
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import numpy as np
from qm import QuantumMachinesManager
from qm.qua import *  # noqa: F401,F403
from qualang_tools.units import unit

u = unit(coerce_to_integer=True)

if OUTPUT_MODE not in ("direct", "amplified"):
    raise ValueError(f"OUTPUT_MODE must be 'direct' or 'amplified', got {OUTPUT_MODE!r}")
if AI_PORT not in (1, 2):
    raise ValueError(f"AI_PORT must be 1 or 2, got {AI_PORT!r}")
AO_LIMIT_V = 0.5 if OUTPUT_MODE == "direct" else 2.5  # output offset clamp for this mode
AI_LIMIT_V = 0.5  # input offset clamp
ELEM = "lf_offset_probe"

# =========================
# Configuration (built from the settings)
# =========================
def build_config(ao_offset, ai_offset):
    # Every controller is an OPX1000; FEM and port keys are all strings.
    controllers = {}
    for name, ctrl in deepcopy(OTHER_CONTROLLERS).items():
        ctrl = dict(ctrl, type="opx1000")
        fems = {}
        for slot, fem in (ctrl.get("fems") or {}).items():
            fem = dict(fem)
            for io in ("analog_outputs", "analog_inputs", "digital_outputs", "digital_inputs"):
                if io in fem:
                    fem[io] = {str(k): v for k, v in fem[io].items()}
            fems[str(slot)] = fem
        ctrl["fems"] = fems
        controllers[name] = ctrl
    ctrl = controllers.setdefault(CON, {"type": "opx1000", "fems": {}})
    fems = ctrl["fems"]
    fem_cfg = fems.setdefault(str(FEM), {"type": "LF"})
    fem_cfg["type"] = "LF"
    aos = fem_cfg.setdefault("analog_outputs", {})
    ais = fem_cfg.setdefault("analog_inputs", {})
    aos[str(AO_PORT)] = {
        "offset": float(ao_offset),
        "output_mode": OUTPUT_MODE,
        "sampling_rate": int(1e9),
        "upsampling_mode": "pulse",
    }
    ais[str(AI_PORT)] = {
        "offset": float(ai_offset),
        "gain_db": AI_GAIN_DB,
        "sampling_rate": int(1e9),
    }
    return {
        "version": 1,
        "controllers": controllers,
        "elements": {
            ELEM: {
                "singleInput": {"port": (CON, FEM, AO_PORT)},
                "intermediate_frequency": 0.0,
                "operations": {"readout": "readout_pulse"},
                "outputs": {"out1": (CON, FEM, AI_PORT)},
                "time_of_flight": TIME_OF_FLIGHT,
                "smearing": 0,
            },
        },
        "pulses": {
            "readout_pulse": {
                "operation": "measurement",
                "length": READOUT_LEN,
                "waveforms": {"single": "zero_wf"},
                "integration_weights": {"cos": "cos_w", "sin": "sin_w"},
                "digital_marker": "ON",
            },
        },
        "waveforms": {"zero_wf": {"type": "constant", "sample": 0.0}},
        "digital_waveforms": {"ON": {"samples": [(1, 0)]}},
        "integration_weights": {
            "cos_w": {"cosine": [(1.0, READOUT_LEN)], "sine": [(0.0, READOUT_LEN)]},
            "sin_w": {"cosine": [(0.0, READOUT_LEN)], "sine": [(1.0, READOUT_LEN)]},
        },
    }

# =========================
# QUA program: averaged raw ADC trace of the chosen input
# =========================
with program() as prog:
    n = declare(int)
    adc_st = declare_stream(adc_trace=True)
    with for_(n, 0, n < N_AVG, n + 1):
        reset_if_phase(ELEM)
        measure("readout", ELEM, adc_stream=adc_st)
    with stream_processing():
        adc_in = adc_st.input1() if AI_PORT == 1 else adc_st.input2()
        adc_in.average().save("adc")

# =========================
# Measurement and calibration loop
# =========================
def _clamp(x, lim):
    return max(-lim, min(lim, float(x)))


def measure_dc_volts(qmm, cfg):
    """Run the program once; return the mean DC of the averaged trace in volts."""
    qm = qmm.open_qm(cfg)
    try:
        job = qm.execute(prog)
        res = job.result_handles
        res.wait_for_all_values()
        adc_v = u.raw2volts(res.get("adc").fetch_all())
        return float(np.mean(np.real(adc_v)))
    finally:
        qm.close()


def calibrate(label, qmm, make_cfg, start_offset, limit):
    """Slope probe, then bounded iterative cancellation. make_cfg(offset) -> config."""
    print(f"\n=== {label}: slope ===")
    lo, hi = _clamp(start_offset - SLOPE_STEP_V, limit), _clamp(start_offset + SLOPE_STEP_V, limit)
    dc1 = measure_dc_volts(qmm, make_cfg(lo))
    print(f"[Slope] offset {lo:+.6f} V -> {dc1 * 1e3:+.3f} mV")
    dc2 = measure_dc_volts(qmm, make_cfg(hi))
    print(f"[Slope] offset {hi:+.6f} V -> {dc2 * 1e3:+.3f} mV")
    slope = (dc2 - dc1) / (hi - lo) if hi != lo else 0.0
    if abs(slope) < 1e-6:
        print("[Slope] Warning: measured slope too small, using 1.0")
        slope = 1.0
    scale = abs(slope)
    coupling = np.sign(slope)
    print(f"[Slope] slope = {slope:+.4f} V/V ({'offset up -> DC down' if coupling < 0 else 'offset up -> DC up'})")

    print(f"\n=== {label}: iterations ===")
    offset = _clamp(start_offset, limit)
    converged = False
    last_dc_mV = float("nan")
    iters = 0
    for k in range(1, MAX_ITERS + 1):
        iters = k
        dc = measure_dc_volts(qmm, make_cfg(offset))
        last_dc_mV = dc * 1e3
        print(f"[Iter {k}] offset = {offset:+.6f} V, DC = {last_dc_mV:+.3f} mV")
        if abs(last_dc_mV) <= TOL_MV:
            print(f"[Done] |DC| <= {TOL_MV} mV at offset {offset:+.6f} V")
            converged = True
            break
        raw = dc / scale
        corr = raw if coupling < 0 else -raw
        corr = float(np.sign(corr) * min(abs(corr), MAX_STEP_V))
        offset = _clamp(offset + corr, limit)
        print(f"[Iter {k}] apply {corr:+.6f} V -> new offset {offset:+.6f} V")
    if not converged:
        print(f"[Warn] {label} did not converge in {MAX_ITERS} iterations")
    return {"offset": offset, "converged": converged, "iters": iters, "last_dc_mV": last_dc_mV}


qmm = QuantumMachinesManager(host=QOP_IP, port=QOP_PORT, cluster_name=CLUSTER_NAME)
ai_result = None
ao_result = None
final_ai = AI_OFFSET
final_ao = AO_OFFSET_START

#%%
# =========================
# Step 1: INPUT offset (nothing connected to the analog input)
# =========================
if RUN_INPUT_CAL:
    print(f"\n*** Step 1: disconnect everything from {CON} FEM {FEM} AI {AI_PORT} ***")
    ai_result = calibrate(
        "INPUT OFFSET",
        qmm,
        lambda off: build_config(AO_OFFSET_START, off),
        AI_OFFSET_START,
        AI_LIMIT_V,
    )
    final_ai = ai_result["offset"]
else:
    print(f"[Skip] input calibration; using AI_OFFSET = {AI_OFFSET:+.6f} V")

#%%
# =========================
# Pause: connect the loopback cable
# =========================
if RUN_OUTPUT_CAL:
    msg = f"*** Connect a cable from {CON} FEM {FEM} AO {AO_PORT} to AI {AI_PORT} ***"
    print("\n" + msg)
    if PAUSE_FOR_CABLE:
        input("Press Enter when the loopback cable is connected...")

#%%
# =========================
# Step 2: OUTPUT offset (loopback AO -> AI)
# =========================
if RUN_OUTPUT_CAL:
    ao_result = calibrate(
        "OUTPUT OFFSET",
        qmm,
        lambda off: build_config(off, final_ai),
        AO_OFFSET_START,
        AO_LIMIT_V,
    )
    final_ao = ao_result["offset"]

#%%
# =========================
# Summary + lf_offsets.csv
# =========================
ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
in_conv = None if ai_result is None else ai_result["converged"]
out_conv = None if ao_result is None else ao_result["converged"]
print("\n" + "=" * 70)
print(" LF-FEM DC OFFSET CALIBRATION SUMMARY")
print("=" * 70)
print(f"{ts}  {CON} FEM {FEM}  mode={OUTPUT_MODE}  gain_db={AI_GAIN_DB}")
print(f"  AO {AO_PORT}: offset = {final_ao:+.6f} V   converged = {out_conv}")
print(f"  AI {AI_PORT}: offset = {final_ai:+.6f} V   converged = {in_conv}")
print("Copy-paste:")
print(f'  ("{CON}", {FEM}) analog_outputs["{AO_PORT}"]["offset"] = {final_ao:.6f}  # {OUTPUT_MODE}')
print(f'  ("{CON}", {FEM}) analog_inputs["{AI_PORT}"]["offset"] = {final_ai:.6f}  # gain_db={AI_GAIN_DB}')
print("=" * 70)

try:
    out_dir = Path(__file__).resolve().parent
except NameError:
    out_dir = Path.cwd()
csv_path = out_dir / "lf_offsets.csv"
fields = ["timestamp", "con", "fem", "ao_port", "ao_offset_V", "ai_port", "ai_offset_V",
          "output_mode", "ai_gain_db", "input_converged", "output_converged"]
new_file = not csv_path.exists()
with csv_path.open("a", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    if new_file:
        w.writerow(fields)
    w.writerow([ts, CON, FEM, AO_PORT, f"{final_ao:.6f}", AI_PORT, f"{final_ai:.6f}",
                OUTPUT_MODE, AI_GAIN_DB, in_conv, out_conv])
print(f"[Saved] {csv_path}")
qmm.close()
'''


def render_script(params: dict | None = None) -> str:
    """Return the calibration script text for `params` (see DEFAULTS); raise ValueError on bad input."""
    v = _validate(params)
    return _HEADER + "\n" + _settings_block(v) + _BODY
