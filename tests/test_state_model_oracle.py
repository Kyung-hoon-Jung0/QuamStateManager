"""The independent state oracle follows the shipped journal contracts."""
import pytest

from tests.state_model import oracle as O


@pytest.mark.parametrize("outside", [False, True])
def test_saved_units_own_the_edit_and_wholesale_stays_below_them(outside):
    m = O.Model({"v": 1})
    m.stage_entries([O.Entry("v", 1, 2)])
    assert m.save().kind == "saved"
    if outside:
        m.outside({"v": 7})
    assert m.apply(force=True, expect_fresh=True).kind == "landed"
    assert [u.effect() for u in m.J] == ([{"v": (7, 1)}] if outside else []) + [{"v": (1, 2)}]
    assert m.cursor == m.pcursor == len(m.J)
    assert m.walk_undo().kind == "live"
    assert m.L == {"v": 1}
    if outside:
        assert m.walk_undo().kind == "live"
        assert m.L == {"v": 7}
    assert m.walk_undo().kind == "noop"


@pytest.mark.parametrize("door", ["take_live", "stage", "restore_live"])
def test_discarded_saved_units_are_skipped_without_losing_applied_units(door):
    m = O.Model({"v": 1})
    m.stage_entries([O.Entry("v", 1, 2)])
    assert m.apply().kind == "landed"
    m.stage_entries([O.Entry("v", 2, 3)])
    m.save()
    if door == "take_live":
        m.take_live(force=True)
    elif door == "stage":
        m.stage({"v": 2}, force=True)
    else:
        m.restore_live({"v": 2}, force_pending=True)
    assert m.J[-1].discarded
    assert m.walk_undo().kind == ("staged" if door == "stage" else "live")
    assert m.W == {"v": 1}
    assert m.cursor == 0
    assert m.pcursor == (2 if door == "stage" else 0)
    assert len(m.J) == 2


def test_landed_saved_units_are_not_reused_by_the_next_wholesale_write():
    m = O.Model({"v": 1})
    m.stage_entries([O.Entry("v", 1, 2)])
    m.save()
    m.apply(force=True, expect_fresh=True)
    m.outside({"v": 7})
    assert m.apply(force=True, expect_fresh=True).kind == "landed"
    assert [u.effect() for u in m.J] == [{"v": (1, 2)}, {"v": (7, 2)}]


def test_wholesale_write_after_undo_replaces_the_redo_tail():
    m = O.Model({"v": 1})
    for old, new in ((1, 2), (2, 3)):
        m.stage_entries([O.Entry("v", old, new)])
        m.apply()
    assert m.walk_undo().kind == "live"
    m.stage({"v": 4}, force=True)
    assert m.apply().kind == "landed"
    assert [u.effect() for u in m.J] == [{"v": (1, 2)}, {"v": (2, 4)}]
    assert m.cursor == m.pcursor == 2


def test_stale_confirmation_is_remembered_until_a_successful_noop_apply():
    m = O.Model({"v": 1})
    assert m.apply(force=True, expect_fresh=False).kind == "refused"
    m.stage_entries([O.Entry("v", 1, 2)])
    assert m.verdict() == "refused"
    assert m.refused_at == O.digest(m.B)
    m.discard_all()
    assert m.apply().kind == "noop"
    assert m.L == {"v": 1} and m.J == []
    m.stage_entries([O.Entry("v", 1, 3)])
    assert m.verdict() == "mine"


def test_journal_inverse_restores_text_verbatim_after_type_assignment():
    m = O.Model({"v": "0.12"})
    m.typefixed.add("v")
    m.stage_entries([O.Entry("v", "0.12", 0.12)])
    m.save()
    assert m.walk_undo().kind == "staged"
    assert m.W == {"v": "0.12"}
    assert (m.cursor, m.pcursor) == (0, 1)
