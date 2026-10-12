"""Explicit current, run-time heuristic, and saved-figure replay ladder."""
import json
from datetime import datetime
from pathlib import Path

from quam_state_manager.core.autofit import sourceroot


def reasons(result):
    out = [str(e.get("trace", "")).strip().splitlines()[-1]
           for e in result.get("errors", []) if str(e.get("trace", "")).strip()]
    missing = result.get("archive_match", {}).get("missing", [])
    if missing:
        out.append("Saved figure keys missing: " + ", ".join(missing))
    for figure in result.get("figures", []):
        if figure.get("marker_defaults"):
            out.append(f"Defaulted fields feed markers in {figure.get('key')}: "
                       + ", ".join(figure["marker_defaults"]))
    return out


_INFRA = ("env", "spawn", "subprocess", "timeout")


def _start(folder):
    try:
        node = json.loads((folder / "node.json").read_text(encoding="utf-8"))
        value = node.get("created_at") or node.get("metadata", {}).get("run_start")
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _label(result, rung, sha, text, attempts, current):
    # the cache watches the CURRENT code for every rung: fixing it re-runs the ladder
    result["cache_watch"] = {"files": [m["path"] for m in current.get("module_files", [])],
                             "trees": current.get("module_trees", []),
                             "code_fingerprint": current.get("code_fingerprint")}
    result["revision"] = {"rung": rung, "sha": sha, "code_fingerprint": result.get("code_fingerprint"),
                          "heuristic": rung == "run_time"}
    result["status_chip"] = text
    result["revision_ladder"] = attempts
    for figure in result.get("figures", []):
        figure["revision"] = result["revision"]
        figure["status_chip"] = text
    return result


def _saved(folder):
    try:
        data = json.loads((folder / "data.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    saved = {}
    for key, value in (data.get("figures") or {}).items():
        if isinstance(value, str):
            path = (folder / value).resolve()
            if path.is_relative_to(folder.resolve()) and path.is_file() and path.suffix.lower() == ".png":
                saved[str(key)] = {"key": str(key), "title": str(key), "static": True}
    for path in sorted(folder.glob("*.png")):
        key = path.stem.removeprefix("figures.").removeprefix("figure_")
        saved.setdefault(key, {"key": key, "title": key, "static": True})
    return list(saved.values())


def reproduce(folder, instance, source_root, invoke):
    current = invoke(source_root)
    failures = reasons(current)
    attempts = [{"rung": "current", "status": "failed" if failures else "ok", "reasons": failures,
                 "code_fingerprint": current.get("code_fingerprint")}]
    if not failures:
        return _label(current, "current", None, "Reproduced with the current analysis code", attempts, current)
    root = source_root or current.get("source_root")
    start = _start(folder)
    sha = None
    skip = "Analysis source tree is unknown" if not root else "Analysis source tree has no git history"
    infra = next((e.get("stage") for e in current.get("errors", []) if e.get("stage") in _INFRA), None)
    if infra:
        # the lab env itself did not run: an older revision would not either
        root, skip = None, f"The lab environment did not run ({infra})"
    if root and sourceroot.is_git_tree(root):
        if start:
            sha = sourceroot._git(root, "rev-list", "-1", f"--before={start}", "HEAD")
            skip = "No revision before the run's time"
            if sha and sha == sourceroot.resolve_rev(root) and not sourceroot.is_dirty(root):
                # the run-time revision IS the current code, which just failed
                sha, skip = None, "The run-time revision is the current code"
        else:
            skip = "Run start time is unavailable"
    last = current
    if sha:
        pinned = sourceroot.materialize(root, sha, sourceroot.cache_dir_for(instance))
        if pinned:
            last = invoke(pinned)
            historic_failures = reasons(last)
            attempts.append({"rung": "run_time", "sha": sha, "heuristic": True,
                             "status": "failed" if historic_failures else "ok", "reasons": historic_failures,
                             "code_fingerprint": last.get("code_fingerprint")})
            if not historic_failures:
                last["revision_lookup"] = {"root": root, "start": start, "sha": sha}
                return _label(last, "run_time", sha,
                              f"Reproduced with analysis code {sha[:7]} chosen by the run's time (heuristic)",
                              attempts, current)
        else:
            attempts.append({"rung": "run_time", "sha": sha, "status": "failed",
                             "reasons": ["Could not materialize the run-time revision"]})
    else:
        attempts.append({"rung": "run_time", "status": "skipped", "reasons": [skip]})
    failures = reasons(last) or failures
    result = dict(last, figures=[], saved_figures=_saved(folder), ndview=True)
    attempts.append({"rung": "saved", "status": "shown", "reasons": failures})
    return _label(result, "saved", sha,
                  "Could not reproduce this run's figures with the current or run-time code; "
                  f"showing the saved figure and raw data. {failures[-1]}", attempts, current)
