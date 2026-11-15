"""The pulse-class edit screen (``/pulse-class``).

A form (the template region's fields and formula), a code pane (the whole
draft file) and a waveform preview. The draft lives in the instance folder
(``core.pulse_class_drafts``); the pane and an outside editor both write it,
and ``core.pulse_class_watch`` keeps them apart: the settle rule on reads, a
question naming the lost side when both changed.

The preview runs in the project's chosen env (or one SM discovered); SM never
imports the class itself.
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template, request

from quam_state_manager.core import config_generator
from quam_state_manager.core import pulse_class_drafts as D
from quam_state_manager.core import pulse_class_template as T
from quam_state_manager.core import pulse_class_watch as W
from quam_state_manager.core import quam_config_save as Q
from quam_state_manager.web.request_body import json_object

pulse_class_page_bp = Blueprint("pulse_class_page", __name__)

NEW_CLASS_FORMULA = "amplitude * np.ones(n)"


def _r():
    from quam_state_manager.web import routes  # noqa: PLC0415 - avoid an import cycle
    return routes


def _scope() -> str:
    r = _r()
    return r._ctx_chip_key(r._active_ctx()) or "no-chip"


def _inst() -> str:
    return current_app.instance_path


def _payload(cls: str, text: str | None = None, info: dict | None = None, sha: str | None = None) -> dict:
    inst, scope = _inst(), _scope()
    if text is None:
        data = D.load_draft(inst, scope, cls)
        if data is None:
            raise D.DraftError(f"There is no draft named {cls}.")
        text = data.decode("utf-8", errors="replace")
        sha = D.sha256_bytes(data)
        info = W._describe(text)
    return {"ok": True, "cls": cls, "text": text, "sha": sha, "info": info,
            "path": str(D.draft_path(inst, scope, cls)),
            "status": W.status(inst, scope, cls)["line"],
            "target": W.get_target(inst, scope, cls) or "",
            "last_preview": D.last_preview(inst, scope, cls)}


def _err(msg: str, code: int = 400):
    return jsonify({"ok": False, "error": msg}), code


@pulse_class_page_bp.route("/pulse-class")
def pulse_class_page():
    r = _r()
    from quam_state_manager.web.port_play_page import _chosen_env, _envs  # noqa: PLC0415
    cls = (request.args.get("cls") or "").strip()
    python, _source = _chosen_env(r._active_ctx())
    store = r._store()
    unused = set(Q.unused_files(_inst(), _scope(), store.merged)) if store else set()
    tpl = dict(page="pulse_class", pce_cls=cls, pce_drafts=D.list_drafts(_inst(), _scope()),
               pce_unused=unused,
               pce_python=python or "", pce_envs=_envs(), pce_partial=r._is_htmx(),
               pce_previous_line=D.PREVIOUS_LINE)
    return render_template("pulse_class_edit.html", **r._ctx(**tpl))


@pulse_class_page_bp.route("/pulse-class/new", methods=["POST"])
def pulse_class_new():
    data = json_object()
    cls = str(data.get("cls") or "").strip()
    spec = T.ClassSpec(cls, [T.FieldSpec("amplitude", "float", 0.1, "V"),
                             T.FieldSpec("length", "int", 100, "ns")], NEW_CLASS_FORMULA)
    try:
        if D.load_draft(_inst(), _scope(), cls) is not None:
            return _err(f"A draft named {cls} already exists; open it from the list.", 409)
        src = T.render(spec)
        D.save_draft(_inst(), _scope(), cls, src)
    except (D.DraftError, T.TemplateError) as exc:
        return _err(str(exc))
    W.watch_for(_inst(), _scope(), cls).adopt(src.encode("utf-8"))
    return jsonify({"ok": True, "cls": cls})


@pulse_class_page_bp.route("/pulse-class/load")
def pulse_class_load():
    try:
        return jsonify(_payload(str(request.args.get("cls") or "")))
    except D.DraftError as exc:
        return _err(str(exc), 404)


def _save_result(cls: str, res: dict):
    if not res.get("ok"):
        return jsonify(res), 409 if res.get("conflict") or res.get("base_unknown") else 400
    return jsonify(_payload(cls, res["text"], res["info"], res["sha"]) | {"kept": res["kept"]})


@pulse_class_page_bp.route("/pulse-class/save", methods=["POST"])
def pulse_class_save():
    data = json_object()
    cls = str(data.get("cls") or "")
    keep = data.get("keep") or None
    try:
        res = W.save_from_pane(_inst(), _scope(), cls, str(data.get("text") or ""),
                               data.get("base_sha") or None, keep=keep,
                               file_sha=data.get("file_sha") or None,
                               region_ok=data.get("hand_edit") is True)
    except D.DraftError as exc:
        return _err(str(exc))
    return _save_result(cls, res)


@pulse_class_page_bp.route("/pulse-class/form", methods=["POST"])
def pulse_class_form():
    """Rewrite only the template region from the form's fields and formula."""
    data = json_object()
    cls = str(data.get("cls") or "")
    try:
        fields = [T.FieldSpec(str(f.get("name") or ""), str(f.get("type") or "float"),
                              f.get("default"), str(f.get("unit") or ""))
                  for f in (data.get("fields") or []) if isinstance(f, dict)]
        spec = T.ClassSpec(cls, fields, str(data.get("formula") or ""))
        cur = D.load_draft(_inst(), _scope(), cls)
        if cur is None:
            return _err(f"There is no draft named {cls}.", 404)
        text = T.rewrite(str(data.get("text") or cur.decode("utf-8", errors="replace")), spec)
        res = W.save_from_pane(_inst(), _scope(), cls, text, data.get("base_sha") or None,
                               keep=data.get("keep") or None, file_sha=data.get("file_sha") or None,
                               region_ok=True)
    except (D.DraftError, T.TemplateError, TypeError, ValueError) as exc:
        return _err(str(exc))
    return _save_result(cls, res)


