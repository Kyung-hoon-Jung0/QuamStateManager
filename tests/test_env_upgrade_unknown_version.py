"""An unreadable version is unknown, not a change, and an unacknowledged
official transition survives later non-official changes.

Reproduces a real sequence: a reference recorded on quam_builder 0.4.0, an
in-place upgrade to 0.6.0, then (after a restart) an observation whose numpy
and scipy versions could not be read."""
from __future__ import annotations

import sys

from quam_state_manager.core import env_seen, state_env_schema, safe_io, waveform_refs
from quam_state_manager.core import state_env_baseline as baseline
from quam_state_manager.web import env_upgrade_view as view

PY = sys.executable
ARROW = "→"
BASE = {"quam": "0.6.0", "qualang_tools": "0.22.0", "qm": "1.4.1"}


def _manifest(quam_builder, numpy="2.5.3", scipy="1.18.1"):
    return {"schema_format": 4, "sources": {},
            "versions": {**BASE, "quam_builder": quam_builder, "numpy": numpy, "scipy": scipy},
            "classes": {"official.Parent": {
                "importable": True, "origin": "official", "bases": [], "canonical": "official.Parent",
                "signatures": {}, "fields": {"f": {"type": {"base": "int"}, "has_default": True,
                                                   "default": 1, "defining_class": "official.Parent"}}}}}


def _observe(inst, chip, manifest):
    key = baseline.record_baseline(inst, manifest, python_path=PY)
    body = baseline.load_baseline(inst, key)
    env_seen.observe(inst, chip, PY, body, context={})
    return body


def _reference(inst, chip, body, stamp):
    key = baseline._hash([chip, stamp])
    reference = {"format": waveform_refs.WAVEFORM_REF_FORMAT, "key": key, "chip_key": chip,
                 "python_path": baseline.normalize_env_path(PY),
                 "versions": {k: (body.get("versions") or {}).get(k) for k in env_seen.VERSIONS},
                 "official_digest": body.get("official_digest"),
                 "source_fingerprint": body.get("source_fingerprint"),
                 "basis": "b" * 64, "recorded_at": stamp, "files": {},
                 "traces": {"t": {}}, "other_json": []}
    directory = inst / "waveform_refs"
    directory.mkdir(parents=True, exist_ok=True)
    safe_io.atomic_write_json(directory / f"{key}.json", reference)
    return key


def _pending(inst, chip):
    return env_seen.read(inst, chip)["envs"][baseline.normalize_env_path(PY)]["pending"]


def _upgrade_then_unreadable(tmp_path):
    inst, chip = tmp_path / "instance", "chip"
    before = _observe(inst, chip, _manifest("0.4.0"))
    before_ref = _reference(inst, chip, before, "2026-10-30T16:00:00+00:00")
    after = _observe(inst, chip, _manifest("0.6.0"))
    after_ref = _reference(inst, chip, after, "2026-10-30T16:21:00+00:00")
    _observe(inst, chip, _manifest("0.6.0", numpy=None, scipy=None))
    return inst, chip, before_ref, after_ref


def test_an_unreadable_version_keeps_the_upgrade_on_the_card(tmp_path):
    inst, chip, _, _ = _upgrade_then_unreadable(tmp_path)
    pending = _pending(inst, chip)
    assert f"quam-builder 0.4.0 {ARROW} 0.6.0 (upgrade)" in pending["direction"]
    assert pending["previous"]["versions"]["quam_builder"] == "0.4.0"
    assert "None" not in pending["direction"]
    assert not any("None" in f["direction"] for f in pending.get("later_changes") or [])
    assert pending["unreadable"] == ["numpy", "scipy"]


def test_the_before_tag_sits_on_the_pre_upgrade_reference(tmp_path):
    inst, chip, before_ref, after_ref = _upgrade_then_unreadable(tmp_path)
    rows = view.present_references(inst, chip, PY, _pending(inst, chip)["previous"])
    tagged = [r["key"] for r in rows if r["is_before"]]
    assert tagged == [before_ref]


def test_unknown_alone_is_not_a_change():
    known = {"versions": {**BASE, "quam_builder": "0.6.0", "numpy": "2.5.3"}}
    unknown = {"versions": {**BASE, "quam_builder": "0.6.0", "numpy": None}}
    for a, b in ((known, unknown), (unknown, known)):
        event = env_seen.transition(a, b)
        assert not event["changed"] and not event["official_changed"] and event["direction"] == ""


