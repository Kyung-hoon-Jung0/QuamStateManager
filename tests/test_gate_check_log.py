"""The per-chip record of the last lab check of each CZ gate."""
from __future__ import annotations

from pathlib import Path

import pytest

from quam_state_manager.core import gate_check_log as log
from tests.cz_gate_chip import add_gate, chip


def test_missing_file_reads_as_no_record(tmp_path):
    assert log.read(tmp_path / "nope") == {}
    assert log.entry(tmp_path, "p", "g") is None


def test_unreadable_file_reads_as_no_record(tmp_path):
    (tmp_path / log.FILE_NAME).write_text("{not json", encoding="utf-8")
    assert log.read(tmp_path) == {}
    (tmp_path / log.FILE_NAME).write_text('{"gates": [1, 2]}', encoding="utf-8")
    assert log.read(tmp_path) == {}


def test_record_then_read_one_entry_per_gate(tmp_path):
    log.record(tmp_path, "q1-q2", "cz_a", result=log.CHECKED, reason=None, content="h1")
    log.record(tmp_path, "q1-q2", "cz_a", result=log.NOT_CHECKED, reason="no env",
               content="h2")
    log.record(tmp_path, "q2-q3", "cz_a", result=log.CHECKED, reason=None, content="h3")
    got = log.read(tmp_path)
    assert set(got) == {"q1-q2/cz_a", "q2-q3/cz_a"}
    assert got["q1-q2/cz_a"]["result"] == log.NOT_CHECKED
    assert got["q1-q2/cz_a"]["reason"] == "no env" and got["q1-q2/cz_a"]["hash"] == "h2"
    assert not list(tmp_path.glob(".gate_checks.*.tmp"))


def test_record_refuses_an_unknown_result(tmp_path):
    with pytest.raises(ValueError):
        log.record(tmp_path, "p", "g", result="maybe", reason=None, content=None)


def test_result_of_a_check():
    assert log.result_of(None) == (log.NOT_CHECKED, "no check ran")
    assert log.result_of({"all_checked": True}) == (log.CHECKED, None)
    assert log.result_of({"all_checked": False, "why_not_checked": "busy"}) == (
        log.NOT_CHECKED, "busy")


def test_record_check_uses_the_history_chip_folder(tmp_path):
    class HM:
        def resolve_chip_dir(self, path):
            return tmp_path / "hist" / Path(path).name, "k", "path", None
    s, _ = add_gate(chip())
    ent = log.record_check(HM(), tmp_path / "chipA", s, "q1-q2", "cz_sq", {"all_checked": True})
    assert ent["result"] == log.CHECKED
    assert ent["hash"] == log.content_hash(s, "q1-q2", "cz_sq")
    assert log.entry(tmp_path / "hist" / "chipA", "q1-q2", "cz_sq") == ent
