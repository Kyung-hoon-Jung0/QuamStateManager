"""Static OPX core budget counter over a raw merged state dict.

Plain dicts only: no quam, no qm, no QUA config. The counter never raises on
chip shape and never blocks anything: every over-limit result is a warning.

Entry points
------------
``core_budget(state) -> dict`` is the machine-readable report;
``core_budget_findings(state) -> list[Finding]`` turns it into warnings.

Report shape (``core_budget``)::

    {"qop_version": str | None,
     "fems": [  # one per (controller, FEM); OPX+ / OPX use fem None
       {"controller": "con1", "fem": 1 | None, "fem_type": "MW" | "LF" | "OPX+",
        "cores": {"limit": 8, "count": 10.0, "firm_count": 10.0, "half_cores": 0.5 steps
                  as a float, "estimated": bool, "limit_estimated": bool},
        "routes": {"limit": 10, "count": 4, "estimated": bool},
        "oscillators": {"max_group": int, "limit": 6},
        "estimated_terms": [str, ...],       # fixed vocabulary, see ESTIMATED_TERMS
        "groups": [  # named groups and one entry per null-core element
          {"core": str | None, "elements": [dot paths], "cost": float,
           "firm_cost": float | None, "estimated": bool, "oscillators": int}],
       }],
     "names_on_two_fems": [{"core": str, "fems": [[con, fem], ...]}],
     "both_keys": [dot paths of elements with both core and thread set],
     "unresolved": [{"element": dot path, "field": str, "ref": Any, "reason": str}],
     "error": None | exception class name,   # set when counting itself failed
    }

An element is any dict with a port field (``opx_output*``, ``opx_input*``) or a
non-empty ``digital_outputs``; a missing ``core`` / ``thread`` key is a null core.
Port references are followed through absolute and relative pointers; one that
cannot be read is listed in ``unresolved`` and reported, never dropped.

Each ``Finding`` has category ``core_layout`` and severity ``warning``; its
``values`` carries the machine-readable part (``kind``, controller, FEM, counts,
limit, firm / estimated) so a surface never parses the message text. The
per-FEM finding's ``detail`` holds the fixed detail lines without the fix line,
which depends on the surface; ``fem_detail(fem, fix_line)`` rebuilds it with one.

Not implemented, because no state field identifies them: MW time tagging and the
OPX+ limit on IF elements per controller.
"""

from __future__ import annotations

from typing import Any

from quam_state_manager.core.pointer_resolver import target_path
from quam_state_manager.core.query import _parse_port_ref

# --- limits ---------------------------------------------------------------
# "Limits: MW-FEM 8, LF-FEM 16" cores per FEM.
MW_FEM_CORE_LIMIT = 8
LF_FEM_CORE_LIMIT = 16
# "OPX+ 18 and OPX 10 per controller" (estimated).
OPX_PLUS_CORE_LIMIT = 18
OPX_CORE_LIMIT = 10
# "At most 10 routes per FEM, whatever the core names or digital ports".
ROUTE_LIMIT = 10
# "Oscillator cap per group: 6".
OSCILLATOR_LIMIT = 6
# The route limit is firm only on "OPX1000 with QOP 3.7.3 declared".
ROUTE_LIMIT_MEASURED_QOP = "3.7.3"

# --- costs, in cores ------------------------------------------------------
# MW-FEM: "MWInput 1; readout process 0.5; digital 0.5".
MW_OUTPUT_COST = 1.0
MW_READOUT_PROCESS_COST = 0.5
MW_DIGITAL_COST = 0.5
# LF-FEM: "SingleInput 1 (2 at 2 GSa/s); MixInput (IQ) 2 (4 at 2 GSa/s);
# readout process 1 (2 at 2 GSa/s); digital 1".
LF_SINGLE_COST = 1.0
LF_IQ_COST = 2.0
LF_READOUT_PROCESS_COST = 1.0
LF_DIGITAL_COST = 1.0
LF_FAST_RATE = 2e9  # "an output at 2e9 doubles"
LF_FAST_FACTOR = 2.0
# OPX+: "SingleInput 1, MixInput 2, readout process 1, digital 1".
OPX_PLUS_SINGLE_COST = 1.0
OPX_PLUS_IQ_COST = 2.0
OPX_PLUS_READOUT_PROCESS_COST = 1.0
OPX_PLUS_DIGITAL_COST = 1.0
# "n_proc = 2 for any readout element".
READOUT_PROCESSES = 2

