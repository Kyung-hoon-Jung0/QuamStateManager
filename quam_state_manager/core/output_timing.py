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

import math

from quam_state_manager.core import pointer_resolver
from quam_state_manager.generator._script_common import (  # noqa: F401  (re-exported)
    tof_follows_note, tof_kept_note, tof_not_moved_note, tof_on_grid,
    tof_reference_not_number_note, tof_reference_note, tof_rounded_note)


def _grid(value):
    """``tof_on_grid`` for a stored value; ``(True, value, "")`` when the rule cannot place it."""
    try:
        return tof_on_grid(value)
    except ValueError:
        return True, value, ""

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


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def stored_tof(doc: dict, rr: dict, path: str):
    """A resonator's stored time of flight, read with the store's resolver
    (``pointer_resolver.resolve_pointer``), as ``(value, why_not, ref)``.

    ``value`` is the plain finite number stored (``why_not`` None), else
    the stored value (a pointer string included); ``why_not`` the clause naming why
    no number is placed at this leaf. ``ref`` is set for a QUAM pointer:
    ``{"target", "through", "resolves", "held"}`` where ``target`` is the
    leaf the chain lands on (``pointer_resolver.landing_path``; the first
    hop when the chain dangles or loops), ``through`` the first hop,
    ``resolves`` whether it gives a finite number, ``held`` what the landing
    leaf holds when that is not a finite number. SM never replaces a pointer
    with a literal (it may be shared by several elements), so a pointer is
    never placed, even one that resolves."""
    if not isinstance(rr, dict) or "time_of_flight" not in rr:
        return None, "no time of flight is stored", None
    stored = rr["time_of_flight"]
    if pointer_resolver.is_pointer(stored):
        holder = tuple(path.split("/"))
        try:
            # the resolver's own hops name the leaf it read the value from
            value, read_at = pointer_resolver.resolve_with_landing(doc, stored, holder)
            first = pointer_resolver.target_path(doc, stored, holder)
            if pointer_resolver.is_self_ref(stored):
                # the resolver hands a stored #./ back as is; name the leaf it names
                land = pointer_resolver.landing_path(doc, stored, holder)
            elif pointer_resolver.is_pointer(value) and not pointer_resolver.is_self_ref(value):
                land = None     # dangling, a cycle, too many hops: named by the first hop
            else:
                land = read_at  # a value, or the leaf holding a #./ the resolver stops at
        except (AttributeError, TypeError, ValueError, KeyError, IndexError):
            value, first, land = stored, None, None   # named as not resolving
        through = "/".join(first) if first else stored
        ref = {"target": "/".join(land) if land else through, "through": through,
               "resolves": _finite(value), "held": None}
        if ref["resolves"]:
            return stored, "it is a reference", ref
        if pointer_resolver.is_self_ref(stored) or pointer_resolver.is_self_ref(value):
            ref["held"] = "SM does not follow a #./ reference"
        elif not pointer_resolver.is_pointer(value):
            # the chain lands on a value: name it (text, a bool, nan, ...)
            ref["held"] = ("it holds " + (f"{value:g}" if isinstance(value, float) else
                                          repr(value) if isinstance(value, (str, bool, int))
                                          or value is None else "an object"))
        return stored, "it is a reference that does not resolve to a number", ref
    if not isinstance(stored, (int, float)) or isinstance(stored, bool):
        return stored, f"the stored value {stored!r} is not a number SM can place", None
    if not math.isfinite(stored):
        return stored, f"the stored value is {stored:g}, not a finite number", None
    return stored, None, None


def follows_lag(moved_by: dict | None, target: str, shift):
    """How many ns more a moved target time of flight moved than the
    referencing qubit's own readout output (``shift``); ``0`` when either is
    unknown (nothing to compare, the plain sentence)."""
    by = (moved_by or {}).get(target)
    if not (_finite(by) and _finite(shift)):
        return 0
    return by - shift


def tof_unplaced_note(qubit: str, reading, moved: dict | None = None, shift=None,
                      moved_by: dict | None = None):
    """The one sentence for a stored time of flight SM does not write, or
    ``None`` when the grid rule places it. ``reading`` is :func:`stored_tof`'s
    answer, ``moved`` ``{path: new value}`` of the times of flight this run
    moves (a reference to one of them follows it) and ``moved_by``
    ``{path: new - old}``, ``shift`` this qubit's readout output delay change
    (a plain number whose sum is negative is named; a reference that follows
    a value moved by a different amount than ``shift`` is told the
    difference, :func:`follows_lag`). Returns ``(note, kind)`` with kind
    ``follows`` / ``reference`` / ``not_number`` / ``not_moved``, or ``None``.
    The band shift plan, its offer, the applied note and the result review
    all print this sentence."""
    value, why_not, ref = reading
    if ref is not None:
        target = ref["target"]
        if not ref["resolves"]:
            return tof_reference_not_number_note(qubit, target, ref["held"]), "not_number"
        if target in (moved or {}):
            return tof_follows_note(qubit, target, moved[target], ref["through"], shift,
                                    follows_lag(moved_by, target, shift)), "follows"
        return tof_reference_note(qubit, target, ref["through"]), "reference"
    if why_not is None:
        if shift is None:
            return None
        raw = value + shift
        try:
            tof_on_grid(raw)
            return None
        except ValueError as exc:
            why_not = f"{value:g} + {shift:g} ns gives {raw:g} ns, and {exc}"
    return tof_not_moved_note(qubit, why_not), "not_moved"


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


def _moved_value(state: dict, qid: str):
    rr = (((state or {}).get("qubits") or {}).get(qid) or {}).get("resonator")
    return rr.get("time_of_flight") if isinstance(rr, dict) else None


