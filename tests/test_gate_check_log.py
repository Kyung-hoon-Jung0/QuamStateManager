"""The per-chip record of the last lab check of each CZ gate."""
from __future__ import annotations

import json
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
    assert len(got) == 2
    e = log.entry(tmp_path, "q1-q2", "cz_a")
    assert e["result"] == log.NOT_CHECKED
    assert e["reason"] == "no env" and e["hash"] == "h2"
    assert not list(tmp_path.glob(".gate_checks.*.tmp"))


def test_record_refuses_an_unknown_result(tmp_path):
    with pytest.raises(ValueError):
        log.record(tmp_path, "p", "g", result="maybe", reason=None, content=None)


CASES = json.loads((Path(__file__).parent / "fixtures" / "gate_outcome_cases.json")
                   .read_text(encoding="utf-8"))["cases"]


def _oc(name):
    c = CASES[name]
    return log.outcome(c["result"], c["result"]["gates"][0], reasons=c["reasons"])


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_shared_cases_come_to_their_outcome(name):
    assert _oc(name) == CASES[name]["outcome"]


def test_outcome_wording_for_the_three_cases():
    assert _oc("checked")["summary"] == "every check passed."
    assert _oc("partly")["summary"] == ("5 checks passed, 1 could not run: preview comparison "
                                        "(no preview for this pulse class).")
    assert _oc("failed")["kind"] == log.KIND_FAILED
    assert _oc("none")["kind"] == log.KIND_NONE


def test_a_failed_check_still_counts_the_checks_that_passed():
    """One failing of six: "1 failed, 5 passed", never "1 failed, 0 passed"."""
    oc = _oc("failed")
    assert (len(oc["failed"]), oc["passed"], oc["total"]) == (1, 5, 6)
    # a check that could not run beside the failure is not counted as passed
    c = json.loads(json.dumps(CASES["failed"]))
    gate = c["result"]["gates"][0]
    gate["unchecked"] = ["preview"]
    oc = log.outcome(c["result"], gate)
    assert (len(oc["failed"]), oc["passed"]) == (1, 4)


def test_result_of_a_check():
    assert log.result_of(None) == (log.NOT_CHECKED, "no check ran")
    assert log.result_of(_oc("checked")) == (log.CHECKED, None)
    assert log.result_of(_oc("partly")) == (
        log.PARTLY, "preview comparison (no preview for this pulse class)")
    assert log.result_of(_oc("failed"))[0] == log.NOT_CHECKED
    assert log.result_of(_oc("none")) == (log.NOT_CHECKED, "No Python environment is selected.")
    # a raw check result is judged by the same outcome
    r = CASES["partly"]["result"]
    assert log.result_of(r, r["gates"][0])[0] == log.PARTLY


@pytest.mark.parametrize("name,pill", [("checked", "checked"), ("partly", "checked, 1 not run"),
                                       ("failed", "not checked"), ("none", "not checked")])
def test_the_status_row_reads_the_same_outcome(tmp_path, name, pill):
    from quam_state_manager.core import gate_status

    class HM:
        def resolve_chip_dir(self, path):
            return tmp_path / "hist", "k", "path", None
    s, _ = add_gate(chip())
    rec = log.record_check(HM(), tmp_path / "chipA", s, "q1-q2", "cz_sq", _oc(name))
    assert rec["ok"]
    item = gate_status._check(tmp_path / "hist", s, "q1-q2", "cz_sq")
    assert item["text"] == pill
    if name == "partly":
        assert "no preview for this pulse class" in item["why"]
    assert "every check passed" not in item["why"]


def test_record_check_uses_the_history_chip_folder(tmp_path):
    class HM:
        def resolve_chip_dir(self, path):
            return tmp_path / "hist" / Path(path).name, "k", "path", None
    s, _ = add_gate(chip())
    rec = log.record_check(HM(), tmp_path / "chipA", s, "q1-q2", "cz_sq", _oc("checked"))
    assert rec["ok"]
    ent = rec["entry"]
    assert ent["result"] == log.CHECKED
    assert ent["hash"] == log.content_hash(s, "q1-q2", "cz_sq")
    assert log.entry(tmp_path / "hist" / "chipA", "q1-q2", "cz_sq") == ent


def test_pair_and_gate_cannot_collide(tmp_path):
    log.record(tmp_path, "a/b", "c", result=log.NOT_CHECKED, reason="one", content="h")
    log.record(tmp_path, "a", "b/c", result=log.NOT_CHECKED, reason="two", content="h")
    assert log.entry(tmp_path, "a/b", "c")["reason"] == "one"
    assert log.entry(tmp_path, "a", "b/c")["reason"] == "two"


def test_an_old_file_still_reads(tmp_path):
    (tmp_path / log.FILE_NAME).write_text(
        '{"gates": {"q1-q2/cz_a": {"result": "checked", "hash": "h"}}}', encoding="utf-8")
    assert log.entry(tmp_path, "q1-q2", "cz_a")["hash"] == "h"


def test_checked_needs_the_hash(tmp_path):
    with pytest.raises(ValueError):
        log.record(tmp_path, "p", "g", result=log.CHECKED, reason=None, content=None)


def test_read_none_and_unwritable_places(tmp_path):
    assert log.read(None) == {} and log.entry(None, "p", "g") is None
    a_file = tmp_path / "f"
    a_file.write_text("x", encoding="utf-8")
    with pytest.raises(log.RecordError):
        log.record(a_file, "p", "g", result=log.NOT_CHECKED, reason="r", content="h")
    (tmp_path / "d" / log.FILE_NAME).mkdir(parents=True)
    with pytest.raises(log.RecordError):
        log.record(tmp_path / "d", "p", "g", result=log.NOT_CHECKED, reason="r", content="h")

    class HM:
        def resolve_chip_dir(self, path):
            return a_file, "k", "path", None
    rec = log.record_check(HM(), tmp_path, {}, "p", "g", None)
    assert rec["ok"] is False and rec["error"]


def _both(tmp_path, t_old, t_new):
    import json
    gates = {"q1-q2/cz_a": {"result": "checked", "hash": "old", "time": t_old},
             json.dumps(["q1-q2", "cz_a"]): {"result": "checked", "hash": "new", "time": t_new}}
    (tmp_path / log.FILE_NAME).write_text(json.dumps({"gates": gates}), encoding="utf-8")
    return log.entry(tmp_path, "q1-q2", "cz_a")["hash"]


def test_old_and_new_key_the_later_time_wins(tmp_path):
    assert _both(tmp_path, "2026-11-14T10:00:00+00:00", "2026-11-14T09:00:00+00:00") == "old"
    assert _both(tmp_path, "2026-11-14T09:00:00+00:00", "2026-11-14T10:00:00+00:00") == "new"
    assert _both(tmp_path, "2026-11-14T10:00:00+00:00", "2026-11-14T10:00:00+00:00") == "new"
    assert _both(tmp_path, "garbage", "2026-11-14T10:00:00+00:00") == "new"


def test_naive_against_aware_time_is_unreadable_and_the_new_key_wins(tmp_path):
    assert _both(tmp_path, "2026-11-14T10:00:00", "2026-11-14T09:00:00+00:00") == "new"
    assert _both(tmp_path, None, "2026-11-14T09:00:00+00:00") == "new"


def test_a_record_carries_the_rule_version(tmp_path):
    from quam_state_manager.core.gate_parts import HASH_RULE
    ent = log.record(tmp_path, "p", "g", result=log.CHECKED, reason=None, content="h")
    assert ent["rule"] == HASH_RULE
