"""Remove a qubit or pair, and the env check every add / remove passes first.

``GET /diagram-remove/panel`` renders the removal preview (every dependent,
the ports it frees, the wiring rack); ``POST /diagram-add/check`` loads the
RESULT of an add or a remove in the chip's own env on a temporary copy --
``Quam.load`` + ``generate_config`` through the Config Viewer's previewer
(``config_generator.run_config_preview``, strict input) -- and reads the
generated config back (:mod:`core.diagram_check`). Nothing is saved; the
temporary folder is removed afterwards. ``POST /diagram-add/stage`` (in
:mod:`diagram_add_routes`) refuses a plan whose check did not pass unless the
form carries ``override=1``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tempfile
import time
from pathlib import Path

from flask import current_app, make_response, render_template, request

from quam_state_manager.core import diagram_check as dc
from quam_state_manager.core import diagram_remove as dr
from quam_state_manager.web import routes as R
from quam_state_manager.web.routes import bp

logger = logging.getLogger(__name__)

_VERDICTS: dict = {}
_VERDICTS_MAX = 32


def _status(msg, level="error", code=400):
    return render_template("_status.html", message=msg, level=level), code


def plan_key(store, plan) -> tuple:
    blob = json.dumps([plan.get("creates", []), plan.get("sets", []),
                       plan.get("deletes", [])], sort_keys=True, default=str)
    return (R._store_uid(store), getattr(store, "mutation_seq", 0),
            hashlib.sha1(blob.encode("utf-8")).hexdigest())


def verdict_for(store, plan):
    return _VERDICTS.get(plan_key(store, plan))


def env_python():
    """The chip's env: the env chosen for its QUAlibrate project, else the
    app's selected env (``project_env.run_env``, the rule every runner uses)."""
    from quam_state_manager.core import project_env
    try:
        ctx = R._active_ctx() or {}
        return project_env.run_env(current_app.instance_path, ctx.get("qualibrate_project"))
    except Exception:  # noqa: BLE001
        return None


def env_name(python_path) -> str:
    if not python_path:
        return ""
    p = Path(python_path)
    return p.parent.parent.name if p.parent.name.lower() in ("scripts", "bin") else p.parent.name


