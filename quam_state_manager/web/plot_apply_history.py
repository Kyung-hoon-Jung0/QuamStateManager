"""Only proven ledger writes can name a later calibration in an Apply popup."""
import os


def changed_since(answer, folder):
    unknown = {"text": "Changes since this run: unknown (no proven later write found).", "proven": False}
    if answer.get("mode") != "ledger":
        return unknown
    canonical = lambda p: os.path.normcase(os.path.abspath(p)) if p else None
    source = canonical(folder)
    points = (answer.get("rows", {}).get("value") or {}).get("points", [])
    from datetime import datetime
    run_anchors = []
    for run in answer.get("runs", []):
        try:
            # By-run timestamps are rounded to seconds; exclude that entire second.
            at = int(datetime.fromisoformat(run["t"].replace("Z", "+00:00")).timestamp()) * 1_000_000 + 999999
            run_anchors.append({**run, "kind": "run", "t_us": at})
        except (KeyError, TypeError, ValueError):
            continue
    anchors = [p for p in points + run_anchors if canonical(p.get("folder")) == source
               and p.get("kind") == "run" and isinstance(p.get("t_us"), int)
               and not set(p.get("flags", [])) & {"time_assumed", "chip_uncertain", "rewritten", "source_gone"}]
    if not anchors:
        return unknown
    at = max(p["t_us"] for p in anchors)
    later = [p for p in points if p.get("proven") is True and p.get("provenance") == "run_proven"
             and p.get("kind") == "run" and isinstance(p.get("t_us"), int) and p["t_us"] > at
             and p.get("run_id") is not None and not p.get("undone")
             and canonical(p.get("folder")) != source
             and not set(p.get("flags", [])) & {"time_assumed", "chip_uncertain", "rewritten", "source_gone", "node_unreadable", "overlaps_sm_write"}]
    if not later:
        return unknown
    newest = max(later, key=lambda p: p["t_us"])
    try:
        when = datetime.fromisoformat(newest["t"].replace("Z", "+00:00")).astimezone().strftime("%H:%M")
    except (KeyError, TypeError, ValueError):
        return unknown
    return {"text": f"Changed since this run by #{newest['run_id']} at {when} (local time).",
            "proven": True, "run_id": newest["run_id"], "t_us": newest["t_us"]}


def notes(ctx, uid, paths, resolve_run, history):
    unknown = {p: {"text": "Changes since this run: unknown (history unavailable).", "proven": False} for p in paths}
    resolved = resolve_run(uid)
    if not resolved:
        return unknown
    run = resolved[0].runs.get(resolved[1])
    if run is None:
        return unknown
    try:
        answer = history(ctx, {p: p for p in paths}, runs=1000)
        return {p: changed_since({**answer, "rows": {"value": answer.get("rows", {}).get(p, {})}}, run.folder_path) for p in paths}
    except (OSError, ValueError, TypeError, KeyError):
        return unknown
