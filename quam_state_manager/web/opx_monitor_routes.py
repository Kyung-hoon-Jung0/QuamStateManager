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
from pathlib import Path

from flask import Blueprint, current_app, jsonify, make_response, render_template, request

from quam_state_manager.core import opx_monitor as om

logger = logging.getLogger(__name__)

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
    """A last-good-read time: HH:MM:SS today, the full date and zone otherwise."""
    full = om.local_time(value)
    t = om.timestamp(value)
    if t is None:
        return full
    if _dt.datetime.fromtimestamp(t).date() == _dt.date.today():
        return full.split(' ')[1]
    return full


def _idle_states(m, st, s) -> dict:
    """Idle state per open QM, as the page shows it (stale data never reads as idle)."""
    watched = m.watch_info()
    idle_states = {q['id']: om.idle_state(q, watched.get(str(q['id'])), s['idle_minutes'])
                   for q in (st.get('answer') or {}).get('open_qms') or []}
    if st['state'] != 'ok':
        age = ('%.0f s ago' % st['age_s']) if st.get('age_s') is not None else 'at an unknown time'
        idle_states = {k: om.stale_idle_state(v, age) for k, v in idle_states.items()}
    return idle_states


def _shown_ids(m, idle: bool) -> set:
    """The QM ids the Clean up page shows right now for "close all" (every open QM) or
    "close idle" (the idle ones, only on a fresh answer); the same rule as the template."""
    st = m.status()
    open_qms = (st.get('answer') or {}).get('open_qms') or []
    if not idle:
        return {str(q['id']) for q in open_qms}
    if st['state'] != 'ok':
        return set()
    return {str(k) for k, v in _idle_states(m, st, m.settings()).items() if v['idle']}


LIST_CHANGED = "The list changed; check it again. Nothing was closed."


