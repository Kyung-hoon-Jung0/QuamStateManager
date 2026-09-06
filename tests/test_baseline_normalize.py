"""docs/174 (amended) -- the node subprocess strips class-default root keys so
the scratch SM adopts matches the chip's own schema and never diverges live.

Found on the real KRISS arbel chain: ``machine.save()`` writes back EVERY field
the quam class declares, so it adds top-level ROOT keys the customer's
state.json never had (the KRISS class's ``flux_crosstalk_max_v`` /
``require_flux_crosstalk_dc`` / ``twpa_ext``). The first docs/174 fix only
cancelled these in SM's DIFF -- but SM's post-run adopt copies the scratch's FULL
state into the working copy (byte-identical, then to live), so the phantom roots
still reached live and diverged it -> ``stale_live`` on the very next node.

The amended fix removes them at the source: ``_strip_phantom_roots`` deletes any
top-level key that the chip did not originally have AND the node's
``state_updates`` did not write. Verified in-session against the real
``quam_config.my_quam.Quam`` (a node saving materialized 3 root defaults; after
the strip the scratch matched the pristine chip schema and the next node did not
refuse). These tests operate on files only (no quam/env needed) so they run in
the plain ``cqt`` suite.
"""

from __future__ import annotations

import json
from pathlib import Path

from quam_state_manager.core import agent_runs
from quam_state_manager.generator import run_experiment as RE

LIVE_STATE = {"qubits": {"qA1": {"resonator": {"time_of_flight": 372}}}}
PHANTOMS = {"flux_crosstalk_max_v": 0.45, "require_flux_crosstalk_dc": False, "twpa_ext": None}


def _write(folder: Path, state: dict, wiring: dict | None = None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state, indent=4), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring or {}), encoding="utf-8")


def _read(folder: Path) -> dict:
    return json.loads((folder / "state.json").read_text(encoding="utf-8"))


def _save_adds_phantoms(folder: Path, extra_state: dict | None = None) -> None:
    """Reproduce machine.save(): rewrite state.json with EVERY declared field,
    so the class-default root keys get materialized alongside the node's writes."""
    state = _read(folder)
    if extra_state:
        state.update(extra_state)
    state.update(PHANTOMS)
    (folder / "state.json").write_text(json.dumps(state, indent=4), encoding="utf-8")


# --------------------------------------------------------------------------
# _state_root_keys: the pre-save snapshot of the chip's own top-level keys.
# --------------------------------------------------------------------------
def test_state_root_keys_reads_top_level(tmp_path):
    scratch = tmp_path / "quam_state"
    _write(scratch, LIVE_STATE)
    assert RE._state_root_keys(str(scratch)) == {"qubits"}


def test_state_root_keys_none_on_unreadable(tmp_path):
    assert RE._state_root_keys(str(tmp_path / "nope")) is None


# --------------------------------------------------------------------------
# The fixture CAN reach the phantom state (a-vacuous-pin-passes discipline):
# without the strip, save() leaves 3 phantom roots that the diff reports.
# --------------------------------------------------------------------------
def test_raw_save_leaks_three_phantom_roots(tmp_path):
    before = tmp_path / "before"
    scratch = tmp_path / "quam_state"
    _write(before, LIVE_STATE)
    _write(scratch, LIVE_STATE)
    _save_adds_phantoms(scratch)                     # an UNTOUCHED node's save()

    assert set(PHANTOMS) <= set(_read(scratch).keys())
    writes, _ = agent_runs.diff_states(before, scratch)
    created = {w["path"] for w in writes if w.get("created")}
    assert created == set(PHANTOMS), created


# --------------------------------------------------------------------------
# The strip removes exactly the phantom roots the chip never had.
# --------------------------------------------------------------------------
def test_strip_removes_phantom_roots(tmp_path):
    scratch = tmp_path / "quam_state"
    _write(scratch, LIVE_STATE)
    _save_adds_phantoms(scratch)

    RE._strip_phantom_roots(str(scratch), {"qubits"}, updates={})

    state = _read(scratch)
    assert set(state.keys()) == {"qubits"}
    assert all(k not in state for k in PHANTOMS)
    # the chip's own data is untouched
    assert state["qubits"]["qA1"]["resonator"]["time_of_flight"] == 372


# --------------------------------------------------------------------------
# After the strip, the diff is genuine writes only -- the whole cascade
# (diff, adopt, live) is now phantom-free.
# --------------------------------------------------------------------------
def test_diff_after_strip_is_writes_only(tmp_path):
    before = tmp_path / "before"
    scratch = tmp_path / "quam_state"
    _write(before, LIVE_STATE)
    _write(scratch, LIVE_STATE)
    # node writes a real leaf AND save() materializes phantoms
    node_state = {"qubits": {"qA1": {"resonator": {"time_of_flight": 388}}}}
    _save_adds_phantoms(scratch, extra_state=node_state)

    RE._strip_phantom_roots(str(scratch), {"qubits"}, updates={})

    writes, _ = agent_runs.diff_states(before, scratch)
    assert len(writes) == 1, writes
    w = writes[0]
    assert w["path"] == "qubits.qA1.resonator.time_of_flight"
    assert w["old"] == 372 and w["new"] == 388


