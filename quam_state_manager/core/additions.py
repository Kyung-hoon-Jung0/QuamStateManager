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

from quam_state_manager.core import addition_cards
from quam_state_manager.core.field_kinds import _arms, _norm
from quam_state_manager.core.pointer_path import _walk, pointer_to_abs

logger = logging.getLogger(__name__)

KINDS = ("pulse", "macro", "field", "default_gate")
_LIST_HEADS = ("list", "List", "tuple", "Tuple", "Sequence")
_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
_TARGET_RE = re.compile(r"^[A-Za-z_][\w\-]*(\.[\w\-]+)+\.operations$")
_MACRO_TARGET_RE = re.compile(r"^(qubits|qubit_pairs)\.[\w\-]+\.macros$")
_PAIR_MACROS_RE = re.compile(r"^qubit_pairs\.[\w\-]+\.macros$")
_MACRO_ROLE ={"qubits": "macro_qubit", "qubit_pairs": "macro_pair"}
_ROLE_WORDS = {"macro_qubit": "a qubit macro", "macro_pair": "a pair macro", "pulse": "a pulse"}
# a plain-``str`` macro field whose NAME says it holds an operation name [derived]
_NAME_FIELD_RE = re.compile(r"(^|_)(pulse|pulse_name|operation|operation_name|op_name)$")


def _deck() -> dict:
    """The knowledge cards (shipped + this instance's additive overlay)."""
    try:
        from flask import current_app
        return addition_cards.cards(current_app.instance_path)
    except (RuntimeError, ImportError):
        return addition_cards.cards()


def card_of(manifest: dict | None, qclass: str, role: str | None = None) -> dict | None:
    """The knowledge card a manifest class follows (its own, or its base's
    through the MRO), or None."""
    c = _class_entry(manifest, qclass)
    if c is None:
        return None
    return addition_cards.card_for(dict(c, path=qclass), role=role, deck=_deck())
_FIELD_TARGET_RE = re.compile(r"^[A-Za-z_][\w\-]*(\.[\w\-]+)*$")
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
            errors.append(f"{where}: unknown kind {kind!r} (expected 'pulse', 'macro', 'field' "
                          "or 'default_gate')")
        t, n, c = a.get("target"), a.get("name"), a.get("class")
        if kind == "default_gate":
            to = a.get("to")
            if not isinstance(t, str) or not _PAIR_MACROS_RE.match(t):
                errors.append(f"{where}: a default gate's target must be qubit_pairs.<pair>.macros "
                              f"(got {t!r})")
            if not isinstance(n, str) or not _NAME_RE.match(n):
                errors.append(f"{where}: name must be letters, digits, '_' or '-' (got {n!r})")
            if not isinstance(to, str) or not _NAME_RE.match(to) or to == n:
                errors.append(f"{where}: 'to' must name another gate of the pair (got {to!r})")
            if isinstance(t, str):
                if ("default_gate", t) in seen:
                    errors.append(f"{where}: {t} has two default gates")
                seen.add(("default_gate", t))
            continue
        if kind == "field":
            if not isinstance(t, str) or not _FIELD_TARGET_RE.match(t):
                errors.append(f"{where}: a field target must be a dot path (got {t!r})")
            elif any(s.startswith("__") for s in t.split(".")):
                errors.append(f"{where}: a field addition sets a value, never a serialisation "
                              f"key such as __class__ (got {t!r})")
            if "value" not in a:
                errors.append(f"{where}: a field addition needs a value")
            if isinstance(t, str):
                if ("field", t) in seen:
                    errors.append(f"{where}: {t} is set twice")
                seen.add(("field", t))
            continue
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
    if not errors:
        errors.extend(gate_name_errors(spec))
    return errors


# ---------------------------------------------------------------------------
# one convention for a pair's 2Q gates: the Populate pair table and the
# "+ CZ..." additions describe ONE set of gates per pair
# ---------------------------------------------------------------------------

