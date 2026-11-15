"""A CZ gate's status line and its actions (routes registered on ``bp``).

``GET /pulse/cz-gate/status?pair=&gate=`` renders the one status line of a
gate (:mod:`core.gate_status`), the same partial the pair detail and the
pulse detail of a gate pulse show. Its actions:

* "Edit values" opens the gate's pulses in the pulse detail;
* "Make active": ``POST /api/pulse/cz-gate/make-active`` ``{pair, gate}``
  refuses missing or changed SM-made class files using the status line check,
  then checks the EXISTING gate with the lab's code again (no creates), then sets
  the pair's ``cz`` alias to ``#./<gate>`` as one ordinary edit (one Ctrl+Z),
  guarded by the store sequence the check ran at, and records the check;
* "Re-check", on a gate whose class file changed since it was checked:
  ``POST /api/pulse/cz-gate/recheck`` ``{pair, gate}`` runs the lab check of
  the EXISTING gate with the class files as they are now and, when the gate
  loads and runs, writes the new file record (``extras.sm_class_files``) as
  one ordinary edit (one Ctrl+Z), guarded like Make active;
* "Delete gate": ``GET /pulse/cz-gate/delete-offer?pair=&gate=`` shows what
  goes (:func:`core.gate_parts.gate_parts`); its button posts the existing
  delete-together door with ``gate`` set.

Everything writes the working copy only; the live files change only through
Apply.
"""
from __future__ import annotations

import logging

from flask import current_app, g, jsonify, make_response, render_template, request

from quam_state_manager.core import cz_gate_build as cgb
from quam_state_manager.core import cz_gate_create, gate_check, gate_check_log, gate_parts, gate_status
from quam_state_manager.core import quam_config_save
from quam_state_manager.web import routes as R
from quam_state_manager.web.request_body import json_object, json_text
from quam_state_manager.web.routes import bp

logger = logging.getLogger(__name__)

#: the one refusal of a gate write on a chip that is not the live one
ARCHIVE_REFUSAL = "This chip is a read-only archive: open the live chip to change it."


def _check_folder(ctx):
    try:
        return gate_check_log.chip_dir(R._history(), ctx["path"])
    except Exception:  # noqa: BLE001 -- no chip folder: no check record
        return None


def _env_info(store):
    """(missing classes, env name) of the selected env's manifest."""
    try:
        manifest = R._live_env_manifest(store)
    except Exception:  # noqa: BLE001
        manifest = None
    missing = list((manifest or {}).get("missing_classes") or [])
    env = getattr(store, "_type_manifest_env", None)
    name = None
    if env:
        from pathlib import Path
        p = Path(env).parent
        if p.name.lower() in ("bin", "scripts") and p.parent.name:
            p = p.parent
        name = p.name or None
    return missing, name


def _scope(ctx) -> str:
    """The chip's key the class drafts are kept under (as the gate dialog)."""
    try:
        return R._ctx_chip_key(ctx or R._active_ctx()) or "no-chip"
    except Exception:  # noqa: BLE001
        return "no-chip"


def _class_path(ctx):
    """``qclass -> path`` of the published SM class files of this chip."""
    try:
        instance = current_app.instance_path
    except RuntimeError:
        instance = None
    scope = _scope(ctx)
    return lambda qclass: quam_config_save.published_path(instance, scope, qclass)


