"""Retained per-environment schema baselines + the manifest-vs-manifest diff.

``state_env_schema`` caches ONE manifest per interpreter and overwrites it when
the env's versions change, so "this field was ``int`` under quam 0.6 and is
``str`` under 0.7" was not computable — the old schema no longer existed
anywhere. This module keeps a **baseline per env identity** (versions incl. the
builder commit), so an upgrade leaves the previous schema on disk and SM can
say what the LIBRARY changed, separately from what the chip's data did.

Three rules shape it:

**Normalize before storing.** :func:`normalize_spec` drops ``raw`` (and the
defaults / bases). ``raw`` is a display string that legitimately churns between
generations for the same effective type, so a diff that saw it would report
hundreds of phantom changes on every upgrade — alarm-fatigue poison. Storing
the normalized form makes the anti-churn property structural rather than a
convention a later caller could forget.

**A moved class is a move, not a death.** Class matching falls back to the
single-home leaf rule the validator already uses, so quam 0.6.0 relocating the
pulse classes reads as ``class_moved`` instead of a wall of
removed+added pairs.

**Abstention is inherited.** A class the probe could not introspect
(``fields: null``) produces no field rows on either side — the same doctrine as
``state_env_validate``: never flag what was never known.

Pure + I/O-only: no Flask, no subprocess, no manifest production. A failure
here must never break a probe (the caller logs and moves on).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quam_state_manager.core import safe_io

logger = logging.getLogger(__name__)

BASELINE_DIRNAME = "state_schema_baselines"
INDEX_FILENAME = "_index.json"

# Bounds. The baselines are a HISTORY (unlike the perf cache next to them), so
# this is deliberately larger than _MAX_CACHED_ENVS — measured ~40-80 KB each.
_MAX_BASELINES = 8
_MAX_BASELINE_BYTES = 4_000_000
_DIFF_CAP = 200                 # total rows in one diff
_PER_CLASS_CAP = 25             # rows from a single class (a rename can't flood)

_VERSION_KEYS = ("quam", "quam_builder", "qualang_tools", "qm",
                 "quam_builder_commit")

_baseline_lock = threading.Lock()


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------

def env_key(versions: dict | None) -> str:
    """Filename-safe identity for an environment's package set.

    Includes the builder commit: an editable install can change schemas without
    changing a version string. Churn is bounded by ``_MAX_BASELINES``, and it
    can never cause a WRONG verdict — :mod:`type_verdicts` gates on the field's
    spec, not on this key.
    """
    v = versions or {}
    quam = str(v.get("quam") or "?")
    qb = str(v.get("quam_builder") or "?")
    digest = hashlib.sha1(
        json.dumps({k: v.get(k) for k in _VERSION_KEYS}, sort_keys=True,
                   default=str).encode("utf-8")).hexdigest()[:10]
    safe = "".join(c if (c.isalnum() or c in "._-") else "-"
                   for c in f"quam-{quam}__qb-{qb}")
    return f"{safe}__{digest}"


def env_label(versions: dict | None) -> str:
    """Human name for an environment — what the popup and the chips show."""
    v = versions or {}
    parts = [f"{name} {v[key]}" for key, name in
             (("quam", "quam"), ("quam_builder", "quam_builder"))
             if v.get(key)]
    return " · ".join(parts) or "unknown environment"


def version_distance(old: dict | None, new: dict | None) -> str:
    """``same`` / ``patch`` / ``minor`` / ``major`` / ``unknown``.

    DISPLAY ONLY — never a gate. Pre-release suffixes ("0.5.0a3") and any
    unparseable string degrade to ``unknown`` rather than to a decision.
    """
    def _parts(s: Any) -> list[int] | None:
        try:
            out = []
            for chunk in str(s).split(".")[:3]:
                digits = ""
                for ch in chunk:
                    if ch.isdigit():
                        digits += ch
                    else:
                        break
                if digits == "":
                    return None
                out.append(int(digits))
            while len(out) < 3:
                out.append(0)
            return out
        except Exception:  # noqa: BLE001
            return None

    o, n = _parts((old or {}).get("quam")), _parts((new or {}).get("quam"))
    if o is None or n is None:
        return "unknown"
    if o == n:
        return "same"
    if o[0] != n[0]:
        return "major"
    if o[1] != n[1]:
        return "minor"
    return "patch"


# ---------------------------------------------------------------------------
# projection — the anti-churn core
# ---------------------------------------------------------------------------

def normalize_spec(ts: Any) -> dict | None:
    """Project a TypeSpec to what a schema COMPARISON may legitimately see.

    ``raw`` is dropped on purpose: it is the annotation's display text, and the
    same effective type renders differently across generations. Everything kept
    here is semantic.
    """
    if not isinstance(ts, dict):
        return None
    out: dict[str, Any] = {"base": ts.get("base") or "any"}
    if ts.get("optional"):
        out["optional"] = True
    if ts.get("enum"):
        out["enum"] = sorted(str(e) for e in ts["enum"])
    if ts.get("class"):
        out["class"] = ts["class"]
    item = normalize_spec(ts.get("item"))
    if item:
        out["item"] = item
    if ts.get("union"):
        members = [normalize_spec(u) for u in ts["union"]]
        members = [m for m in members if m]
        if members:
            out["union"] = sorted(members, key=lambda m: json.dumps(m, sort_keys=True))
    return out


def project_manifest(manifest: dict) -> dict:
    """Manifest → the stored baseline body (normalized, defaults dropped)."""
    classes: dict[str, Any] = {}
    for path, entry in (manifest.get("classes") or {}).items():
        entry = entry or {}
        fields = entry.get("fields")
        if fields is None:
            projected = None                 # abstain — carried as abstain
        else:
            projected = {}
            for name, f in (fields or {}).items():
                f = f or {}
                projected[name] = {
                    "t": normalize_spec(f.get("type")),
                    "o": bool(f.get("optional")),
                    "d": bool(f.get("has_default")),
                }
        classes[path] = {
            "importable": bool(entry.get("importable")),
            "canonical": entry.get("canonical"),
            "fields": projected,
        }
    return {"classes": classes, "versions": manifest.get("versions") or {}}


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------

def baseline_dir(instance_path: Any) -> Path:
    return Path(instance_path) / BASELINE_DIRNAME


def _index_path(instance_path: Any) -> Path:
    return baseline_dir(instance_path) / INDEX_FILENAME


def _load_index(instance_path: Any) -> dict:
    """The index is an ORDERING/label convenience, never the source of truth —
    a missing or corrupt one is rebuilt from the bodies on disk.

    Absence is checked with a stat first: ``safe_io.read_json`` retries a
    missing file with backoff (correct for live files mid-replace, ~900 ms for
    one that was never written), and an instance with no baselines yet is the
    normal case on a render path.
    """
    path = _index_path(instance_path)
    try:
        if path.exists():
            data = safe_io.read_json(path)
            if isinstance(data, dict) and isinstance(data.get("entries"), list):
                return data
    except Exception:  # noqa: BLE001
        logger.debug("baseline index unreadable — rebuilding", exc_info=True)
    return _rebuild_index(instance_path)


def _rebuild_index(instance_path: Any) -> dict:
    entries = []
    d = baseline_dir(instance_path)
    try:
        files = sorted(d.glob("*.json"))
    except OSError:
        files = []
    for f in files:
        if f.name == INDEX_FILENAME:
            continue
        try:
            body = safe_io.read_json(f)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(body, dict):
            continue
        versions = body.get("versions") or {}
        entries.append({
            "key": body.get("key") or f.stem,
            "label": body.get("label") or env_label(versions),
            "versions": versions,
            "python_path": body.get("python_path") or "",
            "first_seen": body.get("recorded_at") or "",
            "last_seen": body.get("recorded_at") or "",
            "classes": len(body.get("classes") or {}),
        })
    return {"version": 1, "current": "", "entries": entries,
            "seen_transitions": {}}


def _write_index(instance_path: Any, index: dict) -> None:
    safe_io.atomic_write_json(_index_path(instance_path), index)


def record_baseline(instance_path: Any, manifest: dict, *,
                    python_path: str = "") -> str | None:
    """Persist this environment's schema as a baseline; return its key.

    Idempotent for an unchanged env (refreshes ``last_seen`` only). Never
    raises — a baseline is a nicety, a probe is not.
    """
    if not manifest or not isinstance(manifest, dict):
        return None
    versions = manifest.get("versions") or {}
    if not any(versions.get(k) for k in ("quam", "quam_builder")):
        return None                       # an env we cannot identify
    key = env_key(versions)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with _baseline_lock:
            d = baseline_dir(instance_path)
            d.mkdir(parents=True, exist_ok=True)
            body = project_manifest(manifest)
            body.update({"version": 1, "key": key, "label": env_label(versions),
                         "python_path": str(python_path or ""),
                         "recorded_at": now})
            blob = json.dumps(body)
            if len(blob) > _MAX_BASELINE_BYTES:
                logger.warning("schema baseline for %s too large (%d bytes) — skipped",
                               key, len(blob))
                return None
            index = _load_index(instance_path)
            entries = [e for e in index.get("entries") or []
                       if isinstance(e, dict)]
            prior = next((e for e in entries if e.get("key") == key), None)
            path = d / f"{key}.json"
            if prior is None or not path.exists():
                safe_io.atomic_write_json(path, body)
            entry = {
                "key": key,
                "label": body["label"],
                "versions": versions,
                "python_path": body["python_path"],
                "first_seen": (prior or {}).get("first_seen") or now,
                "last_seen": now,
                "classes": len(body.get("classes") or {}),
            }
            entries = [e for e in entries if e.get("key") != key] + [entry]
            # Prune oldest-seen first, but never the one we just recorded.
            if len(entries) > _MAX_BASELINES:
                entries.sort(key=lambda e: e.get("last_seen") or "")
                while len(entries) > _MAX_BASELINES:
                    victim = entries.pop(0)
                    if victim.get("key") == key:
                        entries.append(victim)
                        continue
                    try:
                        (d / f"{victim['key']}.json").unlink(missing_ok=True)
                    except OSError:
                        logger.debug("could not prune baseline %s", victim.get("key"))
            index["entries"] = entries
            index["previous"] = (index.get("current") or "") \
                if index.get("current") and index.get("current") != key \
                else index.get("previous") or ""
            index["current"] = key
            index.setdefault("seen_transitions", {})
            _write_index(instance_path, index)
        return key
    except Exception:  # noqa: BLE001 — a baseline write must never fail a probe
        logger.warning("recording the schema baseline failed", exc_info=True)
        return None


def load_baseline(instance_path: Any, key: str) -> dict | None:
    if not key:
        return None
    path = baseline_dir(instance_path) / f"{key}.json"
    try:
        if not path.exists():
            return None            # stat first — see _load_index
        body = safe_io.read_json(path)
        return body if isinstance(body, dict) else None
    except Exception:  # noqa: BLE001
        return None


def list_baselines(instance_path: Any) -> list[dict]:
    """Recorded environments, most recently seen first."""
    index = _load_index(instance_path)
    entries = [e for e in index.get("entries") or [] if isinstance(e, dict)]
    entries.sort(key=lambda e: e.get("last_seen") or "", reverse=True)
    return entries


def previous_baseline(instance_path: Any, current_key: str) -> dict | None:
    """The most recently seen baseline that is NOT the current environment."""
    for entry in list_baselines(instance_path):
        if entry.get("key") and entry["key"] != current_key:
            body = load_baseline(instance_path, entry["key"])
            if body:
                return body
    return None


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------

def _by_leaf(classes: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for path in classes:
        out.setdefault(str(path).rsplit(".", 1)[-1], []).append(path)
    return out


def _match_class(path: str, entry: dict, other: dict,
                 other_by_leaf: dict) -> tuple[str, dict] | None:
    """Find *path*'s counterpart in *other*, tolerating a class MOVE.

    Exact key → canonical match → unique same-leaf home (the rule
    ``state_env_validate._class_entry`` already uses). Ambiguous leaves get no
    fallback: guessing which home moved would be worse than reporting a
    removal.
    """
    if path in other:
        return path, other[path]
    canon = (entry or {}).get("canonical")
    if canon:
        for opath, oentry in other.items():
            if (oentry or {}).get("canonical") == canon:
                return opath, oentry
    homes = other_by_leaf.get(str(path).rsplit(".", 1)[-1]) or []
    if len(homes) == 1:
        return homes[0], other[homes[0]]
    return None


def diff_manifests(old: dict, new: dict, *, cap: int = _DIFF_CAP) -> dict:
    """What the ENVIRONMENT changed between two recorded schemas.

    Both sides may be a raw manifest or an already-projected baseline body —
    projection is idempotent-ish here because a baseline's fields are already
    normalized (``t``/``o``/``d``).
    """
    def _fields(entry: dict) -> dict | None:
        entry = entry or {}
        if "fields" not in entry:
            return None
        f = entry.get("fields")
        if f is None:
            return None
        out = {}
        for name, spec in f.items():
            spec = spec or {}
            if "t" in spec:                      # baseline body
                out[name] = {"t": spec.get("t"), "o": bool(spec.get("o")),
                             "d": bool(spec.get("d"))}
            else:                                 # raw manifest entry
                out[name] = {"t": normalize_spec(spec.get("type")),
                             "o": bool(spec.get("optional")),
                             "d": bool(spec.get("has_default"))}
        return out

    old_classes = (old or {}).get("classes") or {}
    new_classes = (new or {}).get("classes") or {}
    old_leaf, new_leaf = _by_leaf(old_classes), _by_leaf(new_classes)

    rows: list[dict] = []
    abstained: list[str] = []
    moved: list[dict] = []
    truncated = False
    matched_new: set[str] = set()

    def _add(row: dict) -> bool:
        nonlocal truncated
        if len(rows) >= cap:
            truncated = True
            return False
        rows.append(row)
        return True

    for path, oentry in old_classes.items():
        hit = _match_class(path, oentry, new_classes, new_leaf)
        if hit is None:
            _add({"kind": "class_removed", "class": path, "field": "",
                  "old": None, "new": None})
            continue
        npath, nentry = hit
        matched_new.add(npath)
        if npath != path:
            moved.append({"from": path, "to": npath})
        ofields, nfields = _fields(oentry), _fields(nentry)
        if ofields is None or nfields is None:
            abstained.append(npath)          # never flag what was never known
            continue
        per_class = 0
        for name, ospec in ofields.items():
            if per_class >= _PER_CLASS_CAP:
                truncated = True
                break
            nspec = nfields.get(name)
            if nspec is None:
                if _add({"kind": "field_removed", "class": npath, "field": name,
                         "old": ospec["t"], "new": None}):
                    per_class += 1
                continue
            if ospec["t"] != nspec["t"]:
                if _add({"kind": "type_changed", "class": npath, "field": name,
                         "old": ospec["t"], "new": nspec["t"]}):
                    per_class += 1
            elif ospec["o"] != nspec["o"]:
                if _add({"kind": "optional_changed", "class": npath, "field": name,
                         "old": ospec["t"], "new": nspec["t"],
                         "detail": ("now optional" if nspec["o"]
                                    else "no longer optional")}):
                    per_class += 1
        for name, nspec in nfields.items():
            if per_class >= _PER_CLASS_CAP:
                truncated = True
                break
            if name not in ofields:
                if _add({"kind": "field_added", "class": npath, "field": name,
                         "old": None, "new": nspec["t"]}):
                    per_class += 1

    for path in new_classes:
        if path not in matched_new and _match_class(
                path, new_classes[path], old_classes, old_leaf) is None:
            _add({"kind": "class_added", "class": path, "field": "",
                  "old": None, "new": None})

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    return {
        "rows": rows,
        "moved": moved,
        "abstained": sorted(set(abstained)),
        "counts": counts,
        "total": len(rows),
        "truncated": truncated,
        "from_versions": (old or {}).get("versions") or {},
        "to_versions": (new or {}).get("versions") or {},
    }


def diff_signature(diff: dict | None) -> str:
    """Fingerprint of WHAT changed — dismissing silences this exact set."""
    rows = (diff or {}).get("rows") or []
    if not rows:
        return ""
    keys = sorted(
        "\x00".join([r.get("kind") or "", r.get("class") or "",
                     r.get("field") or "",
                     json.dumps(r.get("new"), sort_keys=True)])
        for r in rows)
    return hashlib.sha1("\n".join(keys).encode("utf-8")).hexdigest()[:16]


def env_transition(instance_path: Any, manifest: dict | None) -> dict | None:
    """Has the environment's schema moved since the last one SM recorded?

    ``None`` when there is no manifest at all. With no PRIOR baseline the
    answer is honest rather than invented: ``changed: False`` and
    ``first: True`` — this run establishes the first baseline.
    """
    if not manifest:
        return None
    versions = manifest.get("versions") or {}
    key = env_key(versions)
    prev = previous_baseline(instance_path, key)
    if prev is None:
        return {"changed": False, "first": True, "to_key": key,
                "to_label": env_label(versions), "diff": None, "sig": ""}
    diff = diff_manifests(prev, manifest)
    dismissed = ((_load_index(instance_path).get("seen_transitions") or {})
                 .get(f"{prev.get('key')}>{key}") or {})
    sig = diff_signature(diff)
    return {
        "changed": bool(diff["rows"]),
        "first": False,
        "from_key": prev.get("key"),
        "from_label": prev.get("label") or env_label(prev.get("versions")),
        "to_key": key,
        "to_label": env_label(versions),
        "distance": version_distance(prev.get("versions"), versions),
        "diff": diff,
        "sig": sig,
        "dismissed": dismissed.get("sig") == sig and bool(sig),
    }


def dismiss_transition(instance_path: Any, from_key: str, to_key: str,
                       sig: str) -> None:
    """Memo an env transition as answered (delta-gated: a NEW schema change
    re-raises). Env-scope fact → stored with the baselines, not in the
    chip-keyed prompt memo."""
    if not (from_key and to_key and sig):
        return
    try:
        with _baseline_lock:
            index = _load_index(instance_path)
            index.setdefault("seen_transitions", {})[f"{from_key}>{to_key}"] = {
                "sig": sig,
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            baseline_dir(instance_path).mkdir(parents=True, exist_ok=True)
            _write_index(instance_path, index)
    except Exception:  # noqa: BLE001
        logger.warning("could not memo the env transition", exc_info=True)
