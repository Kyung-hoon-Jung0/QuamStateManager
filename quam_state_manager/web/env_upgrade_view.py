"""Environment report presentation and explicit working-copy review actions."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import secrets
import tempfile
from datetime import datetime

from flask import current_app, jsonify, render_template, request

from quam_state_manager.core import env_seen, env_upgrade, env_rename, waveform_refs
from quam_state_manager.core import config_generator, project_env, state_env_baseline as baseline, state_env_schema


def reference_for(ctx, inst):
    event = (ctx.get("env_upgrade_report") or {}).get("event") or {}
    key = (event.get("previous") or {}).get("baseline_key")
    return baseline.load_baseline(inst, key) if key else None


def schema_transition(ctx, inst):
    """Schema detail uses the same per-chip event and acknowledgement as the card."""
    body = ctx.get("env_upgrade_body")
    if not body:
        return None
    previous = reference_for(ctx, inst)
    to_label = baseline.env_label(body.get("versions"))
    if previous is None:
        entry = next((entry for entry in baseline.list_baselines(inst)
                      if entry["key"] == body.get("key")), {})
        return {"first": True, "changed": False, "to_label": to_label,
                "since": entry.get("first_seen") or body.get("recorded_at") or ""}
    if previous.get("schema_format") != body.get("schema_format"):
        return {"first": False, "changed": False, "unavailable": env_upgrade.OLDER_REFERENCE}
    cache_key = (previous.get("key"), body.get("key"))
    cached = ctx.get("env_schema_detail_cache")
    if cached and cached[0] == cache_key:
        diff = cached[1]
    else:
        diff = baseline.diff_manifests(previous, body)
        if diff["truncated"]:
            full = baseline.diff_manifests(previous, body, cap=10**9, per_class_cap=10**9)
            diff["omitted"] = full["total"] - diff["total"]
        ctx["env_schema_detail_cache"] = (cache_key, diff)
    return {"first": False, "changed": bool(diff["rows"]), "diff": diff,
            "from_key": previous.get("key"), "to_key": body.get("key"),
            "from_label": baseline.env_label(previous.get("versions")), "to_label": to_label,
            "sig": baseline.diff_signature(diff), "previous": previous}


def _legacy_acknowledged(inst, transition, body):
    if not transition or not transition.get("sig"):
        return False
    previous = transition.get("previous") or {}
    keys = [f"{transition.get('from_key')}>{transition.get('to_key')}",
            f"{baseline.stable_env_key(previous.get('versions'))}>{baseline.stable_env_key(body.get('versions'))}"]
    seen = baseline._load_index(inst).get("seen_transitions") or {}
    return next((key + ":" + transition["sig"] for key in keys
                 if (seen.get(key) or {}).get("sig") == transition["sig"]), None)


def env_name(python_path):
    """The env's folder name: python.exe sits in the env root (Windows) or in bin/."""
    if not python_path:
        return ""
    folder = Path(python_path).parent
    if folder.name.lower() in ("bin", "scripts"):
        folder = folder.parent
    return folder.name or str(python_path)


def local_minute(stamp):
    try:
        return datetime.fromisoformat(str(stamp)).astimezone().strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(stamp or "")


def size_text(size):
    size = int(size or 0)
    return f"{size / 1048576:.1f} MB" if size >= 1048576 else f"{max(1, round(size / 1024))} KB"


def checking(ctx):
    """True while a background environment check for this chip is in flight."""
    from quam_state_manager.web import routes
    key = env_seen.chip_key((ctx or {}).get("path") or "")
    with routes._env_upgrade_lock:
        return any(k.startswith(key + "|") for k in routes._env_upgrade_inflight)


def _probe_versions(inst, python_path):
    entry = config_generator._load_probe_cache(inst).get(python_path) or {}
    versions = (entry.get("result") or {}).get("versions") or {}
    return {k: v for k, v in versions.items() if k in env_seen.VERSIONS and v}


def present_references(inst, key):
    rows = []
    for reference in waveform_refs.list_references(inst, key):
        rows.append({**reference, "env_name": env_name(reference.get("python_path")),
                     "local_time": local_minute(reference.get("recorded_at")),
                     "size_text": size_text(reference.get("size")),
                     "waveforms": len(reference.get("traces") or {})})
    return rows


