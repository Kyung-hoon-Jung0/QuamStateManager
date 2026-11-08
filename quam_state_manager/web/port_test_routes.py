"""Port test page: download a filled QUA script, or run it on a target.

``GET /port-test?element=<config element>`` (or ``?con=&slot=&port=&io=`` from
a wiring-diagram port) is the stable entry point; the Add/Remove preview links
to it. Scripts are built from the chip's cached ``generate_config()`` output
(the Config Viewer's cache); nothing here imports ``qm``.
"""
from __future__ import annotations

import logging
import re

from flask import Blueprint, current_app, jsonify, make_response, render_template, request

from quam_state_manager.core import config_generator
from quam_state_manager.core import port_test as pt
from quam_state_manager.core import port_test_runs as ptr
from quam_state_manager.web.request_body import json_object

logger = logging.getLogger(__name__)
port_test_bp = Blueprint("port_test", __name__)


def _r():
    from quam_state_manager.web import routes  # noqa: PLC0415 - avoid an import cycle
    return routes


def runs() -> ptr.Runs:
    r = current_app.config.get("port_test_runs")
    if r is None:
        r = current_app.config["port_test_runs"] = ptr.Runs(current_app.instance_path)
    return r


def _config():
    """(config, stale, store) for the active chip, or (None, False, store)."""
    r = _r()
    store = r._store()
    if not store or not store.generated_config:
        return None, False, store
    return store.generated_config, r._config_stale(store), store


def _port_args(src) -> list | None:
    con, port = (src.get("con") or "").strip(), (src.get("port") or "").strip()
    if not con or not port:
        return None
    slot = (src.get("slot") or "").strip()
    return [con, slot, port]


def _params(src) -> dict:
    def num(k, cast=float):
        v = src.get(k)
        if v in (None, ""):
            return None
        try:
            return cast(v)
        except (TypeError, ValueError):
            raise pt.PortTestError(f"{k} must be a number.") from None
    marker = src.get("marker") or ""
    mp = None
    if marker:
        parts = [p for p in re.split(r"[/,\s]+", str(marker)) if p]
        mp = [parts[0]] + [int(x) for x in parts[1:]] if len(parts) in (2, 3) else None
        if mp is None:
            raise pt.PortTestError("The marker port must read con/fem/port or con/port.")
    return {"operation": (src.get("op") or None), "amplitude": num("amp"),
            "amp_cap": num("cap") or ptr.load_settings(current_app.instance_path)["amp_cap"],
            "period_ns": num("period", int) or pt.DEFAULT_PERIOD_NS,
            "shots": num("shots", int) or pt.DEFAULT_SHOTS, "marker_port": mp}


def _build(src):
    cfg, _stale, _store = _config()
    if cfg is None:
        raise pt.PortTestError("No generated config yet: generate it first.")
    element = (src.get("element") or "").strip()
    test = src.get("test") or "cw"
    basis = src.get("basis") or "chip"
    tcfg, plan = pt.build(cfg, element, test, basis, **_params(src))
    label = _r()._active_ctx().get("name", "") if _r()._active_ctx() else ""
    return pt.render_script(tcfg, plan, chip_label=str(label or "")), plan


@port_test_bp.route("/port-test")
def port_test_page():
    r = _r()
    if not r._store():
        return r._no_chip("port test", "instrument")
    cfg, stale, _ = _config()
    element = (request.args.get("element") or "").strip()
    port = _port_args(request.args)
    choices, info, err = [], None, None
    if cfg is not None:
        if port and not element:
            io = "input" if request.args.get("io") == "input" else "output"
            choices = pt.elements_on_port(cfg, port[0], port[1], port[2], io)
            if len(choices) == 1:
                element = choices[0]
        if element:
            try:
                info = pt.element_info(cfg, element)
            except pt.PortTestError as exc:
                err = str(exc)
    tpl = "_port_test.html" if r._is_htmx() else "port_test.html"
    return render_template(tpl, **r._ctx(
        page="port_test", pt_config=cfg is not None, pt_stale=stale, pt_element=element,
        pt_port=port, pt_choices=choices, pt_info=info, pt_error=err,
        pt_settings=ptr.public_view(current_app.instance_path),
        pt_markers=pt.digital_ports(cfg, info["port"]) if info else [],
        pt_cap_max=pt.AMP_CAP_MAX, pt_status=runs().status()))


