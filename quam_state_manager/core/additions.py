"""Generate-Config additions (lab additions, phase 2: pulses; phase 3: macros).

A spec may carry ``spec.additions`` -- objects the official builder does not
write, added to the built chip afterwards::

    {"kind": "pulse", "target": "qubits.q1.xy.operations", "name": "x180_flat",
     "class": "<module>.<Class>", "fields": {...}}
    {"kind": "macro", "target": "qubit_pairs.q1-2.macros", "name": "cz_added",
     "class": "<module>.<Class>", "fields": {"flux_pulse_qubit": "#/qubits/q1/z/operations/p"}}

A pair is named by the builder's own id (``q1-2``, ``run_build._quam_pair_id``).
Pulses are placed before macros, so a macro may point at a pulse added by the
same spec. A macro's pulse references are checked against the built chip:
a pointer must land on a pulse held by the qubit / coupler that field plays on,
and a plain name must exist exactly once on that qubit's channels (quam's
``Qubit.get_pulse``), because a gate that plays a missing name fails only when
a program is compiled, never at load.

The build itself stays the official one. After it saved ``state.json`` into
the output folder, :func:`apply_additions` adds each addition as a JSON pass
here in SM (SM never imports quam), and :func:`apply_and_verify` then asks the
selected env -- a fresh ``Quam.load`` + ``generate_config`` and, for a class
SM does not draw itself, the class's own ``calculate_waveform()``. An addition
that fails is removed ALONE and reported with its reason; the rest of the chip
is never blocked by it (degrade-only).

Every rule below is [derived]: it is a reading of how quam serialises a
dataclass (every key must be a declared field of the class ``__class__``
names, required fields have no default, a ``#`` string is a reference that
must land), checked by the env load, never cited.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from quam_state_manager.core.field_kinds import _norm
from quam_state_manager.core.pointer_path import _walk, pointer_to_abs

logger = logging.getLogger(__name__)

KINDS = ("pulse", "macro")
_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
_TARGET_RE = re.compile(r"^[A-Za-z_][\w\-]*(\.[\w\-]+)+\.operations$")
_MACRO_TARGET_RE = re.compile(r"^(qubits|qubit_pairs)\.[\w\-]+\.macros$")
_MACRO_ROLE = {"qubits": "macro_qubit", "qubit_pairs": "macro_pair"}
_ROLE_WORDS = {"macro_qubit": "a qubit macro", "macro_pair": "a pair macro", "pulse": "a pulse"}
# a plain-``str`` macro field whose NAME says it holds an operation name [derived]
_NAME_FIELD_RE = re.compile(r"(^|_)(pulse|pulse_name|operation|operation_name|op_name)$")
# pulse-reference fields of the CZ gate family, recognised without a manifest
_KNOWN_PULSE_FIELDS = ("flux_pulse_qubit", "flux_pulse_target", "flux_pulse_high",
                       "flux_pulse_low", "coupler_flux_pulse")
_CLASS_RE = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)+$")
_OFFICIAL = ("quam", "quam_builder", "qualang_tools", "qm")

# wizard line type -> the channel attribute the official builder writes
_QUBIT_CHANNEL = {"xy": "drive", "z": "flux", "resonator": "resonator"}
_PAIR_CHANNEL = {"coupler": "coupler"}
_TWPA_CHANNEL = {"pump": "twpa_pump", "pump_": "twpa_pump", "isolation": "twpa_isolation",
                 "isolation_": "twpa_isolation"}


def is_pointer(v: Any) -> bool:
    return isinstance(v, str) and v.startswith("#")


def quam_pair_id(pair) -> str | None:
    """The builder's own key for a spec pair (``["q1", "q2"]`` / ``"q1-q2"`` ->
    ``q1-2``): ``run_build._quam_pair_id`` itself, never a second derivation."""
    from quam_state_manager.core.script_emitter import _run_build
    if isinstance(pair, (list, tuple)) and len(pair) == 2:
        pair = f"{pair[0]}-{pair[1]}"
    try:
        return _run_build()._quam_pair_id(str(pair))
    except ValueError:
        return None


def _pair_ids(spec: dict) -> set[str]:
    return {i for i in (quam_pair_id(p) for p in spec.get("qubit_pairs") or []) if i}


# ---------------------------------------------------------------------------
# structural check (validate_spec)
# ---------------------------------------------------------------------------

def structural_errors(spec: dict) -> list[str]:
    """Shape errors that make ``spec.additions`` unusable as a whole (400)."""
    adds = spec.get("additions")
    if adds is None:
        return []
    if not isinstance(adds, list):
        return ["additions: must be a list"]
    errors = []
    seen = set()
    for i, a in enumerate(adds):
        where = f"additions[{i}]"
        if not isinstance(a, dict):
            errors.append(f"{where}: must be an object")
            continue
        kind = a.get("kind")
        if kind not in KINDS:
            errors.append(f"{where}: unknown kind {kind!r} (expected 'pulse' or 'macro')")
        t, n, c = a.get("target"), a.get("name"), a.get("class")
        if kind == "macro":
            if not isinstance(t, str) or not _MACRO_TARGET_RE.match(t):
                errors.append(f"{where}: a macro target must be qubits.<q>.macros or "
                              f"qubit_pairs.<pair>.macros (got {t!r})")
        elif not isinstance(t, str) or not _TARGET_RE.match(t):
            errors.append(f"{where}: target must be the dot path of a channel's "
                          f"operations dict (got {t!r})")
        if not isinstance(n, str) or not _NAME_RE.match(n):
            errors.append(f"{where}: name must be letters, digits, '_' or '-' (got {n!r})")
        if not isinstance(c, str) or not _CLASS_RE.match(c):
            errors.append(f"{where}: class must be a dotted module.Class path (got {c!r})")
        if not isinstance(a.get("fields", {}), dict):
            errors.append(f"{where}: fields must be an object")
        key = (t, n)
        if isinstance(t, str) and isinstance(n, str):
            if key in seen:
                errors.append(f"{where}: {t}.{n} is added twice")
            seen.add(key)
    return errors


# ---------------------------------------------------------------------------
# per-addition checks
# ---------------------------------------------------------------------------

def spec_channel_reason(spec: dict, target: str) -> str | None:
    """Why the spec cannot have *target*'s channel, or None. Only shapes the
    wizard knows are judged; anything else is left to the built chip."""
    segs = target.split(".")
    lines = [ln for ln in (spec.get("lines") or []) if isinstance(ln, dict)]
    if segs[0] == "qubits" and len(segs) == 3:
        if segs[1] not in {str(x) for x in spec.get("qubits") or []}:
            return f"qubit {segs[1]} is not in this chip"
        return None
    if segs[0] == "qubit_pairs" and len(segs) == 3:
        return _pair_reason(spec, segs[1])
    if segs[0] == "qubits" and len(segs) == 4:
        q, ch = segs[1], segs[2]
        if q not in {str(x) for x in spec.get("qubits") or []}:
            return f"qubit {q} is not in this chip"
        need = _QUBIT_CHANNEL.get(ch)
        if need and not any(ln.get("line") == need and str(ln.get("element")) == q
                            for ln in lines):
            return f"qubit {q} has no {need} line, so it has no {ch} channel"
    elif segs[0] == "qubit_pairs" and len(segs) == 4:
        p, ch = segs[1], segs[2]
        why = _pair_reason(spec, p)
        if why:
            return why
        need = _PAIR_CHANNEL.get(ch)
        if need and not any(ln.get("line") == need and quam_pair_id(ln.get("element")) == p
                            for ln in lines):
            return f"pair {p} has no {need} line"
    elif segs[0] == "twpas" and len(segs) == 4:
        t = segs[1]
        if t not in {str(x) for x in spec.get("twpas") or []}:
            return f"TWPA {t} is not in this chip"
    return None


def _pair_reason(spec: dict, p: str) -> str | None:
    ids = _pair_ids(spec)
    if p in ids:
        return None
    alias = quam_pair_id(p) if "-" in p else None
    if alias and alias != p and alias in ids:
        return f"pair {p} is named {alias} in the built chip (the builder's id)"
    return f"pair {p} is not in this chip"


def _class_entry(manifest: dict | None, qclass: str) -> dict | None:
    if not manifest:
        return None
    c = (manifest.get("classes") or {}).get(qclass)
    return c if isinstance(c, dict) else None


def origin_of(qclass: str, manifest: dict | None) -> str:
    c = _class_entry(manifest, qclass)
    if c and c.get("origin"):
        return c["origin"]
    return "official" if qclass.split(".", 1)[0] in _OFFICIAL else "lab"


def _type_ok(raw: str, kind: str, value: Any) -> bool:
    """A literal value against the field's declared kind. A pointer and an
    unknown kind are always accepted (the env load decides)."""
    if is_pointer(value) or kind == "unknown":
        return True
    num = isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "scalar_or_pointer":
        return num or isinstance(value, str)
    if kind in ("pointer_pulse", "pointer_channel", "pointer_or_value", "inline_object"):
        return isinstance(value, dict) and "__class__" in value or isinstance(value, str)
    if kind in ("dict_of_pointers", "dict_of_objects"):
        return isinstance(value, dict)
    t = _norm(raw or "")
    if t == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "float":
        return num
    if t == "bool":
        return isinstance(value, bool)
    if t == "str":
        return isinstance(value, str)
    if t.split("[")[0] in ("list", "List", "tuple", "Tuple", "Sequence"):
        return isinstance(value, list)
    return True


def manifest_reason(add: dict, manifest: dict | None) -> str | None:
    """Why the env's role manifest refuses *add*, or None. No manifest: None
    (an unknown is never a blocker -- the env build decides)."""
    if not manifest:
        return None
    qclass = add["class"]
    c = _class_entry(manifest, qclass)
    if c is None:
        return (f"class {qclass} is not in this environment (not importable, or not "
                "in a module the environment was asked to look in)")
    want = ("pulse" if add.get("kind", "pulse") == "pulse"
            else _MACRO_ROLE.get(str(add.get("target", "")).split(".")[0], "macro_pair"))
    role = c.get("role")
    if role != want:
        if want == "pulse":
            return f"class {qclass} is a {role!r}, not a pulse"
        got = _ROLE_WORDS.get(role, f"a {role!r}")
        where = "a pair" if want == "macro_pair" else "a qubit"
        return (f"class {qclass} is {got} (role {role}), so it cannot go on {where}; "
                f"{where} takes a {want} class")
    if c.get("abstract"):
        return f"class {qclass} is abstract and cannot be created"
    fields = c.get("fields") or {}
    if not fields:
        return None
    given = add.get("fields") or {}
    unknown = sorted(k for k in given if k not in fields)
    if unknown:
        return (f"{', '.join(unknown)} {'is not a field' if len(unknown) == 1 else 'are not fields'}"
                f" of {qclass.rsplit('.', 1)[-1]} (Quam.load would refuse it)")
    missing = sorted(n for n, f in fields.items()
                     if not f.get("has_default") and n not in given)
    if missing:
        return f"required field{'s' if len(missing) > 1 else ''} missing: {', '.join(missing)}"
    for n, v in given.items():
        f = fields[n]
        if v is None:
            if not (f.get("optional") or f.get("has_default")):
                return f"field {n} cannot be empty"
            continue
        if not _type_ok(f.get("raw") or "", f.get("kind") or "plain", v):
            return (f"field {n}: {json.dumps(v)[:60]} does not fit its type "
                    f"{_norm(f.get('raw') or '?')}")
    return None


def schema_hash(manifest: dict | None, qclass: str) -> str | None:
    """sha256 over the class's field schema (name, raw type, default, kind)."""
    c = _class_entry(manifest, qclass)
    if c is None:
        return None
    f = c.get("fields") or {}
    canon = json.dumps({n: [v.get("raw"), v.get("has_default"), v.get("default_repr"),
                            v.get("kind")] for n, v in sorted(f.items())},
                       sort_keys=True)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def body_for(add: dict) -> dict:
    body = {"__class__": add["class"]}
    for k, v in (add.get("fields") or {}).items():
        body[k] = copy.deepcopy(v)
    return body


