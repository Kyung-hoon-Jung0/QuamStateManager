"""Simulate for the New CZ gate dialog: start / status / stop.

The start body is the dialog's plan body (the same fields the check takes)
plus ``pair`` (which planned pair to simulate; the first one when absent).
The gate is built on a temporary chip copy with the planned creates; the
working copy and the live files are never written. One run per window
(``X-SM-Window-ID`` header, or ``window`` in the body / query).
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from quam_state_manager.core import chip_copy, gate_check
from quam_state_manager.core import cz_gate_simulate as sim
from quam_state_manager.core import port_test_runs as ptr
from quam_state_manager.web import request_body

cz_gate_sim_bp = Blueprint("cz_gate_sim", __name__)


def manager() -> sim.Manager:
    m = current_app.config.get("cz_gate_sim_manager")
    if m is None:
        m = current_app.config["cz_gate_sim_manager"] = sim.Manager(current_app.instance_path)
    return m


def _window(data: dict | None = None) -> str:
    return (request.headers.get("X-SM-Window-ID") or (data or {}).get("window")
            or request.args.get("window") or "")


def _target():
    from quam_state_manager.web.port_test_routes import _saas_credentials
    settings = ptr.load_settings(current_app.instance_path)
    return sim.target_of(settings, _saas_credentials())


@cz_gate_sim_bp.route("/api/pulse/cz-gate/simulate/start", methods=["POST"])
def cz_gate_simulate_start():
    from quam_state_manager.web import cz_gate_routes as cz
    try:
        data = request_body.json_object({})
        body = cz._body()
    except request_body.BadBody as exc:
        return jsonify(ok=False, error=str(exc)), 400
    store, err = cz._store_or_error()
    if err:
        return err
    if not body["pairs"]:
        return jsonify(ok=False, **sim.answer("not_run", reason="no pair is picked")), 400
    pair = data.get("pair") if isinstance(data.get("pair"), str) else body["pairs"][0]
    if pair not in body["pairs"]:
        return jsonify(ok=False, **sim.answer("not_run", reason="that pair is not in the plan")), 400
    python = cz._env_python()
    target, label, limit_s = _target()
    if not python:
        return jsonify(ok=False, **sim.answer("no_env")), 400
    if target is None:
        # An expected refusal is a handled answer; API callers still get ok=False.
        return jsonify(ok=False, **sim.answer("not_configured")), 200
    with store._lock:
        plans = dict(cz._plan_all(store.merged, body))
    plan = plans.get(pair) or {}
    if "error" in plan:
        return jsonify(ok=False, **sim.answer("not_run", reason=plan["error"])), 400
    creates = [(str(p), v) for p, v in plan.get("creates") or []]
    try:
        ans, code = manager().start(
            _window(data), python=python, target=target, label=label, limit_s=limit_s,
            make_copy=lambda: chip_copy.make_chip_copies(store, creates),
            remove_copy=chip_copy.remove_chip_copies,
            macro=gate_check.macro_path(pair, body["gate_name"]),
            popen_for=lambda folder: sim.lab_popen({"QUAM_STATE_PATH": folder}))
    except (chip_copy.CopyError, chip_copy.CreateError) as exc:
        reason = gate_check.scrub_copy_names(str(exc))
        return jsonify(ok=False, **sim.answer("not_run", reason=reason)), 400
    return jsonify(ok=code == 200, **ans), code


@cz_gate_sim_bp.route("/api/pulse/cz-gate/simulate/status")
def cz_gate_simulate_status():
    return jsonify(ok=True, **manager().status(_window(), request.args.get("id")))


@cz_gate_sim_bp.route("/api/pulse/cz-gate/simulate/stop", methods=["POST"])
def cz_gate_simulate_stop():
    try:
        data = request_body.json_object({})
    except request_body.BadBody as exc:
        return jsonify(ok=False, error=str(exc)), 400
    rid = data.get("id") if isinstance(data.get("id"), str) else None
    return jsonify(ok=True, **manager().stop(_window(data), rid))