def gate_line(store, ctx, pair: str, gate: str, *, env=None, folder=None,
              note: str | None = None, note_tone: str = "neutral") -> dict:
    """Everything ``_gate_status.html`` renders for one gate."""
    missing, env_name = env if env is not None else _env_info(store)
    folder = folder if folder is not None else _check_folder(ctx or {})
    items = gate_status.status(store, ctx, pair, gate, check_folder=folder,
                               missing_classes=missing, env_name=env_name,
                               class_path=_class_path(ctx))
    pills = [i for i in items if i.get("pill")]
    notices = [i for i in items if i["key"] == "needs"]
    recheck = any(i["key"] == "class" and i["text"] == gate_status.FILE_CHANGED for i in items)
    # the details list the check first: its reason and time are not in the pill
    details = ([i for i in items if i["key"] == "check"]
               + [i for i in items if i["key"] != "check"])
    active = any(i["key"] == "active" and i["text"] == "active" for i in items)
    present = not any(i["key"] == "gone" for i in items)
    edit = []
    if present:
        try:
            parts = gate_parts.gate_parts(store.merged, pair, gate)
            edit = (parts["pulses"] or parts["operations"])[:4]
        except Exception:  # noqa: BLE001 -- the line stays without the link
            edit = []
    return {"pair": pair, "gate": gate, "items": items, "pills": pills,
            "details": details, "active": active, "notices": notices, "recheck": recheck,
            "present": present, "edit_paths": edit, "note": note,
            "note_tone": note_tone}


def gate_list(store, ctx, pair: str) -> dict:
    """The pair detail's gate list: its lines and the one head note."""
    lines = gate_lines(store, ctx, pair)
    return {"pair": pair, "lines": lines, "count": len(lines),
            "no_active": bool(lines) and not any(ln["active"] for ln in lines)}


def parts_text(n: int) -> str:
    """"1 part" / "2 parts": the count of what went with a gate."""
    return f"{n} part" if n == 1 else f"{n} parts"


def list_answer(store, ctx, pair: str, *, notice: dict | None = None,
                gate: str | None = None, row_note: str | None = None, code: int = 200):
    """The whole refreshed gate list in place of the zone the press came from:
    *notice* ``{tone, title, text}`` sits in the zone above the rows (never in a
    row, so it outlives a row that went); *row_note* goes on *gate*'s row."""
    gl = gate_list(store, ctx, pair)
    gl["notice"] = notice
    if row_note:
        for ln in gl["lines"]:
            if ln["gate"] == gate:
                ln["note"], ln["note_tone"] = row_note, "danger"
    resp = make_response(render_template("_gate_status_list.html", gate_lines=gl), code)
    resp.headers["HX-Retarget"] = "closest .gate-status-list"
    resp.headers["HX-Reswap"] = "outerHTML"
    return resp


def gone_notice(pair: str, gate: str) -> dict:
    """Say what is known without guessing which window deleted the gate."""
    return {"tone": "warn", "title": f"{gate} is no longer on pair {pair}",
            "text": "This gate is gone. The list below is the chip as it is now."}


def by_name_note(holders: list[str]) -> str:
    """The zone's sentence for a delete a by-name slot blocks: what blocks it
    and where to change that (the zone has no delete-anyway door)."""
    return ("Not deleted: this gate's operation is played by name in " + ", ".join(holders)
            + ", so the gate cannot be deleted while a slot uses it. Point those slots at "
            "another operation in Json Tree, then delete again.")


def _pair_exists(store, pair: str) -> bool:
    with store._lock:
        pairs = store.merged.get("qubit_pairs")
        return isinstance(pairs, dict) and isinstance(pairs.get(pair), dict)


def _broken_line(pair: str, gate: str, exc: BaseException) -> dict:
    """The honest line of a gate whose status could not be computed."""
    item = {"key": "error", "text": "status could not be read", "tone": "warn",
            "why": f"{type(exc).__name__}: {exc}", "pill": True}
    return {"pair": pair, "gate": gate, "items": [item], "pills": [item],
            "details": [item], "active": False, "present": False, "edit_paths": [],
            "notices": [], "recheck": False,
            "note": None, "note_tone": "neutral"}


def gate_lines(store, ctx, pair: str) -> list[dict]:
    """One line per CZ gate of the pair (the pair detail's list)."""
    if store is None:
        return []
    env = _env_info(store)
    folder = _check_folder(ctx or {})
    out = []
    for gate in gate_status.cz_gates(store.merged, pair):
        try:
            out.append(gate_line(store, ctx, pair, gate, env=env, folder=folder))
        except Exception as exc:  # noqa: BLE001 -- one gate's line never vanishes
            logger.warning("gate status for %s/%s failed", pair, gate, exc_info=True)
            out.append(_broken_line(pair, gate, exc))
    return out


