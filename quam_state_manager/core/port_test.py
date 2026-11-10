"""Port test: template QUA scripts that play on one element or one port.

The question answered: "does this element really play on the port, at the
frequency and amplitude the chip says?" SM never writes a test config by
hand per chip. Two bases:

* ``chip``: the chip's own ``generate_config()`` output, the element entry
  verbatim (same port, same IF, same port settings). It proves the CHIP's
  mapping.
* ``raw``: a minimal config that holds only the element's port (its port
  settings copied from the chip config) and a fresh element on it. It proves
  the hardware and the cable, independent of the chip's elements.

Three tests: ``cw`` (a continuous tone until Stop), ``pulse`` (one operation
repeated with a period, with a digital marker on a DIG output as the scope
trigger) and ``loopback`` (play the readout and acquire the raw ADC trace;
needs a cable from the output to the input).

This module is pure: it builds dicts and script text. Nothing here imports
``qm``; the script runs in the chip's env (``generator/run_port_test.py``) or
on the customer's own machine after a download.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import pprint
import re
from datetime import datetime, timezone
from typing import Any, Optional

#: the largest |amplitude| (fraction of full scale) a test may play unless
#: the user raises it on the page; a value above it is scaled down, never sent
AMP_CAP_DEFAULT = 0.5
AMP_CAP_MAX = 1.0
CW_PULSE_NS = 1000
DEFAULT_PERIOD_NS = 10_000
DEFAULT_SHOTS = 100
MARKER = "port_test_on"
TESTS = ("cw", "pulse", "loopback")
BASES = ("chip", "raw")


def con_name(value) -> str:
    """Wizard controllers store ``con`` as an int; accept ``"con1"`` too."""
    text = str(value).strip()
    if text.startswith("con"):
        text = text[3:]
    return "con" + str(int(text))


#: assumed bound for a saved LF-FEM analog input offset (V)
AI_OFFSET_LIMIT = 0.5


def input_key(con, slot, ai) -> str:
    """``spec.port_offsets`` key of an LF-FEM analog input: ``con1/5/ai1``."""
    return f"{con_name(con)}/{int(slot)}/ai{int(ai)}"


def input_pin(pin) -> tuple | None:
    """(con, slot, ai) for an input key, else None (an output key)."""
    m = re.fullmatch(r"(con\d+|\d+)/(\d+)/ai([12])", str(pin))
    if not m:
        return None
    return con_name(m.group(1)), int(m.group(2)), int(m.group(3))


def controllers_from_spec(spec: dict) -> dict:
    """Element-free OPX1000 allocation from wizard step 3, with the saved
    port offsets (``spec.port_offsets``) applied to their ports."""
    controllers = {}
    for ctrl in spec.get("instruments", {}).get("controllers", []):
        con = con_name(ctrl["con"])
        fems = controllers.setdefault(con, {"fems": {}})["fems"]
        for fem in ctrl.get("fems", []):
            slot = str(int(fem["slot"]))
            if fem.get("fem") not in ("lf", "mw") or not 1 <= int(slot) <= 8:
                raise PortTestError("Choose an LF or MW FEM in slots 1..8.")
            entry = fems[slot] = {"type": fem["fem"].upper()}
            if entry["type"] == "LF":
                entry["analog_outputs"] = {
                    str(p): {"offset": 0.0, "output_mode": "direct",
                             "sampling_rate": 1e9, "upsampling_mode": "pulse"}
                    for p in range(1, 9)}
                entry["analog_inputs"] = {
                    str(p): {"offset": 0.0, "gain_db": 0, "sampling_rate": 1e9} for p in (1, 2)}
    for pin, saved in (spec.get("port_offsets") or {}).items():
        ai = input_pin(pin)
        if ai is not None:
            try:
                inp = controllers[ai[0]]["fems"][str(ai[1])]["analog_inputs"][str(ai[2])]
                value = float(saved.get("offset") or 0.0)
            except (KeyError, ValueError, TypeError, AttributeError):
                continue
            if math.isfinite(value) and abs(value) <= AI_OFFSET_LIMIT and int(saved.get("gain_db") or 0) == 0:
                inp["offset"] = value
            continue
        try:
            con, slot, port = pin.split("/")
            out = controllers[con_name(con)]["fems"][str(int(slot))]["analog_outputs"][str(int(port))]
        except (KeyError, ValueError, AttributeError):
            continue  # a port the allocation no longer has
        mode = saved.get("output_mode", "direct")
        value = float(saved.get("offset") or 0.0)
        if mode in ("direct", "amplified") and math.isfinite(value) and abs(value) <= (2.5 if mode == "amplified" else 0.5):
            out.update(offset=value, output_mode=mode)
    return controllers


def build_wizard_dc(spec: dict, port: list, offset: float, output_mode="direct"):
    cfg = {"controllers": controllers_from_spec(spec)}
    info = dc_port(cfg, port)
    con, slot, number = info["port"]
    cfg["controllers"][con]["fems"][str(slot)]["analog_outputs"][str(number)]["offset"] = 0.0
    cfg["controllers"][con]["fems"][str(slot)]["analog_outputs"][str(number)]["output_mode"] = output_mode
    return build_dc(cfg, port, offset)


def dc_port(config: dict, port: list) -> dict:
    """Validate a physical LF-FEM output, including an unused output 1..8."""
    try:
        con, slot, number = str(port[0]), int(port[1]), int(port[2])
        fems = config["controllers"][con]["fems"]
        fem = fems.get(str(slot), fems.get(slot))
        if len(port) != 3 or not 1 <= number <= 8 or not fem or fem.get("type") != "LF":
            raise ValueError
    except (KeyError, IndexError, TypeError, ValueError):
        raise PortTestError("Choose an LF-FEM analog output (1..8).") from None
    settings = _port_entry(config, [con, slot, number], "analog_outputs")
    mode = settings.get("output_mode", "direct")
    if mode not in ("direct", "amplified"):
        raise PortTestError(f"Unsupported output mode {mode!r}.")
    return {"port": [con, slot, number], "output_mode": mode,
            "offset": settings.get("offset") or 0.0, "limit": 2.5 if mode == "amplified" else 0.5}


def build_dc(config: dict, port: list, offset: float) -> tuple[dict, dict]:
    """Keep generated controllers verbatim; override only the selected offset."""
    info = dc_port(config, port)
    try:
        offset = float(offset)
    except (TypeError, ValueError):
        raise PortTestError("Offset must be a number in volts.") from None
    if not math.isfinite(offset) or abs(offset) > info["limit"]:
        raise PortTestError(f"{info['output_mode'].capitalize()} mode requires |offset| <= {info['limit']:g} V.")
    cfg = {"version": 1, "controllers": copy.deepcopy(config["controllers"])}
    con, slot, number = info["port"]
    fems = cfg["controllers"][con]["fems"]
    fem = fems.get(str(slot), fems.get(slot))
    outputs = fem.setdefault("analog_outputs", {})
    key = number if number in outputs else str(number)
    if key not in outputs:
        outputs[key] = {"sampling_rate": 1e9, "output_mode": "direct", "upsampling_mode": "pulse"}
    outputs[key]["offset"] = offset
    cfg.update(elements={"sm_port_play": {"singleInput": {"port": (con, slot, number)},
                                         "intermediate_frequency": 0,
                                         "operations": {"dc": "sm_port_play.pulse"}}},
               pulses={"sm_port_play.pulse": {"operation": "control", "length": CW_PULSE_NS,
                                              "waveforms": {"single": "sm_port_play.zero"}}},
               waveforms={"sm_port_play.zero": {"type": "constant", "sample": 0.0}})
    # Reuse the existing script, watchdog, simulator and halt/close machinery.
    plan = dict(test="dc", basis="port", element="sm_port_play", source_element="sm_port_play",
                operation="dc", amp_scale=1.0, expected_peak=0.0, expected_dbm=None,
                kind="lf", port=info["port"], in_port=None, marker_port=None,
                period_ns=DEFAULT_PERIOD_NS, shots=1, lo=None, **{"if": 0}, rf=None,
                full_scale_power_dbm=None, notes=[], pulse_ns=CW_PULSE_NS,
                config_fingerprint=config_fingerprint(cfg), config_name=f"port-play:{con}:{slot}:{number}",
                output_mode=info["output_mode"], offset=offset)
    return cfg, plan


class PortTestError(ValueError):
    """A request this config cannot answer (the message is for the user)."""


# ---- MW-FEM CW play (wizard step 3) ----------------------------------------
# Limits transcribed from the QM docs, docs/docs/docs/Guides/opx1000_fems.md:
#   "### Bands": band 1 = 50 MHz - 5.5 GHz, band 2 = 4.5 - 7.5 GHz, band 3 = 6.5 - 10.5 GHz.
#   "### Upconverters and Downconverters": settable upconverter frequency per band
#     band 1: 0 - 5.5 GHz, band 2: 4.5 - 7.5 GHz, band 3: 6.5 - 11 GHz; outside the band's
#     specified range the output "will not meet the performance specification".
#   "### Bands": each DUC carries a sub-band of about 800 MHz around its centre (+-400 MHz).
#   "### Output Power": full_scale_power_dbm between -11 and 18 dBm, 1 dB granularity
#     (QOP >= 3.7; earlier QOP 3.x: upper limit 16 dBm; above 16 dBm not guaranteed).
#   "### Output Power": full scale is a waveform of {-1, 1}; P = FSP + 20*log10|amp|.
# IF policy: |IF| > 400 MHz is a warning (outside the DUC sub-band), > 500 MHz is refused
# (the 1 GSa/s quadrature rate).
MW_BAND_SPEC_HZ = {1: (50e6, 5.5e9), 2: (4.5e9, 7.5e9), 3: (6.5e9, 10.5e9)}
MW_LO_SETTABLE_HZ = {1: (0.0, 5.5e9), 2: (4.5e9, 7.5e9), 3: (6.5e9, 11e9)}
MW_FSP_RANGE_DBM = (-11, 18)
MW_FSP_GUARANTEED_DBM = 16
MW_IF_WARN_HZ = 400e6
MW_IF_MAX_HZ = 500e6
MW_AMP_MAX = 1.0  # exclusive: |amp| < 1
MW_DEFAULT_IF_HZ = 50e6


def mw_params(band, lo_hz, if_hz, fsp, amp) -> dict:
    """Validated MW play parameters, plus advisory notes. Raises PortTestError."""
    if isinstance(band, bool) or str(band).strip() not in ("1", "2", "3"):
        raise PortTestError("Band must be 1, 2 or 3.")
    band_i = int(str(band).strip())
    try:
        lo, iff, a = float(lo_hz), float(if_hz), float(amp)
    except (TypeError, ValueError):
        raise PortTestError("LO, IF and amplitude must be numbers.") from None
    if not all(math.isfinite(v) for v in (lo, iff, a)):
        raise PortTestError("LO, IF and amplitude must be finite numbers.")
    try:
        fsp_f = float(fsp)
    except (TypeError, ValueError):
        raise PortTestError("Full-scale power must be an integer in dBm.") from None
    if not math.isfinite(fsp_f) or fsp_f != int(fsp_f) or isinstance(fsp, bool):
        raise PortTestError("Full-scale power must be an integer in dBm (1 dB steps).")
    fsp_i = int(fsp_f)
    lo_min, lo_max = MW_LO_SETTABLE_HZ[band_i]
    if not lo_min <= lo <= lo_max:
        raise PortTestError(f"Band {band_i} upconverter frequency must be {lo_min / 1e9:g}..{lo_max / 1e9:g} GHz.")
    if abs(iff) > MW_IF_MAX_HZ:
        raise PortTestError(f"|IF| must be <= {MW_IF_MAX_HZ / 1e6:g} MHz.")
    if not MW_FSP_RANGE_DBM[0] <= fsp_i <= MW_FSP_RANGE_DBM[1]:
        raise PortTestError(f"Full-scale power must be {MW_FSP_RANGE_DBM[0]}..{MW_FSP_RANGE_DBM[1]} dBm.")
    if not abs(a) < MW_AMP_MAX:
        raise PortTestError("Amplitude must satisfy |amp| < 1 (fraction of full scale).")
    rf = lo + iff
    notes = []
    spec_lo, spec_hi = MW_BAND_SPEC_HZ[band_i]
    if not spec_lo <= lo <= spec_hi or not spec_lo <= rf <= spec_hi:
        notes.append(f"LO or RF is outside band {band_i}'s specified range "
                     f"{spec_lo / 1e9:g}..{spec_hi / 1e9:g} GHz: performance is not specified.")
    if abs(iff) > MW_IF_WARN_HZ:
        notes.append("|IF| is above 400 MHz, outside the upconverter's ~800 MHz sub-band.")
    if fsp_i > MW_FSP_GUARANTEED_DBM:
        notes.append("Full-scale power above 16 dBm needs QOP >= 3.7 and is not guaranteed over the whole band.")
    return {"band": band_i, "lo": lo, "if": iff, "rf": rf, "full_scale_power_dbm": fsp_i,
            "amp": a, "dbm": dbm(fsp_i, a), "notes": notes}


def mw_port(config: dict, port: list) -> list:
    """Validate a physical MW-FEM output 1..8."""
    try:
        con, slot, number = str(port[0]), int(port[1]), int(port[2])
        fems = config["controllers"][con]["fems"]
        fem = fems.get(str(slot), fems.get(slot))
        if len(port) != 3 or not 1 <= number <= 8 or not fem or fem.get("type") != "MW":
            raise ValueError
    except (KeyError, IndexError, TypeError, ValueError):
        raise PortTestError("Choose an MW-FEM analog output (1..8).") from None
    return [con, slot, number]


def build_wizard_mw(spec: dict, port: list, band, lo_hz, if_hz, fsp, amp) -> tuple[dict, dict]:
    """CW tone on one MW-FEM output of the wizard allocation.

    Only the played MW port is declared; every other port keeps what
    ``controllers_from_spec`` gives it (LF ports at their saved offsets,
    MW ports undeclared)."""
    cfg = {"version": 1, "controllers": controllers_from_spec(spec)}
    if len(port) == 3:
        port = [con_name(port[0]), port[1], port[2]]
    con, slot, number = mw_port(cfg, port)
    p = mw_params(band, lo_hz, if_hz, fsp, amp)
    fem = cfg["controllers"][con]["fems"][str(slot)]
    fem["analog_outputs"] = {str(number): {
        "band": p["band"], "upconverter_frequency": p["lo"],
        "full_scale_power_dbm": p["full_scale_power_dbm"], "sampling_rate": 1e9}}
    cfg.update(elements={"sm_port_play": {"MWInput": {"port": (con, slot, number), "upconverter": 1},
                                         "intermediate_frequency": p["if"],
                                         "operations": {"cw": "sm_port_play.pulse"}}},
               pulses={"sm_port_play.pulse": {"operation": "control", "length": CW_PULSE_NS,
                                              "waveforms": {"I": "sm_port_play.amp", "Q": "sm_port_play.zero"}}},
               waveforms={"sm_port_play.amp": {"type": "constant", "sample": p["amp"]},
                          "sm_port_play.zero": {"type": "constant", "sample": 0.0}})
    plan = dict(test="cw", basis="port", element="sm_port_play", source_element="sm_port_play",
                operation="cw", amp_scale=1.0, expected_peak=abs(p["amp"]), expected_dbm=p["dbm"],
                kind="mw", port=[con, slot, number], in_port=None, marker_port=None,
                period_ns=DEFAULT_PERIOD_NS, shots=1, lo=p["lo"], **{"if": p["if"]}, rf=p["rf"],
                full_scale_power_dbm=p["full_scale_power_dbm"], band=p["band"], notes=list(p["notes"]),
                pulse_ns=CW_PULSE_NS, config_fingerprint=config_fingerprint(cfg),
                config_name=f"port-play-mw:{con}:{slot}:{number}")
    return cfg, plan


# ---- several outputs in one QM (wizard step 3) -----------------------------
MAX_PLAY_PORTS = 3
#: MW-FEM output pairs that share a band constraint. QM docs Guides/opx1000_fems.md
#: "### Bands": "Coupled ports must be in the same band, or in bands `1` and `3`."
#: (Out 1 & In 1 and Out 8 & In 2 involve inputs, which a play never declares.)
MW_COUPLED_OUTPUTS = ((2, 3), (4, 5), (6, 7))
#: QM docs Guides/opx1000_fems.md "### Pulse Generation Timing Variation".
MIXED_TIMING_NOTE = ("MW-FEM outputs start about 155 ns (bands 1, 3) or 175 ns (band 2) after "
                     "LF-FEM outputs; this play does not compensate it.")


def _coupled_bands_ok(a: int, b: int) -> bool:
    return a == b or {a, b} == {1, 3}


def _single(spec: dict, entry: dict) -> tuple[dict, dict]:
    """One entry through the one-port builders (their validation and shape)."""
    if not isinstance(entry, dict):
        raise PortTestError("Each port entry must be an object.")
    port = list(entry.get("port") or [])
    if entry.get("kind") == "mw":
        return build_wizard_mw(spec, port, entry.get("band"), entry.get("lo_hz"), entry.get("if_hz"),
                               entry.get("fsp"), entry.get("amp"))
    return build_wizard_dc(spec, port, entry.get("offset"), entry.get("output_mode", "direct"))


def build_wizard_multi(spec: dict, entries: list) -> tuple[dict, dict]:
    """Up to ``MAX_PLAY_PORTS`` outputs in one config and one program.

    One entry returns exactly what the one-port builder returns. Port 1 keeps
    element ``sm_port_play``; port i (2..3) is ``sm_port_play_<i>``."""
    entries = list(entries or [])
    if not entries:
        raise PortTestError("Choose at least one output port.")
    if len(entries) > MAX_PLAY_PORTS:
        raise PortTestError(f"At most {MAX_PLAY_PORTS} ports can play together.")
    if len(entries) == 1:
        return _single(spec, entries[0])
    cfg = {"version": 1, "controllers": controllers_from_spec(spec),
           "elements": {}, "pulses": {}, "waveforms": {}}
    singles, seen, mw_bands, plays, notes = [], {}, {}, [], []
    for i, entry in enumerate(entries, start=1):
        try:
            scfg, splan = _single(spec, entry)
        except PortTestError as exc:
            raise PortTestError(f"Port {i}: {exc}") from None
        con, slot, number = splan["port"]
        key = (str(con), int(slot), int(number))
        label = f"{con}/{slot}/{number}"
        if key in seen:
            raise PortTestError(f"Port {label} is chosen twice.")
        seen[key] = i
        if splan["kind"] == "mw":
            mw_bands[key] = splan["band"]
        singles.append((key, scfg, splan))
    for (con, slot, a), band_a in mw_bands.items():
        for x, y in MW_COUPLED_OUTPUTS:
            if a == x and (con, slot, y) in mw_bands and not _coupled_bands_ok(band_a, mw_bands[(con, slot, y)]):
                raise PortTestError(
                    f"{con}/{slot}/{x} (band {band_a}) and {con}/{slot}/{y} (band {mw_bands[(con, slot, y)]}) "
                    "are coupled MW outputs: they need the same band, or bands 1 and 3.")
    played_mw = {}
    for i, (key, scfg, splan) in enumerate(singles, start=1):
        con, slot, number = key
        name = "sm_port_play" if i == 1 else f"sm_port_play_{i}"
        out = scfg["controllers"][con]["fems"][str(slot)]["analog_outputs"][str(number)]
        if splan["kind"] == "mw":
            played_mw.setdefault((con, slot), {})[str(number)] = copy.deepcopy(out)
        else:
            cfg["controllers"][con]["fems"][str(slot)]["analog_outputs"][str(number)] = copy.deepcopy(out)
        el = copy.deepcopy(scfg["elements"]["sm_port_play"])
        op = splan["operation"]
        el["operations"] = {op: f"{name}.pulse"}
        pulse = copy.deepcopy(scfg["pulses"]["sm_port_play.pulse"])
        pulse["waveforms"] = {k: v.replace("sm_port_play.", f"{name}.", 1) for k, v in pulse["waveforms"].items()}
        cfg["elements"][name] = el
        cfg["pulses"][f"{name}.pulse"] = pulse
        for wname, wf in scfg["waveforms"].items():
            cfg["waveforms"][wname.replace("sm_port_play.", f"{name}.", 1)] = copy.deepcopy(wf)
        play = {"element": name, "operation": op, "kind": splan["kind"], "port": [con, slot, number]}
        for k in ("offset", "output_mode", "band", "lo", "if", "rf", "full_scale_power_dbm", "expected_dbm"):
            if splan.get(k) is not None:
                play[k] = splan[k]
        plays.append(play)
        notes.extend(f"{con}/{slot}/{number}: {n}" for n in splan.get("notes") or [])
    for (con, slot), outputs in played_mw.items():
        # Only the played MW ports are declared, as in a one-port MW play.
        cfg["controllers"][con]["fems"][str(slot)]["analog_outputs"] = outputs
    kinds = {p["kind"] for p in plays}
    if kinds == {"lf", "mw"}:
        notes.append(MIXED_TIMING_NOTE)
    plan = dict(singles[0][2])
    plan.update(test="dc" if kinds == {"lf"} else "cw", plays=plays, notes=notes,
                config_name="port-play-multi:" + ",".join(":".join(map(str, p["port"])) for p in plays),
                config_fingerprint=config_fingerprint(cfg))
    return cfg, plan



def dbm(fsp: Optional[float], amp: Optional[float]) -> Optional[float]:
    """Output power of a MW port: P = FSP + 20*log10|amp| (docs/109)."""
    if fsp is None or amp is None or not amp:
        return None
    try:
        return float(fsp) + 20.0 * math.log10(abs(float(amp)))
    except (TypeError, ValueError):
        return None


def _canon(value):
    if isinstance(value, dict):
        return {str(k): _canon(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_canon(v) for v in value]
    return value


def config_fingerprint(config: dict) -> str:
    """Stable SHA-256 of a config dict for "same test config" checks."""
    raw = json.dumps(_canon(config), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _out_port(el: dict) -> tuple[Optional[str], Optional[list]]:
    if isinstance(el.get("MWInput"), dict):
        return "mw", list(el["MWInput"].get("port") or [])
    if isinstance(el.get("singleInput"), dict):
        return "lf", list(el["singleInput"].get("port") or [])
    if isinstance(el.get("mixInputs"), dict):
        return "iq", list(el["mixInputs"].get("I") or [])
    return None, None


def _in_port(el: dict) -> Optional[list]:
    if isinstance(el.get("MWOutput"), dict):
        return list(el["MWOutput"].get("port") or [])
    outs = el.get("outputs")
    if isinstance(outs, dict) and outs:
        return list(next(iter(outs.values())))
    return None


def _port_entry(config: dict, port: list, io: str) -> dict:
    """The controller's settings dict for *port* (``io`` = analog_outputs/inputs)."""
    if not port:
        return {}
    con = (config.get("controllers") or {}).get(str(port[0])) or {}
    if len(port) == 3:
        fem = (con.get("fems") or {}).get(str(port[1])) or (con.get("fems") or {}).get(port[1]) or {}
        group = fem.get(io) or {}
    else:
        group = con.get(io) or {}
    return group.get(str(port[-1])) or group.get(port[-1]) or {}


