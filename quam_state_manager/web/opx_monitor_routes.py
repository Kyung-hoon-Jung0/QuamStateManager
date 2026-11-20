"""OPX monitor pages (Live / Jobs / History / Clean up / Monitor settings).

The pages read the cached answer of ``core.opx_monitor.Monitor``; a page GET
asks the poller to keep going for a few intervals and never waits on the OPX.
Clean-up and settings are POSTs (the app's origin guard applies)."""
from __future__ import annotations
import math
import re
import datetime as _dt

import json
import logging
import threading
import tempfile
import os
from pathlib import Path

from flask import Blueprint, current_app, jsonify, make_response, render_template, request

from quam_state_manager.core import opx_monitor as om

logger = logging.getLogger(__name__)
_PC_NAMES_LOCK = threading.RLock()


opxmon_bp = Blueprint("opx_monitor", __name__)

TABS = ("live", "jobs", "history", "cleanup", "settings")
TAB_LABELS = {"live": "Live", "jobs": "Jobs", "history": "History", "cleanup": "Clean up",
              "settings": "Monitor settings"}


def _idle_minutes(raw, old):
    """The form's idle minutes as a number; a blank or bad value keeps the old one."""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return float(old)
    return v if math.isfinite(v) else float(old)


def chip_default_source(app):
    """What the monitor watches when Monitor settings name no target: the open chip's
    wiring ``network`` block, polled with the env SM already runs that chip's project
    subprocesses with (the project's chosen env, else the app's selected env). Reads the
    app's active context directly, so the poller thread follows a chip switch too."""
    def source():
        from quam_state_manager.core import project_env
        name = app.config.get("active_context")
        ctx = (app.config.get("contexts") or {}).get(name) if name is not None else None
        if not ctx or ctx.get("type") != "quam":
            return None
        store = ctx.get("store")
        wiring = getattr(store, "wiring", None)
        state = getattr(store, "state", None)
        network = (wiring.get("network") if isinstance(wiring, dict) else None)
        if network is None and isinstance(state, dict):
            network = state.get("network")
        py, py_source = project_env.run_env_source(app.instance_path, ctx.get("qualibrate_project"))
        return {"target": om.chip_target(network), "python": py, "python_source": py_source,
                "env_label": project_env.label(py)}
    return source


def monitor() -> om.Monitor:
    m = current_app.config.get("opx_monitor")
    if m is None:
        app = current_app._get_current_object()
        m = current_app.config["opx_monitor"] = om.Monitor(
            current_app.instance_path, default_source=chip_default_source(app))
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


def _run_candidates():
    """Use the existing chip-root rule and DatasetStore index, never all workspace runs."""
    r = _r()
    ctx = r._active_ctx()
    if not ctx or ctx.get('type') != 'quam':
        return [], 'No chip is open; run links are unavailable.'
    roots = r._hub_roots_for(ctx)
    if not roots:
        return [], 'The open chip has no known dataset folders; run links are unavailable.'
    candidates, seen = [], set()
    for root, source in roots:
        store = r._get_or_create_store(Path(root), rescan=True)
        if store is None or store.scan_truncated:
            return [], 'Dataset index is incomplete; run links are unavailable.'
        for run in list(store.runs.values()):
            instant = om.timestamp(run.created_at)
            if instant is None:
                return [], 'A run creation time or timezone is unknown; run links are unavailable.'
            folder = str(run.folder_path.resolve())
            if folder in seen:
                continue
            seen.add(folder)
            candidates.append({'time': instant, 'id': run.run_id,
                               'url': '/dataset/' + r._folder_key(root) + ':' + str(run.run_id)})
    return candidates, ''


def run_match(job, candidates, reason='', window=90, skew=None):
    """Job times are the OPX clock, run times the PC clock: compare only after
    correcting by the MEASURED offset (``{'skew_s', 'uncertainty_s'}``); its
    uncertainty widens the window, never narrows it. Unknown offset: no link."""
    if reason:
        return {'reason': reason}
    instant = om.timestamp(job.get('created_at'))
    if instant is None:
        return {'reason': 'Job creation time unknown; no estimated link.'}
    if not isinstance(skew, dict) or skew.get('skew_s') is None:
        return {'reason': 'OPX clock offset unknown; no estimated link.'}
    instant += skew['skew_s']
    width = window + abs(skew.get('uncertainty_s') or 0.0)
    matches = [r for r in candidates if abs(r['time'] - instant) <= width]
    if len(matches) == 1:
        return {**matches[0], 'reason': f'estimated from times (job and run creation, +/-{width:.0f} s)'}
    return {'reason': f'{len(matches)} runs at that time; estimated from times (+/-{width:.0f} s)'}


