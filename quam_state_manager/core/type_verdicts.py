"""User verdicts about an ENVIRONMENT's schema — "in this env, that type is
right now".

SM's expected types come from the selected interpreter's dataclasses. That is
the best available truth, but it is not always the user's truth: quam and
quam_builder keep moving, a lab runs a fork, a field's meaning changes. The
user has to be able to teach SM — and the fact they are teaching is about the
LIBRARY, not about one chip, so a verdict is scoped to
``(environment, class, field)``, unlike the per-chip per-path assignments in
:mod:`type_policy` (which stay exactly as they were).

Four decisions shape this module:

**A verdict CHANGES the expectation, it does not merely suppress warnings.**
The edit gate reads ``Expected.enforced``; a suppress-only verdict would leave
the user unable to write the very value they just told SM was correct.

**It is applied by OVERLAYING the manifest**, not by adding a parallel lookup
layer. One overlaid document means the resolver, the judge, ``analyze_state``,
the type chips and the repair planner all become verdict-aware with no forked
code paths — and an "accept" verdict that agrees with the env is provably a
no-op.

**Carry is gated on the FIELD's spec, never on version distance.** A verdict
made against quam 0.7 applies to 0.7.1 iff that class·field's normalized spec
is still what it was when the user decided. That is exactly the condition
under which their reasoning still holds; version distance is a display label.

**``unknown_field`` can never be silenced.** A field the env's class does not
declare makes ``Quam.load()`` raise — that is a fact about loading, not a
disagreement about types, so it is refused at save time with an honest reason.
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
from quam_state_manager.core.state_env_baseline import (
    env_key,
    env_label,
    normalize_spec,
    version_distance,
)

logger = logging.getLogger(__name__)

VERDICTS_FILENAME = "type_verdicts.json"
_MAX_VERDICT_ENVS = 10
_MAX_VERDICTS_PER_ENV = 500
_CONFLICT_CAP = 50

_verdict_lock = threading.Lock()

# status vocabulary
EXACT = "exact"                  # same env, spec unchanged → enforced
CARRIED = "carried"              # another env, spec identical → enforced
NEEDS_REAFFIRM = "needs_reaffirm"  # the field moved since → NOT enforced
OBSOLETE = "obsolete"            # the env now agrees with the user → no-op
_ENFORCED = (EXACT, CARRIED)


def verdicts_path(instance_path: Any) -> Path:
    return Path(instance_path) / VERDICTS_FILENAME


def load_store(instance_path: Any) -> dict:
    """The whole verdict store; ``{}`` on anything unreadable (never raises).

    The absence check is not an optimization detail: ``safe_io.read_json``
    retries a missing file with backoff (right for LIVE files that may be
    mid-replace, ~900 ms for one that simply does not exist), and "no verdicts
    yet" is the normal steady state of this SM-owned sidecar. This resolver
    runs on render paths, so it must cost a stat.
    """
    path = verdicts_path(instance_path)
    try:
        if not path.exists():
            return {"version": 1, "envs": {}}
        data = safe_io.read_json(path)
        if isinstance(data, dict) and isinstance(data.get("envs"), dict):
            return data
    except Exception:  # noqa: BLE001
        logger.debug("type verdicts unreadable — treating as empty", exc_info=True)
    return {"version": 1, "envs": {}}


def _save_store(instance_path: Any, store: dict) -> None:
    safe_io.atomic_write_json(verdicts_path(instance_path), store)


def _key(class_path: str, field: str) -> str:
    return f"{class_path}.{field}"


def _canonical_of(manifest: dict | None, class_path: str) -> str:
    entry = ((manifest or {}).get("classes") or {}).get(class_path) or {}
    return entry.get("canonical") or class_path


def env_field_spec(manifest: dict | None, class_path: str,
                   field: str) -> tuple[dict | None, str]:
    """The env's normalized spec for a class·field, plus a refusal reason.

    Reasons are the honest ones the save route reports: ``unknown_class``,
    ``abstained`` (the probe could not introspect it) and ``unknown_field``
    (the load-breaking case a verdict must never paper over).
    """
    classes = (manifest or {}).get("classes") or {}
    entry = classes.get(class_path)
    if entry is None:
        for path, e in classes.items():
            if (e or {}).get("canonical") == class_path:
                entry = e
                break
    if entry is None:
        return None, "unknown_class"
    fields = entry.get("fields")
    if fields is None:
        return None, "abstained"
    f = fields.get(field)
    if f is None:
        return None, "unknown_field"
    return normalize_spec(f.get("type")), ""


def save_verdict(instance_path: Any, versions: dict, class_path: str,
                 field: str, *, decision: str, spec: dict | None = None,
                 type_expr: str | None = None, spec_source: str = "grammar",
                 env_spec: dict | None = None, note: str = "",
                 carried_from: str | None = None) -> dict:
    """Record one verdict for this environment. Returns the stored record.

    ``decision='accept'`` means "the env is right" — stored so the schema-change
    row stops asking, while changing no expectation. ``decision='override'``
    means "the correct type is this", and ``spec`` is a TypeSpec stored
    VERBATIM (from the env, from the old baseline, or built from the user's
    type grammar) — the grammar cannot express union/component/enum types, and
    the only honest sources for those are the manifests themselves.
    """
    if decision not in ("accept", "override"):
        raise ValueError("decision must be 'accept' or 'override'")
    if decision == "override" and not spec:
        raise ValueError("an override verdict needs a spec")
    key = env_key(versions)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record = {
        "decision": decision,
        "spec_source": spec_source,
        "type": type_expr,
        "spec": normalize_spec(spec) if spec else None,
        "env_spec_at_decision": normalize_spec(env_spec) if env_spec else None,
        "decided_at": now,
        "decided_against_versions": {k: (versions or {}).get(k) for k in
                                     ("quam", "quam_builder",
                                      "quam_builder_commit")},
        "carried_from": carried_from,
        "note": str(note or ""),
    }
    with _verdict_lock:
        store = load_store(instance_path)
        envs = store.setdefault("envs", {})
        env = envs.setdefault(key, {"label": env_label(versions),
                                    "versions": versions or {},
                                    "first_seen": now, "verdicts": {}})
        env["label"] = env_label(versions)
        env["versions"] = versions or {}
        verdicts = env.setdefault("verdicts", {})
        verdicts[_key(class_path, field)] = record
        if len(verdicts) > _MAX_VERDICTS_PER_ENV:
            oldest = sorted(verdicts.items(),
                            key=lambda kv: kv[1].get("decided_at") or "")
            for k, _ in oldest[:len(verdicts) - _MAX_VERDICTS_PER_ENV]:
                verdicts.pop(k, None)
        if len(envs) > _MAX_VERDICT_ENVS:
            stale = sorted(envs.items(),
                           key=lambda kv: kv[1].get("first_seen") or "")
            for k, _ in stale[:len(envs) - _MAX_VERDICT_ENVS]:
                if k != key:
                    envs.pop(k, None)
        _save_store(instance_path, store)
    return record


def revoke_verdict(instance_path: Any, key: str, class_path: str,
                   field: str) -> bool:
    with _verdict_lock:
        store = load_store(instance_path)
        env = (store.get("envs") or {}).get(key) or {}
        if (env.get("verdicts") or {}).pop(_key(class_path, field), None) is None:
            return False
        _save_store(instance_path, store)
        return True


def resolve_for_manifest(instance_path: Any, manifest: dict | None) -> dict:
    """Which verdicts apply to THIS environment, and with what standing.

    Keyed by ``"<canonical class>.<field>"`` so a class that moved homes keeps
    its verdict. The spec-identity gate runs even on an exact env-key hit: it
    is the only thing that can go stale, and it is what makes editable-install
    commit churn harmless.
    """
    if not manifest:
        return {}
    store = load_store(instance_path)
    envs = store.get("envs") or {}
    if not envs:
        return {}
    versions = manifest.get("versions") or {}
    current = env_key(versions)
    ordered = ([(current, envs[current])] if current in envs else []) + [
        (k, v) for k, v in sorted(
            envs.items(), key=lambda kv: kv[1].get("first_seen") or "",
            reverse=True) if k != current]

    resolved: dict[str, dict] = {}
    for key, env in ordered:
        for ck, record in (env.get("verdicts") or {}).items():
            class_path, _, field = ck.rpartition(".")
            if not class_path or not field:
                continue
            canonical = _canonical_of(manifest, class_path)
            out_key = _key(canonical, field)
            if out_key in resolved:
                continue                      # nearer env already answered
            env_spec, reason = env_field_spec(manifest, class_path, field)
            if reason:
                continue                      # this env has no such field
            decided_against = record.get("env_spec_at_decision")
            same_field = (decided_against is None or decided_against == env_spec)
            if record.get("decision") == "override" and record.get("spec") == env_spec:
                status = OBSOLETE            # the library caught up
            elif not same_field:
                status = NEEDS_REAFFIRM      # the field moved under the verdict
            else:
                status = EXACT if key == current else CARRIED
            resolved[out_key] = dict(
                record,
                status=status,
                class_path=canonical,
                field=field,
                from_env_key=key,
                from_label=env.get("label") or env_label(env.get("versions")),
                distance=version_distance(env.get("versions"), versions),
                env_spec=env_spec,
                enforced=(status in _ENFORCED
                          and record.get("decision") == "override"),
            )
    return resolved


def overlay_manifest(manifest: dict, resolved: dict) -> dict:
    """A copy of *manifest* whose taught fields carry the user's type.

    Copy-on-write: only the touched class entries are rebuilt, so an unaffected
    class is shared with the original. The pristine env spec is kept on the
    field as ``env_type`` (the UI must be able to show both sides), and the
    overlay is stamped with a signature so memo keys can include it.
    """
    enforced = {k: v for k, v in (resolved or {}).items() if v.get("enforced")}
    if not manifest or not enforced:
        return manifest
    classes = dict(manifest.get("classes") or {})
    touched = False
    for path, entry in list(classes.items()):
        entry = entry or {}
        fields = entry.get("fields")
        if not fields:
            continue
        canonical = entry.get("canonical") or path
        new_fields = None
        for name in list(fields):
            v = enforced.get(_key(canonical, name))
            if v is None:
                continue
            if new_fields is None:
                new_fields = dict(fields)
            f = dict(new_fields[name] or {})
            f["env_type"] = f.get("type")
            f["type"] = dict(v["spec"] or {})
            f["verdict"] = {"from": v.get("from_label"),
                            "status": v.get("status"),
                            "decided_at": v.get("decided_at"),
                            "note": v.get("note") or ""}
            new_fields[name] = f
        if new_fields is not None:
            new_entry = dict(entry)
            new_entry["fields"] = new_fields
            classes[path] = new_entry
            touched = True
    if not touched:
        return manifest
    out = dict(manifest)
    out["classes"] = classes
    out["verdict_sig"] = verdict_signature(resolved)
    return out


def verdict_signature(resolved: dict) -> str:
    """Fingerprint of the ENFORCED verdict set — folded into analysis memo keys
    so a saved verdict can never serve stale findings."""
    keys = sorted(
        k + "\x00" + json.dumps(v.get("spec"), sort_keys=True)
        for k, v in (resolved or {}).items() if v.get("enforced"))
    if not keys:
        return ""
    return hashlib.sha1("\n".join(keys).encode("utf-8")).hexdigest()[:16]


def conflicting_leaves(state: dict, manifest: dict | None, class_path: str,
                       field: str, spec: dict | None, *,
                       cap: int = _CONFLICT_CAP) -> list[str]:
    """Leaves on THIS chip that the proposed type would reject.

    Shown before saving so the blast radius of a class-wide verdict is visible
    — but it never blocks the save: the verdict may itself be the repair path
    (the same warning-not-block rule as a per-key type assignment).
    """
    if not spec or not state:
        return []
    from quam_state_manager.core.state_env_validate import judge

    canonical = _canonical_of(manifest, class_path)
    hits: list[str] = []

    def walk(node: Any, path: str) -> None:
        if len(hits) >= cap or not isinstance(node, dict):
            return
        cls = node.get("__class__")
        if isinstance(cls, str) and (
                cls == class_path or _canonical_of(manifest, cls) == canonical):
            if field in node:
                value = node[field]
                ok, _code, _msg = judge(value, spec)
                if not ok:
                    hits.append(f"{path}.{field}".lstrip("."))
        for k, v in node.items():
            if isinstance(v, dict):
                walk(v, f"{path}.{k}".lstrip("."))
            elif isinstance(v, list):
                for i, item in enumerate(v):
                    if isinstance(item, dict):
                        walk(item, f"{path}.{k}.{i}".lstrip("."))

    walk(state, "")
    return hits[:cap]
