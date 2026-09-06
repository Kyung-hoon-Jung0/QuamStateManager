"""Standalone Scheduler runner — reports the qualibrate config of its env.

Like ``run_build.py`` and ``run_generate_config.py``, this script runs inside a
*user-selected* conda/venv interpreter that has the QM + qualibrate stack
installed. It is NEVER imported by ``quam_state_manager`` — it may import only
the qualibrate/QM libraries and the Python standard library.

Driven by ``quam_state_manager.core.scheduler``.

Phase 0 implements ``--mode report-config`` only: it reads the *effective*
(project-merged) qualibrate configuration the way a node run would resolve it,
plus the env's editable-install location, and writes the result to
``_result.json``. Later phases add ``scan`` (list nodes/graphs + parameter
schemas, hardware-safe via inspection mode) and ``run`` (execute one prepared
node/graph copy).

Usage::

    python run_experiment.py --mode report-config --out work_dir
"""

import argparse
import json
import sys
import traceback
from pathlib import Path

# Same defensive sys.path insert as the sibling scripts — the script's directory
# is normally sys.path[0], but PYTHONSAFEPATH / -P (3.11+) suppress that.
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
from _script_common import library_versions as _library_versions  # noqa: E402

RESULT_FILENAME = "_result.json"


# ---------------------------------------------------------------------------
# report-config
# ---------------------------------------------------------------------------

def _qualibrate_version() -> str:
    from importlib.metadata import version, PackageNotFoundError
    try:
        return version("qualibrate")
    except PackageNotFoundError:
        return "<not installed>"
    except Exception as exc:  # pragma: no cover - defensive
        return f"<error: {exc}>"


def _file_url_to_path(url: str) -> str:
    """Convert a ``file:///...`` direct_url into a native filesystem path."""
    from urllib.parse import urlparse
    from urllib.request import url2pathname
    return url2pathname(urlparse(url).path)


def _editable_install() -> dict | None:
    """Locate the editable install root of the calibrations package, if any.

    The ExampleVendor project installs ``superconducting_calibrations`` as an
    editable package; its ``direct_url.json`` records the source tree the
    ``.pth`` points at. That tree (not the file being run) is what actually
    resolves ``quam_config`` / ``calibration_utils`` imports — so the Scheduler
    checks it against the chosen calibrations folder to catch a stale-install
    mismatch (the wsl_kri trap).
    """
    from importlib.metadata import distribution
    for name in ("superconducting_calibrations", "superconducting-calibrations"):
        try:
            dist = distribution(name)
        except Exception:
            continue
        try:
            txt = dist.read_text("direct_url.json")
        except Exception:
            txt = None
        if not txt:
            return {"dist": name, "path": None, "editable": None}
        try:
            info = json.loads(txt) or {}
        except (ValueError, TypeError):
            return {"dist": name, "path": None, "editable": None}
        url = info.get("url", "")
        editable = bool((info.get("dir_info") or {}).get("editable"))
        path = None
        if url.startswith("file://"):
            try:
                path = _file_url_to_path(url)
            except Exception:
                path = None
        return {"dist": name, "path": path, "url": url or None, "editable": editable}
    return None


def _effective_config() -> dict:
    """Resolve the *effective* (project-merged) qualibrate config of this env.

    qualibrate deep-merges ``~/.qualibrate/config.toml`` with
    ``projects/<project>/config.toml`` (project wins), so the raw top-level
    file is NOT authoritative. We read it via qualibrate's own resolvers (which
    do the merge + migrations), with a raw-merged-dict fallback so a model/API
    change in a future qualibrate version can't blank the whole report.
    """
    out = {
        "config_file": None,
        "project": None,
        "state_path": None,
        "storage_location": None,
        "calibration_library_folder": None,
        "source": None,
    }

    from qualibrate_config.resolvers import get_qualibrate_config_path
    cfg_path = get_qualibrate_config_path()
    out["config_file"] = str(cfg_path)

    # Primary: the typed model (applies the project merge + migrations).
    try:
        from qualibrate_config.resolvers import get_qualibrate_config
        qs = get_qualibrate_config(cfg_path)
        out["project"] = getattr(qs, "project", None)
        storage = getattr(qs, "storage", None)
        loc = getattr(storage, "location", None) if storage is not None else None
        if loc is not None:
            out["storage_location"] = str(loc)
        callib = getattr(qs, "calibration_library", None)
        folder = getattr(callib, "folder", None) if callib is not None else None
        if folder is not None:
            out["calibration_library_folder"] = str(folder)
        try:
            from qualibrate.core.config.resolvers import get_quam_state_path
            sp = get_quam_state_path(qs)
            if sp is not None:
                out["state_path"] = str(sp)
        except Exception:
            pass
        out["source"] = "model"
    except Exception:
        pass

    # Fallback / cross-check: the raw merged dict (project override applied).
    try:
        from qualibrate_config.file import read_config_file
        raw = read_config_file(cfg_path, solve_references=False) or {}
        q = raw.get("qualibrate", {}) or {}
        if out["project"] is None:
            out["project"] = q.get("project")
        if out["storage_location"] is None:
            loc = (q.get("storage", {}) or {}).get("location")
            out["storage_location"] = str(loc) if loc is not None else None
        if out["calibration_library_folder"] is None:
            folder = (q.get("calibration_library", {}) or {}).get("folder")
            out["calibration_library_folder"] = str(folder) if folder is not None else None
        if out["state_path"] is None:
            sp = (raw.get("quam", {}) or {}).get("state_path")
            out["state_path"] = str(sp) if sp is not None else None
        if out["source"] is None:
            out["source"] = "raw"
    except Exception:
        pass

    return out