def last_job(q):
    if q.get('jobs') is None:
        return 'unknown'
    if not q['jobs']:
        return 'none observed'
    job = max(q['jobs'], key=lambda j: om.timestamp(j.get('updated_at')) or om.timestamp(j.get('created_at')) or 0)
    head = str(job['id']) + ' ' + str(job['status'])
    if not job.get('created_at'):
        return head + ', creation time not reported'
    return head + ', created ' + om.opx_time(job.get('created_at')) + ' (OPX clock)'


_RAW_ERROR = re.compile(r':\s+[A-Za-z_][\w.]*(?:Error|Exception):.*?(?=\. The last good read|$)', re.S)


def plain_error(text):
    """The plain sentence without the raw exception text (shown once in Technical details)."""
    return _RAW_ERROR.sub('', text) if isinstance(text, str) else text


def raw_error(st):
    """The first raw exception text on the page, or None."""
    cap = st.get('capabilities') or {}
    for text in ((st.get('health') or {}).get('details'), st.get('sentence'), cap.get('live_reason'), cap.get('jobs_reason'), cap.get('close_reason')):
        m = _RAW_ERROR.search(text) if isinstance(text, str) else None
        if m:
            return m.group(0).lstrip(': ').strip()
    return None


def stale_time(value):
    """A last-good-read time: HH:MM:SS today, the full date and zone otherwise; '' when no
    time is on record, so a caller drops the time instead of printing a placeholder."""
    full = om.read_time(value)
    t = om.timestamp(value)
    if t is None:
        return full
    if _dt.datetime.fromtimestamp(t).date() == _dt.date.today():
        return full.split(' ')[1]
    return full


def _idle_states(m, st, s) -> dict:
    """Idle state per open QM, as the page shows it (stale data never reads as idle)."""
    watched = st.get('watched', {})
    idle_states = {q['id']: om.idle_state(q, watched.get(str(q['id'])), s['idle_minutes'])
                   for q in (st.get('answer') or {}).get('open_qms') or []}
    if st['state'] != 'ok':
        age = ('%.0f s ago' % st['age_s']) if st.get('age_s') is not None else 'at an unknown time'
        idle_states = {k: om.stale_idle_state(v, age) for k, v in idle_states.items()}
    return idle_states


def _shown_ids(m, idle: bool, st: dict) -> set:
    """The QM ids the Clean up page shows for "close all" (every open QM) or "close idle"
    (the idle ones, only on a fresh answer) in the status answer ``st``; the same rule as
    the template. The caller passes the one answer it also judged freshness on."""
    open_qms = (st.get('answer') or {}).get('open_qms') or []
    if not idle:
        return {str(q['id']) for q in open_qms}
    if st['state'] != 'ok':
        return set()
    return {str(k) for k, v in _idle_states(m, st, m.settings()).items() if v['idle']}


LIST_CHANGED = "The list changed; check it again. Nothing was closed."
STALE_STATUS = "The status is not fresh; wait for the next refresh. Nothing was closed."


def _chip_rack_wiring():
    try:
        engine = _r()._engine()
        return engine.get_instrument_wiring() if engine else None
    except Exception:
        logger.debug("Open chip wiring unavailable for QM rack", exc_info=True)
        return None