def _fem_type(config: dict, port: list) -> Optional[str]:
    if len(port) != 3:
        return None
    con = (config.get("controllers") or {}).get(str(port[0])) or {}
    fem = (con.get("fems") or {}).get(str(port[1])) or {}
    return fem.get("type")


def _waveform_peak(config: dict, name: Optional[str]) -> Optional[float]:
    wf = (config.get("waveforms") or {}).get(name) if name else None
    if not isinstance(wf, dict):
        return None
    if wf.get("type") == "constant":
        return abs(float(wf.get("sample") or 0.0))
    s = wf.get("samples") or []
    try:
        return max((abs(float(x)) for x in s), default=0.0)
    except (TypeError, ValueError):
        return None


def _waveform_samples(config: dict, name: Optional[str]) -> Optional[list]:
    wf = (config.get("waveforms") or {}).get(name) if name else None
    if not isinstance(wf, dict):
        return None
    try:
        if wf.get("type") == "constant":
            return [float(wf.get("sample") or 0.0)]
        return [float(x) for x in (wf.get("samples") or [])]
    except (TypeError, ValueError):
        return None


def pulse_peak(config: dict, pulse_name: str, complex_iq: bool = False) -> Optional[float]:
    """Peak |amplitude| of a pulse: the larger of I and Q (an IQ mixer's two
    DACs, or the single wf), or with ``complex_iq`` (an MW-FEM output, whose
    amplitude is |I + jQ|) the peak magnitude of I + jQ."""
    p = (config.get("pulses") or {}).get(pulse_name) or {}
    wfs = p.get("waveforms") or {}
    if complex_iq and "I" in wfs and "Q" in wfs:
        i, q = _waveform_samples(config, wfs["I"]), _waveform_samples(config, wfs["Q"])
        if i is None or q is None:
            return None
        if not i or not q:
            return max((abs(x) for x in i + q), default=0.0)
        if len(i) == 1 or len(q) == 1 or len(i) == len(q):
            n = max(len(i), len(q))
            return max(math.hypot(i[k if len(i) > 1 else 0], q[k if len(q) > 1 else 0]) for k in range(n))
        # Unequal lengths: the bound that can never be exceeded.
        return math.hypot(max(abs(x) for x in i), max(abs(x) for x in q))
    peaks = [_waveform_peak(config, w) for w in wfs.values()]
    peaks = [x for x in peaks if x is not None]
    return max(peaks) if peaks else None