# The alias a "default gate" addition writes: ``macros.cz = "#./<gate>"`` --
# the shape the builder itself writes on a tunable-coupler chip
# (``run_build._seed_coupler_cz``), and the name SM's CZ plan reads first
# (``cz_gate_plan``: ``operation == "cz"``, else the pair's first gate).
DEFAULT_GATE_NAME = "cz"
# What the official CZ nodes play when their ``operation`` parameter is left
# alone (their ``parameters.py`` default). [measured: the CZ node parameters]
NODE_DEFAULT_OPERATION = "cz_unipolar"
_CZ_VARIANTS = ("unipolar", "flattop", "bipolar", "SNZ", "flattop_erf")


def _long(pair) -> str:
    return f"{pair[0]}-{pair[1]}" if isinstance(pair, (list, tuple)) else str(pair)


def builder_gate_names(spec: dict, pair) -> list[str]:
    """The 2Q gate names the official build writes on *pair* (mirror of
    ``run_build._finalize_pair_gates``): ``cz_<variant>`` for every variant
    asked for on a flux CZ chip (a variant whose pulse class the env lacks is
    skipped there with a warning), ``cz_coupler`` + its ``cz`` alias on a
    tunable-coupler chip, ``cr`` (+ ``stark_cz`` with a ZZ line) on a CR chip."""
    gate = str(spec.get("pair_gate") or "").lower()
    if gate == "cz_coupler":
        return ["cz_coupler", DEFAULT_GATE_NAME]
    if gate == "cr":
        zz = any(isinstance(ln, dict) and ln.get("line") in ("zz", "zz_drive")
                 and str(ln.get("element")) == _long(pair) for ln in spec.get("lines") or [])
        return ["cr"] + (["stark_cz"] if zz else [])
    if gate in ("cz_fixed", "cz_tunable"):
        pop = ((spec.get("populate") or {}).get("pairs") or {})
        vals = pop.get(_long(pair)) or pop.get(quam_pair_id(pair) or "") or {}
        req = vals.get("cz_variant") or "all"
        return [f"cz_{v}" for v in (_CZ_VARIANTS if req == "all" else (req,))]
    return []


def pair_gate_table(spec: dict) -> dict[str, dict]:
    """Per pair id: the ``builder`` gate names, the ``added`` macro names, and
    the ``default`` (``{"name", "to", "by"}``; ``by`` is ``"chosen"`` for a
    default-gate addition, ``"builder"`` for the coupler chip's own alias)."""
    adds = [a for a in (spec.get("additions") or []) if isinstance(a, dict)]
    out: dict[str, dict] = {}
    for p in spec.get("qubit_pairs") or []:
        pid = quam_pair_id(p)
        if not pid:
            continue
        tgt = f"qubit_pairs.{pid}.macros"
        b = builder_gate_names(spec, p)
        added = [a.get("name") for a in adds if a.get("kind") == "macro" and a.get("target") == tgt]
        d = next((a for a in adds if a.get("kind") == "default_gate" and a.get("target") == tgt), None)
        if d is not None:
            default = {"name": d.get("name"), "to": d.get("to"), "by": "chosen"}
        elif DEFAULT_GATE_NAME in b:
            default = {"name": DEFAULT_GATE_NAME, "to": b[0], "by": "builder"}
        else:
            default = None
        out[pid] = {"target": tgt, "builder": b, "added": added, "default": default}
    return out