# --- exact warning strings ------------------------------------------------
ESTIMATED_LABEL = " (estimated)"
FEM_MESSAGE = "{cores} / {limit} cores{ce} on {con} FEM {fem} ({type}-FEM); routes {routes} / 10{re}"
CONTROLLER_MESSAGE = "{cores} / {limit} cores{ce} on {con} (OPX+); routes {routes} / 10{re}"
# A controller-level port whose controller type cannot be told: the OPX limit, said as such.
CONTROLLER_TYPE_UNKNOWN = "unknown"
CONTROLLER_PLACE_UNKNOWN = "OPX or OPX+"
CONTROLLER_MESSAGE_UNKNOWN = ("{cores} / {limit} cores{ce} on {con} (controller type unknown: "
                              "OPX limit 10, OPX+ limit 18); routes {routes} / 10{re}")
LINE_PREFIX = "Core layout warning: {message}"
DETAIL_GROUPS = "Largest groups: {groups}."
DETAIL_GROUP_ITEM = "{name} ({cost})"
DETAIL_OWN_ITEM = "{element} (own, {cost})"
OWN_CORE_NAME = "own"
FIX_RECOMMEND = "Fix: press Recommend core layout, or move elements off this FEM."
FIX_REVIEW = "Fix: tick Share one core per qubit (xy + readout), or move elements off this FEM."
DETAIL_STATIC = ("Static worst case: every declared element is counted as if one program played them all; "
                 "measure_accumulated needs more.")
DETAIL_ROUTES = "Digital routes: {routes} / 10; limit measured on QOP 3.7.3; an ADC trace uses one more."
DETAIL_ESTIMATED = "Estimated: {terms}."
DETAIL_OTHER_CAUSE = ("If a program still fails to allocate resources while these counts are within limits, "
                      "another program open on the same cluster may be using cores from the same budget.")
OSCILLATOR_MESSAGE = "{k} oscillators on core {name} on {con} FEM {fem} (limit 6, estimated)"
OSCILLATOR_MESSAGE_CONTROLLER = "{k} oscillators on core {name} on {con} (limit 6, estimated)"
TWO_FEMS_MESSAGE = "Core name '{name}' on two FEMs; sharing needs the same FEM"
BOTH_KEYS_MESSAGE = "{element}: thread ignored, core used"
UNRESOLVED_MESSAGE = "{n} port references could not be read; the core count may be too low"
UNRESOLVED_DETAIL = "Not read: {items}."
UNRESOLVED_MORE = "{items}, and {more} more"
UNRESOLVED_SHOWN = 5
COUNT_FAILED_MESSAGE = "Core layout could not be counted ({exc})"

# Surface sentences (Load check, Chip Status, Review), copied from the spec by script.
FIX_MOVE = "Fix: move elements off this FEM."
RISK_NOTE = ("Known risk: on QOP 3.8.1 (another device), with xy and resonator on one core, a resonator "
             "update_frequency inside a program made later x180 pulses have no effect; the simulator did not "
             "show it; it depended on the program layout. Not tested on QOP 3.7.3; a hardware test is planned.")
PANEL_STILL_OVER = ("Static count still {firm_after} / {limit} on {con} FEM {fem} after the recommendation; "
                    "move elements off that FEM.")
PANEL_ROUTES = ("Core layout does not change digital routes; remove digital outputs or move elements to "
                "another FEM.")
REASON_OWN_UNSET = "own core: no core or thread set"
REASON_OWN_TWO_FEMS = "own core: on different FEMs"
TOTAL_ROW = "Total {cores} / {limit} cores{ce}"
TOTAL_LOW = "; may be too low: {n} port references not read"

REASON_DANGLING = "dangling reference"
REASON_CYCLE = "cyclic reference"
REASON_MALFORMED = "malformed port reference"
REASON_TYPE_UNKNOWN = "port type unknown"

TERM_TWO_FEMS = "element on two FEMs"
TERM_LF_FAST = "LF port at 2 GSa/s"
TERM_OPX_PLUS = "OPX+ costs"
TERM_OPX = "OPX costs"
TERM_MW_TIME_TAGGING = "MW time tagging"
TERM_MW_READOUT = "MW readout with 2 processes"
TERM_QOP_UNKNOWN = "QOP version unknown"
TERM_QOP_VERSION = "QOP version {v}"
ESTIMATED_TERMS = (TERM_TWO_FEMS, TERM_LF_FAST, TERM_OPX_PLUS, TERM_OPX, TERM_MW_TIME_TAGGING,
                   TERM_MW_READOUT, TERM_QOP_UNKNOWN, TERM_QOP_VERSION)

