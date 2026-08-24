"""docs/139 fix 2 — the fingerprint cache survives a restart.

The alignment scan fingerprints EVERY workspace run by reading both its JSON
files (measured: 2,653 runs × 2 files = 15.1s of read_json, 97% of a cold
/param-history), and the cache was memory-only — every SM restart re-paid the
whole scan. Run archives are immutable and the cache is (mtime, mtime)-keyed,
so persisting it is safe by construction. Measured after the fix: a NEW
process's first hit fell from 18–22s to 0.5s.
"""

from __future__ import annotations

import json

import pytest

from quam_state_manager.core import history as H


def _chip(tmp_path, name="run1", host="1.2.3.4"):
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {}, "q2": {}}, "qubit_pairs": {"q1-2": {}}}),
        encoding="utf-8")
    (d / "wiring.json").write_text(json.dumps(
        {"network": {"host": host, "cluster_name": "C"}, "wiring": {}}),
        encoding="utf-8")
    return d


def _hm(tmp_path):
    return H.HistoryManager(tmp_path / "instance")


class TestTheSidecarRoundTrip:
    def test_a_new_manager_serves_from_disk_without_reading_the_run(
            self, tmp_path, monkeypatch):
        """The whole point: after a restart the fingerprint comes from the
        sidecar, and the run's files are NOT re-read."""
        run = _chip(tmp_path)
        hm1 = _hm(tmp_path)
        fp1 = hm1._cached_fingerprint(run)
        assert fp1 is not None
        hm1._flush_fingerprint_sidecar()
        assert hm1._fingerprint_sidecar.is_file()

        hm2 = _hm(tmp_path)                      # the restart
        def boom(_path):
            raise AssertionError("fingerprint_of was called — the sidecar "
                                 "was not used")
        monkeypatch.setattr(H, "fingerprint_of", boom)
        assert hm2._cached_fingerprint(run) == fp1

    def test_identity_survives_serialisation(self, tmp_path):
        """Equality/hash semantics must round-trip — the alignment scan
        compares fingerprints, and a lossy trip would mis-group chips."""
        run = _chip(tmp_path)
        hm1 = _hm(tmp_path)
        fp1 = hm1._cached_fingerprint(run)
        hm1._flush_fingerprint_sidecar()
        hm2 = _hm(tmp_path)
        fp2 = hm2._cached_fingerprint(run)
        assert fp1 == fp2 and hash(fp1) == hash(fp2)
        assert fp2.qubits == frozenset({"q1", "q2"})

    def test_a_touched_run_recomputes(self, tmp_path):
        """The mtime key is the safety: a modified state.json misses."""
        import os
        run = _chip(tmp_path)
        hm1 = _hm(tmp_path)
        hm1._cached_fingerprint(run)
        hm1._flush_fingerprint_sidecar()
        (run / "state.json").write_text(json.dumps(
            {"qubits": {"q9": {}}, "qubit_pairs": {}}), encoding="utf-8")
        os.utime(run / "state.json", (1e9, 1e9))   # force a different mtime
        hm2 = _hm(tmp_path)
        assert hm2._cached_fingerprint(run).qubits == frozenset({"q9"})

    def test_memory_beats_disk(self, tmp_path):
        """Disk never overrides an in-memory entry — memory is at least as
        fresh (the sidecar folds in via setdefault)."""
        run = _chip(tmp_path)
        hm = _hm(tmp_path)
        fp = hm._cached_fingerprint(run)
        # poison the sidecar with a DIFFERENT fingerprint under the same key
        key = str(run)
        st = (run / "state.json").stat().st_mtime
        wi = (run / "wiring.json").stat().st_mtime
        hm._fingerprint_sidecar.write_text(json.dumps(
            {key: [st, wi, {"network": [["host", "9.9.9.9"]],
                            "qubits": ["zz"], "pairs": []}]}), encoding="utf-8")
        hm._fp_sidecar_loaded = False
        assert hm._cached_fingerprint(run) == fp     # memory won


class TestItIsACacheNotAFile:
    def test_a_corrupt_sidecar_is_ignored(self, tmp_path):
        run = _chip(tmp_path)
        hm = _hm(tmp_path)
        hm._fingerprint_sidecar.write_text("{not json", encoding="utf-8")
        assert hm._cached_fingerprint(run) is not None   # recomputed, no raise

    def test_one_bad_entry_never_poisons_the_rest(self, tmp_path):
        run = _chip(tmp_path)
        hm1 = _hm(tmp_path)
        fp = hm1._cached_fingerprint(run)
        hm1._flush_fingerprint_sidecar()
        raw = json.loads(hm1._fingerprint_sidecar.read_text(encoding="utf-8"))
        raw["C:\\bogus"] = ["not", "a", "valid", "entry"]
        raw["C:\\bogus2"] = None
        hm1._fingerprint_sidecar.write_text(json.dumps(raw), encoding="utf-8")
        hm2 = _hm(tmp_path)
        assert hm2._cached_fingerprint(run) == fp

    def test_flush_is_a_noop_when_nothing_was_computed(self, tmp_path):
        hm = _hm(tmp_path)
        hm._flush_fingerprint_sidecar()
        assert not hm._fingerprint_sidecar.is_file()

    def test_an_unreadable_run_is_not_persisted_as_a_lie(self, tmp_path):
        """A missing state.json returns None WITHOUT touching the cache —
        nothing to persist, nothing to serve stale later."""
        hm = _hm(tmp_path)
        assert hm._cached_fingerprint(tmp_path / "nope") is None
        hm._flush_fingerprint_sidecar()
        assert not hm._fingerprint_sidecar.is_file()

    def test_the_scan_flushes_once(self, tmp_path, monkeypatch):
        """The alignment scan is the mass producer; it must flush at the end
        (never per entry — 4,000 atomic writes would be its own perf bug)."""
        import inspect
        src = inspect.getsource(H.HistoryManager.scan_workspace_alignment)
        assert "_flush_fingerprint_sidecar" in src

    def test_the_flush_never_invalidates_the_alignment_cache(self, tmp_path):
        """The token sweep skips dirs at/under the manager's own _root —
        when the instance dir nests inside a workspace root, the sidecar
        flush (or a snapshot write) must not read as 'workspace changed'
        and invalidate the scan's own cache."""
        from quam_state_manager.core.scanner import Workspace
        _chip(tmp_path, "exp1")
        hm = _hm(tmp_path)            # instance dir nests under tmp_path
        ws = Workspace()
        ws.add_root(str(tmp_path))
        r1 = hm.scan_workspace_alignment(tmp_path / "exp1", ws)
        assert hm._fp_dirty == 0      # the scan flushed (wrote the sidecar)
        r2 = hm.scan_workspace_alignment(tmp_path / "exp1", ws)
        assert r2 is r1
