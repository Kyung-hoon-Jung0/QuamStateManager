"""The value surfaces' one equality and the "removed" flag, pinned again.

S10 C5 deleted the old snapshot fold and with it the pin that a near-miss value is
not "the same" (TestFold::test_a_near_miss_value_is_not_history). The rule lives on
in routes._vh_same, which decides the metric meta's matches_current, By-run "changed"
and only-changed; the final review showed a 1% tolerance passing 195 tests. The
metric meta's "gone" flag (a value removed at the newest event) had no pin either."""
from __future__ import annotations

import math

from quam_state_manager.web import routes
from tests.test_hub_chip_status import META, load
from tests.test_hub_drawer import _inline, chip_state  # noqa: F401 -- _inline is autouse


def test_the_one_equality_is_exact_up_to_float_noise():
    assert routes._vh_same(5.0e9, 5.0e9)
    assert routes._vh_same(math.nan, math.nan)
    assert routes._vh_same(None, None)
    assert not routes._vh_same(5.0e9, 5.0e9 * 1.01), "a 1% near miss is a different value"
    assert not routes._vh_same(5.0e9, 5.0e9 + 1.0), "one hertz off is a different value"
    assert not routes._vh_same(0.0, None) and not routes._vh_same(None, 0.0)
    assert not routes._vh_same("a", "b")


def test_a_near_miss_current_value_does_not_match_the_newest_recorded_one(tmp_path):
    env = load(tmp_path, [(chip_state(f01=5.0e9), None), (chip_state(f01=5.1e9), None)],
               chip_state(f01=5.1e9 * 1.001))
    entry = env["client"].get(META).get_json()["q"]["f_01"]["qA1"]
    assert entry["matches_current"] is False, entry
    env2 = load(tmp_path / "same", [(chip_state(f01=5.0e9), None), (chip_state(f01=5.1e9), None)],
                chip_state(f01=5.1e9))
    entry2 = env2["client"].get(META).get_json()["q"]["f_01"]["qA1"]
    assert entry2["matches_current"] is True, entry2


def test_a_value_removed_at_the_newest_event_reads_gone(tmp_path):
    # the newest run removed T1; the live chip has it again (set outside SM, not
    # recorded yet): the metric is listed (the live chip has it) and its newest
    # recorded event is the removal
    gone = chip_state()
    del gone["qubits"]["qA1"]["T1"]
    env = load(tmp_path, [(chip_state(t1=2e-5), None), (gone, None)], chip_state(t1=2e-5))
    entry = env["client"].get(META).get_json()["q"].get("T1", {}).get("qA1")
    assert entry is not None and entry["gone"] is True, entry
    env2 = load(tmp_path / "kept", [(chip_state(t1=2e-5), None), (chip_state(t1=3e-5), None)],
                chip_state(t1=3e-5))
    entry2 = env2["client"].get(META).get_json()["q"]["T1"]["qA1"]
    assert entry2["gone"] is False, entry2