def gate_name_errors(spec: dict) -> list[str]:
    """Two gates of one pair never share a name: an added macro may not take a
    name the builder writes or the default alias, and a default must name a
    gate the pair will have. [derived: a pair's gates are keys of one dict]"""
    errors = []
    for pid, row in pair_gate_table(spec).items():
        d = row["default"]
        chosen = d if d and d.get("by") == "chosen" else None
        for n in row["added"]:
            if n in row["builder"]:
                errors.append(f"pair {pid}: the build already makes a gate named {n}; "
                              "name the added gate differently")
            elif chosen and n == chosen.get("name"):
                errors.append(f"pair {pid}: {n} is the default-gate alias; "
                              "name the added gate differently")
        if chosen:
            names = (set(row["builder"]) | set(row["added"])) - {chosen.get("name")}
            if chosen.get("to") not in names:
                errors.append(f"pair {pid}: the default gate {chosen.get('to')} is not one of "
                              "this pair's gates")
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
        qd = (((spec.get("qdac") or {}).get("qubits")) or {}).get(q)
        if ch == "z" and isinstance(qd, dict) and not qd.get("bias_tee"):
            rule = addition_cards.forbidden_reason(
                addition_cards.card_by_id("qdac_z_refusal", _deck()), "pulse_on_qdac_z") or (
                "its z is a QdacBiasLine (a DC level), which plays no pulses; put the pulse "
                "on xy or the resonator")
            return f"qubit {q} is biased by the QDAC-II: {rule}"
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
        if t not in {str(x.get("id")) if isinstance(x, dict) else str(x) for x in spec.get("twpas") or []}:
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
    arms = [a for a in (_arms(t) or []) if a not in ("None", "NoneType")]
    if len(arms) > 1:
        if all(a.split("[")[0] in _LIST_HEADS for a in arms):
            return isinstance(value, list)
        return True
    if t == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "float":
        return num
    if t == "bool":
        return isinstance(value, bool)
    if t == "str":
        return isinstance(value, str)
    if t.split("[")[0] in _LIST_HEADS:
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
        pulse_field = kind == "pointer_pulse" or (
            not meta and k in addition_cards.all_pointer_fields(_deck()))
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
    # which qubit / channel a field plays on is the class's knowledge card
    # (its own, or its base's by MRO); without a manifest the card is chosen
    # by the field names the addition carries
    entry = _class_entry(manifest, str(add.get("class") or "")) or {}
    names = set(entry.get("fields") or {}) | set(add.get("fields") or {})
    card = addition_cards.card_for(
        dict(entry, role="macro_pair", path=str(add.get("class") or ""),
             fields={n: {} for n in names}), deck=_deck())
    on = addition_cards.pointer_map(card, names).get(field)
    if on == "target":
        return q["target"]
    if on == "coupler":
        return coupler
    if on == "control":
        return q["control"]
    if on == "moving":
        moving = pair.get("moving_qubit")
        if moving in ("control", "target"):
            return [(n.replace(f"the pair's {moving}", f"the pair's moving qubit, {moving}"), p)
                    for n, p in q[moving]]
        return q["control"] + q["target"]
    if on in ("high", "low"):
        # a both-movers gate plays the high pulse on the qubit with the higher
        # f_01 (ties go to the control) [derived: the class's qubit_high /
        # qubit_low properties]; unknown frequencies leave either qubit open
        f = {r: _f01(state, roles[r]) for r in ("control", "target")}
        if f["control"] is None or f["target"] is None:
            return q["control"] + q["target"]
        high = "control" if f["control"] >= f["target"] else "target"
        pick = high if on == "high" else ("target" if high == "control" else "control")
        word = "higher" if on == "high" else "lower"
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


def _qdac_z_class(state: dict, segs: list[str]) -> str | None:
    """The short class name of a qubit's z when it is a QDAC-II bias line."""
    if not (len(segs) == 4 and segs[0] == "qubits" and segs[2] == "z"):
        return None
    ok, z = _walk(state, segs[:3])
    cls = str(z.get("__class__") or "") if ok and isinstance(z, dict) else ""
    return cls.rsplit(".", 1)[-1] if "QdacBias" in cls else None


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
    if add.get("kind") == "field":
        parent, _, last = str(add.get("target") or "").rpartition(".")
        e = {"index": i, "kind": "field", "target": parent, "name": last, "class": None,
             "origin": "field", "value": add.get("value")}
    elif add.get("kind") == "default_gate":
        e = {"index": i, "kind": "default_gate", "target": add.get("target"),
             "name": add.get("name"), "class": None, "origin": "alias", "to": add.get("to")}
    else:
        e = {"index": i, "kind": add.get("kind"), "target": add.get("target"),
             "name": add.get("name"), "class": add.get("class"),
             "origin": origin_of(str(add.get("class") or ""), manifest)}
    if reason:
        e["reason"] = reason
    return e


def placement_order(adds: list[dict]) -> list[int]:
    """Indices in the order they are placed: every pulse before any macro
    (a macro may reference a pulse the same spec adds), stable otherwise."""
    rank = {"pulse": 0, "macro": 1, "default_gate": 2, "field": 3}
    return sorted(range(len(adds)), key=lambda i: (rank.get(adds[i].get("kind"), 0), i))


