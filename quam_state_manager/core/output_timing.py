"""What a built chip's output timing IS, read from its state / wiring dicts.

One pure reading (plain dicts, no QM imports) of the delays a build or a
Re-generate wrote: per MW-FEM output in use its band and delay, the LF-FEM
output delays, and the resonators whose readout output carries a delay
(with what happened to their time of flight). The wizard's build result
and Re-generate's result both list this, so the page never predicts numbers
the build did not write.

"In use" and "which time of flight is the user's" are the build's own
functions (``generator/run_build.py`` ``_used_port_paths`` and
``_tof_provenance``), loaded from that file, so the screen and the build
cannot count differently: a port no element plays does not count.
"""

from __future__ import annotations

# MW-FEM output latency per band relative to an LF-FEM output; the same
# table as ``generator/run_build.py`` ``_BAND_TO_DELAY_NS``. Where these
# numbers come from is not recorded in this repository; the docs
# (``Guides/opx1000_fems.md``) give different absolute values (about 155 /
# 175 ns). Only the 20 ns difference between bands is confirmed by both.
BAND_LATENCY_NS = {1: 141, 2: 161, 3: 141}

# The same sentence, in user words, under the "Output timing" result.
LATENCY_NOTE = ("The 20 ns step between MW band 2 and bands 1 / 3 is documented. The flux port "
                "delay relative to the MW outputs is a starting value: measure the flux to "
                "microwave timing on the device.")

_MW_PREFIX = "ports/mw_outputs/"
_LF_PREFIX = "ports/analog_outputs/"


def _rb():
    from quam_state_manager.core import script_emitter
    return script_emitter._run_build()


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
    return {**(state or {}), "wiring": (wiring or {}).get("wiring") or {}}


def _ports_in_use(state: dict, wiring: dict, prefix: str) -> list[tuple[str, dict]]:
    doc = _doc(state, wiring)
    found = []
    for path in _rb()._used_port_paths(doc, prefix):
        _, port = _resolve(doc, "#/" + path)
        if isinstance(port, dict):
            found.append((path, port))
    return found


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


def _owners(state: dict, doc: dict) -> dict:
    """``{port path: element name}`` for every LF output reference outside
    ``ports`` / ``wiring`` (a qubit z, a pair coupler, any other element)."""
    out: dict = {}

    def walk(node, where):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, str) and k.startswith("opx_output"):
                    path, _ = _resolve(doc, v)
                    if path:
                        out.setdefault(path, where)
                else:
                    walk(v, where + [str(k)])
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, where + [str(i)])

    for top, node in (state or {}).items():
        if top not in ("ports", "wiring") and isinstance(node, dict):
            for name, el in node.items():
                walk(el, [str(name)])
    return {p: "/".join(w) for p, w in out.items()}


def _readout_delays(state: dict, wiring: dict) -> dict:
    """``{qubit: (readout output delay, time of flight)}`` of a chip."""
    doc, out = _doc(state, wiring), {}
    for qid, q in ((state or {}).get("qubits") or {}).items():
        rr = q.get("resonator") if isinstance(q, dict) else None
        if isinstance(rr, dict):
            _, port = _resolve(doc, rr.get("opx_output"))
            d = (port.get("delay") or 0) if isinstance(port, dict) else None
            out[qid] = (d, rr.get("time_of_flight"))
    return out


