"""Operator notes on a qubit, a pair, or any parameter (docs/167).

The feature's whole value is that a note outlives the person who wrote it, so
the pins are mostly about what must NEVER happen to one: it is not deleted
because its subject vanished, not overwritten because two windows raced, not
moved because an identity ladder healed, and not written into the customer's
state.json at all.

Two of these started life as pins that could not fail — a design review caught
both — and the comments on them say what the fix was, because the shape of the
mistake repeats:
  * a chip name staged into the WORKING COPY does not change what the LIVE
    files say, so a test that stages one and expects the key to move is testing
    nothing;
  * the user-facing load path is `sync_if_clean=False` (docs/87), so editing a
    live file behind the app and re-loading does NOT change `store.merged`.
"""

from __future__ import annotations

import json
import threading

import pytest

from quam_state_manager.core import entity_notes, working_copy


@pytest.fixture
def chip(tmp_path):
    live = tmp_path / "quam_state"
    live.mkdir()
    (live / "state.json").write_text(json.dumps({
        "qubits": {"q1": {"id": "q1", "T1": 1e-5},
                   "q12": {"id": "q12", "T1": 2e-5}},
        "qubit_pairs": {"q1-2": {"id": "q1-2"}},
    }), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps({"wiring": {}}), encoding="utf-8")
    return {"inst": str(tmp_path / "inst"), "live": str(live)}


# ---------------------------------------------------------------------------
# Where it lives
# ---------------------------------------------------------------------------