def _owner_of(state: dict, segs: list[str]) -> tuple[int, Any]:
    """(index, node) of the deepest ancestor of the leaf *segs* that is a
    serialised quam object (a dict naming ``__class__``); the root is index 0."""
    best: tuple[int, Any] = (0, state)
    for k in range(1, len(segs)):
        ok, node = _walk(state, segs[:k])
        if not ok:
            break
        if isinstance(node, dict) and "__class__" in node:
            best = (k, node)
    return best


def field_reason(state: dict, add: dict, manifest: dict | None) -> str | None:
    """Why a ``field`` addition cannot be set on *state*, or None.

    The field must be DECLARED by the class of the object that holds it (quam
    refuses an undeclared key at load), read from the env's manifest. Below a
    declared ``extras`` everything is free-form (``edit_policy.is_free_form_path``).
    A class the manifest does not know is left to the env load. [derived]"""
    segs = str(add["target"]).split(".")
    k, owner = _owner_of(state, segs)
    rest = segs[k:]
    cls = owner.get("__class__") if isinstance(owner, dict) else None
    where = ".".join(segs[:k]) or "the root"
    c = _class_entry(manifest, str(cls)) if cls else None
    fields = (c or {}).get("fields") or {}
    if rest[0] != "extras" and len(rest) > 1:
        return (f"{'.'.join(segs[:k + 1])} is not an object with fields; a field addition "
                "sets one declared field (use a pulse or macro addition for an entry)")
    if c is not None and fields and rest[0] not in fields:
        return (f"{rest[0]} is not a field of {str(cls).rsplit('.', 1)[-1]} ({where}) in this "
                "environment, so Quam.load would refuse it")
    if rest[0] == "extras":
        if len(rest) < 2:
            return "extras itself is a block; name a key under it (extras.<key>)"
        ok, ex = _walk(state, segs[:k + 1])
        if ok and not isinstance(ex, dict):
            return f"{'.'.join(segs[:k + 1])} is not a block"
        return None
    ok, cur = _walk(state, segs)
    if ok and isinstance(cur, dict) and "__class__" in cur:
        return f"{add['target']} holds an object; a field addition sets a value, not an object"
    f = fields.get(rest[0])
    v = add.get("value")
    if f is not None:
        if v is None and not (f.get("optional") or f.get("has_default")):
            return f"field {rest[0]} cannot be empty"
        if v is not None and not _type_ok(f.get("raw") or "", f.get("kind") or "plain", v):
            return (f"field {rest[0]}: {json.dumps(v)[:60]} does not fit its type "
                    f"{_norm(f.get('raw') or '?')}")
    return None


def _set_field(state: dict, add: dict) -> dict:
    """Set a field addition's value; returns how to undo it."""
    segs = str(add["target"]).split(".")
    node = state
    for s_ in segs[:-1]:
        if not isinstance(node.get(s_), dict):
            node[s_] = {}
        node = node[s_]
    undo = {"had": segs[-1] in node, "old": copy.deepcopy(node.get(segs[-1]))}
    node[segs[-1]] = copy.deepcopy(add.get("value"))
    return undo


def _unset_field(state: dict, path: str, undo: dict | None) -> None:
    segs = path.split(".")
    ok, parent = _walk(state, segs[:-1])
    if not (ok and isinstance(parent, dict)):
        return
    if (undo or {}).get("had"):
        parent[segs[-1]] = copy.deepcopy(undo.get("old"))
    else:
        parent.pop(segs[-1], None)


def default_gate_reason(state: dict, add: dict, refused: dict[str, str] | None = None) -> str | None:
    """Why a default-gate alias cannot be written on *state*, or None. The
    alias replaces only another alias (the coupler chip's own ``cz``), never a
    gate object. [derived]"""
    t, n, to = add["target"], add["name"], add["to"]
    gone = f"{t}.{to}"
    if refused and gone in refused:
        return f"the default gate {to} is an addition that was refused"
    ok, macros = _walk(state, t.split("."))
    if not (ok and isinstance(macros, dict)):
        return f"{t} does not exist in the built chip"
    if to not in macros:
        return f"{t} has no gate named {to} in the built chip"
    cur = macros.get(n)
    if n in macros and not is_pointer(cur):
        return (f"{t} already has a gate object named {n}; the default is an alias "
                "and never replaces a gate")
    return None