def run_report_config() -> dict:
    """The parts of the result envelope the report-config step provides."""
    return {
        "config": _effective_config(),
        "editable_install": _editable_install(),
    }


# ---------------------------------------------------------------------------
# run — execute one prepared node/graph copy
# ---------------------------------------------------------------------------

def run_target(target: str, state_path: str | None, config_file: str | None,
               baseline_out: str | None = None) -> None:
    """Execute a prepared node/graph ``.py`` (already overridden) via runpy.

    Pins the chip + config via the env so the experiment loads/saves the
    intended state regardless of the ambient config. The file runs its actions
    at import (run as ``__main__``); a node's spliced ``custom_param`` applies
    the chosen qubits/params/simulate before the experiment body executes.

    SAFETY: dict-style graph files call ``g.run()`` at module top level (no
    ``__main__`` guard), so a plain import/runpy of such a file fires the graph
    on hardware immediately. This MUST only be called by the dry-run-gated
    Scheduler run path on a prepared copy — never import/runpy a calibrations
    ``.py`` in the State Manager process without qualibrate inspection mode.
    """
    import os
    import runpy

    if not target or not Path(target).exists():
        raise FileNotFoundError(f"target not found: {target!r}")
    if state_path:
        os.environ["QUAM_STATE_PATH"] = str(state_path)
    if config_file:
        os.environ["QUALIBRATE_CONFIG_FILE"] = str(config_file)
    # docs/173 S9 (found on the real KRISS env): a node's plot action calls
    # plt.show(), and the customer env's default matplotlib backend is the
    # INTERACTIVE tkagg (tkinter present) -- so a headless Scheduler subprocess
    # blocks forever on a GUI window that never opens. Force a non-interactive
    # backend before the node imports matplotlib; an operator override wins.
    os.environ.setdefault("MPLBACKEND", "Agg")
    # docs/174 (amended): capture the state's top-level keys BEFORE the node runs,
    # while the scratch is still the byte-for-byte make_scratch copy of the chip.
    # ``machine.save()`` later materializes EVERY field the quam class declares,
    # adding class-default ROOT keys the customer's state.json never had
    # (flux_crosstalk_max_v / require_flux_crosstalk_dc / twpa_ext on the KRISS
    # class). Knowing the original roots lets _persist strip exactly those, so the
    # scratch SM reads back is the chip + the node's real writes and nothing else.
    original_roots = _state_root_keys(state_path) if state_path else None
    ns = runpy.run_path(str(target), run_name="__main__")
    if state_path:
        _persist_node_state(ns, str(state_path), original_roots)


