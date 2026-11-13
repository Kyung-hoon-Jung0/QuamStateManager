"""A run's node.json ``state_changes`` record proves it wrote a value.

The node records each accepted change as ``{"key": "#/...", "old", "new"}``.
That is the node's own record of its write, like a leaf patch: a value it
names with exactly the saved new value is proven. A value that merely
matches (no record) stays unproven, and an older ledger is proved again once.
"""
import time

from quam_state_manager.core import hub_build, hub_sync
from quam_state_manager.core.hub_store import PROOF_RULE, HubStore
from tests.test_hub_build import run

KEY = "#/qubit_pairs/qA-qB/macros/cz/fidelity/InterleavedRB"


def _doc(v):
    return {"qubit_pairs": {"qA-qB": {"macros": {"cz": {"fidelity": {"InterleavedRB": v}}}}}}


def _sc(new):
    return {"state_changes": {"recorded_at": "x", "changes": [
        {"key": KEY, "attr": "InterleavedRB", "old": 0.5, "new": new}]}}


def _proven(out):
    with HubStore(out) as store:
        return [r[0] for r in store.conn.execute(
            "SELECT c.proven FROM changes c JOIN events e USING(eid) ORDER BY e.ord")]


def test_a_recorded_state_change_proves_the_write(tmp_path):
    root, out = tmp_path / "archive", tmp_path / "ledger"
    run(root, 1, _doc(0.95))
    run(root, 2, _doc(0.9073), node_extra=_sc(0.9073))
    hub_build.build(root, out)
    assert _proven(out) == [0, 1]


def test_a_value_match_without_a_record_stays_unproven(tmp_path):
    root, out = tmp_path / "archive", tmp_path / "ledger"
    run(root, 1, _doc(0.95))
    run(root, 2, _doc(0.9073))                      # no record at all
    run(root, 3, _doc(0.91), node_extra=_sc(0.92))   # recorded, another value
    hub_build.build(root, out)
    assert _proven(out) == [0, 0, 0]


def test_the_slim_node_keeps_only_key_and_new(tmp_path):
    folder = run(tmp_path / "a", 1, _doc(0.9), node_extra=_sc(0.9))
    node, err = hub_build.read_node(folder)
    assert err is None
    assert node["state_changes"] == {"changes": [{"key": KEY, "new": 0.9}]}


def test_an_older_ledger_is_proved_again_once(tmp_path):
    root, out = tmp_path / "archive", tmp_path / "ledger"
    run(root, 1, _doc(0.95))
    run(root, 2, _doc(0.9073), node_extra=_sc(0.9073))
    hub_build.build(root, out)
    with HubStore(out) as store:
        assert store.meta("proof_rule") == PROOF_RULE
        # as a ledger proved under the patches-only rule left it
        store.conn.execute("UPDATE changes SET proven=0")
        store.set_meta("proof_rule", "patches")
        store.conn.commit()
    assert _proven(out) == [0, 0]
    with HubStore(out) as store:
        with hub_sync.txn(store):
            assert hub_sync.reprove_all(store) >= 1
        assert store.meta("proof_rule") == PROOF_RULE
    assert _proven(out) == [0, 1]


# -- the proof-rule walk runs in budgeted, resumable steps ---------------

def _older_ledger(tmp_path, n=6):
    """A ledger of *n* recorded writes, then left as the patches-only rule
    left it: nothing proven, the old rule recorded."""
    root, out = tmp_path / "archive", tmp_path / "ledger"
    run(root, 1, _doc(0.5))
    for i in range(2, n + 2):
        v = 0.9 + i / 1000
        run(root, i, _doc(v), node_extra=_sc(v))
    hub_build.build(root, out)
    one = _proven(out)
    assert one == [0] + [1] * n
    with HubStore(out) as store:
        store.conn.execute("UPDATE changes SET proven=0")
        store.set_meta("proof_rule", "patches")
        store.conn.commit()
    return root, out, one


def test_a_raising_event_does_not_block_the_walk(tmp_path, monkeypatch):
    _root, out, _one = _older_ledger(tmp_path, 3)
    real = hub_sync._reprove
    with HubStore(out) as store:
        bad = store.conn.execute("SELECT eid FROM events ORDER BY ord").fetchall()[2][0]

    def _reprove(store, ev, node):
        real(store, ev, node)
        if ev["eid"] == bad:
            raise KeyError("planted")
    monkeypatch.setattr(hub_sync, "_reprove", _reprove)
    with HubStore(out) as store:
        n, finished = hub_sync.reprove_some(store)
        assert finished and n == 4       # the first run is looked at too
        assert store.meta("proof_rule") == PROOF_RULE
        assert store.meta(hub_sync.PROOF_CURSOR) is None
    # the failing event keeps its old proof (its own writes were rolled back)
    assert _proven(out) == [0, 1, 0, 1]


def test_a_budgeted_walk_resumes_from_its_cursor(tmp_path):
    _root, out, one = _older_ledger(tmp_path, 5)
    calls = 0
    with HubStore(out) as store:
        while True:
            n, finished = hub_sync.reprove_some(store, lambda: True)   # one event per step
            calls += 1
            if finished:
                break
            assert n == 1
            assert store.meta("proof_rule") == "patches", "the rule is recorded only at the end"
            assert store.meta(hub_sync.PROOF_CURSOR).startswith(PROOF_RULE + "|")
    assert calls == 7                 # six runs, then the step that finds the end
    assert _proven(out) == one


def test_a_step_stops_at_its_deadline(tmp_path, monkeypatch):
    _root, out, one = _older_ledger(tmp_path, 8)
    real = hub_sync._reprove

    def _slow(store, ev, node):
        time.sleep(0.05)
        real(store, ev, node)
    monkeypatch.setattr(hub_sync, "_reprove", _slow)
    with HubStore(out) as store:
        dl = time.monotonic() + 0.08
        n, finished = hub_sync.reprove_some(store, lambda: time.monotonic() >= dl)
        assert not finished and 1 <= n <= 3, n     # the deadline plus at most one event
        while not hub_sync.reprove_some(store, lambda: True)[1]:
            pass
    assert _proven(out) == one


def test_the_projector_slice_walks_and_never_holds_the_ledger(tmp_path):
    root, chip = tmp_path / "data", tmp_path / "chip"
    run(root, 1, _doc(0.5))
    for i in range(2, 6):
        run(root, i, _doc(0.9 + i / 1000), node_extra=_sc(0.9 + i / 1000))
    cs = hub_sync.open_chip(chip, [(str(root), "declared")], kick=False)
    with HubStore(chip) as st:
        while cs.run_slice(st, None):
            pass
        one = [r[0] for r in st.conn.execute("SELECT c.proven FROM changes c JOIN events e USING(eid) ORDER BY e.ord")]
        assert one == [0, 1, 1, 1, 1]
        st.conn.execute("UPDATE changes SET proven=0")
        st.set_meta("proof_rule", "patches")
        st.conn.commit()
        assert cs.run_slice(st, None, should_yield=lambda: True), "one event per slice while a request waits"
        assert cs.reprove_due and st.meta("proof_rule") == "patches"
        while cs.run_slice(st, None, should_yield=lambda: True):
            pass
        assert not cs.reprove_due and st.meta("proof_rule") == PROOF_RULE
        assert [r[0] for r in st.conn.execute(
            "SELECT c.proven FROM changes c JOIN events e USING(eid) ORDER BY e.ord")] == one