def _set_alias(state: dict, add: dict) -> dict:
    _, macros = _walk(state, add["target"].split("."))
    undo = {"had": add["name"] in macros, "old": copy.deepcopy(macros.get(add["name"]))}
    macros[add["name"]] = f"#./{add['to']}"
    return undo


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

    fields_set: list[tuple[int, dict]] = []

    def refuse(i, a, why):
        report["refused"].append(_entry(i, a, manifest, why))
        if a.get("kind") != "field":
            refused[f"{a['target']}.{a['name']}"] = f"{a['target']}.{a['name']}"

    for i in placement_order(adds):
        a = adds[i]
        if a.get("kind") in ("field", "default_gate"):
            continue      # set after the pulses and macros it may name
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
            zcls = _qdac_z_class(state, segs)
            if not ok and zcls:
                reason = (f"qubit {segs[1]}'s z is a {zcls} (its QDAC-II DC bias), which "
                          "plays no pulses; put the pulse on xy or the resonator")
            elif not ok or not isinstance(container, dict):
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
    for i in placement_order(adds):
        a = adds[i]
        if a.get("kind") != "default_gate":
            continue
        why = default_gate_reason(state, a, refused)
        if why:
            refuse(i, a, why)
            continue
        report["added"].append(dict(_entry(i, a, manifest), path=f"{a['target']}.{a['name']}",
                                    undo=_set_alias(state, a)))
    for i in placement_order(adds):
        a = adds[i]
        if a.get("kind") != "field":
            continue
        why = field_reason(state, a, manifest)
        if why:
            refuse(i, a, why)
            continue
        e = dict(_entry(i, a, manifest), path=a["target"], undo=_set_field(state, a))
        report["added"].append(e)
    report["added"].sort(key=lambda e: e["index"])
    report["refused"].sort(key=lambda e: e["index"])
    return report


def _field_precheck(spec: dict, add: dict, manifest: dict | None) -> str | None:
    """A ROOT field (or ``extras.*``) judged before the build, when the spec
    names its root class; anything deeper waits for the built chip."""
    root = spec.get("quam_class")
    segs = str(add["target"]).split(".")
    if not (root and manifest) or not (len(segs) == 1 or segs[0] == "extras"):
        return None
    return field_reason({"__class__": root}, add, manifest)


def _core_reason(spec: dict, a: dict, manifest: dict | None) -> str | None:
    """What can be judged without the built chip (the spec's channels, the
    env's class manifest)."""
    if a.get("kind") == "field" and isinstance(a.get("target"), str):
        return _field_precheck(spec, a, manifest)
    if isinstance(a.get("target"), str) and isinstance(a.get("class"), str):
        return spec_channel_reason(spec, a["target"]) or manifest_reason(a, manifest)
    return None


def precheck(spec: dict, manifest: dict | None) -> list[dict]:
    """Before a build: every addition in placement order with what can be
    judged without the built chip. ``reason`` = refused for sure; no reason =
    still to be checked against the built chip and in the environment. Each
    entry also carries the knowledge ``card`` its class follows (None = no
    card) and the card's ``warnings`` -- the same :func:`lint_proposal` an
    agent's proposal passes."""
    adds = [a for a in (spec.get("additions") or []) if isinstance(a, dict)]
    out = []
    for i in placement_order(adds):
        a = adds[i]
        lint = lint_proposal(spec, a, manifest, structural=False, others=adds[:i] + adds[i + 1:])
        e = _entry(i, a, manifest, lint["errors"][0] if lint["errors"] else None)
        e["card"] = lint["card"]
        if lint["warnings"]:
            e["warnings"] = lint["warnings"]
        out.append(e)
    return out


# ---------------------------------------------------------------------------
# ONE lint for every addition, whoever wrote it (the wizard, an agent, an
# imported spec): structure + the knowledge card + what the env manifest and
# the spec can judge. An agent author is held to one more rule: it may never
# set a calibration value (docs/47 -- an agent proposes structure, numbers
# come from a person or a calibration).
# ---------------------------------------------------------------------------

AUTHORS_PERSON = ("person", "wizard", "import")


_AGENT_FIELD_ROOTS_REFUSED = ("wiring", "ports", "network")


def _as_number(v: Any) -> Any:
    """A numeric string as its number (``"0.5"`` -> 0.5), else None."""
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _num_like(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, list) and v:
        return all(_num_like(x) or isinstance(x, list) and all(_num_like(y) for y in x) for x in v)
    return False