def summary(state: dict, wiring: dict, tof_moved=None, tof_typed=(),
            tof_unrecorded=(), source=None) -> dict | None:
    """The output timing written on a chip, or ``None`` when it has no MW output
    and no LF output in use. Never raises.

    ``mw``: ``{port, label, band, delay}`` per MW output in use; ``flux``:
    ``{port, label, delay, of, kind}`` per LF-FEM output in use (``kind``
    ``flux`` for a qubit z or a coupler, ``lf`` for any other element);
    ``tof``: per resonator whose readout output has a delay ``s > 0`` and
    whose time of flight this run moved or would have moved:
    ``{qubit, shift, value, moved}``.

    A fresh build (``tof_moved`` None) moved every resonator except
    ``tof_typed`` (kept as the user set it, ``moved`` False) and
    ``tof_unrecorded`` (no record of its source: counted in
    ``tof_unrecorded``, one grouped line, not listed per qubit). A
    Re-generate passes ``tof_moved``, the qubits it moved, and ``source``, the
    source chip's ``(state, wiring)``: the moved ones are listed; a resonator
    whose readout output delay is the source chip's is counted in
    ``tof_unchanged``; one whose delay differs from the source chip (or that
    the source chip does not have) and was not moved is counted in
    ``tof_check`` (its time of flight must be measured again). Without
    ``source`` every unmoved resonator is in ``tof_check``: nothing says its
    delay was already there."""
    try:
        doc = _doc(state, wiring)
        mw = [{"port": p, "label": port_label(v, p), "band": v.get("band"),
               "delay": v.get("delay") or 0} for p, v in mw_outputs_in_use(state, wiring)]
        owners = _owners(state, doc)
        z_or_c = {}   # LF port path -> (rank, owner): qubit z first, then couplers
        for i, (qid, q) in enumerate(((state or {}).get("qubits") or {}).items()):
            if isinstance(q, dict) and isinstance(q.get("z"), dict):
                path, _ = _resolve(doc, q["z"].get("opx_output"))
                z_or_c.setdefault(path, ((0, i), qid))
        for i, (pid, pair) in enumerate(((state or {}).get("qubit_pairs") or {}).items()):
            if isinstance(pair, dict) and isinstance(pair.get("coupler"), dict):
                path, _ = _resolve(doc, pair["coupler"].get("opx_output"))
                z_or_c.setdefault(path, ((1, i), pid))
        flux = []
        for path, port in _ports_in_use(state, wiring, _LF_PREFIX):
            rank, of = z_or_c.get(path, ((2, 0), None))
            flux.append({"port": path, "label": port_label(port, path),
                         "delay": port.get("delay") or 0,
                         "of": of or owners.get(path, ""),
                         "kind": "flux" if of else "lf", "_rank": rank})
        flux.sort(key=lambda f: (f["_rank"], f["port"]))
        for f in flux:
            f.pop("_rank")
        delay_of = {m["port"]: m["delay"] for m in mw}
        delay_of.update({f["port"]: f["delay"] for f in flux})
        tof, unrecorded, unchanged, check = [], [], [], []
        typed, unrec = set(tof_typed or ()), set(tof_unrecorded or ())
        before = _readout_delays(*source) if source else {}
        for qid, q in ((state or {}).get("qubits") or {}).items():
            rr = q.get("resonator") if isinstance(q, dict) else None
            if not isinstance(rr, dict):
                continue
            path, _ = _resolve(doc, rr.get("opx_output"))
            s = delay_of.get(path, 0)
            if not (_num(s) and s > 0 and _num(rr.get("time_of_flight"))):
                continue
            entry = {"qubit": qid, "shift": s, "value": rr["time_of_flight"]}
            if tof_moved is not None:
                if qid in set(tof_moved):
                    tof.append({**entry, "moved": True})
                elif qid in before and before[qid][0] == s:
                    unchanged.append(qid)
                else:
                    check.append(qid)
            elif qid in unrec:
                unrecorded.append(qid)
            else:
                tof.append({**entry, "moved": qid not in typed})
        if not mw and not flux:
            return None
        out = {"mw": mw, "flux": flux, "tof": tof}
        if unrecorded:
            out["tof_unrecorded"] = unrecorded
        if unchanged:
            out["tof_unchanged"] = unchanged
        if check:
            out["tof_check"] = check
        if mw and flux:
            out["note"] = LATENCY_NOTE
        return out
    except (AttributeError, TypeError, ValueError, KeyError):
        return None


def tof_provenance(spec: dict | None) -> tuple[set, set]:
    """``(typed, unrecorded)``: the build's own rule
    (``generator.run_build._tof_provenance``)."""
    return _rb()._tof_provenance(spec)


def typed_tof(spec: dict | None) -> set[str]:
    """Qubits whose time of flight the user set in the populate step."""
    return tof_provenance(spec)[0]


def annotate(outcome: dict, state: dict, wiring: dict, tof_moved=None, spec=None,
             source=None) -> None:
    """Put :func:`summary` on ``outcome["result"]["output_timing"]``; a fresh
    build passes its ``spec`` (who set each time of flight), a Re-generate
    passes ``tof_moved`` and the source chip's ``(state, wiring)``."""
    res = outcome.get("result") if isinstance(outcome, dict) else None
    if isinstance(res, dict):
        typed, unrec = tof_provenance(spec) if tof_moved is None else (set(), set())
        summ = summary(state, wiring, tof_moved=tof_moved, tof_typed=typed,
                       tof_unrecorded=unrec, source=source)
        if summ is not None:
            res["output_timing"] = summ