def test_a_later_numpy_change_is_folded_in_not_a_new_anchor(tmp_path):
    inst, chip = tmp_path / "instance", "chip"
    _observe(inst, chip, _manifest("0.4.0"))
    _observe(inst, chip, _manifest("0.6.0"))
    _observe(inst, chip, _manifest("0.6.0", numpy="2.6.0"))
    pending = _pending(inst, chip)
    assert pending["previous"]["versions"]["quam_builder"] == "0.4.0"
    assert [f["direction"] for f in pending["later_changes"]] == [
        f"numpy 2.5.3 {ARROW} 2.6.0 (upgrade)"]


def test_a_cache_hit_answers_the_library_versions(tmp_path, monkeypatch):
    """The cold probe reports numpy/scipy; a cache hit after a restart must
    report the same set rather than the narrower metadata key."""
    inst = tmp_path / "instance"
    key_versions = {**BASE, "quam_builder": "0.6.0", "quam_builder_commit": None}
    library = {**BASE, "quam_builder": "0.6.0", "numpy": "2.5.3", "scipy": "1.18.1"}
    monkeypatch.setattr(state_env_schema, "_env_versions", lambda p: dict(key_versions))
    monkeypatch.setattr(state_env_schema, "_entry_fresh", lambda entry, modules: True)
    monkeypatch.setattr(state_env_schema, "_touch_signature", lambda i, p: None)
    entry = {"format": state_env_schema.SCHEMA_FORMAT, "versions": key_versions,
             "library_versions": library, "classes": {"a.B": {"importable": True, "fields": {}}},
             "pulse_roster": {}}
    monkeypatch.setattr(state_env_schema, "_load_cache", lambda i: {PY: entry})
    result = state_env_schema.probe_state_schema(PY, ["a.B"], inst)
    assert result["cached"] and result["versions"]["numpy"] == "2.5.3"
    assert result["versions"]["scipy"] == "1.18.1"


def test_a_restart_after_a_cold_probe_still_reads_numpy(tmp_path, monkeypatch):
    """Cold probe, then a cache hit on the same instance dir (a restart)."""
    inst = tmp_path / "instance"
    inst.mkdir()
    key_versions = {**BASE, "quam_builder": "0.6.0", "quam_builder_commit": None}
    library = {**BASE, "quam_builder": "0.6.0", "numpy": "2.5.3", "scipy": "1.18.1"}
    monkeypatch.setattr(state_env_schema, "_env_versions", lambda p: dict(key_versions))
    monkeypatch.setattr(state_env_schema, "load_pulse_modules", lambda i: [])

    def fake_run(argv, work_dir, timeout, outcome, **kw):
        outcome.update(ok=True, result={"versions": library, "classes": {
            "a.B": {"importable": True, "fields": {}}}, "pulse_roster": {}})
    monkeypatch.setattr(state_env_schema, "_run_script_outcome", fake_run)
    cold = state_env_schema.probe_state_schema(PY, ["a.B"], inst)
    assert not cold["cached"] and cold["versions"]["numpy"] == "2.5.3"
    monkeypatch.setattr(state_env_schema, "_run_script_outcome",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no subprocess on a hit")))
    warm = state_env_schema.probe_state_schema(PY, ["a.B"], inst)
    assert warm["cached"] and warm["versions"]["numpy"] == "2.5.3" and warm["versions"]["scipy"] == "1.18.1"


def test_the_cached_only_manifest_answers_the_library_versions(tmp_path, monkeypatch):
    library = {**BASE, "quam_builder": "0.6.0", "numpy": "2.5.3", "scipy": "1.18.1"}
    entry = {"format": state_env_schema.SCHEMA_FORMAT, "versions": {**BASE, "quam_builder": "0.6.0"},
             "library_versions": library, "signature": "sig",
             "classes": {"a.B": {"importable": True, "fields": {}}}, "pulse_roster": {}}
    monkeypatch.setattr(state_env_schema, "_load_cache", lambda i: {PY: entry})
    monkeypatch.setattr(state_env_schema, "_env_signature", lambda p: "sig")
    monkeypatch.setattr(state_env_schema, "_entry_fresh", lambda e, m: True)
    monkeypatch.setattr(state_env_schema, "load_pulse_modules", lambda i: [])
    monkeypatch.setattr(state_env_schema, "_harvest_for_store", lambda store: ["a.B"])
    manifest = state_env_schema.manifest_for_store(object(), PY, tmp_path, cached_only=True)
    assert manifest["versions"]["numpy"] == "2.5.3"
