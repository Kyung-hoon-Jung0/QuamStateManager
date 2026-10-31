"""Pins for the final-review must-fix items of the environment upgrade report.

Each test reproduces the failure scenario the review named, through the real
modules (observation record, schema baselines, reference files, the card)."""
from __future__ import annotations

import copy
import json
import re
import sys
import threading

import pytest

from quam_state_manager.core import env_seen, env_upgrade, env_upgrade_service, safe_io, waveform_refs
from quam_state_manager.core import state_env_baseline as baseline
from quam_state_manager.web.app import create_app
from quam_state_manager.web import env_upgrade_view as view, routes

PY = sys.executable
ARROW = "\u2192"


def _manifest(quam_builder, *, renamed=False, sources=None):
    field = {"type": {"base": "int"}, "has_default": True, "default": 1,
             "defining_class": "official.Parent"}
    name = "new" if renamed else "old"
    return {"schema_format": 4, "sources": sources or {},
            "versions": {"quam": "0.6.0", "quam_builder": quam_builder},
            "classes": {
                "official.Parent": {"importable": True, "origin": "official", "bases": [],
                                    "canonical": "official.Parent", "signatures": {},
                                    "fields": {name: copy.deepcopy(field)}},
                "extension.Child": {"importable": True, "origin": "lab", "bases": ["official.Parent"],
                                    "canonical": "extension.Child", "signatures": {},
                                    "fields": {name: copy.deepcopy(field)}}}}


def _observe(inst, chip, manifest):
    key = baseline.record_baseline(inst, manifest, python_path=PY)
    body = baseline.load_baseline(inst, key)
    env_seen.observe(inst, chip, PY, body, context={})
    return body


def _write_reference(inst, chip, body, stamp, *, size_pad=0, files=None):
    key = baseline._hash([chip, stamp])
    reference = {"format": waveform_refs.WAVEFORM_REF_FORMAT, "key": key, "chip_key": chip,
                 "python_path": baseline.normalize_env_path(PY),
                 "versions": {k: (body.get("versions") or {}).get(k) for k in env_seen.VERSIONS},
                 "official_digest": body.get("official_digest"),
                 "source_fingerprint": body.get("source_fingerprint"),
                 "basis": "b" * 64, "recorded_at": stamp, "files": files or {},
                 "traces": {"t": {"pad": "x" * size_pad}}, "other_json": []}
    directory = inst / "waveform_refs"
    directory.mkdir(parents=True, exist_ok=True)
    safe_io.atomic_write_json(directory / f"{key}.json", reference)
    return key


def _pending(inst, chip):
    return env_seen.read(inst, chip)["envs"][baseline.normalize_env_path(PY)]["pending"]


# -- P1-1 ---------------------------------------------------------------------

def test_a_lab_file_saved_after_an_upgrade_keeps_the_upgrade_as_the_anchor(tmp_path):
    inst, chip = tmp_path / "instance", "chip"
    before = _observe(inst, chip, _manifest("0.4.0"))
    ref_key = _write_reference(inst, chip, before, "2026-10-01T00:00:00+00:00")
    _observe(inst, chip, _manifest("0.6.0", renamed=True))
    # the usual reaction to a failing import: the lab saves its own module
    lab = _observe(inst, chip, _manifest("0.6.0", renamed=True,
                                          sources={"D:/lab/extension.py": [1, 2]}))
    pending = _pending(inst, chip)
    assert pending["official_changed"]
    assert f"quam-builder 0.4.0 {ARROW} 0.6.0 (upgrade)" in pending["direction"]
    assert pending["previous"]["versions"]["quam_builder"] == "0.4.0"
    assert [f["direction"] for f in pending["later_changes"]] == ["same version strings, source files changed"]
    # case 3 still offers the rename, measured against the pre-upgrade schema
    reference = baseline.load_baseline(inst, pending["previous"]["baseline_key"])
    report = env_upgrade.report({"__class__": "extension.Child", "old": 1}, lab, reference, event=pending)
    row = report["cases"]["3"]["rows"][0]
    assert row["was_declared"] and [c["field"] for c in row["candidates"]] == ["new"]
    # and the reference recorded before the upgrade still matches
    found = env_upgrade_service.matching_references(inst, chip, PY, pending["previous"])
    assert [r["key"] for r in found] == [ref_key]


def test_another_chips_lab_module_does_not_move_this_chips_fingerprint():
    plain = baseline.project_manifest(_manifest("0.6.0"))
    other = baseline.project_manifest(_manifest("0.6.0", sources={"D:/lab/other_module.py": [1, 2]}))
    own = baseline.project_manifest(_manifest("0.6.0", sources={"D:\\lab\\extension.py": [1, 2]}))
    assert plain["source_fingerprint"] == other["source_fingerprint"]
    assert own["source_fingerprint"] != plain["source_fingerprint"]


