"""The per-env schema cache is shared by every chip opened in that env.

Opening chip B (other classes) and re-probing must never drop chip A's
classes from the env's entry, and a failed probe never overwrites it. A
class the entry does not cover is "not checked yet", never an error.
"""
import json
import sys

from quam_state_manager.core import state_env_schema as ses
from quam_state_manager.core import state_env_validate as sev

A = ["lab.CZGateTwoFlux", "lab.SNZTwoFluxPulse"]
B = ["quam.components.macro.qubit_macros.PulseMacro"]


def test_a_probe_that_answers_less_does_not_drop_what_was_there(tmp_path, monkeypatch):
    py = sys.executable
    _fake_env(monkeypatch)
    ses.probe_state_schema(py, A, tmp_path)
    _fake_env(monkeypatch, only=set(B))            # the probe answers B alone
    assert ses.probe_state_schema(py, B, tmp_path, force=True)["ok"]
    assert set(A) | set(B) <= set(_cached(tmp_path)[py]["classes"])


def _fake_env(monkeypatch, *, fail=False, only=None):
    calls = []
    monkeypatch.setattr(ses, "_env_versions", lambda py: {"quam": "0.6.0"})

    def run(argv, work_dir, timeout, outcome, **kw):
        req = json.loads((work_dir / "_classes.json").read_text(encoding="utf-8"))["classes"]
        calls.append(list(req))
        if fail:
            outcome.update(ok=False, error="timed out")
            return
        outcome.update(ok=True, result={"classes": {
            c: {"importable": True, "canonical": c, "bases": [], "fields": {}}
            for c in req if only is None or c in only}, "pulse_roster": {}})
    monkeypatch.setattr(ses, "_run_script_outcome", run)
    return calls


def _cached(tmp_path):
    return json.loads((tmp_path / ses._SCHEMA_CACHE_FILENAME).read_text(encoding="utf-8"))


def test_open_a_then_b_then_a_keeps_a_covered(tmp_path, monkeypatch):
    py = sys.executable
    calls = _fake_env(monkeypatch)
    assert ses.probe_state_schema(py, A, tmp_path)["ok"]
    # chip B, forced re-probe (Re-probe button / stale entry): A must survive
    assert ses.probe_state_schema(py, B, tmp_path, force=True)["ok"]
    assert set(A) <= set(calls[-1])                      # A re-checked with B
    cls = _cached(tmp_path)[py]["classes"]
    assert set(A) | set(B) <= set(cls)
    # back to chip A: served from the cache, no class error
    res = ses.probe_state_schema(py, A, tmp_path)
    assert res["cached"] and res["missing_classes"] == []
    state = {"__class__": A[0]}
    man = {"classes": res["classes"], "by_leaf": res["by_leaf"]}
    errs = [f for f in sev.analyze_state(state, man)["findings"] if f["severity"] == "error"]
    assert errs == []


def test_a_failed_probe_leaves_the_entry_intact(tmp_path, monkeypatch):
    py = sys.executable
    _fake_env(monkeypatch)
    ses.probe_state_schema(py, A, tmp_path)
    before = _cached(tmp_path)
    _fake_env(monkeypatch, fail=True)
    assert not ses.probe_state_schema(py, B, tmp_path, force=True)["ok"]
    assert _cached(tmp_path) == before


def test_a_class_the_env_entry_lacks_is_not_checked_not_an_error():
    man = {"classes": {}, "by_leaf": {}}
    recs = sev.analyze_state({"__class__": A[0]}, man)["findings"]
    assert recs and all(f["severity"] != "error" for f in recs), recs
    assert "not been checked" in recs[0]["detail"]
