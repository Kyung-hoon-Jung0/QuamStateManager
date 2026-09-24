"""The per-field Auto-Sync verdict.

The rule these pin: an external write to a field the user did NOT touch is not
a conflict and must pull silently; an external write to the SAME field is, and
must ask. Everything else is about not lying in the silent direction.
"""

import pytest

from quam_state_manager.core.sync_conflict import (
    Verdict, classify, covers, originals_from_change_log)


class _Entry:
    """The shape classify() reads off a ChangeEntry."""

    def __init__(self, dot_path, old_value, *, created=False, deleted=False):
        self.dot_path = dot_path
        self.old_value = old_value
        self.created = created
        self.deleted = deleted


class TestTheCustomersTwoCases:
    def test_a_different_field_is_not_a_conflict(self):
        # The user edited q1.T1; an experiment wrote q2.T1. Nothing collides,
        # so Auto-Sync adopts it without a word — the whole point of arming it.
        v = classify(
            live_by_path={"qubits.q2.T1": 2.2e-05},
            change_log=[_Entry("qubits.q1.T1", 1.29e-05)],
        )
        assert v.conflicts == ()
        assert v.may_pull_silently is True
        assert v.external == ("qubits.q2.T1",)

    def test_the_same_field_is_a_conflict(self):
        # The user edited q1.T1 from 1.29e-05; the live chip now says 3.3e-05,
        # which is not where the edit started — so the other writer touched it.
        v = classify(
            live_by_path={"qubits.q1.T1": 3.3e-05},
            change_log=[_Entry("qubits.q1.T1", 1.29e-05)],
        )
        assert v.conflicts == ("qubits.q1.T1",)
        assert v.may_pull_silently is False
        assert v.must_ask is True

    def test_both_at_once_asks_about_only_the_collision(self):
        v = classify(
            live_by_path={"qubits.q1.T1": 3.3e-05, "qubits.q2.T1": 2.2e-05},
            change_log=[_Entry("qubits.q1.T1", 1.29e-05)],
        )
        assert v.conflicts == ("qubits.q1.T1",)
        assert v.external == ("qubits.q2.T1",)


class TestOnlyTheUserMovedIt:
    def test_my_own_edit_is_not_a_conflict_with_itself(self):
        # W and L disagree at q1.T1 because the USER changed it and nobody else
        # did: live still holds the original. Asking here would reproduce the
        # exact prompt-on-every-edit behaviour this replaces.
        v = classify(
            live_by_path={"qubits.q1.T1": 1.29e-05},     # == the original
            change_log=[_Entry("qubits.q1.T1", 1.29e-05)],
        )
        assert v.conflicts == ()
        assert v.may_pull_silently is True

    def test_they_wrote_the_same_value_i_did_is_not_a_conflict(self):
        # If live agrees with the working copy the path is not in the diff at
        # all, so there is nothing to collide over.
        v = classify(live_by_path={}, change_log=[_Entry("qubits.q1.T1", 1.29e-05)])
        assert v.conflicts == ()
        assert v.may_pull_silently is True

    def test_the_earliest_original_is_the_one_compared(self):
        # Three edits to one field still started from ONE original, and that is
        # what the live chip must be judged against. Using the latest old_value
        # (the user's own previous value) would call every repeat edit a conflict.
        log = [_Entry("qubits.q1.T1", 1.0), _Entry("qubits.q1.T1", 2.0),
               _Entry("qubits.q1.T1", 3.0)]
        assert originals_from_change_log(log) == {"qubits.q1.T1": 1.0}
        v = classify(live_by_path={"qubits.q1.T1": 1.0}, change_log=log)
        assert v.conflicts == ()


class TestSubtrees:
    def test_a_deleted_subtree_collides_with_a_change_beneath_it(self):
        v = classify(
            live_by_path={"qubits.q1.T1": 5.0},
            change_log=[_Entry("qubits.q1", {"T1": 1.0}, deleted=True)],
        )
        assert v.conflicts == ("qubits.q1",)

    def test_a_created_subtree_collides_the_same_way(self):
        v = classify(
            live_by_path={"qubits.q9.T1": 5.0},
            change_log=[_Entry("qubits.q9", None, created=True)],
        )
        assert v.conflicts == ("qubits.q9",)

    def test_a_subtree_elsewhere_does_not_collide(self):
        v = classify(
            live_by_path={"qubits.q2.T1": 5.0},
            change_log=[_Entry("qubits.q1", None, deleted=True)],
        )
        assert v.conflicts == ()
        assert v.may_pull_silently is True

    def test_covers_is_about_path_segments_not_string_prefixes(self):
        assert covers("qubits.q1", "qubits.q1.T1") is True
        assert covers("qubits.q1", "qubits.q1") is True
        # "qubits.q1" must NOT cover "qubits.q10" — the classic prefix bug
        assert covers("qubits.q1", "qubits.q10") is False
        assert covers("qubits.q1", "qubits.q10.T1") is False


