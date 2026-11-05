"""Add a qubit or a pair from the chip diagram (routes).

``GET /diagram-add/panel`` renders the add panel under the chip map,
``GET /api/diagram-add/ports`` re-proposes free ports for a typed drive
frequency, ``POST /diagram-add/stage`` previews (``dry=1``) or stages a plan
from :mod:`core.diagram_add`. Staging writes the WORKING COPY only, through
the modifier under one change group: the pending tray, Review / Apply, Ctrl+Z
and the undo journal treat it like any other edit, and nothing reaches the
live files without Apply (or an armed Auto-Sync session).
"""

from __future__ import annotations

import logging
import math

from flask import jsonify, make_response, render_template, request

from quam_state_manager.core import diagram_add as da
from quam_state_manager.web import routes as R
from quam_state_manager.web.routes import bp

logger = logging.getLogger(__name__)

_INV_MEMO: dict = {}


def _inv(store):
    """The port inventory of the loaded chip, memoized on its mutation_seq
    (one walk per state, never one per keystroke)."""
    key = (R._store_uid(store), getattr(store, "mutation_seq", 0))
    hit = _INV_MEMO.get("k")
    if hit is not None and hit[0] == key:
        return hit[1]
    inv = da.inventory(store.merged)
    _INV_MEMO["k"] = (key, inv)
    return inv


def _is_archive() -> bool:
    ctx = R._active_ctx()
    return bool(ctx) and (ctx.get("origin") or "live") != "live"


def _hz(name):
    raw = (request.values.get(name) or "").strip()
    if not raw:
        return None
    try:
        v = float(raw)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _port_arg(name):
    raw = (request.values.get(name) or "").strip()
    parts = raw.split("/")
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        return None
    return parts[0], int(parts[1]), int(parts[2])


def _status(msg, level="error", code=400):
    return render_template("_status.html", message=msg, level=level), code


@bp.route("/diagram-add/panel")
def diagram_add_panel():
    store = R._store()
    if not store:
        return _status("No chip is open.", "warning")
    if _is_archive():
        return _status("This chip is a read-only archive: open the live chip to add to it.",
                       "warning", 409)
    kind = request.args.get("kind", "qubit")
    m = store.merged
    with store._lock:
        if kind == "pair":
            a, b = request.args.get("a", ""), request.args.get("b", "")
            plan = da.plan_pair(m, a, b)
            return render_template("_diagram_add.html", kind="pair", a=a, b=b, plan=plan,
                                   rows=da.review_rows(plan) if not plan.get("error") else [],
                                   expect_chip=R._active_chip_token() or "",
                                   auto_push=bool(R._auto_apply_state()))
        feedline = request.args.get("feedline") or None
        of = request.args.get("feedline_of")
        if of:
            feedline = da.feedline_of(m, of) or feedline
        d = da.qubit_defaults(m, feedline)
        if d.get("error"):
            return _status(d["error"], "warning", 200)
        inv = _inv(store)
        drive = da.drive_candidates(m, inv, d["drive_rf"], prefer_fem=d["fem"], if_hz=d["if_hz"],
                                     prefer_band=d["band"])
        flux = da.flux_candidates(inv) if d["has_flux"] else []
    return render_template("_diagram_add.html", kind="qubit", d=d, drive=drive, flux=flux,
                           expect_chip=R._active_chip_token() or "",
                           auto_push=bool(R._auto_apply_state()))


@bp.route("/api/diagram-add/ports")
def diagram_add_ports():
    store = R._store()
    if not store:
        return jsonify({"error": "No chip is open."}), 400
    m = store.merged
    with store._lock:
        d = da.qubit_defaults(m, request.args.get("feedline") or None)
        if d.get("error"):
            return jsonify({"error": d["error"]}), 400
        inv = _inv(store)
        rf = _hz("drive_rf")
        drive = da.drive_candidates(m, inv, rf, prefer_fem=d["fem"], if_hz=d["if_hz"],
                                     prefer_band=d["band"])
        flux = da.flux_candidates(inv) if d["has_flux"] else []
    return jsonify({"drive": drive, "flux": flux})


def _plan_from_form(store):
    m = store.merged
    kind = request.form.get("kind", "qubit")
    if kind == "pair":
        return da.plan_pair(m, request.form.get("control", ""), request.form.get("target", ""))
    drive = _port_arg("drive")
    if drive is None:
        return {"error": "Pick a drive output."}
    rf = _hz("drive_rf")
    if rf is None:
        return {"error": "Give the drive frequency in Hz."}
    return da.plan_qubit(m, name=(request.form.get("name") or "").strip(),
                         feedline=request.form.get("feedline") or "",
                         drive=drive, flux=_port_arg("flux"), drive_rf=rf,
                         readout_rf=_hz("readout_rf"), inv=_inv(store))


@bp.route("/diagram-add/stage", methods=["POST"])
def diagram_add_stage():
    store, modifier = R._store(), R._modifier()
    if not store or not modifier:
        return _status("No chip is open.", "warning")
    if _is_archive():
        return _status("This chip is a read-only archive: open the live chip to add to it.",
                       "warning", 409)
    tok = request.form.get("expect_chip") or ""
    if not tok or not R._chip_token_ok(tok):
        return _status("The chip changed in another window: reload this page.", "error", 409)
    dry = request.form.get("dry") == "1"
    with store._lock:
        plan = _plan_from_form(store)
        if plan.get("error"):
            return _status(plan["error"], "warning", 400)
        if dry:
            return render_template("_diagram_add_review.html", plan=plan,
                                   rows=da.review_rows(plan), form=request.form)
        from flask import g
        lab_info = g.lab_info = {"notes": []}
        refusal = R._lab_write_refusal(store, plan["creates"], info=lab_info)
        if refusal:
            return _status(refusal[0], "error", 400)
        gid = modifier.new_group_id()
        try:
            for path, value in plan["creates"]:
                modifier.create_subtree(path, value, group_id=gid)
            for path, value in plan["sets"]:
                modifier.set_value(path, value, group_id=gid)
        except Exception as exc:  # noqa: BLE001 -- all or nothing
            rollback_error = None
            # The modifier may have logged a create before a later index
            # update raised. Roll back the actual group, including that entry.
            while store.change_log and store.change_log[-1].group_id == gid:
                try:
                    if not modifier.undo():
                        raise RuntimeError("Undo returned no staged change")
                except Exception as rollback_exc:  # noqa: BLE001
                    rollback_error = rollback_exc
                    break
            done = [e.dot_path for e in store.change_log if e.group_id == gid]
            logger.exception("diagram-add staging failed; remaining staged paths: %s", done)
            if done or rollback_error is not None:
                return _status(
                    f"Add failed: {exc}. Rollback failed: {rollback_error}. "
                    f"Still staged: {', '.join(done) or 'none'}. Review the pending changes.", "error", 500)
            return _status(f"Nothing was added (rolled back): {exc}", "error", 400)
    R._reprobe_if_new_classes(store)
    s = plan["summary"]
    what = (f"qubit {s['name']} on feedline {s['feedline']}" if s["kind"] == "qubit"
            else f"pair {s['name']}")
    msg = render_template(
        "_status.html", level="success",
        message=(f"Added {what} to the working copy ({len(plan['creates'])} new "
                 "subtrees). Review, then Apply to live; Ctrl+Z takes it all back."))
    resp = make_response(msg + "\n" + R._tray_oob())
    resp.headers["HX-Trigger"] = "pulses-changed, diagnostics-changed"
    # after the swap: the page refresh carries this result line over
    resp.headers["HX-Trigger-After-Settle"] = "diagram-added"
    return R._lab_toast(resp)
