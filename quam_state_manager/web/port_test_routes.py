"""Port test page: download a filled QUA script, or run it on a target.

``GET /port-test?element=<config element>`` (or ``?con=&slot=&port=&io=`` from
a wiring-diagram port) is the stable entry point; the Add/Remove preview links
to it. Scripts are built from the chip's cached ``generate_config()`` output
(the Config Viewer's cache); nothing here imports ``qm``.
"""
from __future__ import annotations

import logging
import re
import secrets
import time
import json
import hashlib

from flask import session

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


def resume_runs(app) -> None:
    """Create the run manager now, so a run an earlier SM left behind (alive,
    or a hardware run whose QM was not reported closed) is watched and cleaned
    up from the start."""
    try:
        if app.config.get("port_test_runs") is None:
            app.config["port_test_runs"] = ptr.Runs(app.instance_path)
    except Exception:  # noqa: BLE001 - the page still resumes it on first use
        logger.exception("port test: could not resume earlier runs at start")


def _config():
    """(config, stale, store) for the active chip, or (None, False, store)."""
    r = _r()
    store = r._store()
    if not store or not store.generated_config:
        return None, False, store
    return store.generated_config, r._config_stale(store), store


def _port_args(src) -> list | None:
    con, port = str(src.get("con") or "").strip(), str(src.get("port") or "").strip()
    if not con or not port:
        return None
    slot = str(src.get("slot") or "").strip()
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


def _play_binding(data) -> str:
    """What one Play confirmation authorizes: this port, value, mode, time, target."""
    spec = data.get("spec") or {}
    net = spec.get("network") or {}
    try:
        offset = 0.0 if data.get("kind") == "mw" else float(data.get("offset"))
        seconds = float(data.get("max_s", 300))
    except (TypeError, ValueError):
        raise pt.PortTestError("Offset and max time must be numbers.") from None
    target = {"host": net.get("host"), "cluster": net.get("cluster_name"), "port": net.get("port")}
    extra = _extra(data)
    if extra:
        return _binding({"multi": _multi_binding(data, extra), "seconds": seconds}, target)
    if data.get("kind") == "mw":
        # An MW play authorizes exactly these tone parameters (validated here too).
        p = pt.mw_params(data.get("band"), data.get("lo_hz"), data.get("if_hz"),
                         data.get("full_scale_power_dbm"), data.get("amp"))
        return _binding({"kind": "mw", "port": _port_args(data), "seconds": seconds,
                         **{k: p[k] for k in ("band", "lo", "if", "full_scale_power_dbm", "amp")}}, target)
    return _binding({"port": _port_args(data), "offset": offset, "seconds": seconds,
                     "mode": str(data.get("output_mode") or "direct")}, target)


def _entry(src) -> dict:
    """One port of a play request as a ``build_wizard_multi`` entry."""
    if src.get("kind") == "mw":
        return {"kind": "mw", "port": _port_args(src) or [], "band": src.get("band"), "lo_hz": src.get("lo_hz"),
                "if_hz": src.get("if_hz"), "fsp": src.get("full_scale_power_dbm"), "amp": src.get("amp")}
    return {"kind": "lf", "port": _port_args(src) or [], "offset": src.get("offset"),
            "output_mode": src.get("output_mode", "direct")}


def _extra(data) -> list:
    """The ports played together with the popup's own port (validated shape)."""
    extra = data.get("extra")
    if extra in (None, []):
        return []
    if not isinstance(extra, list) or len(extra) > pt.MAX_PLAY_PORTS - 1 or not all(isinstance(e, dict) for e in extra):
        raise pt.PortTestError(f"At most {pt.MAX_PLAY_PORTS} ports can play together.")
    return extra


def _multi_binding(data, extra) -> str:
    """Every port and value of a multi-port play, as the builder validated them."""
    _cfg, plan = pt.build_wizard_multi(data.get("spec") or {}, [_entry(data)] + [_entry(e) for e in extra])
    return json.dumps(plan["plays"], sort_keys=True, default=str)


def _play_action(data) -> str:
    """"open" holds a QM open with no program; anything else is a Play."""
    return "open" if data.get("action") == "open" else "play"


def _play_owner() -> str:
    return session.setdefault("port_test_owner", secrets.token_urlsafe(24))