def compare_rows(case):
    """Waveform rows for the table: differing first, identical folded."""
    rows = case.get("rows") or []
    order = {"differs": 0, "not comparable": 1, "numerically equal": 2, "bit-identical": 3}
    shown = sorted((r for r in rows if r.get("label") != "bit-identical"),
                   key=lambda r: order.get(r.get("label"), 1))
    identical = [r for r in rows if r.get("label") == "bit-identical"]
    differ = sum(1 for r in rows if r.get("label") == "differs")
    total = len(rows)
    if total and len(identical) == total:
        summary = f"All {total} waveforms are bit-identical to the reference"
    else:
        summary = f"{differ} of {total} differ"
    return {"shown": shown, "identical": identical, "summary": summary, "total": total}


def model(ctx, inst):
    ctx = ctx or {}
    store = ctx.get("store")
    if store and ctx.get("env_upgrade_body") and ctx.get("env_upgrade_report"):
        with store._lock:
            sequence = store.mutation_seq
            state = copy.deepcopy(store.state) if ctx.get("env_upgrade_report_seq") != sequence else None
        if state is not None:
            previous_report = ctx["env_upgrade_report"]
            checked_env = ctx.get("env_upgrade_env")
            refreshed = env_upgrade.report(state, ctx["env_upgrade_body"], reference_for(ctx, inst),
                catalog=state_env_schema.catalog_for_env(checked_env, inst), event=previous_report.get("event"))
            if previous_report["cases"]["6"].get("reference_state_hash"):
                refreshed["cases"]["6"] = previous_report["cases"]["6"]
                refreshed["non_waveform"] = previous_report.get("non_waveform", [])
            ctx["env_upgrade_report"] = refreshed
            ctx["env_upgrade_report_seq"] = sequence
    report = copy.deepcopy(ctx.get("env_upgrade_report"))
    env = ctx.get("env_upgrade_env") or config_generator.get_selected_env(inst) or "not selected"
    body = ctx.get("env_upgrade_body") or {}
    transition = schema_transition(ctx, inst)
    remembered = project_env.remembered(inst, ctx.get("qualibrate_project"))
    key = env_seen.chip_key(ctx.get("path") or "")
    if report:
        event = report.get("event") or {}
        report["findings_signature"] = baseline._hash({k: v["rows"] for k, v in report["cases"].items()})
        report["banner"] = bool(report.get("banner") and env_seen.live_context(ctx))
        legacy = _legacy_acknowledged(inst, transition, body)
        if legacy and report["banner"]:
            env_seen.import_legacy_ack(inst, key, env, event.get("previous") or {}, body,
                                       report["findings_signature"], legacy)
        is_acknowledged = env_seen.acknowledged(inst, key, env, event.get("previous") or {},
                                                body, report.get("findings_signature"))
        if transition is not None:
            transition["dismissed"] = is_acknowledged
        if report["banner"] and is_acknowledged:
            report["banner"] = False
        accepted = env_seen.accepted_renames(inst, key)
        for row in report["cases"]["3"]["rows"]:
            row["candidates"] = [candidate for candidate in row["candidates"] if not any(
                old["class"] == row["defining_class"] and old["old"] == row["field"] and
                old["new"] == candidate["field"] and old["python_path"] == baseline.normalize_env_path(env)
                for old in accepted)]
    previous_versions = (((report or {}).get("event") or {}).get("previous") or {}).get("versions")
    known_previous = any(Path(path).is_file() and all(
        (entry.get("result", {}).get("versions") or {}).get(name) == previous_versions.get(name)
        for name in env_seen.VERSIONS) for path, entry in config_generator._load_probe_cache(inst).items()
        ) if previous_versions else True
    from quam_state_manager.core import type_verdicts
    from quam_state_manager.web.routes import _env_change_rows
    raw_body = {**body, "classes": {path: {**entry, "fields": env_upgrade._fields(entry)}
                                   for path, entry in body.get("classes", {}).items()}}
    resolved = type_verdicts.resolve_for_manifest(inst, raw_body)
    selected = ctx.get("env_upgrade_env") or config_generator.get_selected_env(inst)
    versions = body.get("versions") or (_probe_versions(inst, selected) if selected else {})
    event = (report or {}).get("event") or {}
    transition_seen = bool(report and event.get("changed") and not event.get("first"))
    consequence = bool(report and any(report["cases"][n]["rows"] for n in ("2", "3", "4", "5", "6")))
    entry = next((e for e in baseline.list_baselines(inst) if e.get("key") == body.get("key")), {})
    refs = present_references(inst, key) if ctx.get("path") else []
    if report and not transition_seen and report["cases"]["6"].get("message") == env_upgrade.NO_REFERENCE:
        report["cases"]["6"] = {**report["cases"]["6"],
                                "message": "not compared: no environment change"}
    return {"report": report, "env": env, "versions": versions,
            "env_name": env_name(selected), "remembered_name": env_name(remembered), "no_env": not selected,
            "checking": checking(ctx), "transition": transition_seen, "consequence": consequence,
            "since": local_minute(entry.get("first_seen") or body.get("recorded_at"))[:10],
            "latest_reference": refs[0] if refs else None,
            "case_names": env_upgrade.CASE_NAMES,
            "compare": compare_rows((report or {}).get("cases", {}).get("6") or {}),
            "schema_transition": transition, "schema_rows": _env_change_rows(transition, resolved),
            "schema_verdicts": resolved,
            "remembered": remembered if remembered and baseline.normalize_env_path(remembered) !=
                          baseline.normalize_env_path(env) else None,
            "error": ctx.get("env_upgrade_error"), "recording": ctx.get("env_reference_status"),
            "previous_not_installed": not known_previous,
            "editable": env_seen.live_context(ctx), "signature_heading": env_upgrade.SIGNATURE_HEADING,
            "footer": (report or {}).get("footer") or
                "Not checked: run-time behaviour of macros, hardware timing, classes not probed (0)",
            "all_clear": bool(report and all(report["cases"][str(n)]["status"] == "none_found"
                                             for n in range(1, 6)))}


