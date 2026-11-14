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
    active (the pair's ``cz`` alias is never touched).
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import secrets
import threading
import time
from collections import OrderedDict
from typing import Any

from flask import jsonify, render_template, request

from quam_state_manager.core import chip_copy, cz_gate_build as cgb, cz_gate_create
from quam_state_manager.core import gate_check, gate_check_log, gate_intent, gate_timeline_view, pulse_catalog
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
                    "gates": sorted(n for n, m in macros.items() if cgb.is_cz_macro(n, m))})
    return out


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


def _classes(store, merged) -> list[dict]:
    manifest = _manifest(store)
    pcs = _pulse_classes()
    out = []
    for cls in (cgb.CZ_GATE, cgb.COUPLER_CZ_GATE):
        ok, why = _known_available(manifest, cls)
        slots = [{"field": s.field, "line": s.line, "line_words": _LINE_WORDS.get(s.line, s.line),
                  "required": s.required, "pulse_classes": pcs}
                 for s in cgb.GATE_CLASSES[cls]["slots"]]
        out.append({"class": cls, "label": _CLASS_LABELS[cls], "layout": "known",
                    "available": ok, "why": why, "slots": slots, "models": []})
    lab: dict[str, list] = {}
    for pid, p in sorted((merged.get("qubit_pairs") or {}).items()):
        macros = p.get("macros") if isinstance(p, dict) else None
        for name, m in (macros or {}).items() if isinstance(macros, dict) else ():
            cls = m.get("__class__") if isinstance(m, dict) else None
            if isinstance(cls, str) and cls not in cgb.GATE_CLASSES and cgb.is_cz_macro(name, m):
                lab.setdefault(cls, []).append([pid, name])
    for cls in sorted(lab):
        out.append({"class": cls, "label": cls.rsplit(".", 1)[-1] + " (copies the layout of a gate on this chip)",
                    "layout": "copied", "available": True, "why": "", "slots": [],
                    "models": lab[cls]})
    for cid, short in (("pair.cz_parametric", "ParametricCZGate"),
                       ("pair.stark_cz_gate", "StarkInducedCZGate")):
        out.append({"class": short, "label": short, "layout": "copied", "available": False,
                    "why": ("SM does not know the structure of this class and this chip has "
                            "no gate of it to copy."), "slots": [], "models": []})
    return out


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
    return {"pairs": list(dict.fromkeys(pairs)),
            "gate_name": data.get("gate_name") if isinstance(data.get("gate_name"), str) else "",
            "gate_class": data.get("gate_class") if isinstance(data.get("gate_class"), str) else "",
            "pulses": data.get("pulses") if isinstance(data.get("pulses"), dict) else {},
            "copy_from": data.get("copy_from") or None,
            "model_gate": tuple(model) if isinstance(model, (list, tuple)) else None,
            "macro_fields": data.get("macro_fields") if isinstance(data.get("macro_fields"), dict) else {}}


def _given_values(pulse: dict) -> dict:
    return {k: copy.deepcopy(v) for k, v in pulse.items()
            if k not in ("__class__", "id") and not (isinstance(v, str) and v.startswith("#"))}


def _plan_all(merged: dict, body: dict) -> list[tuple[str, dict]]:
    """``[(pair, plan)]``; the first pair seeds the others' field values."""
    out = []
    first = None
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
            for f in (first.get("operations") or {}):
                if f not in pulses:
                    pp = gate_intent.planned_pulse(first, f) or {}
                    pulses[f] = {"class": pp.get("__class__"), "fields": _given_values(pp)}
        res = cgb.plan(merged, pid, gate_name=body["gate_name"], gate_class=body["gate_class"],
                       pulses=pulses, copy_from=copy_from, model_gate=body["model_gate"],
                       macro_fields=body["macro_fields"] or None)
        if i == 0:
            first = res
        out.append((pid, res))
    return out