def _args():
    return (request.args.get("pair") or "").strip(), (request.args.get("gate") or "").strip()


def _gate_problem(store, pair: str, gate: str) -> str | None:
    """Why (pair, gate) is not a CZ gate of this chip, or None."""
    if not pair or not gate:
        return "Name a pair and a gate."
    if gate == "cz":
        return "cz is the pair's name for its active gate, not a gate."
    with store._lock:
        pairs = store.merged.get("qubit_pairs")
        pobj = pairs.get(pair) if isinstance(pairs, dict) else None
        macros = pobj.get("macros") if isinstance(pobj, dict) else None
        macro = macros.get(gate) if isinstance(macros, dict) else None
        if not isinstance(pobj, dict):
            return f"This chip has no pair {pair}."
        if not isinstance(macro, dict) or not cgb.is_cz_macro(gate, macro):
            return f"Pair {pair} has no CZ gate named {gate}."
    return None


@bp.route("/pulse/cz-gate/status")
def cz_gate_status():
    store = R._store()
    if not store:
        return render_template("_status.html", message="No state loaded", level="warning")
    pair, gate = _args()
    bad = _gate_problem(store, pair, gate)
    if bad:
        return render_template("_status.html", message=bad, level="error"), 404
    try:
        line = gate_line(store, R._active_ctx(), pair, gate)
    except Exception as exc:  # noqa: BLE001 -- an honest line, never a 500
        logger.warning("gate status for %s/%s failed", pair, gate, exc_info=True)
        line = _broken_line(pair, gate, exc)
    return render_template("_gate_status.html", line=line)


@bp.route("/pulse/cz-gate/list")
def cz_gate_list():
    """The pair detail's whole gate list (re-fetched by the zone after an undo
    or redo, so a gate that came back shows without a reload)."""
    store = R._store()
    pair = (request.args.get("pair") or "").strip()
    if not store or not pair or not _pair_exists(store, pair):
        return "", 204
    gl = gate_list(store, R._active_ctx(), pair)
    return render_template("_gate_status_list.html", gate_lines=gl)


@bp.route("/pulse/cz-gate/offer-close")
def cz_gate_offer_close():
    """Cancel / Close of the delete offer: the empty slot. Stages nothing and
    calls no delete door; works on every page, with or without the script."""
    return ""


@bp.route("/pulse/cz-gate/delete-offer")
def cz_gate_delete_offer():
    """What a one-step delete of the gate takes (read only; the button posts
    the delete-together door, which computes the set again)."""
    store = R._store()
    if not store:
        return render_template("_status.html", message="No state loaded", level="warning")
    pair, gate = _args()
    bad = _gate_problem(store, pair, gate)
    in_list = (request.args.get("scope") or "") == "list"
    if bad and in_list and gate and gate != "cz" and _pair_exists(store, pair):
        # a gate another window deleted: say so and show the list as it is
        return list_answer(store, R._active_ctx(), pair, notice=gone_notice(pair, gate))
    if bad:
        return render_template("_status.html", message=bad, level="error"), 404
    with store._lock:
        parts = gate_parts.gate_parts(store.merged, pair, gate)
    return render_template("_gate_delete_offer.html", pair=pair, gate=gate, parts=parts,
                           in_list=in_list)


