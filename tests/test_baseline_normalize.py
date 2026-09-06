"""docs/174 -- the diff baseline is serializer-normalized so class-default
fields don't surface as phantom node writes.

Found on the real KRISS arbel run: ``machine.save()`` materializes every field
the quam class declares that the raw state.json lacked (the KRISS class's
top-level ``flux_crosstalk_max_v`` / ``require_flux_crosstalk_dc`` /
``twpa_ext``). SM diffed ``before/`` (a raw copy of the working state) against
the scratch AFTER that save, so an UNTOUCHED node staged those three as
'created' writes -- ``twpa_ext`` even as ``None -> None``, a write that changes
nothing. The fix runs the same serializer over the PRE-node state and hands SM
that as the baseline, so the defaults appear on BOTH sides and cancel; a genuine
node write still surfaces because the baseline carries its OLD value.

These tests use a fake machine (no quam/env needed) whose ``save()`` reproduces
the real materialization, so they run in the plain ``cqt`` suite. The behaviour
was also verified against the real KRISS ``quam_config.my_quam.Quam`` in-session
(untouched node: 3 writes -> 0; genuine tof write: still 1).
"""

from __future__ import annotations

import json
from pathlib import Path

from quam_state_manager.core import agent_runs, scheduler
from quam_state_manager.generator import run_experiment as RE

LIVE_STATE = {"qubits": {"qA1": {"resonator": {"time_of_flight": 372}}}}
DEFAULTS = ("flux_crosstalk_max_v", "require_flux_crosstalk_dc", "twpa_ext")


def _write(folder: Path, state: dict, wiring: dict | None = None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring or {}), encoding="utf-8")


class FakeMachine:
    """Reproduces the real quam behaviour: ``save()`` writes back EVERY declared
    field, so fields absent from the raw state.json get materialized to their
    class default. ``twpa_ext`` materializes to ``None`` (present-but-null)."""

    def __init__(self, state_path: str | Path):
        self.state_path = Path(state_path)
        self._state = json.loads((self.state_path / "state.json").read_text(encoding="utf-8"))

    def save(self):
        self._state.setdefault("flux_crosstalk_max_v", 0.45)
        self._state.setdefault("require_flux_crosstalk_dc", False)
        self._state.setdefault("twpa_ext", None)
        (self.state_path / "state.json").write_text(json.dumps(self._state), encoding="utf-8")


def _fake_loader(monkeypatch):
    monkeypatch.setattr(RE, "_load_machine", lambda sp: FakeMachine(sp))


# --------------------------------------------------------------------------
# The fixture CAN reach the phantom state (a-vacuous-pin-passes discipline):
# without normalization an untouched node stages exactly the 3 default writes.
# --------------------------------------------------------------------------
def test_raw_baseline_leaks_three_phantom_default_writes(tmp_path):
    before = tmp_path / "before"
    scratch = tmp_path / "quam_state"
    _write(before, LIVE_STATE)          # raw copy of the working state
    _write(scratch, LIVE_STATE)
    FakeMachine(scratch).save()         # an UNTOUCHED node's machine.save()

    writes, _ = agent_runs.diff_states(before, scratch)
    created = {w["path"] for w in writes if w.get("created")}
    assert created == set(DEFAULTS), created
    # twpa_ext is the egregious one: a write whose old and new are both null.
    twpa = next(w for w in writes if w["path"] == "twpa_ext")
    assert twpa["old"] is None and twpa["new"] is None


# --------------------------------------------------------------------------
# WITH docs/174 normalization the phantom defaults cancel: 0 writes.
# --------------------------------------------------------------------------
def test_normalized_baseline_cancels_phantom_defaults(tmp_path, monkeypatch):
    _fake_loader(monkeypatch)
    before = tmp_path / "before"
    scratch = tmp_path / "quam_state"
    _write(before, LIVE_STATE)
    _write(scratch, LIVE_STATE)

    RE._materialize_baseline(str(scratch), str(before))   # normalize the baseline
    FakeMachine(scratch).save()                           # untouched node -> after

    writes, _ = agent_runs.diff_states(before, scratch)
    assert writes == [], writes
    # the baseline itself now carries the materialized defaults on disk
    base_state = json.loads((before / "state.json").read_text(encoding="utf-8"))
    assert all(k in base_state for k in DEFAULTS)


