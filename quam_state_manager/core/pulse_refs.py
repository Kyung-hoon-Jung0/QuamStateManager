"""A pulse field's reference read the way quam reads it (v1.3.0).

quam resolves a reference string on the OBJECT, not on the JSON:
``#/a/b/c`` is attribute ``c`` of the object at ``a/b``, ``#./x`` is ``x`` of
the object that holds the field, ``#../x`` is ``x`` of that object's parent.
So a key the target object does not STORE still has a value -- its class's
default -- and a name that is not a field at all can be a runtime property
the class computes (``inferred_total_length``, a lab class's
``lobe3_length``). A JSON-only resolver calls both "unresolvable", and the
Pulses page then showed the raw reference as the value, refused to preview
the pulse, and handed the raw string to the lab's class (which raised, so
every edit of that gate was refused).

:func:`read` is the one reader the pulse surfaces use instead. It answers
what SM can know honestly and says so when it cannot:

``stored``    the target object stores the attribute (references followed);
``default``   it does not, and its class's dataclass default is known (the
              selected env's own probe of that class, else SM's catalog
              transcription of quam's class);
``computed``  a runtime property SM computes for a class it synthesizes
              (the inferred length of quam's inferred-length classes);
``self``      a runtime property of the pulse being read itself -- left to
              the pulse's own class, which resolves it on itself;
``lab``       a runtime property (or an unknown default) only the target's
              own class knows -- for a drawing in the selected env the
              question is handed to that class (:data:`LAB_REF_KEY`);
``unknown``   nothing answers it (a missing object, a key no class declares);
              ``reason`` says which.

A raw reference string is never returned as a value.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_PREFIXES = ("#/", "#./", "#../")
_MAX_DEPTH = 40

#: a lab-drawing parameter of this shape -- ``{LAB_REF_KEY: {"qclass",
#: "params", "attr"}}`` -- is not a value: the drawing script builds that
#: pulse with its own class and reads ``attr`` from it (quam's own answer)
LAB_REF_KEY = "@sm_ref"


@dataclass(frozen=True)
class Read:
    ok: bool
    value: Any = None
    how: str = "literal"
    target: str | None = None
    reason: str | None = None
    lab: dict | None = None
    reads: tuple = ()


def is_ref(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIXES)


def ref_target(obj_segs, ref: str) -> list[str] | None:
    """Absolute segments of *ref* held by the object at *obj_segs* -- the
    SAME target SM's pointer index records (``pointer_path.pointer_to_abs``),
    so a value read here and the rows the index refreshes on a write never
    disagree. One ``../`` step, as every SM resolver follows (docs/248: the
    multi-level ``#../../`` form is in no real chip); None for anything else."""
    from quam_state_manager.core.pointer_path import pointer_to_abs
    segs = pointer_to_abs(ref, list(obj_segs) + ["_"])
    if not segs or any(s in ("..", ".") for s in segs):
        return None
    return list(segs)


def _child(node: Any, seg: str):
    if isinstance(node, dict):
        return (True, node[seg]) if seg in node else (False, None)
    if isinstance(node, list) and seg.isdigit() and int(seg) < len(node):
        return True, node[int(seg)]
    return False, None


def _locate(merged: dict, segs, seen: set, depth: int):
    """``(real_segs, node)`` of the object at *segs*, following a reference
    met on the way the way quam's attribute access does; ``(None, None)``
    when it does not exist."""
    node, real = merged, []
    for seg in segs:
        if is_ref(node):
            got = _object_of(merged, real[:-1], node, seen, depth + 1)
            if got is None:
                return None, None
            real, node = got
        found, nxt = _child(node, seg)
        if not found:
            return None, None
        real = real + [seg]
        node = nxt
    if is_ref(node):
        got = _object_of(merged, real[:-1], node, seen, depth + 1)
        if got is None:
            return None, None
        real, node = got
    return real, node


def _object_of(merged, holder_segs, ref, seen, depth):
    if depth > _MAX_DEPTH:
        return None
    key = ("obj", tuple(holder_segs), ref)
    if key in seen:
        return None
    seen.add(key)
    t = ref_target(holder_segs, ref)
    if t is None:
        return None
    real, node = _locate(merged, t, seen, depth)
    return None if real is None else (real, node)


# ---------------------------------------------------------------------------
# what a class says about an attribute its object does not store
# ---------------------------------------------------------------------------

def _env_fields(qclass: str) -> dict | None:
    """The selected env's own dataclass field dump of exactly *qclass*: the
    chip's class inventory first, then the env's pulse roster."""
    from quam_state_manager.core.pulse_catalog import (chip_classes_active,
                                                       env_overlay_active)
    crec = (chip_classes_active() or {}).get(qclass)
    if isinstance(crec, dict) and crec.get("importable") and isinstance(crec.get("fields"), dict):
        return crec["fields"]
    if "." in qclass:
        home, leaf = qclass.rsplit(".", 1)
        rec = (env_overlay_active() or {}).get(leaf)
        if (isinstance(rec, dict) and isinstance(rec.get("fields"), dict)
                and (rec.get("canonical") == qclass or home in (rec.get("homes") or []))):
            return rec["fields"]
    return None


def class_default(qclass: Any, attr: str) -> tuple[bool, Any]:
    """``(known, value)`` of the dataclass default of field *attr* of *qclass*.

    The selected env's probe of that exact class decides when it has one (a
    default it could not serialise is unknown, never a guessed None); else
    SM's catalog transcription of quam's class (an exact, env-verified or
    alias match, never a name-only one)."""
    if not isinstance(qclass, str) or not qclass:
        return False, None
    fields = _env_fields(qclass)
    if fields:
        f = fields.get(attr)
        if (isinstance(f, dict) and f.get("has_default") and "default" in f
                and f.get("default_repr") is None):
            return True, f["default"]
        return False, None
    from quam_state_manager.core.pulse_catalog import resolve_qclass
    spec, how = resolve_qclass(qclass)
    if spec is None or how not in ("exact", "env", "alias"):
        return False, None
    if attr == "length" and spec.length_mode == "inferred":
        return True, spec.length_pointer
    for p in spec.params:
        if p.name == attr:
            return (False, None) if p.required else (True, p.default)
    return False, None


def _computed(merged, real, owner, attr, seen, depth):
    """``(known, value)`` of a runtime property SM computes for *owner*'s
    class: the inferred length of quam's inferred-length classes (the same
    transcription the preview draws with)."""
    from quam_state_manager.core.pulse_catalog import inferred_length, resolve_qclass
    spec, how = resolve_qclass(owner.get("__class__"))
    if (spec is None or how not in ("exact", "env", "alias")
            or spec.length_mode != "inferred"
            or attr != spec.length_pointer[len("#./"):]):
        return False, None
    params: dict = {}
    for k, v in owner.items():
        if k == "__class__":
            continue
        if is_ref(v):
            r = _read(merged, real, v, real, False, seen, depth + 1)
            if not r.ok:
                if r.how == "self":
                    continue          # its own runtime property, not an input
                return False, None
            v = r.value
        params[k] = v
    n = inferred_length(spec.key, params)
    return (n is not None), n


def _also_read(r: Read, path: str) -> Read:
    """*r*, with *path* (a stored node the answer went through) among its reads."""
    if path in r.reads:
        return r
    return Read(r.ok, r.value, r.how, r.target, r.reason, r.lab, (path,) + tuple(r.reads))


def _short(qclass: Any) -> str:
    return str(qclass).rsplit(".", 1)[-1] if isinstance(qclass, str) and qclass else "its class"


# ---------------------------------------------------------------------------
# the reader
# ---------------------------------------------------------------------------

def read(merged: dict, obj_segs, value: Any, *, origin=None,
         lab: bool = False) -> Read:
    """Read *value* -- a field of the object at *obj_segs* -- as quam would.

    *origin* is the object being drawn (default: *obj_segs*): a runtime
    property of THAT object is its own class's business (``how="self"``).
    With *lab*, a ``lab`` answer carries the question for the target's own
    class (``Read.lab``), built only when every field it needs is known."""
    obj_segs = list(obj_segs)
    return _read(merged, obj_segs, value,
                 list(origin) if origin is not None else obj_segs,
                 lab, set(), 0)


def _read(merged, obj_segs, value, origin, lab, seen, depth) -> Read:
    if not is_ref(value):
        return Read(True, value, "literal")
    if depth > _MAX_DEPTH:
        return Read(False, None, "unknown", reason=f"{value} goes round in a loop")
    # the same reference read for ANOTHER object being drawn is not a loop
    # (a mirror's length -> the slot's own length -> the slot's property)
    key = ("ref", tuple(obj_segs), value, tuple(origin))
    if key in seen:
        return Read(False, None, "unknown", reason=f"{value} goes round in a loop")
    seen = seen | {key}
    t = ref_target(obj_segs, value)
    if t is None:
        why = ("climbs more than one level; SM follows one ../ step (docs/248)"
               if "../../" in value else "names no target")
        return Read(False, None, "unknown", reason=f"{value} {why}")
    owner_segs, attr = t[:-1], t[-1]
    real, owner = _locate(merged, owner_segs, set(seen), depth)
    tdot = ".".join(t)
    if real is None:
        where = ".".join(owner_segs) or "the root"
        return Read(False, None, "unknown", target=tdot,
                    reason=f"{where} does not exist")
    odot = ".".join(real)
    tdot = ".".join(real + [attr])
    if isinstance(owner, list):
        found, v = _child(owner, attr)
        if not found:
            return Read(False, None, "unknown", target=tdot,
                        reason=f"{odot} has no item {attr}")
        if is_ref(v):
            return _also_read(_read(merged, real, v, origin, lab, seen, depth + 1), tdot)
        return Read(True, v, "stored", target=tdot, reads=(tdot,))
    if not isinstance(owner, dict):
        return Read(False, None, "unknown", target=tdot,
                    reason=f"{odot} is a value, not an object")
    if attr in owner:
        v = owner[attr]
        if is_ref(v):
            return _also_read(_read(merged, real, v, origin, lab, seen, depth + 1), tdot)
        return Read(True, v, "stored", target=tdot, reads=(tdot,))
    # not stored: quam reads the attribute on the object
    qclass = owner.get("__class__")
    known, d = class_default(qclass, attr)
    if known:
        if is_ref(d):
            return _also_read(_read(merged, real, d, origin, lab, seen, depth + 1), odot)
        return Read(True, d, "default", target=tdot, reads=(odot,),
                    reason=f"{odot} stores no {attr}; {_short(qclass)}'s default")
    if real == list(origin):
        return Read(False, None, "self", target=tdot,
                    reason=f"{attr} is {_short(qclass)}'s own runtime property")
    ok, n = _computed(merged, real, owner, attr, seen, depth)
    if ok:
        return Read(True, n, "computed", target=tdot, reads=(odot,),
                    reason=f"{_short(qclass)}.{attr} of {odot}, computed by SM")
    from quam_state_manager.core.pulse_catalog import is_pulse_class
    if isinstance(qclass, str) and is_pulse_class(qclass):
        reason = (f"{attr} of {odot} is not stored; {_short(qclass)}'s own "
                  "code supplies it")
        spec_lab = None
        if lab:
            params, why = _lab_params(merged, real, owner, real, seen, depth + 1)
            if why is None:
                spec_lab = {"qclass": qclass, "params": params, "attr": attr}
            else:
                reason = f"{reason}, and SM cannot build {odot}: {why}"
        return Read(False, None, "lab", target=tdot, reason=reason,
                    lab=spec_lab, reads=(odot,))
    return Read(False, None, "unknown", target=tdot,
                reason=(f"{odot} stores no {attr}, and SM does not know "
                        f"{_short(qclass)}'s default for it"))


def lab_params(merged: dict, obj_segs, body: dict, *, origin=None
               ) -> tuple[dict, str | None]:
    """``(params, error)``: the fields of the pulse at *obj_segs* as its own
    class must receive them -- every reference read (:func:`read`), a
    runtime property of the pulse itself left for its class, a property
    only another pulse's class knows handed over as a :data:`LAB_REF_KEY`
    question, containers kept. *error* names the first field nothing can
    answer (the pulse is then not drawn rather than drawn wrong)."""
    obj_segs = list(obj_segs)
    return _lab_params(merged, obj_segs, body,
                       list(origin) if origin is not None else obj_segs, set(), 0)


def _lab_params(merged, obj_segs, body, origin, seen, depth):
    out: dict = {}
    for k, v in body.items():
        if k == "__class__":
            continue
        if not is_ref(v):
            out[k] = v
            continue
        r = _read(merged, obj_segs, v, origin, True, seen, depth + 1)
        if r.ok:
            out[k] = r.value
        elif r.how == "self":
            out[k] = v
        elif r.how == "lab" and r.lab is not None:
            out[k] = {LAB_REF_KEY: r.lab}
        else:
            return out, f"{k}: {r.reason}"
    return out, None


def value_or_raw(merged: dict, obj_segs, value: Any) -> Any:
    """The value a display shows for a field: the read value, else the
    stored text (callers mark that case themselves)."""
    r = read(merged, obj_segs, value)
    return r.value if r.ok else value