def _refuse(message: str, code: int, *, htmx: bool, pair: str, gate: str, store, ctx,
            **extra):
    if htmx and store is not None and str(_body_scope()) == "list" and code == 404:
        resp = make_response(render_template("_status.html", message=message,
                                             level="error"), 404)
        return resp
    if htmx and store is not None and str(_body_scope()) == "list":
        gl = gate_list(store, ctx, pair)
        for ln in gl["lines"]:
            if ln["gate"] == gate:
                ln["note"], ln["note_tone"] = message, "danger"
        resp = make_response(render_template("_gate_status_list.html", gate_lines=gl), 200)
        resp.headers["HX-Reswap"] = "outerHTML"
        return resp
    if htmx and store is not None:
        try:
            line = gate_line(store, ctx, pair, gate, note=message, note_tone="danger")
        except Exception as exc:  # noqa: BLE001 -- the refusal still reaches the user
            logger.warning("gate status for %s/%s failed", pair, gate, exc_info=True)
            line = dict(_broken_line(pair, gate, exc), note=message, note_tone="danger")
        resp = make_response(render_template("_gate_status.html", line=line), 200)
        resp.headers["HX-Reswap"] = "outerHTML"
        return resp
    return jsonify(ok=False, error=message, **extra), code


def _text_of(resp) -> str:
    """The error text of a refusal response (JSON ``error``), for a line."""
    r = resp[0] if isinstance(resp, tuple) else resp
    try:
        data = r.get_json(silent=True) or {}
    except Exception:  # noqa: BLE001
        data = {}
    return str(data.get("error") or "This change is not allowed now.")


def _body_scope() -> str:
    try:
        return str(json_object(request.form.to_dict).get("scope") or "")
    except Exception:  # noqa: BLE001
        return ""


def _cannot_run(entry: dict | None) -> str | None:
    """The check's own message when the gate ran and failed."""
    if not entry:
        return None
    fails = [c["message"] for c in entry["checks"].values() if c["status"] == "fail"]
    if fails:
        return " ".join(fails[:2])
    return None


def _files_text(names: list[str]) -> str:
    return ("The class file " if len(names) == 1 else "The class files ") + ", ".join(names)


def _class_file_refusal(states: list[tuple[dict, str]]) -> str | None:
    """The Make active refusal of a gate whose SM-made class files are not
    in place (``gate_status.class_file_states``, as the status line), naming
    each file; None when every file is present and unchanged, or the gate
    records none."""
    if not all(gate_status.names_file(f) for f, _st in states):
        return ("Its class file record has no file name, so SM cannot tell which file the "
                "gate needs. Delete the gate and make it again. It was not made active.")
    if not all(gate_status.has_hash(f) for f, _st in states):
        return ("Its class file record has no content hash, so SM cannot tell whether the file "
                "is the one the gate was checked with. Delete the gate and make it again. "
                "It was not made active.")
    gone = [f["file"] for f, st in states if st == "missing"]
    moved = [f["file"] for f, st in states if st == "changed"]
    parts = []
    if gone:
        are = "is" if len(gone) == 1 else "are"
        parts.append(f"{_files_text(gone)} {are} not in quam_config. "
                     f"Save {' and '.join(gone)} to quam_config first.")
    if moved:
        parts.append(f"{_files_text(moved)} changed since the gate was checked. "
                     "Re-check the gate first.")
    return " ".join(parts) + " It was not made active." if parts else None


