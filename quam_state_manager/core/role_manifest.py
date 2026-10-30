"""Per-env ROLE manifest of quam classes (lab additions, phase 1 -- data only).

Spawns ``generator/probe_roles.py`` in the selected interpreter and caches
the result per env under ``<instance>/role_manifest_cache.json``, keyed by
the interpreter path and validated by the env's stat signature (the same
pattern as ``state_env_schema.catalog_for_env``).

* ``roles_for_env(python, instance, ...)`` -- request-path read, NEVER spawns;
  None when cold or stale.
* ``probe_roles(python, instance, ...)`` -- MAY spawn; background use only.
* ``readout_capability(manifest)`` / ``root_capabilities(manifest)`` -- pure
  readers over a manifest.
"""
from __future__ import annotations

import json
import logging
import re
import tempfile
from pathlib import Path
from typing import Any

from quam_state_manager.core import safe_io
from quam_state_manager.core.config_generator import (
    _blank_outcome,
    _cleanup_work_dir,
    _env_signature,
    _run_script_outcome,
    _script_path,
)
from quam_state_manager.core.state_env_schema import _cache_lock

logger = logging.getLogger(__name__)

ROLES_SCRIPT = _script_path("probe_roles.py")
ROLE_FORMAT = 2   # 2: per-class and per-field docstrings
_CACHE_FILENAME = "role_manifest_cache.json"
_MAX_CACHED_ENVS = 5
_MEASURE_MACRO = "quam_builder.architecture.superconducting.custom_gates.single_qubit_gates.MeasureMacro"


def _cache_path(instance_path) -> Path:
    return Path(instance_path) / _CACHE_FILENAME


def _load(instance_path) -> dict:
    try:
        data = json.loads(_cache_path(instance_path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _request_key(state_classes, modules) -> dict:
    return {"classes": sorted({c for c in (state_classes or []) if isinstance(c, str)}),
            "modules": sorted({m for m in (modules or []) if isinstance(m, str)})}


def roles_for_env(python_path: str | None, instance_path=None, *,
                  state_classes=None, modules=None) -> dict | None:
    """Cached manifest, or None when cold / stale / asked for classes or
    modules the cached probe never saw. Never spawns."""
    if not python_path or instance_path is None:
        return None
    with _cache_lock:
        entry = _load(instance_path).get(python_path)
    if not isinstance(entry, dict) or entry.get("format") != ROLE_FORMAT:
        return None
    sig = _env_signature(python_path)
    if sig is None or entry.get("signature") != sig:
        return None
    seen = entry.get("requested") or {}
    want = _request_key(state_classes, modules)
    if not set(want["classes"]) <= set(seen.get("classes") or []):
        return None
    if not set(want["modules"]) <= set(seen.get("modules") or []):
        return None
    man = entry.get("manifest")
    return man if isinstance(man, dict) and man.get("classes") else None


def probe_roles(python_path: str, instance_path=None, *, state_path=None,
                state_classes=None, modules=None, force: bool = False,
                timeout: int = 240, import_timeout_s: float = 30) -> dict:
    """Run (or cache-serve) the role probe. Never raises.
    Returns ``{"ok", "cached", "error", "manifest"}``."""
    result: dict[str, Any] = {"ok": False, "cached": False, "error": None, "manifest": {}}
    if not python_path or not Path(python_path).is_file():
        result["error"] = "selected interpreter no longer exists"
        return result
    classes = list(state_classes or [])
    if state_path:
        try:
            from quam_state_manager.generator.probe_roles import classes_in_state
            classes += classes_in_state(json.loads(Path(state_path).read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            result["error"] = f"could not read state: {exc}"
            return result
    if instance_path is not None and not force:
        cached = roles_for_env(python_path, instance_path, state_classes=classes, modules=modules)
        if cached:
            result.update(ok=True, cached=True, manifest=cached)
            return result
    if not ROLES_SCRIPT.exists():
        result["error"] = f"role probe script not found: {ROLES_SCRIPT}"
        return result
    key = _request_key(classes, modules)
    outcome = _blank_outcome()
    work_dir = Path(tempfile.mkdtemp(prefix="quamroles_work_"))
    try:
        (work_dir / "_spec.json").write_text(json.dumps(
            {"classes": key["classes"], "modules": key["modules"],
             "timeout_s": import_timeout_s}), encoding="utf-8")
        _run_script_outcome(
            [python_path, str(ROLES_SCRIPT), "--spec", str(work_dir / "_spec.json"),
             "--out", str(work_dir / "_result.json")],
            work_dir, timeout, outcome,
            no_result_label="role probe", error_fallback="role probe reported an error")
    finally:
        _cleanup_work_dir(work_dir)
    parsed = outcome.get("result") or {}
    if not outcome.get("ok") or not parsed.get("classes"):
        result["error"] = outcome.get("error") or "the role probe returned no classes"
        return result
    result.update(ok=True, manifest=parsed)
    failed = {m: s for m, s in (parsed.get("modules") or {}).items()
              if s != "ok" and m in key["modules"]}
    if failed:          # a user module that failed: served, never cached as the truth
        result["error"] = "partial: " + "; ".join(f"{m}: {s}" for m, s in failed.items())
        return result
    if instance_path is not None:
        with _cache_lock:
            cache = _load(instance_path)
            cache.pop(python_path, None)
            cache[python_path] = {"format": ROLE_FORMAT, "signature": _env_signature(python_path),
                                  "requested": key, "manifest": parsed}
            while len(cache) > _MAX_CACHED_ENVS:
                cache.pop(next(iter(cache)))
            try:
                safe_io.atomic_write_json(_cache_path(instance_path), cache)
            except OSError:
                logger.warning("Could not persist the role manifest", exc_info=True)
    return result


def _version_tuple(v) -> tuple:
    if not isinstance(v, str):
        return ()
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def readout_capability(manifest: dict) -> dict:
    """``{"mode": "pulse_and_macro" | "pulse_only", "reason"}``.

    quam_builder >= 0.6 ships a transmon MeasureMacro; older versions measure
    through the pulse alone. The class must also be present in the probe --
    a version string alone is never trusted."""
    versions = (manifest or {}).get("versions") or {}
    qb = _version_tuple(versions.get("quam_builder"))
    cls = ((manifest or {}).get("classes") or {}).get(_MEASURE_MACRO) or {}
    has = cls.get("role") == "macro_qubit"
    if qb and qb >= (0, 6) and has:
        return {"mode": "pulse_and_macro", "reason": "quam_builder "
                f"{versions.get('quam_builder')} provides MeasureMacro"}
    if not qb:
        why = "quam_builder version unknown"
    elif qb < (0, 6):
        why = f"quam_builder {versions.get('quam_builder')} has no transmon MeasureMacro"
    else:
        why = "MeasureMacro was not found in this env"
    return {"mode": "pulse_only", "reason": why}


def root_capabilities(manifest: dict) -> dict:
    """Per root class: whether it DECLARES ``active_twpa_names`` and ``extras``."""
    out = {}
    for path, c in ((manifest or {}).get("classes") or {}).items():
        if not isinstance(c, dict) or c.get("role") != "root":
            continue
        f = c.get("fields") or {}
        out[path] = {"origin": c.get("origin"), "active_twpa_names": "active_twpa_names" in f,
                     "extras": "extras" in f}
    return out
