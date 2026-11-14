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
    }

Each ``Finding`` has category ``core_layout`` and severity ``warning``; its
``values`` carries the machine-readable part (``kind``, controller, FEM, counts,
limit, firm / estimated) so a surface never parses the message text. The
per-FEM finding's ``detail`` holds the fixed detail lines without the fix line,
which depends on the surface; ``fem_detail(fem, fix_line)`` rebuilds it with one.
"""

from __future__ import annotations

from typing import Any

from quam_state_manager.core.query import _follow_port_ref, _parse_port_ref, _resolve_port_dict

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
LINE_PREFIX = "Core layout warning: {message}"
DETAIL_GROUPS = "Largest groups: {groups}."
DETAIL_GROUP_ITEM = "{name} ({cost})"
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
TWO_FEMS_MESSAGE = "Core name '{name}' on two FEMs; sharing needs the same FEM"
BOTH_KEYS_MESSAGE = "{element}: thread ignored, core used"

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
_ELEMENT_KEYS = frozenset(("core", "thread") + _OUTPUT_KEYS + _INPUT_KEYS)
_SKIP_TOP = frozenset(("wiring", "ports", "network", "extras"))


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


def _iter_elements(node: Any, path: tuple, out: list) -> None:
    if isinstance(node, dict):
        if path and _ELEMENT_KEYS & set(node):
            out.append((".".join(path), node))
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


def _resolve_port(root: dict, ref: Any) -> tuple[str, str, str | None, dict] | None:
    """(port_type, controller, fem or None, port dict) for a port reference, else None."""
    if isinstance(ref, (list, tuple)):
        if len(ref) == 3:
            return "", str(ref[0]), str(ref[1]), {}
        if len(ref) == 2:
            return "", str(ref[0]), None, {}
        return None
    if not isinstance(ref, str):
        return None
    final = _follow_port_ref(root, ref) if ref.startswith("#/") else ref
    if not final:
        return None
    parsed = _parse_port_ref(final)
    if parsed:
        ptype, con, fem, _port = parsed
        return ptype, con, fem, _resolve_port_dict(root, final)
    if final.startswith("#/ports/"):  # controller-level port, no FEM (OPX+ / OPX)
        parts = final.split("/")
        if len(parts) == 5:
            return parts[2], parts[3], None, _resolve_port_dict(root, final)
    return None


def _fem_kind(root: dict, con: str, fem: str | None, hint: str = "") -> str:
    if fem is None:
        return "OPX+"
    if hint.startswith("mw_"):
        return "MW"
    if hint.startswith("analog_"):
        return "LF"
    ports = root.get("ports") if isinstance(root.get("ports"), dict) else {}
    for t in ("mw_outputs", "mw_inputs"):
        try:
            if fem in ports[t][con]:
                return "MW"
        except (KeyError, TypeError):
            pass
    return "LF"


def _is_fast(port: dict) -> bool:
    try:
        return float(port.get("sampling_rate") or 0) >= LF_FAST_RATE
    except (TypeError, ValueError, AttributeError):
        return False


def _element_parts(root: dict, elem: dict) -> tuple[dict, int]:
    """Per (con, fem): {"kind", "terms": [(cost, firm, term_label)], "routes"}; and route total."""
    parts: dict[tuple, dict] = {}

    def slot(con, fem, kind):
        return parts.setdefault((con, fem), {"kind": kind, "outputs": [], "inputs": [], "digital": 0})

    for k in _OUTPUT_KEYS:
        r = _resolve_port(root, elem.get(k))
        if r:
            ptype, con, fem, pd = r
            slot(con, fem, _fem_kind(root, con, fem, ptype))["outputs"].append((k, pd))
    for k in _INPUT_KEYS:
        r = _resolve_port(root, elem.get(k))
        if r:
            ptype, con, fem, pd = r
            slot(con, fem, _fem_kind(root, con, fem, ptype))["inputs"].append((k, pd))
    routes = 0
    dig = elem.get("digital_outputs")
    if isinstance(dig, dict):
        for entry in dig.values():
            routes += 1
            ref = entry.get("opx_output") if isinstance(entry, dict) else None
            r = _resolve_port(root, ref)
            if r:
                _ptype, con, fem, _pd = r
                slot(con, fem, _fem_kind(root, con, fem))["digital"] += 1
            else:  # unresolvable digital port: the route still counts, on no FEM
                parts.setdefault(("?", "?"), {"kind": "?", "outputs": [], "inputs": [], "digital": 0})["digital"] += 1

    out: dict[tuple, dict] = {}
    for key, p in parts.items():
        if key == ("?", "?"):
            continue
        kind, terms = p["kind"], []
        n_out = len(p["outputs"])
        fast = any(_is_fast(pd) for _k, pd in p["outputs"] + p["inputs"])
        iq_out = n_out >= 2 or any(k != "opx_output" for k, _ in p["outputs"])
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


def core_budget(state: dict) -> dict:
    """The per-FEM report; see the module docstring. Never raises."""
    report: dict = {"qop_version": None, "fems": [], "names_on_two_fems": [], "both_keys": []}
    if not isinstance(state, dict):
        return report
    try:
        return _core_budget(state, report)
    except Exception:  # never fail on chip shape
        return report


def _core_budget(root: dict, report: dict) -> dict:
    qop = _qop_version(root)
    report["qop_version"] = qop
    elements: list = []
    _iter_elements(root, (), elements)
    fems: dict[tuple, dict] = {}
    name_fems: dict[str, set] = {}

    for path, elem in elements:
        name, _src = effective_core_name(elem)
        c, t = elem.get("core"), elem.get("thread")
        if isinstance(c, str) and c and isinstance(t, str) and t:
            report["both_keys"].append(path)
        parts, _routes = _element_parts(root, elem)
        two = len(parts) > 1
        for (con, fem), p in parts.items():
            f = fems.setdefault((con, fem), {"kind": p["kind"], "named": {}, "own": [], "routes": 0})
            f["routes"] += p["routes"]
            cost, firm, labels = _cost_of(p["terms"], two)
            member = {"element": path, "cost": cost, "firm_cost": firm, "labels": labels,
                      "if": _has_if(elem)}
            if name is not None:
                name_fems.setdefault(name, set()).add((con, fem))
            if name is None or two:
                f["own"].append(member)
            else:
                f["named"].setdefault(name, []).append(member)

    for name, where in sorted(name_fems.items()):
        if len(where) > 1:
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
            limit, limit_est = OPX_PLUS_CORE_LIMIT, True
        core_terms = sorted({lb for g in groups for lb in g["labels"]}, key=ESTIMATED_TERMS.index)
        route_firm = kind in ("MW", "LF") and qop == ROUTE_LIMIT_MEASURED_QOP
        route_terms = [] if route_firm else [TERM_QOP_UNKNOWN if not qop else TERM_QOP_VERSION.format(v=qop)]
        for g in groups:
            g.pop("labels", None)
        report["fems"].append({
            "controller": con, "fem": int(fem) if isinstance(fem, str) and fem.isdigit() else fem,
            "fem_type": kind,
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


def fem_message(fem: dict) -> str:
    return FEM_MESSAGE.format(
        cores=_num(fem["cores"]["count"]), limit=fem["cores"]["limit"],
        ce=ESTIMATED_LABEL if fem["cores"]["estimated"] else "",
        con=fem["controller"], fem=fem["fem"] if fem["fem"] is not None else "-",
        type=fem["fem_type"],
        routes=fem["routes"]["count"], re=ESTIMATED_LABEL if fem["routes"]["estimated"] else "")


def fem_detail(fem: dict, fix_line: str | None = None) -> str:
    """The detail lines in their fixed order; ``fix_line`` is the surface's fix sentence."""
    lines = []
    top = fem["groups"][:3]
    if top:
        lines.append(DETAIL_GROUPS.format(groups=", ".join(
            DETAIL_GROUP_ITEM.format(name=g["core"] or OWN_CORE_NAME, cost=_num(g["cost"])) for g in top)))
    if fix_line:
        lines.append(fix_line)
    lines.append(DETAIL_STATIC)
    if fem["routes"]["count"]:
        lines.append(DETAIL_ROUTES.format(routes=fem["routes"]["count"]))
    if fem["estimated_terms"]:
        lines.append(DETAIL_ESTIMATED.format(terms=", ".join(fem["estimated_terms"])))
    lines.append(DETAIL_OTHER_CAUSE)
    return "\n".join(lines)


def core_budget_findings(state: dict) -> list:
    """Warnings for every (controller, FEM) over its core or route limit, every group over the
    oscillator cap, every core name on two FEMs and every element with both core and thread."""
    from quam_state_manager.core.diagnostics import Finding

    rep = core_budget(state)
    out: list = []
    for f in rep["fems"]:
        loc = f"{f['controller']} / FEM {f['fem']} ({f['fem_type']}-FEM)"
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
                out.append(Finding(
                    severity="warning", category=CATEGORY, location=loc,
                    message=OSCILLATOR_MESSAGE.format(k=g["oscillators"], name=g["core"],
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
    return out