def _follow(state: dict, holder: list[str], value: Any, hops: int = 6):
    """(found, path, node) of a pointer, followed through alias chains (each
    hop resolved relative to the node that holds it)."""
    path = None
    for _ in range(hops):
        dest = pointer_to_abs(value, holder)
        if dest is None:
            return False, None, None
        ok, node = _walk(state, dest)
        if not ok:
            return False, dest, None
        path = dest
        if not is_pointer(node):
            return True, path, node
        holder, value = dest, node
    return False, path, None


def _refs(add: dict, manifest: dict | None) -> list[tuple[list[str], Any, str]]:
    """Every reference an addition's fields hold: ``(key path below the
    addition, value, ref-kind)``; ref-kind is ``pulse`` (a pulse-typed field,
    pointer or plain name), ``name`` (a plain-str field naming an operation)
    or ``pointer`` (any other ``#`` string). [derived]"""
    meta = ((_class_entry(manifest, str(add.get("class") or "")) or {}).get("fields") or {})
    out = []
    for k, v in (add.get("fields") or {}).items():
        f = meta.get(k) or {}
        kind = f.get("kind")
        pulse_field = kind == "pointer_pulse" or (not meta and k in _KNOWN_PULSE_FIELDS)
        if add.get("kind") == "macro" and kind == "dict_of_pointers" and isinstance(v, dict):
            for dk, dv in v.items():
                if isinstance(dv, str):
                    out.append(([k, dk], dv, "pulse" if is_pointer(dv) or dv else "pointer"))
            continue
        if isinstance(v, dict):
            continue
        if not isinstance(v, str):
            continue
        if add.get("kind") == "macro" and pulse_field:
            out.append(([k], v, "pulse"))
        elif is_pointer(v):
            out.append(([k], v, "pointer"))
        elif (add.get("kind") == "macro" and _NAME_FIELD_RE.search(k)
              and _norm(f.get("raw") or "str") == "str" and v):
            out.append(([k], v, "name"))
    return out


