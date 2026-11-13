"""A run's node.json ``state_changes`` record proves it wrote a value.

The node records each accepted change as ``{"key": "#/...", "old", "new"}``.
That is the node's own record of its write, like a leaf patch: a value it
names with exactly the saved new value is proven. A value that merely
matches (no record) stays unproven, and an older ledger is proved again once.
"""
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