def _state_root_keys(state_path: str) -> set | None:
    """The top-level keys of ``state.json`` at *state_path* right now, or None if
    it can't be read. Captured BEFORE the node's ``machine.save()`` so
    _strip_phantom_roots can tell a class-default root the serializer added from a
    key the chip genuinely had (docs/174 amended)."""
    try:
        raw = json.loads((Path(state_path) / "state.json").read_text(encoding="utf-8"))
        return set(raw.keys()) if isinstance(raw, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _strip_phantom_roots(state_path: str, original_roots: set | None, updates: dict) -> None:
    """docs/174 (amended -- the real fix, found on the real KRISS arbel chain):
    ``machine.save()`` writes back EVERY field the quam class declares, so it adds
    top-level ROOT keys the customer's state.json never had (the KRISS class's
    ``flux_crosstalk_max_v`` / ``require_flux_crosstalk_dc`` / ``twpa_ext``). The
    first docs/174 fix only cancelled these in SM's DIFF; but SM's post-run adopt
    copies the scratch's FULL state into the working copy (byte-identical, then to
    live), so the phantom roots still reached live and diverged it -> ``stale_live``
    on the very next node.

    Fix at the source: after the node's save, delete any top-level key that (a) the
    chip did not originally have AND (b) the node's ``state_updates`` did not write.
    The scratch SM reads back is then the chip + the node's real writes and nothing
    else, so nothing downstream (diff, apply, adopt, auto-sync) ever sees a phantom.
    A node that genuinely writes a new root key keeps it (its ref is in
    ``state_updates``). Best-effort and never fatal."""
    if original_roots is None:
        return
    try:
        sp = Path(state_path) / "state.json"
        state = json.loads(sp.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            return
        touched = set()
        for key, rec in (updates or {}).items():
            ref = (rec or {}).get("key") or key
            seg = str(ref).lstrip("#/").split("/")[0]
            if seg:
                touched.add(seg)
        removed = [k for k in list(state.keys())
                   if k not in original_roots and k not in touched]
        if not removed:
            return
        for k in removed:
            del state[k]
        sp.write_text(json.dumps(state, indent=4), encoding="utf-8")
        sys.stderr.write(f"[run_experiment] stripped {len(removed)} class-default root key(s): "
                         f"{', '.join(removed)}\n")
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[run_experiment] phantom-root strip skipped: {type(exc).__name__}: {exc}\n")


def _persist_node_state(ns: dict, state_path: str, original_roots: set | None = None) -> None:
    """docs/173 S9 (found on the real KRISS arbel cloud): a qualibrate node NEVER
    rewrites the state.json at QUAM_STATE_PATH. In a non-interactive run its
    ``record_state_updates()`` either applies the calibration to the in-memory
    machine and records nothing (interactive_only=True, the customer default),
    or reverts the machine and records the diff in ``node.state_updates``
    (interactive_only=False). ``node.save()`` only writes the run's storage
    snapshot. So the Scheduler's scratch state.json stays byte-identical and
    SM's leaf-diff sees NO writes.

    Here, on SM's own subprocess boundary, we make the node's PROPOSED state the
    thing on disk at QUAM_STATE_PATH: apply any recorded ``state_updates`` back
    onto the machine (the reverted case), then ``machine.save()`` to the scratch.
    The live chip is never touched -- state_path is always the Scheduler's
    per-run scratch copy. Best-effort and never fatal: a run that produced no
    node object, or a machine without ``save``, just leaves the scratch as-is
    (the pre-S9 behaviour) rather than failing the whole run.
    """
    try:
        node = ns.get("node") if isinstance(ns, dict) else None
        machine = getattr(node, "machine", None)
        if node is None or machine is None or not hasattr(machine, "save"):
            return
        updates = {}
        try:
            updates = dict(getattr(node, "state_updates", {}) or {})
        except Exception:  # noqa: BLE001
            updates = {}
        for key, rec in updates.items():
            # rec = {"key": "#/qubits/qA1/resonator/time_of_flight", "attr": ..., "old": .., "new": ..}
            ref = (rec or {}).get("key") or key
            if "new" not in (rec or {}):
                continue
            try:
                from qualibrate.core.utils.node.record_state_update import update_machine_attribute
                update_machine_attribute(machine, ref, rec["new"])   # writes rec["new"] at ref
            except Exception:  # noqa: BLE001
                _set_by_ref(machine, ref, rec["new"])
        machine.save()
        # docs/174 amended: remove the class-default root keys machine.save() just
        # materialized, so the scratch (and everything SM adopts from it) matches
        # the chip's own schema and never diverges live on the next node.
        _strip_phantom_roots(state_path, original_roots, updates)
    except Exception as exc:  # noqa: BLE001
        # never let the capture step fail a run that already measured on hardware
        sys.stderr.write(f"[run_experiment] state capture skipped: {type(exc).__name__}: {exc}\n")


def _set_by_ref(machine, ref: str, value) -> None:
    """Fallback setter for a ``#/a/b/c`` quam reference when qualibrate's own
    helper is unavailable: walk attributes/items and set the leaf."""
    parts = [p for p in str(ref).lstrip("#/").split("/") if p]
    obj = machine
    for p in parts[:-1]:
        if isinstance(obj, dict):
            obj = obj[p]
        elif isinstance(obj, (list, tuple)):
            obj = obj[int(p)]
        else:
            obj = getattr(obj, p)
    leaf = parts[-1]
    if isinstance(obj, dict):
        obj[leaf] = value
    elif isinstance(obj, list):
        obj[int(leaf)] = value
    else:
        setattr(obj, leaf, value)


# ---------------------------------------------------------------------------
# scan — full parameter schemas via qualibrate inspection (hardware-safe)
# ---------------------------------------------------------------------------

def run_scan(folder: str) -> dict:
    """Discover nodes/graphs in *folder* + their full parameter JSON-schemas.

    Uses qualibrate's inspection-mode library scan: each file is imported, but
    the QualibrationNode/Graph constructor raises StopInspection BEFORE the
    experiment body / Quam.load() runs — so no hardware is touched even for
    dict-style graphs that call g.run() at module top level. Each runnable's
    ``.serialize()`` yields ``{description, parameters: <resolved json-schema>}``.
    """
    from pathlib import Path as _Path

    from qualibrate import QualibrationLibrary

    lib = QualibrationLibrary(library_folder=_Path(folder), set_active=False)
    items: list[dict] = []

    def _collect(collection, kind: str) -> None:
        try:
            names = list(collection.keys())
        except Exception:
            try:
                names = list(collection)
            except Exception:
                names = []
        for name in names:
            entry = {"name": name, "kind": kind, "description": "",
                     "parameters": {}, "targets_name": None, "error": None}
            try:
                runnable = (collection.get_nocopy(name)
                            if hasattr(collection, "get_nocopy") else collection[name])
                # The library captures runnables as PlaceholderNode/Graph whose own
                # serialize() has parameters=null; the full field schema (type,
                # default, description, enum, is_targets) lives on parameters_class.
                try:
                    entry["description"] = (runnable.serialize() or {}).get("description") or ""
                except Exception:
                    pass
                pc = getattr(runnable, "parameters_class", None)
                if pc is not None:
                    entry["parameters"] = dict(pc.serialize())
                    entry["targets_name"] = getattr(pc, "targets_name", None)
            except Exception as exc:  # noqa: BLE001 - one bad file shouldn't sink the scan
                entry["error"] = f"{type(exc).__name__}: {exc}"
            items.append(entry)

    _collect(lib.nodes, "node")
    _collect(lib.graphs, "graph")
    return {"items": items}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Scheduler experiment runner")
    parser.add_argument(
        "--mode", required=True,
        choices=("report-config", "scan", "run"),
        help="report-config: dump the env's effective qualibrate config; "
             "scan: list nodes/graphs + parameter schemas (inspection); "
             "run: execute a prepared node/graph copy",
    )
    parser.add_argument(
        "--out", required=True,
        help="work directory that _result.json is written to",
    )
    parser.add_argument("--folder", help="calibrations folder to scan (scan mode)")
    parser.add_argument("--target", help="prepared node/graph .py to run (run mode)")
    parser.add_argument("--state-path", help="QUAM_STATE_PATH for the run (run mode)")
    parser.add_argument("--baseline-out", help="dir to write the serializer-normalized "
                        "pre-node baseline into, for SM's leaf diff (run mode; docs/174)")
    parser.add_argument("--config-file", help="QUALIBRATE_CONFIG_FILE for the run (run mode)")
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "status": "error",
        "mode": args.mode,
        "versions": {},
        "error": None,
        "traceback": None,
        "config": None,
        "editable_install": None,
    }

    if args.mode == "run":
        result["target"] = args.target

    try:
        versions = _library_versions()
        versions["qualibrate"] = _qualibrate_version()
        result["versions"] = versions
        if args.mode == "report-config":
            result.update(run_report_config())
        elif args.mode == "scan":
            if not args.folder:
                raise ValueError("--folder is required for scan mode")
            result.update(run_scan(args.folder))
        elif args.mode == "run":
            run_target(args.target, args.state_path, args.config_file, args.baseline_out)
        result["status"] = "ok"
    except SystemExit as exc:
        # A node that calls sys.exit() is not a crash; exit code 0/None = success.
        if exc.code in (0, None):
            result["status"] = "ok"
        else:
            result["status"] = "error"
            result["error"] = f"SystemExit: {exc.code}"
    except Exception as exc:  # noqa: BLE001 - top-level guard
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
    finally:
        # Always write _result.json (even on SystemExit / KeyboardInterrupt) so
        # the parent classifies the run instead of seeing 'no _result.json'.
        result_path = out_dir / RESULT_FILENAME
        try:
            with open(result_path, "w", encoding="utf-8") as fh:
                json.dump(result, fh, indent=2)
        except OSError:
            pass

    print(json.dumps({"status": result["status"],
                      "result_file": str(out_dir / RESULT_FILENAME)}))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
