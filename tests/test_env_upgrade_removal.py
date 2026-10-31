"""Reviewed removal of fields the environment no longer declares, and one place for
the schema facts on a transition."""
import copy

from quam_state_manager.core import env_rename
from quam_state_manager.web import routes


def _planned(state):
    return {"state": copy.deepcopy(state), "wiring": {}, "moves": [], "rewrites": [],
            "skipped": [], "selections": [], "signature": ""}


def test_removal_drops_values_and_skips_a_referenced_leaf():
    state = {"pairs": {"p1": {"__class__": "x.Pair", "old_drive": {"amp": 0.1}},
                       "p2": {"__class__": "x.Pair", "old_drive": {"amp": 0.2}}},
             "qubits": {"q1": {"amp": "#/pairs/p2/old_drive/amp"}}}
    planned = env_rename.add_removals(_planned(state), [{"paths": ["pairs.p1.old_drive", "pairs.p2.old_drive"]}])
    assert [r["path"] for r in planned["removals"]] == ["pairs.p1.old_drive"]
    assert planned["removals"][0]["value"] == {"amp": 0.1}
    assert "old_drive" not in planned["state"]["pairs"]["p1"]
    assert "old_drive" in planned["state"]["pairs"]["p2"]
    assert planned["skipped"] == [{"path": "pairs.p2.old_drive", "reason": "referenced by qubits.q1.amp"}]
    assert planned["signature"]


class _Modifier:
    def __init__(self):
        import threading
        self.store = type("S", (), {"_lock": threading.RLock()})()
        self.calls = []

    def new_group_id(self):
        return "g1"

    def delete_subtree(self, path, *, group_id=None):
        self.calls.append((path, group_id))
        return path


def test_removal_applies_as_one_undo_group():
    planned = {"rewrites": [], "moves": [], "removals": [{"path": "a.b"}, {"path": "c.d"}]}
    modifier = _Modifier()
    env_rename.apply(modifier, planned)
    assert modifier.calls == [("a.b", "g1"), ("c.d", "g1")]


def test_transition_with_findings_makes_the_card_own_schema_facts():
    report = {"event": {"changed": True, "first": False},
              "cases": {"3": {"rows": [{"class": "x.Pair", "field": "old_drive"}]}}}
    assert routes._env_upgrade_owns_schema({"env_upgrade_report": report})
    assert not routes._env_upgrade_owns_schema({"env_upgrade_report": {**report, "event": {"changed": False}}})
    assert not routes._env_upgrade_owns_schema({"env_upgrade_report": {**report, "event": {"changed": True, "first": True}}})
    rows, acked = routes._env_rows_unacked({"env_upgrade_report": report},
                                           {"env_findings": [{"kind": "unknown_field"}]})
    assert rows == [] and acked == 0
