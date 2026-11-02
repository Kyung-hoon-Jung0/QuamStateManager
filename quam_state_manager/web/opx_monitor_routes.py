"""OPX monitor pages (Live / Jobs / History / Clean up / Monitor settings).

The pages read the cached answer of ``core.opx_monitor.Monitor``; a page GET
asks the poller to keep going for a few intervals and never waits on the OPX.
Clean-up and settings are POSTs (the app's origin guard applies)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from flask import Blueprint, current_app, jsonify, make_response, render_template, request

from quam_state_manager.core import opx_monitor as om

logger = logging.getLogger(__name__)

opxmon_bp = Blueprint("opx_monitor", __name__)

TABS = ("live", "jobs", "history", "cleanup", "settings")
TAB_LABELS = {"live": "Live", "jobs": "Jobs", "history": "History", "cleanup": "Clean up",
              "settings": "Monitor settings"}


def monitor() -> om.Monitor:
    m = current_app.config.get("opx_monitor")
    if m is None:
        m = current_app.config["opx_monitor"] = om.Monitor(current_app.instance_path)
    return m


def _r():
    from quam_state_manager.web import routes
    return routes


def _envs():
    try:
        from quam_state_manager.core.config_generator import discover_envs
        return [e for e in discover_envs() if e.get("python")]
    except Exception:  # noqa: BLE001
        return []


def _view(tab: str) -> dict:
    m = monitor()
    st = m.status()
    s = m.settings()
    h = m.history()
    t = dict(s.get("target") or {})
    key = om.target_key(t)
    has_headers = bool(om.load_secret_headers(m.instance, key)) if key else False
    first = {r['qm_id']: r['first_seen'] for r in h.table() if not r.get('job_id')} if h else {}
    idle_states = {q['id']: om.idle_state(q, first.get(q['id']), m.clock(), s['idle_minutes'])
                   for q in (st.get('answer') or {}).get('open_qms') or []}
    return {"trends": m.trends().series(m.clock()) if m.trends() and tab == "live" else {}, "idle_states": idle_states, "idle_ids": [k for k, v in idle_states.items() if v['idle']], "tab": tab, "tabs": TABS, "tab_labels": TAB_LABELS, "st": st, "settings": s,
            "target": t, "has_headers": has_headers,
            "history_rows": h.table() if h and tab == "history" else [],
            "actions": h.recent_actions() if h and tab in ("history", "cleanup") else [],
            "offered": om.offered_metrics(st.get("answer")),
            "envs": _envs() if tab == "settings" else [],
            "min_interval": om.MIN_INTERVAL_S,
            "temperature_rows": om.temperature_rows, "slot_temperatures": om.slot_temperatures}


@opxmon_bp.route("/opx-monitor")
@opxmon_bp.route("/opx-monitor/<tab>")
def opx_monitor_page(tab="live"):
    if tab not in TABS:
        tab = "live"
    monitor().want()
    r = _r()
    tpl = "_opx_monitor.html" if r._is_htmx() else "opx_monitor.html"
    return render_template(tpl, **r._ctx(page="opx_monitor", **_view(tab)))


@opxmon_bp.route("/opx-monitor/body")
def opx_monitor_body():
    tab = request.args.get("tab", "live")
    if tab not in TABS:
        tab = "live"
    monitor().want()
    return render_template("_opx_monitor_body.html", **_view(tab))


@opxmon_bp.route("/opx-monitor/api/status")
def opx_monitor_status():
    m = monitor()
    m.want()
    return jsonify(m.status())


@opxmon_bp.route("/opx-monitor/alarm-badge")
def opx_alarm_badge():
    """The top-bar badge; fires a toast once per new alarm in this process."""
    m = monitor()
    m.ensure_running_if_armed()
    st = m.status()
    resp = make_response(render_template("_opx_alarm_badge.html", st=st))
    fresh = m.new_alarm_toasts() if st.get("alarms_armed") else []
    if fresh:
        a = fresh[0]
        more = f" (+{len(fresh) - 1} more)" if len(fresh) > 1 else ""
        resp.headers["HX-Trigger"] = json.dumps({"showToast": {
            "message": f"OPX alarm: {a['controller']} {a['label']} = {a['value']:.1f} {a['unit']} "
                       f"({a['direction']} limit {a['limit']:g}){more}", "level": "warning"}})
    return resp


def _form_bool(name):
    return request.form.get(name) in ("1", "on", "true", "yes")


@opxmon_bp.route("/opx-monitor/settings", methods=["POST"])
def opx_monitor_save_settings():
    m = monitor()
    s = m.settings()
    f = request.form
    msgs = []
    host = (f.get("host") or "").strip()
    port = (f.get("port") or "").strip()
    if port and not port.isdigit():
        return _settings_reply("Port must be a whole number.", error=True)
    target = ({"kind": "qmm", "host": host, "port": int(port) if port else None,
               "cluster_name": (f.get("cluster_name") or "").strip() or None,
               "tls": _form_bool("tls")} if host else None)
    py = (f.get("python") or "").strip() or None
    if py and not Path(py).is_file():
        return _settings_reply("That Python interpreter does not exist.", error=True)
    # a limit for a metric the server does not report right now is kept
    th = dict(s.get("thresholds") or {})
    for k, v in f.items():
        if not k.startswith("th__"):
            continue
        if not v.strip():
            th.pop(k[4:], None)
            continue
        try:
            val = float(v)
        except ValueError:
            val = float("nan")
        if val != val or val in (float("inf"), float("-inf")):
            return _settings_reply(f"The limit for {k[4:]} is not a number.", error=True)
        th[k[4:]] = val
    new = {"target": target, "python": py, "interval_s": om.clamp_interval(f.get("interval_s")),
           "admin_url": (f.get("admin_url") or "").strip(), "idle_minutes": f.get("idle_minutes", s["idle_minutes"]), "thresholds": th, "alarms_armed": _form_bool("alarms_armed")}
    changed_conn = (new["target"] != s.get("target")) or (new["python"] != s.get("python")) or new["admin_url"] != s.get("admin_url")
    om.save_settings(m.instance, new)
    hdr_txt = f.get("headers")
    if target is not None:
        key = om.target_key(target)
        if _form_bool("clear_headers"):
            om.save_secret_headers(m.instance, key, {})
            changed_conn = True
        elif hdr_txt and hdr_txt.strip():
            try:
                h = json.loads(hdr_txt)
                if not isinstance(h, dict):
                    raise ValueError
            except ValueError:
                return _settings_reply("Connection headers must be a JSON object.", error=True)
            om.save_secret_headers(m.instance, key, h)
            changed_conn = True
            msgs.append("Connection headers stored (not shown again).")
    if changed_conn:
        m.reset()
    m.want()
    m.ensure_running_if_armed()
    return _settings_reply(" ".join(["Settings saved."] + msgs))


def _settings_reply(message, error=False):
    r = _r()
    html = render_template("_opx_monitor_body.html", flash=message, flash_error=error,
                           **_view("settings"))
    return make_response(html, 400 if error else 200)


@opxmon_bp.route("/opx-monitor/close", methods=["POST"])
def opx_monitor_close():
    m = monitor()
    qm_id = (request.form.get("qm_id") or "").strip() or None
    idle = request.form.get("idle") == "1"
    every = request.form.get("all") == "1" or idle
    # "close all" closes exactly the QMs the confirm dialog listed, never
    # whatever is open on the server by the time the request lands
    confirmed = [x.strip() for x in (request.form.get("qm_ids") or "").split(",") if x.strip()]
    if (not qm_id and not every) or (every and not confirmed):
        return jsonify({"ok": False, "error": "Nothing named to close."}), 400
    who = (request.form.get("by") or "").strip()[:80]
    res = (m.close(None, who=who, qm_ids=confirmed, idle=idle) if every else m.close(qm_id, who=who))
    return jsonify(res), (200 if res.get("ok") else 409)


def armed() -> bool:
    try:
        return bool(om.load_settings(current_app.instance_path).get("alarms_armed"))
    except Exception:  # noqa: BLE001
        return False