def element_info(config: dict, element: str) -> dict:
    """What the generated config says about *element*: port, frequencies, power."""
    el = (config.get("elements") or {}).get(element)
    if not isinstance(el, dict):
        raise PortTestError(f"The generated config has no element named {element!r}.")
    kind, port = _out_port(el)
    if kind is None:
        raise PortTestError(f"{element!r} has no analog output in the generated config.")
    pe = _port_entry(config, port, "analog_outputs")
    fsp = pe.get("full_scale_power_dbm")
    lo = pe.get("upconverter_frequency")
    if lo is None and isinstance(pe.get("upconverters"), dict):
        up = (el.get("MWInput") or {}).get("upconverter", 1)
        lo = (pe["upconverters"].get(str(up)) or pe["upconverters"].get(up) or {}).get("frequency")
    if kind == "iq":
        lo = (el.get("mixInputs") or {}).get("lo_frequency")
    if_ = el.get("intermediate_frequency")
    ops = []
    for op, pulse in sorted((el.get("operations") or {}).items()):
        p = (config.get("pulses") or {}).get(pulse) or {}
        peak = pulse_peak(config, pulse, complex_iq=kind == "mw")
        ops.append({"name": op, "pulse": pulse, "length": p.get("length"),
                    "measurement": p.get("operation") == "measurement",
                    "peak": peak, "dbm": dbm(fsp, peak) if kind == "mw" else None})
    return {
        "element": element, "kind": kind, "port": port,
        "fem_type": _fem_type(config, port),
        "full_scale_power_dbm": fsp, "lo": lo, "if": if_,
        "rf": (lo + if_) if isinstance(lo, (int, float)) and isinstance(if_, (int, float)) else None,
        "in_port": _in_port(el), "time_of_flight": el.get("time_of_flight"),
        "operations": ops,
        "has_measurement": any(o["measurement"] for o in ops) and _in_port(el) is not None,
    }