def _default_equals(f: dict, v: Any) -> bool:
    """Does *v* only restate the class default (``default_repr``)?"""
    if not f.get("has_default"):
        return False
    try:
        return repr(v) == str(f.get("default_repr")) or json.loads(str(f.get("default_repr"))) == v
    except ValueError:
        return False


def _placeholder(f: dict) -> Any:
    t = _norm(f.get("raw") or "")
    return [] if t.split("[")[0] in _LIST_HEADS else 0


def lint_proposal(spec: dict, add: Any, manifest: dict | None, *, author: str = "person",
                  typed=(), structural: bool = True, others: list | None = None) -> dict:
    """Judge one addition. Returns ``{"ok", "errors", "warnings", "card",
    "needs_person"}``: *errors* refuse it, *warnings* are the card's
    conventions it departs from (a person decides), *needs_person* the
    required calibration fields an agent left for a person to type.
    *typed* = field names a person typed (never refused as an agent number)."""
    errors: list[str] = []
    warnings: list[str] = []
    needs: list[str] = []
    if not isinstance(add, dict):
        return {"ok": False, "errors": ["an addition must be an object"], "warnings": [],
                "card": None, "needs_person": []}
    others = [a for a in (spec.get("additions") or []) if isinstance(a, dict)] if others is None else others
    if structural:
        before = set(structural_errors(dict(spec, additions=others)))
        after = structural_errors(dict(spec, additions=list(others) + [add]))
        n = len(others)
        errors += [e.replace(f"additions[{n}]", "this addition") for e in after if e not in before]
        if errors:
            return {"ok": False, "errors": errors, "warnings": [], "card": None, "needs_person": []}
    kind = add.get("kind")
    qclass = str(add.get("class") or "")
    entry = _class_entry(manifest, qclass) if qclass else None
    meta = (entry or {}).get("fields") or {}
    card = None
    if kind in ("pulse", "macro") and qclass:
        want = ("pulse" if kind == "pulse"
                else _MACRO_ROLE.get(str(add.get("target", "")).split(".")[0], "macro_pair"))
        card = card_of(manifest, qclass, role=want)
        if card is None and entry is None:
            # no manifest entry: the card follows the field names the addition carries
            card = addition_cards.card_for(
                {"path": qclass, "fields": dict.fromkeys(add.get("fields") or {}, {})},
                role=want, deck=_deck())
    agent = author not in AUTHORS_PERSON
    typed = set(typed or ())
    judged = add
    if agent:
        fields = add.get("fields") or {}
        for k, v in fields.items():
            if k in typed or not addition_cards.calibration_field(k, card, _deck()):
                continue
            if is_pointer(v) and str((meta.get(k) or {}).get("kind") or "").startswith("pointer"):
                continue      # a pointer FIELD (flux_pulse_high, ...) names a pulse, not a number
            if is_pointer(v):
                # a pointer in a calibration field borrows another object's number
                errors.append(f"field {k} is a calibration value; an agent may not point it at "
                              f"another value ({v[:60]}) -- leave it out or let a person type it")
            elif _num_like(v) and not _default_equals(meta.get(k) or {}, v):
                errors.append(f"field {k} is a calibration value ({json.dumps(v)[:40]}); an agent "
                              "may not set it -- leave it out (the class default applies) or let a "
                              "person type it")
        if kind == "field":
            tgt = str(add.get("target") or "")
            segs = tgt.split(".")
            leaf = segs[-1]
            val = add.get("value")
            if segs[0] in _AGENT_FIELD_ROOTS_REFUSED:
                errors.append(f"{segs[0]} is the instrument wiring; an agent may not set a "
                              "field there -- a person edits it")
            elif leaf not in typed and segs[0] != "extras" and (
                    addition_cards.calibration_field(leaf, None, _deck())
                    or _num_like(val) or _num_like(_as_number(val))):
                # a calibration leaf in ANY spelling (number, numeric text, a
                # pointer to another value), or any number outside extras
                errors.append(f"{leaf} is a calibration value; an agent may not set it")
        needs = sorted(n for n, f in meta.items()
                       if not f.get("has_default") and n not in fields and n not in typed
                       and addition_cards.calibration_field(n, card, _deck()))
        if needs:
            judged = dict(add, fields=dict(fields, **{n: _placeholder(meta[n]) for n in needs}))
    why = _core_reason(spec, judged, manifest)
    if why:
        errors.append(why)
    if card is not None:
        tgt = str(add.get("target") or "")
        if not addition_cards.placement_ok(card, tgt):
            warnings.append(f"the {card['id']} card places this on "
                            f"{card['placement']['target']}, not {tgt}")
        nm, naming = str(add.get("name") or ""), card.get("naming") or {}
        if naming.get("exact") and nm != naming["exact"]:
            warnings.append(f"the {card['id']} card names it {naming['exact']}")
        elif naming.get("prefix") and not nm.startswith(naming["prefix"]):
            warnings.append(f"the {card['id']} card names it {naming['prefix']}<name>")
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "card": card.get("id") if card else None, "needs_person": needs}


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
    card = addition_cards.card_for(entry, deck=_deck())
    if not (card and card.get("group_form")):
        return None

    def req(n):
        return not (f.get(n) or {}).get("has_default")
    out = [{"field": n, "on": on, "required": req(n)}
           for n, on in addition_cards.pointer_map(card, f).items()]
    return out or None


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
            return [e["path"] for e in entries if e.get("kind") not in ("field", "default_gate")
                    and _needs_own_code(e["class"], manifest, e.get("kind"))]

        by_path = {e["path"]: e for e in report["added"]}

        def _drop(path):
            e = by_path.get(path)
            if e is not None and e.get("kind") in ("field", "default_gate"):
                _unset_field(state, path, e.get("undo"))
            else:
                remove(state, path)

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

            pulses = [e for e in report["added"] if e.get("kind") not in ("macro", "default_gate")]
            macros = [e for e in report["added"] if e.get("kind") == "macro"]
            _round(pulses, [])
            for path in gone:
                _drop(path)
            # a macro whose pulse the env just refused is refused for that reason
            alive = []
            for e in macros:
                why = _ref_problem(state, e["path"].split("."), adds[e["index"]], manifest, gone)
                if why:
                    report["refused"].append({**e, "reason": why})
                    _drop(e["path"])
                else:
                    alive.append(e)
            _round(alive, list(keep))
            for path in gone:
                _drop(path)
            # a default alias needs the gate it names (base, an addition kept)
            aliases = []
            for e in report["added"]:
                if e.get("kind") != "default_gate":
                    continue
                a = adds[e["index"]]
                if f"{a['target']}.{a['to']}" in gone:
                    report["refused"].append(
                        {**e, "reason": f"the default gate {a['to']} is an addition that was refused"})
                    _drop(e["path"])
                else:
                    aliases.append(e)
            _round(aliases, list(keep))
            for path in gone:
                _drop(path)
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
                        _drop(e["path"])
                    report["added"] = []
            report["refused"].sort(key=lambda e: e["index"])
        for e in report["added"] + report["refused"]:
            e.pop("undo", None)
        if report["added"]:
            safe_io.atomic_write_json(out_dir / "state.json", state)
            report["verified"] = {"load": True, "generate_config": True,
                                  "own_code": _checks(report["added"])}
        return report
    except Exception as exc:  # noqa: BLE001 -- degrade-only, never fail the build
        logger.exception("additions pass failed")
        for e in report["added"]:
            report["refused"].append({**e, "reason": f"not applied: {type(exc).__name__}: {exc}"})
        report["added"] = []
        return report
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------
# the editable recipe (script_emitter / regen_script share this one block)
# ---------------------------------------------------------------------------