@bp.route("/api/pulse/cz-gate/make-active", methods=["POST"])
def api_cz_gate_make_active():
    """Make a gate the pair's active CZ gate (see the module doc)."""
    ctx = R._active_ctx()
    store = R._store()
    modifier = R._modifier()
    htmx = bool(request.headers.get("HX-Request"))
    if not store or not modifier:
        return jsonify(ok=False, error=R._no_chip_msg()), 400
    body = json_object(request.form.to_dict)
    pair, gate = json_text(body, "pair"), json_text(body, "gate")
    kw = dict(htmx=htmx, pair=pair, gate=gate, store=store, ctx=ctx)
    if ctx and (ctx.get("origin") or "live") != "live":
        return _refuse(ARCHIVE_REFUSAL, 409, **kw)
    lk = R._agent_edit_lock_refusal(ctx)
    if lk is not None:
        return _refuse(_text_of(lk), 409, **kw) if htmx else lk
    bad = _gate_problem(store, pair, gate)
    if bad:
        return _refuse(bad, 404 if htmx else 400, **kw)
    with store._lock:
        macros = store.merged["qubit_pairs"][pair]["macros"]
        macro = macros[gate]
        if gate_parts.active_gate(pair, macros) == gate:
            return _refuse(f"{gate} is already the active CZ gate of {pair}.", 409, **kw)
        alias = macros.get("cz")
        cls = str(macro.get("__class__") or "")
        seq0 = store.mutation_seq
    if isinstance(alias, dict):
        return _refuse("The pair's cz entry is a gate itself, not a name of one, "
                       "so it cannot be pointed at another gate.", 400, **kw)
    files_bad = _class_file_refusal(gate_status.class_file_states(macro, _class_path(ctx)))
    if files_bad:
        return _refuse(files_bad, 409, **kw)
    try:
        python_path = R.config_generator.get_selected_env(R.current_app.instance_path)
    except Exception:  # noqa: BLE001
        python_path = None
    check = gate_check.check_gates(store, python_path, [(pair, gate)])
    entry = gate_check.gate_result(check, pair, gate)
    why = _cannot_run(entry)
    if why or (check.get("checked") and entry is not None and not entry.get("ran")):
        return _refuse(f"The gate does not run with the lab's code: "
                       f"{why or check.get('why_not_checked') or 'it did not run'}. "
                       "It was not made active.", 400, **kw, check=check)
    if not check.get("all_checked") and cls not in cgb.GATE_CLASSES:
        return _refuse("The lab's code could not check this gate "
                       f"({check.get('why_not_checked') or check.get('error') or 'no reason'}). "
                       "A gate of a class SM does not know becomes active only after "
                       "a check. It was not made active.", 400, **kw, check=check)
    path = f"qubit_pairs.{pair}.macros.cz"
    value = f"#./{gate}"
    info = g.lab_info = {"notes": []}
    release = R._lab_hold(store, [path])
    try:
        with store._lock:
            moved = store.mutation_seq != seq0 or (
                check.get("seq") is not None
                and not gate_check.chip_copy.store_at_seq(store, check))
            if moved:
                return _refuse("The chip changed while the gate was checked. Nothing was "
                               "written; press Make active again.", 409, **kw, stale=True)
            refusal = R._lab_write_refusal(store, [(path, value)], info=info)
            if refusal:
                return _refuse(str(refusal[0]), 400, **kw)
            if "cz" in store.merged["qubit_pairs"][pair]["macros"]:
                modifier.set_value(path, value)
            else:
                modifier.create_subtree(path, value)
    finally:
        release()
    try:
        rec = gate_check_log.record_check(R._history(), ctx["path"], store.merged,
                                          pair, gate, check)
    except Exception as exc:  # noqa: BLE001 -- the record never fails the write
        rec = {"ok": False, "error": str(exc)}
    note = None
    if not rec.get("ok"):
        note = (f"{gate} is now active, but the check record could not be saved: "
                f"{rec.get('error')}.")
    if htmx and str(body.get("scope") or "") == "list":
        # the whole list: its head and the row that was active before change too
        gl = gate_list(store, ctx, pair)
        for ln in gl["lines"]:
            if ln["gate"] == gate and note:
                ln["note"], ln["note_tone"] = note, "warn"
        resp = make_response(render_template("_gate_status_list.html", gate_lines=gl)
                             + R._tray_oob())
        resp.headers["HX-Trigger"] = "pulses-changed"
        return R._lab_toast(resp)
    if htmx:
        resp = make_response(render_template(
            "_gate_status.html", line=gate_line(store, ctx, pair, gate, note=note,
                                                note_tone="warn")) + R._tray_oob())
        resp.headers["HX-Trigger"] = "pulses-changed"
        return R._lab_toast(resp)
    return jsonify(ok=True, active=gate, path=path, tray_html=R._tray_html(),
                   checked=bool(check.get("all_checked")), record_error=None if not note
                   else rec.get("error"))