def test_acknowledge_clears_pending_but_keeps_the_body_protected(tmp_path):
    inst, chip = tmp_path / "instance", "chip"
    _observe(inst, chip, _manifest("0.4.0"))
    body = _observe(inst, chip, _manifest("0.6.0", renamed=True))
    pending = _pending(inst, chip)
    assert env_seen.acknowledge(inst, chip, PY, pending["previous"], body, "sig", context={},
                                event={**pending, "stops_loading": True})
    entry = env_seen.read(inst, chip)["envs"][baseline.normalize_env_path(PY)]
    assert "pending" not in entry
    assert pending["previous"]["baseline_key"] in baseline._protected_keys(inst)
    assert env_seen.acknowledged(inst, chip, PY, pending["previous"], body, "sig")
    _observe(inst, chip, _manifest("0.6.0", renamed=True))
    assert "pending" not in env_seen.read(inst, chip)["envs"][baseline.normalize_env_path(PY)]


@pytest.mark.parametrize("old,new,label", [("0.4.0", "0.6.0", "upgrade"), ("1.10", "1.9", "downgrade"),
                                           ("0.4", "0.4.0rc1", "version changed"),
                                           ("main", "0.4.0", "version changed")])
def test_direction_needs_no_third_party_package(old, new, label):
    text = env_seen.direction({"versions": {"quam": old}}, {"versions": {"quam": new}})
    assert text.endswith(f"({label})")


def test_env_seen_does_not_import_packaging():
    import inspect
    assert "packaging" not in inspect.getsource(env_seen)


# -- P1-2 / P1-3 (rendered card) -----------------------------------------------

