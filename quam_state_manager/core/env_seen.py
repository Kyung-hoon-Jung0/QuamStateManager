"""Per-chip observations of an interpreter; no imports of the web layer."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import threading

from quam_state_manager.core import safe_io
from quam_state_manager.core.state_env_baseline import _hash, normalize_env_path

VERSIONS = ("quam", "quam_builder", "qualang_tools", "qm", "numpy", "scipy")
_lock = threading.RLock()


def live_context(context: dict) -> bool:
    """Only a writable live origin can record an observation or reference."""
    return ((context.get("origin") or "live") == "live"
            and not context.get("readonly") and not context.get("read_only")
            and not context.get("live_readonly_hint"))


def chip_key(folder: str) -> str:
    return _hash(normalize_env_path(folder))[:32]


def record_path(instance_path, key: str) -> Path:
    return Path(instance_path) / "env_seen" / f"{_hash(key)[:32]}.json"


def read(instance_path, key: str) -> dict:
    path = record_path(instance_path, key)
    if not path.exists():
        return {"version": 1, "envs": {}}
    result = safe_io.read_json(path)
    if not isinstance(result, dict) or not isinstance(result.get("envs"), dict):
        raise ValueError("environment observation record is malformed")
    return result


def direction(previous: dict, current: dict) -> str:
    """Describe direction only when the version strings can be ordered."""
    from packaging.version import InvalidVersion, Version

    labels = []
    for name in VERSIONS:
        old = (previous.get("versions") or {}).get(name)
        new = (current.get("versions") or {}).get(name)
        if old == new:
            continue
        suffix = "version changed"
        try:
            suffix = "upgrade" if Version(str(new)) > Version(str(old)) else "downgrade"
        except InvalidVersion:
            pass
        labels.append(f"{name.replace('_', '-')} {old} \u2192 {new} ({suffix})")
    if labels:
        return " \u00b7 ".join(labels)
    if previous.get("official_digest") != current.get("official_digest"):
        return "same version strings, class definitions changed"
    if previous.get("source_fingerprint") != current.get("source_fingerprint"):
        return "same version strings, source files changed"
    return ""


def transition(previous: dict | None, current: dict) -> dict:
    """Compare only the last observation of this chip and this env path."""
    if previous is None:
        return {"first": True, "changed": False, "official_changed": False,
                "lab_changed": False, "direction": "", "previous": None}
    versions_changed = any((previous.get("versions") or {}).get(k) !=
                           (current.get("versions") or {}).get(k) for k in VERSIONS)
    official_changed = previous.get("official_digest") != current.get("official_digest")
    changed = (versions_changed or official_changed or
               previous.get("source_fingerprint") != current.get("source_fingerprint"))
    return {"first": False, "changed": changed,
            "official_changed": versions_changed or official_changed,
            "lab_changed": previous.get("lab_digest") != current.get("lab_digest"),
            "direction": direction(previous, current), "previous": previous}


def observe(instance_path, key: str, python_path: str, body: dict, *, context: dict) -> dict:
    """Persist after background probing or an explicit check, never on open."""
    if not live_context(context):
        return {"recorded": False, "first": False, "changed": False}
    env = normalize_env_path(python_path)
    current = {k: body.get(k) for k in ("official_digest", "lab_digest", "source_fingerprint")}
    current.update(versions={k: (body.get("versions") or {}).get(k) for k in VERSIONS},
                   baseline_key=body.get("key"),
                   time=datetime.now(timezone.utc).isoformat(timespec="microseconds"))
    with _lock:
        record = read(instance_path, key)
        previous = record["envs"].get(env)
        event = transition(previous, current)
        # Preserve the event until acknowledged; repeated renders cannot erase it.
        if event["changed"]:
            current["pending"] = {**event, "previous": {
                k: v for k, v in previous.items() if k != "pending"}}
        elif previous and previous.get("pending"):
            current["pending"] = previous["pending"]
        record["envs"][env] = current
        path = record_path(instance_path, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        safe_io.atomic_write_json(path, record)
    return {**event, "recorded": True, "current": current}


def acknowledgement_key(python_path, previous, current, findings_signature):
    return _hash([normalize_env_path(python_path), previous.get("official_digest"),
                  current.get("official_digest"), findings_signature])


def acknowledged(instance_path, key, python_path, previous, current, findings_signature):
    identity = acknowledgement_key(python_path, previous, current, findings_signature)
    return identity in read(instance_path, key).get("acknowledgements", {})


def acknowledge(instance_path, key, python_path, previous, current, findings_signature, *, context, event):
    """One event memo; the finding signature makes a changed set reappear."""
    if (not live_context(context) or event.get("first") or not event.get("official_changed")
            or not event.get("stops_loading")):
        return False
    with _lock:
        record = read(instance_path, key)
        identity = acknowledgement_key(python_path, previous, current, findings_signature)
        record.setdefault("acknowledgements", {})[identity] = datetime.now(timezone.utc).isoformat()
        safe_io.atomic_write_json(record_path(instance_path, key), record)
    return True


def accepted_renames(instance_path, key):
    return read(instance_path, key).get("accepted_renames", [])


def import_legacy_ack(instance_path, key, python_path, previous, current, signature, legacy_token):
    """Import each legacy memo once, so a changed finding set still reappears."""
    with _lock:
        record = read(instance_path, key)
        imported = record.setdefault("legacy_ack_imports", [])
        identity = _hash([normalize_env_path(python_path), legacy_token])
        if identity in imported:
            return
        imported.append(identity)
        ack = acknowledgement_key(python_path, previous, current, signature)
        record.setdefault("acknowledgements", {})[ack] = datetime.now(timezone.utc).isoformat()
        path = record_path(instance_path, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        safe_io.atomic_write_json(path, record)


def remember_renames(instance_path, key, python_path, selections, event):
    with _lock:
        record = read(instance_path, key)
        accepted = record.setdefault("accepted_renames", [])
        for item in selections:
            value = {k: item[k] for k in ("class", "old", "new")}
            value.update(python_path=normalize_env_path(python_path),
                         from_digest=(event.get("previous") or {}).get("official_digest"))
            if value not in accepted:
                accepted.append(value)
        safe_io.atomic_write_json(record_path(instance_path, key), record)