def _line_response(store, ctx, pair, gate, body, note, note_tone="warn"):
    """The htmx answer of a gate write: the list, or the one line."""
    if str(body.get("scope") or "") == "list":
        gl = gate_list(store, ctx, pair)
        for ln in gl["lines"]:
            if ln["gate"] == gate and note:
                ln["note"], ln["note_tone"] = note, note_tone
        resp = make_response(render_template("_gate_status_list.html", gate_lines=gl)
                             + R._tray_oob())
    else:
        resp = make_response(render_template(
            "_gate_status.html", line=gate_line(store, ctx, pair, gate, note=note,
                                                note_tone=note_tone)) + R._tray_oob())
    resp.headers["HX-Trigger"] = "pulses-changed"
    return R._lab_toast(resp)


@bp.route("/api/pulse/cz-gate/recheck", methods=["POST"])
def api_cz_gate_recheck():
    """Check a gate whose SM-made class file changed with the files as they
    are now, then write the new file record (see the module doc)."""
    ctx = R._active_ctx()
    store = R._store()
    modifier = R._modifier()
    htmx = bool(request.headers.get("HX-Request"))
    if not store or not modifier:
        return jsonify(ok=False, error=R._no_chip_msg()), 400
    body = json_object(request.form.to_dict)
    pair, gate = json_text(body, "pair"), json_text(body, "gate")
    kw = dict(htmx=htmx, pair=pair, gate=gate, store=store, ctx=ctx)
    if ctx and (ctx.get("origin") or "live") != "live":
        return _refuse(ARCHIVE_REFUSAL, 409, **kw)
    lk = R._agent_edit_lock_refusal(ctx)
    if lk is not None:
        return _refuse(_text_of(lk), 409, **kw) if htmx else lk
    bad = _gate_problem(store, pair, gate)
    if bad:
        return _refuse(bad, 404 if htmx else 400, **kw)
    with store._lock:
        macro = store.merged["qubit_pairs"][pair]["macros"][gate]
        old = gate_status.sm_class_files(macro)
        cls = str(macro.get("__class__") or "")
        seq0 = store.mutation_seq
    if not old:
        return _refuse(f"{gate} uses no class SM made, so there is no class file to "
                       "check again.", 409, **kw)
    try:
        new = quam_config_save.class_files(current_app.instance_path, _scope(ctx),
                                           classes=[f.get("qclass") for f in old])
    except quam_config_save.ClassFileError as exc:
        return _refuse(str(exc), 409, **kw)
    if {f.get("qclass") for f in new} != {f.get("qclass") for f in old}:
        return _refuse("A class file of this gate is not saved to quam_config any more, so "
                       "it cannot be checked again. Save the class to quam_config first.",
                       409, **kw)
    try:
        python_path = R.config_generator.get_selected_env(current_app.instance_path)
    except Exception:  # noqa: BLE001
        python_path = None
    check = gate_check.check_gates(store, python_path, [(pair, gate)])
    entry = gate_check.gate_result(check, pair, gate)
    allowed, _state, reason = cz_gate_create.may_create(
        check, entry, gate_class=cls, layout="known", sm_classes=True)
    if not allowed:
        return _refuse(f"The gate did not pass the lab check with the class files as they "
                       f"are now: {reason} The gate's file record was not changed.", 400,
                       **kw, check=check)
    moved = quam_config_save.class_files_moved(current_app.instance_path, _scope(ctx), new)
    if moved:
        return _refuse(moved, 409, **kw)
    path = f"qubit_pairs.{pair}.macros.{gate}.extras.sm_class_files"
    info = g.lab_info = {"notes": []}
    release = R._lab_hold(store, [path])
    try:
        with store._lock:
            moved = store.mutation_seq != seq0 or (
                check.get("seq") is not None
                and not gate_check.chip_copy.store_at_seq(store, check))
            if moved:
                return _refuse("The chip changed while the gate was checked. Nothing was "
                               "written; press Re-check again.", 409, **kw, stale=True)
            refusal = R._lab_write_refusal(store, [(path, new)], info=info)
            if refusal:
                return _refuse(str(refusal[0]), 400, **kw)
            modifier.set_value(path, new)
    finally:
        release()
    try:
        rec = gate_check_log.record_check(R._history(), ctx["path"], store.merged,
                                          pair, gate, check)
    except Exception as exc:  # noqa: BLE001 -- the record never fails the write
        rec = {"ok": False, "error": str(exc)}
    note = None
    if not rec.get("ok"):
        note = (f"{gate} was checked again, but the check record could not be saved: "
                f"{rec.get('error')}.")
    if htmx:
        return _line_response(store, ctx, pair, gate, body, note)
    return jsonify(ok=True, path=path, files=new, tray_html=R._tray_html(),
                   record_error=None if not note else rec.get("error"))


