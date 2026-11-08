"""Read an add / remove back from the GENERATED config (FB-12 ladder step 2).

The add form proposes a port, an LO and amplitudes from ``state.json``; what the
hardware is told is the dict ``machine.generate_config()`` returns. This module
reads that dict, never the state: per element, the port it plays on, LO, IF and
RF, and per operation the peak amplitude and the output power it means.

Power on a MW-FEM output: ``P[dBm] = full_scale_power_dbm + 20*log10(|a|)``
with ``|a|`` the peak of ``sqrt(I^2 + Q^2)`` -- the same identity
:mod:`physical_units` uses for every amplitude on the entity surfaces (docs/109).
An LF-FEM output plays volts: the peak is shown as volts, no dBm.

Pure: no I/O.
"""

from __future__ import annotations

import math
from typing import Any

MW_KEY = "MWInput"          # the element's input = the OPX output it plays on
MW_RO_KEY = "MWOutput"      # the element's output = the OPX input it reads
LF_KEY = "singleInput"


def _fem_port(config: dict, port) -> dict:
    try:
        con, fem, pid = port
        fems = config["controllers"][con]["fems"]
        f = fems.get(str(fem), fems.get(fem)) if isinstance(fems, dict) else None
        outs = (f or {}).get("analog_outputs") or {}
        p = outs.get(str(pid), outs.get(pid))
        return p if isinstance(p, dict) else {}
    except (KeyError, TypeError, ValueError):
        return {}


def _lo(port_cfg: dict, upconverter) -> float | None:
    if isinstance(port_cfg.get("upconverter_frequency"), (int, float)):
        return float(port_cfg["upconverter_frequency"])
    ups = port_cfg.get("upconverters")
    if isinstance(ups, dict):
        u = ups.get(str(upconverter), ups.get(upconverter))
        if isinstance(u, dict) and isinstance(u.get("frequency"), (int, float)):
            return float(u["frequency"])
    return None


def _wf_peak(config: dict, name: str | None):
    """(peak |sample|, samples list or None) of one waveform."""
    wf = (config.get("waveforms") or {}).get(name) if name else None
    if not isinstance(wf, dict):
        return None, None
    if wf.get("type") == "constant":
        v = float(wf.get("sample") or 0.0)
        return abs(v), None
    s = wf.get("samples")
    if isinstance(s, list) and s:
        return max(abs(float(x)) for x in s), [float(x) for x in s]
    return None, None


def op_peak(config: dict, pulse_name: str) -> float | None:
    """Peak output amplitude of a pulse: ``max sqrt(I^2 + Q^2)`` over samples
    (a constant waveform is one sample repeated) [derived]."""
    p = (config.get("pulses") or {}).get(pulse_name)
    if not isinstance(p, dict):
        return None
    wfs = p.get("waveforms") or {}
    if "single" in wfs:
        return _wf_peak(config, wfs["single"])[0]
    pi, si = _wf_peak(config, wfs.get("I"))
    pq, sq = _wf_peak(config, wfs.get("Q"))
    if pi is None and pq is None:
        return None
    if si is not None and sq is not None and len(si) == len(sq):
        return max(math.hypot(a, b) for a, b in zip(si, sq))
    # one side constant (or lengths differ): hypot is monotone in each
    # argument, so max_t hypot(c, q_t) == hypot(|c|, max_t |q_t|) -- the peaks
    # combine exactly [derived; pinned in tests]
    return math.hypot(pi or 0.0, pq or 0.0)


def dbm(fsp: float | None, amp: float | None) -> float | None:
    if fsp is None or amp is None or amp <= 0:
        return None
    return float(fsp) + 20.0 * math.log10(amp)


