"""Only proven ledger writes can name a later calibration in an Apply popup."""
import os
from pathlib import Path
import sqlite3
from bisect import bisect_right


def settled_rewrites(directory):
    """Current saved bytes predate the next run in their own root, if any."""
    from quam_state_manager.core.hub_store import REWRITTEN
    canonical = lambda p: os.path.normcase(os.path.abspath(p))
    with sqlite3.connect((Path(directory) / "ledger.sqlite").as_uri() + "?mode=ro", uri=True) as conn:
        events = conn.execute("SELECT root_id, run_start_us FROM events WHERE kind='run'").fetchall()
        starts = {}
        for root, start in events:
            if isinstance(start, int):
                starts.setdefault(root, []).append(start)
        for times in starts.values():
            times.sort()
        rows = conn.execute(
            "SELECT r.path, e.rel_path, e.root_id, e.run_start_us, f.sig FROM events e "
            "JOIN roots r USING(root_id) JOIN run_files f ON f.root_id=e.root_id AND f.rel_path=e.rel_path "
            "WHERE e.kind='run' AND e.flags & ?", (REWRITTEN,)).fetchall()
    settled = set()
    for root_path, rel, root, start, sig in rows:
        try:
            parts = sig.split("|")
            # Missing saved files cannot prove that a pair settled.
            newest = max(int(p.split(":")[1]) for p in parts)
            times = starts.get(root, [])
            next_index = bisect_right(times, start)
            if next_index == len(times) or newest <= times[next_index] * 1000:
                settled.add(canonical(Path(root_path) / rel))
        except (AttributeError, IndexError, TypeError, ValueError):
            continue
    return settled


def changed_since(answer, folder, settled=()):
    unknown = {"text": "Changes since this run: unknown (no proven later write found).", "proven": False}
    if answer.get("mode") != "ledger":
        return unknown
    canonical = lambda p: os.path.normcase(os.path.abspath(p)) if p else None
    source = canonical(folder)
    def excluded(point, flags):
        active = set(point.get("flags", []))
        if canonical(point.get("folder")) in settled:
            active.discard("rewritten")
        return bool(active & flags)
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
               and not excluded(p, {"time_assumed", "chip_uncertain", "rewritten", "source_gone"})]
    if not anchors:
        return unknown
    at = max(p["t_us"] for p in anchors)
    later = [p for p in points if p.get("proven") is True and p.get("provenance") == "run_proven"
             and p.get("kind") == "run" and isinstance(p.get("t_us"), int) and p["t_us"] > at
             and p.get("run_id") is not None and not p.get("undone")
             and canonical(p.get("folder")) != source
             and not excluded(p, {"time_assumed", "chip_uncertain", "rewritten", "source_gone", "node_unreadable", "overlaps_sm_write"})]
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
        settled = set()
        if any("rewritten" in point.get("flags", []) for row in answer.get("rows", {}).values()
               for point in row.get("points", [])) or any("rewritten" in r.get("flags", []) for r in answer.get("runs", [])):
            from .routes import _hub_chip_dir
            directory = ctx.get("hub_chip_dir") or _hub_chip_dir(ctx["path"])
            try:
                settled = settled_rewrites(directory) if directory else set()
            except (OSError, ValueError, sqlite3.Error):
                pass
        return {p: changed_since({**answer, "rows": {"value": answer.get("rows", {}).get(p, {})}}, run.folder_path, settled) for p in paths}
    except (OSError, ValueError, TypeError, KeyError):
        return unknown