def _view(tab: str, st=None) -> dict:
    m = monitor()
    st = m.status() if st is None else st
    s = m.settings()
    h = m.history()
    t = dict(s.get("target") or {})
    key = om.target_key(t)
    has_headers = bool(om.load_secret_headers(m.instance, key)) if key else False
    skew = st.get('clock_skew')
    idle_states = _idle_states(m, st, s)
    hottest = max((dict(r, controller=c['name']) for c in (st.get('answer') or {}).get('controllers') or [] for r in om.temperature_rows(c) if r['value'] is not None), key=lambda r: (om.temperature_limit(r['key'], s['thresholds']) is not None and r['value'] > om.temperature_limit(r['key'], s['thresholds']), r['value']), default=None)
    try:
        runs, run_reason = _run_candidates() if tab == 'jobs' else ([], '')
    except Exception:
        runs, run_reason = [], 'Dataset index unavailable; no estimated links.'
    open_qms = (st.get('answer') or {}).get('open_qms') or []
    from quam_state_manager.web.opx_monitor_rack import qm_rack
    wiring = _chip_rack_wiring() if tab == 'live' and open_qms else None
    racks = {q['id']: qm_rack(q.get('port_map'), wiring) for q in open_qms} if tab == 'live' else {}
    owners = {q['id']: om.safe_owner_info(q) for q in open_qms}
    # labels are read at render time only and never leave the page
    with _PC_NAMES_LOCK:
        pc_names, pc_names_problem = om.load_pc_names()
    seen_tags = om.owner_tags(st.get('answer'))
    owner_ctx = {'qm_racks': racks, 'owners': owners, 'any_marker': any(o['markers'] for o in owners.values()),
                 'pc_names': pc_names, 'pc_names_problem': pc_names_problem,
                 'this_tag': om.own_pc_tag(), 'seen_tags': seen_tags,
                 'pc_name_rows': sorted(t for t in set(pc_names) | set(seen_tags) if _TAG_RE.fullmatch(t)),
                 'pc_name_bad': [(_key_ref(k), k) for k in sorted(pc_names) if not _TAG_RE.fullmatch(k)]}
    temperature_failed = 'controllers' in st['section_errors'] or any(k.endswith('.temperatures') for k in (st.get('answer') or {}).get('refused', {}))
    verdict = om.verdict(st.get('answer'), st['health'], idle_states, st['state'], st['age_s'])
    hottest_limit = om.temperature_limit(hottest['key'], s['thresholds']) if hottest else None
    failures = len(st.get('deferred_errors') or
                   (set((st.get('answer') or {}).get('refused') or {}) | set(st['section_errors'])))
    details = st['health'].get('details')
    owner_ctx.update({
        'band': om.band_view(verdict, st['state'], st['health'], plain_error(details) if details else None),
        'tiles': om.live_tiles(st.get('answer'), idle_states, None if temperature_failed else hottest, hottest_limit, st['state'], failures) if tab == 'live' else [],
        'qm_states': {q['id']: om.qm_state(q, idle_states.get(q['id'])) for q in open_qms},
        'hottest_limit': hottest_limit,
    })
    for tile in owner_ctx['tiles']:
        if st['state'] != 'ok' and tile['label'] != 'Server' and tile['value'] != 'unknown':
            section = 'controllers' if tile['label'] == 'Hottest sensor' else 'open_qms'
            at = stale_time(st['section_times'].get(section))
            if at:
                tile['note'] = (tile['note'] + '; ' if tile['note'] else '') + 'as of ' + at
    busy_text = om.busy_since_text(verdict.get('since'), skew, st.get('answer'), m.clock())
    if verdict['label'].startswith('BUSY') and (not busy_text or 'busy time unknown' in busy_text):
        observed = st.get('busy_observed_s')
        if observed is not None and observed >= st['interval_s']:
            busy_text = f' for at least {om._span_words(observed)} (observed by SM)'
    owner_ctx['busy_since'] = busy_text
    return {**owner_ctx,
            'temperature_failed': ('controllers' in st['section_errors']
                                   or any(k.endswith('.temperatures')
                                          for k in (st.get('answer') or {}).get('refused', {}))),
            'unreadable_temps': om.unreadable_temperatures(st.get('answer')), 'last_job': last_job, 'hottest': hottest,
            'local_time': om.local_time, 'read_time': om.read_time, 'plain_error': plain_error,
            'call_failure': om.call_failure, 'raw_error': raw_error(st), 'stale_time': stale_time,
            'opx_time': om.opx_time, 'pc_zone': om.zone_text(), 'words': om.words, 'job_activity': om.job_activity,
            'firmware_summary': om.firmware_summary,
            'job_limit': min(1000, max(100, request.args.get('limit', 100, type=int))), 'verdict': verdict,
            'run_reason': run_reason,
            'run_match': lambda j: run_match(j, runs, run_reason, skew=skew) if not run_reason else {},
            'clock_skew': skew, 'skew_text': om.skew_text(skew, m.clock()), 'metric_known': om.metric_known,
            'job_list_cap': om.JOB_LIST_CAP, 'value_vs_limit': om.value_vs_limit,
            "trends": m.trends().series(m.clock()) if m.trends() and tab == "live" else {}, "idle_states": idle_states, "idle_ids": [k for k, v in idle_states.items() if v['idle'] and st['state'] == 'ok'], "tab": tab, "tabs": TABS, "tab_labels": TAB_LABELS, "st": st, "settings": s,
            "target": t, "has_headers": has_headers,
            "saved": m.saved_settings() if tab == "settings" else {},
            "target_label": om.target_label, "env_label": _env_label,
            "history_rows": h.table() if h and tab == "history" else [],
            "actions": h.recent_actions() if h and tab in ("history", "cleanup") else [],
            "offered": om.offered_metrics(st.get("answer")),
            "envs": _envs() if tab == "settings" else [],
            "min_interval": om.MIN_INTERVAL_S,
            "connection_hint": om.connection_hint, "temperature_limit": om.temperature_limit, "temperature_mapping_verified": om.temperature_mapping_verified, "temperature_rows": om.temperature_rows, "slot_temperatures": om.slot_temperatures}