_RECIPE_KEYS = ("kind", "target", "name", "class", "fields", "to", "value")


def kept_additions(spec: dict, report: dict | None) -> list[dict]:
    """The additions a build KEPT (``report["added"]``), in spec order and
    without the sidecar stamps. ``report`` None = every addition (no build)."""
    adds = [a for a in (spec.get("additions") or []) if isinstance(a, dict)]
    if report is not None:
        keep = {e.get("index") for e in report.get("added") or []}
        adds = [a for i, a in enumerate(adds) if i in keep]
    return [{k: copy.deepcopy(a[k]) for k in _RECIPE_KEYS if k in a} for a in adds]


_RECIPE_FUNC = '''
_ADD_RANK = {"pulse": 0, "macro": 1, "default_gate": 2, "field": 3}


def _apply_additions(state_path):
    """Write ADDITIONS into the saved state.json, in the wizard's placement
    order: pulses, then macros, then default-gate aliases, then fields. The
    same JSON pass the State Manager ran after its build (it verified these in
    the environment then; re-check with 03_generate_config.py)."""
    p = Path(state_path)
    st = json.loads(p.read_text(encoding="utf-8"))
    for a in sorted(ADDITIONS, key=lambda a: _ADD_RANK.get(a["kind"], 0)):
        segs = a["target"].split(".")
        node = st
        if a["kind"] == "field":
            for s in segs[:-1]:
                if not isinstance(node.get(s), dict):
                    node[s] = {}
                node = node[s]
            node[segs[-1]] = a["value"]
            continue
        for s in segs[:-1]:
            node = node[s]
        box = node.setdefault(segs[-1], {})
        if a["kind"] == "default_gate":
            box[a["name"]] = "#./" + a["to"]
        else:
            body = {"__class__": a["class"]}
            body.update(a.get("fields") or {})
            box[a["name"]] = body
    p.write_text(json.dumps(st, indent=4), encoding="utf-8")
    print(f"additions: {len(ADDITIONS)} written into {p}")
'''