def elements_on_port(config: dict, con: str, slot, port, io: str = "output") -> list[str]:
    """Element names whose output (or, for io=input, input) is this port."""
    want = [str(con), str(slot), str(port)] if slot not in (None, "") else [str(con), str(port)]
    out = []
    for name, el in sorted((config.get("elements") or {}).items()):
        if not isinstance(el, dict):
            continue
        p = _in_port(el) if io == "input" else _out_port(el)[1]
        if p and [str(x) for x in p] == want:
            out.append(name)
    return out


def digital_ports(config: dict, port: list) -> list[list]:
    """Candidate DIG outputs for the scope marker: the element's own controller /
    FEM first (1..8). Purely an offer; the user picks."""
    if len(port) == 3:
        return [[port[0], port[1], n] for n in range(1, 9)]
    return [[port[0], n] for n in range(1, 11)]


def _add_digital_output(cfg: dict, dport: list) -> None:
    con = cfg["controllers"].setdefault(str(dport[0]), {})
    if len(dport) == 3:
        fem = con.setdefault("fems", {}).setdefault(str(dport[1]), {})
        group = fem.setdefault("digital_outputs", {})
    else:
        group = con.setdefault("digital_outputs", {})
    group.setdefault(str(dport[-1]), {})


def _copy_port(source: dict, target: dict, port: list, io: str) -> None:
    con = target["controllers"].setdefault(str(port[0]), {})
    original = source["controllers"].get(str(port[0]), {})
    if len(port) == 3:
        con = con.setdefault("fems", {}).setdefault(str(port[1]), {})
        original = (original.get("fems", {}).get(str(port[1])) or
                    original.get("fems", {}).get(port[1]) or {})
    # Keep hardware metadata, never unrelated port groups or their DC offsets.
    for key in ("type",):
        if key in original:
            con[key] = copy.deepcopy(original[key])
    con.setdefault(io, {})[str(port[-1])] = copy.deepcopy(_port_entry(source, port, io))