@opxmon_bp.route("/opx-monitor")
@opxmon_bp.route("/opx-monitor/<tab>")
def opx_monitor_page(tab="live"):
    if tab not in TABS:
        tab = "live"
    monitor().want(tab, request.args.get("limit", 100, type=int))
    r = _r()
    tpl = "_opx_monitor.html" if r._is_htmx() else "opx_monitor.html"
    view = _view(tab)
    html = render_template(tpl, **r._ctx(page="opx_monitor", **view))
    return html + _snapshot_badge(view) if r._is_htmx() else html


def _snapshot_badge(view):
    return ('<div id="ui-opxmon-alarm-slot" hx-swap-oob="innerHTML">'
            + render_template('_opx_alarm_badge.html', st=view['st'], value_vs_limit=om.value_vs_limit) + '</div>')


@opxmon_bp.route("/opx-monitor/body")
def opx_monitor_body():
    tab = request.args.get("tab", "live")
    if tab not in TABS:
        tab = "live"
    monitor().want(tab, request.args.get("limit", 100, type=int))
    view = _view(tab)
    html = render_template("_opx_monitor_body.html", **view)
    if request.headers.get("HX-Request"):
        # keep the top-bar health pill in step with the body it sits above
        html += _snapshot_badge(view)
        if tab == 'settings':
            # Other open monitor tabs receive the same read without replacing a Settings form.
            live = render_template('_opx_monitor_body.html', **_view('live', st=view['st']))
            html += '<template data-opxmon-live>' + live + '</template>'
    return html


@opxmon_bp.route("/opx-monitor/api/status")
def opx_monitor_status():
    m = monitor()
    m.want()
    return jsonify(m.status())


@opxmon_bp.route("/opx-monitor/alarm-badge")
def opx_alarm_badge():
    """The top-bar badge; fires a toast once per new alarm in this process."""
    m = monitor()
    m.want()
    st = m.status()
    resp = make_response(render_template("_opx_alarm_badge.html", st=st, value_vs_limit=om.value_vs_limit))
    fresh = m.new_alarm_toasts() if st.get("alarms_armed") else []
    if fresh:
        a = fresh[0]
        value = om.value_vs_limit(a['value'], a['limit'])
        more = f" (+{len(fresh) - 1} more)" if len(fresh) > 1 else ""
        resp.headers["HX-Trigger"] = json.dumps({"showToast": {
            "message": f"OPX alarm: {a['controller']} {a['label']} = {value} {a['unit']} "
                       f"({a['direction']} limit {a['limit']:g} {a['unit']}){more}", "level": "warning"}})
    return resp


def _env_label(python):
    from quam_state_manager.core import project_env
    return project_env.label(python)


def _form_bool(name):
    return request.form.get(name) in ("1", "on", "true", "yes")


