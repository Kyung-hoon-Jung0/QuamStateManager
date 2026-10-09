"""S10 walk 2: the report's Calibration log in reasonable time.

On a copy of a 3,521-run chip the report's Calibration-log section took 47 s
cold (the walk measured 40.7 s): every day was built with every gate computed
on the spot (21 s, one saved state parsed per run), every run folder read one
after another (17 s) and the 12 MB section redacted twice (3 s). Pinned here:

* the report builds each day with the page's own builder and waits for gates
  only within ``REPORT_GATE_BUDGET_S``; past it a gate is checked in the
  background, as on the page, and the file says it was not checked yet;
* a run folder's facts (its ``data.json`` can hold megabytes the card never
  shows) are decoded once per run and kept under the instance, not once per
  process;
* the section is redacted once (its builder's widened redactor already holds
  every literal of the report's).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from quam_state_manager.core import journal, report_redact, story
from quam_state_manager.web import routes
from tests.test_calibration_log_hub import DAY, world  # noqa: F401


def _runs_with_folders(world, n=3):
    for i in range(n):
        world["add"]({"v": i + 1})
    root = Path(world["store"].conn.execute("SELECT path FROM roots").fetchone()[0])
    for (rel,) in world["store"].conn.execute("SELECT rel_path FROM events WHERE kind='run'"):
        folder = root / rel
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "node.json").write_text(json.dumps({
            "metadata": {"run_start": f"{DAY}T12:00:00-04:00", "status": "finished"},
            "data": {"parameters": {"model": {"qubits": ["qA1"]}}, "outcomes": {"qA1": "successful"}}}))
        (folder / "data.json").write_text("{}")


def _stub_gates(monkeypatch):
    calls = []

    def gate(run):
        calls.append(run.get("run_id"))
        return {"verdict": "pass", "reason": "stub gate", "family": None, "anchor": "run"}
    monkeypatch.setattr(story, "_compute_gate", gate)
    return calls


def _section(world, redact="0"):
    r = world["client"].get(f"/chip-status/report/section/calibration_log?redact={redact}")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def test_gates_within_the_budget_are_in_the_file(world, monkeypatch):
    _runs_with_folders(world)
    calls = _stub_gates(monkeypatch)
    body = _section(world)
    assert body.count("gate: pass - stub gate") == 3 and "not checked yet" not in body, body[-1500:]
    assert len(calls) == 3


def test_past_the_budget_a_gate_is_left_to_the_background_and_said(world, monkeypatch):
    _runs_with_folders(world)
    _stub_gates(monkeypatch)
    monkeypatch.setattr(routes, "REPORT_GATE_BUDGET_S", -1.0)
    body = _section(world)
    assert body.count("gate: not checked yet") == 3, body[-1500:]
    assert "gate: pass" not in body and "gate: pending" not in body
    assert ("The gates of 3 runs were not checked yet when this file was made; the Calibration log "
            "checks them in the background. Make the file again to include them.") in body


def test_a_day_build_given_a_deadline_computes_until_it_and_no_longer(world, monkeypatch):
    import time
    _runs_with_folders(world)
    _stub_gates(monkeypatch)
    build = story.build_day(world["inst"], "chipX", DAY, ds=None, ledger=world["ctx"],
                            gate_wait=time.monotonic() - 1.0)
    assert [c["gate"]["verdict"] for c in build["cards"]] == ["pending"] * 3
    assert build["gates_pending"] == 3


def _folder(tmp_path, data=None):
    folder = tmp_path / "root" / DAY / "#7_scan_120000"
    folder.mkdir(parents=True)
    (folder / "node.json").write_text(json.dumps({"metadata": {"status": "finished"}, "data": {
        "parameters": {"model": {"qubits": ["qA1"]}}}}))
    (folder / "data.json").write_text(json.dumps(data or {"figures": {"amp": "./figures.amp.png"}}))
    return folder


def test_a_runs_facts_are_decoded_once_per_run_not_once_per_process(tmp_path, monkeypatch):
    folder, inst = _folder(tmp_path), tmp_path / "inst"
    first = story._read_run_facts(str(folder), inst)
    assert first["qubits"] == ["qA1"] and first["figure_names"] == ["figures.amp"]
    assert list((inst / "story_cache" / "facts").rglob("*.json")), "kept on disk"
    story._RUN_FACTS.clear()                       # a new process: nothing in RAM
    decoded = []
    real = story._read_json_dict
    monkeypatch.setattr(story, "_read_json_dict", lambda p: decoded.append(p) or real(p))
    assert story._read_run_facts(str(folder), inst) == first and decoded == []
    (folder / "data.json").write_text(json.dumps({"figures": {"phase": "./figures.phase.png"}}))
    story._RUN_FACTS.clear()
    again = story._read_run_facts(str(folder), inst)
    assert again["figure_names"] == ["figures.phase"] and len(decoded) == 2, "a rewritten file is read again"


def test_without_an_instance_nothing_is_kept_on_disk(tmp_path):
    folder = _folder(tmp_path)
    story._read_run_facts(str(folder))
    assert not list(tmp_path.rglob("story_cache"))


def test_the_day_build_keeps_its_runs_facts_under_the_instance(world):
    _runs_with_folders(world)
    world["build"]()
    kept = list((Path(world["inst"]) / "story_cache" / "facts").rglob("*.json"))
    assert len(kept) == 3, kept


def test_the_log_section_is_redacted_once(world, monkeypatch):
    world["add"]({"value": "127.0.0.1", "v": 2}, kind="sm_apply")
    journal.append(world["inst"], "chipX", "answer from host.example.internal at C:/private/archive",
                   when=datetime.fromisoformat(f"{DAY}T01:00:00"))
    passes = []
    real = report_redact.Redactor.redact_html

    def spy(self, doc):
        passes.append(len(doc))
        return real(self, doc)
    monkeypatch.setattr(report_redact.Redactor, "redact_html", spy)
    on = _section(world, "1")
    assert len(passes) == 1, passes
    for literal in ("127.0.0.1", "host.example.internal", "C:/private/archive"):
        assert literal not in on


def test_the_background_check_starts_once_after_all_the_reports_days(world, monkeypatch):
    """A gate worker parsing saved states while the report still builds days
    slowed every later day: the report starts it once, after the last day; a
    page's day build still starts it itself."""
    _runs_with_folders(world)
    _stub_gates(monkeypatch)
    starts = []
    monkeypatch.setattr(story, "_start_gates", lambda: starts.append(1))
    _section(world)
    assert starts == [1]
    world["build"]()
    assert starts == [1, 1]