def delete_gate_together(ctx, modifier, body: dict):
    """The delete-together door for a whole CZ gate (``body["gate"]`` set).

    The set is :func:`core.gate_parts.gate_parts` of the state as it is now;
    paths in *body* are ignored. An active gate needs ``confirm_active`` "1",
    and then the pair's ``cz`` name goes in the same group. The write is the
    batch door's own (one group, the lab check, atomic rollback)."""
    store = modifier.store
    htmx = bool(request.headers.get("HX-Request"))
    pair, gate = json_text(body, "pair"), json_text(body, "gate")
    in_list = htmx and str(body.get("scope") or "") == "list"

    def say(message, code, **extra):
        if in_list and _pair_exists(store, pair):
            if _gate_problem(store, pair, gate):
                return list_answer(store, ctx, pair, notice=gone_notice(pair, gate))
            return list_answer(store, ctx, pair, gate=gate, row_note=message)
        if htmx:
            return make_response(render_template(
                "_gate_fail.html", message=message), 200)
        return jsonify(ok=False, error=message, **extra), code

    if ctx and (ctx.get("origin") or "live") != "live":
        return say(ARCHIVE_REFUSAL, 409)
    lk = R._agent_edit_lock_refusal(ctx)
    if lk is not None:
        return say(_text_of(lk), 409) if htmx else lk
    with store._lock:
        parts = gate_parts.gate_parts(store.merged, pair, gate)
    if not parts["ok"]:
        return say(" ".join(parts["refusals"]), 409, refusals=parts["refusals"])
    paths = list(parts["paths"])
    if parts["active"]:
        if str(body.get("confirm_active") or "") != "1":
            return say(f"{gate} is the active CZ gate of pair {pair}. After the delete "
                       f"pair {pair} has no active CZ gate. Confirm to delete it with "
                       "the pair's cz name.", 409, needs_confirm=True)
        paths.append(parts["alias"])
    by_name = R._by_name_cut(store, paths)
    if by_name and str(body.get("force") or "") != "1":
        if in_list and _pair_exists(store, pair) and not _gate_problem(store, pair, gate):
            return list_answer(store, ctx, pair, gate=gate, row_note=by_name_note(by_name))
        return R._by_name_refused_json(by_name)
    payload = {"updates": [{"dot_path": p, "delete": True} for p in paths],
               "group": "new", "expect_chip": body.get("expect_chip") or "",
               "force_chip": body.get("force_chip")}
    resp = R._field_edit_batch_impl(payload, pulse_door=True)
    if not htmx:
        return resp
    r = resp[0] if isinstance(resp, tuple) else resp
    data = r.get_json(silent=True) or {}
    if data.get("ok"):
        done = f"Deleted {gate} and {parts_text(len(paths) - 1)}. Ctrl+Z brings them back."
        if in_list:
            out = list_answer(store, ctx, pair, notice={
                "tone": "note", "title": f"{gate} deleted", "text": done})
            out.set_data(out.get_data(as_text=True) + R._tray_oob())
        else:
            out = make_response(render_template(
                "_status.html", level="success", message=done) + R._tray_oob())
        out.headers["HX-Trigger"] = "pulses-changed, diagnostics-changed"
        return out
    return say(data.get("error") or "Nothing was deleted.", 400)