@opxmon_bp.route("/opx-monitor/settings", methods=["POST"])
def opx_monitor_save_settings():
    """Every field is checked before anything is stored: a 400 stores nothing, leaves the
    monitor as it was and draws the form back with what the user typed."""
    m = monitor()
    # the file as saved: the open chip's default target is never written as an override
    s = m.saved_settings()
    f = request.form
    msgs = []
    hdr_txt = f.get("headers")
    typed_headers = bool(hdr_txt and hdr_txt.strip())

    def refuse(message):
        if typed_headers:
            message += " Type the connection headers again; they are never shown."
        return _settings_reply(message, error=True, form="settings")

    host = (f.get("host") or "").strip()
    port = (f.get("port") or "").strip()
    if port and not port.isdigit():
        return refuse("Port must be a whole number.")
    target = ({"kind": "qmm", "host": host, "port": int(port) if port else None,
               "cluster_name": (f.get("cluster_name") or "").strip() or None,
               "tls": _form_bool("tls")} if host else None)
    py = (f.get("python") or "").strip() or None
    if py and not Path(py).is_file():
        return refuse("That Python interpreter does not exist.")
    admin_url = (f.get("admin_url") or "").strip()
    if admin_url and not om.admin_origin_ok(admin_url):
        if "://" not in admin_url:
            return refuse("The Admin URL must start with http:// or https://; this looks like a host; put it in Host. "
                          "The Admin URL is only for the optional admin panel.")
        return refuse("The Admin URL must be an http:// or https:// address with nothing after the host and port.")
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
            return refuse(f"The limit for {k[4:]} is not a number.")
        th[k[4:]] = val
    headers = None
    if target is not None and not _form_bool("clear_headers") and typed_headers:
        try:
            headers = json.loads(hdr_txt)
            if not isinstance(headers, dict):
                raise ValueError
        except ValueError:
            return refuse("Connection headers must be a JSON object.")
    new = {"target": target, "python": py, "interval_s": om.clamp_interval(f.get("interval_s")),
           "admin_url": admin_url, "idle_minutes": _idle_minutes(f.get("idle_minutes"), s["idle_minutes"]), "thresholds": th, "alarms_armed": _form_bool("alarms_armed")}
    changed_conn = (new["target"] != s.get("target")) or (new["python"] != s.get("python")) or new["admin_url"] != s.get("admin_url")
    om.save_settings(m.instance, new)
    if target is not None:
        key = om.target_key(target)
        if _form_bool("clear_headers"):
            om.save_secret_headers(m.instance, key, {})
            changed_conn = True
        elif headers is not None:
            om.save_secret_headers(m.instance, key, headers)
            changed_conn = True
            msgs.append("Connection headers stored (not shown again).")
    if changed_conn:
        m.reset()
    m.want()
    m.ensure_running_if_armed()
    return _settings_reply(" ".join(["Settings saved."] + msgs), at="settings")


@opxmon_bp.route("/opx-monitor/settings/use-default", methods=["POST"])
def opx_monitor_use_default():
    """Drop the saved target and Python (the override); every other setting is kept.
    The monitor then watches the open chip's OPX with that chip's env."""
    m = monitor()
    s = m.saved_settings()
    if s.get("settings_error"):
        return _settings_reply(s["settings_error"], error=True, at="settings")
    om.save_settings(m.instance, {**s, "target": None, "python": None})
    m.reset()
    m.want()
    eff = m.settings()
    if eff.get("target_source") == "chip":
        msg = "Using the open chip's OPX: " + om.target_label(eff["target"]) + "."
    else:
        msg = "Override cleared. No chip is open, so no OPX is watched."
    return _settings_reply(msg, at="settings")


_TAG_RE = re.compile(r"[a-z0-9]{1,16}")
_ESCAPE_RE = re.compile(r"\\[uU][0-9a-fA-F]{4}")
LABEL_SHOWN_MAX = 40


def _key_ref(key: str) -> str:
    """A form-safe reference to a names-file key that is not a valid tag (the key itself
    may hold characters a form field cannot carry back unchanged)."""
    import hashlib
    return hashlib.sha256(key.encode("utf-8", "surrogatepass")).hexdigest()[:16]


@opxmon_bp.app_template_filter("label_shown")
def _label_shown(text):
    """A PC name as the page shows it: escaped like the owner cells, and a name longer than
    40 characters cut with "..." (the full escaped text goes in a title)."""
    from quam_state_manager.core.qm_owner_marker import display_text
    full = display_text(text.strip(), 10_000)
    return full if len(full) <= LABEL_SHOWN_MAX else full[:LABEL_SHOWN_MAX - 3] + "..."