def _qubit_of(state: dict, holder: list[str], value: Any) -> str | None:
    """The qubit name a pair's ``qubit_control`` / ``qubit_target`` reaches."""
    if not is_pointer(value):
        return None
    ok, path, node = _follow(state, holder, value)
    if ok and path and len(path) == 2 and path[0] == "qubits":
        return path[1]
    return None


def _owners(state: dict, add: dict, field: str, manifest: dict | None) -> list[tuple[str, list[str]]]:
    """Which objects a macro field's pulse plays on: ``[(label, path)]`` where
    *path* is a qubit (``qubits.q``) or a coupler channel. Field names follow
    the CZ gate family [derived: the classes' own ``*_label`` properties --
    CZGate plays ``flux_pulse_qubit`` on ``qubit_pair.moving_qubit``, a two-flux
    gate on the control and ``flux_pulse_target`` on the target,
    ``coupler_flux_pulse`` on the pair's coupler]; any other field may play on
    either qubit or the coupler."""
    segs = str(add["target"]).split(".")
    if segs[0] == "qubits":
        return [(segs[1], ["qubits", segs[1]])]
    pp = segs[:2]
    ok, pair = _walk(state, pp)
    if not ok or not isinstance(pair, dict):
        return []
    roles = {r: _qubit_of(state, pp + ["qubit_" + r], pair.get("qubit_" + r))
             for r in ("control", "target")}
    q = {r: [(f"{n} (the pair's {r})", ["qubits", n])] if n else [] for r, n in roles.items()}
    coupler = [("the pair's coupler", pp + ["coupler"])] if isinstance(pair.get("coupler"), dict) else []
    meta = ((_class_entry(manifest, str(add.get("class") or "")) or {}).get("fields") or {})
    two_flux = "flux_pulse_target" in meta or "flux_pulse_target" in (add.get("fields") or {})
    if field == "flux_pulse_target":
        return q["target"]
    if field == "coupler_flux_pulse":
        return coupler
    if field == "flux_pulse_qubit":
        if two_flux:
            return q["control"]
        moving = pair.get("moving_qubit")
        if moving in ("control", "target"):
            return [(n.replace(f"the pair's {moving}", f"the pair's moving qubit, {moving}"), p)
                    for n, p in q[moving]]
        return q["control"] + q["target"]
    if field in ("flux_pulse_high", "flux_pulse_low"):
        # a both-movers gate plays the high pulse on the qubit with the higher
        # f_01 (ties go to the control) [derived: the class's qubit_high /
        # qubit_low properties]; unknown frequencies leave either qubit open
        f = {r: _f01(state, roles[r]) for r in ("control", "target")}
        if f["control"] is None or f["target"] is None:
            return q["control"] + q["target"]
        high = "control" if f["control"] >= f["target"] else "target"
        pick = high if field == "flux_pulse_high" else ("target" if high == "control" else "control")
        word = "higher" if field == "flux_pulse_high" else "lower"
        return [(n.replace(f"(the pair's {pick})", f"(the pair's {pick}, the {word}-frequency qubit)"), p)
                for n, p in q[pick]]
    return q["control"] + q["target"] + coupler