def summary(state: dict, wiring: dict, tof_moved=None, tof_typed=(),
            tof_unrecorded=(), source=None) -> dict | None:
    """The output timing written on a chip, or ``None`` when it has no MW output
    and no LF output in use. Never raises.

    ``mw``: ``{port, label, band, delay}`` per MW output in use; ``flux``:
    ``{port, label, delay, of, kind}`` per LF-FEM output in use (``kind``
    ``flux`` for a qubit z or a coupler, ``lf`` for any other element);
    ``tof``: per resonator whose readout output has a delay ``s > 0`` (or,
    on a Re-generate, a delay of 0 that differs from the source chip's) and
    whose time of flight this run moved or would have moved:
    ``{qubit, shift, value, moved}``.

    A fresh build (``tof_moved`` None) moved every resonator except
    ``tof_typed`` (kept as the user set it, ``moved`` False) and
    ``tof_unrecorded`` (no record of its source: counted in
    ``tof_unrecorded``, one grouped line, not listed per qubit). A
    Re-generate passes ``tof_moved``, the qubits it moved (a list, or a
    mapping ``{qubit: time of flight before the run}``; a moved entry then
    carries ``from``, that old value, when it is a number), and ``source``, the
    source chip's ``(state, wiring)``: the moved ones are listed; a resonator
    whose readout output delay is the source chip's is counted in
    ``tof_unchanged``; one whose delay differs from the source chip (or that
    the source chip does not have) and was not moved is counted in
    ``tof_check`` (its time of flight must be measured again). Without
    ``source`` every unmoved resonator is in ``tof_check``: nothing says its
    delay was already there.

    ``tof_unplaced``: ``{qubit, note}`` per resonator on such a readout
    output whose stored time of flight is not a finite number (a reference,
    text, nan, none stored), with :func:`tof_unplaced_note`'s sentence, the
    one the band shift plan and its applied note print; a reference to a
    time of flight this run moved is listed even on an undelayed output."""
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
        tof, unrecorded, unchanged, check, unplaced = [], [], [], [], []
        typed, unrec = set(tof_typed or ()), set(tof_unrecorded or ())
        before = _readout_delays(*source) if source else {}
        old_tof = tof_moved if isinstance(tof_moved, dict) else {}
        moved_set = set(tof_moved) if tof_moved is not None else set()
        for qid, q in ((state or {}).get("qubits") or {}).items():
            rr = q.get("resonator") if isinstance(q, dict) else None
            if not isinstance(rr, dict):
                continue
            path, _ = _resolve(doc, rr.get("opx_output"))
            s = delay_of.get(path, 0)
            if not (_num(s) and s >= 0):
                continue
            same = qid in before and same_number(before[qid][0], s)
            if not _finite(rr.get("time_of_flight")):
                # a value SM could not place (a reference, text, nan, none):
                # the same sentence the plan and the applied note print
                tof_path = f"qubits/{qid}/resonator/time_of_flight"
                reading = stored_tof(doc, rr, tof_path)
                moved_to = {f"qubits/{m}/resonator/time_of_flight": v for m in moved_set
                            if _finite(v := _moved_value(state, m))}
                moved_by = {p: v - old_tof[m] for p, v in moved_to.items()
                            if _finite(old_tof.get(m := p.split("/")[1]))}
                # this readout output's own change on this run (unknown: plain sentence)
                own = (s - before[qid][0] if qid in before and _num(before[qid][0])
                       else None) if tof_moved is not None else None
                note = tof_unplaced_note(qid, reading, moved_to, shift=own, moved_by=moved_by)
                kind = note[1] if note is not None else None
                # on an output whose delay is the source chip's nothing moved
                # here: a reference is said nothing, as an unchanged literal
                changed = s != 0 and not (kind == "reference" and same) or (
                    tof_moved is not None and qid in before and not same)
                if kind == "follows" or (note is not None and changed):
                    unplaced.append({"qubit": qid, "note": note[0]})
                continue
            typed_off_grid = qid in typed and not _grid(rr["time_of_flight"])[0]
            if s == 0 and not typed_off_grid and not (tof_moved is not None and (
                    qid in moved_set or (qid in before and not same))):
                continue   # no delay, and none moved away: nothing to measure again
            entry = {"qubit": qid, "shift": s, "value": rr["time_of_flight"]}
            if typed_off_grid:
                _, nearest, reason = _grid(entry["value"])
                entry["grid_note"] = tof_kept_note(entry["value"], nearest, reason)
                if s == 0:
                    # nothing delayed: the only thing to say is the grid
                    entry["grid_only"] = True
            if qid in moved_set and _num(old_tof.get(qid)) and qid in before:
                raw = old_tof[qid] + s - before[qid][0]
                ok, nearest, reason = _grid(raw)
                entry["sum"] = raw
                if not ok and nearest == entry["value"]:
                    entry["grid_note"] = tof_rounded_note(raw, nearest, reason)
            if tof_moved is not None:
                if qid in moved_set:
                    entry["moved"] = True
                    if _num(old_tof.get(qid)):
                        entry["from"] = old_tof[qid]
                    tof.append(entry)
                elif same:
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
        if unplaced:
            out["tof_unplaced"] = unplaced
        if mw and flux:
            out["note"] = LATENCY_NOTE
        return out
    except (AttributeError, TypeError, ValueError, KeyError):
        return None


def same_number(a, b) -> bool:
    """The build's number comparison (``generator.run_build._same_number``):
    a delay stored as text "20" equals 20."""
    return _rb()._same_number(a, b)


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
            if tof_moved is None:
                for item in summ["tof"]:
                    move = (res.get("tof_moves") or {}).get(item["qubit"])
                    if move and item["moved"]:
                        item.update(move)
            res["output_timing"] = summ