@opxmon_bp.app_template_filter("label_full")
def _label_full(text):
    from quam_state_manager.core.qm_owner_marker import display_text
    return display_text(text, 300)


@opxmon_bp.app_template_filter("owner_shown")
def _owner_shown(text, limit=120):
    """Owner text read from a server or the names file, escaped and capped for display."""
    from quam_state_manager.core.qm_owner_marker import display_text
    return display_text(text, limit)


@opxmon_bp.route("/opx-monitor/pc-names", methods=["POST"])
def opx_monitor_save_pc_names():
    with _PC_NAMES_LOCK:
        return _save_pc_names_locked()


def _save_pc_names_locked():
    """Add, change or remove PC names. Replies never echo a name.

    Only the rows this window changed are applied (a row whose posted name equals the
    ``was__<tag>`` it was drawn with is left alone), each one onto the file as read at
    that moment. The caller holds the process lock through conflict checks and writing."""
    f = request.form

    def refuse(message, status=None, current=()):
        return _settings_reply(message, error=True, status=status, form="names", current=current)

    changes = []
    rows = set()
    for k, v in f.items():
        if not k.startswith("label__"):
            continue
        tag = k[len("label__"):]
        if not _TAG_RE.fullmatch(tag):
            continue
        rows.add(tag)
        if _form_bool("remove__" + tag):
            changes.append((tag, None))
            continue
        # a row the user did not touch is left alone, whatever the stored name holds
        if (v or "").strip() == (f.get("was__" + tag) or "").strip():
            continue
        label, problem = om.check_pc_label(v)
        if not problem and _ESCAPE_RE.search(label):
            problem = "A PC name cannot hold an escaped character such as \\u202e; type the name without it."
        if problem:
            return refuse(f"Name for tag {tag}: {problem}")
        changes.append((tag, label or None))
    # names-file keys that are not valid tags: rename to a valid tag, or remove
    names_now, _ = om.load_pc_names()
    # The display loader normalizes labels; untouched stored strings must round-trip exactly.
    try:
        stored = json.loads((om.marker_dir() / 'pc_names.json').read_bytes())
        if isinstance(stored, dict):
            for key in names_now:
                if isinstance(stored.get(key), str):
                    names_now[key] = stored[key]
    except (OSError, ValueError):
        pass
    taken = set()  # a tag that already has another name in the file

    def removed_here(t):
        # a tag ticked for removal in this same save is free for a new name
        return t in rows and _form_bool("remove__" + t)

    by_ref = {_key_ref(k): k for k in names_now if not _TAG_RE.fullmatch(k)}
    for k in f:
        if not k.startswith("badkey__"):
            continue
        key = by_ref.get(k[len("badkey__"):])
        if key is None:
            continue
        new = (f.get("rename__" + k[len("badkey__"):]) or "").strip()
        if new:
            if not _TAG_RE.fullmatch(new):
                return refuse("A tag uses only a-z and 0-9, at most 16 characters.")
            if new in names_now and names_now[new] != names_now[key] and not removed_here(new):
                taken.add(new)
            changes.append((new, names_now[key]))
            changes.append((key, None))
        elif _form_bool("removebad__" + k[len("badkey__"):]):
            changes.append((key, None))
    tag, raw_label = (f.get("new_tag") or "").strip(), f.get("new_label") or ""
    if tag or raw_label.strip():
        if not _TAG_RE.fullmatch(tag):
            return refuse("A tag uses only a-z and 0-9, at most 16 characters.")
        label, problem = om.check_pc_label(raw_label)
        if problem:
            return refuse(problem)
        if not label:
            return refuse("Give the new tag a name.")
        changes.append((tag, label))
        if tag in names_now and names_now[tag] != label and not removed_here(tag):
            taken.add(tag)
    # two changes of this one save that give the same tag different names
    twice, aimed = set(), {}
    for t, label in changes:
        if label is not None:
            if t in aimed and aimed[t] != label:
                twice.add(t)
            aimed.setdefault(t, label)
    # Compare the same display value carried by each row's hidden was__ field.
    # Validate edited fields first so invalid input keeps its field-specific 400.
    conflicts = {t for t in rows if t in names_now and "was__" + t not in f}
    for t, _ in changes:
        if t in rows and ("was__" + t not in f or
                          f["was__" + t] != _label_shown(names_now.get(t, ''))):
            conflicts.add(t)
    if twice or taken or conflicts:
        said = []
        if twice:
            one = "keep one of them." if len(twice) == 1 else "keep one for each."
            said.append(f"This save gives more than one name to {_tags_text(twice)}; {one}")
        taken -= twice
        if taken:
            head = _tags_text(taken)
            head = head[:1].upper() + head[1:]
            if len(taken) == 1:
                said.append(f"{head} already has a name; remove it first or pick another tag.")
            else:
                said.append(f"{head} already have names; remove them first or pick other tags.")
        if conflicts:
            noun = "name" if len(conflicts) == 1 else "names"
            said.append(f"Another window changed the {noun} of {_tags_text(conflicts)}; "
                        "the current names are shown above. Type your change again.")
        return refuse(" ".join(said + ["Nothing was saved."]), status=409, current=conflicts)
    count = None
    try:
        for t, label in changes:
            if label is None:
                names_now.pop(t, None)
            else:
                names_now[t] = label
        if changes:
            directory = om.marker_dir()
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / 'pc_names.json'
            payload = json.dumps(names_now, ensure_ascii=True, indent=1).encode('utf-8')
            # Stage the new file before rotating the previous file into the backup.
            fd, temporary = tempfile.mkstemp(dir=directory, prefix='pc_names-', suffix='.tmp')
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(payload)
                if path.exists():
                    backup = path.with_suffix('.json.bak')
                    backup.write_bytes(path.read_bytes())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            count = len(names_now)
    except OSError:
        return refuse("The PC names file could not be written.")
    if count is None:
        return _settings_reply("No PC name changed.", at="names")
    return _settings_reply(f"PC names saved ({count}).", at="names")