@port_test_bp.route("/port-play/confirm", methods=["POST"])
def port_play_confirm():
    """One-time, 60 s, owner- and port/value-bound token for one Play press."""
    data = json_object()
    if data.get("hardware_ok") is not True:
        return jsonify(ok=False, error="Confirm the port is safe to drive."), 400
    try:
        binding = _play_binding(data)
    except pt.PortTestError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    token = secrets.token_urlsafe(32)
    manager = runs()
    with manager._lock:
        now = time.monotonic()
        manager.confirmations = {k: v for k, v in manager.confirmations.items() if v[0] > now}
        manager.confirmations[token] = (now + 60, _play_owner(), _play_action(data) + ":" + binding)
    return jsonify(ok=True, token=token, expires_s=60)


@port_test_bp.route("/port-play/run", methods=["POST"])
def port_play_run():
    data = json_object()
    owner = _play_owner()
    manager = runs()
    try:
        action = _play_action(data)
        binding = action + ":" + _play_binding(data)
    except pt.PortTestError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    with manager._lock:
        confirmation = manager.confirmations.pop(str(data.get("confirm_token") or ""), None)
    if (not confirmation or confirmation[0] <= time.monotonic()
            or confirmation[1] != owner or confirmation[2] != binding):
        return jsonify(ok=False, error="Confirm the safety box and press Play again."), 400
    status = manager.status()
    if status.get("state") == "running":
        cur = manager.current or {}
        if cur.get("id") == status.get("id") and cur.get("play_owner") == owner:
            # Only our own previous play is stopped; the client confirms again.
            manager.stop(status.get("id"), "Open QM again" if action == "open" else "Play again")
            return jsonify(ok=False, stopping=True,
                           error="Stopping the previous play; waiting for its QM to close."), 409
        return jsonify(ok=False, busy=True,
                       error="Busy: another port test is running. Stop it there first."), 409
    # Generation binding: a Stop pressed after this point refuses the start.
    with manager._lock:
        generation = manager.port_play_generation
    try:
        seconds = _hardware_seconds({"max_s": data.get("max_s", 300)})
        spec = data.get("spec") or {}
        extra = _extra(data)
        if extra:
            cfg, plan = pt.build_wizard_multi(spec, [_entry(data)] + [_entry(e) for e in extra])
        elif data.get("kind") == "mw":
            cfg, plan = pt.build_wizard_mw(spec, _port_args(data) or [], data.get("band"), data.get("lo_hz"),
                                          data.get("if_hz"), data.get("full_scale_power_dbm"), data.get("amp"))
        else:
            cfg, plan = pt.build_wizard_dc(spec, _port_args(data) or [],
                                          data.get("offset"), data.get("output_mode", "direct"))
        python = _r()._build_python(data)
        if not python:
            raise pt.PortTestError("Select a Python environment in step 1.")
        network = spec.get("network") or {}
        host = network.get("host")
        if not host:
            raise pt.PortTestError("Enter the network host in step 2.")
        req = {"mode": "opx", "max_s": seconds, "_port_play_generation": generation, "_play_owner": owner,
               "target": {"host": host, "cluster": network.get("cluster_name"), "port": network.get("port")}}
        plan = dict(plan, open_only=action == "open")
        started = manager.start(python, pt.render_script(cfg, plan, run_program=action == "play"), req,
                                {"plan": plan, "target": "opx", "target_label": host})
        return jsonify(ok=True, **started)
    except (ValueError, RuntimeError, KeyError, TypeError) as exc:
        return jsonify(ok=False, error=str(exc)), 400


@port_test_bp.route("/port-play/stop", methods=["POST"])
def port_play_stop():
    """Stop only this session's own play, by its id (never 'whatever runs')."""
    data = json_object(dict)
    rid = (data.get("id") if isinstance(data, dict) else None) or request.args.get("id")
    if not rid:
        return jsonify(ok=False, error="No play id."), 400
    manager = runs()
    cur = manager.current or {}
    if cur.get("id") != rid or cur.get("play_owner") != session.get("port_test_owner"):
        return jsonify(ok=False, stopping=False, error="Not this window's play."), 409
    why = "the page was left" if request.args.get("left") == "1" else "Stop pressed"
    return jsonify(ok=True, stopping=manager.stop(rid, why))


# ---- Loopback offset calibration (BRIEF3 E-1..E-4) -------------------------

