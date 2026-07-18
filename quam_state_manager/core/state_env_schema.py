"""SM-side wrapper for the in-env class-schema probe (``probe_state_schema.py``).

Harvests the loaded chip's ``__class__`` inventory, runs the schema-dump
subprocess in the user's SELECTED interpreter, and caches the resulting
manifest **version-keyed** (incl. ``quam_builder_commit`` — same-version
different-SHA reinstalls miss correctly) under
``<instance>/state_schema_cache.json``.

Two access modes, one hard rule:

* ``manifest_for_store(..., cached_only=True)`` — request-path reads. NEVER
  spawns a subprocess; freshness is checked with the stat-only
  ``_env_signature`` (interpreter + site-packages mtime — flips on any pip
  install). Cold/stale cache → ``None`` (callers render "not probed yet").
* ``probe_state_schema(...)`` — the real probe, for background warm threads
  and the explicit "Probe environment" button only. Cache hit = env versions
  match AND the requested class set ⊆ the cached set (superset hit — chips
  sharing classes never re-probe); miss probes the UNION of cached+requested
  so the per-env entry grows monotonically (bounded by the real class-path
  vocabulary, ~40 entries).

The manifest document shape is owned by ``generator/probe_state_schema.py``
(the ONE shared contract — see its module doc); this module adds only the
SM-side derivations ``by_leaf`` (class-leaf → [canonical paths], for
class-move portability) and ``missing_classes``.
"""

from __future__ import annotations

import logging
import tempfile
import threading
from pathlib import Path
from typing import Any

from quam_state_manager.core import safe_io
from quam_state_manager.core.config_generator import (
    _blank_outcome,
    _cleanup_work_dir,
    _env_signature,
    _env_versions,
    _run_script_outcome,
    _script_path,
)

logger = logging.getLogger(__name__)

STATE_SCHEMA_SCRIPT = _script_path("probe_state_schema.py")
_SCHEMA_CACHE_FILENAME = "state_schema_cache.json"
_MAX_CACHED_ENVS = 5          # LRU prune bound for per-env cache entries
_CLASS_CAP = 200              # harvest armor (corpus max distinct = 36)

_cache_lock = threading.Lock()   # guards load-modify-write of the cache file


# ---------------------------------------------------------------------------
# class harvest
# ---------------------------------------------------------------------------

def harvest_classes(state: dict, *, cap: int = _CLASS_CAP) -> list[str]:
    """Distinct ``__class__`` string values in *state*, root first, first-seen
    order, capped. wiring.json is never passed here — it is ``__class__``-free
    by design (schema-less; 95% pointers)."""
    out: list[str] = []
    seen: set[str] = set()

    root = state.get("__class__") if isinstance(state, dict) else None
    if isinstance(root, str) and root:
        seen.add(root)
        out.append(root)

    stack: list[Any] = [state]
    while stack and len(out) < cap:
        node = stack.pop()
        if isinstance(node, dict):
            c = node.get("__class__")
            if isinstance(c, str) and c and c not in seen:
                seen.add(c)
                out.append(c)
            stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
        elif isinstance(node, list):
            stack.extend(v for v in node if isinstance(v, (dict, list)))
    return out[:cap]


def package_versions_stamp(state: dict) -> dict | None:
    """The ``__package_versions__`` stamp quam-0.6.0-written states carry
    (``{"quam": "0.6.0", "quam_builder": "0.4.0"}``), or None."""
    stamp = state.get("__package_versions__") if isinstance(state, dict) else None
    return stamp if isinstance(stamp, dict) and stamp else None


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------

def _schema_cache_path(instance_path) -> Path:
    return Path(instance_path) / _SCHEMA_CACHE_FILENAME


def _load_cache(instance_path) -> dict[str, dict]:
    p = _schema_cache_path(instance_path)
    if not p.exists():
        return {}
    try:
        import json
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_cache(instance_path, cache: dict[str, dict]) -> None:
    try:
        safe_io.atomic_write_json(_schema_cache_path(instance_path), cache)
    except OSError:
        logger.warning("Could not persist state-schema cache", exc_info=True)


def _decorate(manifest: dict) -> dict:
    """Add the SM-side derivations: ``by_leaf`` + ``missing_classes``."""
    classes = manifest.get("classes") or {}
    by_leaf: dict[str, list[str]] = {}
    missing: list[str] = []
    for path, entry in classes.items():
        if not isinstance(entry, dict):
            continue
        if not entry.get("importable"):
            missing.append(path)
            continue
        canonical = entry.get("canonical") or path
        leaf = canonical.rsplit(".", 1)[-1]
        homes = by_leaf.setdefault(leaf, [])
        if canonical not in homes:
            homes.append(canonical)
    manifest["by_leaf"] = by_leaf
    manifest["missing_classes"] = missing
    return manifest


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------