def run_env_check(python_path, state, wiring) -> dict:
    """``{"ok", "error", "config", "seconds"}`` of loading (state, wiring) in
    the env at ``python_path``. Tests replace this function."""
    from quam_state_manager.core import config_generator
    tmp = Path(tempfile.mkdtemp(prefix="sm_addrm_check_"))
    t0 = time.monotonic()
    try:
        (tmp / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (tmp / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
        out = config_generator.run_config_preview(python_path, tmp, strict=True)
    finally:
        shutil.rmtree(str(tmp.resolve()), ignore_errors=True)
    res = out.get("result") or {}
    ok = bool(out.get("ok") and res.get("config"))
    err = None
    if not ok:
        err = out.get("error") or res.get("error") or "the env reported an error"
        tb = (res.get("traceback") or "").strip()
        if tb and tb.splitlines()[-1] not in err:
            err = f"{err}\n{tb.splitlines()[-1]}"
    return {"ok": ok, "error": err, "config": res.get("config") if ok else None,
            "seconds": round(time.monotonic() - t0, 1)}


def check(store, plan) -> dict:
    py = env_python()
    if not py:
        verdict = {"ok": False, "unavailable": True,
                   "error": "no Python environment is selected, so the result could not be "
                            "loaded in the chip's env"}
    else:
        with store._lock:
            state, wiring = dr.apply_to_docs(store.state, store.wiring, plan)
        verdict = run_env_check(py, state, wiring)
        verdict["env"] = env_name(py)
        cfg = verdict.pop("config", None)
        verdict["readback"] = _readback(cfg, plan) if cfg else []
        verdict["left"] = _left(cfg, plan) if cfg else []
    if len(_VERDICTS) >= _VERDICTS_MAX:
        _VERDICTS.pop(next(iter(_VERDICTS)))
    _VERDICTS[plan_key(store, plan)] = verdict
    return verdict


def _readback(cfg, plan) -> list:
    s = plan.get("summary") or {}
    if s.get("op") == "remove":
        return []
    if s.get("kind") == "qubit":
        name = s["name"]
        expect = {}
        if s.get("drive_port"):
            expect[f"{name}.xy"] = {"port": s["drive_port"], "rf": s.get("drive_rf")}
        return dc.readback(cfg, dc.elements_of(cfg, name), template=s.get("template"),
                           new=name, expect=expect)
    wanted: dict = {}
    for path in s.get("member_ops") or []:
        parts = path.split(".")
        if len(parts) >= 5 and parts[0] == "qubits" and parts[3] == "operations":
            wanted.setdefault(f"{parts[1]}.{parts[2]}", set()).add(parts[4])
    rows = dc.readback(cfg, sorted(wanted))
    for r in rows:
        r["ops"] = [o for o in r["ops"] if o["op"] in wanted.get(r["element"], ())]
    return rows


def _left(cfg, plan) -> list:
    """Elements of a removed qubit that the generated config still holds
    (should be none)."""
    s = plan.get("summary") or {}
    if s.get("op") != "remove":
        return []
    out = []
    for q in s.get("qubits") or []:
        out += dc.elements_of(cfg, q)
    return out


@bp.route("/diagram-add/check", methods=["POST"])
def diagram_add_check():
    from quam_state_manager.web.diagram_add_routes import _plan_from_form
    store = R._store()
    if not store:
        return _status("No chip is open.", "warning")
    tok = request.form.get("expect_chip") or ""
    if not tok or not R._chip_token_ok(tok):
        return _status("The chip changed in another window: reload this page.", "error", 409)
    with store._lock:
        plan = _plan_from_form(store)
    if plan.get("error"):
        return _status(plan["error"], "warning", 400)
    verdict = verdict_for(store, plan) or check(store, plan)
    return render_template("_diagram_check.html", v=verdict, plan=plan,
                           removing=request.form.get("op") == "remove")


@bp.route("/diagram-remove/panel")
def diagram_remove_panel():
    store = R._store()
    if not store:
        return _status("No chip is open.", "warning")
    ctx = R._active_ctx()
    if ctx and (ctx.get("origin") or "live") != "live":
        return _status("This chip is a read-only archive: open the live chip to remove from it.",
                       "warning", 409)
    kind = "pair" if request.args.get("kind") == "pair" else "qubit"
    ent = request.args.get("id", "")
    with store._lock:
        plan = (dr.plan_remove_pair(store.merged, ent) if kind == "pair"
                else dr.plan_remove_qubit(store.merged, ent))
        rows = dr.removal_rows(plan, store.merged) if "deletes" in plan else []
    return render_template("_diagram_remove.html", kind=kind, ent=ent, plan=plan, rows=rows,
                           expect_chip=R._active_chip_token() or "",
                           auto_push=bool(R._auto_apply_state()),
                           env=env_name(env_python()))


def stage_removal(store, modifier, plan):
    gid = modifier.new_group_id()
    try:
        done: list[str] = []
        for path in sorted(plan["deletes"], key=lambda p: p.count(".")):
            if any(path.startswith(d + ".") for d in done):
                continue
            done.append(path)
            modifier.delete_subtree(path, group_id=gid)
        for path, value in plan["sets"]:
            modifier.set_value(path, value, group_id=gid)
    except Exception as exc:  # noqa: BLE001 -- all or nothing
        while store.change_log and store.change_log[-1].group_id == gid:
            if not modifier.undo():
                break
        left = [e.dot_path for e in store.change_log if e.group_id == gid]
        logger.exception("diagram-remove staging failed; remaining: %s", left)
        if left:
            return _status(f"Remove failed: {exc}. Still staged: {', '.join(left)}.",
                           "error", 500)
        return _status(f"Nothing was removed (rolled back): {exc}", "error", 400)
    s = plan["summary"]
    n = len(s.get("pairs_with") or [])
    extra = f" together with {n} pair{'s' if n != 1 else ''}" if n else ""
    msg = render_template(
        "_status.html", level="success",
        message=(f"Removed {s['kind']} {s['name']}{extra} from the working copy. Review, "
                 "then Apply to live; Ctrl+Z brings it all back."))
    resp = make_response(msg + "\n" + R._tray_oob())
    resp.headers["HX-Trigger"] = "pulses-changed, diagnostics-changed"
    resp.headers["HX-Trigger-After-Settle"] = "diagram-removed"
    return resp