# --------------------------------------------------------------------------
# Honesty: a genuine NEW root key the node actually wrote survives the strip,
# because its ref is in state_updates.
# --------------------------------------------------------------------------
def test_strip_keeps_a_genuinely_written_new_root(tmp_path):
    scratch = tmp_path / "quam_state"
    _write(scratch, LIVE_STATE)
    _save_adds_phantoms(scratch, extra_state={"network": {"host": "10.1.1.6"}})
    updates = {"u0": {"key": "#/network/host", "new": "10.1.1.6"}}

    RE._strip_phantom_roots(str(scratch), {"qubits"}, updates=updates)

    state = _read(scratch)
    assert "network" in state              # genuinely written -> kept
    assert state["network"]["host"] == "10.1.1.6"
    assert all(k not in state for k in PHANTOMS)   # phantoms still stripped


# --------------------------------------------------------------------------
# Best-effort: no captured baseline -> no strip, no crash (pre-174 behaviour).
# --------------------------------------------------------------------------
def test_strip_is_a_noop_when_original_roots_unknown(tmp_path):
    scratch = tmp_path / "quam_state"
    _write(scratch, LIVE_STATE)
    _save_adds_phantoms(scratch)

    RE._strip_phantom_roots(str(scratch), None, updates={})   # must not raise
    assert set(PHANTOMS) <= set(_read(scratch).keys())        # left as-is


# --------------------------------------------------------------------------
# run_target wiring: original roots are captured BEFORE runpy and handed to
# _persist_node_state.
# --------------------------------------------------------------------------
def test_run_target_captures_roots_before_runpy(tmp_path, monkeypatch):
    scratch = tmp_path / "sp"
    _write(scratch, LIVE_STATE)
    target = tmp_path / "node.py"
    target.write_text("node = None\n", encoding="utf-8")

    order: list = []
    monkeypatch.setattr(RE, "_state_root_keys",
                        lambda sp: order.append("capture") or {"qubits"})
    import runpy as _runpy
    monkeypatch.setattr(_runpy, "run_path", lambda *a, **k: order.append("runpy") or {})
    captured = {}
    monkeypatch.setattr(RE, "_persist_node_state",
                        lambda ns, sp, roots=None: captured.update(roots=roots) or order.append("persist"))

    RE.run_target(str(target), str(scratch), None)
    assert order == ["capture", "runpy", "persist"]   # capture strictly before runpy
    assert captured["roots"] == {"qubits"}


# --------------------------------------------------------------------------
# _persist_node_state calls the strip after machine.save() with the roots.
# --------------------------------------------------------------------------
def test_persist_strips_after_save(tmp_path, monkeypatch):
    scratch = tmp_path / "sp"
    _write(scratch, LIVE_STATE)

    calls: list = []

    class FakeMachine:
        def save(self):
            calls.append("save")

    class FakeNode:
        machine = FakeMachine()
        state_updates = {}

    monkeypatch.setattr(RE, "_strip_phantom_roots",
                        lambda sp, roots, updates: calls.append(("strip", roots)))

    RE._persist_node_state({"node": FakeNode()}, str(scratch), original_roots={"qubits"})
    assert calls == ["save", ("strip", {"qubits"})]   # strip AFTER save


# --------------------------------------------------------------------------
# docs/174 amended II: the qualibrate config's [quam] state_path is repointed
# at the scratch so a node never writes live directly.
# --------------------------------------------------------------------------
def test_config_pinned_to_scratch_repoints_state_path(tmp_path):
    cfg = tmp_path / "qualibrate_config.toml"
    cfg.write_text(
        '[qualibrate.storage]\n'
        'type = "local_storage"\n'
        'location = "D:/data/KRISS"\n'
        '[quam]\n'
        'state_path = "D:/live/kriss"\n'
        '[quam.serialization]\n'
        'action = "serialize"\n', encoding="utf-8")
    scratch = tmp_path / "agent_runs" / "k" / "quam_state"
    scratch.mkdir(parents=True)

    out = RE._config_pinned_to_scratch(str(cfg), str(scratch))
    assert Path(out) != cfg                      # a NEW file, original untouched
    text = Path(out).read_text(encoding="utf-8")
    assert f'state_path = "{str(scratch).replace(chr(92), "/")}"' in text
    assert 'location = "D:/data/KRISS"' in text  # storage.location left alone
    assert text.count("state_path =") == 1       # only the one key rewritten


def test_config_pin_returns_original_when_no_state_path(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('[qualibrate.storage]\nlocation = "D:/d"\n', encoding="utf-8")
    scratch = tmp_path / "sp"; scratch.mkdir()
    assert RE._config_pinned_to_scratch(str(cfg), str(scratch)) == str(cfg)


def test_config_pin_is_best_effort(tmp_path):
    scratch = tmp_path / "sp"; scratch.mkdir()
    missing = str(tmp_path / "nope.toml")
    assert RE._config_pinned_to_scratch(missing, str(scratch)) == missing   # no raise