def _f01(state: dict, qubit: str | None) -> float | None:
    """A qubit's ``f_01`` as a number (followed through a pointer), or None."""
    if not qubit:
        return None
    ok, node = _walk(state, ["qubits", qubit, "f_01"])
    if ok and is_pointer(node):
        ok, _, node = _follow(state, ["qubits", qubit, "f_01"], node)
    if ok and isinstance(node, (int, float)) and not isinstance(node, bool):
        return float(node)
    return None


def _ops_under(state: dict, path: list[str]) -> dict[str, list[str]]:
    """``{op name: [holder paths]}`` over a qubit's channels, or a channel's own operations."""
    ok, node = _walk(state, path)
    if not ok or not isinstance(node, dict):
        return {}
    chans = [(path, node)] if isinstance(node.get("operations"), dict) else [
        (path + [k], v) for k, v in node.items()
        if isinstance(v, dict) and isinstance(v.get("operations"), dict)]
    out: dict[str, list[str]] = {}
    for cp, ch in chans:
        for name in ch["operations"]:
            out.setdefault(name, []).append(".".join(cp + ["operations", name]))
    return out


def _ref_problem(state: dict, path: list[str], add: dict, manifest: dict | None,
                 refused: dict[str, str]) -> str | None:
    """Why one placed addition's references do not hold on *state*, or None.
    *refused* maps a refused addition's dot path to its target.name, so a
    reference to it says so."""
    for keys, v, rk in _refs(add, manifest):
        field = keys[0]
        label = ".".join(keys)
        if is_pointer(v):
            holder = path + keys
            ok, dest, node = _follow(state, holder, v)
            if not ok:
                d = ".".join(dest or [])
                hit = next((n for rp, n in refused.items() if d == rp or d.startswith(rp + ".")), None)
                if hit:
                    return f"field {label}: {v} points at {hit}, an addition that was refused"
                return f"field {label}: {v} points at nothing in the built chip"
            if rk == "pulse":
                if not (isinstance(node, dict) and "__class__" in node
                        and len(dest) >= 2 and dest[-2] == "operations"):
                    return f"field {label}: {v} does not point at a pulse"
                owners = _owners(state, add, field, manifest) if len(keys) == 1 else []
                if owners and not any(dest[:len(op)] == op for _, op in owners):
                    on = " or ".join(n for n, _ in owners)
                    return (f"field {label}: {v} is a pulse on {'.'.join(dest[:-2])}, "
                            f"but this gate plays {field} on {on}")
            continue
        if rk in ("pulse", "name") and len(keys) == 1:
            owners = _owners(state, add, field, manifest)
            if not owners:
                continue
            found = []
            for n, op in owners:
                hits = _ops_under(state, op).get(v, [])
                found.append((n, hits))
            if not any(h for _, h in found):
                on = " or ".join(n for n, _ in owners)
                gone = next((rp for rp in refused
                             if rp.rsplit(".", 1)[-1] == v and any(
                                 rp.startswith(".".join(op) + ".") for _, op in owners)), None)
                if gone:
                    return (f"field {label}: plays {v!r}, an addition on {on} that was refused")
                return (f"field {label}: plays an operation named {v!r}, which does not "
                        f"exist on {on} in the built chip")
            for n, h in found:
                if len(h) > 1 and rk == "pulse":
                    return (f"field {label}: {v!r} is not unique on {n} ({', '.join(h)}); "
                            "quam's get_pulse refuses an ambiguous name")
    return None