@pulse_class_page_bp.route("/pulse-class/poll")
def pulse_class_poll():
    cls = str(request.args.get("cls") or "")
    try:
        w = W.watch_for(_inst(), _scope(), cls)
    except D.DraftError as exc:
        return _err(str(exc))
    res = w.poll()
    res["status_line"] = W.status(_inst(), _scope(), cls)["line"]
    if res["status"] == "changed" and request.args.get("dirty") == "1":
        res["conflict"] = W.conflict_block(w.path, res["sha"])
    return jsonify(res)


@pulse_class_page_bp.route("/pulse-class/preview", methods=["POST"])
def pulse_class_preview():
    data = json_object()
    cls = str(data.get("cls") or "")
    python = str(data.get("python") or "").strip()
    if not python or not config_generator.known_interpreter(python, _inst()):
        return _err("Choose a Python environment SM found on this computer.")
    try:
        res = D.preview(python, _inst(), _scope(), cls)
    except D.DraftError as exc:
        return _err(str(exc))
    return jsonify(res)


@pulse_class_page_bp.route("/pulse-class/save-file", methods=["POST"])
def pulse_class_save_file():
    data = json_object()
    cls = str(data.get("cls") or "")
    try:
        st = W.save_to_target(_inst(), _scope(), cls, str(data.get("target") or "").strip(),
                              replace_sha=data.get("replace_sha") or None)
    except D.DraftError as exc:
        return _err(str(exc))
    except OSError as exc:
        return _err(f"The class file could not be written: {exc}")
    if not st.get("ok"):
        return jsonify(st), 409
    target = W.get_target(_inst(), _scope(), cls)
    record_error = _record_class_write(target, (W._target_record(_inst(), _scope(), cls) or {}).get("sha"))
    return jsonify({"ok": True, "status": st["line"], "backup": st["backup"],
                    "target": target, "record_error": record_error})


def _actor() -> str:
    return "agent" if request.headers.get("X-SM-Agent") else "person"


def _history_folder():
    """The open chip's history folder, or None without a chip."""
    r = _r()
    ctx = r._active_ctx()
    if not ctx or not ctx.get("path"):
        return None
    from quam_state_manager.core import gate_check_log
    return gate_check_log.chip_dir(r._history(), ctx["path"])


def _record_class_write(target, sha) -> str | None:
    """One history line for a class file written by Save to file; the text
    to show when it could not be written."""
    folder = _history_folder()
    if folder is None:
        return "The class file was saved, but no chip is open to record it in."
    try:
        Q.record_write(folder, actor=_actor(), path=target, sha256=sha, gates=[])
    except OSError as exc:
        return f"The class file was saved, but its history line failed: {exc}"
    return None


@pulse_class_page_bp.route("/pulse-class/save-config", methods=["POST"])
def pulse_class_save_config():
    payload, code = save_config(json_object(), _actor())
    return jsonify(payload), code


def save_config(data: dict, actor: str) -> tuple[dict, int]:
    """Save to quam_config for the current request: ``(answer, status)``.
    The person's route above and the agent's ``/api/agent/class-file/save``
    call this one function; *actor* is who the history line names
    (``person``, ``agent`` or an agent's ``by_<name>``)."""
    r = _r()
    ctx = r._active_ctx()
    if not ctx or not r._store():
        return {"ok": False, "error": "Open a chip so the class write can be recorded in its history."}, 400
    from quam_state_manager.core import working_copy
    from quam_state_manager.core.loader import merge_state_wiring
    from quam_state_manager.web.port_play_page import _chosen_env
    python = str(data.get("python") or _chosen_env(ctx)[0] or "")
    try:
        live = None
        if ctx.get("working_copy"):
            pair = working_copy.read_live_shared(ctx["working_copy"])
            live = merge_state_wiring(pair.state, pair.wiring)
        result = Q.save_class(
            python, _inst(), _scope(), str(data.get("cls") or ""),
            confirmed_folder=data.get("confirmed_folder"), folder=data.get("folder") or None,
            store=r._store(), live=live,
            history_folder=_history_folder(), actor=actor)
        if result.get("registered"):
            r._reprobe_if_new_classes(r._store())
        return result, 200 if result.get("ok") else 409
    except (D.DraftError, OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}, 400