# --------------------------------------------------------------------------
# Honesty: a genuine node write still surfaces -- the baseline holds its OLD
# value, so old->new is reported, never hidden by the normalization.
# --------------------------------------------------------------------------
def test_genuine_write_survives_normalization(tmp_path, monkeypatch):
    _fake_loader(monkeypatch)
    before = tmp_path / "before"
    scratch = tmp_path / "quam_state"
    _write(before, LIVE_STATE)
    _write(scratch, LIVE_STATE)

    RE._materialize_baseline(str(scratch), str(before))
    fm = FakeMachine(scratch)                             # loads the normalized scratch
    fm._state["qubits"]["qA1"]["resonator"]["time_of_flight"] = 388
    fm.save()

    writes, _ = agent_runs.diff_states(before, scratch)
    assert len(writes) == 1, writes
    w = writes[0]
    assert w["path"] == "qubits.qA1.resonator.time_of_flight"
    assert w["old"] == 372 and w["new"] == 388
    assert not w.get("created")


# --------------------------------------------------------------------------
# A change to a default field IS a real write (not cancelled): the baseline
# holds the materialized old value, the node saves a different one.
# --------------------------------------------------------------------------
def test_a_real_change_to_a_default_field_is_not_cancelled(tmp_path, monkeypatch):
    _fake_loader(monkeypatch)
    before = tmp_path / "before"
    scratch = tmp_path / "quam_state"
    _write(before, LIVE_STATE)
    _write(scratch, LIVE_STATE)

    RE._materialize_baseline(str(scratch), str(before))
    fm = FakeMachine(scratch)
    fm._state["flux_crosstalk_max_v"] = 0.9              # a genuine change
    fm.save()

    writes, _ = agent_runs.diff_states(before, scratch)
    assert len(writes) == 1, writes
    w = writes[0]
    assert w["path"] == "flux_crosstalk_max_v"
    assert w["old"] == 0.45 and w["new"] == 0.9
    assert not w.get("created")   # it EXISTED in the baseline, so it's a change


# --------------------------------------------------------------------------
# Best-effort: a load/save failure leaves the raw before/ standing (pre-174).
# --------------------------------------------------------------------------
def test_materialize_baseline_is_best_effort(tmp_path, monkeypatch):
    monkeypatch.setattr(RE, "_load_machine", lambda sp: (_ for _ in ()).throw(RuntimeError("boom")))
    before = tmp_path / "before"
    scratch = tmp_path / "quam_state"
    _write(before, LIVE_STATE)
    _write(scratch, LIVE_STATE)

    # must not raise; before/ is untouched (still the raw copy)
    RE._materialize_baseline(str(scratch), str(before))
    assert json.loads((before / "state.json").read_text(encoding="utf-8")) == LIVE_STATE


# --------------------------------------------------------------------------
# run_target wiring: baseline normalization runs only when a baseline_out is
# given, and always before the node body (runpy).
# --------------------------------------------------------------------------
def test_run_target_calls_normalize_before_runpy(tmp_path, monkeypatch):
    target = tmp_path / "node.py"
    target.write_text("node = None\n", encoding="utf-8")
    order: list[str] = []
    monkeypatch.setattr(RE, "_materialize_baseline",
                        lambda sp, bo: order.append(f"normalize:{bo}"))
    monkeypatch.setattr(RE.runpy if hasattr(RE, "runpy") else RE, "run_path",
                        lambda *a, **k: order.append("runpy") or {}, raising=False)
    # run_target imports runpy locally, so patch the module attribute it resolves
    import runpy as _runpy
    monkeypatch.setattr(_runpy, "run_path", lambda *a, **k: order.append("runpy") or {})
    monkeypatch.setattr(RE, "_persist_node_state", lambda ns, sp: None)

    RE.run_target(str(target), str(tmp_path / "sp"), None, str(tmp_path / "before"))
    assert order and order[0].startswith("normalize:") and "runpy" in order
    assert order.index("runpy") > 0   # normalize ran first


def test_run_target_skips_normalize_without_baseline(tmp_path, monkeypatch):
    target = tmp_path / "node.py"
    target.write_text("node = None\n", encoding="utf-8")
    called = []
    monkeypatch.setattr(RE, "_materialize_baseline", lambda sp, bo: called.append(bo))
    import runpy as _runpy
    monkeypatch.setattr(_runpy, "run_path", lambda *a, **k: {})
    monkeypatch.setattr(RE, "_persist_node_state", lambda ns, sp: None)

    RE.run_target(str(target), str(tmp_path / "sp"), None, None)
    assert called == []   # no baseline_out -> no normalization


# --------------------------------------------------------------------------
# Scheduler plumbing: an agent item carries baseline_path through _new_item;
# a human item leaves it None.
# --------------------------------------------------------------------------
def test_new_item_carries_baseline_path():
    agent_item = scheduler._new_item(
        {"file": "n.py", "name": "n", "state_path": "/s", "baseline_path": "/b"}, ["qA1"])
    assert agent_item["baseline_path"] == "/b"
    human_item = scheduler._new_item({"file": "n.py", "name": "n"}, ["qA1"])
    assert human_item["baseline_path"] is None