# ---------------------------------------------------------------------------
# the JSON pass
# ---------------------------------------------------------------------------

def _entry(i: int, add: dict, manifest: dict | None, reason: str | None = None) -> dict:
    e = {"index": i, "kind": add.get("kind"), "target": add.get("target"),
         "name": add.get("name"), "class": add.get("class"),
         "origin": origin_of(str(add.get("class") or ""), manifest)}
    if reason:
        e["reason"] = reason
    return e


def placement_order(adds: list[dict]) -> list[int]:
    """Indices in the order they are placed: every pulse before any macro
    (a macro may reference a pulse the same spec adds), stable otherwise."""
    return sorted(range(len(adds)), key=lambda i: (adds[i].get("kind") == "macro", i))


def apply_additions(state: dict, spec: dict, manifest: dict | None) -> dict:
    """Add every acceptable addition to *state* IN PLACE.

    Returns ``{"added": [...], "refused": [...], "notes": [...]}``; each entry
    names ``index/target/name/class/origin`` (+ ``reason`` when refused)."""
    adds = [a for a in (spec.get("additions") or []) if isinstance(a, dict)]
    report: dict = {"added": [], "refused": [], "notes": []}
    if adds and not manifest:
        report["notes"].append(
            "The environment's class manifest was not available, so classes and "
            "fields were checked only by the environment's own load.")
    placed: list[tuple[int, dict, list[str]]] = []
    refused: dict[str, str] = {}

    def refuse(i, a, why):
        report["refused"].append(_entry(i, a, manifest, why))
        refused[f"{a['target']}.{a['name']}"] = f"{a['target']}.{a['name']}"

    for i in placement_order(adds):
        a = adds[i]
        reason = (spec_channel_reason(spec, a["target"]) or manifest_reason(a, manifest))
        segs = a["target"].split(".")
        container = None
        if reason is None:
            ok, container = _walk(state, segs)
            if a.get("kind") == "macro" and not ok:
                # quam omits an empty macros dict on save: create it on an existing owner
                pok, owner = _walk(state, segs[:-1])
                if pok and isinstance(owner, dict):
                    container = owner.setdefault(segs[-1], {})
                    ok = True
            word = "a macro" if a.get("kind") == "macro" else "an operation"
            if not ok or not isinstance(container, dict):
                reason = f"{a['target']} does not exist in the built chip"
            elif a["name"] in container:
                reason = f"{a['target']} already has {word} named {a['name']}"
        if reason:
            refuse(i, a, reason)
            continue
        container[a["name"]] = body_for(a)
        placed.append((i, a, segs + [a["name"]]))
    # references may name another addition, so they are judged on the final
    # state; a refusal can strand another's reference, hence the fixpoint
    changed = True
    while changed:
        changed = False
        for item in list(placed):
            i, a, path = item
            why = _ref_problem(state, path, a, manifest, refused)
            if why:
                _walk(state, path[:-1])[1].pop(path[-1], None)
                placed.remove(item)
                refuse(i, a, why)
                changed = True
    placed.sort(key=lambda t: t[0])
    report["added"] = [dict(_entry(i, a, manifest), path=".".join(p)) for i, a, p in placed]
    report["refused"].sort(key=lambda e: e["index"])
    return report