def element_rows(config: dict, element: str) -> dict | None:
    el = (config.get("elements") or {}).get(element)
    if not isinstance(el, dict):
        return None
    row: dict[str, Any] = {"element": element, "kind": None, "port": None, "in_port": None,
                           "lo": None, "if_": el.get("intermediate_frequency"),
                           "rf": None, "fsp": None, "ops": []}
    if isinstance(el.get(MW_KEY), dict):
        mi = el[MW_KEY]
        row["kind"] = "mw"
        row["port"] = list(mi.get("port") or [])
        pc = _fem_port(config, mi.get("port"))
        row["fsp"] = pc.get("full_scale_power_dbm")
        row["lo"] = _lo(pc, mi.get("upconverter", 1))
        if isinstance(el.get(MW_RO_KEY), dict):
            row["in_port"] = list(el[MW_RO_KEY].get("port") or [])
    elif isinstance(el.get(LF_KEY), dict):
        row["kind"] = "lf"
        row["port"] = list(el[LF_KEY].get("port") or [])
    if row["lo"] is not None and isinstance(row["if_"], (int, float)):
        row["rf"] = row["lo"] + float(row["if_"])
    for op, pulse in sorted((el.get("operations") or {}).items()):
        amp = op_peak(config, pulse)
        row["ops"].append({"op": op, "pulse": pulse, "amp": amp,
                           "dbm": dbm(row["fsp"], amp) if row["kind"] == "mw" else None})
    return row


def readback(config: dict, elements: list[str], *, template: str | None = None,
             new: str | None = None, expect: dict | None = None) -> list[dict]:
    """One row per element present in ``config``. With ``template``/``new``
    (an added qubit copied from a template), each MW operation carries the
    template's power on the same operation and, when the two differ, the
    amplitude factor that would match it (a suggestion only). ``expect`` maps
    an element to ``{"port": [...], "rf": Hz}`` the user chose; a mismatch
    is flagged on the row."""
    out = []
    for name in elements:
        row = element_rows(config, name)
        if row is None:
            continue
        if template and new and name.startswith(new + "."):
            trow = element_rows(config, template + name[len(new):])
            if trow:
                row["template_element"] = trow["element"]
                row["template_fsp"] = trow["fsp"]
                tops = {o["op"]: o for o in trow["ops"]}
                for o in row["ops"]:
                    t = tops.get(o["op"])
                    o["template_dbm"] = t["dbm"] if t else None
                    if o["dbm"] is not None and o.get("template_dbm") is not None \
                            and abs(o["dbm"] - o["template_dbm"]) > 0.05:
                        o["factor"] = 10 ** ((o["template_dbm"] - o["dbm"]) / 20.0)
        deltas = [o["dbm"] - o["template_dbm"] for o in row["ops"]
                  if o.get("template_dbm") is not None and o["dbm"] is not None]
        if deltas and max(deltas) - min(deltas) <= 0.05 and abs(deltas[0]) > 0.05:
            # one shift for the whole element (a port full-scale difference):
            # said once on the element, not on every operation
            row["shift_db"] = deltas[0]
            row["shift_factor"] = 10 ** (-deltas[0] / 20.0)
            row["shift_fsp"] = (row.get("fsp") is not None and row.get("template_fsp") is not None
                                and abs(float(row["fsp"]) - float(row["template_fsp"])) > 0.05)
            for o in row["ops"]:
                o.pop("factor", None)
        exp = (expect or {}).get(name) or {}
        problems = []
        if exp.get("port") and row["port"] and list(exp["port"]) != list(row["port"]):
            problems.append(f"plays on {row['port']}, chosen {exp['port']}")
        if exp.get("rf") is not None and row["rf"] is not None \
                and abs(float(exp["rf"]) - row["rf"]) > 1.0:
            problems.append(f"RF {row['rf']:.0f} Hz, chosen {float(exp['rf']):.0f} Hz")
        row["problems"] = problems
        out.append(row)
    return out


def elements_of(config: dict, entity: str) -> list[str]:
    return sorted(k for k in (config.get("elements") or {}) if k.startswith(entity + "."))
