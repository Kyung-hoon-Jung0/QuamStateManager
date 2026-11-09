"""Pure LF-FEM loopback configuration and offset cancellation.

The LF-FEM input is inverted: the measured slope sign handles this, and the
returned offset must be written as is (vendor-confirmed). Algorithm reference:
f2ed387c-DC_offset_calibration.py. No extra sign inversion is required.
"""

from copy import deepcopy
import math


def mode_limit(output_mode):
    """Maximum absolute offset in volts for an LF-FEM output mode."""
    return {"direct": 0.5, "amplified": 2.5}[output_mode]


def _entry(mapping, number):
    """Accept both native config integer keys and JSON string keys."""
    return mapping[number] if number in mapping else mapping[str(number)]


def build_loopback_config(controllers, con, fem, ao, ai, ao_offset, ai_offset, output_mode):
    """Copy controllers, updating only the selected LF-FEM AO/AI entries.

    Pulse and weights reproduce 08995022-configuration_with_lf_fem_single.py,
    with 5000 ns integration weight durations. The pulse operation is
    "measurement" (the reference uses "control"; measure() needs a measurement
    pulse, and the exported script uses the same).
    """
    mode_limit(output_mode)
    copied = deepcopy(controllers)
    block = _entry(copied[con]["fems"], int(fem))
    if block.get("type") != "LF":
        raise ValueError("Loopback calibration requires an LF FEM")
    if int(ai) not in (1, 2):
        raise ValueError("LF-FEM analog input must be 1 or 2")
    _entry(block["analog_outputs"], int(ao)).update(offset=float(ao_offset), output_mode=output_mode)
    _entry(block["analog_inputs"], int(ai)).update(offset=float(ai_offset), gain_db=0, sampling_rate=1e9)
    names = {
        "cos": "cosine_weights", "sin": "sine_weights", "minus_sin": "minus_sine_weights",
        "rotated_cos": "rotated_cosine_weights", "rotated_sin": "rotated_sine_weights",
        "rotated_minus_sin": "rotated_minus_sine_weights", "opt_cos": "opt_cosine_weights",
        "opt_sin": "opt_sine_weights", "opt_minus_sin": "opt_minus_sine_weights",
    }
    weights = {}
    for name, target in names.items():
        cosine, sine = (1.0, 0.0) if name.endswith("cos") else (0.0, -1.0 if "minus" in name else 1.0)
        if name == "rotated_sin":
            cosine = -0.0
        weights[target] = {"cosine": [(cosine, 5000)], "sine": [(sine, 5000)]}
    return {
        "version": 1, "controllers": copied,
        "elements": {"sm_loopback": {
            "singleInput": {"port": (con, int(fem), int(ao))}, "intermediate_frequency": 0.0,
            "operations": {"readout": "readout_pulse"}, "outputs": {"out1": (con, int(fem), int(ai))},
            "time_of_flight": 28, "smearing": 0,
        }},
        "pulses": {"readout_pulse": {
            "operation": "measurement", "length": 1728, "waveforms": {"single": "readout_wf"},
            "integration_weights": names, "digital_marker": "ON",
        }},
        "waveforms": {"readout_wf": {"type": "constant", "sample": 0.0}},
        "digital_waveforms": {"ON": {"samples": [(1, 0)]}}, "integration_weights": weights,
    }


def calibrate(measure_dc, initial_offset, limit_v, tol_mV=0.5, max_iters=6,
              max_step_v=0.1, slope_step_v=0.05, should_stop=lambda: False, log=print):
    """Cancel DC using the reference slope, sign coupling and bounded correction.

    ``measure_dc(offset)`` receives a clamped offset and returns volts. History
    contains iterative measurements (offset V, DC V/mV), excluding slope probes.
    ``iters`` counts these measurements. A final correction may be unmeasured
    when iterations are exhausted, exactly as in the reference. Stop is checked
    before every measurement; stopping returns an unconverged result.
    """
    if not all(math.isfinite(x) for x in (initial_offset, limit_v, tol_mV, max_step_v, slope_step_v)):
        raise ValueError("Calibration parameters must be finite")
    if limit_v <= 0 or tol_mV < 0 or max_step_v <= 0 or slope_step_v <= 0 or max_iters < 1:
        raise ValueError("Invalid calibration bounds")
    clamp = lambda value: max(-limit_v, min(limit_v, value))
    offset = clamp(float(initial_offset))
    result = {"converged": False, "final_offset": offset, "iters": 0, "slope": None, "history": []}

    def measure(value):
        dc = float(measure_dc(value))
        if not math.isfinite(dc):
            raise ValueError("Measured DC must be finite")
        return dc

    probes = []
    for candidate in (initial_offset - slope_step_v, initial_offset + slope_step_v):
        if should_stop():
            return result
        value = clamp(candidate)
        dc = measure(value)
        probes.append(dc)
        log(f"Slope: offset {value:+.6f} V -> DC {dc * 1e3:+.3f} mV")
    # Retain the reference denominator even when a probe is clamped.
    slope = (probes[1] - probes[0]) / (2 * slope_step_v)
    if abs(slope) < 1e-6:
        log("Measured slope too small; defaulting to 1.0")
        slope = 1.0
    result["slope"] = slope
    for iteration in range(1, max_iters + 1):
        if should_stop():
            break
        dc = measure(offset)
        result["iters"] = iteration
        result["history"].append({"iteration": iteration, "offset": offset, "dc_v": dc, "dc_mV": dc * 1e3})
        log(f"Iter {iteration}: offset {offset:+.6f} V -> DC {dc * 1e3:+.3f} mV")
        if abs(dc * 1e3) <= tol_mV:
            result["converged"] = True
            break
        raw = dc / abs(slope)
        correction = raw if slope < 0 else -raw
        correction = math.copysign(min(abs(correction), max_step_v), correction)
        offset = clamp(offset + correction)
    result["final_offset"] = offset
    return result