def _loopcal_args(data) -> dict:
    """Validated target of one calibration press: con, fem, ao, ai, kind, mode."""
    kind = data.get("kind")
    if kind not in ("input", "output"):
        raise pt.PortTestError("Choose input or output calibration.")
    port = _port_args(data)
    try:
        con, fem, ao = pt.con_name(port[0]), int(port[1]), int(port[2])
        ai = int(data.get("ai"))
    except (TypeError, ValueError, IndexError):
        raise pt.PortTestError("Choose an LF-FEM output and its analog input (1 or 2).") from None
    if ai not in (1, 2) or not 1 <= ao <= 8:
        raise pt.PortTestError("Choose an LF-FEM output 1..8 and analog input 1 or 2.")
    mode = str(data.get("output_mode") or "direct")
    if mode not in ("direct", "amplified"):
        raise pt.PortTestError("Output mode must be direct or amplified.")
    return {"con": con, "fem": fem, "ao": ao, "ai": ai, "kind": kind, "output_mode": mode}


def _saved_ai_offset(spec, con, fem, ai):
    """The saved AI offset for this FEM/AI at gain_db 0, or None."""
    entry = (spec.get("port_offsets") or {}).get(pt.input_key(con, fem, ai))
    if not isinstance(entry, dict) or int(entry.get("gain_db") or 0) != 0:
        return None
    try:
        value = float(entry.get("offset"))
    except (TypeError, ValueError):
        return None
    return value if value == value and abs(value) <= pt.AI_OFFSET_LIMIT else None


def _loopcal_binding(data) -> str:
    args = _loopcal_args(data)
    net = (data.get("spec") or {}).get("network") or {}
    return "loopcal:" + _binding(args, {"host": net.get("host"), "cluster": net.get("cluster_name"),
                                        "port": net.get("port")})


@port_test_bp.route("/port-play/loopcal/confirm", methods=["POST"])
def port_play_loopcal_confirm():
    """One-time, 60 s, owner-bound token for one calibration press."""
    data = json_object()
    if data.get("hardware_ok") is not True:
        return jsonify(ok=False, error="Confirm the port is safe to drive."), 400
    try:
        binding = _loopcal_binding(data)
    except pt.PortTestError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    token = secrets.token_urlsafe(32)
    manager = runs()
    with manager._lock:
        now = time.monotonic()
        manager.confirmations = {k: v for k, v in manager.confirmations.items() if v[0] > now}
        manager.confirmations[token] = (now + 60, _play_owner(), binding)
    return jsonify(ok=True, token=token, expires_s=60)


