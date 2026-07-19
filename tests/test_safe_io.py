"""Tests for quam_state_manager.core.safe_io.

Covers conflict-safe reads/writes of the live state files.  The Windows-only
tests are the proof of the design: ``ReplaceFileW`` writes succeed even while
a reader is attached (``os.replace`` does not), and our reads stay robust
against a concurrent external writer.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time

import pytest

from quam_state_manager.core import safe_io
from quam_state_manager.core.safe_io import (
    LiveFileError,
    atomic_write_json,
    open_shared,
    read_json,
    read_state_wiring,
    state_wiring_mtimes,
    write_state_wiring,
)

_IS_WINDOWS = sys.platform == "win32"
win_only = pytest.mark.skipif(
    not _IS_WINDOWS, reason="file-sharing semantics are Windows-specific"
)


def _seed(folder):
    state = {"qubits": {"q1": {"f_01": 6.0e9}}}
    wiring = {"wiring": {}, "network": {"host": "1.2.3.4"}}
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    return state, wiring


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def test_read_state_wiring_basic(tmp_path):
    state, wiring = _seed(tmp_path)
    got_state, got_wiring = read_state_wiring(tmp_path)
    assert got_state == state
    assert got_wiring == wiring


def test_read_json_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(safe_io, "_READ_BACKOFF_S", 0.0)
    with pytest.raises(FileNotFoundError):
        read_json(tmp_path / "state.json")


def test_read_json_invalid(tmp_path, monkeypatch):
    monkeypatch.setattr(safe_io, "_READ_BACKOFF_S", 0.0)
    (tmp_path / "state.json").write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(LiveFileError):
        read_json(tmp_path / "state.json")


def test_read_json_rejects_non_object(tmp_path, monkeypatch):
    monkeypatch.setattr(safe_io, "_READ_BACKOFF_S", 0.0)
    (tmp_path / "state.json").write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(LiveFileError):
        read_json(tmp_path / "state.json")


def test_open_shared_reads_content(tmp_path):
    (tmp_path / "state.json").write_text('{"v": 1}', encoding="utf-8")
    with open_shared(tmp_path / "state.json") as f:
        assert json.loads(f.read().decode("utf-8")) == {"v": 1}


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def test_write_state_wiring_roundtrip(tmp_path):
    state = {"qubits": {"q1": {"f_01": 6.0e9}}}
    wiring = {"wiring": {"q1": {}}}
    write_state_wiring(tmp_path, state, wiring)
    got_state, got_wiring = read_state_wiring(tmp_path)
    assert got_state == state
    assert got_wiring == wiring


def test_atomic_write_json_is_pretty(tmp_path):
    atomic_write_json(tmp_path / "state.json", {"a": 1})
    text = (tmp_path / "state.json").read_text(encoding="utf-8")
    assert "    " in text  # indent=4
    assert text.endswith("\n")


def test_atomic_write_overwrites_existing(tmp_path):
    p = tmp_path / "state.json"
    atomic_write_json(p, {"v": 1})
    atomic_write_json(p, {"v": 2})
    assert json.loads(p.read_text(encoding="utf-8")) == {"v": 2}


def test_atomic_write_leaves_no_tmp(tmp_path):
    write_state_wiring(tmp_path, {"a": 1}, {"b": 2})
    assert list(tmp_path.glob("*.tmp")) == []


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def test_state_wiring_mtimes(tmp_path):
    _seed(tmp_path)
    sm, wm = state_wiring_mtimes(tmp_path)
    assert isinstance(sm, float) and isinstance(wm, float)


def test_state_wiring_mtimes_missing(tmp_path):
    with pytest.raises(OSError):
        state_wiring_mtimes(tmp_path)


# ---------------------------------------------------------------------------
# Windows file-sharing -- the proof of the design
# ---------------------------------------------------------------------------

@win_only
def test_atomic_write_succeeds_while_reader_holds_file(tmp_path):
    """Our write (ReplaceFileW) must succeed even while a reader is attached.

    This is the guarantee that makes "Apply to live" safe.
    """
    state = tmp_path / "state.json"
    state.write_text('{"v": 1}', encoding="utf-8")

    with open_shared(state) as f:
        assert json.loads(f.read().decode("utf-8")) == {"v": 1}
        atomic_write_json(state, {"v": 2})  # must not raise

    assert json.loads(state.read_text(encoding="utf-8")) == {"v": 2}


@win_only
def test_os_replace_blocked_by_open_reader(tmp_path):
    """Design rationale: a plain os.replace IS blocked by an open reader.

    This is why :func:`atomic_write_json` uses ReplaceFileW instead.  If this
    ever stops failing, the platform no longer reproduces the I/O conflict.
    """
    state = tmp_path / "state.json"
    state.write_text('{"v": 1}', encoding="utf-8")
    new = tmp_path / "new.json"
    new.write_text('{"v": 2}', encoding="utf-8")

    with open(state, "rb"):
        with pytest.raises(PermissionError):
            os.replace(new, state)


@win_only
def test_reader_survives_concurrent_writes(tmp_path):
    """Stress: a writer using atomic_write_json while we read in a loop.

    Both sides must succeed -- the writer is never blocked and every read
    yields a complete, valid dict.
    """
    state = tmp_path / "state.json"
    wiring = tmp_path / "wiring.json"
    state.write_text(json.dumps({"qubits": {}}), encoding="utf-8")
    wiring.write_text(json.dumps({"wiring": {}}), encoding="utf-8")

    stop = threading.Event()
    write_failures: list = []
    writes = [0]

    def writer():
        i = 0
        while not stop.is_set():
            i += 1
            try:
                atomic_write_json(state, {"qubits": {}, "i": i})
                writes[0] += 1
            except Exception as exc:  # noqa: BLE001
                write_failures.append(repr(exc))

    t = threading.Thread(target=writer, daemon=True)
    t.start()

    reads = 0
    corrupt: list = []
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        try:
            s, w = read_state_wiring(tmp_path)
            reads += 1
            if not isinstance(s, dict) or "qubits" not in s:
                corrupt.append(s)
        except Exception as exc:  # noqa: BLE001
            corrupt.append(repr(exc))

    stop.set()
    t.join(timeout=5)

    assert not write_failures, f"writer failed: {write_failures[:3]}"
    assert not corrupt, f"reader saw corrupt/failed data: {corrupt[:3]}"
    assert writes[0] > 5 and reads > 5


@win_only
def test_reader_survives_concurrent_os_replace(tmp_path):
    """A non-cooperative writer (plain os.replace) must not corrupt our reads.

    The writer's own replaces may fail when our handle is briefly attached --
    that is the experiment-tooling problem the working-copy design minimises,
    and is outside this function's control -- but our read must always return
    complete, valid data and never raise.
    """
    state = tmp_path / "state.json"
    wiring = tmp_path / "wiring.json"
    state.write_text(json.dumps({"qubits": {}}), encoding="utf-8")
    wiring.write_text(json.dumps({"wiring": {}}), encoding="utf-8")

    stop = threading.Event()

    def writer():
        i = 0
        while not stop.is_set():
            i += 1
            tmp = tmp_path / "state.json.wtmp"
            tmp.write_text(json.dumps({"qubits": {}, "i": i}), encoding="utf-8")
            try:
                os.replace(tmp, state)
            except PermissionError:
                tmp.unlink(missing_ok=True)

    t = threading.Thread(target=writer, daemon=True)
    t.start()

    reads = 0
    corrupt: list = []
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        try:
            s, w = read_state_wiring(tmp_path)
            reads += 1
            if not isinstance(s, dict) or "qubits" not in s:
                corrupt.append(s)
        except Exception as exc:  # noqa: BLE001
            corrupt.append(repr(exc))

    stop.set()
    t.join(timeout=5)

    assert not corrupt, f"reader saw corrupt/failed data: {corrupt[:3]}"
    assert reads > 5


def test_read_state_wiring_retries_until_mtimes_settle(tmp_path, monkeypatch):
    """If the second mtime stat disagrees with the first, read_state_wiring
    must retry rather than return a torn snapshot.

    We monkey-patch ``state_wiring_mtimes`` to return alternating values on
    the first stat-pair, then settle, and assert that the call returned the
    expected pair without raising.
    """
    (tmp_path / "state.json").write_text(json.dumps({"qubits": {"qA1": {"f_01": 6e9}}}), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps({"wiring": {}}), encoding="utf-8")

    real_fp = safe_io._pair_fingerprint
    calls = {"n": 0}

    def flaky_fp(folder):
        calls["n"] += 1
        (st, wi) = real_fp(folder)
        # Calls 1+2 are the first (before, after) pair: make them differ
        # to simulate a concurrent write between our two reads.
        if calls["n"] == 2:
            return ((st[0] + 5, st[1]), wi)
        return (st, wi)

    monkeypatch.setattr(safe_io, "_pair_fingerprint", flaky_fp)

    state, wiring = read_state_wiring(tmp_path)
    assert state["qubits"]["qA1"]["f_01"] == 6e9
    assert "wiring" in wiring
    # Retried at least once (1st before, 1st after [drifted], 2nd before, 2nd after [settled]).
    assert calls["n"] >= 4


def test_read_state_wiring_never_settles_raises_not_torn(tmp_path, monkeypatch):
    """C28: if the mtimes NEVER settle (sustained external write), raise a
    LiveFileError rather than returning a possibly-torn pair — callers must not
    adopt a torn snapshot as a sync/drift baseline."""
    (tmp_path / "state.json").write_text(json.dumps({"qubits": {}}), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps({"wiring": {}}), encoding="utf-8")

    real_fp = safe_io._pair_fingerprint
    flip = {"n": 0}

    def always_drifting(folder):
        # Every 'after' fingerprint disagrees with its 'before' → never settles.
        flip["n"] += 1
        (st, wi) = real_fp(folder)
        return ((st[0] + flip["n"], st[1]), wi)

    monkeypatch.setattr(safe_io, "_pair_fingerprint", always_drifting)
    monkeypatch.setattr(safe_io, "_READ_BACKOFF_S", 0.0)  # keep the test fast

    with pytest.raises(safe_io.LiveFileError):
        read_state_wiring(tmp_path)


# ---------------------------------------------------------------------------
# POSIX rename durability (cross-platform audit): os.replace is atomic but NOT
# durable until the parent dir is fsync'd — a power cut right after
# apply-to-live could silently lose the rename. (ReplaceFileW is already
# durable, so the branch is a Windows no-op.)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only durability branch")
def test_atomic_write_fsyncs_parent_dir(tmp_path, monkeypatch):
    calls = []
    real = safe_io._fsync_dir

    def spying_fsync_dir(p):
        calls.append(p)
        real(p)

    monkeypatch.setattr(safe_io, "_fsync_dir", spying_fsync_dir)
    target = tmp_path / "sub" / "settings.json"
    target.parent.mkdir()
    atomic_write_json(target, {"a": 1})
    assert calls == [target.parent]
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}


def test_fsync_dir_missing_dir_is_noop(tmp_path):
    safe_io._fsync_dir(tmp_path / "does_not_exist")   # must not raise