class TestTheSidecar:
    def test_a_note_never_touches_the_chip(self, chip):
        before = (chip["live"] + "/state.json")
        with open(before, encoding="utf-8") as f:
            original = f.read()
        entity_notes.save(chip["inst"], chip["live"], "qubits.q12",
                          "flux line contact is suspect")
        with open(before, encoding="utf-8") as f:
            assert f.read() == original, "state.json must be byte-unchanged"

    def test_it_keys_on_the_folder_not_the_identity_ladder(self, chip):
        """The decision, pinned so it has to be re-argued rather than drifted.

        The ladder is designed to re-key and heal — it adopts a directory by
        extras.chip_name and can return a not-yet-existing dir. Correct for a
        snapshot store; wrong for a note, whose text must be exactly as stable
        as the folder the user is looking at.
        """
        path = entity_notes.notes_path(chip["inst"], chip["live"])
        assert path.name == working_copy.key_for(chip["live"]) + ".json"
        assert path.parent.name == "annotations"

    def test_setting_a_chip_name_in_the_live_file_does_not_move_the_key(self, chip):
        """A rename must not orphan every note on the chip.

        This is the pin a review rebuilt: the first version STAGED
        extras.chip_name into the working copy, which does not change what the
        live files say, so it could not have failed either way. The name is
        written to the live state.json here, and the key is read before and
        after.
        """
        entity_notes.save(chip["inst"], chip["live"], "qubits.q12", "suspect")
        before = entity_notes.notes_path(chip["inst"], chip["live"])

        state_file = chip["live"] + "/state.json"
        with open(state_file, encoding="utf-8") as f:
            data = json.load(f)
        data["extras"] = {"chip_name": "PJ_RENAMED"}
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

        after = entity_notes.notes_path(chip["inst"], chip["live"])
        assert after == before, "key_for must not read state.json"
        assert entity_notes.load(chip["inst"], chip["live"])["qubits.q12"]["text"] \
            == "suspect"

    def test_a_corrupt_sidecar_is_an_empty_map_not_a_crash(self, chip):
        path = entity_notes.notes_path(chip["inst"], chip["live"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        assert entity_notes.load(chip["inst"], chip["live"]) == {}
        # and a write over it still works
        entity_notes.save(chip["inst"], chip["live"], "qubits.q1", "hello")
        assert entity_notes.load(chip["inst"], chip["live"])["qubits.q1"]["text"] == "hello"

    def test_the_folder_and_the_chip_token_are_recorded(self, chip):
        """Not used yet, and recorded anyway: an "adopt the notes written for
        <old folder>" offer is impossible for notes written before the fields
        exist."""
        entity_notes.save(chip["inst"], chip["live"], "qubits.q1", "x",
                          chip_token="tok123")
        raw = json.loads(
            entity_notes.notes_path(chip["inst"], chip["live"]).read_text(encoding="utf-8"))
        assert raw["live_folder"] == chip["live"]
        assert raw["chip_token"] == "tok123"


# ---------------------------------------------------------------------------
# Addressing and roll-up
# ---------------------------------------------------------------------------

class TestAddressing:
    @pytest.mark.parametrize("subject,entity", [
        ("qubits.q12", "qubits.q12"),
        ("qubits.q12.T1", "qubits.q12"),
        ("qubits.q12.xy.operations.x180.length", "qubits.q12"),
        ("qubit_pairs.q1-2.gates.CZ.amplitude", "qubit_pairs.q1-2"),
        ("", ""),
        ("qubits", "qubits"),
    ])
    def test_entity_of(self, subject, entity):
        assert entity_notes.entity_of(subject) == entity

    def test_row_marks_key_the_way_each_grid_keys_its_rows(self):
        """`entity_of` returns a DOT-PATH; the grids key rows on the bare id.

        A review caught the mismatch: a marker test of `row.id in note_rows`
        would never have matched a two-segment path. And two maps rather than
        one, because a qubit and a pair could share an id string and the two
        tables would mark each other's rows.
        """
        notes = {
            "qubits.q12": {"text": "flux suspect"},
            "qubits.q12.T1": {"text": "refit this"},
            "qubit_pairs.q1-2": {"text": "coupler drifts"},
        }
        qubits, pairs = entity_notes.row_marks(notes)
        assert set(qubits) == {"q12"}
        assert set(pairs) == {"q1-2"}
        assert "+1 more" in qubits["q12"], "two notes on one entity are counted"

    def test_a_leaf_note_lights_its_entity(self):
        qubits, _ = entity_notes.row_marks({"qubits.q7.T1": {"text": "refit"}})
        assert qubits == {"q7": "refit"}


# ---------------------------------------------------------------------------
# Orphans — visible, never dropped
# ---------------------------------------------------------------------------

class TestOrphans:
    MERGED = {"qubits": {"q1": {"T1": 1e-5, "xy": {"operations": {"x180": "#./x180_Drag"}}}}}

    def test_a_vanished_subject_is_flagged_not_deleted(self):
        notes = {"qubits.q1.T1": {"text": "a"}, "qubits.q9.T1": {"text": "b"}}
        got = entity_notes.classify(self.MERGED, notes)
        assert got["qubits.q1.T1"]["orphan"] is False
        assert got["qubits.q9.T1"]["orphan"] is True
        assert got["qubits.q9.T1"]["text"] == "b", "the text is never touched"

    def test_with_no_readable_chip_nothing_is_stamped(self):
        """A panel that declared every note orphaned because no chip was open
        would be worse than one that says nothing about orphan-ness."""
        got = entity_notes.classify(None, {"qubits.q9.T1": {"text": "b"}})
        assert "orphan" not in got["qubits.q9.T1"]

    def test_a_pointer_subject_counts_as_present(self):
        """SM stores pointer strings as real leaves, so a note on an alias
        survives whether or not the pointer resolves. Following the pointer
        here would silently re-address the note the user wrote."""
        got = entity_notes.classify(
            self.MERGED, {"qubits.q1.xy.operations.x180": {"text": "alias"}})
        assert got["qubits.q1.xy.operations.x180"]["orphan"] is False

    def test_readdress_moves_the_text_and_its_history(self, chip):
        entity_notes.save(chip["inst"], chip["live"], "qubits.qOLD", "still true")
        moved = entity_notes.readdress(chip["inst"], chip["live"],
                                       "qubits.qOLD", "qubits.q12")
        notes = entity_notes.load(chip["inst"], chip["live"])
        assert "qubits.qOLD" not in notes
        assert notes["qubits.q12"]["text"] == "still true"
        assert notes["qubits.q12"]["created_at"] == moved["created_at"]
        assert notes["qubits.q12"]["rev"] == 2

    def test_readdress_refuses_to_clobber_an_existing_note(self, chip):
        entity_notes.save(chip["inst"], chip["live"], "qubits.qOLD", "a")
        entity_notes.save(chip["inst"], chip["live"], "qubits.q12", "b")
        with pytest.raises(ValueError):
            entity_notes.readdress(chip["inst"], chip["live"],
                                   "qubits.qOLD", "qubits.q12")
        assert entity_notes.load(chip["inst"], chip["live"])["qubits.q12"]["text"] == "b"


# ---------------------------------------------------------------------------
# Concurrency — what it buys, and what it does not
# ---------------------------------------------------------------------------

class TestConcurrency:
    def test_two_windows_noting_different_qubits_both_survive(self, chip):
        """The overwhelmingly likely collision. The write re-reads the file
        INSIDE the lock and applies only its own subject, so a whole-file
        replace cannot lose the other one."""
        errors = []

        def writer(i):
            try:
                entity_notes.save(chip["inst"], chip["live"], f"qubits.q{i}", f"note {i}")
            except Exception as exc:                      # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(entity_notes.load(chip["inst"], chip["live"])) == 12

    def test_a_stale_rev_is_refused_with_the_other_text(self, chip):
        entity_notes.save(chip["inst"], chip["live"], "qubits.q1", "mine")
        entity_notes.save(chip["inst"], chip["live"], "qubits.q1", "theirs")   # rev 2
        with pytest.raises(entity_notes.NoteConflict) as exc:
            entity_notes.save(chip["inst"], chip["live"], "qubits.q1",
                              "mine again", expect_rev=1)
        assert exc.value.stored["text"] == "theirs", (
            "the refusal must carry THEIR text so the caller can show it")
        assert entity_notes.load(chip["inst"], chip["live"])["qubits.q1"]["text"] == "theirs"

    def test_no_rev_means_write_regardless(self, chip):
        """Deliberately a separate decision from passing one — the two-token
        discipline docs/120 established."""
        entity_notes.save(chip["inst"], chip["live"], "qubits.q1", "a")
        entity_notes.save(chip["inst"], chip["live"], "qubits.q1", "b")
        assert entity_notes.load(chip["inst"], chip["live"])["qubits.q1"]["text"] == "b"

    def test_rev_increments_and_created_at_does_not_move(self, chip):
        """`created_at` is stamped to the SECOND, so two writes in one test run
        share it whatever the code does — the first version of this pin was
        vacuous for exactly that reason, and a mutation sweep said so. The
        stored timestamp is aged first, so the assertion has something to lose.
        """
        first = entity_notes.save(chip["inst"], chip["live"], "qubits.q1", "a")
        path = entity_notes.notes_path(chip["inst"], chip["live"])
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["notes"]["qubits.q1"]["created_at"] = "2020-01-01T00:00:00+00:00"
        path.write_text(json.dumps(raw), encoding="utf-8")

        second = entity_notes.save(chip["inst"], chip["live"], "qubits.q1", "b")
        assert second["rev"] == first["rev"] + 1
        assert second["created_at"] == "2020-01-01T00:00:00+00:00", (
            "an edit must not restamp when the note was first written")
        assert second["updated_at"] != second["created_at"]


class TestRefusals:
    def test_an_empty_subject_or_text_is_refused(self, chip):
        for subject, text in (("", "x"), ("qubits.q1", ""), ("  ", "x")):
            with pytest.raises(ValueError):
                entity_notes.save(chip["inst"], chip["live"], subject, text)

    def test_a_giant_note_is_refused_rather_than_truncated(self, chip):
        with pytest.raises(ValueError):
            entity_notes.save(chip["inst"], chip["live"], "qubits.q1",
                              "x" * (entity_notes.MAX_TEXT + 1))
        assert entity_notes.load(chip["inst"], chip["live"]) == {}

    def test_deleting_a_note_that_is_not_there_is_false_not_an_error(self, chip):
        assert entity_notes.delete(chip["inst"], chip["live"], "qubits.qX") is False


# ---------------------------------------------------------------------------
# The advisory half of "value locking" — and why it is only advisory
# ---------------------------------------------------------------------------

class TestHandTuned:
    """A real lock was DEFERRED, and this is what shipped instead.

    The claim "every write path must respect it" is not just true, it is
    understated: thirteen write paths, five of which replace the tree wholesale
    and cannot honour a per-path rule; undo and discard write through
    `_revert_entry` and never see a `set_value` gate at all. And the actor that
    actually overwrites a hand-tuned flux point is the lab's own calibration
    node — a process SM spawns but does not mediate. A padlock that walks
    through is WORSE than no padlock, because people stop checking it.

    So the mark promises exactly what it delivers: a sentence in the
    confirmations that already exist.
    """

    def test_the_flag_is_stored_and_defaults_off(self, chip):
        plain = entity_notes.save(chip["inst"], chip["live"], "qubits.q1", "a")
        assert plain["hand_tuned"] is False
        marked = entity_notes.save(chip["inst"], chip["live"], "qubits.q12", "b",
                                   hand_tuned_flag=True)
        assert marked["hand_tuned"] is True
        assert entity_notes.hand_tuned(
            entity_notes.load(chip["inst"], chip["live"])) == ["qubits.q12"]

    def test_it_survives_a_readdress(self, chip):
        entity_notes.save(chip["inst"], chip["live"], "qubits.qOLD", "b",
                          hand_tuned_flag=True)
        entity_notes.readdress(chip["inst"], chip["live"], "qubits.qOLD", "qubits.q12")
        assert entity_notes.hand_tuned(
            entity_notes.load(chip["inst"], chip["live"])) == ["qubits.q12"]

    def test_an_old_sidecar_without_the_field_reads_as_unmarked(self, chip):
        path = entity_notes.notes_path(chip["inst"], chip["live"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "notes": {
            "qubits.q1": {"text": "written before the flag existed", "rev": 1}}}),
            encoding="utf-8")
        assert entity_notes.hand_tuned(
            entity_notes.load(chip["inst"], chip["live"])) == []

    @pytest.mark.parametrize("subject,path,hit", [
        # a write to a leaf under a marked entity
        ("qubits.q12", "qubits.q12.T1", True),
        # a write that replaces the parent of a marked leaf
        ("qubits.q12.T1", "qubits.q12", True),
        ("qubits.q12.T1", "qubits.q12.T1", True),
        # neighbours are not hits
        ("qubits.q12", "qubits.q1", False),
        ("qubits.q12", "qubits.q120.T1", False),
        ("qubits.q12.T1", "qubits.q12.T2echo", False),
    ])
    def test_touches_is_generous_but_not_sloppy(self, subject, path, hit):
        """It decides what a CONFIRMATION mentions: naming one path too many
        costs a sentence, missing one costs the whole point of the mark. The
        prefix test is on a dotted boundary, so q12 never matches q120."""
        got = entity_notes.touches([subject], [path])
        assert bool(got) is hit, got

    def test_touches_takes_the_empties_calmly(self):
        assert entity_notes.touches([], ["qubits.q1"]) == []
        assert entity_notes.touches(["qubits.q1"], []) == []
        assert entity_notes.touches(["qubits.q1"], [None, ""]) == []
