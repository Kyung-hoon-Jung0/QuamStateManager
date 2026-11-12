"""Add a qubit or a pair from the chip diagram, in place.

The chip map's "+" stone and its two-stone pair pick land here. Nothing is
rebuilt: a plan names only NEW subtrees (the qubit or pair, its wiring, any
port it needs, and its name in the chip's ``active_*`` lists), and the route
stages them through the modifier under ONE change group, so the working copy
holds them for Review / Apply and one Ctrl+Z takes all of it back. No value
that exists is ever written, which is what keeps every existing leaf
byte-identical (the property a rebuild cannot give; see the decisions in the
study notes).

What the new entity is made of: the chip's OWN structure. A new qubit is a
copy of a qubit on the chosen feedline (its classes, its operations, its
lab-specific fields), re-expressed in the new id by the rebuild's own rename
rule (:func:`regen_merge._rewrite_ids` / :func:`regen_merge._token_renamer`),
with the parts that belong to OTHER entities left out (operations that point
at a pair or at another qubit) and the measured numbers cleared. A new pair
is a copy of an existing pair re-expressed in its two members, together with
the member-side operations the template pair's macros read (and that read
it). Port rules are the shared ones in :mod:`mw_fem` and
:mod:`spec_constraints` (coupled-port band rule, QOP-aware; IF window).

Pure: functions read ``merged`` (the store's merged state+wiring document)
and return data; they never write.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from quam_state_manager.core import mw_fem, qdac, spec_constraints
from quam_state_manager.core.loader import natural_key
from quam_state_manager.core.regen_merge import (
    _MISSING, _hop, _pair_membership, _rewrite_ids, _token_renamer, is_pointer)

_PORT_RE = re.compile(
    r"^#/ports/(mw_outputs|mw_inputs|analog_outputs|analog_inputs|digital_outputs)"
    r"/([^/]+)/(\d+)/(\d+)")
_ID_RE = re.compile(r"^(.*?)(\d+)$")
N_PORTS = 8                      # outputs per MW-FEM and per LF-FEM
DEFAULT_IF_HZ = 100e6            # [derived] used only when the template has no readable IF
# Measured numbers a copy must not inherit: they describe the template's
# physics, not the new qubit's. Cleared to None (or {} for a dict).
_MEASURED_KEYS = frozenset({
    "T1", "T2ramsey", "T2echo", "gate_fidelity", "fidelity", "confusion",
    "confusion_matrix", "gef_confusion_matrix", "gef_centers",
})
_OFFSET_KEYS = frozenset({"joint_offset", "independent_offset", "min_offset",
                          "arbitrary_offset", "decouple_offset", "interaction_offset",
                          "opx_output_offset"})


# ---------------------------------------------------------------- inventory

def _walk_strings(node: Any, out: list) -> None:
    stack = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, dict):
            stack.extend(n.values())
        elif isinstance(n, list):
            if (len(n) == 3 and isinstance(n[0], str)
                    and isinstance(n[1], int) and isinstance(n[2], int)):
                out.append(("list", n[0], n[1], n[2]))
            else:
                stack.extend(n)
        elif isinstance(n, str) and n.startswith("#/ports/"):
            m = _PORT_RE.match(n)
            if m:
                out.append((m.group(1), m.group(2), int(m.group(3)), int(m.group(4))))


def inventory(merged: dict) -> dict:
    """Ports in use, FEM types and the chip's naming, from one walk.

    ``used`` holds ``(kind, con, fem, port)`` for every port something points
    at (a ``[con, fem, port]`` list counts for every kind). ``fems`` maps
    ``(con, fem)`` to ``"mw"`` / ``"lf"`` from the declared ports."""
    refs: list = []
    for top, val in merged.items():
        if top == "ports":
            continue
        _walk_strings(val, refs)
    used = set()
    for r in refs:
        if r[0] == "list":
            for k in ("mw_outputs", "mw_inputs", "analog_outputs", "analog_inputs"):
                used.add((k, str(r[1]), int(r[2]), int(r[3])))
        else:
            used.add((r[0], r[1], r[2], r[3]))
    ports = merged.get("ports") if isinstance(merged.get("ports"), dict) else {}
    fems: dict = {}
    for kind, typ in (("mw_outputs", "mw"), ("mw_inputs", "mw"),
                      ("analog_outputs", "lf"), ("analog_inputs", "lf")):
        for con, fd in (ports.get(kind) or {}).items():
            if isinstance(fd, dict):
                for fem in fd:
                    if str(fem).isdigit():
                        fems.setdefault((str(con), int(fem)), typ)
    return {"used": used, "fems": fems, "ports": ports}


def _port(ports: dict, kind: str, con: str, fem: int, pid: int) -> dict | None:
    v = (((ports.get(kind) or {}).get(con) or {}).get(str(fem)) or {}).get(str(pid))
    return v if isinstance(v, dict) else None


def _resolve(merged: dict, v: Any, path: tuple = ()) -> Any:
    hops = 0
    while is_pointer(v) and hops < 40:
        v, path = _hop(merged, v, path)
        hops += 1
        if v is _MISSING:
            return None
    return v


def _wire_port(merged: dict, qid: str, line: str, end: str = "opx_output"):
    """``(kind, con, fem, port)`` the wiring of ``qid.line`` names, or None."""
    w = (((merged.get("wiring") or {}).get("qubits") or {}).get(qid) or {}).get(line)
    v = w.get(end) if isinstance(w, dict) else None
    if isinstance(v, str):
        m = _PORT_RE.match(v)
        if m:
            return (m.group(1), m.group(2), int(m.group(3)), int(m.group(4)))
    return None


# ------------------------------------------------------------------- naming

def next_qubit_name(qubit_ids, siblings=()) -> str:
    """The next id in the chip's own scheme: the siblings' shared prefix (else
    the most common prefix on the chip) plus one more than its highest number.
    ``qX1..qX5`` on the feedline gives ``qX6``; ``q1..q20`` gives ``q21``."""
    taken = set(qubit_ids)

    def split(i):
        m = _ID_RE.match(str(i))
        return (m.group(1), int(m.group(2))) if m else (str(i), None)

    pref = None
    sib = [split(s) for s in siblings]
    sib_p = {p for p, n in sib if n is not None}
    if len(sib_p) == 1:
        pref = next(iter(sib_p))
    if pref is None:
        counts: dict = {}
        for i in qubit_ids:
            p, n = split(i)
            if n is not None:
                counts[p] = counts.get(p, 0) + 1
        pref = max(sorted(counts), key=lambda p: counts[p]) if counts else "q"
    nums = [n for p, n in (split(i) for i in qubit_ids) if p == pref and n is not None]
    n = (max(nums) + 1) if nums else 1
    while f"{pref}{n}" in taken:
        n += 1
    return f"{pref}{n}"


def pair_name(merged: dict, control: str, target: str) -> str:
    """The new pair's key, spelled like the chip's own pairs: the full form
    ``qX6-qX5`` unless every pair on the chip uses the short form ``q6-5``."""
    pairs = merged.get("qubit_pairs") or {}
    full = short = 0
    for pid, p in pairs.items():
        m = _pair_membership(p, merged)
        if not m:
            continue
        if pid == f"{m[0]}-{m[1]}":
            full += 1
        else:
            short += 1
    if short and not full:
        mt = _ID_RE.match(target)
        mc = _ID_RE.match(control)
        if mt and mc and mt.group(1) == mc.group(1):
            return f"{control}-{mt.group(2)}"
    return f"{control}-{target}"


# ---------------------------------------------------------------- feedlines

def feedlines(merged: dict) -> list[dict]:
    """One entry per readout line: the ports its resonators share and its
    members, in natural order. ``key`` is the readout output port label."""
    out: dict = {}
    for qid in sorted((merged.get("qubits") or {}), key=natural_key):
        p = _wire_port(merged, qid, "rr")
        if p is None:
            continue
        key = f"{p[1]}/{p[2]}/{p[3]}"
        out.setdefault(key, {"key": key, "port": p, "members": []})["members"].append(qid)
    return sorted(out.values(), key=lambda f: natural_key(f["key"]))


def feedline_of(merged: dict, qid: str) -> str | None:
    p = _wire_port(merged, qid, "rr")
    return f"{p[1]}/{p[2]}/{p[3]}" if p else None


def _grid(q: dict):
    g = q.get("grid_location") if isinstance(q, dict) else None
    if isinstance(g, str) and "," in g:
        try:
            a, b = g.split(",", 1)
            return int(a), int(b)
        except ValueError:
            return None
    return None


def propose_grid(merged: dict, members) -> str | None:
    """A free grid cell next to the feedline's last member (else to the right
    of the whole chip); None when the chip places no qubit on a grid."""
    qs = merged.get("qubits") or {}
    taken = {g for g in (_grid(q) for q in qs.values()) if g}
    if not taken:
        return None
    for m in reversed(list(members)):
        g = _grid(qs.get(m) or {})
        if not g:
            continue
        for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)):
            c = (g[0] + dx, g[1] + dy)
            if c not in taken and c[0] >= 0 and c[1] >= 0:
                return f"{c[0]},{c[1]}"
    mx = max(t[0] for t in taken)
    return f"{mx + 1},{min(t[1] for t in taken)}"


# -------------------------------------------------------------------- ports

def _qop(merged):
    return spec_constraints.qop_version_of(merged)


def _band_ok(b1, b2, qop) -> bool:
    if b1 is None or b2 is None:
        return True
    return (b1, b2) in spec_constraints.band_compat(qop)


def drive_candidates(merged: dict, inv: dict, rf: float | None, *,
                     prefer_fem=None, if_hz: float = DEFAULT_IF_HZ,
                     prefer_band=None, limit: int = 12) -> list[dict]:
    """Free MW-FEM outputs for a drive at ``rf``.

    A declared-but-unused port keeps its LO: it qualifies only when ``rf`` is
    in its band and within the IF window of its LO. An undeclared output on
    an MW-FEM gets ``LO = rf - if_hz``, the band its LO falls in, and must
    pass the coupled-port band rule against its declared peer."""
    ports, used, qop = inv["ports"], inv["used"], _qop(merged)
    out = []
    for (con, fem), typ in inv["fems"].items():
        if typ != "mw":
            continue
        for pid in range(1, N_PORTS + 1):
            if ("mw_outputs", con, fem, pid) in used:
                continue
            decl = _port(ports, "mw_outputs", con, fem, pid)
            peer = mw_fem.lo_peer("mw_outputs", pid)
            pdecl = _port(ports, peer[0], con, fem, peer[1]) if peer else None
            pband = pdecl.get("band") if pdecl else None
            if decl is not None:
                lo = decl.get("upconverter_frequency")
                band = decl.get("band")
                if not isinstance(lo, (int, float)):
                    continue
                if rf is not None and (abs(rf - lo) > mw_fem.MW_MAX_ABS_IF_HZ
                                       or not mw_fem.in_band(rf, band)):
                    continue
                why = f"declared, LO {lo / 1e9:.4g} GHz, band {band}"
                new = False
            else:
                if rf is None:
                    lo, band = None, None
                else:
                    lo = round((rf - if_hz) / 1e6) * 1e6
                    bands = [b for b in mw_fem.bands_of(lo) if _band_ok(b, pband, qop)]
                    if not bands:
                        continue
                    # bands overlap: keep the peer's band, else the template's
                    band = next((b for b in (pband, prefer_band) if b in bands), bands[0])
                why = "new port" + (f", LO {lo / 1e9:.4g} GHz, band {band}" if lo else "")
                new = True
            if decl is not None and not _band_ok(band, pband, qop):
                continue
            out.append({"con": con, "fem": fem, "port": pid, "lo": lo, "band": band,
                        "new": new, "label": f"{con} slot {fem} out {pid}", "why": why})
    out.sort(key=lambda c: (0 if (c["con"], c["fem"]) == prefer_fem else 1,
                            0 if not c["new"] else 1, natural_key(c["con"]), c["fem"], c["port"]))
    return out[:limit] if limit else out


def flux_candidates(inv: dict, *, limit: int = 12) -> list[dict]:
    """Free LF-FEM analog outputs."""
    out = []
    for (con, fem), typ in inv["fems"].items():
        if typ != "lf":
            continue
        for pid in range(1, N_PORTS + 1):
            if ("analog_outputs", con, fem, pid) in inv["used"]:
                continue
            decl = _port(inv["ports"], "analog_outputs", con, fem, pid)
            out.append({"con": con, "fem": fem, "port": pid, "new": decl is None,
                        "label": f"{con} slot {fem} out {pid}",
                        "why": "declared" if decl else "new port"})
    out.sort(key=lambda c: (0 if not c["new"] else 1, natural_key(c["con"]), c["fem"], c["port"]))
    return out[:limit] if limit else out


def _new_port(inv: dict, kind: str, con: str, fem: int, pid: int, **sets) -> dict | None:
    """A port object for an undeclared output, shaped like a sibling on the
    same FEM type: its class and structural settings, never its calibration
    (filters, crosstalk and offsets are not carried; delay starts at 0)."""
    sib = None
    neighbours = list((inv["ports"].get(kind) or {}).items())
    neighbours.sort(key=lambda item: item[0] != con)
    for c, fd in neighbours:
        slots = sorted((fd or {}).items(), key=lambda item: str(item[0]) != str(fem))
        for f, pd in slots:
            for p, v in (pd or {}).items():
                if isinstance(v, dict):
                    sib = v
                    break
            if sib is not None:
                break
        if sib is not None:
            break
    if sib is None:
        return None
    port = {}
    for k, v in sib.items():
        if k == "__class__":
            port[k] = v
        elif k == "crosstalk":
            port[k] = {}
        elif "filter" in k or k == "exponential_dc_gain":
            # Distortion correction is calibration, never carried to a new port.
            port[k] = None
        elif kind == "analog_outputs":
            port[k] = copy.deepcopy(v)
        elif isinstance(v, (list, dict)):
            port[k] = None
        else:
            port[k] = v
    port.update({"controller_id": con, "fem_id": fem, "port_id": pid})
    if "delay" in port:
        port["delay"] = 0
    if "offset" in port:
        port["offset"] = None
    if "shareable" in port:
        port["shareable"] = False
    port.update(sets)
    return port


# ------------------------------------------------------------- copy helpers

def _clear_measured(node: Any) -> None:
    if isinstance(node, dict):
        for k in list(node):
            if k in _MEASURED_KEYS or k.startswith("gef_"):
                node[k] = {} if isinstance(node[k], dict) else None
            elif k in _OFFSET_KEYS and isinstance(node[k], (int, float)) \
                    and not isinstance(node[k], bool):
                node[k] = 0
            elif k == "extras" and isinstance(node[k], dict):
                node[k] = {}
            else:
                _clear_measured(node[k])
    elif isinstance(node, list):
        for value in node:
            _clear_measured(value)


def _pointers(node: Any, path: tuple, out: list) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            _pointers(v, path + (str(k),), out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _pointers(v, path + (str(i),), out)
    elif is_pointer(node):
        out.append((path, node))


def _abs_target(pointer: str, path: tuple, root: Any = None) -> tuple:
    """Where *pointer* (stored at *path*) lands: ``pointer_resolver.
    target_path``, the one rule; () when that cannot be told (a mid-path
    ``..`` needs *root*)."""
    from quam_state_manager.core.pointer_resolver import target_path
    t = target_path(root, pointer, tuple(path))
    return () if t is None else tuple(t)


def _foreign(target: tuple, own_q: set, own_p: set) -> bool:
    if len(target) >= 2 and target[0] == "qubit_pairs":
        return target[1] not in own_p
    if len(target) >= 2 and target[0] == "qubits":
        return target[1] not in own_q
    if len(target) >= 3 and target[:2] == ("wiring", "qubits"):
        return target[2] not in own_q
    return False


def _prune_foreign(node: dict, base: tuple, own_q: set, own_p: set) -> list[str]:
    """Drop every operation / macro entry of a copied entity that reads another
    qubit or pair (a template's CZ pulses belong to the template's pairs).
    Returns the dropped dot paths."""
    dropped = []

    def visit(d: dict, path: tuple):
        for k in list(d):
            v = d[k]
            sub = path + (str(k),)
            refs: list = []
            _pointers(v, sub, refs)
            bad = [1 for p, ptr in refs if _foreign(_abs_target(ptr, p), own_q, own_p)]
            if not bad:
                continue
            if path and path[-1] in ("operations", "macros"):
                del d[k]
                dropped.append(".".join(sub))
            elif isinstance(v, dict):
                visit(v, sub)
            else:
                d[k] = None
                dropped.append(".".join(sub))

    visit(node, base)
    return dropped


# -------------------------------------------------------------- qubit plan

def pick_template(merged: dict, members) -> str | None:
    """The feedline member the copy starts from: the last one whose wiring
    has nothing but xy / rr / z (lines this tool places)."""
    wq = ((merged.get("wiring") or {}).get("qubits") or {})
    for m in reversed(list(members)):
        lines = set((wq.get(m) or {}).keys())
        if lines and lines <= {"xy", "rr", "z"} and "rr" in lines:
            return m
    return None


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


# A new resonator on a shared feedline must not sit on an existing one: two
# readout tones that close cannot be told apart. The step is the feedline's
# own median spacing; the floor and the default for a single member are
# [derived] working numbers, not a hardware rule.
READOUT_MIN_SEP_HZ = 20e6
READOUT_DEFAULT_STEP_HZ = 50e6


def readout_port_info(merged: dict, ports: dict, qid: str) -> dict:
    """The feedline readout output's LO, band, and its LO-coupled peer's band,
    as declared on the chip (``None`` where unknown)."""
    rp = _wire_port(merged, qid, "rr")
    decl = (_port(ports, rp[0], rp[1], rp[2], rp[3]) or {}) if rp else {}
    pband = None
    if rp:
        peer = mw_fem.lo_peer(rp[0], rp[3])
        if peer:
            pband = (_port(ports, peer[0], rp[1], rp[2], peer[1]) or {}).get("band")
    return {"lo": _num(decl.get("upconverter_frequency")), "band": decl.get("band"),
            "peer_band": pband, "qop": _qop(merged)}


def readout_band_problem(rf, info: dict | None) -> str | None:
    """Why a readout tone at ``rf`` cannot sit on this readout port (its band,
    or the coupled-port band rule against its peer), else ``None``."""
    if not info:
        return None
    band, pband = info.get("band"), info.get("peer_band")
    if not mw_fem.in_band(rf, band):
        return (f"The readout frequency {rf / 1e9:.4g} GHz is outside the readout "
                f"port's band {band}.")
    if not mw_fem.in_band(rf, pband):
        return (f"The readout frequency {rf / 1e9:.4g} GHz is outside band {pband} of "
                "the port that shares the readout LO.")
    if not _band_ok(band, pband, info.get("qop")):
        return (f"The readout port's band {band} and its LO-coupled peer's band {pband} "
                "break the coupled-port band rule.")
    return None


def propose_readout(existing, lo, template_rf, port_info: dict | None = None):
    """``(rf, note)``: a readout frequency for a new resonator on a feedline
    whose resonators sit at ``existing`` (Hz), inside the feedline LO's IF
    window when ``lo`` is known. The next free slot above the highest
    resonator, else below the lowest, one median spacing away. When no slot
    fits, the template's value stays and ``note`` says so plainly."""
    fs = sorted(float(v) for v in existing if v is not None)
    if not fs:
        return template_rf, None
    gaps = [b - a for a, b in zip(fs, fs[1:]) if b - a > 0]
    step = sorted(gaps)[len(gaps) // 2] if gaps else READOUT_DEFAULT_STEP_HZ
    step = max(step, READOUT_MIN_SEP_HZ)
    win = mw_fem.MW_MAX_ABS_IF_HZ

    def fits(f):
        if lo is not None and abs(f - lo) > win:
            return False
        if readout_band_problem(f, port_info):
            return False
        return all(abs(f - g) >= READOUT_MIN_SEP_HZ for g in fs)

    for cand in (fs[-1] + step, fs[0] - step):
        if fits(cand):
            return round(cand), None
    # a gap wide enough between two neighbours
    for a, b in zip(fs, fs[1:]):
        mid = (a + b) / 2
        if b - a >= 2 * READOUT_MIN_SEP_HZ and fits(mid):
            return round(mid), None
    return template_rf, ("No free readout slot fits this feedline's IF window and band; the value "
                         "starts at the template's resonator, so change it before staging.")


def qubit_defaults(merged: dict, feedline: str | None = None) -> dict:
    """What the add panel opens with: feedline, name, template, grid cell and
    the template's frequencies as a starting point."""
    fls = feedlines(merged)
    fl = next((f for f in fls if f["key"] == feedline), fls[0] if fls else None)
    if fl is None:
        return {"error": "This chip has no readout feedline to join."}
    qs = merged.get("qubits") or {}
    tpl = pick_template(merged, fl["members"])
    if tpl is None:
        return {"error": f"No qubit on feedline {fl['key']} has a plain xy / rr / z "
                         "wiring this tool can copy."}
    t = qs[tpl]
    xy = t.get("xy") or {}
    rr = t.get("resonator") or {}
    drive = _num(_resolve(merged, xy.get("RF_frequency"), ("qubits", tpl, "xy", "RF_frequency")))
    read = _num(_resolve(merged, rr.get("RF_frequency"), ("qubits", tpl, "resonator", "RF_frequency")))
    tp = _wire_port(merged, tpl, "xy")
    if_hz = DEFAULT_IF_HZ
    if tp and drive is not None:
        lo = _num(((_port(merged.get("ports") or {}, tp[0], tp[1], tp[2], tp[3]) or {})
                   .get("upconverter_frequency")))
        if lo is not None and abs(drive - lo) <= mw_fem.MW_MAX_ABS_IF_HZ:
            if_hz = drive - lo
    read_note = None
    if read is not None:
        others = [v for v in (_num(_resolve(merged, ((qs.get(m) or {}).get("resonator") or {})
                                             .get("RF_frequency"),
                                             ("qubits", m, "resonator", "RF_frequency")))
                               for m in fl["members"]) if v is not None]
        info = readout_port_info(merged, merged.get("ports") or {}, tpl)
        read, read_note = propose_readout(others, info["lo"], read, info)
    return {
        "feedline": fl["key"], "feedlines": [f["key"] for f in fls],
        "members": fl["members"], "template": tpl,
        "name": next_qubit_name(list(qs), fl["members"]),
        "grid": propose_grid(merged, fl["members"]),
        "drive_rf": drive, "readout_rf": read, "readout_note": read_note, "if_hz": if_hz,
        "has_flux": isinstance(t.get("z"), dict) and _wire_port(merged, tpl, "z") is not None,
        "fem": (tp[1], tp[2]) if tp else None,
        "band": ((_port(merged.get("ports") or {}, tp[0], tp[1], tp[2], tp[3]) or {}).get("band")
                 if tp else None),
    }


_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def plan_qubit(merged: dict, *, name: str, feedline: str, drive: tuple,
               flux: tuple | None, drive_rf: float, readout_rf: float | None,
               inv: dict | None = None) -> dict:
    """``{"creates": [(dot_path, value)], "sets": [(dot_path, value)],
    "summary": {...}}`` or ``{"error": str}``.

    ``drive`` / ``flux`` are ``(con, fem, port)``. Every write is a CREATE of
    a path that does not exist, except ``active_qubit_names``, which gets the
    new name appended (the list itself is the one existing value touched)."""
    inv = inv or inventory(merged)
    d = qubit_defaults(merged, feedline)
    if d.get("error"):
        return d
    qs = merged.get("qubits") or {}
    if not _NAME_RE.match(name or ""):
        return {"error": "A qubit name starts with a letter and holds letters, digits and _."}
    if name in qs:
        return {"error": f"{name} already exists on this chip."}
    if _num(drive_rf) is None:
        return {"error": "Give the drive frequency in Hz."}
    tpl = d["template"]
    # Refuse custom port-bearing fields before pruning/renaming can hide them.
    # Only these standard endpoints have a defined allocation/sharing policy.
    for base, node in [(("qubits", tpl), qs[tpl]),
                       (("wiring", "qubits", tpl),
                        ((merged.get("wiring") or {}).get("qubits") or {}).get(tpl, {}))]:
        refs = []
        _pointers(node, base, refs)
        for path, ptr in refs:
            target = _abs_target(ptr, path, merged)
            seen = set()
            while target and target not in seen:
                if target[0] == "ports":
                    if path[len(base):] not in {
                            ("xy", "opx_output"), ("z", "opx_output"),
                            ("resonator", "opx_output"), ("resonator", "opx_input"),
                            ("rr", "opx_output"), ("rr", "opx_input")}:
                        return {"error": f"Cannot copy port pointer at {'.'.join(path)}: "
                                         "no allocation rule for this field."}
                    break
                seen.add(target)
                value = merged
                try:
                    for segment in target:
                        value = value[int(segment)] if isinstance(value, list) else value[segment]
                except (KeyError, TypeError, IndexError, ValueError):
                    break
                if not is_pointer(value):
                    break
                target = _abs_target(value, target, merged)
    con, fem, pid = drive[0], int(drive[1]), int(drive[2])
    cands = drive_candidates(merged, inv, drive_rf, if_hz=d["if_hz"],
                             prefer_band=d["band"], limit=10 ** 6)
    pick = next((c for c in cands if (c["con"], c["fem"], c["port"]) == (con, fem, pid)), None)
    if pick is None:
        return {"error": f"{con} slot {fem} out {pid} is not a free MW-FEM output that "
                         "can drive this frequency (in use, wrong FEM type, band or IF window)."}
    if d["has_flux"]:
        if flux is None:
            return {"error": "Pick a flux output: the qubits on this feedline have a flux line."}
        fc = (flux[0], int(flux[1]), int(flux[2]))
        if fc not in {(c["con"], c["fem"], c["port"]) for c in flux_candidates(inv, limit=10 ** 6)}:
            return {"error": f"{fc[0]} slot {fc[1]} out {fc[2]} is not a free LF-FEM output."}

    # the qubit, re-expressed in the new id by the rebuild's rename rule
    ids = list(qs)
    rename = _token_renamer({tpl: name}, ids)
    q = _rewrite_ids({"qubits": {tpl: copy.deepcopy(qs[tpl])}}, {}, rename)["qubits"][name]
    dropped = _prune_foreign(q, ("qubits", name), {name}, set())
    _clear_measured(q)
    q["id"] = name if "id" in q else q.get("id")
    q["f_01"] = drive_rf
    if _num(q.get("anharmonicity")) is not None and "f_12" in q:
        q["f_12"] = drive_rf - q["anharmonicity"]          # [derived] f12 = f01 - anharmonicity
    if "grid_location" in q:
        q["grid_location"] = d["grid"]
    xy = q.get("xy")
    for line in ("xy", "z"):
        channel = q.get(line)
        if isinstance(channel, dict) and "opx_output" in channel:
            channel["opx_output"] = f"#/wiring/qubits/{name}/{line}/opx_output"
    if isinstance(xy, dict) and _num(xy.get("RF_frequency")) is not None:
        xy["RF_frequency"] = drive_rf
    rr = q.get("resonator")
    notes = []
    if isinstance(rr, dict) and _num(readout_rf) is not None:
        if _num(rr.get("RF_frequency")) is not None:
            rr["RF_frequency"] = readout_rf
        if _num(rr.get("f_01")) is not None:
            rr["f_01"] = readout_rf
        fp = _wire_port(merged, tpl, "rr")
        flo = _num((_port(inv["ports"], fp[0], fp[1], fp[2], fp[3]) or {})
                   .get("upconverter_frequency")) if fp else None
        # W3-3: a readout tone on top of another resonator of this feedline
        for m in d["members"]:
            f = _num(_resolve(merged, ((qs.get(m) or {}).get("resonator") or {}).get("RF_frequency"),
                              ("qubits", m, "resonator", "RF_frequency")))
            if f is not None and abs(readout_rf - f) < READOUT_MIN_SEP_HZ:
                notes.append(f"The readout frequency is {abs(readout_rf - f) / 1e6:.1f} MHz from "
                             f"{m}'s resonator on the same feedline; the two tones may not be "
                             "told apart.")
        why = readout_band_problem(readout_rf, readout_port_info(merged, inv["ports"], tpl))
        if why:
            notes.append(why)
        if flo is not None and abs(readout_rf - flo) > mw_fem.MW_MAX_ABS_IF_HZ:
            return {"error": f"The readout frequency is {abs(readout_rf - flo) / 1e6:.0f} MHz "
                             f"from the feedline LO ({flo / 1e9:.4g} GHz); the IF window "
                             f"is {mw_fem.MW_MAX_ABS_IF_HZ / 1e6:.0f} MHz."}

    # wiring: the feedline's readout ports, the picked drive / flux ports
    wt = copy.deepcopy(((merged.get("wiring") or {}).get("qubits") or {})[tpl])
    w = {}
    for line, val in wt.items():
        val = _rewrite_ids(val, {}, rename)
        if line == "xy":
            val = {k: (f"#/ports/mw_outputs/{con}/{fem}/{pid}" if k == "opx_output" else v)
                   for k, v in val.items()}
        elif line == "z" and flux is not None:
            val = {k: (f"#/ports/analog_outputs/{flux[0]}/{int(flux[1])}/{int(flux[2])}"
                       if k == "opx_output" else v) for k, v in val.items()}
        w[line] = val

    creates = [(f"qubits.{name}", q), (f"wiring.qubits.{name}", w)]
    if pick["new"]:
        port = _new_port(inv, "mw_outputs", con, fem, pid,
                         band=pick["band"], upconverter_frequency=pick["lo"])
        if port is None:
            return {"error": "No MW output port on this chip to shape a new one on."}
        creates.append((f"ports.mw_outputs.{con}.{fem}.{pid}", port))
    if flux is not None and d["has_flux"]:
        fcon, ffem, fpid = flux[0], int(flux[1]), int(flux[2])
        if _port(inv["ports"], "analog_outputs", fcon, ffem, fpid) is None:
            port = _new_port(inv, "analog_outputs", fcon, ffem, fpid)
            if port is None:
                return {"error": "No LF output port on this chip to shape a new one on."}
            creates.append((f"ports.analog_outputs.{fcon}.{ffem}.{fpid}", port))
    creates = _with_parents(merged, creates)
    sets = []
    act = merged.get("active_qubit_names")
    if isinstance(act, list):
        sets.append(("active_qubit_names", list(act) + [name]))
    return {
        "creates": creates, "sets": sets,
        "summary": {"kind": "qubit", "name": name, "template": tpl,
                    "feedline": feedline, "grid": d["grid"],
                    "drive": pick["label"], "drive_lo": pick["lo"], "drive_band": pick["band"],
                    "drive_port": [con, fem, pid], "drive_rf": drive_rf,
                    "flux": (f"{flux[0]} slot {int(flux[1])} out {int(flux[2])}"
                             if flux is not None and d["has_flux"] else None),
                    "dropped": dropped, "notes": notes,
                    "new_ports": [p for p, _ in creates if p.startswith("ports.")]},
    }


def _with_parents(merged: dict, creates: list) -> list:
    """A create whose parent dict is missing (a FEM no port was declared on)
    becomes a create of the first missing ancestor holding the subtree."""
    out = []
    planned = copy.deepcopy(merged)
    for path, val in creates:
        keys = path.split(".")
        node: Any = planned
        i = 0
        while i < len(keys) - 1 and isinstance(node, dict) and keys[i] in node:
            node = node[keys[i]]
            i += 1
        if i < len(keys) - 1:
            for k in reversed(keys[i + 1:]):
                val = {k: val}
            path = ".".join(keys[:i + 1])
        # Fold siblings into the first newly-created ancestor. A modifier
        # cannot create the same missing operations/ports dict twice.
        ancestor = next(((p, v) for p, v in out if path.startswith(p + ".")), None)
        if ancestor:
            parent_path, parent_value = ancestor
            tail = path[len(parent_path) + 1:].split(".")
            dest = parent_value
            for key in tail[:-1]:
                dest = dest.setdefault(key, {})
            dest[tail[-1]] = copy.deepcopy(val)
        else:
            out.append((path, copy.deepcopy(val)))
        dest = planned
        for key in path.split(".")[:-1]:
            dest = dest[key]
        dest[path.split(".")[-1]] = copy.deepcopy(val)
    return out


# --------------------------------------------------------------- pair plan

def pick_pair_template(merged: dict, a: str, b: str) -> str | None:
    """An existing pair to copy: one sharing a member with the new pair first
    (its macros already play on the same kind of line), else the first pair.
    QDAC and bias-tee members are outside this door's wiring contract."""
    pairs = merged.get("qubit_pairs") or {}
    ok = []
    for pid in sorted(pairs, key=natural_key):
        p = pairs[pid]
        if not isinstance(p, dict):
            continue
        m = _pair_membership(p, merged)
        if m and not any(qdac.bias_mode(merged["qubits"][q]) in ("qdac", "bias_tee")
                         for q in m):
            ok.append((pid, m))
    for pid, m in ok:
        if a in m or b in m:
            return pid
    return ok[0][0] if ok else None


def plan_pair(merged: dict, control: str, target: str, *, flux=None, inv=None) -> dict:
    qs = merged.get("qubits") or {}
    if control == target or control not in qs or target not in qs:
        return {"error": "Pick two different qubits of this chip."}
    if any(qdac.bias_mode(qs[q]) in ("qdac", "bias_tee") for q in (control, target)):
        return {"error": "Pairs with QDAC or bias-tee members cannot be added here."}
    pairs = merged.get("qubit_pairs")
    if not isinstance(pairs, dict):
        return {"error": "This chip has no qubit_pairs to add to."}
    for pid, p in pairs.items():
        m = _pair_membership(p, merged)
        if m and set(m) == {control, target}:
            return {"error": f"{control} and {target} are already a pair ({pid})."}
    tpid = pick_pair_template(merged, control, target)
    if tpid is None:
        return {"error": "No pair without QDAC or bias-tee members to copy from."}
    tc, tt = _pair_membership(pairs[tpid], merged)
    new_pid = pair_name(merged, control, target)
    if new_pid in pairs:
        return {"error": f"{new_pid} already exists."}
    tok = {tc: control, tt: target}
    rename = _token_renamer(tok, list(qs))
    pmap = {tpid: new_pid}
    pair = _rewrite_ids({"qubit_pairs": {tpid: copy.deepcopy(pairs[tpid])}},
                        pmap, rename)["qubit_pairs"][new_pid]
    if "id" in pair:
        pair["id"] = new_pid
    _clear_measured(pair)
    if isinstance(pair.get("coupler"), dict):
        for key in _OFFSET_KEYS & pair["coupler"].keys():
            pair["coupler"][key] = 0

    # member-side operations the template's macros read, and that read the
    # template pair, re-expressed onto the new members
    creates = [(f"qubit_pairs.{new_pid}", pair)]
    wp = ((merged.get("wiring") or {}).get("qubit_pairs") or {})
    wire = wp.get(tpid)
    has_flux = bool(pair.get("coupler") or wire)
    if has_flux:
        coupler = pair.get("coupler")
        refs = []
        _pointers(wire, ("wiring", "qubit_pairs", tpid), refs)
        outputs = [(path, ptr) for path, ptr in refs if ptr.startswith("#/ports/")]
        output_path = tuple(rename(key) for key in outputs[0][0][3:]) if outputs else ()
        if (not isinstance(coupler, dict) or not isinstance(wire, dict)
                or len(outputs) != 1 or outputs[0][0][-1] != "opx_output"
                or not outputs[0][1].startswith("#/ports/analog_outputs/")
                or coupler.get("opx_output") != "#/" + "/".join(
                    ("wiring", "qubit_pairs", new_pid) + output_path)):
            return {"error": "The template needs a single LF-FEM coupler output with pointer wiring."}
        inv = inv if inv is not None else inventory(merged)
        candidates = flux_candidates(inv, limit=0)
        if flux is None:
            return {"error": "Pick a free LF-FEM coupler output.", "needs_flux": True,
                    "summary": {"name": new_pid, "template": tpid,
                                "control": control, "target": target}}
        pick = next((c for c in candidates if (c["con"], c["fem"], c["port"]) == flux), None)
        if pick is None:
            return {"error": "The coupler port is not a free LF-FEM output."}
        wire = _rewrite_ids(copy.deepcopy(wire), pmap, rename)
        node = wire
        for key in output_path[:-1]:
            node = node[key]
        con, fem, pid = flux
        node["opx_output"] = f"#/ports/analog_outputs/{con}/{fem}/{pid}"
        creates.append((f"wiring.qubit_pairs.{new_pid}", wire))
        if pick["new"]:
            port = _new_port(inv, "analog_outputs", con, fem, pid)
            if port is None:
                return {"error": "No LF output port on this chip to shape a new one on."}
            creates.append((f"ports.analog_outputs.{con}.{fem}.{pid}", port))
    have = set()
    collisions = []

    def exists(path: tuple) -> bool:
        node: Any = merged
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return False
        return True

    def add_copy(src: tuple):
        """Copy the template-side subtree at ``src`` under its renamed path."""
        node: Any = merged
        for p in src:
            node = node[p]
        dst = tuple(rename(p) for p in src)
        if dst in have:
            return
        if exists(dst):
            collisions.append('.'.join(dst))
            return
        have.add(dst)
        wrapped = _rewrite_ids({"x": copy.deepcopy(node)}, pmap, rename)["x"]
        _clear_measured(wrapped)
        creates.append((".".join(dst), wrapped))

    tref: list = []
    _pointers(pairs[tpid], ("qubit_pairs", tpid), tref)
    for path, ptr in tref:
        tgt = _abs_target(ptr, path, merged)
        if len(tgt) >= 4 and tgt[0] == "qubits" and tgt[1] in (tc, tt) \
                and "operations" in tgt:
            i = tgt.index("operations")
            add_copy(tgt[:i + 2])
    for m in (tc, tt):
        ops_roots = []
        for line, ch in (qs[m] or {}).items():
            if isinstance(ch, dict) and isinstance(ch.get("operations"), dict):
                ops_roots.append((line, ch["operations"]))
        for line, ops in ops_roots:
            for op, val in ops.items():
                refs: list = []
                _pointers(val, ("qubits", m, line, "operations", op), refs)
                if any(_abs_target(p, path, merged)[:2] == ("qubit_pairs", tpid) for path, p in refs):
                    add_copy(("qubits", m, line, "operations", op))
    if collisions:
        return {"error": f"Cannot copy member operation {collisions[0]}: "
                         "it already exists and may belong to the template pair."}
    creates = _with_parents(merged, creates)
    sets = []
    act = merged.get("active_qubit_pair_names")
    if isinstance(act, list):
        sets.append(("active_qubit_pair_names", list(act) + [new_pid]))
    return {"creates": creates, "sets": sets,
            "summary": {"kind": "pair", "name": new_pid, "template": tpid,
                        "control": control, "target": target,
                        "flux": flux,
                        "member_ops": [p for p, _ in creates if p.startswith("qubits.")]}}


# ------------------------------------------------------------------ review

def review_rows(plan: dict, limit: int = 400) -> list[dict]:
    """The change, leaf by leaf, for the review panel: only what the plan
    writes (``path``, ``value``), creates first."""
    rows = []

    def leaves(v, path):
        if isinstance(v, dict) and v:
            for k, x in v.items():
                yield from leaves(x, f"{path}.{k}")
        else:
            yield path, v

    for path, val in plan.get("creates", []):
        for p, v in leaves(val, path):
            rows.append({"path": p, "value": v, "op": "new"})
    for path, val in plan.get("sets", []):
        rows.append({"path": path, "value": val[-1:], "op": "append"})
    return rows[:limit] if limit else rows