def _view(tab: str) -> dict:
    m = monitor()
    st = m.status()
    s = m.settings()
    h = m.history()
    t = dict(s.get("target") or {})
    key = om.target_key(t)
    has_headers = bool(om.load_secret_headers(m.instance, key)) if key else False
    skew = m.clock_skew()
    idle_states = _idle_states(m, st, s)
    hottest = max((dict(r, controller=c['name']) for c in (st.get('answer') or {}).get('controllers') or [] for r in om.temperature_rows(c) if r['value'] is not None), key=lambda r: (om.temperature_limit(r['key'], s['thresholds']) is not None and r['value'] > om.temperature_limit(r['key'], s['thresholds']), r['value']), default=None)
    try:
        runs, run_reason = _run_candidates() if tab == 'jobs' else ([], '')
    except Exception:
        runs, run_reason = [], 'Dataset index unavailable; no estimated links.'
    open_qms = (st.get('answer') or {}).get('open_qms') or []
    owners = {q['id']: om.safe_owner_info(q) for q in open_qms}
    # labels are read at render time only and never leave the page
    pc_names, pc_names_problem = om.load_pc_names()
    seen_tags = om.owner_tags(st.get('answer'))
    owner_ctx = {'owners': owners, 'any_marker': any(o['markers'] for o in owners.values()),
                 'pc_names': pc_names, 'pc_names_problem': pc_names_problem,
                 'this_tag': om.own_pc_tag(), 'seen_tags': seen_tags,
                 'pc_name_rows': sorted(t for t in set(pc_names) | set(seen_tags) if _TAG_RE.fullmatch(t)),
                 'pc_name_bad': [(_key_ref(k), k) for k in sorted(pc_names) if not _TAG_RE.fullmatch(k)]}
    temperature_failed = 'controllers' in st['section_errors'] or any(k.endswith('.temperatures') for k in (st.get('answer') or {}).get('refused', {}))
    verdict = om.verdict(st.get('answer'), st['health'], idle_states, st['state'], st['age_s'])
    hottest_limit = om.temperature_limit(hottest['key'], s['thresholds']) if hottest else None
    failures = len(set((st.get('answer') or {}).get('refused') or {}) | set(st['section_errors']))
    details = st['health'].get('details')
    owner_ctx.update({
        'band': om.band_view(verdict, st['state'], st['health'], plain_error(details) if details else None),
        'tiles': om.live_tiles(st.get('answer'), idle_states, None if temperature_failed else hottest, hottest_limit, st['state'], failures) if tab == 'live' else [],
        'qm_states': {q['id']: om.qm_state(q, idle_states.get(q['id'])) for q in open_qms},
        'hottest_limit': hottest_limit,
    })
    return {**owner_ctx, 'temperature_failed': 'controllers' in st['section_errors'] or any(k.endswith('.temperatures') for k in (st.get('answer') or {}).get('refused', {})), 'unreadable_temps': om.unreadable_temperatures(st.get('answer')), 'last_job': last_job, 'hottest': hottest, 'local_time': om.local_time, 'plain_error': plain_error, 'call_failure': om.call_failure, 'raw_error': raw_error(st), 'stale_time': stale_time, 'opx_time': om.opx_time, 'pc_zone': om.zone_text(), 'words': om.words, 'job_activity': om.job_activity, 'firmware_summary': om.firmware_summary, 'job_limit': min(1000, max(100, request.args.get('limit', 100, type=int))), 'verdict': verdict, 'run_reason': run_reason, 'run_match': lambda j: run_match(j, runs, run_reason, skew=skew) if not run_reason else {}, 'clock_skew': skew, 'skew_text': om.skew_text(skew, m.clock()), 'metric_known': om.metric_known, 'job_list_cap': om.JOB_LIST_CAP, 'value_vs_limit': om.value_vs_limit,
            "trends": m.trends().series(m.clock()) if m.trends() and tab == "live" else {}, "idle_states": idle_states, "idle_ids": [k for k, v in idle_states.items() if v['idle'] and st['state'] == 'ok'], "tab": tab, "tabs": TABS, "tab_labels": TAB_LABELS, "st": st, "settings": s,
            "target": t, "has_headers": has_headers,
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
    return render_template(tpl, **r._ctx(page="opx_monitor", **_view(tab)))


@opxmon_bp.route("/opx-monitor/body")
def opx_monitor_body():
    tab = request.args.get("tab", "live")
    if tab not in TABS:
        tab = "live"
    monitor().want(tab, request.args.get("limit", 100, type=int))
    html = render_template("_opx_monitor_body.html", **_view(tab))
    if request.headers.get("HX-Request"):
        # keep the top-bar health pill in step with the body it sits above
        html += ('<div id="ui-opxmon-alarm-slot" hx-swap-oob="innerHTML">'
                 + render_template("_opx_alarm_badge.html", st=monitor().status(),
                                   value_vs_limit=om.value_vs_limit) + '</div>')
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
    m.ensure_running_if_armed()
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
           "admin_url": (f.get("admin_url") or "").strip(), "idle_minutes": _idle_minutes(f.get("idle_minutes"), s["idle_minutes"]), "thresholds": th, "alarms_armed": _form_bool("alarms_armed")}
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
    full = display_text(text, 10_000)
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
    """Add, change or remove PC names. Replies never echo a name.

    Only the rows this window changed are applied (a row whose posted name equals the
    ``was__<tag>`` it was drawn with is left alone), each one onto the file as read at
    that moment, so two windows editing different tags keep both edits."""
    f = request.form
    changes = []
    for k, v in f.items():
        if not k.startswith("label__"):
            continue
        tag = k[len("label__"):]
        if not _TAG_RE.fullmatch(tag):
            continue
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
            return _settings_reply(f"Name for tag {tag}: {problem}", error=True)
        changes.append((tag, label or None))
    # names-file keys that are not valid tags: rename to a valid tag, or remove
    names_now, _ = om.load_pc_names()
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
                return _settings_reply("A tag uses only a-z and 0-9, at most 16 characters.", error=True)
            changes.append((new, names_now[key]))
            changes.append((key, None))
        elif _form_bool("removebad__" + k[len("badkey__"):]):
            changes.append((key, None))
    tag, raw_label = (f.get("new_tag") or "").strip(), f.get("new_label") or ""
    if tag or raw_label.strip():
        if not _TAG_RE.fullmatch(tag):
            return _settings_reply("A tag uses only a-z and 0-9, at most 16 characters.", error=True)
        label, problem = om.check_pc_label(raw_label)
        if problem:
            return _settings_reply(problem, error=True)
        if not label:
            return _settings_reply("Give the new tag a name.", error=True)
        changes.append((tag, label))
    count = None
    try:
        for t, label in changes:
            count = om.apply_pc_name_change(t, label, om.marker_dir())
    except OSError:
        return _settings_reply("The PC names file could not be written.", error=True)
    if count is None:
        return _settings_reply("No PC name changed.")
    return _settings_reply(f"PC names saved ({count}).")


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
    if request.form.get('all') == '1' and request.form.get('confirm_count') != str(len(confirmed)):
        return jsonify({'ok': False, 'error': 'Type the count to confirm close all.'}), 400
    # the consent is for the exact set the user saw: a set that changed since is refused
    if every and set(confirmed) != _shown_ids(m, idle):
        return jsonify({'ok': False, 'error': LIST_CHANGED, 'list_changed': True}), 409
    who = (request.form.get("by") or "").strip()[:80]
    res = (m.close(None, who=who, qm_ids=confirmed, idle=idle) if every else m.close(qm_id, who=who))
    return jsonify(res), (200 if res.get("ok") else 409)


def armed() -> bool:
    try:
        return bool(om.load_settings(current_app.instance_path).get("alarms_armed"))
    except Exception:  # noqa: BLE001
        return False
