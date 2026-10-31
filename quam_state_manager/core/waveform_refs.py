"""Content-addressed matched-state waveform references and explicit comparison."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import os
import tempfile
import threading
import time

from quam_state_manager.core import config_generator, safe_io, working_copy
from quam_state_manager.core.env_seen import VERSIONS, live_context
from quam_state_manager.core.env_upgrade import non_waveform_diff
from quam_state_manager.core.state_env_baseline import _hash, normalize_env_path

WAVEFORM_REF_FORMAT = 1
AUTO_RECORD_SECONDS = 60   # real chips load far slower than the synthetic fixtures (see MEASURE_step3.md)
_MAX_REFERENCE_BYTES = 64_000_000
# Retention per (chip, env): the newest references up to this count and byte
# total; the newest reference an observation points at is never deleted.
MAX_REFERENCES_PER_ENV = 6
MAX_BYTES_PER_ENV = 48_000_000
_INDEX_FORMAT = 1
_HEADER_KEYS = ("format", "key", "identity", "chip_key", "python_path", "versions", "official_digest",
                "source_fingerprint", "basis", "recorded_at", "files", "other_json", "wall_seconds")
_index_lock = threading.RLock()
_index_memo: dict = {}


def _same_versions(reference: dict, observation: dict) -> bool:
    return all((reference.get("versions") or {}).get(name) == (observation.get("versions") or {}).get(name)
               for name in VERSIONS)


def matches(reference: dict, python_path: str, observation: dict) -> bool:
    """The one predicate: a reference recorded in this env under exactly the
    versions, official class digest and source fingerprint of ``observation``."""
    observation = observation or {}
    return (bool(observation.get("versions"))
            and reference.get("python_path") == normalize_env_path(python_path)
            and reference.get("official_digest") == observation.get("official_digest")
            and reference.get("source_fingerprint") == observation.get("source_fingerprint")
            and _same_versions(reference, observation))


def versions_match(reference: dict, python_path: str, observation: dict) -> bool:
    """Same env and version strings, whatever the class digest or sources."""
    observation = observation or {}
    return (bool(observation.get("versions"))
            and reference.get("python_path") == normalize_env_path(python_path)
            and _same_versions(reference, observation))


def metrics(before: list[float], after: list[float], threshold: float = 1e-12) -> dict:
    """[derived] Lag is onset(after)-onset(before); length is len(after)-len(before).

    Relative deviation is max(abs(after-before))/max(abs(before)) on the union
    of sample indices with zero padding. An absent onset has no lag; a zero
    reference has zero relative deviation only when both traces are zero.
    """
    onset_old = next((i for i, x in enumerate(before) if abs(x) > threshold), None)
    onset_new = next((i for i, x in enumerate(after) if abs(x) > threshold), None)
    delta = max((abs((after[i] if i < len(after) else 0) -
                     (before[i] if i < len(before) else 0))
                 for i in range(max(len(before), len(after)))), default=0)
    peak = max(map(abs, before), default=0)
    reason = ("" if onset_old is not None and onset_new is not None else
              "all-zero waveform" if onset_old is None and onset_new is None else
              "no onset in the reference" if onset_old is None else "no onset in the current env")
    return {"onset_lag": onset_new - onset_old if onset_old is not None and onset_new is not None else None,
            "onset_reason": reason, "lag_unit": "samples", "length_difference": len(after) - len(before),
            "relative_deviation": delta / peak if peak else (0 if delta == 0 else None)}


def traces(config: dict) -> dict:
    result = {}
    for element, entry in (config.get("elements") or {}).items():
        for operation, pulse_name in (entry.get("operations") or {}).items():
            pulse = (config.get("pulses") or {}).get(pulse_name) or {}
            for channel, waveform_name in (pulse.get("waveforms") or {}).items():
                waveform = (config.get("waveforms") or {}).get(waveform_name) or {}
                trace = {"element": element, "operation": operation, "channel": channel,
                         "type": waveform.get("type"), "length": pulse.get("length"),
                         "sampling_rate": waveform.get("sampling_rate"),
                         "two_qubit": bool(re.search(r"cz|cphase|iswap|cross|(?:^|_)cr(?:_|$)",
                                                      element + " " + operation, re.I))}
                try:
                    if waveform.get("type") == "constant":
                        trace["value"] = float(waveform["sample"])
                        trace["trace_hash"] = hashlib.sha256(struct.pack("<d", trace["value"])).hexdigest()
                    elif waveform.get("type") == "arbitrary":
                        samples = [float(x) for x in waveform["samples"]]
                        if not all(math.isfinite(x) for x in samples):
                            raise ValueError("non-finite samples")
                        raw = struct.pack(f"<{len(samples)}f", *samples)
                        trace["samples_f32"] = base64.b64encode(raw).decode("ascii")
                        trace["storage_hash"] = hashlib.sha256(raw).hexdigest()
                        trace["trace_hash"] = hashlib.sha256(struct.pack(f"<{len(samples)}d", *samples)).hexdigest()
                    else:
                        trace["error"] = "waveform type not compared"
                except (KeyError, TypeError, ValueError, OverflowError, struct.error) as exc:
                    trace["error"] = str(exc)
                result[json.dumps([element, operation, channel])] = trace
    return result


def _samples(trace):
    raw = base64.b64decode(trace["samples_f32"], validate=True)
    if hashlib.sha256(raw).hexdigest() != trace["storage_hash"]:
        raise ValueError("reference sample hash differs")
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))


def compare_traces(before: dict, after: dict, name_filter: str = "") -> dict:
    rows = []
    counts = {"differ": 0, "identical": 0, "not_comparable": 0}
    for key in sorted(before.keys() | after.keys()):
        old, new = before.get(key), after.get(key)
        item = old or new
        if name_filter.lower() not in (item["element"] + " " + item["operation"]).lower():
            continue
        row = {k: item[k] for k in ("element", "operation", "channel", "two_qubit")}
        row["label"] = "not comparable"
        if old and new and not old.get("error") and not new.get("error") and old["type"] == new["type"]:
            if old["type"] == "arbitrary":
                values = metrics(_samples(old), _samples(new))
            else:
                values = metrics([old["value"]], [new["value"]])
                values["length_difference"] = (new.get("length") or 0) - (old.get("length") or 0)
            row.update(values)
            if old["trace_hash"] == new["trace_hash"] and row["length_difference"] == 0:
                row["label"] = "bit-identical"
            elif row["relative_deviation"] is not None and row["relative_deviation"] <= 1e-9 and row["length_difference"] == 0:
                row["label"] = "numerically equal"
            else:
                row["label"] = "differs"
        counts["not_comparable" if row["label"] == "not comparable" else
               "differ" if row["label"] == "differs" else "identical"] += 1
        rows.append(row)
    rows.sort(key=lambda r: (not r["two_qubit"], r["element"], r["operation"], r["channel"]))
    return {"rows": rows, "counts": counts,
            "message": f"{counts['differ']} differ \u00b7 {counts['identical']} identical \u00b7 "
                       f"{counts['not_comparable']} not comparable"}


def preview(python_path: str, folder, *, timeout=120, strict=False) -> dict:
    """Freeze exact JSON bytes through share-delete reads before the subprocess."""
    folder = Path(folder)
    inputs = {}
    with tempfile.TemporaryDirectory(prefix="env_reference_inputs_") as temporary:
        destination = Path(temporary)
        for path in sorted(folder.rglob("*.json")):
            _, raw = safe_io.read_json_raw(path)
            relative = path.relative_to(folder).as_posix()
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            inputs[relative] = base64.b64encode(raw).decode("ascii")
        start = time.perf_counter()
        kwargs = {"timeout": timeout} if timeout != 120 else {}
        if strict:
            kwargs["strict"] = True
        outcome = config_generator.run_config_preview(python_path, destination, **kwargs)
        outcome["wall_seconds"] = time.perf_counter() - start
        outcome["reference_inputs"] = inputs
        return outcome


def input_basis(inputs: dict) -> str:
    """Use the same state/wiring content hash as the Config Viewer."""
    state = json.loads(base64.b64decode(inputs["state.json"]))
    wiring = json.loads(base64.b64decode(inputs.get("wiring.json", "e30=")))
    return working_copy.content_hash(state, wiring)


def record(instance_path, chip_key, python_path, body, outcome, *, context, unsaved=False):
    if not live_context(context) or unsaved or not outcome.get("ok"):
        return None
    result = outcome.get("result") or {}
    if result.get("warnings"):
        return None  # loader fallbacks or stripped keys do not describe the captured input
    if result.get("config") is None or not outcome.get("reference_inputs"):
        return None
    inputs = outcome["reference_inputs"]
    basis = input_basis(inputs)
    files = {}
    directory = Path(instance_path) / "waveform_refs"
    blobs = directory / "inputs"
    blobs.mkdir(parents=True, exist_ok=True)
    for name, encoded in inputs.items():
        digest = hashlib.sha256(base64.b64decode(encoded)).hexdigest()
        blob = blobs / f"{digest}.json"
        if not blob.exists():
            safe_io.atomic_write_json(blob, {"bytes": encoded})
        files[name] = digest
    metadata = {"format": WAVEFORM_REF_FORMAT, "chip_key": chip_key,
                "python_path": normalize_env_path(python_path), "versions": result.get("versions") or {},
                "official_digest": body.get("official_digest"), "basis": basis,
                "source_fingerprint": body.get("source_fingerprint"),
                "files": files, "traces": traces(result["config"]),
                "config_non_waveform": {"integration_weights": result["config"].get("integration_weights", {}),
                                        "controllers": result["config"].get("controllers", {})},
                "wall_seconds": outcome.get("wall_seconds"), "other_json": [],
                "recorded_at": datetime.now(timezone.utc).isoformat()}
    live = context.get("path")
    if live:
        for path in sorted(Path(live).rglob("*.json")):
            name = path.relative_to(live).as_posix()
            if name not in inputs:
                metadata["other_json"].append(name)   # named only, never parsed
    identity = _hash([chip_key, metadata["python_path"], metadata["versions"], metadata["official_digest"], basis])
    metadata["identity"] = identity
    key = _hash([identity, metadata["source_fingerprint"], metadata["files"],
                 metadata["traces"], metadata["config_non_waveform"]])
    metadata["key"] = key
    if len(json.dumps(metadata).encode("utf-8")) > _MAX_REFERENCE_BYTES:
        return None
    path = directory / f"{key}.json"
    if not path.exists():
        safe_io.atomic_write_json(path, metadata)
    try:
        prune(instance_path, chip_key, metadata["python_path"], keep=key)
    except Exception:  # noqa: BLE001 -- retention never fails the reference just written
        pass
    return key


def _header(reference: dict, size: int) -> dict:
    header = {k: reference.get(k) for k in _HEADER_KEYS}
    header["waveforms"] = len(reference.get("traces") or {})
    header["size"] = size
    return header


def _index_path(instance_path) -> Path:
    # Outside waveform_refs/ so writing it never moves that directory's mtime.
    return Path(instance_path) / "waveform_refs_index.json"


def _all_headers(instance_path) -> list[dict]:
    """Headers of every reference file, memoized on (dir mtime, names) and kept
    in a small on-disk index keyed by each file's (mtime_ns, size), so a
    reference body is parsed once, when it is new, never on a listing."""
    directory = Path(instance_path) / "waveform_refs"
    try:
        stamp = directory.stat().st_mtime_ns
        names = tuple(sorted(n for n in os.listdir(directory) if n.endswith(".json")))
    except OSError:
        return []
    memo_key = (stamp, names)
    with _index_lock:
        cached = _index_memo.get(str(directory))
        if cached and cached[0] == memo_key:
            return cached[1]
        try:
            index = safe_io.read_json(_index_path(instance_path))
            entries = index.get("entries") if isinstance(index, dict) and index.get("format") == _INDEX_FORMAT else None
        except (OSError, ValueError):
            entries = None
        entries = entries if isinstance(entries, dict) else {}
        fresh, headers, dirty = {}, [], False
        for name in names:
            path = directory / name
            try:
                info = path.stat()
            except OSError:
                continue
            signature = [info.st_mtime_ns, info.st_size]
            entry = entries.get(name)
            if not (isinstance(entry, dict) and entry.get("stat") == signature
                    and isinstance(entry.get("header"), dict)):
                try:
                    entry = {"stat": signature, "header": _header(safe_io.read_json(path), info.st_size)}
                except (OSError, ValueError, AttributeError):
                    continue
                dirty = True
            fresh[name] = entry
            headers.append(entry["header"])
        if dirty or set(fresh) != set(entries):
            try:
                safe_io.atomic_write_json(_index_path(instance_path), {"format": _INDEX_FORMAT, "entries": fresh})
            except OSError:
                pass
        _index_memo[str(directory)] = (memo_key, headers)
        return headers


def list_references(instance_path, chip_key):
    """Reference headers of one chip, newest first; no sample data."""
    result = [dict(h) for h in _all_headers(instance_path) if h.get("chip_key") == chip_key]
    return sorted(result, key=lambda r: r.get("recorded_at") or "", reverse=True)


def _anchors(instance_path, chip_key) -> list | None:
    """Every (env, observation) of this chip a reference may be needed for:
    each env's current observation and each held transition's previous."""
    from quam_state_manager.core import env_seen
    try:
        record = env_seen.read(instance_path, chip_key)
    except Exception:  # noqa: BLE001 -- unreadable history: retention abstains
        return None
    out = []
    for env, entry in (record.get("envs") or {}).items():
        out.append((env, entry))
        for held in ("pending", "acknowledged_event"):
            previous = ((entry or {}).get(held) or {}).get("previous")
            if previous:
                out.append((env, previous))
    return out


def prune(instance_path, chip_key, python_path, *, keep=None) -> list[str]:
    """Keep the newest references of one (chip, env) within the count and byte
    caps. Never deleted: ``keep`` and the newest reference matching any anchor
    (an env's current observation or a transition's previous versions). Input
    blobs no remaining reference names are deleted too. Returns deleted keys."""
    anchors = _anchors(instance_path, chip_key)
    if anchors is None:
        return []
    env = normalize_env_path(python_path)
    group = [r for r in list_references(instance_path, chip_key) if r.get("python_path") == env]
    protected = {keep} if keep else set()
    for anchor_env, observation in anchors:
        newest = next((r for r in group if matches(r, anchor_env, observation)), None)
        if newest:
            protected.add(newest["key"])
    directory = Path(instance_path) / "waveform_refs"
    removed, count, total = [], 0, 0
    for reference in group:                       # newest first
        count += 1
        total += int(reference.get("size") or 0)
        if reference["key"] in protected or (count <= MAX_REFERENCES_PER_ENV and total <= MAX_BYTES_PER_ENV):
            continue
        if not re.fullmatch(r"[0-9a-f]{64}", reference.get("key") or ""):
            continue
        try:
            (directory / f"{reference['key']}.json").unlink(missing_ok=True)
        except OSError:
            continue
        removed.append(reference["key"])
        count -= 1
        total -= int(reference.get("size") or 0)
    if removed:
        used = {digest for header in _all_headers(instance_path)
                for digest in (header.get("files") or {}).values()}
        for blob in (directory / "inputs").glob("*.json"):
            if blob.stem not in used and re.fullmatch(r"[0-9a-f]{64}", blob.stem):
                try:
                    blob.unlink()
                except OSError:
                    pass
    return removed


def load(instance_path, key):
    if not re.fullmatch(r"[0-9a-f]{64}", key or ""):
        raise ValueError("invalid reference key")
    result = safe_io.read_json(Path(instance_path) / "waveform_refs" / f"{key}.json")
    if result.get("format") != WAVEFORM_REF_FORMAT:
        raise ValueError("waveform reference format not checked")
    return result


def compare(instance_path, key, python_path, *, name_filter="", transform=None):
    """Regenerate the reference's stored inputs in this env and compare.

    ``transform(state, wiring) -> (state, wiring, note)`` rewrites the inputs
    first (the reviewed plan of the transition); ``note`` names what it did."""
    note = ""
    try:
        reference = load(instance_path, key)
        with tempfile.TemporaryDirectory(prefix="env_reference_compare_") as temporary:
            folder = Path(temporary)
            for name, digest in reference["files"].items():
                path = folder / name
                if not path.resolve().is_relative_to(folder.resolve()) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                    raise ValueError("invalid reference input path")
                blob = safe_io.read_json(Path(instance_path) / "waveform_refs/inputs" / f"{digest}.json")
                raw = base64.b64decode(blob["bytes"], validate=True)
                if hashlib.sha256(raw).hexdigest() != digest:
                    raise ValueError("reference input hash differs")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            if transform is not None:
                state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
                wiring_path = folder / "wiring.json"
                wiring = json.loads(wiring_path.read_text(encoding="utf-8")) if wiring_path.exists() else {}
                state, wiring, note = transform(state, wiring)
                (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
                if wiring_path.exists():
                    wiring_path.write_text(json.dumps(wiring), encoding="utf-8")
            outcome = preview(python_path, folder, strict=True)
        applied = f" with your {note} applied" if note else ""
        if not outcome.get("ok"):
            return {"status": "not_checked", "attempted": True, "applied": note,
                    "message": f"not compared: the reference inputs{applied} do not load in this env "
                               "(see the load-stopping items above): "
                               + str(outcome.get("error") or "no reason given"),
                    "error": outcome.get("error"), "rows": []}
        compared = compare_traces(reference["traces"], traces(outcome["result"]["config"]), name_filter)
        return {**compared, "status": "found" if compared["counts"]["differ"] else "none_found",
                "attempted": True, "applied": note,
                "applied_text": f"Compared on the reference inputs{applied}." if note else "",
                "versions": (outcome.get("result") or {}).get("versions") or {},
                "reference_state_hash": reference["basis"][:10],
                "current_state_hash": input_basis(outcome["reference_inputs"])[:10],
                "non_waveform": non_waveform_diff(reference["config_non_waveform"], outcome["result"]["config"])}
    except (OSError, ValueError, KeyError, TypeError, struct.error) as exc:
        return {"status": "not_checked", "attempted": True, "message": "could not check: reference unreadable",
                "error": str(exc), "rows": []}