def _plan(ctx, payload, inst):
    selections = payload.get("selections") or []
    if not selections:
        raise ValueError("select and confirm individual rename rows")
    allowed = set()
    for row in (model(ctx, inst)["report"] or {}).get("cases", {}).get("3", {}).get("rows", []):
        allowed.update((row["defining_class"], row["field"], c["field"]) for c in row["candidates"])
    if any((row.get("class"), row.get("old"), row.get("new")) not in allowed for row in selections):
        raise ValueError("rename candidate is no longer present in this report")
    with ctx["store"]._lock:
        return env_rename.plan(ctx["store"].state, ctx["store"].wiring,
            ctx["env_upgrade_body"], selections, reference_for(ctx, inst))


def dispatch(action):
    from quam_state_manager.web import routes

    ctx = routes._active_ctx()
    if not ctx or not ctx.get("store"):
        return jsonify(error="No state loaded"), 400
    inst = current_app.instance_path
    view = model(ctx, inst)
    key = env_seen.chip_key(ctx["path"])
    env = view["env"]
    if action == "card":
        return render_template("_env_upgrade_card.html", upgrade=view)
    if action == "text":
        if view["report"]:
            return env_upgrade.text_report(view["report"], env, view["versions"]), 200, {
                "Content-Type": "text/plain; charset=utf-8"}
        lines = [f"Checked in: {env} ({json.dumps(view['versions'], sort_keys=True)})"]
        if view["report"]:
            lines += env_upgrade.verdict_lines(view["report"])
        else:
            lines += [view["error"] or "could not check: report not recorded"]
        return "\n".join([*lines, view["footer"]]), 200, {"Content-Type": "text/plain; charset=utf-8"}
    if action == "references":
        try:
            envs = [{"name": e.get("name"), "python": e.get("python")}
                    for e in config_generator.discover_envs() if e.get("python")]
        except Exception:  # noqa: BLE001 -- discovery failure only empties the list
            envs = []
        return render_template("_env_upgrade_references.html", references=present_references(inst, key),
                               envs=envs, selected=view["env"] if not view["no_env"] else "")
    if action == "status":
        return jsonify(ok=True, checking=view["checking"])
    if request.method != "POST":
        return jsonify(error="use an explicit action"), 405
    payload = request.get_json(silent=True) or request.form
    try:
        if action == "check":
            if not view["editable"]:
                raise ValueError("environment history checks need a writable live state")
            if not (payload.get("python") or not view["no_env"]):
                raise ValueError("No Python environment selected; pick one in Generate Config first")
            if payload.get("python") and not config_generator.known_interpreter(payload["python"], inst):
                raise ValueError("that environment is not one SM discovered")
            routes._maybe_check_env_upgrade(ctx, inst, python_path_override=payload.get("python"))
            return jsonify(ok=True, checking=checking(ctx), message="Checking...")
        if action == "record":
            if not view["editable"]:
                raise ValueError("a reference is recorded for a writable live state only")
            if view["no_env"]:
                raise ValueError("No Python environment selected; pick one in Generate Config first")
            ctx["env_reference_status"] = None
            routes.config_regenerate()
            return render_template("_env_upgrade_card.html", upgrade=model(ctx, inst))
        if action == "acknowledge":
            report = view["report"]
            if not report or not report.get("banner"):
                return jsonify(error="no transition findings to acknowledge"), 400
            event = {**report["event"], "stops_loading": True}
            done = env_seen.acknowledge(inst, key, env, event.get("previous") or {},
                ctx["env_upgrade_body"], report["findings_signature"], context=ctx, event=event)
            return jsonify(ok=done)
        if action in ("compare", "delete"):
            reference_key = payload.get("reference")
            reference = waveform_refs.load(inst, reference_key)
            if reference.get("chip_key") != key:
                raise ValueError("reference belongs to another state")
            if action == "delete":
                (Path(inst) / "waveform_refs" / f"{reference_key}.json").unlink()
                return jsonify(ok=True)
            python = payload.get("python") or env
            if not config_generator.known_interpreter(python, inst, also=[reference.get("python_path")]):
                raise ValueError("choose an environment SM discovered")
            comparison = waveform_refs.compare(inst, reference_key, python,
                                                 name_filter=payload.get("name_filter") or "")
            comparison["current_env"] = env_name(python)
            ctx["env_upgrade_report"]["cases"]["6"] = {"case": 6, **comparison}
            ctx["env_upgrade_report"]["non_waveform"] = comparison.get("non_waveform") or []
            return render_template("_env_upgrade_card.html", upgrade=model(ctx, inst))
        if action in ("rename-test", "rename-apply"):
            if not view["editable"]:
                raise ValueError("rename review needs a writable live working copy")
            planned = _plan(ctx, payload, inst)
            signature = config_generator._env_signature(env)
            if action == "rename-test":
                with tempfile.TemporaryDirectory(prefix="env_rename_test_") as temporary:
                    folder = Path(temporary)
                    (folder / "state.json").write_text(json.dumps(planned["state"]), encoding="utf-8")
                    (folder / "wiring.json").write_text(json.dumps(planned["wiring"]), encoding="utf-8")
                    outcome = config_generator.run_config_preview(env, folder, strict=True, load_only=True)
                if not outcome.get("ok"):
                    return jsonify(ok=False, error=outcome.get("error"), skipped=planned["skipped"]), 400
                token = secrets.token_urlsafe(24)
                ctx["env_rename_test"] = {"plan": planned["signature"], "env_signature": signature,
                                           "token": token}
                return jsonify(ok=True, token=token, moves=planned["moves"], skipped=planned["skipped"])
            tested = ctx.get("env_rename_test") or {}
            if (tested.get("plan") != planned["signature"] or tested.get("env_signature") != signature
                    or not secrets.compare_digest(tested.get("token", ""), payload.get("token") or "")):
                raise ValueError("test load again after any state, selection, or environment change")
            with ctx["store"]._lock:
                planned = _plan(ctx, payload, inst)
                if planned["signature"] != tested.get("plan"):
                    raise ValueError("test load again after a state change")
                entries = env_rename.apply(ctx["modifier"], planned)
            env_seen.remember_renames(inst, key, env, payload["selections"], view["report"]["event"])
            ctx["env_upgrade_report"] = env_upgrade.report(planned["state"], ctx["env_upgrade_body"],
                reference_for(ctx, inst), event=view["report"]["event"])
            ctx.pop("env_rename_test", None)
            return jsonify(ok=True, changes=len(entries), skipped=planned["skipped"],
                           message="Working copy updated; use the existing Apply to write live files")
    except (ValueError, KeyError, OSError, TypeError) as exc:
        return jsonify(ok=False, error=str(exc)), 400
    return jsonify(error="unknown environment report action"), 404