@port_test_bp.route("/port-play/loopcal/run", methods=["POST"])
def port_play_loopcal_run():
    data = json_object()
    owner = _play_owner()
    manager = runs()
    try:
        args = _loopcal_args(data)
        binding = _loopcal_binding(data)
    except pt.PortTestError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    with manager._lock:
        confirmation = manager.confirmations.pop(str(data.get("confirm_token") or ""), None)
    if (not confirmation or confirmation[0] <= time.monotonic()
            or confirmation[1] != owner or confirmation[2] != binding):
        return jsonify(ok=False, error="Confirm the safety box and press the button again."), 400
    spec = data.get("spec") or {}
    ai_offset = _saved_ai_offset(spec, args["con"], args["fem"], args["ai"])
    if args["kind"] == "output" and ai_offset is None:
        return jsonify(ok=False, error=(
            f"Calibrate AI {args['ai']} of FEM {args['fem']} (input open, gain_db 0) and save it first.")), 400
    status = manager.status()
    if status.get("state") == "running":
        cur = manager.current or {}
        if cur.get("id") == status.get("id") and cur.get("play_owner") == owner:
            manager.stop(status.get("id"), "Calibration started")
            return jsonify(ok=False, stopping=True,
                           error="Stopping the previous run; waiting for its QM to close."), 409
        return jsonify(ok=False, busy=True,
                       error="Busy: another port test is running. Stop it there first."), 409
    with manager._lock:
        generation = manager.port_play_generation
    try:
        from quam_state_manager.core.loopback_cal import build_loopback_config, mode_limit  # noqa: PLC0415
        controllers = pt.controllers_from_spec(spec)
        try:
            ao_offset = float(data.get("offset") or 0.0) if args["kind"] == "output" else 0.0
        except (TypeError, ValueError):
            raise pt.PortTestError("Offset must be a number.") from None
        limit = mode_limit(args["output_mode"])
        if not abs(ao_offset) <= limit:
            raise pt.PortTestError(f"{args['output_mode']} mode requires |offset| <= {limit} V.")
        try:
            build_loopback_config(controllers, args["con"], args["fem"], args["ao"], args["ai"],
                                  ao_offset, ai_offset or 0.0, args["output_mode"])
        except (KeyError, ValueError):
            raise pt.PortTestError("This port is not an LF-FEM output of the step-3 allocation.") from None
        python = _r()._build_python(data)
        if not python:
            raise pt.PortTestError("Select a Python environment in step 1.")
        network = spec.get("network") or {}
        host = network.get("host")
        if not host:
            raise pt.PortTestError("Enter the network host in step 2.")
        job = {"host": host, "port": network.get("port") or None,
               "cluster_name": network.get("cluster_name") or None, "controllers": controllers,
               "con": args["con"], "fem": args["fem"], "ao": args["ao"], "ai": args["ai"],
               "kind": args["kind"], "ai_offset": ai_offset or 0.0, "ao_offset": ao_offset,
               "output_mode": args["output_mode"], "n_avg": 100}
        target = {"host": host, "cluster": network.get("cluster_name"), "port": network.get("port")}
        req = {"mode": "opx", "max_s": ptr.MAX_RUN_S, "_port_play_generation": generation,
               "_play_owner": owner, "target": target}
        meta = {"target": "opx", "target_label": host,
                "loopcal": {**args, "ai_offset": job["ai_offset"], "gain_db": 0}}
        started = manager.start(python, "# loopback calibration: see job.json\n", req, meta, job=job)
        return jsonify(ok=True, **started)
    except (pt.PortTestError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        return jsonify(ok=False, error=str(exc)), 400


@port_test_bp.route("/port-play/loopcal/save", methods=["POST"])
def port_play_loopcal_save():
    """The draft entry for a FINISHED, CONVERGED calibration of this window's run.

    SM judges convergence here; the page writes the returned entry into the
    wizard draft (spec.port_offsets). Nothing is written to any file."""
    data = json_object(dict)
    rid = data.get("id") if isinstance(data, dict) else None
    manager = runs()
    cur = manager.current or {}
    if not rid or cur.get("id") != rid or cur.get("play_owner") != session.get("port_test_owner"):
        return jsonify(ok=False, error="Not this window's calibration."), 409
    info = (cur.get("meta") or {}).get("loopcal")
    if not info:
        return jsonify(ok=False, error="That run was not a calibration."), 400
    st = manager.status(rid)
    res = st.get("result") or {}
    if st.get("state") != "done" or not res.get("ok") or res.get("stopped") or res.get("simulated"):
        return jsonify(ok=False, error="The calibration has not finished."), 409
    if res.get("converged") is not True:
        return jsonify(ok=False, error="Not converged: nothing to save. Check the cable and run again."), 409
    try:
        value = float(res.get("final_offset"))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="The result has no offset."), 400
    if info["kind"] == "input":
        pin = pt.input_key(info["con"], info["fem"], info["ai"])
        entry = {"offset": value, "gain_db": 0, "source": "loopback"}
    else:
        pin = f"{info['con']}/{info['fem']}/{info['ao']}"
        entry = {"offset": value, "output_mode": info["output_mode"], "source": "loopback",
                 "ai": info["ai"], "ai_offset": info["ai_offset"]}
    return jsonify(ok=True, pin=pin, entry=entry, iters=res.get("iters"))


@port_test_bp.route("/port-play/loopcal/script.py", methods=["POST"])
def port_play_loopcal_script():
    """Download the standalone calibration script, prefilled from this popup."""
    from quam_state_manager.core.loopback_script import render_script  # noqa: PLC0415
    data = json_object()
    try:
        args = _loopcal_args({**data, "kind": "output"})
        spec = data.get("spec") or {}
        net = spec.get("network") or {}
        try:
            ao_start = float(data.get("offset") or 0.0)
        except (TypeError, ValueError):
            raise pt.PortTestError("Offset must be a number.") from None
        ai_offset = _saved_ai_offset(spec, args["con"], args["fem"], args["ai"])
        port = net.get("port")
        params = {"qop_ip": str(net.get("host") or "127.0.0.1"),
                  "cluster_name": net.get("cluster_name") or None,
                  "qop_port": int(port) if port not in (None, "") else None,
                  "con": args["con"], "fem": args["fem"], "ao_port": args["ao"], "ai_port": args["ai"],
                  "output_mode": args["output_mode"], "ao_offset_start": ao_start,
                  "ai_offset_start": ai_offset or 0.0,
                  "other_controllers": pt.controllers_from_spec(spec)}
        script = render_script(params)
    except (pt.PortTestError, ValueError, TypeError) as exc:
        return jsonify(ok=False, error=str(exc)), 400
    name = f"lf_offset_{args['con']}_fem{args['fem']}_ao{args['ao']}.py"
    resp = make_response(script)
    resp.headers["Content-Type"] = "text/x-python; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return resp


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