CATEGORY = "core_layout"

_OUTPUT_KEYS = ("opx_output", "opx_output_I", "opx_output_Q")
_INPUT_KEYS = ("opx_input", "opx_input_I", "opx_input_Q")
_PORT_KEYS = frozenset(_OUTPUT_KEYS + _INPUT_KEYS)
_SKIP_TOP = frozenset(("wiring", "ports", "network", "extras"))
_MW_SECTIONS = ("mw_outputs", "mw_inputs")
_LF_SECTIONS = ("analog_outputs", "analog_inputs")
_DIGITAL = "digital"


def _num(x: float) -> str:
    return f"{float(x):g}"


def effective_core_name(element: dict) -> tuple[str | None, str | None]:
    """(name, source key): ``core`` when set, else ``thread``; (None, None) when neither."""
    core = element.get("core")
    if isinstance(core, str) and core != "":
        return core, "core"
    thread = element.get("thread")
    if isinstance(thread, str) and thread != "":
        return thread, "thread"
    return None, None


def _qop_version(state: dict) -> str | None:
    try:
        from quam_state_manager.core.spec_constraints import qop_version_of
        return qop_version_of(state)
    except Exception:
        return None


# A QUA config marks an OPX+ controller with type "opx1" (docs: Introduction/config.md, OPX+ tab).
# No documented value marks the original OPX, and a QUAM state carries no controller type at all.
_OPX_PLUS_CONFIG_TYPE = "opx1"


def _controller_type(root: dict, con: str) -> str | None:
    """``"OPX+"`` when the chip says so (a ``controllers.<con>.type`` of "opx1"), else None
    (unknown: an OPX and an OPX+ look the same in a state)."""
    try:
        t = root["controllers"][con]["type"]
    except (KeyError, TypeError):
        return None
    return "OPX+" if t == _OPX_PLUS_CONFIG_TYPE else None


def _is_element(node: dict) -> bool:
    if _PORT_KEYS & set(node):
        return True
    dig = node.get("digital_outputs")
    return isinstance(dig, dict) and bool(dig)