def build(config: dict, element: str, test: str, basis: str, *,
          operation: Optional[str] = None, amplitude: Optional[float] = None,
          amp_cap: float = AMP_CAP_DEFAULT, period_ns: int = DEFAULT_PERIOD_NS,
          marker_port: Optional[list] = None, shots: int = DEFAULT_SHOTS) -> tuple[dict, dict]:
    """Return ``(test_config, plan)`` for one test.

    ``plan`` carries everything the script and the page need: the element and
    operation to play, the amplitude scale applied by the cap (with the reason),
    the expected peak amplitude and power, and the ports to observe.
    """
    if test not in TESTS:
        raise PortTestError(f"Unknown test {test!r}.")
    if basis not in BASES:
        raise PortTestError(f"Unknown basis {basis!r}.")
    try:
        amp_cap = float(amp_cap)
    except (TypeError, ValueError):
        raise PortTestError("The amplitude cap must be a number.") from None
    if not 0 < amp_cap <= AMP_CAP_MAX:
        raise PortTestError(f"The amplitude cap must be in (0, {AMP_CAP_MAX}].")
    info = element_info(config, element)
    src = config["elements"][element]
    notes: list[str] = []

    if basis == "chip":
        el = copy.deepcopy(src)
        el["operations"] = {}
        name = element
        pulses, wfs, iws = {}, {}, {}
        cfg: dict[str, Any] = {"controllers": {}}
        # Never "octaves": it configures every RF output of the Octave (other
        # qubits' lines included), and no tested element routes through one.
        for k in ("mixers", "oscillators"):
            if config.get(k):
                cfg[k] = copy.deepcopy(config[k])
        notes.append(f"Chip basis: the element entry of {element} is the chip's generate_config() "
                     "output (same port, IF and selected port settings); its operation list holds only "
                     "the tested operation, a copy of the chip's own pulse.")
    else:
        name = "port_test"
        el = {k: copy.deepcopy(v) for k, v in src.items()
              if k in ("MWInput", "singleInput", "mixInputs", "intermediate_frequency")}
        if test == "loopback":
            for k in ("MWOutput", "outputs", "time_of_flight", "smearing"):
                if k in src:
                    el[k] = copy.deepcopy(src[k])
        el["operations"] = {}
        pulses, wfs, iws = {}, {}, {}
        cfg = {"controllers": {}}
        notes.append("Raw-port basis: a fresh element on the selected chip ports.")

    # An isolated QM must reserve and configure only the ports used by this test.
    el.pop("digitalInputs", None)
    if test != "loopback":
        for key in ("MWOutput", "outputs", "time_of_flight", "smearing"):
            el.pop(key, None)
    ports = [(info["port"], "analog_outputs")]
    if info["kind"] == "iq":
        ports.append((list(src["mixInputs"]["Q"]), "analog_outputs"))
        if config.get("mixers"):
            cfg["mixers"] = copy.deepcopy(config["mixers"])
    if test == "loopback":
        inputs = ([src["MWOutput"]["port"]] if "MWOutput" in src
                  else list((src.get("outputs") or {}).values()))
        ports.extend((list(p), "analog_inputs") for p in inputs)
    for port, io in ports:
        _copy_port(config, cfg, port, io)
    loopback = []
    if test == "loopback":
        outs = [p for p, io in ports if io == "analog_outputs"]
        ins = [p for p, io in ports if io == "analog_inputs"]
        for k, i_port in enumerate(ins):
            o_port = outs[min(k, len(outs) - 1)]
            # The simulator's out->in cable: one tuple per input it feeds.
            loopback.append([str(o_port[0])] + [int(x) for x in o_port[1:]]
                            + [str(i_port[0])] + [int(x) for x in i_port[1:]])
    offset = max((abs(float(_port_entry(config, p, io).get("offset", 0)))
                  for p, io in ports if io == "analog_outputs"), default=0.0)
    if not math.isfinite(offset) or offset >= amp_cap:
        raise PortTestError(f"Port DC offset {offset:g} leaves no room under cap {amp_cap:g}.")
    available = amp_cap - offset
    if offset:
        notes.append(f"DC offset magnitude {offset:g}: waveform allowance {available:g} under cap {amp_cap:g}.")

    scale = 1.0
    mw = info["kind"] in ("mw", "iq")
    if test == "cw":
        a = AMP_CAP_DEFAULT / 2 if amplitude is None else float(amplitude)
        if not math.isfinite(a) or abs(a) > available:
            raise PortTestError(f"Amplitude {a:g} plus DC offset {offset:g} exceeds cap {amp_cap:g}. Lower it or raise the cap.")
        op = "port_test_cw"
        wfs["port_test_cw.wf"] = {"type": "constant", "sample": a}
        wfs["port_test_zero.wf"] = {"type": "constant", "sample": 0.0}
        pulses["port_test_cw.pulse"] = {
            "operation": "control", "length": CW_PULSE_NS,
            "waveforms": ({"I": "port_test_cw.wf", "Q": "port_test_zero.wf"} if mw
                          else {"single": "port_test_cw.wf"})}
        el.setdefault("operations", {})[op] = "port_test_cw.pulse"
        peak = abs(a)
    else:
        candidates = [o for o in info["operations"] if (o["measurement"] if test == "loopback" else True)]
        if test == "loopback" and not info["has_measurement"]:
            raise PortTestError(f"{element} has no measurement operation and input port; "
                                "loopback needs a readout element.")
        names = [o["name"] for o in candidates]
        if operation is None:
            pref = ["readout"] if test == "loopback" else ["x180", "readout", "const"]
            operation = next((p for p in pref if p in names), names[0] if names else None)
        if operation not in names:
            raise PortTestError(f"{element} has no operation {operation!r} usable for this test.")
        src_pulse = src["operations"][operation]
        p = copy.deepcopy(config["pulses"][src_pulse])
        for w in (p.get("waveforms") or {}).values():
            wfs.setdefault(w, copy.deepcopy(config["waveforms"][w]))
        for w in (p.get("integration_weights") or {}).values():
            if w in (config.get("integration_weights") or {}):
                iws.setdefault(w, copy.deepcopy(config["integration_weights"][w]))
        peak0 = pulse_peak(config, src_pulse, complex_iq=info["kind"] == "mw") or 0.0
        if peak0 > available:
            scale = available / peak0
            notes.append(f"The operation's peak {peak0:.4g} is above the cap {amp_cap:g}: it plays "
                         f"scaled by {scale:.4g} (peak {available:g}, DC offset {offset:g}).")
        peak = peak0 * scale
        if test == "pulse":
            p["digital_marker"] = MARKER
        op = f"port_test_{operation}" if test == "pulse" or basis == "raw" else operation
        pulses[f"port_test.{operation}.pulse"] = p
        el.setdefault("operations", {})[op] = f"port_test.{operation}.pulse"
    dmarks = {MARKER: {"samples": [[1, 0]]}}
    for pl in pulses.values():
        dm = pl.get("digital_marker")
        if dm and dm != MARKER and dm in (config.get("digital_waveforms") or {}):
            dmarks[dm] = copy.deepcopy(config["digital_waveforms"][dm])
    if test == "pulse":
        dport = list(marker_port) if marker_port else digital_ports(config, info["port"])[0]
        _copy_port(config, cfg, dport, "digital_outputs")
        el["digitalInputs"] = {"port_test_trigger": {
            "port": dport, "delay": 0, "buffer": 0}}
        notes.append(f"Scope trigger: a digital marker on {_fmt_port(dport)} for the length of each pulse.")
    else:
        dport = None
    if test == "pulse" and int(period_ns) < int(p.get("length") or 0) + 16:
        raise PortTestError("The period must be longer than the pulse.")

    cfg["elements"] = {name: el}
    cfg["pulses"] = pulses
    cfg["waveforms"] = wfs
    cfg["digital_waveforms"] = dmarks
    if iws:
        cfg["integration_weights"] = iws
    fsp = info["full_scale_power_dbm"]
    plan = {
        "test": test, "basis": basis, "element": name, "source_element": element,
        "operation": op, "amp_scale": scale, "amp_cap": amp_cap,
        "expected_peak": peak, "expected_dbm": dbm(fsp, peak) if info["kind"] == "mw" else None,
        "kind": info["kind"], "port": info["port"], "in_port": info["in_port"],
        "marker_port": dport, "period_ns": int(period_ns), "shots": int(shots),
        "lo": info["lo"], "if": info["if"], "rf": info["rf"], "full_scale_power_dbm": fsp,
        "loopback": loopback, "pulse_ns": int(p.get("length") or 0) if test != "cw" else CW_PULSE_NS,
        "time_of_flight": info["time_of_flight"],
        "notes": notes,
        "config_fingerprint": config_fingerprint(cfg),
        "config_name": f"port-test:{test}:{basis}:{element}",
    }
    return cfg, plan