def _binding(plan, target):
    target = {k: str(target.get(k) or "").strip() for k in ("host", "cluster", "port")}
    return hashlib.sha256(json.dumps([plan, target], sort_keys=True).encode()).hexdigest()


def _hardware_seconds(data) -> float:
    try:
        seconds = float(data.get("max_s", ptr.MAX_RUN_S))
        if not 0 < seconds <= 3600:
            raise ValueError
        return seconds
    except (TypeError, ValueError):
        raise pt.PortTestError("Hardware duration must be in (0, 3600] seconds.") from None


def _confirm_binding(plan: dict, target: dict, seconds: float) -> str:
    return _binding({"plan": plan, "seconds": float(seconds)}, target)


@port_test_bp.route("/port-test/confirm", methods=["POST"])
def port_test_confirm():
    data = json_object()
    if data.get("hardware_ok") is not True:
        return jsonify(ok=False, error="Confirm the fridge-line warning."), 400
    try:
        _, plan = _build(data)
        seconds = _hardware_seconds(data)
    except pt.PortTestError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    owner = session.setdefault("port_test_owner", secrets.token_urlsafe(24))
    token = secrets.token_urlsafe(32)
    manager = runs()
    with manager._lock:
        now = time.monotonic()
        manager.confirmations = {k: v for k, v in manager.confirmations.items() if v[0] > now}
        manager.confirmations[token] = (now + 60, owner, _confirm_binding(plan, data.get("opx") or {}, seconds))
    return jsonify(ok=True, token=token, expires_s=60)


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
        try:
            seconds = _hardware_seconds(data)
        except pt.PortTestError as exc:
            return jsonify(ok=False, error=str(exc)), 400
        with runs()._lock:
            confirmation = runs().confirmations.pop(str(data.get("confirm_token") or ""), None)
        if (not confirmation or confirmation[0] <= time.monotonic()
                or confirmation[1] != session.get("port_test_owner")
                or confirmation[2] != _confirm_binding(plan, s["opx"], seconds)):
            return jsonify({"ok": False, "error": "Confirm the fridge-line warning before a hardware run."}), 400
        host = str(s["opx"].get("host") or "").strip()
        if not host:
            return jsonify({"ok": False, "error": "Set the OPX host first."}), 400
        req = {"max_s": seconds, "mode": "opx", "target": {"host": host, "cluster": s["opx"].get("cluster") or None,
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


@port_test_bp.route("/port-test/close-open", methods=["POST"])
def port_test_close_open():
    data = json_object(dict)
    rid = (data.get("id") if isinstance(data, dict) else None) or request.args.get("id")
    try:
        return jsonify({"ok": True, **runs().close_matching_open_qms(rid)})
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409


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


@port_test_bp.route("/port-test/settings/panel", methods=["GET", "POST"])
def port_test_targets_panel():
    """The saved run targets inside the Settings window (FB-12 D-4)."""
    inst = current_app.instance_path
    msg = ""
    if request.method == "POST":
        f = request.form
        before = ptr.load_settings(inst)
        ptr.save_settings(inst, {
            "opx": {k: f.get("opx_" + k, "") for k in ("host", "cluster", "port")},
            "saas": {k: f.get("saas_" + k, "") for k in ("email", "host", "version")}})
        after = ptr.load_settings(inst)
        msg = "Saved."
        try:
            if f.get("do") == "remove_password":
                ptr.remove_password(inst, after)
                msg = "Saved password removed."
            elif f.get("saas_password"):
                ptr.save_password(inst, after, f["saas_password"])
                msg = "Saved (password in the OS keyring)."
            elif ptr._secret_key(before) != ptr._secret_key(after):
                old, _w = ptr.load_password(inst, before)
                if old:  # a renamed account keeps its password
                    ptr.save_password(inst, after, old)
                    ptr.remove_password(inst, before)
        except ValueError as exc:
            msg = str(exc)
    return render_template("_port_test_targets.html", pt_settings=ptr.public_view(inst), pt_msg=msg)