def precheck(spec: dict, manifest: dict | None) -> list[dict]:
    """Before a build: every addition in placement order with what can be
    judged without the built chip (the spec's channels, the env's class
    manifest). ``reason`` = refused for sure; no reason = still to be checked
    against the built chip and in the environment."""
    adds = [a for a in (spec.get("additions") or []) if isinstance(a, dict)]
    out = []
    for i in placement_order(adds):
        a = adds[i]
        why = None
        if isinstance(a.get("target"), str) and isinstance(a.get("class"), str):
            why = spec_channel_reason(spec, a["target"]) or manifest_reason(a, manifest)
        out.append(_entry(i, a, manifest, why))
    return out


def cz_recipe(entry: dict | None) -> list[dict] | None:
    """The flux pulses a pair-macro class plays, read from its own field
    names, or None when it is not a flux CZ gate. Each item is ``{"field",
    "on", "required"}`` with *on* one of ``moving`` (``qubit_pair.moving_qubit``),
    ``control``, ``target``, ``high``, ``low`` (by idle frequency) or
    ``coupler``. [derived: the gate classes' ``*_label`` properties, see
    :func:`_owners`]"""
    if not isinstance(entry, dict) or entry.get("role") != "macro_pair" or entry.get("abstract"):
        return None
    f = entry.get("fields") or {}

    def req(n):
        return not (f.get(n) or {}).get("has_default")
    if "flux_pulse_high" in f and "flux_pulse_low" in f:
        out = [{"field": "flux_pulse_high", "on": "high", "required": req("flux_pulse_high")},
               {"field": "flux_pulse_low", "on": "low", "required": req("flux_pulse_low")}]
    elif "flux_pulse_qubit" in f and "flux_pulse_target" in f:
        out = [{"field": "flux_pulse_qubit", "on": "control", "required": req("flux_pulse_qubit")},
               {"field": "flux_pulse_target", "on": "target", "required": req("flux_pulse_target")}]
    elif "flux_pulse_qubit" in f:
        out = [{"field": "flux_pulse_qubit", "on": "moving", "required": req("flux_pulse_qubit")}]
    else:
        return None
    if "coupler_flux_pulse" in f:
        out.append({"field": "coupler_flux_pulse", "on": "coupler",
                    "required": req("coupler_flux_pulse")})
    return out


