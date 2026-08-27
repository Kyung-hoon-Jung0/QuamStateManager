"""The raw-bytes read/write pair (2026-08-27): an apply and a history snapshot
copy the bytes they parsed instead of re-serialising -- byte-identical
copies, one dump fewer per file. Pins: identity, the same torn-pair refusal
as read_state_wiring, the same state rollback when the wiring replace
fails, and that a snapshot IS a byte copy of its source."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import safe_io
from quam_state_manager.core.history import HistoryManager


def _seed(folder: Path, state: dict, wiring: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    # deliberately NOT SM's canonical format: 2-space indent, sorted keys,
    # no trailing newline -- a copy must keep it verbatim
    (folder / "state.json").write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")


def test_read_json_raw_returns_the_parsed_dict_and_its_exact_bytes(tmp_path):
    p = tmp_path / "state.json"
    raw_in = b'{"qubits": {"q1": {"T1": 1e-05}}}\n'
    p.write_bytes(raw_in)                      # bytes: text mode would CRLF it on Windows
    data, raw = safe_io.read_json_raw(p)
    assert data == {"qubits": {"q1": {"T1": 1e-05}}}
    assert raw == raw_in


def test_read_json_raw_rejects_a_non_object_like_read_json(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(safe_io.LiveFileError):
        safe_io.read_json_raw(p, attempts=1)
    with pytest.raises(FileNotFoundError):
        safe_io.read_json_raw(tmp_path / "missing.json", attempts=1)


def test_read_state_wiring_raw_agrees_with_read_state_wiring(tmp_path):
    _seed(tmp_path, {"b": 1, "a": {"x": [1, 2]}}, {"w": True})
    s, w = safe_io.read_state_wiring(tmp_path)
    s2, w2, sb, wb = safe_io.read_state_wiring_raw(tmp_path)
    assert (s, w) == (s2, w2)
    assert sb == (tmp_path / "state.json").read_bytes()
    assert wb == (tmp_path / "wiring.json").read_bytes()


def test_read_state_wiring_raw_refuses_a_torn_pair(tmp_path, monkeypatch):
    _seed(tmp_path, {"a": 1}, {})
    fps = iter([("A", "A"), ("B", "B")] * 10)   # never settles
    monkeypatch.setattr(safe_io, "_pair_fingerprint", lambda folder: next(fps))
    monkeypatch.setattr(safe_io, "_READ_BACKOFF_S", 0)
    with pytest.raises(safe_io.LiveFileError, match="kept changing"):
        safe_io.read_state_wiring_raw(tmp_path, attempts=2)


def test_write_state_wiring_bytes_is_a_verbatim_copy(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _seed(src, {"qubits": {"q1": {"T1": 1e-05, "f_01": 4.8e9}}}, {"ports": {}})
    _, _, sb, wb = safe_io.read_state_wiring_raw(src)
    safe_io.write_state_wiring_bytes(dst, sb, wb)
    assert (dst / "state.json").read_bytes() == (src / "state.json").read_bytes()
    assert (dst / "wiring.json").read_bytes() == (src / "wiring.json").read_bytes()
    # and it parses to the same content the dict path would have written
    assert safe_io.read_state_wiring(dst) == safe_io.read_state_wiring(src)


def test_write_state_wiring_bytes_rolls_state_back_when_wiring_fails(tmp_path, monkeypatch):
    dst = tmp_path / "dst"
    _seed(dst, {"old": 1}, {"old": 1})
    old_state = (dst / "state.json").read_bytes()
    real = safe_io._replace_into_place
    calls = {"n": 0}

    def flaky(tmp, target):
        calls["n"] += 1
        if calls["n"] == 2:                 # the WIRING replace
            raise OSError("disk says no")
        return real(tmp, target)
    monkeypatch.setattr(safe_io, "_replace_into_place", flaky)
    with pytest.raises(OSError):
        safe_io.write_state_wiring_bytes(dst, b'{"new": 1}\n', b'{"new": 1}\n')
    assert (dst / "state.json").read_bytes() == old_state, "state rolled back to its exact bytes"


def test_a_history_snapshot_is_a_byte_copy_of_its_source(tmp_path):
    """check_and_snapshot used to re-dump the parsed dicts; now the snapshot
    files are the source files' bytes -- including a foreign format."""
    live = tmp_path / "chip" / "quam_state"
    _seed(live, {"qubits": {"qA1": {"T1": 1e-05}}, "active_qubit_names": ["qA1"]}, {"ports": {}})
    hm = HistoryManager(tmp_path / "inst")
    meta = hm.check_and_snapshot(str(live), "manual", force=True)
    assert meta is not None
    snap_dir = next(p for p in (tmp_path / "inst").rglob(meta.timestamp) if p.is_dir())
    assert (snap_dir / "state.json").read_bytes() == (live / "state.json").read_bytes()
    assert (snap_dir / "wiring.json").read_bytes() == (live / "wiring.json").read_bytes()
