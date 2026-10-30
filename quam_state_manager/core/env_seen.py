"""Per-chip observations of an interpreter; no imports of the web layer."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import threading

from quam_state_manager.core import safe_io
from quam_state_manager.core.state_env_baseline import _hash, normalize_env_path

VERSIONS = ("quam", "quam_builder", "qualang_tools", "qm", "numpy", "scipy")
# The packages whose change is an environment upgrade of its own; numpy and
# scipy are recorded and reported but never anchor a transition.
OFFICIAL_VERSIONS = ("quam", "quam_builder", "qualang_tools", "qm")


def _known(value) -> bool:
    """A version that could not be read is unknown, not a version."""
    return value is not None and str(value).strip().lower() not in ("", "none", "unknown")


def _version_pairs(previous: dict, current: dict, names=VERSIONS):
    """(name, old, new) for every package whose version is KNOWN on both
    sides and differs; an unreadable side is never a change."""
    out = []
    for name in names:
        old = (previous.get("versions") or {}).get(name)
        new = (current.get("versions") or {}).get(name)
        if _known(old) and _known(new) and old != new:
            out.append((name, old, new))
    return out


def unreadable(current: dict) -> list:
    """Packages whose version this observation could not read."""
    return [name for name in VERSIONS
            if not _known((current.get("versions") or {}).get(name))]
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


_RELEASE = re.compile(r"(\d+(?:\.\d+)*)(.*)")


def _order(old, new):
    """[derived] Order two version strings by their leading numeric release
    (missing parts are 0). Returns "upgrade"/"downgrade", or None when the
    strings cannot be ordered: no leading release, or equal releases with
    different suffixes (pre-release / local tags are not ranked here)."""
    a, b = _RELEASE.fullmatch(str(old).strip()), _RELEASE.fullmatch(str(new).strip())
    if old is None or new is None or not a or not b:
        return None
    left = [int(x) for x in a.group(1).split(".")]
    right = [int(x) for x in b.group(1).split(".")]
    width = max(len(left), len(right))
    left += [0] * (width - len(left))
    right += [0] * (width - len(right))
    if left == right:
        return None
    return "upgrade" if right > left else "downgrade"


def direction(previous: dict, current: dict) -> str:
    """Describe direction only when the version strings can be ordered."""
    labels = []
    for name, old, new in _version_pairs(previous, current):
        suffix = _order(old, new) or "version changed"
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
    official_versions_changed = bool(_version_pairs(previous, current, OFFICIAL_VERSIONS))
    other_versions_changed = bool(_version_pairs(previous, current))
    official_changed = (official_versions_changed or
                        previous.get("official_digest") != current.get("official_digest"))
    changed = (other_versions_changed or official_changed or
               previous.get("source_fingerprint") != current.get("source_fingerprint"))
    return {"first": False, "changed": changed,
            "official_changed": official_changed,
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
        held = (previous or {}).get("pending")
        if event["changed"] and not event["official_changed"] and held and held.get("official_changed"):
            # An unacknowledged official transition stays the anchor: its
            # ``previous`` (the versions before the change) is what the schema
            # reference, the waveform references and the reviewed plan key on.
            # A later change that is not itself official (lab files, numpy /
            # scipy, a fingerprint) is kept as an additional fact.
            later = list(held.get("later_changes") or [])
            later.append({"direction": event["direction"], "lab_changed": event["lab_changed"],
                          "time": current["time"]})
            current["pending"] = {**held, "later_changes": later[-20:]}
        elif event["changed"]:
            current["pending"] = {**event, "previous": {
                k: v for k, v in previous.items() if k not in ("pending", "acknowledged_event")}}
        elif held:
            current["pending"] = held
        missing = unreadable(current)
        if current.get("pending"):
            current["pending"] = {**current["pending"], "unreadable": missing}
        record["envs"][env] = current
        path = record_path(instance_path, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        safe_io.atomic_write_json(path, record)
    return {**event, "unreadable": missing, "recorded": True, "current": current}


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
        entry = record["envs"].get(normalize_env_path(python_path)) or {}
        if entry.get("pending"):
            # The card returns to its compact form; the event stays readable so
            # baseline pruning still protects the body it points at.
            entry["acknowledged_event"] = entry.pop("pending")
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


def _review_key(python_path, event):
    return _hash([normalize_env_path(python_path), (event.get("previous") or {}).get("official_digest"),
                  (event.get("previous") or {}).get("versions")])


def remember_review(instance_path, key, python_path, event, renames, removals):
    """Keep the reviewed plan of one transition so the matched-state compare can
    apply the same plan to the reference inputs."""
    with _lock:
        record = read(instance_path, key)
        record.setdefault("reviewed_plans", {})[_review_key(python_path, event)] = {
            "renames": list(renames), "removals": [list(paths) for paths in removals]}
        path = record_path(instance_path, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        safe_io.atomic_write_json(path, record)


def reviewed_plan(instance_path, key, python_path, event):
    return (read(instance_path, key).get("reviewed_plans") or {}).get(_review_key(python_path, event))


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