def _tags_text(tags) -> str:
    tags = sorted(tags)
    return ("tag " if len(tags) == 1 else "tags ") + ", ".join(tags)


def _settings_reply(message, error=False, status=None, form=None, at=None, current=()):
    """The settings body with the message next to the Save button of the form it is about
    (``at``, default ``form``). A refusal draws that form back with the values the user
    sent, except the PC-name rows in ``current`` (drawn from the file as it is now) and
    the connection headers (never shown)."""
    posted = {}
    if error and form:
        posted = {k: v for k, v in request.form.items() if k != "headers"}
        # a row the user did not touch shows the file as it is now
        untouched = [k[len("label__"):] for k, v in posted.items() if k.startswith("label__")
                     and not posted.get("remove__" + k[len("label__"):])
                     and (v or "").strip() == (posted.get("was__" + k[len("label__"):]) or "").strip()]
        for t in list(current) + untouched:
            for k in ("label__" + t, "was__" + t, "remove__" + t):
                posted.pop(k, None)
    html = render_template("_opx_monitor_body.html", flash=message, flash_error=error,
                           flash_at=at or form, posted=posted, posted_form=form if posted else None,
                           **_view("settings"))
    return make_response(html, status if status is not None else (400 if error else 200))


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
    if request.form.get('all') == '1' and request.form.get('confirm_count') != str(len(confirmed)):
        return jsonify({'ok': False, 'error': 'Type the count to confirm close all.'}), 400
    st = m.status()  # one answer for the freshness check and the shown list
    if idle and st["state"] != "ok":
        return jsonify({"ok": False, "error": STALE_STATUS}), 409
    # the consent is for the exact set the user saw: a set that changed since is refused
    if every and set(confirmed) != _shown_ids(m, idle, st):
        return jsonify({'ok': False, 'error': LIST_CHANGED, 'list_changed': True}), 409
    who = (request.form.get("by") or "").strip()[:80]
    res = (m.close(None, who=who, qm_ids=confirmed, idle=idle) if every else m.close(qm_id, who=who))
    return jsonify(res), (200 if res.get("ok") else 409)


def armed() -> bool:
    try:
        return bool(om.load_settings(current_app.instance_path).get("alarms_armed"))
    except Exception:  # noqa: BLE001
        return False
