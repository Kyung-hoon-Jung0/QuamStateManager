"""A run-to-run state diff shows a pulse class change as a row."""
import json
from pathlib import Path

from quam_state_manager.core.differ import Differ
from quam_state_manager.core.history import diff_state_folders


def _folder(root: Path, name: str, pulses: dict) -> Path:
    d = root / name
    d.mkdir()
    state = {"__class__": "pkg.Machine",
             "qubits": {"q1": {"xy": {"operations": pulses}}}}
    (d / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (d / "wiring.json").write_text("{}", encoding="utf-8")
    return d


def _cls(entries):
    return [e for e in entries if e.dot_path.rsplit(".", 1)[-1] == "__class__"]


def test_class_only_change_is_a_modified_row(tmp_path):
    a = _folder(tmp_path, "a", {"cz": {"__class__": "pkg.SquarePulse", "length": 44}})
    b = _folder(tmp_path, "b", {"cz": {"__class__": "pkg.WaveformPulse", "length": 44}})
    ents = diff_state_folders(a, b, keep_class=True)
    rows = _cls(ents)
    assert [(r.change_type, r.old_value, r.new_value) for r in rows] == [
        ("modified", "pkg.SquarePulse", "pkg.WaveformPulse")]
    assert Differ.summary(ents)["modified"] == 1
    assert diff_state_folders(a, b, keep_class=False) == []


def test_equal_class_adds_no_row(tmp_path):
    a = _folder(tmp_path, "a", {"cz": {"__class__": "pkg.SquarePulse", "length": 44}})
    b = _folder(tmp_path, "b", {"cz": {"__class__": "pkg.SquarePulse", "length": 48}})
    ents = diff_state_folders(a, b, keep_class=True)
    assert _cls(ents) == [] and len(ents) == 1


def test_pulse_in_one_run_only(tmp_path):
    a = _folder(tmp_path, "a", {})
    b = _folder(tmp_path, "b", {"cz": {"__class__": "pkg.SquarePulse", "length": 44}})
    rows = _cls(diff_state_folders(a, b, keep_class=True))
    assert [r.change_type for r in rows] == ["added"]


def test_class_change_still_a_row_when_the_cached_read_fails(tmp_path, monkeypatch):
    """The unreadable-pair fallback reads the folders directly and keeps the class."""
    from quam_state_manager.core import doc_cache

    def refuse(*args, **kwargs):
        raise OSError("pair read refused")

    monkeypatch.setattr(doc_cache, "read_pair", refuse)
    a = _folder(tmp_path, "a", {"cz": {"__class__": "pkg.SquarePulse", "length": 44}})
    b = _folder(tmp_path, "b", {"cz": {"__class__": "pkg.WaveformPulse", "length": 44}})
    rows = _cls(diff_state_folders(a, b, keep_class=True))
    assert [(r.change_type, r.old_value, r.new_value) for r in rows] == [
        ("modified", "pkg.SquarePulse", "pkg.WaveformPulse")]
    assert diff_state_folders(a, b, keep_class=False) == []