def _hz(v, div, unit, nd) -> str:
    return "?" if not isinstance(v, (int, float)) else f"{v / div:.{nd}f} {unit}"


def _fmt_port(p) -> str:
    return "/".join(str(x) for x in (p or [])) or "?"


_TITLES = {"dc": "DC offset", "cw": "CW tone", "pulse": "pulse train with a scope trigger", "loopback": "loopback raw ADC"}
_USE = {
    "dc": "Oscilloscope: zero waveform plus the selected port offset. Runs until Stop or the time limit.",
    "cw": "Spectrum analyser: a tone at the RF frequency below, at the power below. Runs until Stop (Ctrl+C).",
    "pulse": "Oscilloscope: trigger on the marker port; each pulse has the length and peak below, "
             "repeated every PERIOD_NS. Runs until Stop (Ctrl+C).",
    "loopback": "Cable the output port to the input port. The script plays the readout SHOTS times and "
                "plots the averaged raw ADC trace (time of flight, amplitude).",
}


def render_script(cfg: dict, plan: dict, *, chip_label: str = "", run_program: bool = True) -> str:
    """The downloadable script. SM runs exactly this text (``run_port_test.py``).

    ``run_program=False`` opens the QM and holds it with no program: an OPX1000
    output sits at its configured offset while a QM with that config is open
    (QM docs Guides/output_idle_values.md), and closing the QM returns it to 0 V."""
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rf = plan.get("rf")
    lines = [
        f"Port test: {_TITLES[plan['test']]} on {plan['source_element']} "
        f"({plan['basis']} basis).",
        f"Generated by QUAM State Manager at {when}" + (f" from {chip_label}." if chip_label else "."),
        "",
        f"Output port : {_fmt_port(plan['port'])}" + (f"   input port: {_fmt_port(plan['in_port'])}"
                                                    if plan["test"] == "loopback" else ""),
        f"RF / LO / IF: {_hz(rf, 1e9, 'GHz', 6)} / {_hz(plan['lo'], 1e9, 'GHz', 6)} / "
        f"{_hz(plan['if'], 1e6, 'MHz', 3)}",
        f"Peak amplitude {plan['expected_peak']:.4g} (fraction of full scale)"
        + (f" -> {plan['expected_dbm']:.2f} dBm (P = FSP + 20*log10|amp|, FSP "
           f"{plan['full_scale_power_dbm']} dBm)" if plan.get("expected_dbm") is not None else ""),
        "",
        _USE[plan["test"]],
        "",
        "WARNING: a CW tone or pulse train on a line into a fridge heats it and drives the qubits.",
        "Disconnect the line or make sure this is intended before running on hardware.",
        "",
        *plan["notes"],
        "",
        "Run:   python this_file.py opx  --host HOST [--cluster NAME] [--port PORT]",
        "       python this_file.py saas --email EMAIL --host SAAS_HOST   (password: SAAS_PASSWORD env var)",
    ]
    doc = '"""' + "\n".join(lines).replace('"""', "'''") + '\n"""'
    text = _SCRIPT.format(
        doc=doc, config=pprint.pformat(cfg, indent=1, width=100, sort_dicts=True),
        element=plan["element"], operation=plan["operation"], test=plan["test"],
        scale=repr(float(plan["amp_scale"])), period=int(plan["period_ns"]),
        shots=int(plan["shots"]), port=repr(list(plan["port"])),
        marker=repr(list(plan["marker_port"]) if plan["marker_port"] else None),
        peak=repr(float(plan["expected_peak"])),
        inputs=1 if plan["kind"] == "mw" else 2,
        loopback=repr([tuple(c) for c in plan.get("loopback") or []]),
        pulse_ns=int(plan.get("pulse_ns") or 0), tof=int(plan.get("time_of_flight") or 0),
        config_fp=plan["config_fingerprint"], config_name=plan["config_name"],
        run_program=bool(run_program),
    )
    plays = plan.get("plays") or []
    if len(plays) > 1:
        text = _multi_script(text, plays)
    return text


