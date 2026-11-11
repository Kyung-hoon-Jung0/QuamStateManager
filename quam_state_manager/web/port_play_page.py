"""Standalone Port play page (``/port-play``) and its read-only connection test.

The page mounts the Instrument Wiring rack and the same Port play popup the
wizard mounts (``port-play.js``) over a spec reconstructed from the open chip
(``regen_spec.reconstruct_spec``). The env is the project's remembered env
(``project_env.run_env``), shown in a picker the person can change. Without an
open chip the page says so in one sentence and links to the wizard's step 3,
where a draft chassis is played.

``POST /port-play/test-connection`` opens one ``QuantumMachinesManager`` in the
chosen env and reports the QOP version. It never opens a QM and never plays.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys

from flask import Blueprint, current_app, jsonify, render_template, request

from quam_state_manager.core import config_generator
from quam_state_manager.web.request_body import json_object

logger = logging.getLogger(__name__)
port_play_page_bp = Blueprint("port_play_page", __name__)

CONNECT_TIMEOUT_S = 60

# Read-only: one manager open and a version read. No QM is opened, nothing plays.
_CONNECT_SCRIPT = r"""
import json, sys
req = json.loads(sys.stdin.readline())
try:
    from qm import QuantumMachinesManager
    kw = {"host": req["host"]}
    if req.get("cluster"):
        kw["cluster_name"] = req["cluster"]
    if req.get("port"):
        kw["port"] = int(req["port"])
    qmm = QuantumMachinesManager(**kw)
    try:
        ver = qmm.version()
        ver = getattr(ver, "qop", None) or str(ver)
    except Exception as exc:  # the connection held; the version call is optional
        ver = None
    print(json.dumps({"ok": True, "version": None if ver is None else str(ver)}))
except Exception as exc:
    print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
"""


def _r():
    from quam_state_manager.web import routes  # noqa: PLC0415 - avoid an import cycle
    return routes


def _envs() -> list[dict]:
    try:
        rows = config_generator.discover_envs()
    except Exception:  # noqa: BLE001 - the picker degrades to the chosen env alone
        logger.exception("port play page: env discovery failed")
        rows = []
    return [{"name": e.get("name") or e.get("python"), "python": e.get("python")}
            for e in rows if e.get("python")]


def _chosen_env(ctx) -> tuple[str | None, str]:
    from quam_state_manager.core import project_env
    try:
        return project_env.run_env_source(current_app.instance_path,
                                          (ctx or {}).get("qualibrate_project"))
    except Exception:  # noqa: BLE001 - unknown is "none"
        return None, ""


def chip_spec(store) -> dict | None:
    """The open chip's spec, or None when it cannot be reconstructed."""
    from quam_state_manager.core import regen_spec
    try:
        return regen_spec.reconstruct_spec(store.state, store.wiring).spec
    except Exception:  # noqa: BLE001 - the page says so instead of a 500
        logger.exception("port play page: spec reconstruction failed")
        return None


def spec_fems(spec: dict) -> list[str]:
    """``con/slot`` of every FEM the spec declares, sorted."""
    out = []
    ctrls = ((spec or {}).get("instruments") or {}).get("controllers") or []
    for c in ctrls if isinstance(ctrls, list) else []:
        con = str(c.get("con") or c.get("name") or "")
        con = con if con.startswith("con") else "con" + con
        for f in c.get("fems") or []:
            slot = f.get("slot") if isinstance(f, dict) else None
            if slot is not None:
                out.append(f"{con}/{slot}")
    return sorted(out)


@port_play_page_bp.route("/port-play")
def port_play_page():
    r = _r()
    store = r._store()
    ctx = r._active_ctx()
    spec, instrument_json, error = None, '{"controllers": {}}', None
    if store:
        spec = chip_spec(store)
        if spec is None:
            error = "This chip's wiring could not be read as a rack, so nothing can be played from it."
        engine = r._engine()
        try:
            instrument_json = json.dumps(engine.get_instrument_wiring()) if engine else instrument_json
        except Exception as exc:  # noqa: BLE001 - shown, never an empty rack
            logger.exception("port play page: instrument wiring failed")
            error = str(exc) or exc.__class__.__name__
    python, source = _chosen_env(ctx)
    net = (spec or {}).get("network") or {}
    tpl_ctx = dict(
        page="port_play", pp_has_chip=bool(store), pp_spec=spec, pp_error=error,
        pp_instrument_json=instrument_json, pp_wiring_json=r._wiring_json() if store else "{}",
        pp_python=python, pp_env_source=source, pp_envs=_envs(),
        pp_host=net.get("host") or "", pp_cluster=net.get("cluster_name") or "",
        pp_pin=(request.args.get("pin") or "").strip(),
        pp_partial=r._is_htmx(),
    )
    return render_template("port_play.html", **r._ctx(**tpl_ctx))


@port_play_page_bp.route("/port-play/test-connection", methods=["POST"])
def port_play_test_connection():
    data = json_object()
    host = str(data.get("host") or "").strip()
    python = str(data.get("python") or "").strip()
    if not host:
        return jsonify({"ok": False, "error": "No host: the chip's network block names none. Enter one."}), 400
    if not python or not config_generator.known_interpreter(python, current_app.instance_path):
        return jsonify({"ok": False, "error": "Choose a Python environment first."}), 400
    req = {"host": host, "cluster": str(data.get("cluster") or "").strip() or None,
           "port": data.get("port") or None}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        res = subprocess.run([python, "-I", "-c", _CONNECT_SCRIPT], input=json.dumps(req) + "\n",
                             capture_output=True, text=True, timeout=CONNECT_TIMEOUT_S,
                             creationflags=flags, env={**os.environ, "PYTHONUTF8": "1"})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": f"No answer from {host} within {CONNECT_TIMEOUT_S} s."})
    except OSError as exc:
        return jsonify({"ok": False, "error": f"Could not start the environment: {exc}"})
    lines = [ln for ln in (res.stdout or "").splitlines() if ln.startswith("{")]
    try:
        out = json.loads(lines[-1])
    except (IndexError, ValueError):
        tail = (res.stderr or "").strip().splitlines()[-1:] or ["no output"]
        out = {"ok": False, "error": tail[0]}
    return jsonify(out)
