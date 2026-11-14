"""What a built chip's output timing IS, read from its state / wiring dicts.

One pure reading (plain dicts, no QM imports) of the delays a build or a
Re-generate wrote: per MW-FEM output in use its band and delay, the flux
LF-FEM port delays, and the resonators whose readout output carries a delay
(with whether their time of flight moved with it). The wizard's build result
and Re-generate's result both list this, so the page never predicts numbers
the build did not write.

"In use" is the build's rule (``generator/run_build.py`` ``_mw_output_ports``):
an MW output counts when an element's output reference resolves to it. A
stored chip may keep port objects no element points at; they do not count.
"""

from __future__ import annotations

# MW-FEM output latency per band relative to an LF-FEM output; the same
# measured table as ``generator/run_build.py`` ``_BAND_TO_DELAY_NS``.
BAND_LATENCY_NS = {1: 141, 2: 161, 3: 141}

_MW_PREFIX = "ports/mw_outputs/"
_LF_PREFIX = "ports/analog_outputs/"


def _resolve(doc: dict, value, depth: int = 0):
    """Follow a ``#/...`` pointer chain; ``(path, value)`` or ``(None, None)``."""
    path = None
    while isinstance(value, str) and value.startswith("#/") and depth < 12:
        path = value[2:]
        node = doc
        for part in path.split("/"):
            if not isinstance(node, dict) or part not in node:
                return None, None
            node = node[part]
        value, depth = node, depth + 1
    return path, value


def _doc(state: dict, wiring: dict) -> dict:
    return {"wiring": (wiring or {}).get("wiring") or {}, **(state or {})}


def _output_refs(node, out: list):
    """Every ``opx_output`` value under ``node`` (elements, not ``ports``)."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "opx_output":
                out.append(v)
            else:
                _output_refs(v, out)
    elif isinstance(node, list):
        for v in node:
            _output_refs(v, out)


def _ports_in_use(state: dict, wiring: dict, prefix: str) -> list[tuple[str, dict]]:
    doc = _doc(state, wiring)
    refs: list = []
    _output_refs({k: v for k, v in (state or {}).items() if k != "ports"}, refs)
    seen, found = set(), []
    for ref in refs:
        path, port = _resolve(doc, ref)
        if path and path.startswith(prefix) and isinstance(port, dict) and path not in seen:
            seen.add(path)
            found.append((path, port))
    return sorted(found)


def mw_outputs_in_use(state: dict, wiring: dict) -> list[tuple[str, dict]]:
    """``(path, port)`` for every MW-FEM output an element uses (see module doc)."""
    return _ports_in_use(state, wiring, _MW_PREFIX)


def port_label(port: dict, path: str) -> str:
    """A port named the way the wizard names ports: controller / slot / port."""
    con, fem, num = port.get("controller_id"), port.get("fem_id"), port.get("port_id")
    if con is None or num is None:
        return path
    return f"{con} / slot {fem} / port {num}" if fem is not None else f"{con} / port {num}"


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def summary(state: dict, wiring: dict, tof_moved=None, tof_typed=()) -> dict | None:
    """The output timing written on a chip, or ``None`` when it has no MW output
    and no flux port in use. Never raises.

    ``mw``: ``{port, label, band, delay}`` per MW output in use; ``flux``:
    ``{port, label, delay, of}`` per LF-FEM output a qubit z or a coupler uses;
    ``tof``: per resonator whose readout output has a delay ``s > 0``:
    ``{qubit, shift, value, moved}``. ``moved`` says whether its time of flight
    moved with that delay: by default (a fresh build) every resonator except
    the ones in ``tof_typed`` (values the user typed stay as typed); a
    Re-generate passes ``tof_moved``, the qubits whose time of flight it moved.
    """
    try:
        doc = _doc(state, wiring)
        mw = [{"port": p, "label": port_label(v, p), "band": v.get("band"),
               "delay": v.get("delay") or 0} for p, v in mw_outputs_in_use(state, wiring)]
        mw_delay = {m["port"]: m["delay"] for m in mw}
        flux, seen = [], set()

        def add_flux(ref, of):
            path, port = _resolve(doc, ref)
            if path and path.startswith(_LF_PREFIX) and isinstance(port, dict) \
                    and path not in seen:
                seen.add(path)
                flux.append({"port": path, "label": port_label(port, path),
                             "delay": port.get("delay") or 0, "of": of})

        tof = []
        typed = set(tof_typed or ())
        for qid, q in ((state or {}).get("qubits") or {}).items():
            if not isinstance(q, dict):
                continue
            if isinstance(q.get("z"), dict):
                add_flux(q["z"].get("opx_output"), qid)
            rr = q.get("resonator")
            if not isinstance(rr, dict):
                continue
            path, _ = _resolve(doc, rr.get("opx_output"))
            s = mw_delay.get(path, 0)
            if _num(s) and s > 0 and _num(rr.get("time_of_flight")):
                moved = (qid not in typed) if tof_moved is None else (qid in set(tof_moved))
                tof.append({"qubit": qid, "shift": s, "value": rr["time_of_flight"],
                            "moved": moved})
        for pid, pair in ((state or {}).get("qubit_pairs") or {}).items():
            if isinstance(pair, dict) and isinstance(pair.get("coupler"), dict):
                add_flux(pair["coupler"].get("opx_output"), pid)
        if not mw and not flux:
            return None
        return {"mw": mw, "flux": flux, "tof": tof}
    except (AttributeError, TypeError, ValueError, KeyError):
        return None


def typed_tof(spec: dict | None) -> set[str]:
    """Qubits whose time of flight the user typed in the populate step."""
    rows = (((spec or {}).get("populate") or {}).get("resonator")) or {}
    return {q for q, v in rows.items() if isinstance(v, dict) and "time_of_flight" in v}


def annotate(outcome: dict, state: dict, wiring: dict, tof_moved=None, tof_typed=()) -> None:
    """Put :func:`summary` on ``outcome["result"]["output_timing"]``."""
    res = outcome.get("result") if isinstance(outcome, dict) else None
    if isinstance(res, dict):
        summ = summary(state, wiring, tof_moved=tof_moved, tof_typed=tof_typed)
        if summ is not None:
            res["output_timing"] = summ