class TestDirtTheServerCannotSee:
    def test_a_typed_but_uncommitted_cell_the_live_chip_moved_is_a_conflict(self):
        # The DOM cell is not in the working copy, so a disagreement at that
        # path is entirely the other writer's — a conflict by construction.
        v = classify(live_by_path={"qubits.q1.f_01": 5e9},
                     dom_paths=["qubits.q1.f_01"])
        assert v.conflicts == ("qubits.q1.f_01",)

    def test_a_typed_cell_elsewhere_is_not(self):
        v = classify(live_by_path={"qubits.q2.T1": 1.0},
                     dom_paths=["qubits.q1.f_01"])
        assert v.conflicts == ()
        assert v.may_pull_silently is True


class TestTheHonestyRule:
    def test_saved_but_unapplied_edits_forbid_a_silent_pull(self):
        # The save journalled and cleared the change log, so the originals are
        # gone. Guessing "no conflict" here would destroy work with no prompt.
        v = classify(live_by_path={"qubits.q2.T1": 1.0}, working_dirty=True)
        assert v.may_pull_silently is False
        assert v.unaccounted and "saved edits" in v.unaccounted

    def test_a_mid_merge_stash_forbids_it_too(self):
        v = classify(live_by_path={"qubits.q2.T1": 1.0},
                     reapply_paths=["qubits.q1.T1"])
        assert v.may_pull_silently is False
        assert v.unaccounted is not None

    def test_working_dirty_with_a_live_change_log_still_classifies(self):
        # working_dirty is also set on the apply path, where the log is intact.
        # The originals are there, so the verdict is exact and must not be
        # thrown away — otherwise every apply re-arms the old blanket prompt.
        v = classify(
            live_by_path={"qubits.q2.T1": 2.0},
            change_log=[_Entry("qubits.q1.T1", 1.0)],
            working_dirty=True,
        )
        assert v.unaccounted is None
        assert v.may_pull_silently is True

    def test_nothing_anywhere_is_trivially_silent(self):
        assert classify(live_by_path={}).may_pull_silently is True

    def test_an_empty_verdict_is_silent_by_default(self):
        assert Verdict().may_pull_silently is True


class TestAStashWithItsOriginals:
    """QA liveedit-r2-05 (review): a stash path whose pre-edit value was
    recorded when it was stashed is judged like a change-log edit, so the
    same-field gate can run on the doors that replay a stash."""

    def test_only_i_moved_it_is_not_a_conflict(self):
        # saved working copy holds mine (2.0); live still has the original 1.0
        v = classify(live_by_path={"a.x": 1.0}, reapply_paths=["a.x"],
                     reapply_originals={"a.x": 1.0}, working_dirty=True)
        assert v.conflicts == ()

    def test_the_chip_moved_it_too_is_a_conflict(self):
        v = classify(live_by_path={"a.x": 7.0}, reapply_paths=["a.x"],
                     reapply_originals={"a.x": 1.0}, working_dirty=True)
        assert v.conflicts == ("a.x",)

    def test_the_stash_original_beats_a_later_log_one(self):
        # saved (log cleared), edited again: the log's original is MY saved
        # value, the sync point is the stash's
        v = classify(live_by_path={"a.x": 1.0},
                     change_log=[_Entry("a.x", 2.0)],
                     reapply_paths=["a.x"], reapply_originals={"a.x": 1.0})
        assert v.conflicts == ()

    def test_without_an_original_the_old_rule_stands(self):
        v = classify(live_by_path={"a.x": 1.0}, reapply_paths=["a.x"],
                     reapply_originals={"b.y": 1.0})
        assert v.conflicts == ("a.x",)

    def test_the_honesty_rule_is_unchanged(self):
        v = classify(live_by_path={"b.y": 1.0}, reapply_paths=["a.x"],
                     reapply_originals={"a.x": 1.0}, working_dirty=True)
        assert v.may_pull_silently is False and "saved edits" in v.unaccounted


class TestTheReport:
    def test_paths_are_sorted_so_a_message_reads_the_same_twice(self):
        v = classify(
            live_by_path={"b.x": 1, "a.x": 1, "c.x": 1},
            change_log=[_Entry("b.x", 0), _Entry("a.x", 0)],
        )
        assert v.conflicts == ("a.x", "b.x")
        assert v.external == ("c.x",)

    def test_mine_lists_every_kind_of_dirt(self):
        v = classify(live_by_path={}, change_log=[_Entry("a.x", 0)],
                     dom_paths=["b.y"], reapply_paths=["c.z"])
        assert v.mine == ("a.x", "b.y", "c.z")


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