def remove(state: dict, path: str) -> None:
    segs = path.split(".")
    ok, parent = _walk(state, segs[:-1])
    if ok and isinstance(parent, dict):
        parent.pop(segs[-1], None)


# ---------------------------------------------------------------------------
# env verification
# ---------------------------------------------------------------------------

def _needs_own_code(qclass: str, manifest: dict | None, kind: str | None = None) -> bool:
    """A lab class always runs its own code; a macro always does too (its
    ``*_label`` properties resolve the pulses it plays -- the only check a
    gate gets before a program is compiled)."""
    from quam_state_manager.core import lab_watch
    return (kind == "macro" or origin_of(qclass, manifest) == "lab"
            or lab_watch.is_lab_class(qclass))


def _write_folder(root: Path, name: str, state: dict, wiring_src: Path) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(state, indent=4), encoding="utf-8")
    shutil.copyfile(wiring_src, d / "wiring.json")
    return d


def apply_and_verify(python_path: str | None, out_dir, spec: dict,
                     manifest: dict | None, *, verify=None, timeout: int = 600) -> dict:
    """The JSON pass + env verification for a build that already wrote
    ``out_dir/state.json``. Rewrites that file with the additions that
    passed. *verify* (tests) replaces :func:`config_generator.run_additions_verify`.
    Never raises: a failure of the check itself is reported, and then no
    addition is kept (an unverified addition is never written)."""
    from quam_state_manager.core import config_generator, safe_io
    out_dir = Path(out_dir)
    verify = verify or config_generator.run_additions_verify
    if not spec.get("additions"):
        return {"added": [], "refused": [], "notes": []}
    try:
        base = json.loads((out_dir / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"added": [], "refused": [], "notes": [f"could not read the built state: {exc}"]}
    state = copy.deepcopy(base)
    report = apply_additions(state, spec, manifest)
    if not report["added"]:
        return report
    if not python_path:
        for e in report["added"]:
            report["refused"].append({**e, "reason": "no environment to verify it in"})
        report["added"] = []
        return report
    work = Path(tempfile.mkdtemp(prefix="quamadd_work_"))
    try:
        wiring = out_dir / "wiring.json"

        def _checks(entries):
            return [e["path"] for e in entries
                    if _needs_own_code(e["class"], manifest, e.get("kind"))]

        def _run(folders: dict[str, tuple[Path, list[str]]]) -> dict:
            res = verify(python_path, {k: {"folder": str(f), "own_code": c}
                                       for k, (f, c) in folders.items()}, timeout=timeout)
            return res

        res = _run({"all": (_write_folder(work, "all", state, wiring), _checks(report["added"]))})
        verdict = (res.get("folders") or {}).get("all") if res.get("ok") else None
        if verdict is None:
            err = res.get("error") or "the environment check did not run"
            for e in report["added"]:
                report["refused"].append({**e, "reason": f"not verified: {err}"})
            report["added"] = []
            return report
        if not verdict.get("ok"):
            # one at a time: base + that pulse alone; then base + the pulses
            # that passed + that macro (a macro needs the pulses it plays)
            adds = [x for x in (spec.get("additions") or []) if isinstance(x, dict)]
            keep: list[dict] = []
            gone: dict[str, str] = {}

            def _graft(st, e):
                segs = e["path"].split(".")
                ok, parent = _walk(st, segs[:-1])
                if not ok:
                    pok, owner = _walk(st, segs[:-2])
                    parent = owner.setdefault(segs[-2], {})
                parent[e["name"]] = copy.deepcopy(_walk(state, segs)[1])

            def _round(entries, extra):
                singles = {}
                for e in entries:
                    st = copy.deepcopy(base)
                    for x in extra + [e]:
                        _graft(st, x)
                    singles[str(e["index"])] = (_write_folder(work, f"one{e['index']}", st, wiring),
                                                _checks([e]))
                res = _run(singles) if singles else {"ok": True, "folders": {}}
                per = (res.get("folders") or {}) if res.get("ok") else {}
                for e in entries:
                    v = per.get(str(e["index"]))
                    if v and v.get("ok"):
                        keep.append(e)
                    else:
                        why = (v or {}).get("error") or res.get("error") or "the check did not run"
                        report["refused"].append({**e, "reason": f"the environment refused it: {why}"})
                        gone[e["path"]] = f"{e['target']}.{e['name']}"

            pulses = [e for e in report["added"] if e.get("kind") != "macro"]
            macros = [e for e in report["added"] if e.get("kind") == "macro"]
            _round(pulses, [])
            for path in gone:
                remove(state, path)
            # a macro whose pulse the env just refused is refused for that reason
            alive = []
            for e in macros:
                why = _ref_problem(state, e["path"].split("."), adds[e["index"]], manifest, gone)
                if why:
                    report["refused"].append({**e, "reason": why})
                    remove(state, e["path"])
                else:
                    alive.append(e)
            _round(alive, list(keep))
            for path in gone:
                remove(state, path)
            keep.sort(key=lambda e: e["index"])
            report["added"] = keep
            if keep:
                res = _run({"final": (_write_folder(work, "final", state, wiring), _checks(keep))})
                v = (res.get("folders") or {}).get("final") if res.get("ok") else None
                if not (v and v.get("ok")):
                    why = (v or {}).get("error") or res.get("error") or "the check did not run"
                    for e in keep:
                        report["refused"].append(
                            {**e, "reason": f"refused together with the others: {why}"})
                        remove(state, e["path"])
                    report["added"] = []
            report["refused"].sort(key=lambda e: e["index"])
        if report["added"]:
            safe_io.atomic_write_json(out_dir / "state.json", state)
            report["verified"] = {"load": True, "generate_config": True,
                                  "own_code": [e["path"] for e in report["added"]
                                               if _needs_own_code(e["class"], manifest,
                                                                  e.get("kind"))]}
        return report
    except Exception as exc:  # noqa: BLE001 -- degrade-only, never fail the build
        logger.exception("additions pass failed")
        for e in report["added"]:
            report["refused"].append({**e, "reason": f"not applied: {type(exc).__name__}: {exc}"})
        report["added"] = []
        return report
    finally:
        shutil.rmtree(work, ignore_errors=True)


def stamp(spec: dict, manifest: dict | None, python_path: str | None) -> list[dict]:
    """``spec.additions`` with the env identity + a per-addition schema hash,
    for the sidecar (so a later re-generate in another env can tell)."""
    versions = (manifest or {}).get("versions") or {}
    out = []
    for a in spec.get("additions") or []:
        if not isinstance(a, dict):
            continue
        b = copy.deepcopy(a)
        b["env"] = {"python": python_path, "versions": versions}
        b["schema_hash"] = schema_hash(manifest, str(a.get("class") or ""))
        out.append(b)
    return out