def recipe_block(adds: list[dict]) -> str:
    """Python source (stdlib ``json`` + ``pathlib.Path``) defining
    ``ADDITIONS`` and ``_apply_additions(state_path)``; empty without additions."""
    if not adds:
        return ""
    from pprint import pformat
    return ("# ============================ EDIT: additions =========================\n"
            "# Objects the official builder does not write, added after its save\n"
            "# (pulses, macros, the pair's default-gate alias, declared fields).\n"
            "ADDITIONS = " + pformat(adds, indent=4, width=88, sort_dicts=False) + "\n"
            + _RECIPE_FUNC)


def restore_paths(merged: dict, built: dict, paths: list[str]) -> list[str]:
    """Additions are authoritative on the paths they name (re-generate): put
    the *built* (fresh build + additions) value back at each path of
    *merged*, replacing whatever the merge carried, grafted or re-typed there.
    Returns the paths whose merged value differed. [derived: REVIEW_1 Q5]"""
    changed = []
    for p in paths:
        segs = p.split(".")
        ok, val = _walk(built, segs)
        if not ok:
            continue
        node = merged
        for s_ in segs[:-1]:
            if not isinstance(node.get(s_), dict):
                node[s_] = {}
            node = node[s_]
        if node.get(segs[-1]) != val:
            changed.append(p)
        node[segs[-1]] = copy.deepcopy(val)
    return changed


def env_change_notes(spec: dict, manifest: dict | None, python_path: str | None) -> list[str]:
    """What differs between the env each addition was recorded in (its sidecar
    stamp) and this one: the interpreter, quam / quam_builder versions, and a
    class whose field schema hash moved. Additions are re-checked here either
    way; this only says why a result may differ from the first build."""
    notes, seen = [], set()
    versions = (manifest or {}).get("versions") or {}
    for a in spec.get("additions") or []:
        if not isinstance(a, dict) or not isinstance(a.get("env"), dict):
            continue
        env = a["env"]
        if env.get("python") and python_path and env["python"] != python_path and "py" not in seen:
            seen.add("py")
            notes.append(f"The additions were recorded with another environment ({env['python']}); "
                         "they were re-checked in this one.")
        for k in ("quam", "quam_builder"):
            old, new = (env.get("versions") or {}).get(k), versions.get(k)
            if old and new and old != new and k not in seen:
                seen.add(k)
                notes.append(f"{k} {old} when the additions were recorded, {new} now.")
        cls = a.get("class")
        if cls and a.get("schema_hash") and manifest:
            now = schema_hash(manifest, str(cls))
            if now and now != a["schema_hash"] and cls not in seen:
                seen.add(cls)
                notes.append(f"{str(cls).rsplit('.', 1)[-1]}'s fields changed since the additions were "
                             f"recorded (schema {a['schema_hash']} -> {now}); re-checked against the new fields.")
    return notes


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
