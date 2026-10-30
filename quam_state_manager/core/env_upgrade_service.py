"""Explicit offline checks and reference snapshots; all writes stay in instance."""
from __future__ import annotations

from pathlib import Path

from quam_state_manager.core import config_generator, env_rename, env_seen, env_upgrade, safe_io
from quam_state_manager.core import state_env_baseline as baseline, state_env_schema, waveform_refs


def matching_references(instance_path, key, python_path, previous):
    """References recorded in this env under exactly the versions the change came from."""
    return [r for r in waveform_refs.list_references(instance_path, key)
            if r["python_path"] == baseline.normalize_env_path(python_path)
            and r.get("official_digest") == previous.get("official_digest")
            and r.get("source_fingerprint") == previous.get("source_fingerprint")
            and all(r["versions"].get(name) == (previous.get("versions") or {}).get(name)
                    for name in env_seen.VERSIONS)]


def compare_matched(instance_path, key, python_path, event, body, reference_baseline, reference_key):
    """Matched-state compare: the reference inputs with exactly the reviewed plan
    of this transition applied (when one was staged), regenerated here."""
    reviewed = env_seen.reviewed_plan(instance_path, key, python_path, event)
    transform = None
    if reviewed and (reviewed.get("renames") or reviewed.get("removals")):
        def transform(state, wiring):
            planned = env_rename.replay(state, wiring, body, reviewed, reference_baseline)
            return planned["state"], planned["wiring"], env_rename.reviewed_note(reviewed)
    try:
        return waveform_refs.compare(instance_path, reference_key, python_path, transform=transform)
    except ValueError as exc:  # the reviewed plan no longer fits the reference inputs
        return {"status": "not_checked", "attempted": True, "rows": [], "error": str(exc),
                "message": "not compared: the reviewed changes could not be applied to the reference inputs: "
                           + str(exc)}


def check(folder, python_path, instance_path, *, context=None, compare_waveforms=True):
    """Probe, compare the same chip/env history, and optionally regenerate stored inputs."""
    folder = Path(folder)
    context = {"path": str(folder), **(context or {})}
    state, _ = safe_io.read_state_wiring(folder)
    classes = list(dict.fromkeys(obj["__class__"] for _, obj in env_upgrade.objects(state)))
    live = env_seen.live_context(context)
    probed = state_env_schema.probe_state_schema(python_path, classes, instance_path if live else None)
    current = {**probed, "schema_format": state_env_schema.SCHEMA_FORMAT}
    current = baseline.select_classes(current, classes)
    key = env_seen.chip_key(str(folder))
    if not probed.get("ok"):
        result = env_upgrade.report(state, current)
        result.update(checked_in=python_path, versions=probed.get("versions") or {})
        result["verdicts"] = env_upgrade.verdict_lines(result)
        return result
    if live:
        body_key = baseline.record_baseline(instance_path, current, python_path=python_path)
        body = baseline.load_baseline(instance_path, body_key) if body_key else None
        if body is None:
            raise ValueError("could not record schema reference")
        observation = env_seen.observe(instance_path, key, python_path, body, context=context)
        event = observation.get("current", {}).get("pending") or observation
    else:
        body = {**baseline.project_manifest(current), "version": 2}
        previous = env_seen.read(instance_path, key)["envs"].get(baseline.normalize_env_path(python_path))
        event = env_seen.transition(previous, {**body, "baseline_key": None})
    previous = event.get("previous") or {}
    old_key = previous.get("baseline_key")
    reference = baseline.load_baseline(instance_path, old_key) if old_key else None
    result = env_upgrade.report(state, current, reference,
        catalog=state_env_schema.catalog_for_env(python_path, instance_path), event=event)
    result.update(checked_in=python_path, versions=body["versions"], schema_body_key=body.get("key"),
                  reference_contents="generate_config() waveform arrays and the exact JSON files used to generate them")
    matching = matching_references(instance_path, key, python_path, previous)
    if compare_waveforms and matching and not any(result["cases"][str(n)]["rows"] for n in (2, 3, 4)):
        compared = compare_matched(instance_path, key, python_path, event, body, reference, matching[0]["key"])
        result["cases"]["6"] = {"case": 6, **compared}
        result["non_waveform"] = compared.get("non_waveform") or []
    elif not matching and previous.get("versions"):
        known = config_generator._load_probe_cache(instance_path)
        if not any(Path(path).is_file() and all(
            (entry.get("result", {}).get("versions") or {}).get(name) == previous["versions"].get(name)
            for name in env_seen.VERSIONS) for path, entry in known.items()):
            result["previous_version_note"] = ("the previous version is not installed in any environment SM knows; "
                "install it in a separate env to compare")
    result["findings_signature"] = baseline._hash({k: v["rows"] for k, v in result["cases"].items()})
    result["verdicts"] = env_upgrade.verdict_lines(result)
    return result


def snapshot(folder, python_path, instance_path, *, context=None):
    """Record only under instance; freeze JSON inputs before invoking the previewer."""
    context = {"path": str(folder), **(context or {})}
    if not env_seen.live_context(context):
        raise ValueError("reference recording needs a writable live state")
    result = check(folder, python_path, instance_path, context=context, compare_waveforms=False)
    body = baseline.load_baseline(instance_path, result.get("schema_body_key"))
    if body is None:
        raise ValueError("could not check: schema probe did not produce a reference")
    generated = waveform_refs.preview(python_path, folder, strict=True)
    if not generated.get("ok"):
        raise ValueError("could not record reference: " + (generated.get("error") or "previewer failed"))
    reference = waveform_refs.record(instance_path, env_seen.chip_key(str(folder)), python_path,
                                      body, generated, context=context)
    if reference is None:
        raise ValueError("could not record reference: input changed during loading or reference exceeds the size limit")
    return {"reference": reference, "checked_in": python_path, "basis": waveform_refs.input_basis(generated["reference_inputs"]),
            "wall_seconds": generated.get("wall_seconds"), "message": "Reference recorded"}