def _iter_elements(node: Any, path: tuple, out: list) -> None:
    if isinstance(node, dict):
        if path and _is_element(node):
            out.append((path, node))
            return
        for k, v in node.items():
            if not isinstance(k, str) or k.startswith("__") or k == "digital_outputs":
                continue
            if not path and k in _SKIP_TOP:
                continue
            _iter_elements(v, path + (k,), out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _iter_elements(v, path + (str(i),), out)


class _Unresolved(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _get_path(root: Any, path: tuple) -> Any:
    obj = root
    for seg in path:
        if isinstance(obj, dict) and seg in obj:
            obj = obj[seg]
        elif isinstance(obj, list) and seg.isdigit() and int(seg) < len(obj):
            obj = obj[int(seg)]
        else:
            raise _Unresolved(REASON_DANGLING)
    return obj


def _as_int(x: Any) -> int | None:
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, float) and x.is_integer():
        return int(x)
    return None


def _section_for(root: dict, con: str, fem: str, sections: tuple) -> str | None:
    """The first ports section that declares (con, fem)."""
    ports = root.get("ports")
    if not isinstance(ports, dict):
        return None
    for t in sections:
        sec = ports.get(t)
        if isinstance(sec, dict) and isinstance(sec.get(con), dict) and fem in sec[con]:
            return t
    return None


def _fem_kind(root: dict, con: str, fem: str, ptype: str) -> str:
    """MW / LF for an OPX1000 FEM, from the port type or else the state's ports section."""
    if ptype.startswith("mw_"):
        return "MW"
    if ptype.startswith("analog_"):
        return "LF"
    if _section_for(root, con, fem, _MW_SECTIONS):
        return "MW"
    if _section_for(root, con, fem, _LF_SECTIONS):
        return "LF"
    raise _Unresolved(REASON_TYPE_UNKNOWN)


def _port_dict(root: dict, ptype: str, con: str, fem: str | None, port: str) -> dict:
    try:
        node = root["ports"][ptype][con]
        if fem is not None:
            node = node[fem]
        node = node[port]
        return node if isinstance(node, dict) else {}
    except (KeyError, TypeError, IndexError):
        return {}


def _from_sequence(root: dict, ref: Any, field: str) -> tuple[str, str, str | None, dict]:
    """A ``[con, fem, port]`` or ``[con, port]`` reference."""
    if len(ref) not in (2, 3) or not isinstance(ref[0], str) or not ref[0]:
        raise _Unresolved(REASON_MALFORMED)
    nums = [_as_int(x) for x in ref[1:]]
    if any(n is None for n in nums):
        raise _Unresolved(REASON_MALFORMED)
    con = ref[0]
    if len(ref) == 2:
        return "OPX+", con, None, {}
    fem, port = str(nums[0]), str(nums[1])
    if field in _INPUT_KEYS:
        own = ("mw_inputs", "analog_inputs")
    elif field == _DIGITAL:
        own = ("digital_outputs",)
    else:
        own = ("mw_outputs", "analog_outputs")
    kind = _fem_kind(root, con, fem, "")
    ptype = _section_for(root, con, fem, own) or ""
    return kind, con, fem, _port_dict(root, ptype, con, fem, port) if ptype else {}


def _resolve_port(root: dict, holder: tuple, ref: Any, field: str) -> tuple[str, str, str | None, dict]:
    """(kind MW / LF / OPX+, controller, fem or None, port dict); raises _Unresolved.

    ``holder`` is the path of the leaf that stores ``ref``, so relative pointers
    resolve from where they are written; chains are followed until a port or a
    repeat (cycle)."""
    seen: set = set()
    cur, where = ref, holder
    while True:
        if isinstance(cur, (list, tuple)):
            return _from_sequence(root, cur, field)
        if not isinstance(cur, str) or not cur:
            raise _Unresolved(REASON_MALFORMED)
        if not cur.startswith("#"):
            parsed = _parse_port_ref(cur)  # label form, e.g. MW-FEM/1/2
            if not parsed:
                raise _Unresolved(REASON_MALFORMED)
            ptype, con, fem, _port = parsed
            return _fem_kind(root, con, fem, ptype), con, fem, {}
        tp = target_path(root, cur, where)
        if not tp:
            raise _Unresolved(REASON_DANGLING)
        if tp[0] == "ports":
            if len(tp) == 4:  # controller-level port, no FEM (OPX+ / OPX)
                return "OPX+", tp[2], None, _port_dict(root, tp[1], tp[2], None, tp[3])
            if len(tp) == 5:
                ptype, con, fem, port = tp[1], tp[2], tp[3], tp[4]
                return _fem_kind(root, con, fem, ptype), con, fem, _port_dict(root, ptype, con, fem, port)
            raise _Unresolved(REASON_MALFORMED)
        key = tuple(tp)
        if key in seen:
            raise _Unresolved(REASON_CYCLE)
        seen.add(key)
        cur, where = _get_path(root, key), key


def _is_fast(port: dict) -> bool:
    try:
        return float(port.get("sampling_rate") or 0) >= LF_FAST_RATE
    except (TypeError, ValueError, AttributeError):
        return False


def _element_parts(root: dict, path: tuple, elem: dict, unresolved: list) -> tuple[dict, int]:
    """Per (con, fem): {"kind", "terms": [(cost, firm, term_label)], "routes"}; and route total.

    A reference that cannot be read goes to ``unresolved``; a digital route whose
    port cannot be read is counted in the total but charged to no FEM."""
    parts: dict[tuple, dict] = {}
    dotted = ".".join(path)

    def slot(con, fem, kind):
        return parts.setdefault((con, fem), {"kind": kind, "outputs": [], "inputs": [], "digital": 0})

    def resolve(field, label, ref, holder):
        try:
            return _resolve_port(root, holder, ref, field)
        except _Unresolved as exc:
            reason = exc.reason
        except RecursionError:
            reason = REASON_CYCLE
        unresolved.append({"element": dotted, "field": label, "ref": ref, "reason": reason})
        return None

    fast = False  # doubling follows the element's own LF ports
    for keys, bucket in ((_OUTPUT_KEYS, "outputs"), (_INPUT_KEYS, "inputs")):
        for k in keys:
            ref = elem.get(k)
            if ref is None:
                continue
            r = resolve(k, k, ref, path + (k,))
            if r:
                kind, con, fem, pd = r
                slot(con, fem, kind)[bucket].append(k)
                if kind == "LF" and _is_fast(pd):
                    fast = True
    routes = 0
    dig = elem.get("digital_outputs")
    if isinstance(dig, dict):
        for dk, entry in dig.items():
            routes += 1
            label = f"digital_outputs.{dk}.opx_output"
            ref = entry.get("opx_output") if isinstance(entry, dict) else None
            if ref is None:
                unresolved.append({"element": dotted, "field": label, "ref": None, "reason": REASON_DANGLING})
                continue
            r = resolve(_DIGITAL, label, ref, path + ("digital_outputs", str(dk), "opx_output"))
            if r:
                kind, con, fem, _pd = r
                slot(con, fem, kind)["digital"] += 1

    out: dict[tuple, dict] = {}
    for key, p in parts.items():
        kind, terms = p["kind"], []
        n_out = len(p["outputs"])
        iq_out = n_out >= 2 or any(k != "opx_output" for k in p["outputs"])
        if kind == "MW":
            if n_out:
                terms.append((MW_OUTPUT_COST, True, None))
            if p["inputs"]:
                terms.append((MW_READOUT_PROCESS_COST * READOUT_PROCESSES, False, TERM_MW_READOUT))
            if p["digital"]:
                terms.append((MW_DIGITAL_COST, True, None))
        elif kind == "LF":
            f = LF_FAST_FACTOR if fast else 1.0
            firm, label = (False, TERM_LF_FAST) if fast else (True, None)
            if n_out:
                terms.append(((LF_IQ_COST if iq_out else LF_SINGLE_COST) * f, firm, label))
            if p["inputs"]:
                terms.append((LF_READOUT_PROCESS_COST * READOUT_PROCESSES * f, firm, label))
            if p["digital"]:
                terms.append((LF_DIGITAL_COST, firm, label))
        else:  # controller-level ports: OPX+ costs (OPX cannot be told apart in a state)
            if n_out:
                terms.append((OPX_PLUS_IQ_COST if iq_out else OPX_PLUS_SINGLE_COST, False, TERM_OPX_PLUS))
            if p["inputs"]:
                terms.append((OPX_PLUS_READOUT_PROCESS_COST * READOUT_PROCESSES, False, TERM_OPX_PLUS))
            if p["digital"]:
                terms.append((OPX_PLUS_DIGITAL_COST, False, TERM_OPX_PLUS))
        out[key] = {"kind": kind, "terms": terms, "routes": p["digital"]}
    return out, routes


def _cost_of(terms: list, two_fems: bool) -> tuple[float, float | None, list]:
    """(cost, firm cost or None, estimated labels that the cost rests on)."""
    if not terms:
        return 0.0, None, []
    cost = max(t[0] for t in terms)
    firm_vals = [t[0] for t in terms if t[1]]
    firm = max(firm_vals) if firm_vals else None
    if two_fems:
        return cost, None, [TERM_TWO_FEMS]
    threshold = firm if firm is not None else -1.0
    labels = sorted({t[2] for t in terms if not t[1] and t[0] > threshold and t[2]})
    return cost, firm, labels


def _has_if(elem: dict) -> bool:
    return elem.get("intermediate_frequency") is not None


def _empty_report() -> dict:
    return {"qop_version": None, "fems": [], "names_on_two_fems": [], "both_keys": [],
            "unresolved": [], "error": None}


def core_budget(state: dict) -> dict:
    """The per-FEM report; see the module docstring. Never raises; a failure sets ``error``."""
    if not isinstance(state, dict):
        return _empty_report()
    try:
        return _core_budget(state, _empty_report())
    except Exception as exc:  # never fail on chip shape, but say so
        report = _empty_report()
        report["error"] = type(exc).__name__
        return report


def _core_budget(root: dict, report: dict) -> dict:
    qop = _qop_version(root)
    report["qop_version"] = qop
    elements: list = []
    _iter_elements(root, (), elements)
    fems: dict[tuple, dict] = {}
    name_fems: dict[str, dict] = {}  # name -> element -> FEMs

    for tpath, elem in elements:
        path = ".".join(tpath)
        name, _src = effective_core_name(elem)
        c, t = elem.get("core"), elem.get("thread")
        if isinstance(c, str) and c and isinstance(t, str) and t:
            report["both_keys"].append(path)
        parts, _routes = _element_parts(root, tpath, elem, report["unresolved"])
        two = len(parts) > 1
        if name is not None and parts:
            name_fems.setdefault(name, {})[path] = set(parts)
        for (con, fem), p in parts.items():
            f = fems.setdefault((con, fem), {"kind": p["kind"], "named": {}, "own": [], "routes": 0})
            f["routes"] += p["routes"]
            cost, firm, labels = _cost_of(p["terms"], two)
            member = {"element": path, "cost": cost, "firm_cost": firm, "labels": labels,
                      "if": _has_if(elem)}
            if name is None or two:
                f["own"].append(member)
            else:
                f["named"].setdefault(name, []).append(member)

    for name, per_elem in sorted(name_fems.items()):
        where = set().union(*per_elem.values())
        if len(per_elem) > 1 and len(where) > 1:
            report["names_on_two_fems"].append({"core": name, "fems": [list(w) for w in sorted(where, key=str)]})

    for (con, fem), f in sorted(fems.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        kind = f["kind"]
        groups = []
        for name, members in f["named"].items():
            firm_vals = [m["firm_cost"] for m in members if m["firm_cost"] is not None]
            cost = max(m["cost"] for m in members)
            firm = max(firm_vals) if firm_vals else None
            osc = sum(1 for m in members if m["if"]) + (1 if any(not m["if"] for m in members) else 0)
            groups.append({"core": name, "elements": [m["element"] for m in members], "cost": cost,
                           "firm_cost": firm, "estimated": any(m["labels"] for m in members),
                           "labels": sorted({lb for m in members for lb in m["labels"]}), "oscillators": osc})
        for m in f["own"]:
            groups.append({"core": None, "elements": [m["element"]], "cost": m["cost"],
                           "firm_cost": m["firm_cost"], "estimated": bool(m["labels"]),
                           "labels": list(m["labels"]), "oscillators": 1 if m["if"] else 0})
        groups.sort(key=lambda g: (-g["cost"], g["core"] is None, str(g["core"]), g["elements"][0]))
        count = float(sum(g["cost"] for g in groups))
        firm_count = float(sum(g["firm_cost"] for g in groups if g["firm_cost"] is not None))
        if kind == "MW":
            limit, limit_est = MW_FEM_CORE_LIMIT, False
        elif kind == "LF":
            limit, limit_est = LF_FEM_CORE_LIMIT, False
        else:
            # Unknown type: the lower OPX limit, said as such, never the OPX+ one silently.
            ctype = _controller_type(root, con) or CONTROLLER_TYPE_UNKNOWN
            limit = OPX_PLUS_CORE_LIMIT if ctype == "OPX+" else OPX_CORE_LIMIT
            limit_est = True
        core_terms = sorted({lb for g in groups for lb in g["labels"]}, key=ESTIMATED_TERMS.index)
        route_firm = kind in ("MW", "LF") and qop == ROUTE_LIMIT_MEASURED_QOP
        route_terms = [] if route_firm else [TERM_QOP_UNKNOWN if not qop else TERM_QOP_VERSION.format(v=qop)]
        for g in groups:
            g.pop("labels", None)
        entry_type = {} if kind in ("MW", "LF") else {"controller_type": ctype}
        report["fems"].append({
            "controller": con, "fem": int(fem) if isinstance(fem, str) and fem.isdigit() else fem,
            "fem_type": kind, **entry_type,
            "cores": {"limit": limit, "count": count, "firm_count": firm_count,
                      "estimated": bool(core_terms) or limit_est, "limit_estimated": limit_est},
            "routes": {"limit": ROUTE_LIMIT, "count": f["routes"], "estimated": not route_firm},
            "oscillators": {"limit": OSCILLATOR_LIMIT,
                            "max_group": max((g["oscillators"] for g in groups if g["core"]), default=0)},
            "estimated_terms": core_terms + [t for t in route_terms if t not in core_terms],
            "core_estimated_terms": core_terms,
            "groups": groups,
        })
    return report


def _place(fem: dict) -> str:
    """Where a FEM entry sits, as the location text shows it."""
    if fem["fem"] is None:
        if fem.get("controller_type") == "OPX+":
            return f"{fem['controller']} (OPX+)"
        return f"{fem['controller']} ({CONTROLLER_PLACE_UNKNOWN})"
    return f"{fem['controller']} / FEM {fem['fem']} ({fem['fem_type']}-FEM)"


def fem_message(fem: dict) -> str:
    if fem["fem"] is None:
        template = (CONTROLLER_MESSAGE if fem.get("controller_type") == "OPX+"
                    else CONTROLLER_MESSAGE_UNKNOWN)
    else:
        template = FEM_MESSAGE
    return template.format(
        cores=_num(fem["cores"]["count"]), limit=fem["cores"]["limit"],
        ce=ESTIMATED_LABEL if fem["cores"]["estimated"] else "",
        con=fem["controller"], fem=fem["fem"], type=fem["fem_type"],
        routes=fem["routes"]["count"], re=ESTIMATED_LABEL if fem["routes"]["estimated"] else "")


def _group_item(g: dict) -> str:
    if g["core"]:
        return DETAIL_GROUP_ITEM.format(name=g["core"], cost=_num(g["cost"]))
    return DETAIL_OWN_ITEM.format(element=g["elements"][0], cost=_num(g["cost"]))


def fem_detail(fem: dict, fix_line: str | None = None) -> str:
    """The detail lines in their fixed order; ``fix_line`` is the surface's fix sentence."""
    lines = []
    top = fem["groups"][:3]
    if top:
        lines.append(DETAIL_GROUPS.format(groups=", ".join(_group_item(g) for g in top)))
    if fix_line:
        lines.append(fix_line)
    lines.append(DETAIL_STATIC)
    if fem["routes"]["count"]:
        lines.append(DETAIL_ROUTES.format(routes=fem["routes"]["count"]))
    if fem["estimated_terms"]:
        lines.append(DETAIL_ESTIMATED.format(terms=", ".join(fem["estimated_terms"])))
    lines.append(DETAIL_OTHER_CAUSE)
    return "\n".join(lines)


def _unresolved_detail(items: list) -> str:
    names = [f"{u['element']}.{u['field']}" for u in items]
    shown = ", ".join(names[:UNRESOLVED_SHOWN])
    if len(names) > UNRESOLVED_SHOWN:
        shown = UNRESOLVED_MORE.format(items=shown, more=len(names) - UNRESOLVED_SHOWN)
    return UNRESOLVED_DETAIL.format(items=shown)


def _failed(Finding, exc_name: str):
    return Finding(severity="warning", category=CATEGORY, location=CATEGORY,
                   message=COUNT_FAILED_MESSAGE.format(exc=exc_name),
                   values={"kind": "count_failed", "error": exc_name})


def core_budget_findings(state: dict) -> list:
    """Warnings for every (controller, FEM) over its core or route limit, every group over the
    oscillator cap, every core name on two FEMs, every element with both core and thread, and
    the port references that could not be read. A counting failure is one finding, never silence."""
    from quam_state_manager.core.diagnostics import Finding

    try:
        rep = core_budget(state)
        if rep.get("error"):
            return [_failed(Finding, rep["error"])]
        return _findings(Finding, rep)
    except Exception as exc:
        return [_failed(Finding, type(exc).__name__)]


def _findings(Finding, rep: dict) -> list:
    out: list = []
    for f in rep["fems"]:
        loc = _place(f)
        over_cores = f["cores"]["count"] > f["cores"]["limit"]
        over_routes = f["routes"]["count"] > f["routes"]["limit"]
        if over_cores or over_routes:
            out.append(Finding(
                severity="warning", category=CATEGORY, location=loc, message=fem_message(f),
                detail=fem_detail(f), values={"kind": "fem_budget", "over_cores": over_cores,
                                              "over_routes": over_routes, **{k: f[k] for k in (
                                                  "controller", "fem", "fem_type", "cores", "routes",
                                                  "estimated_terms")}}))
        for g in f["groups"]:
            if g["core"] and g["oscillators"] > OSCILLATOR_LIMIT:
                template = OSCILLATOR_MESSAGE_CONTROLLER if f["fem"] is None else OSCILLATOR_MESSAGE
                out.append(Finding(
                    severity="warning", category=CATEGORY, location=loc,
                    message=template.format(k=g["oscillators"], name=g["core"],
                                            con=f["controller"], fem=f["fem"]),
                    values={"kind": "oscillators", "controller": f["controller"], "fem": f["fem"],
                            "core": g["core"], "count": g["oscillators"], "limit": OSCILLATOR_LIMIT,
                            "estimated": True, "elements": list(g["elements"])}))
    for n in rep["names_on_two_fems"]:
        out.append(Finding(severity="warning", category=CATEGORY, location=n["core"],
                           message=TWO_FEMS_MESSAGE.format(name=n["core"]),
                           values={"kind": "name_on_two_fems", **n}))
    for p in rep["both_keys"]:
        out.append(Finding(severity="warning", category=CATEGORY, location=p, jump_path=p,
                           message=BOTH_KEYS_MESSAGE.format(element=p),
                           values={"kind": "both_keys", "element": p}))
    un = rep["unresolved"]
    if un:
        out.append(Finding(severity="warning", category=CATEGORY, location=un[0]["element"],
                           jump_path=un[0]["element"],
                           message=UNRESOLVED_MESSAGE.format(n=len(un)), detail=_unresolved_detail(un),
                           values={"kind": "unresolved_ports", "count": len(un),
                                   "unresolved": [dict(u) for u in un]}))
    return out


def surface_fix_line(fem_finding_values: dict, surface: str) -> str:
    """The fix sentence for a per-FEM finding on one surface (``load_check``,
    ``diagnostics`` or ``review``). Core layout cannot fix a route overage."""
    if not fem_finding_values.get("over_cores"):
        return PANEL_ROUTES
    return FIX_REVIEW if surface == "review" else FIX_MOVE


def surface_findings(state: dict, surface: str) -> list:
    """``core_budget_findings`` with the surface's fix line in each per-FEM detail."""
    out = core_budget_findings(state)
    if not out:
        return out
    rep = core_budget(state)
    by_place = {_place(f): f for f in rep.get("fems") or []}
    for fd in out:
        v = fd.values if isinstance(fd.values, dict) else {}
        if v.get("kind") == "fem_budget" and fd.location in by_place:
            fd.detail = fem_detail(by_place[fd.location], surface_fix_line(v, surface))
    return out


def finding_line(finding) -> dict:
    """One finding as a surface shows it: the warning line plus its detail lines."""
    v = finding.values if isinstance(finding.values, dict) else {}
    return {"kind": v.get("kind"), "location": finding.location, "message": finding.message,
            "line": LINE_PREFIX.format(message=finding.message),
            "detail": [ln for ln in (finding.detail or "").split("\n") if ln]}


def review_table(state: dict) -> dict | None:
    """Rows per FEM for the build review: element, core (or own with the reason),
    source key, controller / FEM, cost, plus one total line per FEM. None when the
    state has no counted port at all, so no empty table is shown."""
    rep = core_budget(state)
    if not rep.get("fems"):
        return None
    elements: list = []
    _iter_elements(state, (), elements)
    source = {".".join(tpath): effective_core_name(elem)[1] for tpath, elem in elements}
    seen: dict = {}
    for f in rep["fems"]:
        for g in f["groups"]:
            for e in g["elements"]:
                seen[e] = seen.get(e, 0) + 1
    n_unresolved = len(rep.get("unresolved") or [])
    fems = []
    for f in rep["fems"]:
        rows = []
        for g in f["groups"]:
            for e in g["elements"]:
                if g["core"]:
                    core = g["core"]
                elif seen.get(e, 0) > 1:
                    core = REASON_OWN_TWO_FEMS
                else:
                    core = REASON_OWN_UNSET
                rows.append({"element": e, "core": core,
                             "shared": bool(g["core"]) and len(g["elements"]) > 1,
                             "source": source.get(e) or "", "place": _place(f),
                             "cost": _num(g["cost"])})
        total = TOTAL_ROW.format(cores=_num(f["cores"]["count"]), limit=f["cores"]["limit"],
                                 ce=ESTIMATED_LABEL if f["cores"]["estimated"] else "")
        if n_unresolved:
            total += TOTAL_LOW.format(n=n_unresolved)
        fems.append({"place": _place(f), "controller": f["controller"], "fem": f["fem"],
                     "fem_type": f["fem_type"], "count": f["cores"]["count"],
                     "limit": f["cores"]["limit"],
                     "over": f["cores"]["count"] > f["cores"]["limit"], "total": total, "rows": rows})
    shared = sum(1 for f in rep["fems"] for g in f["groups"] if g["core"] and len(g["elements"]) > 1)
    return {"fems": fems, "shared_groups": shared}


def review_summary(state: dict) -> dict | None:
    """What the build review shows: the table, the warning lines (Review fix
    sentence) and the risk note when any element shares a core. None when the
    state has no counted port and nothing to report."""
    table = review_table(state)
    findings = [finding_line(f) for f in surface_findings(state, "review")]
    if table is None and not findings:
        return None
    return {"table": table, "findings": findings,
            "risk_note": RISK_NOTE if table and table["shared_groups"] else None}