@port_test_bp.route("/port-test/plan")
def port_test_plan():
    try:
        _script, plan = _build(request.args)
    except pt.PortTestError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "plan": plan})


@port_test_bp.route("/port-test/script.py")
def port_test_script():
    try:
        script, plan = _build(request.args)
    except pt.PortTestError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"port_test_{plan['test']}_{plan['basis']}_"
                                              f"{plan['source_element']}.py")
    resp = make_response(script)
    resp.headers["Content-Type"] = "text/x-python; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return resp


@port_test_bp.route("/port-test/run", methods=["POST"])
def port_test_run():
    data = json_object()
    inst = current_app.instance_path
    target = data.get("target")
    if target not in ("saas", "opx"):
        return jsonify({"ok": False, "error": "Choose a run target."}), 400
    try:
        script, plan = _build(data)
    except pt.PortTestError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    python = config_generator.get_selected_env(inst)
    if not python:
        return jsonify({"ok": False, "error": "No environment selected for this chip."}), 400
    s = ptr.load_settings(inst)
    s["target"] = target
    if target == "opx":
        if data.get("hardware_ok") is not True:
            return jsonify({"ok": False, "error": "Confirm the fridge-line warning before a hardware run."}), 400
        host = str(s["opx"].get("host") or "").strip()
        if not host:
            return jsonify({"ok": False, "error": "Set the OPX host first."}), 400
        req = {"mode": "opx", "target": {"host": host, "cluster": s["opx"].get("cluster") or None,
                                         "port": s["opx"].get("port") or None}}
    else:
        pw, _where = ptr.load_password(inst, s)
        if not (s["saas"].get("email") and s["saas"].get("host") and pw):
            return jsonify({"ok": False, "error": "Set the SaaS email, host and password first."}), 400
        req = {"mode": "saas", "target": {"email": s["saas"]["email"], "host": s["saas"]["host"],
                                          "password": pw, "version": s["saas"].get("version") or None}}
    ptr.save_settings(inst, {"target": target})
    try:
        started = runs().start(python, script, req, {"plan": plan, "target": target,
                                                    "target_label": (s["opx"]["host"] if target == "opx"
                                                                     else s["saas"]["host"])})
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    return jsonify({"ok": True, **started})


@port_test_bp.route("/port-test/status")
def port_test_status():
    st = runs().status(request.args.get("id") or None)
    st.get("meta", {}).pop("secret", None)
    return jsonify(st)


@port_test_bp.route("/port-test/stop", methods=["POST"])
def port_test_stop():
    data = json_object(dict)
    rid = (data.get("id") if isinstance(data, dict) else None) or request.args.get("id")
    why = "the page was left" if request.args.get("left") == "1" else "Stop pressed"
    return jsonify({"ok": True, "stopping": runs().stop(rid, why)})


@port_test_bp.route("/port-test/settings", methods=["POST"])
def port_test_settings():
    data = json_object()
    inst = current_app.instance_path
    clean = {k: data[k] for k in ("target", "opx", "saas") if isinstance(data.get(k), (str, dict))}
    if "amp_cap" in data:
        try:
            cap = float(data["amp_cap"])
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "The amplitude cap must be a number."}), 400
        if not 0 < cap <= pt.AMP_CAP_MAX:
            return jsonify({"ok": False, "error": f"The amplitude cap must be in (0, {pt.AMP_CAP_MAX}]."}), 400
        clean["amp_cap"] = cap
    before = ptr.load_settings(inst)
    ptr.save_settings(inst, clean)
    after = ptr.load_settings(inst)
    pw = data.get("password")
    if isinstance(pw, str) and pw:
        try:
            ptr.save_password(inst, after, pw)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
    elif ptr._secret_key(before) != ptr._secret_key(after):
        old, _w = ptr.load_password(inst, before)
        if old:  # a renamed account keeps its password
            ptr.save_password(inst, after, old)
            ptr.remove_password(inst, before)
    return jsonify({"ok": True, "settings": ptr.public_view(inst)})


@port_test_bp.route("/port-test/settings/password/remove", methods=["POST"])
def port_test_password_remove():
    inst = current_app.instance_path
    ptr.remove_password(inst, ptr.load_settings(inst))
    return jsonify({"ok": True, "settings": ptr.public_view(inst)})
