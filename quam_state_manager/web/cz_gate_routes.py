"""The CZ gate dialog and its one write door (JSON in, JSON out).

``GET /pulse/cz-gate/dialog?pair=<id>``
    The dialog (``_cz_gate_dialog.html``) with its data as one JSON block:
    ``{"pairs": [{id, control, target, moving_qubit, gates: [name]}],
    "selected": <pair id or "">, "classes": [{class, label, layout:
    "known" | "copied", available, why, slots: [{field, line, required,
    pulse_classes: [{class, label, fields: [{name, label, kind, default,
    unit}]}]}], models: [[pair, gate]]}], "env": <env name or "">}``.

``POST /api/pulse/cz-gate/plan``
    Body ``{pairs: [id], gate_name, gate_class, pulses, copy_from,
    model_gate}``. Pure and fast: ``{ok, plan_sig, pairs: [{pair, ok,
    refusals, creates: n, paths, direction, layout, operations, fields,
    pulses, sources, renamed, reset}]}``. The first pair takes ``copy_from``;
    every other pair gets the first pair's resulting field values, so the
    same starting values go to every pair.

``POST /api/pulse/cz-gate/check``
    The same body. Plans every pair, runs ONE gate check for all pairs that
    can be created (chip copies, the lab's own code, the copies removed),
    and answers ``{ok, status, why_not_checked, plan_sig, check_token,
    leftover, pairs: [{pair, gate, ok, refusals, checks: [{name, status,
    message}], unchecked, timeline: <GateTimeline data>, intent: [...],
    previews_skipped: [str], verdict: {allowed, state, reason}}]}``.
    The verdicts stay on the server under ``check_token`` (store id,
    sequence value, plan signature, per-pair creates): the client never
    supplies a verdict.

``POST /api/pulse/cz-gate/create``
    Body ``{check_token, plan_sig}``. Under the store lock: the token is
    known, unused and from this chip, the plan is the one checked, the store
    has not moved since the check, and planning again gives the same
    creates; then every allowed pair's creates are written in ONE change
    group (one Ctrl+Z) with rollback on any error. Pairs not allowed are
    skipped and named. ``{ok, created: [{pair, gate, state, reason,
    paths}], skipped: [{pair, reason}], count, group, tray_html}``. A store
    that moved is 409 ``stale: true`` with nothing written. This door writes
    the working copy only, never the live files; the new gate is not made
    active (the pair's ``cz`` alias is never touched). The token keeps who
    ran the check (a person or an agent, by the callers gate): a create by
    the other kind is refused (409, ``check_again``).
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import logging
import secrets
import threading
import time
import types
from pathlib import Path
from collections import OrderedDict
from typing import Any

from flask import current_app, jsonify, render_template, request

from quam_state_manager.core import chip_copy, cz_gate_build as cgb, cz_gate_create
from quam_state_manager.core.quam_config_save import (ClassFileError, class_files,
                                                      class_files_moved, class_refusal,
                                                      lab_package, published_path)
from quam_state_manager.core import gate_check, gate_check_log, gate_intent, gate_timeline_view, pulse_catalog
from quam_state_manager.core import slot_gate_plan as sgp, slot_gate_template as sgt
from quam_state_manager.web import request_body
from quam_state_manager.web import routes as R
from quam_state_manager.web.routes import bp

logger = logging.getLogger(__name__)

#: check tokens kept in memory: token -> what the check saw
_TOKENS: "OrderedDict[str, dict]" = OrderedDict()
_TOKENS_MAX = 32
TOKEN_TTL_S = 30 * 60
_TOKENS_LOCK = threading.Lock()

#: capability ids of the classes whose structure SM knows
_CLASS_CAPS = {cgb.CZ_GATE: "pair.cz_gate", cgb.COUPLER_CZ_GATE: "pair.coupler_cz"}
_CLASS_LABELS = {cgb.CZ_GATE: "CZ gate (quam-builder)",
                 cgb.COUPLER_CZ_GATE: "Coupler-only CZ gate (SM extension)"}
_LINE_WORDS = {"moving": "the moving qubit's flux line", "control": "the control qubit's flux line",
               "target": "the target qubit's flux line", "coupler": "the pair's coupler"}
_SKIP_FIELDS = ("id", "digital_marker")
SPECTATORS_ONE_PAIR = ("Spectators given as a list belong to one pair. With several pairs, "
                       "give them per pair.")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _qid(merged, pid, key):
    return cgb.pair_qubit(merged, pid, key)


def _pairs(merged: dict) -> list[dict]:
    out = []
    for pid, p in sorted(((merged.get("qubit_pairs") or {}).items())):
        if not isinstance(p, dict):
            continue
        macros = p.get("macros") if isinstance(p.get("macros"), dict) else {}
        out.append({"id": pid, "control": _qid(merged, pid, "qubit_control"),
                    "target": _qid(merged, pid, "qubit_target"),
                    "moving_qubit": p.get("moving_qubit"),
                    "gates": sorted(n for n, m in macros.items() if cgb.is_cz_macro(n, m)),
                    "spectator_candidates": cgb.spectator_candidates(merged, pid),
                    "lines": _pulse_lines(merged, pid),
                    "xy_operations": {r: sgp.xy_operations(merged, pid, r) for r in sgt.XY_ROLES}})
    return out


def _pulse_lines(merged: dict, pid: str) -> dict:
    """``{control, target, coupler: bool}``: whether that line of the pair can hold a pulse."""
    try:
        lines = cgb._lines(merged, pid)
    except Exception:  # noqa: BLE001 -- a broken pair offers no line
        return {s: False for s in sgt.SLOTS}
    return {s: isinstance(lines.get(s), tuple) for s in sgt.SLOTS}


def _pulse_classes() -> list[dict]:
    """Pulse classes that can play on a flux line or a coupler, with fields."""
    out = []
    for spec in pulse_catalog.PULSE_CATALOG.values():
        if not spec.creatable or "z" not in spec.channels:
            continue
        fields = []
        for p in spec.params:
            if p.name in _SKIP_FIELDS:
                continue
            d = p.default
            fields.append({"name": p.name, "label": p.label, "kind": p.kind, "unit": p.unit,
                           "default": list(d) if isinstance(d, tuple) else d})
        out.append({"class": spec.qclass, "label": spec.label or spec.key,
                    "length": spec.length_mode, "fields": fields})
    return out


def _manifest(store):
    try:
        return R._env_manifest_and_versions(store)[0]
    except Exception:  # noqa: BLE001
        return None


def _known_available(manifest, gate_class) -> tuple[bool, str]:
    """A class SM knows is available unless the env's own probe says it
    does not import it; with no probe it is offered (the check says the rest)."""
    if not manifest:
        return True, ""
    from quam_state_manager.core import capabilities
    caps = (manifest or {}).get("capabilities") or {}
    cid = _CLASS_CAPS[gate_class]
    if cid not in caps:
        return True, ""
    ok, detail = capabilities._manifest_available(manifest, cid)
    if ok:
        return True, ""
    return False, ("The selected environment does not import this class"
                   + (f" ({detail})." if detail else "."))


class SlotClass:
    """A slot gate class draft of the open scope: its spec and the ONE
    descriptor every door of a request plans and judges it with."""

    def __init__(self, name: str, spec: sgt.SlotSpec, version: int = sgt.TEMPLATE_VERSION):
        self.name = name
        self.spec = spec
        self.version = version
        self.qclass = sgp.qclass_of(spec)
        self.desc = sgp.slot_desc(spec)


def _scope() -> str:
    return R._ctx_chip_key(R._active_ctx()) or "no-chip"


def _slot_classes() -> list[SlotClass]:
    """Every slot gate class draft of the open scope that the template
    parser reads back, at any version it reads (read only; an unreadable
    draft is left out)."""
    try:
        return [SlotClass(n, s, v) for n, s, v in sgp.slot_drafts(current_app.instance_path, _scope(), True)]
    except OSError:
        return []


def _request_slot(gate_class: Any) -> SlotClass | None:
    """The slot class a request names, built once per request, or None."""
    if not isinstance(gate_class, str) or gate_class in cgb.GATE_CLASSES:
        return None
    return next((sc for sc in _slot_classes() if sc.qclass == gate_class), None)


def _desc_of(slot: SlotClass | None) -> dict | None:
    return slot.desc if slot is not None else None


def _slot_entry(sc: SlotClass, pcs: list) -> dict:
    slots = [s for s in sgt.SLOTS if s in sc.spec.slots]
    return {"class": sc.qclass, "label": f"{sc.name} (slot class SM made)", "layout": "slot",
            "available": True, "why": "", "older_render": sc.version != sgt.TEMPLATE_VERSION,
            "slots": [{"field": sgp.slot_field(s), "line": s,
                       "line_words": _LINE_WORDS.get(s, s), "required": True,
                       "pulse_classes": pcs} for s in slots],
            "models": [],
            "slot_spec": {"slots": slots, "spectators": bool(sc.spec.spectators),
                          "before": [dict(x) for x in sc.spec.before if isinstance(x, dict)],
                          "after": [dict(x) for x in sc.spec.after if isinstance(x, dict)]},
            "spectators": _spectator_offer(sc.qclass, sc)}


def _slot_presets() -> list[dict]:
    return [{"key": k, "label": k.replace("_", " ").capitalize(),
             "slots": [s for s in sgt.SLOTS if s in v]} for k, v in sgt.PRESETS.items()]


def _number(v: Any) -> bool:
    import math
    try:
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    except OverflowError:
        return False


def _unit_candidates(merged: dict, pairs: list[dict]) -> None:
    """Each spectator candidate gains ``unit`` (the stored amplitude of the
    unit operation, None when absent) and ``reachable`` (the template's
    own range for that unit, None without a number). In place."""
    for p in pairs:
        rows = p.get("spectator_candidates") or []
        amps = sgp.unit_amplitudes(merged, [r["qubit"] for r in rows])
        for r in rows:
            u = amps.get(r["qubit"])
            r["unit"] = u if _number(u) else None
            r["reachable"] = sgt.reachable_level(u) if _number(u) else None


def _classes(store, merged, slot_classes: list[SlotClass] | None = None) -> list[dict]:
    manifest = _manifest(store)
    pcs = _pulse_classes()
    out = []
    for cls in (cgb.CZ_GATE, cgb.COUPLER_CZ_GATE):
        ok, why = _known_available(manifest, cls)
        slots = [{"field": s.field, "line": s.line, "line_words": _LINE_WORDS.get(s.line, s.line),
                  "required": s.required, "pulse_classes": pcs}
                 for s in cgb.GATE_CLASSES[cls]["slots"]]
        out.append({"class": cls, "label": _CLASS_LABELS[cls], "layout": "known",
                    "available": ok, "why": why, "slots": slots, "models": [],
                    "spectators": _spectator_offer(cls)})
    slot_names = {sc.qclass for sc in slot_classes or ()}
    lab: dict[str, list] = {}
    for pid, p in sorted((merged.get("qubit_pairs") or {}).items()):
        macros = p.get("macros") if isinstance(p, dict) else None
        for name, m in (macros or {}).items() if isinstance(macros, dict) else ():
            cls = m.get("__class__") if isinstance(m, dict) else None
            if (isinstance(cls, str) and cls not in cgb.GATE_CLASSES and cls not in slot_names
                    and cgb.is_cz_macro(name, m)):
                lab.setdefault(cls, []).append([pid, name])
    for cls in sorted(lab):
        out.append({"class": cls, "label": cls.rsplit(".", 1)[-1] + " (copies the layout of a gate on this chip)",
                    "layout": "copied", "available": True, "why": "", "slots": [],
                    "models": lab[cls], "spectators": _spectator_offer(cls)})
    for cid, short in (("pair.cz_parametric", "ParametricCZGate"),
                       ("pair.stark_cz_gate", "StarkInducedCZGate")):
        out.append({"class": short, "label": short, "layout": "copied", "available": False,
                    "why": ("SM does not know the structure of this class and this chip has "
                            "no gate of it to copy."), "slots": [], "models": [],
                    "spectators": _spectator_offer(short)})
    out.extend(_slot_entry(sc, pcs) for sc in slot_classes or ())
    return out


def _spectator_offer(gate_class: str, slot: SlotClass | None = None) -> dict:
    """``{"offered": bool, "why": str}``: whether the dialog offers
    spectators for *gate_class*, and the reason sentence when it does not.
    A slot class offers them when its spec says so, with ``method: "unit"``
    (each row scales the qubit's unit operation)."""
    if slot is not None:
        if slot.spec.spectators:
            return {"offered": True, "method": "unit", "why": ""}
        why = cgb.spectator_refusal(gate_class, gate_desc=slot.desc)
        return {"offered": False, "method": "unit", "why": why or ""}
    why = cgb.spectator_refusal(gate_class)
    return {"offered": why is None, "why": why or ""}


def _slot_rows(rows: Any) -> Any:
    """The rows of a slot class as the plan reads them: a blank unit
    (missing, None or "") is left out, never passed on as "". Rows of any
    other shape are passed on as they are (the plan refuses them)."""
    if not isinstance(rows, list):
        return rows
    out = []
    for r in rows:
        if isinstance(r, dict) and "unit" in r and (r["unit"] is None or r["unit"] == ""):
            r = {k: v for k, v in r.items() if k != "unit"}
        out.append(r)
    return out


def _spectators(data: dict, pairs: list) -> dict | None:
    """The request's spectators per pair, or None when none were given.
    ``spectators`` is ``{pair: [{qubit, length, level}]}``; a bare list is
    the spectators of the one asked pair. Anything else is passed on so the
    plan refuses it in words."""
    raw = data.get("spectators")
    if raw is None or raw == [] or raw == {}:
        return None
    if isinstance(raw, list):
        return {pid: raw for pid in pairs[:1]} if len(pairs) == 1 else {"": raw}
    if isinstance(raw, dict):
        out = {str(k): v for k, v in raw.items() if v not in (None, [])}
        return out or None
    return {pid: raw for pid in pairs}


def _body() -> dict:
    data = request_body.json_object({})
    if not isinstance(data, dict):
        raise request_body.BadBody("A JSON object is required.")
    pairs = data.get("pairs")
    if isinstance(pairs, str):
        pairs = [pairs]
    if not isinstance(pairs, list) or not pairs or not all(isinstance(x, str) and x for x in pairs):
        pairs = []
    model = data.get("model_gate")
    pairs = list(dict.fromkeys(pairs))
    spectators = _spectators(data, pairs)
    extra = {"spectators": spectators} if spectators else {}
    return {**extra, "pairs": pairs,
            "gate_name": data.get("gate_name") if isinstance(data.get("gate_name"), str) else "",
            "gate_class": data.get("gate_class") if isinstance(data.get("gate_class"), str) else "",
            "pulses": data.get("pulses") if isinstance(data.get("pulses"), dict) else {},
            "copy_from": data.get("copy_from") or None,
            "model_gate": tuple(model) if isinstance(model, (list, tuple)) else None,
            "macro_fields": data.get("macro_fields") if isinstance(data.get("macro_fields"), dict) else {}}


def _given_values(pulse: dict) -> dict:
    return {k: copy.deepcopy(v) for k, v in pulse.items()
            if k not in ("__class__", "id") and not (isinstance(v, str) and v.startswith("#"))}


def _plan_all(merged: dict, body: dict, slot: SlotClass | None = None) -> list[tuple[str, dict]]:
    """``[(pair, plan)]``; the first pair seeds the others' field values.
    A given value of the wrong kind refuses every pair, naming the field.

    For a slot class (*slot*) every pair is planned with the class's one
    descriptor and no spectators; the rows are then planned by
    ``slot_gate_plan.plan_spectators`` against one ``planned_units`` dict
    for the whole request. A gate refused afterwards leaves no unit in it."""
    desc = _desc_of(slot)
    planned_units: dict = {}
    bad = check_values(merged, body, desc)
    spect = body.get("spectators") or {}
    for key in spect:
        if key not in body["pairs"]:
            bad = bad + [SPECTATORS_ONE_PAIR if key == "" else
                         f"Spectators were given for pair {key}, which is not among the "
                         "pairs to create. Pick that pair, or remove its spectators."]
    if bad:
        return [(pid, {"error": bad[0], "refusals": bad}) for pid in body["pairs"]]
    out = []
    first = None
    scope = R._ctx_chip_key(R._active_ctx()) or "no-chip"
    lab_folder = lab_package(_env_python())
    for i, pid in enumerate(body["pairs"]):
        pulses = body["pulses"]
        copy_from = body["copy_from"] if i == 0 else None
        if i > 0 and first is not None and "error" not in first:
            pulses = {}
            for f, spec in body["pulses"].items():
                pp = gate_intent.planned_pulse(first, f)
                cls = (spec or {}).get("class") if isinstance(spec, dict) else None
                pulses[f] = {"class": cls or (pp or {}).get("__class__"),
                             "fields": _given_values(pp or {})}
            for f, op in (first.get("operations") or {}).items():
                if op.get("group") == "spectator":
                    continue
                if f not in pulses:
                    pp = gate_intent.planned_pulse(first, f) or {}
                    pulses[f] = {"class": pp.get("__class__"), "fields": _given_values(pp)}
        requested = [body["gate_class"]] + [s.get("class") for s in pulses.values() if isinstance(s, dict)]
        why = class_refusal(current_app.instance_path, scope, classes=requested, lab_folder=lab_folder)
        added: list = []
        if slot is not None:
            res = {} if why else cgb.plan(
                merged, pid, gate_name=body["gate_name"], gate_class=body["gate_class"],
                pulses=pulses, copy_from=copy_from, model_gate=body["model_gate"],
                macro_fields=body["macro_fields"] or None, gate_desc=desc)
            if not why and "error" not in res:
                missing = sgp.xy_steps_problem(merged, pid, slot.spec)
                if missing:
                    res = {"error": missing, "refusals": [missing]}
            if not why and "error" not in res and pid in spect:
                res, added = _plan_slot_rows(merged, pid, body["gate_name"], slot, res,
                                             spect[pid], planned_units)
        else:
            res = {} if why else cgb.plan(
                merged, pid, gate_name=body["gate_name"], gate_class=body["gate_class"],
                pulses=pulses, copy_from=copy_from, model_gate=body["model_gate"],
                macro_fields=body["macro_fields"] or None,
                **({"spectators": spect[pid]} if pid in spect else {}))
        why = why or class_refusal(current_app.instance_path, scope, res.get("creates") or [],
                                   lab_folder=lab_folder)
        files = []
        if not why:
            try:
                files = class_files(current_app.instance_path, scope, res.get("creates") or [],
                                    classes=requested)
            except ClassFileError as exc:
                why = str(exc)
        if why or "error" in res:
            for key in added:
                planned_units.pop(key, None)
        if why:
            res = {"error": why, "refusals": [why]}
            if getattr(why, "cls", None):
                # the class to save first: the dialog offers its class page, no text is read
                res["save_class"] = {"cls": why.cls, "refusal": str(why)}
        else:
            _record_class_files(res, pid, body["gate_name"], files)
            if slot is not None:
                note = older_render_note(slot, published_path(current_app.instance_path, scope, slot.qclass))
                steps = sgp.steps_sentence(slot.spec)
                if note or steps:
                    # the build's own notes stay first, in order
                    res["notes"] = list(res.get("notes") or []) + [n for n in (steps, note) if n]
        if i == 0:
            first = res
        out.append((pid, res))
    if slot is None:
        cgb.count_request_spectators(merged, out)
    else:
        out = assign_units(out)
    return out


def assign_units(plans: list[tuple[str, dict]], allowed: Any = None) -> list[tuple[str, dict]]:
    """*plans* with each new spectator unit operation created by exactly one
    of the gates that are written: the first gate (in request order) among
    *allowed* (every plan without an error when None) whose rows use it. A
    gate outside that set creates no unit, and a unit no written gate uses
    is not created. The rows' ``unit_created`` follow. Plans are copied,
    never changed in place."""
    new_units: dict[str, Any] = {}
    for _, p in plans:
        made = {r.get("unit_operation") for r in p.get("spectators") or []
                if isinstance(r, dict) and r.get("unit_created")}
        for path, value in p.get("creates") or []:
            if path in made and path not in new_units:
                new_units[path] = value
    if not new_units:
        return plans
    out, given = [], set()
    for pid, p in plans:
        if "error" in p:
            out.append((pid, p))
            continue
        p = dict(p)
        writes = "error" not in p and (allowed is None or pid in allowed)
        rows = [dict(r) if isinstance(r, dict) else r for r in p.get("spectators") or []]
        mine = []
        for r in rows:
            if not isinstance(r, dict) or r.get("unit_operation") not in new_units:
                continue
            path = r["unit_operation"]
            r["unit_created"] = bool(writes and path not in given)
            if r["unit_created"]:
                given.add(path)
                mine.append((path, copy.deepcopy(new_units[path])))
        if "spectators" in p:
            p["spectators"] = rows
        p["creates"] = [c for c in p.get("creates") or [] if c[0] not in new_units] + mine
        out.append((pid, p))
    return out


def _plan_slot_rows(merged: dict, pid: str, gate: str, slot: SlotClass, res: dict,
                    rows: Any, planned_units: dict) -> tuple[dict, list]:
    """*res* with the slot class's spectator rows planned into its macro:
    ``(plan, [unit keys this gate added to planned_units])``. A class with
    no spectators refuses rows with the descriptor's own sentence; a row
    refusal is passed on as the plan sentence, unchanged."""
    if not slot.spec.spectators:
        why = cgb.spectator_refusal(slot.qclass, gate_desc=slot.desc)
        return {"error": why, "refusals": [why]}, []
    mpath = f"qubit_pairs.{pid}.macros.{gate}"
    macro = next((v for p, v in res.get("creates") or [] if p == mpath), None)
    if not isinstance(macro, dict):
        return {"error": sgp.UNREADABLE_PROBLEM, "refusals": [sgp.UNREADABLE_PROBLEM]}, []
    before = set(planned_units)
    creates, extra, refusals = sgp.plan_spectators(
        merged, pid, gate, _slot_rows(rows), macro, ("qubit_pairs", pid, "macros", gate),
        planned_units)
    if refusals:
        return {"error": refusals[0], "refusals": list(refusals)}, []
    res["creates"] = list(res["creates"]) + list(creates)
    res.update(extra)
    for r in res.get("spectators") or []:
        r["not_calibrated"] = cgb.NOT_CALIBRATED
    res["spectator_ops"] = {r["qubit"]: 1 for r in extra.get("spectators") or []}
    return res, [k for k in planned_units if k not in before]


#: the hint added to a refusal of a gate with SM-made classes whose class has
#: no extras slot (the lab check's own load failure names the key)
EXTRAS_SLOT_REFUSAL = ("A gate that uses a class SM made records its class files in the "
                       "gate's extras; this gate class has no extras slot, so it cannot be "
                       "combined with a class SM made.")


OLDER_RENDER_NOTE = ("The class file quam_config/{file} is of an older render that refuses the stored control "
                     "or target pulse. Make {name} again under its own name and save it to quam_config; "
                     "saving the class again updates the file.")
OLDER_RENDER_SAVE_NOTE = ("The class file quam_config/{file} is of an older render that refuses the stored control "
                          "or target pulse. {name} is already made again in the current render; save it to "
                          "quam_config to update the file.")


def older_render_note(slot: SlotClass, path) -> str | None:
    """The OLDER_RENDER_NOTE sentence when the lab file at *path* of the slot
    class is a version-1 render with a control or target slot, else None
    (also when the file is absent or unreadable: other doors name that).
    When the slot's draft is already of the current render, the
    OLDER_RENDER_SAVE_NOTE sentence instead: only the save is left."""
    if not path:
        return None
    try:
        with open(path, "rb") as fh:
            parsed = sgt.parse(fh.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError):
        return None
    if parsed.ok and parsed.spec is not None and sgt.older_render_refuses_pulse(parsed.spec, parsed.version):
        note = OLDER_RENDER_SAVE_NOTE if slot.version == sgt.TEMPLATE_VERSION else OLDER_RENDER_NOTE
        return note.format(file=Path(path).name, name=slot.name)
    return None


def _record_class_files(plan: dict, pid: str, gate: str, files: list[dict]) -> None:
    """Put the SM-made class files into the planned macro's
    ``extras.sm_class_files`` (in place), so the lab check loads the gate as
    it will be written. A plan without SM-made classes is left alone."""
    plan["sm_class_files"] = files
    if not files:
        return
    mpath = f"qubit_pairs.{pid}.macros.{gate}"
    for path, value in plan.get("creates") or []:
        if path == mpath and isinstance(value, dict):
            extras = value.get("extras") if isinstance(value.get("extras"), dict) else {}
            value["extras"] = {**extras, "sm_class_files": copy.deepcopy(files)}
            return


def _creates_sig(plan: dict) -> str:
    blob = json.dumps(plan.get("creates") or [], sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def plan_signature(body: dict, plans: list) -> str:
    blob = json.dumps({"body": {k: body[k] for k in sorted(body)},
                       "creates": [[pid, p.get("creates") or [], p.get("refusals") or []]
                                   for pid, p in plans]}, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


_KINDS = ("int", "float", "bool", "str")


def value_kind(value: Any, declared: str | None = None) -> str:
    """The kind of a field the person may type: the catalog's declared kind
    when it knows the field (a float field holding the JSON integer 1 stays a
    float field), else the state value's own type, never its digits:
    ``bool`` | ``int`` | ``float`` | ``str``; ``any`` (a number or text) for
    a null value with no declaration."""
    if declared in _KINDS:
        return declared
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    return "any"


def kind_mismatch(value: Any, declared: str | None) -> str | None:
    """A hint when the state's value does not have the declared kind (a bool
    in an int field): the field keeps the declared kind and says so."""
    if declared not in _KINDS or value is None:
        return None
    if value_kind(value) != declared and not (declared == "float" and value_kind(value) == "int"):
        return f"The chip holds {value!r} here, but the class declares {declared}."
    return None


def value_problem(value: Any, kind: str, optional: bool = True) -> str | None:
    """Why *value* cannot go into a field of *kind* (the page's own words),
    or None. Numbers are finite ints or floats, never bools, strings, lists or
    dicts; an int field takes whole numbers only."""
    import math
    if value is None:
        return None if optional else "Type a value."
    if kind in ("int", "float"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "Type a number."
        if not math.isfinite(float(value)):
            return "Type a number."
        if kind == "int" and float(value) != int(value):
            return "Type a whole number."
        return None
    if kind == "bool":
        return None if isinstance(value, bool) else "Type true or false."
    if kind == "str":
        return None if isinstance(value, str) else "Type text."
    if isinstance(value, bool) or isinstance(value, (list, dict)):
        return "Type a number or text."
    if isinstance(value, float) and not math.isfinite(value):
        return "Type a number."
    return None if isinstance(value, (int, float, str)) else "Type a number or text."


def _given_kinds(merged: dict, body: dict, desc: dict | None = None) -> tuple[dict, dict]:
    """``({slot: {field: (kind, optional)}}, {key: (kind, optional)})`` for the
    values the person may give: from the catalog for a class SM knows, from
    the model's own pulses and settings (with the catalog's declarations) for
    a copied layout."""
    pulse_kinds: dict = {}
    for slot, spec in (body.get("pulses") or {}).items():
        cls = (spec or {}).get("class") if isinstance(spec, dict) else None
        cat = pulse_catalog.by_qclass(cls) if isinstance(cls, str) else None
        if cat is not None:
            pulse_kinds[slot] = {n: (prm.kind, False) for prm in cat.params
                                 for n in (prm.name, *getattr(prm, "aliases", ()))}
    setting_kinds: dict = {}
    if body.get("gate_class") not in cgb.GATE_CLASSES and desc is None and body.get("pairs"):
        bare = cgb.plan(merged, body["pairs"][0], gate_name=body.get("gate_name") or "cz_x",
                        gate_class=body.get("gate_class") or "", pulses={},
                        model_gate=body.get("model_gate"))
        if "error" not in bare:
            for f in (bare.get("operations") or {}):
                pulse_kinds[f] = {k: (v["kind"], v["optional"])
                                  for k, v in field_specs(gate_intent.planned_pulse(bare, f)).items()}
            for st in _settings(bare):
                setting_kinds[st["key"]] = (st["kind"], st["optional"])
    return pulse_kinds, setting_kinds


def check_values(merged: dict, body: dict, desc: dict | None = None) -> list[str]:
    """Every given pulse field and gate setting checked against its kind on
    the server (the page checks too, but nothing it sends is trusted): one
    refusal per bad value, naming the field."""
    pulse_kinds, setting_kinds = _given_kinds(merged, body, desc)
    out = []
    for slot, spec in (body.get("pulses") or {}).items():
        for k, v in ((spec or {}).get("fields") or {}).items() if isinstance(spec, dict) else ():
            kind, optional = (pulse_kinds.get(slot) or {}).get(k, (value_kind(v), True))
            why = value_problem(v, kind, optional or v is None and kind == "any")
            if why:
                out.append(f"{cgb.cap_name(slot)}.{cgb.cap_name(k)}: {why}")
    for k, v in (body.get("macro_fields") or {}).items():
        kind, optional = setting_kinds.get(k, (value_kind(v), True))
        why = value_problem(v, kind, optional)
        if why:
            out.append(f"gate setting {cgb.cap_name(k)}: {why}")
    return out


def field_specs(pulse: dict | None) -> dict:
    """``{field: {"kind", "label", "unit", "optional"}}`` for every plain
    field of a planned pulse: labels and units from the pulse catalog when it
    knows the class (the env's lab classes included), else the raw key; a
    null value is optional (shown folded)."""
    out: dict = {}
    if not isinstance(pulse, dict):
        return out
    spec = pulse_catalog.by_qclass(pulse.get("__class__")) if isinstance(
        pulse.get("__class__"), str) else None
    params = {}
    for prm in getattr(spec, "params", ()) or ():
        for n in (prm.name, *getattr(prm, "aliases", ())):
            params[n] = prm
    for k, v in pulse.items():
        if k in ("__class__", "id") or (isinstance(v, str) and v.startswith("#")):
            continue
        prm = params.get(k)
        declared = getattr(prm, "kind", None)
        out[k] = {"kind": value_kind(v, declared),
                  "label": getattr(prm, "label", None) or k,
                  "unit": getattr(prm, "unit", "") or "",
                  "optional": v is None}
        hint = kind_mismatch(v, declared)
        if hint:
            out[k]["hint"] = hint
    return out


def _settings(p: dict) -> list[dict]:
    """The gate's own settings of a copied layout: every key of the planned
    macro that is not a pulse field, not a key the copy resets and not the
    class marker. ``source`` is the plan's provenance (copied / given / kept
    reference); a null value is ``optional`` (shown folded); a reference is
    read-only."""
    if p.get("layout") != "copied":
        return []
    macro = (p.get("creates") or [(None, {})])[0][1] or {}
    prov = (p.get("fields") or {}).get("macro") or {}
    pulses = set(p.get("operations") or {})
    reset = set(p.get("reset") or [])
    out = []
    for k in sorted(macro):
        if k in ("__class__", "extras") or k in pulses or k in reset:
            continue
        v = macro[k]
        ref = isinstance(v, str) and v.startswith("#")
        out.append({"key": k, "value": v, "kind": value_kind(v),
                    "source": prov.get(k) or (cgb.KEPT_REF if ref else cgb.FROM_MODEL),
                    "reference": ref, "optional": v is None})
    return out


#: the plan's refusal when no gate class was given: it belongs to the class field
_NO_CLASS = cgb.NO_CLASS


def class_fit(merged: dict, pairs: list[dict], classes: list[dict],
              descs: dict | None = None) -> dict:
    """``{pair: {class: reason}}``, empty reason when the class can run there.
    *descs* maps a slot class to its descriptor."""
    descs = descs or {}
    out = {}
    for p in pairs:
        out[p["id"]] = {c["class"]: (cgb.class_problem(merged, p["id"], c["class"],
                                                        gate_desc=descs.get(c["class"])) or "")
                        for c in classes}
    return out


def _summary(pid: str, p: dict, merged: dict | None = None, gate_class: str = "",
             desc: dict | None = None) -> dict:
    if "error" in p:
        refusals = p.get("refusals") or [p["error"]]
        out = {"pair": pid, "ok": False, "refusals": refusals}
        about = {_NO_CLASS}
        if merged is not None and gate_class:
            r = cgb.class_problem(merged, pid, gate_class, gate_desc=desc)
            if r:
                about.add(r)
                if r not in refusals:
                    # the plan stops at the first group of reasons (a bad
                    # name); the class reason is named too, for its own field
                    refusals = refusals + [r]
                    out["refusals"] = refusals
        mine = [r for r in refusals if r in about]
        if mine:
            out["class_refusals"] = mine
        if isinstance(p.get("save_class"), dict):
            out["save_class"] = p["save_class"]
        return out
    pulses = {f: gate_intent.planned_pulse(p, f) for f in (p.get("operations") or {})}
    return {"pair": pid, "ok": True, "refusals": [],
            "field_specs": {f: field_specs(v) for f, v in pulses.items()},
            "settings": _settings(p),
            "model_gate": list(p["model_gate"]) if p.get("model_gate") else None,
            "creates": len(p["creates"]), "paths": [c[0] for c in p["creates"]],
            "direction": p.get("direction"), "layout": p.get("layout"),
            "operations": p.get("operations"), "fields": p.get("fields"),
            "pulses": pulses, "sources": p.get("sources"),
            "renamed": p.get("renamed") or {}, "reset": p.get("reset") or [],
            "notes": list(p.get("notes") or []),
            **{k: p[k] for k in ("spectators", "spectator_ops") if k in p}}


def _store_or_error():
    store = R._store()
    if not store:
        return None, (jsonify(ok=False, error=R._no_chip_msg()), 400)
    return store, None


# ---------------------------------------------------------------------------
# previews and the check
# ---------------------------------------------------------------------------

def _element(op: dict) -> str | None:
    ch = op.get("channel")
    if not isinstance(ch, str):
        return None
    if ch.startswith("coupler "):
        return ch[len("coupler "):]
    if ch.startswith("the coupler of pair "):
        return None
    return ch


def _previews(plan: dict) -> tuple[dict, list[str]]:
    """``({(element, operation): samples | constant}, [why skipped])``."""
    from quam_state_manager.core import waveform_synth
    out, skipped = {}, []
    for f, op in (plan.get("operations") or {}).items():
        pulse = gate_intent.planned_pulse(plan, f) or {}
        el = _element(op)
        qclass = pulse.get("__class__")
        if el is None:
            skipped.append(f"{op.get('key')}: SM does not know the coupler's element name.")
            continue
        if not isinstance(qclass, str) or pulse_catalog.spec_at_known_home(qclass) is None:
            skipped.append(f"{op.get('key')}: SM cannot draw the pulse class {qclass} itself.")
            continue
        params = {k: v for k, v in pulse.items() if k not in ("__class__", "id")
                  and not (isinstance(v, str) and v.startswith("#"))}
        try:
            pay = waveform_synth.synthesize(qclass, params)
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"{op.get('key')}: SM could not draw it ({type(exc).__name__}).")
            continue
        if not pay.get("ok") or pay.get("truncated"):
            skipped.append(f"{op.get('key')}: {pay.get('error') or 'SM could not draw it in full.'}")
            continue
        cv = pay.get("constant_value")
        if (op.get("group") == "spectator" and isinstance(cv, (int, float))
                and isinstance(pulse.get("length"), int)):
            # every sample, so the check compares the entered length as well
            out[(el, op.get("key"))] = [float(cv)] * pulse["length"]
            continue
        out[(el, op.get("key"))] = float(cv) if isinstance(cv, (int, float)) else list(pay.get("i") or [])
    must = [(_element(op), op.get("key")) for op in (plan.get("operations") or {}).values()
            if op.get("group") == "spectator"]
    if must:
        out[gate_check.REQUIRED] = must
    return out, skipped


def sync_now() -> dict:
    """What the top bar's one sync control knows right now (the same
    ``_sync_view`` it renders from, no second source): ``{"auto_push": bool,
    "state": str | None, "unapplied": int}``. ``auto_push``: an Auto-Sync
    session that pushes is armed, so a create also reaches the chip's files."""
    try:
        ctx = R._active_ctx()
        sv = R._sync_view(ctx)
        sess = R._auto_sync_state(ctx) or {}
        if not sv:
            raise ValueError("no sync view")
        return {"auto_push": bool(R._auto_apply_state(ctx)), "auto_pull": bool(sess.get("pull")),
                "state": sv.get("state"), "unapplied": int(sv.get("unapplied") or 0),
                "readable": True}
    except Exception:  # noqa: BLE001 -- unknown is said as unknown, never a guess
        return {"auto_push": False, "auto_pull": False, "state": None, "unapplied": 0,
                "readable": False}


def range_sentence(lane: dict, v: float) -> str:
    """What the line would play against the port's own range. With a channel
    offset the sentence names the level, the offset, their sum and the range
    (never an offset-shifted number as "the port's limit")."""
    fmt = gate_timeline_view.fmt_num
    off = float(lane.get("offset_v") or 0.0)
    lim = lane.get("limit_v")
    if not lim:
        return f"{lane['key']} would play {fmt(v)} V, over the port's range."
    rng = lim[1] + off                       # limit_v = [-range - offset, range - offset]
    if off == 0:
        edge = rng if v >= 0 else -rng
        return f"{lane['key']} would play {fmt(v)} V. This port's limit is {fmt(edge)} V."
    return (f"{lane['key']} would play {fmt(v)} V on top of its {fmt(off)} V offset: "
            f"{fmt(v + off)} V, {'above' if v + off > 0 else 'below'} the port's "
            f"{fmt(rng)} V range.")


def headline(entry: dict | None, tl: dict | None, reason: str) -> str:
    """One short sentence for the verdict banner. A failed range check says
    which line would play what against which limit, from the drawn lanes;
    anything else keeps the rule's own reason."""
    checks = (entry or {}).get("checks") or {}
    if (checks.get("range") or {}).get("status") == "fail":
        for ln in (tl or {}).get("lanes") or []:
            for s in ln.get("segments") or []:
                if "over_range" in (s.get("flags") or []):
                    v = s["peak_v"] if abs(s.get("peak_v") or 0) >= abs(s.get("min_v") or 0) else s["min_v"]
                    return range_sentence(ln, v)
    return reason


def run_check(store, python_path, gates, creates, previews):
    """The one gate check (tests replace this function)."""
    return gate_check.check_gates(store, python_path, gates, creates, previews=previews)


def _remember(entry: dict) -> str:
    tok = secrets.token_urlsafe(18)
    now = time.time()
    with _TOKENS_LOCK:
        for k in [k for k, v in _TOKENS.items() if now - v["t"] > TOKEN_TTL_S]:
            _TOKENS.pop(k, None)
        _TOKENS[tok] = {**entry, "t": now, "used": False}
        while len(_TOKENS) > _TOKENS_MAX:
            _TOKENS.popitem(last=False)
    return tok


#: who ran the check and who presses create must be the same kind of caller
_CHECKED_BY_OTHER = {
    "person": "This check was run by a person. An agent's create needs the agent's own check: "
              "run the check again.",
    "agent": "This check was run by an agent. A person's create needs a check pressed in the SM "
             "window: press Check again.",
}


def _caller_kind() -> str:
    """``person`` or ``agent``, by the callers gate (a person's window
    proof), never by the absence of a header."""
    from quam_state_manager.web import callers
    return "person" if callers.from_person() else "agent"


def _token(tok: Any) -> dict | None:
    if not isinstance(tok, str):
        return None
    with _TOKENS_LOCK:
        e = _TOKENS.get(tok)
        if e is None or time.time() - e["t"] > TOKEN_TTL_S:
            _TOKENS.pop(tok, None)
            return None
        return e


def _env_python():
    from quam_state_manager.web import diagram_remove_routes as DR
    return DR.env_python()


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------

@bp.route("/pulse/cz-gate/dialog")
def cz_gate_dialog():
    """The CZ gate dialog, its pair preselected when given."""
    store = R._store()
    if not store:
        return render_template("_status.html", message=R._no_chip_msg(), level="warning"), 400
    with store._lock:
        merged = store.merged
        pairs = _pairs(merged)
        slots = _slot_classes()
        if slots:
            _unit_candidates(merged, pairs)
        classes = _classes(store, merged, slots)
        fit = class_fit(merged, pairs, classes, {sc.qclass: sc.desc for sc in slots})
    want = request.args.get("pair") or ""
    selected = want if any(p["id"] == want for p in pairs) else ""
    from quam_state_manager.web import diagram_remove_routes as DR
    data = {"pairs": pairs, "selected": selected, "classes": classes, "fit": fit,
            "env": DR.env_name(_env_python()), "sync": sync_now(),
            "slot_presets": _slot_presets(), "slot_names": list(sgt.SLOTS)}
    return render_template("_cz_gate_dialog.html", data=data, selected=selected,
                           unknown_pair=want if want and not selected else "")


@bp.route("/api/pulse/cz-gate/plan", methods=["POST"])
def api_cz_gate_plan():
    """Plan the gate on every asked pair; writes nothing."""
    return plan_answer()


@bp.route("/api/pulse/cz-gate/check", methods=["POST"])
def api_cz_gate_check():
    """Check the planned gates with the lab's code on chip copies; the
    verdicts stay on the server under the returned token."""
    return check_answer()


@bp.route("/api/pulse/cz-gate/create", methods=["POST"])
def api_cz_gate_create():
    """Write the checked gates in one change group (see the module docstring).
    Person-only (``callers.PERSON_ONLY``)."""
    return create_answer()


# The bodies of the three doors. The person's routes above and the agent's
# routes (``agent_api``: ``/api/agent/cz-gate/*``) call these same functions,
# so every refusal reads the same for both (one implementation per concept).

def plan_answer():
    """The plan door's answer for the current request."""
    store, err = _store_or_error()
    if err:
        return err
    body = _body()
    if not body["pairs"]:
        return jsonify(ok=False, error="Pick a pair."), 400
    slot = _request_slot(body["gate_class"])
    with store._lock:
        plans = _plan_all(store.merged, body, slot)
        rows = [_summary(pid, p, store.merged, body["gate_class"], _desc_of(slot))
                for pid, p in plans]
    return jsonify(ok=True, plan_sig=plan_signature(body, plans), pairs=rows)


def check_answer():
    """The check door's answer for the current request."""
    store, err = _store_or_error()
    if err:
        return err
    body = _body()
    if not body["pairs"]:
        return jsonify(ok=False, error="Pick a pair."), 400
    slot = _request_slot(body["gate_class"])
    with store._lock:
        plans = _plan_all(store.merged, body, slot)
        pre_seq = store.mutation_seq
    sig = plan_signature(body, plans)
    scope = R._ctx_chip_key(R._active_ctx()) or "no-chip"
    good = [(pid, p) for pid, p in plans if "error" not in p]
    checked: dict = {}
    skipped_by: dict = {}
    res = None
    # The check of each gate reads the creates the final write makes: when
    # the verdicts move a shared unit operation to another gate (or drop it),
    # the gates that may be written are checked again as they will be written.
    for _ in range(len(plans) + 1):
        gates = [(pid, body["gate_name"]) for pid, _ in good]
        creates = [c for _, p in good for c in p["creates"]]
        previews = {}
        for pid, p in good:
            pv, sk = _previews(p)
            previews[(pid, body["gate_name"])] = pv
            skipped_by[pid] = sk
        res = run_check(store, _env_python(), gates, creates, previews) if gates else None
        for pid, p in good:
            checked[pid] = (res, p, _verdict(res, pid, p, body, slot, scope))
        allowed = [pid for pid, _ in good if checked[pid][2][0]]
        if slot is None:
            break
        nxt = [(pid, p) for pid, p in assign_units(good, set(allowed)) if pid in allowed]
        if [(pid, p["creates"]) for pid, p in nxt] == [(pid, checked[pid][1]["creates"])
                                                       for pid in allowed]:
            break
        good = nxt
    # a refused gate shows the paths it would hold beside the gates written
    shown = dict(assign_units(plans, {pid for pid, (_, _, v) in checked.items() if v[0]})
                 if slot is not None else plans)
    out_pairs, verdicts, sigs, checks_by, timelines = [], {}, {}, {}, {}
    for pid, p in plans:
        if "error" not in p:
            rp, p, (allowed, state, reason, entry) = checked[pid]
        if "error" in p:
            out_pairs.append({"pair": pid, "gate": body["gate_name"], "ok": False,
                              "refusals": p.get("refusals") or [p["error"]],
                              "verdict": {"allowed": False, "state": cz_gate_create.NOT_CHECKED,
                                          "reason": " ".join(p.get("refusals") or [p["error"]])}})
            verdicts[pid] = {"allowed": False}
            continue
        couplers = {_element(op) for op in (p.get("operations") or {}).values()
                    if (op.get("channel") or "").startswith(("coupler", "the coupler"))}
        src = p.get("sources") or {}
        rows_ops = {op.get("key") for op in (p.get("operations") or {}).values()
                    if (op.get("channel") or "").startswith(("coupler", "the coupler"))}
        for r in (((entry or {}).get("timeline") or {}).get("rows") or []):
            if r.get("operation") in rows_ops:
                couplers.add(r.get("element"))
        tl = gate_timeline_view.timeline_data(entry, control=src.get("control"),
                                              target=src.get("target"),
                                              coupler_elements=[c for c in couplers if c])
        skips = skipped_by.get(pid) or []
        oc = gate_check_log.outcome(rp, entry, reasons=(
            {"preview": "no preview for this pulse class"}
            if skips and all("pulse class" in t for t in skips) else {}))
        out_pairs.append({
            "pair": pid, "gate": body["gate_name"], "ok": True, "refusals": [],
            "notes": list(p.get("notes") or []), "outcome": oc,
            "checks": [{"name": k, "status": v.get("status"), "message": v.get("message")}
                       for k, v in ((entry or {}).get("checks") or {}).items()],
            "unchecked": (entry or {}).get("unchecked") or [],
            "timeline": tl, "tiles": gate_timeline_view.tiles(tl),
            "intent": gate_intent.intent_rows(p, entry),
            "previews_skipped": skipped_by.get(pid) or [],
            "paths": [c[0] for c in (p if allowed else shown[pid])["creates"]],
            "verdict": {"allowed": allowed, "state": state, "reason": reason,
                        "headline": headline(entry, tl, reason)}})
        verdicts[pid] = {"allowed": allowed, "state": state, "reason": reason}
        # the check record reads the same outcome the dialog showed
        checks_by[pid] = oc
        timelines[pid] = tl
        sigs[pid] = _creates_sig(p)
    seq = (res or {}).get("seq")
    tok = _remember({"by": _caller_kind(), "store_id": chip_copy.store_id(store),
                     "seq": seq if seq is not None else pre_seq,
                     "plan_sig": sig, "body": body, "verdicts": verdicts, "sigs": sigs,
                     "checks": checks_by, "timelines": timelines})
    return jsonify(ok=True, status=(res or {}).get("status") or "not-run", sync=sync_now(),
                   why_not_checked=(res or {}).get("why_not_checked"),
                   leftover=(res or {}).get("leftover"),
                   plan_sig=sig, check_token=tok, pairs=out_pairs)


def _verdict(res, pid: str, p: dict, body: dict, slot, scope: str) -> tuple:
    """``(allowed, state, reason, entry)`` of one planned gate from the check *res*."""
    entry = gate_check.gate_result(res or {}, pid, body["gate_name"])
    allowed, state, reason = cz_gate_create.may_create(
        res, entry, gate_class=body["gate_class"],
        layout="slot" if slot is not None else p.get("layout"),
        sm_classes=bool(p.get("sm_class_files")))
    if not allowed and p.get("sm_class_files") and "extras" in reason:
        reason = f"{reason} {EXTRAS_SLOT_REFUSAL}"
    moved = class_files_moved(current_app.instance_path, scope, p.get("sm_class_files"))
    if moved:
        allowed, state, reason = False, cz_gate_create.NOT_CHECKED, moved
    return allowed, state, reason, entry


def _refuse(msg: str, code: int = 409, **extra):
    return jsonify(ok=False, error=msg, **extra), code


def create_answer():
    """The create door's answer for the current request. Every row of the
    group carries the presser (``routes._request_actor``): a person, or the
    agent whose bridge pressed."""
    ctx = R._active_ctx()
    store = R._store()
    modifier = R._modifier()
    if not store or not modifier:
        return jsonify(ok=False, error=R._no_chip_msg()), 400
    if ctx and (ctx.get("origin") or "live") != "live":
        return _refuse("This chip is a read-only archive. Open the live chip to create a gate.")
    lk = R._agent_edit_lock_refusal(ctx)
    if lk is not None:
        return lk
    data = request_body.json_object({})
    if not isinstance(data, dict):
        return _refuse("A JSON object is required.", 400)
    tok = _token(data.get("check_token"))
    if tok is None:
        return _refuse("This check is not known any more (it expired or the app restarted). "
                       "Press Check again.", 409, check_again=True)
    if tok.get("by") != _caller_kind():
        return _refuse(_CHECKED_BY_OTHER.get(tok.get("by")) or "This check is not known any more. "
                       "Press Check again.", 409, check_again=True)
    if tok["used"]:
        return _refuse("These gates were already created from this check. Press Check "
                       "again to create more.", 409, check_again=True)
    if tok["store_id"] != chip_copy.store_id(store):
        return _refuse("This check was made on another chip. Press Check again on this chip.",
                       409, check_again=True)
    if str(data.get("plan_sig") or "") != tok["plan_sig"]:
        return _refuse("The inputs changed after the check. Press Check again.", 409,
                       check_again=True)
    body = tok["body"]
    gid = None
    created, skipped = [], []
    with store._lock:
        if not chip_copy.store_at_seq(store, {"seq": tok["seq"], "store_id": tok["store_id"]}):
            return _refuse("The chip changed after the check. Nothing was written. The check "
                           "runs again.", 409, stale=True)
        slot = _request_slot(body["gate_class"])
        allowed = {pid for pid in body["pairs"] if (tok["verdicts"].get(pid) or {}).get("allowed")}
        plans = _plan_all(store.merged, body, slot)
        plans = dict(assign_units(plans, allowed) if slot is not None else plans)
        for pid in body["pairs"]:
            v = tok["verdicts"].get(pid) or {}
            p = plans.get(pid) or {}
            if not v.get("allowed"):
                skipped.append({"pair": pid, "reason": v.get("reason") or " ".join(
                    p.get("refusals") or ["It cannot be created."])})
                continue
            if "error" in p or _creates_sig(p) != tok["sigs"].get(pid):
                return _refuse("The chip changed after the check. Nothing was written. The "
                               "check runs again.", 409, stale=True)
        todo = [pid for pid in body["pairs"] if (tok["verdicts"].get(pid) or {}).get("allowed")]
        if not todo:
            return _refuse("No pair can be created: " + " ".join(
                f"{s['pair']}: {s['reason']}" for s in skipped), 400, skipped=skipped)
        gid = modifier.new_group_id()
        try:
            for pid in todo:
                for path, value in plans[pid]["creates"]:
                    modifier.create_subtree(path, copy.deepcopy(value), group_id=gid)
            actor = R._request_actor()
            for e in store.change_log:
                if e.group_id == gid:
                    e.actor = actor
        except Exception as exc:  # noqa: BLE001 -- all or nothing
            # A write may log its entry and then raise: undo the whole group by
            # its id (as the diagram add door does), not by a count of returns.
            undo_error = None
            while store.change_log and store.change_log[-1].group_id == gid:
                try:
                    if not modifier.undo():
                        raise RuntimeError("undo returned no change")
                except Exception as rb:  # noqa: BLE001
                    undo_error = rb
                    break
            left = [e.dot_path for e in store.change_log if e.group_id == gid]
            logger.exception("cz gate create failed; entries left in its group: %d", len(left))
            if left or undo_error is not None:
                return _refuse("The create failed part way and could not be undone automatically: "
                               "press Ctrl+Z, then check the pair.", 500, partial=True)
            from quam_state_manager.core.type_policy import TypeMismatchError
            code = 400 if isinstance(exc, (TypeMismatchError, ValueError, TypeError)) else 500
            return _refuse(f"Nothing was created: the write failed and was rolled back ({exc}).",
                           code)
        with _TOKENS_LOCK:
            tok["used"] = True
        for pid in todo:
            v = tok["verdicts"][pid]
            # the check record is taken from the store AFTER the write: its
            # content hash is what a later status row compares against
            rec = gate_check_log.record_check(R._history(), ctx["path"], store.merged, pid,
                                              body["gate_name"], (tok.get("checks") or {}).get(pid))
            created.append({"pair": pid, "gate": body["gate_name"], "state": v.get("state"),
                            "outcome": (tok.get("checks") or {}).get(pid),
                            "reason": v.get("reason") or "",
                            "record": rec.get("entry", {}).get("result") if rec.get("ok") else None,
                            "record_error": None if rec.get("ok") else rec.get("error"),
                            "paths": [c[0] for c in plans[pid]["creates"]]})
    R._reprobe_if_new_classes(store)
    resp = jsonify(ok=True, created=created, skipped=skipped, count=len(created), group=gid,
                   sync=sync_now(),
                   tray_html=R._tray_html())
    resp.headers["HX-Trigger"] = "pulses-changed, diagnostics-changed"
    return resp


# ---------------------------------------------------------------------------
# a new slot gate class
# ---------------------------------------------------------------------------

NAME_NOT_TEXT = "The class name must be text. Type a class name."
NAME_TABLE = "{name} is already a gate class SM knows. Pick another class name."
NAME_PULSE = "{name} is already a pulse class. Pick another class name."
NAME_LAB = "{name} is already a class file of the lab's quam_config folder. Pick another class name."
NAME_DRAFT = "A class draft named {name} already exists and is not a slot gate class. Pick another class name."
NAME_IN_USE = ("The slot gate class {name} is used by {count} gate(s) of this chip, so SM does not "
               "replace it. Pick another class name.")


def _step_at(steps: dict, problems: list) -> dict | None:
    """``{where, index}`` of the step a refusal sentence of the one step check
    names (index from 0), or None. Found by asking that same check of growing
    prefixes, so no rule is restated here."""
    for where in ("before", "after"):
        listed = steps.get(where)
        problem = sgt.sequence_steps_problem(listed, where)
        if not problem or problem not in problems or type(listed) not in (list, tuple):
            continue
        for k in range(1, len(listed) + 1):
            if sgt.sequence_steps_problem(listed[:k], where):
                return {"where": where, "index": k - 1}
    return None


PAIR_UNKNOWN = "The pair {pair} is not in this chip, so its xy steps cannot be checked."


def _xy_step_refusal(merged: dict, pair, steps: dict) -> dict | None:
    """The refusal body when the dialog of *pair* posts an xy step whose operation the pair's
    qubit of that role lacks, with ``step`` naming that row; None when every xy step plays.
    The one lookup is ``slot_gate_plan.xy_steps_problem``; the row is found by asking it of
    growing prefixes, so no rule is restated here."""
    pairs = (merged or {}).get("qubit_pairs")
    if not isinstance(pair, str) or not isinstance(pairs, dict) or pair not in pairs:
        text = PAIR_UNKNOWN.format(pair=pair if isinstance(pair, str) else repr(pair))
        return {"ok": False, "error": text, "problems": [text]}
    for where in ("before", "after"):
        listed = list(steps.get(where) or ())
        for k in range(1, len(listed) + 1):
            only = types.SimpleNamespace(before=(), after=())
            setattr(only, where, listed[:k])
            problem = sgp.xy_steps_problem(merged, pair, only)
            if problem:
                return {"ok": False, "error": problem, "problems": [problem],
                        "step": {"where": where, "index": k - 1}}
    return None


def _gates_using(merged: dict, qclass: str) -> int:
    n = 0
    for p in ((merged or {}).get("qubit_pairs") or {}).values():
        macros = p.get("macros") if isinstance(p, dict) else None
        for m in (macros or {}).values() if isinstance(macros, dict) else ():
            if isinstance(m, dict) and m.get("__class__") == qclass:
                n += 1
    return n


def _older_match(spec: sgt.SlotSpec | None, mine: list) -> sgt.SlotSpec | None:
    """The stored spec of an older-render draft that *spec* may make again, or None.

    *spec* must equal the stored spec once the steps of both are left out, and it
    must either post no steps (the stored steps are kept as they are) or post
    exactly the stored steps. Other steps would change what the gates that use
    the class play, so they fall under the in-use refusal."""
    if spec is None:
        return None
    bare = dataclasses.replace(spec, before=(), after=())
    for s, v in mine:
        if v == sgt.TEMPLATE_VERSION or dataclasses.replace(s, before=(), after=()) != bare:
            continue
        if (not spec.before and not spec.after) or (s.before, s.after) == (spec.before, spec.after):
            return s
    return None


#: the "spec to write" of a make request whose text equals the stored draft: nothing is written
UNCHANGED = object()


def _name_check(name: str, merged: dict, spec: sgt.SlotSpec | None = None):
    """``(why *name* cannot be (re)made here or None, the spec to write or None for *spec*)``."""
    from quam_state_manager.core import pulse_class_drafts as drafts
    from quam_state_manager.core.quam_config_save import _lab_made
    if any(c.rsplit(".", 1)[-1] == name for c in cgb.GATE_CLASSES):
        return NAME_TABLE.format(name=name), None
    if any(spec.key == name or str(spec.qclass).rsplit(".", 1)[-1] == name
           for spec in pulse_catalog.PULSE_CATALOG.values()):
        return NAME_PULSE.format(name=name), None
    data = drafts.load_draft(current_app.instance_path, _scope(), name)
    if data is not None:
        mine = [(s, v) for n, s, v in sgp.slot_drafts(current_app.instance_path, _scope(), True) if n == name]
        if not mine:
            return NAME_DRAFT.format(name=name), None
        count = _gates_using(merged, f"quam_config.{name}.{name}")
        if not count:
            return None, None
        # an older render made again with the same spec only widens what its slots accept,
        # so the gates that use it keep working: that remake is allowed while they use it,
        # and the stored steps are written again unchanged (posted steps must equal them)
        # a make that renders exactly the stored draft changes nothing, so it is answered ok
        if spec is not None and sgt.render(spec).encode("utf-8") == data:
            return None, UNCHANGED
        same_older = _older_match(spec, mine)
        if same_older is None:
            return NAME_IN_USE.format(name=name, count=count), None
        return None, same_older
    lab = lab_package(_env_python())
    if lab is not None and _lab_made(lab / f"{name}.py"):
        return NAME_LAB.format(name=name), None
    return None, None


@bp.route("/api/pulse/cz-gate/slot-class", methods=["POST"])
def api_cz_gate_slot_class():
    """Make (or replace, while no gate uses it) a slot gate class draft:
    ``{class_name, slots, spectators, before?, after?}`` -> ``{ok, cls, qclass}`` or
    ``{ok: false, error, problems, step?}`` (``step`` names the refused step). Replacing an existing draft whose
    content would change answers 409 ``{confirm_replace: true}`` and writes nothing until the same request is
    sent again with ``confirm_replace`` true; identical content answers 200 without a write. Writes the draft only;
    saving it into the lab's code is the class save door's two-step."""
    from quam_state_manager.core import pulse_class_drafts as drafts
    from quam_state_manager.core import pulse_class_watch as watch
    store, err = _store_or_error()
    if err:
        return err
    data = request_body.json_object({})
    if not isinstance(data, dict):
        raise request_body.BadBody("A JSON object is required.")

    def refuse(problems):
        return jsonify(ok=False, error=problems[0], problems=problems), 400

    name = data.get("class_name")
    if not isinstance(name, str):
        return refuse([NAME_NOT_TEXT])
    slots = data.get("slots")
    if isinstance(slots, list):
        slots = [s if isinstance(s, (str, int, float, bool, type(None))) else repr(s) for s in slots]
    steps = {k: (() if data.get(k) is None else data.get(k)) for k in ("before", "after")}
    spec = sgt.SlotSpec(name, slots, data.get("spectators", False), steps["before"], steps["after"])
    problems = sgt.validate(spec)
    if problems:
        j = {"ok": False, "error": problems[0], "problems": problems}
        at = _step_at(steps, problems)
        if at:
            j["step"] = at
        return jsonify(j), 400
    pair = data.get("pair")
    if pair is not None:
        with store._lock:
            xy = _xy_step_refusal(store.merged, pair, steps)
        if xy:
            return jsonify(xy), 400
    with store._lock:
        taken, keep = _name_check(name, store.merged, spec)
    if taken:
        return jsonify(ok=False, error=taken, problems=[taken]), 409
    if keep is UNCHANGED:
        return jsonify(ok=True, cls=name, qclass=sgp.qclass_of(spec))
    source = sgt.render(keep or spec).encode("utf-8")
    previous = drafts.load_draft(current_app.instance_path, _scope(), name)
    if previous == source:
        return jsonify(ok=True, cls=name, qclass=sgp.qclass_of(spec))
    if previous is None:
        lab = lab_package(_env_python())
        target = lab / f"{name}.py" if lab else None
        previous = target.read_bytes() if target is not None and target.is_file() else None
    if previous is not None and previous != source and keep is None:
        if _gates_using(store.merged, sgp.qclass_of(spec)):
            text = NAME_IN_USE.format(name=name, count=_gates_using(store.merged, sgp.qclass_of(spec)))
            return jsonify(ok=False, error=text, problems=[text]), 409
        if data.get("confirm_replace") is not True:
            text = f"A class named {name} exists; making it again replaces its steps."
            return jsonify(ok=False, error=text, confirm_replace=True), 409
    try:
        src = sgt.render(keep or spec)
        drafts.save_draft(current_app.instance_path, _scope(), name, src)
    except (drafts.DraftError, ValueError) as exc:
        return refuse([str(exc)])
    watch.watch_for(current_app.instance_path, _scope(), name).adopt(src.encode("utf-8"))
    return jsonify(ok=True, cls=name, qclass=sgp.qclass_of(spec))