@pytest.fixture
def loaded(tmp_path):
    folder = tmp_path / "state"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps({"qubits": {}}), encoding="utf-8")
    (folder / "wiring.json").write_text("{}", encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    client = app.test_client()
    client.post("/load", data={"folder": str(folder)})
    ctx = app.config["contexts"][app.config["active_context"]]
    return app, client, ctx


def _card_ctx(ctx, case6, previous=None):
    report = env_upgrade.report({"qubits": {}}, {"schema_format": 4, "classes": {}}, None,
                                event={"changed": True, "first": False, "previous": previous or {}})
    for n in range(1, 6):
        report["cases"][str(n)] = {"case": n, "rows": [], "status": "none_found", "message": "none found"}
    report["cases"]["6"] = case6
    ctx["env_upgrade_report"] = report
    ctx["env_upgrade_report_seq"] = ctx["store"].mutation_seq
    ctx["env_upgrade_env"] = PY
    ctx["env_upgrade_body"] = {"versions": {"quam_builder": "0.6.0"}}


def test_the_summary_never_says_n_of_m_differ(loaded):
    _, client, ctx = loaded
    rows = [{"element": "q", "operation": f"x{i}", "channel": "I", "two_qubit": False, "label": label}
            for i, label in enumerate(["differs"] * 3 + ["bit-identical"] * 40 + ["not comparable"] * 5)]
    _card_ctx(ctx, {"case": 6, "rows": rows, "status": "found", "message": "m"})
    html = client.get("/env-upgrade/card").get_data(as_text=True)
    summary = re.search(r'env-wave-summary"><span class="ui-dot"[^>]*></span><strong class="ui-verdict-key">(.*?)</strong>', html).group(1)
    assert summary == "3 differ \u00b7 40 identical \u00b7 5 not comparable"
    assert not re.search(r"\d+ of \d+ differ", html)


def test_the_compared_env_is_labelled_with_the_versions_it_ran(loaded, monkeypatch):
    _, client, ctx = loaded
    reference = {"format": waveform_refs.WAVEFORM_REF_FORMAT, "files": {}, "basis": "a" * 64,
                 "traces": {}, "config_non_waveform": {}}
    monkeypatch.setattr(waveform_refs, "load", lambda inst, key: reference)
    monkeypatch.setattr(waveform_refs, "preview", lambda *a, **k: {
        "ok": True, "result": {"config": {}, "versions": {"quam_builder": "0.4.0"}},
        "reference_inputs": {"state.json": "e30="}})
    compared = waveform_refs.compare("inst", "k", PY)
    assert compared["versions"] == {"quam_builder": "0.4.0"}
    compared["rows"] = [{"element": "q", "operation": "x", "channel": "I", "two_qubit": False,
                         "label": "differs"}]
    compared["current_env"] = "older-env"
    _card_ctx(ctx, {"case": 6, **compared})
    html = client.get("/env-upgrade/card").get_data(as_text=True)
    line = re.search(r'class="env-state-hashes ui-note"[^>]*>(.*?)</p>', html, re.S).group(1)
    assert "<code class=\"ui-chip\">older-env</code> (quam_builder 0.4.0)" in line and "0.6.0" not in line


# -- P1-5 ------------------------------------------------------------------------

def test_listing_fifty_references_reads_the_index_not_the_bodies(tmp_path, monkeypatch):
    inst = tmp_path / "instance"
    body = {"versions": {"quam_builder": "0.6.0"}, "official_digest": "d", "source_fingerprint": "f"}
    for i in range(50):
        _write_reference(inst, "chip" if i % 2 else "other", body, f"2026-10-{1 + i % 28:02d}T{i % 24:02d}:00:00+00:00")
    waveform_refs._index_memo.clear()
    assert len(waveform_refs.list_references(inst, "chip")) == 25        # cold: parses each once
    reads = []
    original = safe_io.read_json
    monkeypatch.setattr(safe_io, "read_json", lambda path, *a, **k: (reads.append(str(path)), original(path, *a, **k))[1])
    for _ in range(3):
        listed = waveform_refs.list_references(inst, "chip")
    assert reads == [] and len(listed) == 25 and "traces" not in listed[0]
    waveform_refs._index_memo.clear()                                   # a new process
    assert len(waveform_refs.list_references(inst, "chip")) == 25
    assert reads == [str(waveform_refs._index_path(inst))]


def test_retention_keeps_the_newest_and_the_transitions_previous_reference(tmp_path):
    inst, chip = tmp_path / "instance", "chip"
    before = _observe(inst, chip, _manifest("0.4.0"))
    after = _observe(inst, chip, _manifest("0.6.0", renamed=True))
    blob = "c" * 64
    (inst / "waveform_refs" / "inputs").mkdir(parents=True)
    safe_io.atomic_write_json(inst / "waveform_refs" / "inputs" / f"{blob}.json", {"bytes": "e30="})
    anchor = _write_reference(inst, chip, before, "2026-09-01T00:00:00+00:00", files={"state.json": blob})
    newer = [_write_reference(inst, chip, after, f"2026-10-{d:02d}T00:00:00+00:00") for d in range(1, 11)]
    removed = waveform_refs.prune(inst, chip, PY, keep=newer[-1])
    kept = {r["key"] for r in waveform_refs.list_references(inst, chip)}
    assert anchor in kept                       # the 0.4.0 reference the pending transition needs
    assert set(newer[-waveform_refs.MAX_REFERENCES_PER_ENV:]) <= kept
    assert set(removed) == set(newer[:-waveform_refs.MAX_REFERENCES_PER_ENV])
    assert (inst / "waveform_refs" / "inputs" / f"{blob}.json").exists()
    # once nothing anchors on it, the old basis and its input blob go
    env_seen.acknowledge(inst, chip, PY, _pending(inst, chip)["previous"], after, "s", context={},
                         event={**_pending(inst, chip), "stops_loading": True})
    record = env_seen.read(inst, chip)
    record["envs"][baseline.normalize_env_path(PY)].pop("acknowledged_event")
    safe_io.atomic_write_json(env_seen.record_path(inst, chip), record)
    assert anchor in waveform_refs.prune(inst, chip, PY)
    assert not (inst / "waveform_refs" / "inputs" / f"{blob}.json").exists()


# -- P2s -------------------------------------------------------------------------

def test_card_predicate_agrees_with_the_compare_predicate(loaded):
    app, client, ctx = loaded
    inst = app.instance_path
    chip = env_seen.chip_key(ctx["path"])
    previous = {"versions": {"quam_builder": "0.4.0"}, "official_digest": "old", "source_fingerprint": "f"}
    _card_ctx(ctx, {"case": 6, "rows": [], "status": "not_checked", "message": ""}, previous)
    _write_reference(__import__("pathlib").Path(inst), chip,
                     {**previous, "official_digest": "different"}, "2026-10-01T00:00:00+00:00")
    html = client.get("/env-upgrade/card").get_data(as_text=True)
    assert env_upgrade_service.matching_references(inst, chip, PY, previous) == []
    assert "press Check to run it" not in html
    assert "different class definitions or source files" in html


@pytest.mark.parametrize("action,body", [("check", "abc"), ("rename-test", {"selections": "abc"}),
                                         ("compare", {"reference": ["x"]})])
def test_a_body_of_the_wrong_shape_is_a_400(loaded, action, body):
    _, client, _ = loaded
    assert client.post(f"/env-upgrade/{action}", json=body).status_code == 400


def test_a_failed_reference_ride_says_why(loaded, monkeypatch):
    app, _, ctx = loaded

    class Now(threading.Thread):
        def start(self):
            self.run()

    def boom(*a, **k):
        raise RuntimeError("previewer exploded")

    monkeypatch.setattr(routes.threading, "Thread", Now)
    monkeypatch.setattr(waveform_refs, "preview", boom)
    ctx["env_upgrade_body"] = {"versions": {}, "official_digest": "d", "source_fingerprint": "f"}
    ctx["env_upgrade_env"] = PY
    routes._cfg_warm_failed.clear()
    assert routes._warm_generated_config_async(ctx, app.instance_path, reference_only=True) == "started"
    assert ctx["env_reference_status"] == "could not record reference: RuntimeError: previewer exploded"
