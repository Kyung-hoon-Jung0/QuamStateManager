"""docs/124 M-9/M-10 — the ONE cellsReverted entry shape.

Five emit sites (undo, journal-staged undo, both redo branches, discard-all,
the per-change ✕) used to hand-build their entry dicts and all five shipped
only the 7-sig-fig ``_fmt_val`` string, which the grids wrote into cells whose
own rendering is the lossless ``group_digits`` — the on-screen value was wrong
by the sub-kHz tail after every undo of a large float, and the truncated
string became the next edit's baseline. ``_revert_entry_payload`` is now the
one builder; these tests pin its contract so the sites cannot drift apart
again (the exact multi-site divergence class docs/124 M-9 documents).
"""

import pytest

from quam_state_manager.web.routes import _revert_entry_payload


class TestDisplayString:
    def test_disp_is_lossless_where_str_truncates(self):
        p = _revert_entry_payload("qubits.q1.f_01", 4333001234.5678)
        assert p["old_value_disp"] == "4,333,001,234.5678"
        # the inspector-input format stays what it always was
        assert p["old_value_str"] == "4.333001e+09"

    def test_int_groups(self):
        p = _revert_entry_payload("qubits.q1.f_01", 4333200000)
        assert p["old_value_disp"] == "4,333,200,000"

    def test_none_is_empty_both_ways(self):
        p = _revert_entry_payload("a.b", None)
        assert p["old_value_str"] == ""
        assert p["old_value_disp"] == ""
        assert p["old_kind"] == "null"


class TestKind:
    @pytest.mark.parametrize("value,kind", [
        ("#/qubits/q1/f_01", "pointer"),
        ("#./x180", "pointer"),
        ("0.13", "str_numeric"),
        ("4e9", "str_numeric"),
        ("deviceC_lab3", "str"),
        (True, "bool"),          # bool BEFORE num — Python bools ARE ints
        (False, "bool"),
        (3, "num"),
        (3.5, "num"),
        (None, "null"),
        ({"a": 1}, "other"),
        ([1, 2], "other"),
    ])
    def test_classification(self, value, kind):
        assert _revert_entry_payload("x.y", value)["old_kind"] == kind


class TestPassthrough:
    def test_flags_and_source(self):
        p = _revert_entry_payload("a.b", 1, created=True, deleted=False,
                                  source_file="wiring")
        assert p["dot_path"] == "a.b"
        assert p["created"] is True
        assert p["deleted"] is False
        assert p["source_file"] == "wiring"

    def test_defaults(self):
        p = _revert_entry_payload("a.b", 1)
        assert p["created"] is False and p["deleted"] is False
        assert p["source_file"] == "state"