def _multi_script(text: str, plays: list) -> str:
    """Several ports: one play per element inside the one loop, no align, so
    every element (its own thread) starts together. Exact anchors or raise."""
    listing = [(p["element"], p["operation"]) for p in plays]
    ports = ", ".join(_fmt_port(p["port"]) for p in plays)
    swaps = [
        ("\nOutput port : ", f"\nPlays together: {ports}\nOutput port : "),
        ("\nOPERATION = ", f"\nPLAYS = {listing!r}  # (element, operation), all in parallel\nOPERATION = "),
        ("                play(OPERATION * amp(AMP_SCALE), ELEMENT)\n",
         "                for _el, _op in PLAYS:\n                    play(_op * amp(AMP_SCALE), _el)\n"),
    ]
    for old, new in swaps:
        if text.count(old) != 1:
            raise PortTestError("Internal: the play script template changed.")
        text = text.replace(old, new)
    return text


_SCRIPT = '''{doc}
import argparse
import signal
import threading
import queue
import os
import time

from qm import QuantumMachinesManager, SimulationConfig
from qm.qua import (program, play, measure, wait, infinite_loop_, for_, declare,
                    declare_stream, stream_processing, amp)

CONFIG = {config}

ELEMENT = {element!r}
OPERATION = {operation!r}
TEST = {test!r}
AMP_SCALE = {scale}
PERIOD_NS = {period}
SHOTS = {shots}
OUTPUT_PORT = {port}
MARKER_PORT = {marker}
EXPECTED_PEAK = {peak}
ADC_INPUTS = {inputs}  # an MW-FEM input is one complex input; LF / OPX+ have two
LOOPBACK = {loopback}  # simulator only: the out->in cable (from ..., to ...)
PULSE_NS = {pulse_ns}
TIME_OF_FLIGHT_NS = {tof}
CONFIG_FINGERPRINT = {config_fp!r}
CONFIG_NAME = {config_name!r}
#: False: open the QM and hold it (outputs at their offsets) with no program
RUN_PROGRAM = {run_program!r}
#: job states that hold no port; ANY other (or unknown) status counts as busy
TERMINAL = ("done", "completed", "canceled", "cancelled", "failed")


def _canon(value):
    if isinstance(value, dict):
        return {{str(k): _canon(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}}
    if isinstance(value, (list, tuple)):
        return [_canon(v) for v in value]
    return value


def config_fingerprint(config):
    import hashlib
    import json

    raw = json.dumps(_canon(config), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_program(shots=None):
    """The test program. CW and pulse train loop until halted."""
    shots = SHOTS if shots is None else int(shots)
    with program() as prog:
        if TEST == "loopback":
            adc = declare_stream(adc_trace=True)
            n = declare(int)
            with for_(n, 0, n < shots, n + 1):
                measure(OPERATION * amp(AMP_SCALE), ELEMENT, adc_stream=adc)
                wait(PERIOD_NS // 4, ELEMENT)
            with stream_processing():
                adc.input1().average().save("adc1")
                if ADC_INPUTS == 2:
                    adc.input2().average().save("adc2")
        else:
            with infinite_loop_():
                play(OPERATION * amp(AMP_SCALE), ELEMENT)
                if TEST == "pulse":
                    wait(PERIOD_NS // 4, ELEMENT)
    return prog


def reserved_ports(config):
    ports = set()
    for cname, con in config.get("controllers", {{}}).items():
        groups = [(None, con)] + list(con.get("fems", {{}}).items())
        for slot, group in groups:
            for io in ("analog_outputs", "analog_inputs", "digital_outputs", "digital_inputs"):
                for port in group.get(io, {{}}):
                    ports.add((str(cname), str(slot), io, str(port)))
    return ports


def busy_jobs(qmm):
    """Only explicit terminal states are idle; unknown or failed checks refuse."""
    busy = []
    needed = reserved_ports(CONFIG)
    for qm_id in qmm.list_open_qms():
        qm = qmm.get_qm(qm_id)
        if needed & reserved_ports(qm.get_config()):
            busy.append((str(qm_id), "ports reserved"))
        if hasattr(qm, "get_jobs"):
            jobs = qm.get_jobs()
        else:
            # Legacy API: its queue and current job are separate surfaces.
            jobs = list(qm.queue.pending_jobs)
            running = qm.get_running_job()
            if running is not None:
                jobs.append(running)
        for job in jobs:
            status = getattr(job, "status", None)
            if callable(status):
                try:
                    status = status()
                except Exception:
                    status = None
            if str(status).strip().lower() not in TERMINAL:
                busy.append((str(qm_id), str(getattr(job, "id", ""))))
    return busy


CALL_TIMEOUT_S = 15.0


def bounded(call, timeout=None, late=None):
    """Bound a stuck SDK call; dispose of a late resource rather than leak it."""
    timeout = CALL_TIMEOUT_S if timeout is None else timeout
    answer = queue.Queue()
    lock = threading.Lock()
    expired = False
    def worker():
        try:
            value = call()
            with lock:
                dispose = expired
                answer.put((True, value))
            if dispose and late:
                late(value)
        except BaseException as exc:
            answer.put((False, exc))
    thread = threading.Thread(target=worker, daemon=late is None)
    thread.start()
    thread.join(timeout)
    with lock:
        if answer.empty():
            expired = True
            raise TimeoutError("QM API call timed out; refusing further execution.")
        ok, value = answer.get_nowait()
    if not ok:
        raise value
    return value


def halt_close(qm, job=None):
    """Halt our job and close only the QM we created, even if halt fails or stalls."""
    try:
        if job is not None:
            bounded(job.halt)
    finally:
        bounded(qm.close)


class _Cleanup:
    """Halt + close exactly once, whichever of the guard, Stop or finally gets there first."""
    def __init__(self, qm, record):
        self.qm, self.record, self.job = qm, record, None
        self.lock = threading.Lock()
        self.done = False

    def __call__(self):
        with self.lock:
            if self.done:
                return
            self.done = True
            halt_close(self.qm, self.job)
            self.record(cleaned=True)


def run_on_hardware(qmm, should_stop=lambda: False, max_s=120.0, log=print,
                    record=lambda **ids: None):
    """Refuse busy/unknown hardware; open our own QM (never close another);
    run until should_stop() or max_s; always halt and close ONLY our QM."""
    max_s = float(max_s)
    if not 0 < max_s <= 3600:
        raise ValueError("Hardware duration must be in (0, 3600] seconds.")
    try:
        busy = bounded(lambda: busy_jobs(qmm))
    except Exception as exc:
        raise RuntimeError(f"Could not check the OPX for running jobs ({{exc}}); not running.")
    if busy:
        raise RuntimeError(f"The OPX is busy (open QM / job: {{busy}}); not running.")
    if should_stop():
        raise RuntimeError("Stopped before opening a QM.")
    record(phase="opening a QM", qm_config_fingerprint=CONFIG_FINGERPRINT, qm_config_name=CONFIG_NAME)
    def opened():
        qm = qmm.open_qm(CONFIG, close_other_machines=False)
        record(qm_id=str(qm.id))
        return qm
    qm = bounded(opened, late=lambda q: bounded(q.close))
    cleanup = _Cleanup(qm, record)
    finished = threading.Event()
    deadline = time.monotonic() + max_s
    def guard():
        # Stop / parent death / deadline halt the job even while the main
        # thread waits inside an SDK call.
        while not finished.wait(0.1):
            if should_stop() or time.monotonic() >= deadline:
                try:
                    cleanup()
                except Exception as exc:
                    record(cleanup_error=str(exc))
                return
    threading.Thread(target=guard, daemon=True).start()
    result = {{}}
    if not RUN_PROGRAM:
        try:
            record(phase="QM open, no program running")
            log("QM open; no program running")
            while not should_stop() and time.monotonic() < deadline:
                time.sleep(0.2)
        finally:
            finished.set()
            cleanup()
        return result
    try:
        def execute():
            j = qm.execute(build_program())
            cleanup.job = j
            record(job_id=str(getattr(j, "id", "")))
            return j
        job = bounded(execute, late=lambda j: halt_close(qm, j))
        log(f"running job {{getattr(job, 'id', '')}}")
        if TEST == "loopback":
            bounded(lambda: job.result_handles.wait_for_all_values(timeout=max_s),
                    timeout=max_s + CALL_TIMEOUT_S)
            for k in ("adc1", "adc2"):
                if k in job.result_handles:
                    result[k] = bounded(lambda: job.result_handles.get(k).fetch_all())
        else:
            while not should_stop() and time.monotonic() < deadline:
                time.sleep(0.2)
    finally:
        finished.set()
        cleanup()
    return result


SIM_SHOTS = 2


def simulate(qmm, duration_ns=None):
    """Simulated output samples, keyed by port name, and (loopback) the
    simulated raw ADC trace: the simulator models the out->in cable."""
    from qm.simulate import LoopbackInterface
    kw = {{}}
    if TEST == "loopback":
        kw["simulation_interface"] = LoopbackInterface([tuple(c) for c in LOOPBACK])
        prog = build_program(SIM_SHOTS)
        if duration_ns is None:
            duration_ns = SIM_SHOTS * (PULSE_NS + PERIOD_NS + TIME_OF_FLIGHT_NS) + 2000
    else:
        prog = build_program()
        if duration_ns is None:
            duration_ns = 4 * PERIOD_NS if TEST != "cw" else 4000
    job = qmm.simulate(CONFIG, prog, SimulationConfig(duration=int(duration_ns) // 4, **kw))
    job.wait_until("Done") if hasattr(job, "wait_until") else None
    adc = {{}}
    if TEST == "loopback":
        handles = job.result_handles
        handles.wait_for_all_values()
        for k in ("adc1", "adc2"):
            if k in handles:
                adc[k] = handles.get(k).fetch_all()
    return job.get_simulated_samples(), adc


def saas_qmm(client, version=None):
    """Start a QM-SaaS simulator matching CONFIG's FEMs; returns (instance, qmm)."""
    from qm_saas import ClusterConfig
    # QM-SaaS (QOP > 3.3) takes ONE controller; its FEM layout applies to
    # every controller in the cluster, so declare the union of all slots.
    cluster = ClusterConfig()
    ctl = cluster.controller()
    slots = {{}}
    for con in CONFIG["controllers"].values():
        for slot, fem in (con.get("fems") or {{}}).items():
            slots.setdefault(int(slot), fem.get("type"))
    for slot, kind in sorted(slots.items()):
        (ctl.mw_fems if kind == "MW" else ctl.lf_fems)(slot)
    inst = client.simulator(version or client.latest_version(), cluster)
    inst.spawn()
    if hasattr(type(inst), "qmm_connection_params"):
        params = inst.qmm_connection_params
    else:
        import ssl
        params = dict(host=inst.host, port=inst.port,
                      connection_headers=inst.default_connection_headers,
                      credentials=ssl.create_default_context(),
                      follow_gateway_redirections=False)
    return inst, QuantumMachinesManager(**params)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="target", required=True)
    o = sub.add_parser("opx")
    o.add_argument("--host", required=True)
    o.add_argument("--seconds", type=float, default=120.0, help="maximum hardware duration (default 120 s)")
    o.add_argument("--cluster", default=None)
    o.add_argument("--port", type=int, default=None)
    s = sub.add_parser("saas")
    s.add_argument("--email", required=True)
    s.add_argument("--host", required=True)
    s.add_argument("--ns", type=int, default=None, help="simulated duration in ns")
    a = ap.parse_args()
    if a.target == "opx":
        kw = {{"host": a.host}}
        if a.cluster:
            kw["cluster_name"] = a.cluster
        if a.port:
            kw["port"] = a.port
        stopped = threading.Event()
        def stop_signal(signum, frame):
            # Only flag it: the run loop halts the job and closes our QM.
            stopped.set()
        signal.signal(signal.SIGINT, stop_signal)
        signal.signal(signal.SIGTERM, stop_signal)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, stop_signal)
        qmm = bounded(lambda: QuantumMachinesManager(timeout=CALL_TIMEOUT_S, **kw))
        try:
            res = run_on_hardware(qmm, should_stop=stopped.is_set, max_s=a.seconds)
        except KeyboardInterrupt:
            print("stopped")
            return
        if res:
            import matplotlib.pyplot as plt
            for k, v in res.items():
                plt.plot(v, label=k)
            plt.xlabel("time [ns]"); plt.ylabel("ADC"); plt.legend(); plt.show()
    else:
        from qm_saas import QmSaas
        client = QmSaas(email=a.email, password=os.environ["SAAS_PASSWORD"], host=a.host)
        inst, qmm = saas_qmm(client)
        try:
            samples, adc = simulate(qmm, a.ns)
            samples.con1.plot() if hasattr(samples, "con1") else None
            import matplotlib.pyplot as plt
            if adc:
                plt.figure()
                for k, v in adc.items():
                    plt.plot(v, label=k)
                plt.xlabel("time [ns]"); plt.ylabel("simulated ADC (loopback)"); plt.legend()
            plt.show()
        finally:
            inst.close()


if __name__ == "__main__":
    main()
'''