def _creates_sig(plan: dict) -> str:
    blob = json.dumps(plan.get("creates") or [], sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def plan_signature(body: dict, plans: list) -> str:
    blob = json.dumps({"body": {k: body[k] for k in sorted(body)},
                       "creates": [[pid, p.get("creates") or [], p.get("refusals") or []]
                                   for pid, p in plans]}, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def value_kind(value: Any, declared: str | None = None) -> str:
    """The kind of a field the person may type, decided from the state's own
    value type (never guessed from its digits): ``bool`` | ``int`` |
    ``float`` | ``str``. A null value takes the class's declared kind when
    the catalog knows it, else ``any`` (a number or text, as typed)."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if declared in ("int", "float", "bool", "str"):
        return declared
    return "any"


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
        out[k] = {"kind": value_kind(v, getattr(prm, "kind", None)),
                  "label": getattr(prm, "label", None) or k,
                  "unit": getattr(prm, "unit", "") or "",
                  "optional": v is None}
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
        if k == "__class__" or k in pulses or k in reset:
            continue
        v = macro[k]
        ref = isinstance(v, str) and v.startswith("#")
        out.append({"key": k, "value": v, "kind": value_kind(v),
                    "source": prov.get(k) or (cgb.KEPT_REF if ref else cgb.FROM_MODEL),
                    "reference": ref, "optional": v is None})
    return out


def _summary(pid: str, p: dict) -> dict:
    if "error" in p:
        return {"pair": pid, "ok": False, "refusals": p.get("refusals") or [p["error"]]}
    pulses = {f: gate_intent.planned_pulse(p, f) for f in (p.get("operations") or {})}
    return {"pair": pid, "ok": True, "refusals": [],
            "field_specs": {f: field_specs(v) for f, v in pulses.items()},
            "settings": _settings(p),
            "model_gate": list(p["model_gate"]) if p.get("model_gate") else None,
            "creates": len(p["creates"]), "paths": [c[0] for c in p["creates"]],
            "direction": p.get("direction"), "layout": p.get("layout"),
            "operations": p.get("operations"), "fields": p.get("fields"),
            "pulses": pulses, "sources": p.get("sources"),
            "renamed": p.get("renamed") or {}, "reset": p.get("reset") or []}


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
        out[(el, op.get("key"))] = float(cv) if isinstance(cv, (int, float)) else list(pay.get("i") or [])
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
                    lim = ln.get("limit_v")
                    edge = (lim[1] if v >= 0 else lim[0]) if lim else None
                    tail = f" This port's limit is {gate_timeline_view.fmt_num(edge)} V." if edge is not None else ""
                    return f"{ln['key']} would play {gate_timeline_view.fmt_num(v)} V.{tail}"
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
        classes = _classes(store, merged)
    want = request.args.get("pair") or ""
    selected = want if any(p["id"] == want for p in pairs) else ""
    from quam_state_manager.web import diagram_remove_routes as DR
    data = {"pairs": pairs, "selected": selected, "classes": classes,
            "env": DR.env_name(_env_python()), "sync": sync_now()}
    return render_template("_cz_gate_dialog.html", data=data, selected=selected,
                           unknown_pair=want if want and not selected else "")


@bp.route("/api/pulse/cz-gate/plan", methods=["POST"])
def api_cz_gate_plan():
    """Plan the gate on every asked pair; writes nothing."""
    store, err = _store_or_error()
    if err:
        return err
    body = _body()
    if not body["pairs"]:
        return jsonify(ok=False, error="Pick a pair."), 400
    with store._lock:
        plans = _plan_all(store.merged, body)
    return jsonify(ok=True, plan_sig=plan_signature(body, plans),
                   pairs=[_summary(pid, p) for pid, p in plans])


@bp.route("/api/pulse/cz-gate/check", methods=["POST"])
def api_cz_gate_check():
    """Check the planned gates with the lab's code on chip copies; the
    verdicts stay on the server under the returned token."""
    store, err = _store_or_error()
    if err:
        return err
    body = _body()
    if not body["pairs"]:
        return jsonify(ok=False, error="Pick a pair."), 400
    with store._lock:
        plans = _plan_all(store.merged, body)
        pre_seq = store.mutation_seq
    sig = plan_signature(body, plans)
    good = [(pid, p) for pid, p in plans if "error" not in p]
    gates = [(pid, body["gate_name"]) for pid, _ in good]
    creates = [c for _, p in good for c in p["creates"]]
    previews, skipped_by = {}, {}
    for pid, p in good:
        pv, sk = _previews(p)
        previews[(pid, body["gate_name"])] = pv
        skipped_by[pid] = sk
    res = run_check(store, _env_python(), gates, creates, previews) if gates else None
    out_pairs, verdicts, sigs, checks_by = [], {}, {}, {}
    for pid, p in plans:
        if "error" in p:
            out_pairs.append({"pair": pid, "gate": body["gate_name"], "ok": False,
                              "refusals": p.get("refusals") or [p["error"]],
                              "verdict": {"allowed": False, "state": cz_gate_create.NOT_CHECKED,
                                          "reason": " ".join(p.get("refusals") or [p["error"]])}})
            verdicts[pid] = {"allowed": False}
            continue
        entry = gate_check.gate_result(res or {}, pid, body["gate_name"])
        allowed, state, reason = cz_gate_create.may_create(
            res, entry, gate_class=body["gate_class"], layout=p.get("layout"))
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
        out_pairs.append({
            "pair": pid, "gate": body["gate_name"], "ok": True, "refusals": [],
            "checks": [{"name": k, "status": v.get("status"), "message": v.get("message")}
                       for k, v in ((entry or {}).get("checks") or {}).items()],
            "unchecked": (entry or {}).get("unchecked") or [],
            "timeline": tl, "tiles": gate_timeline_view.tiles(tl),
            "intent": gate_intent.intent_rows(p, entry),
            "previews_skipped": skipped_by.get(pid) or [],
            "paths": [c[0] for c in p["creates"]],
            "verdict": {"allowed": allowed, "state": state, "reason": reason,
                        "headline": headline(entry, tl, reason)}})
        verdicts[pid] = {"allowed": allowed, "state": state, "reason": reason}
        # what the check record needs: "checked" only when the lab ran every check of THIS gate
        full = bool(res and res.get("status") == "ok" and res.get("checked") and entry
                    and entry.get("ran") and not entry.get("unchecked") and not entry.get("partly_checked")
                    and not any((c or {}).get("status") == "fail" for c in (entry.get("checks") or {}).values()))
        checks_by[pid] = {"all_checked": full,
                          "why_not_checked": None if full else (reason or (res or {}).get("why_not_checked")
                                                                or "not every check ran")}
        sigs[pid] = _creates_sig(p)
    seq = (res or {}).get("seq")
    tok = _remember({"store_id": chip_copy.store_id(store),
                     "seq": seq if seq is not None else pre_seq,
                     "plan_sig": sig, "body": body, "verdicts": verdicts, "sigs": sigs,
                     "checks": checks_by})
    return jsonify(ok=True, status=(res or {}).get("status") or "not-run", sync=sync_now(),
                   why_not_checked=(res or {}).get("why_not_checked"),
                   leftover=(res or {}).get("leftover"),
                   plan_sig=sig, check_token=tok, pairs=out_pairs)


def _refuse(msg: str, code: int = 409, **extra):
    return jsonify(ok=False, error=msg, **extra), code


@bp.route("/api/pulse/cz-gate/create", methods=["POST"])
def api_cz_gate_create():
    """Write the checked gates in one change group (see the module docstring).
    Person-only (``callers.PERSON_ONLY``)."""
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
        plans = dict(_plan_all(store.merged, body))
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
        done = 0
        try:
            for pid in todo:
                for path, value in plans[pid]["creates"]:
                    modifier.create_subtree(path, copy.deepcopy(value), group_id=gid)
                    done += 1
        except Exception as exc:  # noqa: BLE001 -- all or nothing
            for _ in range(done):
                try:
                    modifier.undo()
                except Exception:  # noqa: BLE001
                    break
            logger.exception("cz gate create failed; rolled back")
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