def probe_state_schema(python_path: str, class_paths: list[str], instance_path=None, *,
                       force: bool = False, timeout: int = 180) -> dict:
    """Run (or cache-serve) the schema dump for *class_paths* in the env.

    Returns ``{"ok", "cached", "error", "classes", "pulse_roster", "versions",
    "by_leaf", "missing_classes"}``. Never raises. MAY spawn a subprocess —
    call only from background warm threads / the explicit probe button.
    """
    result: dict[str, Any] = {"ok": False, "cached": False, "error": None,
                              "classes": {}, "pulse_roster": {}, "versions": {},
                              "by_leaf": {}, "missing_classes": []}

    if not python_path or not Path(python_path).is_file():
        # The vanished-interpreter failure mode (a conda env deleted underfoot):
        # a structured error the UI turns into a "reselect the environment" prompt.
        result["error"] = ("selected interpreter no longer exists — reselect the "
                           "environment in Generate Config")
        return result

    versions = _env_versions(python_path)
    result["versions"] = versions
    requested = [c for c in dict.fromkeys(class_paths) if isinstance(c, str) and c]

    cached_classes: dict = {}
    if instance_path is not None and not force:
        with _cache_lock:
            entry = _load_cache(instance_path).get(python_path)
        if isinstance(entry, dict) and entry.get("versions") == versions:
            cached_classes = entry.get("classes") or {}
            if set(requested) <= set(cached_classes):
                manifest = _decorate({"classes": cached_classes,
                                      "pulse_roster": entry.get("pulse_roster") or {}})
                result.update(ok=True, cached=True,
                              classes=manifest["classes"],
                              pulse_roster=manifest["pulse_roster"],
                              by_leaf=manifest["by_leaf"],
                              missing_classes=manifest["missing_classes"])
                # keep the stat signature fresh so cached_only reads stay warm
                _touch_signature(instance_path, python_path)
                return result

    if not STATE_SCHEMA_SCRIPT.exists():
        result["error"] = f"schema probe script not found: {STATE_SCHEMA_SCRIPT}"
        return result

    # miss → probe the UNION so the per-env entry grows monotonically
    union = list(dict.fromkeys([*cached_classes.keys(), *requested]))[:_CLASS_CAP]

    import json
    outcome = _blank_outcome()
    work_dir = Path(tempfile.mkdtemp(prefix="quamschema_work_"))
    try:
        (work_dir / "_classes.json").write_text(
            json.dumps({"classes": union, "pulse_roster": True}), encoding="utf-8")
        _run_script_outcome(
            [python_path, str(STATE_SCHEMA_SCRIPT),
             "--classes", str(work_dir / "_classes.json"),
             "--out", str(work_dir / "_result.json")],
            work_dir, timeout, outcome,
            no_result_label="state-schema probe",
            error_fallback="state-schema probe reported an error",
        )
    finally:
        _cleanup_work_dir(work_dir)

    parsed = outcome.get("result") or {}
    if not outcome.get("ok"):
        result["error"] = outcome.get("error") or "state-schema probe failed"
        return result

    classes = parsed.get("classes") or {}
    roster = parsed.get("pulse_roster") or {}
    manifest = _decorate({"classes": classes, "pulse_roster": roster})
    result.update(ok=True,
                  classes=manifest["classes"], pulse_roster=manifest["pulse_roster"],
                  by_leaf=manifest["by_leaf"], missing_classes=manifest["missing_classes"],
                  versions=parsed.get("versions") or versions)

    if instance_path is not None:                       # cache only successes
        with _cache_lock:
            cache = _load_cache(instance_path)
            cache.pop(python_path, None)                # re-insert last = most recent
            cache[python_path] = {
                "versions": versions,
                "signature": _env_signature(python_path),
                "classes": classes,
                "pulse_roster": roster,
            }
            while len(cache) > _MAX_CACHED_ENVS:        # LRU prune (insertion order)
                cache.pop(next(iter(cache)))
            _save_cache(instance_path, cache)
    return result


def _touch_signature(instance_path, python_path: str) -> None:
    """Refresh the stored stat signature after a version-verified cache hit
    (an unrelated pip install moves site-packages mtime without changing the
    QM versions — cached_only reads would otherwise go cold forever)."""
    sig = _env_signature(python_path)
    if sig is None:
        return
    with _cache_lock:
        cache = _load_cache(instance_path)
        entry = cache.get(python_path)
        if isinstance(entry, dict) and entry.get("signature") != sig:
            entry["signature"] = sig
            _save_cache(instance_path, cache)


# ---------------------------------------------------------------------------
# store-facing accessor
# ---------------------------------------------------------------------------

def manifest_for_store(store, python_path: str | None, instance_path=None, *,
                       cached_only: bool = False, force: bool = False) -> dict | None:
    """The manifest for *store*'s chip against *python_path*, or None.

    ``cached_only=True`` is the REQUEST-PATH mode: it never spawns a
    subprocess and never even runs the metadata probe — freshness is the
    stat-only ``_env_signature`` stored at probe time. None means "no env
    selected / cache cold or stale / probe never ran" and callers must
    degrade (type layer abstains, diagnostics card shows 'Probe now').
    """
    if not python_path or instance_path is None:
        return None
    try:
        lock = getattr(store, "_lock", None)
        if lock is not None:
            with lock:
                requested = harvest_classes(store.state)
        else:
            requested = harvest_classes(store.state)
    except Exception:  # noqa: BLE001 — a weird state must not break activation
        logger.warning("class harvest failed", exc_info=True)
        return None

    if cached_only:
        with _cache_lock:
            entry = _load_cache(instance_path).get(python_path)
        if not isinstance(entry, dict):
            return None
        sig = _env_signature(python_path)
        if sig is None or entry.get("signature") != sig:
            return None                                  # pip install / env gone
        classes = entry.get("classes") or {}
        if not set(requested) <= set(classes):
            return None                                  # new chip classes unprobed
        manifest = _decorate({"classes": classes,
                              "pulse_roster": entry.get("pulse_roster") or {}})
        manifest["versions"] = entry.get("versions") or {}
        return manifest

    res = probe_state_schema(python_path, requested, instance_path,
                             force=force)
    if not res.get("ok"):
        return None
    return {"classes": res["classes"], "pulse_roster": res["pulse_roster"],
            "by_leaf": res["by_leaf"], "missing_classes": res["missing_classes"],
            "versions": res["versions"]}
