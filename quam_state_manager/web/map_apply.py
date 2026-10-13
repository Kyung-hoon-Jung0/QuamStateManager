"""Bind map staging to server results and the chip that requested them."""
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path

from flask import current_app

from .request_body import BadBody


def bind(ctx, run, result, figure=None):
    from . import routes
    from quam_state_manager.core.interactive_plots.replot import _fingerprint
    grants = current_app.extensions.setdefault("map_apply_grants", OrderedDict())
    rows = []
    store = ctx.get("store") if ctx else None
    for row in result["updates"] if store else []:
        try:
            leaf = routes._resolve_edit_path(store, row["dot_path"])
            rows.append((deepcopy(row), leaf, deepcopy(store.get_value(leaf))))
        except (KeyError, ValueError, TypeError, IndexError):
            continue
    key = (result["updates"][0]["provenance"]["run_uid"], result["request_hash"]) if result["updates"] else None
    if key:
        folder = Path(run.folder_path)
        from quam_state_manager.generator.iplot_sources import snapshot
        files = [m["path"] for m in (figure or {}).get("module_files", [])]
        trees = (figure or {}).get("module_trees", [])
        grants[key] = {"ctx": ctx, "rows": rows, "folder": folder,
                       "inputs": _fingerprint(folder), "files": files, "trees": trees,
                       "code_stats": snapshot(files, trees)}
        grants.move_to_end(key)
        while len(grants) > 64:
            grants.popitem(last=False)


def validate(ctx, path, value, origin, *, create=False, delete=False):
    """Reject changed numbers, targets, chip contexts and expired evidence."""
    if not origin or origin.get("src") != "map_fit":
        return
    if (ctx.get("origin") or "live") != "live":
        raise BadBody("Map staging requires an editable live chip.")
    from . import map_analysis, routes
    from quam_state_manager.core.interactive_plots.replot import _fingerprint
    key = (origin.get("run_uid"), origin.get("request_hash"))
    grant = current_app.extensions.get("map_apply_grants", {}).get(key)
    if not grant or grant["ctx"] is not ctx or not map_analysis.replay(*key):
        raise BadBody("Map analysis expired or belongs to another chip; analyze again.")
    if _fingerprint(grant["folder"]) != grant["inputs"]:
        raise BadBody("Run inputs changed; analyze again.")
    from quam_state_manager.generator.iplot_sources import snapshot
    if snapshot(grant["files"], grant["trees"]) != grant["code_stats"]:
        raise BadBody("Analysis code changed; analyze again.")
    if create or delete or isinstance(value, bool):
        raise BadBody("Map results can only assign their verified numeric targets.")
    store = ctx["store"]
    for row, leaf, previous in grant["rows"]:
        if path not in (row["dot_path"], leaf) or origin != row["provenance"]:
            continue
        try:
            numeric = float(value)
            unchanged = (routes._resolve_edit_path(store, row["dot_path"]) == leaf
                         and store.get_value(leaf) == previous)
        except (ValueError, TypeError, KeyError, IndexError):
            break
        if numeric == row["value"] and unchanged:
            return
    raise BadBody("Map value, target or provenance changed; analyze again.")


def checked(modifier, path, parsed, origin):
    if origin and origin.get("src") == "map_fit":
        value = modifier._checked_value(path, modifier.store.get_value(path), parsed,
                                        coerce=True, enforce=True)
        if type(value) not in (int, float) or value != float(parsed):
            raise ValueError("Type policy would change the map result; convert the target type first.")
